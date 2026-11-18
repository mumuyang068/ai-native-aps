"""One Firm priority and/or one qualified SEW person's reduced calendar.

This is an explicit enterprise action, not a general profile/JSON composer.
Legacy public actions keep their existing capability gates.
"""
import copy
import hashlib
import json
from datetime import datetime

from jsonschema import Draft202012Validator

from demand_priority import CHANGE_SCHEMA as ORDER_SCHEMA, apply_change as apply_priority
from planning_capabilities import profile_id, supports
from resource_unavailability import apply_change as apply_unavailability

ACTION = 'simulate_controlled_conditions'
INTENT = '受控条件影响试算'
PROFILE = 'enterprise-accessory-supply-v1'
OPERATOR_SCHEMA = {'type': 'object', 'additionalProperties': False,
                   'properties': {key: {'type': 'string', 'minLength': 1, 'maxLength': 200}
                                  for key in ('resource_id', 'start_at', 'end_at')},
                   'required': ['resource_id', 'start_at', 'end_at']}
IDENTITY_FIELDS = ('plan_id', 'data_version_id', 'baseline_run_id', 'run_id', 'target', 'profile',
                   'snapshot_sha256', 'source_sha256')
CONFIRMATION_SCHEMA = {'type': 'object', 'additionalProperties': False, 'properties': {
    'identity': {'type': 'object', 'additionalProperties': False,
                 'properties': {k: {'type': 'string', 'minLength': 1} for k in IDENTITY_FIELDS},
                 'required': list(IDENTITY_FIELDS)},
    'fingerprint': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'},
    'acknowledge_limits': {'const': True}}, 'required': ['identity', 'fingerprint', 'acknowledge_limits']}
CHANGE_SCHEMA = {'type': 'object', 'additionalProperties': False,
                 'properties': {'order': ORDER_SCHEMA, 'operator': OPERATOR_SCHEMA,
                                'confirmation': CONFIRMATION_SCHEMA,
                                'reason': {'type': 'string', 'minLength': 1, 'maxLength': 1000}},
                 'required': ['reason'], 'anyOf': [{'required': ['order']}, {'required': ['operator']}]}


def apply(data, reason, order=None, operator=None, confirmation=None):
    from drafts import DraftError
    from finished_delivery import stages
    from planning_adapter import validate_input

    def require(ok, code):
        if not ok:
            raise DraftError(code)

    args = {'reason': reason, **({'order': order} if order is not None else {}),
            **({'operator': operator} if operator is not None else {}),
            **({'confirmation': confirmation} if confirmation is not None else {})}
    require(not list(Draft202012Validator(CHANGE_SCHEMA).iter_errors(args)), 'CONTROLLED_INVALID_CONDITIONS')
    require(isinstance(reason, str) and bool(reason.strip()), 'CONTROLLED_REASON_REQUIRED')
    require(profile_id(data) == PROFILE and supports(PROFILE, 'actions', 'controlled_conditions'),
            'UNSUPPORTED_PROFILE_CAPABILITY: controlled_conditions')
    candidate = copy.deepcopy(data)
    validate_input(candidate)
    order_change = operator_change = None
    if order is not None:
        require(any(d['id'] == order['demand_id'] and d.get('kind') == 'FIRM_ORDER'
                    for d in candidate['demand_lines']), 'CONTROLLED_REQUIRES_FIRM_ORDER')
        order_change = apply_priority(candidate, **order)
    if operator is not None:
        rows = [r for r in candidate['resources'] if r['id'] == operator['resource_id']]
        require(len(rows) == 1 and rows[0].get('role') == 'OPERATOR'
                and rows[0].get('capacity_model', 'CONTINUOUS') == 'CONTINUOUS',
                'CONTROLLED_REQUIRES_NAMED_OPERATOR')
        person = rows[0]
        _, sew, _ = stages(candidate)
        route = next(r for r in candidate['routing_revisions'] if r['id'] == sew['routing_revision_id'])
        requirement = route['operations'][0]['operator_requirement']
        require(person.get('pool_id') == requirement['pool_id'] and any(
            s['resource_id'] == person['id'] and s['skill_id'] == requirement['skill_id']
            and datetime.fromisoformat(s['effective_from']) <= datetime.fromisoformat(candidate['planning_at'])
            and datetime.fromisoformat(candidate['horizon']['end_at']) <= datetime.fromisoformat(s['effective_to'])
            for s in candidate['resource_skills']), 'CONTROLLED_REQUIRES_QUALIFIED_SEW_OPERATOR')
        operator_change = apply_unavailability(candidate, **operator)
    validate_input(candidate)
    # Publish the pure input change only after every condition validates.
    data.clear()
    data.update(candidate)
    conditions = {}
    if order_change:
        conditions['order'] = {'demand_id': order_change['demand_id'], 'priority': order_change['after_priority']}
    if operator_change:
        conditions['operator'] = {k: operator_change[k] for k in ('resource_id', 'start_at', 'end_at')}
    return dict(action=ACTION, intent=INTENT, conditions=conditions, order=order_change, operator=operator_change,
                reason=reason.strip(), confirmation=copy.deepcopy(confirmation), simulation_only=True, business_permission='NOT_CONFIRMED',
                execution_authorized=False, engine_executed=False)


