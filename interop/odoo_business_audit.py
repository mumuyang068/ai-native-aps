"""Independent native ERP stock accounting; never produces planning balances."""
from decimal import Decimal
from collections import defaultdict
import re


def _id(value):return value[0] if isinstance(value,list) and value else value
def _number(value):return format(Decimal(str(value)).normalize(),'f')


def audit_stock(capture):
    issues=[];balances={};d=capture['documents']
    internal={r['id'] for r in d['stock.location'] if r['usage']=='internal'}
    products={r['id']:r for r in d['product.product']}
    moves=d['stock.move'];lines=d['stock.move.line'];quants=d['stock.quant']
    for table in ['stock.move','stock.move.line','stock.quant']:
        if len({r['id'] for r in d[table]})!=len(d[table]):issues.append(dict(code='DUPLICATE_PHYSICAL_IDENTITY',table=table))
    ledger=defaultdict(Decimal);physical=defaultdict(Decimal);reserved=defaultdict(Decimal)
    location_ledger=defaultdict(Decimal);location_physical=defaultdict(Decimal)
    for move in moves:
        if move['state']!='done':continue
        pid=_id(move['product_id']);q=Decimal(str(move['quantity']))
        if _id(move['product_uom'])!=_id(products[pid]['uom_id']):issues.append(dict(code='MOVEMENT_UNIT_MISMATCH',move_id=move['id']))
        details=[l for l in lines if _id(l['move_id'])==move['id']]
        if sum((Decimal(str(l['quantity'])) for l in details),Decimal(0))!=q or any(l['state']!='done' for l in details):issues.append(dict(code='MOVE_LINE_RECONCILIATION',move_id=move['id']))
        for line in details:
            if _id(line['product_id'])!=pid or _id(line['product_uom_id'])!=_id(move['product_uom']):issues.append(dict(code='MOVE_LINE_PRODUCT_UNIT_MISMATCH',move_id=move['id']))
            amount=Decimal(str(line['quantity']));source,dest=_id(line['location_id']),_id(line['location_dest_id'])
            if source in internal:location_ledger[(pid,source)]-=amount
            if dest in internal:location_ledger[(pid,dest)]+=amount
            ledger[pid]+=amount*((1 if dest in internal else 0)-(1 if source in internal else 0))
    for q in quants:
        if _id(q['location_id']) not in internal:continue
        pid=_id(q['product_id']);location_physical[(pid,_id(q['location_id']))]+=Decimal(str(q['quantity']));physical[pid]+=Decimal(str(q['quantity']));reserved[pid]+=Decimal(str(q['reserved_quantity']))
    for key in set(location_ledger)|set(location_physical):
        if location_ledger[key]!=location_physical[key]:issues.append(dict(code='PHYSICAL_LOCATION_MOVEMENT_BALANCE',product_id=key[0],location_id=key[1]))
    for pid in products:
        if physical[pid]!=ledger[pid]:issues.append(dict(code='PHYSICAL_MOVEMENT_BALANCE',product_id=pid,physical=_number(physical[pid]),movement_balance=_number(ledger[pid])))
        balances[str(pid)]=dict(physical=_number(physical[pid]),reserved=_number(reserved[pid]),movement_balance=_number(ledger[pid]))
    for mo in d['mrp.production']:
        output=sum((Decimal(str(m['quantity'])) for m in moves if m['id'] in mo['move_finished_ids'] and m['state']=='done' and _id(m['product_id'])==_id(mo['product_id'])),Decimal(0))
        if output!=Decimal(str(mo['qty_produced'])):issues.append(dict(code='MANUFACTURED_OUTPUT_MISMATCH',production_id=mo['id']))
    return dict(status='FAIL' if issues else 'PASS',issues=issues,balances=balances,
        scope='COMPLETE_SCOPED_NATIVE_MOVE_HISTORY_VS_QUANT;NOT_A_QUALITY_CERTIFICATE',
        identity={'company_id':capture['company_id'],'source_sha256':capture['source_sha256']},
        reservation_semantics='TRACKED_SEPARATELY_NEVER_ACTUAL_CONSUMPTION')


