"""Independent event audit using Decimal ledgers; no simulator imports or final-state oracle."""
import copy
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation

ZERO = Decimal(0)
class _Invalid(Exception):
    pass

def _at(value):
    value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('timezone offset required')
    return value

def _number(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise InvalidOperation()
        return result
    except (InvalidOperation, ValueError, TypeError):
        raise _Invalid('INVALID_QUANTITY')

def _positive(value):
    result = _number(value)
    if result <= ZERO:
        raise _Invalid('NON_POSITIVE_QUANTITY')
    return result

def _fmt(value):
    return format(value.normalize(), 'f')

def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))

def _check(condition, code):
    if not condition:
        raise _Invalid(code)

def _get(mapping, key):
    _check(key in mapping, 'UNKNOWN_IDENTITY')
    return mapping[key]

def _overlap(start, end, other):
    return start < other['end'] and end > other['start']


def _formal_plan(definition, job, operations, occurred_at):
    """Independent planned-document checks, without trusting World/adapter code."""
    route = definition['products'][job['product']]['routing']
    _check([op.get('operation_id') for op in operations] == route, 'FORMAL_MO_ROUTE_INVALID')
    previous = None
    for op in operations:
        spec = _get(definition['operations'], op['operation_id'])
        start, end = _at(op['start_at']), _at(op['end_at'])
        _check(_positive(op['quantity']) == job['quantity'] and op.get('uom') == '件', 'FORMAL_MO_QUANTITY_INVALID')
        _check(op['resource_id'] in spec.get('resource_ids', [spec['resource']]) and op['operator_id'] in definition['operators'], 'UNQUALIFIED_ASSIGNMENT')
        _check(occurred_at <= start < end and (previous is None or previous <= start), 'FORMAL_MO_INTERVAL_INVALID')
        previous = end
        minutes = _number(spec.get('fixed_minutes', 0)) + _number(spec.get('minutes_per_piece', 0))*job['quantity']
        _check(_number(str((end-start).total_seconds())) >= minutes*60, 'DURATION_BELOW_STANDARD')
        _check(spec.get('batch_quantity') is None or _positive(spec['batch_quantity'])==job['quantity'],'INVALID_BATCH_SIZE')
        cal = definition['workday']
        start_seconds = start.hour*3600 + start.minute*60 + start.second + start.microsecond/1e6
        end_seconds = end.hour*3600 + end.minute*60 + end.second + end.microsecond/1e6
        _check(start.date() == end.date() and start.weekday() in cal['weekdays'] and
            any(a*3600 <= start_seconds < end_seconds <= b*3600 for a,b in cal.get('segments', [[cal['start'],cal['end']]])), 'NON_WORKING_TIME')

