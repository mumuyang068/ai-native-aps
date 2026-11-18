"""Task-owned annual simulator HTTP mount; the product accesses it by HTTP only."""
import hmac
import json
from pathlib import Path

from fastapi import Depends,FastAPI,HTTPException,Request

from enterprise_sim.live import instant
from enterprise_sim.year_live import AnnualEnterprise
from enterprise_sim.year_sources import YearSourceViews
from simulation_service import Control


def create_app(simulation_root,policy_path,read_token,operator_token):
    if not read_token or not operator_token or read_token==operator_token:raise ValueError('SEPARATE_OPERATOR_READ_TOKENS_REQUIRED')
    declared=json.loads(Path(policy_path).read_text('utf8'))
    if set(declared)!={'definition','operating','reference'} or declared['operating'].get('year_policy',{}).get('schema')!='year-operating-policy/1':
        raise ValueError('YEAR_DECLARATIONS_REQUIRED')
    root=Path(simulation_root).resolve();branches={}
    for branch in ['YEAR-BASE','YEAR-INTERVENTION']:
        path=root/branch
        if not (path/'bootstrap.json').is_file():continue
        owner=AnnualEnterprise.open(path)
        if owner.branch_id!=branch or owner.bootstrap['declarations']!=declared:raise ValueError('YEAR_SOURCE_DECLARATIONS_MISMATCH')
        branches[branch]=YearSourceViews(owner)
    app=FastAPI(title='Annual Virtual Enterprise Visible Sources',version='0.1')
    app.state.year_sources=branches
    def authenticate(request,token):
        if not hmac.compare_digest(request.headers.get('authorization',''),'Bearer '+token):
            raise HTTPException(403,detail={'code':'SIM_SOURCE_AUTHORIZATION_REQUIRED'})
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
    def health():return dict(status='OK',mode='ANNUAL_RESEARCH_SIMULATION_ONLY',branches=len(branches),enterprise_execution_authorized=False)
    @app.get('/v1/sim/{branch}/view',dependencies=[Depends(reader)])
    def view(branch:str,revision:int|None=None):return invoke(lambda:owner(branch).read_view(read_token=read_token,revision=revision))
    @app.get('/v1/sim/{branch}/source-checkpoint',dependencies=[Depends(reader)])
    def source_checkpoint(branch:str,revision:int|None=None):return invoke(lambda:owner(branch).source_checkpoint(read_token=read_token,revision=revision))
    @app.get('/v1/sim/{branch}/changes',dependencies=[Depends(reader)])
    def changes(branch:str,system:str,revision:int,previous_as_of:str):
        return invoke(lambda:owner(branch).changes(system,read_token=read_token,revision=revision,previous_as_of=previous_as_of))
    @app.get('/v1/sim/{branch}/verify',dependencies=[Depends(reader)])
    def verify(branch:str,revision:int):return invoke(lambda:owner(branch).verify_view(revision,read_token=read_token))
    @app.get('/v1/sim/{branch}/status',dependencies=[Depends(operator)])
    def status(branch:str):return invoke(lambda:owner(branch).status())
    @app.get('/v1/sim/{branch}/checkpoints',dependencies=[Depends(operator)],
             description='Month checkpoints query sources at the following month first day 00:00 Asia/Shanghai. The timestamp is an as-of query instant, not an event-period filter.')
    def checkpoints(branch:str):return invoke(lambda:owner(branch).checkpoints())
    @app.get('/v1/sim/{branch}/history',dependencies=[Depends(operator)],
             description='Visible source state whose events have occurred and are known by the as-of instant. YYYY-MM selects the following month first day 00:00 Asia/Shanghai; it does not filter events to that month.')
    def history(branch:str,checkpoint_id:str|None=None,as_of:str|None=None):
        return invoke(lambda:owner(branch).history(read_token=read_token,checkpoint_id=checkpoint_id,as_of=as_of))
    @app.get('/v1/sim/{branch}/monthly-summary',dependencies=[Depends(operator)])
    def monthly(branch:str):return invoke(lambda:owner(branch).monthly_summary(read_token=read_token))
    @app.get('/v1/sim/{branch}/events',dependencies=[Depends(operator)])
    def events(branch:str,after_sequence:int=0,limit:int=500):
        def perform():
            if after_sequence<0 or limit<1 or limit>5000:raise ValueError('SIM_EVENT_RANGE_INVALID')
            views=owner(branch)
            with views.lock,views.owner._locked():
                head=views.status();selected=head['selected_source'];cutoff=instant(selected['as_of'])
                rows=[event for event in views.owner.events(operator_token=operator_token,after_sequence=after_sequence) if instant(event['occurred_at'])<=cutoff]
                return dict(branch_id=branch,business_revision=head['business_revision'],selected_source=selected,
                    events=rows[:limit],next_sequence=after_sequence+min(limit,len(rows)),has_more=len(rows)>limit)
        return invoke(perform)
    @app.get('/v1/sim/{branch}/trace',dependencies=[Depends(operator)])
    def trace(branch:str,order_id:str):
        def perform():
            views=owner(branch)
            if views.mode=='APS_INTERVENTION' and views.status()['selected_source']['checkpoint_id']=='CURRENT':
                return views.owner.trace(order_id,operator_token=operator_token)
            view=views.read_view(read_token=read_token);state=views.source_state(view)
            order=next((row for row in state['orders'] if row['id']==order_id),None)
            if order is None:raise ValueError('UNKNOWN_IDENTITY')
            jobs={row['id']:row for row in state['manufacturing_tasks'] if row['order_id']==order_id}
            visible={identifier for document in view['documents'].values() for table in document['event_refs'].values() for ids in table.values() for identifier in ids}
            records=[event for event in views.owner.world.events if event['event_id'] in visible]
            relevant=[event for event in records if event['payload'].get('order_id')==order_id or event['payload'].get('job_id') in jobs]
            lots={event['payload']['lot_id'] for event in relevant if event['kind']=='MaterialIssued'}
            from decimal import Decimal
            return dict(order=order,jobs=jobs,events=relevant,supply_events=[event for event in records if event['payload'].get('lot_id') in lots],
                        actions=[],remaining_quantity=str(Decimal(order['quantity'])-Decimal(order['shipped'])),as_of=view['as_of'])
        return invoke(perform)
    @app.get('/v1/sim/{branch}/audit',dependencies=[Depends(operator)])
    def audit(branch:str):return invoke(lambda:owner(branch).owner.export_audit(operator_token=operator_token))
    @app.post('/v1/sim/{branch}/{action}',dependencies=[Depends(operator)])
    def control(branch:str,action:str,body:Control):
        def perform():
            views=owner(branch);live=views.owner
            common=dict(operator_token=operator_token,expected_revision=body.expected_revision,idempotency_key=body.idempotency_key)
            if action=='source-select':return views.select_source(body.payload,**common)
            if action=='advance':return live.advance(body.payload['to'],**common)
            if action=='checkpoint':return live.checkpoint(body.payload['label'],**common)
            if action=='adopt':views.require_current_adoption();return live.adopt(body.payload,**common)
            if action=='order':return live.add_order(body.payload,**common)
            if action=='supply':return live.perturb_supply(body.payload,**common)
            if action=='cancel':return live.cancel(body.payload['action_id'],**common)
            if action=='restore':return live.restore(body.payload['checkpoint_id'],**common)
            raise ValueError('SIM_ACTION_UNSUPPORTED')
        return invoke(perform)
    return app
