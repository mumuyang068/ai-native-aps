"""One confirmed replenishment lane, proposed DOs and separate location balances."""
from datetime import datetime
from decimal import Decimal
PROFILE='single-lane-distribution-v1'
def enabled(data):return data.get('rules',{}).get('manufacturing_profile')==PROFILE
def validate(data):
 from planning_adapter import InputError
 def require(ok,message):
  if not ok:raise InputError('UNSUPPORTED_DISTRIBUTION: '+message)
 if not enabled(data):
  require(not any(k in data for k in ['locations','distribution_lanes','distribution_orders']),'distribution fields require explicit profile');return
 require(not any(data.get(k) for k in ['production_recipes','bom_revisions','routing_revisions','resources','calendars','suppliers','supply_sources','existing_orders','reservations','previous_manufacturing_suggestions','skills','resource_skills','distribution_orders']),'no manufacturing, personnel, PO/QC or existing/confirmed/in-transit DO combination')
 require('procurement_strategy' not in data['rules'],'no procurement strategy combination')
 require(data['rules'].get('move_approved_early')==0 and data['rules'].get('fix_broken_supply_path') is False and data['rules'].get('erase_previous_proposed') is True,'explicit native protection settings')
 t0=datetime.fromisoformat(data['planning_at']);end=datetime.fromisoformat(data['horizon']['end_at'])
 require(datetime.fromisoformat(data['horizon']['start_at'])==t0 and t0<end,'planning horizon')
 locs=data.get('locations',[]);require(len(locs)==2 and len({l['id'] for l in locs})==2,'exactly two independent locations')
 require(all(l['timezone']==data['site']['timezone'] for l in locs),'one confirmed time zone')
 from zoneinfo import ZoneInfo
 try:ZoneInfo(data['site']['timezone'])
 except Exception:require(False,'unknown time zone')
 lanes=data.get('distribution_lanes',[]);require(len(lanes)==1,'exactly one lane')
 lane=lanes[0];ids={l['id'] for l in locs};require({lane['origin_location_id'],lane['destination_location_id']}==ids,'lane origin and destination must be distinct and declared')
 require(data['site']['id']==lane['destination_location_id'],'primary planning site is destination; demand remains there')
 items=data['items'];require(len(items)==1 and items[0]['kind']=='FINISHED','one existing finished inventory item; no production')
 item=items[0];require(lane['item_id']==item['id'] and lane['uom']==item['base_uom'],'lane item/unit identity')
 require(lane['status']=='CONFIRMED_PATH' and lane['lead_time_seconds']>0,'confirmed path and positive continuous-time leadtime')
 require(Decimal(lane['minimum_transfer_quantity'])==Decimal(lane['transfer_multiple'])==1,'only unit minimum/multiple; no MOQ strategy')
 require(datetime.fromisoformat(lane['effective_from'])<=t0 and datetime.fromisoformat(lane['effective_to'])>=end,'lane must cover whole horizon')
 stock=data['inventory_positions'];require(len(stock)==2 and {s['location_id'] for s in stock}==ids,'one opening balance per location, no duplicate')
 for s in stock:
  require(s['item_id']==item['id'] and s['uom']==item['base_uom'] and s['as_of']==data['planning_at'],'inventory identity/unit/snapshot')
  require(s['physical_lot_ref'] is None and Decimal(s['quality_blocked'])==Decimal(s['other_nonnettable'])==0,'only released net opening stock; no lot/QC combination')
 require(1<=len(data['demand_lines'])<=8 and len({d['id'] for d in data['demand_lines']})==len(data['demand_lines']),'one/eight independent demand lines')
 for d in data['demand_lines']:
  require(d['item_id']==item['id'] and d['location_id']==lane['destination_location_id'] and d['uom']==item['base_uom'],'demand item/unit/destination')
  require(t0<=datetime.fromisoformat(d['due_at'])<=end and Decimal(d['minimum_shipment'])<=Decimal(d['quantity']),'demand due and shipment')

