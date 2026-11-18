"""Independent system views of known events; never serialize hidden future events."""
import copy,csv,hashlib,json
from pathlib import Path
from decimal import Decimal
from .model import dt,dec,text,operation_key
from .scenario import dump

SYSTEMS=['ERP','WMS','MES']
def project_sources(definition,events,as_of,system):
 if system not in SYSTEMS:raise ValueError('UNKNOWN_SYSTEM')
 visible=[];seen={}
 for index,e in enumerate(events):
  if system not in e['known_at'] or dt(e['known_at'][system])>dt(as_of) or dt(e['occurred_at'])>dt(as_of):continue
  signature=json.dumps(e,ensure_ascii=False,sort_keys=True)
  if e['event_id'] in seen:
   if seen[e['event_id']]!=signature:raise ValueError('EVENT_ID_CONFLICT')
   continue
  seen[e['event_id']]=signature;visible.append((index,e))
 visible=[e for index,e in sorted(visible,key=lambda pair:(dt(pair[1]['occurred_at']),pair[0]))]
 tables={};refs={};known={};occurred={};unresolved=[]
 def record(table,key,values,event,replace=False):
  bucket=tables.setdefault(table,{})
  row=bucket.setdefault(key,{'id':key})
  if replace:row.clear();row['id']=key
  row.update(copy.deepcopy(values));refs.setdefault(table,{}).setdefault(key,[]).append(event['event_id'])
  known[(table,key)]=max(known.get((table,key),event['known_at'][system]),event['known_at'][system],key=dt);occurred[(table,key)]=event['occurred_at']
  return row
 def addrow(table,key,field,value,event):
  row=record(table,key,{},event);row[field]=text(dec(row.get(field,'0'))+dec(value));return row
 for e in visible:
  p=e['payload'];kind=e['kind']
  dependency=None
  if system=='ERP':
   if kind in ['PurchaseETAChanged','GoodsReceived'] and p.get('po_id') not in tables.get('purchase_orders',{}):dependency='purchase_orders:'+str(p.get('po_id'))
   if kind in ['SalesOrderChanged','ShipmentPosted'] and p.get('order_id') not in tables.get('sales_orders',{}):dependency='sales_orders:'+str(p.get('order_id'))
  elif system=='WMS':
   if kind in ['QCReleased','QCHeld','MaterialIssued'] and p.get('lot_id') not in tables.get('lots',{}):dependency='lots:'+str(p.get('lot_id'))
   if kind=='ShipmentPosted' and p.get('job_id') not in tables.get('finished_goods',{}):dependency='finished_goods:'+str(p.get('job_id'))
  elif system=='MES':
   if kind in ['MaterialIssued','OperationStarted','OperationCompleted','OperationInterrupted','FinishedGoodsReceived','ManufacturingOrderConfirmed','ManufacturingOrderRescheduled'] and p.get('job_id') not in tables.get('manufacturing_tasks',{}):dependency='manufacturing_tasks:'+str(p.get('job_id'))
   if kind in ['OperationCompleted','OperationInterrupted'] and operation_key(p) not in tables.get('operations',{}):dependency='operations:'+operation_key(p)
  if dependency:
   unresolved.append({'event_id':e['event_id'],'kind':kind,'missing_dependency':dependency,'business_occurred_at':e['occurred_at'],'source_known_at':e['known_at'][system],'payload':copy.deepcopy(p)});continue
  if system=='ERP':
   if kind=='SalesOrderCreated':record('sales_orders',p['order_id'],{**p,'shipped':'0','status':'OPEN'},e)
   elif kind=='SalesOrderChanged':record('sales_orders',p['order_id'],p,e)
   elif kind=='PurchaseOrderCreated':record('purchase_orders',p['po_id'],{**p,'received':'0','outstanding':p['quantity'],'status':'OPEN'},e)
   elif kind=='PurchaseETAChanged':record('purchase_orders',p['po_id'],{'eta':p['eta']},e)
   elif kind=='GoodsReceived':
    row=addrow('purchase_orders',p['po_id'],'received',p['quantity'],e)
    row['outstanding']=text(dec(row['quantity'])-dec(row['received']));row['status']='RECEIVED' if dec(row['outstanding'])==0 else 'PARTIAL'
    record('purchase_receipts',p['receipt_id'],p,e)
   elif kind=='ShipmentPosted':
    row=addrow('sales_orders',p['order_id'],'shipped',p['quantity'],e);row['status']='SHIPPED' if dec(row['shipped'])==dec(row['quantity']) else 'PARTIAL';record('shipments',e['event_id'],p,e)
  elif system=='WMS':
   if kind=='GoodsReceived':
    record('receipts',p['receipt_id'],p,e);record('lots',p['lot_id'],{**p,'physical':p['quantity'],'pending':p['quantity'],'available':'0','held':'0','consumed':'0'},e)
   elif kind in ['QCReleased','QCHeld']:
    target='available' if kind=='QCReleased' else 'held';origin=p.get('from_status','pending')
    row=addrow('lots',p['lot_id'],origin,-dec(p['quantity']),e);row=addrow('lots',p['lot_id'],target,p['quantity'],e)
    record('quality_events',e['event_id'],{'kind':kind,**p},e)
   elif kind=='MaterialIssued':
    for field,sign in [('available',-1),('physical',-1),('consumed',1)]:addrow('lots',p['lot_id'],field,dec(p['quantity'])*sign,e)
    record('movements',e['event_id'],{'kind':kind,**p},e)
   elif kind=='FinishedGoodsReceived':
    addrow('finished_goods',p['job_id'],'physical',p['quantity'],e);record('movements',e['event_id'],{'kind':kind,**p},e)
   elif kind=='ShipmentPosted':
    addrow('finished_goods',p['job_id'],'physical',-dec(p['quantity']),e);record('movements',e['event_id'],{'kind':kind,**p},e)
  elif system=='MES':
   if kind in ['ManufacturingTaskCreated','ManufacturingOrderDraftCreated']:
    record('manufacturing_tasks',p['job_id'],{**p,'status':'DRAFT' if kind=='ManufacturingOrderDraftCreated' else 'RELEASED','completed_quantity':'0'},e)
   elif kind in ['ManufacturingOrderConfirmed','ManufacturingOrderRescheduled']:
    updates={'external_version':p['external_version']}
    if kind=='ManufacturingOrderConfirmed':updates['status']='CONFIRMED'
    else:updates['plan_operations']=copy.deepcopy(p['plan_operations'])
    record('manufacturing_tasks',p['job_id'],updates,e)
   elif kind=='MaterialIssued':record('material_issues',e['event_id'],p,e)
   elif kind=='OperationStarted':
    record('operations',operation_key(p),{**p,'start':e['occurred_at'],'status':'STARTED'},e);record('manufacturing_tasks',p['job_id'],{'status':'WIP'},e)
   elif kind=='OperationInterrupted':
    record('operations',operation_key(p),{'actual_quantity':p['actual_quantity'],'actual_end':e['occurred_at'],
        'interruption_reason':p['reason'],'status':'INTERRUPTED'},e)
   elif kind=='OperationCompleted':
    record('operations',operation_key(p),{**p,'actual_end':e['occurred_at'],'status':'COMPLETED'},e)
   elif kind=='FinishedGoodsReceived':
    job=addrow('manufacturing_tasks',p['job_id'],'completed_quantity',p['quantity'],e)
    job['status']='COMPLETED' if dec(job['completed_quantity'])==dec(job['quantity']) else 'WIP'
   elif kind=='ResourceUnavailable':record('resource_unavailability',e['event_id'],{**p,'announced_at':e['occurred_at']},e)
 # Each physical/system time is explicit. Sources contain trace IDs, not truth payloads.
 for table,rows in tables.items():
  for key,row in rows.items():
   row['business_occurred_at']=occurred[(table,key)];row['source_known_at']=known[(table,key)];row['aps_read_at']=as_of
   for field in ['item_id','product_id']:
    if field in row:row['source_'+field]=system+'-'+row[field]
 return {'schema':'source-visibility/1','enterprise_id':definition['enterprise_id'],'system':system,'as_of':as_of,'mode':'FULL_SNAPSHOT',
   'absence_semantics':'MISSING_REVIEW_NOT_CANCELLATION','tables':{t:list(rows.values()) for t,rows in tables.items()},
   'event_refs':{t:{key:list(dict.fromkeys(ids)) for key,ids in rows.items()} for t,rows in refs.items()},'unresolved_events':unresolved}

