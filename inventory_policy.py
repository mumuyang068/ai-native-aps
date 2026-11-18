"""Bounded, read-only policy gaps over authenticated native inventory events.

Policies are explicit versioned synthetic assumptions. This module does not
infer replenishment quantities, order allocations, physical receipts or release.
"""
import copy
from datetime import datetime
from decimal import Decimal, InvalidOperation

from planning_capabilities import supports,profile_id

PROFILE = 'enterprise-forecast-fg-v1'
FACT_KIND = 'SYNTHETIC_POLICY_ASSUMPTION'
TARGET_KIND = 'MINIMUM_NET_AVAILABLE'
IDENTITY_FIELDS = ('plan_id', 'data_version_id', 'run_id', 'target', 'profile')
QUANTITY_FIELDS = ('opening_quantity', 'inflow_quantity', 'consumption_quantity', 'ending_quantity',
                   'minimum_quantity', 'ending_gap_quantity', 'maximum_gap_quantity')


def _fail(code, detail=''):
    from planning_adapter import InputError
    raise InputError(code + (': ' + detail if detail else ''))


def _time(value):
    if not isinstance(value, str):
        raise ValueError('显式时间及UTC偏移必填')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('显式时间必须包含UTC偏移')
    return parsed


def _number(value):
    if not isinstance(value, (str, int, float)) or isinstance(value, bool) or str(value).strip() == '':
        raise ValueError('数量缺失')
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError('数量必须有限')
    return number


def _quantity(value):
    return format(value.normalize(), 'f')


def validate(data):
    """Early canonical gate, also called by the P2 source-sheet mapper."""
    policies = data.get('inventory_policies', [])
    if not isinstance(policies, list):
        _fail('INVENTORY_POLICY_INVALID', 'inventory_policies必须为显式桶数组')
    if not policies:
        return
    if data.get('rules', {}).get('manufacturing_profile') not in {PROFILE, 'enterprise-accessory-supply-v1'}:
        _fail('UNSUPPORTED_INVENTORY_POLICY_PROFILE')
    items = {item['id']: item for item in data.get('items', [])}
    locations = {data.get('site', {}).get('id')} | {row['id'] for row in data.get('locations', [])}
    seen, windows = set(), {}
    for row in policies:
        try:
            for field in ('id', 'policy_version', 'item_id', 'location_id', 'uom'):
                if not isinstance(row[field], str) or not row[field].strip():
                    raise ValueError(field+'必填')
            if row['id'] in seen:
                _fail('INVENTORY_POLICY_DUPLICATE', row['id'])
            seen.add(row['id'])
            if row['fact_kind'] != FACT_KIND or row['target_kind'] != TARGET_KIND:
                raise ValueError('仅接受显式合成最低净可用政策')
            if row['item_id'] not in items or row['location_id'] not in locations:
                raise ValueError('物料或地点不属于当前确认版本')
            if row['uom'] != items[row['item_id']]['base_uom']:
                raise ValueError('政策单位必须与物料基本单位完全一致，不自动换算')
            if not isinstance(row['target_quantity'], str) or _number(row['target_quantity']) < 0:
                raise ValueError('政策目标必须为非负显式十进制字符串')
            start, end = _time(row['start_at']), _time(row['end_at'])
            if not (_time(data['planning_at']) <= start < end <= _time(data['horizon']['end_at'])):
                raise ValueError('政策桶必须处于当前规划时点至规划期末的半开区间内')
            if start < _time(data['horizon']['start_at']):
                raise ValueError('政策桶早于规划期')
            provenance = row['provenance']
            for field in ('source_system', 'source_record_id', 'source_version', 'source_locator', 'import_batch_id'):
                if not isinstance(provenance[field], str) or not provenance[field].strip():
                    raise ValueError('政策必须保留来源字段 '+field)
            _time(provenance['observed_at'])
            dimension = (row['item_id'], row['location_id'], row['uom'])
            for prior_start, prior_end in windows.get(dimension, []):
                if start < prior_end and end > prior_start:
                    _fail('INVENTORY_POLICY_OVERLAP', row['id'])
            windows.setdefault(dimension, []).append((start, end))
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            _fail('INVENTORY_POLICY_INVALID', str(exc))


