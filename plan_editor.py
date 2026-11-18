"""Bounded, replayable manufacturing hypotheses through the existing pipeline.

Existing facts stay immutable. A frozen research contract may give a proposed
task a stable business selector; a runtime OperationPlan number never does so.
"""
import copy,hashlib,json
from datetime import datetime
from decimal import Decimal
from jsonschema import Draft202012Validator
from controlled_conditions import CONFIRMATION_SCHEMA

ACTION='simulate_plan_editor'
AUTHORITY=dict(simulation_only=True,business_permission='NOT_CONFIRMED',execution_authorized=False)
EDIT_SCHEMA={'type':'object','additionalProperties':False,'properties':{
    'kind':{'enum':['window','proposed_window','machine','sequence','optimized_schedule','native_strategy','task','demand']},'reference':{'type':'string','minLength':1,'maxLength':200},
    'changes':{'type':'object'},'action':{'enum':['UNPLAN','FORWARD','BACKWARD']},'demand_ids':{'type':'array','minItems':1,'maxItems':1000,'uniqueItems':True,'items':{'type':'string','minLength':1}},
    'start_at':{'type':'string','maxLength':100},'resource_id':{'type':'string','maxLength':200},
    'assignments':{'type':'array','minItems':1,'maxItems':128,'items':{'type':'object'}},'solver':{'enum':['heuristic','heuristic_2']},'machine_search':{'enum':['PRIORITY','MINPENALTY']},
    'order':{'type':'array','items':{'type':'string'},'minItems':1,'maxItems':24}},'required':['kind','reference']}
CHANGE_SCHEMA={'type':'object','additionalProperties':False,'properties':{
    'edits':{'type':'array','items':EDIT_SCHEMA,'minItems':1,'maxItems':24},
    'reason':{'type':'string','minLength':1,'maxLength':1000},'source_tasks':{'type':'array','maxItems':128,'items':{'type':'object'}},'confirmation':CONFIRMATION_SCHEMA},'required':['edits','reason']}
TEXT={'type':'string','minLength':1,'maxLength':200}
QUANTITY={'type':'string','pattern':'^[0-9]+(\\.[0-9]+)?$'}
STATE={'type':'string','pattern':'^[A-Za-z0-9_-]{1,100}$'}
def record(properties):return dict(type='object',additionalProperties=False,properties=properties,required=list(properties))
TASK_SCHEMA=record(dict(id=TEXT,native_operation=TEXT,quantity=QUANTITY,demand_id=TEXT))
HYPOTHESIS_SCHEMA=record(dict(reference=TEXT,native_operation=TEXT,quantity=QUANTITY,demand_id=TEXT,resource_id=TEXT))
SETUP_SCHEMA=record(dict(resource_id=TEXT,initial=STATE,operation_states=dict(type='object',minProperties=1,additionalProperties=STATE),
    transitions=dict(type='array',maxItems=600,items=record(dict(from_state=STATE,to_state=STATE,seconds=dict(type='integer',minimum=0,maximum=86400)))),source=dict(type='string',minLength=1,maxLength=2000),tools_required=dict(const=False)))
SEQUENCE_SCHEMA=record(dict(demand_ids=dict(type='array',minItems=2,maxItems=24,uniqueItems=True,items=TEXT),goal=dict(enum=['setup_then_delay','delivery_then_setup','least_disturbance']),max_candidates=dict(type='integer',minimum=1,maximum=6),delay_cap_seconds=dict(const=0)))
CONTRACT_SCHEMA=dict(type='object',additionalProperties=False,required=['revision','purpose','source','tasks'],properties=dict(
    revision=dict(const='research-editor-v1'),purpose=dict(const='ISOLATED_RESEARCH'),source=dict(type='string',minLength=1,maxLength=2000),
    tasks=dict(type='array',maxItems=24,items=TASK_SCHEMA),setup=SETUP_SCHEMA,sequence=SEQUENCE_SCHEMA))

