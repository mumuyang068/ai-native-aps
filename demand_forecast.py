"""Source-traceable, deterministic same-dimension forecast order consumption.

This is a bounded product conversion, not the native frePPLe Forecast engine or
a statistical prediction model. All quantities remain exact decimal strings.
"""
import copy
import re
from datetime import datetime
from decimal import Decimal

PROFILE = 'forecast-net-demand-v1'
POLICY = 'SAME_DIMENSION_HALF_OPEN_V1'
COVERAGE = 'GROSS_INCLUDES_FIRM_ORDERS'
DIMENSIONS = ('item_id', 'location_id', 'uom', 'customer_id')
PROVENANCE_FIELDS = ('source_system', 'source_record_id', 'source_version', 'observed_at', 'import_batch_id', 'source_locator')
QUANTITY_FIELDS = ('gross_quantity', 'firm_quantity', 'consumed_quantity', 'net_quantity', 'effective_quantity')


def enabled(data):
    from enterprise_forecast import enabled as enterprise_enabled
    return data.get('rules', {}).get('manufacturing_profile') == PROFILE or enterprise_enabled(data)


def _require(ok, code, text):
    if not ok:
        from planning_adapter import InputError
        raise InputError(code + ': ' + text)


def _text(value):
    return format(value.normalize(), 'f')


def _quantity(value, field, *, positive=False):
    _require(isinstance(value, str) and re.fullmatch(r'(0|[1-9][0-9]{0,11})(\.[0-9]{1,8})?', value),
             'FORECAST_QUANTITY', field + ' requires an explicit nonnegative decimal string')
    result = Decimal(value)
    _require(not positive or result > 0, 'FORECAST_QUANTITY', field + ' must be positive')
    return result


def _time(value, field):
    _require(isinstance(value, str) and re.search(r'(Z|[+-][0-9]{2}:[0-9]{2})$', value),
             'FORECAST_TIME', field + ' requires an explicit timestamp and timezone')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        _require(False, 'FORECAST_TIME', field + ' is not a valid timestamp')
    _require(result.utcoffset() is not None, 'FORECAST_TIME', field + ' has no timezone')
    return result


def _identity(obj, fields):
    for key in fields:
        _require(isinstance(obj.get(key), str) and bool(obj[key].strip()), 'FORECAST_MISSING_DIMENSION', 'missing ' + key)
    return tuple(obj[key] for key in fields)


def _provenance(obj):
    p = obj.get('provenance')
    _require(isinstance(p, dict), 'FORECAST_SOURCE', 'explicit source provenance required')
    _identity(p, PROVENANCE_FIELDS)
    _time(p['observed_at'], 'provenance.observed_at')
    return p


def _source_key(obj):
    p = _provenance(obj)
    return p['source_system'], p['source_version'], p['source_record_id']