def validate_events(definition, events, assignment_validator=None, *, include_ledger=False):
    """Audit the supplied occurrence-ordered stream without applying it to World.

    Invalid events are reported and excluded from the audit ledger so later valid
    events cannot legitimize an earlier violation. PASS does not mean all orders
    are completed: a partial but consistent history is a valid stream.
    """
    issues, seen = [], {}
    b = {'orders': {}, 'pos': {}, 'lots': {}, 'receipts': {}, 'jobs': {}, 'ops': {}, 'blocks': []}
    duplicates = accepted = 0
    accepted_rows = [] if include_ledger else None
    previous = None
    for index, event in enumerate(events):
        event_id = event.get('event_id')
        signature = _canonical(event)
        if event_id in seen:
            if signature != seen[event_id]:
                issues.append({'code': 'EVENT_ID_CONFLICT', 'event_id': event_id, 'index': index})
            else:
                duplicates += 1
            continue
        seen[event_id] = signature
        before = copy.deepcopy(b)
        try:
            _check(bool(event_id), 'MISSING_EVENT_ID')
            at = _at(event['occurred_at'])
            _check(previous is None or at >= previous, 'EVENT_TIME_REVERSED')
            previous = at
            known = event.get('known_at', {})
            _check(bool(known) and all(_at(t) >= at for t in known.values()), 'INVALID_KNOWN_TIME')
            p, kind = event['payload'], event['kind']
            for owner in (event, p):
                if 'enterprise_id' in owner:
                    _check(owner['enterprise_id'] == definition['enterprise_id'], 'CROSS_SYSTEM_IDENTITY')
            if kind != 'GoodsReceived' and p.get('lot_id') in b['lots']:
                _check(p.get('item_id', b['lots'][p['lot_id']]['item']) == b['lots'][p['lot_id']]['item'], 'CROSS_SYSTEM_IDENTITY')
            if kind != 'ManufacturingTaskCreated' and p.get('job_id') in b['jobs']:
                linked = b['jobs'][p['job_id']]
                _check(p.get('product_id', linked['product']) == linked['product'] and p.get('order_id', linked['order']) == linked['order'], 'CROSS_SYSTEM_IDENTITY')
            if 'item_id' in p:
                item = _get(definition['items'], p['item_id'])
                _check(p.get('unit', item['unit']) == item['unit'], 'UNIT_MISMATCH')
            if kind in ('SalesOrderCreated', 'SalesOrderChanged', 'ManufacturingTaskCreated','ManufacturingOrderDraftCreated', 'OperationCompleted', 'FinishedGoodsReceived', 'ShipmentPosted'):
                product_id = p.get('product_id')
                if product_id is None and p.get('job_id') in b['jobs']:
                    product_id = b['jobs'][p['job_id']]['product']
                if product_id is None and p.get('order_id') in b['orders']:
                    product_id = b['orders'][p['order_id']]['product']
                if product_id is not None:
                    product_unit = _get(definition['items'], product_id)['unit']
                    _check(p.get('unit', product_unit) == product_unit, 'UNIT_MISMATCH')
            q = _positive(p['quantity']) if 'quantity' in p else None
            if kind == 'SalesOrderCreated':
                _get(definition['products'], p['product_id'])
                _check(p['order_id'] not in b['orders'], 'DUPLICATE_BUSINESS_ID')
                b['orders'][p['order_id']] = {'product': p['product_id'], 'quantity': q, 'shipped': ZERO}
            elif kind == 'SalesOrderChanged':
                row = _get(b['orders'], p['order_id'])
                if q is not None:
                    _check(q >= row['shipped'], 'ORDER_BELOW_SHIPPED')
                    _check(q == row['quantity'] or not any(j['order'] == p['order_id'] for j in b['jobs'].values()), 'ORDER_CHANGE_AFTER_RELEASE')
                    row['quantity'] = q
            elif kind == 'PurchaseOrderCreated':
                _check(p['po_id'] not in b['pos'], 'DUPLICATE_BUSINESS_ID')
                _get(definition['items'], p['item_id'])
                b['pos'][p['po_id']] = {'item': p['item_id'], 'quantity': q, 'received': ZERO}
            elif kind == 'PurchaseETAChanged':
                _get(b['pos'], p['po_id'])
                _at(p['eta'])
            elif kind == 'GoodsReceived':
                po = _get(b['pos'], p['po_id'])
                _check(po['item'] == p['item_id'], 'CROSS_SYSTEM_IDENTITY')
                _check(p['receipt_id'] not in b['receipts'] and p['lot_id'] not in b['lots'], 'DUPLICATE_RECEIPT')
                _check(po['received'] + q <= po['quantity'], 'RECEIPT_EXCEEDS_PO')
                po['received'] += q
                b['receipts'][p['receipt_id']] = p['lot_id']
                b['lots'][p['lot_id']] = {'item': p['item_id'], 'received': q, 'pending': q, 'available': ZERO, 'held': ZERO, 'consumed': ZERO}
            elif kind in ('QCReleased', 'QCHeld'):
                lot = _get(b['lots'], p['lot_id'])
                _check(p.get('unit', definition['items'][lot['item']]['unit']) == definition['items'][lot['item']]['unit'], 'UNIT_MISMATCH')
                origin, dest = p.get('from_status', 'pending'), 'available' if kind == 'QCReleased' else 'held'
                _check(origin in ('pending', 'held', 'available') and origin != dest, 'INVALID_QC_TRANSITION')
                _check(q <= lot[origin], 'QC_EXCEEDS_BALANCE')
                lot[origin] -= q
                lot[dest] += q
            elif kind in ('ManufacturingTaskCreated','ManufacturingOrderDraftCreated'):
                order = _get(b['orders'], p['order_id'])
                _get(definition['products'], p['product_id'])
                _check(order['product'] == p['product_id'], 'CROSS_SYSTEM_IDENTITY')
                _check(p['job_id'] not in b['jobs'], 'DUPLICATE_BUSINESS_ID')
                assigned = sum((j['quantity'] for j in b['jobs'].values() if j['order'] == p['order_id']), ZERO)
                _check(assigned + q <= order['quantity'], 'JOB_ORDER_MISMATCH')
                b['jobs'][p['job_id']] = {'order': p['order_id'], 'product': p['product_id'], 'quantity': q, 'issued': {}, 'completed': {}, 'fg': ZERO, 'shipped': ZERO}
                if kind == 'ManufacturingOrderDraftCreated':
                    job=b['jobs'][p['job_id']]
                    _check(type(p.get('external_version')) is int and p['external_version'] == 1 and bool(p.get('command_id')), 'FORMAL_MO_SOURCE_INVALID')
                    _formal_plan(definition,job,p['plan_operations'],at)
                    job.update(formal_state='DRAFT',external_version=1,protected=p.get('protected',False),frozen=p.get('frozen',False))
            elif kind in ('ManufacturingOrderConfirmed','ManufacturingOrderRescheduled'):
                job=_get(b['jobs'],p['job_id'])
                _check(type(p.get('expected_document_version')) is int and type(p.get('external_version')) is int and p['expected_document_version'] == job.get('external_version',1) and
                    p.get('external_version') == job.get('external_version',1)+1,'EXTERNAL_DOCUMENT_VERSION_CONFLICT')
                _check(job.get('formal_state') in ('DRAFT','CONFIRMED') and not job['issued'] and not job['completed'] and
                    not job.get('protected') and not job.get('frozen') and
                    not any(key[0] == p['job_id'] for key in b['ops']), 'EXTERNAL_DOCUMENT_NOT_EDITABLE')
                if kind == 'ManufacturingOrderConfirmed':
                    _check(job['formal_state'] == 'DRAFT','EXTERNAL_DOCUMENT_NOT_DRAFT')
                    job['formal_state']='CONFIRMED'
                else:
                    _formal_plan(definition,job,p['plan_operations'],at)
                job['external_version']=p['external_version']
            elif kind == 'MaterialIssued':
                job, lot = _get(b['jobs'], p['job_id']), _get(b['lots'], p['lot_id'])
                _check(job.get('formal_state') != 'DRAFT','MANUFACTURING_ORDER_NOT_CONFIRMED')
                item = lot['item']
                _check(p.get('item_id', item) == item, 'CROSS_SYSTEM_IDENTITY')
                _check(p.get('unit', definition['items'][item]['unit']) == definition['items'][item]['unit'], 'UNIT_MISMATCH')
                bom = definition['products'][job['product']]['bom']
                _check(item in bom, 'MATERIAL_NOT_IN_BOM')
                _check(q <= lot['available'], 'INSUFFICIENT_RELEASED')
                _check(job['issued'].get(item, ZERO) + q <= _number(bom[item]) * job['quantity'], 'ISSUE_EXCEEDS_BOM')
                job['issued'][item] = job['issued'].get(item, ZERO) + q
                lot['available'] -= q
                lot['consumed'] += q
            elif kind == 'ResourceUnavailable':
                _check(p['resource_id'] in definition['resources'] or p['resource_id'] in definition['operators'], 'UNKNOWN_IDENTITY')
                start, end = _at(p['start']), _at(p['end'])
                _check(start < end, 'INVALID_INTERVAL')
                _check(not any(p['resource_id'] in (o['machine'], o['person']) and _overlap(start, end, o) for o in b['ops'].values()), 'UNAVAILABILITY_OVERLAPS_EXECUTION')
                b['blocks'].append({'id': p['resource_id'], 'start': start, 'end': end})
            elif kind == 'OperationStarted':
                job = _get(b['jobs'], p['job_id'])
                _check(job.get('formal_state') != 'DRAFT','MANUFACTURING_ORDER_NOT_CONFIRMED')
                route = definition['products'][job['product']]['routing']
                op = _get(definition['operations'], p['operation_id'])
                _get(definition['resources'], p['resource_id'])
                _get(definition['operators'], p['operator_id'])
                _check(p['operation_id'] in route, 'OPERATION_NOT_IN_ROUTING')
                _check(p['resource_id'] in op.get('resource_ids',[op['resource']]), 'UNQUALIFIED_ASSIGNMENT')
                _check(op['operator'] == p['operator_id'] if assignment_validator is None else assignment_validator(definition, event) is True, 'UNQUALIFIED_ASSIGNMENT')
                work_quantity = q if q is not None else job['quantity']
                segmented = 'segment_id' in p
                if segmented:
                    import re
                    _check(work_quantity == work_quantity.to_integral_value(), 'INTEGER_OPERATION_SEGMENT_REQUIRED')
                    _check(isinstance(p['segment_id'], str) and re.fullmatch(r'[A-Za-z0-9_-]{1,200}', p['segment_id']) is not None, 'INVALID_OPERATION_SEGMENT_ID')
                    _check(op.get('batch_quantity') is not None or (_number(op.get('fixed_minutes', '0')) == ZERO and _number(op.get('minutes_per_piece', '0')) > ZERO), 'CONTINUOUS_SEGMENT_MODEL_REQUIRED')
                _check(work_quantity == job['quantity'] or segmented, 'OPERATION_SEGMENT_ID_REQUIRED')
                completed = job['completed'].get(p['operation_id'], ZERO)
                _check(completed + work_quantity <= job['quantity'], 'OPERATION_QUANTITY_EXCEEDS_REMAINING')
                _check(all(job['completed'].get(prior, ZERO) >= completed + work_quantity for prior in route[:route.index(p['operation_id'])]), 'PREDECESSOR_NOT_COMPLETED')
                _check(all(job['issued'].get(item, ZERO) >= _number(n) * job['quantity'] for item, n in definition['products'][job['product']]['bom'].items()), 'MATERIAL_NOT_ISSUED')
                end, cal = _at(p['end']), definition['workday']
                _check(at < end and at.date() == end.date() and at.weekday() in cal['weekdays'] and at.hour + at.minute/60 >= cal['start'] and end.hour + end.minute/60 + end.second/3600 <= cal['end'], 'NON_WORKING_TIME')
                seconds_in_day = lambda value: Decimal(value.hour * 3600 + value.minute * 60 + value.second) + Decimal(value.microsecond) / 1000000
                segments = cal.get('segments', [[cal['start'], cal['end']]])
                _check(any(seconds_in_day(at) >= _number(low) * 3600 and seconds_in_day(end) <= _number(high) * 3600 for low, high in segments), 'NON_WORKING_TIME')
                fixed = _number(op.get('fixed_minutes', '0'))
                batch = op.get('batch_quantity')
                if batch is not None:
                    _check(work_quantity == job['quantity'] == _positive(batch), 'FIXED_BATCH_MISMATCH')
                required_minutes = fixed + _number(op.get('minutes_per_piece', '0')) * work_quantity
                _check(_number(str((end-at).total_seconds())) >= required_minutes * 60, 'DURATION_BELOW_STANDARD')
                overlap_codes = []
                if any(o['machine'] == p['resource_id'] and _overlap(at, end, o) for o in b['ops'].values()):
                    overlap_codes.append('RESOURCE_OVERLAP')
                if any(o['person'] == p['operator_id'] and _overlap(at, end, o) for o in b['ops'].values()):
                    overlap_codes.append('PERSON_OVERLAP')
                if overlap_codes:
                    raise _Invalid('|'.join(overlap_codes))
                _check(not any(x['id'] in (p['resource_id'], p['operator_id']) and _overlap(at, end, x) for x in b['blocks']), 'RESOURCE_UNAVAILABLE')
                key = (p['job_id'], p['operation_id']) + ((p['segment_id'],) if 'segment_id' in p else ())
                _check(key not in b['ops'], 'OPERATION_ALREADY_STARTED')
                _check(not any(key[:2] == old_key[:2] and not old['complete'] for old_key, old in b['ops'].items()), 'OPERATION_ALREADY_STARTED')
                b['ops'][key] = {'machine': p['resource_id'], 'person': p['operator_id'], 'start': at, 'end': end, 'quantity': work_quantity, 'complete': False}
            elif kind == 'OperationInterrupted':
                job = _get(b['jobs'], p['job_id'])
                op = _get(b['ops'], (p['job_id'], p['operation_id']) + ((p['segment_id'],) if 'segment_id' in p else ()))
                spec = _get(definition['operations'], p['operation_id'])
                _check(not spec.get('batch_quantity') and _number(spec.get('fixed_minutes',0)) == ZERO and _number(spec.get('minutes_per_piece',0)) > ZERO, 'CONTINUOUS_INTERRUPTION_REQUIRED')
                actual = _number(p['actual_quantity'])
                _check(not op['complete'] and ZERO <= actual < op['quantity'] and actual == actual.to_integral_value() and bool(p.get('reason')), 'INVALID_INTERRUPTION')
                _check(op['start'] <= at <= op['end'] and _number(str((at-op['start']).total_seconds())) >= _number(spec['minutes_per_piece'])*actual*60, 'INTERRUPTION_DURATION_BELOW_STANDARD')
                op['complete'], op['end'], op['actual_quantity'] = True, at, actual
                job['completed'][p['operation_id']] = job['completed'].get(p['operation_id'], ZERO) + actual
            elif kind == 'OperationCompleted':
                job = _get(b['jobs'], p['job_id'])
                op = _get(b['ops'], (p['job_id'], p['operation_id']) + ((p['segment_id'],) if 'segment_id' in p else ()))
                _check(not op['complete'] and at >= op['end'] and q == op['quantity'], 'INVALID_COMPLETION')
                # A late completion occupies the resource until its actual completion.
                _check(not any(other is not op and (other['machine'] == op['machine'] or other['person'] == op['person']) and _overlap(op['start'], at, other) for other in b['ops'].values()), 'RESOURCE_OVERLAP')
                op['complete'], op['end'] = True, at
                job['completed'][p['operation_id']] = job['completed'].get(p['operation_id'], ZERO) + q
            elif kind == 'FinishedGoodsReceived':
                job = _get(b['jobs'], p['job_id'])
                route = definition['products'][job['product']]['routing']
                _check(bool(route) and all(job['completed'].get(op, ZERO) >= job['fg'] + q for op in route), 'NOT_COMPLETED')
                job['fg'] += q
            elif kind == 'ShipmentPosted':
                job, order = _get(b['jobs'], p['job_id']), _get(b['orders'], p['order_id'])
                _check(job['order'] == p['order_id'], 'CROSS_SYSTEM_IDENTITY')
                _check(job['shipped'] + q <= job['fg'] and order['shipped'] + q <= order['quantity'], 'SHIPMENT_EXCEEDS_AVAILABLE')
                job['shipped'] += q
                order['shipped'] += q
            else:
                raise _Invalid('UNSUPPORTED_EVENT')
            for po in b['pos'].values():
                _check(ZERO <= po['received'] <= po['quantity'], 'PO_CONSERVATION')
            for lot in b['lots'].values():
                _check(all(lot[k] >= ZERO for k in ('pending', 'available', 'held', 'consumed')) and lot['received'] == sum((lot[k] for k in ('pending', 'available', 'held', 'consumed')), ZERO), 'LOT_CONSERVATION')
            accepted += 1
            if include_ledger:
                accepted_rows.append(copy.deepcopy(event))
        except _Invalid as exc:
            b = before
            for code in str(exc).split('|'):
                issues.append({'code': code, 'event_id': event_id, 'index': index})
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            b = before
            issues.append({'code': 'INVALID_EVENT_SCHEMA', 'event_id': event_id, 'index': index, 'detail': str(exc)})
    received, consumed = {}, {}
    for lot in b['lots'].values():
        item = lot['item']
        received[item] = received.get(item, ZERO) + lot['received']
        consumed[item] = consumed.get(item, ZERO) + lot['consumed']
    metrics = {'unique_event_ids': len(seen), 'accepted_events': accepted, 'duplicate_messages': duplicates, 'lot_received': _fmt(sum(received.values(), ZERO)), 'material_consumed': _fmt(sum(consumed.values(), ZERO)), 'po_outstanding': _fmt(sum((r['quantity']-r['received'] for r in b['pos'].values()), ZERO)), 'shipped': _fmt(sum((r['shipped'] for r in b['orders'].values()), ZERO)), 'received_by_item': {k: _fmt(v) for k,v in received.items()}, 'consumed_by_item': {k: _fmt(v) for k,v in consumed.items()}, 'totals_scope': 'Arithmetic audit counters only; use per-item figures for mixed-unit business quantities'}
    result = {'status': 'FAIL' if issues else 'PASS', 'issues': issues, 'metrics': metrics}
    if include_ledger:
        # Opt-in in-memory audit projection. The default historical JSON receipt
        # remains byte-compatible; no World/simulator state is used as an oracle.
        result['ledger'] = b
        result['ledger']['accepted_events'] = accepted_rows
    return result


