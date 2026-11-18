"""Prevalidated batch adoption through registered frePPLe setters.

This is NOT a native transaction. All source tuples are resolved before any
mutation; an execution/readback failure must fail and discard the isolated
Worker calculation. No forced dates, setup overrides or feasibility writes.
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation

from p3_fixed_batch.editor import native_time
from p3_fixed_batch.foundation import native_state, refresh_native_feasibility


SOURCE_FIELDS = {'id', 'baseline_run_id', 'native_operation', 'quantity', 'start_at',
                 'end_at', 'resources', 'source_native_reference', 'status'}
MAX_ALIGNMENT_PASSES = 3
TIME_FIELDS = {'start', 'setup_end', 'end', 'setup_duration_seconds'}


def _fail(code, detail=''):
    raise ValueError('OPT_NATIVE_' + code + (':' + detail if detail else ''))


def _time(value, zone):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None or parsed.microsecond:
            _fail('TIME', 'offset-aware whole-second dates required')
        return native_time(value, zone)
    except (TypeError, ValueError, KeyError) as exc:
        _fail('TIME', str(exc))


def _quantity(value, positive=True):
    try:
        quantity = Decimal(str(value))
        if not quantity.is_finite() or positive and quantity <= 0:
            _fail('SOURCE_QUANTITY')
        return quantity
    except (InvalidOperation, ValueError, TypeError):
        _fail('SOURCE_QUANTITY')


def _snapshot(frepple):
    """Capture roots and children through the public operationplans iterator."""
    pending = list(frepple.operationplans())
    plans, states, owners, parents = {}, {}, {}, set()
    while pending:
        p = pending.pop()
        ref = str(p.reference)
        if ref in plans:
            if plans[ref] != p:
                _fail('MODEL_IDENTITY', ref)
            continue
        plans[ref] = p
        children = list(p.operationplans)
        if children:
            parents.add(ref)
            pending.extend(children)
        states[ref] = native_state(frepple, p)
        owners[ref] = str(p.owner.reference) if p.owner else None
    return dict(plans=plans, states=states, owners=owners, parents=parents)


def _differences(expected, actual, keys):
    result = []
    for key in keys:
        if key == 'quantity':
            same = _quantity(expected[key], False) == _quantity(actual[key], False)
        else:
            same = expected[key] == actual[key]
        if not same:
            result.append(key)
    return result


def _check_final(before, after, resolved):
    """Validate the candidate and every unedited object, including protected MO."""
    if set(before['states']) != set(after['states']):
        _fail('MODEL_IDENTITY', 'operationplan set changed')
    selected = {row['actual_reference']: row for row in resolved}
    intent, unchanged = [], []
    for ref, original in before['states'].items():
        actual = after['states'][ref]
        if ref in selected:
            row = selected[ref]
            expected = dict(original, start=row['start'].isoformat(),
                            setup_end=row['setup_end'].isoformat(), end=row['end'].isoformat(),
                            resources=row['expected_resources'])
            # setup duration is computed by the native matrix/calendar. It is
            # observed in the receipt, never overwritten from a wall span.
            fields = set(expected) - {'setup_duration_seconds'}
            changed = _differences(expected, actual, fields)
            if before['owners'][ref] != after['owners'][ref]:
                changed.append('owner')
            if changed:
                intent.append(ref + '=' + ','.join(sorted(changed)))
        else:
            fields = set(original)
            # Proposed routing parents can follow their leaves. Protected
            # parents and every unedited leaf must remain exactly unchanged.
            if ref in before['parents'] and original['status'] == 'proposed':
                fields -= TIME_FIELDS
            changed = _differences(original, actual, fields)
            if before['owners'][ref] != after['owners'][ref]:
                changed.append('owner')
            if changed:
                unchanged.append(ref + '=' + ','.join(sorted(changed)))
    return intent, unchanged


def apply(frepple, spec):
    """Resolve all source tuples, rebind all machines, then align single starts.

    Source native references are tracing information from the baseline run;
    identity is the unique reproduced operation/quantity/window/resources tuple.
    Product/draft signature validation owns the problem and baseline identity.
    """
    if not isinstance(spec, dict) or not isinstance(spec.get('timezone'), str):
        _fail('SPEC')
    machine_ids = spec.get('machine_ids')
    if (not isinstance(machine_ids, list) or not machine_ids or
            any(not isinstance(v, str) or not v for v in machine_ids) or
            len(set(machine_ids)) != len(machine_ids)):
        _fail('SPEC_MACHINE_IDS')
    machine_ids = set(machine_ids)
    hypotheses = spec.get('hypotheses')
    if (not isinstance(hypotheses, list) or len(hypotheses) != 1 or
            not isinstance(hypotheses[0], dict) or
            hypotheses[0].get('kind') != 'optimized_schedule' or
            hypotheses[0].get('reference') != 'optimization'):
        _fail('SPEC_HYPOTHESIS')
    assignments = hypotheses[0].get('assignments')
    if not isinstance(assignments, list) or not assignments:
        _fail('SPEC_ASSIGNMENTS')
    before = _snapshot(frepple)
    # resource(name=...) can create missing input objects; preflight only reads.
    resources = {r.name: r for r in frepple.resources()}
    resolved, used_refs, used_ids, baseline_runs = [], set(), set(), set()
    for assignment in assignments:
        if not isinstance(assignment, dict):
            _fail('SPEC_ASSIGNMENT')
        task = assignment.get('source_task')
        if (not isinstance(task, dict) or set(task) != SOURCE_FIELDS or
                task.get('status') != 'proposed' or
                any(not isinstance(task.get(k), str) or not task[k] for k in
                    ['id', 'baseline_run_id', 'native_operation', 'source_native_reference'])):
            _fail('SOURCE_CONTRACT')
        if task['id'] in used_ids:
            _fail('SOURCE_DUPLICATE', task['id'])
        used_ids.add(task['id'])
        baseline_runs.add(task['baseline_run_id'])
        if len(baseline_runs) != 1:
            _fail('SOURCE_BASELINE_MIXED')
        source_resources = task['resources']
        if (not isinstance(source_resources, list) or
                any(not isinstance(v, str) or not v for v in source_resources) or
                len(set(source_resources)) != len(source_resources) or
                len(machine_ids.intersection(source_resources)) != 1):
            _fail('SOURCE_RESOURCES')
        source_start, source_end = (_time(task[k], spec['timezone']) for k in ['start_at', 'end_at'])
        quantity = _quantity(task['quantity'])
        matches = []
        for ref, p in before['plans'].items():
            row = before['states'][ref]
            if (p.ordertype == 'MO' and ref not in before['parents'] and
                    row['status'] == 'proposed' and row['operation'] == task['native_operation'] and
                    _quantity(row['quantity'], False) == quantity and
                    row['start'] == source_start.isoformat() and row['end'] == source_end.isoformat() and
                    sorted(row['resources']) == sorted(source_resources)):
                matches.append(ref)
        if len(matches) != 1:
            _fail('SOURCE_NOT_REPRODUCED', task['id'])
        ref = matches[0]
        if ref in used_refs:
            _fail('SOURCE_DUPLICATE', task['id'])
        used_refs.add(ref)
        target = assignment.get('resource_id')
        if not isinstance(target, str) or target not in machine_ids or target not in resources:
            _fail('TARGET_RESOURCE', str(target))
        start, setup_end, end = (_time(assignment.get(k), spec['timezone']) for k in
                                 ['start_at', 'setup_end_at', 'end_at'])
        if not start <= setup_end < end:
            _fail('TARGET_DATES', task['id'])
        resolved.append(dict(task=task, plan=before['plans'][ref], actual_reference=ref,
                             target=resources[target], start=start, setup_end=setup_end, end=end,
                             expected_resources=sorted((set(source_resources) - machine_ids) | {target})))

    # All read-only validation and unique object resolution is complete above.
    for row in resolved:
        frepple.loadplan(operationplan=row['plan'], resource=row['target'], action='C')
    ordered = sorted(resolved, key=lambda row: (row['start'], row['actual_reference']))
    for alignment_pass in range(1, MAX_ALIGNMENT_PASSES + 1):
        for row in ordered:
            row['plan'].start = row['start']
        after = _snapshot(frepple)
        intent, unchanged = _check_final(before, after, resolved)
        if not intent and not unchanged:
            break
    if intent:
        _fail('FINAL_INTENT_MISMATCH', ';'.join(intent))
    if unchanged:
        _fail('UNCHANGED_OBJECT_DRIFT', ';'.join(unchanged))

    feasibility = refresh_native_feasibility(frepple)
    if feasibility['false_after']:
        _fail('INFEASIBLE', ','.join(feasibility['false_after']))
    # Refresh is diagnostic, and is not allowed to turn drift into acceptance.
    final = _snapshot(frepple)
    intent, unchanged = _check_final(before, final, resolved)
    if intent:
        _fail('FINAL_INTENT_MISMATCH', ';'.join(intent))
    if unchanged:
        _fail('UNCHANGED_OBJECT_DRIFT', ';'.join(unchanged))
    changes = [dict(reference=row['task']['id'],
                    source_native_reference=row['task']['source_native_reference'],
                    before=before['states'][row['actual_reference']],
                    after=final['states'][row['actual_reference']]) for row in resolved]
    return dict(changes=changes, all_leaf_preserved=True, feasibility=feasibility,
                alignment_passes=alignment_pass, baseline_run_id=next(iter(baseline_runs)),
                atomicity='PREVALIDATED_BATCH_NO_NATIVE_TRANSACTION')
