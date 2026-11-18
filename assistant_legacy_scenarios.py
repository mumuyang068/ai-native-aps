"""Thin typed adapters for existing P22 research scenario Owners.

The module performs no HTTP, persistence, forecast netting, inventory decision,
authorization or solve. TaskGateway owns user consent. Each returned request is
validated with the original P22 API model and dispatched by the existing caller.
"""
import copy
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from p22.api import (ForecastBucket, ForecastOverridePreview, ForecastOverrideDraft,
                     FoundationForecastPreview, FoundationForecastDraft,
                     InventoryBufferPolicy, InventoryDecisionPreview, InventoryDecisionDraft,
                     DemandPriorityPreview, DemandPriorityDraft)
from p22.foundation_projection import digest


IDENTITY_FIELDS = ('plan_id', 'data_version_id', 'baseline_run_id', 'run_id', 'target')
JOINT_FIELDS = ('netting', 'inquiry', 'replenishment', 'supply')


class LegacyScenarioError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class _Change(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class LegacyForecastOverride(_Change):
    kind: Literal['legacy_forecast_override']
    bucket: ForecastBucket
    gross_quantity: str = copy.deepcopy(ForecastOverridePreview.model_fields['gross_quantity'])


class FoundationForecast(_Change):
    kind: Literal['foundation_forecast']
    bucket: dict[str, str] = copy.deepcopy(FoundationForecastPreview.model_fields['bucket'])
    gross_quantity: str = copy.deepcopy(FoundationForecastPreview.model_fields['gross_quantity'])


class InventoryDecision(_Change):
    kind: Literal['inventory_decision']
    policy: InventoryBufferPolicy | None = None
    source_option_id: str | None = copy.deepcopy(InventoryDecisionPreview.model_fields['source_option_id'])

class DemandPriority(_Change):
    kind: Literal['demand_priority']
    demand_id: str = copy.deepcopy(DemandPriorityPreview.model_fields['demand_id'])
    priority: int = copy.deepcopy(DemandPriorityPreview.model_fields['priority'])


DomainChange = Annotated[LegacyForecastOverride | FoundationForecast | InventoryDecision | DemandPriority,
                         Field(discriminator='kind')]
CHANGE_ADAPTER = TypeAdapter(DomainChange)
ROUTES = {
    'legacy_forecast_override': ('forecast-override', ForecastOverridePreview, ForecastOverrideDraft),
    'foundation_forecast': ('foundation-forecast', FoundationForecastPreview, FoundationForecastDraft),
    'inventory_decision': ('inventory-decision', InventoryDecisionPreview, InventoryDecisionDraft),
    'demand_priority': ('demand-priority', DemandPriorityPreview, DemandPriorityDraft),
}


def parse_change(value):
    if isinstance(value, BaseModel):
        value = value.model_dump(mode='json')
    return CHANGE_ADAPTER.validate_python(value)


def validate_exclusive_domain(arguments):
    """Reusable by the outer model without accepting a second policy branch."""
    if arguments.get('domain_change') is None:
        return None
    if any(arguments.get(key) is not None for key in JOINT_FIELDS):
        raise LegacyScenarioError('LEGACY_DOMAIN_EXCLUSIVE')
    return parse_change(arguments['domain_change'])


def _scope(arguments):
    try:
        identity = {key: arguments[key] for key in IDENTITY_FIELDS}
    except (TypeError, KeyError):
        raise LegacyScenarioError('LEGACY_IDENTITY_REQUIRED') from None
    if (not isinstance(identity['plan_id'], str)
            or not re.fullmatch(r'PLAN-[a-f0-9]{16}', identity['plan_id'])
            or not isinstance(identity['data_version_id'], str)
            or not 1 <= len(identity['data_version_id']) <= 100
            or any(not isinstance(identity[key], str)
                   or not re.fullmatch(r'API-[a-f0-9]{16}', identity[key])
                   for key in ('baseline_run_id', 'run_id'))
            or not isinstance(identity['target'], str)
            or not re.fullmatch(r'(baseline|DRAFT-[a-f0-9]{16})', identity['target'])):
        raise LegacyScenarioError('LEGACY_IDENTITY_REQUIRED')
    return identity


def _arguments(arguments):
    if isinstance(arguments, BaseModel):
        arguments = arguments.model_dump(mode='json')
    allowed = set(IDENTITY_FIELDS) | set(JOINT_FIELDS) | {
        'domain_change', 'reason', 'fingerprint', 'source_sha256', 'acknowledge_limits'}
    if not isinstance(arguments, dict) or set(arguments) - allowed:
        raise LegacyScenarioError('LEGACY_ARGUMENTS_SHAPE')
    change = validate_exclusive_domain(arguments)
    if change is None:
        raise LegacyScenarioError('LEGACY_DOMAIN_REQUIRED')
    return arguments, _scope(arguments), change


def _path(identity, change, suffix):
    return '/v1/enterprise-plans/' + identity['plan_id'] + '/' + ROUTES[change.kind][0] + suffix


def preview_path(plan_id, domain_change):
    if not isinstance(plan_id, str) or not re.fullmatch(r'PLAN-[a-f0-9]{16}', plan_id):
        raise LegacyScenarioError('LEGACY_IDENTITY_REQUIRED')
    return _path({'plan_id': plan_id}, parse_change(domain_change), '-preview')


def create_path(plan_id, domain_change):
    if not isinstance(plan_id, str) or not re.fullmatch(r'PLAN-[a-f0-9]{16}', plan_id):
        raise LegacyScenarioError('LEGACY_IDENTITY_REQUIRED')
    return _path({'plan_id': plan_id}, parse_change(domain_change), '-drafts')


def preview_payload(arguments):
    values, identity, change = _arguments(arguments)
    payload = {key: identity[key] for key in IDENTITY_FIELDS if key != 'plan_id'}
    payload.update(change.model_dump(mode='json', exclude={'kind'}), reason=values.get('reason'))
    return ROUTES[change.kind][1].model_validate(payload).model_dump(mode='json')


def preview_request(arguments):
    values, identity, change = _arguments(arguments)
    return _path(identity, change, '-preview'), preview_payload(values)


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value) is not None


