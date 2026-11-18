"""Bounded real Odoo facts -> existing typed Canonical tables.

The ERP owns quantities and execution. A separately approved policy owns only
the missing research personnel/setup rules. Neither is inferred from names.
"""
import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
from p21.canonical import DOMAIN, digest

PROFILE = 'odoo-verified-manufacturing-v1'


def ident(value):
    return value[0] if isinstance(value, (list, tuple)) and value else value


def at(value):
    d = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d


def key(model, identifier):
    return 'ODOO:' + model + ':' + str(identifier)


def readiness(capture, policy):
    errors = []
    def check(ok, code):
        if not ok and code not in errors: errors.append(code)
    if capture.get('schema') != 'odoo-planning-facts/1':
        return dict(ready=False, blocking_issues=['ODOO_SCOPE_CAPTURE_REQUIRED'])
    d = capture.get('documents', {}); company = capture.get('company_id')
    check(capture.get('transaction') == 'SINGLE_ODOO_READ_ONLY_ORM_TRANSACTION', 'ATOMIC_CAPTURE_REQUIRED')
    check(capture.get('source_sha256') == digest(d), 'SOURCE_REVISION_MISMATCH')
    check(all(ident(r.get('company_id')) in (None, False, company) for rows in d.values() for r in rows), 'FOREIGN_COMPANY')
    check(all(set(capture.get('revisions', {}).get(m, {})) == {str(r['id']) for r in rows} for m,rows in d.items()), 'SOURCE_REVISION_INCOMPLETE')
    check(policy.get('status') == 'USER_APPROVED_RESEARCH_ONLY' and policy.get('approval', {}).get('identity') == 'HUMAN_USER', 'PROFESSIONAL_POLICY_APPROVAL_REQUIRED')
    check(policy.get('company_id') == company and policy.get('enterprise_id') == capture.get('enterprise_id'), 'POLICY_SCOPE_MISMATCH')
    boms = d.get('mrp.bom', []); lines = d.get('mrp.bom.line', [])
    check(len(boms) == 1 and len(lines) >= 2 and boms[0].get('active') and boms[0].get('type') == 'normal', 'BOM_COMPONENTS_REQUIRED')
    if boms:
        check(set(boms[0].get('bom_line_ids',[])) == {r['id'] for r in lines}, 'BOM_COMPONENTS_REQUIRED')
        check(boms[0].get('product_qty', 0) > 0, 'BOM_BASIS_REQUIRED')
    products = {r['id']:r for r in d.get('product.product', [])}
    check(bool(products) and all(r.get('is_storable') for r in products.values()), 'STORABLE_PRODUCTS_REQUIRED')
    if boms:
        b=boms[0]
        fg=[r for r in products.values() if (ident(b['product_id'])==r['id'] if b['product_id'] else ident(b['product_tmpl_id'])==ident(r['product_tmpl_id']))]
        check(len(fg)==1 and ident(b['product_uom_id'])==ident(fg[0]['uom_id']), 'BOM_OUTPUT_SCOPE_UNIT_MISMATCH')
        if len(fg)==1:
            check(all(ident(m['product_id'])==fg[0]['id'] and ident(m['bom_id'])==b['id'] and ident(m['product_uom_id'])==ident(fg[0]['uom_id']) for m in d.get('mrp.production',[])), 'MO_PRODUCT_BOM_UNIT_MISMATCH')
            check(all(l.get('display_type') or (ident(l['product_id'])==fg[0]['id'] and ident(l['product_uom'])==ident(fg[0]['uom_id']) and 0<=l['qty_delivered']<=l['product_uom_qty']) for l in d.get('sale.order.line',[])), 'DEMAND_PRODUCT_UNIT_MISMATCH')

    for line in lines:
        p = products.get(ident(line['product_id']), {})
        check(line.get('product_qty',0)>0 and ident(line.get('product_uom_id')) == ident(p.get('uom_id')), 'BOM_UNIT_MISMATCH')
    ops = sorted(d.get('mrp.routing.workcenter', []), key=lambda r:r['sequence'])
    check(len(ops)==4 and all(o['time_mode']=='manual' and o['time_cycle_manual']>0 for o in ops), 'FOUR_EXPLICIT_OPERATIONS_REQUIRED')
    for i,o in enumerate(ops):
        check(set(o['blocked_by_operation_ids']) == ({ops[i-1]['id']} if i else set()), 'ROUTING_DEPENDENCY_UNSUPPORTED')
    centers = {r['id']:r for r in d.get('mrp.workcenter',[])}
    check(bool(centers) and all(ident(o['workcenter_id']) in centers for o in ops), 'RESOURCE_CAPABILITY_MISSING')
    check(all(r['active'] and r['default_capacity']==1 and r['time_efficiency']==100 and r['time_start']==r['time_stop']==0 for r in centers.values()) and not d.get('mrp.workcenter.capacity'), 'RESOURCE_MODEL_UNSUPPORTED')
    check(bool(d.get('resource.calendar')) and all(not r['two_weeks_calendar'] for r in d.get('resource.calendar',[])), 'CALENDAR_REQUIRED')
    check(len(policy.get('personnel', [])) == 4 and all(p.get('capacity')==1 and p.get('skills') for p in policy.get('personnel',[])), 'NAMED_PERSON_POLICY_REQUIRED')
    check(all(sum(o['name'] in p.get('skills',[]) for p in policy.get('personnel',[]))==1 for o in ops), 'PERSONNEL_QUALIFICATION_COVERAGE_REQUIRED')
    calendar_ids={r['id'] for r in d.get('resource.calendar',[])}
    check(all(p.get('calendar_source',{}).get('model')=='resource.calendar' and p['calendar_source'].get('id') in calendar_ids for p in policy.get('personnel',[])), 'PERSONNEL_CALENDAR_SOURCE_REQUIRED')
    check(bool(policy.get('setup',{}).get('transitions')) and {str(x) for x in centers} == set(policy.get('setup',{}).get('initial_state_by_workcenter',{})), 'SETUP_POLICY_REQUIRED')
    check(len(d.get('stock.warehouse',[])) == 1 and d['stock.warehouse'][0]['manufacture_steps']=='pbm_sam', 'WAREHOUSE_FLOW_UNSUPPORTED')
    check(all(not q.get('owner_id') and not q.get('package_id') and q['quantity'] >= 0 and 0 <= q['reserved_quantity'] <= q['quantity'] for q in d.get('stock.quant',[])), 'STOCK_SCOPE_UNSUPPORTED')
    check(bool(d.get('sale.order')) and all(s['state']=='sale' and s['commitment_date'] for s in d.get('sale.order',[])), 'CONFIRMED_SALES_REQUIRED')
    moves = d.get('stock.move',[])
    check(len({m['id'] for m in moves})==len(moves), 'DUPLICATE_PHYSICAL_EVENT')
    cutoff=at(capture['captured_at']) if capture.get('captured_at') else None
    check(cutoff is not None and all(m['state']!='done' or at(m['date']) <= cutoff for m in moves), 'FUTURE_ACTUAL_NOT_KNOWN')
    # This bounded profile plans only reservations belonging to the selected
    # sales/MO graph. Shared physical stock may not silently fund foreign work.
    from collections import defaultdict
    reserved=defaultdict(Decimal);owned=defaultdict(Decimal)
    scope_mos={m['id'] for m in d.get('mrp.production',[])}
    scope_sales={r['name'] for r in d.get('sale.order',[])}
    own_moves={m['id'] for m in moves if ident(m.get('raw_material_production_id')) in scope_mos or ident(m.get('production_id')) in scope_mos}
    previous=None
    while previous!=own_moves:
        previous=set(own_moves)
        own_moves.update(x for m in moves if m['id'] in previous for x in m.get('move_orig_ids',[]))
    own_pickings={p['id'] for p in d.get('stock.picking',[]) if p.get('origin') in scope_sales}
    own_moves.update(m['id'] for m in moves if ident(m.get('picking_id')) in own_pickings)
    for q in d.get('stock.quant',[]):
        reserved[(ident(q['product_id']),ident(q['location_id']))]+=Decimal(str(q['reserved_quantity']))
    internal_ids={l['id'] for l in d.get('stock.location',[]) if l['usage']=='internal'}
    for l in d.get('stock.move.line',[]):
        if ident(l['location_id']) in internal_ids and l['state'] not in ('done','cancel') and ident(l['move_id']) in own_moves:
            owned[(ident(l['product_id']),ident(l['location_id']))]+=Decimal(str(l['quantity']))
    check(all(v==owned[k] for k,v in reserved.items()) and all(v==reserved[k] for k,v in owned.items()),'STOCK_RESERVATION_SCOPE_MISMATCH')
    check(all(not q.get('lot_id') for q in d.get('stock.quant',[])), 'LOT_ALLOCATION_UNSUPPORTED')
    post_ids={ident(w['sam_loc_id']) for w in d.get('stock.warehouse',[])}
    check(not any(ident(q['location_id']) in post_ids and q['quantity']>0 for q in d.get('stock.quant',[])), 'POST_PRODUCTION_TRANSFER_REQUIRED')
    # A verified physical snapshot is used once. Done movements independently
    # reconcile it; they are never appended as another planning receipt.
    if d.get('product.product') and d.get('stock.location'):
        from interop.odoo_business_audit import audit_stock
        check(audit_stock(capture)['status']=='PASS','NATIVE_STOCK_AUDIT_FAILED')
        from interop.odoo_business_audit import stored_quantity
        try:
            for m in d.get('mrp.production',[]):stored_quantity(capture,m)
        except ValueError:check(False,'AMBIGUOUS_STORAGE_LINEAGE')
    works = {w['id']:w for w in d.get('mrp.workorder',[])}
    for m in d.get('mrp.production',[]):
        if m['state'] in ('done','cancel'): continue
        check(m['state']=='confirmed' and m['qty_produced']==0 and all(works.get(i,{}).get('state') in ('ready','pending','waiting') for i in m['workorder_ids']), 'PARTIAL_STAGE_MAPPING_REQUIRED')
        check(all(works.get(i,{}).get('date_start') and works.get(i,{}).get('date_finished') for i in m['workorder_ids']), 'EXISTING_MO_PLAN_REQUIRED')
    return dict(ready=not errors, status='PLANNING_READY' if not errors else 'BLOCKED', blocking_issues=errors,
                company_id=company, source_sha256=capture.get('source_sha256'), policy_id=policy.get('policy_id'),
                native_validated=False)


