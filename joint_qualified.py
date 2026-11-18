"""Finite PRO01 + qualified SEW composition; CUT has no personnel claim."""
import copy
from datetime import datetime
from decimal import Decimal

PROFILE='mixed-fixed-batch-confirmed-qualified-v1'
CUT_PROFILE='mixed-fixed-batch-confirmed-cut-qualified-v1'
def enabled(data):return data.get('rules',{}).get('manufacturing_profile') in {PROFILE,CUT_PROFILE}
def cut_enabled(data):return data.get('rules',{}).get('manufacturing_profile')==CUT_PROFILE

def manufacturing_view(data):
    """Validation-only scope projection. Never submitted, persisted or solved."""
    d=copy.deepcopy(data);d['rules']['manufacturing_profile']='mixed-fixed-batch-confirmed-supply-v1'
    d.pop('skills',None);d.pop('resource_skills',None)
    d['resources']=[r for r in d['resources'] if r.get('role')=='MACHINE']
    for r in d['resources']:r.pop('role',None);r.pop('pool_id',None)
    for route in d['routing_revisions']:
        for op in route['operations']:op.pop('operator_requirement',None)
    return d

def validate(data):
    from planning_adapter import InputError
    from mixed_batch_confirmed_supply import validate as validate_manufacturing
    def require(ok,msg):
        if not ok:raise InputError('JOINT_QUALIFIED_UNSUPPORTED: '+msg)
    validate_manufacturing(manufacturing_view(data))
    resources=data['resources'];pools=[r for r in resources if r.get('role')=='OPERATOR_POOL'];people=[r for r in resources if r.get('role')=='OPERATOR'];machines=[r for r in resources if r.get('role')=='MACHINE']
    require(len(pools)==1 and 1<=len(people)<=8 and len(machines)==2 and len(resources)==3+len(people),'two declared machines, one pool and one/eight named people')
    pool=pools[0];cal={c['id']:c for c in data['calendars']};work=cal[pool['availability_calendar_id']]
    require(pool.get('parallel_units')=='1' and bool(work['available_intervals']),'explicit unit working pool')
    for r in resources:
        require(r['location_id']==data['site']['id'],'single site resources')
        if r.get('capacity_model')=='WINDOW_QUANTITY':continue
        require(r.get('parallel_units')=='1' and r.get('capacity_model','CONTINUOUS')=='CONTINUOUS','unit continuous SEW/person resources')
        require((r.get('pool_id')==pool['id']) if r['role']=='OPERATOR' else 'pool_id' not in r,'named pool membership only')
        for w in cal[r['availability_calendar_id']]['available_intervals']:
            require(any(datetime.fromisoformat(a['start_at'])<=datetime.fromisoformat(w['start_at'])<datetime.fromisoformat(w['end_at'])<=datetime.fromisoformat(a['end_at']) for a in work['available_intervals']),'individual windows within pool work envelope')
    specs=[o for route in data['routing_revisions'] for o in route['operations']];sew=[o for o in specs if o['duration_model']=='FIXED_PLUS_PER_UNIT'];cut=[o for o in specs if o['duration_model']=='FIXED_BATCH']
    require(len(sew)==len(cut)==1,'one fixed CUT and one continuous SEW')
    require(cut_enabled(data) or 'operator_requirement' not in cut[0],'original joint profile only supports SEW staffing')
    rq=sew[0].get('operator_requirement',{});skills={s['id'] for s in data.get('skills',[])}
    require(bool(skills) and len(skills)==len(data.get('skills',[])) and rq.get('pool_id')==pool['id'] and rq.get('quantity')=='1' and rq.get('skill_id') in skills,'explicit one-person SEW skill requirement')
    require(sew[0]['resource_id'] in {m['id'] for m in machines} and sew[0]['resource_units']=='1','specified SEW machine')
    seen=set()
    for s in data.get('resource_skills',[]):
        key=s['resource_id'],s['skill_id'];require(key not in seen,'duplicate qualification');seen.add(key)
        require(s['resource_id'] in {r['id'] for r in people} and s['skill_id'] in skills,'unknown person or skill')
        require(datetime.fromisoformat(s['effective_from'])<=datetime.fromisoformat(data['planning_at']) and datetime.fromisoformat(s['effective_to'])>=datetime.fromisoformat(data['horizon']['end_at']),'qualification covers whole horizon')
    require(any(s['skill_id']==rq['skill_id'] for s in data.get('resource_skills',[])),'no evidenced qualified person')
    if cut_enabled(data):
        cr=cut[0].get('operator_requirement',{})
        require(cr.get('pool_id')==pool['id'] and cr.get('quantity')=='1' and cr.get('skill_id') in skills,'CUT requires one explicitly qualified person')
        require(cut[0]['resource_id'] in {m['id'] for m in machines} and cut[0]['resource_units']=='1','specified bucket CUT machine')
        require(any(s['skill_id']==cr['skill_id'] for s in data.get('resource_skills',[])),'no evidenced CUT-qualified person')
