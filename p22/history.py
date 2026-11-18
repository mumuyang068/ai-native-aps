"""Historical Plan references owned by PlanStore; original run artifacts stay authoritative.

Registration is an offline administrative recovery action, never an HTTP path
parameter or a historical recalculation. Only explicitly selected JSON members
are authenticated. In particular this Owner never opens legacy SQL or XML.
"""
import copy,json,re,hashlib
from pathlib import Path
from decimal import Decimal
from p22.contracts import validate_version,digest,encoded
from p22.query_reads import artifact_members
from p21.history_buckets import buckets,instant
from p21.history_model import text

MEMBERS=('canonical_input.json','PlanResult.json','enterprise-context.json','FoundationNativeAudit-v1.json')

def initialize(store):
    with store.db() as db:
        db.execute('CREATE TABLE IF NOT EXISTS history_runs(scope_version_id TEXT,plan_id TEXT,run_id TEXT,record TEXT,assets TEXT,PRIMARY KEY(scope_version_id,plan_id,run_id))')

def _summary(data,result):
    items={r['id']:r for r in data['items']};demands={r['id']:r for r in data['demand_lines']};totals={}
    for r in result['deliveries']:
        demand=demands[r['demand_id']];item=items[demand['item_id']];unit=r.get('uom') or item['base_uom']
        key=(demand['item_id'],unit);t=totals.setdefault(key,dict(item_id=key[0],uom=unit,requested=Decimal(0),planned=Decimal(0),unplanned=Decimal(0),late_lines=0))
        for k,field in [('requested','requested_quantity'),('planned','planned_quantity'),('unplanned','unfulfilled_quantity')]:t[k]+=Decimal(r[field])
        t['late_lines']+=r.get('delay_seconds') is not None and r['delay_seconds']>0 and Decimal(r['planned_quantity'])>0
    return [{k:text(v) if isinstance(v,Decimal) else v for k,v in t.items()} for _,t in sorted(totals.items())]

