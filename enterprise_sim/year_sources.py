"""Visible annual source selection; business commits remain owned by YearLive.

BASE view revisions identify an as-of source view, never a business commit.
INTERVENTION CURRENT retains the original Live business revision contract.
"""
import copy
import json
import os
import re
import threading
import uuid
from bisect import bisect_right, insort
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from .live import TABLES, LiveEnterprise, digest, instant
from .model import SimulationError
from .sources import SYSTEMS, project_sources


ZONE=ZoneInfo('Asia/Shanghai')
VIEW_SEMANTICS='SOURCE_AS_OF_EPOCH_SECONDS_NOT_BUSINESS_COMMIT'
QUERY_SEMANTICS='SOURCE_QUERY_INSTANT_OCCURRED_AND_KNOWN_NOT_PERIOD_FILTER'


class YearSourceViews:
    def __init__(self,owner):
        self.owner=owner
        self.mode=owner.bootstrap.get('year_mode')
        if self.mode not in {'BASE','APS_INTERVENTION'}:raise SimulationError('YEAR_SOURCE_OWNER_REQUIRED')
        self.root=owner.root/'source-views';self.root.mkdir(exist_ok=True)
        self.selection_path=owner.root/'source-selection.json'
        self.lock=threading.RLock()
        # These anchors are derived from the authenticated Owner, never loaded
        # from the cache file. A new process must establish them again.
        self._verified_views={}
        self._source_indexes={}
        self._source_event_times={system:[] for system in SYSTEMS}
        self._source_event_count=0
        self._source_deltas={}

    def source_checkpoint(self,*,read_token,revision=None):
        """Authenticated source watermark, without constructing source views.

        The business Owner still validates its immutable history. This endpoint
        does not crop a full view or claim to certify an unseen source payload.
        """
        self.owner._authorize(read_token,'read')
        with self.lock,self.owner._thread_lock:
            # Retain the original history/head verification, while avoiding
            # status()'s complete World snapshot and operator-only action list.
            self.owner._reload()
            head={k:self.owner.current[k] for k in ('revision','as_of')};selection=self._selection()
            if revision is not None and (type(revision) is not int or revision<0):raise SimulationError('UNKNOWN_VIEW_REVISION')
            business=self.mode=='APS_INTERVENTION' and ((revision is None and selection['checkpoint_id']=='CURRENT') or
                (revision is not None and revision<=head['revision']))
            if business:
                selected=head['revision'] if revision is None else revision
                record=self.owner.current if selected==head['revision'] else self.owner._read_commit(selected)
                as_of=record['as_of'];semantics='BUSINESS_COMMIT'
            else:
                if revision is not None and not int(instant(self.owner.definition['origin']).timestamp())<=revision<=int(instant(head['as_of']).timestamp()):raise SimulationError('UNKNOWN_VIEW_REVISION')
                as_of=(datetime.fromtimestamp(revision,timezone.utc).astimezone(ZONE).isoformat() if revision is not None else
                    self._cutoff(head,checkpoint_id='CURRENT') if selection['checkpoint_id']=='CURRENT' else selection['as_of'])
                as_of=self._cutoff(head,as_of=as_of);selected=int(instant(as_of).timestamp());semantics=VIEW_SEMANTICS
            return dict(schema='source-checkpoint/1',branch_id=self.owner.branch_id,enterprise_id=self.owner.definition['enterprise_id'],
                revision=selected,as_of=as_of,revision_semantics=semantics,source_cursor=self.owner.branch_id+':'+str(selected),
                declarations=copy.deepcopy(self.owner.bootstrap['declarations']),source_access='AUTHENTICATED_OWNER_CHECKPOINT',
                incremental_support='SALES_ORDER_AND_PURCHASE_ETA_WITH_EXPLICIT_DOMAIN_FALLBACK')

    def _remember(self,view):
        # Only a view returned by the original authenticated full Owner becomes
        # a trusted parent. Cold processes must establish that anchor again.
        self._source_indexes[view['revision']]=dict(as_of=view['as_of'],headers={s:{k:copy.deepcopy(v) for k,v in d.items() if k not in {'tables','event_refs'}} for s,d in view['documents'].items()},
            rows={s:{t:{r['id']:(copy.deepcopy(r),copy.deepcopy(d['event_refs'][t][r['id']])) for r in rr} for t,rr in d['tables'].items()} for s,d in view['documents'].items()})
        return view

    def _new_visible(self,system,previous,until):
        events=self.owner.world.events
        if self._source_event_count>len(events):
            self._source_event_times={s:[] for s in SYSTEMS};self._source_event_count=0
        for index in range(self._source_event_count,len(events)):
            event=events[index]
            for name in SYSTEMS:
                if name in event['known_at']:
                    insort(self._source_event_times[name],(max(instant(event['occurred_at']),instant(event['known_at'][name])).timestamp(),index))
        self._source_event_count=len(events)
        times=self._source_event_times[system]
        start=bisect_right(times,(instant(previous).timestamp(),float('inf')))
        end=bisect_right(times,(instant(until).timestamp(),float('inf')))
        return [event for index,event in sorted(((index,events[index]) for _,index in times[start:end]),key=lambda pair:(instant(pair[1]['occurred_at']),pair[0]))]

    def _local_deltas(self,point,previous_as_of,read_token):
        key=(point['revision'],previous_as_of)
        if key in self._source_deltas:return copy.deepcopy(self._source_deltas[key])
        previous=next((v for v in self._source_indexes.values() if instant(v['as_of'])==instant(previous_as_of)),None)
        if previous is None:
            # BASE revisions encode the exact cutoff. INTERVENTION revisions
            # are business commits; the original Owner identifies that parent.
            if self.mode=='BASE':old_revision=int(instant(previous_as_of).timestamp())
            else:
                candidates=[r for r in range(self.owner.current['revision']+1) if self.owner._read_commit(r)['as_of']==previous_as_of]
                if len(candidates)!=1:return None
                old_revision=candidates[0]
            cache_key=('asof-' if self.mode=='BASE' else 'business-')+str(old_revision)
            if not (self.root/(cache_key+'.json')).is_file():return None
            self.read_view(read_token=read_token,revision=old_revision)
            previous=self._source_indexes[old_revision]
        newly={s:self._new_visible(s,previous_as_of,point['as_of']) for s in SYSTEMS}
        if any(e['kind'] not in {'SalesOrderChanged','PurchaseETAChanged'} for ee in newly.values() for e in ee):return None
        # Same identity maps are shared; only changed rows are copied. There is
        # no full event projection or full payload copy in the normal path.
        rows={s:{t:dict(rr) for t,rr in tt.items()} for s,tt in previous['rows'].items()}
        changed={s:{} for s in SYSTEMS}
        for system,events in newly.items():
            if system!='ERP' and events:return None
            for event in events:
                payload=event['payload'];table='sales_orders' if event['kind']=='SalesOrderChanged' else 'purchase_orders'
                identifier=payload['order_id' if table=='sales_orders' else 'po_id']
                before=rows[system].get(table,{}).get(identifier)
                if before is None or instant(event['occurred_at'])<instant(before[0]['business_occurred_at']):return None
                row,refs=copy.deepcopy(before)
                row.update(copy.deepcopy(payload) if table=='sales_orders' else {'eta':payload['eta']})
                row.update(business_occurred_at=event['occurred_at'],source_known_at=max(row['source_known_at'],event['known_at'][system],key=instant),aps_read_at=point['as_of'])
                row.pop('source_record_revision',None)
                row['source_record_revision']=digest({k:v for k,v in row.items() if k!='aps_read_at'})
                refs=list(dict.fromkeys(refs+[event['event_id']]))
                rows[system][table][identifier]=(row,refs);changed[system].setdefault(table,{})[identifier]=(row,refs)
        documents={};headers={}
        for system in SYSTEMS:
            header=dict(previous['headers'][system],as_of=point['as_of'],knowledge_cutoff=point['as_of'],source_revision=point['revision'],source_cursor=point['source_cursor'])
            visible_known=[r['source_known_at'] for rr in changed[system].values() for r,refs in rr.values()]
            header['latest_source_known_at']=max(([header['latest_source_known_at']] if header.get('latest_source_known_at') else [])+visible_known,default=None,key=instant)
            header.update(mode='INCREMENTAL_UPSERT',absence_semantics='NO_CHANGE',previous_as_of=previous_as_of,
                tables={t:[r for r,refs in changed[system].get(t,{}).values()] for t in TABLES[system]},
                event_refs={t:{i:refs for i,(r,refs) in rr.items()} for t,rr in changed[system].items()},
                source_delta_mode='AUTHENTICATED_SOURCE_OBJECT_CHANGES',source_delta_event_count=len(newly[system]))
            documents[system]=header
            headers[system]={k:v for k,v in header.items() if k not in {'tables','event_refs','previous_as_of','source_delta_mode','source_delta_event_count'}}
            headers[system].update(mode='FULL_SNAPSHOT',absence_semantics='MISSING_REVIEW_NOT_CANCELLATION')
        self._source_indexes[point['revision']]=dict(as_of=point['as_of'],headers=headers,rows=rows)
        self._source_deltas[key]=copy.deepcopy(documents)
        return documents

    def _selection(self):
        if not self.selection_path.exists():return dict(schema='year-source-selection/1',branch_id=self.owner.branch_id,checkpoint_id='CURRENT',requests={})
        value=json.loads(self.selection_path.read_text('utf8'))
        if value.get('schema')!='year-source-selection/1' or value.get('branch_id')!=self.owner.branch_id:
            raise SimulationError('SOURCE_BRANCH_MISMATCH')
        return value

    def _write_selection(self,value):
        path=self.selection_path.with_name(self.selection_path.name+'.'+uuid.uuid4().hex+'.tmp')
        with path.open('x',encoding='utf8') as handle:
            json.dump(value,handle,ensure_ascii=False,sort_keys=True,separators=(',',':'));handle.flush();os.fsync(handle.fileno())
        os.replace(path,self.selection_path)

    def _cutoff(self,head,*,checkpoint_id=None,as_of=None):
        if checkpoint_id and as_of:raise SimulationError('SOURCE_CHECKPOINT_SELECTION_INVALID')
        if checkpoint_id=='CURRENT':return instant(head['as_of']).isoformat()
        if checkpoint_id and re.fullmatch(r'CP-[a-f0-9]{32}',checkpoint_id):
            path=self.owner.root/'checkpoints'/(checkpoint_id+'.json')
            if not path.is_file():raise SimulationError('SOURCE_CHECKPOINT_NOT_FOUND')
            point=json.loads(path.read_text('utf8'))
            if point.get('branch_id')!=self.owner.branch_id:raise SimulationError('SOURCE_BRANCH_MISMATCH')
            if self.owner._read_commit(point['revision'])['sha256']!=point.get('commit_sha256'):
                raise SimulationError('CHECKPOINT_COMMIT_MISMATCH')
            as_of=point['as_of']
        elif checkpoint_id:
            if not re.fullmatch(r'20[0-9]{2}-(0[1-9]|1[0-2])',checkpoint_id):raise SimulationError('SOURCE_CHECKPOINT_NOT_FOUND')
            year,month=map(int,checkpoint_id.split('-'))
            following=datetime(year+1,1,1,tzinfo=ZONE) if month==12 else datetime(year,month+1,1,tzinfo=ZONE)
            as_of=following.isoformat()
        try:cutoff=instant(as_of)
        except (TypeError,ValueError,AttributeError):raise SimulationError('SOURCE_CHECKPOINT_SELECTION_INVALID') from None
        if cutoff.microsecond:raise SimulationError('SOURCE_CHECKPOINT_PRECISION_UNSUPPORTED')
        if cutoff<instant(self.owner.definition['origin']):raise SimulationError('SOURCE_CHECKPOINT_BEFORE_ORIGIN')
        if cutoff>instant(head['as_of']):raise SimulationError('SOURCE_CHECKPOINT_AFTER_BUSINESS_HEAD')
        return cutoff.isoformat()

    def _cache(self,key,build,input_evidence):
        path=self.root/(key+'.json')
        declarations_sha=digest(self.owner.bootstrap['declarations'])
        anchor=(key,declarations_sha,input_evidence)
        if path.exists():
            record=json.loads(path.read_text('utf8'));view=record['view']
            if (record.get('sha256')!=digest(view) or record.get('declarations_sha256')!=declarations_sha or
                    record.get('input_evidence_sha256')!=input_evidence or view.get('branch_id')!=self.owner.branch_id):
                raise SimulationError('YEAR_SOURCE_CACHE_CHANGED')
            expected=self._verified_views.get(anchor)
            if expected is None:
                expected=digest(build())
            if record['sha256']!=expected:
                raise SimulationError('YEAR_SOURCE_CACHE_CHANGED')
            self._verified_views[anchor]=expected
            return view
        view=build();record=dict(view=view,sha256=digest(view),declarations_sha256=declarations_sha,input_evidence_sha256=input_evidence)
        try:LiveEnterprise._atomic_new(path,record)
        except (SimulationError,FileExistsError):
            if not path.exists() or json.loads(path.read_text('utf8'))!=record:raise SimulationError('YEAR_SOURCE_CACHE_CHANGED') from None
        self._verified_views[anchor]=record['sha256']
        return copy.deepcopy(view)

    def _at(self,as_of):
        revision=int(instant(as_of).timestamp())
        evidence=digest([event for event in self.owner.world.events if instant(event['occurred_at'])<=instant(as_of)])
        def build():
            documents={}
            for system in SYSTEMS:
                document=project_sources(self.owner.definition,self.owner.world.events,as_of,system)
                for table in TABLES[system]:
                    document['tables'].setdefault(table,[]);document['event_refs'].setdefault(table,{})
                for rows in document['tables'].values():
                    for row in rows:row['source_record_revision']=digest({k:v for k,v in row.items() if k!='aps_read_at'})
                document.update(branch_id=self.owner.branch_id,source_id=self.owner.definition['enterprise_id']+'/'+self.owner.branch_id+'/'+system,
                    source_revision=revision,source_cursor=self.owner.branch_id+':'+str(revision),knowledge_cutoff=as_of,
                    source_revision_semantics=VIEW_SEMANTICS,
                    latest_source_known_at=max((row['source_known_at'] for rows in document['tables'].values() for row in rows),default=None,key=instant))
                documents[system]=document
            return dict(branch_id=self.owner.branch_id,enterprise_id=self.owner.definition['enterprise_id'],revision=revision,
                revision_semantics=VIEW_SEMANTICS,as_of=as_of,documents=documents,
                declarations=copy.deepcopy(self.owner.bootstrap['declarations']),source_cursor=self.owner.branch_id+':'+str(revision))
        return self._cache('asof-'+str(revision),build,evidence)

    def read_view(self,*,read_token,revision=None):
        self.owner._authorize(read_token,'read')
        with self.lock,self.owner._thread_lock:
            head=self.owner.status();selection=self._selection()
            if revision is not None and (type(revision) is not int or revision<0):raise SimulationError('UNKNOWN_VIEW_REVISION')
            if self.mode=='APS_INTERVENTION' and ((revision is None and selection['checkpoint_id']=='CURRENT') or
                                                 (revision is not None and revision<=head['revision'])):
                selected=head['revision'] if revision is None else revision
                return self._remember(self._cache('business-'+str(selected),lambda:self.owner.read_view(read_token=read_token,revision=selected),
                                   self.owner._read_commit(selected)['sha256']))
            if revision is not None and not int(instant(self.owner.definition['origin']).timestamp())<=revision<=int(instant(head['as_of']).timestamp()):
                raise SimulationError('UNKNOWN_VIEW_REVISION')
            as_of=(datetime.fromtimestamp(revision,timezone.utc).astimezone(ZONE).isoformat() if revision is not None else
                   self._cutoff(head,checkpoint_id='CURRENT') if selection['checkpoint_id']=='CURRENT' else selection['as_of'])
            return self._remember(self._at(self._cutoff(head,as_of=as_of)))

    def verify_view(self,revision,*,read_token):
        view=self.source_checkpoint(read_token=read_token)
        if view['revision']!=revision:raise SimulationError('SOURCE_CHANGED_AFTER_PREVIEW')
        return dict(revision=revision,as_of=view['as_of'])

    def select_source(self,payload,*,operator_token,expected_revision,idempotency_key):
        self.owner._authorize(operator_token,'operator')
        if not isinstance(idempotency_key,str) or not idempotency_key or len(idempotency_key)>200:raise SimulationError('IDEMPOTENCY_KEY_REQUIRED')
        if payload.get('branch_id',self.owner.branch_id)!=self.owner.branch_id:raise SimulationError('SOURCE_BRANCH_MISMATCH')
        if set(payload)-{'branch_id','checkpoint_id','as_of'}:raise SimulationError('SOURCE_CHECKPOINT_SELECTION_INVALID')
        signature=digest(dict(branch_id=self.owner.branch_id,payload=payload,expected_revision=expected_revision))
        with self.lock,self.owner._locked():
            selection=self._selection();old=selection['requests'].get(idempotency_key)
            if old:
                if old['signature']!=signature:raise SimulationError('IDEMPOTENCY_CONFLICT')
                return copy.deepcopy(old['response'])
            head=self.owner.status()
            if type(expected_revision) is not int or expected_revision!=head['revision']:raise SimulationError('STALE_WORLD_REVISION')
            as_of=self._cutoff(head,checkpoint_id=payload.get('checkpoint_id'),as_of=payload.get('as_of'))
            point=payload.get('checkpoint_id') or 'AS_OF'
            selection.update(checkpoint_id=point,as_of=as_of)
            response=dict(branch_id=self.owner.branch_id,business_revision=head['revision'],business_as_of=head['as_of'],
                          checkpoint_id=point,source_as_of=as_of,business_head_changed=False)
            selection['requests'][idempotency_key]=dict(signature=signature,response=copy.deepcopy(response))
            self._write_selection(selection)
            return response

    def changes(self,system,*,read_token,revision,previous_as_of):
        if system not in SYSTEMS:raise SimulationError('UNKNOWN_SYSTEM')
        point=self.source_checkpoint(read_token=read_token,revision=revision)
        if instant(previous_as_of)>instant(point['as_of']):raise SimulationError('INCREMENTAL_CURSOR_GAP')
        with self.lock,self.owner._thread_lock:
            local=self._local_deltas(point,previous_as_of,read_token)
            if local is not None:return copy.deepcopy(local[system])
        after=self.read_view(read_token=read_token,revision=revision)
        if instant(previous_as_of)>instant(after['as_of']):raise SimulationError('INCREMENTAL_CURSOR_GAP')
        previous=[]
        for path in self.root.glob('*.json'):
            value=json.loads(path.read_text('utf8'))
            if instant(value['view']['as_of'])==instant(previous_as_of):
                previous.append(self.read_view(read_token=read_token,revision=value['view']['revision']))
        if not previous:raise SimulationError('INCREMENTAL_CURSOR_GAP')
        before=previous[0]['documents'][system];document=copy.deepcopy(after['documents'][system]);tables={}
        for table,rows in document['tables'].items():
            old={row['id']:row for row in before['tables'].get(table,[])}
            business=lambda row:{k:v for k,v in row.items() if k!='aps_read_at'}
            tables[table]=[row for row in rows if row['id'] not in old or business(row)!=business(old[row['id']])]
        document.update(mode='INCREMENTAL_UPSERT',absence_semantics='NO_CHANGE',previous_as_of=previous_as_of,tables=tables,
            event_refs={table:{row['id']:document['event_refs'][table][row['id']] for row in rows} for table,rows in tables.items()},
            source_delta_mode='FULL_SOURCE_DOMAIN_FALLBACK',source_delta_fallback_reason='EVENT_DOMAIN_OR_HISTORICAL_PARENT_REQUIRES_FULL_PROJECTION')
        return document

    def checkpoints(self,*,head=None):
        head=self.owner.status() if head is None else head
        first=instant(self.owner.definition['origin']).replace(day=1,hour=0,minute=0,second=0)
        end=instant(self.owner.definition['end']);points=[]
        while first<end:
            following=first.replace(year=first.year+1,month=1) if first.month==12 else first.replace(month=first.month+1)
            cutoff=following
            if cutoff<end:
                points.append(dict(id=first.strftime('%Y-%m'),as_of=cutoff.isoformat(),available=cutoff<=instant(head['as_of'])))
            first=following
        for path in sorted((self.owner.root/'checkpoints').glob('CP-*.json')):
            point=json.loads(path.read_text('utf8'))
            cutoff=self._cutoff(head,checkpoint_id=point['checkpoint_id'])
            points.append(dict(id=point['checkpoint_id'],label=point.get('label',point['checkpoint_id']),as_of=cutoff,
                               business_revision=point['revision'],available=True))
        return dict(branch_id=self.owner.branch_id,business_as_of=head['as_of'],selected=self._selection()['checkpoint_id'],
                    month_checkpoint_semantics='FOLLOWING_MONTH_START_AS_OF_QUERY',as_of_semantics=QUERY_SEMANTICS,
                    checkpoints=[dict(id='CURRENT',as_of=head['as_of'],available=True)]+points)

    def require_current_adoption(self):
        if self.mode!='APS_INTERVENTION':raise SimulationError('YEAR_BASELINE_ROUTINE_ONLY')
        if self._selection()['checkpoint_id']!='CURRENT':raise SimulationError('HISTORICAL_SOURCE_ADOPTION_FORBIDDEN')

    @staticmethod
    def source_state(view):
        erp,wms,mes=(view['documents'][system]['tables'] for system in ['ERP','WMS','MES'])
        return dict(orders=erp.get('sales_orders',[]),purchase_orders=erp.get('purchase_orders',[]),
            lots=wms.get('lots',[]),finished_goods=wms.get('finished_goods',[]),manufacturing_tasks=mes.get('manufacturing_tasks',[]),
            shipments=erp.get('shipments',[]),operations=mes.get('operations',[]))

    def status(self,*,read_token=''):
        head=self.owner.status()
        # Source-only credentials remain required at HTTP boundaries. The status
        # endpoint is an authenticated Operator view, not a new read-token bypass.
        selected=self._selection();as_of=self._cutoff(head,checkpoint_id='CURRENT') if selected['checkpoint_id']=='CURRENT' else selected['as_of']
        current=self.mode=='APS_INTERVENTION' and selected['checkpoint_id']=='CURRENT'
        source_revision=head['revision'] if current else int(instant(as_of).timestamp())
        head.update(year_mode=self.mode,business_revision=head['revision'],business_as_of=head['as_of'],
            selected_source=dict(checkpoint_id=selected['checkpoint_id'],revision=source_revision,as_of=as_of,
                                 revision_semantics='BUSINESS_COMMIT' if current else VIEW_SEMANTICS),
            adoption_available=current,checkpoints=self.checkpoints(head=head)['checkpoints'])
        return head

    def history(self,*,read_token,checkpoint_id=None,as_of=None):
        self.owner._authorize(read_token,'read');head=self.owner.status()
        cutoff=self._cutoff(head,checkpoint_id=checkpoint_id,as_of=as_of) if checkpoint_id or as_of else self.status()['selected_source']['as_of']
        view=self._at(cutoff)
        return dict(branch_id=self.owner.branch_id,as_of=view['as_of'],as_of_semantics=QUERY_SEMANTICS,
            source_revision=view['revision'],source_state=self.source_state(view),
            systems=[dict(system=system,as_of=document['as_of'],latest_source_known_at=document.get('latest_source_known_at'),
                          records=sum(len(rows) for rows in document['tables'].values())) for system,document in view['documents'].items()])

    def monthly_summary(self,*,read_token):
        self.owner._authorize(read_token,'read');cutoff=instant(self.status()['selected_source']['as_of']);summary={}
        origin=instant(self.owner.definition['origin']);fields=('physical','available','pending','held');sources={}
        raw_items={item for product in self.owner.definition['products'].values() for item in product['bom']}
        def source_at(point,system):
            key=(point,system)
            if key not in sources:
                sources[key]=project_sources(self.owner.definition,self.owner.world.events,point.isoformat(),system)['tables']
            return sources[key]
        def inventory_at(point):
            values={item:{field:Decimal(0) for field in fields} for item in raw_items}
            for lot in source_at(point,'WMS').get('lots',[]):
                item=lot['item_id']
                if item not in values:continue
                if lot['unit']!=self.owner.definition['items'][item]['unit']:raise SimulationError('UNIT_MISMATCH')
                for field in fields:values[item][field]+=Decimal(lot[field])
            return values
        first=origin.replace(day=1,hour=0,minute=0,second=0,microsecond=0)
        while first<cutoff:
            following=first.replace(year=first.year+1,month=1) if first.month==12 else first.replace(month=first.month+1)
            opening=max(first,origin);closing=min(following,cutoff);month=first.strftime('%Y-%m')
            before,after=inventory_at(opening),inventory_at(closing)
            raw={item:dict(item_id=item,uom=self.owner.definition['items'][item]['unit'],
                opening={field:str(before[item][field]) for field in fields},
                ending={field:str(after[item][field]) for field in fields},
                delta={field:str(after[item][field]-before[item][field]) for field in fields}) for item in sorted(raw_items)}
            open_demand=sum((Decimal(row['quantity'])-Decimal(row['shipped'])
                             for row in source_at(closing,'ERP').get('sales_orders',[])),Decimal(0))
            summary[month]=dict(month=month,orders=0,demand_quantity='0',shipped_quantity='0',manufactured_quantity='0',
                purchase_by_item={},received_by_item={},exceptions=0,opening_as_of=opening.isoformat(),closing_as_of=closing.isoformat(),
                ending_open_demand_quantity=str(open_demand),raw_inventory_by_item=raw)
            first=following
        authority={'SalesOrderCreated':'ERP','PurchaseOrderCreated':'ERP','ShipmentPosted':'ERP','GoodsReceived':'WMS',
                   'FinishedGoodsReceived':'MES','ResourceUnavailable':'MES','QCHeld':'WMS','SalesOrderChanged':'ERP','PurchaseETAChanged':'ERP'}
        for event in self.owner.world.events:
            system=authority.get(event['kind'])
            if not system or system not in event['known_at'] or instant(event['occurred_at'])>cutoff or instant(event['known_at'][system])>cutoff:continue
            month=instant(event['occurred_at']).strftime('%Y-%m')
            if month not in summary or instant(event['known_at'][system])>instant(summary[month]['closing_as_of']):continue
            record=summary[month]
            p=event['payload'];kind=event['kind']
            def add(key):record[key]=str(Decimal(record[key])+Decimal(p['quantity']))
            if kind=='SalesOrderCreated':record['orders']+=1;add('demand_quantity')
            elif kind=='ShipmentPosted':add('shipped_quantity')
            elif kind=='FinishedGoodsReceived':add('manufactured_quantity')
            elif kind in {'PurchaseOrderCreated','GoodsReceived'}:
                bucket=record['purchase_by_item' if kind=='PurchaseOrderCreated' else 'received_by_item'];item=p['item_id']
                value=bucket.setdefault(item,dict(quantity='0',uom=p['unit']));value['quantity']=str(Decimal(value['quantity'])+Decimal(p['quantity']))
            else:record['exceptions']+=1
        return dict(branch_id=self.owner.branch_id,as_of=cutoff.isoformat(),months=[summary[key] for key in sorted(summary)],
                    quantity_basis='KNOWN_SOURCE_EVENTS_CREATED_ORDERS_AND_ACTUAL_RECEIPTS_OUTPUT_SHIPMENTS')