def validate_visibility(events, source_package, as_of):
    """Check every referenced event against occurrence and per-system knowledge cutoffs."""
    issues = []
    try:
        cutoff = _at(as_of)
        system = source_package['system']
        if system not in ('ERP', 'MES', 'WMS'):
            return [{'code': 'INVALID_SOURCE_SYSTEM'}]
        package_cutoff = _at(source_package['as_of'])
        if package_cutoff > cutoff:
            issues.append({'code': 'FUTURE_PACKAGE_AS_OF'})
        cutoff = min(cutoff, package_cutoff)
        lookup = {}
        for event in events:
            key = event['event_id']
            if key in lookup and _canonical(event) != _canonical(lookup[key]):
                issues.append({'code': 'EVENT_ID_CONFLICT', 'event_id': key})
            lookup[key] = event
        for unresolved in source_package.get('unresolved_events', []):
            key = unresolved['event_id']
            where = {'event_id': key, 'section': 'unresolved_events'}
            if key not in lookup:
                issues.append({'code': 'UNKNOWN_EVENT_REFERENCE', **where})
                continue
            event = lookup[key]
            known = event.get('known_at', {}).get(system)
            if _at(event['occurred_at']) > cutoff or _at(unresolved['business_occurred_at']) > cutoff:
                issues.append({'code': 'FUTURE_EVENT_LEAK', **where})
            if known is None:
                issues.append({'code': 'SYSTEM_KNOWLEDGE_UNKNOWN', **where})
            elif _at(known) > cutoff or _at(unresolved['source_known_at']) > cutoff:
                issues.append({'code': 'FUTURE_KNOWLEDGE_LEAK', **where})
            if unresolved.get('kind') != event['kind'] or unresolved.get('payload') != event['payload'] or unresolved.get('business_occurred_at') != event['occurred_at'] or unresolved.get('source_known_at') != known:
                issues.append({'code': 'UNRESOLVED_EVENT_MISMATCH', **where})
        tables, refs = source_package['tables'], source_package.get('event_refs', {})
        for table, records in tables.items():
            for record in records:
                if record['id'] not in refs.get(table, {}):
                    issues.append({'code': 'MISSING_EVENT_REFERENCE', 'table': table, 'record_id': record['id']})
        for table, rows in refs.items():
            record_ids = {r['id'] for r in tables.get(table, [])}
            for record_id, ids in rows.items():
                if record_id not in record_ids:
                    issues.append({'code': 'ORPHAN_EVENT_REFERENCE', 'table': table, 'record_id': record_id})
                if not ids:
                    issues.append({'code': 'MISSING_EVENT_REFERENCE', 'table': table, 'record_id': record_id})
                for key in ids:
                    where = {'event_id': key, 'table': table, 'record_id': record_id}
                    if key not in lookup:
                        issues.append({'code': 'UNKNOWN_EVENT_REFERENCE', **where})
                        continue
                    event = lookup[key]
                    if _at(event['occurred_at']) > cutoff:
                        issues.append({'code': 'FUTURE_EVENT_LEAK', **where})
                    known = event.get('known_at', {}).get(system)
                    if known is None:
                        issues.append({'code': 'SYSTEM_KNOWLEDGE_UNKNOWN', **where})
                    elif _at(known) > cutoff:
                        issues.append({'code': 'FUTURE_KNOWLEDGE_LEAK', **where})
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        issues.append({'code': 'INVALID_SOURCE_PACKAGE', 'detail': str(exc)})
    return issues


