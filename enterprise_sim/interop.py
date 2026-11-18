"""Narrow publication transactions on the existing independent enterprise Owner.

All entry points run under LiveEnterprise's existing OS lock/_mutate commit.
Only formal source events own documents; this module retains command receipts,
never another order/inventory/WIP store. Actuals still use the original engine.
"""
import copy
import re
from datetime import timedelta

from .model import SimulationError, dec, amount, text


def require(test, code):
    if not test:
        raise SimulationError(code)


def _identity(owner, command):
    from .live import digest
    require(isinstance(command, dict) and command.get('schema') == 'aps-publication/1', 'PUBLICATION_SCHEMA_INVALID')
    require(isinstance(command.get('command_id'), str) and re.fullmatch(r'[A-Za-z0-9_-]{1,160}', command['command_id']), 'PUBLICATION_COMMAND_ID_INVALID')
    require(command.get('action_type') in {'CREATE_MO', 'RESCHEDULE_MO', 'CONTINUE_MO'}, 'PUBLICATION_ACTION_UNSUPPORTED')
    confirmation = command.get('operator_confirmation', {})
    require(confirmation.get('scope') == 'ISOLATED_TEST_COMPANY_ONLY' and confirmation.get('confirmed') is True and
        all(isinstance(confirmation.get(k), str) and confirmation[k] for k in ['actor_id', 'record_id']), 'PUBLICATION_TEST_SCOPE_REQUIRED')
    payload = copy.deepcopy(command.get('payload', {}))
    require(command.get('enterprise_id') == owner.definition['enterprise_id'] and command.get('branch_id') == owner.branch_id and
        all(command.get(k) and command[k] == payload.get(k) for k in ['enterprise_id', 'branch_id', 'data_version_id', 'plan_id', 'run_id']) and
        command.get('suggestion_id') == payload.get('manufacture_id') and bool(command.get('suggestion_id')), 'PUBLICATION_IDENTITY_MISMATCH')
    require(payload.get('action_type') == 'MANUFACTURE', 'PUBLICATION_ACTION_UNSUPPORTED')
    # The authenticated target explicitly authorizes only this test-company
    # transaction. It never expands the APS research session's original scope.
    payload['operator_confirmation'] = dict(confirmation, role='RESEARCH_SANDBOX_OPERATOR')
    owner._check_adoption(payload)
    publication_key = tuple(command[k] for k in ['data_version_id', 'run_id', 'suggestion_id', 'action_type'])
    for record in owner.current.get('publications', {}).values():
        if tuple(record['identity'][k] for k in ['data_version_id', 'run_id', 'suggestion_id', 'action_type']) == publication_key:
            raise SimulationError('SUGGESTION_ALREADY_PUBLISHED')
    request_hash=digest({k:v for k,v in command.items() if k!='request_hash'})
    require(command.get('request_hash')==request_hash,'PUBLICATION_REQUEST_HASH_MISMATCH')
    return payload, request_hash


def _operations(owner, payload, *, continuation=False):
    from .live import instant
    rows = copy.deepcopy(payload.get('operations', []))
    product = payload.get('product_id')
    require(product in owner.definition['products'], 'PUBLICATION_PRODUCT_UNKNOWN')
    route = owner.definition['products'][product]['routing']
    if not continuation:
        require([r.get('operation_id') for r in rows] == route, 'COMPLETE_ORDERED_ROUTING_REQUIRED')
    require(bool(rows) or continuation, 'COMPLETE_ORDERED_ROUTING_REQUIRED')
    for row in rows:
        spec = owner.definition['operations'].get(row.get('operation_id'))
        require(spec is not None and row['operation_id'] in route, 'OPERATION_NOT_IN_ROUTING')
        q = amount(row.get('quantity', payload['quantity']))
        require(continuation or q == amount(payload['quantity']), 'PUBLICATION_OPERATION_QUANTITY_CONFLICT')
        require(row.get('uom','件')==owner.definition['items'][product]['unit'],'PUBLICATION_OPERATION_UNIT_CONFLICT')
        begin, finish = instant(row['start_at']), instant(row['end_at'])
        minimum = dec(spec.get('fixed_minutes', 0)) + dec(spec.get('minutes_per_piece', 0))*q
        require(begin < finish and finish-begin >= timedelta(minutes=float(minimum)), 'DURATION_BELOW_STANDARD')
        require(begin >= instant(owner.current['as_of']), 'PUBLICATION_PLANNED_START_PASSED')
        calendar = owner.definition['workday']
        require(begin.date() == finish.date() and begin.weekday() in calendar['weekdays'] and
            any(a*60 <= begin.hour*60+begin.minute and finish.hour*60+finish.minute <= b*60 and
                (finish.hour*60+finish.minute < b*60 or not (finish.second or finish.microsecond))
                for a,b in calendar.get('segments', [[calendar['start'], calendar['end']]])), 'NON_WORKING_TIME')
        owner.qualification.validate_assignment(row['operation_id'], row['resource_id'], row['operator_id'], row['start_at'], row['end_at'])
        row.update(quantity=text(q), uom=row.get('uom', '件'))
    if not continuation:
        require(all(instant(a['end_at']) <= instant(b['start_at']) for a,b in zip(rows, rows[1:])), 'NATIVE_PRECEDENCE_VIOLATION')
    return rows


