"""Atomic event transitions. Decimal quantities remain strings on disk."""
import copy
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

class SimulationError(ValueError):pass

def dt(value):return datetime.fromisoformat(value.replace('Z','+00:00'))
def dec(value):
 try:
  result=Decimal(str(value))
  if not result.is_finite():raise InvalidOperation()
  return result
 except (InvalidOperation,ValueError,TypeError):raise SimulationError('INVALID_QUANTITY')
def amount(value):
 result=dec(value)
 if result<=0:raise SimulationError('NON_POSITIVE_QUANTITY')
 return result
def text(value):return format(dec(value).normalize(),'f')
def add(record,key,value):record[key]=text(dec(record.get(key,'0'))+dec(value))
def identity(mapping,key):
 if key not in mapping:raise SimulationError('UNKNOWN_IDENTITY: '+str(key))
 return mapping[key]

def operation_key(payload):
 """Legacy whole operations keep their identity; new segments never overwrite it."""
 key=payload['job_id']+'::'+payload['operation_id']
 if 'segment_id' in payload:
  segment=payload['segment_id']
  if not isinstance(segment,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',segment):raise SimulationError('INVALID_OPERATION_SEGMENT_ID')
  key+='::SEG:'+segment
 return key

def formal_plan(definition,product,quantity,operations,occurred_at):
 """Validate formal planned windows without recording execution or inventory."""
 route=definition['products'][product]['routing'];previous=None;quantity=amount(quantity)
 if [o.get('operation_id') for o in operations]!=route:raise SimulationError('FORMAL_MO_ROUTE_INVALID')
 for op in operations:
  spec=definition['operations'][op['operation_id']];start,end=dt(op['start_at']),dt(op['end_at'])
  if amount(op['quantity'])!=quantity or op.get('uom')!='件':raise SimulationError('FORMAL_MO_QUANTITY_INVALID')
  if op['resource_id'] not in spec.get('resource_ids',[spec['resource']]) or op['operator_id'] not in definition['operators']:raise SimulationError('UNQUALIFIED_ASSIGNMENT')
  if not dt(occurred_at)<=start<end or (previous is not None and previous>start):raise SimulationError('FORMAL_MO_INTERVAL_INVALID')
  previous=end
  minimum=(dec(spec.get('fixed_minutes',0))+dec(spec.get('minutes_per_piece',0))*quantity)*60
  if dec(str((end-start).total_seconds()))<minimum:raise SimulationError('DURATION_BELOW_STANDARD')
  if spec.get('batch_quantity') is not None and amount(spec['batch_quantity'])!=quantity:raise SimulationError('INVALID_BATCH_SIZE')
  cal=definition['workday'];a=start.hour*3600+start.minute*60+start.second+start.microsecond/1e6;b=end.hour*3600+end.minute*60+end.second+end.microsecond/1e6
  if start.date()!=end.date() or start.weekday() not in cal['weekdays'] or not any(lo*3600<=a<b<=hi*3600 for lo,hi in cal.get('segments',[[cal['start'],cal['end']]])):raise SimulationError('NON_WORKING_TIME')

_STATE_ATOMS=(str,int,float,bool,type(None))
def _copy_state(value,memo=None):
 """Copy every state container, skipping deepcopy dispatch for JSON scalars."""
 if type(value) not in (dict,list):return copy.deepcopy(value,memo)
 if memo is None:memo={}
 key=id(value)
 if key in memo:return memo[key]
 if type(value) is dict:
  result={};memo[key]=result
  for k,v in value.items():
   result[k if type(k) in _STATE_ATOMS else copy.deepcopy(k,memo)]=v if type(v) in _STATE_ATOMS else _copy_state(v,memo)
 else:
  result=[];memo[key]=result
  result.extend(v if type(v) in _STATE_ATOMS else _copy_state(v,memo) for v in value)
 return result

class World:
 def __init__(self,definition,assignment_validator=None):
  self.definition=copy.deepcopy(definition);self.events=[];self.ids={};self.assignment_validator=assignment_validator
  self.state={'orders':{},'purchase_orders':{},'lots':{},'jobs':{},'operations':{},'finished':{},'resource_blocks':[], 'shipments':{},'last_at':None}
 def snapshot(self):return copy.deepcopy(self.state)
 def fork(self):
  """Isolated scratch state and ledger; retain the exact validation callback."""
  scratch=World(self.definition,assignment_validator=self.assignment_validator)
  scratch.state,scratch.events,scratch.ids=copy.deepcopy((self.state,self.events,self.ids))
  return scratch
 def apply(self,event):
  signature=json.dumps(event,sort_keys=True,ensure_ascii=False)
  key=event['event_id']
  if key in self.ids:
   if self.ids[key]!=signature:raise SimulationError('EVENT_ID_CONFLICT')
   return False
  occurred=dt(event['occurred_at'])
  if not event.get('known_at') or any(dt(t)<occurred for t in event['known_at'].values()):raise SimulationError('INVALID_KNOWN_TIME')
  if self.state['last_at'] and occurred<dt(self.state['last_at']):raise SimulationError('EVENT_TIME_REVERSED')
  # A callback may carry arbitrary state behavior; keep its original copy path.
  custom_assignment=self.assignment_validator is not None and event['kind'] in ['OperationStarted','OperationCompleted','OperationInterrupted']
  proposed=copy.deepcopy(self.state) if custom_assignment else _copy_state(self.state)
  self._transition(proposed,event)
  proposed['last_at']=event['occurred_at'];self.state=proposed;self.events.append(copy.deepcopy(event));self.ids[key]=signature
  return True
 def _transition(self,s,e):
  p=e['payload'];kind=e['kind'];t=e['occurred_at'];d=self.definition
  if p.get('lot_id') in s['lots'] and p.get('item_id',s['lots'][p['lot_id']]['item_id'])!=s['lots'][p['lot_id']]['item_id']:raise SimulationError('CROSS_SYSTEM_IDENTITY')
  product=p.get('product_id')
  if p.get('job_id') in s['jobs']:
   job=s['jobs'][p['job_id']]
   if p.get('product_id',job['product_id'])!=job['product_id'] or p.get('order_id',job['order_id'])!=job['order_id']:raise SimulationError('CROSS_SYSTEM_IDENTITY')
   product=job['product_id']
  if kind in ['SalesOrderCreated','SalesOrderChanged','ManufacturingTaskCreated','ManufacturingOrderDraftCreated','OperationCompleted','FinishedGoodsReceived','ShipmentPosted']:
   if not product and p.get('order_id') in s['orders']:product=s['orders'][p['order_id']]['product_id']
   if product and p.get('unit',identity(d['items'],product)['unit'])!=d['items'][product]['unit']:raise SimulationError('UNIT_MISMATCH')
  def unit(item):
   expected=identity(d['items'],item)['unit']
   if p.get('unit',expected)!=expected:raise SimulationError('UNIT_MISMATCH')
  if kind=='SalesOrderCreated':
   key=p['order_id'];identity(d['products'],p['product_id'])
   if key in s['orders']:raise SimulationError('DUPLICATE_BUSINESS_ID')
   s['orders'][key]={**p,'quantity':text(amount(p['quantity'])),'shipped':'0','status':'OPEN','created_at':t}
  elif kind=='SalesOrderChanged':
   row=identity(s['orders'],p['order_id']);q=amount(p.get('quantity',row['quantity']))
   if q<dec(row['shipped']):raise SimulationError('ORDER_BELOW_SHIPPED')
   jobs=[j for j in s['jobs'].values() if j['order_id']==p['order_id']]
   if jobs and q!=dec(row['quantity']):raise SimulationError('ORDER_CHANGE_AFTER_RELEASE')
   row.update({k:v for k,v in p.items() if k in ['quantity','due','priority']});row['quantity']=text(q)
  elif kind=='PurchaseOrderCreated':
   key=p['po_id'];unit(p['item_id'])
   if key in s['purchase_orders']:raise SimulationError('DUPLICATE_BUSINESS_ID')
   s['purchase_orders'][key]={**p,'quantity':text(amount(p['quantity'])),'received':'0','outstanding':text(p['quantity']),'status':'OPEN'}
  elif kind=='PurchaseETAChanged':
   row=identity(s['purchase_orders'],p['po_id']);row['eta']=p['eta'];row['eta_changed_at']=t
  elif kind=='GoodsReceived':
   po=identity(s['purchase_orders'],p['po_id']);unit(p['item_id']);q=amount(p['quantity'])
   if po['item_id']!=p['item_id']:raise SimulationError('CROSS_SYSTEM_IDENTITY')
   if p['lot_id'] in s['lots'] or any(l['receipt_id']==p['receipt_id'] for l in s['lots'].values()):raise SimulationError('DUPLICATE_RECEIPT')
   if q>dec(po['outstanding']):raise SimulationError('RECEIPT_EXCEEDS_PO')
   add(po,'received',q);add(po,'outstanding',-q);po['status']='RECEIVED' if dec(po['outstanding'])==0 else 'PARTIAL'
   s['lots'][p['lot_id']]={**p,'received_at':t,'received':text(q),'physical':text(q),'pending':text(q),'available':'0','held':'0','consumed':'0'}
  elif kind in ['QCReleased','QCHeld']:
   row=identity(s['lots'],p['lot_id']);unit(row['item_id']);q=amount(p['quantity']);origin=p.get('from_status','pending')
   destination='available' if kind=='QCReleased' else 'held'
   if origin not in ['pending','held','available'] or origin==destination:raise SimulationError('INVALID_QC_TRANSITION')
   if q>dec(row[origin]):raise SimulationError('QC_EXCEEDS_BALANCE')
   add(row,origin,-q);add(row,destination,q)
  elif kind in ['ManufacturingTaskCreated','ManufacturingOrderDraftCreated']:
   order=identity(s['orders'],p['order_id']);identity(d['products'],p['product_id']);q=amount(p['quantity'])
   if p['job_id'] in s['jobs']:raise SimulationError('DUPLICATE_BUSINESS_ID')
   existing=sum((dec(j['quantity']) for j in s['jobs'].values() if j['order_id']==p['order_id']),Decimal(0))
   if order['product_id']!=p['product_id'] or existing+q>dec(order['quantity']):raise SimulationError('JOB_ORDER_MISMATCH')
   if kind=='ManufacturingOrderDraftCreated':
    if not p.get('plan_operations') or type(p.get('external_version')) is not int or p['external_version']!=1 or not p.get('command_id'):raise SimulationError('FORMAL_MO_SOURCE_INVALID')
    formal_plan(d,p['product_id'],q,p['plan_operations'],t)
   s['jobs'][p['job_id']]={**p,'quantity':text(q),'issued':{},'completed':{},'fg_received':'0','shipped':'0','status':'DRAFT' if kind=='ManufacturingOrderDraftCreated' else 'RELEASED'}
   if kind=='ManufacturingOrderDraftCreated':
    s['jobs'][p['job_id']]['interop_command_id']=p['command_id'];s['jobs'][p['job_id']]['plan_command_id']=p['command_id']
  elif kind in ['ManufacturingOrderConfirmed','ManufacturingOrderRescheduled']:
   job=identity(s['jobs'],p['job_id'])
   if type(p.get('expected_document_version')) is not int or type(p.get('external_version')) is not int or p['expected_document_version']!=job.get('external_version',1) or p['external_version']!=job.get('external_version',1)+1:raise SimulationError('EXTERNAL_DOCUMENT_VERSION_CONFLICT')
   if job['status'] not in ['DRAFT','CONFIRMED'] or job['issued'] or job['completed'] or job.get('protected') or job.get('frozen') or any(o['job_id']==p['job_id'] for o in s['operations'].values()):raise SimulationError('EXTERNAL_DOCUMENT_NOT_EDITABLE')
   if kind=='ManufacturingOrderConfirmed':
    if job['status']!='DRAFT':raise SimulationError('EXTERNAL_DOCUMENT_NOT_DRAFT')
    job['status']='CONFIRMED'
   else:
    if not p.get('plan_operations'):raise SimulationError('FORMAL_MO_SOURCE_INVALID')
    formal_plan(d,job['product_id'],job['quantity'],p['plan_operations'],t)
    job['plan_operations']=copy.deepcopy(p['plan_operations'])
    job['plan_command_id']=p['command_id']
   job['external_version']=p['external_version']
  elif kind=='MaterialIssued':
   job=identity(s['jobs'],p['job_id']);lot=identity(s['lots'],p['lot_id']);unit(lot['item_id']);q=amount(p['quantity']);bom=d['products'][job['product_id']]['bom']
   if job['status']=='DRAFT':raise SimulationError('MANUFACTURING_ORDER_NOT_CONFIRMED')
   if lot['item_id'] not in bom:raise SimulationError('MATERIAL_NOT_IN_BOM')
   if q>dec(lot['available']):raise SimulationError('INSUFFICIENT_RELEASED')
   required=dec(bom[lot['item_id']])*dec(job['quantity'])
   if dec(job['issued'].get(lot['item_id'],'0'))+q>required:raise SimulationError('ISSUE_EXCEEDS_BOM')
   add(lot,'available',-q);add(lot,'physical',-q);add(lot,'consumed',q);add(job['issued'],lot['item_id'],q)
  elif kind=='ResourceUnavailable':
   if p['resource_id'] not in d['resources'] and p['resource_id'] not in d['operators']:raise SimulationError('UNKNOWN_IDENTITY')
   start,end=dt(p['start']),dt(p['end'])
   if start>=end:raise SimulationError('INVALID_INTERVAL')
   for op in s['operations'].values():
    if p['resource_id'] in [op['resource_id'],op['operator_id']] and start<dt(op.get('actual_end',op['end'])) and end>dt(op['start']):raise SimulationError('UNAVAILABILITY_OVERLAPS_COMMITTED_EXECUTION')
   s['resource_blocks'].append({**p,'announced_at':t})
  elif kind=='OperationStarted':
   job=identity(s['jobs'],p['job_id']);route=d['products'][job['product_id']]['routing'];op=p['operation_id'];spec=identity(d['operations'],op)
   if job['status']=='DRAFT':raise SimulationError('MANUFACTURING_ORDER_NOT_CONFIRMED')
   identity(d['resources'],p['resource_id']);identity(d['operators'],p['operator_id'])
   if op not in route:raise SimulationError('OPERATION_NOT_IN_ROUTING')
   q=amount(p.get('quantity',job['quantity']))
   if spec.get('batch_quantity') and (q!=dec(spec['batch_quantity']) or q!=dec(job['quantity'])):raise SimulationError('INVALID_BATCH_SIZE')
   segmented='segment_id' in p
   if segmented and q!=q.to_integral_value():raise SimulationError('INTEGER_OPERATION_SEGMENT_REQUIRED')
   if q!=dec(job['quantity']) and not segmented:raise SimulationError('OPERATION_SEGMENT_ID_REQUIRED')
   if segmented and not spec.get('batch_quantity') and (dec(spec.get('fixed_minutes',0))!=0 or dec(spec.get('minutes_per_piece',0))<=0):raise SimulationError('CONTINUOUS_SEGMENT_MODEL_REQUIRED')
   completed=dec(job['completed'].get(op,0))
   if completed+q>dec(job['quantity']):raise SimulationError('OPERATION_QUANTITY_EXCEEDS_REMAINING')
   if p['resource_id'] not in spec.get('resource_ids',[spec['resource']]):raise SimulationError('UNQUALIFIED_ASSIGNMENT')
   if self.assignment_validator is None:
    if spec['operator']!=p['operator_id']:raise SimulationError('UNQUALIFIED_ASSIGNMENT')
   elif self.assignment_validator(d,e,s) is not True:raise SimulationError('UNQUALIFIED_ASSIGNMENT')
   for prior in route[:route.index(op)]:
    if dec(job['completed'].get(prior,'0'))<completed+q:raise SimulationError('PREDECESSOR_NOT_COMPLETED')
   for item,qty in d['products'][job['product_id']]['bom'].items():
    if dec(job['issued'].get(item,'0'))<dec(qty)*dec(job['quantity']):raise SimulationError('MATERIAL_NOT_ISSUED')
   start,end=dt(t),dt(p['end']);cal=d['workday']
   if start>=end or start.date()!=end.date() or start.weekday() not in cal['weekdays'] or start.hour<cal['start'] or end.hour>cal['end'] or (end.hour==cal['end'] and (end.minute or end.second)):raise SimulationError('NON_WORKING_TIME')
   minutes=dec(str((end-start).total_seconds()))/60
   if minutes<dec(spec.get('fixed_minutes','0'))+dec(spec['minutes_per_piece'])*q:raise SimulationError('DURATION_BELOW_STANDARD')
   if spec.get('batch_quantity') and dec(job['quantity'])!=dec(spec['batch_quantity']):raise SimulationError('INVALID_BATCH_SIZE')
   if cal.get('segments') and not any(start.hour*60+start.minute>=a*60 and end.hour*60+end.minute<=b*60 for a,b in cal['segments']):raise SimulationError('NON_WORKING_TIME')
   for old in s['operations'].values():
    if (old['resource_id']==p['resource_id'] or old['operator_id']==p['operator_id']) and (old['status']=='STARTED' or (start<dt(old.get('actual_end',old['end'])) and end>dt(old['start']))):raise SimulationError('RESOURCE_OVERLAP')
   for block in s['resource_blocks']:
    if block['resource_id'] in [p['resource_id'],p['operator_id']] and start<dt(block['end']) and end>dt(block['start']):raise SimulationError('RESOURCE_UNAVAILABLE')
   key=operation_key(p)
   if key in s['operations']:raise SimulationError('OPERATION_ALREADY_STARTED')
   if any(o['job_id']==p['job_id'] and o['operation_id']==op and o['status']=='STARTED' for o in s['operations'].values()):raise SimulationError('OPERATION_ALREADY_STARTED')
   s['operations'][key]={**p,'start':t,'status':'STARTED','quantity':text(q)};job['status']='WIP'
  elif kind=='OperationInterrupted':
   key=operation_key(p);row=identity(s['operations'],key);job=s['jobs'][p['job_id']]
   spec=identity(d['operations'],p['operation_id'])
   if spec.get('batch_quantity') or dec(spec.get('fixed_minutes',0))!=0 or dec(spec.get('minutes_per_piece',0))<=0:raise SimulationError('CONTINUOUS_INTERRUPTION_REQUIRED')
   q=dec(p['actual_quantity'])
   if row['status']!='STARTED' or not 0<=q<dec(row['quantity']) or q!=q.to_integral_value() or not p.get('reason'):raise SimulationError('INVALID_INTERRUPTION')
   if not dt(row['start'])<=dt(t)<=dt(row['end']) or dec(str((dt(t)-dt(row['start'])).total_seconds()))<dec(spec['minutes_per_piece'])*q*60:raise SimulationError('INTERRUPTION_DURATION_BELOW_STANDARD')
   if self.assignment_validator is not None and self.assignment_validator(d,e,s) is not True:raise SimulationError('UNQUALIFIED_ASSIGNMENT')
   row.update(status='INTERRUPTED',actual_end=t,actual_quantity=text(q),interruption_reason=p['reason'])
   add(job['completed'],p['operation_id'],q)
  elif kind=='OperationCompleted':
   key=operation_key(p);row=identity(s['operations'],key);job=s['jobs'][p['job_id']];q=amount(p['quantity'])
   if row['status']!='STARTED' or dt(t)<dt(row['end']) or q!=dec(row['quantity']):raise SimulationError('INVALID_COMPLETION')
   if self.assignment_validator is not None and self.assignment_validator(d,e,s) is not True:raise SimulationError('UNQUALIFIED_ASSIGNMENT')
   row['status']='COMPLETED';row['actual_end']=t;add(job['completed'],p['operation_id'],q)
   if all(dec(job['completed'].get(op,0))==dec(job['quantity']) for op in d['products'][job['product_id']]['routing']):job['status']='COMPLETED'
  elif kind=='FinishedGoodsReceived':
   job=identity(s['jobs'],p['job_id']);q=amount(p['quantity']);route=d['products'][job['product_id']]['routing']
   if not route or dec(job['completed'].get(route[-1],'0'))<dec(job['fg_received'])+q:raise SimulationError('NOT_COMPLETED')
   add(job,'fg_received',q);add(s['finished'],p['job_id'],q)
  elif kind=='ShipmentPosted':
   order=identity(s['orders'],p['order_id']);job=identity(s['jobs'],p['job_id']);q=amount(p['quantity'])
   if job['order_id']!=p['order_id'] or q>dec(s['finished'].get(p['job_id'],'0')) or dec(order['shipped'])+q>dec(order['quantity']):raise SimulationError('INSUFFICIENT_FINISHED_GOODS')
   add(s['finished'],p['job_id'],-q);add(order,'shipped',q);add(job,'shipped',q)
   order['status']='SHIPPED' if dec(order['shipped'])==dec(order['quantity']) else 'PARTIAL'
   s['shipments'][e['event_id']]={**p,'shipped_at':t}
  else:raise SimulationError('UNSUPPORTED_EVENT: '+kind)

def replay(definition,events,as_of=None,assignment_validator=None):
 world=World(definition,assignment_validator=assignment_validator)
 for event in events:
  if as_of is None or dt(event['occurred_at'])<=dt(as_of):world.apply(event)
 return world