def register(store,scope_version_id,version,state,status,folder,*,label):
    """Verify original saved identities, confirmation, hashes and actual saved result."""
    validate_version(version);folder=Path(folder)
    with store.db() as db:scope_row=db.execute('SELECT record FROM plans WHERE version_id=?',(scope_version_id,)).fetchone()
    if not scope_row:raise ValueError('HISTORY_SCOPE_PLAN_NOT_FOUND')
    scope_snapshot=store.snapshot(json.loads(scope_row[0])['id'])
    scope_canonical=scope_snapshot['data_version']['canonical']
    scope_bundle=next(r['continuity_bundle'] for r in scope_canonical['tables']['locations'] if r.get('continuity_bundle'))
    manifest=json.loads((folder/'run-manifest.json').read_text('utf8'))
    identity={k:manifest[k] for k in ('plan_id','data_version_id','run_id','snapshot_sha256')}
    if not re.fullmatch(r'PLAN-[a-f0-9]{16}',identity['plan_id']) or not re.fullmatch(r'API-[a-f0-9]{16}',identity['run_id']):raise ValueError('HISTORY_ARCHIVE_IDENTITY_INVALID')
    values=artifact_members(folder,manifest,MEMBERS)
    data,result,context,audit=(values[k] for k in MEMBERS)
    run=state['baseline']['run']
    if (identity['data_version_id']!=version['id'] or result['solve_run_id']!=identity['run_id'] or
        result['scenario_id']!=identity['plan_id'] or data['scenario_id']!=identity['plan_id'] or result['input_hash']!=digest(data) or
        run['solve_run_id']!=identity['run_id'] or run['state']!='SUCCEEDED' or status['run']!=run or
        state['baseline']['result']!=result or result['result_hash']!=digest({k:v for k,v in result.items() if k!='result_hash'}) or
        data['foundation_projection']['source_version_id']!=version['id'] or data['foundation_projection']['canonical_semantic_sha256']!=version['canonical']['semantic_sha256'] or
        {r['demand_id'] for r in result['deliveries']}!={r['id'] for r in data['demand_lines']} or
        any(context[k]!=identity[k] for k in ('plan_id','data_version_id','snapshot_sha256'))):raise ValueError('HISTORY_ARCHIVE_SCOPE_MISMATCH')
    c=version['canonical'];bundle=next(r['continuity_bundle'] for r in c['tables']['locations'] if r.get('continuity_bundle'))
    branches={d['branch_id'] for d in bundle['documents'].values()}
    if len(branches)!=1:raise ValueError('HISTORY_ARCHIVE_BRANCH_MISMATCH')
    if c['enterprise_id']!=scope_canonical['enterprise_id'] or branches!={d['branch_id'] for d in scope_bundle['documents'].values()}:raise ValueError('HISTORY_ARCHIVE_ENTERPRISE_SCOPE_MISMATCH')
    record=dict(identity=identity,label=label,enterprise_id=c['enterprise_id'],branch_id=next(iter(branches)),planning_at=data['planning_at'],
        created_at=run.get('created_at'),saved_at=result['computed_at'],version_confirmed_at=version['created_at'],horizon=data['horizon'],
        source_as_of={k:d['as_of'] for k,d in bundle['documents'].items()},source_receipt=version.get('source_sync_receipt'),
        run_state=run['state'],business_status=state['baseline']['interpretation'].get('business_status'),
        policy=data.get('foundation_projection',{}).get('policy'),audit=audit,
        order_links={r['id']:r['order_ref'] for r in c['tables']['demand_lines'] if r.get('order_ref')},
        totals=_summary(data,result),operation_count=len(result['operations']),result_hash=result['result_hash'],engine=result['engine'],
        selection_basis='ORIGINAL_SAVED_MONTH_CLOSE_BASELINE',execution_authorized=False)
    assets=dict(folder=str(folder.resolve()),manifest=manifest,version_sha256=digest(version),data_version_id=version['id'])
    initialize(store)
    with store.db() as db:
        old=db.execute('SELECT record,assets FROM history_runs WHERE scope_version_id=? AND plan_id=? AND run_id=?',(scope_version_id,identity['plan_id'],identity['run_id'])).fetchone()
        if old:
            prior=json.loads(old[0])
            if old[1]!=encoded(assets) or {k:v for k,v in prior.items() if k!='totals'}!={k:v for k,v in record.items() if k!='totals'}:raise ValueError('HISTORY_ARCHIVE_CHANGED')
            # Rebuild only the derived summary from the same authenticated run.
            # This never modifies saved run facts, identities, policy or audits.
            if prior['totals']!=record['totals']:db.execute('UPDATE history_runs SET record=? WHERE scope_version_id=? AND plan_id=? AND run_id=?',(encoded(record),scope_version_id,identity['plan_id'],identity['run_id']))
        else:db.execute('INSERT INTO history_runs VALUES(?,?,?,?,?)',(scope_version_id,identity['plan_id'],identity['run_id'],encoded(record),encoded(assets)))
    return record

