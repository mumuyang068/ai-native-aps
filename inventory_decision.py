"""Bounded, source-backed LABEL inventory and purchase-term simulations.

The catalog is dormant enterprise-version evidence. Only an explicit isolated
selection changes native purchase terms. Inventory minimum is a soft target;
maximum is the replenishment target after a shortage triggers replenishment.
Neither is a promise of inventory availability or an execution authorization.
"""
import copy
import hashlib
import json
import re
from datetime import datetime, timedelta
from decimal import Decimal

PROFILE = 'enterprise-accessory-supply-v1'
CATALOG_FIELDS = {'id', 'label', 'source_id', 'item_id', 'location_id', 'uom',
    'minimum_order_quantity', 'order_multiple', 'maximum_order_quantity',
    'total_capacity_quantity', 'lead_time_seconds', 'qc_wait_seconds',
    'earliest_order_at', 'latest_order_at', 'effective_from', 'effective_to',
    'provenance', 'fact_kind'}
POLICY_FIELDS = {'item_id', 'location_id', 'uom', 'minimum', 'maximum', 'start_at', 'end_at', 'provenance'}
SOURCE_TERMS = ('minimum_order_quantity', 'order_multiple', 'maximum_order_quantity',
                'lead_time_seconds', 'effective_from', 'effective_to')
OPTION_TERMS = ('earliest_order_at', 'latest_order_at', 'total_capacity_quantity', 'uom')
PROVENANCE_FIELDS = {'source_system', 'source_record_id', 'source_version', 'observed_at', 'import_batch_id', 'source_locator'}


def _require(condition, code, detail=''):
    if not condition:
        from planning_adapter import InputError
        raise InputError(code + (': '+detail if detail else ''))


def _number(value):
    _require(isinstance(value, str) and re.fullmatch(r'(0|[1-9][0-9]{0,11})(\.[0-9]{1,8})?', value),
             'INVENTORY_DECISION_QUANTITY', 'explicit canonical decimal string required')
    return Decimal(value)


def _time(value):
    try:
        _require(isinstance(value, str), 'INVENTORY_DECISION_TIME')
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        _require(result.utcoffset() is not None, 'INVENTORY_DECISION_TIME', 'timezone required')
        return result
    except (ValueError, TypeError):
        _require(False, 'INVENTORY_DECISION_TIME')


def _provenance(value):
    _require(isinstance(value, dict) and set(value) == PROVENANCE_FIELDS and
             all(isinstance(v, str) and v.strip() for v in value.values()), 'INVENTORY_DECISION_SOURCE')
    _time(value['observed_at'])


def _selected(data):
    selection = data.get('inventory_supply_selection')
    if selection is None:
        return None
    _require(isinstance(selection, dict) and set(selection) == {'option_id'} and
             isinstance(selection['option_id'], str), 'INVENTORY_DECISION_SELECTION')
    found = [row for row in data.get('inventory_decision_catalog', []) if row.get('id') == selection['option_id']]
    _require(len(found) == 1, 'INVENTORY_DECISION_SELECTION', 'unknown or ambiguous frozen option')
    return found[0]