def _calendar(capture, calendar_id, resource_id=None):
    d=capture['documents'];cal=next(r for r in d['resource.calendar'] if r['id']==calendar_id)
    start=at(capture['captured_at']);end=start+timedelta(days=30);zone=ZoneInfo(cal['tz'])
    attendance=[a for a in d['resource.calendar.attendance'] if ident(a['calendar_id'])==calendar_id and a['day_period']!='lunch']
    own={ident(w['leave_id']) for w in d['mrp.workorder']}
    leaves=[l for l in d['resource.calendar.leaves'] if l['id'] not in own and ident(l['calendar_id'])==calendar_id and (not l['resource_id'] or ident(l['resource_id'])==resource_id)]
    result=[];day=start.astimezone(zone).replace(hour=0,minute=0,second=0,microsecond=0)
    while day<end:
        for a in attendance:
            if int(a['dayofweek'])!=day.weekday() or (a['date_from'] and day.date()<at(a['date_from']).date()) or (a['date_to'] and day.date()>at(a['date_to']).date()): continue
            lo=max(start,day+timedelta(hours=a['hour_from']));hi=min(end,day+timedelta(hours=a['hour_to']))
            parts=[(lo,hi)] if lo<hi else []
            for l in leaves:
                x,y=at(l['date_from']),at(l['date_to']);parts=[p for left,right in parts for p in ([(left,right)] if y<=left or right<=x else [(left,min(right,x)),(max(left,y),right)]) if p[0]<p[1]]
            result.extend(dict(start_at=l.isoformat(),end_at=h.isoformat()) for l,h in parts)
        day+=timedelta(days=1)
    return sorted(result,key=lambda r:at(r['start_at']))


