"""One continuous resource, subtraction only, in an isolated P3 scenario."""
import copy,hashlib,json
from datetime import datetime
from zoneinfo import ZoneInfo

INTENT='资源不可用模拟'
from planning_capabilities import profiles_for
PROFILES=profiles_for('actions','resource_unavailability')

def apply(data,resource_id,start_at,end_at):
    from drafts import DraftError
    if data['rules'].get('manufacturing_profile') not in PROFILES:
        raise DraftError('UNSUPPORTED_RESOURCE_SIMULATION_PROFILE')
    return apply_change(data,resource_id,start_at,end_at)

def apply_change(data,resource_id,start_at,end_at):
    """Pure calendar subtraction; caller owns its explicit profile/action gate."""
    from drafts import DraftError
    def require(ok,code):
        if not ok:raise DraftError(code)
    rows=[r for r in data['resources'] if r['id']==resource_id]
    require(len(rows)==1,'UNKNOWN_RESOURCE')
    resource=rows[0]
    require(resource.get('role') in {'MACHINE','OPERATOR'} and resource.get('capacity_model','CONTINUOUS')=='CONTINUOUS','UNSUPPORTED_RESOURCE_REDUCTION')
    from routing_capacity import enabled as routing_enabled
    if not routing_enabled(data):require(not any(o['type']=='MO' for o in data['existing_orders']),'PROTECTED_MO_RESOURCE_CONFLICT')
    zone=ZoneInfo(data['site']['timezone'])
    try:
        a=datetime.fromisoformat(start_at);b=datetime.fromisoformat(end_at)
        require(a.tzinfo is not None and b.tzinfo is not None,'INVALID_RESOURCE_DATE')
        a=a.astimezone(zone);b=b.astimezone(zone)
        require(datetime.fromisoformat(data['planning_at'])<=a<b<=datetime.fromisoformat(data['horizon']['end_at']),'INVALID_RESOURCE_RANGE')
    except (ValueError,TypeError):raise DraftError('INVALID_RESOURCE_DATE') from None
    if routing_enabled(data):
        for mo in data['existing_orders']:
            for c in mo.get('operations',[]):
                require(not (resource_id in {c['resource_id'],c.get('operator_id')} and a<datetime.fromisoformat(c['end_at']) and datetime.fromisoformat(c['start_at'])<b),'PROTECTED_MO_RESOURCE_CONFLICT')
    original=next(c for c in data['calendars'] if c['id']==resource['availability_calendar_id'])
    windows=[];removed=0
    for window in original['available_intervals']:
        x=datetime.fromisoformat(window['start_at']);y=datetime.fromisoformat(window['end_at'])
        if y<=a or x>=b:windows.append(copy.deepcopy(window));continue
        removed+=(min(y,b)-max(x,a)).total_seconds()
        if x<a:windows.append({**window,'end_at':a.isoformat()})
        if y>b:windows.append({**window,'start_at':b.isoformat()})
    require(removed>0,'NO_AVAILABLE_TIME_REMOVED')
    digest=hashlib.sha256(json.dumps([resource_id,a.isoformat(),b.isoformat(),original['id']]).encode()).hexdigest()[:16]
    calendar=copy.deepcopy(original);calendar.update(id='CAL-SIM-'+digest,available_intervals=windows)
    require(not any(c['id']==calendar['id'] for c in data['calendars']),'SIMULATION_CALENDAR_COLLISION')
    calendar['provenance']={**calendar['provenance'],'source_record_id':'SIM-'+digest,'source_locator':'p3-resource-unavailability:'+resource_id}
    resource['availability_calendar_id']=calendar['id'];data['calendars'].append(calendar)
    return {'intent':INTENT,'resource_id':resource_id,'reference':resource_id,'start_at':a.isoformat(),'end_at':b.isoformat(),
            'before_calendar_id':original['id'],'after_calendar_id':calendar['id'],'before_intervals':copy.deepcopy(original['available_intervals']),
            'after_intervals':copy.deepcopy(windows),'removed_seconds':int(removed),
            'simulation_only':True,'business_permission':'NOT_CONFIRMED','execution_authorized':False}

def verify(baseline,draft):
    from drafts import DraftError
    changes=draft['changes']
    if changes.get('intent')!=INTENT:return
    try:
        expected=copy.deepcopy(baseline)
        recreated=apply(expected,changes['resource_id'],changes['start_at'],changes['end_at'])
        expected.update(scenario_id=draft['id'],scenario_revision=1)
        if recreated!=changes or expected!=draft['input']:raise ValueError()
    except (KeyError,TypeError,ValueError) as error:
        raise DraftError('RESOURCE_SCOPE_MISMATCH: 不可用草案超出指定资源可用时段的减少范围') from error
