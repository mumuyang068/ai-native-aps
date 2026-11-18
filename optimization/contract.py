"""Research-only compound intent through the original editor/draft Owner."""
import copy
from datetime import datetime,timedelta
from .validate import baseline_assignments,validate_candidate

REVISION='aps-optimizer-v1'
KINDS={'optimized_schedule','native_strategy'}

def enabled(data):
 return data.get('foundation_projection',{}).get('editor_scope',{}).get('optimizer_revision')==REVISION

def require(data):
 from plan_editor import fail
 if not enabled(data):fail('OPTIMIZER_RESEARCH_SCOPE_REQUIRED')

def event(data,edit,source):
 from plan_editor import fail,at
 require(data)
 if edit['kind']=='native_strategy':
  if set(edit)!={'kind','reference','solver','machine_search'} or edit['reference']!='optimization' or edit['solver'] not in {'heuristic','heuristic_2'} or edit['machine_search'] not in {'PRIORITY','MINPENALTY'} or source:fail('OPTIMIZER_NATIVE_STRATEGY_INVALID')
  return copy.deepcopy(edit)
 if set(edit)!={'kind','reference','assignments'} or edit['reference']!='optimization' or not isinstance(edit['assignments'],list) or not 1<=len(edit['assignments'])<=128:fail('OPTIMIZER_ASSIGNMENTS_INVALID')
 rows=[]
 for a in edit['assignments']:
  if set(a)!={'reference','start_at','setup_end_at','end_at','resource_id'}:fail('OPTIMIZER_ASSIGNMENT_FIELDS')
  t=next((s for s in source if s['id']==a['reference']),None)
  if not t or not at(a['start_at'])<=at(a['setup_end_at'])<at(a['end_at']):fail('OPTIMIZER_ASSIGNMENT_SOURCE')
  rows.append({**copy.deepcopy(a),'source_task':copy.deepcopy(t)})
 if len({a['reference'] for a in rows})!=len(rows) or {a['reference'] for a in rows}!={s['id'] for s in source}:fail('OPTIMIZER_ASSIGNMENT_IDENTITY')
 return dict(kind='optimized_schedule',reference='optimization',assignments=rows)

def to_edit(problem,assignments):
 checked=validate_candidate(problem,assignments)
 if checked['status']!='PASS':raise ValueError('OPTIMIZER_MODEL_SCREEN_FAILED')
 epoch=datetime.fromisoformat(problem['epoch'])
 jobs={j['id']:j for j in problem['jobs']};rows=[]
 for a in assignments:
  j=jobs[a['id']]
  if all(a[k]==j[k] for k in ('start','setup_end','end','machine','person')):continue
  if j['fixed']:raise ValueError('OPTIMIZER_FIXED_JOB_CHANGED')
  rows.append(dict(reference=j['source_task']['id'],resource_id=a['machine'],**{out:(epoch+timedelta(seconds=a[key])).isoformat() for out,key in [('start_at','start'),('setup_end_at','setup_end'),('end_at','end')]}))
 if not rows:raise ValueError('OPTIMIZER_NO_CHANGED_CANDIDATE')
 return dict(kind='optimized_schedule',reference='optimization',assignments=rows)

def screen_edit(problem,edit):
 from plan_editor import at,fail
 if not isinstance(edit.get('assignments'),list) or not 1<=len(edit['assignments'])<=128:fail('OPTIMIZER_ASSIGNMENTS_INVALID')
 for a in edit['assignments']:
  if not isinstance(a,dict) or set(a)!={'reference','start_at','setup_end_at','end_at','resource_id'} or not all(isinstance(v,str) and v for v in a.values()):fail('OPTIMIZER_ASSIGNMENT_FIELDS')
 if len({a['reference'] for a in edit['assignments']})!=len(edit['assignments']):fail('OPTIMIZER_ASSIGNMENT_IDENTITY')
 rows={a['id']:a for a in baseline_assignments(problem)}
 jobs={j['source_task']['id']:j for j in problem['jobs'] if j.get('source_task')}
 epoch=at(problem['epoch'])
 for a in edit.get('assignments',[]):
  j=jobs.get(a.get('reference'))
  if not j or j['fixed']:fail('OPTIMIZER_ASSIGNMENT_SOURCE')
  row=rows[j['id']]
  row.update(machine=a['resource_id'])
  for out,key in [('start_at','start'),('setup_end_at','setup_end'),('end_at','end')]:
   seconds=(at(a[out])-epoch).total_seconds()
   if seconds!=int(seconds):fail('OPTIMIZER_SUBSECOND_UNSUPPORTED')
   row[key]=int(seconds)
 checked=validate_candidate(problem,list(rows.values()))
 if checked['status']!='PASS':fail('OPTIMIZER_MODEL_SCREEN_FAILED:'+str(checked['issues'][:3]))
 return checked

def read_problem(service,scope='setup_and_downstream'):
 import json
 from service import artifact_root
 from .problem import build_problem
 view=service.baseline_view();folder=artifact_root()/view['run']['solve_run_id']
 data=json.loads((folder/'canonical_input.json').read_text('utf8'))
 post=json.loads((folder/'raw/fixed_batch_post.json').read_text('utf8'))
 from calculation_validation import evidence_rows
 orders=[r for t in ('manufacturingorder','workorder') for r in evidence_rows(json.loads((folder/'raw'/f'{t}.json').read_text('utf8'))) or []]
 if data!=service.drafts.baseline:raise ValueError('OPTIMIZER_BASELINE_INPUT_CHANGED')
 return build_problem(data,view,post,orders,scope=scope)