def validate_confirmation(changes, baseline_sha256):
    """A persisted action must bind the exact pure preview and explicit acknowledgement."""
    from drafts import DraftError
    confirmation = changes.get('confirmation')
    if not isinstance(confirmation, dict):
        raise DraftError('CONTROLLED_CONFIRMATION_REQUIRED: 请先确认当前条件预览')
    if list(Draft202012Validator(CONFIRMATION_SCHEMA).iter_errors(confirmation)):
        raise DraftError('CONTROLLED_CONFIRMATION_MISMATCH: 确认记录不完整')
    identity = confirmation['identity']
    if (confirmation['acknowledge_limits'] is not True or identity['profile'] != PROFILE
            or identity['target'] != 'baseline' or identity['run_id'] != identity['baseline_run_id']):
        raise DraftError('CONTROLLED_CONFIRMATION_MISMATCH: 确认记录不属于基线预览')
    preview_changes = copy.deepcopy(changes)
    preview_changes['confirmation'] = None
    payload = dict(identity=identity, baseline_sha256=baseline_sha256, changes=preview_changes)
    expected = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                         separators=(',', ':')).encode()).hexdigest()
    if confirmation['fingerprint'] != expected:
        raise DraftError('CONTROLLED_CONFIRMATION_MISMATCH: 预览条件与确认指纹不一致')


def verify(baseline, draft):
    """Return ownership only after full replay; missing labels cannot hide edits."""
    from drafts import DraftError
    changes = draft.get('changes', {})
    marked = isinstance(changes, dict) and (changes.get('action') == ACTION or changes.get('intent') == INTENT)
    original = {d['id']: d for d in baseline['demand_lines']}
    candidate = draft.get('input', {})
    rows = candidate.get('demand_lines', []) if isinstance(candidate, dict) else []
    priority_changed = any(isinstance(d, dict) and d.get('id') in original
                           and d.get('priority') != original[d['id']]['priority']
                           for d in rows) if isinstance(rows, list) else True
    calendar_changed = not isinstance(candidate, dict) or any(candidate.get(k) != baseline.get(k) for k in ('calendars', 'resources'))
    if not marked and not (profile_id(baseline) == PROFILE and (priority_changed or calendar_changed)):
        return False
    try:
        if not isinstance(changes, dict) or changes.get('action') != ACTION:
            raise ValueError('missing action')
        expected = copy.deepcopy(baseline)
        confirmation = changes.get('confirmation')
        validate_confirmation(changes, draft['baseline_sha256'])
        recreated = apply(expected, reason=changes['reason'], confirmation=confirmation, **changes['conditions'])
        expected.update(scenario_id=draft['id'], scenario_revision=1)
        if recreated != changes or expected != candidate:
            raise ValueError('undeclared mutation')
        arguments = {**changes['conditions'], 'reason': changes['reason'], 'confirmation': confirmation}
        request_hash = hashlib.sha256(json.dumps({'name': ACTION, 'arguments': arguments}, sort_keys=True).encode()).hexdigest()
        if draft.get('external_request', {}).get('sha256') != request_hash:
            raise ValueError('confirmation binding differs from original request')
    except (KeyError, TypeError, ValueError) as error:
        raise DraftError('CONTROLLED_CONDITIONS_SCOPE_MISMATCH: only declared Firm priority and one SEW operator calendar may change') from error
    return True
