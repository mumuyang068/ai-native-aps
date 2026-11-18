"""One source-backed LABEL policy/terms scenario in the existing draft store."""
import copy
import hashlib
import json

from jsonschema import Draft202012Validator
from controlled_conditions import CONFIRMATION_SCHEMA, PROFILE
from planning_capabilities import profile_id, supports

ACTION = 'simulate_inventory_decision'
INTENT = '库存政策与有来源补给条件试算'
POLICY_SCHEMA = {'type':'object', 'additionalProperties':False,
    'properties':{k:{'type':'string', 'minLength':1, 'maxLength':200} for k in
        ('item_id','location_id','uom','minimum','maximum','start_at','end_at')},
    'required':['item_id','location_id','uom','minimum','maximum','start_at','end_at']}
CHANGE_SCHEMA = {'type':'object', 'additionalProperties':False, 'properties':{
    'policy':POLICY_SCHEMA, 'source_option_id':{'type':'string','minLength':1,'maxLength':200},
    'reason':{'type':'string','minLength':1,'maxLength':1000}, 'confirmation':CONFIRMATION_SCHEMA},
    'required':['reason'], 'anyOf':[{'required':['policy']},{'required':['source_option_id']}]}


def available(data):
    return (profile_id(data) == PROFILE and supports(PROFILE,'actions','inventory_decision')
            and isinstance(data.get('inventory_decision_catalog'),list) and bool(data['inventory_decision_catalog']))


def apply(data, reason, policy=None, source_option_id=None, confirmation=None):
    from drafts import DraftError
    from inventory_decision import apply as apply_native, summary
    from planning_adapter import validate_input
    args = dict(reason=reason, **({'policy':policy} if policy is not None else {}),
        **({'source_option_id':source_option_id} if source_option_id is not None else {}),
        **({'confirmation':confirmation} if confirmation is not None else {}))
    if list(Draft202012Validator(CHANGE_SCHEMA).iter_errors(args)) or not isinstance(reason,str) or not reason.strip():
        raise DraftError('INVENTORY_DECISION_INVALID_CONDITIONS')
    if not available(data):
        raise DraftError('UNSUPPORTED_PROFILE_CAPABILITY: inventory_decision requires a frozen source catalog')
    validate_input(data)
    sourced_policy = copy.deepcopy(policy)
    if sourced_policy is not None:
        # Identity and provenance come from the confirmed source, never client JSON.
        prov = copy.deepcopy(data['inventory_decision_catalog'][0]['provenance'])
        prov['source_record_id'] += ':SIMULATED_BUFFER_POLICY'
        prov['source_locator'] += '#simulated-buffer-policy'
        sourced_policy['provenance'] = prov
    candidate = apply_native(data, policy=sourced_policy, option_id=source_option_id)
    validate_input(candidate)
    if candidate == data:
        raise DraftError('INVENTORY_DECISION_NO_CHANGE')
    before, after = summary(data), summary(candidate)
    conditions = {**({'policy':copy.deepcopy(policy)} if policy is not None else {}),
                  **({'source_option_id':source_option_id} if source_option_id is not None else {})}
    data.clear(); data.update(candidate)
    return dict(action=ACTION,intent=INTENT,conditions=conditions,reason=reason.strip(),
        before=before,after=after,confirmation=copy.deepcopy(confirmation),simulation_only=True,
        business_permission='NOT_CONFIRMED',execution_authorized=False,engine_executed=False)


def validate_confirmation(changes, baseline_sha256):
    from drafts import DraftError
    confirmation = changes.get('confirmation')
    if not isinstance(confirmation,dict):
        raise DraftError('INVENTORY_DECISION_CONFIRMATION_REQUIRED: 请先确认当前库存与来源条件预览')
    if list(Draft202012Validator(CONFIRMATION_SCHEMA).iter_errors(confirmation)):
        raise DraftError('INVENTORY_DECISION_CONFIRMATION_MISMATCH')
    identity = confirmation['identity']
    if identity['profile'] != PROFILE or identity['target'] != 'baseline' or identity['run_id'] != identity['baseline_run_id']:
        raise DraftError('INVENTORY_DECISION_CONFIRMATION_MISMATCH')
    preview = copy.deepcopy(changes); preview['confirmation'] = None
    payload = dict(identity=identity,baseline_sha256=baseline_sha256,changes=preview)
    digest = hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    if digest != confirmation['fingerprint']:
        raise DraftError('INVENTORY_DECISION_CONFIRMATION_MISMATCH')


def verify(baseline, draft):
    """Full replay also catches removed action labels and unauthorized field edits."""
    from drafts import DraftError
    changes = draft.get('changes')
    actual = draft.get('input')
    marked = isinstance(changes,dict) and (changes.get('action') == ACTION or changes.get('intent') == INTENT)
    keys = ('inventory_buffer_policy','inventory_supply_selection','inventory_decision_catalog')
    altered = isinstance(actual,dict) and any(actual.get(k) != baseline.get(k) for k in keys)
    if not marked and not altered:
        return False
    try:
        if not isinstance(changes,dict) or changes.get('action') != ACTION:
            raise ValueError('missing action')
        validate_confirmation(changes,draft['baseline_sha256'])
        expected = copy.deepcopy(baseline)
        recreated = apply(expected,reason=changes['reason'],confirmation=changes['confirmation'],**changes['conditions'])
        expected.update(scenario_id=draft['id'],scenario_revision=1)
        arguments = dict(**changes['conditions'],reason=changes['reason'],confirmation=changes['confirmation'])
        request_hash = hashlib.sha256(json.dumps({'name':ACTION,'arguments':arguments},sort_keys=True).encode()).hexdigest()
        if recreated != changes or expected != actual or draft.get('external_request',{}).get('sha256') != request_hash:
            raise ValueError('undeclared mutation or confirmation binding')
    except (KeyError,TypeError,ValueError) as exc:
        raise DraftError('INVENTORY_DECISION_SCOPE_MISMATCH: 仅允许声明的库存政策与冻结来源条件，其他企业事实保持不变') from exc
    return True
