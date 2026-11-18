"""Single unreceived confirmed PO for the existing P3 supply profile.

Reuses F-Integrate-02 joint/model.py's native purchaseorder and supply-path
hard_safety_leadtime mapping, and joint/results.py's receipt + QC semantics.
No procurement strategy, receipt workflow or new plan store is implemented.
"""
from datetime import datetime, timedelta
from decimal import Decimal


def validate_receipts(data,*,allow_extra_qc=False):
    from planning_adapter import InputError
    def require(ok,reason):
        if not ok:raise InputError('UNSUPPORTED_CONFIRMED_SUPPLY: '+reason)
    pos=data['existing_orders'];sources=data['supply_sources']
    qc_sources=[s for s in sources if 'receiving_qc' in s]
    if not pos:
        require(not qc_sources,'receiving QC is only supported with the single confirmed supply')
        return
    require(len(pos)==1,'only one complete, unreceived confirmed PO is supported')
    po=pos[0]
    require(po['type']=='PO','existing manufacturing/transfer orders are not supported')
    require(po['source_status']=='confirmed','only explicit confirmed status is supported')
    require(all(k in po for k in ['supply_source_id','available_at','inventory_includes_receipt']),
            'source identity, available time and inventory exclusion must be explicit')
    require(Decimal(po['received_quantity'])==Decimal(po['cancelled_quantity'])==0 and
            Decimal(po['ordered_quantity'])==Decimal(po['remaining_quantity'])>0,'partial receipt/cancellation or inconsistent quantity')
    require(po['inventory_includes_receipt'] is False,'future receipt cannot already be included in opening stock')
    matches=[s for s in sources if s['id']==po['supply_source_id']]
    require(len(matches)==1,'unknown supply source')
    source=matches[0]
    require((po['item_id'],po['location_id'],po['supplier_id'])==
            (source['item_id'],source['location_id'],source['supplier_id']),'supply identity mismatch')
    require(len([s for s in sources if (s['item_id'],s['location_id'],s['supplier_id'])==
            (po['item_id'],po['location_id'],po['supplier_id'])])==1,'ambiguous native supply path')
    require(('receiving_qc' in source) and (allow_extra_qc or len(qc_sources)==1),'explicit QC evidence for the selected path is required')
    item=next((i for i in data['items'] if i['id']==po['item_id']),None)
    require(item is not None and item['base_uom']==po['uom'],'purchase unit must be the material base unit')
    for prov in [po['provenance'],source['receiving_qc']['provenance']]:
        require(all(isinstance(v,str) and v.strip() for v in prov.values()),'empty provenance is not evidence')
    start_value=po.get('planned_start_at') or po.get('ordered_at')
    require(bool(start_value),'an explicit native planned start or actual order date is required')
    ordered=datetime.fromisoformat(start_value)
    receipt,available=[datetime.fromisoformat(po[k]) for k in ['expected_receipt_at','available_at']]
    now=datetime.fromisoformat(data['planning_at']);end=datetime.fromisoformat(data['horizon']['end_at'])
    require(ordered<=receipt and now<=receipt<=available<=end,'planned start, future physical receipt and availability must be chronological and within horizon')
    if po['ordered_at'] is not None:
        require(datetime.fromisoformat(po['ordered_at'])<=now,'an actual order date cannot be in the future')
    wait=source['receiving_qc']['wait_seconds']
    require(wait<=(end-receipt).total_seconds(),'receiving QC extends beyond the planning horizon')
    require(available==receipt+timedelta(seconds=wait),'available time must equal physical receipt plus evidenced QC wait')
    require(datetime.fromisoformat(source['effective_from'])<=ordered<=receipt<=datetime.fromisoformat(source['effective_to']),
            'confirmed supply outside source validity')


def add_native_supply(data,native):
    """Shared supply-path mapping for ordinary and explicitly joint profiles."""
    from planning_adapter import duration
    native['supplier']=[{'name':s['id']} for s in data['suppliers']]
    native['itemsupplier']=[{'id':i,'item':s['item_id'],'location':s['location_id'],'supplier':s['supplier_id'],
        'leadtime':duration(s['lead_time_seconds']),'sizeminimum':s['minimum_order_quantity'],
        'sizemultiple':s['order_multiple'],'batchwindow':duration(s['batch_window_seconds'])}
        for i,s in enumerate(data['supply_sources'],1)]
    add_native_receipts(data,native)


