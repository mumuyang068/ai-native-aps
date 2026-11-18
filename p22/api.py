from typing import Literal
from fastapi import Depends,HTTPException,Request
from pydantic import BaseModel,ConfigDict,Field
from p21.api import local_import_client,business_view_query
from p21.store import ImportError
from p22.service import EnterprisePlans
from planning_adapter import InputError
from agent import AgentError,redact
from drafts import DraftError

class ReviewNoteInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    data_version_id:str=Field(min_length=1,max_length=100)
    baseline_run_id:str=Field(pattern='^API-[a-f0-9]{16}$')
    target:str=Field(pattern='^DRAFT-[a-f0-9]{16}$')
    fingerprint:str=Field(pattern='^[a-f0-9]{64}$')
    recommendation:str=Field(pattern='^(DISCUSS|RECOMMEND_DISCUSSION|DEFER)$')
    reviewer:str=Field(min_length=1,max_length=100)
    reason:str=Field(min_length=1,max_length=2000)
    object_scope:str=Field(min_length=1,max_length=1000)
    next_action:str=Field(min_length=1,max_length=1000)
    acknowledge_limits:bool=Field(strict=True)

class FoundationForecastPreview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    data_version_id:str=Field(min_length=1,max_length=100)
    baseline_run_id:str=Field(pattern='^API-[a-f0-9]{16}$')
    run_id:str=Field(pattern='^API-[a-f0-9]{16}$')
    target:str=Field(pattern='^(baseline|DRAFT-[a-f0-9]{16})$')
    bucket:dict[str,str]
    gross_quantity:str=Field(pattern=r'^(0|[1-9][0-9]{0,11})(\.[0-9]{1,8})?$')
    reason:str=Field(min_length=1,max_length=1000)

class FoundationForecastDraft(FoundationForecastPreview):
    confirmation:dict[str,str]
    request_id:str=Field(pattern='^[a-f0-9]{32}$')

class ProfessionalPolicyPreview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    data_version_id:str=Field(min_length=1,max_length=100)
    baseline_run_id:str=Field(pattern='^API-[a-f0-9]{16}$')
    run_id:str=Field(pattern='^API-[a-f0-9]{16}$')
    target:str=Field(pattern='^(baseline|DRAFT-[a-f0-9]{16})$')
    netting:dict|None
    inquiry:dict|None
    replenishment:dict|None
    supply:dict|None=None
    reason:str=Field(min_length=1,max_length=1000)

class ProfessionalPolicyDraft(ProfessionalPolicyPreview):
    fingerprint:str=Field(pattern='^[a-f0-9]{64}$')
    acknowledge_limits:bool=Field(strict=True)
    request_id:str=Field(pattern='^[a-f0-9]{32}$')

class InquiryNextPreview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    data_version_id:str=Field(min_length=1,max_length=100)
    baseline_run_id:str=Field(pattern='^API-[a-f0-9]{16}$')
    run_id:str=Field(pattern='^API-[a-f0-9]{16}$')
    target:str=Field(pattern='^DRAFT-[a-f0-9]{16}$')
    due_at:str=Field(min_length=1,max_length=100)
    reason:str=Field(min_length=1,max_length=1000)

class InquiryNextDraft(InquiryNextPreview):
    fingerprint:str=Field(pattern='^[a-f0-9]{64}$')
    acknowledge_limits:bool=Field(strict=True)
    request_id:str=Field(pattern='^[a-f0-9]{32}$')

class CreatePlan(BaseModel):
    model_config=ConfigDict(extra='forbid')
    data_version_id:str
    policy_id:str|None=Field(default=None,max_length=100)

