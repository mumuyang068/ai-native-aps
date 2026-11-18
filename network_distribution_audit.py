"""Independent reconciler, never a second ledger or a source of solver orders."""
from collections import Counter,defaultdict
from datetime import datetime,timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
import hashlib,json

def audit(data,raw,*,run_id=None):
    from calculation_validation import evidence_rows
    from confirmed_supply import validate_native_receipts
    from inventory_availability import net_available
    from planning_adapter import engine_time
    issues=[];missing=[];zone=ZoneInfo(data['site']['timezone'])
    def check(ok,code,ref=None):
        if not ok:issues.append(dict(code=code,reference=ref))
    def q(v):
        n=Decimal(str(v))
        if not n.is_finite():raise ValueError('nonfinite')
        return n
    def at(v):
        d=datetime.fromisoformat(str(v).replace('Z','+00:00'))
        return d.replace(tzinfo=zone) if d.tzinfo is None else d.astimezone(zone)
    def event(ref,loc,when,quantity):return str(ref),loc,at(when).isoformat(),str(q(quantity).normalize())
    def table(name):
        rows=evidence_rows(raw.get(name))
        if rows is None:missing.append(name)
        return rows
    stocks={s['location_id']:net_available(s) for s in data['inventory_positions']}
    item=data['items'][0]['id'];uom=data['items'][0]['base_uom'];origin=data['site']['id']
    lanes={r['id']:r for r in data['distribution_lanes']};demands={d['id']:d for d in data['demand_lines']}
    po=data['existing_orders'][0];source=data['supply_sources'][0]
    t0=at(data['planning_at']);end=at(data['horizon']['end_at'])
    expected_hash=hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    material=table('operationplanmaterial');dos=table('distributionorder');deliveries=table('deliveryorder');demand_rows=table('demand');purchases=table('purchaseorder')
    ledgers=[];transit=[];initializations=[];summary={};native_events=[]
    # Scan known hard failures before dependent parsing or missing collections.
    for r in material or []:
        try:
            if 'onhand' in r:check(q(r['onhand'])>=0,'NETWORK_NEGATIVE_REPORTED_STOCK',r.get('id'))
        except (ValueError,TypeError,ArithmeticError):missing.append('unreadable_material_balance')
    if run_id is None:missing.append('expected_run_id')
    try:
        for phase in ['pre','post']:
            snap=raw.get('fixed_batch_'+phase)
            if not isinstance(snap,dict):missing.append('native_'+phase);continue
            for field,expected in [('run_id',run_id),('input_hash',expected_hash),('move_approved_early',0)]:
                if field not in snap:missing.append('native_'+phase+':'+field)
                elif expected is not None:check(snap[field]==expected,'NETWORK_NATIVE_'+field.upper(),phase)
            model=snap.get('distribution_model')
            if not isinstance(model,dict):missing.append('native_'+phase+':distribution_model');continue
            if 'fix_broken_supply_path' not in model:missing.append('native_'+phase+':fix_broken_supply_path')
            else:check(model['fix_broken_supply_path']=='false','NETWORK_UNKNOWN_SUPPLY_PROTECTION',phase)
            if 'locations' not in model:missing.append('native_'+phase+':locations')
            else:check(set(model['locations'])==set(stocks),'NETWORK_NATIVE_LOCATIONS',phase)
            routes=model.get('routes')
            if not isinstance(routes,list):missing.append('native_'+phase+':routes')
            else:
                check(len(routes)==2,'NETWORK_NATIVE_ROUTE_COUNT',phase)
                found=[]
                for r in routes:
                    if 'source' not in r:missing.append('native_'+phase+':route_source');continue
                    lane=lanes.get(r['source']);found.append(r['source'])
                    check(lane is not None,'NETWORK_NATIVE_ROUTE_SOURCE',r['source'])
                    if lane:
                        check((r['item'],r['origin'],r['destination'])==(item,lane['origin_location_id'],lane['destination_location_id']),'NETWORK_NATIVE_ROUTE_IDENTITY',lane['id'])
                        check(q(r['leadtime'])==lane['lead_time_seconds'] and q(r['size_minimum'])==q(lane['minimum_transfer_quantity']) and q(r['size_multiple'])==q(lane['transfer_multiple']),'NETWORK_NATIVE_ROUTE_TERMS',lane['id'])
                        check(at(r['effective_start'])==at(lane['effective_from']) and at(r['effective_end'])==at(lane['effective_to']),'NETWORK_NATIVE_ROUTE_EFFECTIVITY',lane['id'])
                if len(found)==len(routes):check(Counter(found)==Counter(lanes.keys()),'NETWORK_NATIVE_ROUTE_SET',phase)
            buffers=model.get('buffers')
            if not isinstance(buffers,list):missing.append('native_'+phase+':buffers')
            else:
                check(len(buffers)==3 and {b['location'] for b in buffers}==set(stocks),'NETWORK_NATIVE_BUFFER_SET',phase)
                for b in buffers:check(b['item']==item and b['location'] in stocks and q(b['opening'])==stocks.get(b['location']),'NETWORK_OPENING_NET_STOCK',b['location'])
            ds=model.get('demands')
            if not isinstance(ds,list):missing.append('native_'+phase+':demands')
            else:
                check(Counter(r['name'] for r in ds)==Counter(demands.keys()),'NETWORK_NATIVE_DEMAND_SET',phase)
                for r in ds:
                    d=demands.get(r['name']);check(d is not None,'NETWORK_NATIVE_DEMAND_IDENTITY',r['name'])
                    if d:check((r['item'],r['location'],q(r['quantity']),at(r['due']),q(r['minshipment']),q(r['maxlateness']))==(item,d['location_id'],q(d['quantity']),at(d['due_at']),q(d['minimum_shipment']),d['maximum_lateness_seconds']),'NETWORK_NATIVE_DEMAND_FACTS',r['name'])
                    if d:
                        for field,wanted in [('priority',d['priority']),('status','open')]:
                            if r.get(field) is None:missing.append('native_'+phase+':demand:'+field)
                            else:check(r[field]==wanted,'NETWORK_NATIVE_DEMAND_'+field.upper(),r['name'])
            supply=snap.get('confirmed_supply_model')
            if not isinstance(supply,dict):missing.append('native_'+phase+':confirmed_supply_model')
            else:
                paths=supply.get('paths');keys=supply.get('itemsuppliers')
                if not isinstance(paths,list) or not isinstance(keys,list):missing.append('native_'+phase+':supply_paths')
                else:
                    check(keys==[dict(item=item,supplier=source['supplier_id'],location=origin)] and len(paths)==1,'NETWORK_NATIVE_SUPPLY_IDENTITY',phase)
                    for p in paths:
                        check(q(p['lead_time_seconds'])==source['lead_time_seconds'] and q(p['minimum'])==q(source['minimum_order_quantity']) and q(p['multiple'])==q(source['order_multiple']),'NETWORK_NATIVE_SUPPLY_TERMS',phase)
                        check(not p['loads'] and len(p['flows'])==1 and p['flows'][0]['item']==item and q(p['flows'][0]['quantity'])==1 and q(p['flows'][0]['offset_seconds'])==source['receiving_qc']['wait_seconds'],'NETWORK_NATIVE_QC_PATH',phase)
        receipt=validate_native_receipts(data,raw)
        issues.extend(dict(r,reference=r.get('reference')) for r in receipt['issues']);missing+=receipt['missing_evidence']
        expected=Counter();by_demand=defaultdict(Decimal);dates=defaultdict(list)
        if purchases is not None:
            check(Counter(str(r['reference']) for r in purchases)==Counter([po['id']]),'NETWORK_UNDECLARED_OR_MISSING_PO')
            for r in purchases:
                check(str(r['reference'])==po['id'] and (r['status'],r['item'],r['location'],r['supplier'],q(r['quantity']),at(r['startdate']),at(r['enddate']))==('confirmed',item,origin,po['supplier_id'],q(po['remaining_quantity']),at(po.get('planned_start_at') or po['ordered_at']),at(po['expected_receipt_at'])),'NETWORK_CONFIRMED_PO_DRIFT',r['reference'])
        expected[event(po['id'],origin,po['available_at'],po['remaining_quantity'])]+=1
        seen=set()
        for r in dos or []:
            ref=str(r['reference']);check(ref not in seen,'NETWORK_DUPLICATE_DO',ref);seen.add(ref)
            lane=next((l for l in lanes.values() if (item,l['origin_location_id'],l['destination_location_id'])==(r['item'],r.get('origin'),r['destination'])),None)
            check(lane is not None and r['status']=='proposed','NETWORK_DO_IDENTITY_OR_STATUS',ref)
            start,finish=at(r['startdate']),at(r['enddate']);qty=q(r['quantity'])
            check(qty>0 and t0<=start<finish<=end,'NETWORK_SHIPPING_WINDOW',ref)
            if lane:
                check((finish-start).total_seconds()==lane['lead_time_seconds'],'NETWORK_TRANSPORT_LEADTIME',ref)
                check(qty>=q(lane['minimum_transfer_quantity']) and qty%q(lane['transfer_multiple'])==0,'NETWORK_TRANSFER_BATCH_TERMS',ref)
            expected[event(ref,r.get('origin'),start,-qty)]+=1;expected[event(ref,r['destination'],finish,qty)]+=1
        seen_deliveries=set()
        for r in deliveries or []:
            ref=str(r['reference']);check(ref not in seen_deliveries,'NETWORK_DUPLICATE_DLVR',ref);seen_deliveries.add(ref)
            d=demands.get(r.get('demand'));check(d is not None,'NETWORK_UNKNOWN_DEMAND',ref)
            if d is None:continue
            qty=q(r['quantity']);when=at(r['enddate'])
            check(r['item']==item and r['location']==d['location_id'] and r['status']=='proposed','NETWORK_DLVR_IDENTITY',ref)
            check(qty>0 and t0<=when<=end and when<=at(d['due_at'])+timedelta(seconds=d['maximum_lateness_seconds']),'NETWORK_DLVR_TIME',ref)
            by_demand[d['id']]+=qty;dates[d['id']].append(when);expected[event(ref,d['location_id'],when,-qty)]+=1
        if demand_rows is not None and deliveries is not None:
            check(Counter(r['name'] for r in demand_rows)==Counter(demands.keys()),'NETWORK_RESULT_DEMAND_SET')
            for r in demand_rows:
                if r.get('plannedquantity') is None:missing.append('demand:'+r['name']+':plannedquantity');continue
                check(q(r['plannedquantity'])==by_demand[r['name']]<=q(demands[r['name']]['quantity']),'NETWORK_DEMAND_ALLOCATION',r['name'])
                check((at(r['deliverydate']) if r.get('deliverydate') else None)==max(dates[r['name']],default=None),'NETWORK_DEMAND_DATE',r['name'])
        model=(raw.get('fixed_batch_post') or {}).get('distribution_model',{})
        orders=model.get('orders');inventory_orders={}
        if not isinstance(orders,list):missing.append('native_post:orders')
        else:
            inventory_orders={str(r['reference']):r for r in orders if r['operation_class']=='operation_inventory'}
            native_dos=[r for r in orders if r['order_type']=='DO']
            if dos is not None:
                native=Counter((str(r['reference']),r['item'],r['origin'],r['location'],q(r['quantity']),at(r['start']),at(r['end']),r['status']) for r in native_dos)
                api=Counter((str(r['reference']),r['item'],r['origin'],r['destination'],q(r['quantity']),at(r['startdate']),at(r['enddate']),r['status']) for r in dos)
                check(native==api,'NETWORK_NATIVE_DO_READBACK')
            if deliveries is not None:
                native_dlvr=[r for r in orders if r['order_type']=='DLVR']
                if any('demand' not in r for r in native_dlvr):missing.append('native_post:delivery_demand')
                else:
                    native=Counter((str(r['reference']),r['item'],r['location'],q(r['quantity']),at(r['end']),r['status'],r['demand']) for r in native_dlvr)
                    api=Counter((str(r['reference']),r['item'],r['location'],q(r['quantity']),at(r['enddate']),r['status'],r['demand']) for r in deliveries)
                    check(native==api,'NETWORK_NATIVE_DLVR_READBACK')
            check(not any(r['order_type']=='MO' for r in orders),'NETWORK_UNSUPPORTED_MANUFACTURING')
            check(Counter(str(r['reference']) for r in orders if r['order_type']=='PO')==Counter([po['id']]),'NETWORK_NATIVE_UNDECLARED_PO')
        api_events=Counter();api_initial=Counter()
        for r in material or []:
            check(r['item']==item and r['location'] in stocks,'NETWORK_MATERIAL_IDENTITY',r.get('id'))
            if str(r['operationplan']) in inventory_orders:
                check(q(r['quantity'])==stocks.get(r['location']) and q(r['onhand'])==stocks.get(r['location']),'NETWORK_API_OPENING',r.get('id'))
                api_initial[event(r['operationplan'],r['location'],r['flowdate'],r['quantity'])]+=1
            else:api_events[event(r['operationplan'],r['location'],r['flowdate'],r['quantity'])]+=1
        if all(v is not None for v in [material,dos,deliveries,purchases]) and isinstance(orders,list):check(api_events==expected,'NETWORK_DOUBLE_ENTRY_MATERIAL_EVENTS')
        buffers=model.get('buffers')
        if not isinstance(buffers,list):missing.append('native_post:buffers')
        else:
            native_flow=Counter();native_initial=Counter();complete=True
            for b in buffers:
                loc=b['location'];balance=stocks.get(loc);minimum=balance;rows=[];opening=[];previous=t0
                if balance is None:check(False,'NETWORK_UNKNOWN_BUFFER',loc);continue
                events=b.get('events')
                if not isinstance(events,list):missing.append('native_post:events:'+loc);complete=False;continue
                for index,f in enumerate(events):
                    qty=q(f['quantity']);when=at(f['at']);ref=str(f['reference'])
                    if f['operation_class']=='operation_inventory':
                        check(qty==stocks[loc] and q(f['onhand'])==stocks[loc] and when<=t0,'NETWORK_OPENING_INITIALIZATION',loc)
                        opening.append(f);initializations.append(dict(location_id=loc,**f));native_initial[event(ref,loc,when,qty)]+=1;continue
                    check(t0<=when<=end and when>=previous,'NETWORK_NATIVE_FLOW_ORDER',loc);previous=when
                    balance+=qty;minimum=min(minimum,balance)
                    check(balance>=0 and balance==q(f['onhand']),'NETWORK_NEGATIVE_OR_INCONSISTENT_BALANCE',loc)
                    native_flow[event(ref,loc,when,qty)]+=1;native_events.append((when,loc,qty))
                    rows.append(dict(native_index=index,reference=ref,order_type=f['order_type'],at=when.isoformat(),quantity=str(qty),balance=str(balance)))
                check(len(opening)==(1 if stocks[loc] else 0),'NETWORK_OPENING_COUNT',loc)
                stock=next(s for s in data['inventory_positions'] if s['location_id']==loc)
                ledgers.append(dict(item_id=item,location_id=loc,uom=uom,physical_opening=stock['on_hand'],quality_blocked=stock['quality_blocked'],other_nonnettable=stock['other_nonnettable'],opening=str(stocks[loc]),ending=str(balance),minimum=str(minimum),events=rows))
            if material is not None and isinstance(orders,list) and complete:
                check(native_flow==api_events,'NETWORK_NATIVE_MATERIAL_RECONCILIATION');check(native_initial==api_initial,'NETWORK_NATIVE_INITIALIZATION_RECONCILIATION')
            if complete and dos is not None and deliveries is not None and purchases is not None:
                times=sorted({t0,end,at(po['expected_receipt_at']),at(po['available_at'])}|{e[0] for e in native_events}|{at(r[k]) for r in dos for k in ['startdate','enddate']})
                for when in times:
                    local=sum(stocks.values())+sum(delta for date,_,delta in native_events if date<=when)
                    moving=sum((q(r['quantity']) for r in dos if at(r['startdate'])<=when<at(r['enddate'])),Decimal(0))
                    consumed=sum((q(r['quantity']) for r in deliveries if at(r['enddate'])<=when),Decimal(0))
                    available=q(po['remaining_quantity']) if at(po['available_at'])<=when else Decimal(0)
                    waiting=q(po['remaining_quantity']) if at(po['expected_receipt_at'])<=when<at(po['available_at']) else Decimal(0)
                    check(local+moving+consumed==sum(stocks.values())+available,'NETWORK_TRANSIT_CONSERVATION',when.isoformat())
                    transit.append(dict(at=when.isoformat(),available_in_locations=str(local),planned_in_transit=str(moving),purchase_qc_waiting=str(waiting),purchase_available=str(available),demand_consumed=str(consumed)))
            summary=dict(locations=ledgers,inventory=[dict(r,opening_net_available=r['opening'],ending_net_available=r['ending']) for r in ledgers],
                in_transit_timeline=transit,initialization_events=initializations,uom=uom,routes=list(lanes.values()),confirmed_purchase=po,
                execution_status='SIMULATION_PROPOSED_NOT_DISPATCHED',pegging_status='UNSUPPORTED',
                planned_dispatch_quantity=str(sum((q(r['quantity']) for r in dos),Decimal(0))) if dos is not None else None,
                planned_fulfillment_quantity=str(sum(by_demand.values())) if deliveries is not None else None)
        for name in ['manufacturingorder','workorder']:
            rows=table(name)
            if rows:check(False,'NETWORK_UNSUPPORTED_MANUFACTURING',name)
    except (KeyError,ValueError,TypeError,ArithmeticError) as exc:missing.append('unreadable_network_evidence:'+type(exc).__name__)
    return dict(status='FAIL' if issues else 'NOT_CHECKED' if missing else 'PASS',scope='network_confirmed_purchase_QC_double_entry_and_transit',
        issues=issues,missing_evidence=sorted(set(missing)),summary=summary or None,
        not_checked=['physical_dispatch_receipt','physical_QC_release','enterprise_execution_authorization'],
        unsupported=['confirmed_DO','manufacturing','partial_receipts','transport_capacity_calendars','MINCOST','order_level_pegging'])
