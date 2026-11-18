"""One exact confirmed forecast bucket's hypothetical gross in an isolated draft.

Consumption and Net are server-derived with the existing deterministic policy.
The source forecast, Firm orders and supply facts remain in the confirmed input.
"""
import copy
import re
from decimal import Decimal

from demand_forecast import DIMENSIONS, PROVENANCE_FIELDS, apply_net_forecasts, net_forecasts, validate
from planning_capabilities import profile_id, supports

ACTION = 'set_forecast_gross_override'
INTENT = '预测毛需求覆盖模拟'
PROFILES = {'forecast-net-demand-v1', 'enterprise-forecast-fg-v1'}
QUANTITY_PATTERN = r'^(0|[1-9][0-9]{0,11})(\.[0-9]{1,8})?$'
IDENTITY_FIELDS = ('forecast_id', 'forecast_version_id', *DIMENSIONS, 'bucket_start', 'bucket_end')
BUCKET_SCHEMA = {'type': 'object', 'properties': {
    **{key: {'type': 'string', 'minLength': 1, 'maxLength': 1000} for key in IDENTITY_FIELDS},
    'provenance': {'type': 'object', 'properties': {
        key: {'type': 'string', 'minLength': 1} for key in PROVENANCE_FIELDS},
        'required': list(PROVENANCE_FIELDS), 'additionalProperties': False}},
    'required': [*IDENTITY_FIELDS, 'provenance'], 'additionalProperties': False}
CHANGE_SCHEMA = {'type': 'object', 'properties': {'bucket': BUCKET_SCHEMA,
    'gross_quantity': {'type': 'string', 'pattern': QUANTITY_PATTERN},
    'reason': {'type': 'string', 'minLength': 1, 'maxLength': 1000}},
    'required': ['bucket', 'gross_quantity', 'reason'], 'additionalProperties': False}


def bucket_identity(forecast):
    return dict(forecast_id=forecast['id'], forecast_version_id=forecast['version_id'],
        **{key: copy.deepcopy(forecast[key]) for key in (*DIMENSIONS, 'bucket_start', 'bucket_end', 'provenance')})


def apply(data, bucket, gross_quantity, reason):
    from drafts import DraftError

    profile = profile_id(data)
    if profile not in PROFILES or not supports(profile, 'actions', 'forecast_override'):
        raise DraftError('UNSUPPORTED_PROFILE_CAPABILITY: 当前模型未认证预测毛需求覆盖')
    if not isinstance(gross_quantity, str) or not re.fullmatch(QUANTITY_PATTERN, gross_quantity):
        raise DraftError('FORECAST_GROSS: Gross 必须为最多12位整数及8位小数的非负十进制字符串')
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
        raise DraftError('FORECAST_REASON: 需要1至1000字的假设理由')
    matches = [f for f in data.get('forecasts', []) if isinstance(bucket, dict) and f.get('id') == bucket.get('forecast_id')]
    if len(matches) != 1 or bucket_identity(matches[0]) != bucket:
        raise DraftError('FORECAST_BUCKET_MISMATCH: 需要当前确认预测桶的精确维度、版本、期间与来源')
    # A malformed or closed Firm line cannot be silently repaired by this action.
    validate(data)
    forecast = matches[0]
    if Decimal(forecast['gross_quantity']) == Decimal(gross_quantity):
        raise DraftError('NO_FORECAST_CHANGE: 新 Gross 与原值相同')
    before = next(row for row in net_forecasts(data)['rows'] if row['forecast_id'] == forecast['id'])
    forecast['gross_quantity'] = format(Decimal(gross_quantity).normalize(), 'f')
    source = copy.deepcopy(data)
    source['demand_lines'] = [row for row in source['demand_lines'] if row.get('kind') != 'NET_FORECAST']
    generated = [row for row in apply_net_forecasts(source)['demand_lines']
                 if row.get('kind') == 'NET_FORECAST' and row.get('forecast_id') == forecast['id']]
    # Preserve every unrelated row byte-for-byte at the canonical object level.
    rows, replaced = [], False
    for row in data['demand_lines']:
        if row.get('kind') == 'NET_FORECAST' and row.get('forecast_id') == forecast['id']:
            rows.extend(generated); replaced = True
        else:
            rows.append(row)
    if not replaced:
        rows.extend(generated)
    data['demand_lines'] = rows
    validate(data)
    after = next(row for row in net_forecasts(data)['rows'] if row['forecast_id'] == forecast['id'])
    return dict(action=ACTION, intent=INTENT, bucket=copy.deepcopy(bucket), reason=reason.strip(),
        before=before, after=after, gross_quantity=forecast['gross_quantity'],
        calculation_basis='DETERMINISTIC_SAME_DIMENSION_HALF_OPEN_RULE', engine_executed=False,
        simulation_only=True, business_permission='NOT_CONFIRMED', execution_authorized=False)


def verify(baseline, draft):
    """Rebuild a declared decision before the existing Worker accepts the input."""
    from drafts import DraftError
    from firm_scenario import ACTION as FIRM_ACTION, verify as verify_firm
    if draft.get('changes',{}).get('action') == FIRM_ACTION:
        verify_firm(baseline,draft)
        return

    changes = draft.get('changes', {})
    marked = isinstance(changes, dict) and (changes.get('action') == ACTION or changes.get('intent') == INTENT)
    actual = draft.get('input', {})
    nets = lambda d: [row for row in d.get('demand_lines', []) if isinstance(row, dict) and row.get('kind') == 'NET_FORECAST']
    changed = baseline.get('forecasts') != actual.get('forecasts') or nets(baseline) != nets(actual)
    if not marked and not changed:
        return
    try:
        expected = copy.deepcopy(baseline)
        recreated = apply(expected, changes['bucket'], changes['gross_quantity'], changes['reason'])
        expected.update(scenario_id=draft['id'], scenario_revision=1)
        if recreated != changes or expected != actual:
            raise ValueError('scope differs')
    except (KeyError, TypeError, ValueError) as error:
        raise DraftError('FORECAST_OVERRIDE_SCOPE_MISMATCH: 草案只能覆盖指定来源桶的 Gross 与服务器生成的 Net') from error