def _rows_by_id(rows):
    if isinstance(rows, dict):
        return rows
    return {r.get('id', r.get('order_id')): r for r in rows}


def validate_knowledge(source_orders, aps_orders):
    """Snapshot absence cannot justify cancellation; an explicit source status can."""
    source, aps = _rows_by_id(source_orders), _rows_by_id(aps_orders)
    issues = []
    cancelled = {'CANCELLED', 'CANCELED'}
    for key, row in aps.items():
        if str(row.get('status', '')).upper() in cancelled and (key not in source or str(source[key].get('status', '')).upper() not in cancelled):
            issues.append({'code': 'ABSENCE_FALSE_CANCELLATION' if key not in source else 'UNSUPPORTED_CANCELLATION', 'order_id': key})
    return issues


def validate_execution(expected_events, actual_events):
    """Compare exact source events, including timestamps, identities and quantities."""
    issues = []
    def index(rows, side):
        result = {}
        for event in rows:
            key = event['event_id']
            if key in result and _canonical(event) != _canonical(result[key]):
                issues.append({'code': 'EVENT_ID_CONFLICT', 'event_id': key, 'side': side})
            result[key] = event
        return result
    expected, actual = index(expected_events, 'expected'), index(actual_events, 'actual')
    for key in expected.keys() - actual.keys():
        issues.append({'code': 'EXECUTION_MISSING_EVENT', 'event_id': key})
    for key in actual.keys() - expected.keys():
        issues.append({'code': 'EXECUTION_UNEXPECTED_EVENT', 'event_id': key})
    for key in expected.keys() & actual.keys():
        if _canonical(expected[key]) != _canonical(actual[key]):
            issues.append({'code': 'EXECUTION_EVENT_MISMATCH', 'event_id': key})
    if list(expected) != list(actual) and expected.keys() == actual.keys():
        issues.append({'code': 'EXECUTION_ORDER_MISMATCH'})
    return issues


