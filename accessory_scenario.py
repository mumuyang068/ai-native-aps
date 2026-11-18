"""A single simulation permission over frozen source terms, with full replay."""
import copy
from planning_capabilities import supports, profile_id
ACTION='enable_accessory_supply'
INTENT='有来源辅料补给模拟'
CHANGE_SCHEMA={'type':'object','additionalProperties':False,'properties':{'source_id':{'type':'string','minLength':1,'maxLength':200},'reason':{'type':'string','minLength':1,'maxLength':1000}},'required':['source_id','reason']}

def apply(data,source_id,reason):
    from drafts import DraftError
    from accessory_supply import enabled,validate
    if not enabled(data) or not supports(profile_id(data),'actions','accessory_supply'):
        raise DraftError('UNSUPPORTED_PROFILE_CAPABILITY: 当前模型未认证辅料补给模拟')
    validate(data)
    option=data['accessory_supply_option']
    if source_id!=option['source_id']:raise DraftError('ACCESSORY_SOURCE_MISMATCH')
    if not isinstance(reason,str) or not reason.strip() or len(reason)>1000:raise DraftError('ACCESSORY_REASON_REQUIRED')
    if option['enabled']:raise DraftError('ACCESSORY_ALREADY_ENABLED')
    option['enabled']=True
    return dict(action=ACTION,intent=INTENT,source_id=source_id,reason=reason.strip(),before={'enabled':False},after={'enabled':True},simulation_only=True)

def verify(baseline,draft):
    from drafts import DraftError
    from firm_scenario import ACTION as FIRM_ACTION, verify as verify_firm
    if draft.get('changes',{}).get('action') == FIRM_ACTION:
        verify_firm(baseline,draft)
        return
    from purchase_delay_scenario import ACTION as PURCHASE_DELAY, verify as verify_purchase_delay
    if draft.get('changes',{}).get('action')==PURCHASE_DELAY:
        verify_purchase_delay(baseline,draft)
        return
    from accessory_supply import enabled
    changes=draft.get('changes',{});actual=draft.get('input',{})
    if not enabled(baseline) and changes.get('action')!=ACTION and baseline.get('accessory_supply_option')==actual.get('accessory_supply_option'):return
    try:
        if changes.get('action')!=ACTION:raise ValueError('missing declared action')
        expected=copy.deepcopy(baseline)
        recreated=apply(expected,changes['source_id'],changes['reason'])
        expected.update(scenario_id=draft['id'],scenario_revision=1)
        if recreated!=changes or actual!=expected:raise ValueError('undeclared field mutation')
    except (KeyError,ValueError,TypeError) as error:
        raise DraftError('ACCESSORY_SUPPLY_SCOPE_MISMATCH: 只允许启用指定冻结来源，不能改原事实或其他条件') from error
