"""Explicit composite projection mapping through the existing native owners.

This module performs no I/O or solving.  The enterprise/version/field receipt
validator remains authoritative before this native-boundary validator runs.
"""
import copy
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from planning_adapter import InputError, duration, engine_time


def _require(ok, code, detail):
    if not ok:
        raise InputError('FOUNDATION_NATIVE_' + code + ': ' + detail)


def _quantity(value, code):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise InputError('FOUNDATION_NATIVE_' + code + ': explicit numeric value required') from None
    _require(number.is_finite(), code, 'finite numeric value required')
    return number


def _at(value, code):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (AttributeError, TypeError, ValueError):
        raise InputError('FOUNDATION_NATIVE_' + code + ': explicit timestamp required') from None
    _require(result.utcoffset() is not None, code, 'timestamp timezone required')
    return result


def _parts(data):
    resources = {r['id']: r for r in data['resources']}
    routes = {r['id']: r for r in data['routing_revisions']}
    recipes = {r['id']: r for r in data['production_recipes']}
    specs = {o['id']: (p, o) for p in recipes.values() for o in routes[p['routing_revision_id']]['operations']}
    return resources, routes, recipes, specs


def validate(data):
    """Reject missing native semantics, without accepting a legacy profile gate."""
    projection = data.get('foundation_projection')
    _require(isinstance(projection, dict), 'METADATA', 'foundation projection is required')
    _require(projection.get('schema_revision') == 'foundation-planning-projection-v1'
             and projection.get('scope') == 'RESEARCH_SIMULATION_ONLY', 'METADATA', 'explicit research projection required')
    _require(all(projection.get(k) for k in ['policy_id', 'source_version_id', 'canonical_semantic_sha256']),
             'METADATA', 'version and policy identities required')
    rules = data.get('rules', {})
    _require(rules.get('move_approved_early') == 0 and rules.get('fix_broken_supply_path') is False
             and rules.get('erase_previous_proposed') is True, 'PROTECTION', 'protected native parameters required')
    _require(rules.get('procurement_strategy') == 'PRIORITY', 'MINCOST_HARD_QC_UNSUPPORTED_919',
             'only declared PRIORITY procurement is mapped')
    resources, routes, recipes, specs = _parts(data)
    start, end = _at(data['planning_at'], 'TIME'), _at(data['horizon']['end_at'], 'TIME')
    _require(start < end, 'TIME', 'nonempty planning interval required')
    calendars = {r['id'] for r in data['calendars']}
    _require(all(r.get('availability_calendar_id') in calendars for r in resources.values()),
             'CALENDAR', 'every resource requires its evidenced calendar')
    _require(all(isinstance(d.get('customer_id'), str) and d['customer_id'].strip()
                 and d['customer_id'] != 'CUSTOMER-DEMO' for d in data['demand_lines']),
             'IDENTITY', 'each demand requires its enterprise customer')
    expected_map = {(p['id'] if len(routes[p['routing_revision_id']]['operations']) == 1 else p['id'] + ':' + o['id']):
                    {'recipe_id': p['id'], 'operation_id': o['id']}
                    for p, o in specs.values()}
    _require(projection.get('native_operation_map') == expected_map, 'IDENTITY',
             'native operation map must cover every recipe/leaf exactly')
    pools = [r for r in resources.values() if r.get('role') == 'OPERATOR_POOL']
    _require(len(pools) == 1, 'OPERATOR', 'one explicit technical operator pool required')
    skills = {s['id'] for s in data.get('skills', [])}
    qualifications = data.get('resource_skills', [])
    _require(bool(qualifications), 'QUALIFICATION', 'resource skill evidence required')
    seen = set()
    for q in qualifications:
        key = q.get('resource_id'), q.get('skill_id')
        _require(key not in seen and key[0] in resources and key[1] in skills, 'QUALIFICATION',
                 'unique, existing resource and skill required')
        seen.add(key)
        _require(type(q.get('priority')) is int and q['priority'] > 0, 'QUALIFICATION', 'explicit eligible priority required')
        a, b = _at(q.get('effective_from'), 'QUALIFICATION'), _at(q.get('effective_to'), 'QUALIFICATION')
        _require(a < b and a < end and start < b, 'QUALIFICATION', 'qualification must intersect this planning interval')
    for _, op in specs.values():
        if op.get('duration_model')=='AVAILABILITY_TRANSFER':
            receiving=projection.get('policy',{}).get('wip_progress_policy',{}).get('receiving',{})
            source=next((o for o in projection['source_tables']['operations'] if o['id']==op['id']),{})
            _require(receiving.get('basis')=='DECLARED_RESEARCH_ZERO_TIME_RECEIPT' and receiving.get('duration_seconds')==0 and source.get('continuation_kind')=='AVAILABILITY_TRANSFER' and not source.get('resource_ids') and source.get('machine_and_person_simultaneously_required') is False and op.get('availability_calendar_id')==receiving.get('calendar_id') and op['availability_calendar_id'] in calendars and _quantity(op['fixed_seconds'],'DURATION')==_quantity(op['seconds_per_unit'],'DURATION')==0 and 'resource_id' not in op and 'operator_requirement' not in op,'AVAILABILITY_TRANSFER','explicit plan-only source receiving policy required')
            continue
        resource = resources.get(op.get('resource_id'))
        _require(resource is not None, 'RESOURCE', 'operation resource missing')
        requirement = op.get('operator_requirement', {})
        _require(requirement.get('pool_id') == pools[0]['id'] and requirement.get('quantity') == '1'
                 and requirement.get('skill_id') in skills, 'OPERATOR', 'explicit one-person pool/skill requirement needed')
        _require(any(q['skill_id'] == requirement['skill_id'] and resources[q['resource_id']].get('role') == 'OPERATOR'
                     and resources[q['resource_id']].get('pool_id') == pools[0]['id'] for q in qualifications),
                 'QUALIFICATION', 'required person skill has no eligible member')
        if resource.get('role') == 'MACHINE_POOL':
            _require(op.get('machine_skill_id') in skills, 'QUALIFICATION', 'machine pool requires explicit skill')
            _require(any(q['skill_id'] == op['machine_skill_id'] and resources[q['resource_id']].get('pool_id') == resource['id']
                         for q in qualifications), 'QUALIFICATION', 'no qualified machine pool member')
        _require(op.get('duration_model') in {'FIXED_BATCH', 'FIXED_PLUS_PER_UNIT'}, 'DURATION', 'unmapped duration model')
        if op['duration_model'] == 'FIXED_PLUS_PER_UNIT':
            _require(_quantity(op.get('fixed_seconds'), 'DURATION') == 0
                     and _quantity(op.get('seconds_per_unit'), 'DURATION') > 0,
                     'DURATION', 'positive continuous processing rate required')
    window_resources = {r['id']: r for r in resources.values() if r.get('capacity_model') == 'WINDOW_QUANTITY'}
    windows = projection.get('capacity_windows')
    _require(isinstance(windows, list) and (bool(windows) or not window_resources), 'WINDOW', 'explicit windows required for window quantity resources')
    for rid, resource in window_resources.items():
        selected = sorted([w for w in windows if w.get('resource_id') == rid], key=lambda w: w.get('start_at', ''))
        _require(bool(selected), 'WINDOW', 'no windows for ' + rid)
        prior = None
        for w in selected:
            a, b = _at(w.get('start_at'), 'WINDOW'), _at(w.get('end_at'), 'WINDOW')
            _require(start <= a < b <= end and (prior is None or prior <= a), 'WINDOW', 'invalid or overlapping capacity windows')
            prior = b
            _require(_quantity(w.get('capacity_quantity'), 'CAPACITY') > 0
                     and _quantity(w['capacity_quantity'], 'CAPACITY') == _quantity(resource.get('window_capacity_quantity'), 'CAPACITY'),
                     'CAPACITY', 'capacity window quantity must match resource')
            _require(w.get('uom') == resource.get('capacity_uom') and bool(w.get('uom')),
                     'CAPACITY', 'capacity window units must match resource')
        for recipe, op in specs.values():
            if op.get('resource_id') != rid:
                continue
            _require(op['duration_model'] == 'FIXED_BATCH', 'WINDOW', 'window resources require fixed batches')
            batch = [_quantity(recipe.get(k), 'BATCH') for k in
                     ['manufacturing_minimum_quantity', 'manufacturing_maximum_quantity', 'manufacturing_multiple']]
            seconds = _quantity(op.get('fixed_seconds'), 'DURATION')
            _require(batch[0] > 0 and batch.count(batch[0]) == 3 and _quantity(op.get('seconds_per_unit'), 'DURATION') == 0,
                     'BATCH', 'indivisible fixed batch required')
            _require(seconds > 0 and all(Decimal(str((_at(w['end_at'], 'WINDOW') - _at(w['start_at'], 'WINDOW')).total_seconds())) == seconds
                                           for w in selected), 'WINDOW', 'each real window must fit the fixed processing duration')
    _require(all(w.get('resource_id') in window_resources for w in windows), 'WINDOW', 'capacity window references unknown resource')
    initial = projection.get('resource_initial_setups', {})
    operation_setups = projection.get('operation_setups', {})
    transitions = projection.get('setup_transitions', [])
    _require(bool(initial) and all(r in resources and isinstance(s, str) and s for r, s in initial.items()),
             'SETUP', 'explicit real resource initial setup required')
    _require(all(o in specs and isinstance(s, str) and s for o, s in operation_setups.items()), 'SETUP', 'unknown operation setup identity')
    for oid, (_, op) in specs.items():
        if op.get('duration_model')=='AVAILABILITY_TRANSFER':continue
        candidates = {op['resource_id']} | {r['id'] for r in resources.values() if r.get('pool_id') == op['resource_id']}
        if candidates.intersection(initial):
            _require(oid in operation_setups, 'SETUP', 'required operation setup missing: ' + oid)
    states = set(initial.values()) | set(operation_setups.values())
    edges = set()
    for t in transitions:
        edge = t.get('from_state'), t.get('to_state')
        _require(edge not in edges and edge[0] in states and edge[1] in states
                 and _quantity(t.get('duration_seconds'), 'SETUP') >= 0, 'SETUP', 'invalid or duplicate setup transition')
        edges.add(edge)
    _require(edges == {(a, b) for a in states for b in states}, 'SETUP', 'setup transitions are incomplete; no default duration')
    sources = {s['id']: s for s in data['supply_sources']}
    for source in sources.values():
        cap = source.get('capacity', {})
        _require(cap.get('scope') == 'WHOLE_HORIZON_RECEIPTS' and _quantity(cap.get('quantity'), 'CAPACITY') > 0,
                 'CAPACITY', 'explicit finite supplier horizon capacity required')
        _require(cap.get('start_at') == data['planning_at'] and cap.get('end_at') == data['horizon']['end_at'],
                 'CAPACITY', 'supplier capacity must use the same horizon')
        _require(type(source.get('priority')) is int and source['priority'] > 0
                 and source.get('batch_window_seconds') == 0, 'SUPPLY', 'explicit source priority and no aggregation required')
        _require('wait_seconds' in source.get('receiving_qc', {}) and source['receiving_qc']['wait_seconds'] >= 0,
                 'QC', 'explicit receiving QC duration required')
    for existing in data['existing_orders']:
        if existing['type'] == 'PO':
            source = sources.get(existing.get('supply_source_id'))
            _require(source is not None and source['supplier_id'] == existing.get('supplier_id')
                     and source['item_id'] == existing.get('item_id'), 'SUPPLY', 'PO source identity mismatch')
            _require(existing.get('source_status') == 'confirmed' and existing.get('inventory_includes_receipt') is False,
                     'QC', 'confirmed PO must be excluded from opening stock')
            ordered, received, cancelled, remaining = [_quantity(existing.get(k), 'QC') for k in
                ['ordered_quantity', 'received_quantity', 'cancelled_quantity', 'remaining_quantity']]
            _require(ordered > 0 and remaining > 0 and received >= 0 and cancelled == 0
                     and ordered == received + cancelled + remaining, 'QC',
                     'explicit reconciled unreceived PO balances required')
            a = _at(existing.get('expected_receipt_at'), 'QC')
            b = _at(existing.get('available_at'), 'QC')
            _require(b == a + timedelta(seconds=source['receiving_qc']['wait_seconds']), 'QC', 'physical/QC dates disagree')
        elif existing['type'] == 'MO':
            commitments = existing.get('operations', [])
            _require(existing.get('source_status') in {'confirmed', 'approved'} and existing.get('freeze_policy') == 'LOCKED'
                     and bool(commitments) and existing.get('recipe_id') in recipes,
                     'PROTECTION', 'protected source processing commitments required')
            if len(commitments)>1:
                route=routes[recipes[existing['recipe_id']]['routing_revision_id']]['operations']
                _require([c.get('operation_id') for c in commitments]==[o['id'] for o in route],
                         'PROTECTION','complete ordered external MO routing required')
                _require(existing.get('start_at')==commitments[0]['start_at'] and existing.get('end_at')==commitments[-1]['end_at'],
                         'PROTECTION','external MO envelope differs from its commitments')
            prior=None
            for c in commitments:
                _require(c.get('operation_id') in specs and specs[c['operation_id']][0]['id'] == existing['recipe_id']
                         and c.get('resource_id') in resources, 'PROTECTION', 'MO processing identity missing')
                a, b = _at(c.get('start_at'), 'PROTECTION'), _at(c.get('end_at'), 'PROTECTION')
                _require(start <= a < b <= end and (prior is None or prior<=a),
                         'PROTECTION', 'MO original window outside horizon or routing order')
                prior=b
                recipe, op = specs[c['operation_id']]
                machine = resources[c['resource_id']]
                _require(machine.get('role') == 'MACHINE' and
                         (machine['id'] == op['resource_id'] or machine.get('pool_id') == op['resource_id']),
                         'PROTECTION', 'protected MO machine is not an operation candidate')
                qty = _quantity(existing.get('quantity'), 'PROTECTION')
                _require(qty > 0 and _quantity(c.get('quantity'), 'PROTECTION') == qty,
                         'PROTECTION', 'MO/processing commitment quantity differs')
                seconds = (_quantity(op['fixed_seconds'], 'PROTECTION') if op['duration_model'] == 'FIXED_BATCH'
                           else _quantity(op.get('fixed_seconds',0),'PROTECTION')+qty * _quantity(op['seconds_per_unit'], 'PROTECTION'))
                observed_seconds=Decimal(str((b-a).total_seconds()))
                if projection.get('policy',{}).get('profile')=='odoo-verified-manufacturing-v1':
                    # Odoo native processing may span a real nonworking break.
                    # Count the intersection of BOTH machine and assigned person
                    # availability, retaining the exact original external window.
                    from p22.foundation_audit import working_seconds
                    calendar_by_id={x['id']:x for x in data['calendars']}
                    person=resources.get(c.get('operator_id'),{})
                    mcal=calendar_by_id[machine['availability_calendar_id']]['available_intervals']
                    pcal=calendar_by_id.get(person.get('availability_calendar_id'),{}).get('available_intervals',[])
                    spans=[(max(_at(x['start_at'],'CALENDAR'),_at(y['start_at'],'CALENDAR')),min(_at(x['end_at'],'CALENDAR'),_at(y['end_at'],'CALENDAR'))) for x in mcal for y in pcal]
                    observed_seconds=Decimal(str(working_seconds(a,b,[(x,y) for x,y in spans if x<y])))
                _require(observed_seconds == seconds,
                         'PROTECTION', 'protected MO processing duration changed')
                if len(commitments)>1:
                    person=resources.get(c.get('operator_id'),{})
                    _require(person.get('role')=='OPERATOR' and any(g['resource_id']==person['id'] and
                        g['skill_id']==op['operator_requirement']['skill_id'] and _at(g['effective_from'],'PROTECTION')<=a and
                        b<=_at(g['effective_to'],'PROTECTION') for g in data['resource_skills']),
                        'PROTECTION','external MO named person qualification missing')
                if op['duration_model'] == 'FIXED_BATCH':
                    _require(qty == _quantity(recipe['manufacturing_minimum_quantity'], 'PROTECTION')
                             and any(w['resource_id'] == machine['id'] and _at(w['start_at'], 'WINDOW') == a
                                     and _at(w['end_at'], 'WINDOW') == b for w in windows),
                             'PROTECTION', 'protected fixed MO must occupy its original complete batch window')
        else:
            raise InputError('FOUNDATION_NATIVE_PROTECTION: unmapped existing order type')
    continuity=projection.get('policy',{}).get('profile')=='continuity-current-state-v1'
    if continuity:
        # Continuity's authenticated rebuild reconciles actual WMS receipts
        # against ERP identities and current WMS opening. These are historical
        # receipts, unlike the separate excluded-opening receipt profile.
        from p22.continuity_projection import validate as validate_continuity
        validate_continuity(data)
        _require(not data.get('purchase_receipts'), 'QC', 'current-balance continuity cannot replay historical receipts')
    elif data.get('purchase_receipts') or any(_quantity(p.get('received_quantity','0'),'QC')>0 for p in data['existing_orders'] if p['type']=='PO'):
        from multi_receipt_supply import validate_receipt_snapshot
        validate_receipt_snapshot(data,lambda ok,why:_require(ok,'QC',why))
    if data.get('distribution_lanes'):
        from network_distribution import validate_joint_lanes
        validate_joint_lanes(data,lambda ok,why:_require(ok,'DISTRIBUTION',why))
    return data


