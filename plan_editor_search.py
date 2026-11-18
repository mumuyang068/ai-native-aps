"""Bounded candidate orchestration; the existing serial Worker does all solves."""
import copy,hashlib,json,os,re,threading,time
from decimal import Decimal
from pathlib import Path
from plan_editor import ACTION,digest,sequence_candidates,audit_native,fail

METRICS_REVISION='editor-native-metrics-v2'

def metrics(service,target):
    view=service.selected_view(target);run=view['run']['solve_run_id']
    if view['run']['state']!='SUCCEEDED' or view.get('interpretation',{}).get('business_status') not in {'FEASIBLE_ON_TIME','LATE_DELIVERY','PARTIAL_OR_UNPLANNED'}:fail('EDITOR_SEARCH_INVALID_BUSINESS_RESULT')
    interpretation=view['interpretation']
    if interpretation.get('schedule_check')!='PASS' or interpretation.get('material_validation',{}).get('status')!='PASS':fail('EDITOR_SEARCH_AUDIT_REQUIRED')
    from service import artifact_root
    folder=artifact_root()/run;data=json.loads((folder/'canonical_input.json').read_text('utf8'))
    raw={p.stem:json.loads(p.read_text('utf8')) for p in (folder/'raw').glob('*.json') if p.stem in {'fixed_batch_pre','fixed_batch_post','manufacturingorder','workorder','operationplanresource'}}
    audit=interpretation.get('plan_editor_validation') or audit_native(data,raw,run_id=run)
    if audit['status']!='PASS':fail('EDITOR_SEARCH_NATIVE_AUDIT_REQUIRED')
    manifest=json.loads((folder/'run-manifest.json').read_text('utf8'))
    return dict(metrics_revision=METRICS_REVISION,run_id=run,setup_seconds=audit['setup_seconds'],deliveries=[dict(demand_id=r['demand_id'],planned=r['planned_quantity'],delay=r['delay_seconds']) for r in view['result']['deliveries']],
        native_audit=audit['status'],business_status=interpretation['business_status'],input_hash=view['result']['input_hash'],result_hash=view['result']['result_hash'],
        proof_files={name:sha for name,sha in manifest['files'].items() if name.endswith('.json')})

def verify_files(value):
    """Derived search scores never substitute for the current source archive."""
    from service import artifact_root
    if not re.fullmatch('API-[a-f0-9]{16}',value.get('run_id','')) or not value.get('proof_files'):fail('EDITOR_SEARCH_EVIDENCE_CHANGED')
    folder=(artifact_root()/value['run_id']).resolve()
    for name,sha in value['proof_files'].items():
        path=(folder/name).resolve()
        if not path.is_relative_to(folder) or not name.endswith('.json') or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=sha:fail('EDITOR_SEARCH_EVIDENCE_CHANGED')

def compare_metrics(base,candidate,goal):
    if base.get('setup_seconds') is None or candidate.get('setup_seconds') is None:return dict(eligible=False,improved=False,reason='SETUP_EVIDENCE_MISSING')
    left={d['demand_id']:d for d in base['deliveries']};right={d['demand_id']:d for d in candidate['deliveries']}
    if set(left)!=set(right) or any(Decimal(right[k]['planned'])<Decimal(left[k]['planned']) or (left[k]['delay'] is not None and (right[k]['delay'] is None or right[k]['delay']>left[k]['delay'])) for k in left):return dict(eligible=False,improved=False,reason='PROTECTED_DELIVERY_REGRESSION')
    saving=Decimal(base['setup_seconds'])-Decimal(candidate['setup_seconds'])
    if goal not in {'setup_then_delay','delivery_then_setup'}:fail('EDITOR_GOAL_NOT_CERTIFIED')
    improved=objective_key(candidate,goal)<objective_key(base,goal)
    return dict(eligible=True,improved=improved,setup_saved_seconds=str(saving),reason='MEASURED_IMPROVEMENT' if improved else 'NO_MEASURED_IMPROVEMENT')

def objective_key(value,goal):
    quantity=-sum(Decimal(d['planned']) for d in value['deliveries']);delay=sum(d['delay'] or 0 for d in value['deliveries']);setup=Decimal(value['setup_seconds'])
    return (setup,delay,quantity) if goal=='setup_then_delay' else (quantity,delay,setup)

