from pathlib import Path
from urllib.parse import urlparse
from fastapi import FastAPI,Request,Depends,HTTPException
from fastapi.responses import FileResponse,RedirectResponse
from pydantic import BaseModel,ConfigDict,Field
from agent import AgentError,redact
from p21.store import ImportStore,ImportError
from p21.validation import GROUP_NAMES,headers_for,PROFILE
import base64,json

class Revision(BaseModel):
    model_config=ConfigDict(extra='forbid')
    revision:int=Field(ge=1)

class Edit(Revision):change:dict
class Confirmation(Revision):
    reviewer:str
    groups:list[str]
class ProposalDecision(Revision):decision:str
class SuggestionInput(Revision):note:str=Field(default='',max_length=1500)
class VersionSave(Revision):
    parent_version_id:str|None=None
    sync_receipt:dict|None=None

def local_import_client(request:Request):
    origin=request.headers.get('origin')
    if request.headers.get('X-Planning-Client')!='p1' or (origin and urlparse(origin).netloc!=urlparse(str(request.base_url)).netloc):
        raise HTTPException(status_code=403,detail={'code':'LOCAL_CLIENT_REQUIRED'})

def business_view_query(request:Request,*,plan=False):
    allowed={'start','end','item_id','location_id','kind','status','offset','page_size','section','order_id'}|({'data_version_id'} if plan else set())
    if set(request.query_params)-allowed or any(len(request.query_params.getlist(k))>1 for k in allowed):
        raise HTTPException(422,detail={'code':'BUSINESS_QUERY_FIELDS_INVALID'})
    try:return {k:int(v) if k in {'offset','page_size'} else v for k,v in request.query_params.items() if k!='data_version_id'}
    except ValueError:raise HTTPException(422,detail={'code':'BUSINESS_QUERY_PAGE_LIMIT'}) from None

