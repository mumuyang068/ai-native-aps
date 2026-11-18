"""Build a finite scheduling problem without removing enterprise relationships.

Production quantities, routing topology, purchases and existing deliveries stay
fixed. Selected leaf dates and eligible machines may change. Every captured
material event remains in the reservoir, including WIP and QC-released supply.
"""
import copy
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
from plan_editor import digest
from p22.foundation_editor import source_tasks,setup_metric

def fail(code):raise ValueError('OPT_'+code)
def intersect(left,right):
 return [(max(a,c),min(b,d)) for a,b in left for c,d in right if max(a,c)<min(b,d)]
def seconds_in(windows,a,b):return sum(max(0,min(b,y)-max(a,x)) for x,y in windows)

def build_problem(data,view,post,orders,scope='setup_and_downstream'):
 if scope not in {'setup_only','setup_and_downstream'}:fail('SCOPE_UNSUPPORTED')
 rid=view['run']['solve_run_id'];interpretation=view.get('interpretation',{})
 if view['run']['state']!='SUCCEEDED' or post.get('run_id')!=rid or post.get('input_hash')!=digest(data) or view['result']['input_hash']!=digest(data):fail('SOURCE_IDENTITY')
 if interpretation.get('schedule_check')!='PASS' or interpretation.get('material_validation',{}).get('status')!='PASS' or interpretation.get('foundation_validation',{}).get('status')!='PASS':fail('BASELINE_AUDIT_REQUIRED')
 if not isinstance(post.get('buffers'),list) or not post['buffers']:fail('BUFFER_EVIDENCE_REQUIRED')
 try:measured=setup_metric(data,post)
 except (KeyError,ValueError,ArithmeticError):fail('SETUP_EVIDENCE')
 zone=ZoneInfo(data['site']['timezone']);epoch=datetime.fromisoformat(data['planning_at']).astimezone(zone)
 def tick(value):
  v=datetime.fromisoformat(value.replace('Z','+00:00'))
  if v.tzinfo is None:v=v.replace(tzinfo=zone)
  seconds=(v-epoch).total_seconds()
  if seconds!=int(seconds):fail('SUBSECOND_TIME_UNSUPPORTED')
  return int(seconds)
 resources={r['id']:r for r in data['resources']};calendars={c['id']:[(tick(w['start_at']),tick(w['end_at'])) for w in c['available_intervals']] for c in data['calendars']};horizon=max(b for windows in calendars.values() for a,b in windows)
 meta=data['foundation_projection'];mapping=meta['native_operation_map'];specs={o['id']:o for r in data['routing_revisions'] for o in r['operations']};setup_states=meta['operation_setups'];tasks=source_tasks(data,view);by_ref={t['source_native_reference']:t for t in tasks};api={r['reference']:r for r in orders};jobs=[]
 def qualified(rid,skill,windows):
  if not skill:return windows
  valid=[(tick(q['effective_from']),tick(q['effective_to'])) for q in data['resource_skills'] if q['resource_id']==rid and q['skill_id']==skill]
  return intersect(windows,valid)
 for row in post['operationplans']:
  identity=mapping.get(row['operation'])
  if not identity or not row.get('resources'):continue
  ref=row['reference'];o=specs[identity['operation_id']];machines=[r for r in row['resources'] if resources.get(r,{}).get('role')=='MACHINE'];people=[r for r in row['resources'] if resources.get(r,{}).get('role')=='OPERATOR']
  if len(machines)!=1 or len(people)!=1 or len(row['resources'])!=2:fail('RESOURCE_COMBINATION_UNMAPPED')
  machine,person=machines[0],people[0]
  if any(Decimal(resources[r].get('parallel_units','1'))!=1 for r in row['resources']):fail('PARALLEL_CAPACITY_UNMAPPED')
  quantity=Decimal(row['quantity']);work=Decimal(o['fixed_seconds'])+(Decimal(o['seconds_per_unit'])*quantity if o['duration_model']!='FIXED_BATCH' else 0)
  if work!=int(work) or work<=0:fail('PROCESSING_DURATION_UNMAPPED')
  state=setup_states.get(o['id']);selected=row['status']=='proposed' and (state is not None or scope=='setup_and_downstream' and (o['id'].startswith('QC-') or o['id'].startswith('PACK-')))
  if selected and ref not in by_ref:fail('AMBIGUOUS_SOURCE_TASK')
  candidates=[machine]
  if state and selected:
   candidates=[r['id'] for r in data['resources'] if r.get('role')=='MACHINE' and r.get('pool_id')==o['resource_id']]
  modes=[]
  for rid2 in candidates:
   windows=calendars[resources[rid2]['availability_calendar_id']];windows=qualified(rid2,o.get('machine_skill_id'),windows);windows=intersect(windows,qualified(person,o.get('operator_requirement',{}).get('skill_id'),calendars[resources[person]['availability_calendar_id']]))
   windows=sorted(set((max(0,a),min(horizon,b)) for a,b in windows if max(0,a)<min(horizon,b)))
   if windows:modes.append(dict(machine=rid2,person=person,windows=windows))
  if not modes or machine not in [m['machine'] for m in modes]:fail('BASELINE_QUALIFICATION_UNMAPPED')
  task=by_ref.get(ref);jid=task['id'] if task else 'LOCKED:'+ref
  j=dict(id=jid,native_ref=ref,native_operation=row['operation'],operation_id=o['id'],recipe_id=identity['recipe_id'],quantity=str(quantity),resources=sorted(row['resources']),person=person,machine=machine,state=state,processing_seconds=int(work),start=tick(row['start']),setup_end=tick(row['setup_end']),end=tick(row['end']),fixed=not selected,modes=modes,source_task=copy.deepcopy(task),owner=api.get(ref,{}).get('owner'),status=row['status'])
  actual=next(m for m in modes if m['machine']==machine)
  if seconds_in(actual['windows'],j['setup_end'],j['end'])!=int(work):fail('BASELINE_PROCESSING_CALENDAR_MISMATCH')
  jobs.append(j)
 if not jobs or len({j['id'] for j in jobs})!=len(jobs):fail('NO_UNIQUE_JOBS')
 refs={j['native_ref']:j for j in jobs};buffer_events=[]
 for buffer in post['buffers']:
  if not isinstance(buffer.get('events'),list):fail('BUFFER_EVENTS_MISSING')
  for e in buffer['events']:
   q=Decimal(e['quantity'])
   if not q.is_finite():fail('MATERIAL_QUANTITY_INVALID')
   if not q:continue
   j=refs.get(e['reference']);point=None
   if j:
    point='setup_end' if q<0 else 'end'
    if tick(e['at'])!=j[point]:fail('MATERIAL_EVENT_POINT_UNMAPPED')
   buffer_events.append(dict(buffer=buffer['name'],item=buffer['item'],location=buffer['location'],job=j['id'] if j else None,point=point,at=max(0,tick(e['at'])),quantity=str(q)))
 # Materials may be swapped within the same item buffer, but recipe routing
 # precedence is explicit and cannot be replaced by aggregate inventory.
 precedences=[]
 for j in jobs:
  o=specs[j['operation_id']]
  for pred in o.get('predecessors',[]):
   matches=[x for x in jobs if x['owner']==j['owner'] and x['owner'] is not None and x['operation_id']==pred and x['recipe_id']==j['recipe_id']]
   if len(matches)!=1:fail('ROUTING_PRECEDENCE_UNMAPPED')
   precedences.append([matches[0]['id'],j['id']])
 deliveries=copy.deepcopy(view['result']['deliveries'])
 if len(deliveries)!=len(data['demand_lines']) or {x['demand_id'] for x in deliveries}!={x['id'] for x in data['demand_lines']}:fail('DELIVERY_COVERAGE')
 problem=dict(revision='aps-opt-problem-v1',baseline_run_id=rid,input_hash=digest(data),source_fact_sha256=digest(meta['source_tables']),source_fact_count=sum(len(v) for v in meta['source_tables'].values()),timezone=data['site']['timezone'],epoch=epoch.isoformat(),horizon=horizon,scope=scope,jobs=jobs,precedences=precedences,buffer_events=buffer_events,deliveries=deliveries,initial_setups=copy.deepcopy(meta['resource_initial_setups']),setup_transitions=copy.deepcopy(meta['setup_transitions']),baseline_setup_seconds=int(Decimal(measured['setup_seconds'])),coverage=dict(gaps=[],limits=['Fixed native production quantities/topology, purchases, firm delivery quantities/times and nonselected tasks; cannot certify global enterprise optimum or recover original120 shortage.','Named personnel preserved; no automatic staff substitution.','Wall-envelope no-overlap is conservative across off-shift gaps.','Unknown external stock stays unknown and supplies no invented available evidence.']),execution_authorized=False)
 problem['problem_sha256']=digest(problem)
 return problem
