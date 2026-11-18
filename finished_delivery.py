"""Explicit single-product CUT -> SEW -> inspection/PACK -> customer FG slice.

Inspection is a mandatory planned operation, never proof of actual quality release.
This profile adds no execution, purchase approval, rework, alternate or lot semantics.
"""
from collections import Counter, defaultdict
from datetime import datetime
from inventory_availability import net_available
from decimal import Decimal
import hashlib
import json
from zoneinfo import ZoneInfo

PROFILE = 'finished-garment-confirmed-v1'
TOLERANCE = Decimal('0.000001')


def enabled(data):
    from enterprise_forecast import enabled as enterprise_enabled
    return data.get('rules', {}).get('manufacturing_profile') == PROFILE or enterprise_enabled(data)


def stages(data):
    routes = {r['id']: r for r in data['routing_revisions']}
    fixed = [p for p in data['production_recipes'] if any(o['duration_model'] == 'FIXED_BATCH' for o in routes[p['routing_revision_id']]['operations'])]
    cut = fixed[0]
    fg = next(i for i in data['items'] if i['kind'] == 'FINISHED')
    finish = next(p for p in data['production_recipes'] if p['output_item_id'] == fg['id'])
    sew = next(p for p in data['production_recipes'] if p['id'] not in {cut['id'], finish['id']})
    return cut, sew, finish


def upstream(data):
    cut, _, _ = stages(data)
    op = next(r for r in data['routing_revisions'] if r['id'] == cut['routing_revision_id'])['operations'][0]
    return cut, op, next(r for r in data['resources'] if r['id'] == op['resource_id'])