def register_imports(app,root):
    store=ImportStore(root);app.state.imports=store
    def guarded(action):
        try:return action()
        except (ImportError,AgentError) as e:
            code=str(e).split(':',1)[0]
            raise HTTPException(status_code=409 if code=='REVISION_CONFLICT' else 404 if code.endswith('NOT_FOUND') else 503 if code.startswith('MODEL_') else 422,
                detail={'code':code,'message':redact(e)}) from None
        except (KeyError,TypeError,ValueError,IndexError):
            raise HTTPException(422,detail={'code':'INVALID_IMPORT_INPUT','message':'修改信息不完整或格式有误，请重新选择字段、单元格和内容。'}) from None
    def source_context(task_id):
        for service in getattr(app.state,'connector_registry',{}).values():
            if callable(getattr(service,'import_context',None)):
                context=service.import_context(task_id)
                if context:return context
        # A missing/broken runtime connector configuration cannot turn a
        # persisted native source task into an ordinary upload. Corrections
        # retain the old provenance even if someone edited the bundle field.
        task=guarded(lambda:store.get(task_id))
        locations=task.get('canonical',{}).get('tables',{}).get('locations',[])
        native=any(r.get('odoo_bundle') is not None or r.get('source_origin')=='REAL_ODOO_NOT_SIMULATOR' for r in locations)
        native=native or any(c.get('table')=='locations' and
            (c.get('field')=='odoo_bundle' and c.get('previous_value') is not None or
             c.get('field')=='source_origin' and c.get('previous_value')=='REAL_ODOO_NOT_SIMULATOR')
            for c in task.get('corrections',[]))
        if native:return dict(status='SOURCE_UNAVAILABLE',enterprise_id=task['canonical']['enterprise_id'])
        return None
    def ordinary_confirmation(task_id):
        if source_context(task_id):raise HTTPException(403,detail={'code':'SOURCE_CONFIRMATION_CHANNEL_REQUIRED','message':'请在对应企业来源中确认，以核对最新修订并更新同步状态。'})
    @app.get('/v1/imports')
    def list_imports():return store.list()
    @app.get('/v1/import-schema')
    def schema():return {'groups':GROUP_NAMES,'targets':{name:headers_for(name) for name in [*PROFILE['sheets'],*PROFILE['auxiliary'],'工厂参数','使用说明']}}
    @app.post('/v1/import-packages',dependencies=[Depends(local_import_client)])
    async def upload_package(request:Request):
        chunks=[];size=0
        async for chunk in request.stream():
            size+=len(chunk)
            if size>48*1024*1024:raise HTTPException(413,detail={'code':'FILE_TOO_LARGE','message':'来源包超过48MB上限'})
            chunks.append(chunk)
        def parse():
            body=json.loads(b''.join(chunks));files=body['files']
            if set(body)!={'files'} or not isinstance(files,list) or not 1<=len(files)<=32:raise ImportError('SOURCE_PACKAGE_LIMIT: 请选择1至32份有明确来源映射的原件')
            if any(not isinstance(f,dict) or set(f)!={'filename','content_base64'} for f in files):raise ImportError('INVALID_SOURCE_INPUT: 原件仅接收文件名及原始内容')
            return store.upload_package([(f['filename'],base64.b64decode(f['content_base64'],validate=True)) for f in files])
        return guarded(parse)
    @app.post('/v1/imports',dependencies=[Depends(local_import_client)])
    async def upload(request:Request,filename:str):
        chunks=[];size=0
        async for chunk in request.stream():
            size+=len(chunk)
            if size>10*1024*1024:raise HTTPException(413,detail={'code':'FILE_TOO_LARGE','message':'文件超过 10 MB，请拆分上传'})
            chunks.append(chunk)
        return guarded(lambda:store.upload(filename,b''.join(chunks)))
    @app.post('/v1/structured-imports',dependencies=[Depends(local_import_client)])
    async def structured_upload(request:Request):
        chunks=[];size=0
        async for chunk in request.stream():
            size+=len(chunk)
            if size>48*1024*1024:raise HTTPException(413,detail={'code':'SOURCE_PACKAGE_TOO_LARGE'})
            chunks.append(chunk)
        raw=b''.join(chunks)
        return guarded(lambda:store.upload_structured(json.loads(raw),raw=raw))
    @app.get('/v1/imports/{task_id}')
    def task(task_id:str):
        value=guarded(lambda:store.get(task_id));context=source_context(task_id)
        return value|{'source_sync':context} if context else value
    @app.get('/v1/imports/{task_id}/original')
    def original(task_id:str):
        t=guarded(lambda:store.get(task_id))
        if t.get('source_package'):raise HTTPException(422,detail={'code':'SOURCE_PACKAGE_ORIGINALS','message':'来源包有多个原件，请按文件下载'})
        return FileResponse(store.original(task_id),filename=t['filename'])
    @app.get('/v1/imports/{task_id}/originals/{file_sha256}')
    def source_original(task_id:str,file_sha256:str):
        p=guarded(lambda:store.source_original(task_id,file_sha256));t=store.get(task_id)
        filename=next(f['filename'] for f in t['source_package']['files'] if f['sha256']==file_sha256)
        return FileResponse(p,filename=filename)
    @app.patch('/v1/imports/{task_id}',dependencies=[Depends(local_import_client)])
    def edit(task_id:str,body:Edit):
        ordinary_confirmation(task_id)
        return guarded(lambda:store.edit(task_id,body.revision,body.change))
    @app.post('/v1/imports/{task_id}/confirm',dependencies=[Depends(local_import_client)])
    def confirm(task_id:str,body:Confirmation):
        ordinary_confirmation(task_id)
        return guarded(lambda:store.confirm(task_id,body.revision,body.reviewer,body.groups))
    @app.post('/v1/imports/{task_id}/validate',dependencies=[Depends(local_import_client)])
    def validate(task_id:str,body:Revision):
        def check():
            task=store.get(task_id);store._expect(task,body.revision);return task
        return guarded(check)
    @app.post('/v1/imports/{task_id}/suggest',dependencies=[Depends(local_import_client)])
    def suggest(task_id:str,body:SuggestionInput):
        raise HTTPException(410,detail={'code':'WEB_MODEL_REMOVED','message':'本项目已移除Web模型字段建议；原始资料、人工核对及历史记录继续保留。'})
    @app.post('/v1/imports/{task_id}/proposals/{proposal_id}',dependencies=[Depends(local_import_client)])
    def decide(task_id:str,proposal_id:str,body:ProposalDecision):return guarded(lambda:store.proposal_decision(task_id,body.revision,proposal_id,body.decision))
    @app.post('/v1/imports/{task_id}/versions',dependencies=[Depends(local_import_client)])
    def save(task_id:str,body:VersionSave):
        ordinary_confirmation(task_id)
        return guarded(lambda:store.save_version(task_id,body.revision,
            parent_version_id=body.parent_version_id,sync_receipt=body.sync_receipt))
    @app.get('/v1/data-versions')
    def versions():return store.versions()
    @app.get('/v1/data-versions/{version_id}')
    def version(version_id:str):return guarded(lambda:store.version(version_id))
    @app.get('/v1/data-versions/{version_id}/business-facts')
    def business_facts(version_id:str):return guarded(lambda:store.business_facts(version_id))
    @app.get('/v1/data-versions/{version_id}/business-view',dependencies=[Depends(local_import_client)])
    def business_view(request:Request,version_id:str):
        query=business_view_query(request)
        return guarded(lambda:store.business_view(version_id,**query))
    @app.get('/imports')
    def entry():return RedirectResponse('/workspace/#/imports',status_code=307)

def create_import_app(root):
    app=FastAPI(title='企业生产资料导入',version='0.2.1');register_imports(app,root);return app
