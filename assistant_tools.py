"""Typed professional tool adapters over existing P3 Owners.

No domain algorithm, task store or confirmation authority lives here. execute()
is the server-side dispatcher, called by the task gateway after its authorization
check. MCP wrappers only submit task-bound requests to that gateway.
"""
import base64
import re
import copy
from datetime import datetime
import hashlib
import json
from typing import Annotated, Literal, Union
from urllib.parse import quote, urlencode, urlsplit

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, model_validator
import requests
import assistant_legacy_scenarios as legacy_scenarios


class BusinessToolError(ValueError):
    def __init__(self,code,*,status_code=None,outcome_unknown=False):
        super().__init__(code)
        self.status_code=status_code
        self.outcome_unknown=outcome_unknown


def _time(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.utcoffset() is None:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Explicit timezone required') from None
    return value


Text = Annotated[str, Field(min_length=1, max_length=200)]
Reason = Annotated[str, Field(min_length=1, max_length=1000)]
Sha = Annotated[str, Field(pattern='^[a-f0-9]{64}$')]
Key = Annotated[str, Field(pattern='^[a-f0-9]{32}$')]
At = Annotated[str, Field(min_length=1, max_length=100), AfterValidator(_time)]
Quantity = Annotated[str, Field(pattern=r'^(0|[1-9][0-9]{0,11})(\.[0-9]{1,8})?$')]
Run = Annotated[str, Field(pattern='^API-[a-f0-9]{16}$')]
Draft = Annotated[str, Field(pattern='^DRAFT-[a-f0-9]{16}$')]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Identity(StrictModel):
    plan_id: Annotated[str, Field(pattern='^PLAN-[a-f0-9]{16}$')]
    data_version_id: Annotated[str, Field(min_length=1, max_length=100)]
    baseline_run_id: Run
    run_id: Run
    target: Annotated[str, Field(pattern='^(baseline|DRAFT-[a-f0-9]{16})$')]


IDENTITY_FIELDS = tuple(Identity.model_fields)


class EvidenceFilters(StrictModel):
    job_id: Text | None = None
    lot_id: Text | None = None
    record_id: Text | None = None
    order_line_id: Text | None = None
    resource_id: Text | None = None
    item_id: Text | None = None
    location_id: Text | None = None
    uom: Text | None = None
    reference: Text | None = None
    version_id: Text | None = None
    row_id: Text | None = None
    dataset_id: Text | None = None
    inventory_id: Text | None = None
    source_id: Text | None = None
    supplier_id: Text | None = None
    customer_id: Text | None = None
    status: Text | None = None
    kind: Text | None = None
    operation_id: Text | None = None
    role: Text | None = None
    quote_id: Text | None = None
    search_id: Annotated[str, Field(pattern='^OPT-[a-f0-9]{24}$',description='schedule: exact search id; omit to discover current contract and latest search reference.')] | None = None
    draft_ids: Annotated[list[Draft], Field(max_length=3,description='scenario_review requires an explicit selection: [] means baseline plus available draft references; up to 3 exact draft ids selects candidates.')] | None = None
    start: At | None = None
    end: At | None = None


class EvidenceArgs(Identity):
    view: Literal['demand_risks', 'resources', 'inventory', 'manufacturing', 'purchases', 'forecast',
                  'inventory_research', 'promising', 'schedule', 'schedule_result', 'scenario_review', 'plan_conditions', 'versions',
                  'legacy_forecast_override', 'foundation_forecast', 'inventory_decision']
    filters: EvidenceFilters = Field(default_factory=EvidenceFilters)
    page_size: Annotated[int, Field(ge=1, le=100)] = 20
    cursor: Annotated[str, Field(min_length=1, max_length=3000)] | None = None


class VersionRef(StrictModel):
    id: Text
    sha256: Sha


class ConsumptionPolicy(StrictModel):
    policy_id: Text
    version_id: Text
    forecast_version_id: Text
    statuses: Annotated[list[Literal['OPEN']], Field(min_length=1, max_length=1)]
    backward_days: Annotated[int, Field(ge=0, le=366)]
    forward_days: Annotated[int, Field(ge=0, le=366)]
    match_customer: bool
    coverage: Literal['GROSS_INCLUDES_FIRM_ORDERS']


class NetTerms(StrictModel):
    priority: Annotated[int, Field(ge=0, le=2147483647)]
    minimum_shipment: Quantity
    maximum_lateness_seconds: Annotated[int, Field(ge=0, le=2147483647)]
    due_at: At


class AdvancedNetting(StrictModel):
    revision: Literal['advanced-netting-v1']
    forecast_version_ref: VersionRef
    consumption_policy: ConsumptionPolicy
    net_terms: NetTerms


class Inquiry(StrictModel):
    id: Annotated[str, Field(pattern='^INQUIRY-[A-Za-z0-9_-]{1,60}$')]
    customer_id: Text
    item_id: Text
    location_id: Text
    uom: Text
    quantity: Quantity
    due_at: At
    delivery_rule: Literal['ALL_OR_NOTHING', 'SPLIT_BY_NATIVE_EVENTS']
    coverage: Literal['INCREMENTAL_OUTSIDE_GROSS']
    protect_existing: Literal[True]
    request_source: Reason | None = None


class ReplenishmentRow(StrictModel):
    item_id: Text
    location_id: Text
    uom: Text
    minimum: Quantity
    maximum: Quantity
    effective_from: At
    effective_to: At


class Replenishment(StrictModel):
    policy_version: Text
    approval_status: Literal['RESEARCH_POLICY']
    source: Reason
    rows: Annotated[list[ReplenishmentRow], Field(min_length=1, max_length=30)]


class JointPurchase(StrictModel):
    reference: Text
    new_receipt_at: At


class JointDistribution(StrictModel):
    lane_id: Text
    new_lead_time_seconds: Annotated[int, Field(ge=1, le=2147483647)]


class Supply(StrictModel):
    purchase: JointPurchase | None
    distribution: JointDistribution | None


class ProfessionalPreview(Identity):
    netting: AdvancedNetting | None = None
    inquiry: Inquiry | None = None
    replenishment: Replenishment | None = None
    supply: Supply | None = None
    domain_change: legacy_scenarios.DomainChange | None = None
    reason: Reason

    @model_validator(mode='after')
    def exclusive_domain(self):
        legacy_scenarios.validate_exclusive_domain(self.model_dump(mode='json'))
        return self


class ProfessionalCreate(ProfessionalPreview):
    fingerprint: Sha
    source_sha256: Sha | None = None
    acknowledge_limits: Literal[True]


class ForecastStart(Identity):
    dataset_id: Text
    model: Literal['Naive', 'SeasonalNaive', 'AutoETS', 'CrostonSBA', 'TSB']
    training_cutoff: At
    issue_time: At
    horizon: Annotated[int, Field(ge=1, le=20)]


class ForecastChange(StrictModel):
    version_id: Text
    actor: Annotated[str, Field(min_length=1, max_length=100)]
    reason: Reason


class ConfirmForecast(ForecastChange):
    kind: Literal['confirm']


class OverrideForecast(ForecastChange):
    kind: Literal['override']
    row_id: Text
    quantity: Quantity


class RestoreForecast(ForecastChange):
    kind: Literal['restore']
    restore_version_id: Text


class WithdrawForecast(ForecastChange):
    kind: Literal['withdraw']


class ForecastRevise(Identity):
    change: Annotated[Union[ConfirmForecast, OverrideForecast, RestoreForecast, WithdrawForecast], Field(discriminator='kind')]


class InventoryResearch(Identity):
    source_id: Text
    service_level: Annotated[float, Field(gt=0.5, lt=0.9999)]
    reason: Reason
    method: Literal['CURRENT_SOFT_MIN_MAX', 'DETERMINISTIC_ORDER_UP_TO', 'NORMAL_SERVICE_ORDER_UP_TO'] | None = None
    forecast_version_id: Text | None = None


class QuoteRequest(StrictModel):
    item_id: Text
    location_id: Text
    uom: Text
    customer_id: Text
    quantity: Quantity
    due_at: At
    allow_partial: bool
    supply_policy: Literal['CONFIRMED_ONLY', 'RESEARCH_CTP'] = 'CONFIRMED_ONLY'


class CreateQuote(StrictModel):
    kind: Literal['create']
    request: QuoteRequest


class QuoteTransition(StrictModel):
    kind: Literal['calculate', 'offer', 'release', 'commit_simulated']
    quote_id: Text


class HoldQuote(StrictModel):
    kind: Literal['hold']
    quote_id: Text
    expires_at: At


class ResearchQuote(Identity):
    transition: Annotated[Union[CreateQuote, QuoteTransition, HoldQuote], Field(discriminator='kind')]


class OptimizationStart(Identity):
    algorithm: Literal['cp_sat', 'vns']
    scope: Literal['setup_only', 'setup_and_downstream']
    budget_seconds: Annotated[int, Field(ge=5, le=600)]


class WindowEdit(StrictModel):
    kind: Literal['window', 'proposed_window']
    reference: Text
    start_at: At


class MachineEdit(StrictModel):
    kind: Literal['machine']
    reference: Text
    resource_id: Text


class SequenceEdit(StrictModel):
    kind: Literal['sequence']
    reference: Text
    order: Annotated[list[Text], Field(min_length=1, max_length=24)]


class ScheduleAssignment(StrictModel):
    reference: Text
    start_at: At
    setup_end_at: At
    end_at: At
    resource_id: Text


class OptimizedEdit(StrictModel):
    kind: Literal['optimized_schedule']
    reference: Literal['optimization']
    assignments: Annotated[list[ScheduleAssignment], Field(min_length=1, max_length=128)]


class NativeStrategyEdit(StrictModel):
    kind: Literal['native_strategy']
    reference: Literal['optimization']
    solver: Literal['heuristic', 'heuristic_2']
    machine_search: Literal['PRIORITY', 'MINPENALTY']


class TaskStartAnchor(StrictModel):
    mode: Literal['START_ANCHOR']
    start_at: At


class TaskStartChange(StrictModel):
    time: TaskStartAnchor


class TaskStartEdit(StrictModel):
    """Expose the existing shift-aware Owner contract, without other task edits."""
    kind: Literal['task']
    reference: Text
    changes: TaskStartChange


EditorEdit = Annotated[Union[WindowEdit, MachineEdit, SequenceEdit, OptimizedEdit, NativeStrategyEdit, TaskStartEdit], Field(discriminator='kind')]


class SchedulePreview(Identity):
    edits: Annotated[list[EditorEdit], Field(min_length=1, max_length=24)]
    reason: Reason


class ScheduleCreate(SchedulePreview):
    source_sha256: Sha
    fingerprint: Sha
    acknowledge_limits: Literal[True]


class ReviewSave(Identity):
    target: Draft
    candidate_run_id: Run
    fingerprint: Sha
    recommendation: Literal['DISCUSS', 'RECOMMEND_DISCUSSION', 'DEFER']
    reviewer: Annotated[str, Field(min_length=1, max_length=100)]
    reason: Annotated[str, Field(min_length=1, max_length=2000)]
    object_scope: Reason
    next_action: Reason
    acknowledge_limits: Literal[True]


class PlanningRun(Identity):
    target: Annotated[str,Field(pattern='^DRAFT-[a-f0-9]{16}$',description='Must be the same existing candidate as draft_id, never baseline. Before first run its current run_id is still the source baseline; recover task metadata with its original task identity.')]
    draft_id: Draft


class BusinessTarget(StrictModel):
    kind: Text
    id: Text


class ProfessionalIntent(Identity):
    requested_outcome: Reason
    business_targets: Annotated[list[BusinessTarget], Field(max_length=30)]
    hard_constraints: Annotated[list[Reason], Field(max_length=30)]
    optimization_objectives: Annotated[list[Reason], Field(max_length=20)]
    preferences: Annotated[list[Reason], Field(max_length=20)]
    missing_information: Annotated[list[Reason], Field(max_length=20)]
    evidence_requirements: Annotated[list[Reason], Field(max_length=30)]


class ProfessionalAnswer(Identity):
    answer: Annotated[str, Field(min_length=1, max_length=20000)]
    limitations: Annotated[list[Reason], Field(max_length=30)]
    evidence_call_ids: Annotated[list[Text], Field(min_length=1, max_length=100)]


ACTION_SPECS = {
    'CREATE_PROFESSIONAL_SCENARIO': dict(name='request_professional_scenario', model=ProfessionalCreate, route='professional-policy-drafts'),
    'FORECAST_START': dict(name='request_forecast_research', model=ForecastStart, action='forecast_start', expensive=True),
    'FORECAST_REVISE': dict(name='request_forecast_revision', model=ForecastRevise),
    'INVENTORY_RECOMMEND': dict(name='request_inventory_research', model=InventoryResearch, action='inventory_recommend', expensive=True),
    'RESEARCH_QUOTE': dict(name='request_research_quote', model=ResearchQuote),
    'START_SCHEDULE_OPTIMIZATION': dict(name='request_schedule_optimization', model=OptimizationStart, route='plan-editor/optimization', expensive=True),
    'CREATE_SCHEDULE_CANDIDATE': dict(name='request_schedule_candidate', model=ScheduleCreate, route='plan-editor/drafts'),
    'SAVE_SCENARIO_REVIEW': dict(name='request_scenario_review', model=ReviewSave, route='review-notes'),
    'RUN_PLANNING': dict(name='request_run_planning', model=PlanningRun, expensive=True),
}


DESCRIPTIONS = {
    'record_professional_intent': 'Record the external model interpretation of the original business request in this task: exact business targets, intended outcome, hard constraints, optimization objectives, preferences, missing information and evidence requirements. Task metadata only. Does not grant action permissions, confirm user decisions, edit enterprise facts or prove a business audit.',
    'finish_professional_task': 'Save the external model business answer with limitations and exact completed evidence call IDs from this same task. Task metadata only; server rejects unrelated or unfinished calls. Does not promote a model conclusion to business audit PASS, grant execution permission or create another scenario.',
    'get_professional_evidence': 'Read bounded, source-backed evidence for one exact plan/version/baseline/run/target. Select a view and exact filters, then page by cursor. inventory requires exact item_id and location_id (uom optional); use lot_id/status/kind/record_id to retrieve opening lots, nested source records and native events completely. Its time window preserves the authenticated predecessor sequence. manufacturing queries exact job_id, source order_line_id or operation_id: actual execution, issued material, remaining native operations/resources and same-run WIP audit stay separate; record_id selects one evidence fragment and detail_fields pointers name its paged children. scenario_review requires draft_ids explicitly ([] reads baseline and available drafts); schedule without search_id discovers current contract and latest optimization, with search_id reads that job; plan_conditions reads explicit current rules including overtime, never inferring rules from calendars. Inventory quality, Firm, Forecast and Inquiry stay separate. Missing evidence is never zero or PASS; shared flows are not order allocations. No solve or enterprise fact change.',
    'preview_professional_scenario': 'Preview existing professional research. Use joint netting/inquiry/replenishment/supply, OR exclusive typed domain_change for original legacy forecast bucket override, Foundation Gross bucket, inventory source/MOQ decision or exact current Firm demand_priority. Read corresponding source evidence first; priority uses demand_risks/order evidence and only changes one priority, never quantity/due/resources. Original Owner validates applicability and pending policy; no draft or Worker. Preserve preview fingerprint and source_sha256 if present; user confirmation is required for creation.',
    'preview_schedule_candidate': 'Preview exact edits under the current baseline Plan Editor contract. Protected orders, qualified machines/persons and original commitments stay protected. Optimization assignments require original model screening; preview is not native feasibility or execution approval. No draft or solve.',
    'request_professional_scenario': 'Request a NOT_RUN research draft using exact current preview fingerprint. Joint Foundation fields and typed domain_change are mutually exclusive. Inventory decision also requires original source_sha256. Server rechecks the original Owner preview and preserves its confirmation contract and policy blocks. Task confirmation required; no self approval. Same request key recovers response; run separately. No formal policy or enterprise change.',
    'request_forecast_research': 'Request a bounded real statistical Forecast job on an existing registered dataset. Five supported models; horizon 1..20. Task confirmation and a configured real runtime are required. Produces a research candidate, never an approved enterprise policy; inspect training cutoff, backtest, intervals and source before revising.',
    'request_forecast_revision': 'Request explicit confirm, manual override, restore or withdrawal of an immutable research Forecast version. Requires an exact version and actor/reason; only the selected typed change is accepted. Server user confirmation is mandatory. Original enterprise Gross is never overwritten and no enterprise policy approval is granted.',
    'request_inventory_research': 'Request one source-bound Stockpyl policy research candidate using registered history and explicit service level. Original Owner rejects wrong dimensions, unmapped reservations and active conflicting research Holds. A real library receipt is required. Saves a research candidate only; no PO or Worker is created. Task confirmation required.',
    'request_research_quote': 'Request a typed research Inquiry/ATP/Offer/Hold/Release/simulated-commit transition. Every persistent step passes the server task confirmation gate. Strict source policies and original HoldStore enforce competition and expiry; never promise both competing Holds succeeded. calculate records ATP, it does not run finite CTP. No ERP reservation, formal promise or real commit.',
    'request_schedule_optimization': 'Request bounded CP-SAT or linked VNS on the certified current-baseline Plan Editor scope. Explicit seconds budget 5..600 and server user confirmation required. Recover by original search_id. Model-screened candidates require preview, isolated draft, original Worker and audit before business comparison. No global optimality or setup saving is promised.',
    'request_schedule_candidate': 'Request a NOT_RUN schedule candidate using exact edits, original source SHA and current preview fingerprint. Server confirmation required. Original Plan Editor replays and screens edits; protected MO and delivery commitments remain. No solve, publication or production instruction.',
    'request_scenario_review': 'Request saving a non-executing review only when the user explicitly asks. Bind exact candidate run and original current review fingerprint; original review gate requires comparable audited evidence. Recommendation only DISCUSS/RECOMMEND_DISCUSSION/DEFER. Reviewer is self-reported local identity; never enterprise approval or publication.',
    'request_run_planning': 'Request original Worker execution of one exact existing isolated draft. Server task confirmation and compute budget required. Reuses original draft/run idempotency. Submission is not completion, and SUCCEEDED is not business audit PASS. Inspect current task and actual run evidence before reporting a result.',
}


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_encoded(value).encode()).hexdigest()


