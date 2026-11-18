import json
import copy
import os
import threading
import uuid
import re
import hashlib
from functools import lru_cache
from contextlib import contextmanager
from contextvars import ContextVar
from order_evidence import project_order_evidence, find_order, explain_order
from planning_adapter import read_order_evidence,InputError
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
import requests
from agent import interpret, AgentError, redact
from drafts import DraftStore, DraftError
from semantics import audit_result, business_status
from plan_context import build_plan_context, business_summary, condition_decision, due_change_is_noop, unsupported_message
from interaction_state import SessionStore

ROOT=Path(__file__).resolve().parent
RAW_TABLES={'purchaseorder','manufacturingorder','workorder','demand','operationplanmaterial','operationplanresource','constraint','problem','deliveryorder'}

def artifact_root():return Path(os.environ.get('PLANNING_RUNS_DIR',str(ROOT/'evidence/runs')))


def read_editor_failure_details(folder,run,data):
    """Attach only this failed run's native observation, never certification."""
    path=Path(folder)/'raw/editor-failed-native.json'
    if run.get('state')!='FAILED' or not path.is_file():return None
    try:
        value=json.loads(path.read_text(encoding='utf-8'))
        expected=hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
        if (value.get('schema')!='native-editor-failure/1' or value.get('run_id')!=run['solve_run_id']
            or value.get('input_hash')!=run['input_hash'] or expected!=run['input_hash']
            or value.get('scope')!='NATIVE_FAILURE_OBSERVATION' or value.get('schedule_certified') is not False
            or value.get('infeasibility_proven') is not False):raise ValueError()
    except (OSError,ValueError,KeyError,TypeError):
        raise AgentError('RUN_SOURCE_MISMATCH: 原生失败诊断不属于本次输入或运行') from None
    return value


@lru_cache(maxsize=8)
def _foundation_interpretation_cached(data_text,result_text,raw_text,native_text,loaded_text,metadata_text):
    """Pure derived result keyed by all consumed bytes, never by run ID/mtime.

    PlanningService.view authenticates the current scope and manifest members
    before reaching this cache. Callers receive a copy, and run state is fresh.
    """
    return audit_result(json.loads(data_text),json.loads(result_text),json.loads(raw_text),
                        native_input=json.loads(native_text),loaded_input=json.loads(loaded_text),
                        runtime_metadata=json.loads(metadata_text))

_request_read_scope = ContextVar('planning_request_read_scope', default=None)


@contextmanager
def planning_read_scope(owner,*,fresh=False,reuse=False):
    """Reuse only authenticated reads belonging to this synchronous request.

    ContextVar prevents another request/thread from observing the memo. Every
    outer request starts empty and resets even when a source validation fails.
    """
    current = _request_read_scope.get()
    if not fresh and current is not None and current['owner'] is owner:
        yield current
        return
    state = dict(owner=owner, runs={}, views={}, candidates={}, review_status={},reuse_immutable=reuse and not fresh)
    token = _request_read_scope.set(state)
    try:
        yield state
    finally:
        _request_read_scope.reset(token)


def read_run(owner, run_id):
    state = _request_read_scope.get()
    if state is None or state['owner'] is not owner:
        return owner.compute().run(run_id)
    if run_id not in state['runs']:
        state['runs'][run_id] = owner.compute().run(run_id)
    return copy.deepcopy(state['runs'][run_id])


class ComputeClient:
    def __init__(self):
        if os.environ.get('PLANNING_COMPUTE_DISABLED')=='1':
            raise AgentError('COMPUTE_CONFIG_MISSING: 当前工作台未连接规划 Worker')
        self.base=os.environ.get('PLANNING_COMPUTE_URL','http://127.0.0.1:9295').rstrip('/')
        # An archive reader may deliberately reject writes. Mutations still go
        # through the original Worker API, with the same token and contracts.
        self.write_base=os.environ.get('PLANNING_COMPUTE_WRITE_URL',self.base).rstrip('/')
        if any(urlparse(base).hostname not in {'localhost','127.0.0.1'} for base in (self.base,self.write_base)):
            raise AgentError('COMPUTE_ENDPOINT_MUST_BE_LOCAL')
        token=os.environ.get('PLANNING_COMPUTE_TOKEN')
        if not token:
            try:token=dict(line.split('=',1) for line in (ROOT/'.env').read_text().splitlines() if '=' in line)['P0_API_TOKEN']
            except (OSError,KeyError):raise AgentError('COMPUTE_CONFIG_MISSING: 计算服务凭据尚未配置') from None
        self.session=requests.Session()
        self.session.headers['Authorization']='Bearer '+token

    def request(self,method,path,**kwargs):
        base=self.base if method.upper() in {'GET','HEAD','OPTIONS'} else self.write_base
        timeout=kwargs.pop('timeout',20)
        try:response=self.session.request(method,base+path,timeout=timeout,**kwargs)
        except requests.RequestException:raise AgentError('COMPUTE_UNAVAILABLE') from None
        if not response.ok:
            code='COMPUTE_HTTP_'+str(response.status_code)
            try:body=response.json()
            except ValueError:body=None
            detail=body.get('detail') if isinstance(body,dict) else None
            declared=detail.get('code') if isinstance(detail,dict) else None
            if isinstance(declared,str) and declared:code=declared
            raise AgentError(code)
        return response.json()

    def submit(self,data,key):return self.request('POST','/v1/planning/solve',json=data,headers={'Idempotency-Key':key})
    def run(self,run_id):return self.request('GET','/v1/solve-runs/'+run_id)
    def run_metadata(self,run_ids):return self.request('GET','/v1/solve-run-metadata',params={'run_ids':','.join(run_ids)},timeout=2)
    def result(self,run_id):return self.request('GET','/v1/solve-runs/'+run_id+'/result')
    def diff(self,base,candidate):return self.request('GET','/v1/plan-diffs',params={'base_run_id':base,'candidate_run_id':candidate})