def _verified_events(series, inventory, start, end):
    """Return authenticated sequence only; no incomplete series becomes a line."""
    if inventory.get('status') not in ('PRESENT', 'EMPTY_CHECKED') or inventory.get('validation', {}).get('status') != 'PASS':
        raise ValueError('动态库存证据未完整核验')
    if series.get('complete') is not True or not isinstance(series.get('events'), list):
        raise ValueError('动态库存事件未采集完整')
    opening_at = _time(series['opening_at'])
    if opening_at > start or _time(series['verified_through']) < end:
        raise ValueError('已核验库存区间未覆盖此政策桶')
    events = series['events']
    if series.get('event_status') != ('PRESENT' if events else 'EMPTY_CHECKED'):
        raise ValueError('事件状态不是明确已取得或已查询为空')
    balance, previous, ids = _number(series['opening']), None, set()
    for event in events:
        at = _time(event['at'])
        sequence = event['native_sequence']
        if not isinstance(sequence, int) or isinstance(sequence, bool):
            raise ValueError('缺少原生同刻顺序')
        order = (at, sequence)
        if at < opening_at or (previous is not None and order <= previous):
            raise ValueError('原生事件时间或同刻顺序不一致')
        previous = order
        if not event.get('id') or event['id'] in ids or not event.get('sources'):
            raise ValueError('原生事件身份或来源缺失')
        ids.add(event['id'])
        balance += _number(event['quantity'])
        if balance != _number(event['balance']):
            raise ValueError('原生事件后余额与净期初及真实事件不守恒')
    if balance != _number(series['ending']):
        raise ValueError('原生期末余额不守恒')
    return events