def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
def fail(code):
    from drafts import DraftError
    raise DraftError(code)

def reject_quantity_edits(edits):
    if isinstance(edits,list) and any(isinstance(e,dict) and (e.get('kind')=='quantity' or 'quantity' in e) for e in edits):fail('EDITOR_QUANTITY_EDIT_UNSUPPORTED')
def at(v):
    try:
        d=datetime.fromisoformat(v.replace('Z','+00:00'))
        if d.tzinfo is None:raise ValueError()
        return d
    except (TypeError,ValueError,AttributeError):fail('INVALID_DATE')

def contract(data):
    if 'foundation_projection' in data:
        from p22.foundation_editor import contract as enterprise_contract
        return enterprise_contract(data)
    c=data.get('plan_editor_contract')
    if not c:return None
    if list(Draft202012Validator(CONTRACT_SCHEMA).iter_errors(c)):fail('EDITOR_CONTRACT_INVALID')
    if not isinstance(c,dict) or set(c)-{'revision','purpose','source','tasks','setup','sequence'} or c.get('revision')!='research-editor-v1' or c.get('purpose')!='ISOLATED_RESEARCH' or not isinstance(c.get('source'),str) or not c['source'].strip():fail('EDITOR_CONTRACT_INVALID')
    tasks=c.get('tasks',[])
    profile=data['rules'].get('manufacturing_profile','ordinary-routing-v1')
    if profile not in {'routing-alternate-capacity-v1','ordinary-routing-v1'} or (profile=='routing-alternate-capacity-v1' and (c.get('setup') or c.get('sequence'))) or (profile=='ordinary-routing-v1' and tasks):fail('UNSUPPORTED_PROFILE_CAPABILITY: plan_editor contract')
    if not isinstance(tasks,list) or len(tasks)>24 or len({t.get('id') for t in tasks})!=len(tasks):fail('EDITOR_CONTRACT_INVALID')
    specs={p['id']+':'+o['id']:(p,o) for p in data['production_recipes'] for r in data['routing_revisions'] if r['id']==p['routing_revision_id'] for o in r['operations']}
    for t in tasks:
        if set(t)!={'id','native_operation','quantity','demand_id'} or t['native_operation'] not in specs or not t['id'] or Decimal(t['quantity'])<=0:fail('EDITOR_CONTRACT_INVALID')
        # Unique root demand is the business anchor, not a reused native ID.
        ds=[d for d in data['demand_lines'] if d['id']==t['demand_id']]
        if len(data['demand_lines'])!=1 or len(ds)!=1 or ds[0]['item_id']!=specs[t['native_operation']][0]['output_item_id']:fail('EDITOR_AMBIGUOUS_TASK_IDENTITY')
    if c.get('sequence'):
        s=c['sequence']
        if set(s)!={'demand_ids','goal','max_candidates','delay_cap_seconds'} or s['goal'] not in {'setup_then_delay','delivery_then_setup','least_disturbance'} or type(s['max_candidates']) is not int or not 1<=s['max_candidates']<=6 or s['delay_cap_seconds']!=0:fail('EDITOR_SEQUENCE_POLICY_INVALID')
        if len(s['demand_ids'])!=len(set(s['demand_ids'])) or set(s['demand_ids'])!={d['id'] for d in data['demand_lines']}:fail('EDITOR_SEQUENCE_POLICY_INVALID')
        if len({d['uom'] for d in data['demand_lines']})!=1:fail('EDITOR_SEQUENCE_UNITS_INCOMPARABLE')
        if s['goal']=='least_disturbance':fail('EDITOR_DISTURBANCE_GOAL_NOT_CERTIFIED')
    if c.get('setup'):
        s=c['setup'];rs={r['id']:r for r in data['resources']}
        if set(s)!={'resource_id','initial','operation_states','transitions','source','tools_required'} or s['resource_id'] not in rs or s['tools_required'] is not False or not s['source']:fail('EDITOR_SETUP_INCOMPLETE')
        if set(s['operation_states'])!=set(specs) or any(o['resource_id']!=s['resource_id'] for _,o in specs.values()):fail('EDITOR_SETUP_INCOMPLETE')
        states=set(s['operation_states'].values())|{s['initial']}
        if not states or any(not isinstance(v,str) or not v for v in states):fail('EDITOR_SETUP_INCOMPLETE')
        pairs=[(r['from_state'],r['to_state']) for r in s['transitions']]
        expected={(a,b) for a in states for b in states if a!=b}
        if len(pairs)!=len(set(pairs)) or set(pairs)!=expected or any(type(r.get('seconds')) is not int or r['seconds']<0 for r in s['transitions']):fail('EDITOR_SETUP_INCOMPLETE')
    return c

