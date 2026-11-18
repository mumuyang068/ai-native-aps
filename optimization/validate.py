"""Independent deterministic screening. PASS is a finite-model verdict only."""
from collections import defaultdict
from decimal import Decimal

def work(windows,start,end):return sum(max(0,min(end,b)-max(start,a)) for a,b in windows)
def baseline_assignments(problem):return [{k:j[k] for k in ['id','start','setup_end','end','machine','person']} for j in problem['jobs']]

def validate_candidate(problem,assignments):
 issues=[];jobs={j['id']:j for j in problem['jobs']};by={a.get('id'):a for a in assignments if isinstance(a,dict)}
 def check(condition,code,**detail):
  if not condition:issues.append(dict(code=code,**detail))
 check(not problem.get('coverage',{}).get('gaps'),'UNMAPPED_CONSTRAINTS')
 check(len(assignments)==len(by)==len(jobs) and set(by)==set(jobs),'TASK_SET_CHANGED')
 if issues:return dict(status='FAIL',issues=issues)
 intervals=defaultdict(list);sequences=defaultdict(list);changed=0;shift=0
 for jid,j in jobs.items():
  a=by[jid]
  check(set(a)=={'id','start','setup_end','end','machine','person'},'ASSIGNMENT_FIELDS',job=jid)
  if not all(type(a.get(k)) is int for k in ['start','setup_end','end']):check(False,'INVALID_TIME',job=jid);continue
  check(0<=a['start']<=a['setup_end']<a['end']<=problem['horizon'],'TIME_RANGE',job=jid)
  mode=next((m for m in j['modes'] if m['machine']==a['machine'] and m['person']==a['person']),None)
  check(mode is not None,'QUALIFICATION_OR_PERSON',job=jid)
  if not mode:continue
  check(work(mode['windows'],a['setup_end'],a['end'])==j['processing_seconds'],'PROCESSING_CALENDAR',job=jid)
  check(any(x<=a['start']<y for x,y in mode['windows']),'START_CALENDAR',job=jid)
  check(a['setup_end']==a['start'] or any(x<a['setup_end']<=y for x,y in mode['windows']),'SETUP_END_CALENDAR',job=jid)
  check(any(x<a['end']<=y for x,y in mode['windows']),'END_CALENDAR',job=jid)
  if j['fixed']:check(all(a[k]==j[k] for k in ['start','setup_end','end','machine','person']),'PROTECTED_OR_FIXED_DRIFT',job=jid)
  diff=any(a[k]!=j[k] for k in ['start','setup_end','end','machine','person']);changed+=diff;shift+=abs(a['start']-j['start'])+abs(a['end']-j['end'])
  for rid in [a['machine'],a['person']]:intervals[rid].append((a['start'],a['end'],jid,mode['windows']))
  if j['state'] is not None:sequences[a['machine']].append((a['start'],a['end'],jid,j['state'],work(mode['windows'],a['start'],a['setup_end'])))
  else:check(a['setup_end']==a['start'],'UNOWNED_SETUP',job=jid)
 for rid,rows in intervals.items():
  rows.sort()
  for left,right in zip(rows,rows[1:]):check(left[1]<=right[0],'RESOURCE_ENVELOPE_OVERLAP',resource=rid,jobs=[left[2],right[2]])
 total=0
 rules={(r['from_state'],r['to_state']):int(Decimal(str(r['duration_seconds']))) for r in problem['setup_transitions']}
 for rid,rows in sequences.items():
  state=problem['initial_setups'].get(rid)
  for start,end,jid,next_state,observed in sorted(rows):
   expected=rules.get((state,next_state));check(expected!=0 or by[jid]['setup_end']==by[jid]['start'],'ZERO_SETUP_END',job=jid);check(expected is not None and observed==expected,'SETUP_TRANSITION_OR_CALENDAR',job=jid,resource=rid,expected=expected,actual=observed);total+=observed;state=next_state
 for before,after in problem['precedences']:check(by[before]['end']<=by[after]['start'],'ROUTING_PRECEDENCE',before=before,after=after)
 buffers=defaultdict(list)
 for e in problem['buffer_events']:
  t=by[e['job']][e['point']] if e['job'] else e['at'];q=Decimal(e['quantity']);buffers[e['buffer']].append((t,0 if q>0 else 1,q))
 for buffer,events in buffers.items():
  level=Decimal(0)
  for t,phase,q in sorted(events):
   level+=q
   if level<Decimal('-0.000001'):check(False,'MATERIAL_OR_DELIVERY_SHORTFALL',buffer=buffer,at=t,level=str(level));break
 return dict(status='FAIL' if issues else 'PASS',issues=issues,setup_seconds=total,changed_jobs=changed,shift_seconds=shift,scope='FINITE_MODEL_SCREEN_ONLY',aps_business_certified=False)