def project(data, analysis, identity, *, policies=None, item_id=None, location_id=None, uom=None, start=None, end=None):
    """Project explicit half-open buckets from a read_analysis authenticated model.

    The caller must read the current run through the existing manifest gate.
    No data, result or policy source is fetched from another plan/version/run.
    """
    if (not all(identity.get(key) for key in IDENTITY_FIELDS) or
            any(analysis.get('identity', {}).get(key) != identity.get(key) for key in IDENTITY_FIELDS) or
            profile_id(data) != identity['profile']):
        _fail('INVENTORY_POLICY_IDENTITY_MISMATCH')
    value = dict(identity=copy.deepcopy(identity), timezone=analysis.get('timezone'), status='NOT_READY',
                 mode='POLICY_DIAGNOSIS_ONLY', rows=[],
                 known_business_fail=(analysis.get('native_result_status', {}).get('constraint_check') == 'FAIL' or
                                      analysis.get('inventory_analysis', {}).get('validation', {}).get('status') == 'FAIL'),
                 limits=['政策目标是版本化合成假设；缺口不是最优补货量，不生成采购或配送指令。',
                         '余额来自同一运行净期初及原生事件；质量冻结与计划在途不补入净可用。',
                         '政策缺口、负库存和客户需求欠量分别表示；库存事件不证明订单级分配。'])
    if profile_id(data)=='foundation-planning-projection-v1':
        from p22.foundation_projection import validate as validate_projection,digest
        validate_projection(data)
        if (data['foundation_projection']['source_version_id']!=identity['data_version_id'] or
                any(not identity.get(key) or analysis['identity'].get(key)!=identity[key]
                    for key in ('snapshot_sha256','scenario_id','input_hash')) or
                data['scenario_id']!=identity['scenario_id'] or digest(data)!=identity['input_hash']):
            _fail('INVENTORY_POLICY_SOURCE_MISMATCH')
        # Existing explicit synthetic minimum buckets cannot represent sourced
        # overlapping soft min/max objectives without changing their semantics.
        value.update(status='UNSUPPORTED',reason_code='FOUNDATION_SOFT_MIN_MAX_NOT_SYNTHETIC_POLICY_BUCKETS',
            reason='本入口仅支持显式合成最低净可用政策桶；当前来源软性最低/最高目标使用同运行库存分析观察，不转换为此政策。',
            source_observation_path='inventory_analysis.policy_observations',
            limits=['当前来源软目标保留原最低/最高值、适用区间及软约束语义；此入口未将其转为最低库存政策桶。',
                    '已有目标观察及偏离仍由同运行库存分析提供，不生成采购、执行或批准指令。'])
        return value
    if not supports(identity['profile'], 'analyses', 'inventory'):
        value.update(status='UNSUPPORTED', reason='当前模型尚未认证动态库存，不能计算政策缺口。')
        return value
    source_data = {**data, 'inventory_policies': data.get('inventory_policies', []) if policies is None else policies}
    validate(source_data)
    try:
        range_start, range_end = _time(start or analysis['planning_at']), _time(end or analysis['horizon_end'])
        if not (_time(analysis['planning_at']) <= range_start < range_end <= _time(analysis['horizon_end'])):
            raise ValueError('查询区间必须处于当前运行规划期内')
        if analysis['timezone'] != data['site']['timezone']:
            raise ValueError('当前运行与确认版本时区不同')
    except (KeyError, TypeError, ValueError) as exc:
        _fail('INVENTORY_POLICY_INVALID_RANGE', str(exc))
    value['window'] = {'start_at': range_start.isoformat(), 'end_at': range_end.isoformat()}
    inventory = analysis.get('inventory_analysis', {})
    for policy in source_data['inventory_policies']:
        if any(expected and policy[field] != expected for field, expected in [('item_id', item_id), ('location_id', location_id), ('uom', uom)]):
            continue
        a, b = max(range_start, _time(policy['start_at'])), min(range_end, _time(policy['end_at']))
        if a >= b:
            continue
        row = {key: copy.deepcopy(policy[key]) for key in ('id', 'policy_version', 'item_id', 'location_id', 'uom', 'target_quantity')}
        row.update(start_at=a.isoformat(), end_at=b.isoformat(), end_boundary='OPEN', policy=copy.deepcopy(policy),
                   status='NOT_READY', negative_inventory_status='NOT_CHECKED', first_gap_at=None,
                   events=[], event_ids=[], predecessor_event_ids=[], sources=[copy.deepcopy(policy['provenance'])],
                   physical_opening=None, quality_blocked=None, other_nonnettable=None, inspection_pending_quantity=None,
                   allocation_scope='RUN_MATERIAL_EVENT_NOT_ORDER_ALLOCATION')
        row.update({field: None for field in QUANTITY_FIELDS})
        matches = [series for series in inventory.get('series', []) if
                   (series.get('item_id'), series.get('location_id'), series.get('uom')) ==
                   (policy['item_id'], policy['location_id'], policy['uom'])]
        try:
            if len(matches) != 1:
                raise ValueError('未取得同物料、地点、单位的唯一动态库存序列')
            series = matches[0]
            events = _verified_events(series, inventory, a, b)
            balance, inflow, consumed = _number(series['opening']), Decimal(0), Decimal(0)
            predecessors = [event for event in events if _time(event['at']) < a]
            for event in predecessors:
                balance += _number(event['quantity'])
            opening, minimum, target = balance, balance, _number(policy['target_quantity'])
            first_gap = a.isoformat() if opening < target else None
            selected = [event for event in events if a <= _time(event['at']) < b]
            for event in selected:
                delta = _number(event['quantity'])
                balance += delta
                inflow += max(delta, Decimal(0))
                consumed += max(-delta, Decimal(0))
                minimum = min(minimum, balance)
                if first_gap is None and balance < target:
                    first_gap = event['at']
            amounts = (opening, inflow, consumed, balance, minimum, max(target-balance, Decimal(0)), max(target-minimum, Decimal(0)))
            row.update(dict(zip(QUANTITY_FIELDS, map(_quantity, amounts))))
            row.update(status='PRESENT', first_gap_at=first_gap,
                       negative_inventory_status='FAIL' if minimum < 0 else 'PASS',
                       events=copy.deepcopy(selected), event_ids=[event['id'] for event in selected],
                       predecessor_event_ids=[event['id'] for event in predecessors],
                       opening_at=series['opening_at'], opening_source=copy.deepcopy(series.get('opening_source')),
                       physical_opening=series.get('physical_opening'), quality_blocked=series.get('quality_blocked'),
                       other_nonnettable=series.get('other_nonnettable'), inspection_pending_quantity=series.get('inspection_pending_quantity'))
            row['sources'] += [copy.deepcopy(series.get('opening_source'))] + [copy.deepcopy(source) for event in predecessors+selected for source in event['sources']]
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            row['reason'] = str(exc)
        value['rows'].append(row)
    ready = sum(row['status'] == 'PRESENT' for row in value['rows'])
    value['status'] = 'EMPTY_CHECKED' if not value['rows'] else 'PRESENT' if ready == len(value['rows']) else 'PARTIAL' if ready else 'NOT_READY'
    if value['status'] == 'NOT_READY':
        value['reason'] = '政策桶尚无完整、同维度且已核验的动态库存证据。'
    return value