def available(data):
    if 'foundation_projection' in data:return contract(data) is not None
    p=data.get('rules',{}).get('manufacturing_profile','ordinary-routing-v1')
    from planning_capabilities import supports
    return supports(p,'actions','plan_editor') and (p=='fixed-batch-window-v1' or (p in {'routing-alternate-capacity-v1','ordinary-routing-v1'} and contract(data) is not None))

def validate_input(data):
    if 'plan_editor_contract' in data:
        if not available(data):fail('UNSUPPORTED_PROFILE_CAPABILITY: plan_editor')
        contract(data)
    hypothesis=data.get('plan_editor_hypothesis')
    if hypothesis is not None:
        if list(Draft202012Validator(dict(type='array',maxItems=24,items=HYPOTHESIS_SCHEMA)).iter_errors(hypothesis)):fail('EDITOR_HYPOTHESIS_INVALID')
        if len({h['reference'] for h in hypothesis})!=len(hypothesis):fail('EDITOR_DUPLICATE_OBJECT')
        c=contract(data)
        if not c:fail('EDITOR_TASK_NOT_DECLARED')
        for t in hypothesis:
            declared=next((v for v in c['tasks'] if v['id']==t['reference']),None)
            if not declared or any(t[k]!=declared[k] for k in ['native_operation','quantity','demand_id']):fail('EDITOR_TASK_NOT_DECLARED')
            validate_machine(data,declared,t['resource_id'])

def validate_machine(data,t,rid):
    resources={r['id']:r for r in data['resources']};r=resources.get(rid)
    if not r or r.get('role')!='MACHINE':fail('UNQUALIFIED_RESOURCE')
    o=next(o for route in data['routing_revisions'] for o in route['operations'] if t['native_operation'].endswith(':'+o['id']))
    skill=o.get('machine_skill_id')
    if not skill or r.get('pool_id')!=o['resource_id'] or not any(s['resource_id']==rid and s['skill_id']==skill and s['priority']>0 and at(s['effective_from'])<=at(data['planning_at']) and at(s['effective_to'])>=at(data['horizon']['end_at']) for s in data.get('resource_skills',[])):fail('UNQUALIFIED_RESOURCE')

