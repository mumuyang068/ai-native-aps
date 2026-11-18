"""Advanced research capabilities over existing PlanStore, DraftStore and Worker."""
import copy,json,os,threading,time,uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime,timezone
from p22.foundation_projection import digest,require
from p22.intelligence_bridge import require_scope
from p22.intelligence_registry import ResearchRegistry
from p22.intelligence_versions import statistical_version
from planning_adapter import InputError

POOL=ThreadPoolExecutor(max_workers=1,thread_name_prefix='forecast-research')
ACTIVE=set();LOCK=threading.RLock()

def research_input(context):
    return context if isinstance(context,dict) else context.drafts.baseline

def shape(value,required,optional=()):
    require(isinstance(value,dict) and set(required)<=set(value) and not set(value)-set(required)-set(optional),'INTELLIGENCE_ACTION_FIELDS')


def _display_evidence(value,record_ref,path='',*,training_lists=False):
    """Bound long display evidence with a digest and its original Owner locator.

    Period rows and order allocations stay complete. Only long source-reference
    lists, or training receipt lists that the workbench doesn't iterate, become
    summaries. Immutable registry/archives are never mutated by this projection.
    """
    if isinstance(value,dict):
        return {key:_display_evidence(item,record_ref,path+'/'+str(key).replace('~','~0').replace('/','~1'),
                    training_lists=training_lists) for key,item in value.items()}
    if isinstance(value,list):
        field=path.rsplit('/',1)[-1]
        references=field=='source_refs' or field.endswith('_source_refs')
        if (references or training_lists) and (len(value)>32 or len(json.dumps(value,ensure_ascii=False,separators=(',',':')).encode())>4096):
            return dict(count=len(value),sha256=digest(value),record_ref=record_ref+'#'+path)
        return [_display_evidence(item,record_ref,path+'/'+str(index),training_lists=training_lists)
                for index,item in enumerate(value)]
    return copy.deepcopy(value)


def _forecast_display(version):
    ref='professional_artifacts/version/'+version['id']
    result={key:_display_evidence(value,ref,'/'+key,training_lists=key=='algorithm_receipt')
            for key,value in version.items() if key!='backtest'}
    if 'backtest' in version:
        result['backtest']=copy.deepcopy(version['backtest'])
        if isinstance(version['backtest'],dict) and 'receipt' in version['backtest']:
            result['backtest']['receipt']=_display_evidence(version['backtest']['receipt'],ref,
                '/backtest/receipt',training_lists=True)
    return result


def _draft_display(draft):
    if draft is None:return None
    result={key:copy.deepcopy(draft[key]) for key in ('id','state','run_id') if key in draft}
    parameters=draft.get('changes',{}).get('parameters')
    parameters=parameters if isinstance(parameters,dict) else {}
    netting_parameters=parameters.get('netting')
    netting_parameters=netting_parameters if isinstance(netting_parameters,dict) else {}
    forecast=netting_parameters.get('forecast_version') or netting_parameters.get('forecast_version_ref')
    forecast=forecast if isinstance(forecast,dict) else {}
    consumption=netting_parameters.get('consumption_policy')
    consumption=consumption if isinstance(consumption,dict) else {}
    inventory=parameters.get('replenishment')
    inventory=inventory if isinstance(inventory,dict) and inventory else None
    inquiry=parameters.get('inquiry')
    inquiry=inquiry if isinstance(inquiry,dict) and inquiry else None
    result['adopted']=dict(forecast_version_id=forecast.get('id'),forecast_digest=forecast.get('sha256'),
        inventory_policy_version=inventory.get('policy_version') if inventory else None,
        inventory_policy=copy.deepcopy(inventory),
        netting_windows=dict(backward=consumption.get('backward_days'),forward=consumption.get('forward_days')) if netting_parameters else None,
        inquiry=copy.deepcopy(inquiry),reason=copy.deepcopy(parameters.get('reason')),
        has_forecast=bool(forecast.get('id')),has_inventory=inventory is not None,has_inquiry=inquiry is not None)
    result['changes']={}
    netting=draft.get('changes',{}).get('netting')
    if isinstance(netting,dict) and isinstance(netting.get('rows'),list):
        result['changes']['netting']=dict(rows=_display_evidence(netting['rows'],'draft/'+draft['id'],
            '/changes/netting/rows'))
    return result