def validate(data):
    """Reject unsupported semantics before profile dispatch or native mapping."""
    new_fields = {'inventory_decision_catalog', 'inventory_buffer_policy', 'inventory_supply_selection'} & data.keys()
    maxima = [row for row in data.get('supply_sources', []) if 'maximum_order_quantity' in row]
    if not new_fields:
        _require(not maxima, 'UNSUPPORTED_INVENTORY_DECISION_TERMS')
        return
    _require(data.get('rules', {}).get('manufacturing_profile') == PROFILE,
             'UNSUPPORTED_INVENTORY_DECISION_PROFILE')
    _require('inventory_decision_catalog' in data, 'INVENTORY_DECISION_CATALOG_REQUIRED')
    catalog = data['inventory_decision_catalog']
    _require(isinstance(catalog, list) and 1 <= len(catalog) <= 2, 'INVENTORY_DECISION_CATALOG')
    option = data.get('accessory_supply_option', {})
    source_rows = [s for s in data.get('supply_sources', []) if s.get('id') == option.get('source_id')]
    _require(len(source_rows) == 1, 'INVENTORY_DECISION_SOURCE')
    source = source_rows[0]
    _require(source.get('item_id') == 'LABEL' and source.get('location_id') == data['site']['id'] == 'FACTORY',
             'INVENTORY_DECISION_DIMENSION')
    item = next((i for i in data.get('items', []) if i.get('id') == 'LABEL'), {})
    now, end = _time(data['planning_at']), _time(data['horizon']['end_at'])
    seen = set()
    for row in catalog:
        _require(isinstance(row, dict) and set(row) == CATALOG_FIELDS, 'INVENTORY_DECISION_CATALOG_FIELDS')
        _require(all(isinstance(row[k], str) and row[k].strip() for k in ('id', 'label', 'source_id', 'item_id', 'location_id', 'uom')),
                 'INVENTORY_DECISION_CATALOG_IDENTITY')
        _require(row['id'] not in seen, 'INVENTORY_DECISION_DUPLICATE_OPTION')
        seen.add(row['id'])
        _require(row['fact_kind'] == 'SYNTHETIC_RESEARCH_ASSUMPTION', 'INVENTORY_DECISION_FACT_KIND')
        _require((row['source_id'], row['item_id'], row['location_id'], row['uom']) ==
                 (source['id'], source['item_id'], source['location_id'], item.get('base_uom')),
                 'INVENTORY_DECISION_DIMENSION')
        minimum, multiple, maximum, capacity = [_number(row[k]) for k in
            ('minimum_order_quantity', 'order_multiple', 'maximum_order_quantity', 'total_capacity_quantity')]
        _require(minimum > 0 and multiple > 0 and minimum % multiple == 0 and maximum >= minimum and
                 maximum % multiple == 0 and capacity >= minimum, 'INVENTORY_DECISION_TERMS')
        _require(all(type(row[k]) is int and row[k] > 0 for k in ('lead_time_seconds', 'qc_wait_seconds')),
                 'INVENTORY_DECISION_TERMS', 'positive explicit lead time and QC wait required')
        first, last = _time(row['earliest_order_at']), _time(row['latest_order_at'])
        _require(now <= first <= last < end and
                 last + timedelta(seconds=row['lead_time_seconds']+row['qc_wait_seconds']) <= end,
                 'INVENTORY_DECISION_ORDER_WINDOW')
        _require(_time(row['effective_from']) <= now and _time(row['effective_to']) >= end,
                 'INVENTORY_DECISION_SOURCE_VALIDITY', 'partial source validity unsupported')
        _provenance(row['provenance'])
    policy = data.get('inventory_buffer_policy')
    if policy is not None:
        _require(isinstance(policy, dict) and set(policy) == POLICY_FIELDS, 'INVENTORY_DECISION_POLICY_FIELDS')
        _require((policy['item_id'], policy['location_id'], policy['uom']) == ('LABEL', 'FACTORY', item.get('base_uom')),
                 'INVENTORY_DECISION_DIMENSION')
        _require(_number(policy['minimum']) <= _number(policy['maximum']), 'INVENTORY_DECISION_POLICY_RANGE')
        _require(_time(policy['start_at']) == now and _time(policy['end_at']) == end,
                 'INVENTORY_DECISION_POLICY_VALIDITY', 'constant complete planning horizon only')
        _provenance(policy['provenance'])
    selected = _selected(data)
    if selected is not None:
        _require(all(source.get(k) == selected[k] for k in SOURCE_TERMS) and
                 source.get('receiving_qc', {}).get('wait_seconds') == selected['qc_wait_seconds'] and
                 all(option.get(k) == selected[k] for k in OPTION_TERMS) and option.get('enabled') is True,
                 'INVENTORY_DECISION_SELECTION_REPLAY', 'selected frozen terms must exactly match candidate source and option')
        _require(source.get('provenance') == selected['provenance'] and
                 source.get('receiving_qc', {}).get('provenance') == selected['provenance'] and
                 option.get('provenance') == selected['provenance'], 'INVENTORY_DECISION_SELECTION_REPLAY', 'term provenance mismatch')
    _require(not maxima or selected is not None and len(maxima) == 1 and maxima[0] is source,
             'UNSUPPORTED_INVENTORY_DECISION_TERMS')


def apply(data, *, policy=None, option_id=None):
    """Pure typed replay into an independent candidate; never changes a version."""
    validate(data)
    _require(policy is not None or option_id is not None, 'INVENTORY_DECISION_EMPTY')
    candidate = copy.deepcopy(data)
    if policy is not None:
        candidate['inventory_buffer_policy'] = copy.deepcopy(policy)
    if option_id is not None:
        candidate['inventory_supply_selection'] = {'option_id': option_id}
        selected = _selected(candidate)
        source = next(s for s in candidate['supply_sources'] if s['id'] == selected['source_id'])
        for key in SOURCE_TERMS:
            source[key] = selected[key]
        source['provenance'] = copy.deepcopy(selected['provenance'])
        source['receiving_qc'] = dict(wait_seconds=selected['qc_wait_seconds'], provenance=copy.deepcopy(selected['provenance']))
        candidate['accessory_supply_option'].update({key: selected[key] for key in OPTION_TERMS})
        candidate['accessory_supply_option'].update(enabled=True, provenance=copy.deepcopy(selected['provenance']))
    validate(candidate)
    return candidate