def validate_assignment_preview(data,result,edits):
    """Necessary checks for unchanged native windows; not a feasible-plan claim."""
    resources={r['id']:r for r in data['resources']};calendars={c['id']:c for c in data['calendars']}
    for e in edits:
        if e['kind']!='machine':continue
        t=next(t for t in contract(data)['tasks'] if t['id']==e['reference']);validate_machine(data,t,e['resource_id'])
        recipe,operation=t['native_operation'].rsplit(':',1);parents={m['id']:m for m in result.get('manufacture_suggestions',[]) if m['recipe_id']==recipe}
        rows=[o for o in result.get('operations',[]) if o['operation_id']==operation and o['manufacture_id'] in parents and Decimal(o['quantity'])==Decimal(t['quantity'])]
        if len(rows)!=1:fail('EDITOR_AMBIGUOUS_NATIVE_TASK')
        row=rows[0];a,b=at(row['start_at']),at(row['end_at'])
        spec=next(o for rt in data['routing_revisions'] for o in rt['operations'] if o['id']==operation)
        seconds=Decimal(spec['seconds_per_unit'])*Decimal(row['quantity'])
        def active(rid):return Decimal(str(sum(max(0,(min(b,at(w['end_at']))-max(a,at(w['start_at']))).total_seconds()) for w in calendars[resources[rid]['availability_calendar_id']]['available_intervals'])))
        if active(e['resource_id'])!=seconds:fail('EDITOR_MACHINE_CALENDAR_CONFLICT')
        def conflicts(rid):return any(l['operation_id']!=row['id'] and l['resource_id']==rid and at(l['start_at'])<b and a<at(l['end_at']) for l in result.get('resource_loads',[]))
        if conflicts(e['resource_id']):fail('EDITOR_MACHINE_CAPACITY_CONFLICT')
        people=[l['resource_id'] for l in result.get('resource_loads',[]) if l['operation_id']==row['id'] and resources.get(l['resource_id'],{}).get('role')=='OPERATOR']
        if len(people)!=1:fail('EDITOR_PERSON_EVIDENCE_REQUIRED')
        person=people[0]
        if active(person)!=seconds:fail('EDITOR_PERSON_CALENDAR_CONFLICT')
        if conflicts(person):fail('EDITOR_PERSON_CAPACITY_CONFLICT')

def apply(data,edits,reason,confirmation=None,source_tasks=None):
    from planning_adapter import validate_input as validate
    reject_quantity_edits(edits)
    if list(Draft202012Validator(CHANGE_SCHEMA).iter_errors(dict(edits=edits,reason=reason,**({'confirmation':confirmation} if confirmation is not None else {})))) or not reason.strip():fail('EDITOR_INVALID_CHANGE')
    if len({(e['kind'],e['reference']) for e in edits})!=len(edits):fail('EDITOR_DUPLICATE_OBJECT')
    if 'foundation_projection' in data:
        from p22.foundation_editor import apply as apply_enterprise
        candidate=copy.deepcopy(data);changes=apply_enterprise(candidate,edits,reason,confirmation,source_tasks)
        validate(candidate);data.clear();data.update(candidate);return changes
    if source_tasks is not None:fail('EDITOR_SOURCE_TASK_UNSUPPORTED')
    if any(e['kind'] in {'optimized_schedule','native_strategy'} for e in edits):fail('OPTIMIZER_RESEARCH_SCOPE_REQUIRED')
    # Explicit field whitelist per action, even though the union schema is shared.
    for e in edits:
        fields={'window':{'kind','reference','start_at'},'machine':{'kind','reference','resource_id'},'sequence':{'kind','reference','order'}}[e['kind']]
        if set(e)!=fields:fail('EDITOR_INVALID_CHANGE')
    if len({(e['kind'],e['reference']) for e in edits})!=len(edits):fail('EDITOR_DUPLICATE_OBJECT')
    candidate=copy.deepcopy(data);events=[]
    for e in edits:
        if e['kind']=='window':
            if candidate['rules'].get('manufacturing_profile')!='fixed-batch-window-v1':fail('UNSUPPORTED_PROFILE_CAPABILITY: plan_editor window')
            from approved_simulation import apply as move
            at(e['start_at'])
            event=move(candidate,e['reference'],at(e['start_at']).isoformat(),acknowledged_at=candidate['planning_at'])
            events.append(dict(kind='window',reference=e['reference'],before=event['before'],after=event['after']))
        elif e['kind']=='machine':
            c=contract(candidate);t=next((t for t in (c or {}).get('tasks',[]) if t['id']==e['reference']),None)
            if not t:fail('EDITOR_TASK_NOT_DECLARED')
            if candidate['rules'].get('manufacturing_profile')!='routing-alternate-capacity-v1':fail('UNSUPPORTED_PROFILE_CAPABILITY: plan_editor machine')
            validate_machine(candidate,t,e['resource_id'])
            h=dict(reference=t['id'],**{k:t[k] for k in ['native_operation','quantity','demand_id']},resource_id=e['resource_id'])
            candidate.setdefault('plan_editor_hypothesis',[]).append(h);events.append(dict(kind='machine',**h))
        else:
            c=contract(candidate);s=(c or {}).get('sequence')
            if not s or e['reference']!='manufacturing-sequence' or len(e['order'])!=len(set(e['order'])) or set(e['order'])!=set(s['demand_ids']):fail('EDITOR_SEQUENCE_POLICY_INVALID')
            before=[d['id'] for d in sorted(candidate['demand_lines'],key=lambda d:d['priority'])]
            if before==e['order']:fail('EDITOR_UNCHANGED')
            ranks={v:i+1 for i,v in enumerate(e['order'])}
            for d in candidate['demand_lines']:d['priority']=ranks[d['id']]
            events.append(dict(kind='sequence',reference=e['reference'],before=before,after=e['order'],goal=s['goal']))
    # All windows are checked together, including unchanged protected orders.
    if candidate['rules'].get('manufacturing_profile')=='fixed-batch-window-v1':
        for resource in candidate['resources']:
            calendar=next(c for c in candidate['calendars'] if c['id']==resource['availability_calendar_id'])
            for window in calendar['available_intervals']:
                amount=sum((Decimal(m['quantity'])*Decimal(o['resource_units']) for m in candidate['existing_orders'] if m['type']=='MO' for o in m['operations'] if o['resource_id']==resource['id'] and at(window['start_at'])<=at(o['start_at'])<at(window['end_at'])),Decimal(0))
                if amount>Decimal(resource['window_capacity_quantity']):fail('EDITOR_RESOURCE_CONFLICT')
    validate(candidate)
    result=dict(action=ACTION,intent='专业排程联合调整',edits=copy.deepcopy(edits),events=events,reason=reason.strip(),confirmation=copy.deepcopy(confirmation),**AUTHORITY)
    data.clear();data.update(candidate);return result

