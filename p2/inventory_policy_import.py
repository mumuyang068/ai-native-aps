"""Explicit policy source cells in the existing P2 confirmation pipeline."""
from inventory_policy import PROFILE, validate

SHEET = '库存目标策略'
FIELDS = {
    '策略桶编码': ('id', 'string'), '策略版本': ('policy_version', 'string'),
    '物料编码': ('item_id', 'string'), '地点编码': ('location_id', 'string'), '数量单位': ('uom', 'string'),
    '期间开始': ('start_at', 'datetime_offset'), '期间结束': ('end_at', 'datetime_offset'),
    '目标净可用量': ('target_quantity', 'decimal_string'), '目标口径': ('target_kind', 'string'),
    '事实类型': ('fact_kind', 'string'),
}
SOURCE_HEADERS = ['来源记录', '来源版本', '来源说明']
HEADERS = [*FIELDS, *SOURCE_HEADERS]


def apply(candidate, tables, settings, issue, lineage):
    from p2.preflight import parsed
    from planning_adapter import InputError
    tab = tables['sheets'].get(SHEET)
    if not tab:
        return
    rows = [(i, dict(zip(tab['headers'], values))) for i, values in enumerate(tab['rows'])
            if any(value not in (None, '') for value in values)]
    if not rows and not tab.get('formulas'):
        return
    if settings.get('rules.manufacturing_profile') not in {PROFILE, 'enterprise-accessory-supply-v1'}:
        issue('UNSUPPORTED_INVENTORY_POLICY', '当前模型不支持非空库存政策字段，零目标也不可静默丢弃', SHEET)
        return
    if len(tab['headers']) != len(set(tab['headers'])) or set(tab['headers']) != set(HEADERS):
        issue('INVENTORY_POLICY_MAPPING', '政策表头必须完整、唯一且没有未知列', SHEET)
        return
    if tab.get('formulas'):
        for cell in tab['formulas']:
            issue('FORMULA_INPUT', '库存政策公式必须提供核对后的显式值，不接受缓存数量', SHEET+'!'+cell)
        return
    policies = []
    for ri, row in rows:
        try:
            obj = {field: parsed(row[header], typ) for header, (field, typ) in FIELDS.items()}
            for header in SOURCE_HEADERS:
                parsed(row[header], 'string')
            obj['provenance'] = dict(source_system='SYNTHETIC_POLICY_INPUT', source_record_id=row['来源记录'],
                source_version=row['来源版本'], observed_at=settings['planning_at'],
                import_batch_id='P2-'+tables['sha256'][:12], source_locator=SHEET+'!A'+str(ri+6))
            trial = {**candidate, 'rules':{**candidate.get('rules', {}), 'manufacturing_profile':PROFILE},
                     'inventory_policies':policies+[obj]}
            validate(trial)
            policies.append(obj)
            for header in HEADERS:
                field = FIELDS[header][0] if header in FIELDS else 'provenance/'+header
                parsed_value = obj[FIELDS[header][0]] if header in FIELDS else row[header]
                lineage(SHEET, ri, tab['headers'].index(header), row[header], parsed_value,
                        '/inventory_policies/'+str(len(policies)-1)+'/'+field, 'EXPLICIT_SYNTHETIC_POLICY_ASSUMPTION')
        except (KeyError, ValueError, TypeError, InputError) as exc:
            issue('INVENTORY_POLICY_MAPPING', str(exc), SHEET+'!A'+str(ri+6))
    if policies:
        candidate['inventory_policies'] = policies


def values(policy):
    """Preserve supplied values and provenance when authoring source workbooks."""
    provenance = policy['provenance']
    return [policy[field] for field, _ in FIELDS.values()] + [provenance['source_record_id'], provenance['source_version'],
        'Versioned synthetic policy assumption; source '+provenance['source_locator']]


def write_sheet(book, policies):
    """Write only the new policy sheet; callers own their workbook and bytes."""
    if SHEET in book.sheetnames:
        sheet = book[SHEET]
        if sheet.max_row > 5:
            raise ValueError('Existing policy source rows must not be overwritten')
        book.remove(sheet)
    sheet = book.create_sheet(SHEET)
    sheet.append(['P405 versioned synthetic inventory policy assumptions'])
    for _ in range(3):
        sheet.append([None])
    sheet.append(HEADERS)
    for policy in policies:
        sheet.append(values(policy))
    return sheet