def _scope(data):
    _require(enabled(data), 'FORECAST_UNSUPPORTED_PROFILE', 'forecast facts require ' + PROFILE)
    from enterprise_forecast import enabled as enterprise_enabled, scope
    if enterprise_enabled(data):
        return scope(data)
    for key in ('suppliers', 'supply_sources', 'existing_orders', 'reservations', 'previous_manufacturing_suggestions',
                'skills', 'resource_skills', 'distribution_lanes', 'distribution_orders', 'locations'):
        _require(not data.get(key), 'FORECAST_UNSUPPORTED_COMBINATION', key + ' is outside this bounded profile')
    _require(len(data.get('production_recipes', [])) == len(data.get('bom_revisions', [])) == len(data.get('routing_revisions', [])) == 1,
             'FORECAST_UNSUPPORTED_COMBINATION', 'one independent continuous recipe required')
    _require(len(data.get('resources', [])) == 1, 'FORECAST_UNSUPPORTED_COMBINATION', 'one continuous resource required')
    resource = data['resources'][0]
    _require(resource.get('capacity_model', 'CONTINUOUS') == 'CONTINUOUS' and resource.get('parallel_units') == '1'
             and not any(key in resource for key in ('role', 'pool_id', 'window_capacity_quantity', 'capacity_uom', 'maxearly_seconds')),
             'FORECAST_UNSUPPORTED_COMBINATION', 'unit continuous resource without qualification/window extensions required')
    route = data['routing_revisions'][0]
    _require(len(route.get('operations', [])) == 1, 'FORECAST_UNSUPPORTED_COMBINATION', 'one simplified manufacturing step required')
    op = route['operations'][0]
    _require(op.get('duration_model') == 'FIXED_PLUS_PER_UNIT' and Decimal(op.get('fixed_seconds', '-1')) == 0
             and Decimal(op.get('seconds_per_unit', '0')) > 0 and op.get('resource_units') == '1'
             and 'operator_requirement' not in op,
             'FORECAST_UNSUPPORTED_COMBINATION', 'one positive continuous per-unit step without qualified personnel required')
    recipe = data['production_recipes'][0]
    _require(recipe.get('manufacturing_minimum_quantity') == recipe.get('manufacturing_multiple') == '1'
             and recipe.get('transfer_policy') == 'WHOLE_MANUFACTURING_BATCH' and 'manufacturing_maximum_quantity' not in recipe,
             'FORECAST_UNSUPPORTED_COMBINATION', 'continuous unit batch recipe required')
    items = {x['id']: x for x in data.get('items', [])}
    _require(len(items) == 2 and sorted(x.get('kind', '') for x in items.values()) == ['FINISHED', 'RAW'],
             'FORECAST_UNSUPPORTED_COMBINATION', 'one raw material and one finished product; no WIP or multi-level chain')
    bom = data['bom_revisions'][0]
    _require(len(bom.get('lines', [])) == 1 and items.get(bom['lines'][0].get('item_id'), {}).get('kind') == 'RAW'
             and Decimal(bom['lines'][0].get('scrap_fraction', '-1')) == 0 and bom['lines'][0].get('consume_at') == 'START',
             'FORECAST_UNSUPPORTED_COMBINATION', 'single raw zero-loss BOM required')
    _require(recipe.get('location_id') == resource.get('location_id') == data['site']['id'],
             'FORECAST_LOCATION', 'resource and recipe must use the declared site')
    return items