def _plain(model, *, unset=False):
    return model.model_dump(mode='json', exclude_none=False, exclude_unset=unset)


def _identity(value):
    return {key: value[key] for key in IDENTITY_FIELDS}


def _model_task_projection(value):
    """UI confirmation credentials never cross the model/MCP boundary."""
    if isinstance(value,dict):
        return {key:_model_task_projection(item) for key,item in value.items()
                if not key.lower().endswith('nonce')}
    if isinstance(value,list):
        return [_model_task_projection(item) for item in value]
    return copy.deepcopy(value)


def _task_url(identity,task_id):
    return '/workspace/#/plans/'+quote(identity['plan_id'],safe='')+'?target='+quote(identity['target'],safe='')+'&assistant=1&assistant_task='+quote(task_id,safe='')


def _task_summary_for_model(task,endpoint,call_offset=0,page_size=10):
    """Bound task recovery, keeping the unabridged record in SessionStore."""
    if type(call_offset) is not int or not 0<=call_offset<=80 or type(page_size) is not int or not 1<=page_size<=20:
        raise BusinessToolError('TASK_PAGE_INVALID')
    task=_model_task_projection(task)
    def bounded(value,pointer,budget=4000):
        projected=BusinessTools._compact(value,endpoint,pointer)
        if len(_encoded(projected).encode())<=budget:return projected
        return dict(record_ref=endpoint+'#'+pointer,sha256=_digest(value),projection_status='BOUNDED_TASK_FIELD',
                    **({'excerpt':value[:1000],'characters':len(value)} if isinstance(value,str) else {}))
    omitted={'messages','events','tool_calls','evidence','candidates','pending_confirmation'}
    result={key:bounded(value,'/'+key,8000 if key=='original_request' else 4000) for key,value in task.items() if key not in omitted}
    messages=task.get('messages',[])
    result['messages']=[{key:bounded(value,'/messages/'+str(index)+'/'+key,2000) for key,value in message.items()}
                        for index,message in list(enumerate(messages))[-6:]]
    events=task.get('events',[])
    result['events']=[bounded(event,'/events/'+str(index),2000) for index,event in list(enumerate(events))[-12:]]
    ledger=sorted(task.get('tool_calls',{}).items(),key=lambda item:(item[1].get('started_at',''),item[1].get('call_id','')),reverse=True)
    window=task.get('history_window',{})
    owner_offset=window.get('tool_calls_offset',0)
    local_offset=max(0,call_offset-owner_offset) if window.get('bounded') else call_offset
    total_calls=window.get('tool_calls_total',len(ledger))
    result['tool_calls']={}
    for key,call in ledger[local_offset:local_offset+page_size]:
        pointer='/tool_calls/'+key;receipt=call.get('result') or {};business=receipt.get('result') or {}
        call_ref=call.get('receipt_ref') or endpoint+'#'+pointer
        result_ref=call_ref+'#/call/result' if call.get('receipt_ref') else endpoint+'#'+pointer+'/result'
        arguments_ref=call_ref+'#/call/arguments' if call.get('receipt_ref') else endpoint+'#'+pointer+'/arguments'
        summary={field:call[field] for field in ('call_id','request_id','tool','state','read_only','action_kind','fingerprint','started_at','finished_at','elapsed_ms') if field in call}
        summary.update(identity=receipt.get('identity') or {field:call.get('arguments',{}).get(field) for field in IDENTITY_FIELDS},
            source=bounded(receipt.get('source',{}),pointer+'/result/source',2000),
            evidence=dict(record_ref=result_ref+'/evidence',**({'sha256':_digest(receipt['evidence'])} if 'evidence' in receipt else {})),
            result_status=receipt.get('result_status','NOT_COLLECTED'),
            result_ref=result_ref,arguments_ref=arguments_ref,
            arguments_sha256=_digest(call['arguments']) if 'arguments' in call else call.get('fingerprint'),
            error=bounded(call.get('error'),pointer+'/error',1000))
        if call.get('receipt_ref'):summary['receipt_ref']=call['receipt_ref']
        fields={'id','draft_id','run_id','solve_run_id','search_id','version_id','inventory_id','quote_id','state','status','business_status','fingerprint','workbench_url'}
        if isinstance(business,dict):
            summary['artifacts']={field:value for field,value in business.items() if field in fields}
            for child in ('draft','run','quote'):
                if isinstance(business.get(child),dict):summary['artifacts'][child]={field:value for field,value in business[child].items() if field in fields}
        result['tool_calls'][key]=summary
    end=call_offset+len(result['tool_calls'])
    result['ledger_page']=dict(offset=call_offset,returned=len(result['tool_calls']),total=total_calls,
        next_offset=end if end<total_calls and result['tool_calls'] else None,order='MOST_RECENT_FIRST',task_revision=task.get('revision'))
    result['candidates']=bounded(task.get('candidates',[]),'/candidates',4000)
    result['evidence']=bounded(task.get('evidence',[]),'/evidence',4000)
    pending=task.get('pending_confirmation')
    result['pending_confirmation']={key:bounded(value,'/pending_confirmation/'+key,3000) for key,value in pending.items()} if isinstance(pending,dict) else pending
    result['projection']=dict(schema='aps-professional-task-projection/1',source=dict(owner='SessionStore',endpoint=endpoint,
        response_sha256=_digest(task)),messages_returned=len(result['messages']),messages_total=window.get('messages_total',len(messages)),
        events_returned=len(result['events']),events_total=window.get('events_total',len(events)),full_record_retained=True,
        limits=['Task and call summaries are metadata, never business audit PASS. Read exact professional evidence by its original Owner and identity.'])
    result['workbench_task_url']=_task_url(task['identity'],task['id'])
    if len(_encoded(result).encode())>64000:
        raise BusinessToolError('TASK_RESPONSE_BUDGET_EXCEEDED')
    return result