def add_native_receipts(data,native):
    """Same reference/status/quantity/start/end mapping as joint.model.native."""
    from planning_adapter import engine_time,duration
    if not any(p['type']=='PO' for p in data['existing_orders']):return
    zone=data['site']['timezone'];po=next(p for p in data['existing_orders'] if p['type']=='PO')
    source=next(s for s in data['supply_sources'] if s['id']==po['supply_source_id'])
    for row in native['itemsupplier']:
        if (row['item'],row['location'],row['supplier'])==(po['item_id'],po['location_id'],po['supplier_id']):
            row.update(hard_safety_leadtime=duration(source['receiving_qc']['wait_seconds']),extra_safety_leadtime=duration(0))
    native['purchaseorder']=[{'reference':po['id'],'status':'confirmed','item':po['item_id'],
        'supplier':po['supplier_id'],'location':po['location_id'],'quantity':po['remaining_quantity'],
        'startdate':engine_time(po.get('planned_start_at') or po['ordered_at'],zone),'enddate':engine_time(po['expected_receipt_at'],zone)}]


def receipt_windows(data):
    """Input-identified receipt windows; not a claim of actual arrival or release."""
    return [{'id':p['id'],'external_ref':p['external_ref'],'source_status':p['source_status'],
        'item_id':p['item_id'],'supplier_id':p['supplier_id'],'location_id':p['location_id'],
        'quantity':p['remaining_quantity'],'physical_receipt_at':p['expected_receipt_at'],
        'actual_ordered_at':p['ordered_at'],'planned_start_at':p.get('planned_start_at') or p['ordered_at'],
        'available_at':p['available_at'],'supply_source_id':p['supply_source_id'],
        'qc_wait_seconds':next(s['receiving_qc']['wait_seconds'] for s in data['supply_sources'] if s['id']==p['supply_source_id']),
        'provenance':p['provenance']} for p in data['existing_orders'] if p['type']=='PO']


def validate_native_receipts(data,raw):
    """Finite PO fields and actual native supply events, before and after solve.

    This does not authenticate real receiving or inventory release. Missing
    memory capture remains NOT_CHECKED; REST readback is not a substitute.
    """
    from planning_adapter import engine_time
    issues=[];missing=[];zone=data['site']['timezone']
    expected=[p for p in data['existing_orders'] if p['type']=='PO']
    def issue(phase,code,reference):issues.append({'phase':phase,'code':code,'reference':reference})
    for phase in ['fixed_batch_pre','fixed_batch_post']:
        snap=raw.get(phase,{})
        if not isinstance(snap.get('purchaseorders'),list) or not isinstance(snap.get('buffers'),list):
            missing.append(phase);continue
        for po in expected:
            rows=[p for p in snap['purchaseorders'] if p.get('reference')==po['id']]
            if len(rows)!=1:issue(phase,'NATIVE_CONFIRMED_PO_IDENTITY',po['id']);continue
            want={'item':po['item_id'],'supplier':po['supplier_id'],'location':po['location_id'],
                'status':'confirmed','quantity':po['remaining_quantity'],
                'start':engine_time(po.get('planned_start_at') or po['ordered_at'],zone),
                'end':engine_time(po['expected_receipt_at'],zone)}
            for field,value in want.items():
                actual=rows[0].get(field)
                try:equal=Decimal(str(actual))==Decimal(value) if field=='quantity' else actual==value
                except ArithmeticError:equal=False
                if not equal:issue(phase,'NATIVE_CONFIRMED_PO_DRIFT:'+field,po['id'])
            buffers=[b for b in snap['buffers'] if b.get('item')==po['item_id'] and b.get('location')==po['location_id']]
            if len(buffers)!=1 or not isinstance(buffers[0].get('events'),list):
                missing.append(phase+':purchase_buffer_events');continue
            events=[f for f in buffers[0]['events'] if f.get('reference')==po['id']]
            try:
                equal=(len(events)==1 and Decimal(events[0]['quantity'])==Decimal(po['remaining_quantity'])
                       and events[0].get('at')==engine_time(po['available_at'],zone))
            except (KeyError,ArithmeticError,TypeError):equal=False
            if not equal:issue(phase,'NATIVE_CONFIRMED_PO_QC_EVENT',po['id'])
    return {'status':'FAIL' if issues else 'NOT_CHECKED' if missing else 'PASS',
        'scope':'joint_confirmed_purchase_native_pre_post','issues':issues,'missing_evidence':missing,
        'not_checked':['unlisted_native_fields','actual_receipt','physical_QC_release','binary_build_attestation']}
