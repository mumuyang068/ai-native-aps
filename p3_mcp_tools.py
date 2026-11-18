"""Thin MCP boundary over the P3 HTTP API. No Agent, solver or scenario ledger."""
import argparse
import functools
import json
from pathlib import Path
import time
from urllib.parse import urlsplit,quote
import uuid
import requests
from assistant_sensitive import SafeArgumentParser, SensitiveOutput, private_process_diagnostics


LEGACY_TOOL_NAMES = frozenset({'get_plan_context','create_due_draft','create_purchase_delay_draft',
    'find_manufacturing_opportunities','create_approved_mo_draft','run_planning',
    'get_order_run_evidence','get_planning_status','compare_scenarios','create_accessory_supply_draft'})
LEGACY_DRAFT_ACTIONS = frozenset({'propose_due_change','delay_confirmed_purchase',
    'simulate_approved_mo_move','enable_accessory_supply'})


class P3ToolError(ValueError):
    pass


def _audit_json(value):
    """Serialize typed new tool arguments without changing legacy metadata."""
    from pydantic import BaseModel
    if isinstance(value, BaseModel):
        return value.model_dump(mode='json')
    raise TypeError('Unsupported MCP audit value: ' + type(value).__name__)


def _wip_projection(value):
    """Keep an optional WIP description distinct from its authoritative audit."""
    if 'wip' not in value and 'wip_validation' not in value:
        return {}
    projection = {key: value[key] for key in ('wip', 'wip_validation') if key in value}
    validation = value.get('wip_validation')
    if not isinstance(validation, dict) or not validation.get('status'):
        projection['wip_validation'] = {
            'status': 'NOT_CHECKED', 'scope': 'wip_validation_projection',
            'issues': [], 'missing_evidence': ['wip_validation'],
            'not_checked': ['standalone_wip_validation_projection'], 'unsupported': [],
            'reason': 'WIP_VALIDATION_NOT_PROVIDED: A stage/WIP description is not a WIP audit. '
                      'Keep the original material validation and its scope separate.'}
    return projection


