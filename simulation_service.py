"""Separate sandbox Owner. APS can read visible views, never sealed truth."""
import hmac
from fastapi import Depends,FastAPI,HTTPException,Request
from pydantic import BaseModel,ConfigDict,Field


class Control(BaseModel):
    model_config=ConfigDict(extra='forbid')
    payload:dict
    expected_revision:int=Field(strict=True,ge=0)
    idempotency_key:str=Field(min_length=1,max_length=200)


def create_app(branches,*,read_token,operator_token):
    app=FastAPI(title='Virtual Enterprise Visible Sources',version='0.1')
    def authenticate(request,expected):
        supplied=request.headers.get('authorization','')
        if not hmac.compare_digest(supplied,'Bearer '+expected):raise HTTPException(403,detail={'code':'SIM_SOURCE_AUTHORIZATION_REQUIRED'})
    def reader(request:Request):authenticate(request,read_token)
    def operator(request:Request):authenticate(request,operator_token)
    def owner(branch):
        if branch not in branches:raise HTTPException(404,detail={'code':'SIM_BRANCH_NOT_FOUND'})
        return branches[branch]
    def invoke(action):
        try:return action()
        except (ValueError,KeyError,TypeError) as exc:
            code=str(exc).split(':',1)[0]
            if not code or not all(c.isupper() or c.isdigit() or c=='_' for c in code):code='SIM_REQUEST_REJECTED'
            raise HTTPException(409,detail={'code':code}) from None
    @app.get('/health')
    def health():return dict(status='OK',mode='RESEARCH_SIMULATION_ONLY',branches=len(branches),enterprise_execution_authorized=False)
    @app.get('/v1/sim/{branch}/view',dependencies=[Depends(reader)])
    def view(branch:str,revision:int|None=None):return invoke(lambda:owner(branch).read_view(read_token=read_token,revision=revision))
    @app.get('/v1/sim/{branch}/source-checkpoint',dependencies=[Depends(reader)])
    def source_checkpoint(branch:str,revision:int|None=None):
        def capture():
            visible=owner(branch).read_view(read_token=read_token,revision=revision)
            return dict(schema='source-checkpoint/1',**{key:visible[key] for key in
                ['enterprise_id','branch_id','revision','as_of','declarations','source_cursor']})
        return invoke(capture)
    @app.get('/v1/sim/{branch}/changes',dependencies=[Depends(reader)])
    def changes(branch:str,system:str,revision:int,previous_as_of:str):
        return invoke(lambda:owner(branch).changes(system,read_token=read_token,revision=revision,previous_as_of=previous_as_of))
    @app.get('/v1/sim/{branch}/verify',dependencies=[Depends(reader)])
    def verify(branch:str,revision:int):return invoke(lambda:owner(branch).verify_view(revision,read_token=read_token))
    @app.get('/v1/sim/{branch}/status',dependencies=[Depends(operator)])
    def status(branch:str):return invoke(lambda:owner(branch).status())
    @app.get('/v1/sim/{branch}/events',dependencies=[Depends(operator)])
    def events(branch:str,after_sequence:int=0):return invoke(lambda:dict(events=owner(branch).events(operator_token=operator_token,after_sequence=after_sequence)))
    @app.get('/v1/sim/{branch}/trace',dependencies=[Depends(operator)])
    def trace(branch:str,order_id:str):return invoke(lambda:owner(branch).trace(order_id,operator_token=operator_token))
    @app.get('/v1/sim/{branch}/audit',dependencies=[Depends(operator)])
    def audit(branch:str):return invoke(lambda:owner(branch).export_audit(operator_token=operator_token))
    @app.get('/v1/sim/{branch}/publication/{command_id}',dependencies=[Depends(reader)])
    def publication(branch:str,command_id:str):return invoke(lambda:owner(branch).publication_receipt(command_id,read_token=read_token))
    @app.post('/v1/sim/{branch}/{action}',dependencies=[Depends(operator)])
    def control(branch:str,action:str,body:Control):
        def perform():
            live=owner(branch);common=dict(operator_token=operator_token,expected_revision=body.expected_revision,idempotency_key=body.idempotency_key)
            if action=='advance':return live.advance(body.payload['to'],**common)
            if action=='order':return live.add_order(body.payload,**common)
            if action=='supply':return live.perturb_supply(body.payload,**common)
            if action=='adopt':return live.adopt(body.payload,**common)
            if action=='publication':return live.publish(body.payload,**common)
            if action=='confirm-mo':return live.confirm_manufacturing_order(body.payload,**common)
            if action=='withdraw-execution-plan':return live.withdraw_published_execution(body.payload,**common)
            if action=='execution-segment':return live.record_execution_segment(body.payload,**common)
            if action=='execution-start':return live.record_execution_start(body.payload,**common)
            if action=='execution-interruption':return live.record_operation_interruption(body.payload,**common)
            if action=='finished-receipt':return live.record_finished_goods_receipt(body.payload,**common)
            if action=='shipment':return live.record_shipment(body.payload,**common)
            if action=='cancel':return live.cancel(body.payload['action_id'],**common)
            if action=='checkpoint':return live.checkpoint(body.payload['label'],**common)
            if action=='restore':return live.restore(body.payload['checkpoint_id'],**common)
            raise ValueError('SIM_ACTION_UNSUPPORTED')
        return invoke(perform)
    return app
