"""Task-configured bridge to the separate virtual enterprise Owner.

Only the existing authenticated research Operator may control this sandbox.
Agent research and Worker planning keep their existing owners and permissions.
"""
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlparse
from fastapi import Depends,HTTPException,Request
from pydantic import BaseModel,ConfigDict,Field
from assistant_authorization import COOKIE
from p21.live_connector import LiveSourceClient


class Mutation(BaseModel):
    model_config=ConfigDict(extra='forbid')
    payload:dict
    expected_revision:int=Field(strict=True,ge=0)
    idempotency_key:str=Field(min_length=1,max_length=200)


class Selection(BaseModel):
    model_config=ConfigDict(extra='forbid')
    plan_id:str=Field(pattern=r'^PLAN-[a-f0-9]{16}$')
    target:str=Field(pattern=r'^(baseline|DRAFT-[a-f0-9]{16})$')
    order_id:str=Field(min_length=1,max_length=200)
    action_type:str=Field(pattern=r'^(MANUFACTURE|PURCHASE)$')
    manufacture_id:str|None=None
    purchase_reference:str|None=None


class Adoption(BaseModel):
    model_config=ConfigDict(extra='forbid')
    selection:Selection
    fingerprint:str=Field(pattern=r'^[a-f0-9]{64}$')
    confirmed:bool=Field(strict=True)
    expected_revision:int=Field(strict=True,ge=0)
    idempotency_key:str=Field(min_length=1,max_length=200)