def validate(data):
    """Finite shape, units, quality gates and qualifications; old profiles unchanged."""
    from planning_adapter import InputError
    from confirmed_supply import validate_receipts
    from accessory_supply import enabled as accessory_enabled, validate as validate_accessory
    if accessory_enabled(data):
        import copy
        validate_accessory(data)
        core=copy.deepcopy(data)
        original=core['existing_orders'][0]
        core['supply_sources']=[s for s in core['supply_sources'] if s['id']==original['supply_source_id']]
        core['suppliers']=[s for s in core['suppliers'] if s['id']==original['supplier_id']]
        core.pop('accessory_supply_option')
        core['rules']['manufacturing_profile']='enterprise-forecast-fg-v1'
        validate(core)
        return
    def require(ok, why):
        if not ok:
            raise InputError('UNSUPPORTED_FINISHED_DELIVERY: ' + why)
    require(not data['reservations'] and not data.get('previous_manufacturing_suggestions'), 'no reservations, previous proposals or execution replay')
    require(len(data['suppliers']) == len(data['supply_sources']) == len(data['existing_orders']) == 1, 'one full confirmed fabric supply only; accessories use explicit opening stock')
    validate_receipts(data)
    require('procurement_strategy' not in data['rules'], 'no new-purchase strategy composition')
    kinds = Counter(i['kind'] for i in data['items'])
    require(kinds == Counter(RAW=4, SEMI=2, FINISHED=1), 'four RAW inputs, independent CUT and SEWN WIP, one independent FG')
    require(len(data['production_recipes']) == len(data['bom_revisions']) == len(data['routing_revisions']) == 3, 'exactly three independent recipes/BOMs/routes')
    boms = {b['id']: b for b in data['bom_revisions']}
    routes = {r['id']: r for r in data['routing_revisions']}
    require(len(boms) == len(routes) == 3 and all(p['bom_revision_id'] in boms and p['routing_revision_id'] in routes for p in data['production_recipes']), 'unique known recipe revisions')
    require(len({p['bom_revision_id'] for p in data['production_recipes']}) == len({p['routing_revision_id'] for p in data['production_recipes']}) == 3, 'no shared or alternate recipes')
    fixed = [p for p in data['production_recipes'] if any(o['duration_model'] == 'FIXED_BATCH' for o in routes[p['routing_revision_id']]['operations'])]
    require(len(fixed) == 1 and len(routes[fixed[0]['routing_revision_id']]['operations']) == 1, 'one complete fixed CUT operation')
    try:
        cut, sew, finish = stages(data)
    except (StopIteration, IndexError):
        raise InputError('UNSUPPORTED_FINISHED_DELIVERY: independent CUT, SEWN and FG outputs required')
    items = {i['id']: i for i in data['items']}
    require(items[cut['output_item_id']]['kind'] == items[sew['output_item_id']]['kind'] == 'SEMI' and cut['output_item_id'] != sew['output_item_id'], 'CUT and SEWN are separate SEMI buffers')
    require({p['output_item_id'] for p in [cut, sew, finish]} == {i['id'] for i in data['items'] if i['kind'] != 'RAW'}, 'only one manufacturing recipe per independent produced item')
    cut_ops, sew_ops, finish_ops = [sorted(routes[p['routing_revision_id']]['operations'], key=lambda o: o['sequence']) for p in [cut, sew, finish]]
    require(len(cut_ops) == len(sew_ops) == 1 and len(finish_ops) == 2, 'CUT, SEW, then two mandatory QC/PACK steps')
    co, so = cut_ops[0], sew_ops[0]
    qc, pack = finish_ops
    require(qc['predecessors'] == [] and pack['predecessors'] == [qc['id']] and qc['sequence'] < pack['sequence'], 'QC must complete before PACK; no bypass')
    require(Decimal(co['fixed_seconds']) == 3600 and Decimal(co['seconds_per_unit']) == 0, 'CUT20 requires a full 3600-second window')
    require(all(o['duration_model'] == 'FIXED_PLUS_PER_UNIT' and Decimal(o['fixed_seconds']) == 0 and Decimal(o['seconds_per_unit']) == seconds for o, seconds in [(so, 480), (qc, 180), (pack, 120)]), 'retained TB-M SEW480/QC180/PACK120 seconds per unit')
    cb, sb, fb = [boms[p['bom_revision_id']] for p in [cut, sew, finish]]
    require(len(cb['lines']) == 1 and len(sb['lines']) == 2 and len(fb['lines']) == 3, 'fabric, sewing thread, LABEL and BAG must remain explicit')
    fabric = cb['lines'][0]['item_id']
    require(items[fabric]['kind'] == 'RAW' and data['supply_sources'][0]['item_id'] == fabric, 'only fabric has the confirmed purchase path')
    thread = [l for l in sb['lines'] if l['item_id'] != cut['output_item_id']]
    accessories = [l for l in fb['lines'] if l['item_id'] != sew['output_item_id']]
    require(len(thread) == 1 and len(accessories) == 2 and len({fabric, thread[0]['item_id'], *(l['item_id'] for l in accessories)}) == 4, 'four distinct RAW inputs required')
    require(all(items[l['item_id']]['kind'] == 'RAW' for l in thread + accessories), 'accessories cannot be replaced by WIP')
    expected = [
        Counter([(fabric, co['id'], Decimal(1))]),
        Counter([(cut['output_item_id'], so['id'], Decimal(1)), (thread[0]['item_id'], so['id'], Decimal(2))]),
        Counter([(sew['output_item_id'], qc['id'], Decimal(1)), *((l['item_id'], pack['id'], Decimal(1)) for l in accessories)]),
    ]
    end = datetime.fromisoformat(data['horizon']['end_at'])
    for index, p in enumerate([cut, sew, finish]):
        bom = boms[p['bom_revision_id']]
        require(bom['output_uom'] == items[p['output_item_id']]['base_uom'] and Decimal(bom['output_quantity']) == 1, 'explicit one-unit BOM output')
        require(Counter((l['item_id'], l['consume_operation_id'], Decimal(l['quantity_per_output'])) for l in bom['lines']) == expected[index], 'preserve all stage inputs, quantities and consumption steps')
        require(all(l['consume_at'] == 'START' and Decimal(l['scrap_fraction']) == 0 for l in bom['lines']), 'declared start consumption and zero modeled loss only')
        require(p['transfer_policy'] == 'WHOLE_MANUFACTURING_BATCH', 'whole manufacturing batches only')
        if index == 0:
            require(all(Decimal(p[k]) == 20 for k in ['manufacturing_minimum_quantity', 'manufacturing_maximum_quantity', 'manufacturing_multiple']), 'CUT must remain exactly20 sets')
        else:
            require('manufacturing_maximum_quantity' not in p and Decimal(p['manufacturing_minimum_quantity']) == Decimal(p['manufacturing_multiple']) == 1, 'whole continuous units only after CUT')
        require(all(datetime.fromisoformat(x['effective_to']) >= end for x in [p, bom, routes[p['routing_revision_id']]]), 'revision validity covers the whole horizon')
    require(len(data['inventory_positions']) == len(items) and Counter(i['item_id'] for i in data['inventory_positions']) == Counter(items.keys()), 'one explicit opening balance per item, no duplicate receipt')
    require(all(d['item_id'] == finish['output_item_id'] and d.get('customer_id') and d['customer_id'] != 'CUSTOMER-DEMO' for d in data['demand_lines']), 'only explicit customer demand for final FG')
    require(all(Decimal(d['quantity']) == Decimal(d['quantity']).to_integral_value() for d in data['demand_lines']), 'whole garment customer quantities only')
    require(data['rules'].get('move_approved_early') == 0 and data['rules'].get('erase_previous_proposed') is True and data['rules'].get('fix_broken_supply_path') is False, 'all existing native protection parameters explicit')
    source = data['supply_sources'][0]
    require(datetime.fromisoformat(source['effective_from']) <= datetime.fromisoformat(data['planning_at']) and datetime.fromisoformat(source['effective_to']) >= end, 'confirmed fabric path validity covers horizon')
    resources = {r['id']: r for r in data['resources']}
    cals = {c['id']: c for c in data['calendars']}
    pools = [r for r in resources.values() if r.get('role') == 'OPERATOR_POOL']
    people = [r for r in resources.values() if r.get('role') == 'OPERATOR']
    machines = [r for r in resources.values() if r.get('role') == 'MACHINE']
    require(len(pools) == 1 and 4 <= len(people) <= 8 and len(machines) == 4 and len(resources) == 5 + len(people), 'four distinct machines/workcenters, one pool and four/eight named people')
    pool = pools[0]
    require(pool['availability_calendar_id'] in cals, 'known pool calendar')
    work = cals[pool['availability_calendar_id']]['available_intervals']
    require(bool(work), 'explicit nonempty pool work envelope')
    ops = [co, so, qc, pack]
    require(len({o['resource_id'] for o in ops}) == 4 and {o['resource_id'] for o in ops} == {r['id'] for r in machines}, 'four distinct declared processing resources')
    cutter = resources[co['resource_id']]
    require(cutter.get('capacity_model') == 'WINDOW_QUANTITY' and 'parallel_units' not in cutter and cutter['capacity_uom'] == items[cut['output_item_id']]['base_uom'] and Decimal(cutter['window_capacity_quantity']) == 20, 'CUT quantity-window capacity20 independent of one-person occupancy')
    windows = cals[cutter['availability_calendar_id']]['available_intervals']
    require(bool(windows) and all((datetime.fromisoformat(w['end_at']) - datetime.fromisoformat(w['start_at'])).total_seconds() == 3600 for w in windows), 'legal complete one-hour CUT windows')
    for r in resources.values():
        require(r['location_id'] == data['site']['id'] and r['availability_calendar_id'] in cals, 'resource location/calendar identity')
        if r['id'] != cutter['id']:
            require(r.get('capacity_model', 'CONTINUOUS') == 'CONTINUOUS' and r.get('parallel_units') == '1' and not {'window_capacity_quantity', 'capacity_uom', 'maxearly_seconds'} & r.keys(), 'unit continuous downstream and personnel resources')
        require((r.get('pool_id') == pool['id']) if r['role'] == 'OPERATOR' else 'pool_id' not in r, 'only named people belong to pool')
        require(all(any(datetime.fromisoformat(a['start_at']) <= datetime.fromisoformat(w['start_at']) < datetime.fromisoformat(w['end_at']) <= datetime.fromisoformat(a['end_at']) for a in work) for w in cals[r['availability_calendar_id']]['available_intervals']), 'all processing windows within pool working envelope')
    skills = {s['id'] for s in data.get('skills', [])}
    require(len(skills) == len(data.get('skills', [])) == 4, 'four explicit unique operation skills')
    seen = set()
    for s in data.get('resource_skills', []):
        key = s['resource_id'], s['skill_id']
        require(key not in seen and s['resource_id'] in {r['id'] for r in people} and s['skill_id'] in skills, 'unique known named-person qualifications')
        seen.add(key)
        require(datetime.fromisoformat(s['effective_from']) <= datetime.fromisoformat(data['planning_at']) and datetime.fromisoformat(s['effective_to']) >= end, 'qualification valid throughout horizon')
    for o in ops:
        rq = o.get('operator_requirement', {})
        require(o['resource_units'] == '1' and rq.get('pool_id') == pool['id'] and rq.get('quantity') == '1' and rq.get('skill_id') in skills and any(s['skill_id'] == rq['skill_id'] for s in data.get('resource_skills', [])), 'every CUT/SEW/QC/PACK step requires one qualified person simultaneously')
    require(len({o['operator_requirement']['skill_id'] for o in ops}) == 4, 'QC and PACK qualifications remain distinct')