def map_native(data, native):
    """Map exact native buffer parameters and the selected source order maximum."""
    validate(data)
    policy = _native_policy(data)
    if policy is not None:
        rows = [b for b in native['buffer'] if (b['item'], b['location']) == (policy['item_id'], policy['location_id'])]
        _require(len(rows) == 1, 'INVENTORY_DECISION_NATIVE_BUFFER')
        rows[0].update(minimum=policy['minimum'], maximum=policy['maximum'])
    selected = _selected(data)
    if selected is not None:
        source = next(s for s in data['supply_sources'] if s['id'] == selected['source_id'])
        rows = [s for s in native['itemsupplier'] if (s['item'], s['supplier'], s['location']) ==
                (source['item_id'], source['supplier_id'], source['location_id'])]
        _require(len(rows) == 1, 'INVENTORY_DECISION_NATIVE_SOURCE')
        rows[0]['sizemaximum'] = selected['maximum_order_quantity']
    return native


def _native_policy(data):
    """Catalog-enabled runs must replace optional persisted policy values.

    The REST importer updates existing buffer rows. Omitting minimum/maximum
    would preserve an earlier scenario's4/10 even when this input has no policy.
    Explicit0/0 is scoped only to the new bounded catalog; old inputs keep their
    existing native mapping. A missing observer field never proves this zero.
    """
    if 'inventory_decision_catalog' not in data:
        return None
    return data.get('inventory_buffer_policy') or dict(item_id='LABEL', location_id='FACTORY',
        minimum='0', maximum='0')


def summary(data):
    if not any(key in data for key in ('inventory_decision_catalog', 'inventory_buffer_policy', 'inventory_supply_selection')):
        return None
    validate(data)
    policy = copy.deepcopy(data.get('inventory_buffer_policy'))
    if policy is not None:
        policy['semantics'] = 'SOFT_MINIMUM_REPLENISH_TO_MAXIMUM'
    selected = copy.deepcopy(_selected(data))
    if selected is not None:
        selected.update(unit_price=None, currency=None, price_status='NOT_PROVIDED',
                        capacity_scope='ONE_TOTAL_ORDER_WINDOW_BUDGET_NOT_DAILY')
    return dict(policy=policy, supply=selected, catalog=copy.deepcopy(data.get('inventory_decision_catalog', [])),
                simulation_only=True, business_permission='NOT_CONFIRMED', execution_authorized=False,
                limits=['最低库存是软目标，最高库存是发生补给时的目标，不保证一定触发或实现。',
                        '来源条款是冻结的合成假设；不是供应商确认、真实采购、付款或最低成本证明。'])


def audit_policy(data, raw, *, run_id=None, policy=None):
    """Read 198/201 native policy fields; no target gap becomes a hard violation."""
    policy = _native_policy(data) if policy is None else policy
    issues, missing = [], []
    result = dict(status='UNSUPPORTED' if policy is None else 'NOT_CHECKED', issues=issues,
                  missing_evidence=missing, scope='NATIVE_CONSTANT_BUFFER_POLICY_NOT_TARGET_FEASIBILITY', summary=summary(data))
    if policy is None:
        return result
    result.update(declared_policy='inventory_buffer_policy' in data,
                  expected_native_values={k: policy[k] for k in ('item_id', 'location_id', 'minimum', 'maximum')},
                  isolation_scope='EXPLICIT_ZERO_WHEN_NO_POLICY_IN_CATALOG_ENABLED_RUN')
    digest = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    for phase in ('pre', 'post'):
        snap = raw.get('fixed_batch_'+phase)
        if not isinstance(snap, dict):
            missing.append('fixed_batch_'+phase)
            continue
        if run_id is None or snap.get('run_id') != run_id or snap.get('input_hash') != digest or snap.get('phase') != phase:
            issues.append(dict(code='INVENTORY_POLICY_NATIVE_IDENTITY', phase=phase))
        buffers = snap.get('buffers')
        if not isinstance(buffers, list):
            missing.append(phase+':policy_buffers')
            continue
        rows = [b for b in buffers if (b.get('item'), b.get('location')) == (policy['item_id'], policy['location_id'])]
        if len(rows) != 1:
            issues.append(dict(code='INVENTORY_POLICY_NATIVE_BUFFER_IDENTITY', phase=phase))
            continue
        row = rows[0]
        for field in ('minimum', 'maximum', 'minimum_calendar', 'maximum_calendar'):
            if field not in row:
                missing.append(phase+':policy_'+field)
                continue
            try:
                same = row[field] is None if field.endswith('_calendar') else Decimal(str(row[field])) == Decimal(policy[field])
            except (TypeError, ValueError, ArithmeticError):
                same = False
            if not same:
                issues.append(dict(code='INVENTORY_POLICY_NATIVE_DRIFT', phase=phase, field=field))
    result['status'] = 'FAIL' if issues else 'NOT_CHECKED' if missing else 'PASS'
    return result
