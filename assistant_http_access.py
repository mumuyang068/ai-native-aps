"""Task-local Agent access at the existing API boundary, not an SSO service."""
import re
from fastapi import Request
from fastapi.responses import JSONResponse
from assistant_authorization import COOKIE
from assistant_agent_access import AgentAccess, AgentAccessError


def register(app):
    error=None
    try:access=AgentAccess.from_env()
    except AgentAccessError as exc:access=None;error=exc.code
    app.state.agent_access=access

    @app.middleware('http')
    async def authorize(request:Request,call_next):
        path=request.url.path
        if not path.startswith('/v1/'):
            return await call_next(request)
        def deny(code,status=403):return JSONResponse(status_code=status,content={'detail':{'code':code}})
        if error:return deny('AGENT_ACCESS_CONFIGURATION_INVALID',503)
        if not access.status()['configured']:
            return await call_next(request)  # Explicit legacy local trust, not authentication.
        owner=request.headers.get('X-APS-Owner')
        bearer=request.headers.get('authorization')
        if owner:
            try:access.authenticate_owner(owner)
            except AgentAccessError as exc:return deny(exc.code)
            request.state.internal_owner=True
            return await call_next(request)
        if bearer:
            try:
                principal=access.authenticate_agent(bearer)
                request.state.agent_principal=principal
                if path=='/v1/agent-operator-session' or path.endswith('/confirm'):
                    return deny('AGENT_CONFIRMATION_CHANNEL_DENIED')
                primary=set(re.findall(r'/enterprise-plans/(PLAN-[a-f0-9]{16})(?=/|$)',path))
                primary.update(request.query_params.getlist('plan_id'))
                plans=set(primary)
                for key in request.query_params:
                    if key=='plan_id' or key.endswith('_plan_id'):
                        plans.update(request.query_params.getlist(key))
                if any(pid not in principal['plan_ids'] for pid in plans):return deny('AGENT_PLAN_ACCESS_DENIED')
                if request.method in {'GET','HEAD'}:
                    if 'READ' not in principal['permissions']:return deny('AGENT_ACCESS_PERMISSION_FORBIDDEN')
                    discover=path=='/v1/professional-contexts'
                    scoped=(path.startswith('/v1/enterprise-plans/PLAN-') or path.startswith('/v1/agent-tasks') or
                        path in {'/v1/state','/v1/agent-capabilities'} or
                        (re.fullmatch(r'/v1/drafts/DRAFT-[a-f0-9]{16}/result',path) is not None and len(request.query_params.getlist('plan_id'))==1))
                    if not discover and (not scoped or not primary):return deny('AGENT_READ_SCOPE_REQUIRED')
                else:
                    if not path.startswith('/v1/agent-tasks'):return deny('AGENT_DIRECT_DOMAIN_WRITE_DENIED')
                    if len(primary)!=1:return deny('AGENT_PLAN_ACCESS_DENIED')
                    pid=next(iter(primary));access.authorize_agent(bearer,pid,'TASK_METADATA')
                    if path.endswith('/tools'):
                        body=await request.json()
                        from assistant_tools import BusinessTools, BusinessToolError
                        try:spec=BusinessTools.tool_spec(body.get('tool'))
                        except BusinessToolError:return deny('TOOL_NOT_ALLOWED')
                        access.authorize_agent(bearer,pid,'READ' if spec['readOnly'] else 'RESEARCH_PROPOSAL')
                    elif path=='/v1/agent-tasks':
                        body=await request.json()
                        if body.get('mode','external')!='external':return deny('AGENT_EXTERNAL_MODE_REQUIRED')
                return await call_next(request)
            except AgentAccessError as exc:return deny(exc.code)
            except (ValueError,TypeError,AttributeError):return deny('TASK_ARGUMENTS_INVALID',422)
        # Only the explicit operator login/status is public. Dropping the Agent
        # header cannot turn a credentialed client into an anonymous domain writer.
        if path=='/v1/agent-operator-session':return await call_next(request)
        operators=getattr(app.state,'agent_operators',None)
        if operators and operators.principal(request.cookies.get(COOKIE)):
            return await call_next(request)
        return deny('TASK_LOCAL_AUTHENTICATION_REQUIRED',401)