class RetryPlan(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id:str

class DueDraft(BaseModel):
    model_config=ConfigDict(extra='forbid')
    data_version_id:str=Field(min_length=1,max_length=100)
    baseline_run_id:str=Field(pattern='^API-[a-f0-9]{16}$')
    order_id:str=Field(min_length=1,max_length=200)
    new_due_at:str=Field(min_length=1,max_length=100)
    request_id:str=Field(pattern='^[a-f0-9]{32}$')

class PurchaseDelayDraft(BaseModel):
    model_config=ConfigDict(extra='forbid')
    data_version_id:str=Field(min_length=1,max_length=100)
    baseline_run_id:str=Field(pattern='^API-[a-f0-9]{16}$')
    reference:str=Field(min_length=1,max_length=200)
    new_receipt_at:str=Field(min_length=1,max_length=100)
    request_id:str=Field(pattern='^[a-f0-9]{32}$')

class DemandPriorityDraft(BaseModel):
    model_config=ConfigDict(extra='forbid')
    data_version_id:str=Field(min_length=1,max_length=100)
    baseline_run_id:str=Field(pattern='^API-[a-f0-9]{16}$')
    demand_id:str=Field(min_length=1,max_length=200)
    priority:int=Field(strict=True,ge=0,le=2147483647)
    request_id:str=Field(pattern='^[a-f0-9]{32}$')

class DemandPriorityPreview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    data_version_id:str=Field(min_length=1,max_length=100)
    baseline_run_id:str=Field(pattern='^API-[a-f0-9]{16}$')
    run_id:str=Field(pattern='^API-[a-f0-9]{16}$')
    target:Literal['baseline']
    demand_id:str=Field(min_length=1,max_length=200)
    priority:int=Field(strict=True,ge=0,le=2147483647)
    reason:str=Field(min_length=1,max_length=1000)

class AccessorySupplyPreview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    data_version_id:str=Field(min_length=1,max_length=100)
    baseline_run_id:str=Field(pattern='^API-[a-f0-9]{16}$')
    run_id:str=Field(pattern='^API-[a-f0-9]{16}$')
    target:str=Field(min_length=1,max_length=100)
    source_id:str=Field(min_length=1,max_length=200)
    reason:str=Field(min_length=1,max_length=1000)

class AccessorySupplyDraft(AccessorySupplyPreview):
    request_id:str=Field(pattern='^[a-f0-9]{32}$')

class ControlledOrder(BaseModel):
    model_config=ConfigDict(extra='forbid')
    demand_id:str=Field(min_length=1,max_length=200)
    priority:int=Field(strict=True,ge=0,le=2147483647)

class ControlledOperator(BaseModel):
    model_config=ConfigDict(extra='forbid')
    resource_id:str=Field(min_length=1,max_length=200)
    start_at:str=Field(min_length=1,max_length=200)
    end_at:str=Field(min_length=1,max_length=200)

class ControlledConditionsPreview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    data_version_id:str=Field(min_length=1,max_length=100)
    baseline_run_id:str=Field(pattern='^API-[a-f0-9]{16}$')
    run_id:str=Field(pattern='^API-[a-f0-9]{16}$')
    target:str=Field(min_length=1,max_length=100)
    order:ControlledOrder|None=None
    operator:ControlledOperator|None=None
    reason:str=Field(min_length=1,max_length=1000)

class ControlledConditionsDraft(ControlledConditionsPreview):
    source_sha256:str=Field(pattern='^[a-f0-9]{64}$')
    fingerprint:str=Field(pattern='^[a-f0-9]{64}$')
    acknowledge_limits:bool=Field(strict=True)
    request_id:str=Field(pattern='^[a-f0-9]{32}$')

class InventoryBufferPolicy(BaseModel):
    model_config=ConfigDict(extra='forbid')
    item_id:str=Field(min_length=1,max_length=200,strict=True)
    location_id:str=Field(min_length=1,max_length=200,strict=True)
    uom:str=Field(min_length=1,max_length=200,strict=True)
    minimum:str=Field(min_length=1,max_length=200,strict=True)
    maximum:str=Field(min_length=1,max_length=200,strict=True)
    start_at:str=Field(min_length=1,max_length=200,strict=True)
    end_at:str=Field(min_length=1,max_length=200,strict=True)

class InventoryDecisionPreview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    data_version_id:str=Field(min_length=1,max_length=100)
    baseline_run_id:str=Field(pattern='^API-[a-f0-9]{16}$')
    run_id:str=Field(pattern='^API-[a-f0-9]{16}$')
    target:str=Field(min_length=1,max_length=100)
    policy:InventoryBufferPolicy|None=None
    source_option_id:str|None=Field(default=None,min_length=1,max_length=200,strict=True)
    reason:str=Field(min_length=1,max_length=1000)

class InventoryDecisionDraft(InventoryDecisionPreview):
    source_sha256:str=Field(pattern='^[a-f0-9]{64}$')
    fingerprint:str=Field(pattern='^[a-f0-9]{64}$')
    acknowledge_limits:bool=Field(strict=True)
    request_id:str=Field(pattern='^[a-f0-9]{32}$')

class OutstandingReceiptPreview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    data_version_id:str=Field(min_length=1,max_length=100,strict=True)
    baseline_run_id:str=Field(pattern='^API-[a-f0-9]{16}$',strict=True)
    run_id:str=Field(pattern='^API-[a-f0-9]{16}$',strict=True)
    target:str=Field(min_length=1,max_length=100,strict=True)
    reference:str=Field(min_length=1,max_length=200,strict=True)
    new_receipt_at:str=Field(min_length=1,max_length=100,strict=True)
    reason:str=Field(min_length=1,max_length=1000,strict=True)

class OutstandingReceiptDraft(OutstandingReceiptPreview):
    source_sha256:str=Field(pattern='^[a-f0-9]{64}$',strict=True)
    fingerprint:str=Field(pattern='^[a-f0-9]{64}$',strict=True)
    acknowledge_limits:bool=Field(strict=True)
    request_id:str=Field(pattern='^[a-f0-9]{32}$',strict=True)

class DistributionLeadtimePreview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    data_version_id:str=Field(min_length=1,max_length=100,strict=True)
    baseline_run_id:str=Field(pattern='^API-[a-f0-9]{16}$',strict=True)
    run_id:str=Field(pattern='^API-[a-f0-9]{16}$',strict=True)
    target:str=Field(min_length=1,max_length=100,strict=True)
    lane_id:str=Field(min_length=1,max_length=200,strict=True)
    new_lead_time_seconds:int=Field(strict=True,ge=1,le=2147483647)
    reason:str=Field(min_length=1,max_length=1000,strict=True)

class DistributionLeadtimeDraft(DistributionLeadtimePreview):
    source_sha256:str=Field(pattern='^[a-f0-9]{64}$',strict=True)
    fingerprint:str=Field(pattern='^[a-f0-9]{64}$',strict=True)
    acknowledge_limits:bool=Field(strict=True)
    request_id:str=Field(pattern='^[a-f0-9]{32}$',strict=True)

class ResourceCapacityPreview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    data_version_id:str=Field(min_length=1,max_length=100,strict=True)
    baseline_run_id:str=Field(pattern='^API-[a-f0-9]{16}$',strict=True)
    run_id:str=Field(pattern='^API-[a-f0-9]{16}$',strict=True)
    target:str=Field(min_length=1,max_length=100,strict=True)
    resource_id:str=Field(min_length=1,max_length=200,strict=True)
    start_at:str=Field(min_length=1,max_length=100,strict=True)
    end_at:str=Field(min_length=1,max_length=100,strict=True)
    reason:str=Field(min_length=1,max_length=1000,strict=True)

class ResourceCapacityDraft(ResourceCapacityPreview):
    source_sha256:str=Field(pattern='^[a-f0-9]{64}$',strict=True)
    fingerprint:str=Field(pattern='^[a-f0-9]{64}$',strict=True)
    acknowledge_limits:bool=Field(strict=True)
    request_id:str=Field(pattern='^[a-f0-9]{32}$',strict=True)

class PlanEditorPreview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    data_version_id:str=Field(min_length=1,max_length=100,strict=True)
    baseline_run_id:str=Field(pattern='^API-[a-f0-9]{16}$',strict=True)
    run_id:str=Field(pattern='^API-[a-f0-9]{16}$',strict=True)
    target:str=Field(min_length=1,max_length=100,strict=True)
    edits:list[dict]=Field(min_length=1,max_length=24)
    reason:str=Field(min_length=1,max_length=1000,strict=True)

class PlanEditorDraft(PlanEditorPreview):
    source_sha256:str=Field(pattern='^[a-f0-9]{64}$',strict=True)
    fingerprint:str=Field(pattern='^[a-f0-9]{64}$',strict=True)
    acknowledge_limits:bool=Field(strict=True)
    request_id:str=Field(pattern='^[a-f0-9]{32}$',strict=True)

class OptimizationStart(BaseModel):
    model_config=ConfigDict(extra='forbid')
    data_version_id:str=Field(min_length=1,max_length=100,strict=True)
    baseline_run_id:str=Field(pattern='^API-[a-f0-9]{16}$',strict=True)
    run_id:str=Field(pattern='^API-[a-f0-9]{16}$',strict=True)
    target:str=Field(min_length=1,max_length=100,strict=True)
    algorithm:Literal['cp_sat','vns','tabu','late_acceptance','pyjobshop']
    scope:Literal['setup_only','setup_and_downstream']
    budget_seconds:int=Field(ge=5,le=600,strict=True)
    request_id:str=Field(pattern='^[a-f0-9]{32}$',strict=True)


class FirmHypothesis(BaseModel):
    model_config=ConfigDict(extra='forbid')
    id:str=Field(min_length=1,max_length=200)
    order_ref:str=Field(min_length=1,max_length=200)
    line_ref:str=Field(min_length=1,max_length=200)
    customer_id:str=Field(min_length=1,max_length=200)
    item_id:str=Field(min_length=1,max_length=200)
    location_id:str=Field(min_length=1,max_length=200)
    uom:str=Field(min_length=1,max_length=200)
    quantity:str=Field(min_length=1,max_length=200)
    due_at:str=Field(min_length=1,max_length=200)
    priority:int=Field(strict=True,ge=0,le=2147483647)
    minimum_shipment:str=Field(min_length=1,max_length=200)
    maximum_lateness_seconds:int=Field(strict=True,ge=0,le=2147483647)
    research_identity:str=Field(min_length=1,max_length=200)
    evidence_status:str=Field(pattern='^SYNTHETIC_RESEARCH_ASSUMPTION$')

class FirmDemandPreview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    data_version_id:str=Field(min_length=1,max_length=100)
    baseline_run_id:str=Field(pattern='^API-[a-f0-9]{16}$')
    run_id:str=Field(pattern='^API-[a-f0-9]{16}$')
    target:str=Field(min_length=1,max_length=100)
    hypothesis:FirmHypothesis
    reason:str=Field(min_length=1,max_length=1000)
    simulation_permission:bool=Field(strict=True)

class FirmDemandDraft(FirmDemandPreview):
    request_id:str=Field(pattern='^[a-f0-9]{32}$')

class ForecastBucketSource(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source_system:str=Field(min_length=1)
    source_record_id:str=Field(min_length=1)
    source_version:str=Field(min_length=1)
    observed_at:str=Field(min_length=1)
    import_batch_id:str=Field(min_length=1)
    source_locator:str=Field(min_length=1)

class ForecastBucket(BaseModel):
    model_config=ConfigDict(extra='forbid')
    forecast_id:str=Field(min_length=1,max_length=1000)
    forecast_version_id:str=Field(min_length=1,max_length=1000)
    item_id:str=Field(min_length=1,max_length=1000)
    location_id:str=Field(min_length=1,max_length=1000)
    customer_id:str=Field(min_length=1,max_length=1000)
    uom:str=Field(min_length=1,max_length=1000)
    bucket_start:str=Field(min_length=1,max_length=1000)
    bucket_end:str=Field(min_length=1,max_length=1000)
    provenance:ForecastBucketSource

class ForecastOverridePreview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    data_version_id:str=Field(min_length=1,max_length=100)
    baseline_run_id:str=Field(pattern='^API-[a-f0-9]{16}$')
    run_id:str=Field(pattern='^API-[a-f0-9]{16}$')
    target:str=Field(min_length=1,max_length=100)
    bucket:ForecastBucket
    gross_quantity:str=Field(pattern=r'^(0|[1-9][0-9]{0,11})(\.[0-9]{1,8})?$')
    reason:str=Field(min_length=1,max_length=1000)

class ForecastOverrideDraft(ForecastOverridePreview):
    request_id:str=Field(pattern='^[a-f0-9]{32}$')

class ApprovedMODraft(BaseModel):
    model_config=ConfigDict(extra='forbid')
    data_version_id:str=Field(min_length=1,max_length=100)
    baseline_run_id:str=Field(pattern='^API-[a-f0-9]{16}$')
    reference:str=Field(min_length=1,max_length=200)
    new_start_at:str=Field(min_length=1,max_length=100)
    request_id:str=Field(pattern='^[a-f0-9]{32}$')

class ExecutionCheckpointInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    data_version_id:str=Field(min_length=1,max_length=100)
    baseline_run_id:str=Field(pattern='^API-[a-f0-9]{16}$')
    checkpoint:dict

class ResourceUnavailableDraft(BaseModel):
    model_config=ConfigDict(extra='forbid')
    data_version_id:str=Field(min_length=1,max_length=100)
    baseline_run_id:str=Field(pattern='^API-[a-f0-9]{16}$')
    resource_id:str=Field(min_length=1,max_length=200)
    start_at:str=Field(min_length=1,max_length=100)
    end_at:str=Field(min_length=1,max_length=100)
    request_id:str=Field(pattern='^[a-f0-9]{32}$')

class PrioritySourceDraft(BaseModel):
    model_config=ConfigDict(extra='forbid')
    data_version_id:str=Field(min_length=1,max_length=100)
    baseline_run_id:str=Field(pattern='^API-[a-f0-9]{16}$')
    source_id:str=Field(min_length=1,max_length=200)
    capacity_quantity:str|None=Field(default=None,pattern='^[1-9][0-9]*(\\.[0-9]+)?$')
    minimum_order_quantity:str|None=Field(default=None,pattern='^[1-9][0-9]*(\\.[0-9]+)?$')
    request_id:str=Field(pattern='^[a-f0-9]{32}$')

def guarded(action):
    try:return action()
    except (InputError,ImportError,AgentError,DraftError) as e:
        code=str(e).split(':',1)[0]
        raise HTTPException(404 if code.endswith('NOT_FOUND') else 503 if code.startswith('COMPUTE_') else 409 if code in {'DATA_VERSION_MISMATCH','BASELINE_RUN_MISMATCH','IDEMPOTENCY_CONFLICT'} else 422,
            detail={'code':code,'message':redact(e)}) from None

def register_plans(app,root):
    plans=EnterprisePlans(root,app.state.imports);app.state.enterprise_plans=plans
    from p22.history_api import register as register_history
    register_history(app,plans)
    from p22.intelligence_api import register as register_intelligence
    register_intelligence(app,plans,guarded)
    @app.get('/v1/enterprise-plans/{pid}/professional-policy',dependencies=[Depends(local_import_client)])
    def professional_policy(request:Request,pid:str,data_version_id:str,baseline_run_id:str,run_id:str,target:str='baseline'):
        allowed={'data_version_id','baseline_run_id','run_id','target'}
        if set(request.query_params)-allowed or any(len(request.query_params.getlist(k))!=1 for k in allowed):raise HTTPException(422,detail={'code':'UNKNOWN_QUERY_FIELD'})
        return guarded(lambda:plans.professional_policy_view(pid,data_version_id,baseline_run_id,run_id,target))
    @app.post('/v1/enterprise-plans/{pid}/professional-policy-preview',dependencies=[Depends(local_import_client)])
    def professional_policy_preview(pid:str,body:ProfessionalPolicyPreview):return guarded(lambda:plans.preview_professional_policy(pid,**body.model_dump()))
    @app.post('/v1/enterprise-plans/{pid}/professional-policy-drafts',status_code=201,dependencies=[Depends(local_import_client)])
    def professional_policy_draft(pid:str,body:ProfessionalPolicyDraft):return guarded(lambda:plans.create_professional_policy_draft(pid,**body.model_dump()))
    @app.post('/v1/enterprise-plans/{pid}/professional-inquiry-next-preview',dependencies=[Depends(local_import_client)])
    def inquiry_next_preview(pid:str,body:InquiryNextPreview):return guarded(lambda:plans.preview_next_inquiry(pid,**body.model_dump()))
    @app.post('/v1/enterprise-plans/{pid}/professional-inquiry-next-drafts',status_code=201,dependencies=[Depends(local_import_client)])
    def inquiry_next_draft(pid:str,body:InquiryNextDraft):return guarded(lambda:plans.create_next_inquiry(pid,**body.model_dump()))
    @app.get('/v1/enterprise-plans/{pid}/review-notes',dependencies=[Depends(local_import_client)])
    def review_notes(request:Request,pid:str,data_version_id:str,baseline_run_id:str,draft_ids:str|None=None):
        allowed={'data_version_id','baseline_run_id','draft_ids'}
        if set(request.query_params)-allowed or any(len(request.query_params.getlist(k))>1 for k in allowed):raise HTTPException(422,detail={'code':'UNKNOWN_QUERY_FIELD','message':'评审意见仅接受本计划版本、基线和候选。'})
        return guarded(lambda:plans.review_notes(pid,data_version_id,baseline_run_id,draft_ids))
    @app.post('/v1/enterprise-plans/{pid}/review-notes',status_code=201,dependencies=[Depends(local_import_client)])
    def save_review_note(pid:str,body:ReviewNoteInput):return guarded(lambda:plans.save_review_note(pid,**body.model_dump()))
    @app.post('/v1/enterprise-plans',status_code=202,dependencies=[Depends(local_import_client)])
    def create(body:CreatePlan):return guarded(lambda:plans.create(body.data_version_id,body.policy_id))
    @app.get('/v1/data-versions/{version_id}/planning-readiness',dependencies=[Depends(local_import_client)])
    def planning_readiness(version_id:str):return guarded(lambda:plans.prepare(version_id)['readiness'])
    @app.get('/v1/enterprise-plans')
    def list_plans():return plans.store.list()
    @app.get('/v1/enterprise-plans/catalog')
    def plan_catalog(request:Request,offset:int=0,limit:int=20):
        if set(request.query_params)-{'offset','limit'} or any(len(request.query_params.getlist(k))!=1 for k in request.query_params):
            raise HTTPException(422,detail=dict(code='UNKNOWN_QUERY_FIELD'))
        from p22.catalog import catalog
        return guarded(lambda:catalog(plans,offset=offset,limit=limit))
    @app.get('/v1/enterprise-plans/{pid}')
    def get_plan(pid:str):return guarded(lambda:plans.get(pid))
    @app.post('/v1/enterprise-plans/{pid}/retry',status_code=202,dependencies=[Depends(local_import_client)])
    def retry_plan(pid:str,body:RetryPlan):return guarded(lambda:plans.retry(pid,body.request_id))
    @app.post('/v1/enterprise-plans/{pid}/due-drafts',status_code=201,dependencies=[Depends(local_import_client)])
    def due_draft(pid:str,body:DueDraft):return guarded(lambda:plans.create_due_draft(pid,**body.model_dump()))
    @app.post('/v1/enterprise-plans/{pid}/purchase-delay-drafts',status_code=201,dependencies=[Depends(local_import_client)])
    def purchase_delay_draft(pid:str,body:PurchaseDelayDraft):return guarded(lambda:plans.create_purchase_delay_draft(pid,**body.model_dump()))
    @app.post('/v1/enterprise-plans/{pid}/demand-priority-drafts',status_code=201,dependencies=[Depends(local_import_client)])
    def demand_priority_draft(pid:str,body:DemandPriorityDraft):return guarded(lambda:plans.create_demand_priority_draft(pid,**body.model_dump()))
    @app.post('/v1/enterprise-plans/{pid}/demand-priority-preview',dependencies=[Depends(local_import_client)])
    def demand_priority_preview(pid:str,body:DemandPriorityPreview):return guarded(lambda:plans.preview_demand_priority(pid,**body.model_dump()))
    @app.get('/v1/enterprise-plans/{pid}/accessory-supply',dependencies=[Depends(local_import_client)])
    def accessory_supply(request:Request,pid:str,data_version_id:str,run_id:str,target:str='baseline'):
        allowed={'data_version_id','run_id','target'}
        if set(request.query_params)-allowed or any(len(request.query_params.getlist(k))>1 for k in allowed):raise HTTPException(422,detail={'code':'UNKNOWN_QUERY_FIELD','message':'来源调查仅接受唯一计划版本、运行和方案。'})
        return guarded(lambda:plans.accessory_supply_view(pid,data_version_id,run_id,target))
    @app.post('/v1/enterprise-plans/{pid}/accessory-supply-preview',dependencies=[Depends(local_import_client)])
    def accessory_preview(pid:str,body:AccessorySupplyPreview):return guarded(lambda:plans.preview_accessory_supply(pid,**body.model_dump()))
    @app.post('/v1/enterprise-plans/{pid}/accessory-supply-drafts',status_code=201,dependencies=[Depends(local_import_client)])
    def accessory_draft(pid:str,body:AccessorySupplyDraft):return guarded(lambda:plans.create_accessory_supply_draft(pid,**body.model_dump()))
    @app.post('/v1/enterprise-plans/{pid}/firm-demand-preview',dependencies=[Depends(local_import_client)])
    def firm_preview(pid:str,body:FirmDemandPreview):return guarded(lambda:plans.preview_firm_demand(pid,**body.model_dump()))
    @app.post('/v1/enterprise-plans/{pid}/firm-demand-drafts',status_code=201,dependencies=[Depends(local_import_client)])
    def firm_draft(pid:str,body:FirmDemandDraft):return guarded(lambda:plans.create_firm_demand_draft(pid,**body.model_dump()))
    @app.get('/v1/enterprise-plans/{pid}/inventory-decision',dependencies=[Depends(local_import_client)])
    def inventory_decision(request:Request,pid:str,data_version_id:str,run_id:str,item_id:str,location_id:str,target:str='baseline'):
        allowed={'data_version_id','run_id','target','item_id','location_id'}
        if set(request.query_params)-allowed or any(len(request.query_params.getlist(k))>1 for k in allowed):raise HTTPException(422,detail={'code':'UNKNOWN_QUERY_FIELD','message':'库存决策仅接受唯一版本、运行和精确物料地点。'})
        return guarded(lambda:plans.inventory_decision_view(pid,data_version_id,run_id,target,item_id,location_id))
    @app.get('/v1/enterprise-plans/{pid}/receipt-protection',dependencies=[Depends(local_import_client)])
    def receipt_protection(request:Request,pid:str,data_version_id:str,run_id:str,target:str='baseline',item_id:str|None=None,location_id:str|None=None):
        allowed={'data_version_id','run_id','target','item_id','location_id'}
        if set(request.query_params)-allowed or any(len(request.query_params.getlist(k))>1 for k in allowed):raise HTTPException(422,detail={'code':'UNKNOWN_QUERY_FIELD','message':'采购与工单调查仅接受唯一计划版本、运行及业务对象。'})
        return guarded(lambda:plans.receipt_protection_view(pid,data_version_id,run_id,target,item_id,location_id))
    @app.post('/v1/enterprise-plans/{pid}/receipt-protection-preview',dependencies=[Depends(local_import_client)])
    def outstanding_receipt_preview(pid:str,body:OutstandingReceiptPreview):
        return guarded(lambda:plans.preview_outstanding_receipt(pid,**body.model_dump()))
    @app.post('/v1/enterprise-plans/{pid}/receipt-protection-drafts',status_code=201,dependencies=[Depends(local_import_client)])
    def outstanding_receipt_draft(pid:str,body:OutstandingReceiptDraft):
        return guarded(lambda:plans.create_outstanding_receipt_draft(pid,**body.model_dump()))
    @app.post('/v1/enterprise-plans/{pid}/distribution-leadtime-preview',dependencies=[Depends(local_import_client)])
    def distribution_leadtime_preview(pid:str,body:DistributionLeadtimePreview):
        return guarded(lambda:plans.preview_distribution_leadtime(pid,**body.model_dump()))
    @app.post('/v1/enterprise-plans/{pid}/distribution-leadtime-drafts',status_code=201,dependencies=[Depends(local_import_client)])
    def distribution_leadtime_draft(pid:str,body:DistributionLeadtimeDraft):
        return guarded(lambda:plans.create_distribution_leadtime_draft(pid,**body.model_dump()))
    @app.post('/v1/enterprise-plans/{pid}/resource-capacity-preview',dependencies=[Depends(local_import_client)])
    def resource_capacity_preview(pid:str,body:ResourceCapacityPreview):
        return guarded(lambda:plans.preview_resource_capacity(pid,**body.model_dump()))

    @app.get('/v1/enterprise-plans/{pid}/plan-editor',dependencies=[Depends(local_import_client)])
    def plan_editor_options(request:Request,pid:str,data_version_id:str,baseline_run_id:str,run_id:str,target:str):
        allowed={'data_version_id','baseline_run_id','run_id','target'}
        if set(request.query_params)!=allowed or any(len(request.query_params.getlist(k))!=1 for k in allowed):raise HTTPException(422,detail={'code':'UNKNOWN_QUERY_FIELD'})
        return guarded(lambda:plans.plan_editor_options(pid,data_version_id,baseline_run_id,run_id,target))
    @app.post('/v1/enterprise-plans/{pid}/plan-editor/optimization',status_code=202,dependencies=[Depends(local_import_client)])
    def start_optimization(pid:str,body:OptimizationStart):
        def action():
            from optimization.service import start
            with plans.lock:
                service,identity=plans._plan_editor_scope(pid,body.data_version_id,body.baseline_run_id,body.run_id,body.target)
                return start(service,identity,body.algorithm,body.scope,body.budget_seconds,body.request_id)
        return guarded(action)
    @app.get('/v1/enterprise-plans/{pid}/plan-editor/optimization',dependencies=[Depends(local_import_client)])
    def latest_optimization(request:Request,pid:str,data_version_id:str,baseline_run_id:str,run_id:str,target:str):
        allowed={'data_version_id','baseline_run_id','run_id','target'}
        if set(request.query_params)!=allowed or any(len(request.query_params.getlist(k))!=1 for k in allowed):raise HTTPException(422,detail={'code':'UNKNOWN_QUERY_FIELD'})
        def action():
            from optimization.service import latest
            with plans.lock:
                service,identity=plans._plan_editor_scope(pid,data_version_id,baseline_run_id,run_id,target)
                return dict(identity=identity,job=latest(service,identity))
        return guarded(action)
    @app.get('/v1/enterprise-plans/{pid}/plan-editor/optimization/{search_id}',dependencies=[Depends(local_import_client)])
    def read_optimization(request:Request,pid:str,search_id:str,data_version_id:str,baseline_run_id:str,run_id:str,target:str):
        allowed={'data_version_id','baseline_run_id','run_id','target'}
        if set(request.query_params)!=allowed or any(len(request.query_params.getlist(k))!=1 for k in allowed):raise HTTPException(422,detail={'code':'UNKNOWN_QUERY_FIELD'})
        def action():
            from optimization.service import read
            with plans.lock:
                service,identity=plans._plan_editor_scope(pid,data_version_id,baseline_run_id,run_id,target)
                return read(service,identity,search_id)
        return guarded(action)
    @app.post('/v1/enterprise-plans/{pid}/plan-editor/preview',dependencies=[Depends(local_import_client)])
    def plan_editor_preview(pid:str,body:PlanEditorPreview):return guarded(lambda:plans.preview_plan_editor(pid,**body.model_dump()))
    @app.post('/v1/enterprise-plans/{pid}/plan-editor/drafts',status_code=201,dependencies=[Depends(local_import_client)])
    def plan_editor_draft(pid:str,body:PlanEditorDraft):return guarded(lambda:plans.create_plan_editor_draft(pid,**body.model_dump()))
    @app.post('/v1/enterprise-plans/{pid}/plan-editor/search',status_code=202,dependencies=[Depends(local_import_client)])
    def plan_editor_search(pid:str,body:PlanEditorDraft):
        from plan_editor_search import start
        def run():
            with plans.lock:return start(plans,pid,body.model_dump())
        return guarded(run)
    @app.get('/v1/enterprise-plans/{pid}/plan-editor/search/{search_id}',dependencies=[Depends(local_import_client)])
    def plan_editor_search_status(request:Request,pid:str,search_id:str,data_version_id:str,baseline_run_id:str,run_id:str,target:str):
        from plan_editor_search import read_search
        def read():
            allowed={'data_version_id','baseline_run_id','run_id','target'}
            if set(request.query_params)!=allowed or any(len(request.query_params.getlist(k))!=1 for k in allowed):raise InputError('UNKNOWN_QUERY_FIELD')
            with plans.lock:
                _,identity=plans._plan_editor_scope(pid,data_version_id,baseline_run_id,run_id,target)
                return read_search(plans,pid,identity,search_id)
        return guarded(read)
    @app.post('/v1/enterprise-plans/{pid}/resource-capacity-drafts',status_code=201,dependencies=[Depends(local_import_client)])
    def resource_capacity_draft(pid:str,body:ResourceCapacityDraft):
        return guarded(lambda:plans.create_resource_capacity_draft(pid,**body.model_dump()))
    @app.post('/v1/enterprise-plans/{pid}/inventory-decision-preview',dependencies=[Depends(local_import_client)])
    def inventory_decision_preview(pid:str,body:InventoryDecisionPreview):
        return guarded(lambda:plans.preview_inventory_decision(pid,**body.model_dump(exclude_none=True)))
    @app.post('/v1/enterprise-plans/{pid}/inventory-decision-drafts',status_code=201,dependencies=[Depends(local_import_client)])
    def inventory_decision_draft(pid:str,body:InventoryDecisionDraft):
        return guarded(lambda:plans.create_inventory_decision_draft(pid,**body.model_dump(exclude_none=True)))
    @app.post('/v1/enterprise-plans/{pid}/controlled-conditions-preview',dependencies=[Depends(local_import_client)])
    def controlled_preview(pid:str,body:ControlledConditionsPreview):
        return guarded(lambda:plans.preview_controlled_conditions(pid,**body.model_dump(exclude_none=True)))
    @app.post('/v1/enterprise-plans/{pid}/controlled-conditions-drafts',status_code=201,dependencies=[Depends(local_import_client)])
    def controlled_draft(pid:str,body:ControlledConditionsDraft):
        return guarded(lambda:plans.create_controlled_conditions_draft(pid,**body.model_dump(exclude_none=True)))
    @app.post('/v1/enterprise-plans/{pid}/forecast-override-preview',dependencies=[Depends(local_import_client)])
    def forecast_override_preview(pid:str,body:ForecastOverridePreview):return guarded(lambda:plans.preview_forecast_override(pid,**body.model_dump()))
    @app.get('/v1/enterprise-plans/{pid}/foundation-forecast',dependencies=[Depends(local_import_client)])
    def foundation_forecast(request:Request,pid:str,data_version_id:str,baseline_run_id:str,run_id:str,target:str='baseline'):
        allowed={'data_version_id','baseline_run_id','run_id','target'}
        if set(request.query_params)-allowed or any(len(request.query_params.getlist(k))>1 for k in allowed):raise HTTPException(422,detail={'code':'UNKNOWN_QUERY_FIELD','message':'预测管理仅接受当前版本与唯一来源基线。'})
        return guarded(lambda:plans.foundation_forecast_view(pid,data_version_id,baseline_run_id,run_id,target))
    @app.post('/v1/enterprise-plans/{pid}/foundation-forecast-preview',dependencies=[Depends(local_import_client)])
    def foundation_forecast_preview(pid:str,body:FoundationForecastPreview):return guarded(lambda:plans.preview_foundation_forecast(pid,**body.model_dump()))
    @app.post('/v1/enterprise-plans/{pid}/foundation-forecast-drafts',status_code=201,dependencies=[Depends(local_import_client)])
    def foundation_forecast_draft(pid:str,body:FoundationForecastDraft):return guarded(lambda:plans.create_foundation_forecast_draft(pid,**body.model_dump()))
    @app.post('/v1/enterprise-plans/{pid}/forecast-override-drafts',status_code=201,dependencies=[Depends(local_import_client)])
    def forecast_override_draft(pid:str,body:ForecastOverrideDraft):return guarded(lambda:plans.create_forecast_override_draft(pid,**body.model_dump()))
    @app.get('/v1/enterprise-plans/{pid}/demand-planning',dependencies=[Depends(local_import_client)])
    def demand_planning(request:Request,pid:str,data_version_id:str,run_id:str,target:str='baseline'):
        allowed={'data_version_id','run_id','target'}
        if set(request.query_params)-allowed or any(len(request.query_params.getlist(k))>1 for k in allowed):raise HTTPException(422,detail={'code':'UNKNOWN_QUERY_FIELD','message':'需求规划仅接受当前版本和唯一运行。'})
        return guarded(lambda:plans.demand_decision_view(pid,data_version_id,run_id,target,'forecast'))
    @app.get('/v1/enterprise-plans/{pid}/inventory-policy',dependencies=[Depends(local_import_client)])
    def inventory_policy(request:Request,pid:str,data_version_id:str,run_id:str,target:str='baseline',item_id:str|None=None,location_id:str|None=None,uom:str|None=None,start:str|None=None,end:str|None=None):
        allowed={'data_version_id','run_id','target','item_id','location_id','uom','start','end'}
        if set(request.query_params)-allowed or any(len(request.query_params.getlist(k))>1 for k in allowed):raise HTTPException(422,detail={'code':'UNKNOWN_QUERY_FIELD','message':'库存政策查询字段无效或重复。'})
        return guarded(lambda:plans.inventory_policy_view(pid,data_version_id,run_id,target,item_id=item_id,location_id=location_id,uom=uom,start=start,end=end))
    @app.get('/v1/enterprise-plans/{pid}/inventory-availability',dependencies=[Depends(local_import_client)])
    def inventory_availability(request:Request,pid:str,data_version_id:str,run_id:str,target:str='baseline'):
        allowed={'data_version_id','run_id','target'}
        if set(request.query_params)-allowed or any(len(request.query_params.getlist(k))>1 for k in allowed):raise HTTPException(422,detail={'code':'UNKNOWN_QUERY_FIELD','message':'质量可用性仅接受当前版本和唯一运行。'})
        return guarded(lambda:plans.demand_decision_view(pid,data_version_id,run_id,target,'quality'))
    @app.post('/v1/enterprise-plans/{pid}/approved-mo-drafts',status_code=201,dependencies=[Depends(local_import_client)])
    def approved_mo_draft(pid:str,body:ApprovedMODraft):return guarded(lambda:plans.create_approved_mo_draft(pid,**body.model_dump()))
    @app.get('/v1/enterprise-plans/{pid}/approved-mo-windows',dependencies=[Depends(local_import_client)])
    def approved_mo_windows(request:Request,pid:str,data_version_id:str,baseline_run_id:str,reference:str):
        allowed={'data_version_id','baseline_run_id','reference'}
        if set(request.query_params)-allowed or any(len(request.query_params.getlist(k))!=1 for k in allowed):raise HTTPException(422,detail={'code':'UNKNOWN_QUERY_FIELD','message':'工单窗口查询身份不完整或重复。'})
        return guarded(lambda:plans.approved_mo_windows(pid,data_version_id,baseline_run_id,reference))
    @app.post('/v1/enterprise-plans/{pid}/source-terms-drafts',status_code=201,dependencies=[Depends(local_import_client)])
    def source_terms_draft(pid:str,body:PrioritySourceDraft):return guarded(lambda:plans.create_source_terms_draft(pid,**body.model_dump()))
    @app.post('/v1/enterprise-plans/{pid}/resource-unavailability-drafts',status_code=201,dependencies=[Depends(local_import_client)])
    def unavailable_draft(pid:str,body:ResourceUnavailableDraft):return guarded(lambda:plans.create_resource_unavailability_draft(pid,**body.model_dump()))
    @app.post('/v1/enterprise-plans/{pid}/execution-checkpoints/preview',dependencies=[Depends(local_import_client)])
    def execution_preview(pid:str,body:ExecutionCheckpointInput):return guarded(lambda:plans.preview_execution_checkpoint(pid,**body.model_dump()))
    @app.get('/v1/enterprise-plans/{pid}/manufacturing-work',dependencies=[Depends(local_import_client)])
    def manufacturing_work(request:Request,pid:str,data_version_id:str,run_id:str,target:str):
        allowed={'data_version_id','run_id','target'}
        if set(request.query_params)-allowed or any(len(request.query_params.getlist(k))!=1 for k in allowed):raise HTTPException(422,detail={'code':'UNKNOWN_QUERY_FIELD','message':'制造作业需要唯一的版本、运行和方案身份。'})
        return guarded(lambda:plans.manufacturing_work_view(pid,data_version_id,run_id,target))
    @app.get('/v1/enterprise-plans/{pid}/rolling-comparison',dependencies=[Depends(local_import_client)])
    def rolling_comparison(request:Request,pid:str,data_version_id:str,run_id:str,parent_plan_id:str,parent_run_id:str):
        allowed={'data_version_id','run_id','parent_plan_id','parent_run_id'}
        if set(request.query_params)-allowed or any(len(request.query_params.getlist(k))!=1 for k in allowed):raise HTTPException(422,detail={'code':'UNKNOWN_QUERY_FIELD','message':'跨版本对照需要精确的父子版本与运行。'})
        return guarded(lambda:plans.rolling_comparison(pid,data_version_id,run_id,parent_plan_id,parent_run_id))
    @app.post('/v1/enterprise-plans/{pid}/execution-checkpoints',status_code=201,dependencies=[Depends(local_import_client)])
    def execution_checkpoint(pid:str,body:ExecutionCheckpointInput):return guarded(lambda:plans.create_execution_checkpoint(pid,**body.model_dump()))
    @app.get('/v1/enterprise-plans/{pid}/snapshot')
    def snapshot(pid:str):return guarded(lambda:plans.store.snapshot(pid))
    @app.get('/v1/enterprise-plans/{pid}/manufacturing-opportunities')
    def manufacturing_opportunities(pid:str,order_id:str):return guarded(lambda:plans.service(pid).manufacturing_opportunities(order_id))
    @app.get('/v1/enterprise-plans/{pid}/order-run-evidence')
    def order_run_evidence(pid:str,data_version_id:str,run_id:str,order_line_id:str):
        return guarded(lambda:plans.order_run_evidence(pid,data_version_id,run_id,order_line_id))
    @app.get('/v1/enterprise-plans/{pid}/order-investigation',dependencies=[Depends(local_import_client)])
    def order_investigation(request:Request,pid:str,data_version_id:str,run_id:str,order_line_id:str,target:str):
        allowed={'data_version_id','run_id','order_line_id','target'}
        if set(request.query_params)-allowed or any(len(request.query_params.getlist(k))!=1 for k in allowed):
            raise HTTPException(422,detail={'code':'UNKNOWN_QUERY_FIELD','message':'订单调查只接受唯一的计划、版本、运行、方案和需求行身份。'})
        return guarded(lambda:plans.order_run_evidence(pid,data_version_id,run_id,order_line_id,
                            target=target,require_valid_run=True))
    @app.get('/v1/enterprise-plans/{pid}/business-view',dependencies=[Depends(local_import_client)])
    def business_view(request:Request,pid:str,data_version_id:str):
        query=business_view_query(request,plan=True)
        return guarded(lambda:plans.business_view(pid,data_version_id,**query))

    @app.get('/v1/enterprise-plans/{pid}/analysis',dependencies=[Depends(local_import_client)])
    def run_analysis(request:Request,pid:str,data_version_id:str,run_id:str,target:str='baseline',
                     resource_id:str|None=None,item_id:str|None=None,location_id:str|None=None,
                     start:str|None=None,end:str|None=None):
        if set(request.query_params)-{'data_version_id','run_id','target','resource_id','item_id','location_id','start','end'}:
            raise HTTPException(422,detail={'code':'UNKNOWN_QUERY_FIELD','message':'规划分析不接受文件路径或未知查询字段。'})
        return guarded(lambda:plans.run_analysis(pid,data_version_id,run_id,target,resource_id=resource_id,
                        item_id=item_id,location_id=location_id,start=start,end=end))

    @app.get('/v1/enterprise-plans/{pid}/exceptions',dependencies=[Depends(local_import_client)])
    def exceptions(request:Request,pid:str,data_version_id:str,run_id:str,target:str='baseline'):
        allowed={'data_version_id','run_id','target'}
        if set(request.query_params)-allowed or any(len(request.query_params.getlist(k))>1 for k in allowed):
            raise HTTPException(422,detail={'code':'UNKNOWN_QUERY_FIELD','message':'异常调查只接受当前计划和唯一运行身份。'})
        return guarded(lambda:plans.exceptions(pid,data_version_id,run_id,target))

    @app.get('/v1/enterprise-plans/{pid}/scenario-review',dependencies=[Depends(local_import_client)])
    def scenario_review(request:Request,pid:str,data_version_id:str,baseline_run_id:str,draft_ids:str|None=None):
        allowed={'data_version_id','baseline_run_id','draft_ids'}
        if set(request.query_params)-allowed or any(len(request.query_params.getlist(k))>1 for k in allowed):
            raise HTTPException(422,detail={'code':'UNKNOWN_QUERY_FIELD','message':'评审只接受同计划基线与最多三个独立草案。'})
        return guarded(lambda:plans.scenario_review(pid,data_version_id,baseline_run_id,draft_ids))

    @app.get('/v1/enterprise-plans/{pid}/reports',dependencies=[Depends(local_import_client)])
    def period_reports(request:Request,pid:str,data_version_id:str,run_id:str,target:str='baseline',grain:str='day',start:str|None=None,end:str|None=None):
        allowed={'data_version_id','run_id','target','grain','start','end'}
        if set(request.query_params)-allowed or any(len(request.query_params.getlist(k))>1 for k in allowed):
            raise HTTPException(422,detail={'code':'UNKNOWN_QUERY_FIELD','message':'期间报表只接受当前计划身份与唯一的时间范围。'})
        return guarded(lambda:plans.period_reports(pid,data_version_id,run_id,target,grain,start,end))
