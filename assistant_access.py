"""Discovery and task lifecycle adapters, owned by existing plan/task APIs."""
from typing import Annotated
from urllib.parse import quote
import re

from pydantic import Field, ValidationError
from assistant_tools import BusinessTools, BusinessToolError, Identity, Key, _plain, _task_summary_for_model, IDENTITY_FIELDS


def register(mcp,api,tool):
    from p3_mcp_tools import P3ToolError
    boundary=BusinessTools(api.base,transport=api.request)

    @tool(True)
    def discover_professional_context(plan_id:Annotated[str,Field(pattern='^PLAN-[a-f0-9]{16}$')]|None=None)->dict:
        """Discover authorized current plan/version/baseline/run identities and actual capability readiness from the original PlanStore metadata. No enterprise overview, solve, model call or new permission. A missing native run is NOT_SUBMITTED, not zero or success. Only the access scope of this MCP connection is visible."""
        query=dict(plan_id=plan_id) if plan_id else {}
        value=api.request('GET',boundary._query('/v1/professional-contexts',query))
        if value.get('schema_version')!='professional-contexts/1':raise P3ToolError('CONTEXT_SCHEMA_INVALID')
        if plan_id and any(row.get('plan_id')!=plan_id for row in value.get('contexts',[])):
            raise P3ToolError('CONTEXT_SCOPE_MISMATCH')
        return value

    def lifecycle(name,identity,task_id,request_id,expected_revision,message=None):
        try:
            identity=_plain(Identity.model_validate(identity))
            boundary._key(request_id)
            if not re.fullmatch('TASK-[a-f0-9]{16}',task_id):raise BusinessToolError('TASK_ARGUMENTS_INVALID')
            path='/v1/agent-tasks/'+quote(task_id,safe='')
            endpoint=boundary._query(path,dict(plan_id=identity['plan_id']))
            current=api.request('GET',endpoint)
            boundary._check_identity(current,identity,IDENTITY_FIELDS)
            body=dict(request_id=request_id,expected_revision=expected_revision)
            if message is not None:body['message']=message
            value=api.request('POST',boundary._query(path+'/'+name,dict(plan_id=identity['plan_id'])),body)
            boundary._check_identity(value,identity,IDENTITY_FIELDS)
            return _task_summary_for_model(value,endpoint)
        except (ValidationError,BusinessToolError) as error:
            raise P3ToolError('TASK_ARGUMENTS_INVALID' if isinstance(error,ValidationError) else str(error)) from None

    @tool(False)
    def continue_professional_task(plan_id:str,data_version_id:str,baseline_run_id:str,run_id:str,target:str,
        task_id:Annotated[str,Field(pattern='^TASK-[a-f0-9]{16}$')],
        expected_revision:Annotated[int,Field(ge=1)],message:Annotated[str,Field(min_length=1,max_length=8000)],request_id:Key)->dict:
        """Append the user's follow-up to the same external Agent task through its original SessionStore; no model is launched. Keep the immutable task origin identity, not a candidate's new run. Exact revision and request_id protect lost-response replay. Pending confirmation, unresolved write and cancelled task remain blocked. This is task metadata, not business consent; continue reasoning using actual receipts after success."""
        return lifecycle('continue',dict(plan_id=plan_id,data_version_id=data_version_id,baseline_run_id=baseline_run_id,run_id=run_id,target=target),
            task_id,request_id,expected_revision,message)

    @tool(False)
    def cancel_professional_task(plan_id:str,data_version_id:str,baseline_run_id:str,run_id:str,target:str,
        task_id:Annotated[str,Field(pattern='^TASK-[a-f0-9]{16}$')],expected_revision:Annotated[int,Field(ge=1)],request_id:Key)->dict:
        """Stop subsequent actions of the exact existing Agent task via its original SessionStore. Does not cancel or delete an already submitted native job, hold, review or run archive. Read original business references separately to know their current states. Immutable origin identity, revision and same request_id required; this tool never approves anything."""
        return lifecycle('cancel',dict(plan_id=plan_id,data_version_id=data_version_id,baseline_run_id=baseline_run_id,run_id=run_id,target=target),
            task_id,request_id,expected_revision)
