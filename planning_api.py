import json
import hmac
import threading
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Header, Depends, Body, Request
from planning_adapter import ROOT, SCHEMA, FreppleClient, InputError, compare_results, engine_config, ENGINE_EVIDENCE
from run_store import RunStore

VALUES=engine_config()
STORE=RunStore(ENGINE_EVIDENCE)
STOP=threading.Event()

def worker():
    client=FreppleClient(base_url=os.environ.get('PLANNING_ENGINE_URL','http://127.0.0.1:9294'))
    while not STOP.is_set():
        job=STORE.claim()
        if not job:
            STOP.wait(.2);continue
        run_id,data=job
        try:
            result=client.solve(data,run_id,STORE.root/'runs'/run_id,context=STORE.context(run_id))
            STORE.finish(run_id,result)
        except Exception as error:STORE.fail(run_id,error)

@asynccontextmanager
async def lifespan(app):
    STORE.stop_interrupted()
    STOP.clear()
    thread=threading.Thread(target=worker,name='p0-serial-engine-worker',daemon=True);thread.start()
    yield
    STOP.set();thread.join(timeout=5)

app=FastAPI(title='Planning P0 compute API',version='0.1.0',lifespan=lifespan)

def authorize(authorization: str|None=Header(default=None)):
    expected='Bearer '+VALUES['P0_API_TOKEN']
    if not authorization or not hmac.compare_digest(authorization,expected):
        raise HTTPException(status_code=401,detail={'code':'UNAUTHORIZED'})

from recovery import register_recovery
register_recovery(app, STORE, authorize)

def error(exc):
    code=str(exc).split(':')[0]
    return HTTPException(status_code=409 if code in {'IDEMPOTENCY_CONFLICT','RESULT_NOT_READY','BASELINE_MISMATCH'} else 422,
        detail={'code':code,'message':str(exc)})

@app.get('/health')
def health():
    return {'status':'OK','worker':'SERIAL','worker_state':STORE.worker_state(),'engine_version':'9.19.0'}

@app.post('/v1/planning/solve',status_code=202,dependencies=[Depends(authorize)])
def solve(data:dict=Body(...),idempotency_key:str=Header(alias='Idempotency-Key')):
    try:return STORE.submit(data,idempotency_key)
    except InputError as exc:raise error(exc)

@app.post('/v1/planning/enterprise-solve',status_code=202,dependencies=[Depends(authorize)])
def enterprise_solve(body:dict=Body(...),idempotency_key:str=Header(alias='Idempotency-Key')):
    try:
        if set(body)!={'plan_input','context'} or not isinstance(body['context'],dict):raise InputError('INVALID_ENTERPRISE_INPUT')
        return STORE.submit(body['plan_input'],idempotency_key,context=body['context'])
    except InputError as exc:raise error(exc)

@app.get('/v1/solve-runs/{run_id}',dependencies=[Depends(authorize)])
def get_run(run_id:str):
    try:return STORE.get(run_id)
    except KeyError:raise HTTPException(status_code=404,detail={'code':'RUN_NOT_FOUND'})

@app.get('/v1/solve-run-metadata',dependencies=[Depends(authorize)])
def run_metadata(request:Request,run_ids:str):
    if set(request.query_params)!={'run_ids'} or len(request.query_params.getlist('run_ids'))!=1:
        raise HTTPException(422,detail={'code':'RUN_METADATA_QUERY_INVALID'})
    try:return STORE.metadata_many(run_ids.split(','))
    except InputError as exc:raise error(exc)

@app.get('/v1/solve-runs/{run_id}/result',dependencies=[Depends(authorize)])
def get_result(run_id:str):
    try:return STORE.result(run_id)
    except KeyError:raise HTTPException(status_code=404,detail={'code':'RUN_NOT_FOUND'})
    except InputError as exc:raise error(exc)

@app.get('/v1/plan-diffs',dependencies=[Depends(authorize)])
def diff(base_run_id:str,candidate_run_id:str):
    try:
        STORE.get(base_run_id);STORE.get(candidate_run_id)
        a=json.loads((STORE.root/'runs'/base_run_id/'canonical_input.json').read_text(encoding='utf-8'))
        b=json.loads((STORE.root/'runs'/candidate_run_id/'canonical_input.json').read_text(encoding='utf-8'))
        if any(a[k]!=b[k] for k in ['snapshot_id','planning_at','engine','rules']):raise InputError('BASELINE_MISMATCH')
        return compare_results(STORE.result(base_run_id),STORE.result(candidate_run_id))
    except (FileNotFoundError,KeyError):raise HTTPException(status_code=404,detail={'code':'RUN_NOT_FOUND'})
    except InputError as exc:raise error(exc)

# Reuse the frozen model definitions in the façade's advertised input contract.
original_openapi=app.openapi
def openapi():
    if app.openapi_schema:return app.openapi_schema
    definition=original_openapi()
    schemas=json.loads(json.dumps(SCHEMA['$defs']).replace('#/$defs/','#/components/schemas/'))
    definition.setdefault('components',{}).setdefault('schemas',{}).update(schemas)
    definition['paths']['/v1/planning/solve']['post']['requestBody']['content']['application/json']['schema']={'$ref':'#/components/schemas/PlanInput'}
    app.openapi_schema=definition
    return definition
app.openapi=openapi
