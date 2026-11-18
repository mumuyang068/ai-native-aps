"""Same product import workspace, same local Operator/Agent access boundary."""
from fastapi import Depends,HTTPException,Request
from pydantic import BaseModel,ConfigDict,Field
from p21.api import local_import_client
from p21.connector_service import configured_services
from urllib.parse import urlparse
from assistant_authorization import COOKIE


class PreviewInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    checkpoint:str=Field(min_length=1,max_length=40)
    request_id:str|None=Field(default=None,pattern=r'^SYNC-[a-f0-9]{32}$')


class CommitInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    preview_id:str=Field(pattern=r'^SYNC-[a-f0-9]{32}$')
    revision:int=Field(strict=True,ge=1)
    reviewer:str=Field(min_length=1,max_length=100)
    groups:list[str]=Field(min_length=1,max_length=40)


def register_connectors(app):
    registry={};configuration_error=None
    try:registry=configured_services(app.state.imports)
    except (ValueError,OSError,KeyError,TypeError):configuration_error='CONNECTOR_CONFIGURATION_INVALID'
    app.state.connector_registry=registry
    app.state.connectors=registry.get('demo-garment')
    def require(identifier='demo-garment'):
        if identifier not in registry:
            if identifier!='demo-garment':raise HTTPException(404,detail=dict(code='CONNECTOR_NOT_FOUND'))
            raise HTTPException(503,detail=dict(code=configuration_error or 'CONNECTOR_NOT_CONFIGURED',message='演示数据源尚未配置或配置无效，请核对本地来源配置。'))
        return registry[identifier]
    def guarded(action):
        try:return action()
        except (ValueError,OSError,KeyError,TypeError) as exc:
            code=str(exc).split(':',1)[0]
            # Do not echo OS errors, raw rows, filesystem paths or config secrets.
            if not code or not all(c.isupper() or c.isdigit() or c=='_' for c in code):code='CONNECTOR_SYNC_FAILED'
            raise HTTPException(409 if 'CONFLICT' in code or 'COMMIT' in code else 422,
                detail=dict(code=code,message='同步未完成，请查看同步记录并核对来源；上一合法企业版本仍保留。')) from None
    def source_operator(identifier,request):
        from p21.odoo_connector import OdooConnectorService
        if not isinstance(require(identifier),OdooConnectorService):return None
        origin=urlparse(request.headers.get('origin',''));base=urlparse(str(request.base_url))
        if not origin.netloc or (origin.scheme,origin.netloc)!=(base.scheme,base.netloc):
            raise HTTPException(403,detail=dict(code='SAME_ORIGIN_REQUIRED'))
        authority=getattr(app.state,'agent_operators',None)
        principal=authority.principal(request.cookies.get(COOKIE)) if authority else None
        if not principal or getattr(request.state,'agent_principal',None) or request.headers.get('authorization'):
            raise HTTPException(403,detail=dict(code='SOURCE_OPERATOR_REQUIRED',message='请先验证现有研究确认身份。'))
        return principal
    @app.get('/v1/connectors')
    def catalog():
        if not registry:return dict(connectors=[],configuration_error=configuration_error or 'CONNECTOR_NOT_CONFIGURED')
        return guarded(lambda:dict(connectors=[s.get_sync_status() for s in registry.values()]))
    @app.get('/v1/connectors/{identifier}')
    def status(identifier:str):return guarded(lambda:require(identifier).get_sync_status())
    @app.get('/v1/connectors/{identifier}/history')
    def history(identifier:str):return guarded(lambda:dict(history=require(identifier).get_sync_status()['history']))
    @app.get('/v1/connectors/{identifier}/syncs/{sync_id}',dependencies=[Depends(local_import_client)])
    def sync_record(identifier:str,sync_id:str):
        service=require(identifier)
        if not callable(getattr(service,'get_sync_record',None)):raise HTTPException(404,detail=dict(code='SYNC_TASK_NOT_FOUND'))
        return guarded(lambda:service.get_sync_record(sync_id))
    @app.get('/v1/connectors/{identifier}/comparison',dependencies=[Depends(local_import_client)])
    def comparison(identifier:str,previous_version_id:str,current_version_id:str,include_intermediate:bool=False):
        from p21.connector_compare import compare_checkpoints
        return guarded(lambda:compare_checkpoints(require(identifier),app.state.enterprise_plans,previous_version_id,current_version_id,include_intermediate=include_intermediate))
    @app.post('/v1/connectors/{identifier}/test',dependencies=[Depends(local_import_client)])
    def test(identifier:str,request:Request):
        source_operator(identifier,request)
        service=require(identifier)
        action=service.test_connection if callable(getattr(service,'test_connection',None)) else service.adapter.test_connection
        return guarded(action)
    @app.post('/v1/connectors/{identifier}/preview',dependencies=[Depends(local_import_client)])
    def preview(identifier:str,body:PreviewInput,request:Request):
        source_operator(identifier,request)
        service=require(identifier)
        if body.request_id:
            if not callable(getattr(service,'get_sync_record',None)):raise HTTPException(422,detail=dict(code='CONNECTOR_REQUEST_ID_NOT_SUPPORTED'))
            return guarded(lambda:service.preview_sync(body.checkpoint,request_id=body.request_id))
        return guarded(lambda:service.preview_sync(body.checkpoint))
    @app.post('/v1/connectors/{identifier}/commit',dependencies=[Depends(local_import_client)])
    def commit(identifier:str,body:CommitInput,request:Request):
        if getattr(request.state,'agent_principal',None) is not None or request.headers.get('authorization'):
            raise HTTPException(403,detail=dict(code='AGENT_CONFIRMATION_CHANNEL_DENIED'))
        principal=source_operator(identifier,request)
        if principal:return guarded(lambda:require(identifier).commit_sync(body.preview_id,revision=body.revision,reviewer=body.reviewer,groups=body.groups,authorization=principal))
        return guarded(lambda:require(identifier).commit_sync(body.preview_id,revision=body.revision,reviewer=body.reviewer,groups=body.groups))
