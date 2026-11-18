"""Bounded three-location procurement and proposed distribution, one native run.

This independent profile doesn't widen the former single-lane contract. All
quantities are in one base unit; physical frozen stock stays outside net buffers.
"""
from datetime import datetime
from decimal import Decimal

PROFILE='network-distribution-confirmed-supply-v1'
def enabled(data):return data.get('rules',{}).get('manufacturing_profile')==PROFILE


def validate_joint_lanes(data,require):
    """Acyclic, source-released internal supply paths in the enterprise model."""
    items={i['id']:i for i in data['items']};stocks={(s['item_id'],s['location_id']):s for s in data['inventory_positions']}
    seen=set();origins=set();destinations=set();start=datetime.fromisoformat(data['planning_at']);end=datetime.fromisoformat(data['horizon']['end_at'])
    for lane in data.get('distribution_lanes',[]):
        identity=lane['item_id'],lane['origin_location_id'],lane['destination_location_id']
        require(identity not in seen and identity[1]!=identity[2],'DUPLICATE_OR_CYCLIC_DISTRIBUTION');seen.add(identity)
        require(lane['status']=='CONFIRMED_PATH' and lane['uom']==items[identity[0]]['base_uom'],'DISTRIBUTION_SCOPE_OR_UNIT')
        require(all((identity[0],loc) in stocks and stocks[identity[0],loc].get('on_hand') is not None for loc in identity[1:]),'UNKNOWN_REACHABLE_INVENTORY')
        require(lane.get('destination_receiving_qc',{}).get('wait_seconds')==0 and lane['destination_receiving_qc'].get('basis')=='SOURCE_RELEASED_SEALED_INTERNAL_TRANSFER' and bool(lane['destination_receiving_qc'].get('source')),'DISTRIBUTION_DESTINATION_QC_NOT_CERTIFIED')
        require(Decimal(str(lane['lead_time_seconds']))>0 and Decimal(str(lane['minimum_transfer_quantity']))>0 and Decimal(str(lane['transfer_multiple']))>0,'DISTRIBUTION_TERMS')
        require(datetime.fromisoformat(lane['effective_from'])<=start<end<=datetime.fromisoformat(lane['effective_to']),'DISTRIBUTION_EFFECTIVITY')
        origins.add((identity[0],identity[1]));destinations.add((identity[0],identity[2]))
    require(not origins.intersection(destinations),'CYCLIC_OR_MULTIHOP_DISTRIBUTION_NOT_CERTIFIED')


def map_joint_lanes(data,model):
    from planning_adapter import duration,engine_time
    zone=data['site']['timezone']
    model['location']=[{'name':l['id']} for l in data['locations'] if l['id'] in {s['location_id'] for s in data['inventory_positions']}]
    model['itemdistribution']=[dict(id=i,item=r['item_id'],origin=r['origin_location_id'],location=r['destination_location_id'],
        leadtime=duration(r['lead_time_seconds']),sizeminimum=r['minimum_transfer_quantity'],sizemultiple=r['transfer_multiple'],
        effective_start=engine_time(r['effective_from'],zone),effective_end=engine_time(r['effective_to'],zone),priority=r['priority'],source=r['id']) for i,r in enumerate(data['distribution_lanes'],1)]
    return model