def export_sources(definition,events,as_of,out,previous_as_of=None):
 out=Path(out);out.mkdir(parents=True,exist_ok=True);packages={}
 for system in SYSTEMS:
  package=project_sources(definition,events,as_of,system);folder=out/system;folder.mkdir(exist_ok=True);dump(folder/'snapshot.json',package);packages[system]=package
  for table,rows in package['tables'].items():
   headers=sorted({k for row in rows for k in row})
   # Deliberately different ERP quantity / MES quantity field labels; mapping is public.
   labels={key:('订购数量' if system=='ERP' else 'reported_qty') if key=='quantity' else key for key in headers}
   with (folder/(table+'.csv')).open('w',encoding='utf-8-sig',newline='') as f:
    writer=csv.DictWriter(f,fieldnames=[labels[k] for k in headers]);writer.writeheader()
    for row in rows:writer.writerow({labels[k]:json.dumps(row.get(k),ensure_ascii=False) if isinstance(row.get(k),(dict,list)) else row.get(k) for k in headers})
  previous=project_sources(definition,events,previous_as_of,system) if previous_as_of else {'tables':{}}
  changes={};absent={}
  for table in set(package['tables'])|set(previous['tables']):
   old={r['id']:r for r in previous['tables'].get(table,[])};new={r['id']:r for r in package['tables'].get(table,[])}
   def business(row):return {k:v for k,v in row.items() if k!='aps_read_at'}
   changes[table]=[r for key,r in new.items() if key not in old or business(old[key])!=business(r)]
   absent[table]=[key for key in old if key not in new]
  delta={'schema':'source-visibility/1','enterprise_id':definition['enterprise_id'],'system':system,'as_of':as_of,'previous_as_of':previous_as_of,'mode':'INCREMENTAL_UPSERT','absence_semantics':'NO_CHANGE','tables':changes,'absent_from_snapshot':absent,
   'event_refs':{t:{r['id']:package['event_refs'][t][r['id']] for r in rows} for t,rows in changes.items() if rows}}
  dump(folder/'incremental.json',delta)
  duplicate=copy.deepcopy(delta);duplicate['delivery_id']='REPLAY-'+system+'-'+as_of;dump(folder/'duplicate-delivery.json',duplicate)
  authored={'snapshot.json','incremental.json','duplicate-delivery.json',*(t+'.csv' for t in package['tables'])}
  files=[{'file':p.name,'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(folder.iterdir()) if p.is_file() and p.name in authored]
  dump(folder/'manifest.json',{'system':system,'as_of':as_of,'mode':'FULL_SNAPSHOT_AND_INCREMENTAL','source_record_scope':'Only known events at APS read time','files':files})
 dump(out/'identity-registry.json',{'enterprise_id':definition['enterprise_id'],'status':'RESEARCH_DICTIONARY_REQUIRES_APS_REVIEW','items':[{'business_item_id':item,'unit':spec['unit'],'source_codes':{s:s+'-'+item for s in SYSTEMS},'definition_reference':spec['inherited_entity_id']} for item,spec in definition['items'].items()]})
 return packages
