"""A daily, FIFO execution simulation. No optimization or APS oracle access."""
import copy,hashlib,json,random
from pathlib import Path
from datetime import datetime,timedelta
from decimal import Decimal
from .model import World,replay,dec,text,dt,SimulationError

ORIGIN='2026-11-02T00:00:00+08:00'
END='2026-12-27T23:59:59+08:00'

def dump(path,data):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data,ensure_ascii=False,indent=2,sort_keys=True),encoding='utf-8')
def build_definition(reference_path):
 ref=json.loads(Path(reference_path).read_text(encoding='utf-8'));tables=ref['tables'];items={r['id']:{'unit':r['base_uom'],'inherited_entity_id':r['entity_id']} for r in tables['items']}
 recipes={r['output_item_id']:r for r in tables['recipes']};ops={r['id']:r for r in tables['operations']};products={};newops={}
 skills={}
 for row in tables['resource_skills']:skills.setdefault(row['skill_id'],[]).append(row['resource_id'])
 def flatten(item,factor=Decimal(1)):
  if item not in recipes:return {item:factor}
  recipe=recipes[item];result={}
  for b in tables['bom_lines']:
   if b['recipe_id']==recipe['id']:
    for raw,qty in flatten(b['input_item_id'],factor*dec(b['quantity_per_output'])).items():result[raw]=result.get(raw,Decimal(0))+qty
  return result
 for product in ['FG-TB-M','FG-TW-M']:
  suffix=product[3:];route=['CUT-'+suffix,'SEW-'+suffix,'QC-'+suffix,'PACK-'+suffix]
  products[product]={'bom':{k:text(v) for k,v in flatten(product).items()},'routing':route,'batch_quantity':20,'inherited_item_id':product}
  for name in route:
   row=ops[name];newops[name]={'resource':row['resource_ids'][0],'operator':sorted(skills[row['required_operator_skill']])[0],
    'minutes_per_piece':text(dec(row['seconds_per_unit'] or 0)/60),'fixed_minutes':text(dec(row['fixed_seconds'] or 0)/60),
    'batch_quantity':row.get('batch_quantity'),'inherited_entity_id':row['entity_id'],'research_execution_policy':'NO_SCRAP_NO_REWORK_FIXED_20_PIECE_TASK'}
 return {'schema':'continuous-enterprise/1','enterprise_id':'SIM-GARMENT-CONT-01','timezone':'Asia/Shanghai','origin':ORIGIN,'end':END,
  'items':items,'products':products,'operations':newops,'resources':{r['id']:{'inherited_entity_id':r['entity_id']} for r in tables['resources']},
  'operators':{r['id']:{'inherited_entity_id':r['entity_id']} for r in tables['operators']},
  'workday':{'start':8,'end':17,'weekdays':[0,1,2,3,4],'segments':[[8,12],[13,17]]},
  'source_reference':ref['source'],'scope':'Independent derived research enterprise; inherited identities are references, original MOs/stock/events not executed',
  'assumptions':[
   {'id':'A-CONT-CALENDAR','value':'New research calendar 8 weeks Mon-Fri 08-12/13-17; explicitly not extending original calendars or qualifications'},
   {'id':'A-CONT-DEMAND','value':'Day-index-seeded two-product batch20 demand, 1 weekday arrival before day14 then 3; due 7 calendar days; not publicly calibrated'},
   {'id':'A-CONT-EXECUTION','value':'FIFO execution; batch20; no scrap/rework, full material issued at task release, four sequential operations from original durations, no alternate machine optimization'},
   {'id':'A-CONT-SUPPLY','value':'New POs and positive balances created only by receipt/QC events; weekly research replenishment and delays; no original stock copied'},
   {'id':'A-CONT-QUALIFICATION','value':'Named original skill matching reused as reference; new research roles explicitly declared for 8 weeks; no enterprise qualification authorization'},
   {'id':'A-CONT-LATENCY','value':'ERP 2h, WMS 5min, MES 10min default recording latency; special late ERP receipt 26h; study assumptions'},
   {'id':'A-CONT-NO-OPTIMALITY','value':'Actual completion is FIFO constrained execution, not an optimization baseline or customer performance estimate'}],
  'unknown_reference_positions':[{'id':r['id'],'physical':None,'status':'UNKNOWN','excluded_from_execution':True} for r in tables['inventory_positions'] if r['physical'] is None]}