def validate_confirmation(changes,baseline_sha256):
    c=changes.get('confirmation')
    if not isinstance(c,dict) or list(Draft202012Validator(CONFIRMATION_SCHEMA).iter_errors(c)):fail('EDITOR_CONFIRMATION_REQUIRED')
    p=copy.deepcopy(changes);p['confirmation']=None;i=c['identity']
    if i['target']!='baseline' or i['run_id']!=i['baseline_run_id'] or digest(dict(identity=i,baseline_sha256=baseline_sha256,changes=p))!=c['fingerprint']:fail('EDITOR_CONFIRMATION_MISMATCH')

def verify(baseline,draft):
    changes=draft.get('changes')
    if not isinstance(changes,dict):
        if draft.get('action_owner')==ACTION or 'plan_editor_hypothesis' in draft.get('input',{}):fail('EDITOR_SCOPE_MISMATCH')
        return False
    if changes.get('action')!=ACTION:
        if draft.get('action_owner')==ACTION or 'edits' in changes or 'plan_editor_hypothesis' in draft.get('input',{}):fail('EDITOR_SCOPE_MISMATCH')
        return False
    try:
        c=draft['changes'];validate_confirmation(c,draft['baseline_sha256']);expected=copy.deepcopy(baseline)
        replay=apply(expected,c['edits'],c['reason'],c['confirmation'],c.get('source_tasks'));expected.update(scenario_id=draft['id'],scenario_revision=1)
        if replay!=c or expected!=draft['input']:fail('EDITOR_SCOPE_MISMATCH')
        args=dict(edits=c['edits'],reason=c['reason'],confirmation=c['confirmation'])
        if 'source_tasks' in c:args['source_tasks']=c['source_tasks']
        request_hash=hashlib.sha256(json.dumps(dict(name=ACTION,arguments=args),sort_keys=True).encode()).hexdigest()
        if draft.get('external_request',{}).get('sha256')!=request_hash:fail('EDITOR_SCOPE_MISMATCH')
    except (KeyError,TypeError):fail('EDITOR_SCOPE_MISMATCH')
    return True

