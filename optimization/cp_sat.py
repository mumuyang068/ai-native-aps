"""OR-Tools CP-SAT: machine circuits, calendar work coordinates and inventory.

Model optimality is confined to the frozen problem, never the whole enterprise.
No weights mix setup with delivery or disturbance: objectives solve in stages.
"""
import math,time
from collections import defaultdict
from decimal import Decimal
from ortools.sat.python import cp_model
from .validate import baseline_assignments,validate_candidate

def solve(problem,budget_seconds=60,free_ids=None,hint=None,goal='setup_then_disturbance',seed=1):
 started=time.monotonic();deadline=started+float(budget_seconds)
 empty=dict(model_status='MODEL_INVALID',assignments=[],aps_business_certified=False)
 if not 0<float(budget_seconds)<=1800 or problem.get('coverage',{}).get('gaps') or goal!='setup_then_disturbance':return dict(empty,stop_reason='INVALID_BUDGET_GOAL_OR_COVERAGE')
 model=cp_model.CpModel();H=problem['horizon'];jobs=problem['jobs'];by_job={j['id']:j for j in jobs};incumbent={a['id']:a for a in (hint or baseline_assignments(problem))};vars={};present={};reservations=defaultdict(list);setup_machines=defaultdict(list);changes=[];shifts=[];mode_records=[]
 rules={(r['from_state'],r['to_state']):int(Decimal(str(r['duration_seconds']))) for r in problem['setup_transitions']};maxsetup=max(rules.values(),default=0)
 def map_clock(wall,coordinate,windows,active,ending,label):
  bits=[];offset=0
  for index,(a,b) in enumerate(windows):
   z=model.new_bool_var(label+str(index));bits.append(z)
   model.add(coordinate>=offset+(1 if ending else 0)).only_enforce_if(z)
   model.add(coordinate<=offset+(b-a)-(0 if ending else 1)).only_enforce_if(z)
   model.add(wall==a+coordinate-offset).only_enforce_if(z);offset+=b-a
  model.add(sum(bits)==active)
 for j in jobs:
  jid=j['id'];s=model.new_int_var(0,H,'s'+jid);p=model.new_int_var(0,H,'p'+jid);e=model.new_int_var(0,H,'e'+jid);su=model.new_int_var(0,maxsetup if j['state'] else 0,'setup'+jid);span=model.new_int_var(1,H,'span'+jid);model.add(span==e-s);model.add(s<=p);model.add(p<e);vars[jid]=(s,p,e,su)
  ps=[];same_machine=[]
  for k,mode in enumerate(j['modes']):
   x=model.new_bool_var('mode'+jid+str(k));present[jid,mode['machine']]=x;ps.append(x);mode_records.append((jid,mode,x))
   total=sum(b-a for a,b in mode['windows']);ws=model.new_int_var(0,total,'ws'+jid+str(k));wp=model.new_int_var(0,total,'wp'+jid+str(k));we=model.new_int_var(0,total,'we'+jid+str(k));model.add(wp==ws+su).only_enforce_if(x);model.add(we==wp+j['processing_seconds']).only_enforce_if(x)
   map_clock(s,ws,mode['windows'],x,False,'cs'+jid+str(k))
   map_clock(e,we,mode['windows'],x,True,'ce'+jid+str(k))
   if j['state']:
    positive=model.new_bool_var('pos'+jid+str(k));model.add(positive<=x);model.add(su>=1).only_enforce_if(positive);model.add(su==0).only_enforce_if([x,positive.Not()]);model.add(p==s).only_enforce_if([x,positive.Not()]);map_clock(p,wp,mode['windows'],positive,True,'cp'+jid+str(k));setup_machines[mode['machine']].append(jid)
   else:model.add(p==s)
   interval=model.new_optional_interval_var(s,span,e,x,'env'+jid+str(k))
   reservations[mode['machine']].append(interval);reservations[mode['person']].append(interval)
   if mode['machine']==incumbent[jid]['machine']:same_machine.append(x)
   model.add_hint(x,mode['machine']==incumbent[jid]['machine'])
  model.add_exactly_one(ps)
  if j['fixed'] or free_ids is not None and jid not in set(free_ids):
   a=incumbent[jid]
   for var,key in [(s,'start'),(p,'setup_end'),(e,'end')]:model.add(var==a[key])
   for mode in j['modes']:model.add(present[jid,mode['machine']]==(mode['machine']==a['machine']))
  same_s=model.new_bool_var('sameS'+jid);same_e=model.new_bool_var('sameE'+jid);model.add(s==j['start']).only_enforce_if(same_s);model.add(s!=j['start']).only_enforce_if(same_s.Not());model.add(e==j['end']).only_enforce_if(same_e);model.add(e!=j['end']).only_enforce_if(same_e.Not());changed=model.new_bool_var('changed'+jid)
  original=present.get((jid,j['machine']))
  model.add_bool_or([same_s.Not(),same_e.Not(),original.Not()]).only_enforce_if(changed);model.add_bool_and([same_s,same_e,original]).only_enforce_if(changed.Not());changes.append(changed)
  for var,old in [(s,j['start']),(e,j['end'])]:
   distance=model.new_int_var(0,H,'dist'+jid+str(old)+var.name);model.add_abs_equality(distance,var-old);shifts.append(distance)
  for var,key in [(s,'start'),(p,'setup_end'),(e,'end')]:model.add_hint(var,incumbent[jid][key])
 for rid,intervals in reservations.items():model.add_no_overlap(intervals)
 for rid,ids in setup_machines.items():
  ids=list(dict.fromkeys(ids));arcs=[];is_empty=model.new_bool_var('empty'+rid);arcs.append((0,0,is_empty));prs=[present[jid,rid] for jid in ids];model.add(sum(prs)==0).only_enforce_if(is_empty);model.add(sum(prs)>=1).only_enforce_if(is_empty.Not())
  for i,jid in enumerate(ids,1):
   x=present[jid,rid];arcs.append((i,i,x.Not()));first=model.new_bool_var('first'+rid+jid);last=model.new_bool_var('last'+rid+jid);arcs.extend([(0,i,first),(i,0,last)])
   required=rules.get((problem['initial_setups'][rid],by_job[jid]['state']))
   if required is None:return dict(empty,stop_reason='INITIAL_SETUP_RULE_MISSING')
   model.add(vars[jid][3]==required).only_enforce_if(first)
   for k,next_id in enumerate(ids,1):
    if i==k:continue
    a=model.new_bool_var('arc'+rid+str(i)+'_'+str(k));arcs.append((i,k,a));required=rules.get((by_job[jid]['state'],by_job[next_id]['state']))
    if required is None:return dict(empty,stop_reason='SETUP_RULE_MISSING')
    model.add(vars[next_id][0]>=vars[jid][2]).only_enforce_if(a);model.add(vars[next_id][3]==required).only_enforce_if(a)
  model.add_circuit(arcs)
 for before,after in problem['precedences']:model.add(vars[before][2]<=vars[after][0])
 buffers=defaultdict(list)
 for e in problem['buffer_events']:buffers[e['buffer']].append(e)
 for name,events in buffers.items():
  precision=max(max(0,-Decimal(e['quantity']).normalize().as_tuple().exponent) for e in events)
  if precision>6:return dict(empty,stop_reason='MATERIAL_PRECISION_UNMAPPED')
  scale=10**precision;times=[];deltas=[]
  for e in events:
   q=int(Decimal(e['quantity'])*scale);point={'start':0,'setup_end':1,'end':2}.get(e['point']);t=vars[e['job']][point] if e['job'] else e['at'];times.append(2*t+(1 if q<0 else 0));deltas.append(q)
  model.add_reservoir_constraint(times,deltas,0,max(1,sum(q for q in deltas if q>0)))
 setup=sum(v[3] for v in vars.values());model.minimize(setup)
 # Construct a complete feasible hint from the independently checked source or
 # incumbent. All circuit and calendar-selector bits are included; partial date
 # hints alone can spend the entire budget searching for a first incumbent.
 hint_check=validate_candidate(problem,list(incumbent.values()))
 if hint_check.get('status')=='PASS' and deadline-time.monotonic()>1:
  seed_model=model.clone()
  for jid,a in incumbent.items():
   for var,key in zip(vars[jid][:3],['start','setup_end','end']):seed_model.add(var==a[key])
   for mode in by_job[jid]['modes']:seed_model.add(present[jid,mode['machine']]==(mode['machine']==a['machine']))
  seeder=cp_model.CpSolver();seeder.parameters.num_search_workers=1;seeder.parameters.max_time_in_seconds=min(5,max(.01,(deadline-time.monotonic())/3))
  if seeder.solve(seed_model) in [cp_model.OPTIMAL,cp_model.FEASIBLE]:
   model.clear_hints()
   for index in range(len(model.proto.variables)):model.add_hint(model.get_int_var_from_proto_index(index),seeder.value(seed_model.get_int_var_from_proto_index(index)))

 solver=cp_model.CpSolver();solver.parameters.num_search_workers=4;solver.parameters.random_seed=seed;solver.parameters.max_time_in_seconds=max(.01,deadline-time.monotonic());solver.parameters.log_search_progress=False
 status=solver.solve(model);name=solver.status_name(status)
 if status not in [cp_model.OPTIMAL,cp_model.FEASIBLE]:return dict(empty,model_status=name,stop_reason='TIME_LIMIT' if status==cp_model.UNKNOWN else name,elapsed_seconds=round(time.monotonic()-started,4),primary_bound=solver.best_objective_bound)
 primary_optimal=status==cp_model.OPTIMAL;primary=int(round(solver.objective_value));bound=solver.best_objective_bound;phases=[dict(objective='setup_seconds',status=name,value=primary,bound=bound)]
 def extract():
  rows=[]
  for j in jobs:
   mode=next(m for m in j['modes'] if solver.value(present[j['id'],m['machine']]))
   s,p,e,_=vars[j['id']];rows.append(dict(id=j['id'],start=solver.value(s),setup_end=solver.value(p),end=solver.value(e),machine=mode['machine'],person=mode['person']))
  return rows
 best=extract();secondary_optimal=True;completed_secondary=0
 # Exact lexicographic stages: keep best setup, then count changed jobs, then
 # absolute time displacement. Never mix quantities, seconds and counts.
 for title,objective in [('changed_jobs',sum(changes)),('shift_seconds',sum(shifts))]:
  remaining=deadline-time.monotonic()
  if remaining<.1:break
  model.add(setup==primary);model.minimize(objective);model.clear_hints()
  for a in best:
   for var,key in zip(vars[a['id']][:3],['start','setup_end','end']):model.add_hint(var,a[key])
   for m in by_job[a['id']]['modes']:model.add_hint(present[a['id'],m['machine']],m['machine']==a['machine'])
  solver.parameters.max_time_in_seconds=remaining;status=solver.solve(model);name=solver.status_name(status)
  phases.append(dict(objective=title,status=name,value=solver.objective_value,bound=solver.best_objective_bound))
  if status not in [cp_model.OPTIMAL,cp_model.FEASIBLE]:break
  best=extract();value=int(round(solver.objective_value));model.add(objective==value);secondary_optimal=secondary_optimal and status==cp_model.OPTIMAL;completed_secondary+=1
 checked=validate_candidate(problem,best)
 return dict(model_status='OPTIMAL' if primary_optimal and secondary_optimal and completed_secondary==2 else 'FEASIBLE',assignments=best,setup_seconds=checked.get('setup_seconds'),changed_jobs=checked.get('changed_jobs'),shift_seconds=checked.get('shift_seconds'),primary_bound=bound,primary_optimal=primary_optimal,phases=phases,elapsed_seconds=round(time.monotonic()-started,4),stop_reason='OPTIMAL_IN_FINITE_MODEL' if primary_optimal and secondary_optimal and completed_secondary==2 else 'TIME_LIMIT_OR_FEASIBLE_ONLY',independent_screen=checked,aps_business_certified=False)