class PlanningService:
    def __init__(self,root=None,baseline_path=None,enterprise_context=None,*,controlled_conditions_preflight=None,inventory_decision_preflight=None,outstanding_receipt_preflight=None,resource_capacity_preflight=None,distribution_leadtime_preflight=None,professional_policy_preflight=None):
        self.enterprise=enterprise_context
        self.controlled_conditions_preflight=controlled_conditions_preflight
        self.inventory_decision_preflight=inventory_decision_preflight
        self.outstanding_receipt_preflight=outstanding_receipt_preflight
        self.resource_capacity_preflight=resource_capacity_preflight
        self.distribution_leadtime_preflight=distribution_leadtime_preflight
        self.professional_policy_preflight=professional_policy_preflight
        self.root=Path(root or ROOT/'evidence/p1');self.root.mkdir(parents=True,exist_ok=True)
        foundation_changes=False
        if enterprise_context and enterprise_context.get('adapter_profile')=='canonical-enterprise-factory-v1':
            baseline=json.loads(Path(baseline_path or ROOT/'fixtures/baseline.json').read_bytes())
            meta=baseline.get('foundation_projection',{})
            foundation_changes=(meta.get('schema_revision')=='foundation-planning-projection-v1' and
                enterprise_context.get('foundation_projection_sha256')==meta.get('projection_sha256') and
                enterprise_context.get('simulation_only') is True and enterprise_context.get('execution_authorized') is False)
        self.drafts=DraftStore(self.root,baseline_path or ROOT/'fixtures/baseline.json',
                               {r['id'] for r in enterprise_context['customers']} if enterprise_context else None,
                               allow_changes=not bool(enterprise_context and (enterprise_context.get('execution_checkpoint') or
                                   (enterprise_context.get('adapter_profile')=='canonical-enterprise-factory-v1' and not foundation_changes))))
        self.lock=threading.RLock()
        self.sessions=SessionStore(self.root)

    def compute(self):return ComputeClient()

    def submit(self,data,key,draft=None):
        if not self.enterprise:return self.compute().submit(data,key)
        import copy
        context=copy.deepcopy(self.enterprise)
        if 'foundation_projection' in data:
            if draft:
                verified=self.drafts.for_run(draft['id'])
                if verified['input']!=data:raise DraftError('DEMAND_PRIORITY_SCOPE_MISMATCH: 提交输入与已重放草案不一致')
                context['foundation_projection_sha256']=data['foundation_projection']['projection_sha256']
            elif data['foundation_projection'].get('priority_overlay'):
                raise DraftError('DEMAND_PRIORITY_SCOPE_MISMATCH: 研究修改需要已有完整重放草案')
            if data['foundation_projection'].get('professional_overlay'):
                if not draft:raise DraftError('PROFESSIONAL_DRAFT_REQUIRED')
                context['demand_customer_links']={r['id']:r['customer_id'] for r in data['demand_lines']}
        if draft and draft['changes']['intent']=='新增急单':
            context['demand_customer_links'][draft['changes']['demand_id']]=draft['changes']['customer_id']
        if draft and draft['changes'].get('action')=='add_firm_demand':
            row=draft['changes']['added_demand']
            context['demand_customer_links'][row['id']]=row['customer_id']
        return self.compute().request('POST','/v1/planning/enterprise-solve',
            json={'plan_input':data,'context':context},headers={'Idempotency-Key':key})

    def bootstrap(self):
        with self.lock:
            self.drafts.verify_baseline()
            path=self.root/'baseline-run.json'
            client=self.compute()
            if path.exists():return client.run(json.loads(path.read_text())['solve_run_id'])
            run=self.submit(self.drafts.baseline,'p1-baseline-'+self.drafts.digest)
            with path.open('x',encoding='utf-8') as stream:json.dump(run,stream,ensure_ascii=False,indent=2)
            return run

    def baseline_id(self):
        path=self.root/'baseline-run.json'
        if not path.exists():raise AgentError('BASELINE_NOT_READY')
        return json.loads(path.read_text())['solve_run_id']

    def view(self,run_id):
        with planning_read_scope(self) as reads:
            if run_id not in reads['views']:
                if reads.get('reuse_immutable') and self.enterprise and read_run(self,run_id).get('state')=='SUCCEEDED':
                    from p22.query_reads import immutable_read
                    identity=dict(run_id=run_id,**{k:self.enterprise[k] for k in ('plan_id','data_version_id','snapshot_sha256')})
                    reads['views'][run_id]=immutable_read(self,artifact_root()/run_id,identity,'authenticated-view-v1',
                        lambda manifest:self._authenticated_view(run_id))
                    reads['views'][run_id]['run']=read_run(self,run_id)
                else:reads['views'][run_id]=self._authenticated_view(run_id)
            # draft_view and enterprise_view add target-specific fields.
            return copy.deepcopy(reads['views'][run_id])

    def certified_artifacts(self,run_id,target='baseline',*,fresh=False):
        """Exact native result members for a lightweight query; not a full business audit.

        A manifest-sealed successful immutable run is authenticated on cold use.
        Subsequent reads reuse that certification. ``view`` remains the strict
        per-read byte audit and ``fresh=True`` rechecks these original members.
        """
        from p22.query_reads import immutable_read,artifact_members
        from p22.contracts import digest
        if not re.fullmatch(r'API-[a-f0-9]{16}',run_id):raise InputError('RUN_NOT_IN_PLAN')
        run=read_run(self,run_id)
        if run.get('solve_run_id')!=run_id or run.get('state')!='SUCCEEDED':raise InputError('ANALYSIS_RUN_NOT_SUCCEEDED')
        expected_id=self.baseline_id() if target=='baseline' else self.drafts.get(target).get('run_id')
        if expected_id!=run_id:raise InputError('RUN_NOT_IN_PLAN')
        identity=dict(run_id=run_id,**{k:self.enterprise[k] for k in ('plan_id','data_version_id','snapshot_sha256')})
        folder=artifact_root()/run_id
        def build(manifest):
            values=artifact_members(folder,manifest,('canonical_input.json','PlanResult.json','enterprise-context.json'))
            data=values['canonical_input.json'];result=values['PlanResult.json'];context=values['enterprise-context.json']
            expected=self.drafts.baseline if target=='baseline' else self.drafts.get(target)['input']
            scenario=self.enterprise['plan_id'] if target=='baseline' else target
            if (data!=expected or data.get('scenario_id')!=scenario or result.get('scenario_id')!=scenario
                or result.get('solve_run_id')!=run_id or result.get('input_hash')!=digest(data)
                or run.get('input_hash')!=result['input_hash']
                or any(context.get(k)!=identity[k] for k in ('plan_id','data_version_id','snapshot_sha256'))):
                raise InputError('RUN_SOURCE_MISMATCH')
            context_seal=hashlib.sha256(json.dumps(context,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
            if manifest.get('context_sha256')!=context_seal:raise InputError('RUN_SOURCE_MISMATCH')
            return dict(data=data,result=result,context=context,manifest=manifest,
                certification_scope='MANIFEST_BOUND_CORE_RESULT_MEMBERS;FULL_BUSINESS_AUDIT_ON_DEMAND')
        value=immutable_read(self,folder,identity,'core-result-v1:'+target,build,fresh=fresh)
        if run.get('input_hash')!=value['result']['input_hash']:raise InputError('RUN_SOURCE_MISMATCH')
        return value

    def _authenticated_view(self,run_id):
        client=self.compute();run=read_run(self,run_id)
        if self.enterprise:
            folder=artifact_root()/run_id
            try:
                context=json.loads((folder/'enterprise-context.json').read_text(encoding='utf-8'))
                if any(context.get(k)!=self.enterprise[k] for k in ['plan_id','data_version_id','snapshot_sha256']):raise ValueError()
                if run['state']=='SUCCEEDED':
                    manifest=json.loads((folder/'run-manifest.json').read_text(encoding='utf-8'))
                    if any(manifest.get(k)!=self.enterprise[k] for k in ['plan_id','data_version_id','snapshot_sha256']):raise ValueError()
                    for name in ['canonical_input.json','enterprise-context.json','native_input.json','PlanResult.json']:
                        if hashlib.sha256((folder/name).read_bytes()).hexdigest()!=manifest['files'][name]:raise ValueError()
                    for name,digest in manifest['files'].items():
                        if name.startswith('raw/'):
                            path=(folder/name).resolve()
                            if not path.is_relative_to(folder.resolve()):raise ValueError()
                            if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError()
            except OSError:raise AgentError('RUN_EVIDENCE_UNAVAILABLE: 原生归档成员暂不可读取；计算记录保留，本次未重新认证结果。') from None
            except (ValueError,KeyError):raise AgentError('RUN_SOURCE_MISMATCH: 计算记录缺少有效的企业版本来源，请基于已确认版本重新创建情景') from None
        if run['state']!='SUCCEEDED':
            data=json.loads((artifact_root()/run_id/'canonical_input.json').read_text(encoding='utf-8'))
            view={'run':run,'interpretation':business_status(run['state'])}
            details=read_editor_failure_details(artifact_root()/run_id,run,data)
            if details is not None:view['failure_details']=details
            view['context']=build_plan_context(data,view)
            return view
        result=client.result(run_id)
        folder=artifact_root()/run_id
        data=json.loads((folder/'canonical_input.json').read_text(encoding='utf-8'))
        if 'foundation_projection' in data or 'plan_editor_contract' in data or 'plan_editor_hypothesis' in data:
            # A file's presence is not source authentication. Every attachment
            # used below must belong to this run's authenticated manifest,
            # including optional captures and the input-readback tables.
            try:
                manifest=json.loads((folder/'run-manifest.json').read_text(encoding='utf-8'))
                if manifest.get('run_id')!=run_id:raise ValueError()
                names={'raw/'+name+'.json' for name in RAW_TABLES}
                names.update('raw/'+name for name in ('fixed_batch_pre.json','fixed_batch_post.json',
                    'distributionorder.json','fixed_batch_pre.xml','fixed_batch_post.xml') if (folder/'raw'/name).is_file())
                names.update(path.relative_to(folder).as_posix() for path in (folder/'raw').glob('imported-*.json'))
                for name in names:
                    path=(folder/name).resolve()
                    if not path.is_relative_to(folder.resolve()):raise ValueError()
                    if hashlib.sha256(path.read_bytes()).hexdigest()!=manifest['files'][name]:raise ValueError()
            except (OSError,ValueError,KeyError):
                raise AgentError('RUN_SOURCE_MISMATCH: 企业或排程编辑运行附件未列入同一运行清单或摘要不一致') from None
        try:raw={name:json.loads((folder/'raw'/(name+'.json')).read_text(encoding='utf-8')) for name in RAW_TABLES}
        except (OSError,ValueError):raise AgentError('ORDER_EVIDENCE_INCOMPLETE: 原始运行文件缺失或不可读，无法核对订单证据；请核对归档，不会用其他运行替代') from None
        for name in ['fixed_batch_pre','fixed_batch_post','distributionorder']:
            path=folder/'raw'/(name+'.json')
            if path.is_file():raw[name]=json.loads(path.read_text(encoding='utf-8'))
        if 'foundation_projection' in data:
            for phase in ['pre','post']:
                path=folder/'raw'/('fixed_batch_'+phase+'.xml')
                if path.is_file():raw['fixed_batch_'+phase+'_xml']=path.read_text(encoding='utf-8')
        native_path=folder/'native_input.json'
        native=json.loads(native_path.read_text(encoding='utf-8')) if native_path.is_file() else None
        imported={p.stem.removeprefix('imported-'):json.loads(p.read_text(encoding='utf-8')) for p in (folder/'raw').glob('imported-*.json')}
        # Current runtime metadata must not be rebound to a historical run.
        metadata={'engine_reported_version':result.get('engine',{}).get('runtime_version')}
        provenance_path=folder/'EngineProvenance-v1.json';manifest_path=folder/'run-manifest.json'
        if provenance_path.is_file() and manifest_path.is_file():
            manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
            expected=manifest.get('files',{}).get('EngineProvenance-v1.json')
            if expected:
                if hashlib.sha256(provenance_path.read_bytes()).hexdigest()!=expected:
                    raise AgentError('RUN_SOURCE_MISMATCH: 引擎来源记录摘要不一致')
                metadata=json.loads(provenance_path.read_text(encoding='utf-8'))
        if 'foundation_projection' in data:
            encoded=[json.dumps(v,sort_keys=True,ensure_ascii=False,separators=(',',':'))
                     for v in (data,result,raw,native,imported,metadata)]
            semantic=copy.deepcopy(_foundation_interpretation_cached(*encoded))
        else:
            semantic=audit_result(data,result,raw,native_input=native,loaded_input=imported,runtime_metadata=metadata)
        from accessory_supply import enabled as accessory_enabled
        if accessory_enabled(data):
            semantic['accessory_supply']=semantic.get('material_validation',{}).get('accessory_supply_validation')
            if data.get('inventory_decision_catalog'):
                semantic['inventory_decision']=(semantic['accessory_supply'] or {}).get('inventory_policy_validation')
        if self.enterprise and self.enterprise.get('execution_checkpoint'):
            from execution_checkpoint import project_validation
            checkpoint=project_validation(data,self.enterprise['execution_checkpoint'],semantic['material_validation'])
            semantic.update(execution_checkpoint=checkpoint['summary'],checkpoint_validation=checkpoint)
            if checkpoint['status']=='FAIL':semantic.update(business_status='INVALID_SCHEDULE',schedule_check='FAIL')
            elif checkpoint['status']=='NOT_CHECKED' and semantic['business_status']!='INVALID_SCHEDULE':semantic['business_status']='VALIDATION_INCOMPLETE'
        artifact=folder/('BusinessInterpretation-'+semantic['interpretation_version']+'.json')
        if not artifact.exists():
            with artifact.open('x',encoding='utf-8') as stream:json.dump(semantic,stream,ensure_ascii=False,indent=2)
        view={'run':run,'result':result,'interpretation':semantic}
        # Historical PlanResult and its hash remain immutable. This projection
        # uses the saved run, never the engine's mutable current workspace.
        view['order_evidence']=read_order_evidence(data,result,folder,{k:v for k,v in raw.items() if not k.endswith('_xml')})
        view['context']=build_plan_context(data,view)
        return view

    def baseline_view(self):return self.view(self.baseline_id())

    def manufacturing_opportunities(self,order_id):
        from manufacturing_opportunities import discover
        with self.lock:
            self.drafts.verify_baseline()
            result=discover(self.drafts.baseline,self.baseline_view(),order_id)
            if self.enterprise:
                result.update({k:self.enterprise[k] for k in ['plan_id','data_version_id','snapshot_sha256']})
            return result

    def run_draft(self,draft_id):
        with self.lock:
            draft=self.drafts.for_run(draft_id)
            if draft['changes'].get('action')=='simulate_plan_editor':
                if draft['run_id']:return self.compute().run(draft['run_id'])
                preflight=getattr(self,'plan_editor_preflight',None)
                if not callable(preflight):raise AgentError('EDITOR_RUN_PREFLIGHT_REQUIRED')
                preflight(draft)
            if draft['changes'].get('action')=='simulate_professional_policy':
                if draft['run_id']:return self.compute().run(draft['run_id'])
                preflight=getattr(self,'professional_policy_preflight',None)
                if not callable(preflight):raise AgentError('PROFESSIONAL_PREFLIGHT_REQUIRED')
                preflight(draft)
            if draft['changes'].get('action')=='simulate_outstanding_receipt':
                if draft['run_id']:return self.compute().run(draft['run_id'])
                preflight=getattr(self,'outstanding_receipt_preflight',None)
                if not callable(preflight):raise AgentError('OUTSTANDING_RECEIPT_RUN_PREFLIGHT_REQUIRED')
                preflight(draft)
            if draft['changes'].get('action')=='simulate_inventory_decision':
                if draft['run_id']:return self.compute().run(draft['run_id'])
                preflight=getattr(self,'inventory_decision_preflight',None)
                if not callable(preflight):raise AgentError('INVENTORY_DECISION_RUN_PREFLIGHT_REQUIRED')
                preflight(draft)
            if draft['changes'].get('action')=='simulate_controlled_conditions':
                if draft['run_id']:return self.compute().run(draft['run_id'])
                preflight=getattr(self,'controlled_conditions_preflight',None)
                if not callable(preflight):
                    raise AgentError('CONTROLLED_RUN_PREFLIGHT_REQUIRED: 企业条件试算缺少当前来源核验')
                preflight(draft)
            if draft['changes'].get('action')=='simulate_distribution_leadtime':
                if draft['run_id']:return self.compute().run(draft['run_id'])
                preflight=getattr(self,'distribution_leadtime_preflight',None)
                if not callable(preflight):raise AgentError('DISTRIBUTION_RUN_PREFLIGHT_REQUIRED')
                preflight(draft)
            if draft['changes'].get('action')=='simulate_resource_capacity':
                if draft['run_id']:return self.compute().run(draft['run_id'])
                preflight=getattr(self,'resource_capacity_preflight',None)
                if not callable(preflight):raise AgentError('RESOURCE_CAPACITY_RUN_PREFLIGHT_REQUIRED')
                preflight(draft)
            baseline=self.baseline_view()
            if baseline['run']['state']!='SUCCEEDED':raise AgentError('BASELINE_NOT_READY')
            if draft['run_id']:return self.compute().run(draft['run_id'])
            run=self.submit(draft['input'],'p1-draft-'+draft_id,draft=draft)
            self.drafts.attach_run(draft_id,run['solve_run_id'])
            return run

    def draft_view(self,draft_id):
        draft=self.drafts.get(draft_id)
        from plan_editor import verify as verify_editor
        editor=verify_editor(self.drafts.baseline,draft)
        if editor:draft=self.drafts.for_run(draft_id)
        from p22.professional_policy import verify as verify_professional
        professional=verify_professional(self.drafts.baseline,draft)
        if professional:draft=self.drafts.for_run(draft_id)
        from routing_capacity_scenario import verify as verify_capacity
        capacity=False if editor else verify_capacity(self.drafts.baseline,draft)
        from distribution_scenario import verify as verify_distribution
        distribution=False if editor else verify_distribution(self.drafts.baseline,draft)
        from controlled_conditions import verify as verify_controlled
        from inventory_decision_scenario import verify as verify_inventory
        from multi_receipt_scenario import verify as verify_outstanding
        outstanding=False if editor else verify_outstanding(self.drafts.baseline,draft)
        inventory=False if editor or outstanding else verify_inventory(self.drafts.baseline,draft)
        controlled=False if editor or inventory or outstanding else verify_controlled(self.drafts.baseline,draft)
        if distribution or capacity or controlled or inventory or outstanding:draft=self.drafts.for_run(draft_id)
        from approved_simulation import verify_scope,INTENT
        verify_scope(self.drafts.baseline,draft)
        if not draft['run_id']:
            view={'draft':self.public_draft(draft),'run':{'state':'NOT_RUN'},'interpretation':business_status('NOT_RUN')}
            view['context']=build_plan_context(draft['input'],view,str(self.root/'draft-inputs'/(draft['id']+'.json')))
            return view
        view=self.view(draft['run_id'])
        view['draft']=self.public_draft(draft)
        if view['run']['state']=='SUCCEEDED':
            if editor:view['plan_editor_changes']=copy.deepcopy(draft['changes'])
            base_id=draft['changes']['confirmation']['identity']['baseline_run_id'] if distribution or capacity or controlled or inventory or outstanding else self.baseline_id()
            view['diff']=self.compute().diff(base_id,draft['run_id'])
            if editor:
                from plan_editor import comparison
                view['diff']['plan_editor']=comparison(self.baseline_view(),view,draft['changes'])
            if draft['changes']['intent']==INTENT:
                from approved_simulation import comparison
                view['diff'].update(comparison(self.baseline_view(),view,draft['changes'],self.drafts.baseline['items']))
            if distribution:view['diff']['distribution_leadtime']=copy.deepcopy(draft['changes'])
            if capacity:view['diff']['resource_capacity']=copy.deepcopy(draft['changes'])
            if outstanding:
                view['diff']['outstanding_receipt_simulation']=copy.deepcopy(draft['changes'])
                from multi_receipt_supply import summary as receipt_summary
                view['diff']['receipt_protection']={'base':receipt_summary(self.drafts.baseline),
                    'candidate':receipt_summary(draft['input']),
                    'simulation_only':True,'execution_authorized':False}
                from confirmed_supply import receipt_windows
                before={p['id']:p for p in receipt_windows(self.drafts.baseline)}
                view['diff']['confirmed_supplies']=[{'reference':p['id'],'base':before[p['id']],'candidate':p,
                    'receipt_shift_seconds':int((datetime.fromisoformat(p['physical_receipt_at'])-datetime.fromisoformat(before[p['id']]['physical_receipt_at'])).total_seconds()),
                    'availability_shift_seconds':int((datetime.fromisoformat(p['available_at'])-datetime.fromisoformat(before[p['id']]['available_at'])).total_seconds()),
                    'scope':'OUTSTANDING_BALANCE_ONLY','hypothesis_kind':'PLANNING_ASSUMPTION'}
                    for p in receipt_windows(draft['input'])]
            if draft['changes']['intent']=='确认采购延期模拟':
                from confirmed_supply import receipt_windows
                before={r['id']:r for r in receipt_windows(self.drafts.baseline)}
                view['diff']['confirmed_supplies']=[{'reference':r['id'],'base':before[r['id']],'candidate':r,
                    'receipt_shift_seconds':int((datetime.fromisoformat(r['physical_receipt_at'])-datetime.fromisoformat(before[r['id']]['physical_receipt_at'])).total_seconds()),
                    'availability_shift_seconds':int((datetime.fromisoformat(r['available_at'])-datetime.fromisoformat(before[r['id']]['available_at'])).total_seconds())}
                    for r in receipt_windows(draft['input'])]
            if professional:
                from p22.professional_outcome import outcome,assessment_eligible
                assessment=outcome(self,self.drafts.baseline,draft,self.baseline_view(),view)
                view['professional_assessment']=assessment
                if not assessment_eligible(assessment):
                    view.pop('diff',None)
                    view['business_summary']='本候选的专业审计或原订单逐期交付保护未通过，不能作为合法接单、补货建议或可比方案。请在原专业页面核对实际受损和缺失证据。'
        return view

    @staticmethod
    def public_draft(draft):return {k:v for k,v in draft.items() if k!='input'}

    def decide_draft(self,draft_id,decision):
        with self.lock:return self.public_draft(self.drafts.decide(draft_id,decision))

    def conversation_history(self,session_id):
        if not re.fullmatch('[a-f0-9]{32}',session_id):raise AgentError('INVALID_SESSION: 会话标识格式无效')
        record=self.sessions.get_existing(session_id)
        if record is None:raise AgentError('SESSION_NOT_FOUND: 当前计划没有这份会话')
        messages=[]
        for i,message in enumerate(record['history']):
            if message.get('role')=='user':role='user';text=message.get('content','')
            elif message.get('role')=='tool':
                try:text=json.loads(message.get('content','{}')).get('message','')
                except (ValueError,TypeError):continue
                role='assistant'
            else:continue
            if isinstance(text,str) and text:messages.append({'id':session_id+':'+str(i),'role':role,'text':redact(text)})
        return {'session_id':session_id,'messages':messages,'pending_request':json.loads(redact(json.dumps(record['pending'],ensure_ascii=False)))}

    def selected_view(self,target='baseline'):
        with planning_read_scope(self):
            view=self.baseline_view() if target=='baseline' else self.draft_view(target)
            return self.enterprise_view(view)

    def enterprise_view(self,view):
        if view.get('draft',{}).get('changes',{}).get('simulation_only') and 'context' in view:
            from approved_simulation import AUTHORITY
            changes=view['draft']['changes']
            if changes.get('action')=='simulate_plan_editor':
                simulation_identity={'action':changes['action'],'references':[e['reference'] for e in changes['edits']],
                    'edits':copy.deepcopy(changes['edits'])}
            elif changes.get('action')=='enable_accessory_supply':
                simulation_identity={'source_id':changes['source_id']}
            elif changes.get('action')=='delay_confirmed_purchase':
                simulation_identity={'purchase_reference':changes['reference']}
            elif changes.get('action')=='add_firm_demand':
                simulation_identity={'demand_id':changes['added_demand']['id'],'hypothesis':changes['hypothesis']}
            elif changes.get('action')=='set_forecast_gross_override':
                bucket=changes['bucket']
                simulation_identity={'forecast_id':bucket['forecast_id'],'forecast_version_id':bucket['forecast_version_id'],'bucket':bucket}
            elif changes.get('action')=='set_foundation_forecast_gross':
                from foundation_forecast import verify_candidate
                verify_candidate(self.drafts.baseline,self.drafts.get(view['draft']['id']))
                bucket=changes['bucket']
                simulation_identity={'action':changes['action'],'forecast_id':bucket['forecast_id'],
                    'forecast_version_id':bucket['forecast_version_id'],'bucket':copy.deepcopy(bucket),
                    'policy_status':'NET_UNKNOWN','worker_eligible':False}
            elif changes.get('action')=='simulate_professional_policy':
                simulation_identity={'action':changes['action'],'conditions':copy.deepcopy(changes['parameters']),
                    'policy_status':'RESEARCH_POLICY','execution_authorized':False}
            elif changes.get('action')=='simulate_outstanding_receipt':
                simulation_identity={'action':changes['action'],'purchase_reference':changes['reference'],
                    'conditions':copy.deepcopy(changes['conditions']),'scope':'OUTSTANDING_BALANCE_ONLY'}
            elif changes.get('action')=='simulate_distribution_leadtime':
                simulation_identity={'action':changes['action'],'lane_id':changes['reference'],'conditions':copy.deepcopy(changes['conditions'])}
            elif changes.get('action')=='simulate_resource_capacity':
                simulation_identity={'action':changes['action'],'resource_id':changes['reference'],'conditions':copy.deepcopy(changes['conditions'])}
            elif changes.get('action')=='simulate_inventory_decision':
                simulation_identity={'action':changes['action'],'conditions':copy.deepcopy(changes['conditions'])}
            elif changes.get('action')=='simulate_controlled_conditions':
                simulation_identity={'action':changes['action'],'conditions':copy.deepcopy(changes['conditions']),
                    'order':copy.deepcopy(changes['order']),'operator':copy.deepcopy(changes['operator'])}
            else:
                simulation_identity={'demand_id':changes['demand_id']} if changes.get('action')=='set_demand_priority' else {'source_id':changes['source_id']} if changes.get('source_id') else {'resource_id':changes['resource_id']} if changes.get('resource_id') else {'reference':changes['reference']}
            view['context']['simulation']={**AUTHORITY,**simulation_identity}
        if self.enterprise and 'context' in view:
            c=view['context']
            if c.get('data_kind')=='REAL_ODOO_ENTERPRISE_FACTS':c['source_kind']=c['data_kind']
            c['data_kind']='IMPORTED_ENTERPRISE_SNAPSHOT'
            c['enterprise_source']={k:self.enterprise[k] for k in ['plan_id','data_version_id','snapshot_sha256','source_sha256']}
            c['customers']=self.enterprise['customers']
            c['demand_customer_links']=dict(self.enterprise['demand_customer_links'])
            if view.get('draft',{}).get('changes',{}).get('customer_id'):
                changes=view['draft']['changes'];c['demand_customer_links'][changes['demand_id']]=changes['customer_id']
            if view.get('draft',{}).get('changes',{}).get('action')=='add_firm_demand':
                row=view['draft']['changes']['added_demand'];c['demand_customer_links'][row['id']]=row['customer_id']
            c['plan_version'].update(c['enterprise_source'])
        if 'context' in view:
            from p22.continuity_read_model import continuity_wip_context
            continuity_wip=continuity_wip_context(view.get('interpretation'),view.get('run',{}).get('solve_run_id'))
            if continuity_wip is not None:
                view['context']['continuity_wip']=continuity_wip
            from planning_capabilities import profile_id,profile_capabilities
            view['context']['capabilities']=profile_capabilities(view['context']['manufacturing_profile'])
            from inventory_decision_scenario import available as inventory_available
            inventory_data=None
            # This optional projection must not add store/replay requirements to
            # legacy profile views or enterprise versions without the catalog.
            inventory_store=getattr(self,'drafts',None)
            from plan_editor import available as editor_available
            if inventory_store is not None and editor_available(inventory_store.baseline):
                view['context']['capabilities']['actions']['plan_editor'].update(status='AVAILABLE_WITH_EVIDENCE',reason='受控研究合同内的基线建议调整，原保护与审计保持。')
            if inventory_store is not None and not editor_available(inventory_store.baseline):
                view['context']['capabilities']['actions']['plan_editor'].update(status='UNSUPPORTED',reason='当前版本没有已认证的可编辑工单或冻结研究任务合同。')
            if inventory_store is not None and inventory_available(inventory_store.baseline):
                inventory_data=inventory_store.baseline if not view.get('draft') else inventory_store.for_run(view['draft']['id'])['input']
            if inventory_data is not None:
                from inventory_decision import summary as inventory_summary
                view['context']['inventory_decision']=inventory_summary(inventory_data)
                view['context']['inventory_decision']['native_policy_evidence']=copy.deepcopy(view.get('interpretation',{}).get('inventory_decision'))
            else:
                view['context']['capabilities']['actions']['inventory_decision'].update(status='UNSUPPORTED',reason='当前版本没有已确认的库存与来源条款目录。')
            from multi_receipt_supply import enabled as receipts_enabled,summary as receipt_summary
            if inventory_store is not None and receipts_enabled(inventory_store.baseline):
                receipts_data=inventory_store.baseline if not view.get('draft') else inventory_store.for_run(view['draft']['id'])['input']
                view['context']['receipt_protection']=receipt_summary(receipts_data)
                receipt_material=copy.deepcopy(view.get('interpretation',{}).get('material_validation'))
                view['context']['receipt_protection']['material_validation']=receipt_material
                # Compatibility alias is explicitly a material/receipt check.
                # Resource feasibility is a separate existing qualified audit.
                view['context']['receipt_protection']['validation']=({**copy.deepcopy(receipt_material),
                    'scope':'MATERIAL_RECEIPT_AND_PROTECTED_MO_FIELDS_ONLY',
                    'source_scope':receipt_material.get('scope'),
                    'excludes':['resource_capacity','named_operator_qualification','LoadPlan_feasibility']}
                    if receipt_material is not None else None)
            if self.enterprise and self.enterprise.get('execution_checkpoint'):
                for rule in view['context']['capabilities']['actions'].values():
                    rule.update(status='UNSUPPORTED',reason='CHECKPOINT_BASELINE_ONLY: 执行结转版本仅认证下一轮基线')
                view['context']['checkpoint_scope']='NEXT_BASELINE_ONLY'
            from routing_capacity import enabled as routing_enabled
            if inventory_store is not None and routing_enabled(inventory_store.baseline):
                stage=next(i for i in inventory_store.baseline['items'] if i['kind']=='FINISHED')
                view['context']['wip']={'stage_endpoint':{'kind':'STAGE_OUTPUT','item_id':stage['id'],
                    'uom':stage['base_uom'],'label':'声明的制造阶段产出；不证明完整成衣或客户实际收货'}}
            view['business_summary']=business_summary(view['context'])
            if view['context'].get('simulation'):
                simulation=view['context']['simulation']
                label='专业排程联合调整' if simulation.get('action')=='simulate_plan_editor' else '运输提前期条件试算' if simulation.get('action')=='simulate_distribution_leadtime' else '设备可用条件试算' if simulation.get('action')=='simulate_resource_capacity' else '未收采购余额到货条件试算' if simulation.get('action')=='simulate_outstanding_receipt' else '库存政策与有来源补给条件试算' if simulation.get('action')=='simulate_inventory_decision' else '受控条件影响试算' if simulation.get('action')=='simulate_controlled_conditions' else '确认采购预期到货延期' if simulation.get('purchase_reference') else '新增需求影响试算' if simulation.get('hypothesis') else '预测毛需求覆盖' if simulation.get('forecast_id') else '需求优先级' if simulation.get('demand_id') else '资源不可用' if simulation.get('resource_id') else '供应来源条件' if simulation.get('source_id') else '指定 approved 工单窗口'
                view['business_summary']+='\n'+label+'的隔离模拟，待业务确认，未获实际执行授权。'
        return view

    def plan_context(self,target='baseline'):
        return self.selected_view(target)['context']

    def order_evidence(self,identifier,target='baseline'):
        view=self.selected_view(target)
        projection=view.get('order_evidence')
        if not projection:raise AgentError('ORDER_EVIDENCE_NOT_READY: 当前方案没有可读取的订单证据')
        try:order=find_order(projection,identifier)
        except KeyError:raise AgentError('ORDER_NOT_FOUND: 当前方案没有唯一匹配的订单，请提供完整订单行编号') from None
        return {k:v for k,v in projection.items() if k!='orders'}|{'order':order,'message':explain_order(projection,identifier),'target':target}

    def pending_note(self,pending,current_request=False):
        if not pending:return ''
        labels={'order_id':'订单编号','item_id':'产品','quantity':'数量','due_at':'明确到时间的交期','demand_id':'目标订单','shift_days':'交期变更条件','customer_id':'已确认的客户编码'}
        missing='、'.join(labels.get(k,k) for k in pending['missing_fields'])
        return ('\n该项变更仍待确认' if current_request else '\n另有一项变更仍待确认')+'，尚未创建订单或提交计算；请补充：'+missing+'。'

    def confirmed_urgent_fields(self,message,args,old=None):
        known=dict(old['known_fields']) if old and old['intent']=='ADD_URGENT_ORDER' else {}
        compact=re.sub(r'\s+','',message)
        for key,value in args.items():
            if key=='quantity' and re.search(r'(?<![0-9])'+re.escape(value)+r'(?![0-9])',compact):known[key]=value
            elif key=='order_id' and value in message:known[key]=value
            elif key=='item_id' and (value in message or ('同款' in message and len(self.drafts.baseline['production_recipes'])==1)):known[key]=value
            elif key=='customer_id' and value in message and self.enterprise and value in self.drafts.customers:known[key]=value
            elif key=='due_at':
                try:
                    value=self.drafts.date(value);dt=datetime.fromisoformat(value)
                    day=any(token in compact for token in [dt.strftime('%Y-%m-%d'),f'{dt.year}年{dt.month}月{dt.day}日',f'{dt.month}月{dt.day}日'])
                    clock=any(token in compact for token in [f'{dt.hour}:{dt.minute:02d}',f'{dt.hour:02d}:{dt.minute:02d}']) or (dt.minute==0 and any(token in compact for token in [f'{dt.hour}点',f'{dt.hour}时']))
                    if day and clock:known[key]=value
                except DraftError:pass
        return known

    def remember_clarification(self,session_id,args,message=''):
        intent=args.get('intent','UNKNOWN')
        if intent not in {'ADD_URGENT_ORDER','CHANGE_DUE_DATE'}:return self.sessions.get(session_id)['pending']
        old=self.sessions.get(session_id)['pending']
        known=dict(old['known_fields']) if old and old['intent']==intent else {}
        required=['order_id','item_id','quantity','due_at']+(['customer_id'] if self.enterprise else [])
        allowed=set(required) if intent=='ADD_URGENT_ORDER' else {'demand_id','shift_days','due_at'}
        fields={k:v for k,v in args.get('known_fields',{}).items() if k in allowed}
        known=self.confirmed_urgent_fields(message,fields,old) if intent=='ADD_URGENT_ORDER' else {**known,**fields}
        missing=[k for k in required if not known.get(k)] if intent=='ADD_URGENT_ORDER' else [k for k in ['demand_id'] if not known.get(k)]
        if intent=='CHANGE_DUE_DATE' and known.get('due_at') is None and known.get('shift_days') is None:missing.append('due_at')
        if not missing:return old
        return self.sessions.set_pending(session_id,intent,known,missing)

    def state(self,session_id=None):
        if session_id is not None and (len(session_id)!=32 or any(c not in '0123456789abcdef' for c in session_id)):raise AgentError('INVALID_SESSION')
        try:baseline=self.baseline_view()
        except AgentError as exc:
            status='NOT_RUN' if str(exc)=='BASELINE_NOT_READY' else 'UNAVAILABLE'
            baseline={'error':str(exc),'interpretation':business_status(status)}
            if status=='UNAVAILABLE':
                baseline['run']={'state':status,'failure_code':str(exc).split(':',1)[0]}
                try:baseline['run']['solve_run_id']=self.baseline_id()
                except (AgentError,OSError,ValueError,KeyError):pass
        try:
            from agent import ModelConfig
            config=ModelConfig.from_env();model={'configured':True,'provider':config.provider,'model':config.model}
        except AgentError:model={'configured':False,'provider':os.environ.get('PLANNING_MODEL_PROVIDER','deepseek')}
        return {'baseline':self.enterprise_view(baseline),'drafts':[self.public_draft(r) for r in self.drafts.list()],
                'model':model,'planning_at':self.drafts.baseline['planning_at'],'timezone':self.drafts.baseline['site']['timezone'],
                'baseline_sha256':self.drafts.digest,'data_kind':'IMPORTED_ENTERPRISE_SNAPSHOT' if self.enterprise else 'SYNTHETIC_FACTORY',
                'pending_request':self.sessions.get(session_id)['pending'] if session_id else None}

    def chat(self,message,session_id=None,target='baseline',selected_order_id=None):
        if not isinstance(message,str) or not message.strip() or len(message)>4000:raise AgentError('INVALID_MESSAGE')
        # Never send known credentials to a model, even if pasted into a message.
        message=redact(message)
        if session_id is None:session_id=uuid.uuid4().hex
        if len(session_id)!=32 or any(c not in '0123456789abcdef' for c in session_id):raise AgentError('INVALID_SESSION')
        self.drafts.verify_baseline()
        baseline=self.baseline_view()
        if baseline['run']['state']!='SUCCEEDED':raise AgentError('BASELINE_NOT_READY')
        current=self.enterprise_view(baseline if target=='baseline' else self.draft_view(target))
        selected_order=None
        if selected_order_id:
            selected_order=self.order_evidence(selected_order_id,target)['order']['demand_id']
        session=self.sessions.get(session_id)
        context={'current_target':target,'current_plan':current['context'],
                 'selected_order_id':selected_order,
                 'scenario_base':{'plan_version':baseline['context']['plan_version'],'orders':self.drafts.baseline['demand_lines']},
                 'pending_request':session['pending'],
                 'planning_at':self.drafts.baseline['planning_at'],'timezone':self.drafts.baseline['site']['timezone'],
                 'finished_items':[r['output_item_id'] for r in self.drafts.baseline['production_recipes']],
                 'drafts':[self.public_draft(r) for r in self.drafts.list()[:10]]}
        name,args,evidence=interpret(message,context,session['history'])
        if re.search('成本|采购价格|采购费用|采购金额|预算',message):
            outcome={'kind':'REJECTED','message':'当前缺少供应价格与完整成本模型，成本条件尚不能评估，未创建草案或提交计算。不会静默忽略这项要求；如只调整交期，请明确提出不带成本条件的新请求。'}
        elif name in {'propose_due_change','propose_urgent_order'}:
            current_input=self.drafts.baseline if target=='baseline' else self.drafts.get(target)['input']
            confirmed=self.confirmed_urgent_fields(message,args,session['pending']) if name=='propose_urgent_order' else {}
            required=['order_id','item_id','quantity','due_at']+(['customer_id'] if self.enterprise else [])
            missing=[k for k in required if not confirmed.get(k)] if name=='propose_urgent_order' else []
            if missing:
                self.sessions.set_pending(session_id,'ADD_URGENT_ORDER',confirmed,missing)
                outcome={'kind':'CLARIFICATION','message':'部分关键条件尚未由你明确提供，已保留为待确认请求。'}
            elif name=='propose_due_change' and due_change_is_noop(current_input,args):
                outcome={'kind':'NO_CHANGE','decision':{'status':'ALREADY_SATISFIED'},'message':'当前订单交期已经满足该条件，无需修改，也不会重复计算。'}
            else:
                resolved={k:confirmed[k] for k in required} if name=='propose_urgent_order' else args
                draft=self.drafts.propose(name,resolved)
                intent='CHANGE_DUE_DATE' if name=='propose_due_change' else 'ADD_URGENT_ORDER'
                if session['pending'] and session['pending']['intent']==intent:self.sessions.clear_pending(session_id)
                outcome={'kind':'DRAFT','draft':self.public_draft(draft),'confirmed_arguments':resolved,'message':'已识别变更条件，产生基于原始基线的独立草案。请核对后运行情景；基线保持不变。'}
        elif name=='query_order_evidence':
            proof=self.order_evidence(args['demand_id'],target)
            outcome={'kind':'ORDER_EVIDENCE','evidence':proof,'message':proof['message']}
        elif name=='query_plan':
            view=self.selected_view(args['target'])
            outcome={'kind':'PLAN','view':view,'message':business_summary(view['context'])}
        elif name=='compare_plans':
            view=self.selected_view(args['candidate_id'])
            text='与基线的真实差异：\n'+business_summary(view['context']) if 'diff' in view else '该草案尚无可比较的计算结果，请先运行情景。'
            if 'diff' in view:
                units={r['id']:r['base_uom'] for r in self.drafts.baseline['items']}
                for row in view['diff']['purchase_groups']:
                    text+='\n采购 '+row['item_id']+'：基线 '+row['base_quantity']+' → 情景 '+row['candidate_quantity']+' '+units[row['item_id']]+'，变化 '+row['quantity_delta']+'。'
            outcome={'kind':'COMPARISON','view':view,'message':text}
        elif name=='check_plan_condition':
            decision=condition_decision(current['context'],args['condition'])
            outcome={'kind':'NO_CHANGE' if decision['status']=='ALREADY_SATISFIED' else 'UNSUPPORTED_CONDITION','decision':decision,'message':decision['message']}
            if decision['status']!='ALREADY_SATISFIED':outcome['message']+='\n'+unsupported_message('OVERTIME_POLICY_CHANGE' if args['condition']=='NO_OVERTIME' else 'RESOURCE_CAPACITY_CHANGE')
        elif name=='ask_clarification':
            pending=self.remember_clarification(session_id,args,message)
            outcome={'kind':'CLARIFICATION','message':'这项变更已保留为待确认请求。' if pending else args['question']}
        else:outcome={'kind':'REJECTED','message':unsupported_message(args.get('capability','OTHER'))}
        pending=self.sessions.get(session_id)['pending']
        outcome['pending_request']=pending
        outcome['message']+=self.pending_note(pending,outcome['kind']=='CLARIFICATION')
        record_id=uuid.uuid4().hex
        record={'id':record_id,'session_id':session_id,'user_message':message,'model':evidence,
                'tool':{'name':name,'arguments':args},'tool_result':outcome,'baseline_sha256':self.drafts.digest}
        folder=self.root/'conversations';folder.mkdir(exist_ok=True)
        with (folder/(record_id+'.json')).open('x',encoding='utf-8') as stream:stream.write(redact(json.dumps(record,ensure_ascii=False,indent=2)))
        completed_call={'id':evidence['tool_call_id'],'type':'function','function':{'name':name,'arguments':json.dumps(args,ensure_ascii=False)}}
        summary={k:v for k,v in outcome.items() if k!='view'}
        if 'evidence' in summary:
            proof=summary['evidence']
            summary['evidence']={k:proof[k] for k in ['solve_run_id','target','projection_hash','source_result_hash']}
            summary['evidence']['demand_id']=proof['order']['demand_id']
        if 'view' in outcome:summary['actual_result']={'business_status':outcome['view']['interpretation']['business_status'],'run_id':outcome['view'].get('run',{}).get('solve_run_id')}
        self.sessions.append_turn(session_id,[
            {'role':'user','content':message},
            {'role':'assistant','content':'','tool_calls':[completed_call]},
            {'role':'tool','tool_call_id':evidence['tool_call_id'],'content':json.dumps(summary,ensure_ascii=False)}])
        return {'record_id':record_id,'session_id':session_id,'tool':record['tool'],'outcome':outcome,
                'model':{'provider':evidence['provider'],'requested_model':evidence['requested_model'],'returned_model':evidence['returned_model'],'response_id':evidence['response_id']}}
