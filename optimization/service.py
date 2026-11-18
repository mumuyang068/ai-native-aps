"""Bounded async candidate generation, preserving original DraftStore authority."""
import json,os,re,subprocess,threading,uuid
from datetime import datetime,timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from plan_editor import digest
from planning_adapter import InputError
from .contract import require,read_problem,to_edit

_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='aps-optimizer')
_lock=threading.RLock()
_active=set()

def folder(service):
 p=service.root/'optimization';p.mkdir(exist_ok=True);return p

def save(path,record):
 temp=path.with_suffix('.tmp');temp.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf8');temp.replace(path)

def read(service,identity,search_id):
 if not re.fullmatch('OPT-[a-f0-9]{24}',search_id):raise InputError('OPTIMIZER_NOT_FOUND')
 with _lock:
  path=folder(service)/(search_id+'.json')
  if not path.exists():raise InputError('OPTIMIZER_NOT_FOUND')
  record=json.loads(path.read_text('utf8'))
  if record['identity']!=identity:raise InputError('OPTIMIZER_IDENTITY_MISMATCH')
  if record['state'] in {'RUNNING','QUEUED'} and search_id not in _active:
   record.update(state='INTERRUPTED',stop_reason='PROCESS_RESTART_REVIEW_REQUIRED');save(path,record)
  return project_record(record)

def latest(service,identity):
 with _lock:
  candidates=[]
  for path in folder(service).glob('OPT-*.json'):
   if not re.fullmatch('OPT-[a-f0-9]{24}',path.stem):continue
   record=json.loads(path.read_text('utf8'))
   if record.get('identity')==identity:candidates.append(path)
  if not candidates:return None
  path=max(candidates,key=lambda p:p.stat().st_mtime_ns)
  return read(service,identity,path.stem)

def project_record(record):
 record=dict(record)
 if record.get('algorithm') in {'vns','tabu','late_acceptance'}:
  record.update(best_repair_status=record.get('best_repair_status',record.get('model_status')),primary_bound_scope='REPAIR_NEIGHBORHOOD_ONLY',global_optimum_proven=False,model_scope='BOUNDED_REPAIR_SEARCH')
  if record.get('model_status')=='OPTIMAL':record['model_status']='FEASIBLE'
 return record

def result_fields(candidate,algorithm):
 fields=dict(model_status=candidate.get('model_status'),setup_seconds=candidate.get('setup_seconds'),elapsed_seconds=candidate.get('elapsed_seconds',candidate.get('runtime')),stop_reason=candidate.get('stop_reason'),primary_bound=candidate.get('primary_bound'),changed_jobs=candidate.get('changed_jobs'),best_repair_status=candidate.get('best_repair_status'),primary_bound_scope=candidate.get('primary_bound_scope','FINITE_MODEL_ONLY'),global_optimum_proven=False,model_scope='FINITE_MODEL_ONLY' if algorithm=='cp_sat' else 'BOUNDED_REPAIR_SEARCH')
 if algorithm=='pyjobshop':fields.update(model_scope='RELAXATION_ONLY',comparison_objective=candidate.get('objective'),comparison_objective_name=candidate.get('objective_name'),comparison_bound=candidate.get('lower_bound'),comparison_coverage=candidate.get('coverage'))
 return fields

def start(service,identity,algorithm,scope,budget_seconds,request_id):
 require(service.drafts.baseline)
 if algorithm not in {'cp_sat','vns','tabu','late_acceptance','pyjobshop'} or scope not in {'setup_only','setup_and_downstream'} or type(budget_seconds)!=int or not 5<=budget_seconds<=600 or not re.fullmatch('[a-f0-9]{32}',request_id):raise InputError('OPTIMIZER_SETTINGS_INVALID')
 python=Path(os.environ.get('PLANNING_OPTIMIZER_PYTHON',''))
 if not python.is_file():raise InputError('OPTIMIZER_RUNTIME_UNAVAILABLE')
 fingerprint=digest(dict(identity=identity,algorithm=algorithm,scope=scope,budget_seconds=budget_seconds))
 sid='OPT-'+digest(dict(identity=identity,request_id=request_id))[:24];path=folder(service)/(sid+'.json')
 with _lock:
  if path.exists():
   old=read(service,identity,sid)
   if old['fingerprint']!=fingerprint:raise InputError('IDEMPOTENCY_CONFLICT')
   return old
  if _active:raise InputError('OPTIMIZER_BUSY')
  problem=read_problem(service,scope)
  problem_path=folder(service)/(sid+'-problem.json');problem_path.write_text(json.dumps(problem,ensure_ascii=False,indent=2),encoding='utf8')
  record=dict(search_id=sid,created_at=datetime.now(timezone.utc).isoformat(),identity=identity,fingerprint=fingerprint,state='QUEUED',algorithm=algorithm,scope=scope,budget_seconds=budget_seconds,problem_sha256=problem['problem_sha256'],job_count=len(problem['jobs']),movable_jobs=sum(not j['fixed'] for j in problem['jobs']),goal='Protect each existing delivery, minimize setup, then changed jobs and displacement',baseline_setup_seconds=problem['baseline_setup_seconds'],coverage=problem['coverage'],limits=problem['coverage']['limits'],execution_authorized=False,aps_business_certified=False)
  save(path,record);_active.add(sid)
  def run():
   try:
    with _lock:record['state']='RUNNING';save(path,record)
    env={k:v for k,v in os.environ.items() if k in {'SystemRoot','SYSTEMROOT','WINDIR','PATH','TEMP','TMP'}};env['PYTHONDONTWRITEBYTECODE']='1'
    output=folder(service)/(sid+'-result.json')
    command=[str(python),'-B','-m','optimization.runner',str(problem_path),str(output),'--algorithm',algorithm,'--budget',str(budget_seconds)]
    from .process import run as solver_process
    result=solver_process(command,cwd=Path(__file__).resolve().parent.parent,env=env,timeout=budget_seconds+45)
    if result.returncode:raise ValueError('OPTIMIZER_PROCESS_FAILED:'+result.stderr[-2000:])
    candidate=json.loads(output.read_text('utf8'))
    record.update(state='COMPLETED',edit=None,**result_fields(candidate,algorithm))
    if algorithm=='pyjobshop':record.update(candidate_status='RELAXATION_ONLY',stop_reason='MATERIAL_AND_SETUP_CREW_CALENDAR_NOT_EXACTLY_MAPPED')
    elif candidate.get('assignments'):
     from .validate import validate_candidate
     check=validate_candidate(problem,candidate['assignments']);record['model_screen']=check
     if check['status']=='PASS' and check['changed_jobs']:
      record.update(edit=to_edit(problem,candidate['assignments']),candidate_status='MODEL_SCREENED_PENDING_NATIVE')
     else:record['candidate_status']='NO_CHANGED_VALID_CANDIDATE'
    else:record['candidate_status']='NO_INCUMBENT'
   except subprocess.TimeoutExpired:record.update(state='FAILED',stop_reason='OPTIMIZER_PROCESS_TIMEOUT')
   except Exception as error:record.update(state='FAILED',stop_reason=str(error))
   finally:
    with _lock:save(path,record);_active.discard(sid)
  _pool.submit(run)
  return record.copy()