def schedule_issues(data, operations, raw):
    from mixed_batch_wip import schedule_issues as cut_issues
    issues = cut_issues(data, operations, raw)
    _, _, finish = stages(data)
    route = next(r for r in data['routing_revisions'] if r['id'] == finish['routing_revision_id'])
    qc, pack = sorted(route['operations'], key=lambda o: o['sequence'])
    groups = defaultdict(list)
    for row in operations:
        if row['operation_id'] in {qc['id'], pack['id']}:
            groups[row['manufacture_id']].append(row)
    for ref, rows in groups.items():
        q = [r for r in rows if r['operation_id'] == qc['id']]
        p = [r for r in rows if r['operation_id'] == pack['id']]
        ok = len(q) == len(p) == 1 and Decimal(q[0]['quantity']) == Decimal(p[0]['quantity']) and datetime.fromisoformat(q[0]['end_at']) <= datetime.fromisoformat(p[0]['start_at'])
        if not ok:
            issues.append({'code': 'FINISHED_QC_BEFORE_PACK', 'severity': 'ERROR', 'entity_id': ref, 'field_ref': None, 'message': 'QC must finish for the same quantity before PACK starts', 'evidence_ids': []})
    return issues


def audit(data, raw, *, run_id=None):
    """Reconcile actual native model/events; missing evidence can never be PASS."""
    from calculation_validation import routing_material_validation, evidence_rows
    from confirmed_supply import validate_native_receipts
    from two_level_confirmed_supply import supply_scope, audit_supply_model
    from qualified_operator import audit_resources
    from mixed_batch_wip import audit_native
    from planning_adapter import engine_time
    base = routing_material_validation(data, raw)
    receipt = validate_native_receipts(data, raw)
    from accessory_supply import enabled as accessory_enabled, audit as accessory_audit
    if accessory_enabled(data):
        from accessory_supply import audit_supply_model
    supply = accessory_audit(data, raw, run_id=run_id) if accessory_enabled(data) else supply_scope(data, raw)
    staff = audit_resources(data, raw, run_id=run_id)
    reports = [base, receipt, supply, staff]
    issues = [x for r in reports for x in r.get('issues', [])]
    missing = [x for r in reports for x in r.get('missing_evidence', [])]
    zone = ZoneInfo(data['site']['timezone'])
    def at(value):
        t = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return t.replace(tzinfo=zone) if t.tzinfo is None else t.astimezone(zone)
    workorders = evidence_rows(raw.get('workorder'))
    if workorders is not None:
        specs = {p['id'] + ':' + o['id']: o for p in data['production_recipes'] for r in data['routing_revisions'] if r['id'] == p['routing_revision_id'] for o in r['operations']}
        try:
            observed = [dict(id=str(w['reference']), manufacture_id=str(w['owner']), operation_id=specs[w['operation']]['id'],
                             quantity=w['quantity'], resource_units=str(Decimal(w['quantity']) * Decimal(specs[w['operation']]['resource_units'])) if specs[w['operation']]['duration_model'] == 'FIXED_BATCH' else specs[w['operation']]['resource_units'],
                             start_at=at(w['startdate']).isoformat(), end_at=at(w['enddate']).isoformat()) for w in workorders if w.get('operation') in specs]
            issues.extend({'code': i['code'], 'reference': i['entity_id']} for i in schedule_issues(data, observed, raw))
        except (KeyError, TypeError, ValueError, ArithmeticError):
            missing.append('workorder:finished_quality_chain')
    def check(ok, code, ref=None):
        if not ok:
            issues.append({'code': code, 'reference': ref})
    expected_hash = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    def q(value):
        x = Decimal(str(value))
        if not x.is_finite():
            raise ValueError('nonfinite quantity')
        return x
    boms = {b['id']: b for b in data['bom_revisions']}
    routes = {r['id']: r for r in data['routing_revisions']}
    opening = {i['item_id']: net_available(i) for i in data['inventory_positions']}
    summaries = []
    ledger = []
    try:
        for phase in ['pre', 'post']:
            snap = raw.get('fixed_batch_' + phase)
            if not isinstance(snap, dict):
                missing.append('native_' + phase)
                continue
            check(snap.get('input_hash') == expected_hash and snap.get('phase') == phase and run_id is not None and snap.get('run_id') == run_id, 'FINISHED_NATIVE_INPUT_RUN_IDENTITY', phase)
            check(snap.get('move_approved_early') == 0, 'FINISHED_GLOBAL_PROTECTION', phase)
            model = snap.get('wip_model')
            if not isinstance(model, dict):
                missing.append('native_' + phase + ':wip_model')
                continue
            check(model.get('fix_broken_supply_path') == 'false', 'FINISHED_UNKNOWN_SUPPLY_DISABLED', phase)
            audit_supply_model(data, snap, phase, check, missing)
            ops = {o['name']: o for o in model['operations']}
            for p in data['production_recipes']:
                route = sorted(routes[p['routing_revision_id']]['operations'], key=lambda o: o['sequence'])
                parent = ops.get(p['id'])
                check(parent is not None and parent['type'] == 'operation_routing' and not parent['flows'], 'FINISHED_PARENT_NO_DUPLICATE_FG', p['id'])
                for o in route:
                    name = p['id'] + ':' + o['id']
                    child = ops.get(name)
                    if child is None:
                        missing.append('native_' + phase + ':operation:' + name)
                        continue
                    expected = Counter((l['item_id'], -q(l['quantity_per_output'])) for l in boms[p['bom_revision_id']]['lines'] if l['consume_operation_id'] == o['id'])
                    if o['id'] == route[-1]['id']:
                        expected[(p['output_item_id'], Decimal(1))] += 1
                    check(Counter((f['item'], q(f['quantity'])) for f in child['flows']) == expected, 'FINISHED_NATIVE_STAGE_FLOWS', name)
                    check(child['type'] == ('operation_fixed_time' if o['duration_model'] == 'FIXED_BATCH' else 'operation_time_per') and q(child['fixed_seconds']) == q(o['fixed_seconds']) and (o['duration_model'] == 'FIXED_BATCH' or q(child['seconds_per_unit']) == q(o['seconds_per_unit'])), 'FINISHED_NATIVE_STAGE_DURATION', name)
            audit_native(data, snap, phase, check, missing)
            for item, net in opening.items():
                buffers = [b for b in snap['buffers'] if b['item'] == item and b['location'] == data['site']['id']]
                if len(buffers) != 1 or not isinstance(buffers[0].get('events'), list):
                    missing.append('native_' + phase + ':buffer:' + item)
                    continue
                b = buffers[0]
                check(abs(q(b['onhand']) - net) <= TOLERANCE, 'FINISHED_BLOCKED_OPENING_EXCLUDED', item)
                if phase != 'post':
                    continue
                balance = Decimal(0)
                events = b['events']
                produced = consumed = purchased = Decimal(0)
                po_ids = {p['id'] for p in data['existing_orders']}
                if accessory_enabled(data):
                    po_ids.update(str(p['reference']) for p in (evidence_rows(raw.get('purchaseorder')) or []))
                for event in events:
                    balance += q(event['quantity'])
                    check(abs(balance - q(event['onhand'])) <= TOLERANCE and balance >= -TOLERANCE, 'FINISHED_NATIVE_BUFFER_BALANCE', item)
                    if at(event['at']) >= at(data['planning_at']) and q(event['quantity']):
                        amount = q(event['quantity'])
                        purchase = amount > 0 and str(event['reference']) in po_ids
                        if purchase:
                            purchased += amount
                        elif amount > 0:
                            produced += amount
                        else:
                            consumed -= amount
                        ledger.append({'item_id': item, 'location_id': data['site']['id'], 'reference': str(event['reference']),
                                       'at': at(event['at']).isoformat(), 'quantity': str(amount), 'net_available_balance': str(balance),
                                       'source': 'raw/fixed_batch_post.json:/buffers',
                                       'event_kind': 'PURCHASE_RECEIPT' if purchase else 'MANUFACTURING_OUTPUT' if amount > 0 else 'CONSUMPTION_OR_DELIVERY'})
                opening_events = sum((q(e['quantity']) for e in events if at(e['at']) < at(data['planning_at'])), Decimal(0))
                check(abs(opening_events - net) <= TOLERANCE, 'FINISHED_NO_OPENING_DOUBLE_COUNT', item)
                inv = next(i for i in data['inventory_positions'] if i['item_id'] == item)
                summaries.append({'item_id': item, 'kind': next(i['kind'] for i in data['items'] if i['id'] == item), 'location_id': data['site']['id'], 'uom': inv['uom'],
                                  'physical_opening': inv['on_hand'], 'quality_blocked': inv['quality_blocked'], 'other_nonnettable': inv['other_nonnettable'],
                                  'net_available_opening': str(net), 'future_production': str(produced), 'future_consumption': str(consumed),
                                  'future_purchase_receipts': str(purchased), 'ending_net_available': str(balance), 'source': inv['provenance'],
                                  'opening_as_of': inv['as_of'], 'historical_receipt_date': 'NOT_PROVIDED', 'native_initialization_is_receipt_date': False,
                                  'opening_nettable': str(net), 'closing_nettable': str(balance), 'event_count': len(events)})
        # Reconcile the independent native post-buffer events against the exported table.
        from calculation_validation import evidence_rows, same_events
        exported = evidence_rows(raw.get('operationplanmaterial'))
        post = raw.get('fixed_batch_post', {})
        if exported is not None and isinstance(post.get('buffers'), list):
            def key(item, ref, qty, when):
                return ((item, data['site']['id']), str(ref), str(q(qty)), at(when).isoformat())
            memory = Counter(key(b['item'], e['reference'], e['quantity'], e['at']) for b in post['buffers'] for e in b['events'] if at(e['at']) >= at(data['planning_at']) and q(e['quantity']))
            rest = Counter(key(e['item'], e['operationplan'], e['quantity'], e['flowdate']) for e in exported if at(e['flowdate']) >= at(data['planning_at']) and q(e['quantity']))
            check(same_events(memory, rest), 'FINISHED_NATIVE_REST_EVENT_RECONCILIATION')
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        missing.append('unreadable_finished_native_evidence:' + type(exc).__name__)
    return {'status': 'FAIL' if issues else 'UNSUPPORTED' if supply['status'] == 'UNSUPPORTED' else 'NOT_CHECKED' if missing or any(r['status'] != 'PASS' for r in reports) else 'PASS',
            'scope': 'bounded_finished_garment_material_quality_time_ledger', 'issues': issues, 'missing_evidence': sorted(set(missing)),
            'confirmed_supply_validation': receipt, 'qualified_resource_validation': staff, 'unvalidated_purchases': supply['unvalidated_purchases'],
            **({'accessory_supply_validation':supply} if accessory_enabled(data) else {}),
            'buffers': summaries, 'endpoint': endpoint(data),
            'summary': {'profile': data['rules'].get('manufacturing_profile',PROFILE), 'inventory': summaries, 'material_events': ledger, 'stage_endpoint': endpoint(data),
                        'confirmed_purchases': [{**p, 'certification': 'EXISTING_CONFIRMED_ONLY'} for p in data['existing_orders']],
                        'unvalidated_purchases': supply['unvalidated_purchases'],
                        **({'accessory_supply': supply['summary']} if accessory_enabled(data) else {}),
                        'limits': ['independent synthetic slice; no enterprise execution evidence', 'inspection is planned work, not actual quality release', 'all quality holds excluded throughout planning', 'no order-level allocation inferred']},
            'unsupported': ['existing_MO', 'multiple_or_partial_receipts', *(['new_proposed_purchase_certification'] if not accessory_enabled(data) else []), 'physical_lots', 'alternate_recipes', 'conditional_inspection', 'rework', 'multi_site'],
            'not_checked': ['actual_execution', 'actual_finished_quality_release', 'conditional_inspection_outcome', 'physical_lots', 'rework', 'enterprise_fact_authentication']}


def endpoint(data):
    _, _, finish = stages(data)
    return {'item_id': finish['output_item_id'], 'scope': 'BOUNDED_SYNTHETIC_FINISHED_CUSTOMER_DEMAND',
            'label': '独立成品：必须完成计划QC与PACK后供客户FG需求',
            'quality_semantics': 'mandatory scheduled inspection before packaging; no claim of actual quality release',
            'excluded': ['actual_execution', 'conditional_QC_outcome', 'rework', 'full_SIM_factory']}