class Simulation:
 def __init__(self,definition,seed=73):
  self.definition=copy.deepcopy(definition);self.seed=int(seed);self.clock=definition['origin'];self.world=World(definition);self.paused=False;self.next_day=0;self.pending=[]
 def emit(self,kind,payload,at,tag,latencies=None):
  known={s:(at+timedelta(minutes=m)).isoformat() for s,m in (latencies or {'ERP':120,'WMS':5,'MES':10}).items()}
  event={'event_id':'CONT-'+tag,'kind':kind,'occurred_at':at.isoformat(),'known_at':known,'payload':payload,'research_id':'APS-SIM-ENTERPRISE-01'}
  self.world.apply(event);return event
 def queue(self,kind,payload,at,tag,latencies=None):self.pending.append((at.isoformat(),tag,kind,payload,latencies))
 def _day(self,index):
  base=dt(self.definition['origin'])+timedelta(days=index);rng=random.Random(self.seed*1000+index)
  at=lambda h,m=0:base.replace(hour=h,minute=m,second=0)
  # These are day-local decisions, not a pre-generated eight-week truth stream.
  if index==0:
   raw=sorted({r for p in self.definition['products'].values() for r in p['bom']})
   for n,item in enumerate(raw):
    qty=100 if item=='FAB-BLK' else (400 if item=='FAB-WHT' else 4000)
    po='PO-OPEN-'+item;lot='LOT-OPEN-'+item
    self.emit('PurchaseOrderCreated',{'po_id':po,'item_id':item,'quantity':str(qty),'unit':self.definition['items'][item]['unit'],'eta':at(9).isoformat(),'supplier_id':'SUP-CONT-'+str(n%3+1)},at(7),'PO-OPEN-'+item)
    self.emit('GoodsReceived',{'po_id':po,'receipt_id':'RCV-OPEN-'+item,'lot_id':lot,'item_id':item,'quantity':str(qty),'unit':self.definition['items'][item]['unit']},at(7),'GR-OPEN-'+item)
    self.emit('QCReleased',{'lot_id':lot,'quantity':str(qty),'unit':self.definition['items'][item]['unit']},at(7),'QC-OPEN-'+item)
   self.emit('SalesOrderCreated',{'order_id':'SO-FUTURE','product_id':'FG-TB-M','quantity':'20','due':'2026-12-10T17:00:00+08:00','customer_id':'CUST-CONT-1','priority':1,'release_not_before':'2026-11-30T08:00:00+08:00'},at(7),'FUTURE-ORDER')
  if base.weekday()==0 and index>0:
   for item in ['FAB-BLK','FAB-WHT']:
    po='PO-W'+str(index//7)+'-'+item
    self.emit('PurchaseOrderCreated',{'po_id':po,'item_id':item,'quantity':'400','unit':'米','eta':at(9).isoformat(),'supplier_id':'SUP-CONT-1'},at(7),'CREATE-'+po)
    self.emit('GoodsReceived',{'po_id':po,'receipt_id':'R-'+po,'lot_id':'L-'+po,'item_id':item,'quantity':'400','unit':'米'},at(7),'GR-'+po)
    self.emit('QCReleased',{'lot_id':'L-'+po,'quantity':'400','unit':'米'},at(7),'QC-'+po)
  if index==13:
   self.emit('PurchaseOrderCreated',{'po_id':'PO-PARTIAL-100','item_id':'FAB-BLK','quantity':'100','unit':'米','eta':(base+timedelta(days=2)).replace(hour=9).isoformat(),'supplier_id':'SUP-CONT-1'},at(7),'PARTIAL-PO')
  if index==14:
   self.emit('SalesOrderChanged',{'order_id':'SO-FUTURE','quantity':'40','due':'2026-12-11T17:00:00+08:00'},at(7),'FUTURE-ORDER-CHANGE')
   self.emit('PurchaseETAChanged',{'po_id':'PO-PARTIAL-100','eta':(base+timedelta(days=5)).replace(hour=9).isoformat()},at(7),'PARTIAL-ETA')
   self.emit('GoodsReceived',{'po_id':'PO-PARTIAL-100','receipt_id':'RCV-PARTIAL-60','lot_id':'LOT-PARTIAL','item_id':'FAB-BLK','quantity':'60','unit':'米'},at(7),'PARTIAL-GR',{'WMS':5,'MES':10,'ERP':1560})
   self.emit('QCReleased',{'lot_id':'LOT-PARTIAL','quantity':'50','unit':'米'},at(7),'PARTIAL-QC')
  if index==16:self.emit('QCHeld',{'lot_id':'LOT-PARTIAL','quantity':'10','unit':'米','from_status':'pending'},at(7),'PARTIAL-HOLD')
  if index==19:
   self.emit('GoodsReceived',{'po_id':'PO-PARTIAL-100','receipt_id':'RCV-PARTIAL-40','lot_id':'LOT-REMAINING','item_id':'FAB-BLK','quantity':'40','unit':'米'},at(7),'REMAINING-GR')
   self.emit('QCReleased',{'lot_id':'LOT-REMAINING','quantity':'40','unit':'米'},at(7),'REMAINING-QC')
  if index==21:
   for resource in ['SEW-01','OP-01']:
    self.emit('ResourceUnavailable',{'resource_id':resource,'start':at(8).isoformat(),'end':(base+timedelta(days=3)).replace(hour=17).isoformat(),'reason':'研究设备检修' if resource=='SEW-01' else '研究人员缺勤'},at(7),'DOWN-'+resource)
  if base.weekday() in self.definition['workday']['weekdays']:
   count=1 if index<14 else 3
   for n in range(count):
    oid='SO-'+str(index).zfill(2)+'-'+str(n);product=rng.choice(sorted(self.definition['products']))
    self.emit('SalesOrderCreated',{'order_id':oid,'product_id':product,'quantity':'20','due':(base+timedelta(days=7)).replace(hour=17).isoformat(),'customer_id':'CUST-CONT-'+str(n%3+1),'priority':1},at(7),'ORDER-'+oid)
   if index==28:
    self.emit('SalesOrderCreated',{'order_id':'SO-URGENT','product_id':'FG-TB-M','quantity':'40','due':(base+timedelta(days=2)).replace(hour=17).isoformat(),'customer_id':'CUST-CONT-1','priority':0},at(7),'URGENT')
    self.emit('SalesOrderChanged',{'order_id':'SO-URGENT','quantity':'60','due':(base+timedelta(days=3)).replace(hour=17).isoformat(),'priority':0},at(7),'URGENT-CHANGE')
   self._execute_day(base)
 def _execute_day(self,base):
  cursor=base.replace(hour=8,minute=0,second=0);close=base.replace(hour=17,minute=0,second=0)
  for oid,order in list(self.world.state['orders'].items()):
   if order.get('release_not_before') and cursor<dt(order['release_not_before']):continue
   jobs=[(key,j) for key,j in self.world.state['jobs'].items() if j['order_id']==oid and dec(j['shipped'])<dec(j['quantity'])]
   if not jobs and dec(order['shipped'])<dec(order['quantity']):
    jid='JOB-'+oid+'-'+text(dec(order['shipped'])/20);product=self.definition['products'][order['product_id']]
    required={item:dec(qty)*20 for item,qty in product['bom'].items()}
    if any(sum((dec(l['available']) for l in self.world.state['lots'].values() if l['item_id']==item),Decimal(0))<q for item,q in required.items()):continue
    self.emit('ManufacturingTaskCreated',{'job_id':jid,'order_id':oid,'product_id':order['product_id'],'quantity':'20'},cursor,'JOB-'+jid)
    for item,q in sorted(required.items()):
     for lid,lot in list(self.world.state['lots'].items()):
      if lot['item_id']!=item or dec(lot['available'])<=0:continue
      take=min(q,dec(lot['available']))
      self.emit('MaterialIssued',{'job_id':jid,'lot_id':lid,'quantity':text(take),'unit':self.definition['items'][item]['unit']},cursor,'ISSUE-'+jid+'-'+lid);q-=take
      if q==0:break
    jobs=[(jid,self.world.state['jobs'][jid])]
   for jid,job in jobs:
    for op in self.definition['products'][job['product_id']]['routing']:
     job=self.world.state['jobs'][jid]
     if op in job['completed']:continue
     spec=self.definition['operations'][op];minutes=dec(spec['fixed_minutes'])+dec(spec['minutes_per_piece'])*dec(job['quantity'])
     if cursor.hour>=12 and cursor.hour<13:cursor=cursor.replace(hour=13,minute=0)
     end=cursor+timedelta(minutes=float(minutes))
     if cursor.hour<12 and end>base.replace(hour=12,minute=0):cursor=base.replace(hour=13,minute=0);end=cursor+timedelta(minutes=float(minutes))
     if end>close:return
     if any(block['resource_id'] in [spec['resource'],spec['operator']] and dt(block['start'])<end and dt(block['end'])>cursor for block in self.world.state['resource_blocks']):return
     self.emit('OperationStarted',{'job_id':jid,'operation_id':op,'resource_id':spec['resource'],'operator_id':spec['operator'],'end':end.isoformat()},cursor,'START-'+jid+'-'+op)
     self.emit('OperationCompleted',{'job_id':jid,'operation_id':op,'quantity':job['quantity']},end,'END-'+jid+'-'+op);cursor=end
    self.emit('FinishedGoodsReceived',{'job_id':jid,'quantity':job['quantity']},cursor,'FG-'+jid)
    self.emit('ShipmentPosted',{'order_id':oid,'job_id':jid,'quantity':job['quantity']},cursor,'SHIP-'+jid)
 def advance(self,to):
  if self.paused:raise ValueError('PAUSED')
  target=dt(to)
  if target<dt(self.clock):raise ValueError('CLOCK_CANNOT_REVERSE; use replay')
  if target>dt(self.definition['end']):raise ValueError('BEYOND_SCENARIO_END')
  # Day execution is generated on a scratch replay; only elapsed events become actual.
  # Future planned intraday events remain in pending, never in exported truth/events.
  while self.next_day<56 and dt(self.definition['origin'])+timedelta(days=self.next_day)<=target:
   day=self.next_day;daystart=dt(self.definition['origin'])+timedelta(days=day)
   if self.pending:
    for e in self.pending:
     if dt(e['occurred_at'])<=target:self.world.apply(e)
    self.pending=[e for e in self.pending if dt(e['occurred_at'])>target]
    if self.pending:break
    self.next_day+=1;continue
   actual=self.world;self.world=replay(self.definition,actual.events);before=len(self.world.events);self._day(day);generated=self.world.events[before:];self.world=actual
   for e in generated:
    if dt(e['occurred_at'])<=target:self.world.apply(e)
   self.pending=[e for e in generated if dt(e['occurred_at'])>target]
   if self.pending:break
   self.next_day+=1
  self.clock=target.isoformat();return self.world.snapshot()
 def pause(self):self.paused=True
 def resume(self):self.paused=False
 def save(self,folder):
  folder=Path(folder);folder.mkdir(parents=True,exist_ok=True);dump(folder/'definition.json',self.definition)
  dump(folder/'clock.json',{'clock':self.clock,'seed':self.seed,'paused':self.paused,'next_day':self.next_day,'pending':self.pending})
  (folder/'events.jsonl').write_text(''.join(json.dumps(e,ensure_ascii=False,sort_keys=True)+'\n' for e in self.world.events),encoding='utf-8');dump(folder/'state.json',self.world.snapshot())
 @classmethod
 def load(cls,folder):
  folder=Path(folder);meta=json.loads((folder/'clock.json').read_text(encoding='utf-8'));obj=cls(json.loads((folder/'definition.json').read_text(encoding='utf-8')),meta['seed'])
  obj.world=replay(obj.definition,[json.loads(line) for line in (folder/'events.jsonl').read_text(encoding='utf-8').splitlines()]);obj.clock=meta['clock'];obj.paused=meta['paused'];obj.next_day=meta['next_day'];obj.pending=meta['pending'];return obj
