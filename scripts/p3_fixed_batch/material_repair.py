"""Bounded research coordination of existing native purchase proposals.

This is an opt-in adapter after native SupplyPlanning, not a replacement solver.
Only the foundation WHOLE_HORIZON_RECEIPTS single bucket, unit supplier load,
single unit receipt with a fixed QC offset is supported. Unknown models fail
closed. Native quantity setters own flow/load propagation; no balance or
feasibility field is assigned here. A rejected mutation invalidates the run;
the caller must retain the failure rather than export it as a usable plan.
"""
import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from zoneinfo import ZoneInfo


REVISION = 'bounded-proposed-po-topup-v1'
EPSILON = Decimal('0.000001')


class MaterialRepairError(ValueError):
    """A failed coordination with a JSON-safe diagnostic receipt attached."""
    def __init__(self, code, receipt, detail=None):
        receipt.update(status='REJECTED', reason=code)
        if detail is not None:
            receipt['detail'] = str(detail)
        self.receipt = receipt
        super().__init__('MATERIAL_REPAIR_' + code + (':' + str(detail) if detail else ''))


def _number(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('nonfinite quantity')
    return result


def _text(value):
    value = _number(value)
    if abs(value) <= EPSILON:
        value = Decimal(0)
    return format(value.normalize(), 'f')


def _name(obj):
    return getattr(obj, 'name', None) if obj is not None else None


def _ref(obj):
    return str(obj.reference) if obj is not None else None


def _seconds(value):
    return _number(value.total_seconds() if isinstance(value, timedelta) else value)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':')).encode('utf-8')).hexdigest()


