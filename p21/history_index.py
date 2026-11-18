"""Versioned, read-only history projection in the ORIGINAL ImportStore database."""
import copy,json,time
from datetime import datetime,timezone
from .history_buckets import instant,buckets,check_coverage,coverage_end
from .history_model import demand_report,inventory_report,balance_records,apply_demand,apply_inventory,text
from decimal import Decimal
from .history_source import prepare,digest

def document_identity(documents):
    """Match SourceSync business identity while retaining every source revision.

    Incremental heads retain unchanged rows' original optional read clock;
    a later full view refreshes it. SourceSync excludes only that clock from
    business revisions, after checking it does not exceed the document as_of.
    """
    value=copy.deepcopy(documents)
    for d in value.values():
        for name,rows in d['tables'].items():
            for row in rows:
                if row.get('aps_read_at') and instant(row['aps_read_at'])>instant(d['as_of']):
                    raise ValueError('HISTORY_SOURCE_READ_TIME_CONFLICT')
                row.pop('aps_read_at',None)
            d['tables'][name]=sorted(rows,key=lambda r:r['id'])
        for records in d['event_refs'].values():
            for key,ids in records.items():records[key]=sorted(ids)
    return digest(value)


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS history_scopes (version_id TEXT PRIMARY KEY, data TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS history_observations (version_id TEXT,system TEXT,event_id TEXT,occurred REAL,known REAL,kind TEXT,item_id TEXT,location_id TEXT,uom TEXT,customer_id TEXT,order_id TEXT,lot_id TEXT,ordinal INTEGER,data TEXT NOT NULL,PRIMARY KEY(version_id,system,event_id))')
    db.execute('CREATE INDEX IF NOT EXISTS history_time_object ON history_observations(version_id,system,occurred,item_id,location_id)')
    db.execute('CREATE INDEX IF NOT EXISTS history_order ON history_observations(version_id,order_id,occurred)')
    db.execute('CREATE TABLE IF NOT EXISTS history_confirmed_versions (scope_version_id TEXT,version_id TEXT,data TEXT NOT NULL,PRIMARY KEY(scope_version_id,version_id))')
    db.execute('CREATE TABLE IF NOT EXISTS history_members (scope_version_id TEXT,version_id TEXT,system TEXT,event_id TEXT,PRIMARY KEY(scope_version_id,version_id,system,event_id))')


def register(store,version_id,capture,*,archived_versions=()):
    """Internal SourceSync adapter entry. Not an untrusted public upload route."""
    version=store.version(version_id);meta=store.version_metadata(version_id)
    store.verify_version_sources(version)
    if meta.get('confirmation_status')!='CONFIRMED':raise ValueError('HISTORY_VERSION_NOT_CONFIRMED')
    canonical=version.get('canonical',{})
    bundle=next((r['continuity_bundle'] for r in canonical.get('tables',{}).get('locations',[]) if r.get('continuity_bundle')),None)
    if not bundle:raise ValueError('HISTORY_SOURCE_COVERAGE_NOT_AVAILABLE')
    if canonical['enterprise_id']!=capture['enterprise_id'] or document_identity(bundle['documents'])!=document_identity(capture['source_documents']):raise ValueError('HISTORY_CONFIRMED_SOURCE_MISMATCH')
    rows=prepare(capture)
    scope=dict(enterprise_id=capture['enterprise_id'],branch_id=capture['branch_id'],source_as_of=capture['source_as_of'],
        origin=capture['origin'],end=capture['end'],timezone=canonical['timezone'],captured_at=datetime.now(timezone.utc).isoformat(),
        capture_sha256=digest(capture),observation_count=len(rows),source_basis=capture['observation_basis'],
        coverage='ERP_ORDER_CREATE_CHANGE_SHIP;WMS_RECEIPT_QC_MATERIAL_FG_SHIP;MES_JOB_OPERATION_TRACE',execution_authorized=False)
    with store.transaction() as db:
        initialize(db)
        old=db.execute('SELECT data FROM history_scopes WHERE version_id=?',(version_id,)).fetchone()
        if old:
            if json.loads(old[0])['capture_sha256']!=scope['capture_sha256']:raise ValueError('HISTORY_CAPTURE_CHANGED')
            scope=json.loads(old[0])
        else:
            db.execute('INSERT INTO history_scopes VALUES(?,?)',(version_id,json.dumps(scope)))
            for r in rows:
                db.execute('INSERT INTO history_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(version_id,r['system'],r['event_id'],instant(r['occurred_at']).timestamp(),instant(r['source_known_at']).timestamp(),r['kind'],r['item_id'],r['location_id'],r['uom'],r['customer_id'],r['order_id'],r['lot_id'],r['ordinal'],json.dumps(r,ensure_ascii=False)))
        for v in [version,*archived_versions]:
            from p22.contracts import validate_version
            validate_version(v)
            c=v.get('canonical',{});b=next((r['continuity_bundle'] for r in c.get('tables',{}).get('locations',[]) if r.get('continuity_bundle')),None)
            if not b or c.get('enterprise_id')!=scope['enterprise_id']:raise ValueError('HISTORY_ARCHIVE_ENTERPRISE_MISMATCH')
            documents=b['documents']
            if any(d.get('branch_id')!=scope['branch_id'] for d in documents.values()):raise ValueError('HISTORY_ARCHIVE_BRANCH_MISMATCH')
            saved=dict(version_id=v['id'],confirmed_at=v['created_at'],source_as_of=c['planning_at'],source_refs=v.get('source_sync_receipt'),
                confirmation_basis='SAVED_CONFIRMED_CANONICAL_VERSION',wall_clock_is_not_business_clock=True,version_sha256=digest(v))
            members={(system,eid) for system,document in documents.items()
                     for refs in document['event_refs'].values() for event_ids in refs.values() for eid in event_ids}
            prior=db.execute('SELECT data FROM history_confirmed_versions WHERE scope_version_id=? AND version_id=?',(version_id,v['id'])).fetchone()
            if prior:
                original=json.loads(prior[0])
                if {k:value for k,value in original.items() if k!='version_sha256'}!={k:value for k,value in saved.items() if k!='version_sha256'} or original.get('version_sha256',saved['version_sha256'])!=saved['version_sha256']:
                    raise ValueError('HISTORY_ARCHIVE_VERSION_CHANGED')
                actual={(r[0],r[1]) for r in db.execute('SELECT system,event_id FROM history_members WHERE scope_version_id=? AND version_id=?',(version_id,v['id']))}
                if actual!=members:raise ValueError('HISTORY_ARCHIVE_MEMBERS_CHANGED')
                if 'version_sha256' not in original:
                    # Only the current version can be recovered from the original
                    # authenticated ImportStore here. Metadata alone cannot prove
                    # the complete content of an older external archive.
                    if v['id']!=version_id:raise ValueError('HISTORY_ARCHIVE_VERSION_EVIDENCE_REQUIRED')
                    db.execute('UPDATE history_confirmed_versions SET data=? WHERE scope_version_id=? AND version_id=?',(json.dumps(saved),version_id,v['id']))
                continue
            db.execute('INSERT INTO history_confirmed_versions VALUES(?,?,?)',(version_id,v['id'],json.dumps(saved)))
            db.executemany('INSERT INTO history_members VALUES(?,?,?,?)',[(version_id,v['id'],system,eid) for system,eid in sorted(members)])
    return scope


def context(store,version_id):
    store.version_metadata(version_id) # Original Owner enforces version existence.
    with store.connect() as db:
        initialize(db);row=db.execute('SELECT data FROM history_scopes WHERE version_id=?',(version_id,)).fetchone()
        if not row:raise ValueError('HISTORY_NOT_COVERED')
        scope=json.loads(row[0])
        versions=[json.loads(r[0]) for r in db.execute('SELECT data FROM history_confirmed_versions WHERE scope_version_id=?',(version_id,))]
        dims=[dict(zip(('item_id','location_id','uom','customer_id'),r)) for r in db.execute('SELECT DISTINCT item_id,location_id,uom,customer_id FROM history_observations WHERE version_id=? ORDER BY item_id,customer_id',(version_id,))]
    return dict(identity=dict(data_version_id=version_id,enterprise_id=scope['enterprise_id'],branch_id=scope['branch_id'],run_id=None),scope=scope,versions=versions,dimensions=dims,
        query_end_by_grain={grain:coverage_end(scope,grain).isoformat() for grain in ('day','week','month')})


def query(store,version_id,*,domain,start,end,grain='month',perspective='current',known_at=None,source_system=None,knowledge_version_id=None,
          item_id=None,location_id=None,customer_id=None,lot_id=None,order_id=None,bucket_id=None,metric=None,page_size=50,offset=0,cohort_status=None,bucket_page_size=None,bucket_offset=0):
    started=time.perf_counter();c=context(store,version_id);scope=c['scope']
    if domain not in {'demand','inventory'} or perspective not in {'current','source','aps'}:raise ValueError('HISTORY_QUERY_UNSUPPORTED')
    if domain=='inventory' and (order_id is not None or customer_id is not None):raise ValueError('HISTORY_INVENTORY_BALANCE_ORDER_FILTER_UNSUPPORTED')
    if domain=='demand' and lot_id is not None:raise ValueError('HISTORY_DEMAND_LOT_FILTER_UNSUPPORTED')
    traces={'order_trace','lot_trace'}
    domain_metrics={'demand':{'new_orders','new_quantity','revision_quantity','shipped','opening_open','ending_open','effective_demand','overdue','order_trace'},'inventory':{'physical_in','physical_out','adjustment','opening_physical','ending_physical','ending_pending','ending_held','ending_available','batches','lot_trace'}}
    if metric is not None and (metric not in domain_metrics[domain] or metric not in traces and bucket_id is None):raise ValueError('HISTORY_METRIC_SCOPE_INVALID')
    if cohort_status is not None and cohort_status not in ({'OPEN','PARTIAL','COMPLETED'} if domain=='demand' else {'pending','held','available'}):raise ValueError('HISTORY_COHORT_STATUS_INVALID')
    if type(page_size) is not int or not 1<=page_size<=100 or type(offset) is not int or offset<0:raise ValueError('HISTORY_PAGE_INVALID')
    if type(bucket_offset)is not int or bucket_offset<0 or bucket_page_size is not None and (type(bucket_page_size)is not int or not 1<=bucket_page_size<=62):raise ValueError('HISTORY_BUCKET_PAGE_INVALID')
    periods=buckets(start,end,grain,scope['timezone'])
    check_coverage(scope,start,end,grain)
    authority='ERP' if domain=='demand' else 'WMS';system=source_system or authority
    if system not in {'ERP','MES','WMS'}:raise ValueError('HISTORY_SOURCE_UNKNOWN')
    cutoff=instant(scope['source_as_of']);membership=None
    if perspective=='source':
        if not known_at:raise ValueError('HISTORY_KNOWLEDGE_TIME_REQUIRED')
        cutoff=min(cutoff,instant(known_at))
    elif perspective=='aps':
        v=next((v for v in c['versions'] if v['version_id']==knowledge_version_id),None)
        if v is None:raise ValueError('HISTORY_NO_CONFIRMED_VERSION_EVIDENCE')
        if known_at and instant(v['confirmed_at'])>instant(known_at):raise ValueError('HISTORY_APS_VERSION_NOT_YET_CONFIRMED')
        cutoff=min(cutoff,instant(v['source_as_of']));membership=v
    else:system=authority
    kinds=('SalesOrderCreated','SalesOrderChanged','ShipmentPosted') if domain=='demand' else ('GoodsReceived','QCReleased','QCHeld','MaterialIssued','FinishedGoodsReceived','ShipmentPosted')
    conditions=['o.version_id=?','o.system=?','o.occurred<?','o.occurred<=?','o.known<=?','o.kind IN ('+','.join('?' for _ in kinds)+')']
    values=[version_id,system,instant(end).timestamp(),cutoff.timestamp(),cutoff.timestamp(),*kinds]
    for field,value in [('item_id',item_id),('location_id',location_id),('customer_id',customer_id),('lot_id',lot_id),('order_id',order_id)]:
        if value is not None:conditions.append('o.'+field+'=?');values.append(value)
    if membership:
        conditions.append('EXISTS (SELECT 1 FROM history_members m WHERE m.scope_version_id=o.version_id AND m.version_id=? AND m.system=o.system AND m.event_id=o.event_id)');values.append(knowledge_version_id)
    sql='SELECT o.data FROM history_observations o WHERE '+' AND '.join(conditions)+' ORDER BY o.occurred,o.ordinal,o.event_id'
    with store.connect() as db:rows=[json.loads(r[0]) for r in db.execute(sql,values)]
    unsupported=system!=authority
    if cohort_status:
        if unsupported:raise ValueError('HISTORY_SOURCE_COHORT_NOT_COVERED')
        state={}
        for r in rows:(apply_demand if domain=='demand' else apply_inventory)(state,r)
        eligible={key for key,s in state.items() if (('COMPLETED' if s['shipped']==s['quantity'] else 'PARTIAL' if s['shipped'] else 'OPEN')==cohort_status if domain=='demand' else s[cohort_status]>0)}
        rows=[r for r in rows if r['order_id' if domain=='demand' else 'lot_id'] in eligible]
    if unsupported:
        out=dict(buckets=periods,groups=[],totals=[],status='SOURCE_METRIC_NOT_COVERED',source_records=len(rows))
    else:out=demand_report(rows,start,end,grain,scope['timezone'],knowledge_cutoff=cutoff) if domain=='demand' else inventory_report(rows,start,end,grain,scope['timezone'])
    if not out.get('groups') and not unsupported:out['status']='NOT_YET_KNOWN' if cutoff<=instant(start) else 'NO_MATCHING_OBSERVED_OBJECT' # Absence is not a zero-valued object.
    for group in out.get('groups',[]):
        for cell,b in zip(group['cells'],periods):
            if instant(b['start'])>cutoff:
                for key in list(cell):
                    if key not in {'bucket_id','status'}:cell[key]=None
                cell['status']='NOT_YET_KNOWN'
            elif instant(b['end'])>cutoff:
                cell['status']='PARTIAL_KNOWLEDGE';cell['observed_through']=cutoff.isoformat()
    if any(instant(b['end'])>cutoff for b in periods):out['totals']=[]
    detail=None
    if metric in traces:
        if not (order_id if metric=='order_trace' else lot_id) or bucket_id is not None:raise ValueError('HISTORY_TRACE_SCOPE_REQUIRED')
        detail=dict(total=len(rows),rows=rows[offset:offset+page_size],next_offset=offset+page_size if offset+page_size<len(rows) else None,basis='EXACT_SOURCE_OBJECT_EVENT_CHAIN',metric=metric)
    if bucket_id is not None:
        b=next((b for b in periods if b['id']==bucket_id),None)
        if b is None:raise ValueError('HISTORY_BUCKET_MISMATCH')
        a,z=instant(b['start']),instant(b['end'])
        kind_filter={'new_orders':{'SalesOrderCreated'},'new_quantity':{'SalesOrderCreated'},'revision_quantity':{'SalesOrderChanged'},'shipped':{'ShipmentPosted'},
            'physical_in':{'GoodsReceived','FinishedGoodsReceived'},'physical_out':{'MaterialIssued','ShipmentPosted'},'adjustment':set()}
        if metric not in set(kind_filter)|{'opening_open','ending_open','effective_demand','overdue','opening_physical','ending_physical','ending_pending','ending_held','ending_available','batches'}:raise ValueError('HISTORY_METRIC_UNSUPPORTED')
        if instant(b['start'])>cutoff:raise ValueError('HISTORY_BUCKET_NOT_YET_KNOWN')
        if metric=='batches':
            events={}
            for r in rows:
                if a<=instant(r['occurred_at'])<z:events.setdefault((r['item_id'],r['location_id'],r['uom'],r['lot_id']),[]).append(r)
            selected=[dict(object_id=key[-1],lot_id=key[-1],item_id=key[0],location_id=key[1],uom=key[2],contribution='1',source_events=[dict(event_id=r['event_id'],occurred_at=r['occurred_at'],source_known_at=r['source_known_at'],source_records=r['source_records']) for r in values[:8]],source_event_count=len(values),source_events_truncated=len(values)>8) for key,values in sorted(events.items())]
        elif metric in kind_filter:
            selected=[r for r in rows if a<=instant(r['occurred_at'])<z and r['kind'] in kind_filter[metric]]
            if metric=='new_orders':selected=[dict(r,contribution='1') for r in selected]
            if metric=='revision_quantity':
                state={};amounts={}
                for r in rows:amounts[r['event_id']]=text(apply_demand(state,r))
                selected=[dict(r,contribution=amounts[r['event_id']]) for r in selected]
        else:selected=balance_records(rows,domain,metric,min(a if metric.startswith('opening_') else z,cutoff),include_boundary=not metric.startswith('opening_') and cutoff<z)
        detail=dict(total=len(selected),rows=selected[offset:offset+page_size],next_offset=offset+page_size if offset+page_size<len(selected) else None,
            basis='CONSTITUENT_CHANGED_BATCHES' if metric=='batches' else 'CONSTITUENT_FLOW_RECORDS' if metric in kind_filter else 'CONSTITUENT_BALANCE_OBJECTS',metric=metric,bucket=b)
    out.update(schema_version='business-history/1',identity=c['identity'],scope=scope,perspective=perspective,knowledge_version=membership,
        source_system=system,knowledge_cutoff=cutoff.isoformat(),detail=detail,filters=dict(item_id=item_id,location_id=location_id,customer_id=customer_id,lot_id=lot_id,order_id=order_id,cohort_status=cohort_status),
        query_coverage=dict(requested_end=end,calendar_end=coverage_end(scope,grain).isoformat(),end_by_grain=c['query_end_by_grain'],observed_through=cutoff.isoformat(),complete=instant(end)<=cutoff),
        diagnostics=dict(aggregation_ms=round((time.perf_counter()-started)*1000,3),observations_loaded=len(rows),full_snapshot_loaded=False,worker_called=False),execution_authorized=False)
    size=bucket_page_size or len(periods)
    out['bucket_page']=dict(total=len(periods),offset=bucket_offset,returned=len(periods[bucket_offset:bucket_offset+size]),next_offset=bucket_offset+size if bucket_offset+size<len(periods) else None,totals_scope='FULL_REQUESTED_RANGE_NOT_COLUMN_PAGE')
    out['buckets']=out['buckets'][bucket_offset:bucket_offset+size]
    for group in out.get('groups',[]):group['cells']=group['cells'][bucket_offset:bucket_offset+size]
    return out