def register(app,plans,local_client):
    path=os.environ.get('PLANNING_SIMULATION_BRIDGE_FILE')
    configuration=json.loads(Path(path).read_text('utf8')) if path else None
    if configuration and (configuration.get('schema')!='simulation-bridge/1' or not configuration.get('branches')):
        raise ValueError('SIM_BRIDGE_CONFIGURATION_INVALID')
    def principal(request:Request):
        p=app.state.agent_operators.principal(request.cookies.get(COOKIE))
        if not p or getattr(request.state,'agent_principal',None):
            raise HTTPException(403,detail={'code':'RESEARCH_OPERATOR_SESSION_REQUIRED'})
        return p
    def write(request:Request):
        local_client(request)
        origin=request.headers.get('origin')
        supplied=urlparse(origin or '');expected=urlparse(str(request.base_url))
        if not origin or (supplied.scheme,supplied.netloc)!=(expected.scheme,expected.netloc):
            raise HTTPException(403,detail={'code':'SAME_ORIGIN_REQUIRED'})
        return principal(request)
    def bridge(branch):
        if not configuration:raise HTTPException(503,detail={'code':'SIM_BRIDGE_NOT_CONFIGURED'})
        if branch not in configuration['branches']:raise HTTPException(404,detail={'code':'SIM_BRANCH_NOT_FOUND'})
        return LiveSourceClient(dict(base_url=configuration['base_url'],branch_id=branch),configuration['operator_token'])
    def invoke(fn):
        try:return fn()
        except ValueError as exc:
            code=str(exc).split(':',1)[0]
            if not code or not all(c.isupper() or c.isdigit() or c=='_' for c in code):code='SIM_REQUEST_REJECTED'
            messages={
                'WIP_REQUIRES_CERTIFIED_CONTINUATION':'该订单已有已领料或已完成前序的在制任务；剩余工序续排尚未认证，不能再次释放覆盖同一数量的整批制造。',
                'NATIVE_MANUFACTURE_ALREADY_ADOPTED':'这笔原生制造建议已被采纳，不能重复用于本订单或其他订单。请核对已有采纳动作与实际执行。',
                'ADOPTION_EXCEEDS_OPEN_DEMAND':'已有制造任务及待执行动作已覆盖未履行需求，不能再增加这笔整批制造。',
                'HISTORICAL_SOURCE_ADOPTION_FORBIDDEN':'当前正在查看历史来源。请先返回研究分支的当前经营时点，再核对并模拟采纳。',
                'YEAR_BASELINE_ROUTINE_ONLY':'年度基础线按已声明的常规经营政策运行。请在派生研究分支核对并模拟采纳。',
            }
            detail={'code':code}
            if code in messages:detail['message']=messages[code]
            raise HTTPException(409,detail=detail) from None
    def post(client,path,body):
        try:return client.request(path,body=body)
        except ValueError as exc:
            if str(exc)=='LIVE_SOURCE_UNAVAILABLE':raise ValueError('WRITE_STATUS_UNKNOWN') from None
            raise
    def status(branch):
        value=bridge(branch).request('/v1/sim/'+branch+'/status')
        connector=getattr(app.state,'connector_registry',{}).get('live-'+branch)
        value['connector_id']='live-'+branch
        synced=connector.get_sync_status() if connector else {}
        value['current_version_id']=synced.get('current_version_id')
        if value.get('year_mode'):
            value['latest_data_version_id']=value['current_version_id']
            cutoff=value.get('selected_source',{}).get('as_of')
            matching=next((record for record in synced.get('history',[]) if record.get('as_of')==cutoff and record.get('status')=='COMMITTED'),None)
            value['current_version_id']=matching.get('version_id') if matching else None
        return value
    def adoption_head(branch):
        head=status(branch)
        if head.get('year_mode')=='BASE':raise ValueError('YEAR_BASELINE_ROUTINE_ONLY')
        if head.get('year_mode') and head.get('adoption_available') is not True:raise ValueError('HISTORICAL_SOURCE_ADOPTION_FORBIDDEN')
        return head
    @app.get('/v1/simulation/branches',dependencies=[Depends(principal)])
    def branches():
        return invoke(lambda:dict(branches=[status(b) for b in configuration['branches']] if configuration else []))
    @app.get('/v1/simulation/{branch}',dependencies=[Depends(principal)])
    def current(branch:str):return invoke(lambda:status(branch))
    @app.get('/v1/simulation/{branch}/events',dependencies=[Depends(principal)])
    def events(branch:str,after_sequence:int=0,limit:int=500):return invoke(lambda:bridge(branch).request('/v1/sim/'+branch+'/events',{'after_sequence':after_sequence,'limit':limit}))
    @app.get('/v1/simulation/{branch}/trace',dependencies=[Depends(principal)])
    def trace(branch:str,order_id:str):return invoke(lambda:bridge(branch).request('/v1/sim/'+branch+'/trace',{'order_id':order_id}))
    @app.get('/v1/simulation/{branch}/checkpoints',dependencies=[Depends(principal)])
    def checkpoints(branch:str):return invoke(lambda:bridge(branch).request('/v1/sim/'+branch+'/checkpoints'))
    @app.get('/v1/simulation/{branch}/history',dependencies=[Depends(principal)])
    def history(branch:str,checkpoint_id:str|None=None,as_of:str|None=None):
        return invoke(lambda:bridge(branch).request('/v1/sim/'+branch+'/history',
            {key:value for key,value in {'checkpoint_id':checkpoint_id,'as_of':as_of}.items() if value is not None}))
    @app.get('/v1/simulation/{branch}/monthly-summary',dependencies=[Depends(principal)])
    def monthly_summary(branch:str):return invoke(lambda:bridge(branch).request('/v1/sim/'+branch+'/monthly-summary'))
    @app.post('/v1/simulation/{branch}/adoption-preview',dependencies=[Depends(write)])
    def preview(branch:str,body:Selection):
        from closed_loop_actions import adoption_preview
        return invoke(lambda:adoption_preview(plans,branch,body.model_dump(exclude_none=True),adoption_head(branch)))
    @app.post('/v1/simulation/{branch}/adopt')
    def adopt(branch:str,body:Adoption,operator=Depends(write)):
        from closed_loop_actions import adoption_preview,digest
        def perform():
            if body.confirmed is not True:raise ValueError('SIM_ADOPTION_CONFIRMATION_REQUIRED')
            selected=body.selection.model_dump(exclude_none=True)
            # The simulator owns both action and idempotency receipt. Recover a
            # lost response from that same immutable record before checking head.
            record_id='SIM-OPERATOR-'+digest(dict(actor=operator['subject'],key=body.idempotency_key))[:24]
            head=adoption_head(branch)
            for action in head['actions']:
                confirmation=action['payload']['operator_confirmation']
                if confirmation['record_id']==record_id:
                    if (confirmation.get('preview_fingerprint')!=body.fingerprint or action['payload'].get('selection')!=selected
                        or action['payload']['world_revision']!=body.expected_revision):
                        raise ValueError('IDEMPOTENCY_KEY_CONFLICT')
                    return dict(status='ALREADY_ADOPTED',action=action,branch_id=branch,revision=head['revision'])
            if body.expected_revision!=head['revision']:raise ValueError('LIVE_REVISION_CONFLICT')
            current=adoption_preview(plans,branch,selected,head)
            if current['fingerprint']!=body.fingerprint:raise ValueError('ADOPTION_PREVIEW_CHANGED')
            payload=current['payload']
            payload['selection']=selected
            payload['operator_confirmation']=dict(record_id=record_id,actor_id=operator['subject'],
                role='RESEARCH_SANDBOX_OPERATOR',confirmed=True,authentication=operator['authentication'],
                preview_fingerprint=body.fingerprint,session_id=operator['session_id'])
            return post(bridge(branch),'/v1/sim/'+branch+'/adopt',dict(payload=payload,
                expected_revision=body.expected_revision,idempotency_key=body.idempotency_key))
        return invoke(perform)
    @app.post('/v1/simulation/{branch}/{action}',dependencies=[Depends(write)])
    def control(branch:str,action:str,body:Mutation):
        if action not in {'advance','order','supply','checkpoint','restore','cancel','source-select'}:
            raise HTTPException(422,detail={'code':'SIM_ACTION_UNSUPPORTED'})
        return invoke(lambda:post(bridge(branch),'/v1/sim/'+branch+'/'+action,body.model_dump()))