def repair(frepple, spec):
    """Resize eligible native proposed POs under the explicit research flag.

    Missing/disabled flag returns without inspecting or changing native state.
    Every chosen PO must independently cover its buffer's complete negative
    prefix. There is no split-order search or creation, and at most the declared
    number of adjustments. A second, existing foundation feasibility refresh
    may follow this function; both use the official no-argument native method.
    """
    config = spec.get('foundation', {}).get('material_repair')
    receipt = dict(status='NOT_ENABLED', revision=REVISION,
        module_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        method='EXISTING_PROPOSED_PO_NATIVE_QUANTITY_SETTER',
        simulation_only=True, execution_authorized=False, steps=[],
        limits=['WHOLE_HORIZON_RECEIPTS_SINGLE_BUCKET', 'UNIT_LOAD_AND_QC_RECEIPT',
                'NO_NEW_ORDER_NO_DATE_OR_SOURCE_CHANGE', 'NO_GLOBAL_OPTIMALITY_CLAIM'])
    if not config or config.get('enabled') is not True:
        return receipt

    def require(condition, code, detail=None):
        if not condition:
            raise MaterialRepairError(code, receipt, detail)

    try:
        require(config.get('revision') == REVISION
            and config.get('scope') == 'ADVANCED_PLANNING_RESEARCH'
            and config.get('simulation_only') is True
            and config.get('execution_authorized') is False, 'SCOPE')
        require(isinstance(spec.get('run_id'), str) and bool(spec['run_id'])
            and isinstance(spec.get('input_hash'), str) and len(spec['input_hash']) == 64, 'IDENTITY')
        receipt.update(run_id=spec['run_id'], input_hash=spec['input_hash'], scope=config['scope'])
        receipt['native_version'] = str(getattr(frepple, 'version', 'NOT_OBSERVED'))
        zone = ZoneInfo(config['time_zone'])

        def at(value):
            value = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace('Z', '+00:00'))
            return value.astimezone(zone).replace(tzinfo=None) if value.tzinfo is not None else value

        planning = at(frepple.settings.current)
        dimensions = config['allowed_dimensions']
        require(isinstance(dimensions, list) and 0 < len(dimensions) <= 30, 'SCOPE')
        allowed = {}
        for dim in dimensions:
            require(isinstance(dim, dict) and all(isinstance(dim.get(k), str) and dim[k]
                for k in ('item_id', 'location_id', 'uom')), 'SCOPE')
            key = (dim['item_id'], dim['location_id'])
            require(key not in allowed, 'SCOPE', 'duplicate dimension')
            allowed[key] = dim['uom']
        budget = config.get('max_adjustments')
        require(type(budget) is int and 1 <= budget <= 30, 'SCOPE')

        def scan_buffers():
            """Keep native iterator order, including every same-clock prefix."""
            result = {}
            for buffer in frepple.buffers():
                key = (_name(buffer.item), _name(buffer.location))
                require(key not in result, 'BUFFER_MODEL', 'duplicate item/location')
                opening = _number(buffer.onhand)
                balance = opening
                minimum = opening
                first_negative = None
                events = []
                opening_seen = False
                previous_date = None
                for index, flow in enumerate(buffer.flowplans):
                    when, qty, reference = at(flow.date), _number(flow.quantity), _ref(flow.operationplan)
                    require(previous_date is None or when >= previous_date, 'EVENT_ORDER', buffer.name)
                    previous_date = when
                    is_opening = when < planning and reference == buffer.name
                    if is_opening:
                        require(not opening_seen and abs(qty - opening) <= EPSILON, 'OPENING_MODEL', buffer.name)
                        opening_seen = True
                    else:
                        balance += qty
                    require(abs(balance - _number(flow.onhand)) <= EPSILON, 'FLOW_BALANCE', buffer.name)
                    minimum = min(minimum, balance)
                    if balance < -EPSILON and first_negative is None:
                        first_negative = index
                    events.append(dict(reference=reference, at=when.isoformat(), quantity=_text(qty),
                        onhand=_text(balance), index=index, opening=is_opening))
                result[key] = dict(name=buffer.name, opening=_text(opening), minimum=_text(minimum),
                    first_negative=first_negative, events=events)
            return result

        before_buffers = scan_buffers()
        negative = {key: value for key, value in before_buffers.items()
            if _number(value['minimum']) < -EPSILON}
        require(all(key in allowed for key in negative), 'OUTSIDE_SCOPE_SHORTAGE')
        if not negative:
            receipt.update(status='NO_HARD_SHORTAGE', minimum_before=_text(min(
                (_number(b['minimum']) for b in before_buffers.values()), default=Decimal(0))))
            return receipt
        receipt['minimum_before'] = _text(min(_number(b['minimum']) for b in negative.values()))

        operations = {o.name: o for o in frepple.operations()}
        plans = {}
        for operation in operations.values():
            for plan in operation.operationplans:
                ref = _ref(plan)
                require(ref not in plans, 'PLAN_MODEL', 'duplicate reference ' + ref)
                plans[ref] = plan

        def plan_facts():
            return {ref: dict(quantity=_text(p.quantity), ordertype=p.ordertype,
                operation=p.operation.name, start=at(p.start).isoformat(), end=at(p.end).isoformat(),
                status=p.status, owner=_ref(p.owner), item=_name(getattr(p, 'item', None)),
                location=_name(getattr(p, 'location', None)), supplier=_name(getattr(p, 'supplier', None)),
                source=getattr(p, 'source', None), demand=_name(getattr(p, 'demand', None)),
                setup_end=at(p.setupend).isoformat() if hasattr(p, 'setupend') else None,
                setup_seconds=_text(_seconds(p.setup)) if hasattr(p, 'setup') else None)
                for ref, p in plans.items()}

        def load_facts():
            rows = []
            for resource in frepple.resources():
                for load in resource.loadplans:
                    rows.append(dict(resource=resource.name, reference=_ref(load.operationplan),
                        quantity=_text(load.quantity), at=at(load.date).isoformat(),
                        start=at(load.startdate).isoformat(), end=at(load.enddate).isoformat()))
            return sorted(rows, key=lambda r: tuple(r.values()))

        before_plans, before_loads = plan_facts(), load_facts()
        frozen_mos = {ref: row for ref, row in before_plans.items() if row['ordertype'] == 'MO'}
        receipt['manufacturing_frozen'] = dict(count=len(frozen_mos), sha256=_hash(frozen_mos),
            fields=['reference', 'quantity', 'status', 'operation', 'start', 'end', 'owner',
                    'item', 'location', 'supplier', 'source', 'demand', 'setup_end', 'setup_seconds'], loads_sha256=_hash(
                        [row for row in before_loads if row['reference'] in frozen_mos]))

        sources = spec['foundation']['sources']
        require(isinstance(sources, list), 'SOURCE_MODEL')
        source_ids = [s['id'] for s in sources]
        require(len(set(source_ids)) == len(source_ids), 'SOURCE_MODEL')
        source_models = []
        seen_operations, seen_resources = set(), set()

        def purchase_owner(plan, key):
            """Recognize only native's empty, single-PO replenishment ALT.

            9.19 Operation::setOperationPlanQuantity (operation.cpp 2241-2245)
            mirrors the child quantity on this ALT. No parent setter fallback
            is used: absent native propagation remains a rejected run.
            """
            owner = plan.owner
            if owner is None:
                return None
            reference = _ref(owner)
            observation = dict(reference=reference, ordertype=owner.ordertype,
                operation=owner.operation.name, operation_type=type(owner.operation).__name__,
                status=owner.status, owner=_ref(owner.owner), quantity=_text(owner.quantity),
                start=at(owner.start).isoformat(), end=at(owner.end).isoformat(),
                demand=_name(getattr(owner, 'demand', None)),
                setup_seconds=_text(_seconds(getattr(owner, 'setup', 0))),
                purchase=dict(reference=_ref(plan), status=plan.status, quantity=_text(plan.quantity),
                    start=at(plan.start).isoformat(), end=at(plan.end).isoformat()),
                flow_count=len(list(owner.operation.flows)), load_count=len(list(owner.operation.loads)))
            receipt.setdefault('alternate_owner_observations', {})[_ref(plan)] = observation
            children = sorted(_ref(p) for p in plans.values() if _ref(p.owner) == reference)
            native_children = sorted(_ref(p) for p in owner.operationplans)
            observation.update(global_children=children, native_children=native_children)
            # getSetup() returns Duration(-1) when no setup event exists
            # (9.19 operationplan.cpp 2646-2647; model.h default -1). It is
            # an unset sentinel, never a negative processing duration.
            checks = dict(registered_reference=reference in plans,
                alternate_operation=type(owner.operation).__name__ == 'operation_alternate',
                alternate_ordertype=owner.ordertype == 'ALT', proposed_owner=owner.status == 'proposed',
                top_level_owner=owner.owner is None, no_demand=observation['demand'] is None,
                proposed_purchase=plan.status == 'proposed',
                replenishment_name=owner.operation.name == 'Replenish ' + before_buffers[key]['name'],
                no_operation_flow=observation['flow_count'] == 0,
                no_operation_load=observation['load_count'] == 0,
                same_quantity=_number(owner.quantity) == _number(plan.quantity),
                same_start=at(owner.start) == at(plan.start), same_end=at(owner.end) == at(plan.end),
                setup_absent_or_zero=_number(observation['setup_seconds']) in (Decimal(-1), Decimal(0)),
                single_global_child=children == [_ref(plan)], native_children_match=native_children == children,
                no_actual_load=not any(row['reference'] == reference for row in load_facts()),
                no_actual_flow=not any(event['reference'] == reference
                    for buffer in before_buffers.values() for event in buffer['events']))
            observation.update(checks=checks, failed_checks=[name for name, passed in checks.items() if not passed])
            require(all(checks.values()), 'OWNER_MODEL',
                _ref(plan) + ':' + ','.join(observation['failed_checks']))
            return owner

        def source_model(source):
            key = (source['item_id'], source['location_id'])
            name = ('Purchase ' + key[0] + ' @ ' + key[1] + ' from ' + source['supplier_id']
                + ' valid from ' + source['effective_from_native'])
            require(name in operations and name not in seen_operations, 'SOURCE_MODEL', source['id'])
            seen_operations.add(name)
            operation = operations[name]
            require(type(operation).__name__ == 'operation_itemsupplier', 'SOURCE_MODEL', name)
            cap = source['capacity']
            require(cap.get('scope') == 'WHOLE_HORIZON_RECEIPTS' and cap.get('uom') == allowed[key],
                'CAPACITY_MODEL', source['id'])
            total, minimum, multiple = (_number(cap['quantity']), _number(source['minimum_order_quantity']),
                _number(source['order_multiple']))
            require(total > 0 and minimum > 0 and multiple > 0, 'SOURCE_MODEL', source['id'])
            lead, qc = _number(source['lead_time_seconds']), _number(source['receiving_qc']['wait_seconds'])
            require(lead >= 0 and qc >= 0 and source.get('batch_window_seconds', 0) == 0, 'SOURCE_MODEL')
            require(abs(_seconds(operation.duration) - lead) <= EPSILON
                and abs(_number(operation.size_minimum) - minimum) <= EPSILON
                and abs(_number(operation.size_multiple) - multiple) <= EPSILON
                and _seconds(getattr(operation, 'batchwindow', 0)) == 0, 'SOURCE_TERMS', source['id'])
            start, end = at(source['effective_from']), at(source['effective_to'])
            require(start == at(source['effective_from_native']) and end == at(source['effective_to_native'])
                and start < end, 'SOURCE_EFFECTIVITY', source['id'])
            cap_start, cap_end = at(cap['start_at']), at(cap['end_at'])
            require(cap_start == planning and cap_end > cap_start, 'CAPACITY_MODEL', source['id'])
            flows, loads = list(operation.flows), list(operation.loads)
            require(len(flows) == 1 and _number(flows[0].quantity) == 1
                and abs(_seconds(flows[0].offset) - qc) <= EPSILON, 'QC_MODEL', source['id'])
            require(len(loads) == 1 and _number(loads[0].quantity) == 1, 'CAPACITY_MODEL', source['id'])
            resource = loads[0].resource
            resource_name = 'P3-SUPPLY-CAP:' + source['id']
            require(resource.name == resource_name and resource_name not in seen_resources
                and type(resource).__name__ == 'resource_buckets', 'CAPACITY_MODEL', source['id'])
            seen_resources.add(resource_name)
            purchases = [p for p in plans.values() if p.operation.name == name]
            require(all(p.ordertype == 'PO'
                and (_name(p.item), _name(p.location), _name(p.supplier))
                    == (*key, source['supplier_id']) and p.status in ('proposed', 'confirmed')
                for p in purchases), 'SOURCE_MODEL', source['id'])
            for purchase in purchases:
                purchase_owner(purchase, key)
            used = sum((_number(p.quantity) for p in purchases), Decimal(0))
            require(used <= total + EPSILON, 'CAPACITY_EXCEEDED', source['id'])
            calendar = resource.maximum_calendar
            require(calendar is not None and calendar.name == resource_name + ':TOTAL'
                and _number(calendar.default) == 0, 'CAPACITY_MODEL', source['id'])
            buckets = list(calendar.buckets)
            require(len(buckets) == 1, 'CAPACITY_MODEL', source['id'])
            bucket = buckets[0]
            accounting_start = min([cap_start] + [at(p.start) for p in purchases])
            require(at(bucket.start) == accounting_start and at(bucket.end) == cap_end
                and abs(_number(bucket.value) - total) <= EPSILON and bucket.days == 127
                and _seconds(bucket.starttime) == 0 and _seconds(bucket.endtime) == 86400,
                'CAPACITY_MODEL', source['id'])
            calendar_facts = dict(name=calendar.name, default=_text(calendar.default),
                start=at(bucket.start).isoformat(), end=at(bucket.end).isoformat(),
                value=_text(bucket.value), days=bucket.days, starttime=0, endtime=86400)
            expected_loads = sorted(((_ref(p), _text(-_number(p.quantity)), at(p.start).isoformat(),
                at(p.start).isoformat(), at(p.end).isoformat()) for p in purchases))
            actual_loads = sorted((_ref(l.operationplan), _text(l.quantity), at(l.date).isoformat(),
                at(l.startdate).isoformat(), at(l.enddate).isoformat()) for l in resource.loadplans)
            require(actual_loads == expected_loads, 'CAPACITY_LOAD', source['id'])
            receipt_events = []
            for p in purchases:
                qty, physical = _number(p.quantity), at(p.end)
                available = physical + timedelta(seconds=float(qc))
                require(qty > 0 and cap_start <= physical <= available < cap_end,
                    'QC_MODEL', _ref(p))
                # Confirmed sources can legitimately have an immutable start
                # before T0/effectivity; their remaining receipts still consume
                # this total bucket. Only proposed orders are change candidates.
                if p.status == 'proposed':
                    require(at(p.start) >= max(planning, start) and physical <= end
                        and _seconds(physical - at(p.start)) >= lead
                        and qty >= minimum and abs(qty % multiple) <= EPSILON,
                        'SOURCE_TERMS', _ref(p))
                events = [e for e in before_buffers[key]['events'] if e['reference'] == _ref(p)]
                require(len(events) == 1 and _number(events[0]['quantity']) == qty
                    and at(events[0]['at']) == available and not events[0]['opening'], 'QC_MODEL', _ref(p))
                receipt_events.append((p, events[0]))
            return dict(source=source, key=key, operation=operation, resource=resource,
                total=total, used=used, minimum=minimum, multiple=multiple, qc=qc,
                maximum=_number(operation.size_maximum), calendar=calendar_facts, receipts=receipt_events)

        for source in sources:
            key = (source['item_id'], source['location_id'])
            if key in negative:
                source_models.append(source_model(source))
        # Every actual PO on a repaired dimension must have exactly one
        # recognized source, so unknown loads cannot disappear from accounting.
        recognized = {row['operation'].name for row in source_models}
        require(all(p.operation.name in recognized for p in plans.values()
            if p.ordertype == 'PO' and (_name(p.item), _name(p.location)) in negative), 'SOURCE_MODEL')

        adjustments = []
        for key, buffer in sorted(negative.items()):
            deficit = -_number(buffer['minimum'])
            candidates = []
            for model in source_models:
                if model['key'] != key:
                    continue
                increment = (deficit / model['multiple']).to_integral_value(rounding=ROUND_CEILING) * model['multiple']
                for plan, event in model['receipts']:
                    if plan.status != 'proposed' or event['index'] >= buffer['first_negative']:
                        continue
                    new_quantity = _number(plan.quantity) + increment
                    if (model['used'] + increment <= model['total'] + EPSILON
                        and new_quantity >= model['minimum'] and new_quantity <= model['maximum'] + EPSILON
                        and abs(new_quantity % model['multiple']) <= EPSILON):
                        candidates.append((model['source']['priority'], event['at'], _ref(plan),
                            plan, model, increment, event))
            require(bool(candidates), 'NO_LEGAL_TOPUP', '/'.join(key))
            _, _, _, plan, model, increment, event = sorted(candidates, key=lambda c: c[:3])[0]
            adjustments.append((plan, model, increment))
            receipt['steps'].append(dict(reference=_ref(plan), source_id=model['source']['id'],
                item_id=key[0], location_id=key[1], uom=allowed[key],
                before_quantity=_text(plan.quantity), increment=_text(increment),
                after_quantity=_text(_number(plan.quantity) + increment),
                physical_receipt_at=at(plan.end).isoformat(), qc_available_at=event['at'],
                qc_wait_seconds=_text(model['qc']), source_total_capacity=_text(model['total']),
                source_used_before=_text(model['used']), source_used_after=_text(model['used'] + increment),
                minimum_order_quantity=_text(model['minimum']), order_multiple=_text(model['multiple']),
                capacity_calendar=model['calendar'], hard_prefix_minimum_before=buffer['minimum']))
            if plan.owner is not None:
                receipt['steps'][-1]['alternate_owner'] = dict(reference=_ref(plan.owner),
                    operation=plan.owner.operation.name, before_quantity=_text(plan.owner.quantity),
                    after_quantity=_text(_number(plan.owner.quantity) + increment),
                    propagation='OFFICIAL_CHILD_QUANTITY_SETTER')
        require(len(adjustments) <= budget, 'ADJUSTMENT_LIMIT')

        # The only business mutation in this module. This native API invokes
        # OperationPlan::setQuantityExternal and updates native flow/load plans.
        for plan, _, increment in adjustments:
            receipt['native_mutation_attempted'] = True
            plan.quantity = float(_number(plan.quantity) + increment)

        expected_plans = {ref: dict(row) for ref, row in before_plans.items()}
        increments = {_ref(p): increment for p, _, increment in adjustments}
        for ref, increment in increments.items():
            expected_plans[ref]['quantity'] = _text(_number(expected_plans[ref]['quantity']) + increment)
            owner = plans[ref].owner
            if owner is not None:
                expected_plans[_ref(owner)]['quantity'] = _text(
                    _number(expected_plans[_ref(owner)]['quantity']) + increment)
        require(plan_facts() == expected_plans, 'PLAN_DRIFT')
        # Detect creations/deletions as well as changes to already held wrappers.
        require({_ref(p) for o in frepple.operations() for p in o.operationplans} == set(plans), 'PLAN_DRIFT')
        expected_loads = [dict(row) for row in before_loads]
        target_resources = {_ref(p): model['resource'].name for p, model, _ in adjustments}
        for row in expected_loads:
            if row['reference'] in increments:
                require(row['resource'] == target_resources[row['reference']], 'LOAD_MODEL')
                row['quantity'] = _text(_number(row['quantity']) - increments[row['reference']])
        require(load_facts() == expected_loads, 'LOAD_PROPAGATION')

        after_buffers = scan_buffers()
        require(set(after_buffers) == set(before_buffers), 'FLOW_DRIFT')
        for key, before in before_buffers.items():
            after = after_buffers[key]
            require(after['opening'] == before['opening'] and len(after['events']) == len(before['events']), 'FLOW_DRIFT')
            for previous, actual in zip(before['events'], after['events']):
                require(all(previous[k] == actual[k] for k in ('reference', 'at', 'index', 'opening')), 'FLOW_DRIFT')
                expected_quantity = _number(previous['quantity']) + increments.get(previous['reference'], Decimal(0))
                require(abs(_number(actual['quantity']) - expected_quantity) <= EPSILON, 'FLOW_PROPAGATION')
            require(_number(after['minimum']) >= -EPSILON, 'HARD_SHORTAGE_REMAINS', before['name'])
        # Re-read source terms, capacity calendars and native loads after setter.
        seen_operations.clear(); seen_resources.clear()
        # source_model uses the original receipt evidence; only temporarily
        # advance that reference after actual flows have passed every check.
        before_buffers = after_buffers
        for model in source_models:
            after = source_model(model['source'])
            require(after['calendar'] == model['calendar'], 'CAPACITY_DRIFT')
            require(after['maximum'] == model['maximum'], 'SOURCE_DRIFT')

        before_refresh_plans, before_refresh_loads = plan_facts(), load_facts()
        before_refresh_buffers = scan_buffers()
        feasibility = []
        for ref, plan in sorted(plans.items()):
            before = plan.feasible
            plan.updateFeasible()
            feasibility.append(dict(reference=ref, before=before, after=plan.feasible))
        require(plan_facts() == before_refresh_plans and load_facts() == before_refresh_loads
            and scan_buffers() == before_refresh_buffers, 'REFRESH_BUSINESS_DRIFT')
        receipt['native_feasibility'] = dict(method='OFFICIAL_OPERATIONPLAN_UPDATE_FEASIBLE',
            sequence='AFTER_QUANTITY_FLOW_LOAD_AND_PREFIX_GUARDS_BEFORE_EXISTING_FOUNDATION_REFRESH',
            count=len(feasibility), rows=feasibility,
            false_after=[r['reference'] for r in feasibility if r['after'] is False])
        require(all(r['after'] is True for r in feasibility), 'NATIVE_INFEASIBLE')
        receipt.update(status='REPAIRED_NATIVE', minimum_after=_text(min(
            _number(after_buffers[key]['minimum']) for key in negative)),
            manufacturing_unchanged=True, confirmed_purchase_orders_unchanged=True,
            original_native_events_order_preserved=True)
        return receipt
    except MaterialRepairError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, ArithmeticError) as exc:
        raise MaterialRepairError('UNSUPPORTED_MODEL', receipt, exc) from exc