def sequence_candidates(rows,budget):
    if type(budget) is not int or not 1<=budget<=6:fail('EDITOR_SEARCH_BUDGET_INVALID')
    original=[r['id'] for r in rows];result=[]
    priorities=sorted({r.get('priority',0) for r in rows})
    variants=[sorted(rows,key=lambda r:(r.get('priority',0),r['family'])),[r for p in priorities for r in sorted((r for r in rows if r.get('priority',0)==p),key=lambda r:r['family'],reverse=True)]]
    for i in range(len(rows)-1):
        if rows[i].get('priority',0)!=rows[i+1].get('priority',0):continue
        v=rows[:];v[i],v[i+1]=v[i+1],v[i];variants.append(v)
    for v in variants:
        order=[r['id'] for r in v]
        if order!=original and order not in result:result.append(order)
        if len(result)>=budget:break
    return result

def map_native(data,native):
    if 'foundation_projection' in data:return native
    c=contract(data)
    if not c:return native
    if c.get('setup'):
        from planning_adapter import duration
        s=c['setup'];native['setupmatrix']=[dict(name='EDITOR-SETUP')]
        native['setuprule']=[dict(setupmatrix='EDITOR-SETUP',priority=i+1,fromsetup=r['from_state'],tosetup=r['to_state'],duration=duration(r['seconds']),cost='0') for i,r in enumerate(s['transitions'])]
        for r in native['resource']:
            if r['name']==s['resource_id']:r.update(setup=s['initial'],setupmatrix='EDITOR-SETUP')
        for load in native['operationresource']:
            if load['resource']==s['resource_id']:load['setup']=s['operation_states'][load['operation']]
        # Import FK order: matrix before resource, rules immediately afterwards.
        order=['parameter','location','customer','item','calendar','calendarbucket','setupmatrix','setuprule','resource','skill','resourceskill','operation','operationresource','operationmaterial','buffer','supplier','itemsupplier','demand']
        native={k:native[k] for k in order+list(native) if k in native}
    native['parameter']=[p for p in native['parameter'] if p['name']!='plan.move_approved_early']+[dict(name='plan.move_approved_early',value='0')]
    return native