def _calculate(data, *, projection_items=None):
    # Only the separately validated enterprise projection Owner supplies items.
    # Legacy profiles keep their exact certification gate and source contract.
    items = _scope(data) if projection_items is None else projection_items
    start, end = (_time(data['horizon'][key], 'horizon.' + key) for key in ('start_at', 'end_at'))
    _require(start < end and _time(data['planning_at'], 'planning_at') == start, 'FORECAST_TIME', 'invalid planning horizon')
    forecasts = data.get('forecasts')
    _require(isinstance(forecasts, list) and bool(forecasts), 'FORECAST_SOURCE', 'at least one explicit forecast is required')
    firm, net_inputs, seen_ids, seen_source, seen_business = [], [], set(), set(), set()
    for order in data.get('demand_lines', []):
        _identity(order, ('id', 'order_ref', 'line_ref'))
        _require(order['id'] not in seen_ids, 'FORECAST_DUPLICATE_ORDER', 'duplicate demand id ' + order['id'])
        seen_ids.add(order['id'])
        kind = order.get('kind')
        _require(kind in ('FIRM_ORDER', 'NET_FORECAST'), 'FORECAST_DEMAND_KIND', 'only explicit FIRM_ORDER or NET_FORECAST demand')
        if kind == 'NET_FORECAST':
            net_inputs.append(order)
            continue
        _require('forecast_id' not in order, 'FORECAST_NET_DEMAND', 'firm orders cannot carry forecast_id')
        _identity(order, DIMENSIONS)
        _require(order.get('status') == 'OPEN', 'FORECAST_CLOSED_HISTORY', 'only currently OPEN firm orders are eligible; historical closed sales are not future demand')
        _require(order['item_id'] in items and items[order['item_id']]['kind'] == 'FINISHED', 'FORECAST_ITEM', 'firm order must reference the finished item')
        _require(order['location_id'] == data['site']['id'], 'FORECAST_LOCATION', 'cross-location consumption is unsupported')
        _require(order['uom'] == items[order['item_id']]['base_uom'], 'FORECAST_UOM', 'cross-unit consumption is unsupported')
        qty = _quantity(order.get('quantity'), 'order.quantity', positive=True)
        due = _time(order.get('due_at'), 'order.due_at')
        _require(start <= due < end, 'FORECAST_TIME', 'firm order must be inside the current future horizon')
        source_key = _source_key(order)
        business_key = (order['customer_id'], order['order_ref'], order['line_ref'])
        _require(source_key not in seen_source and business_key not in seen_business,
                 'FORECAST_DUPLICATE_ORDER', 'same source/business order line cannot enter planning twice')
        seen_source.add(source_key); seen_business.add(business_key)
        firm.append((order, qty, due))
    firm.sort(key=lambda x: (x[2], x[0]['id']))
    rows, parsed, forecast_ids, forecast_sources = [], [], set(), set()
    for forecast in sorted(forecasts, key=lambda x: str(x.get('id', ''))):
        _identity(forecast, ('id', 'version_id', *DIMENSIONS))
        _require(forecast['id'] not in forecast_ids, 'FORECAST_DUPLICATE_BUCKET', 'forecast id repeated')
        forecast_ids.add(forecast['id'])
        source_key = _source_key(forecast)
        _require(source_key not in forecast_sources, 'FORECAST_DUPLICATE_BUCKET', 'same forecast source row repeated')
        forecast_sources.add(source_key)
        _require(forecast.get('policy_id') == POLICY and forecast.get('coverage') == COVERAGE,
                 'FORECAST_UNSUPPORTED_POLICY', 'only exact dimensions, same half-open bucket and gross-includes-orders coverage supported')
        _require(forecast.get('evidence_status') == 'SOURCE_CONFIRMED_SYNTHETIC', 'FORECAST_SOURCE', 'this profile is certified only for source-confirmed synthetic slices')
        _require(forecast['item_id'] in items and items[forecast['item_id']]['kind'] == 'FINISHED', 'FORECAST_ITEM', 'forecast finished item missing')
        _require(forecast['location_id'] == data['site']['id'], 'FORECAST_LOCATION', 'cross-location forecast is unsupported')
        _require(forecast['uom'] == items[forecast['item_id']]['base_uom'], 'FORECAST_UOM', 'forecast unit must exactly match the item base unit')
        a, b = _time(forecast.get('bucket_start'), 'bucket_start'), _time(forecast.get('bucket_end'), 'bucket_end')
        _require(start <= a < b <= end, 'FORECAST_BUCKET', 'bucket must be fully contained in the horizon; partial coverage and cross-period carry are unsupported')
        dimension = tuple(forecast[key] for key in DIMENSIONS)
        _require(not any(dim == dimension and a < old_b and old_a < b for dim, old_a, old_b in parsed),
                 'FORECAST_OVERLAPPING_BUCKET', 'overlapping same-dimension forecasts would consume an order more than once')
        parsed.append((dimension, a, b))
        gross = _quantity(forecast.get('gross_quantity'), 'gross_quantity')
        policy = forecast.get('net_demand')
        _require(isinstance(policy, dict), 'FORECAST_MISSING_POLICY', 'explicit net forecast planning terms required')
        due = _time(policy.get('due_at'), 'net_demand.due_at')
        _require(a <= due < b, 'FORECAST_TIME', 'net forecast due must lie inside its half-open bucket')
        _require(type(policy.get('priority')) is int and policy['priority'] >= 0
                 and type(policy.get('maximum_lateness_seconds')) is int and policy['maximum_lateness_seconds'] >= 0,
                 'FORECAST_MISSING_POLICY', 'explicit priority and lateness policy required')
        minimum = _quantity(policy.get('minimum_shipment'), 'net_demand.minimum_shipment', positive=True)
        matched = [(o, q) for o, q, d in firm if tuple(o[k] for k in DIMENSIONS) == dimension and a <= d < b]
        firm_quantity = sum((q for _, q in matched), Decimal(0))
        consumed, remaining, consumptions = min(gross, firm_quantity), gross, []
        for order, quantity in matched:
            share = min(remaining, quantity)
            consumptions.append(dict(demand_id=order['id'], order_quantity=_text(quantity), consumed_quantity=_text(share), provenance=copy.deepcopy(order['provenance'])))
            remaining -= share
        net = gross - consumed
        _require(net == remaining and net >= 0 and gross == consumed + net, 'FORECAST_CONSERVATION', 'forecast consumption does not conserve quantity')
        _require(net == 0 or minimum <= net, 'FORECAST_NET_DEMAND', 'explicit minimum shipment exceeds positive net forecast')
        net_id = 'NET_FORECAST:' + forecast['version_id'] + ':' + forecast['id'] if net > 0 else None
        row = dict(forecast_id=forecast['id'], version_id=forecast['version_id'], **{key: forecast[key] for key in DIMENSIONS},
                   bucket_start=forecast['bucket_start'], bucket_end=forecast['bucket_end'], gross_quantity=_text(gross),
                   firm_quantity=_text(firm_quantity), consumed_quantity=_text(consumed), net_quantity=_text(net),
                   effective_quantity=_text(firm_quantity + net), net_demand_id=net_id, consumptions=consumptions,
                   provenance=copy.deepcopy(forecast['provenance']), coverage=forecast['coverage'], evidence_status=forecast['evidence_status'])
        rows.append(row)
    matched_ids = {entry['demand_id'] for row in rows for entry in row['consumptions']}
    unmatched = [dict(demand_id=o['id'], quantity=_text(q), reason='NO_EXACT_DIMENSION_AND_HALF_OPEN_BUCKET', provenance=copy.deepcopy(o['provenance'])) for o, q, _ in firm if o['id'] not in matched_ids]
    units = sorted({row['uom'] for row in rows} | {o['uom'] for o, _, _ in firm})
    totals = []
    for unit in units:
        total = {key: sum((Decimal(row[key]) for row in rows if row['uom'] == unit), Decimal(0)) for key in QUANTITY_FIELDS}
        total['firm_quantity'] = sum((q for o, q, _ in firm if o['uom'] == unit), Decimal(0))
        total['effective_quantity'] = total['firm_quantity'] + total['net_quantity']
        _require(total['gross_quantity'] + total['firm_quantity'] - total['consumed_quantity'] == total['effective_quantity'],
                 'FORECAST_CONSERVATION', 'effective planning demand is not conserved')
        totals.append(dict(uom=unit, **{key: _text(value) for key, value in total.items()}))
    return dict(profile=PROFILE, policy_id=POLICY, snapshot_id=data['snapshot_id'], engine_executed=False,
                rows=rows, unmatched_firm_orders=unmatched, totals_by_uom=totals), net_inputs


