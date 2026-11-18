"""Typed, source-cell preserving P2 inventory/purchase simulation declarations."""
import copy

from inventory_decision import PROFILE, validate

SHEET = '库存与供应条件'
KIND = '条件类型'
FIELDS = {
    '选项编码': ('id', 'string'), '选项名称': ('label', 'string'), '供应来源编码': ('source_id', 'string'),
    '物料编码': ('item_id', 'string'), '地点编码': ('location_id', 'string'), '数量单位': ('uom', 'string'),
    '最小采购量': ('minimum_order_quantity', 'decimal_string'), '采购倍数': ('order_multiple', 'decimal_string'),
    '单笔采购上限': ('maximum_order_quantity', 'decimal_string'), '全期供应总量': ('total_capacity_quantity', 'decimal_string'),
    '采购提前秒': ('lead_time_seconds', 'integer'), '质检等待秒': ('qc_wait_seconds', 'integer'),
    '最早下单': ('earliest_order_at', 'datetime_offset'), '最晚下单': ('latest_order_at', 'datetime_offset'),
    '来源有效开始': ('effective_from', 'datetime_offset'), '来源有效结束': ('effective_to', 'datetime_offset'),
    '事实类型': ('fact_kind', 'string'),
    '最低净库存': ('minimum', 'decimal_string'), '补给目标净库存': ('maximum', 'decimal_string'),
    '政策开始': ('start_at', 'datetime_offset'), '政策结束': ('end_at', 'datetime_offset'),
}
SOURCE_HEADERS = ['来源记录', '来源版本', '来源说明']
HEADERS = [KIND, *FIELDS, *SOURCE_HEADERS]
COMMON = {'item_id', 'location_id', 'uom'}
POLICY_KEYS = COMMON | {'minimum', 'maximum', 'start_at', 'end_at'}
OPTION_KEYS = set(field for field, _ in FIELDS.values()) - {'minimum', 'maximum', 'start_at', 'end_at'}


def apply(candidate, tables, settings, issue, lineage):
    from p2.preflight import parsed, column
    from planning_adapter import InputError
    tab = tables['sheets'].get(SHEET)
    if not tab:
        return
    nonempty = [(ri, values) for ri, values in enumerate(tab['rows']) if any(v not in (None, '') for v in values)]
    if not nonempty and not tab.get('formulas'):
        return
    if settings.get('rules.manufacturing_profile') != PROFILE:
        for ri, values in nonempty:
            for ci, raw in enumerate(values):
                if raw not in (None, ''):
                    header = tab['headers'][ci] if ci < len(tab['headers']) else column(ci+1)
                    issue('UNSUPPORTED_INVENTORY_DECISION', f'字段“{header}”需要显式{PROFILE}，不能静默丢弃',
                          SHEET+'!'+column(ci+1)+str(ri+6), 'CAPABILITY')
        for cell in tab.get('formulas', []):
            issue('UNSUPPORTED_INVENTORY_DECISION', '当前模型不支持库存供应条件公式', SHEET+'!'+cell, 'CAPABILITY')
        return
    if len(tab['headers']) != len(set(tab['headers'])) or set(tab['headers']) != set(HEADERS):
        issue('INVENTORY_DECISION_MAPPING', '库存与供应条件表头必须完整、唯一且没有未知列', SHEET)
        return
    if tab.get('formulas'):
        for cell in tab['formulas']:
            issue('FORMULA_INPUT', '政策和来源条款必须为有来源的显式值，不接受缓存公式', SHEET+'!'+cell)
        return
    catalogs, policies, entries = [], [], []
    for ri, values in nonempty:
        row = dict(zip(tab['headers'], values))
        def cell(header):
            return SHEET+'!'+column(tab['headers'].index(header)+1)+str(ri+6)
        try:
            kind = parsed(row.get(KIND), 'string')
            if kind not in ('SUPPLY_OPTION', 'BUFFER_POLICY'):
                raise ValueError('条件类型仅支持SUPPLY_OPTION或BUFFER_POLICY；不在导入时启用采购选项')
            required = OPTION_KEYS if kind == 'SUPPLY_OPTION' else POLICY_KEYS
            obj = {}
            for header, (field, typ) in FIELDS.items():
                if field in required:
                    try:
                        obj[field] = parsed(row.get(header), typ)
                    except (TypeError, ValueError) as exc:
                        issue('INVENTORY_DECISION_MAPPING', f'字段“{header}”：{exc}', cell(header))
                        raise ValueError('字段解析失败')
                elif row.get(header) not in (None, ''):
                    issue('INVENTORY_DECISION_MAPPING', f'字段“{header}”不属于{kind}，已填值不能丢弃', cell(header))
                    raise ValueError('混入不适用字段')
            for header in SOURCE_HEADERS:
                parsed(row.get(header), 'string')
            obj['provenance'] = dict(source_system='SYNTHETIC_RESEARCH_ASSUMPTION', source_record_id=row['来源记录'],
                source_version=row['来源版本'], observed_at=settings['planning_at'],
                import_batch_id='P2-'+tables['sha256'][:12], source_locator=cell(KIND))
            (catalogs if kind == 'SUPPLY_OPTION' else policies).append(obj)
            entries.append((ri, row, obj, kind))
        except (KeyError, TypeError, ValueError) as exc:
            issue('INVENTORY_DECISION_MAPPING', str(exc), cell(KIND))
    if not catalogs or len(policies) > 1:
        issue('INVENTORY_DECISION_MAPPING', '需要1至2条冻结采购条件，至多1条全期库存政策；不推断缺失来源', SHEET)
        return
    trial = copy.deepcopy(candidate)
    trial['inventory_decision_catalog'] = catalogs
    if policies:
        trial['inventory_buffer_policy'] = policies[0]
    try:
        validate(trial)
    except InputError as exc:
        issue('INVENTORY_DECISION_MAPPING', str(exc), SHEET)
        return
    candidate['inventory_decision_catalog'] = catalogs
    if policies:
        candidate['inventory_buffer_policy'] = policies[0]
    for ri, row, obj, kind in entries:
        root = '/inventory_decision_catalog/'+str(catalogs.index(obj)) if kind == 'SUPPLY_OPTION' else '/inventory_buffer_policy'
        for header in HEADERS:
            if row.get(header) in (None, ''):
                continue
            field = FIELDS[header][0] if header in FIELDS else 'provenance/'+header if header in SOURCE_HEADERS else 'declaration_kind'
            value = obj.get(field, row[header])
            lineage(SHEET, ri, tab['headers'].index(header), row[header], value, root+'/'+field,
                    'EXPLICIT_SYNTHETIC_INVENTORY_OR_SUPPLY_CONDITION')


def write_sheet(book, data):
    """Author explicit typed source cells; never imports, enables or computes."""
    validate(data)
    if SHEET in book.sheetnames:
        raise ValueError('Existing inventory and supply declaration sheet must not be overwritten')
    sheet = book.create_sheet(SHEET)
    sheet.append(['P414 explicitly frozen synthetic policy and purchase-term assumptions; simulation only'])
    for _ in range(3):
        sheet.append([None])
    sheet.append(HEADERS)
    for kind, row in [('SUPPLY_OPTION', r) for r in data.get('inventory_decision_catalog', [])] + (
            [('BUFFER_POLICY', data['inventory_buffer_policy'])] if data.get('inventory_buffer_policy') is not None else []):
        p = row['provenance']
        sheet.append([kind] + [row.get(field) for field, _ in FIELDS.values()] +
                     [p['source_record_id'], p['source_version'], 'Declared synthetic source '+p['source_locator']])
    return sheet