def read_search(plans,pid,identity,search_id):
    import re
    if not re.fullmatch('[a-f0-9]{32}',search_id):fail('EDITOR_SEARCH_NOT_FOUND')
    service=plans.service(pid);path=service.root/'editor-searches'/(search_id+'.json')
    if not path.is_file():fail('EDITOR_SEARCH_NOT_FOUND')
    value=json.loads(path.read_text('utf8'))
    if value['identity']!=identity:fail('EDITOR_SEARCH_IDENTITY_MISMATCH')
    if value['state']=='RUNNING' and value['owner_pid']!=os.getpid():value.update(state='INTERRUPTED',stop_reason='PROCESS_RESTART_REVIEW_REQUIRED')
    if value.get('baseline'):
        if not value['baseline'].get('proof_files'):
            checked=metrics(service,'baseline')
            if any(checked[k]!=value['baseline'][k] for k in ['run_id','setup_seconds','deliveries']):fail('EDITOR_SEARCH_EVIDENCE_CHANGED')
            value['baseline']=checked
        verify_files(value['baseline'])
    removed=[]
    for entry in value['candidates']:
        if not entry.get('metrics'):continue
        draft=service.drafts.get(entry['draft_id'])
        if draft['run_id']!=entry['run_id']:fail('EDITOR_SEARCH_IDENTITY_MISMATCH')
        if draft['state']=='DISCARDED':entry['historical_only']=True;removed.append(draft['id']);continue
        if not entry['metrics'].get('proof_files'):
            checked=metrics(service,entry['draft_id'])
            if any(checked[k]!=entry['metrics'][k] for k in ['run_id','setup_seconds','deliveries']):fail('EDITOR_SEARCH_EVIDENCE_CHANGED')
            entry['metrics']=checked
        verify_files(entry['metrics'])
    if removed:
        value['ranking']=[v for v in value.get('ranking',[]) if v not in removed]
        value['improvement_certified']=any(e.get('comparison',{}).get('improved') and not e.get('historical_only') for e in value['candidates'])
    # Historical receipts stay immutable; current scores must follow current
    # semantics after authenticating the original native archive.
    if value.get('baseline') and (value['baseline'].get('metrics_revision')!=METRICS_REVISION or any(e.get('metrics') and e['metrics'].get('metrics_revision')!=METRICS_REVISION for e in value['candidates'])):
        value['historical_scores']=dict(baseline=copy.deepcopy(value['baseline']),candidates=[dict(draft_id=e['draft_id'],metrics=copy.deepcopy(e.get('metrics')),comparison=copy.deepcopy(e.get('comparison'))) for e in value['candidates']])
        value['baseline']=metrics(service,'baseline');verify_files(value['baseline'])
        for e in value['candidates']:
            if e.get('metrics') and not e.get('historical_only'):
                e['metrics']=metrics(service,e['draft_id']);verify_files(e['metrics'])
                e['comparison']=compare_metrics(value['baseline'],e['metrics'],value['goal'])
        eligible=[e for e in value['candidates'] if e.get('comparison',{}).get('eligible') and not e.get('historical_only')]
        value['ranking']=[e['draft_id'] for e in sorted(eligible,key=lambda e:objective_key(e['metrics'],value['goal']))]
        value['improvement_certified']=any(e['comparison']['improved'] for e in eligible);value['metrics_rederived']=True
    return value