class BusinessTools:
    MAX_RESPONSE_BYTES = 64000
    MODELS = {'get_professional_evidence': EvidenceArgs,
              'preview_professional_scenario': ProfessionalPreview,
              'preview_schedule_candidate': SchedulePreview,
              'record_professional_intent': ProfessionalIntent,
              'finish_professional_task': ProfessionalAnswer,
              **{spec['name']: spec['model'] for spec in ACTION_SPECS.values()}}

    def __init__(self, base_url, *, transport=None, trusted_owner=False):
        parsed = urlsplit(base_url)
        if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost'}
                or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'', '/'}):
            raise BusinessToolError('LOCAL_P3_URL_REQUIRED')
        self.base = base_url.rstrip('/')
        self.http = requests.Session()
        self.http.headers['X-Planning-Client'] = 'p1'
        if trusted_owner:
            from assistant_agent_access import AgentAccess
            access=AgentAccess.from_env()
            if access.status()['configured']:
                self.http.headers.update(access.internal_owner_headers())
        self.transport = transport

    @classmethod
    def specs(cls):
        kinds = {spec['name']: (kind, spec) for kind, spec in ACTION_SPECS.items()}
        return [dict(name=name, description=DESCRIPTIONS[name], inputSchema=model.model_json_schema(),
                     readOnly=name not in kinds, expensive=kinds.get(name, ('', {}))[1].get('expensive', False),
                     **({'action_kind': kinds[name][0], 'confirmation_required': True} if name in kinds else {}))
                for name, model in cls.MODELS.items()]

    @classmethod
    def tool_spec(cls, name):
        found = next((spec for spec in cls.specs() if spec['name'] == name), None)
        if found is None:
            raise BusinessToolError('TOOL_NOT_ALLOWED')
        return found

    @classmethod
    def validate(cls, name, arguments):
        if name not in cls.MODELS:
            raise BusinessToolError('TOOL_NOT_ALLOWED')
        if name=='request_run_planning' and isinstance(arguments,dict) and arguments.get('target')!=arguments.get('draft_id'):
            raise BusinessToolError('DRAFT_TARGET_MISMATCH')
        try:
            return cls.MODELS[name].model_validate(arguments)
        except (ValidationError, TypeError, ValueError):
            raise BusinessToolError('TOOL_ARGUMENTS_INVALID') from None

    def _request(self, method, path, body=None):
        if self.transport:
            return self.transport(method, path, body)
        try:
            reply = self.http.request(method, self.base + path, json=body, timeout=(5, 30))
        except requests.RequestException:
            raise BusinessToolError('P3_UNAVAILABLE',outcome_unknown=method not in {'GET','HEAD'}) from None
        try:
            value = reply.json()
        except ValueError:
            raise BusinessToolError('P3_INVALID_RESPONSE',status_code=reply.status_code,
                                    outcome_unknown=method not in {'GET','HEAD'}) from None
        if not isinstance(value,dict):
            raise BusinessToolError('P3_INVALID_RESPONSE',status_code=reply.status_code,
                                    outcome_unknown=method not in {'GET','HEAD'})
        if not reply.ok:
            detail = value.get('detail', {})
            code = detail.get('code', 'P3_HTTP_' + str(reply.status_code)) if isinstance(detail, dict) else 'P3_INVALID_ARGUMENTS'
            raise BusinessToolError(code,status_code=reply.status_code,outcome_unknown=reply.status_code>=500)
        return value

    @staticmethod
    def _query(path, values):
        return path + '?' + urlencode({key: value for key, value in values.items() if value is not None})

    @staticmethod
    def _path(identity, suffix=''):
        return '/v1/enterprise-plans/' + quote(identity['plan_id'], safe='') + ('/' + suffix if suffix else '')

    def _bind(self, identity):
        record = self._request('GET', self._path(identity))
        if (record.get('id') != identity['plan_id'] or record.get('data_version_id') != identity['data_version_id']
                or record.get('run', {}).get('solve_run_id') != identity['baseline_run_id']):
            raise BusinessToolError('TOOL_SCOPE_MISMATCH')
        return record

    @staticmethod
    def _check_identity(value, identity, required):
        actual = value.get('identity', {})
        if not isinstance(actual, dict) or any(actual.get(k) != identity[k] for k in required):
            raise BusinessToolError('TOOL_SCOPE_MISMATCH')
        for key in IDENTITY_FIELDS:
            if key in actual and actual[key] != identity[key]:
                raise BusinessToolError('TOOL_SCOPE_MISMATCH')

    @classmethod
    def _compact(cls, value, endpoint, pointer=''):
        """Bound display arrays with retrievable original Owner pointers, not edits."""
        if isinstance(value, dict):
            result={}
            for key,item in value.items():
                path=pointer+'/'+key.replace('~','~0').replace('/','~1')
                if key=='forecast_version' and isinstance(item,dict) and {'id','sha256','rows'}<=set(item):
                    result[key]={field:item[field] for field in ('id','sha256','status','classification','model','training_cutoff','issue_time','dataset_id','dataset_sha256','parent_version_id') if field in item}
                    result[key].update(row_count=len(item['rows']),record_ref=endpoint+'#'+path,
                        registry_ref='professional_artifacts/version/'+item['id'],projection_status='FORECAST_VERSION_REFERENCE')
                elif {'gross_quantity','consumed_quantity','net_quantity'}<=set(value) and key in {'allocations','consumptions','source_refs'} and isinstance(item,list) and len(_encoded(item).encode())>1500:
                    result[key]=dict(count=len(item),sha256=_digest(item),record_ref=endpoint+'#'+path,projection_status='BOUNDED_REFERENCE_LIST')
                else:result[key]=cls._compact(item,endpoint,path)
            return result
        if isinstance(value, list):
            if len(value)<=24 and value and all(isinstance(row,dict) and {'gross_quantity','consumed_quantity','net_quantity'}<=set(row) for row in value):
                return [cls._compact(item,endpoint,pointer+'/'+str(index)) for index,item in enumerate(value)]
            if len(value) > 24 or len(_encoded(value).encode()) > 6000:
                return dict(count=len(value), sha256=_digest(value), record_ref=endpoint + '#' + pointer,
                            projection_status='BOUNDED_REFERENCE_LIST')
            return [cls._compact(v, endpoint, pointer + '/' + str(i)) for i, v in enumerate(value)]
        return copy.deepcopy(value)

    @classmethod
    def _envelope(cls, identity, endpoint, raw, *, status='PRESENT', checked=None, result=None):
        evidence = dict(original=cls._compact(raw.get('evidence', {}), endpoint, '/evidence'),
                        validation=cls._compact(raw.get('validation', {}), endpoint, '/validation'),
                        files=cls._compact(raw.get('evidence_files', []), endpoint, '/evidence_files'))
        return dict(schema_version='aps-professional-tools/1', identity=identity, result_status=status,
                    source=dict(owner='EXISTING_P3_DOMAIN_OWNER', endpoint=endpoint,
                                response_sha256=_digest(raw)), evidence=evidence,
                    checked_scope=dict(identity=identity, **(checked or {})),
                    result=cls._compact(raw,endpoint) if result is None else result,
                    simulation_only=True, execution_authorized=False,
                    workbench_url='/workspace/#/plans/' + identity['plan_id'] + '?target=' + identity['target'])

    @staticmethod
    def _source_record(raw,pointer):
        value=raw
        if not pointer:return value
        try:
            for part in pointer[1:].split('/'):
                part=part.replace('~1','/').replace('~0','~')
                value=value[int(part)] if isinstance(value,list) else value[part]
            return value
        except (KeyError,IndexError,ValueError,TypeError):
            raise BusinessToolError('INVALID_EVIDENCE_POINTER') from None

    def request_task_tool(self, task_id, name, arguments, request_id):
        model = self.validate(name, arguments)
        if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 200:
            raise BusinessToolError('TASK_ID_INVALID')
        self._key(request_id)
        args = _plain(model, unset=True)
        path = self._query('/v1/agent-tasks/' + quote(task_id, safe='') + '/tools', dict(plan_id=model.plan_id))
        value = self._request('POST', path, dict(request_id=request_id, tool=name, arguments=args))
        if value.get('task_id') != task_id:
            raise BusinessToolError('TASK_SCOPE_MISMATCH')
        self._check_identity(value, _identity(_plain(model)), IDENTITY_FIELDS)
        value=_model_task_projection(value)
        value['workbench_task_url']=_task_url(_identity(_plain(model)),task_id)
        return value

    @staticmethod
    def _key(request_id):
        import re
        if not isinstance(request_id, str) or not re.fullmatch('[a-f0-9]{32}', request_id):
            raise BusinessToolError('REQUEST_ID_INVALID')

    def execute(self, name, arguments, request_id=None):
        """Server use only, after the task Owner has authorized this exact call."""
        model = self.validate(name, arguments)
        args = _plain(model)
        identity = _identity(args)
        if name in {'record_professional_intent','finish_professional_task'}:
            raise BusinessToolError('ASSISTANT_METADATA_OWNER_REQUIRED')
        if name == 'get_professional_evidence':
            return self._read(model)
        if name in {'preview_professional_scenario', 'preview_schedule_candidate'}:
            self._bind(identity)
            if args.get('domain_change') is not None:
                return self._legacy_scenario(args)
            route = 'professional-policy-preview' if name == 'preview_professional_scenario' else 'plan-editor/preview'
            path = self._path(identity, route)
            payload = {k: v for k, v in args.items() if k != 'plan_id'}
            payload.pop('domain_change', None)
            if payload.get('inquiry') and payload['inquiry'].get('request_source') is None:
                payload['inquiry'].pop('request_source',None)
            if route == 'professional-policy-preview' and payload.get('supply') is None:
                payload.pop('supply')
            raw = self._request('POST', path, payload)
            self._check_identity(raw, identity, IDENTITY_FIELDS)
            return self._envelope(identity, path, raw, checked=dict(engine_executed=False, persistent_draft_created=False))
        self._key(request_id)
        self._bind(identity)
        if name == 'request_professional_scenario' and args.get('domain_change') is not None:
            return self._legacy_scenario(args,request_id)
        spec = next(spec for spec in ACTION_SPECS.values() if spec['name'] == name)
        payload = {k: v for k, v in args.items() if k != 'plan_id'}
        payload.pop('domain_change', None)
        if payload.get('source_sha256') is None:
            payload.pop('source_sha256', None)
        if payload.get('inquiry') and payload['inquiry'].get('request_source') is None:
            payload['inquiry'].pop('request_source',None)
        if name == 'request_run_planning':
            if identity['target'] != args['draft_id']:
                raise BusinessToolError('DRAFT_TARGET_MISMATCH')
            path = self._query('/v1/drafts/' + quote(args['draft_id'], safe='') + '/run', dict(plan_id=identity['plan_id']))
            raw = self._request('POST', path, {})
        elif name in {'request_forecast_research', 'request_forecast_revision', 'request_inventory_research', 'request_research_quote'}:
            business = {k: v for k, v in args.items() if k not in IDENTITY_FIELDS and v is not None}
            if name == 'request_forecast_revision':
                business = business['change']; action = 'forecast_' + business.pop('kind')
            elif name == 'request_research_quote':
                business = business['transition']; kind = business.pop('kind')
                action = 'quote_' + ('commit' if kind == 'commit_simulated' else kind)
            else:
                action = spec['action']
            path = self._path(identity, 'intelligence/actions')
            raw = self._request('POST', path, {**{k: identity[k] for k in IDENTITY_FIELDS if k != 'plan_id'},
                                               'action': action, 'payload': business, 'request_id': request_id})
            self._check_identity(raw, identity, IDENTITY_FIELDS)
        else:
            path = self._path(identity, spec['route'])
            if name == 'request_scenario_review':
                if args['candidate_run_id'] != identity['run_id']:
                    raise BusinessToolError('REVIEW_CANDIDATE_RUN_MISMATCH')
                gate_path = self._query(self._path(identity, 'review-notes'), dict(data_version_id=identity['data_version_id'],
                    baseline_run_id=identity['baseline_run_id'], draft_ids=identity['target']))
                gates = self._request('GET', gate_path)
                self._check_identity(gates, identity, ('plan_id', 'data_version_id', 'baseline_run_id'))
                gate = gates.get('gates', {}).get(identity['target'])
                if (not gate or gate.get('identity', {}).get('candidate_run_id') != args['candidate_run_id']
                        or gate.get('fingerprint') != args['fingerprint']):
                    raise BusinessToolError('REVIEW_EVIDENCE_CHANGED')
                payload = {k: v for k, v in payload.items() if k not in {'run_id', 'candidate_run_id'}}
                # Existing note Owner has no request_id. Task Owner deduplicates
                # confirmed calls and must never blindly replay unknown writes.
            else:
                payload['request_id'] = request_id
            raw = self._request('POST', path, payload)
            if 'identity' in raw:
                self._check_identity(raw, identity, ('plan_id', 'data_version_id', 'baseline_run_id'))
            elif any(k in raw and raw[k] != identity[k] for k in ('plan_id', 'data_version_id', 'baseline_run_id')):
                raise BusinessToolError('TOOL_SCOPE_MISMATCH')
        return self._envelope(identity, path, raw, checked=dict(authorized_by='SERVER_TASK_GATEWAY',
            request_id=request_id, business_audit='ORIGINAL_OWNER_ONLY', formal_execution_authorized=False))

    def _legacy_scenario(self,args,request_id=None):
        """Select a fixed existing Owner and retain its native confirmation contract."""
        identity=_identity(args)
        try:
            path,payload=legacy_scenarios.preview_request(args)
            raw=self._request('POST',path,payload)
            current=legacy_scenarios.preview_envelope(args,raw)
            if request_id is None:
                current['owner_response']=self._compact(raw,path)
                return self._envelope(identity,path,raw,result=current,
                    checked=dict(engine_executed=False,persistent_draft_created=False))
            path,payload=legacy_scenarios.create_request(args,request_id,current)
            raw=self._request('POST',path,payload)
            if args['domain_change']['kind']=='demand_priority':
                # The original priority Owner returns these three fields at
                # the response root, not inside an invented identity envelope.
                if any(raw.get(k)!=identity[k] for k in ('plan_id','data_version_id','baseline_run_id')):
                    raise BusinessToolError('TOOL_SCOPE_MISMATCH')
            else:self._check_identity(raw,identity,('plan_id','data_version_id','baseline_run_id'))
            return self._envelope(identity,path,raw,checked=dict(authorized_by='SERVER_TASK_GATEWAY',
                request_id=request_id,business_audit='ORIGINAL_OWNER_ONLY',formal_execution_authorized=False))
        except legacy_scenarios.LegacyScenarioError as error:
            raise BusinessToolError(error.code) from None

    def _read(self, model):
        """Prefer the advertised exact domain API; retain old-service compatibility."""
        args=_plain(model); identity=_identity(args)
        record=self._bind(identity)
        if model.view in legacy_scenarios.ROUTES:
            if model.cursor is not None:
                raise BusinessToolError('TOOL_CURSOR_NOT_APPLICABLE')
            try:
                path,query=legacy_scenarios.read_request(identity,model.view,model.filters.model_dump(exclude_none=True))
            except legacy_scenarios.LegacyScenarioError as error:
                raise BusinessToolError(error.code) from None
            path=self._query(path,query)
            raw=self._request('GET',path)
            self._check_identity(raw,identity,('plan_id','data_version_id','run_id','target'))
            status=raw.get('status') if raw.get('status') in {'UNSUPPORTED','UNREADABLE','INVALID_EVIDENCE','NOT_COLLECTED','EMPTY_CHECKED'} else 'PRESENT'
            envelope=self._envelope(identity,path,raw,status=status,checked=dict(original_owner_view=True,
                projection='BOUNDED_OWNER_RESPONSE',domain_pagination=False))
            if len(_encoded(envelope).encode())>self.MAX_RESPONSE_BYTES:
                raise BusinessToolError('EVIDENCE_RESPONSE_BUDGET_EXCEEDED')
            return envelope
        if model.view!='plan_conditions' and record.get('capabilities',{}).get('professional_query')=='professional-query/1':
            path=self._query(self._path(identity,'professional-query'),dict(
                **{k:identity[k] for k in IDENTITY_FIELDS if k!='plan_id'},view=model.view,
                filters=_encoded(model.filters.model_dump(exclude_none=True)),page_size=model.page_size,cursor=model.cursor))
            raw=self._request('GET',path)
            self._check_identity(raw,identity,IDENTITY_FIELDS)
            rows=[]
            for index,row in enumerate(raw['rows']):
                display=copy.deepcopy(row['record']) if model.view=='scenario_review' else self._compact(row['record'],path,'/rows/'+str(index)+'/record')
                rows.append({**row,'record':display,'projection':dict(display_sha256=_digest(display),
                    kind=(row.get('projection_status','EXACT_OWNER_RECORD') if model.view=='scenario_review' else 'EXACT_OWNER_RECORD' if display==row['record'] else 'BOUNDED_OWNER_RECORD'))})
            summary=copy.deepcopy(raw.get('summary',{})) if model.view=='scenario_review' else self._compact(raw.get('summary',{}),path,'/summary')
            result=dict(rows=rows,summary=summary)
            envelope=self._envelope(identity,path,raw,status=raw['result_status'],
                checked={k:v for k,v in raw.get('checked_scope',{}).items() if k!='identity'},result=result)
            envelope['page']=raw['page']
            envelope['source']['owner']='EnterprisePlans.professional_query'
            envelope['source']['snapshot_fingerprint']=raw['snapshot_fingerprint']
            envelope['evidence']['files']=raw.get('source_artifacts',[])
            envelope['timings']=raw.get('timings',{})
            if len(_encoded(envelope).encode())>self.MAX_RESPONSE_BYTES:raise BusinessToolError('EVIDENCE_RESPONSE_BUDGET_EXCEEDED')
            return envelope
        if model.view in {'versions','manufacturing'}:raise BusinessToolError('TOOL_VIEW_NOT_SUPPORTED_BY_SERVICE')
        return self._read_legacy(model)

    def _read_legacy(self, model):
        args = _plain(model); identity = _identity(args); view = model.view
        filters = model.filters.model_dump(exclude_none=True)
        allowed = {
            'demand_risks': {'order_line_id', 'item_id', 'location_id', 'start', 'end'},
            'resources': {'resource_id', 'start', 'end'},
            'inventory': {'item_id', 'location_id', 'uom', 'start', 'end'},
            'purchases': {'reference', 'item_id', 'location_id'},
            'forecast': {'version_id', 'row_id', 'dataset_id'},
            'inventory_research': {'inventory_id', 'source_id'},
            'promising': {'quote_id', 'item_id', 'location_id'},
            'schedule': {'search_id','reference'}, 'scenario_review': {'draft_ids'}, 'plan_conditions': set(),
        }
        if set(filters) - allowed[view]:
            raise BusinessToolError('TOOL_FILTER_NOT_APPLICABLE')
        if view == 'inventory' and not {'item_id', 'location_id'} <= set(filters):
            raise BusinessToolError('EXACT_INVENTORY_DIMENSION_REQUIRED')
        if view == 'scenario_review' and ('draft_ids' not in filters or len(set(filters['draft_ids'])) != len(filters['draft_ids'])):
            raise BusinessToolError('EXACT_REVIEW_SELECTION_REQUIRED')
        if filters.get('start') and filters.get('end') and datetime.fromisoformat(filters['start'].replace('Z','+00:00'))>=datetime.fromisoformat(filters['end'].replace('Z','+00:00')):
            raise BusinessToolError('INVALID_ANALYSIS_RANGE')
        self._bind(identity)
        query = {key: identity[key] for key in ('data_version_id', 'run_id', 'target')}
        rows = []; present = True; supporting_sources=[];latest_optimization=None
        def add(collection, pointer, kind):
            nonlocal present
            if not isinstance(collection, list):
                present = False; return
            for index, record in enumerate(collection):
                if not isinstance(record, dict):
                    raise BusinessToolError('INVALID_EVIDENCE')
                rows.append(dict(kind=kind, record=copy.deepcopy(record), pointer=pointer + '/' + str(index)))
        if view=='plan_conditions':
            path=self._query('/v1/plan-context',dict(plan_id=identity['plan_id'],target=identity['target']))
            raw=self._request('GET',path);version=raw.get('plan_version',{})
            if any(version.get(k)!=identity[v] for k,v in (('plan_id','plan_id'),('data_version_id','data_version_id'),('solve_run_id','run_id'))):
                raise BusinessToolError('TOOL_SCOPE_MISMATCH')
            if isinstance(raw.get('overtime'),dict):rows.append(dict(kind='overtime_policy',record=raw['overtime'],pointer='/overtime'))
            add(raw.get('effective_constraints'),'/effective_constraints','plan_constraint')
            continuity=raw.get('continuity_wip')
            if isinstance(continuity,dict):
                rows.append(dict(kind='continuity_wip_summary',record={k:copy.deepcopy(v) for k,v in continuity.items() if k!='rows'},pointer='/continuity_wip'))
                add(continuity.get('rows'),'/continuity_wip/rows','continuity_wip')
        elif view in {'demand_risks', 'resources', 'inventory'}:
            route = 'exceptions' if view == 'demand_risks' else 'analysis'
            query.update({k: v for k, v in filters.items() if k in {'resource_id', 'item_id', 'location_id', 'start', 'end'} and route == 'analysis'})
            path = self._query(self._path(identity, route), query)
            raw = self._request('GET', path)
            self._check_identity(raw, identity, ('plan_id', 'data_version_id', 'run_id', 'target'))
            if view == 'demand_risks':
                add(raw.get('demands'), '/demands', 'demand')
            elif view == 'resources':
                component = raw.get('resource_analysis', {})
                add(component.get('resources'), '/resource_analysis/resources', 'resource')
                add(component.get('intervals', []), '/resource_analysis/intervals', 'resource_interval')
            else:
                component = raw.get('inventory_analysis', {})
                series = component.get('series')
                if not isinstance(series, list): present = False
                else:
                    for index, row in enumerate(series):
                        base = '/inventory_analysis/series/' + str(index)
                        rows.append(dict(kind='inventory_series',record={k:v for k,v in row.items() if k!='events'},pointer=base))
                        for event_index,event in enumerate(row.get('events', [])):
                            rows.append(dict(kind='inventory_event', record={**{k: row[k] for k in ('item_id','location_id','uom') if k in row}, **event},
                                             pointer=base + '/events/' + str(event_index)))
        elif view in {'forecast', 'inventory_research', 'promising'}:
            query['baseline_run_id'] = identity['baseline_run_id']
            path = self._query(self._path(identity, 'intelligence'), query)
            raw = self._request('GET', path)
            self._check_identity(raw, identity, IDENTITY_FIELDS)
            if view == 'forecast':
                draft=raw.get('context',{}).get('draft') or {}
                adopted=draft.get('adopted',{}).get('forecast_version_id')
                net_rows=draft.get('changes',{}).get('netting',{}).get('rows',[])
                if isinstance(net_rows,list) and not filters.get('dataset_id'):
                    for index,row in enumerate(net_rows):
                        version=row.get('version_id',adopted)
                        if filters.get('version_id') and version!=filters['version_id']:continue
                        if filters.get('row_id') and filters['row_id'] not in {row.get('id'),row.get('forecast_id')}:continue
                        rows.append(dict(kind='forecast_netting_row',record=dict(row,forecast_version_id=version),
                            pointer='/context/draft/changes/netting/rows/'+str(index)))
                if not filters.get('version_id') and not filters.get('row_id'):
                    for index,dataset in enumerate(raw.get('datasets',[])):
                        if filters.get('dataset_id') and dataset.get('id')!=filters['dataset_id']:continue
                        base='/datasets/'+str(index)
                        rows.append(dict(kind='forecast_dataset',record={k:v for k,v in dataset.items() if k!='history_rows'},pointer=base))
                        for offset,row in enumerate(dataset.get('history_rows',[])):
                            rows.append(dict(kind='forecast_history_row',record=dict(row,dataset_id=dataset['id'],dataset_sha256=dataset.get('sha256')),pointer=base+'/history_rows/'+str(offset)))
                    for index,job in enumerate(raw.get('jobs',[])):
                        if filters.get('dataset_id') and job.get('parameters',{}).get('dataset_id')!=filters['dataset_id']:continue
                        rows.append(dict(kind='forecast_job',record=job,pointer='/jobs/'+str(index)))
                versions = raw.get('forecast_versions')
                if not isinstance(versions, list): present = False
                else:
                    for index, version in enumerate(versions):
                        if filters.get('version_id') and version.get('id') != filters['version_id']: continue
                        if filters.get('dataset_id') and version.get('dataset_id') != filters['dataset_id']: continue
                        base = '/forecast_versions/' + str(index)
                        if not filters.get('row_id'):
                            rows.append(dict(kind='forecast_version', record={k:v for k,v in version.items() if k!='rows'}, pointer=base))
                        for offset, row in enumerate(version.get('rows', [])):
                            if filters.get('row_id') and row.get('id') != filters['row_id']: continue
                            rows.append(dict(kind='forecast_row', record=dict(row, forecast_version_id=version['id'],version_sha256=version.get('sha256')),
                                             pointer=base+'/rows/'+str(offset)))
            elif view == 'inventory_research': add(raw.get('inventory_candidates'), '/inventory_candidates', 'inventory_candidate')
            else: add(raw.get('quotes'), '/quotes', 'research_quote')
        elif view == 'purchases':
            query['baseline_run_id'] = identity['baseline_run_id']
            path = self._query(self._path(identity, 'professional-policy'), query)
            raw = self._request('GET', path)
            self._check_identity(raw, identity, IDENTITY_FIELDS)
            add(raw.get('purchases'), '/purchases', 'purchase')
            add(raw.get('purchase_receipts', []), '/purchase_receipts', 'purchase_receipt')
            add(raw.get('source_terms', []), '/source_terms', 'source_terms')
            sources={row['id']:(index,row) for index,row in enumerate(raw.get('source_terms',[]))}
            purchases={row['id']:(index,row) for index,row in enumerate(raw.get('purchases',[]))}
            if len(sources)!=len(raw.get('source_terms',[])) or len(purchases)!=len(raw.get('purchases',[])):
                raise BusinessToolError('INVALID_EVIDENCE')
            for row in rows:
                record=row['record'];po=record;links={}
                if row['kind']=='purchase_receipt':
                    if record.get('po_id') not in purchases:raise BusinessToolError('INVALID_EVIDENCE')
                    po_index,po=purchases[record['po_id']]
                    links.update(purchase_id=record['po_id'],purchase_pointer='/purchases/'+str(po_index))
                if row['kind'] in {'purchase','purchase_receipt'}:
                    if po.get('supply_source_id') not in sources:raise BusinessToolError('INVALID_EVIDENCE')
                    source_index,source=sources[po['supply_source_id']]
                    row['resolved_dimensions']={k:source[k] for k in ('item_id','location_id') if k in source}
                    links.update(supply_source_id=source['id'],source_pointer='/source_terms/'+str(source_index))
                    row['owner_links']=links
        elif view == 'schedule':
            query['baseline_run_id'] = identity['baseline_run_id']
            route = 'plan-editor/optimization/' + filters['search_id'] if filters.get('search_id') else 'plan-editor'
            path = self._query(self._path(identity, route), query)
            raw = self._request('GET', path)
            self._check_identity(raw, identity, IDENTITY_FIELDS)
            if filters.get('search_id'):
                edit=raw.get('edit') or {}
                rows.append(dict(kind='optimization',record={**{k:v for k,v in raw.items() if k!='edit'},
                    'edit':{k:v for k,v in edit.items() if k!='assignments'}},pointer=''))
                for index,assignment in enumerate(edit.get('assignments',[])):
                    rows.append(dict(kind='schedule_assignment',record=assignment,pointer='/edit/assignments/'+str(index)))
            else:
                for key in ('orders', 'proposal_tasks', 'resources', 'calendars', 'resource_skills'):
                    add(raw.get(key, []), '/' + key, key)
                if raw.get('contract'): rows.append(dict(kind='schedule_contract',record=raw['contract'],pointer='/contract'))
                latest_path=self._query(self._path(identity,'plan-editor/optimization'),query)
                latest_raw=self._request('GET',latest_path)
                self._check_identity(latest_raw,identity,IDENTITY_FIELDS)
                latest_optimization=latest_raw.get('job')
                supporting_sources=[dict(endpoint=path,response_sha256=_digest(raw),owner='EnterprisePlans.plan_editor_options'),
                    dict(endpoint=latest_path,response_sha256=_digest(latest_raw),owner='optimization.service.latest')]
        else:
            query = dict(data_version_id=identity['data_version_id'], baseline_run_id=identity['baseline_run_id'], draft_ids=','.join(filters['draft_ids']))
            path = self._query(self._path(identity, 'scenario-review'), query)
            raw = self._request('GET', path)
            self._check_identity(raw, identity, ('plan_id', 'data_version_id', 'baseline_run_id'))
            add(raw.get('candidates'), '/candidates', 'scenario_candidate')
            # Exact current review gates are needed to save a non-executing note.
            notes_path = self._query(self._path(identity, 'review-notes'), query)
            notes = self._request('GET', notes_path)
            self._check_identity(notes, identity, ('plan_id', 'data_version_id', 'baseline_run_id'))
            supporting_sources=[dict(endpoint=path,response_sha256=_digest(raw),owner='EnterprisePlans.scenario_review'),
                                dict(endpoint=notes_path,response_sha256=_digest(notes),owner='EnterprisePlans.review_notes')]
            raw = {**raw, 'review_notes': notes}
        total=len(rows)
        rows = self._filter_rows(rows, view, filters)
        component = raw.get('resource_analysis' if view == 'resources' else 'inventory_analysis', {}) if view in {'resources','inventory'} else {}
        component_status = component.get('status')
        status = component_status if component_status in {'NOT_COLLECTED','UNSUPPORTED','UNREADABLE','INVALID_EVIDENCE'} else 'NOT_COLLECTED' if not present else 'PRESENT' if rows else 'EMPTY_CHECKED'
        token_scope = _digest(dict(identity=identity,view=view,filters=filters,page_size=model.page_size))
        fingerprint = _digest(dict(primary=raw,latest_optimization=latest_optimization)) if view=='schedule' and not filters.get('search_id') else _digest(raw)
        offset = self._cursor(model.cursor, token_scope, fingerprint)
        if offset > len(rows): raise BusinessToolError('CURSOR_INVALID')
        page_rows = []
        for row in rows[offset:offset + model.page_size]:
            compact = self._compact(row['record'], path, row['pointer'])
            original_record=self._source_record(raw,row['pointer'])
            rendered = dict(kind=row['kind'],record=compact,source=dict(endpoint=path,pointer=row['pointer'],sha256=_digest(original_record)),
                            projection=dict(display_sha256=_digest(compact),kind='EXACT_OWNER_RECORD' if original_record==row['record'] else 'OWNER_RECORD_WITH_CONTEXT_PROJECTION'),
                            **({key:row[key] for key in ('resolved_dimensions','owner_links') if key in row}))
            if len(_encoded(page_rows+[rendered]).encode()) > 38000:
                if not page_rows: raise BusinessToolError('EVIDENCE_RECORD_TOO_LARGE')
                break
            page_rows.append(rendered)
        end = offset + len(page_rows)
        cursor = self._token(token_scope,fingerprint,end) if end < len(rows) else None
        summary_keys = ('timezone','window','planning_at','horizon_end','endpoint','limits','netting_templates','review_notes','validation','available_drafts')
        result = dict(rows=page_rows, summary={k:self._compact(raw[k],path,'/'+k) for k in summary_keys if k in raw})
        if view=='scenario_review' and supporting_sources:
            result['summary']['review_notes']=self._compact(notes,notes_path)
        if view=='schedule' and not filters.get('search_id'):
            result['summary']['latest_optimization']=self._compact(latest_optimization,latest_path,'/job')
        if view=='plan_conditions':
            result['summary']['supported_operations']=self._compact(raw.get('supported_operations',{}),path,'/supported_operations')
        if view=='forecast':
            result['summary']['supported_forecast_models']=['Naive','SeasonalNaive','AutoETS','CrostonSBA','TSB']
            result['summary']['forecast_context']=self._compact({k:v for k,v in raw.get('context',{}).items() if k in {'original_policy_status','original_gross','draft','outcome'}},path,'/context')
        if view=='inventory':
            result['summary']['inventory_risk']=self._compact(component.get('risk',{}),path,'/inventory_analysis/risk')
        if view=='schedule' and filters.get('search_id'):
            result['summary']['optimization']={k:raw[k] for k in ('search_id','state','algorithm','candidate_status','fingerprint') if k in raw}
            result['summary']['optimization']['edit']={k:v for k,v in (raw.get('edit') or {}).items() if k!='assignments'}
            result['summary']['optimization']['assignment_count']=len((raw.get('edit') or {}).get('assignments',[]))
        envelope = self._envelope(identity,path,raw,status=status,checked=dict(view=view,filters=filters,
            relation_scope='EXACT_OWNER_REFERENCES_ONLY;NO_INFERRED_ALLOCATION_OR_UNIQUE_CAUSE',
            omitted='Other views and records outside this page; original evidence remains with its Owner',
            transport_projection='BOUNDED_MCP_RESPONSE;ORIGINAL_HTTP_OWNER_MAY_RETURN_FULL_PROJECTION'),result=result)
        if view=='inventory':
            envelope['checked_scope']['event_sequence_scope']='COMPLETE_AUTHENTICATED_BUFFER_SEQUENCE_FOR_RANGE_PREDECESSOR;RISK_SUMMARY_USES_REQUESTED_WINDOW'
        envelope['source']['owner']={'demand_risks':'EnterprisePlans.exceptions','resources':'EnterprisePlans.run_analysis',
            'inventory':'EnterprisePlans.run_analysis','purchases':'EnterprisePlans.professional_policy_view',
            'forecast':'IntelligenceOwner.overview','inventory_research':'IntelligenceOwner.overview',
            'promising':'IntelligenceOwner.overview','schedule':'optimization.service.read' if filters.get('search_id') else 'EnterprisePlans.plan_editor_options',
            'scenario_review':'EnterprisePlans.scenario_review','plan_conditions':'PlanningService.plan_context'}[view]
        if supporting_sources:
            envelope['source']['response_sha256']=supporting_sources[0]['response_sha256']
            envelope['source']['supporting_sources']=supporting_sources
        if component:
            envelope['evidence']['component_validation'] = self._compact(component.get('validation', {}), path)
        envelope['page'] = dict(returned=len(page_rows),matched=len(rows),total=total,offset=offset,next_cursor=cursor,truncated=cursor is not None)
        if len(_encoded(envelope).encode()) > self.MAX_RESPONSE_BYTES:
            raise BusinessToolError('EVIDENCE_RESPONSE_BUDGET_EXCEEDED')
        return envelope

    @staticmethod
    def _filter_rows(rows, view, filters):
        selected=[]
        aliases={'order_line_id':('demand_id','id'),'resource_id':('resource_id','id'),
                 'reference':('reference','purchase_reference','po_id','receipt_reference','id'),'inventory_id':('id',),'quote_id':('id',)}
        ignored={'start','end','version_id','row_id','dataset_id','draft_ids','search_id'}
        for row in rows:
            record=row['record']; nested=record.get('request',{}) if view=='promising' else {}
            def matches(key,value):
                if key in ignored:return True
                keys=aliases.get(key,(key,))
                return any(record.get(k,nested.get(k,row.get('resolved_dimensions',{}).get(k)))==value for k in keys)
            if not all(matches(k,v) for k,v in filters.items()):continue
            if view=='demand_risks':
                due=record.get('due_at')
                if (filters.get('start') or filters.get('end')) and not due:continue
                if filters.get('start') and datetime.fromisoformat(due.replace('Z','+00:00'))<datetime.fromisoformat(filters['start'].replace('Z','+00:00')):continue
                if filters.get('end') and datetime.fromisoformat(due.replace('Z','+00:00'))>=datetime.fromisoformat(filters['end'].replace('Z','+00:00')):continue
            selected.append(row)
        return selected

    @staticmethod
    def _token(scope, fingerprint, offset):
        return base64.urlsafe_b64encode(_encoded(dict(scope=scope,fingerprint=fingerprint,offset=offset)).encode()).decode()

    @staticmethod
    def _cursor(cursor, scope, fingerprint):
        if cursor is None:return 0
        try:
            value=json.loads(base64.b64decode(cursor,altchars=b'-_',validate=True))
            if set(value)!={'scope','fingerprint','offset'} or type(value['offset']) is not int or value['offset']<0:raise ValueError()
        except (ValueError,TypeError,KeyError):raise BusinessToolError('CURSOR_INVALID') from None
        if value['scope']!=scope:raise BusinessToolError('CURSOR_SCOPE_MISMATCH')
        if value['fingerprint']!=fingerprint:raise BusinessToolError('CURSOR_EVIDENCE_CHANGED')
        return value['offset']