def preview_envelope(arguments, owner_response):
    """Keep the entire original Owner response and expose its exact binding.

    Legacy forecast override has no Owner fingerprint. Its adapter fingerprint
    binds the unchanged full preview solely for TaskGateway's stale-read check;
    it is never presented to the original Owner as an invented confirmation.
    """
    values, identity, change = _arguments(arguments)
    raw = owner_response.get('owner_response', owner_response) if isinstance(owner_response, dict) else None
    if not isinstance(raw, dict) or not isinstance(raw.get('identity'), dict):
        raise LegacyScenarioError('LEGACY_OWNER_RESPONSE')
    if any(raw['identity'].get(key) != identity[key] for key in IDENTITY_FIELDS):
        raise LegacyScenarioError('LEGACY_OWNER_IDENTITY_MISMATCH')
    if raw.get('engine_executed') is True or raw.get('calculation_submitted') is True:
        raise LegacyScenarioError('LEGACY_PREVIEW_EXECUTED')
    if change.kind == 'foundation_forecast':
        confirmation = raw.get('confirmation')
        if (not isinstance(confirmation, dict) or set(confirmation) != {'sha256'}
                or not _sha(confirmation['sha256'])):
            raise LegacyScenarioError('LEGACY_OWNER_CONFIRMATION_REQUIRED')
        fingerprint = confirmation['sha256']
        binding = 'ORIGINAL_FOUNDATION_FORECAST_CONFIRMATION_SHA256'
    elif change.kind == 'inventory_decision':
        fingerprint = raw.get('fingerprint')
        if not _sha(fingerprint) or not _sha(raw['identity'].get('source_sha256')):
            raise LegacyScenarioError('LEGACY_OWNER_FINGERPRINT_REQUIRED')
        binding = 'ORIGINAL_INVENTORY_DECISION_FINGERPRINT'
    else:
        fingerprint = digest(raw)
        binding = 'ADAPTER_FULL_OWNER_PREVIEW_STALENESS_ONLY'
    result = dict(kind=change.kind, identity=copy.deepcopy(raw['identity']),
                  domain_change=change.model_dump(mode='json'), fingerprint=fingerprint,
                  fingerprint_binding=binding, owner_response=copy.deepcopy(raw),
                  owner='EnterprisePlans.' + {'legacy_forecast_override': 'preview_forecast_override',
                      'foundation_forecast': 'preview_foundation_forecast',
                      'inventory_decision': 'preview_inventory_decision',
                      'demand_priority': 'preview_demand_priority'}[change.kind])
    if change.kind == 'inventory_decision':
        result['source_sha256'] = raw['identity']['source_sha256']
    return result


