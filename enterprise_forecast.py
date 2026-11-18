"""Explicit intersection of deterministic forecast netting and the bounded FG model.

This is not a universal composition switch. Native mapping and all manufacturing
and material audits stay owned by the existing finished-delivery implementation.
"""
PROFILE = 'enterprise-forecast-fg-v1'


def enabled(data):
    return data.get('rules', {}).get('manufacturing_profile') in {PROFILE, 'enterprise-accessory-supply-v1'}


def scope(data):
    from finished_delivery import validate
    from planning_adapter import InputError
    validate(data)
    forecasts = data.get('forecasts', [])
    if len(forecasts) != 1 or len({d.get('customer_id') for d in data['demand_lines']}) != 1:
        raise InputError('ENTERPRISE_FORECAST_SCOPE: one SKU, customer and forecast bucket only')
    return {i['id']: i for i in data['items']}