def map_native(data, native=None):
    """Map the complete checked projection, preserving source data byte semantics."""
    validate(data)
    from planning_adapter import _to_native_standard
    from qualified_operator import map_native as map_qualified
    from fixed_batch import add_window_resource
    from priority_supply import add_native as map_supply_terms

    projection = data['foundation_projection']
    resources, routes, recipes, specs = _parts(data)
    neutral_bucket_resources=set()
    neutral_bucket_operations=set()
    if projection.get('policy',{}).get('profile')=='continuity-current-state-v1':
        neutral_bucket_resources={rid for rid,r in resources.items() if r.get('capacity_model')=='WINDOW_QUANTITY'}
        _require(projection['policy'].get('setup_policy')=='NO_ADDITIONAL_SETUP_IN_DECLARED_SIMULATION_NEUTRAL_STATE'
                 and all(_quantity(t['duration_seconds'],'SETUP')==0 and t['from_state']==t['to_state']=='CONT:NEUTRAL' for t in projection['setup_transitions'])
                 and all(projection['resource_initial_setups'].get(rid)=='CONT:NEUTRAL' for rid in neutral_bucket_resources),
                 'SETUP','bucket setup omission requires the authenticated neutral zero-duration contract')
        neutral_bucket_operations={oid for oid,(_,o) in specs.items() if o.get('resource_id') in neutral_bucket_resources}
    zone = data['site']['timezone']
    result = copy.deepcopy(native) if native is not None else _to_native_standard(data)
    # Every upload explicitly resets buffer policy values. Importing a later
    # scenario must not retain a prior candidate's targets in the mutable engine.
    policies={}
    receipt=projection.get('professional_overlay')
    if receipt:
        from p22.professional_policy import validate_overlay
        validate_overlay(data)
        stock=receipt['changes'].get('replenishment')
        policies={(r['item_id'],r['location_id']):r for r in stock['rows']} if stock else {}
    for row in result['buffer']:
        policy=policies.get((row['item'],row['location']),{})
        row.update(minimum=policy.get('minimum','0'),maximum=policy.get('maximum','0'),minimum_calendar=None,maximum_calendar=None)
    customers = {d['id']: d['customer_id'] for d in data['demand_lines']}
    result['customer'] = [{'name': c} for c in sorted(set(customers.values()))]
    for demand in result['demand']:
        demand['customer'] = customers[demand['name']]
    result = map_qualified(data, result)
    if projection.get('policy',{}).get('profile')=='odoo-verified-manufacturing-v1':
        # Explicit continuous unit capacity, with source working time on
        # `available`. Reusing a 0/1 working calendar as maximum_calendar
        # makes a confirmed cross-break envelope falsely overload at lunch.
        # Capacity stays finite=1; source absences remain actual unavailable
        # processing time and the independent calendar/function audit applies.
        for row in result['resource']:
            fact=resources[row['name']]
            _require(fact.get('capacity_model','CONTINUOUS')=='CONTINUOUS','CAPACITY','Odoo bounded continuous resource required')
            row.update(available=fact['availability_calendar_id'],maximum_calendar=None)
    # The qualified helper maps people. Machine membership uses the same native hierarchy.
    for row in result['resource']:
        fact = resources[row['name']]
        if fact.get('role') == 'MACHINE' and fact.get('pool_id'):
            row['owner'] = fact['pool_id']
    processing = {p['id'] + ':' + o['id']: o for p, o in specs.values()}
    for load in result['operationresource']:
        op = processing[load['operation']]
        if load['resource'] == op['resource_id'] and op.get('machine_skill_id'):
            load.update(skill=op['machine_skill_id'], search='PRIORITY')
        if load['resource'] == op['resource_id'] and op['id'] in projection['operation_setups'] and op['id'] not in neutral_bucket_operations:
            load['setup'] = projection['operation_setups'][op['id']]
    # Source work shifts remain untouched. Derived legal windows are separate calendars.
    fixed_work = {}
    for rid, resource in resources.items():
        if resource.get('capacity_model') != 'WINDOW_QUANTITY':
            continue
        work = 'FOUNDATION-WORK-WINDOWS:' + rid
        fixed_work[rid] = work
        selected = [w for w in projection['capacity_windows'] if w['resource_id'] == rid]
        intervals = [{k: w[k] for k in ['start_at', 'end_at']} for w in selected]
        result['calendar'].append({'name': work, 'defaultvalue': '0'})
        for w in intervals:
            result['calendarbucket'].append(dict(id=len(result['calendarbucket']) + 1, calendar=work,
                startdate=engine_time(w['start_at'], zone), enddate=engine_time(w['end_at'], zone),
                value='1', priority=0, **{d: True for d in
                    ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']},
                starttime='00:00:00', endtime='23:59:59'))
        temporary = {**data, 'calendars': data['calendars'] + [{'id': work, 'available_intervals': intervals}]}
        result['resource'] = [r for r in result['resource'] if r['name'] != rid]
        add_window_resource(temporary, result, {**resource, 'availability_calendar_id': work}, legal_start_only=True)
    op_rows = {r['name']: r for r in result['operation']}
    rename = {}
    for recipe in recipes.values():
        operations = routes[recipe['routing_revision_id']]['operations']
        for op in operations:
            child = op_rows[recipe['id'] + ':' + op['id']]
            if op['duration_model']=='AVAILABILITY_TRANSFER':
                child.update(type='fixed_time',duration=duration(op['fixed_seconds']),available=op['availability_calendar_id'])
                child.pop('duration_per',None)
            if op['duration_model']=='FIXED_PLUS_PER_UNIT':
                child.update(sizeminimum=recipe['manufacturing_minimum_quantity'],sizemultiple=recipe['manufacturing_multiple'])
                if recipe.get('manufacturing_maximum_quantity') is not None:child['sizemaximum']=recipe['manufacturing_maximum_quantity']
            if op['duration_model'] == 'FIXED_BATCH':
                size = dict(sizeminimum=recipe['manufacturing_minimum_quantity'],
                            sizemaximum=recipe['manufacturing_maximum_quantity'], sizemultiple=recipe['manufacturing_multiple'])
                child.update(type='fixed_time', duration=duration(op['fixed_seconds']), available=fixed_work[op['resource_id']], **size)
                child.pop('duration_per', None)
                op_rows[recipe['id']].update(size)
            if len(operations) == 1:
                old = child['name']
                merged = {**op_rows[recipe['id']], **child, 'name': recipe['id'], 'item': recipe['output_item_id']}
                merged.pop('owner', None)
                merged.pop('priority', None)
                op_rows[recipe['id']] = merged
                op_rows.pop(old)
                rename[old] = recipe['id']
    result['operation'] = list(op_rows.values())
    # Multiple producing operations are assembled by the native buffer Owner
    # into its replenishment alternate. Only declared source groups set order.
    for group in projection.get('editor_scope',{}).get('alternate_groups',[]):
        for rank,rid in enumerate(group['recipe_ids'],1):op_rows[rid]['priority']=rank
    stage_sources=[s for s in projection.get('source_tables',{}).get('recipes',[]) if s.get('progress_contract')=='stage-flow-v2']
    stage_outputs={s['output_item_id'] for s in stage_sources}
    for recipe in recipes.values():
        if recipe['output_item_id'] in stage_outputs:op_rows[recipe['id']]['priority']=1 if any(s['id']==recipe['id'] for s in stage_sources) else 2
    for table in ['operationresource', 'operationmaterial']:
        for row in result[table]:
            row['operation'] = rename.get(row['operation'], row['operation'])
    # All sources, rather than the legacy first confirmed PO, receive their own terms.
    map_supply_terms(data, result)
    if projection.get('policy',{}).get('profile')=='continuity-current-state-v1':
        # Empty source price stays unknown. Native default cost is only a
        # technical value: this profile uses PRIORITY and makes no value claim.
        for row in result['itemsupplier']:
            if row.get('cost') is None:row.pop('cost',None)
    result['purchaseorder'] = [dict(reference=p['id'], status=p['source_status'], item=p['item_id'],
        supplier=p['supplier_id'], location=p['location_id'], quantity=p['remaining_quantity'],
        startdate=engine_time(p.get('planned_start_at') or p['ordered_at'], zone),
        enddate=engine_time(p['expected_receipt_at'], zone), source=p['external_ref'])
        for p in data['existing_orders'] if p['type'] == 'PO']
    actual_names = {(v['recipe_id'], v['operation_id']): k for k, v in projection['native_operation_map'].items()}
    result['manufacturingorder'] = []
    for m in data['existing_orders']:
        if m['type'] != 'MO':
            continue
        bindings={}
        for c in m['operations']:
            _,op=specs[c['operation_id']];resource=resources[c['resource_id']]
            quantity=_quantity(op['resource_units'],'CAPACITY')
            if resource.get('capacity_model')=='WINDOW_QUANTITY':quantity*=_quantity(m['quantity'],'CAPACITY')
            bindings[c['operation_id']]=(actual_names[m['recipe_id'],c['operation_id']],format(quantity,'f'))
        from interop.manufacturing import native_manufacturing_rows
        result['manufacturingorder'].extend(native_manufacturing_rows(m,bindings,zone))
    matrix = 'FOUNDATION-SETUP:' + projection['source_version_id']
    result['setupmatrix'] = [{'name': matrix, 'source': projection['policy_id']}]
    result['setuprule'] = [dict(id=i + 1, setupmatrix=matrix, priority=i + 1,
        fromsetup=t['from_state'], tosetup=t['to_state'], duration=duration(t['duration_seconds']),
        source=t.get('id', projection['policy_id'])) for i, t in enumerate(projection['setup_transitions'])]
    for row in result['resource']:
        if row['name'] in projection['resource_initial_setups'] and row['name'] not in neutral_bucket_resources:
            row.update(setup=projection['resource_initial_setups'][row['name']], setupmatrix=matrix)
    result['resource'].sort(key=lambda r: bool(r.get('owner')))
    if data.get('purchase_receipts'):
        from multi_receipt_supply import map_receipt_availability
        map_receipt_availability(data,result)
    if data.get('distribution_lanes'):
        from network_distribution import map_joint_lanes
        map_joint_lanes(data,result)
    from optimization.native_controls import configure
    configure(data,result)
    parameters = {'plan.move_approved_early': '0', 'plan.fixBrokenSupplyPath': 'false', 'plan.individualPoolResources': 'false'}
    result['parameter'] = [p for p in result['parameter'] if p['name'] not in parameters]
    result['parameter'].extend({'name': k, 'value': v} for k, v in parameters.items())
    order = ['parameter', 'location', 'customer', 'item', 'calendar', 'calendarbucket', 'skill', 'setupmatrix',
             'resource', 'resourceskill', 'setuprule', 'operation', 'operationresource', 'operationmaterial',
             'buffer', 'supplier', 'itemsupplier', 'purchaseorder', 'manufacturingorder', 'demand']
    return {k: result[k] for k in order if k in result} | {k: v for k, v in result.items() if k not in order}