def create_payload(arguments, request_id, fresh_preview):
    values, identity, change = _arguments(arguments)
    current = preview_envelope(values, fresh_preview)
    if values.get('acknowledge_limits') is not True:
        raise LegacyScenarioError('LEGACY_ACK_REQUIRED')
    if not _sha(values.get('fingerprint')) or values['fingerprint'] != current['fingerprint']:
        raise LegacyScenarioError('LEGACY_PREVIEW_CHANGED')
    payload = preview_payload(values)
    payload['request_id'] = request_id
    if change.kind == 'demand_priority':
        # The existing priority draft Owner accepts only its original arguments;
        # the Task gateway binds this complete preview before dispatching it.
        for key in ('run_id','target','reason'):payload.pop(key)
    if change.kind == 'foundation_forecast':
        payload['confirmation'] = copy.deepcopy(current['owner_response']['confirmation'])
    elif change.kind == 'inventory_decision':
        if values.get('source_sha256') != current['source_sha256']:
            raise LegacyScenarioError('LEGACY_SOURCE_SHA_MISMATCH')
        payload.update(source_sha256=current['source_sha256'], fingerprint=current['fingerprint'],
                       acknowledge_limits=True)
    return ROUTES[change.kind][2].model_validate(payload).model_dump(mode='json')


def create_request(arguments, request_id, fresh_preview):
    values, identity, change = _arguments(arguments)
    return _path(identity, change, '-drafts'), create_payload(values, request_id, fresh_preview)


def read_request(identity, kind, filters=None):
    """Select an existing finite professional view, without rebuilding it here.

    The caller retains the Owner response and applies its existing bounded
    evidence projection. MOQ/source options and bucket provenance must not be
    relabeled or recomputed by this transport adapter.
    """
    identity = _scope(identity)
    if kind not in {'legacy_forecast_override','foundation_forecast','inventory_decision'}:
        raise LegacyScenarioError('LEGACY_DOMAIN_UNSUPPORTED')
    filters = {} if filters is None else filters
    if not isinstance(filters, dict):
        raise LegacyScenarioError('LEGACY_READ_FILTERS')
    if kind == 'inventory_decision':
        if (set(filters) != {'item_id', 'location_id'}
                or any(not isinstance(value, str) or not 1 <= len(value) <= 200
                       for value in filters.values())):
            raise LegacyScenarioError('LEGACY_READ_FILTERS')
        tail = 'inventory-decision'
        query = {key: identity[key] for key in ('data_version_id', 'run_id', 'target')} | filters
    elif kind == 'foundation_forecast':
        if filters:
            raise LegacyScenarioError('LEGACY_READ_FILTERS')
        tail = 'foundation-forecast'
        query = {key: identity[key] for key in IDENTITY_FIELDS if key != 'plan_id'}
    else:
        if filters:
            raise LegacyScenarioError('LEGACY_READ_FILTERS')
        tail = 'demand-planning'
        query = {key: identity[key] for key in ('data_version_id', 'run_id', 'target')}
    return '/v1/enterprise-plans/' + identity['plan_id'] + '/' + tail, query
