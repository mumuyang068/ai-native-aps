"""Pure projections of permitted source observations, no simulator or planning calls."""
from collections import defaultdict
from decimal import Decimal,InvalidOperation
from .history_buckets import buckets,instant


def quantity(value):
    try:
        q=Decimal(str(value))
        if not q.is_finite() or q<0:raise ValueError()
        return q
    except (ValueError,InvalidOperation):raise ValueError('HISTORY_QUANTITY_INVALID') from None


def text(value):return format(value.normalize(),'f')
def dimension(row):return tuple(row.get(k,'') for k in ('item_id','location_id','uom','customer_id'))
def _ordered(rows):return sorted(rows,key=lambda r:(instant(r['occurred_at']),r.get('ordinal',0),r['event_id']))

def apply_demand(state,e):
    p=e['payload'];oid=p['order_id'];kind=e['kind'];revision=Decimal(0)
    if kind=='SalesOrderCreated':
        if oid in state:raise ValueError('HISTORY_ORDER_DUPLICATE')
        state[oid]=dict(quantity=quantity(p['quantity']),shipped=Decimal(0),due=p['due'])
    else:
        if oid not in state:raise ValueError('HISTORY_ORDER_DEPENDENCY_MISSING')
        s=state[oid]
        if kind=='SalesOrderChanged':
            if 'quantity' in p:revision=quantity(p['quantity'])-s['quantity'];s['quantity']+=revision
            if 'due' in p:s['due']=p['due']
            if p.get('status')=='CANCELLED':raise ValueError('HISTORY_CANCELLATION_CONTRACT_REQUIRED')
        elif kind=='ShipmentPosted':s['shipped']+=quantity(p['quantity'])
        else:raise ValueError('HISTORY_DEMAND_KIND_UNSUPPORTED')
        if s['shipped']>s['quantity']:raise ValueError('HISTORY_FULFILLMENT_CONFLICT')
    return revision

def apply_inventory(lots,e):
    p=e['payload'];q=quantity(p['quantity']);kind=e['kind'];s=lots.setdefault(e['lot_id'],dict(physical=Decimal(0),pending=Decimal(0),held=Decimal(0),available=Decimal(0)))
    if kind=='GoodsReceived':s['physical']+=q;s['pending']+=q
    elif kind=='FinishedGoodsReceived':s['physical']+=q;s['available']+=q
    elif kind in {'MaterialIssued','ShipmentPosted'}:s['physical']-=q;s['available']-=q
    elif kind in {'QCReleased','QCHeld'}:
        origin=p.get('from_status','pending')
        if origin not in {'pending','held','available'}:raise ValueError('HISTORY_QC_PARTITION_UNSUPPORTED')
        s[origin]-=q;s['available' if kind=='QCReleased' else 'held']+=q
    else:raise ValueError('HISTORY_INVENTORY_KIND_UNSUPPORTED')
    if any(v<0 for v in s.values()) or s['physical']!=s['pending']+s['held']+s['available']:raise ValueError('HISTORY_INVENTORY_CONFLICT')

def balance_records(rows,domain,metric,boundary,*,include_boundary=False):
    """Actual contributing orders/lots, with complete reconstruction references."""
    state={};refs=defaultdict(list);latest={}
    for e in _ordered(rows):
        if instant(e['occurred_at'])>boundary or not include_boundary and instant(e['occurred_at'])==boundary:continue
        key=e['order_id'] if domain=='demand' else e['lot_id']
        (apply_demand if domain=='demand' else apply_inventory)(state,e)
        refs[key].append(dict(event_id=e['event_id'],occurred_at=e['occurred_at'],source_known_at=e['source_known_at'],source_records=e['source_records']))
        latest[key]=e
    out=[]
    for key,s in state.items():
        if domain=='demand':
            value=s['quantity'] if metric=='effective_demand' else s['quantity']-s['shipped']
            if metric=='overdue' and instant(s['due'])>=boundary:continue
        else:value=s.get(metric.removeprefix('opening_').removeprefix('ending_'))
        if value is None or value==0:continue
        e=latest[key]
        out.append(dict(object_id=key,item_id=e['item_id'],location_id=e['location_id'],uom=e['uom'],order_id=e['order_id'],lot_id=e['lot_id'],
            contribution=text(value),balance={k:text(v) if isinstance(v,Decimal) else v for k,v in s.items()},source_events=refs[key][:8],source_event_count=len(refs[key]),source_events_truncated=len(refs[key])>8,
            status=('COMPLETED' if s['quantity']==s['shipped'] else 'PARTIAL' if s['shipped'] else 'OPEN') if domain=='demand' else 'OBSERVED_PARTITIONS'))
    return out