def start(plans,pid,arguments):
    service,identity=plans._plan_editor_scope(pid,**{k:arguments[k] for k in ['data_version_id','baseline_run_id','run_id','target']})
    from plan_editor import contract
    c=contract(service.drafts.baseline) or {};policy=c.get('sequence')
    if not policy or not c.get('setup'):fail('EDITOR_SEARCH_POLICY_REQUIRED')
    if len(arguments['edits'])!=1 or arguments['edits'][0]['kind']!='sequence':fail('EDITOR_SEARCH_SEQUENCE_ONLY')
    sid=arguments['request_id'];root=service.root/'editor-searches';root.mkdir(exist_ok=True);path=root/(sid+'.json');signature=digest(arguments)
    if path.exists():
        value=read_search(plans,pid,identity,sid)
        if value['arguments_sha256']!=signature:fail('IDEMPOTENCY_CONFLICT')
        return value
    active=getattr(plans,'editor_searches',{})
    if any(t.is_alive() for (p,_),t in active.items() if p==pid):fail('EDITOR_SEARCH_ALREADY_RUNNING')
    # Only after checking existing receipts and the active budget may a new
    # original DraftStore candidate be created.
    response=plans.create_plan_editor_draft(pid,**arguments)
    data=service.drafts.baseline;source=[dict(id=d['id'],family=d['item_id'],**({'priority':d['priority']} if 'foundation_projection' in data else {})) for d in sorted(data['demand_lines'],key=lambda d:d['priority'])]
    orders=[arguments['edits'][0]['order']]
    for order in sequence_candidates(source,policy['max_candidates']):
        if order not in orders:orders.append(order)
    orders=orders[:policy['max_candidates']]
    receipt=dict(search_id=sid,identity=identity,arguments_sha256=signature,owner_pid=os.getpid(),state='RUNNING',goal=policy['goal'],
        max_candidates=policy['max_candidates'],max_seconds=600,rounds=1,candidates=[],native_submissions=0,stop_reason=None,execution_authorized=False)
    def save():
        temp=path.with_suffix('.tmp');temp.write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf8');temp.replace(path)
    save()
    def run():
        started=time.monotonic()
        try:
            receipt['baseline']=metrics(service,'baseline');save()
            for index,order in enumerate(orders):
                if time.monotonic()-started>=600:receipt['stop_reason']='TIME_BUDGET';break
                edits=[dict(kind='sequence',reference='manufacturing-sequence',order=order)]
                if index==0:created=response
                else:
                    args={k:arguments[k] for k in ['data_version_id','baseline_run_id','run_id','target','reason']};args['edits']=edits
                    preview=plans.preview_plan_editor(pid,**args)
                    created=plans.create_plan_editor_draft(pid,**args,source_sha256=preview['identity']['source_sha256'],fingerprint=preview['fingerprint'],acknowledge_limits=True,request_id=digest(dict(search=sid,index=index))[:32])
                draft=created['draft'];entry=dict(index=index,order=order,draft_id=draft['id'],state='NOT_RUN');receipt['candidates'].append(entry);save()
                job=service.run_draft(draft['id']);receipt['native_submissions']+=1;entry.update(state=job['state'],run_id=job['solve_run_id']);save()
                while job['state'] in {'QUEUED','RUNNING'}:
                    if time.monotonic()-started>=600:receipt['stop_reason']='TIME_BUDGET_CURRENT_RUN_RETAINED';break
                    time.sleep(.5);job=service.compute().run(job['solve_run_id'])
                entry['state']=job['state']
                if job['state']=='SUCCEEDED':
                    try:
                        entry['metrics']=metrics(service,draft['id']);entry['comparison']=compare_metrics(receipt['baseline'],entry['metrics'],policy['goal'])
                    except ValueError as exc:entry.update(eligible=False,error=str(exc))
                else:entry['error']=job.get('failure_code')
                save()
                if receipt['stop_reason']:break
            eligible=[e for e in receipt['candidates'] if e.get('comparison',{}).get('eligible')]
            ranked=sorted(eligible,key=lambda e:objective_key(e['metrics'],policy['goal']))
            receipt['ranking']=[e['draft_id'] for e in ranked];receipt['improvement_certified']=any(e['comparison']['improved'] for e in eligible)
            receipt['state']='COMPLETED';receipt['stop_reason']=receipt['stop_reason'] or 'CANDIDATE_BUDGET_EXHAUSTED'
        except Exception as exc:receipt.update(state='STOPPED',stop_reason=str(exc).split(':',1)[0],error=str(exc))
        finally:receipt['elapsed_seconds']=round(time.monotonic()-started,3);save()
    thread=threading.Thread(target=run,name='bounded-editor-search',daemon=True);active[(pid,sid)]=thread;plans.editor_searches=active;thread.start()
    return copy.deepcopy(receipt)
