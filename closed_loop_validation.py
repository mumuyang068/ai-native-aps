"""Independent read-only closed-loop audit over sealed, supplied documents.

No filesystem, HTTP, simulator state transition, source projection or APS solver
is invoked. ``enterprise_sim.validation`` supplies the original independent
Decimal event and source audits. Callers obtain APS records from their owners,
not from action payload assertions. Missing independent evidence stays unknown.
"""
import copy
import hashlib
import json
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from enterprise_sim.validation import (
    validate_events, validate_knowledge, validate_source_values, validate_visibility,
)


DIMENSIONS = ('inherited_prefix', 'event_integrity', 'source_identity', 'source_visibility',
    'source_values', 'dynamic_discovery', 'adoption_authorization', 'native_plan_lineage',
    'actual_execution', 'order_fulfillment', 'aps_feedback', 'version_continuity', 'plan_actual_comparison')
EXECUTION_KINDS = {'ManufacturingTaskCreated', 'MaterialIssued', 'OperationStarted',
    'OperationCompleted', 'OperationInterrupted', 'FinishedGoodsReceived', 'ShipmentPosted'}
FORMAL_KINDS={'ManufacturingOrderDraftCreated','ManufacturingOrderConfirmed','ManufacturingOrderRescheduled'}
OPERATION_FIELDS = ('operation_id', 'resource_id', 'operator_id', 'start_at', 'end_at')
LIVE_QUALIFICATION_POLICY = {
    'schema': 'live-execution-qualification/1', 'policy_id': 'LIVE_ORIGINAL_QUALIFICATIONS_V1',
    'machine_policy': 'FIXED_DECLARED_MACHINE',
    'operator_policy': 'EXACT_NATIVE_OPERATOR_WITH_ORIGINAL_SKILL_AND_FULL_INTERVAL_VALIDITY',
    'qualification_time_policy': 'ORIGINAL_REFERENCE_EFFECTIVE_INTERVAL_NO_EXTENSION',
    'calendar_policy': 'DECLARED_CONT_WORKDAY_AND_ACTUAL_BLOCKS',
}
ORIGINAL_DAY_GENERATOR_SHA256 = 'ef81da8bc77c9f82c696c30c056bfde7a97ce2c6b1668c2656f619913d32d615'
LEGACY_GENERATOR_AUTHORITY = {
    'SalesOrderCreated': 'ERP', 'SalesOrderChanged': 'ERP', 'PurchaseOrderCreated': 'ERP',
    'PurchaseETAChanged': 'ERP', 'GoodsReceived': 'WMS', 'QCReleased': 'WMS',
    'QCHeld': 'WMS', 'ResourceUnavailable': 'MES',
}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def at(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.utcoffset() is None:
        raise ValueError('TIMEZONE_REQUIRED')
    return result


def number(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('FINITE_QUANTITY_REQUIRED')
    return result


def text(value):
    return format(number(value).normalize(), 'f')


def _rows(value):
    return {row['id']: row for row in value}


def _tables_equal(left, right):
    def normalized(tables):
        return {table: {row['id']: {k: v for k, v in row.items() if k != 'aps_read_at'}
            for row in rows} for table, rows in tables.items() if rows}
    return normalized(left) == normalized(right)


def _same_operation(candidate, requested):
    return all(candidate.get(k) == requested.get(k) for k in OPERATION_FIELDS[:3]) and all(
        at(candidate[k]) == at(requested[k]) for k in OPERATION_FIELDS[3:])


def _is_legacy_generator_event(event):
    return ('branch_id' not in event and not event.get('adoption_id') and
        event.get('kind') in LEGACY_GENERATOR_AUTHORITY and
        event.get('research_id') == 'APS-SIM-ENTERPRISE-01' and
        isinstance(event.get('event_id'), str) and event['event_id'].startswith('CONT-'))


def verify_closed_loop(*, audit, source_views, inherited_prefix=None, aps_evidence=None, order_ids=None):
    """Audit current branch facts, immutable source views and independent APS receipts.

    ``audit`` is LiveEnterprise.export_audit's current-prefix export. ``source_views``
    contains read_view responses including adoption-basis and post-execution views.
    ``inherited_prefix`` comes from the separately sealed legal checkpoint.

    ``aps_evidence`` uses lists named ``versions``, ``syncs``, ``runs``, ``adoptions``
    and ``operator_records``. Versions are ImportStore documents. Sync rows bind
    version_id, branch_id, source_revision, as_of, status and configuration_fingerprint.
    Run rows bind run_id, plan_id, data_version_id, input_hash, state, native_audit
    and extracted native operations. Adoption mappings bind action_id,
    payload_sha256, operator_record_id and plan/run/data ids. Operator records bind
    the trusted confirmation's fields and payload_sha256. These are supplied
    independently by the caller; this function certifies their consistency, not
    the real-world identity of an enterprise approver.

    A derived live qualification policy requires export fields
    ``execution_contract`` and the complete original public ``execution_reference``.
    Its reference and definition digests are independently rebuilt here. The
    callback supplied to the original independent Decimal audit validates actual
    operator identities and original skill validity; original history without
    this versioned contract retains the original fixed-operator rule. The CONT
    declared workday, actual resource blocks and actual occupancy remain the
    calendar authority; inherited enterprise calendars are not extended.

    Old day-local generator events lacking an embedded branch may be attributed
    only through ``branch_provenance``: the pinned generator identity, exact
    legacy event allowlist, and full original immutable commits from zero to the
    exported head. Each original commit digest, parent, event prefix, control,
    clock and head is recomputed. Successful compatibility remains explicitly
    PASS_WITH_LIMITS; event payloads are never rewritten or implicitly tagged.
    """
    dimensions = {name: dict(status='PASS', issues=[], missing_evidence=[]) for name in DIMENSIONS}
    result = dict(schema='independent-closed-loop-audit/1', status='NOT_CERTIFIED', dimensions=dimensions,
        orders=[], actions=[], plan_actual=[], scope='SEALED_CURRENT_BRANCH_AND_SUPPLIED_APS_RECORDS',
        limits=['Research sandbox Operator identity is not real enterprise approval.',
            'Provided APS records must be obtained independently from their owning services.',
            'Matching actual and planned timestamps alone neither proves nor disproves real execution.'])

    def issue(dimension, code, **detail):
        dimensions[dimension]['issues'].append(dict(code=code, **detail))

    def missing(dimension, code, **detail):
        row = dict(code=code, **detail)
        if row not in dimensions[dimension]['missing_evidence']:
            dimensions[dimension]['missing_evidence'].append(row)

    def check(ok, dimension, code, **detail):
        if not ok:
            issue(dimension, code, **detail)

    def guard(dimension, callback):
        try:
            callback()
        except (KeyError, TypeError, ValueError, AttributeError, ArithmeticError) as exc:
            issue(dimension, 'INVALID_EVIDENCE_SCHEMA', detail=str(exc))

    if not isinstance(audit, dict) or not all(key in audit for key in
            ('definition', 'branch_id', 'as_of', 'revision', 'events', 'state', 'actions')):
        issue('event_integrity', 'CURRENT_BRANCH_EXPORT_REQUIRED')
        return _finish(result)
    definition, events, branch = audit['definition'], audit['events'], audit['branch_id']
    enterprise = definition.get('enterprise_id')
    result.update(branch_id=branch, enterprise_id=enterprise, revision=audit['revision'], as_of=audit['as_of'])
    prefix_count = len(inherited_prefix) if inherited_prefix is not None else audit.get('parent_checkpoint', {}).get('event_count', 0)
    aps = aps_evidence or {}
    unique_events = list({event.get('event_id'): event for event in events}.values())
    actions = {action['action_id']: action for action in audit['actions']}
    event_by_action = defaultdict(list)
    jobs, orders, issued, receipts, lot_qc, lot_issues, fg, shipped, starts, completes = {}, {}, defaultdict(Decimal), defaultdict(Decimal), defaultdict(lambda: defaultdict(Decimal)), defaultdict(Decimal), defaultdict(Decimal), defaultdict(Decimal), {}, {}
    execution_contract = audit.get('execution_contract')
    execution_reference = audit.get('execution_reference')
    qualified_actions = {action_id for action_id, action in actions.items() if
        action.get('execution_contract') or action.get('payload', {}).get('execution_contract') or
        any(e.get('adoption_id') == action_id and e.get('execution_contract_sha256') for e in unique_events)}
    qualification_ready = False
    qualification_tables = {}
    research_grants = audit.get('research_qualification_declarations')
    actual_completions = {(e['payload'].get('job_id'), e['payload'].get('operation_id'), e['payload'].get('segment_id')): e['occurred_at']
        for e in unique_events if e.get('kind') in ('OperationCompleted','OperationInterrupted')}

    def qualification_contract_audit():
        nonlocal qualification_ready, qualification_tables
        if not execution_contract:
            if qualified_actions:
                missing('event_integrity', 'LIVE_EXECUTION_CONTRACT_EXPORT_REQUIRED')
            return
        dimensions['event_integrity']['qualification_policy_id'] = execution_contract.get('policy_id')
        if not execution_reference:
            missing('event_integrity', 'ORIGINAL_QUALIFICATION_REFERENCE_REQUIRED')
            return
        qualification_tables = execution_reference['tables']
        reference_match = execution_contract.get('reference_sha256') == digest(execution_reference)
        reference_match = reference_match and execution_contract.get('reference_roles_sha256') == digest(
            {k: qualification_tables.get(k, []) for k in ['items','operations','skills','resource_skills','machine_qualifications']})
        definition_match = execution_contract.get('definition_sha256') == digest(definition)
        expected_policy=dict(LIVE_QUALIFICATION_POLICY)
        if any(len(o.get('resource_ids',[]))>1 for o in definition['operations'].values()):
            expected_policy['machine_policy']='EXPLICIT_DECLARED_ALTERNATE_MACHINES'
        policy_match = all(execution_contract.get(k) == value for k, value in expected_policy.items())
        if research_grants:
            declared=research_grants.get('year_policy',{})
            annual_expected={**expected_policy,'policy_id':declared.get('id'),
                'operator_policy':'EXPLICIT_NAMED_SYNTHETIC_RESEARCH_ROLES',
                'qualification_time_policy':'EXPLICIT_SYNTHETIC_RESEARCH_INTERVALS'}
            roles={k:research_grants.get(k) for k in ['research_qualifications','research_machine_qualifications']}
            policy_match=(declared.get('schema')=='year-operating-policy/1' and declared.get('id')=='APS-SIM-YEAR-01-POLICY-V1' and
                all(execution_contract.get(k)==v for k,v in annual_expected.items()) and
                execution_contract.get('research_roles_sha256')==digest(roles) and
                execution_contract.get('operating_policy_sha256')==digest(declared))
        check(reference_match, 'actual_execution', 'LIVE_EXECUTION_CONTRACT_REFERENCE_MISMATCH')
        check(definition_match, 'actual_execution', 'LIVE_EXECUTION_CONTRACT_DEFINITION_MISMATCH')
        check(policy_match, 'actual_execution', 'UNSUPPORTED_LIVE_EXECUTION_CONTRACT')
        qualification_ready = reference_match and definition_match and policy_match

    guard('event_integrity', qualification_contract_audit)

    def qualified_assignment(operation_id, resource_id, operator_id, start, end, **where):
        """Check supplied reference facts without importing the runtime policy."""
        valid = True
        def require(ok, code):
            nonlocal valid
            if not ok:
                valid = False
                issue('actual_execution', code, operation_id=operation_id, operator_id=operator_id, **where)
        original = next((row for row in qualification_tables.get('operations', []) if row['id'] == operation_id), None)
        people = {row['id'] for row in qualification_tables.get('operators', [])}
        require(operator_id in people and operator_id in definition['operators'], 'LIVE_OPERATOR_IDENTITY_NOT_IN_REFERENCE')
        require(original is not None, 'LIVE_OPERATION_NOT_IN_REFERENCE')
        if original is None:
            return False
        skill = original.get('required_operator_skill')
        require(skill is not None and skill in {row['id'] for row in qualification_tables.get('skills', [])},
            'LIVE_ORIGINAL_SKILL_DECLARATION_REQUIRED')
        spec = definition['operations'][operation_id]
        require(resource_id in spec.get('resource_ids',[spec['resource']]) and resource_id in original.get('resource_ids', []),
            'LIVE_MACHINE_NOT_DECLARED_FOR_OPERATION')
        people_grants=research_grants['research_qualifications'] if research_grants else qualification_tables.get('resource_skills', [])
        require(start < end and any(row.get('resource_id') == operator_id and row.get('skill_id') == skill and
            at(row['effective_from']) <= start and end <= at(row['effective_to'])
            for row in people_grants), 'LIVE_OPERATOR_SKILL_NOT_VALID_FOR_FULL_INTERVAL')
        if research_grants:
            require(any(r.get('resource_id')==resource_id and r.get('operation_id')==operation_id and
                at(r['effective_from'])<=start and end<=at(r['effective_to']) for r in research_grants['research_machine_qualifications']),
                'LIVE_MACHINE_QUALIFICATION_NOT_VALID_FOR_FULL_INTERVAL')
        elif len(spec.get('resource_ids',[]))>1:
            require(any(r.get('resource_id')==resource_id and r.get('skill_id')==skill and
                at(r['effective_from'])<=start and end<=at(r['effective_to']) for r in qualification_tables.get('machine_qualifications',[])),
                'LIVE_MACHINE_QUALIFICATION_NOT_VALID_FOR_FULL_INTERVAL')
        return valid

    def actual_assignment_validator(checked_definition, event):
        p = event['payload']; operation = checked_definition['operations'][p['operation_id']]
        source_record = event.get('source_execution_record')
        if source_record and event['event_id'] not in source_bindings:
            return False
        if not source_record and event.get('adoption_id') not in qualified_actions:
            # Restored history keeps the original fixed dispatch policy even in
            # an export that also contains later explicitly qualified actions.
            return p['operator_id'] == operation['operator']
        action = actions.get(event.get('adoption_id'))
        if not source_record:
            if not action:
                return False
            if event.get('execution_contract_sha256') != digest(execution_contract):
                return False
        start = at(event['occurred_at'])
        end = at(actual_completions.get((p['job_id'], p['operation_id'], p.get('segment_id')), p['end']))
        valid = qualified_assignment(p['operation_id'], p['resource_id'], p['operator_id'], start, end, event_id=event['event_id'])
        zone = ZoneInfo(definition.get('timezone', 'Asia/Shanghai'))
        local_start, local_end = start.astimezone(zone), end.astimezone(zone)
        cal = definition['workday']
        seconds = lambda value: value.hour * 3600 + value.minute * 60 + value.second + value.microsecond / 1000000
        calendar_ok = local_start < local_end and local_start.date() == local_end.date() and local_start.weekday() in cal['weekdays'] and any(
            seconds(local_start) >= number(low) * 3600 and seconds(local_end) <= number(high) * 3600
            for low, high in cal.get('segments', [[cal['start'], cal['end']]]))
        check(calendar_ok, 'actual_execution', 'LIVE_ACTUAL_CALENDAR_VIOLATION', event_id=event['event_id'])
        for block in unique_events:
            if block['kind'] != 'ResourceUnavailable':
                continue
            unavailable = block['payload']
            if unavailable['resource_id'] in (p['resource_id'], p['operator_id']) and start < at(unavailable['end']) and end > at(unavailable['start']):
                issue('actual_execution', 'LIVE_ACTUAL_RESOURCE_UNAVAILABLE', event_id=event['event_id'], block_event_id=block['event_id'])
                valid = False
        requested = next((row for row in action['payload'].get('operations', []) if row['operation_id'] == p['operation_id']), None) if action else None
        assignment_matches = bool(source_record) or (requested is not None and all(requested.get(k) == p.get(k) for k in OPERATION_FIELDS[:3]))
        check(assignment_matches, 'actual_execution', 'LIVE_ACTUAL_ASSIGNMENT_DIFFERS_FROM_ADOPTION', event_id=event['event_id'])
        return valid and calendar_ok and assignment_matches

    def prefix_audit():
        if inherited_prefix is None:
            missing('inherited_prefix', 'INDEPENDENT_CHECKPOINT_PREFIX_REQUIRED')
            return
        check(events[:prefix_count] == inherited_prefix, 'inherited_prefix', 'INHERITED_PREFIX_CHANGED')
        parent = audit.get('parent_checkpoint')
        if not parent:
            missing('inherited_prefix', 'PARENT_CHECKPOINT_RECEIPT_REQUIRED')
            return
        check(parent.get('event_count') == prefix_count and parent.get('events_sha256') == digest(inherited_prefix),
            'inherited_prefix', 'PARENT_CHECKPOINT_PREFIX_MISMATCH')
        check(all(at(event['occurred_at']) <= at(parent['as_of']) for event in inherited_prefix),
            'inherited_prefix', 'CHECKPOINT_PREFIX_HAS_FUTURE_EVENT')

    guard('inherited_prefix', prefix_audit)
    legacy_bindings = {}
    source_bindings = {}
    publication_bindings = {}

    def branch_provenance_audit():
        proof = audit.get('branch_provenance')
        if proof is None:
            return
        valid = True
        def require(ok, code, **detail):
            nonlocal valid
            if not ok:
                valid = False
                issue('event_integrity', code, **detail)
        require(proof.get('schema') == 'live-branch-commit-provenance/1' and proof.get('branch_id') == branch,
            'BRANCH_PROVENANCE_IDENTITY_MISMATCH')
        generator = proof.get('generator', {})
        require(generator.get('origin') == 'ORIGINAL_DAY_LOCAL_GENERATOR' and
            generator.get('source_sha256') == ORIGINAL_DAY_GENERATOR_SHA256, 'BRANCH_GENERATOR_SOURCE_NOT_PINNED')
        commits = proof['commits']
        require(isinstance(commits, list) and len(commits) == audit['revision'] + 1,
            'BRANCH_COMMIT_REVISION_GAP')
        if not commits:
            return
        first_appearances = {}
        previous = None
        for index, commit in enumerate(commits):
            revision = commit['revision']
            require(isinstance(revision, int) and not isinstance(revision, bool) and revision == index,
                'BRANCH_COMMIT_REVISION_GAP', commit_revision=revision)
            require(commit.get('sha256') == digest({k: v for k, v in commit.items() if k != 'sha256'}),
                'BRANCH_COMMIT_HASH_MISMATCH', commit_revision=revision)
            require(commit.get('parent_sha256') == (previous['sha256'] if previous else None),
                'BRANCH_COMMIT_PARENT_MISMATCH', commit_revision=revision)
            require(all(at(event['occurred_at']) <= at(commit['as_of']) for event in commit['events']),
                'BRANCH_COMMIT_CONTAINS_FUTURE_EVENT', commit_revision=revision)
            require(len({event['event_id'] for event in commit['events']}) == len(commit['events']),
                'BRANCH_COMMIT_DUPLICATE_EVENT_ID', commit_revision=revision)
            if previous is None:
                require(commit['events'] == events[:prefix_count] and commit['as_of'] == audit['parent_checkpoint']['as_of'],
                    'BRANCH_COMMIT_CHECKPOINT_PREFIX_MISMATCH')
                require(not commit['controls'], 'BRANCH_COMMIT_CONTROL_CHAIN_MISMATCH', commit_revision=revision)
            else:
                require(at(previous['as_of']) < at(commit['as_of']), 'BRANCH_COMMIT_CLOCK_REGRESSION', commit_revision=revision)
                require(commit['events'][:len(previous['events'])] == previous['events'],
                    'BRANCH_COMMIT_EVENT_PREFIX_CHANGED', commit_revision=revision)
                require(len(commit['controls']) == len(previous['controls']) + 1 and
                    commit['controls'][:len(previous['controls'])] == previous['controls'],
                    'BRANCH_COMMIT_CONTROL_CHAIN_MISMATCH', commit_revision=revision)
                if 'bootstrap_sha256' in previous:
                    require(commit.get('bootstrap_sha256') == previous['bootstrap_sha256'],
                        'BRANCH_COMMIT_BOOTSTRAP_CHANGED', commit_revision=revision)
            for event in commit['events']:
                first_appearances.setdefault(event['event_id'], commit)
            previous = commit
        head = commits[-1]
        require(proof.get('head_revision') == audit['revision'] == head['revision'] and
            proof.get('head_commit_sha256') == audit.get('commit_sha256') == head['sha256'] and
            head['events'] == events and head['as_of'] == audit['as_of'], 'BRANCH_PROVENANCE_HEAD_MISMATCH')
        declared = proof.get('legacy_event_ids', [])
        candidates = {event['event_id'] for event in events[prefix_count:] if _is_legacy_generator_event(event)}
        require(isinstance(declared, list) and len(declared) == len(set(declared)) and set(declared) == candidates,
            'LEGACY_PROVENANCE_EVENT_SET_MISMATCH')
        by_id = {event['event_id']: event for event in events[prefix_count:]}
        pending_bindings = {}
        for event_id in declared:
            event = by_id.get(event_id)
            require(event is not None and _is_legacy_generator_event(event),
                'LEGACY_BRANCH_EXCEPTION_NOT_ALLOWED', event_id=event_id)
            if event is None:
                continue
            commit = first_appearances[event_id]
            control = commit.get('controls', [])[-1] if commit.get('controls') else None
            require(commit['revision'] > 0 and control is not None and control.get('kind') == 'ADVANCE' and
                control.get('at') == commit['as_of'], 'LEGACY_EVENT_NOT_FROM_ADVANCE_COMMIT', event_id=event_id)
            pending_bindings[event_id] = dict(event_id=event_id, branch_id=branch, event_sha256=digest(event),
                first_committed_revision=commit['revision'], first_commit_sha256=commit['sha256'],
                head_commit_sha256=head['sha256'], generator_source_sha256=generator.get('source_sha256'),
                attribution='ORIGINAL_UNCHANGED_EVENT_BOUND_BY_VERIFIED_APPEND_ONLY_COMMIT_CHAIN')
        pending_sources = {}
        for event in events[prefix_count:]:
            record = event.get('source_execution_record')
            if not record:
                continue
            event_id = event['event_id']; commit = first_appearances[event_id]
            control = commit['controls'][-1] if commit.get('controls') else {}
            payload = record.get('payload', {}); kind = record.get('kind')
            receipt = commit.get('requests', {}).get(record.get('record_id'), {}).get('response', {})
            confirmation = payload.get('confirmation', {})
            require(record.get('schema') == 'research-execution-source-record/1' and
                kind in ('RECORD_EXECUTION_SEGMENT','RECORD_EXECUTION_START','RECORD_OPERATION_INTERRUPTION','RECORD_FINISHED_GOODS_RECEIPT','RECORD_SHIPMENT') and
                not event.get('adoption_id') and not event.get('run_id') and event.get('branch_id') == branch and
                record.get('payload_sha256') == digest(payload) and
                all(control.get(k) == value for k, value in {'kind':kind,'request_id':record.get('record_id'),'payload_sha256':digest(payload)}.items()) and
                control.get('at') == commit['as_of'] and receipt.get('source_record') == record and event_id in receipt.get('event_ids', []) and
                confirmation.get('confirmed') is True and confirmation.get('role') == 'RESEARCH_SANDBOX_OPERATOR' and
                all(isinstance(confirmation.get(k), str) and confirmation[k].strip() for k in ('actor_id','basis')),
                'SOURCE_EXECUTION_CONTROL_MISMATCH', event_id=event_id)
            requested = {key:payload.get(key) for key in ('job_id','quantity')}
            when = payload.get('received_at')
            if kind in ('RECORD_EXECUTION_SEGMENT','RECORD_EXECUTION_START'):
                requested.update({key:payload.get(key) for key in ('operation_id','segment_id')})
                if requested.get('segment_id') is None:requested.pop('segment_id')
                when = payload.get('end_at')
                require(event['kind'] in ('OperationStarted','OperationCompleted'), 'SOURCE_EXECUTION_KIND_MISMATCH', event_id=event_id)
                if kind=='RECORD_EXECUTION_START':require(event['kind']=='OperationStarted','SOURCE_EXECUTION_KIND_MISMATCH',event_id=event_id)
                if event['kind'] == 'OperationStarted':
                    requested.update({key:payload.get(key) for key in ('resource_id','operator_id')})
                    requested['end'] = payload.get('end_at'); when = payload.get('start_at')
                spec = definition['operations'].get(payload.get('operation_id'), {})
                if kind=='RECORD_EXECUTION_SEGMENT':
                    require(not spec.get('batch_quantity') and number(spec.get('fixed_minutes',0)) == 0 and
                        number(spec.get('minutes_per_piece',0)) > 0, 'SOURCE_CONTINUOUS_SEGMENT_MODEL_REQUIRED', event_id=event_id)
            elif kind=='RECORD_OPERATION_INTERRUPTION':
                requested={key:payload.get(key) for key in ('job_id','operation_id','actual_quantity','reason')}
                if payload.get('segment_id') is not None:requested['segment_id']=payload['segment_id']
                when=payload.get('interrupted_at')
                require(event['kind']=='OperationInterrupted','SOURCE_EXECUTION_KIND_MISMATCH',event_id=event_id)
            elif kind=='RECORD_SHIPMENT':
                when=payload.get('shipped_at')
                roots=[e for e in events if e['kind'] in {'ManufacturingTaskCreated','ManufacturingOrderDraftCreated'} and
                    e['payload'].get('job_id')==payload.get('job_id')]
                require(len(roots)==1,'SOURCE_SHIPMENT_JOB_IDENTITY_MISMATCH',event_id=event_id)
                requested['order_id']=roots[0]['payload'].get('order_id') if len(roots)==1 else None
                require(event['kind']=='ShipmentPosted','SOURCE_EXECUTION_KIND_MISMATCH',event_id=event_id)
            else:
                require(event['kind'] == 'FinishedGoodsReceived','SOURCE_EXECUTION_KIND_MISMATCH',event_id=event_id)
            require(event['payload'] == requested and event['occurred_at'] == when and
                all(value == event['occurred_at'] for value in event.get('known_at',{}).values()),
                'SOURCE_EXECUTION_RECORD_EVENT_MISMATCH',event_id=event_id)
            pending_sources[event_id] = dict(event_id=event_id,record_id=record.get('record_id'),kind=kind,
                first_committed_revision=commit['revision'],first_commit_sha256=commit['sha256'],source_fact=True,native_adoption=False)
        pending_publications={}
        formal_confirmations={}
        for commit in commits[1:]:
            control=commit['controls'][-1]
            if control.get('kind')=='PUBLICATION':
                cid=control.get('request_id');record=commit.get('publications',{}).get(cid,{})
                command=record.get('command',{});receipt=record.get('receipt',{});confirmation=command.get('operator_confirmation',{})
                stored=commit.get('requests',{}).get(cid,{})
                require(command.get('schema')=='aps-publication/1' and command.get('command_id')==cid and
                    command.get('branch_id')==branch and command.get('enterprise_id')==enterprise and
                    command.get('request_hash')==digest({k:v for k,v in command.items() if k!='request_hash'}) and
                    control.get('payload_sha256')==digest(command) and
                    stored.get('signature')==digest(dict(kind='PUBLICATION',payload=command,expected_revision=commit['revision']-1)) and
                    stored.get('response',{}).get('receipt')==receipt and receipt.get('command_id')==cid and
                    receipt.get('request_hash')==command.get('request_hash') and receipt.get('status')=='ACCEPTED' and
                    confirmation.get('scope')=='ISOLATED_TEST_COMPANY_ONLY' and confirmation.get('confirmed') is True and
                    all(confirmation.get(k) for k in ('actor_id','record_id')),
                    'PUBLICATION_TARGET_TRANSACTION_MISMATCH',command_id=cid)
                pending_publications[cid]=dict(command=command,receipt=receipt,commit_revision=commit['revision'])
            elif control.get('kind')=='CONFIRM_MO':
                stored=commit.get('requests',{}).get(control.get('request_id'),{})
                response=stored.get('response',{})
                request=dict(external_document_id=response.get('external_document_id'),
                    expected_document_version=response.get('external_version',0)-1,confirmed=True)
                require(response.get('external_state')=='CONFIRMED' and control.get('payload_sha256')==digest(request) and
                    stored.get('signature')==digest(dict(kind='CONFIRM_MO',payload=request,expected_revision=commit['revision']-1)),
                    'PUBLICATION_CONFIRM_TRANSACTION_MISMATCH',commit_revision=commit['revision'])
                formal_confirmations[commit['revision']]=response
            elif control.get('kind')=='WITHDRAW_PUBLISHED_EXECUTION':
                stored=commit.get('requests',{}).get(control.get('request_id'),{})
                response=stored.get('response',{});request=response.get('request',{})
                prior=commits[commit['revision']-1].get('actions',{}).get(response.get('action_id'),{})
                action=commit.get('actions',{}).get(response.get('action_id'),{})
                require(response.get('scope')=='UNEXECUTED_PLAN_REMAINDER_ONLY' and request.get('confirmed') is True and
                    bool(request.get('reason')) and request.get('external_document_id')==action.get('job_id') and
                    bool(action.get('publication_command_id')) and prior.get('status') not in (None,'COMPLETED','CANCELLED') and
                    action.get('status')=='CANCELLED' and action.get('cancellation_scope')==response['scope'] and
                    action.get('payload')==prior.get('payload') and action.get('evidence_sha256')==prior.get('evidence_sha256') and
                    control.get('payload_sha256')==digest(request) and stored.get('signature')==digest(dict(
                        kind='WITHDRAW_PUBLISHED_EXECUTION',payload=request,expected_revision=commit['revision']-1)),
                    'PUBLICATION_EXECUTION_WITHDRAWAL_MISMATCH',commit_revision=commit['revision'])
        for event in events[prefix_count:]:
            if event['kind'] not in FORMAL_KINDS:continue
            commit=first_appearances[event['event_id']];p=event['payload']
            if event['kind']=='ManufacturingOrderConfirmed':
                response=formal_confirmations.get(commit['revision'],{})
                require(response.get('external_document_id')==p.get('job_id') and
                    response.get('external_version')==p.get('external_version'),
                    'FORMAL_MO_CONFIRM_EVENT_UNAUTHORIZED',event_id=event['event_id'])
            else:
                binding=pending_publications.get(p.get('command_id'),{})
                command=binding.get('command',{});receipt=binding.get('receipt',{});wanted=command.get('payload',{})
                require(binding.get('commit_revision')==commit['revision'] and receipt.get('external_document_id')==p.get('job_id') and
                    receipt.get('external_version')==p.get('external_version') and p.get('plan_operations')==wanted.get('operations') and
                    command.get('action_type')==('CREATE_MO' if event['kind']=='ManufacturingOrderDraftCreated' else 'RESCHEDULE_MO'),
                    'FORMAL_MO_PUBLICATION_EVENT_MISMATCH',event_id=event['event_id'])
                if event['kind']=='ManufacturingOrderDraftCreated':
                    require(all(p.get(k)==wanted.get(k) for k in ('order_id','product_id','quantity')) and
                        receipt.get('external_state')=='DRAFT','FORMAL_MO_DRAFT_EVENT_MISMATCH',event_id=event['event_id'])
        if valid:
            legacy_bindings.update(pending_bindings)
            source_bindings.update(pending_sources)
            publication_bindings.update(pending_publications)
            if pending_publications:
                dimensions['event_integrity']['publication_commands']=[dict(command_id=cid,
                    external_document_id=b['receipt']['external_document_id'],external_state=b['receipt']['external_state'],
                    commit_revision=b['commit_revision'],execution_fact=False) for cid,b in pending_publications.items()]
            if pending_sources:
                dimensions['event_integrity']['source_execution_records'] = list(pending_sources.values())
            dimensions['event_integrity']['legacy_event_branch_bindings'] = list(pending_bindings.values())
            if pending_bindings:
                dimensions['event_integrity']['limits'] = [
                    'Legacy generator events lack embedded branch ids; exact unchanged events are attributed by verified original commit proofs.']

    guard('event_integrity', branch_provenance_audit)

    def event_audit():
        if (execution_contract or qualified_actions) and not qualification_ready:
            independent = validate_events(definition, inherited_prefix or [])
            missing('event_integrity', 'LIVE_EVENTS_REQUIRE_VERIFIED_QUALIFICATION_CONTRACT')
            dimensions['event_integrity']['independent_event_audit_scope'] = 'INHERITED_PREFIX_ONLY'
        elif execution_contract:
            independent = validate_events(definition, events, assignment_validator=actual_assignment_validator)
        else:
            independent = validate_events(definition, events)
        dimensions['event_integrity']['independent_event_metrics'] = independent['metrics']
        dimensions['event_integrity']['issues'].extend(independent['issues'])
        clock = at(audit['as_of'])
        for index, event in enumerate(unique_events):
            p, kind, eid = event['payload'], event['kind'], event['event_id']
            check(at(event['occurred_at']) <= clock, 'event_integrity', 'EVENT_AFTER_SIMULATION_CLOCK', event_id=eid)
            if index >= prefix_count:
                if event.get('branch_id') != branch:
                    if _is_legacy_generator_event(event):
                        if eid not in legacy_bindings:
                            missing('event_integrity', 'LEGACY_GENERATOR_BRANCH_PROVENANCE_REQUIRED', event_id=eid)
                    else:
                        issue('event_integrity', 'EVENT_BRANCH_MISMATCH', event_id=eid)
            if event.get('adoption_id'):
                event_by_action[event['adoption_id']].append(event)
            if kind == 'SalesOrderCreated':
                orders[p['order_id']] = dict(p)
            elif kind == 'SalesOrderChanged':
                for name in ('quantity', 'due', 'priority'):
                    if name in p:
                        orders[p['order_id']][name] = p[name]
            elif kind in ('ManufacturingTaskCreated','ManufacturingOrderDraftCreated'):
                jobs[p['job_id']] = dict(p)
            elif kind=='ManufacturingOrderRescheduled':
                jobs[p['job_id']]['plan_operations']=p['plan_operations']
                jobs[p['job_id']]['external_version']=p['external_version']
            elif kind=='ManufacturingOrderConfirmed':
                jobs[p['job_id']]['external_version']=p['external_version']
            elif kind == 'GoodsReceived':
                receipts[p['lot_id']] += number(p['quantity'])
            elif kind in ('QCReleased', 'QCHeld'):
                origin = p.get('from_status', 'pending')
                target = 'available' if kind == 'QCReleased' else 'held'
                lot_qc[p['lot_id']][origin] -= number(p['quantity'])
                lot_qc[p['lot_id']][target] += number(p['quantity'])
            elif kind == 'MaterialIssued':
                issued[p['job_id']] += number(p['quantity'])
                lot_issues[p['lot_id']] += number(p['quantity'])
            elif kind == 'FinishedGoodsReceived':
                fg[p['job_id']] += number(p['quantity'])
            elif kind == 'ShipmentPosted':
                shipped[p['order_id']] += number(p['quantity'])
                fg[p['job_id']] -= number(p['quantity'])
            elif kind == 'OperationStarted':
                starts[(p['job_id'], p['operation_id'], p.get('segment_id'))] = event
            elif kind == 'OperationCompleted':
                completes[(p['job_id'], p['operation_id'], p.get('segment_id'))] = event
        for order_id, order in orders.items():
            actual = audit['state'].get('orders', {}).get(order_id)
            check(actual is not None and number(actual['quantity']) == number(order['quantity']) and
                number(actual['shipped']) == shipped[order_id], 'actual_execution',
                'ACTUAL_ORDER_STATE_MISMATCH', order_id=order_id)
        check(set(audit['state'].get('orders', {})) == set(orders), 'actual_execution',
            'ACTUAL_ORDER_IDENTITY_SCOPE_MISMATCH')
        check(set(audit['state'].get('lots', {})) == set(receipts), 'actual_execution',
            'ACTUAL_LOT_IDENTITY_SCOPE_MISMATCH')
        for lot_id, received in receipts.items():
            actual = audit['state'].get('lots', {}).get(lot_id)
            expected = dict(physical=received - lot_issues[lot_id], consumed=lot_issues[lot_id],
                pending=received + lot_qc[lot_id]['pending'], available=lot_qc[lot_id]['available'] - lot_issues[lot_id], held=lot_qc[lot_id]['held'])
            check(actual is not None and all(number(actual[k]) == value for k, value in expected.items()),
                'actual_execution', 'ACTUAL_LOT_STATE_MISMATCH', lot_id=lot_id)
        for job_id, quantity in fg.items():
            check(number(audit['state'].get('finished', {}).get(job_id, 0)) == quantity,
                'actual_execution', 'ACTUAL_FINISHED_STATE_MISMATCH', job_id=job_id)
        for event in unique_events[prefix_count:]:
            if event['kind'] not in EXECUTION_KINDS:
                continue
            p = event['payload']; job = jobs.get(p.get('job_id'), {})
            order = orders.get(p.get('order_id', job.get('order_id')), {})
            if order.get('release_not_before'):
                check(at(event['occurred_at']) >= at(order['release_not_before']), 'actual_execution',
                    'EXECUTION_BEFORE_ORDER_RELEASE', event_id=event['event_id'])
            check(event.get('adoption_id') in actions or event['event_id'] in source_bindings,
                'adoption_authorization', 'UNADOPTED_EXECUTION', event_id=event['event_id'])

    guard('event_integrity', event_audit)
    views = {}

    def source_audit():
        if not source_views:
            for name in ('source_identity', 'source_visibility', 'source_values', 'dynamic_discovery'):
                missing(name, 'SOURCE_VIEWS_REQUIRED')
            return
        for view in source_views:
            revision = view['revision']
            check(view.get('branch_id') == branch and view.get('enterprise_id') == enterprise,
                'source_identity', 'SOURCE_BRANCH_MISMATCH', source_revision=revision)
            check(revision <= audit['revision'] and at(view['as_of']) <= at(audit['as_of']),
                'source_identity', 'SOURCE_VIEW_AFTER_CURRENT_CLOCK', source_revision=revision)
            if revision in views:
                check(view == views[revision], 'source_identity', 'IMMUTABLE_SOURCE_VIEW_CHANGED', source_revision=revision)
            views[revision] = view
            check(set(view['documents']) == {'ERP', 'WMS', 'MES'}, 'source_identity', 'THREE_SOURCE_SYSTEMS_REQUIRED')
            for system, document in view['documents'].items():
                check(document.get('system') == system and document.get('enterprise_id') == enterprise and
                    document.get('branch_id') == branch and document.get('source_revision') == revision and
                    document.get('as_of') == view['as_of'], 'source_identity', 'SOURCE_BRANCH_MISMATCH',
                    system=system, source_revision=revision)
                dimensions['source_visibility']['issues'].extend(dict(row, system=system, source_revision=revision)
                    for row in validate_visibility(events, document, view['as_of']))
                dimensions['source_values']['issues'].extend(dict(row, system=system, source_revision=revision)
                    for row in validate_source_values(definition, events, document))
        ordered = sorted(views.values(), key=lambda v: v['revision'])
        latest = ordered[-1]
        for event_id in legacy_bindings:
            event = next(event for event in events if event['event_id'] == event_id)
            system = LEGACY_GENERATOR_AUTHORITY[event['kind']]
            if at(event['known_at'][system]) <= at(latest['as_of']):
                references = {ref for rows in latest['documents'][system].get('event_refs', {}).values()
                    for refs in rows.values() for ref in refs}
                check(event_id in references, 'source_values', 'LEGACY_GENERATOR_SOURCE_REFERENCE_MISSING',
                    event_id=event_id, system=system)
        if ordered[-1]['revision'] != audit['revision']:
            missing('source_visibility', 'LATEST_CURRENT_SOURCE_VIEW_REQUIRED', current_revision=audit['revision'])
        for before, after in zip(ordered, ordered[1:]):
            check(at(before['as_of']) < at(after['as_of']), 'source_identity', 'SOURCE_VIEW_CLOCK_REGRESSION')
        if len(ordered) < 2:
            missing('dynamic_discovery', 'BEFORE_AND_AFTER_SOURCE_VIEWS_REQUIRED')
            return
        first, last = ordered[0], ordered[-1]
        initial_orders = _rows(first['documents']['ERP']['tables'].get('sales_orders', []))
        latest_orders = _rows(last['documents']['ERP']['tables'].get('sales_orders', []))
        new_ids = sorted(order_id for order_id in latest_orders if order_id not in initial_orders and
            any(e['kind'] == 'SalesOrderCreated' and e['payload']['order_id'] == order_id and
                at(e['occurred_at']) > at(first['as_of']) for e in events))
        dimensions['dynamic_discovery']['new_order_ids'] = new_ids
        dimensions['dynamic_discovery']['changed_source_systems'] = [system for system in ('ERP', 'WMS', 'MES')
            if not _tables_equal(first['documents'][system]['tables'], last['documents'][system]['tables'])]
        if not dimensions['dynamic_discovery']['changed_source_systems']:
            missing('dynamic_discovery', 'NO_POST_CONNECTION_BUSINESS_CHANGE_OBSERVED')

    guard('source_values', source_audit)
    runs = {row.get('run_id', row.get('solve_run_id')): row for row in aps.get('runs', [])}
    adoption_records = {row['action_id']: row for row in aps.get('adoptions', [])}
    confirmations = {row['record_id']: row for row in aps.get('operator_records', [])}
    publications = {row.get('command',{}).get('command_id'):row for row in aps.get('publications',[])}

    def stage_action_audit(action,actual,run):
        p=action['payload'];meta=p['continuation'];jid=p['existing_job_id'];ref=dict(action_id=action['action_id'])
        route=definition['products'][p['product_id']]['routing'];job=jobs.get(jid,{})
        prior=[e for e in unique_events if at(e['occurred_at'])<=at(action['adopted_at']) and
            e.get('adoption_id')!=action['action_id'] and e['payload'].get('job_id')==jid]
        progress={op:sum((number(e['payload']['actual_quantity'] if e['kind']=='OperationInterrupted' else e['payload']['quantity'])
            for e in prior if e['kind'] in ('OperationCompleted','OperationInterrupted') and e['payload']['operation_id']==op),Decimal(0)) for op in route}
        received=sum((number(e['payload']['quantity']) for e in prior if e['kind']=='FinishedGoodsReceived'),Decimal(0))
        sent=sum((number(e['payload']['quantity']) for e in prior if e['kind']=='ShipmentPosted'),Decimal(0));total=number(job.get('quantity',0))
        check(job.get('order_id')==p['order_id'] and job.get('product_id')==p['product_id'] and action.get('job_id')==jid and
            meta.get('continuity_job_id')==jid and meta.get('actual_order_id')==p['order_id'],
            'actual_execution','CONTINUATION_JOB_IDENTITY_MISMATCH',**ref)
        check(set(meta.get('completed_quantities',{}))==set(route) and all(number(meta['completed_quantities'][op])==q for op,q in progress.items()),
            'actual_execution','CONTINUATION_PROGRESS_CHANGED',**ref)
        check(total==20 and number(meta.get('original_job_quantity',0))==total and received==number(meta.get('finished_received_quantity',-1)) and
            sent==number(meta.get('shipped_quantity',-1)) and number(p['quantity'])==number(meta.get('remaining_quantity',-1))==total-received,
            'actual_execution','CONTINUATION_QUANTITY_CONFLICT',**ref)
        for op in route:
            selected=[o for o in p['operations'] if o['operation_id']==op]
            check(sum((number(o['quantity']) for o in selected),Decimal(0))==total-progress[op],
                'native_plan_lineage','CONTINUATION_STAGE_OPERATION_QUANTITY',operation_id=op,**ref)
        check(sum((number(r['quantity']) for r in p.get('planned_receipts',[])),Decimal(0))==total-received,
            'native_plan_lineage','CONTINUATION_RECEIPT_QUANTITY_CONFLICT',**ref)
        check(not any(e['kind'] in ('ManufacturingTaskCreated','MaterialIssued') for e in actual),
            'actual_execution','CONTINUATION_RELEASE_OR_ISSUE_REPEATED',**ref)
        for index,o in enumerate(p['operations']):
            matches=[e for e in actual if e['kind'] in ('OperationStarted','OperationCompleted') and
                e['payload'].get('segment_id')==action['action_id']+'_'+str(index)]
            check(all(e['payload']['operation_id']==o['operation_id'] and number(e['payload']['quantity'])==number(o['quantity']) for e in matches),
                'actual_execution','CONTINUATION_SEGMENT_IDENTITY_MISMATCH',**ref)
            if run:
                check(any(_same_operation(c,o) and number(c.get('quantity',-1))==number(o['quantity']) for c in run.get('operations',[])),
                    'native_plan_lineage','ADOPTED_OPERATION_NOT_IN_NATIVE_RUN',**ref)
        if run and 'planned_receipts' not in run:missing('native_plan_lineage','NATIVE_RECEIPT_EXTRACTION_REQUIRED',**ref)
        elif run:
            check(run['planned_receipts']==p.get('planned_receipts',[]),'native_plan_lineage','ADOPTED_RECEIPT_NOT_IN_NATIVE_RUN',**ref)

    def action_audit():
        if not actions:
            missing('adoption_authorization', 'ADOPTION_RECORDS_REQUIRED')
            missing('native_plan_lineage', 'ADOPTED_NATIVE_RUN_REQUIRED')
        for action_id, action in actions.items():
            p = action['payload']; actual = event_by_action[action_id]
            reference = dict(action_id=action_id)
            payload_sha = digest(p)
            if action_id in qualified_actions:
                if not execution_contract:
                    missing('adoption_authorization', 'LIVE_EXECUTION_CONTRACT_EXPORT_REQUIRED', **reference)
                else:
                    check(action.get('execution_contract') == execution_contract and p.get('execution_contract') == execution_contract,
                        'adoption_authorization', 'ACTION_EXECUTION_CONTRACT_MISMATCH', **reference)
                    for event in actual:
                        check(event.get('execution_contract_sha256') == digest(execution_contract), 'actual_execution',
                            'EVENT_EXECUTION_CONTRACT_MISMATCH', event_id=event['event_id'], **reference)
                if qualification_ready and p.get('action_type') == 'MANUFACTURE':
                    for requested in p['operations']:
                        qualified_assignment(requested['operation_id'], requested['resource_id'], requested['operator_id'],
                            at(requested['start_at']), at(requested['end_at']), action_id=action_id, interval_basis='ADOPTED_NATIVE_PLAN')
            check(all(p.get(k) for k in ('plan_id', 'run_id', 'data_version_id', 'input_hash')),
                'native_plan_lineage', 'IMMUTABLE_PLAN_RUN_EVIDENCE_REQUIRED', **reference)
            check(at(action['adopted_at']) <= at(audit['as_of']), 'actual_execution',
                'ADOPTION_AFTER_SIMULATION_CLOCK', **reference)
            check(action.get('evidence_sha256') == payload_sha, 'adoption_authorization', 'ADOPTION_PAYLOAD_CHANGED', **reference)
            check(p.get('branch_id') == branch and p.get('enterprise_id') == enterprise,
                'adoption_authorization', 'ADOPTION_BRANCH_MISMATCH', **reference)
            check(set(action.get('actual_event_ids', [])) == {e['event_id'] for e in actual},
                'actual_execution', 'ACTION_EVENT_REFERENCE_MISMATCH', **reference)
            for event in actual:
                check(all(event.get(k) == p.get(k) for k in ('plan_id', 'run_id', 'data_version_id')),
                    'native_plan_lineage', 'EXECUTION_RUN_IDENTITY_MISMATCH', event_id=event['event_id'], **reference)
                check(at(event['occurred_at']) >= at(action['adopted_at']), 'actual_execution', 'EXECUTION_BEFORE_ADOPTION', **reference)
                if action.get('cancelled_at'):
                    check(at(event['occurred_at']) <= at(action['cancelled_at']), 'actual_execution', 'EXECUTION_AFTER_CANCELLATION', **reference)
                if action.get('job_id') and event['kind'] in EXECUTION_KINDS:
                    check(event['payload'].get('job_id') == action['job_id'], 'actual_execution', 'ACTION_JOB_IDENTITY_MISMATCH', **reference)
                if event['kind'] == 'ManufacturingTaskCreated':
                    check(all(event['payload'].get(k) == p.get(k) for k in ('order_id', 'product_id')) and
                        number(event['payload']['quantity']) == number(p['quantity']), 'actual_execution',
                        'ACTION_TARGET_IDENTITY_MISMATCH', **reference)
                elif event['kind'] == 'PurchaseOrderCreated':
                    check(event['payload'].get('po_id') == action.get('po_id') and
                        all(event['payload'].get(k) == p.get(k) for k in ('item_id', 'supplier_id')) and
                        number(event['payload']['quantity']) == number(p['quantity']), 'actual_execution',
                        'ACTION_TARGET_IDENTITY_MISMATCH', **reference)
            claimed = p.get('operator_confirmation', {})
            check(claimed.get('confirmed') is True and claimed.get('role') == 'RESEARCH_SANDBOX_OPERATOR' and
                all(claimed.get(k) for k in ('record_id', 'actor_id')),
                'adoption_authorization', 'RESEARCH_OPERATOR_CONFIRMATION_REQUIRED', **reference)
            publication_id=action.get('publication_command_id')
            binding=publication_bindings.get(publication_id)
            published=False
            if publication_id:
                if not binding:missing('adoption_authorization','INDEPENDENT_TARGET_PUBLICATION_REQUIRED',**reference)
                independent=publications.get(publication_id)
                if not independent:missing('adoption_authorization','INDEPENDENT_APS_PUBLICATION_REQUIRED',**reference)
                if binding and independent:
                    published=True
                    check(independent.get('command')==binding['command'] and independent.get('receipt')==binding['receipt'],
                        'adoption_authorization','PUBLICATION_APS_RECORD_MISMATCH',**reference)
                    expected=copy.deepcopy(binding['command']['payload'])
                    expected['operator_confirmation']=dict(binding['command']['operator_confirmation'],role='RESEARCH_SANDBOX_OPERATOR')
                    expected['execution_contract']=execution_contract
                    if expected.get('existing_job_id') and not expected.get('continuation'):
                        expected.pop('existing_job_id');expected.pop('expected_document_version',None)
                    check(p==expected and action.get('job_id')==binding['receipt']['external_document_id'],
                        'adoption_authorization','PUBLICATION_EXECUTION_MAPPING_MISMATCH',**reference)
            mapping = adoption_records.get(action_id)
            if not mapping and not published:
                missing('adoption_authorization', 'INDEPENDENT_ADOPTION_MAPPING_REQUIRED', **reference)
            elif mapping:
                check(mapping.get('payload_sha256') == payload_sha and
                    all(mapping.get(k) == p.get(k) for k in ('plan_id', 'run_id', 'data_version_id')),
                    'adoption_authorization', 'ADOPTION_MAPPING_MISMATCH', **reference)
                check(mapping.get('operator_record_id') == claimed.get('record_id'),
                    'adoption_authorization', 'OPERATOR_CONFIRMATION_MISMATCH', **reference)
            trusted = confirmations.get(claimed.get('record_id'))
            if trusted is None and not published:
                missing('adoption_authorization', 'INDEPENDENT_OPERATOR_RECORD_REQUIRED', **reference)
            elif trusted is not None:
                check(trusted.get('payload_sha256') == payload_sha and all(trusted.get(k) == claimed.get(k)
                    for k in ('record_id', 'actor_id', 'role', 'confirmed')), 'adoption_authorization', 'OPERATOR_CONFIRMATION_MISMATCH', **reference)
            evidence = p.get('source_evidence', {})
            check(evidence.get('revision') == p.get('world_revision'), 'native_plan_lineage', 'STALE_ADOPTION_SOURCE_EVIDENCE', **reference)
            basis = views.get(evidence.get('revision'))
            if basis is None:
                missing('native_plan_lineage', 'ADOPTION_SOURCE_VIEW_REQUIRED', **reference)
            else:
                check(evidence.get('as_of') == basis['as_of'] and at(basis['as_of']) <= at(action['adopted_at']),
                    'native_plan_lineage', 'STALE_ADOPTION_SOURCE_EVIDENCE', **reference)
            run = runs.get(p.get('run_id'))
            if run is None:
                missing('native_plan_lineage', 'INDEPENDENT_NATIVE_RUN_REQUIRED', **reference)
            else:
                check(all(run.get(k) == p.get(k) for k in ('plan_id', 'data_version_id', 'input_hash')) and run.get('state') == 'SUCCEEDED',
                    'native_plan_lineage', 'ADOPTION_RUN_IDENTITY_MISMATCH', **reference)
                native = run.get('native_audit')
                if not native:
                    missing('native_plan_lineage', 'INDEPENDENT_NATIVE_AUDIT_REQUIRED', **reference)
                else:
                    check(native.get('status') == 'PASS' and all(native.get(k) == p.get(k) for k in ('run_id', 'input_hash')),
                        'native_plan_lineage', 'NATIVE_AUDIT_NOT_PASSING_FOR_ADOPTED_INPUT', **reference)
                if p['action_type'] == 'MANUFACTURE':
                    if 'operations' not in run:
                        missing('native_plan_lineage', 'NATIVE_OPERATION_EXTRACTION_REQUIRED', **reference)
                    else:
                        for operation in p['operations']:
                            check(any(_same_operation(candidate, operation)
                                for candidate in run['operations']), 'native_plan_lineage', 'ADOPTED_OPERATION_NOT_IN_NATIVE_RUN', **reference)
                elif p['action_type'] == 'PURCHASE':
                    proposals = run.get('purchase_actions', [])
                    bounded = mapping and mapping.get('bounded_action_approved') is True
                    if not bounded and not any(all(candidate.get(k) == p.get(k) for k in ('item_id', 'supplier_id', 'quantity', 'expected_receipt_at')) for candidate in proposals):
                        missing('native_plan_lineage', 'NATIVE_OR_EXPLICIT_BOUNDED_PURCHASE_EVIDENCE_REQUIRED', **reference)
            kind = p['action_type']
            check(action.get('status') in {'ADOPTED', 'EXECUTING', 'BLOCKED', 'CANCELLED', 'COMPLETED'},
                'actual_execution', 'UNKNOWN_ACTION_STATUS', **reference)
            if action.get('status') == 'COMPLETED':
                target_kind = 'ShipmentPosted' if kind == 'MANUFACTURE' else 'GoodsReceived'
                check(sum((number(e['payload']['quantity']) for e in actual if e['kind'] == target_kind), Decimal(0)) == number(p['quantity']),
                    'actual_execution', 'ACTION_COMPLETION_WITHOUT_FULFILLMENT', **reference)
            if kind == 'MANUFACTURE':
                route = definition['products'][p['product_id']]['routing']
                staged=p.get('continuation',{}).get('progress_contract')=='stage-flow-v2'
                if staged:
                    stage_action_audit(action,actual,run)
                    route=[o['operation_id'] for o in p['operations']]
                if p.get('existing_job_id') and not staged:
                    meta = p.get('continuation', {})
                    job_id = p['existing_job_id']; job = jobs.get(job_id, {})
                    prior_events = [event for event in unique_events if at(event['occurred_at']) <= at(action['adopted_at'])
                        and event.get('adoption_id') != action_id and event['payload'].get('job_id') == job_id]
                    progress = {op: sum((number(event['payload']['quantity']) for event in prior_events
                        if event['kind'] == 'OperationCompleted' and event['payload']['operation_id'] == op), Decimal(0)) for op in route}
                    received = sum((number(event['payload']['quantity']) for event in prior_events if event['kind'] == 'FinishedGoodsReceived'), Decimal(0))
                    sent = sum((number(event['payload']['quantity']) for event in prior_events if event['kind'] == 'ShipmentPosted'), Decimal(0))
                    total = number(job.get('quantity', 0)); done = progress[route[-1]]
                    boundary = next((index for index, op in enumerate(route) if progress[op] != total), None)
                    check(job.get('order_id') == p['order_id'] and job.get('product_id') == p['product_id']
                        and action.get('job_id') == job_id and meta.get('continuity_job_id') == job_id
                        and meta.get('actual_order_id') == p['order_id'] and meta.get('recipe_id') == 'CONT-CONTINUE:' + job_id,
                        'actual_execution', 'CONTINUATION_JOB_IDENTITY_MISMATCH', **reference)
                    supplied = meta.get('completed_quantities', {})
                    check(set(supplied) == set(route) and all(number(supplied[op]) == progress[op] for op in route),
                        'actual_execution', 'CONTINUATION_PROGRESS_CHANGED', **reference)
                    check(total == 20 and number(meta.get('original_job_quantity', 0)) == total and
                        received == done == number(meta.get('finished_received_quantity', -1)) and
                        sent == number(meta.get('shipped_quantity', -1)) and
                        number(p['quantity']) == number(meta.get('remaining_quantity', 0)) == total - done,
                        'actual_execution', 'CONTINUATION_QUANTITY_CONFLICT', **reference)
                    check(boundary is not None and boundary > 0 and all(progress[op] == done for op in route[boundary:]) and
                        all(not definition['operations'][op].get('batch_quantity') and
                            number(definition['operations'][op].get('fixed_minutes', 0)) == 0 and
                            number(definition['operations'][op].get('minutes_per_piece', 0)) > 0 for op in route[boundary:]),
                        'actual_execution', 'CONTINUATION_MULTISTAGE_OR_FIXED_REMAINDER_UNSUPPORTED', **reference)
                    route = route[boundary:] if boundary is not None else []
                    check(route == meta.get('remaining_operation_ids') and route and route[0] == meta.get('remaining_from_operation_id'),
                        'native_plan_lineage', 'CONTINUATION_REMAINING_ROUTE_MISMATCH', **reference)
                    check(not any(event['kind'] in ('ManufacturingTaskCreated', 'MaterialIssued') for event in actual),
                        'actual_execution', 'CONTINUATION_RELEASE_OR_ISSUE_REPEATED', **reference)
                    check(all(number(event['payload']['quantity']) == number(p['quantity']) and
                        (event['kind'] not in ('OperationStarted', 'OperationCompleted') or event['payload'].get('segment_id') == action_id)
                        for event in actual if event['kind'] in EXECUTION_KINDS),
                        'actual_execution', 'CONTINUATION_SEGMENT_IDENTITY_MISMATCH', **reference)
                check([row['operation_id'] for row in p['operations']] == route,
                    'native_plan_lineage', 'ADOPTED_ROUTING_MISMATCH', **reference)
                for index,requested in enumerate(p['operations']):
                    key = (action.get('job_id'), requested['operation_id'], action_id+('_'+str(index) if staged else '') if p.get('existing_job_id') else None); started, completed = starts.get(key), completes.get(key)
                    # The same formal job can have a cancelled earlier plan.
                    # A later action's actual is never evidence for that plan.
                    if started and started.get('adoption_id')!=action_id:started=None
                    if completed and completed.get('adoption_id')!=action_id:completed=None
                    if started:
                        row = dict(action_id=action_id, job_id=key[0], operation_id=key[1], planned_start=requested['start_at'],
                            planned_end=requested['end_at'], actual_start=started['occurred_at'],
                            actual_end=completed['occurred_at'] if completed else None,
                            start_deviation_seconds=(at(started['occurred_at']) - at(requested['start_at'])).total_seconds(),
                            end_deviation_seconds=(at(completed['occurred_at']) - at(requested['end_at'])).total_seconds() if completed else None)
                        result['plan_actual'].append(row)
                        if key[2] is not None:
                            row['segment_id'] = key[2]
            result['actions'].append(dict(action_id=action_id, action_type=kind, status=action.get('status'),
                execution_state='NOT_EXECUTED' if not actual else 'COMPLETED' if action.get('status') == 'COMPLETED' else 'PARTIAL_OR_BLOCKED',
                actual_event_count=len(actual), actual_event_ids=[e['event_id'] for e in actual]))
        for action_id in event_by_action:
            check(action_id in actions, 'adoption_authorization', 'UNKNOWN_ADOPTION_REFERENCE', action_id=action_id)
        for block in audit.get('execution_blocks', []):
            check(block.get('action_id') in actions, 'actual_execution', 'EXECUTION_BLOCK_ACTION_UNKNOWN',
                action_id=block.get('action_id'))
            check(at(block['occurred_at']) <= at(audit['as_of']), 'actual_execution',
                'EXECUTION_BLOCK_AFTER_SIMULATION_CLOCK', action_id=block.get('action_id'))
        if not result['plan_actual']:
            missing('plan_actual_comparison', 'ACTUAL_OPERATION_EVENTS_REQUIRED')
        dimensions['plan_actual_comparison']['deviating_operations'] = sum(
            row['start_deviation_seconds'] != 0 or row['end_deviation_seconds'] not in (0, None) for row in result['plan_actual'])

    guard('adoption_authorization', action_audit)

    def fulfillment_audit():
        targets = order_ids if order_ids is not None else sorted({a['payload'].get('order_id') for a in actions.values() if a['payload'].get('order_id')})
        if not targets:
            missing('order_fulfillment', 'TARGET_ORDERS_REQUIRED')
        for order_id in targets:
            order = orders.get(order_id)
            if order is None:
                issue('order_fulfillment', 'TARGET_ORDER_UNKNOWN', order_id=order_id)
                continue
            job_ids = {job_id for job_id, job in jobs.items() if job['order_id'] == order_id}
            operations = [key for key in completes if key[0] in job_ids]
            remaining = number(order['quantity']) - shipped[order_id]
            blocks = [block for block in audit.get('execution_blocks', []) if
                actions.get(block.get('action_id'), {}).get('payload', {}).get('order_id') == order_id]
            active_blocks = [block for block in blocks if actions[block['action_id']].get('status') == 'BLOCKED']
            shipments = [e for e in events if e['kind'] == 'ShipmentPosted' and e['payload']['order_id'] == order_id]
            result['orders'].append(dict(order_id=order_id, quantity=text(order['quantity']), shipped=text(shipped[order_id]),
                remaining=text(remaining), actual_job_ids=sorted(job_ids), actual_operation_count=len(operations),
                shipment_event_ids=[e['event_id'] for e in shipments], execution_blocks=blocks))
            if remaining and active_blocks:
                dimensions['order_fulfillment']['limits'] = ['Actual remaining demand and explicit legal blocking retained.']
            elif remaining:
                missing('order_fulfillment', 'TARGET_ORDER_NOT_FULFILLED_OR_LEGALLY_BLOCKED', order_id=order_id)

    guard('order_fulfillment', fulfillment_audit)

    def aps_audit():
        versions, syncs = aps.get('versions', []), aps.get('syncs', [])
        if not versions or not syncs:
            missing('aps_feedback', 'INDEPENDENT_CANONICAL_AND_SYNC_RECEIPTS_REQUIRED')
            missing('version_continuity', 'INDEPENDENT_VERSION_CHAIN_REQUIRED')
            return
        sync_by_version = {row['version_id']: row for row in syncs}
        prior = None
        fingerprints = {row.get('configuration_fingerprint') for row in syncs}
        if None in fingerprints:
            missing('version_continuity', 'CONNECTOR_CONFIGURATION_FINGERPRINT_REQUIRED')
        check(len(fingerprints - {None}) <= 1, 'version_continuity', 'CONNECTOR_CONFIGURATION_CHANGED')
        seen_versions = {}
        source_heads = {}
        latest_view = max(views.values(), key=lambda row: row['revision']) if views else None
        latest_synced_revision = None
        for version in versions:
            version_id = version['id']
            if version_id in seen_versions:
                check(version == seen_versions[version_id], 'version_continuity', 'IMMUTABLE_APS_VERSION_CHANGED', version_id=version_id)
                continue
            seen_versions[version_id] = version
            if prior is not None:
                check(version.get('parent_version_id') == prior['id'], 'version_continuity', 'APS_VERSION_PARENT_MISMATCH', version_id=version_id)
            prior = version
            canonical = version.get('canonical', version.get('business_data', {}).get('canonical'))
            if not canonical:
                missing('aps_feedback', 'CANONICAL_VERSION_DOCUMENT_REQUIRED', version_id=version_id)
                continue
            sync = sync_by_version.get(version_id)
            if not sync:
                missing('aps_feedback', 'VERSION_SOURCE_SYNC_MAPPING_REQUIRED', version_id=version_id)
                continue
            check(sync.get('branch_id') == branch and sync.get('status') in {'COMMITTED', 'UNCHANGED'},
                'aps_feedback', 'APS_SOURCE_BRANCH_MISMATCH', version_id=version_id)
            check(canonical.get('enterprise_id') == enterprise and canonical.get('planning_at') == sync.get('as_of'),
                'aps_feedback', 'APS_SOURCE_CLOCK_MISMATCH', version_id=version_id)
            receipt = version.get('source_sync_receipt', version.get('business_data', {}).get('source_sync_receipt'))
            if not receipt:
                missing('version_continuity', 'CANONICAL_SOURCE_SYNC_RECEIPT_REQUIRED', version_id=version_id)
            else:
                check(receipt.get('enterprise_id') == enterprise and receipt.get('as_of') == canonical.get('planning_at'),
                    'version_continuity', 'SOURCE_SYNC_RECEIPT_CLOCK_MISMATCH', version_id=version_id)
                heads = receipt.get('batches', [])
                check(len(heads) == 3 and {head.get('system') for head in heads} == {'ERP', 'WMS', 'MES'},
                    'version_continuity', 'SOURCE_SYNC_RECEIPT_SYSTEMS_MISMATCH', version_id=version_id)
                for head in heads:
                    system = head['system']; chain = head.get('chain') or [head]
                    prior_head = source_heads.get(system)
                    previous_id = prior_head.get('batch_id') if prior_head else None
                    for index, batch in enumerate(chain):
                        sha = batch.get('source_sha256', '')
                        check(isinstance(sha, str) and len(sha) == 64 and all(c in '0123456789abcdef' for c in sha)
                            and bool(batch.get('cursor')) and bool(batch.get('batch_id')), 'version_continuity',
                            'SOURCE_BATCH_RECEIPT_IDENTITY_REQUIRED', system=system, version_id=version_id)
                        if previous_id is not None and batch.get('batch_id') != previous_id:
                            check(batch.get('previous_batch_id') == previous_id, 'version_continuity',
                                'SOURCE_BATCH_CHAIN_MISMATCH', system=system, version_id=version_id)
                        if batch.get('as_of'):
                            check(at(batch['as_of']) <= at(receipt['as_of']), 'version_continuity',
                                'SOURCE_BATCH_AFTER_KNOWLEDGE_CUTOFF', system=system, version_id=version_id)
                        if batch.get('imported_at'):
                            at(batch['imported_at'])
                        else:
                            missing('version_continuity', 'APS_INGESTION_TIME_REQUIRED', system=system, version_id=version_id)
                        previous_id = batch.get('batch_id')
                    check(all(chain[-1].get(k) == head.get(k) for k in
                        ('batch_id', 'source_sha256', 'cursor', 'previous_batch_id', 'as_of', 'mode')),
                        'version_continuity', 'SOURCE_BATCH_CHAIN_MISMATCH', system=system, version_id=version_id)
                    source_heads[system] = head
            view = views.get(sync.get('source_revision'))
            if view is None:
                missing('aps_feedback', 'VERSION_IMMUTABLE_SOURCE_VIEW_REQUIRED', version_id=version_id)
                continue
            latest_synced_revision = sync['source_revision']
            tables = canonical['tables']
            bundle = next((row['continuity_bundle'] for row in tables.get('locations', []) if 'continuity_bundle' in row), None)
            if not bundle:
                missing('aps_feedback', 'CANONICAL_ORIGINAL_SOURCE_BUNDLE_REQUIRED', version_id=version_id)
                continue
            docs = bundle['documents']
            for system in ('ERP', 'WMS', 'MES'):
                check(docs[system].get('branch_id') == branch and docs[system].get('source_revision') == view['revision'] and
                    docs[system].get('as_of') == view['as_of'] and _tables_equal(docs[system]['tables'], view['documents'][system]['tables']),
                    'aps_feedback', 'APS_SOURCE_DOCUMENT_MISMATCH', system=system, version_id=version_id)
            source_orders = _rows(docs['ERP']['tables'].get('sales_orders', []))
            demands = defaultdict(Decimal)
            for row in tables.get('demand_lines', []):
                demands[row['order_ref']] += number(row['quantity'])
            for order_id, row in source_orders.items():
                expected = number(row['quantity']) - number(row['shipped'])
                check(demands[order_id] == expected, 'aps_feedback', 'APS_OPEN_DEMAND_MISMATCH', version_id=version_id, order_id=order_id)
            for order_id, quantity in demands.items():
                check(order_id in source_orders or quantity == 0, 'aps_feedback', 'APS_DEMAND_WITHOUT_SOURCE_ORDER', version_id=version_id, order_id=order_id)
            dimensions['aps_feedback']['issues'].extend(validate_knowledge(source_orders, tables.get('sales_orders', [])))
            inventory = _rows(tables.get('inventory_positions', []))
            for lot in docs['WMS']['tables'].get('lots', []):
                row = inventory.get(lot['id'])
                check(row is not None and number(row['physical']) == number(lot['physical']) and
                    number(row['net_available']) == number(lot['available']) and
                    number(row['quality_blocked']) == number(lot['pending']) + number(lot['held']),
                    'aps_feedback', 'APS_CURRENT_INVENTORY_MISMATCH', version_id=version_id, lot_id=lot['id'])
            for finished in docs['WMS']['tables'].get('finished_goods', []):
                row = inventory.get('FG:' + finished['id'])
                check(row is not None and number(row['physical']) == number(finished['physical']) and
                    number(row['net_available']) == number(finished['physical']), 'aps_feedback',
                    'APS_CURRENT_FINISHED_GOODS_MISMATCH', version_id=version_id, job_id=finished['id'])
            canonical_pos = _rows(tables.get('purchase_orders', []))
            for po in docs['ERP']['tables'].get('purchase_orders', []):
                received = sum((number(r['quantity']) for r in docs['WMS']['tables'].get('receipts', []) if r['po_id'] == po['id']), Decimal(0))
                remainder = number(po['quantity']) - received
                row = canonical_pos.get(po['id'])
                check(row is None if not remainder else row is not None and
                    number(row['quantity']) == number(po['quantity']) and number(row['received_quantity']) == received and
                    number(row['unreceived_quantity']) == remainder and row['uom'] == po['unit'], 'aps_feedback',
                    'APS_PURCHASE_REMAINDER_MISMATCH', version_id=version_id, po_id=po['id'])
        if latest_view and latest_synced_revision != latest_view['revision']:
            missing('aps_feedback', 'LATEST_SOURCE_EXECUTION_NOT_YET_SYNCED', source_revision=latest_view['revision'])
        for action in actions.values():
            if action['payload'].get('data_version_id') not in seen_versions:
                missing('native_plan_lineage', 'ADOPTED_DATA_VERSION_NOT_PROVIDED', action_id=action['action_id'])

    guard('aps_feedback', aps_audit)
    return _finish(result)


def _finish(result):
    for value in result['dimensions'].values():
        value['status'] = 'FAIL' if value['issues'] else 'NOT_CERTIFIED' if value['missing_evidence'] else 'PASS_WITH_LIMITS' if value.get('limits') else 'PASS'
    states = {row['status'] for row in result['dimensions'].values()}
    result['status'] = 'FAIL' if 'FAIL' in states else 'NOT_CERTIFIED' if 'NOT_CERTIFIED' in states else 'PASS_WITH_LIMITS' if 'PASS_WITH_LIMITS' in states else 'PASS'
    return result