def _outcome_display(outcome):
    if outcome is None:return None
    fields={'status','business_audit','baseline_business_audit','baseline_audit','identity',
        'plan_id','data_version_id','baseline_run_id','candidate_run_id','run_id','target',
        'original_commitments','inquiry','atp_status','atp_quantity','formal_promise','simulation_only',
        'execution_authorized','search_exhaustive','issues','missing_evidence','blocking_code','failure_code'}
    return {key:copy.deepcopy(value) for key,value in outcome.items() if key in fields}


def netting_templates(versions,revoked,data):
    """Shared research-policy templates for workbench and exact query readers."""
    from datetime import timedelta
    from demand_forecast import _time
    templates={}
    for version in versions:
        if version['id'] in revoked or version['status']!='CONFIRMED_RESEARCH':continue
        templates[version['id']]=dict(revision='advanced-netting-v1',
            forecast_version_ref=dict(id=version['id'],sha256=version['sha256']),
            consumption_policy=dict(policy_id='RESEARCH-CONSUMPTION',version_id='EXP06-WINDOW-v1',forecast_version_id=version['id'],
                statuses=['OPEN'],backward_days=0,forward_days=0,match_customer=True,coverage='GROSS_INCLUDES_FIRM_ORDERS'),
            net_terms=dict(priority=1000,minimum_shipment='1',maximum_lateness_seconds=0,
                due_at=(_time(data['horizon']['end_at'],'horizon')-timedelta(seconds=1)).isoformat()))
    return templates


def read_research_jobs(registry):
    """Original process-restart state recovery shared by both read projections."""
    jobs=registry.list('job')
    with LOCK:
        for job in jobs:
            if job['state'] in {'QUEUED','RUNNING'} and job['id'] not in ACTIVE:
                if job['state']=='QUEUED':registry.update_job(job['id'],dict(state='RUNNING'))
                job.update(registry.update_job(job['id'],dict(state='INTERRUPTED',error='PROCESS_RESTART_REVIEW_REQUIRED')))
    return jobs