class P3Tools:
    def __init__(self,base_url,*,access_file=None):
        parsed=urlsplit(base_url)
        if parsed.scheme!='http' or parsed.hostname not in {'127.0.0.1','localhost'} or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'','/'}:
            raise P3ToolError('LOCAL_P3_URL_REQUIRED')
        self.base=base_url.rstrip('/');self.http=requests.Session();self.output=SensitiveOutput()
        self.http.headers['X-Planning-Client']='p1'
        if access_file is not None:
            try:
                path=Path(access_file)
                if path.stat().st_size>8192:raise ValueError()
                credential=json.loads(path.read_text('utf-8'))
                token=credential['token']
                if not isinstance(token,str) or not 32<=len(token)<=256 or any(c.isspace() for c in token):raise ValueError()
                if credential.get('base_url',self.base).rstrip('/')!=self.base:raise ValueError()
            except (OSError,ValueError,KeyError,TypeError):raise P3ToolError('AGENT_ACCESS_FILE_INVALID') from None
            self.http.headers['Authorization']='Bearer '+token
            self.output=SensitiveOutput((token,))

    def request(self,method,path,body=None):
        if self.output.contains((path,body)):raise P3ToolError('P3_SENSITIVE_ARGUMENT_REJECTED')
        try:reply=self.http.request(method,self.base+path,json=body,timeout=(5,30))
        except requests.RequestException as error:raise P3ToolError('P3_UNAVAILABLE: '+type(error).__name__) from None
        try:value=reply.json()
        except ValueError:raise P3ToolError('P3_INVALID_RESPONSE') from None
        if not reply.ok:
            detail=value.get('detail',{})
            code=detail.get('code','P3_HTTP_'+str(reply.status_code)) if isinstance(detail,dict) else 'P3_INVALID_ARGUMENTS'
            raise P3ToolError(self.output.text(str(code)))
        return self.output.clean(value)

    def context(self,plan_id,*,include_review_notes=True):
        record=self.request('GET','/v1/enterprise-plans/'+quote(plan_id,safe=''))
        state=self.request('GET','/v1/state?plan_id='+quote(plan_id,safe=''))
        baseline=state['baseline'];run=baseline['run'];context=baseline['context']
        source=context.get('enterprise_source',{})
        if record['id']!=plan_id or source.get('plan_id')!=plan_id or record['data_version_id']!=source.get('data_version_id') or record['snapshot_sha256']!=source.get('snapshot_sha256'):
            raise P3ToolError('P3_CONTEXT_MISMATCH')
        snapshot_path='/v1/enterprise-plans/'+quote(plan_id,safe='')+'/snapshot'
        snapshot=self.request('GET',snapshot_path)
        if snapshot['data_version']['id']!=record['data_version_id'] or snapshot['plan_input']['scenario_id']!=plan_id:
            raise P3ToolError('P3_CONTEXT_MISMATCH')
        from confirmed_supply import receipt_windows
        purchases=[p for p in snapshot['plan_input']['existing_orders'] if p['type']=='PO']
        purchase_indexes={p['id']:i for i,p in enumerate(snapshot['plan_input']['existing_orders']) if p['type']=='PO'}
        confirmed=[{**row,'reference':row['id'],'plan_id':plan_id,'data_version_id':record['data_version_id'],
                    'uom':purchases[i]['uom'],
                    'source':{'artifact_ref':snapshot_path,'pointer':'/plan_input/existing_orders/'+str(purchase_indexes[row['id']]),
                              'snapshot_sha256':record['snapshot_sha256']}}
                   for i,row in enumerate(receipt_windows(snapshot['plan_input']))]
        from fixed_batch import enabled,joint_enabled
        fixed=enabled(snapshot['plan_input'])
        from planning_capabilities import profile_id,supports,profile_capabilities
        profile=profile_id(snapshot['plan_input'])
        protected_receipts=profile=='multi-receipt-protected-manufacturing-v1'
        checkpoint_only=context.get('checkpoint_scope')=='NEXT_BASELINE_ONLY' or bool(context.get('execution_checkpoint'))
        can_move=not checkpoint_only and supports(profile,'actions','approved_mo_move')
        can_due=not checkpoint_only and supports(profile,'actions','due_change');can_purchase=not checkpoint_only and supports(profile,'actions','purchase_delay')
        can_accessory=not checkpoint_only and supports(profile,'actions','accessory_supply')
        manufacturing=[{**row,'reference':row['id'],'plan_id':plan_id,'data_version_id':record['data_version_id'],
                        'source':{'artifact_ref':snapshot_path,'pointer':'/plan_input/existing_orders/'+str(i),
                                  'snapshot_sha256':record['snapshot_sha256']}}
                       for i,row in enumerate(snapshot['plan_input']['existing_orders']) if (fixed or protected_receipts or profile=='foundation-planning-projection-v1') and row['type']=='MO']
        value={**({'accessory_supply':context['accessory_supply']} if context.get('accessory_supply') else {}),**({'forecast_summary':context['forecast_summary'],'net_forecast_deliveries':context.get('net_forecast_deliveries',[])} if context.get('forecast_summary') else {}),**({k:context[k] for k in ['execution_checkpoint','checkpoint_validation']} if context.get('execution_checkpoint') else {}),**_wip_projection(context),**({k:context[k] for k in ['procurement','procurement_validation']} if context.get('procurement') else {}),
                **({k:context[k] for k in ['distribution','distribution_validation']} if context.get('distribution_validation') else {}),
                **({'qualified_resource_validation':context['qualified_resource_validation']} if context.get('qualified_resource_validation') else {}),
                'status':'PRESENT','plan_id':plan_id,'data_version_id':record['data_version_id'],
                'snapshot_sha256':record['snapshot_sha256'],'baseline_run_id':run['solve_run_id'],
                'calculation_status':run['state'],'orders':context['order_summary']['orders'],
                'timezone':context['timezone'],'planning_at':context['planning_at'],
                'validation_scope':context.get('validation_scope'),'drafts':state['drafts'],
                'confirmed_purchases':confirmed,'capabilities':context['capabilities'] if checkpoint_only else profile_capabilities(profile),
                **({'checkpoint_scope':'NEXT_BASELINE_ONLY'} if checkpoint_only else {}),
                'existing_manufacturing_orders':manufacturing,
                'global_moveApprovedEarly':snapshot['plan_input']['rules'].get('move_approved_early'),
                'allowed_change':'ONE_EXISTING_ORDER_DUE_DATE' if can_due else None,'storage':'EXISTING_P3_PLAN_WORKSPACE',
                'allowed_changes':(['ONE_EXISTING_ORDER_DUE_DATE'] if can_due else [])+(['ONE_CONFIRMED_PURCHASE_RECEIPT_DELAY'] if can_purchase else [])+(['ONE_APPROVED_MO_WINDOW_SIMULATION'] if can_move else [])+(['ONE_SOURCED_ACCESSORY_SIMULATION'] if can_accessory else []),
                'permissions':(['READ_PLAN'] if checkpoint_only else ['READ_PLAN','RUN_ISOLATED_DRAFT','COMPARE'])+(['CREATE_DUE_DRAFT'] if can_due else [])+(['CREATE_PURCHASE_DELAY_DRAFT'] if can_purchase else [])+(['FIND_MO_OPPORTUNITIES','CREATE_APPROVED_MO_SIMULATION_DRAFT'] if can_move else [])+(['CREATE_ACCESSORY_SUPPLY_DRAFT'] if can_accessory else []),
                'limits':['Baseline and confirmed enterprise data cannot be edited.','Only one complete unreceived confirmed PO; no partial receipts or status/quantity changes.','Approved-MO simulation is restricted to the fixed-batch window profile and one explicit target; enterprise permission and execution authorization are not granted.','No priority, capacity, procurement strategy, formal purchase editing, approval or publication tools.']}

        if protected_receipts:
            value['limits']=[
                'Baseline, received receipt facts and confirmed enterprise commitments cannot be edited.',
                'P4-15 reads two to eight confirmed PO balances for one RAW supply path; received released, pending-QC and frozen quantities remain separate immutable facts.',
                'The existing MCP purchase-delay write tool still supports complete unreceived POs only; P4-15 outstanding-balance timing hypotheses require the existing P3 preview and explicit confirmation flow.',
                'Confirmed manufacturing identities, quantities, machine/person assignments and windows stay protected; approved-MO adjustment is unsupported in this profile.',
                'Material validation is not resource feasibility; inspect separate qualified-resource and PlanResult hard-constraint evidence. No formal receipt, quality release, procurement execution, approval or publication is authorized.']
        if context.get('receipt_protection'):
            value['receipt_protection']=context['receipt_protection']
        if supports(profile,'analyses','resources') and run['state']=='SUCCEEDED':
            value['manufacturing_summary']=self.manufacturing_summary(plan_id,record['data_version_id'],run['solve_run_id'],'baseline')
        if include_review_notes:
            value['review_notes']=self.review_notes(plan_id,record['data_version_id'],run['solve_run_id'])
        return value

    def overview(self,plan_id,*,draft_id=None):
        """Default Agent context: one bounded original-Owner call, details are explicit queries."""
        path='/v1/enterprise-plans/'+quote(plan_id,safe='')+'/professional-overview'
        if draft_id is not None:path+='?draft_id='+quote(draft_id,safe='')
        value=self.request('GET',path)
        if (value.get('schema_version')!='professional-overview/1' or value.get('plan_id')!=plan_id
            or not value.get('data_version_id') or not value.get('snapshot_sha256')):raise P3ToolError('P3_CONTEXT_MISMATCH')
        return value

    def manufacturing_summary(self,plan_id,data_version_id,run_id,target):
        """Thin read mapping to the existing authenticated period-report service."""
        query='data_version_id='+quote(data_version_id,safe='')+'&run_id='+quote(run_id,safe='')+'&target='+quote(target,safe='')+'&grain=week'
        path='/v1/enterprise-plans/'+quote(plan_id,safe='')+'/reports?'+query
        reports=self.request('GET',path)
        expected=dict(plan_id=plan_id,data_version_id=data_version_id,run_id=run_id,target=target)
        if any(reports.get('identity',{}).get(k)!=v for k,v in expected.items()):raise P3ToolError('SUMMARY_SCOPE_MISMATCH')
        component=reports.get('manufacturing',{'status':'NOT_COLLECTED','validation':{'status':'NOT_CHECKED'},'tasks':[],'orders':[],'rows':[]})
        return {**component,'identity':reports['identity'],'timezone':reports.get('timezone'),'range':reports.get('range'),
                'evidence_files':reports.get('evidence_files',[]),'source_endpoint':path,'read_only':True}

    def bound(self,plan_id,data_version_id,baseline_run_id,*,draft_id=None):
        scope=self.overview(plan_id,draft_id=draft_id)
        if scope['data_version_id']!=data_version_id:raise P3ToolError('DATA_VERSION_MISMATCH')
        if scope['baseline_run_id']!=baseline_run_id:raise P3ToolError('BASELINE_RUN_MISMATCH')
        if scope['calculation_status']!='SUCCEEDED':raise P3ToolError('BASELINE_NOT_READY')
        return scope

    def review_notes(self,plan_id,data_version_id,baseline_run_id,target=None,candidate_run_id=None):
        """Read original review states; keep plan history and exact-candidate reads distinct."""
        query='&'.join(k+'='+quote(v,safe='') for k,v in dict(data_version_id=data_version_id,
            baseline_run_id=baseline_run_id,draft_ids=target if target is not None else '').items())
        path='/v1/enterprise-plans/'+quote(plan_id,safe='')+'/review-notes?'+query
        value=self.request('GET',path)
        expected=dict(plan_id=plan_id,data_version_id=data_version_id,baseline_run_id=baseline_run_id)
        if any(value.get('identity',{}).get(k)!=v for k,v in expected.items()):raise P3ToolError('REVIEW_SCOPE_MISMATCH')
        gates=value['gates']
        if target is not None:
            if any(key!=target for key in gates):raise P3ToolError('REVIEW_SCOPE_MISMATCH')
            gate=gates.get(target)
            exact={**value['identity'],'target':target,'candidate_run_id':candidate_run_id}
            if gate and any(gate.get('identity',{}).get(k)!=v for k,v in exact.items()):raise P3ToolError('REVIEW_SCOPE_MISMATCH')
        notes=[]
        for note in value['notes']:
            if note.get('identity',{}).get('plan_id')!=plan_id:raise P3ToolError('REVIEW_SCOPE_MISMATCH')
            if target is not None and note['identity'].get('target')!=target:continue
            if target is not None and note.get('review_state')=='CURRENT_REVIEW':
                gate=gates.get(target)
                if not gate or note['identity']!=gate['identity'] or note['fingerprint']!=gate['fingerprint']:
                    raise P3ToolError('REVIEW_SCOPE_MISMATCH')
            notes.append(note)
        return {**value,'notes':notes,'ineligible':{k:v for k,v in value['ineligible'].items() if target is None or k==target},
                'source_endpoint':path,'read_only':True}

    def create(self,plan_id,data_version_id,baseline_run_id,order_id,new_due_at,request_id):
        value=self.request('POST','/v1/enterprise-plans/'+quote(plan_id,safe='')+'/due-drafts',
            {'data_version_id':data_version_id,'baseline_run_id':baseline_run_id,'order_id':order_id,'new_due_at':new_due_at,'request_id':request_id})
        return {'status':'CREATED',**value}

    def create_purchase_delay(self,plan_id,data_version_id,baseline_run_id,reference,new_receipt_at,request_id):
        value=self.request('POST','/v1/enterprise-plans/'+quote(plan_id,safe='')+'/purchase-delay-drafts',
            {'data_version_id':data_version_id,'baseline_run_id':baseline_run_id,'reference':reference,
             'new_receipt_at':new_receipt_at,'request_id':request_id})
        return {'status':'CREATED',**value}

    def create_accessory_supply(self,plan_id,data_version_id,baseline_run_id,run_id,target,source_id,reason,request_id):
        value=self.request('POST','/v1/enterprise-plans/'+quote(plan_id,safe='')+'/accessory-supply-drafts',
            {'data_version_id':data_version_id,'baseline_run_id':baseline_run_id,'run_id':run_id,'target':target,
             'source_id':source_id,'reason':reason,'request_id':request_id})
        return {'status':'CREATED',**value}

    def opportunities(self,plan_id,data_version_id,baseline_run_id,order_id):
        self.bound(plan_id,data_version_id,baseline_run_id)
        value=self.request('GET','/v1/enterprise-plans/'+quote(plan_id,safe='')+'/manufacturing-opportunities?order_id='+quote(order_id,safe=''))
        if any(value.get(k)!=v for k,v in {'plan_id':plan_id,'data_version_id':data_version_id}.items()):
            raise P3ToolError('P3_CONTEXT_MISMATCH')
        if value.get('baseline_run_id')!=baseline_run_id:
            if value.get('baseline_run_id') is not None or value.get('status') not in {'UNSUPPORTED_PROFILE','INSUFFICIENT_EVIDENCE'}:
                raise P3ToolError('P3_CONTEXT_MISMATCH')
        return value

    def create_approved_move(self,plan_id,data_version_id,baseline_run_id,reference,new_start_at,request_id):
        value=self.request('POST','/v1/enterprise-plans/'+quote(plan_id,safe='')+'/approved-mo-drafts',
            {'data_version_id':data_version_id,'baseline_run_id':baseline_run_id,'reference':reference,
             'new_start_at':new_start_at,'request_id':request_id})
        return {'status':'CREATED',**value}

    def order_run_evidence(self,plan_id,data_version_id,run_id,order_line_id):
        query='&'.join(k+'='+quote(v,safe='') for k,v in {'data_version_id':data_version_id,'run_id':run_id,'order_line_id':order_line_id}.items())
        return self.request('GET','/v1/enterprise-plans/'+quote(plan_id,safe='')+'/order-run-evidence?'+query)

    def run(self,plan_id,data_version_id,baseline_run_id,draft_id):
        scope=self.bound(plan_id,data_version_id,baseline_run_id,draft_id=draft_id)
        drafts=[draft for draft in scope.get('drafts',[]) if draft.get('id')==draft_id]
        if len(drafts)!=1:raise P3ToolError('DRAFT_NOT_IN_PLAN')
        if drafts[0].get('changes',{}).get('action') not in LEGACY_DRAFT_ACTIONS:
            raise P3ToolError('TASK_BOUND_TOOL_REQUIRED: Use request_run_planning and server user confirmation for professional research drafts.')
        run=self.request('POST','/v1/drafts/'+quote(draft_id,safe='')+'/run?plan_id='+quote(plan_id,safe=''),{})
        return {'status':'SUBMITTED','plan_id':plan_id,'data_version_id':data_version_id,'baseline_run_id':baseline_run_id,'draft_id':draft_id,'run':run,
                'note':'Submission is not completion. Read get_planning_status; repeated run uses the existing draft/run idempotency key.'}

    def result(self,plan_id,data_version_id,baseline_run_id,draft_id,compare=False):
        scope=self.bound(plan_id,data_version_id,baseline_run_id)
        view=self.request('GET','/v1/drafts/'+quote(draft_id,safe='')+'/result?plan_id='+quote(plan_id,safe=''))
        run=view['run'];meaning=view.get('interpretation',{});context=view.get('context',{})
        value={'status':run['state'],'plan_id':plan_id,'data_version_id':data_version_id,'baseline_run_id':baseline_run_id,
               'draft_id':draft_id,'run':run,'draft':view['draft'],'business_status':meaning.get('business_status'),
               'validation_scope':context.get('validation_scope'),'workbench_url':'/workspace/#/plans/'+plan_id+'?target='+draft_id}
        if context.get('receipt_protection'):
            value['receipt_protection']=context['receipt_protection']
        if run['state']=='SUCCEEDED':
            if meaning.get('accessory_supply'):
                value['accessory_supply']=meaning['accessory_supply']
            if meaning.get('distribution_validation'):
                value.update(distribution=meaning.get('distribution'),distribution_validation=meaning['distribution_validation'])
            if meaning.get('qualified_resource_validation'):
                value['qualified_resource_validation']=meaning['qualified_resource_validation']
            if meaning.get('execution_checkpoint'):
                value.update(execution_checkpoint=meaning['execution_checkpoint'],checkpoint_validation=meaning['checkpoint_validation'])
            value.update(_wip_projection(meaning) or _wip_projection(context))
            if meaning.get('procurement'):
                value.update(procurement=meaning['procurement'],procurement_validation=meaning['procurement_validation'])
            value.update(result_hash=view['result']['result_hash'],summary=view.get('business_summary'),
                         material_validation=meaning.get('material_validation'),input_readback=meaning.get('input_readback'),
                         mapping_check=meaning.get('mapping_check','NOT_CHECKED'),schedule_check=meaning.get('schedule_check','NOT_CHECKED'),
                         native_manufacturing_validation=meaning.get('native_manufacturing_validation',{'status':'NOT_CHECKED'}),
                         native_model_validation=meaning.get('native_model_validation',{'status':'NOT_CHECKED'}),
                         engine_provenance=meaning.get('engine_provenance'))
            if compare:
                if 'diff' not in view:raise P3ToolError('COMPARISON_NOT_READY')
                value.update(diff=view['diff'],deliveries=view['result']['deliveries'])
            if scope.get('manufacturing_summary') is not None:
                value['manufacturing_summary']=self.manufacturing_summary(plan_id,data_version_id,run['solve_run_id'],draft_id)
        value['review_notes']=self.review_notes(plan_id,data_version_id,baseline_run_id,draft_id,run.get('solve_run_id'))
        return value