def audit_native(data,raw,*,run_id):
    if 'foundation_projection' in data and data['foundation_projection'].get('editor_scope'):
        from p22.foundation_editor import audit_native as enterprise_audit
        return enterprise_audit(data,raw,run_id)
    from calculation_validation import evidence_rows
    from zoneinfo import ZoneInfo
    c=contract(data)
    if not c:return dict(status='PASS',issues=[],missing_evidence=[],setup_seconds=None,scope='NO_EDITOR_NATIVE_CONDITIONS')
    issues=[];missing=[];total=None;observed=[];sequence=[];zone=ZoneInfo(data['site']['timezone'])
    def native_at(v):
        d=datetime.fromisoformat(v);return d.replace(tzinfo=zone) if d.tzinfo is None else d.astimezone(zone)
    def check(ok,code,ref=None):
        if not ok:issues.append(dict(code=code,reference=ref))
    post=raw.get('fixed_batch_post');pre=raw.get('fixed_batch_pre')
    for phase,value in [('pre',pre),('post',post)]:
        if not isinstance(value,dict):missing.append(phase);continue
        check(value.get('run_id')==run_id and value.get('input_hash')==digest(data),'EDITOR_NATIVE_IDENTITY',phase)
        check(value.get('move_approved_early')==0,'EDITOR_PROTECTION',phase)
    for h in data.get('plan_editor_hypothesis',[]):
        rows=[r for table in ['manufacturingorder','workorder'] for r in evidence_rows(raw.get(table)) or [] if r.get('operation')==h['native_operation'] and Decimal(str(r.get('quantity',0)))==Decimal(h['quantity'])]
        check(len(rows)==1,'EDITOR_AMBIGUOUS_NATIVE_TASK',h['reference'])
        if len(rows)!=1:continue
        loads=evidence_rows(raw.get('operationplanresource'))
        if loads is None:missing.append('operationplanresource');continue
        assigned=[r for r in loads if str(r['operationplan'])==str(rows[0]['reference'])]
        check(any(r['resource']==h['resource_id'] for r in assigned),'EDITOR_ACTUAL_MACHINE_MISMATCH',h['reference'])
        observed.append(dict(reference=h['reference'],native_reference=str(rows[0]['reference']),resources=[r['resource'] for r in assigned]))
    if c.get('setup') and isinstance(post,dict) and isinstance(pre,dict):
        s=c['setup'];model=pre.get('editor_model',{});rs=model.get('resources',[])
        matrix=model.get('setup_matrices',[]);resource=next((r for r in rs if r['name']==s['resource_id']),None)
        if resource is None or not matrix:missing.append('native_setup_model')
        else:
            check(resource.get('setup')==s['initial'] and resource.get('setupmatrix')=='EDITOR-SETUP','EDITOR_INITIAL_SETUP')
            rules=next((m['rules'] for m in matrix if m['name']=='EDITOR-SETUP'),None)
            check(rules is not None and {(r['from_state'],r['to_state'],Decimal(r['duration_seconds'])) for r in rules}=={(r['from_state'],r['to_state'],Decimal(r['seconds'])) for r in s['transitions']},'EDITOR_SETUP_MODEL_MISMATCH')
        jobs=[p for p in post.get('operationplans',[]) if p['operation'] in s['operation_states']]
        if not jobs:missing.append('native_setup_jobs')
        else:
            state=s['initial'];total=Decimal(0);transitions={(r['from_state'],r['to_state']):r['seconds'] for r in s['transitions']}
            for p in sorted(jobs,key=lambda p:(native_at(p['start']),p['reference'])):
                try:
                    required=s['operation_states'][p['operation']];expected=0 if state==required else transitions[(state,required)]
                    actual=Decimal(p['setup_duration_seconds']);check(actual==expected,'EDITOR_SETUP_SEQUENCE',p['reference'])
                    a,b=native_at(p['start']),native_at(p['setup_end']);res=next(r for r in data['resources'] if r['id']==s['resource_id']);cal=next(c for c in data['calendars'] if c['id']==res['availability_calendar_id'])
                    active=sum(max(0,(min(b,at(i['end_at']))-max(a,at(i['start_at']))).total_seconds()) for i in cal['available_intervals'])
                    check(Decimal(str(active))==actual,'EDITOR_SETUP_CALENDAR',p['reference'])
                    check(s['resource_id'] in p['resources'],'EDITOR_SETUP_MACHINE',p['reference'])
                    check(p.get('feasible') is True,'EDITOR_NATIVE_INFEASIBLE',p['reference'])
                    total+=actual;state=required
                    sequence.append(dict(native_reference=p['reference'],operation=p['operation'],state=required,quantity=p['quantity'],
                        start_at=native_at(p['start']).isoformat(),setup_end_at=native_at(p['setup_end']).isoformat(),end_at=native_at(p['end']).isoformat(),setup_seconds=str(actual),resource_id=s['resource_id']))
                except (KeyError,TypeError,ValueError):missing.append('setup_job:'+str(p.get('reference')))
    return dict(status='FAIL' if issues else 'NOT_CHECKED' if missing else 'PASS',issues=issues,missing_evidence=missing,
        setup_seconds=str(total) if total is not None and not missing and not issues else None,assignments=observed,setup_sequence=sequence,
        scope='FROZEN_RESEARCH_EDITOR_NATIVE_CONDITIONS',execution_authorized=False)