class IntelligenceOwner:
    def __init__(self,plans,pid,*,source_mode=False):
        self.plans=plans;self.pid=pid;self.source_mode=source_mode;self.registry=ResearchRegistry(plans.store,pid)
        self.folder=plans.store.folder(pid)/'intelligence';self.folder.mkdir(exist_ok=True)

    def scope(self,identity,creating=False,allow_discarded_release=False):
        if self.source_mode:
            require(set(identity)=={'data_version_id'},'SOURCE_RESEARCH_IDENTITY_FIELDS')
            data=self.plans.source_input(self.pid,identity['data_version_id'])
            from p22.foundation_projection import validate
            validate(data)
            return data,dict(plan_id=self.pid,**identity,baseline_run_id=None,run_id=None,target='baseline',
                snapshot_sha256=self.plans.store.get(self.pid)['snapshot_sha256'])
        expected={'data_version_id','baseline_run_id','run_id','target'}
        require(set(identity)==expected,'INTELLIGENCE_IDENTITY_FIELDS')
        options=dict(creating=creating)
        if allow_discarded_release:options['allow_discarded_release']=True
        service,full=self.plans._professional_scope(self.pid,**identity,**options)
        require_scope(service.drafts.baseline)
        return service,full

    def check_version(self,version):
        saved=self.registry.get('version',version['id'])
        require(saved==version,'FORECAST_VERSION_REGISTRY_MISMATCH')
        require(not any(r['version_id']==version['id'] for r in self.registry.list('revocation')),'FORECAST_VERSION_WITHDRAWN')
        require(version['status']=='CONFIRMED_RESEARCH','FORECAST_VERSION_NOT_CONFIRMED')

    def overview(self,identity):
        service,full=self.scope(identity)
        data=research_input(service)
        if self.source_mode:
            source=data['foundation_projection']['source_tables']
            report=dict(items=data['items'],customers=source['customers'],horizon=data['horizon'],timezone=data['site']['timezone'],
                stock_facts=data['inventory_positions'],source_terms=data['supply_sources'],forecasts=source['forecasts'])
        else:report=self.plans.professional_policy_view(self.pid,**identity)
        revoked={r['version_id'] for r in self.registry.list('revocation')}
        versions=[dict(v,effective_status='WITHDRAWN' if v['id'] in revoked else v['status']) for v in self.registry.list('version')]
        datasets=[]
        for d in self.registry.list('dataset'):
            from demand_forecast import _time
            cutoff=_time(d['training_cutoff'],'training_cutoff')
            rows=[r for r in d['rows'] if r.get('observation_kind')==d.get('observation_kind','OBSERVED_DEMAND')
                and _time(r['bucket_end'],'bucket_end')<=cutoff and _time(r['observed_at'],'observed_at')<=cutoff];groups={}
            for r in rows:
                key=tuple(r.get(k) for k in ('item_id','location_id','customer_id','uom'));groups.setdefault(key,[]).append(r)
            slim=[{k:r.get(k) for k in ('item_id','location_id','customer_id','uom','bucket_start','bucket_end','quantity','censored','observation_kind','observed_at')}
                for group in groups.values() for r in sorted(group,key=lambda x:x['bucket_start'])[-52:]]
            datasets.append({k:v for k,v in d.items() if k not in {'rows','backtest'}}|dict(rows=len(d['rows']),channel_rows=len(rows),series=len(groups),history_rows=slim))
        jobs=read_research_jobs(self.registry)
        templates=netting_templates(versions,revoked,data)
        from order_promising import HoldStore
        quotes=HoldStore(self.folder/'promising').list()
        # Keep current-run records and disclose other still-active reservations.
        wanted={k:full[k] for k in ('plan_id','data_version_id','baseline_run_id','run_id','target')}
        visible=[q for q in quotes if q['identity']==wanted or q['state'] in {'HELD','COMMITTED_SIMULATED'}]
        current={'run':{'state':'NOT_REQUESTED'}} if self.source_mode else service.selected_view(identity['target'])
        return dict(identity=full,simulation_only=True,execution_authorized=False,datasets=datasets,
            forecast_versions=[_forecast_display(v) for v in versions],jobs=jobs,inventory_candidates=self.registry.list('inventory'),quotes=visible,
            netting_templates=templates,context=dict(items=report['items'],customers=report['customers'],
                horizon=report['horizon'],timezone=report['timezone'],stock_facts=report['stock_facts'],
                source_terms=report['source_terms'],original_gross=report['forecasts'],run_state=current['run']['state'],
                draft=_draft_display(report.get('draft')),outcome=_outcome_display(report.get('outcome')),original_policy_status='FORECAST_POLICY_PENDING',source_only=self.source_mode))

    def forecast_start(self,identity,payload,request_id):
        shape(payload,{'dataset_id','model','training_cutoff','issue_time','horizon'})
        require(payload['model'] in {'Naive','SeasonalNaive','AutoETS','CrostonSBA','TSB'},'FORECAST_MODEL_UNSUPPORTED')
        require(type(payload['horizon']) is int and 1<=payload['horizon']<=20,'FORECAST_HORIZON_BUDGET')
        service,full=self.scope(identity,creating=True);data=copy.deepcopy(research_input(service))
        dataset=self.registry.get('dataset',payload['dataset_id'])
        parameters=dict(payload,observation_kind=dataset.get('observation_kind','OBSERVED_DEMAND'),freq=dataset.get('freq','W-MON'))
        from demand_intelligence import select_training_history
        select_training_history(dataset['rows'],training_cutoff=payload['training_cutoff'],observation_kind=parameters['observation_kind'],censored_policy='exclude_series')
        from demand_forecast import _time
        require(_time(payload['training_cutoff'],'cutoff')<=_time(payload['issue_time'],'issue')<=_time(data['planning_at'],'planning_at'),'FORECAST_TIME_LEAKAGE')
        python=os.environ.get('PLANNING_FORECAST_PYTHON','')
        require(bool(python) and Path(python).is_file(),'FORECAST_RUNTIME_UNAVAILABLE')
        with LOCK:
            previous=self.registry.list('job')
            # The registry handles exact duplicate requests before the active budget.
            duplicate=next((j for j in previous if j.get('request_id')==request_id),None)
            if not duplicate:require(not ACTIVE,'FORECAST_RUNTIME_BUSY')
            job=self.registry.create('job',request_id,dict(identity=full,parameters=payload),lambda:dict(
                id='FCJOB-'+uuid.uuid4().hex[:24],state='QUEUED',identity=full,parameters=copy.deepcopy(payload),
                request_id=request_id,model=payload['model'],created_at=datetime.now(timezone.utc).isoformat()))
            if job['state']!='QUEUED' or job['id'] in ACTIVE:return job
            ACTIVE.add(job['id'])
        def execute():
            started=time.perf_counter()
            try:
                self.registry.update_job(job['id'],dict(state='RUNNING'))
                source=self.folder/(job['id']+'-input.json');target=self.folder/(job['id']+'-result.json')
                source.write_text(json.dumps(dict(rows=dataset['rows'],parameters=parameters),ensure_ascii=False),encoding='utf8')
                from p22.intelligence_process import run
                command=[python,'-B','-m','p22.intelligence_runner',str(source),str(target)]
                env={k:v for k,v in os.environ.items() if k in {'SystemRoot','SYSTEMROOT','WINDIR','PATH','TEMP','TMP'}};env['PYTHONDONTWRITEBYTECODE']='1'
                done=run(command,cwd=Path(__file__).resolve().parent.parent,env=env,timeout=180)
                if done.returncode:raise ValueError('FORECAST_ALGORITHM_FAILED:'+done.stderr[-1600:])
                result=json.loads(target.read_text('utf8'))
                version=self.registry.create('version',request_id,dict(job_id=job['id']),lambda:statistical_version(data,result,dataset,payload,planning_scope=not self.source_mode))
                self.registry.update_job(job['id'],dict(state='COMPLETED',version_id=version['id'],elapsed_seconds=time.perf_counter()-started,completed_at=datetime.now(timezone.utc).isoformat()))
            except Exception as error:
                self.registry.update_job(job['id'],dict(state='FAILED',error=str(error)[:2000],elapsed_seconds=time.perf_counter()-started))
            finally:
                with LOCK:ACTIVE.discard(job['id'])
        POOL.submit(execute);return job

    def promising_snapshot(self,identity):
        service,full=self.scope(identity);view=service.selected_view(identity['target'])
        service.baseline_view() # Authenticate the original delivery manifest too.
        require(view['run']['state']=='SUCCEEDED','PROMISING_RUN_NOT_READY')
        data=service.drafts.baseline if identity['target']=='baseline' else service.drafts.get(identity['target'])['input']
        from service import artifact_root
        from p22.professional_outcome import commitment_protection
        root=artifact_root()/identity['run_id']/'raw'
        raw={name:json.loads((root/(name+'.json')).read_text('utf8')) for name in ('fixed_batch_post','operationplanmaterial','purchaseorder','distributionorder','deliveryorder','manufacturingorder') if (root/(name+'.json')).exists()}
        oldroot=artifact_root()/identity['baseline_run_id']/'raw'
        original=json.loads((oldroot/'deliveryorder.json').read_text('utf8'))
        protection=commitment_protection(data['foundation_projection']['source_tables']['demand_lines'],original,raw['deliveryorder'],data['site']['timezone'])
        from p22.promising_projection import project_snapshot
        wanted={k:full[k] for k in ('plan_id','data_version_id','baseline_run_id','run_id','target')}
        return project_snapshot(data,view,raw,wanted,protection=protection)

    def action(self,identity,action,payload,request_id):
        if self.source_mode:require(action in {'history_register','forecast_start','forecast_confirm','forecast_override','forecast_restore','forecast_withdraw','inventory_recommend'},'SOURCE_RESEARCH_ACTION_UNSUPPORTED')
        from p22.intelligence_actions import execute
        return execute(self,identity,action,payload,request_id)