def _editable(owner, payload):
    jid = payload.get('existing_job_id')
    job = owner.world.state['jobs'].get(jid)
    require(job is not None, 'EXTERNAL_DOCUMENT_NOT_FOUND')
    require(type(payload.get('expected_document_version')) is int and payload['expected_document_version'] == job.get('external_version', 1), 'EXTERNAL_DOCUMENT_VERSION_CONFLICT')
    require(job.get('status') in {'DRAFT', 'CONFIRMED'} and not job.get('protected') and not job.get('frozen') and
        not job['issued'] and not job['completed'] and not dec(job['fg_received']) and
        not any(o['job_id'] == jid for o in owner.world.state['operations'].values()), 'EXTERNAL_DOCUMENT_NOT_EDITABLE')
    require(job['order_id'] == payload.get('order_id') and job['product_id'] == payload.get('product_id') and
        dec(job['quantity']) == dec(payload.get('quantity')), 'PUBLICATION_IDENTITY_MISMATCH')
    return job


def _action(owner, payload, jid, command_id):
    from .live import digest, instant
    payload = copy.deepcopy(payload)
    if payload.get('existing_job_id') and not payload.get('continuation'):
        # A confirmed, never-executed formal MO is the same job, not a WIP
        # remainder. Keep the original immutable command in publications.
        payload.pop('existing_job_id')
        payload.pop('expected_document_version',None)
    payload['execution_contract'] = copy.deepcopy(owner.qualification.contract)
    aid = 'PUB-ACTION-' + digest(dict(branch=owner.branch_id, command_id=command_id))[:24]
    action = dict(action_id=aid, payload=payload, adopted_at=owner.current['as_of'], status='ADOPTED',
        actual_event_ids=[], evidence_sha256=digest(payload), execution_contract=copy.deepcopy(owner.qualification.contract),
        job_id=jid, publication_command_id=command_id,
        retry_at=min((r['start_at'] for r in payload.get('operations', [])), key=instant) if payload.get('operations') else
            min(r['start_at'] for r in payload['planned_receipts']))
    owner.current['actions'][aid] = action
    return action


def publish(owner, command):
    from .live import check_manufacture_release, check_continuation_release, digest
    payload, request_hash = _identity(owner, command)
    kind = command['action_type']
    if kind == 'CREATE_MO':
        require(not payload.get('existing_job_id') and not payload.get('continuation'), 'CONTINUATION_JOB_IDENTITY_MISMATCH')
        check_manufacture_release(owner.world.state, owner.current['actions'].values(), payload)
        operations = _operations(owner, payload)
        jid = 'MO-PUB-' + digest(dict(branch=owner.branch_id, command_id=command['command_id']))[:24]
        owner._emit('ManufacturingOrderDraftCreated', dict(job_id=jid, order_id=payload['order_id'], product_id=payload['product_id'],
            quantity=payload['quantity'], unit='件', plan_operations=operations, command_id=command['command_id'],
            external_version=1, protected=False, frozen=False), latencies={s:0 for s in ['ERP','WMS','MES']})
        external = owner.world.state['jobs'][jid]
    elif kind == 'RESCHEDULE_MO':
        external = _editable(owner, payload)
        operations = _operations(owner, payload)
        jid = external['job_id']
        owner._emit('ManufacturingOrderRescheduled', dict(job_id=jid, plan_operations=operations,
            expected_document_version=external.get('external_version', 1), external_version=external.get('external_version', 1)+1,
            command_id=command['command_id']), latencies={s:0 for s in ['ERP','WMS','MES']})
        external = owner.world.state['jobs'][jid]
        for action in list(owner.current['actions'].values()):
            if action.get('job_id') == jid and action.get('publication_command_id') and action['status'] == 'ADOPTED':
                action.update(status='CANCELLED',cancelled_at=owner.current['as_of'],superseded_by_command_id=command['command_id'])
        if external['status']=='CONFIRMED':
            payload['operations']=copy.deepcopy(operations)
            _action(owner,payload,jid,command['command_id'])
    else:
        check_continuation_release(owner.world.state, owner.current['actions'].values(), payload, owner.definition)
        payload['operations'] = _operations(owner, payload, continuation=True)
        jid = payload['existing_job_id']
        external = owner.world.state['jobs'][jid]
        _action(owner, payload, jid, command['command_id'])
    receipt = dict(command_id=command['command_id'], request_hash=request_hash, status='ACCEPTED',state='ACCEPTED',external_system='SIM',
        enterprise_id=owner.definition['enterprise_id'], branch_id=owner.branch_id, external_document_id=jid,
        external_version=external.get('external_version', 1), external_state=external['status'], action_type=kind,
        accepted_at=owner.current['as_of'])
    owner.current.setdefault('publications', {})[command['command_id']] = dict(receipt=copy.deepcopy(receipt),command=copy.deepcopy(command),
        identity={k:command[k] for k in ['data_version_id','run_id','suggestion_id','action_type']}, payload=payload)
    return dict(receipt=receipt)