def comparison(base,view,changes):
    """Pair only declared business identities; native references remain run-local."""
    rows=[]
    def existing(v,ref):
        found=[m for m in v['result'].get('existing_manufacturing_orders',[]) if m['engine_ref']==ref]
        if len(found)!=1:return None
        m=found[0];ops=[o for o in v['result']['operations'] if o['manufacture_id']==m['id']]
        return dict(reference=ref,quantity=m['quantity'],uom=m['uom'],source_status=m['source_status'],operations=ops)
    events=[]
    for e in changes['events']:
        if e['kind']=='optimized_schedule':events.extend(dict(a,kind='optimized_schedule') for a in e['assignments'])
        else:events.append(e)
    for e in events:
        if e['kind']=='demand':
            for did in e['demand_ids']:
                rows.append(dict(reference=did,kind='demand',action=e['action'],demand_id=did,base=next((d for d in base['result']['deliveries'] if d['demand_id']==did),None),candidate=next((d for d in view['result']['deliveries'] if d['demand_id']==did),None)))
            continue
        if 'source_task' in e:
            t=e['source_task'];record=next((r for r in view.get('interpretation',{}).get('plan_editor_validation',{}).get('assignments',[]) if r['reference']==e['reference']),None)
            def observed(v,ref):
                os=[o for o in v['result']['operations'] if o['id'].rsplit(':',1)[-1]==ref]
                return dict(operations=os,resources=[r['resource_id'] for r in v['result']['resource_loads'] if os and r['operation_id']==os[0]['id']],quantity=os[0]['quantity'],recipe_id=next((m['recipe_id'] for m in v['result']['manufacture_suggestions'] if m['id']==os[0]['manufacture_id']),None)) if len(os)==1 else None
            rows.append(dict(reference=e['reference'],kind=e['kind'],base=observed(base,t['source_native_reference']) if base['run']['solve_run_id']==t['baseline_run_id'] else None,candidate=observed(view,record['after']['reference']) if record else None))
        elif e['kind']=='window':rows.append(dict(reference=e['reference'],kind='window',base=existing(base,e['reference']),candidate=existing(view,e['reference'])))
        elif e['kind']=='machine':
            def task(v):
                recipe,op=e['native_operation'].rsplit(':',1);parents={m['id'] for m in v['result']['manufacture_suggestions'] if m['recipe_id']==recipe}
                os=[o for o in v['result']['operations'] if o['manufacture_id'] in parents and o['operation_id']==op and Decimal(o['quantity'])==Decimal(e['quantity'])]
                if len(os)!=1:return None
                return dict(operations=os,resources=[r['resource_id'] for r in v['result']['resource_loads'] if r['operation_id']==os[0]['id']],quantity=e['quantity'])
            rows.append(dict(reference=e['reference'],kind='machine',demand_id=e['demand_id'],base=task(base),candidate=task(view)))
    a=base.get('interpretation',{}).get('plan_editor_validation',{});b=view.get('interpretation',{}).get('plan_editor_validation',{})
    known=a.get('status')==b.get('status')=='PASS' and a.get('setup_seconds') is not None and b.get('setup_seconds') is not None
    return dict(stable_objects=rows,declared_changes=copy.deepcopy(changes['events']),
        setup=dict(status='CHECKED' if known else 'NOT_CERTIFIED',base_seconds=a.get('setup_seconds'),candidate_seconds=b.get('setup_seconds'),
            saved_seconds=str(Decimal(a['setup_seconds'])-Decimal(b['setup_seconds'])) if known else None,
            base_sequence=a.get('setup_sequence',[]),candidate_sequence=b.get('setup_sequence',[])),
        optimization_certification=__import__('optimization.business',fromlist=['compare']).compare(base,view) if any(e['kind']=='optimized_schedule' for e in changes['events']) else None,
        edited_business_objects=len(rows),proposed_mo_disturbance_count=None,proposed_identity_limit='No cross-run matching of temporary proposed operationplan numbers',**AUTHORITY)
