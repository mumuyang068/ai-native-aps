from fastapi import Depends,Request,HTTPException
from pydantic import BaseModel,ConfigDict,Field
from p21.api import local_import_client
from p22.intelligence_service import IntelligenceOwner
from planning_adapter import InputError

class IntelligenceAction(BaseModel):
    model_config=ConfigDict(extra='forbid')
    data_version_id:str=Field(min_length=1,max_length=100)
    baseline_run_id:str=Field(pattern='^API-[a-f0-9]{16}$')
    run_id:str=Field(pattern='^API-[a-f0-9]{16}$')
    target:str=Field(pattern='^(baseline|DRAFT-[a-f0-9]{16})$')
    action:str=Field(min_length=1,max_length=60)
    payload:dict
    request_id:str=Field(pattern='^[a-f0-9]{32}$')

class SourceResearchAction(BaseModel):
    model_config=ConfigDict(extra='forbid')
    data_version_id:str=Field(min_length=1,max_length=100)
    action:str=Field(min_length=1,max_length=60)
    payload:dict
    request_id:str=Field(pattern='^[a-f0-9]{32}$')

def register(app,plans,guarded):
    def run(fn):
        def call():
            try:return fn()
            except ValueError as error:raise InputError(str(error)) from None
            except KeyError as error:
                if error.args==('PROMISING_QUOTE_NOT_FOUND',):
                    raise HTTPException(404,detail={'code':'PROMISING_QUOTE_NOT_FOUND'}) from None
                raise
        return guarded(call)
    @app.get('/v1/enterprise-plans/{pid}/source-workspace',dependencies=[Depends(local_import_client)])
    def source_workspace(request:Request,pid:str,data_version_id:str):
        if set(request.query_params)!={'data_version_id'} or len(request.query_params.getlist('data_version_id'))!=1:raise HTTPException(422,detail={'code':'UNKNOWN_QUERY_FIELD'})
        return run(lambda:plans.source_workspace(pid,data_version_id))
    @app.get('/v1/enterprise-plans/{pid}/intelligence/source',dependencies=[Depends(local_import_client)])
    def source_overview(request:Request,pid:str,data_version_id:str):
        if set(request.query_params)!={'data_version_id'} or len(request.query_params.getlist('data_version_id'))!=1:raise HTTPException(422,detail={'code':'UNKNOWN_QUERY_FIELD'})
        return run(lambda:IntelligenceOwner(plans,pid,source_mode=True).overview(dict(data_version_id=data_version_id)))
    @app.post('/v1/enterprise-plans/{pid}/intelligence/source-actions',dependencies=[Depends(local_import_client)])
    def source_action(pid:str,body:SourceResearchAction):
        def execute():
            owner=IntelligenceOwner(plans,pid,source_mode=True);identity=dict(data_version_id=body.data_version_id)
            result=owner.action(identity,body.action,body.payload,body.request_id)
            _,full=owner.scope(identity)
            aliases={}
            if body.action in {'forecast_confirm','forecast_override','forecast_restore'}:aliases['version_id']=result['id']
            if body.action=='inventory_recommend':aliases['inventory_id']=result['id']
            return dict(result,**aliases,identity=full,simulation_only=True,execution_authorized=False)
        return run(execute)
    @app.get('/v1/enterprise-plans/{pid}/intelligence',dependencies=[Depends(local_import_client)])
    def overview(request:Request,pid:str,data_version_id:str,baseline_run_id:str,run_id:str,target:str):
        allowed={'data_version_id','baseline_run_id','run_id','target'}
        if set(request.query_params)!=allowed or any(len(request.query_params.getlist(k))!=1 for k in allowed):raise HTTPException(422,detail={'code':'UNKNOWN_QUERY_FIELD'})
        return run(lambda:IntelligenceOwner(plans,pid).overview(dict(data_version_id=data_version_id,baseline_run_id=baseline_run_id,run_id=run_id,target=target)))
    @app.post('/v1/enterprise-plans/{pid}/intelligence/actions')
    def action(pid:str,body:IntelligenceAction,_=Depends(local_import_client)):
        value=body.model_dump();kind=value.pop('action');payload=value.pop('payload');key=value.pop('request_id')
        def execute():
            owner=IntelligenceOwner(plans,pid)
            _,identity=owner.scope(value,allow_discarded_release=kind=='quote_release')
            result=owner.action(value,kind,payload,key)
            aliases={}
            if kind in {'forecast_confirm','forecast_override','forecast_restore'}:aliases['version_id']=result['id']
            if kind=='inventory_recommend':aliases['inventory_id']=result['id']
            if kind.startswith('quote_') and 'id' in result:aliases['quote']=result
            return dict(result,**aliases,identity=identity,simulation_only=True,execution_authorized=False)
        return run(execute)