def native(data):
 from planning_adapter import engine_time,duration,decimal_text
 zone=data['site']['timezone'];lane=data['distribution_lanes'][0]
 return {'parameter':[{'name':'currentdate','value':engine_time(data['planning_at'],zone).replace('T',' ')},
  {'name':'plan.move_approved_early','value':'0'},{'name':'plan.fixBrokenSupplyPath','value':'false'}],
  'location':[{'name':r['id']} for r in data['locations']],'customer':[{'name':'CUSTOMER-DEMO'}],
  'item':[{'name':r['id']} for r in data['items']],'calendar':[],'calendarbucket':[],'resource':[],
  'operation':[],'operationresource':[],'operationmaterial':[],
  'buffer':[{'id':i+1,'item':s['item_id'],'location':s['location_id'],'batch':'','onhand':s['on_hand']} for i,s in enumerate(data['inventory_positions'])],
  'supplier':[],'itemsupplier':[],
  'itemdistribution':[{'id':1,'item':lane['item_id'],'origin':lane['origin_location_id'],'location':lane['destination_location_id'],
   'leadtime':duration(lane['lead_time_seconds']),'sizeminimum':lane['minimum_transfer_quantity'],'sizemultiple':lane['transfer_multiple'],
   'effective_start':engine_time(lane['effective_from'],zone),'effective_end':engine_time(lane['effective_to'],zone),'priority':1,'source':lane['id']}],
  'demand':[{'name':r['id'],'item':r['item_id'],'location':r['location_id'],'customer':'CUSTOMER-DEMO','quantity':r['quantity'],
   'due':engine_time(r['due_at'],zone),'status':'open','priority':r['priority'],'minshipment':r['minimum_shipment'],'maxlateness':duration(r['maximum_lateness_seconds'])} for r in data['demand_lines']]}

def suggestions(data,rows,run_id):
 from planning_adapter import decimal_text
 from zoneinfo import ZoneInfo
 lane=data['distribution_lanes'][0];zone=ZoneInfo(data['site']['timezone'])
 def at(v):
  d=datetime.fromisoformat(v.replace('Z','+00:00'));return (d.replace(tzinfo=zone) if d.tzinfo is None else d.astimezone(zone)).isoformat()
 return [{'id':run_id+':DO:'+str(r['reference']),'engine_ref':str(r['reference']),'lane_id':lane['id'],'item_id':r['item'],
  'origin_location_id':r.get('origin'),'destination_location_id':r['destination'],'quantity':decimal_text(r['quantity']),
  'uom':lane['uom'],'ship_at':at(r['startdate']),'receipt_at':at(r['enddate']),'source_status':r['status'],'origin':'PROPOSED',
  'evidence_ids':['ENGINE:distributionorder:'+str(r['reference'])]} for r in rows]