def build_snapshot(capture, policy):
    ready=readiness(capture,policy)
    if not ready['ready']: raise ValueError('ODOO_READINESS_BLOCKED:'+','.join(ready['blocking_issues']))
    d=capture['documents'];start=at(capture['captured_at']);end=start+timedelta(days=30)
    if not at(policy['effective_from'])<=start<end<=at(policy['effective_to']):raise ValueError('POLICY_VALIDITY_MISMATCH')
    t={k:[] for k in DOMAIN};site=key('stock.warehouse',d['stock.warehouse'][0]['id']);bom=d['mrp.bom'][0]
    fg=next(r for r in d['product.product'] if (r['id']==ident(bom['product_id']) if bom['product_id'] else ident(r['product_tmpl_id'])==ident(bom['product_tmpl_id'])))['id']
    rid=key('mrp.bom',bom['id']);routing=rid+':ROUTE';products={p['id']:p for p in d['product.product']}
    units={u['id']:u['name'] for u in d['uom.uom']};uom=lambda p:units[ident(products[p]['uom_id'])]
    def row(table,value,source='ODOO'):
        value.update(dataset_id='ODOO03',source_id=source,source_sha256=capture['source_sha256'] if source=='ODOO' else digest(policy),source_section=table,entity_id=value['id'])
        t[table].append(value);return value
    row('locations',dict(id=site,name=d['stock.warehouse'][0]['name'],timezone='Asia/Shanghai',odoo_bundle={'capture':copy.deepcopy(capture),'professional_policy':copy.deepcopy(policy)},source_origin='REAL_ODOO_NOT_SIMULATOR'))
    for p in products.values():row('items',dict(id=key('product.product',p['id']),kind='FINISHED' if p['id']==fg else 'RAW',base_uom=uom(p['id'])))
    for s in d['sale.order']:
        customer=key('res.partner',ident(s['partner_id']))
        if not any(r['id']==customer for r in t['customers']):row('customers',dict(id=customer,name=str(s['partner_id'][1])))
        row('sales_orders',dict(id=key('sale.order',s['id']),customer_id=customer,reference=s['name']))
        for line in d['sale.order.line']:
            if ident(line['order_id'])!=s['id'] or line['display_type']:continue
            if ident(line['product_uom'])!=ident(products[ident(line['product_id'])]['uom_id']):raise ValueError('DEMAND_UNIT_MISMATCH')
            q=Decimal(str(line['product_uom_qty']))-Decimal(str(line['qty_delivered']))
            if q<0:raise ValueError('DEMAND_EXECUTION_BALANCE')
            if q:row('demand_lines',dict(id=key('sale.order.line',line['id']),customer_id=customer,item_id=key('product.product',ident(line['product_id'])),location_id=site,quantity=float(q),uom=uom(ident(line['product_id'])),due_at=at(s['commitment_date']).isoformat(),order_ref=key('sale.order',s['id']),priority=policy['planning_rules']['demand_priority'],minimum_shipment=policy['planning_rules']['minimum_shipment'],maximum_lateness_seconds=policy['planning_rules']['maximum_lateness_seconds'],status='CONFIRMED',gross_quantity=line['product_uom_qty'],actual_delivered=line['qty_delivered']))
    default_cal=d['resource.calendar'][0]
    row('calendars',dict(id='CAL-FACTORY',timezone=default_cal['tz'],available_intervals=_calendar(capture,default_cal['id'])))
    for wc in d['mrp.workcenter']:
        calid=key('mrp.workcenter',wc['id'])+':CAL'
        row('calendars',dict(id=calid,timezone=default_cal['tz'],available_intervals=_calendar(capture,ident(wc['resource_calendar_id']),ident(wc['resource_id']))))
        row('resources',dict(id=key('mrp.workcenter',wc['id']),location_id=site,calendar_id=calid,role='MACHINE',capacity_model='CONTINUOUS',parallel_units=wc['default_capacity'],efficiency_percent=wc['time_efficiency'],initial_setup=policy['setup']['initial_state_by_workcenter'][str(wc['id'])],setup_source='INDEPENDENT_APPROVED_POLICY'))
    for p in policy['personnel']:
        calid=key('person',p['id'])+':CAL'
        row('calendars',dict(id=calid,timezone=default_cal['tz'],available_intervals=_calendar(capture,p['calendar_source']['id'])),'PROFESSIONAL_POLICY')
        row('operators',dict(id=p['id'],name=p['name'],location_id=site,calendar_id=calid,capacity=p['capacity'],efficiency_percent=100),'PROFESSIONAL_POLICY')
        for sk in p['skills']:
            skill='ODOO03:SKILL:'+sk
            if not any(r['id']==skill for r in t['skills']):row('skills',dict(id=skill),'PROFESSIONAL_POLICY')
            row('resource_skills',dict(id=p['id']+':'+sk,resource_id=p['id'],skill_id=skill,effective_from=policy['effective_from'],effective_to=policy['effective_to']),'PROFESSIONAL_POLICY')
    ops=sorted(d['mrp.routing.workcenter'],key=lambda r:r['sequence']);prior=None
    for o in ops:
        wc=next(w for w in d['mrp.workcenter'] if w['id']==ident(o['workcenter_id']));skill='ODOO03:SKILL:'+o['name']
        candidates=[wc['id']]+wc['alternative_workcenter_ids']
        for wid in candidates:row('machine_qualifications',dict(id=f'Q:{wid}:{o["id"]}',resource_id=key('mrp.workcenter',wid),skill_id=skill,effective_from=start.isoformat(),effective_to=end.isoformat()))
        opid=key('mrp.routing.workcenter',o['id'])
        row('operations',dict(id=opid,recipe_id=rid,routing_id=routing,sequence=o['sequence'],predecessor_id=prior,duration_model='PER_UNIT',fixed_seconds=0,seconds_per_unit=o['time_cycle_manual']*60,resource_ids=[key('mrp.workcenter',w) for w in candidates],resources_are_alternatives=len(candidates)>1,machine_and_person_simultaneously_required=True,required_operator_skill=skill,produces_output=o==ops[-1]))
        prior=opid
    row('recipes',dict(id=rid,output_item_id=key('product.product',fg),bom_id=rid+':BOM',routing_id=routing,location_id=site,effective_from=start.isoformat(),effective_to=end.isoformat(),minimum_quantity=policy['planning_rules']['minimum_manufacturing_quantity'],multiple=policy['planning_rules']['manufacturing_multiple'],maximum_quantity=None))
    row('routings',dict(id=routing,recipe_id=rid,output_item_id=key('product.product',fg),kind='SEQUENTIAL'))
    for l in d['mrp.bom.line']:
        operation=ident(l['operation_id'])
        if operation not in {o['id'] for o in ops}:raise ValueError('BOM_CONSUMPTION_OPERATION_REQUIRED')
        row('bom_lines',dict(id=key('mrp.bom.line',l['id']),recipe_id=rid,input_item_id=key('product.product',ident(l['product_id'])),output_item_id=key('product.product',fg),input_uom=uom(ident(l['product_id'])),output_uom=uom(fg),quantity_per_output=float(Decimal(str(l['product_qty']))/Decimal(str(bom['product_qty']))),scrap_fraction=0,consume_operation_id=key('mrp.routing.workcenter',operation),consume_at='START'))
    active_states=set(policy['setup']['initial_state_by_workcenter'].values()) | {policy['setup']['operation_state']}
    for i,tr in enumerate(policy['setup']['transitions']):
        if tr['from_state'] in active_states and tr['to_state'] in active_states:
            row('setup_transitions',dict(tr,id='ODOO03:SETUP:'+str(i)),'PROFESSIONAL_POLICY')
    locations={r['id']:r for r in d['stock.location']};warehouse=d['stock.warehouse'][0]
    for pid in products:
        quants=[q for q in d['stock.quant'] if ident(q['product_id'])==pid and locations[ident(q['location_id'])]['usage']=='internal']
        physical=sum(Decimal(str(q['quantity'])) for q in quants)
        post=sum(Decimal(str(q['quantity'])) for q in quants if ident(q['location_id'])==ident(warehouse['sam_loc_id']))
        row('inventory_positions',dict(id=key('product.product',pid)+':STOCK',item_id=key('product.product',pid),location_id=site,physical=float(physical),quality_blocked=0,other_unavailable=float(post),net_available=float(physical-post),uom=uom(pid),as_of=start.isoformat(),knowledge='COMPLETE_SCOPED_ODOO_QUANT_QUERY',reserved_not_consumed=sum(q['reserved_quantity'] for q in quants),availability_basis='NATIVE_MRP_INTERNAL_STOCK; NO_QUALITY_CERTIFICATE_INFERRED',quant_ids=[q['id'] for q in quants]))
    works={w['id']:w for w in d['mrp.workorder']}
    for mo in d['mrp.production']:
        if mo['state'] in ('done','cancel'):continue
        children=sorted([works[x] for x in mo['workorder_ids']],key=lambda w:next(o['sequence'] for o in ops if o['id']==ident(w['operation_id'])))
        commitments=[]
        for w in children:
            o=next(o for o in ops if o['id']==ident(w['operation_id']));person=next(p for p in policy['personnel'] if o['name'] in p['skills'])
            commitments.append(dict(operation_id=key('mrp.routing.workcenter',o['id']),resource_id=key('mrp.workcenter',ident(w['workcenter_id'])),operator_id=person['id'],start_at=at(w['date_start']).isoformat(),end_at=at(w['date_finished']).isoformat(),quantity=str(mo['product_qty']),external_workorder_id=w['id']))
        row('existing_manufacturing_orders',dict(id=key('mrp.production',mo['id']),reference=mo['name'],recipe_id=rid,operation_id=commitments[0]['operation_id'],resource_id=commitments[0]['resource_id'],quantity=mo['product_qty'],completed_quantity=0,start_at=commitments[0]['start_at'],end_at=commitments[-1]['end_at'],status='confirmed',protected=True,plan_operations=commitments))
    return dict(enterprise_id=capture['enterprise_id'],planning_at=start.isoformat(),horizon=dict(start_at=start.isoformat(),end_at=end.isoformat()),timezone='Asia/Shanghai',tables=t)