def validate_source_values(definition, events, source_package):
    """Recalculate exported business fields from this system's known events only.

    This is a grouped Decimal audit, not a call into the simulator or projection
    generator. Full snapshots require every expected record. Incremental packages
    compare only supplied upserts; absence remains NO_CHANGE.
    """
    issues = []
    try:
        system, cutoff = source_package['system'], _at(source_package['as_of'])
        if system not in ('ERP', 'WMS', 'MES'):
            return [{'code': 'INVALID_SOURCE_SYSTEM'}]
        if source_package.get('enterprise_id', definition['enterprise_id']) != definition['enterprise_id']:
            issues.append({'code': 'CROSS_SYSTEM_IDENTITY'})
        visible, seen = [], {}
        for index, event in enumerate(events):
            key = event['event_id']
            if key in seen:
                if seen[key] != _canonical(event):
                    issues.append({'code': 'EVENT_ID_CONFLICT', 'event_id': key})
                continue
            seen[key] = _canonical(event)
            known = event.get('known_at', {}).get(system)
            if known is not None and _at(known) <= cutoff and _at(event['occurred_at']) <= cutoff:
                visible.append((index, event))
        visible.sort(key=lambda pair: (_at(pair[1]['occurred_at']), pair[1].get('sequence', pair[0])))
        rows = [event for index, event in visible]
        # Determine unresolved dependencies from known events and prior identities,
        # never from the source package's own assertion that an event is unresolved.
        identities = {name: set() for name in ('purchase_orders', 'sales_orders', 'lots', 'finished_goods', 'manufacturing_tasks', 'operations')}
        projected, expected_unresolved = [], {}
        for event in rows:
            payload, kind = event['payload'], event['kind']
            dependencies = []
            if system == 'ERP':
                if kind in ('PurchaseETAChanged', 'GoodsReceived'):
                    dependencies.append(('purchase_orders', payload.get('po_id')))
                if kind in ('SalesOrderChanged', 'ShipmentPosted'):
                    dependencies.append(('sales_orders', payload.get('order_id')))
            elif system == 'WMS':
                if kind in ('QCReleased', 'QCHeld', 'MaterialIssued'):
                    dependencies.append(('lots', payload.get('lot_id')))
                if kind == 'ShipmentPosted':
                    dependencies.append(('finished_goods', payload.get('job_id')))
            else:
                if kind in ('MaterialIssued', 'OperationStarted', 'OperationCompleted', 'OperationInterrupted', 'FinishedGoodsReceived',
                            'ManufacturingOrderConfirmed','ManufacturingOrderRescheduled'):
                    dependencies.append(('manufacturing_tasks', payload.get('job_id')))
                if kind in ('OperationCompleted','OperationInterrupted'):
                    dependencies.append(('operations', payload['job_id'] + '::' + payload['operation_id'] +
                        ('::SEG:'+payload['segment_id'] if 'segment_id' in payload else '')))
            missing = [table + ':' + str(key) for table, key in dependencies if key not in identities[table]]
            if missing:
                expected_unresolved[event['event_id']] = {'event_id': event['event_id'], 'kind': kind, 'missing_dependency': missing[-1], 'business_occurred_at': event['occurred_at'], 'source_known_at': event['known_at'][system], 'payload': payload}
                continue
            projected.append(event)
            if system == 'ERP' and kind == 'PurchaseOrderCreated':
                identities['purchase_orders'].add(payload['po_id'])
            elif system == 'ERP' and kind == 'SalesOrderCreated':
                identities['sales_orders'].add(payload['order_id'])
            elif system == 'WMS' and kind == 'GoodsReceived':
                identities['lots'].add(payload['lot_id'])
            elif system == 'WMS' and kind == 'FinishedGoodsReceived':
                identities['finished_goods'].add(payload['job_id'])
            elif system == 'MES' and kind in ('ManufacturingTaskCreated','ManufacturingOrderDraftCreated'):
                identities['manufacturing_tasks'].add(payload['job_id'])
            elif system == 'MES' and kind == 'OperationStarted':
                identities['operations'].add(payload['job_id'] + '::' + payload['operation_id'] +
                    ('::SEG:'+payload['segment_id'] if 'segment_id' in payload else ''))
        declared_unresolved = {}
        for unresolved in source_package.get('unresolved_events', []):
            key = unresolved['event_id']
            if key in declared_unresolved:
                issues.append({'code': 'DUPLICATE_UNRESOLVED_EVENT', 'event_id': key})
            declared_unresolved[key] = unresolved
            if key not in expected_unresolved:
                issues.append({'code': 'UNRESOLVED_EVENT_UNEXPECTED', 'event_id': key})
            elif _canonical(unresolved) != _canonical(expected_unresolved[key]):
                issues.append({'code': 'UNRESOLVED_EVENT_MISMATCH', 'event_id': key})
        for key in expected_unresolved.keys() - declared_unresolved.keys():
            issues.append({'code': 'UNRESOLVED_EVENT_MISSING', 'event_id': key, 'missing_dependency': expected_unresolved[key]['missing_dependency']})
        rows = projected
        by_kind = {}
        order_index = {e['event_id']: n for n, e in enumerate(rows)}
        for event in rows:
            by_kind.setdefault(event['kind'], []).append(event)
        def selected(kinds, field, value):
            return [e for e in rows if e['kind'] in kinds and e['payload'].get(field) == value]
        expected, references = {}, {}
        def put(table, key, value, causes):
            causes = sorted(causes, key=lambda e: order_index[e['event_id']])
            value = {'id': key, **value}
            # Core business fields are mandatory; metadata is checked if exported.
            if causes:
                value['_metadata'] = {'business_occurred_at': causes[-1]['occurred_at'], 'source_known_at': max((e['known_at'][system] for e in causes), key=_at), 'aps_read_at': source_package['as_of']}
            for name in ('item_id', 'product_id'):
                if name in value:
                    value.setdefault('_metadata', {})['source_' + name] = system + '-' + value[name]
            expected.setdefault(table, {})[key] = value
            references.setdefault(table, {})[key] = {e['event_id'] for e in causes}
        def quantity(events_for_row):
            return sum((_number(e['payload']['quantity']) for e in events_for_row), ZERO)
        def event_rows(table, kinds, key_field=None, include_kind=False, extra=None):
            for kind in kinds:
                for event in by_kind.get(kind, []):
                    payload = event['payload']
                    value = dict(payload)
                    if include_kind:
                        value['kind'] = kind
                    if extra:
                        value.update(extra(event))
                    put(table, payload[key_field] if key_field else event['event_id'], value, [event])
        if system == 'ERP':
            for created in by_kind.get('PurchaseOrderCreated', []):
                payload = created['payload']; key = payload['po_id']
                receipts = selected(('GoodsReceived',), 'po_id', key)
                changes = selected(('PurchaseETAChanged',), 'po_id', key)
                value = dict(payload)
                if changes:
                    value['eta'] = changes[-1]['payload']['eta']
                received = quantity(receipts); outstanding = _number(payload['quantity']) - received
                value.update(received=_fmt(received), outstanding=_fmt(outstanding), status='OPEN' if not receipts else 'RECEIVED' if outstanding == ZERO else 'PARTIAL')
                put('purchase_orders', key, value, [created] + receipts + changes)
            for created in by_kind.get('SalesOrderCreated', []):
                payload = created['payload']; key = payload['order_id']
                changes = selected(('SalesOrderChanged',), 'order_id', key)
                shipments = selected(('ShipmentPosted',), 'order_id', key)
                value = dict(payload)
                for change in changes:
                    value.update(change['payload'])
                shipped = quantity(shipments)
                value.update(shipped=_fmt(shipped), status='OPEN' if not shipments else 'SHIPPED' if shipped == _number(value['quantity']) else 'PARTIAL')
                put('sales_orders', key, value, [created] + changes + shipments)
            event_rows('purchase_receipts', ('GoodsReceived',), 'receipt_id')
            event_rows('shipments', ('ShipmentPosted',))
        elif system == 'WMS':
            for receipt in by_kind.get('GoodsReceived', []):
                payload = receipt['payload']; key = payload['lot_id']
                qc = selected(('QCReleased', 'QCHeld'), 'lot_id', key)
                movements = selected(('MaterialIssued',), 'lot_id', key)
                received = _number(payload['quantity'])
                balances = {'pending': received, 'available': ZERO, 'held': ZERO}
                for event in qc:
                    origin = event['payload'].get('from_status', 'pending')
                    destination = 'available' if event['kind'] == 'QCReleased' else 'held'
                    q = _number(event['payload']['quantity'])
                    balances[origin] -= q; balances[destination] += q
                consumed = quantity(movements)
                balances['available'] -= consumed
                put('lots', key, {**payload, **{k: _fmt(v) for k,v in balances.items()}, 'physical': _fmt(received-consumed), 'consumed': _fmt(consumed)}, [receipt] + qc + movements)
            for job_id in {e['payload']['job_id'] for kind in ('FinishedGoodsReceived', 'ShipmentPosted') for e in by_kind.get(kind, [])}:
                incoming = selected(('FinishedGoodsReceived',), 'job_id', job_id)
                outgoing = selected(('ShipmentPosted',), 'job_id', job_id)
                put('finished_goods', job_id, {'physical': _fmt(quantity(incoming)-quantity(outgoing))}, incoming + outgoing)
            event_rows('receipts', ('GoodsReceived',), 'receipt_id')
            event_rows('quality_events', ('QCReleased', 'QCHeld'), include_kind=True)
            event_rows('movements', ('MaterialIssued', 'FinishedGoodsReceived', 'ShipmentPosted'), include_kind=True)
        else:
            for created in by_kind.get('ManufacturingTaskCreated', []) + by_kind.get('ManufacturingOrderDraftCreated', []):
                payload = created['payload']; key = payload['job_id']
                started = selected(('OperationStarted',), 'job_id', key)
                finished = selected(('FinishedGoodsReceived',), 'job_id', key)
                changes = sorted(started + finished, key=lambda e: order_index[e['event_id']])
                formal = selected(('ManufacturingOrderConfirmed','ManufacturingOrderRescheduled'), 'job_id', key)
                value=copy.deepcopy(payload)
                status='DRAFT' if created['kind']=='ManufacturingOrderDraftCreated' else 'RELEASED'
                for event in formal:
                    value['external_version']=event['payload']['external_version']
                    if event['kind']=='ManufacturingOrderConfirmed':status='CONFIRMED'
                    else:value['plan_operations']=copy.deepcopy(event['payload']['plan_operations'])
                if changes:status='COMPLETED' if changes[-1]['kind']=='FinishedGoodsReceived' and quantity(finished)==_number(payload['quantity']) else 'WIP'
                put('manufacturing_tasks',key,{**value,'status':status,'completed_quantity':_fmt(quantity(finished))},[created]+formal+changes)
            for started in by_kind.get('OperationStarted', []):
                payload = started['payload']; key = payload['job_id'] + '::' + payload['operation_id']
                if 'segment_id' in payload:
                    key += '::SEG:' + payload['segment_id']
                ended = [e for e in by_kind.get('OperationCompleted', []) if e['payload']['job_id'] == payload['job_id'] and e['payload']['operation_id'] == payload['operation_id'] and e['payload'].get('segment_id') == payload.get('segment_id')]
                value = {**payload, 'start': started['occurred_at'], 'status': 'STARTED'}
                if ended:
                    value.update(ended[-1]['payload']); value.update(actual_end=ended[-1]['occurred_at'], status='COMPLETED')
                interrupted = [e for e in by_kind.get('OperationInterrupted', []) if e['payload']['job_id'] == payload['job_id'] and e['payload']['operation_id'] == payload['operation_id'] and e['payload'].get('segment_id') == payload.get('segment_id')]
                if interrupted:
                    last=interrupted[-1]
                    value.update(actual_quantity=last['payload']['actual_quantity'],actual_end=last['occurred_at'],
                                 interruption_reason=last['payload']['reason'],status='INTERRUPTED')
                put('operations', key, value, [started] + ended + interrupted)
            event_rows('material_issues', ('MaterialIssued',))
            event_rows('resource_unavailability', ('ResourceUnavailable',), extra=lambda e: {'announced_at': e['occurred_at']})
        numeric = {'quantity', 'actual_quantity', 'received', 'outstanding', 'shipped', 'pending', 'available', 'held', 'consumed', 'physical', 'completed_quantity', 'priority'}
        actual = {}
        for table, records in source_package['tables'].items():
            actual[table] = {}
            for record in records:
                key = record['id']
                if key in actual[table]:
                    issues.append({'code': 'DUPLICATE_SOURCE_RECORD', 'table': table, 'record_id': key})
                actual[table][key] = record
                if key not in expected.get(table, {}):
                    issues.append({'code': 'SOURCE_RECORD_NOT_KNOWN', 'table': table, 'record_id': key})
                    continue
                target = expected[table][key]
                for field, value in {**{k:v for k,v in target.items() if k != '_metadata'}, **{k:v for k,v in target.get('_metadata', {}).items() if k in record}}.items():
                    supplied = record.get(field)
                    try:
                        equal = field in record and (_number(supplied) == _number(value) if field in numeric else supplied == value)
                    except _Invalid:
                        equal = False
                    if not equal:
                        issues.append({'code': 'SOURCE_VALUE_MISMATCH', 'table': table, 'record_id': key, 'field': field, 'expected': value, 'actual': supplied})
                if set(source_package.get('event_refs', {}).get(table, {}).get(key, [])) != references[table][key]:
                    issues.append({'code': 'SOURCE_REFERENCE_MISMATCH', 'table': table, 'record_id': key})
        if source_package.get('mode', 'FULL_SNAPSHOT') == 'FULL_SNAPSHOT':
            for table, records in expected.items():
                for key in records.keys() - actual.get(table, {}).keys():
                    issues.append({'code': 'SOURCE_RECORD_MISSING', 'table': table, 'record_id': key})
    except (_Invalid, KeyError, ValueError, TypeError, AttributeError) as exc:
        issues.append({'code': 'INVALID_SOURCE_VALUES_INPUT', 'detail': str(exc)})
    return issues
