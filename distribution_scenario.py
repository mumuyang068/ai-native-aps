"""One frozen route's longer transport assumption in the existing DraftStore."""
import copy,hashlib,json
from jsonschema import Draft202012Validator
from controlled_conditions import CONFIRMATION_SCHEMA
from network_distribution import PROFILE,enabled
from planning_capabilities import supports

ACTION='simulate_distribution_leadtime'
INTENT='运输提前期条件试算'
CHANGE_SCHEMA=dict(type='object',additionalProperties=False,properties={
    'lane_id':dict(type='string',minLength=1,maxLength=200),
    'new_lead_time_seconds':dict(type='integer',minimum=1,maximum=2147483647),
    'reason':dict(type='string',minLength=1,maxLength=1000),'confirmation':CONFIRMATION_SCHEMA},
    required=['lane_id','new_lead_time_seconds','reason'])
def available(data):return enabled(data) and supports(PROFILE,'actions','distribution_leadtime')
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def joint_distribution_condition(data,condition):
    """One legal lane delay; no executed DO or confirmed shipping fact changes."""
    from p22.foundation_projection import require
    require(isinstance(condition,dict) and set(condition)=={'lane_id','new_lead_time_seconds'},'JOINT_DISTRIBUTION_SHAPE')
    rows=[r for r in data.get('distribution_lanes',[]) if r['id']==condition['lane_id']]
    require(len(rows)==1,'JOINT_DISTRIBUTION_UNKNOWN')
    row=rows[0];value=condition['new_lead_time_seconds']
    from datetime import datetime
    limit=(datetime.fromisoformat(data['horizon']['end_at'])-datetime.fromisoformat(data['planning_at'])).total_seconds()
    require(type(value) is int and row['lead_time_seconds']<value<=min(limit,2147483647),'JOINT_DISTRIBUTION_DELAY_ONLY')
    return dict(reference=row['id'],before=dict(lead_time_seconds=row['lead_time_seconds']),after=dict(lead_time_seconds=value),scope='PLANNING_ROUTE_ASSUMPTION_ONLY')

def apply(data,lane_id,new_lead_time_seconds,reason,confirmation=None):
    from drafts import DraftError
    from planning_adapter import validate_input
    args=dict(lane_id=lane_id,new_lead_time_seconds=new_lead_time_seconds,reason=reason,**({'confirmation':confirmation} if confirmation is not None else {}))
    if list(Draft202012Validator(CHANGE_SCHEMA).iter_errors(args)) or not reason.strip():raise DraftError('DISTRIBUTION_INVALID_CONDITIONS')
    if not available(data):raise DraftError('UNSUPPORTED_PROFILE_CAPABILITY: distribution_leadtime')
    validate_input(data);candidate=copy.deepcopy(data);option=candidate['distribution_lead_time_option']
    lane=next((r for r in candidate['distribution_lanes'] if r['id']==lane_id),None)
    if lane is None or lane_id!=option['lane_id']:raise DraftError('DISTRIBUTION_ROUTE_NOT_DECLARED_FOR_CHANGE')
    before=lane['lead_time_seconds']
    if not before<new_lead_time_seconds<=option['maximum_lead_time_seconds']:raise DraftError('DISTRIBUTION_LEADTIME_INCREASE_ONLY')
    lane['lead_time_seconds']=new_lead_time_seconds;validate_input(candidate);data.clear();data.update(candidate)
    return dict(action=ACTION,intent=INTENT,reference=lane_id,conditions=dict(lane_id=lane_id,new_lead_time_seconds=new_lead_time_seconds),
        before_lead_time_seconds=before,after_lead_time_seconds=new_lead_time_seconds,reason=reason.strip(),confirmation=copy.deepcopy(confirmation),
        simulation_only=True,business_permission='NOT_CONFIRMED',execution_authorized=False,engine_executed=False)

def validate_confirmation(changes,baseline_sha256):
    from drafts import DraftError
    c=changes.get('confirmation')
    if not isinstance(c,dict) or list(Draft202012Validator(CONFIRMATION_SCHEMA).iter_errors(c)):raise DraftError('DISTRIBUTION_CONFIRMATION_REQUIRED')
    identity=c['identity'];preview=copy.deepcopy(changes);preview['confirmation']=None
    if identity['profile']!=PROFILE or identity['target']!='baseline' or identity['run_id']!=identity['baseline_run_id'] or digest(dict(identity=identity,baseline_sha256=baseline_sha256,changes=preview))!=c['fingerprint']:
        raise DraftError('DISTRIBUTION_CONFIRMATION_MISMATCH')

def verify(baseline,draft):
    from drafts import DraftError
    if not enabled(baseline):return False
    try:
        changes=draft['changes']
        if changes.get('action')!=ACTION:raise ValueError('unsupported action')
        validate_confirmation(changes,draft['baseline_sha256'])
        expected=copy.deepcopy(baseline);replayed=apply(expected,reason=changes['reason'],confirmation=changes['confirmation'],**changes['conditions'])
        expected.update(scenario_id=draft['id'],scenario_revision=1)
        args=dict(**changes['conditions'],reason=changes['reason'],confirmation=changes['confirmation'])
        request_hash=hashlib.sha256(json.dumps(dict(name=ACTION,arguments=args),sort_keys=True).encode()).hexdigest()
        if replayed!=changes or expected!=draft['input'] or draft.get('external_request',{}).get('sha256')!=request_hash:raise ValueError('undeclared mutation')
    except (KeyError,ValueError,TypeError,StopIteration) as exc:raise DraftError('DISTRIBUTION_SCOPE_MISMATCH: 仅允许冻结路线运输提前期增加；采购、库存、需求及确认事实不变') from exc
    return True