def audit(data,raw,*,run_id=None):
 """Reconstruct two balances and transit from actual DO/DLVR and native flow order.

 Inventory initialization is verified once, not added on top of opening stock.
 The report never changes input, selects a route or repairs a solver result.
 """
 from collections import Counter,defaultdict
 from zoneinfo import ZoneInfo
 from datetime import timedelta
 import json,hashlib
 from calculation_validation import evidence_rows
 issues=[];missing=[];zone=ZoneInfo(data['site']['timezone']);lane=data['distribution_lanes'][0]
 ledgers=[];initialization=[];transit=[];summary={};native_events=[]
 def check(ok,code,ref=None):
  if not ok:issues.append({'code':code,'reference':ref})
 def q(v):
  x=Decimal(str(v))
  if not x.is_finite():raise ValueError('nonfinite quantity')
  return x
 def at(v):
  d=datetime.fromisoformat(str(v).replace('Z','+00:00'));return d.replace(tzinfo=zone) if d.tzinfo is None else d.astimezone(zone)
 def get(name):
  value=evidence_rows(raw.get(name))
  if value is None:missing.append(name)
  return value
 def event(ref,loc,when,quantity):return (str(ref),loc,at(when).isoformat(),str(q(quantity).normalize()))
 t0=at(data['planning_at']);end=at(data['horizon']['end_at']);item=data['items'][0]['id']
 stocks={s['location_id']:q(s['on_hand']) for s in data['inventory_positions']};demands={d['id']:d for d in data['demand_lines']}
 expected_hash=hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
 if run_id is None:missing.append('expected_run_id')
 material=get('operationplanmaterial');dos=get('distributionorder');deliveries=get('deliveryorder');demand_rows=get('demand')
 # Known violations are retained even if another required collection is absent.
 for r in material or []:
  try:
   if 'onhand' in r:check(q(r['onhand'])>=0,'DISTRIBUTION_NEGATIVE_REPORTED_STOCK',r.get('id'))
  except (ValueError,TypeError):missing.append('unreadable_material_balance')
 try:
  for phase in ['pre','post']:
   snap=raw.get('fixed_batch_'+phase)
   if not isinstance(snap,dict):missing.append('native_'+phase);continue
   for field,expected in [('run_id',run_id),('input_hash',expected_hash)]:
    if field not in snap:missing.append('native_'+phase+':'+field)
    elif expected is not None:check(snap[field]==expected,'DISTRIBUTION_'+field.upper()+'_MISMATCH',phase)
   if 'move_approved_early' not in snap:missing.append('native_'+phase+':move_approved_early')
   else:check(snap['move_approved_early']==0,'DISTRIBUTION_GLOBAL_PROTECTION',phase)
   model=snap.get('distribution_model')
   if not isinstance(model,dict):missing.append('native_'+phase+':distribution_model');continue
   if 'fix_broken_supply_path' not in model:missing.append('native_'+phase+':fix_broken_supply_path')
   else:check(model['fix_broken_supply_path']=='false','DISTRIBUTION_UNKNOWN_SUPPLY_PROTECTION',phase)
   if 'locations' not in model:missing.append('native_'+phase+':locations')
   else:check(set(model['locations'])==set(stocks),'DISTRIBUTION_NATIVE_LOCATIONS',phase)
   routes=model.get('routes')
   if not isinstance(routes,list):missing.append('native_'+phase+':routes')
   else:
    check(len(routes)==1,'DISTRIBUTION_NATIVE_LANE_COUNT',phase)
    for r in routes:
     check((r['item'],r.get('origin'),r.get('destination'))==(item,lane['origin_location_id'],lane['destination_location_id']),'DISTRIBUTION_NATIVE_LANE_IDENTITY',phase)
     if 'source' not in r:missing.append('native_'+phase+':lane_source')
     else:check(r['source']==lane['id'],'DISTRIBUTION_NATIVE_LANE_SOURCE',phase)
     check(q(r['leadtime'])==q(lane['lead_time_seconds']) and q(r['size_minimum'])==q(lane['minimum_transfer_quantity']) and q(r['size_multiple'])==q(lane['transfer_multiple']),'DISTRIBUTION_NATIVE_LANE_TERMS',phase)
     check(at(r['effective_start'])==at(lane['effective_from']) and at(r['effective_end'])==at(lane['effective_to']),'DISTRIBUTION_NATIVE_EFFECTIVITY',phase)
   model_demands=model.get('demands')
   if not isinstance(model_demands,list):missing.append('native_'+phase+':demands')
   else:
    check({r['name'] for r in model_demands}==set(demands),'DISTRIBUTION_NATIVE_DEMAND_IDENTITIES',phase)
    for r in model_demands:
     d=demands.get(r['name'])
     check(d is not None and (r['item'],r['location'],q(r['quantity']),at(r['due']))==(d['item_id'],d['location_id'],q(d['quantity']),at(d['due_at'])),'DISTRIBUTION_NATIVE_DEMAND_FACTS',r['name'])
   buffers=model.get('buffers')
   if not isinstance(buffers,list):missing.append('native_'+phase+':buffers')
   else:
    check(len(buffers)==2 and {b['location'] for b in buffers}==set(stocks),'DISTRIBUTION_NATIVE_BUFFER_IDENTITIES',phase)
    for b in buffers:check(b['item']==item and b['location'] in stocks and q(b['opening'])==stocks.get(b['location']),'DISTRIBUTION_OPENING_STOCK_MISMATCH',b['location'])
  expected_events=Counter();expected_dos={};expected_deliveries={}
  for r in dos or []:
   ref=str(r['reference']);check(ref not in expected_dos,'DISTRIBUTION_DUPLICATE_DO',ref);expected_dos[ref]=r
   check((r['item'],r.get('origin'),r.get('destination'))==(item,lane['origin_location_id'],lane['destination_location_id']),'DISTRIBUTION_DO_IDENTITY',ref)
   check(r['status']=='proposed','DISTRIBUTION_PROPOSED_NOT_COMMITMENT',ref)
   start,finish=at(r['startdate']),at(r['enddate']);quantity=q(r['quantity'])
   check(quantity>0 and t0<=start<finish<=end,'DISTRIBUTION_SHIPPING_WINDOW',ref)
   check((finish-start).total_seconds()==lane['lead_time_seconds'],'DISTRIBUTION_TRANSPORT_LEADTIME',ref)
   expected_events[event(ref,lane['origin_location_id'],start,-quantity)]+=1
   expected_events[event(ref,lane['destination_location_id'],finish,quantity)]+=1
  by_demand=defaultdict(Decimal);dates=defaultdict(list)
  for r in deliveries or []:
   ref=str(r['reference']);check(ref not in expected_deliveries,'DISTRIBUTION_DUPLICATE_DELIVERY',ref);expected_deliveries[ref]=r
   d=demands.get(r.get('demand'));check(d is not None,'DISTRIBUTION_UNKNOWN_DEMAND',ref)
   if d is None:continue
   quantity=q(r['quantity']);when=at(r['enddate'])
   check(r['item']==item and r['location']==d['location_id'] and r['status']=='proposed','DISTRIBUTION_DELIVERY_IDENTITY',ref)
   check(quantity>0 and t0<=when<=end and when<=at(d['due_at'])+timedelta(seconds=d['maximum_lateness_seconds']),'DISTRIBUTION_DEMAND_TIME',ref)
   by_demand[d['id']]+=quantity;dates[d['id']].append(when)
   expected_events[event(ref,d['location_id'],when,-quantity)]+=1
  if demand_rows is not None and deliveries is not None:
   check({r['name'] for r in demand_rows}==set(demands),'DISTRIBUTION_RESULT_DEMAND_SET')
   for r in demand_rows:
    check(q(r.get('plannedquantity') or 0)==by_demand[r['name']]<=q(demands[r['name']]['quantity']),'DISTRIBUTION_DEMAND_DOUBLE_ALLOCATION',r['name'])
    check((at(r['deliverydate']) if r.get('deliverydate') else None)==max(dates[r['name']],default=None),'DISTRIBUTION_DELIVERY_DATE',r['name'])
  post_snapshot=raw.get('fixed_batch_post',{})
  post_model=post_snapshot.get('distribution_model',{}) if isinstance(post_snapshot,dict) else {}
  native_order_rows=post_model.get('orders')
  inventory_orders={str(o['reference']):o for o in native_order_rows or [] if o['operation_class']=='operation_inventory'}
  api_events=Counter();api_initializations=Counter()
  for r in material or []:
   check(r['item']==item and r['location'] in stocks,'DISTRIBUTION_MATERIAL_IDENTITY',r.get('id'))
   opening_order=inventory_orders.get(str(r['operationplan']))
   if opening_order:
    check(r['location']==opening_order['location'] and q(r['quantity'])==stocks.get(r['location']) and q(r['onhand'])==stocks.get(r['location']) and at(r['flowdate'])==at(opening_order['start']),'DISTRIBUTION_API_OPENING_INITIALIZATION',r.get('id'))
    api_initializations[event(r['operationplan'],r['location'],r['flowdate'],r['quantity'])]+=1
    continue
   api_events[event(r['operationplan'],r['location'],r['flowdate'],r['quantity'])]+=1
  if material is not None and dos is not None and deliveries is not None and isinstance(native_order_rows,list):
   check(api_events==expected_events,'DISTRIBUTION_DOUBLE_ENTRY_MATERIAL_EVENTS')
  post=raw.get('fixed_batch_post',{});model=post.get('distribution_model',{}) if isinstance(post,dict) else {}
  orders=model.get('orders')
  if not isinstance(orders,list):missing.append('native_post:orders')
  else:
   actual_dos={str(r['reference']):r for r in orders if r['order_type']=='DO'}
   if dos is not None:
    check(set(actual_dos)==set(expected_dos),'DISTRIBUTION_NATIVE_DO_IDENTITIES')
    for ref,r in actual_dos.items():
     old=expected_dos.get(ref)
     if old:check((r['item'],r.get('origin'),r['location'],q(r['quantity']),at(r['start']),at(r['end']),r['status'])==(old['item'],old.get('origin'),old['destination'],q(old['quantity']),at(old['startdate']),at(old['enddate']),old['status']),'DISTRIBUTION_NATIVE_DO_READBACK',ref)
   check(not any(r['order_type'] in {'MO','PO'} for r in orders),'DISTRIBUTION_UNDECLARED_REPLENISHMENT')
  buffers=model.get('buffers')
  if isinstance(buffers,list):
   complete_events=all(isinstance(b.get('events'),list) for b in buffers)
   end_balances={};actual_native=Counter();native_initializations=Counter()
   for b in buffers:
    loc=b['location'];balance=stocks.get(loc,Decimal(0));opening=[];rows=[{'kind':'OPENING_SNAPSHOT','at':t0.isoformat(),'quantity':str(balance),'balance':str(balance)}];previous=t0;minimum=balance
    if 'events' not in b:missing.append('native_post:events:'+loc);continue
    for index,f in enumerate(b['events']):
     quantity=q(f['quantity']);when=at(f['at']);ref=str(f['reference'])
     if f['operation_class']=='operation_inventory':
      opening.append(f);check(quantity==stocks.get(loc) and q(f['onhand'])==stocks.get(loc) and when<=t0,'DISTRIBUTION_DUPLICATE_OPENING',loc)
      native_initializations[event(ref,loc,when,quantity)]+=1
      initialization.append({'location_id':loc,**f});continue
     check(t0<=when<=end and when>=previous,'DISTRIBUTION_NATIVE_FLOW_ORDER',loc);previous=when
     balance+=quantity;minimum=min(minimum,balance)
     check(balance>=0 and q(f['onhand'])==balance,'DISTRIBUTION_NEGATIVE_OR_INCONSISTENT_BALANCE',loc)
     actual_native[event(ref,loc,when,quantity)]+=1
     native_events.append((when,loc,quantity))
     rows.append({'kind':'PLANNED_FLOW','native_index':index,'reference':ref,'order_type':f['order_type'],'at':when.isoformat(),'quantity':str(quantity),'balance':str(balance)})
    check(len(opening)==(1 if stocks.get(loc) else 0),'DISTRIBUTION_OPENING_INITIALIZATION_COUNT',loc)
    end_balances[loc]=balance;ledgers.append({'location_id':loc,'item_id':item,'uom':lane['uom'],'opening':str(stocks.get(loc)),
      'ending':str(balance),'minimum':str(minimum),'events':rows})
   if material is not None and complete_events and isinstance(native_order_rows,list):
    check(actual_native==api_events,'DISTRIBUTION_NATIVE_MATERIAL_RECONCILIATION')
    check(native_initializations==api_initializations,'DISTRIBUTION_NATIVE_INITIALIZATION_RECONCILIATION')
   times=sorted({t0,end}|{e[0] for e in native_events}|{at(r[k]) for r in dos or [] for k in ['startdate','enddate']}) if complete_events and dos is not None and deliveries is not None else []
   for when in times:
    local=sum(stocks.values())+sum(delta for date,_,delta in native_events if date<=when)
    moving=sum((q(r['quantity']) for r in dos or [] if at(r['startdate'])<=when<at(r['enddate'])),Decimal(0))
    consumed=sum((q(r['quantity']) for r in deliveries or [] if at(r['enddate'])<=when),Decimal(0))
    check(moving>=0 and local+moving+consumed==sum(stocks.values()),'DISTRIBUTION_TRANSIT_CONSERVATION',when.isoformat())
    transit.append({'at':when.isoformat(),'available_in_locations':str(local),'planned_in_transit':str(moving),'demand_consumed':str(consumed)})
   summary={'locations':ledgers,'planned_dispatch_quantity':str(sum((q(r['quantity']) for r in dos),Decimal(0))) if dos is not None else None,
     'planned_fulfillment_quantity':str(sum(by_demand.values())) if deliveries is not None else None,'ending_in_transit':transit[-1]['planned_in_transit'] if transit else None,
     'uom':lane['uom'],'lane':lane,'in_transit_timeline':transit,'initialization_events':initialization,
     'execution_status':'SIMULATION_PROPOSED_NOT_DISPATCHED'}
  else:missing.append('native_post:buffers')
  if raw.get('purchaseorder') or raw.get('manufacturingorder') or raw.get('workorder'):check(False,'DISTRIBUTION_UNDECLARED_SUPPLY')
 except (KeyError,ValueError,TypeError,ArithmeticError) as exc:missing.append('unreadable_distribution_evidence:'+type(exc).__name__)
 return {'status':'FAIL' if issues else 'NOT_CHECKED' if missing else 'PASS','scope':'single_lane_native_double_entry_and_transit',
  'issues':issues,'missing_evidence':sorted(set(missing)),'not_checked':['physical_dispatch_receipt','enterprise_execution_authorization'],
  'unsupported':['confirmed_or_in_transit_DO','partial_receiving','multi_lane_network','manufacturing_or_procurement_combinations'],
  'summary':summary or None}