def audit_rolling_partial(before,after,first_result,second_result,production_id):
    """Independent bounded backorder/actual conservation; not a plan builder."""
    issues=[];a=before['documents'];b=after['documents']
    def fail(ok,code):
        if not ok:issues.append(code)
    old=next(m for m in a['mrp.production'] if m['id']==production_id)
    completed=next(m for m in b['mrp.production'] if m['id']==production_id)
    new_ids={m['id'] for m in b['mrp.production']}-{m['id'] for m in a['mrp.production']}
    backorders=[m for m in b['mrp.production'] if m['id'] in new_ids and
        m['name'].startswith(old['name']+'-') and m['backorder_sequence']>completed['backorder_sequence'] and
        all(m[k]==old[k] for k in ['product_id','bom_id','product_uom_id','origin','procurement_group_id'])]
    fail(completed['state']=='done' and len(backorders)==1,'NATIVE_BACKORDER_LINEAGE')
    remaining=sum((Decimal(str(m['product_qty'])) for m in backorders),Decimal(0))
    done=Decimal(str(completed['qty_produced']));original=Decimal(str(old['product_qty']))
    fail(done+remaining==original and Decimal(str(completed['product_qty']))==done,'PARTIAL_QUANTITY_CONSERVATION')
    fail(a['sale.order.line']==b['sale.order.line'],'ORIGINAL_DEMAND_CHANGED')
    fail(audit_stock(before)['status']==audit_stock(after)['status']=='PASS','PHYSICAL_STOCK_CONSERVATION')
    raw=[m for m in b['stock.move'] if _id(m['raw_material_production_id'])==production_id and m['state']=='done']
    consumed=defaultdict(Decimal)
    for m in raw:consumed[str(_id(m['product_id']))]+=Decimal(str(m['quantity']))
    bom=next(m for m in b['mrp.bom'] if m['id']==_id(completed['bom_id']))
    expected_consumption=defaultdict(Decimal)
    for line in b['mrp.bom.line']:
        expected=done*Decimal(str(line['product_qty']))/Decimal(str(bom['product_qty']))
        expected_consumption[str(_id(line['product_id']))]+=expected
    fail(dict(consumed)==dict(expected_consumption),'ACTUAL_BOM_CONSUMPTION')
    fail(not second_result['manufacture_suggestions'],'DUPLICATE_PROPOSED_MANUFACTURE')
    fail(first_result['deliveries'][0]['requested_quantity']==second_result['deliveries'][0]['requested_quantity'],'PLAN_DEMAND_INFLATION')
    active={m['id'] for m in b['mrp.production'] if m['state'] not in ['done','cancel']}
    native_roots={o['manufacture_id'].split(':MO:',1)[1] for o in second_result['operations']}
    fail(native_roots=={'ODOO:mrp.production:'+str(i) for i in active},'EXECUTED_OR_MISSING_FUTURE_TASK')
    return dict(status='FAIL' if issues else 'PASS',issues=issues,
        identity=dict(company_id=after['company_id'],before_source_sha256=before['source_sha256'],after_source_sha256=after['source_sha256'],
            first_run=first_result['solve_run_id'],second_run=second_result['solve_run_id']),
        original_quantity=_number(original),completed_quantity=_number(done),backorder_quantity=_number(remaining),
        completed_production_id=production_id,backorder_ids=[m['id'] for m in backorders],remaining_production_ids=sorted(active),
        actual_consumption={p:_number(q) for p,q in consumed.items()},stock=audit_stock(after),
        proposed_manufacture_count=len(second_result['manufacture_suggestions']),future_operation_count=len(second_result['operations']),
        actual_execution_basis='NATIVE_DONE_STOCK_MOVE_AND_BACKORDER;WO_PICKED_IS_NOT_POSTED_CONSUMPTION')


def stored_quantity(capture,production):
    """Three-step native storage only; separate from WO/production completion."""
    d=capture['documents'];warehouse=d['stock.warehouse'][0];finished=set(production['move_finished_ids'])
    if any(m['state']=='done' and finished.intersection(m['move_orig_ids']) and set(m['move_orig_ids'])-finished for m in d['stock.move']):
        raise ValueError('AMBIGUOUS_STORAGE_LINEAGE')
    return sum((Decimal(str(m['quantity'])) for m in d['stock.move'] if m['state']=='done'
        and _id(m['location_dest_id'])==_id(warehouse['lot_stock_id'])
        and _id(m['product_id'])==_id(production['product_id']) and finished.intersection(m['move_orig_ids'])),Decimal(0))


def manufacturing_actuals(capture,production_id):
    """Bounded current native observation, separate from DATA and plan quantities."""
    audit=audit_stock(capture)
    if audit['status']!='PASS':raise ValueError('ODOO_CURRENT_STOCK_AUDIT_FAILED')
    d=capture['documents'];matches=[m for m in d['mrp.production'] if m['id']==production_id]
    if len(matches)!=1:raise ValueError('ODOO_CURRENT_MANUFACTURING_SCOPE_MISMATCH')
    mo=matches[0];pid=_id(mo['product_id']);consumed=defaultdict(Decimal)
    for move in d['stock.move']:
        if move['state']=='done' and _id(move['raw_material_production_id'])==production_id:
            consumed[str(_id(move['product_id']))]+=Decimal(str(move['quantity']))
    customers={r['id'] for r in d['stock.location'] if r['usage']=='customer'}
    shipped=sum((Decimal(str(m['quantity']))*((1 if _id(m['location_dest_id']) in customers else 0)-(1 if _id(m['location_id']) in customers else 0))
        for m in d['stock.move'] if m['state']=='done' and _id(m['product_id'])==pid),Decimal(0))
    units={r['id']:r['name'] for r in d['uom.uom']}
    root_name=re.sub(r'-\d{3}$','',mo['name']) if mo['backorder_sequence'] else mo['name']
    return dict(company_id=capture['company_id'],source_as_of=capture['captured_at'],source_sha256=capture['source_sha256'],
        production_id=production_id,production_revision=capture['revisions']['mrp.production'][str(production_id)],
        formal_quantity=_number(mo['product_qty']),produced_quantity=_number(mo['qty_produced']),
        stored_quantity=_number(stored_quantity(capture,mo)),uom=units[_id(mo['product_uom_id'])],
        backorders=[dict(id=m['id'],name=m['name'],quantity=_number(m['product_qty']),state=m['state']) for m in d['mrp.production']
            if m['id']!=production_id and m['name'].startswith(root_name+'-')
            and m['backorder_sequence']>mo['backorder_sequence'] and all(m[k]==mo[k] for k in ('product_id','bom_id','procurement_group_id','origin'))],
        actual_consumption={p:_number(q) for p,q in consumed.items()},
        consumption_units={str(p['id']):units[_id(p['uom_id'])] for p in d['product.product']},
        physical_finished_stock=audit['balances'][str(pid)]['physical'],scope_customer_shipped_quantity=_number(shipped),
        shipment_scope='CONFIGURED_COMPANY_PRODUCT_NATIVE_DONE_MOVES_NOT_ORDER_ALLOCATION',stock_audit=audit['status'])
