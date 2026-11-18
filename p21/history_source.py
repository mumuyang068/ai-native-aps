"""Normalize authorized source trace/postimages, never simulator world state."""
import copy,json,hashlib
from .history_buckets import instant
from .history_model import quantity,text

KINDS={'SalesOrderCreated','SalesOrderChanged','ShipmentPosted','GoodsReceived','QCReleased','QCHeld','MaterialIssued','FinishedGoodsReceived','ManufacturingTaskCreated','OperationStarted','OperationCompleted'}
INFO_ONLY_KINDS={'ManufacturingOrderDraftCreated','ManufacturingOrderConfirmed','ManufacturingOrderRescheduled'}
KINDS|=INFO_ONLY_KINDS

def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def prepare(capture):
    if capture.get('schema')!='source-history-capture/1' or capture.get('observation_basis')!='ORIGINAL_AUTHORIZED_VISIBLE_SOURCE_TRACE_AND_IMMUTABLE_SOURCE_ROWS':raise ValueError('HISTORY_SOURCE_CONTRACT_REQUIRED')
    docs=capture['source_documents'];definition=capture['declarations']['definition'];site=capture['declarations']['operating']['site_id']
    known={};ordinal={};record_links={}
    for system,doc in docs.items():
        if doc['enterprise_id']!=capture['enterprise_id'] or doc.get('branch_id')!=capture['branch_id'] or doc['as_of']!=capture['source_as_of']:raise ValueError('HISTORY_SOURCE_SCOPE_MISMATCH')
        known[system]=set()
        for table,records in doc['event_refs'].items():
            for rid,ids in records.items():
                for n,eid in enumerate(ids):
                    known[system].add(eid);ordinal[system,eid]=max(n,ordinal.get((system,eid),0))
                    record_links.setdefault((system,eid),[]).append(dict(system=system,table=table,record_id=rid))
    jobs={r['id']:r for r in docs['MES']['tables'].get('manufacturing_tasks',[])}
    lots={r['id']:r for r in docs['WMS']['tables'].get('lots',[])}
    orders={r['id']:r for r in docs['ERP']['tables'].get('sales_orders',[])}
    rows=[];seen={}
    created={o['payload']['order_id']:o['payload'] for o in capture['observations'] if o['system']=='ERP' and o['kind']=='SalesOrderCreated'}
    # Current postimages may confirm identities, never rewrite historic dimensions.
    for oid,p in created.items():
        if any(p.get(k)!=orders.get(oid,{}).get(k) for k in ('product_id','customer_id')):raise ValueError('HISTORY_DIMENSION_REVISION_UNSUPPORTED')
    for o in capture['observations']:
        if o['kind']=='SalesOrderChanged' and any(k in o['payload'] for k in ('product_id','customer_id')):raise ValueError('HISTORY_DIMENSION_REVISION_UNSUPPORTED')
    for original in capture['observations']:
        r=copy.deepcopy(original);system=r['system'];eid=r['event_id'];p=r['payload'];kind=r['kind']
        if system not in known or eid not in known[system] or kind not in KINDS:raise ValueError('HISTORY_UNOBSERVED_SOURCE_EVENT')
        if instant(r['occurred_at'])>instant(r['source_known_at']) or instant(r['source_known_at'])>instant(capture['source_as_of']):raise ValueError('HISTORY_FUTURE_SOURCE_EVENT')
        key=system,eid
        if key in seen:
            if seen[key]!=original:raise ValueError('HISTORY_SOURCE_EVENT_CONFLICT')
            continue
        seen[key]=original
        job=jobs.get(p.get('job_id'),{});order=orders.get(p.get('order_id') or job.get('order_id'),{})
        lot=lots.get(p.get('lot_id'),{})
        item=p.get('item_id') or p.get('product_id') or lot.get('item_id') or job.get('product_id') or order.get('product_id')
        if not item or item not in definition['items']:raise ValueError('HISTORY_DIMENSION_UNRESOLVED')
        unit=definition['items'][item]['unit']
        if p.get('unit',unit)!=unit:raise ValueError('HISTORY_UNIT_CONFLICT')
        if 'quantity' in p:quantity(p['quantity'])
        r.update(item_id=item,location_id=site,uom=unit,customer_id=p.get('customer_id') or order.get('customer_id',''),
            order_id=p.get('order_id') or job.get('order_id',''),lot_id=p.get('lot_id') or ('FG:'+p['job_id'] if p.get('job_id') else ''),
            ordinal=ordinal[system,eid],source_records=record_links[key],event_observation_sha256=digest(original),historical_record_revision='NOT_RETAINED_EVENT_REFERENCE_PRESERVED')
        if kind in INFO_ONLY_KINDS:r['history_effect']='INFO_ONLY'
        rows.append(r)
    # Required immutable source records must be present for their supported domain.
    for system,tables in [('ERP',('sales_orders','shipments','purchase_receipts')),('WMS',('receipts','quality_events','movements')),('MES',('manufacturing_tasks','operations'))]:
        needed={eid for table in tables for ids in docs[system]['event_refs'].get(table,{}).values() for eid in ids}
        if needed-{eid for s,eid in seen if s==system}:raise ValueError('HISTORY_SOURCE_DOMAIN_COVERAGE_INCOMPLETE')
    return rows
