"""One sourced machine-calendar reduction through the original isolated drafts."""
import copy
import hashlib
import json
from datetime import datetime
from jsonschema import Draft202012Validator
from controlled_conditions import CONFIRMATION_SCHEMA
from planning_capabilities import supports
from routing_capacity import PROFILE, enabled

ACTION='simulate_resource_capacity'
INTENT='设备可用条件试算'
CHANGE_SCHEMA={'type':'object','additionalProperties':False,'properties':{
    **{k:{'type':'string','minLength':1,'maxLength':200} for k in ['resource_id','start_at','end_at']},
    'reason':{'type':'string','minLength':1,'maxLength':1000},'confirmation':CONFIRMATION_SCHEMA},
    'required':['resource_id','start_at','end_at','reason']}

def available(data):return enabled(data) and supports(PROFILE,'actions','resource_capacity')
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

def apply(data,resource_id,start_at,end_at,reason,confirmation=None):
    from drafts import DraftError
    from resource_unavailability import apply_change
    from planning_adapter import validate_input
    args=dict(resource_id=resource_id,start_at=start_at,end_at=end_at,reason=reason,
        **({'confirmation':confirmation} if confirmation is not None else {}))
    if list(Draft202012Validator(CHANGE_SCHEMA).iter_errors(args)) or not reason.strip():raise DraftError('RESOURCE_CAPACITY_INVALID_CONDITIONS')
    if not available(data):raise DraftError('UNSUPPORTED_PROFILE_CAPABILITY: resource_capacity')
    validate_input(data);candidate=copy.deepcopy(data)
    r=next((r for r in candidate['resources'] if r['id']==resource_id),None)
    if r is None or r.get('role')!='MACHINE':raise DraftError('REAL_MACHINE_REQUIRED')
    # Parse and test protected commitments before alternative qualification so
    # a conflict cannot be hidden by an ordinary unsupported-selection message.
    try:
        a,b=[datetime.fromisoformat(v.replace('Z','+00:00')) for v in [start_at,end_at]]
        if a.tzinfo is None or b.tzinfo is None:raise ValueError()
        if not datetime.fromisoformat(data['planning_at'])<=a<b<=datetime.fromisoformat(data['horizon']['end_at']):raise DraftError('INVALID_RESOURCE_RANGE')
    except (ValueError,TypeError) as exc:
        if isinstance(exc,DraftError):raise
        raise DraftError('INVALID_RESOURCE_DATE') from None
    for m in candidate['existing_orders']:
        if m['type']!='MO':continue
        for c in m['operations']:
            if resource_id in {c['resource_id'],c.get('operator_id')} and a<datetime.fromisoformat(c['end_at']) and datetime.fromisoformat(c['start_at'])<b:
                raise DraftError('PROTECTED_MO_RESOURCE_CONFLICT')
    pool=next(x for x in candidate['resources'] if x.get('role')=='MACHINE_POOL')
    required={o['machine_skill_id'] for rt in candidate['routing_revisions'] for o in rt['operations'] if o.get('machine_skill_id') and o['resource_id']==pool['id']}
    if r.get('pool_id')!=pool['id'] or not any(s['resource_id']==r['id'] and s['skill_id'] in required and s['priority']>0 and datetime.fromisoformat(s['effective_from'])<=a<b<=datetime.fromisoformat(s['effective_to']) for s in candidate['resource_skills']):raise DraftError('UNQUALIFIED_RESOURCE')
    change=apply_change(candidate,resource_id,start_at,end_at)
    validate_input(candidate);data.clear();data.update(candidate)
    return dict(action=ACTION,intent=INTENT,reference=resource_id,conditions=dict(resource_id=resource_id,start_at=change['start_at'],end_at=change['end_at']),
        resource_change=change,reason=reason.strip(),confirmation=copy.deepcopy(confirmation),simulation_only=True,
        business_permission='NOT_CONFIRMED',execution_authorized=False,engine_executed=False)

def validate_confirmation(changes,baseline_sha256):
    from drafts import DraftError
    c=changes.get('confirmation')
    if not isinstance(c,dict) or list(Draft202012Validator(CONFIRMATION_SCHEMA).iter_errors(c)):raise DraftError('RESOURCE_CAPACITY_CONFIRMATION_REQUIRED')
    identity=c['identity'];preview=copy.deepcopy(changes);preview['confirmation']=None
    if identity['profile']!=PROFILE or identity['target']!='baseline' or identity['run_id']!=identity['baseline_run_id'] or digest(dict(identity=identity,baseline_sha256=baseline_sha256,changes=preview))!=c['fingerprint']:
        raise DraftError('RESOURCE_CAPACITY_CONFIRMATION_MISMATCH')

def verify(baseline,draft):
    from drafts import DraftError
    if not enabled(baseline):return False
    try:
        changes=draft['changes']
        if changes.get('action')!=ACTION:raise ValueError('unsupported write path')
        validate_confirmation(changes,draft['baseline_sha256'])
        expected=copy.deepcopy(baseline);replayed=apply(expected,reason=changes['reason'],confirmation=changes['confirmation'],**changes['conditions'])
        expected.update(scenario_id=draft['id'],scenario_revision=1)
        args=dict(**changes['conditions'],reason=changes['reason'],confirmation=changes['confirmation'])
        request_hash=hashlib.sha256(json.dumps(dict(name=ACTION,arguments=args),sort_keys=True).encode()).hexdigest()
        if replayed!=changes or expected!=draft['input'] or draft.get('external_request',{}).get('sha256')!=request_hash:raise ValueError('undeclared mutation')
    except (KeyError,ValueError,TypeError,StopIteration) as exc:raise DraftError('RESOURCE_CAPACITY_SCOPE_MISMATCH: 仅允许当前基线指定合格机器的可用时段减少，企业事实与已确认工单不变') from exc
    return True