def build(base_url,audit,*,professional_only=False,access_file=None):
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations
    api=P3Tools(base_url,access_file=access_file);session=uuid.uuid4().hex;audit=Path(audit)
    class HostMCP(FastMCP):
        async def run_stdio_async(self):
            from anyio import EndOfStream
            from anyio.abc import ObjectReceiveStream
            from mcp.server.stdio import stdio_server

            async with stdio_server() as (incoming,outgoing):
                class HostReadStream(ObjectReceiveStream):
                    async def receive(self):
                        message=await incoming.receive()
                        if isinstance(message,Exception):
                            return ValueError('P3_INVALID_PROTOCOL_MESSAGE')
                        if api.output.contains(message.message.model_dump(mode='json')):
                            # The SDK echoes request IDs outside call_tool. A
                            # secret cannot be a safe correlation ID: close this
                            # connection before dispatch, without rewriting IDs
                            # or letting the envelope reach a business Owner.
                            raise EndOfStream
                        return message

                    async def aclose(self):
                        await incoming.aclose()

                await self._mcp_server.run(HostReadStream(),outgoing,
                    self._mcp_server.create_initialization_options())

        async def call_tool(self,name,arguments):
            if api.output.contains(arguments):
                return {'status':'ERROR','code':'P3_SENSITIVE_ARGUMENT_REJECTED'}
            try:
                return api.output.clean(await super().call_tool(name,arguments))
            except Exception:
                # Includes framework validation before the wrapper executes.
                # Never forward input_value or an arbitrary Owner traceback.
                return {'status':'ERROR','code':'P3_TOOL_ARGUMENTS_OR_EXECUTION_ERROR',
                        'recovery':'Check the discovered schema and original task state before retrying.'}
    mcp=HostMCP('p3-due-scenario',instructions='Legacy tools keep their historical isolated due-date, confirmed-purchase delay, bounded approved-MO window and frozen sourced-accessory scope. New multi-step professional work must open a task and use task-bound typed tools; persistent and expensive requests wait for server-side user confirmation. MCP cannot approve a request or supply a user confirmation nonce. Read exact current profile capabilities and source permission; creation is NOT_RUN and never formal purchasing. Use exact plan/version/baseline/run/target identities. Forecast, inventory, ATP/CTP and schedule research use existing domain Owners. A candidate or submitted job is not a completed native solve or business audit PASS. Preserve original commitments and global moveApprovedEarly=0. No formal publication, enterprise fact editing, inferred order allocation or Web model.',log_level='WARNING')

    def tool(readonly):
        def register(fn):
            @functools.wraps(fn)
            def call(*args,**kwargs):
                import inspect
                arguments=dict(inspect.signature(fn).bind(*args,**kwargs).arguments);call_id=uuid.uuid4().hex;start=time.monotonic()
                def record(event,**data):
                    audit.parent.mkdir(parents=True,exist_ok=True)
                    with audit.open('a',encoding='utf-8') as output:output.write(json.dumps({'event':event,'at':time.time(),'session_id':session,'call_id':call_id,'tool':fn.__name__,**data},ensure_ascii=False,default=_audit_json)+'\n')
                record('MCP_CALL_BEGIN',arguments=api.output.clean(arguments))
                try:result=fn(*args,**kwargs)
                except P3ToolError as error:result={'status':'ERROR','code':str(error).split(':',1)[0],'message':str(error),'recovery':'Keep the same plan identities and request_id. Inspect P3 state before retrying; do not submit a substitute request.'}
                except Exception as error:
                    record('MCP_CALL_END',elapsed_seconds=time.monotonic()-start,
                           result={'status':'ERROR','code':'P3_TOOL_INTERNAL_ERROR'},
                           error_type=type(error).__name__)
                    return {'status':'ERROR','code':'P3_TOOL_INTERNAL_ERROR',
                            'recovery':'Inspect the original task state; the action outcome may be unknown.'}
                result=api.output.clean(result)
                record('MCP_CALL_END',elapsed_seconds=time.monotonic()-start,result=result)
                return result
            if not (professional_only and fn.__name__ in LEGACY_TOOL_NAMES):
                mcp.tool(annotations=ToolAnnotations(readOnlyHint=readonly,destructiveHint=False,idempotentHint=True,openWorldHint=False))(call)
            return call
        return register

    @tool(True)
    def get_plan_context(plan_id:str)->dict:
        """Read bounded metadata and summary for an explicit P3 plan, baseline run and DATA version. Use its precise query links for order IDs and professional evidence; full snapshots and audits are explicit detail reads. No plan is guessed."""
        return api.overview(plan_id)

    @tool(False)
    def create_due_draft(plan_id:str,data_version_id:str,baseline_run_id:str,order_id:str,new_due_at:str,request_id:str)->dict:
        """Create one existing order's due-date draft in P3. new_due_at must include timezone. request_id is 32 lowercase hex chars; reuse it for lost-response recovery. No compute or formal order edit."""
        return api.create(plan_id,data_version_id,baseline_run_id,order_id,new_due_at,request_id)

    @tool(False)
    def create_purchase_delay_draft(plan_id:str,data_version_id:str,baseline_run_id:str,reference:str,new_receipt_at:str,request_id:str)->dict:
        """Create an isolated delay draft for an existing complete unreceived confirmed PO. Read reference and original physical_receipt_at from get_plan_context; new_receipt_at is a later physical receipt with timezone. The original service computes QC availability. request_id is 32 lowercase hex chars; reuse it for lost-response recovery. Does not edit the formal PO or submit compute."""
        return api.create_purchase_delay(plan_id,data_version_id,baseline_run_id,reference,new_receipt_at,request_id)

    @tool(True)
    def find_manufacturing_opportunities(plan_id:str,data_version_id:str,baseline_run_id:str,order_id:str)->dict:
        """Read the existing D07 service's bounded, evidence-based candidates for a shortfall order. No draft or solve. A candidate is untested, not a guarantee or a delay cause. No candidate when decisive evidence is absent."""
        return api.opportunities(plan_id,data_version_id,baseline_run_id,order_id)

    @tool(False)
    def create_approved_mo_draft(plan_id:str,data_version_id:str,baseline_run_id:str,reference:str,new_start_at:str,request_id:str)->dict:
        """Delegate to the existing D06 single approved-MO window simulation. Read the exact reference and timezone-aware start from a current candidate or explicit user hypothesis. One target only; global moveApprovedEarly stays 0. Other commitments, original facts and quantity stay protected. No compute, approval or publication. Reuse the same 32 lowercase-hex request_id for recovery."""
        return api.create_approved_move(plan_id,data_version_id,baseline_run_id,reference,new_start_at,request_id)

    @tool(False)
    def run_planning(plan_id:str,data_version_id:str,baseline_run_id:str,draft_id:str)->dict:
        """Run the existing isolated P3 draft through its original Worker. Does not create another ledger or solver."""
        return api.run(plan_id,data_version_id,baseline_run_id,draft_id)

    @tool(True)
    def get_order_run_evidence(plan_id:str,data_version_id:str,run_id:str,order_line_id:str)->dict:
        """Read an exact P3-owned run and exact order line through existing R1 readers. Keep shared customer order references separate. PRESENT/EMPTY describes collected records; missing or unreadable evidence is an error. native_tables records are RUN_LEVEL_ONLY, never order allocations or unique causes. No solve, diagnosis runtime, new storage or business changes."""
        return api.order_run_evidence(plan_id,data_version_id,run_id,order_line_id)

    @tool(True)
    def get_planning_status(plan_id:str,data_version_id:str,baseline_run_id:str,draft_id:str)->dict:
        """Read real job status and bounded validation. No implicit solve or retry."""
        return api.result(plan_id,data_version_id,baseline_run_id,draft_id)

    @tool(True)
    def compare_scenarios(plan_id:str,data_version_id:str,baseline_run_id:str,draft_id:str)->dict:
        """Read the original P3 baseline/candidate diff and delivery results. An unsuccessful goal is not proof all alternatives are impossible."""
        return api.result(plan_id,data_version_id,baseline_run_id,draft_id,compare=True)

    @tool(False)
    def create_accessory_supply_draft(plan_id:str,data_version_id:str,baseline_run_id:str,run_id:str,target:str,source_id:str,reason:str,request_id:str)->dict:
        """Create an isolated NOT_RUN draft enabling the one frozen sourced accessory simulation option. Use exact current plan/version/baseline/run/target and source_id from get_plan_context; only the certified enterprise-accessory profile is supported. The original P3 API owns permissions, replay, source terms and identity validation. No change to MOQ, capacity, QC, confirmed PO, inventory, Firm or Forecast; no solve, actual purchase, approval or publication. Reuse one 32 lowercase-hex request_id for lost-response recovery."""
        return api.create_accessory_supply(plan_id,data_version_id,baseline_run_id,run_id,target,source_id,reason,request_id)

    from assistant_tools import register_assistant_tools
    register_assistant_tools(mcp, api, tool)
    from assistant_access import register as register_task_access
    register_task_access(mcp,api,tool)
    return mcp


if __name__=='__main__':
    parser=SafeArgumentParser();parser.add_argument('--base-url',required=True);parser.add_argument('--audit',required=True)
    parser.add_argument('--professional-only',action='store_true',help='Expose only task-bound professional tools; no legacy write surface.')
    parser.add_argument('--access-file',help='Task-local Agent bearer credential file; never an operator credential.')
    args=parser.parse_args()
    try:
        with private_process_diagnostics():
            build(args.base_url,args.audit,professional_only=args.professional_only,access_file=args.access_file).run(transport='stdio')
    except KeyboardInterrupt:pass
    except Exception:
        import sys
        print('MCP_SERVER_STARTUP_OR_CONNECTION_FAILED',file=sys.stderr)
        sys.exit(1)