def validate(data):
    from planning_adapter import InputError
    from confirmed_supply import validate_receipts
    from priority_supply import TERM_FIELDS
    from inventory_availability import net_available
    def require(ok,why):
        if not ok:raise InputError('UNSUPPORTED_NETWORK_DISTRIBUTION: '+why)
    require(enabled(data),'explicit independent profile required')
    require(not any(data.get(k) for k in ['production_recipes','bom_revisions','routing_revisions','resources','calendars','reservations','previous_manufacturing_suggestions','skills','resource_skills','distribution_orders','purchase_receipts']),'manufacturing, resource calendars, existing DO and partial receipts unsupported')
    require('procurement_strategy' not in data['rules'] and not any(TERM_FIELDS & s.keys() for s in data['supply_sources']),'procurement priority/cost/capacity strategy unsupported')
    require(data['rules'].get('move_approved_early')==0 and data['rules'].get('fix_broken_supply_path') is False and data['rules'].get('erase_previous_proposed') is True,'explicit native protection settings')
    t0=datetime.fromisoformat(data['planning_at']);end=datetime.fromisoformat(data['horizon']['end_at'])
    require(datetime.fromisoformat(data['horizon']['start_at'])==t0<end,'horizon')
    from zoneinfo import ZoneInfo
    try:ZoneInfo(data['site']['timezone'])
    except Exception:require(False,'unknown timezone')
    locs=data['locations'];ids={l['id'] for l in locs};origin=data['site']['id']
    require(len(locs)==len(ids)==3 and origin in ids,'three independent locations and one origin site')
    require(all(l['timezone']==data['site']['timezone'] for l in locs),'one timezone')
    require(len(data['items'])==1 and data['items'][0]['kind']=='RAW','one purchased RAW material, no manufacturing')
    item=data['items'][0];itemid=item['id'];uom=item['base_uom'];lanes=data['distribution_lanes']
    require(len(lanes)==2 and len({r['id'] for r in lanes})==2,'two distinct routes')
    require({r['destination_location_id'] for r in lanes}==ids-{origin},'one route to each destination')
    for lane in lanes:
        require(lane['origin_location_id']==origin and lane['item_id']==itemid and lane['uom']==uom,'route identity/unit')
        require(lane['status']=='CONFIRMED_PATH' and lane['lead_time_seconds']>0,'confirmed route positive leadtime')
        require(Decimal(lane['minimum_transfer_quantity'])==Decimal(lane['transfer_multiple'])==1,'only unit minimum and multiple')
        require(datetime.fromisoformat(lane['effective_from'])<=t0 and datetime.fromisoformat(lane['effective_to'])>=end,'route covers horizon')
    stocks=data['inventory_positions']
    require(len(stocks)==3 and {r['location_id'] for r in stocks}==ids,'one stock snapshot per location')
    for s in stocks:
        require(s['item_id']==itemid and s['uom']==uom and s['as_of']==data['planning_at'],'stock identity/unit/time')
        require(s['physical_lot_ref'] is None and 'quality_availability' not in s,'no lot allocation or future QC release')
        require(net_available(s)>=0,'negative opening net stock')
    require(len(data['suppliers'])==len(data['supply_sources'])==len(data['existing_orders'])==1,'one supplier, source and complete unreceived confirmed PO')
    source=data['supply_sources'][0]
    require(source['item_id']==itemid and source['location_id']==origin and source['supplier_id']==data['suppliers'][0]['id'],'origin supply identity')
    require(source['minimum_order_quantity']==source['order_multiple']=='1' and source['batch_window_seconds']==0,'no procurement batching policy')
    require(datetime.fromisoformat(source['effective_from'])<=t0 and datetime.fromisoformat(source['effective_to'])>=end,'source covers horizon')
    validate_receipts(data)
    ds=data['demand_lines']
    require(2<=len(ds)<=8 and len({d['id'] for d in ds})==len(ds),'two to eight distinct demands')
    require({d['location_id'] for d in ds}==ids-{origin},'competing demand at both destinations')
    for d in ds:
        require(d['item_id']==itemid and d['uom']==uom and d['status']=='OPEN','open material demand identity')
        require(t0<=datetime.fromisoformat(d['due_at'])<=end and Decimal(d['minimum_shipment'])<=Decimal(d['quantity']),'demand due/shipment')
    option=data['distribution_lead_time_option'];lane=next((r for r in lanes if r['id']==option['lane_id']),None)
    require(lane is not None and lane['lead_time_seconds']<=option['maximum_lead_time_seconds']<=(end-t0).total_seconds(),'one frozen route leadtime option')

def native(data):
    from single_lane_distribution import native as old_native
    from confirmed_supply import add_native_supply
    from inventory_availability import net_available
    from planning_adapter import decimal_text,engine_time,duration
    # Reuse the unchanged native table mapping, then add the independently
    # validated routes and supply contract rather than changing old validation.
    model=old_native(data)
    for row,stock in zip(model['buffer'],data['inventory_positions']):row['onhand']=decimal_text(net_available(stock))
    model['itemdistribution']=[dict(id=i,item=r['item_id'],origin=r['origin_location_id'],location=r['destination_location_id'],
        leadtime=duration(r['lead_time_seconds']),sizeminimum=r['minimum_transfer_quantity'],sizemultiple=r['transfer_multiple'],
        effective_start=engine_time(r['effective_from'],data['site']['timezone']),effective_end=engine_time(r['effective_to'],data['site']['timezone']),priority=1,source=r['id']) for i,r in enumerate(data['distribution_lanes'],1)]
    add_native_supply(data,model)
    return model

def suggestions(data,rows,run_id):
    from single_lane_distribution import suggestions as old_suggestions
    result=[]
    for row in rows:
        lane=next((r for r in data['distribution_lanes'] if (r['item_id'],r['origin_location_id'],r['destination_location_id'])==(row.get('item'),row.get('origin'),row.get('destination'))),None)
        if lane is None:continue # audit retains the illegal raw record as FAIL
        result+=old_suggestions({**data,'distribution_lanes':[lane]},[row],run_id)
    return result

def audit(data,raw,*,run_id=None):
    from network_distribution_audit import audit as inspect
    return inspect(data,raw,run_id=run_id)