def demand_report(rows,start,end,grain,timezone,*,knowledge_cutoff=None):
    periods=buckets(start,end,grain,timezone);groups=defaultdict(list)
    for r in rows:groups[dimension(r)].append(r)
    output=[];totals=defaultdict(lambda:defaultdict(Decimal))
    for dim,events in sorted(groups.items()):
        events=_ordered(events);state={};cells=[];index=0
        def apply(e):
            return apply_demand(state,e)
        def balance():return sum((s['quantity']-s['shipped'] for s in state.values()),Decimal(0))
        for b in periods:
            a,z=instant(b['start']),instant(b['end'])
            while index<len(events) and instant(events[index]['occurred_at'])<a:apply(events[index]);index+=1
            opening=balance();new=Decimal(0);revision=Decimal(0);shipped=Decimal(0);count=0
            while index<len(events) and instant(events[index]['occurred_at'])<z:
                e=events[index];p=e['payload'];kind=e['kind'];revision+=apply(e)
                if kind=='SalesOrderCreated':count+=1;new+=quantity(p['quantity'])
                if kind=='ShipmentPosted':shipped+=quantity(p['quantity'])
                index+=1
            ending=balance();effective=sum((s['quantity'] for s in state.values()),Decimal(0))
            overdue=sum((s['quantity']-s['shipped'] for s in state.values() if instant(s['due'])<min(z,knowledge_cutoff or z)),Decimal(0))
            cells.append(dict(bucket_id=b['id'],new_orders=count,new_quantity=text(new),revision_quantity=text(revision),shipped=text(shipped),
                opening_open=text(opening),ending_open=text(ending),overdue=text(overdue),effective_demand=text(effective),
                conservation='PASS' if ending==opening+new+revision-shipped else 'FAIL',status='KNOWN'))
        output.append(dict(dimension=dict(zip(('item_id','location_id','uom','customer_id'),dim)),cells=cells))
        t=totals[dim[2]]
        for metric in ('new_quantity','revision_quantity','shipped'):t[metric]+=sum((Decimal(c[metric]) for c in cells),Decimal(0))
        t['new_orders']+=sum(c['new_orders'] for c in cells)
        for metric in ('opening_open','ending_open','effective_demand','overdue'):t[metric]+=Decimal(cells[0 if metric=='opening_open' else -1][metric])
    return dict(buckets=periods,groups=output,totals=[dict(uom=unit,**{k:text(v) for k,v in t.items()}) for unit,t in sorted(totals.items())])


def inventory_report(rows,start,end,grain,timezone):
    periods=buckets(start,end,grain,timezone);groups=defaultdict(list)
    for r in rows:groups[tuple(r.get(k,'') for k in ('item_id','location_id','uom'))].append(r)
    output=[]
    for dim,events in sorted(groups.items()):
        events=_ordered(events);lots=defaultdict(lambda:dict(physical=Decimal(0),pending=Decimal(0),held=Decimal(0),available=Decimal(0)));index=0;cells=[]
        def apply(e):
            apply_inventory(lots,e)
        def total(k):return sum((s[k] for s in lots.values()),Decimal(0))
        for b in periods:
            a,z=instant(b['start']),instant(b['end'])
            while index<len(events) and instant(events[index]['occurred_at'])<a:apply(events[index]);index+=1
            opening=total('physical');incoming=Decimal(0);outgoing=Decimal(0);changed=set()
            while index<len(events) and instant(events[index]['occurred_at'])<z:
                e=events[index];apply(e);q=quantity(e['payload']['quantity']);changed.add(e['lot_id'])
                if e['kind'] in {'GoodsReceived','FinishedGoodsReceived'}:incoming+=q
                if e['kind'] in {'MaterialIssued','ShipmentPosted'}:outgoing+=q
                index+=1
            ending=total('physical')
            cells.append(dict(bucket_id=b['id'],opening_physical=text(opening),physical_in=text(incoming),physical_out=text(outgoing),adjustment='0',
                **{'ending_'+k:text(total(k)) for k in ('physical','pending','held','available')},batches=len(changed),
                conservation='PASS' if ending==opening+incoming-outgoing else 'FAIL',status='KNOWN'))
        output.append(dict(dimension=dict(zip(('item_id','location_id','uom'),dim)),cells=cells))
    return dict(buckets=periods,groups=output)