def net_forecasts(data):
    """Return the source audit with exact row consumption and per-unit totals."""
    report, _ = _calculate(data)
    return report


def projection_netting(data, items):
    """Shared arithmetic for an authenticated projection, not a profile switch.

    The projection Owner authenticates source facts, policy and full native
    model first. Row identities, units, duplicates, buckets and conservation
    are still independently checked here with the same decimal arithmetic.
    """
    report, existing = _calculate(data, projection_items=items)
    _require(not existing, 'FORECAST_ALREADY_APPLIED', 'projection source must contain Firm only')
    return report, _generated(data, report)


def _generated(data, report):
    forecasts = {f['id']: f for f in data['forecasts']}
    result = []
    firm_ids = {d['id'] for d in data['demand_lines'] if d.get('kind') == 'FIRM_ORDER'}
    for row in report['rows']:
        if row['net_demand_id'] is None:
            continue
        f = forecasts[row['forecast_id']]
        _require(row['net_demand_id'] not in firm_ids, 'FORECAST_DUPLICATE_ORDER', 'generated forecast identity collides with a firm order')
        result.append(dict(id=row['net_demand_id'], order_ref='FORECAST:' + f['id'], line_ref=f['version_id'],
                           **{key: f[key] for key in DIMENSIONS}, quantity=row['net_quantity'], **copy.deepcopy(f['net_demand']),
                           status='OPEN', kind='NET_FORECAST', forecast_id=f['id'], provenance=copy.deepcopy(f['provenance'])))
    return result


def apply_net_forecasts(data):
    """Convert a source-only snapshot once; caller confirms/saves its new version."""
    report, existing = _calculate(data)
    _require(not existing, 'FORECAST_ALREADY_APPLIED', 'source import already includes net forecast demands; do not append twice')
    out = copy.deepcopy(data)
    out['demand_lines'].extend(_generated(data, report))
    return out


def validate(data):
    """Gate the canonical PlanInput, including its exact generated net demands."""
    if not enabled(data):
        present = 'forecasts' in data or any('kind' in d or 'forecast_id' in d for d in data.get('demand_lines', []))
        _require(not present, 'FORECAST_UNSUPPORTED_PROFILE', 'forecast demand fields require ' + PROFILE)
        return data
    report, actual = _calculate(data)
    expected = _generated(data, report)
    _require(len(actual) == len(expected), 'FORECAST_NET_DEMAND', 'net forecast demand set does not match calculated positive net amounts')
    by_id = {x['id']: x for x in actual}
    for demand in expected:
        found = by_id.get(demand['id'])
        _require(found is not None and set(found) == set(demand), 'FORECAST_NET_DEMAND', 'net forecast identity/fields differ from the audited conversion')
        for key, value in demand.items():
            same = _quantity(found[key], 'net.' + key) == Decimal(value) if key in ('quantity', 'minimum_shipment') else found[key] == value
            _require(same, 'FORECAST_NET_DEMAND', 'net forecast ' + key + ' differs from the audited conversion')
    return data
