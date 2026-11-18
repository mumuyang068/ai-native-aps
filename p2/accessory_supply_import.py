"""An explicit P2 source option; never a PO, stock event or execution permission."""
import copy
from datetime import datetime
from decimal import Decimal

PROFILE = 'enterprise-accessory-supply-v1'
SHEET = '辅料补给选项'
FIELDS = {
    '供应来源编码': ('source_id', 'string'), '模拟启用': ('enabled', 'boolean'),
    '最早下单': ('earliest_order_at', 'datetime_offset'), '最晚下单': ('latest_order_at', 'datetime_offset'),
    '全期供应总量': ('total_capacity_quantity', 'decimal_string'), '数量单位': ('uom', 'string'),
    '事实类型': ('fact_kind', 'string'), '许可口径': ('permission', 'string'),
}
QC_HEADER = '辅料质检等待秒'
SOURCE_HEADERS = ['来源记录', '来源版本', '来源说明']
HEADERS = [*FIELDS, QC_HEADER, *SOURCE_HEADERS]


def apply(candidate, tables, settings, issue, lineage):
    """Run before canonical validation; retain all source cells or reject them."""
    from p2.preflight import parsed, column
    tab = tables['sheets'].get(SHEET)
    active = settings.get('rules.manufacturing_profile') == PROFILE
    if not tab:
        if active:
            issue('MISSING_ACCESSORY_SUPPLY_OPTION', '辅料补给模型需要明确来源选项表；不能推断来源或许可', SHEET)
        return
    rows = [(i, dict(zip(tab['headers'], values))) for i, values in enumerate(tab['rows'])
            if any(value not in (None, '') for value in values)]
    if not rows and not tab.get('formulas'):
        if active:
            issue('MISSING_ACCESSORY_SUPPLY_OPTION', '辅料补给来源选项不能为空', SHEET)
        return
    if not active:
        for ri, values in enumerate(tab['rows']):
            for ci, raw in enumerate(values):
                if raw not in (None, ''):
                    header = tab['headers'][ci] if ci < len(tab['headers']) else column(ci+1)
                    issue('UNSUPPORTED_ACCESSORY_SUPPLY_OPTION',
                          f'字段“{header}”需要显式 {PROFILE}；不能静默丢弃已填值',
                          SHEET+'!'+column(ci+1)+str(ri+6), 'CAPABILITY')
        for cell in tab.get('formulas', []):
            issue('UNSUPPORTED_ACCESSORY_SUPPLY_OPTION', '当前模型不支持辅料补给公式字段', SHEET+'!'+cell, 'CAPABILITY')
        return
    if len(tab['headers']) != len(set(tab['headers'])) or set(tab['headers']) != set(HEADERS):
        issue('ACCESSORY_SUPPLY_MAPPING', '辅料来源选项表头必须完整、唯一且没有未知列', SHEET)
        return
    if tab.get('formulas'):
        for cell in tab['formulas']:
            issue('FORMULA_INPUT', '来源条款必须使用核对后的显式值，不计算工作簿公式', SHEET+'!'+cell)
        return
    if len(rows) != 1:
        issue('ACCESSORY_SUPPLY_MAPPING', '仅支持单条明确的辅料来源选项，不得合并多个来源', SHEET)
        return
    ri, row = rows[0]
    def cell(header):
        return SHEET+'!'+column(tab['headers'].index(header)+1)+str(ri+6)
    values = {}
    bad = False
    for header in HEADERS:
        try:
            typ = FIELDS[header][1] if header in FIELDS else 'integer' if header == QC_HEADER else 'string'
            values[header] = parsed(row.get(header), typ)
        except (KeyError, ValueError, TypeError) as exc:
            issue('ACCESSORY_SUPPLY_MAPPING', f'字段“{header}”：{exc}', cell(header))
            bad = True
    if bad:
        return
    option = {field: values[header] for header, (field, _) in FIELDS.items()}
    source = next((s for s in candidate.get('supply_sources', []) if s.get('id') == option['source_id']), None)
    checks = [
        ('供应来源编码', source is not None, '来源必须与当前版本供应来源记录精确匹配'),
        ('模拟启用', option['enabled'] is False, '确认企业版本只声明停用的模拟选项；启用须创建独立草案'),
        ('最晚下单', datetime.fromisoformat(option['latest_order_at']) >= datetime.fromisoformat(option['earliest_order_at']), '最晚下单不得早于最早下单'),
        ('全期供应总量', Decimal(option['total_capacity_quantity']) > 0, '全规划期供应总量必须为正，不能把0当作未填写'),
        ('事实类型', option['fact_kind'] == 'SYNTHETIC_RESEARCH_ASSUMPTION', '本入口仅接受独立声明的研发合成来源'),
        ('许可口径', option['permission'] == 'SIMULATION_ONLY', '仅允许模拟，不代表批准、真实PO或执行许可'),
        (QC_HEADER, values[QC_HEADER] > 0, '辅料质检等待必须明确为正数；不能默认零等待'),
    ]
    if source:
        item = next((x for x in candidate.get('items', []) if x.get('id') == source.get('item_id')), None)
        checks.append(('数量单位', bool(item) and option['uom'] == item.get('base_uom'), '数量单位必须与来源物料的基本单位一致'))
    for header, valid, message in checks:
        if not valid:
            issue('ACCESSORY_SUPPLY_MAPPING', f'字段“{header}”：{message}', cell(header))
            bad = True
    if bad:
        return
    provenance = dict(source_system='SYNTHETIC_RESEARCH_ASSUMPTION', source_record_id=values['来源记录'],
        source_version=values['来源版本'], observed_at=settings['planning_at'],
        import_batch_id='P2-'+tables['sha256'][:12], source_locator=cell('供应来源编码'))
    option['provenance'] = copy.deepcopy(provenance)
    candidate['accessory_supply_option'] = option
    source['receiving_qc'] = dict(wait_seconds=values[QC_HEADER], provenance=copy.deepcopy(provenance))
    for header, (field, _) in FIELDS.items():
        lineage(SHEET, ri, tab['headers'].index(header), row[header], values[header],
                '/accessory_supply_option/'+field, 'EXPLICIT_SYNTHETIC_ACCESSORY_SOURCE_OPTION')
    lineage(SHEET, ri, tab['headers'].index(QC_HEADER), row[QC_HEADER], values[QC_HEADER],
            '/supply_sources/'+str(candidate['supply_sources'].index(source))+'/receiving_qc/wait_seconds',
            'EXPLICIT_PROPOSED_SOURCE_QC_RULE')
    for header in SOURCE_HEADERS:
        lineage(SHEET, ri, tab['headers'].index(header), row[header], values[header],
                '/retained/'+SHEET+'/'+header, 'RETAIN_SYNTHETIC_ACCESSORY_SOURCE_EVIDENCE')


def write_sheet(book, option, source):
    """Author cells only; never confirms, enables, imports or computes a source."""
    if source['id'] != option['source_id']:
        raise ValueError('Source identity mismatch')
    if SHEET in book.sheetnames:
        sheet = book[SHEET]
        if sheet.max_row > 5:
            raise ValueError('Existing accessory source rows must not be overwritten')
        book.remove(sheet)
    sheet = book.create_sheet(SHEET)
    sheet.append(['P406 independent synthetic research supply option; simulation only'])
    for _ in range(3):
        sheet.append([None])
    sheet.append(HEADERS)
    provenance = option['provenance']
    import json
    sheet.append([option[field] for field, _ in FIELDS.values()] + [source['receiving_qc']['wait_seconds'],
        provenance['source_record_id'], provenance['source_version'], json.dumps(provenance, ensure_ascii=False, sort_keys=True)])
    return sheet