def query(store,scope_version_id,*,start,end,grain='month',historical_plan_id=None,run_id=None,compare_plan_id=None,compare_run_id=None,page_size=50,offset=0,allowed_plan_ids=None,section='deliveries',actual_demand_id=None,observed_through=None):
    periods=buckets(start,end,grain,'Asia/Shanghai');initialize(store)
    if type(page_size)is not int or not 1<=page_size<=100 or type(offset)is not int or offset<0:raise ValueError('HISTORY_PAGE_INVALID')
    if section not in {'deliveries','resources','operations','audit'}:raise ValueError('HISTORY_RUN_SECTION_INVALID')
    with store.db() as db:records=[json.loads(r[0]) for r in db.execute('SELECT record FROM history_runs WHERE scope_version_id=?',(scope_version_id,))]
    if allowed_plan_ids is not None:records=[r for r in records if r['identity']['plan_id'] in allowed_plan_ids]
    cutoff=instant(observed_through) if observed_through is not None else None
    if cutoff is not None:records=[r for r in records if instant(r['planning_at'])<=cutoff]
    records.sort(key=lambda r:(instant(r['planning_at']),r['identity']['run_id']))
    selected=[r for r in records if instant(start)<=instant(r['planning_at'])<instant(end)]
    def public(r):
        p={k:v for k,v in r.items() if k not in {'audit','order_links','source_receipt','policy'}}
        p['policy']={k:r['policy'].get(k) for k in ('policy_id','scope','approval')} if r['policy'] else None
        p['audit']={k:r['audit'].get(k) for k in ('status','schema_version')}
        return p
    out=dict(schema_version='plan-history/1',buckets=[dict(b,run_count=sum(instant(b['start'])<=instant(r['planning_at'])<instant(b['end']) for r in records),status='RECORDED' if any(instant(b['start'])<=instant(r['planning_at'])<instant(b['end']) for r in records) else 'NO_RECORDED_PLAN') for b in periods],
        records=[public(r) for r in selected[offset:offset+page_size]],total=len(selected),next_offset=offset+page_size if offset+page_size<len(selected) else None,detail=None,comparison=None,worker_called=False,execution_authorized=False)
    if cutoff is not None:
        out['knowledge_cutoff']=cutoff.isoformat()
        for b in out['buckets']:
            b['knowledge_status']='NOT_YET_KNOWN' if instant(b['start'])>cutoff else 'PARTIAL_KNOWLEDGE' if instant(b['end'])>cutoff else 'KNOWN'
            if instant(b['end'])>cutoff:b['observed_through']=cutoff.isoformat()
    def resolve(pid,rid):
        r=next((r for r in records if r['identity']['plan_id']==pid and r['identity']['run_id']==rid),None)
        if r is None:raise ValueError('HISTORY_RUN_NOT_IN_SCOPE')
        return r
    if historical_plan_id is not None or run_id is not None:
        r=resolve(historical_plan_id,run_id)
        with store.db() as db:a=json.loads(db.execute('SELECT assets FROM history_runs WHERE scope_version_id=? AND plan_id=? AND run_id=?',(scope_version_id,historical_plan_id,run_id)).fetchone()[0])
        folder=Path(a['folder']);current=json.loads((folder/'run-manifest.json').read_text('utf8'))
        if current!=a['manifest']:raise ValueError('HISTORY_ARCHIVE_CHANGED')
        values=artifact_members(folder,current,MEMBERS);result=values['PlanResult.json'];data=values['canonical_input.json']
        member={'deliveries':'deliveries','resources':'resource_loads','operations':'operations'}.get(section)
        all_rows=result[member] if member else [];page=all_rows[offset:offset+page_size]
        record=public(r)
        if section=='audit':record.update(audit=r['audit'],policy=r['policy'],source_receipt=r['source_receipt'])
        demand_ids={d.get('demand_id') for d in page} if section=='deliveries' else set()
        out['detail']=dict(record=record,section=section,rows=page,deliveries=page if section=='deliveries' else [],total=len(all_rows),total_deliveries=len(result['deliveries']),
            next_offset=offset+page_size if offset+page_size<len(all_rows) else None,
            resources=page if section=='resources' else [],constraints=copy.deepcopy(result['assessment']) if section=='audit' else {},
            input_orders=[dict(id=d['id'],source_order_id=r['order_links'].get(d['id']),item_id=d['item_id'],uom=d.get('uom'),due_at=d['due_at']) for d in data['demand_lines'] if d['id'] in demand_ids],
            operations=page if section=='operations' else [],purchase_suggestions=result['purchase_suggestions'] if section=='audit' else [],
            actual_comparison_basis='LATER_OBSERVED_ACTUALS_NOT_HISTORICAL_INPUT')
        if actual_demand_id is not None:
            delivery=next((d for d in result['deliveries'] if d['demand_id']==actual_demand_id),None)
            order_id=r['order_links'].get(actual_demand_id)
            if delivery is None or not order_id:raise ValueError('HISTORY_EXACT_ACTUAL_LINK_NOT_AVAILABLE')
            out['detail']['actual_link']=dict(original_plan=r['identity'],original_delivery=delivery,source_order_id=order_id,
                association_basis='ORIGINAL_CONFIRMED_CANONICAL_ORDER_REF',hindsight=True,reintroduced_to_historical_input=False)
        if compare_plan_id is not None or compare_run_id is not None:
            other=resolve(compare_plan_id,compare_run_id)
            if any(r[k]!=other[k] for k in ('enterprise_id','branch_id')):raise ValueError('HISTORY_COMPARISON_SCOPE_MISMATCH')
            out['comparison']=dict(left=public(r),right=public(other),same_data=r['identity']['data_version_id']==other['identity']['data_version_id'],same_policy=r['policy']==other['policy'],same_horizon=r['horizon']==other['horizon'],interpretation='BUSINESS_STATE_CHANGE_NOT_ALGORITHM_GAIN')
    elif compare_plan_id or compare_run_id:raise ValueError('HISTORY_COMPARISON_LEFT_REQUIRED')
    return out