def register_assistant_tools(mcp, api, tool):
    @tool(True)
    def read_source_information(plan_id:str,data_version_id:str,section:Literal['resources','calendars','manufacturing','qualifications','policies','datasets','forecast_versions','inventory_candidates'],page_size:Annotated[int,Field(ge=1,le=50)]=10,offset:Annotated[int,Field(ge=0,le=100000)]=0,record_id:str|None=None)->dict:
        """Read original plan-input or independent research without native solving. Research lists return metadata; supply record_id for paged forecast periods, inventory candidates or the SAME display history tail as the page (52 rows per dimension, explicitly not complete training history). Same source Owners, READ only. No allocations, promises, edits or task approval. Unknown native load stays unknown."""
        from p3_mcp_tools import P3ToolError
        if not re.fullmatch(r'PLAN-[a-f0-9]{16}',plan_id) or not re.fullmatch(r'DATA-[A-Za-z0-9_-]{1,100}',data_version_id):raise P3ToolError('INVALID_BUSINESS_SCOPE')
        research=section in {'datasets','forecast_versions','inventory_candidates'}
        value=api.request('GET','/v1/enterprise-plans/'+plan_id+('/intelligence/source' if research else '/source-workspace')+'?'+urlencode(dict(data_version_id=data_version_id)))
        if value.get('identity',{}).get('plan_id')!=plan_id or value['identity'].get('data_version_id')!=data_version_id or value['identity'].get('run_id') is not None:raise P3ToolError('TOOL_SCOPE_MISMATCH')
        field={'manufacturing':'existing_orders','qualifications':'resource_skills','policies':'inventory_buffer_policy'}.get(section,section)
        rows=value.get(section,[]) if research else value.get('input',{}).get(field,[])
        if section=='policies':rows=value.get('inventory_targets',[])
        if section=='manufacturing':rows=[r for r in rows if r.get('type')=='MO']
        if isinstance(rows,dict):rows=rows.get('rows',[])
        record=None;coverage=None
        if research:
            def metadata(r):
                fields={'id','sha256','classification','status','effective_status','model','dataset_id','dataset_sha256','training_cutoff','issue_time','source','source_id','reason','observation_kind','freq','series','channel_rows','planning_scope_applied'}
                out={k:r[k] for k in fields if k in r}
                if 'rows' in r:out['row_count']=len(r['rows']) if isinstance(r['rows'],list) else r['rows']
                return out
            if record_id is not None:
                selected=next((r for r in rows if r['id']==record_id),None)
                if selected is None:raise P3ToolError('SOURCE_RESEARCH_RECORD_NOT_FOUND')
                record=metadata(selected)
                rows=selected.get('history_rows',[]) if section=='datasets' else selected.get('rows',[]) if section=='forecast_versions' else selected.get('result',{}).get('candidates',[])
                coverage='DISPLAY_TAIL_52_PER_DIMENSION' if section=='datasets' else 'FULL_RECORD_ROWS'
            else:rows=[metadata(r) for r in rows]
        elif record_id is not None:raise P3ToolError('SOURCE_RESEARCH_RECORD_SCOPE_REQUIRED')
        result=dict(identity=value['identity'],basis='SOURCE_RESEARCH' if research else value['basis'],section=section,total=len(rows),rows=rows[offset:offset+page_size],next_offset=offset+page_size if offset+page_size<len(rows) else None)
        if record is not None:result.update(record=record,coverage=coverage)
        if len(_encoded(result).encode())>64000:raise P3ToolError('TASK_RESPONSE_BUDGET_EXCEEDED')
        return result

    @tool(True)
    def read_business_facts(plan_id:str,data_version_id:str,start:str|None=None,end:str|None=None,item_id:str|None=None,location_id:str|None=None,section:Literal['orders','purchases','inventory','resources','forecasts','manufacturing']='orders',kind:Literal['FIRM_ORDER','NET_FORECAST','UNKNOWN']='FIRM_ORDER',status:Literal['open','overdue']|None=None,page_size:Annotated[int,Field(ge=1,le=100)]=10,offset:Annotated[int,Field(ge=0,le=100000)]=0)->dict:
        """Read confirmed source orders, actual shipped balances, purchase and inventory facts from the same Owner as Home. No successful run, task, or solver is required. Dates filter order due dates; inventory remains version-as-of. Unknown stays unknown. Use bounded pages and exact dimensions. Does not certify native coverage or supply promises."""
        import re
        from p3_mcp_tools import P3ToolError
        if not re.fullmatch(r'PLAN-[a-f0-9]{16}',plan_id) or not re.fullmatch(r'DATA-[A-Za-z0-9_-]{1,100}',data_version_id):raise P3ToolError('INVALID_BUSINESS_SCOPE')
        query=dict(data_version_id=data_version_id,start=start,end=end,item_id=item_id,location_id=location_id,section=section,kind=kind,status=status,page_size=page_size,offset=offset)
        path='/v1/enterprise-plans/'+plan_id+'/business-view?'+urlencode({k:v for k,v in query.items() if v is not None})
        value=api.request('GET',path)
        if value.get('identity',{}).get('plan_id')!=plan_id or value['identity'].get('data_version_id')!=data_version_id or value['identity'].get('run_id') is not None:raise P3ToolError('TOOL_SCOPE_MISMATCH')
        if len(_encoded(value).encode())>64000:raise P3ToolError('TASK_RESPONSE_BUDGET_EXCEEDED')
        return value

    @tool(True)
    def query_business_history(plan_id:str,data_version_id:str,domain:Literal['demand','inventory','plans'],start:str,end:str,grain:Literal['day','week','month']='month',perspective:Literal['current','source','aps']='current',known_at:str|None=None,source_system:Literal['ERP','MES','WMS']|None=None,knowledge_version_id:str|None=None,item_id:str|None=None,location_id:str|None=None,customer_id:str|None=None,lot_id:str|None=None,order_id:str|None=None,cohort_status:str|None=None,bucket_id:str|None=None,metric:str|None=None,historical_plan_id:str|None=None,run_id:str|None=None,compare_plan_id:str|None=None,compare_run_id:str|None=None,actual_demand_id:str|None=None,section:Literal['deliveries','resources','operations','audit']='deliveries',bucket_page_size:Annotated[int,Field(ge=1,le=62)]=12,bucket_offset:Annotated[int,Field(ge=0,le=100000)]=0,page_size:Annotated[int,Field(ge=1,le=100)]=20,offset:Annotated[int,Field(ge=0,le=100000)]=0)->dict:
        """Query source-backed demand/inventory history or truly saved Plan/run history. Dates are timezone-aware [start,end); Monday weeks, natural months. An inclusive source knowledge instant is not an exclusive period end: use query_coverage.calendar_end (context.query_end_by_grain) to include the final known instant. The final period may be PARTIAL_KNOWLEDGE and is observed only through observed_through; incomplete ranges have no complete totals and unknown future is never zero. Flows sum; closing balances do not. current means currently observed actuals; source requires known_at; APS requires an actually confirmed knowledge_version_id. No Worker. Plans from different business states are not algorithm gains. Time columns page independently (bucket_page.next_offset); available full-range totals remain full-range. Use exact object filters, bounded drills and pagination; missing evidence is never zero. Run identities are accepted only for domain=plans. Order trace metric requires order_id. Inventory balances reject order/customer filters."""
        import re
        from p3_mcp_tools import P3ToolError
        if not re.fullmatch(r'PLAN-[a-f0-9]{16}',plan_id) or not re.fullmatch(r'DATA-[A-Za-z0-9_-]{1,100}',data_version_id):raise P3ToolError('INVALID_BUSINESS_SCOPE')
        if domain=='plans' and (perspective!='current' or any(v is not None for v in (known_at,source_system,knowledge_version_id,item_id,location_id,customer_id,lot_id,order_id,cohort_status,bucket_id,metric))):raise P3ToolError('HISTORY_PLAN_QUERY_FIELDS_INVALID')
        q=dict(data_version_id=data_version_id,domain=domain,start=start,end=end,grain=grain,page_size=page_size,offset=offset)
        if domain!='plans':q.update(bucket_page_size=bucket_page_size,bucket_offset=bucket_offset,perspective=perspective,known_at=known_at,source_system=source_system,knowledge_version_id=knowledge_version_id,item_id=item_id,location_id=location_id,customer_id=customer_id,lot_id=lot_id,order_id=order_id,cohort_status=cohort_status,bucket_id=bucket_id,metric=metric)
        else:q.update(historical_plan_id=historical_plan_id,run_id=run_id,compare_plan_id=compare_plan_id,compare_run_id=compare_run_id,actual_demand_id=actual_demand_id,section=section)
        if domain!='plans' and any(x is not None for x in (historical_plan_id,run_id,compare_plan_id,compare_run_id,actual_demand_id)):raise P3ToolError('HISTORY_FACT_QUERY_RUN_FORBIDDEN')
        value=api.request('GET','/v1/enterprise-plans/'+plan_id+'/history?'+urlencode({k:v for k,v in q.items() if v is not None}))
        if value.get('identity',{}).get('plan_id')!=plan_id or value['identity'].get('data_version_id')!=data_version_id:raise P3ToolError('TOOL_SCOPE_MISMATCH')
        if len(_encoded(value).encode())>64000:raise P3ToolError('TASK_RESPONSE_BUDGET_EXCEEDED')
        return value

    """Professional mutations use task gates; confirmed fact reads need no run."""
    from p3_mcp_tools import P3ToolError
    boundary=BusinessTools(api.base, transport=api.request)
    def request(task_id,name,arguments,request_id):
        try:return boundary.request_task_tool(task_id,name,_plain(arguments,unset=True),request_id)
        except BusinessToolError as error:raise P3ToolError(str(error)) from None

    @tool(False)
    def open_professional_task(plan_id:str,data_version_id:str,baseline_run_id:str,run_id:str,target:str,original_request:Annotated[str,Field(min_length=1,max_length=8000)],session_id:Key,request_id:Key)->dict:
        """Open external Codex professional task metadata for one exact plan/version/baseline/run/target and original business request. Does not call a model or approve any action. Use task-bound professional tools; user confirms persistent/expensive actions in the existing workbench. Reuse request_id to recover."""
        try:
            identity=_plain(Identity.model_validate(dict(plan_id=plan_id,data_version_id=data_version_id,baseline_run_id=baseline_run_id,run_id=run_id,target=target)))
            boundary._key(request_id)
            boundary._key(session_id)
            if not original_request.strip() or len(original_request)>8000:raise BusinessToolError('TASK_ARGUMENTS_INVALID')
            boundary._bind(identity)
            body=dict(request_id=request_id,session_id=session_id,message=original_request,target=target,
                      expected_run_id=run_id,data_version_id=data_version_id,mode='external')
            value=api.request('POST',boundary._query('/v1/agent-tasks',dict(plan_id=plan_id)),body)
            boundary._check_identity(value,identity,IDENTITY_FIELDS)
            endpoint=boundary._query('/v1/agent-tasks/'+quote(value.get('id',''),safe=''),dict(plan_id=plan_id))
            return _task_summary_for_model(value,endpoint)
        except (BusinessToolError,ValidationError) as error:raise P3ToolError('TASK_ARGUMENTS_INVALID' if isinstance(error,ValidationError) else str(error)) from None

    @tool(True)
    def read_professional_task(plan_id:str,data_version_id:str,baseline_run_id:str,run_id:str,target:str,task_id:str,call_offset:Annotated[int,Field(ge=0,le=80)]=0,page_size:Annotated[int,Field(ge=1,le=20)]=10,call_id:Annotated[str,Field(pattern='^CALL-[a-f0-9]{16}$')]|None=None,expected_revision:Annotated[int,Field(ge=1)]|None=None)->dict:
        """Recover one exact professional task, pending confirmation and approved tool results without repeating writes or launching a model. This tool cannot approve, fabricate a user nonce or resume an unknown write automatically."""
        try:
            identity=_plain(Identity.model_validate(dict(plan_id=plan_id,data_version_id=data_version_id,baseline_run_id=baseline_run_id,run_id=run_id,target=target)))
            path='/v1/agent-tasks/'+quote(task_id,safe='')
            if call_id:path+='/calls/'+quote(call_id,safe='')
            query=dict(plan_id=plan_id) if call_id else dict(plan_id=plan_id,call_offset=call_offset,page_size=page_size)
            if not call_id and expected_revision is not None:query['expected_revision']=expected_revision
            endpoint=boundary._query(path,query)
            value=api.request('GET',endpoint)
            boundary._check_identity(value,identity,IDENTITY_FIELDS)
            if call_id:
                if value.get('task_id')!=task_id or value.get('call',{}).get('call_id')!=call_id:
                    raise BusinessToolError('TASK_SCOPE_MISMATCH')
                result=BusinessTools._compact(_model_task_projection(value),endpoint)
                result['source']=dict(owner='SessionStore',endpoint=endpoint)
                result['workbench_task_url']=_task_url(identity,task_id)
                if len(_encoded(result).encode())>64000:raise BusinessToolError('TASK_RESPONSE_BUDGET_EXCEEDED')
                return result
            return _task_summary_for_model(value,endpoint,call_offset,page_size)
        except (BusinessToolError,ValidationError) as error:raise P3ToolError('TASK_ARGUMENTS_INVALID' if isinstance(error,ValidationError) else str(error)) from None

    # Build real typed Python signatures for FastMCP, retaining the public tool
    # names and schemas. There is no client-controlled route or action_kind.
    import inspect
    for name,model in BusinessTools.MODELS.items():
        def invoke(task_id,arguments,request_id,_name=name):
            return request(task_id,_name,arguments,request_id)
        invoke.__name__=name
        invoke.__doc__=DESCRIPTIONS[name]
        invoke.__signature__=inspect.Signature([
            inspect.Parameter('task_id',inspect.Parameter.POSITIONAL_OR_KEYWORD,annotation=str),
            inspect.Parameter('arguments',inspect.Parameter.POSITIONAL_OR_KEYWORD,annotation=model),
            inspect.Parameter('request_id',inspect.Parameter.POSITIONAL_OR_KEYWORD,annotation=Key)],return_annotation=dict)
        spec=BusinessTools.tool_spec(name)
        # Session metadata writes do not change business facts and need no
        # business consent, but MCP's readOnlyHint must still describe persistence.
        tool(spec['readOnly'] and name not in {'record_professional_intent','finish_professional_task'})(invoke)