def confirm(owner, request):
    require(set(request) == {'external_document_id','expected_document_version','confirmed'} and request['confirmed'] is True,
        'EXTERNAL_CONFIRMATION_REQUIRED')
    jid = request['external_document_id']
    job = owner.world.state['jobs'].get(jid)
    require(job is not None, 'EXTERNAL_DOCUMENT_NOT_FOUND')
    require(type(request['expected_document_version']) is int and request['expected_document_version'] == job.get('external_version', 1), 'EXTERNAL_DOCUMENT_VERSION_CONFLICT')
    require(job['status'] == 'DRAFT', 'EXTERNAL_DOCUMENT_NOT_DRAFT')
    plan_command_id=job.get('plan_command_id',job.get('interop_command_id'))
    original = owner.current.get('publications', {}).get(plan_command_id)
    require(original is not None, 'PUBLICATION_EVIDENCE_MISSING')
    payload = copy.deepcopy(original['payload'])
    payload['operations'] = copy.deepcopy(job['plan_operations'])
    payload['quantity'] = job['quantity']
    _operations(owner, payload)
    owner._emit('ManufacturingOrderConfirmed', dict(job_id=jid, expected_document_version=job['external_version'],
        external_version=job['external_version']+1), latencies={s:0 for s in ['ERP','WMS','MES']})
    _action(owner, payload, jid, plan_command_id)
    return dict(external_document_id=jid, external_version=job['external_version']+1, external_state='CONFIRMED')


def receipt(owner, command_id, *, token):
    require(isinstance(command_id, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,160}', command_id), 'PUBLICATION_COMMAND_ID_INVALID')
    owner._authorize(token, 'read')
    with owner._thread_lock:
        owner._reload()
        record = owner.current.get('publications', {}).get(command_id)
        require(record is not None, 'PUBLICATION_COMMAND_NOT_FOUND')
        return copy.deepcopy(record['receipt'])


def withdraw_execution(owner,request):
    """Withdraw an unexecuted plan remainder, never cancel the formal MO.

    The external execution Owner may then report actual segments through its
    existing source-record methods. Active operations must finish first.
    """
    require(set(request)=={'external_document_id','expected_document_version','confirmed','reason'} and
        request['confirmed'] is True and isinstance(request['reason'],str) and bool(request['reason'].strip()),
        'EXTERNAL_CONFIRMATION_REQUIRED')
    jid=request['external_document_id'];job=owner.world.state['jobs'].get(jid)
    require(job is not None,'EXTERNAL_DOCUMENT_NOT_FOUND')
    require(type(request['expected_document_version']) is int and request['expected_document_version']==job.get('external_version'),
        'EXTERNAL_DOCUMENT_VERSION_CONFLICT')
    require(job.get('status') in {'CONFIRMED','WIP'} and not job.get('protected') and not job.get('frozen'),
        'EXTERNAL_EXECUTION_PLAN_NOT_WITHDRAWABLE')
    require(not any(r['job_id']==jid and r['status']=='STARTED' for r in owner.world.state['operations'].values()),
        'INFLIGHT_EXECUTION_PLAN_WITHDRAWAL_FORBIDDEN')
    actions=[a for a in owner.current['actions'].values() if a.get('job_id')==jid and a.get('publication_command_id') and
        a['status'] not in {'COMPLETED','CANCELLED'}]
    require(len(actions)==1,'PUBLISHED_EXECUTION_PLAN_REQUIRED')
    action=actions[0]
    action.update(status='CANCELLED',cancelled_at=owner.current['as_of'],
        cancellation_scope='UNEXECUTED_PLAN_REMAINDER_ONLY',cancellation_reason=request['reason'])
    return dict(external_document_id=jid,external_version=job['external_version'],action_id=action['action_id'],
        scope='UNEXECUTED_PLAN_REMAINDER_ONLY',request=copy.deepcopy(request))
