from pathlib import Path
import os
from urllib.parse import urlparse
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict
from service import PlanningService, ComputeClient, ROOT
from agent import AgentError, redact
from drafts import DraftError
from p21.api import register_imports
from p22.api import register_plans
from p22.validation_scope import AnnualValidationScopeMiddleware
from planning_adapter import InputError
from workspace_ui import mount_workspace

class ChatInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    message:str
    session_id:str|None=None
    target:str='baseline'
    selected_order_id:str|None=None

class DecisionInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    decision:str

def local_client(request:Request):
    origin=request.headers.get('origin')
    expected=urlparse(str(request.base_url))
    if request.headers.get('X-Planning-Client')!='p1' or (origin and urlparse(origin).netloc!=expected.netloc):
        raise HTTPException(status_code=403,detail={'code':'LOCAL_CLIENT_REQUIRED'})

def create_app(root=None):
    app=FastAPI(title='AI Native APS',version='0.1.1')
    app.add_middleware(AnnualValidationScopeMiddleware)
    service=PlanningService(root);app.state.service=service
    register_imports(app,Path(root)/'imports' if root else ROOT/'evidence/imports')
    from p21.connector_api import register_connectors
    register_connectors(app)
    register_plans(app,Path(root)/'p22' if root else ROOT/'evidence/p22')
    def scoped(plan_id=None):return service if plan_id is None else app.state.enterprise_plans.service(plan_id)
    from diagnosis.api import register_diagnosis
    register_diagnosis(app,service.root,scoped,local_client)
    def guarded(action):
        try:return action()
        except (AgentError,DraftError,InputError) as exc:
            code=str(exc).split(':',1)[0]
            status=404 if code.endswith('NOT_FOUND') else 503 if code.startswith(('MODEL_','COMPUTE_')) or code=='BASELINE_NOT_READY' else 422
            raise HTTPException(status_code=status,detail={'code':code,'message':redact(exc)}) from None
    from assistant_api import register as register_assistant
    register_assistant(app,app.state.enterprise_plans,local_client)
    from professional_queries import register as register_professional_queries
    register_professional_queries(app,app.state.enterprise_plans,guarded)
    from assistant_http_access import register as register_agent_access
    register_agent_access(app)
    from simulation_api import register as register_simulation
    register_simulation(app,app.state.enterprise_plans,local_client)
    from interop.api import register as register_interop
    register_interop(app,app.state.enterprise_plans,local_client)
    @app.get('/health')
    def health():
        return {'status':'OK','scope':'DRAFT_ONLY','diagnosis_available':False,'web_model_available':False}
    @app.get('/v1/state')
    def state(session_id:str|None=None,plan_id:str|None=None):return guarded(lambda:scoped(plan_id).state(session_id))
    @app.get('/v1/plan-context')
    def plan_context(target:str='baseline',plan_id:str|None=None):return guarded(lambda:scoped(plan_id).plan_context(target))
    @app.get('/v1/session-history')
    def session_history(plan_id:str,session_id:str):return guarded(lambda:scoped(plan_id).conversation_history(session_id))
    @app.get('/v1/order-evidence')
    def order_evidence(demand_id:str,target:str='baseline',plan_id:str|None=None):return guarded(lambda:scoped(plan_id).order_evidence(demand_id,target))
    @app.post('/v1/bootstrap',dependencies=[Depends(local_client)])
    def bootstrap(plan_id:str|None=None):return guarded(lambda:scoped(plan_id).bootstrap())
    @app.post('/v1/chat',dependencies=[Depends(local_client)])
    def chat(body:ChatInput,plan_id:str|None=None):
        raise HTTPException(410,detail={'code':'WEB_MODEL_REMOVED','message':'本项目已移除Web模型功能，请使用业务操作或现有MCP入口。'})
    @app.post('/v1/drafts/{draft_id}/run',dependencies=[Depends(local_client)])
    def run(draft_id:str,plan_id:str|None=None):return guarded(lambda:scoped(plan_id).run_draft(draft_id))
    @app.get('/v1/drafts/{draft_id}/result')
    def result(draft_id:str,plan_id:str|None=None):return guarded(lambda:scoped(plan_id).selected_view(draft_id))
    @app.post('/v1/drafts/{draft_id}/decision',dependencies=[Depends(local_client)])
    def decide(draft_id:str,body:DecisionInput,plan_id:str|None=None):return guarded(lambda:scoped(plan_id).decide_draft(draft_id,body.decision))
    def recovery_action(action):
        try:return action()
        except AgentError as exc:
            code=str(exc).split(':')[0]
            raise HTTPException(503 if code.startswith('COMPUTE_') else 422 if code=='RECOVERY_INVALID_CONFIRMATION' else 409,
                                detail={'code':code,'message':redact(exc)}) from None
    @app.get('/v1/operations/recovery')
    def recovery_state():return recovery_action(lambda:ComputeClient().request('GET','/v1/operations/recovery'))
    @app.post('/v1/operations/recovery/inspect',dependencies=[Depends(local_client)])
    def inspect_recovery():return recovery_action(lambda:ComputeClient().request('POST','/v1/operations/recovery/inspect'))
    @app.post('/v1/operations/recovery/resolve',dependencies=[Depends(local_client)])
    def resolve_recovery(body:dict):return recovery_action(lambda:ComputeClient().request('POST','/v1/operations/recovery/resolve',json=body))
    @app.get('/')
    def index():return RedirectResponse('/workspace/',status_code=307)
    from professional_data import register_professional_data
    register_professional_data(app)
    mount_workspace(app,Path(__file__).resolve().parent/'frontend/dist')
    return app

app=create_app(os.environ.get('PLANNING_STATE_DIR') or None)
