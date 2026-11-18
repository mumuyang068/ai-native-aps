"""Explicit forecast cells in the existing P2 mapping/confirmation pipeline."""
from demand_forecast import PROFILE, apply_net_forecasts, enabled

SHEET = '预测需求'
FIELDS = {
    '预测编码': ('id', 'string'),
    '产品物料编码': ('item_id', 'string'),
    '工厂编码': ('location_id', 'string'),
    '客户编码': ('customer_id', 'string'),
    '数量单位': ('uom', 'string'),
    '期间开始': ('bucket_start', 'datetime_offset'),
    '期间结束': ('bucket_end', 'datetime_offset'),
    '原始预测量': ('gross_quantity', 'decimal_string'),
    '预测确认版本': ('version_id', 'string'),
    '订单抵扣策略': ('policy_id', 'string'),
    '预测覆盖口径': ('coverage', 'string'),
    '来源证据类型': ('evidence_status', 'string'),
    '净预测需求时点': ('net_demand.due_at', 'datetime_offset'),
    '净预测优先级': ('net_demand.priority', 'integer'),
    '净预测最小交付量': ('net_demand.minimum_shipment', 'decimal_string'),
    '净预测延期搜索秒': ('net_demand.maximum_lateness_seconds', 'integer'),
}
SOURCE_HEADERS = ['来源记录', '来源版本', '来源说明']
HEADERS = [*FIELDS, *SOURCE_HEADERS]
ORDER_FIELDS = {'需求类型': ('kind', 'string'), '预测来源编码': ('forecast_id', 'string')}


def apply(candidate, tables, settings, issue, lineage):
    """Called before canonical validation; no solver or store side effects."""
    from p2.preflight import parsed, assign
    tabs = tables['sheets']; active = enabled({'rules':{'manufacturing_profile':settings.get('rules.manufacturing_profile')}})
    def records(name):
        tab = tabs.get(name)
        return [] if not tab else [(i, dict(zip(tab['headers'], row))) for i, row in enumerate(tab['rows']) if any(v not in (None, '') for v in row)]
    if not active:
        if records(SHEET):
            issue('UNSUPPORTED_FORECAST', '预测资料需要显式 forecast-net-demand-v1；不静默丢弃', SHEET, 'CAPABILITY')
        for i, row in records('订单'):
            if any(row.get(h) not in (None, '') for h in ORDER_FIELDS):
                issue('UNSUPPORTED_FORECAST', '显式需求类型需要预测profile', '订单!A' + str(i + 6), 'CAPABILITY')
        return
    orders = {o['id']: o for o in candidate.get('demand_lines', [])}
    for i, row in records('订单'):
        try:
            obj = orders.get(row.get('记录编码'))
            if obj is None:
                raise ValueError('需求行身份不存在')
            value = parsed(row.get('需求类型'), 'string')
            if value != 'FIRM_ORDER' or row.get('预测来源编码') not in (None, ''):
                raise ValueError('原始订单表仅接受 FIRM_ORDER；NET_FORECAST 由已确认预测政策独立生成')
            obj['kind'] = value
            lineage('订单', i, tabs['订单']['headers'].index('需求类型'), row['需求类型'], value, '/demand_lines/' + str(candidate['demand_lines'].index(obj)) + '/kind', 'EXPLICIT_FIRM_ORDER_KIND')
            if not obj.get('customer_id'):
                raise ValueError('缺少经现有客户映射确认的客户身份')
        except (KeyError, ValueError, TypeError) as exc:
            issue('FORECAST_MAPPING', str(exc), '订单!A' + str(i + 6))
    tab = tabs.get(SHEET)
    if not tab:
        issue('MISSING_SHEET', '预测profile需要显式预测资料表', SHEET)
        return
    if len(tab['headers']) != len(set(tab['headers'])) or set(tab['headers']) != set(HEADERS):
        issue('MAPPING_CONFIRMATION_REQUIRED', '预测资料表头不完整、重复或包含未知列', SHEET)
        return
    if tab.get('formulas'):
        issue('FORMULA_INPUT', '预测公式必须提供核对后的显式值，不计算工作簿公式', SHEET)
        return
    candidate['forecasts'] = []
    for i, row in records(SHEET):
        try:
            obj = {}
            for header, (field, typ) in FIELDS.items():
                value = parsed(row.get(header), typ)
                assign(obj, field, value)
                lineage(SHEET, i, tab['headers'].index(header), row.get(header), value,
                        '/forecasts/' + str(len(candidate['forecasts'])) + '/' + field.replace('.', '/'), 'EXPLICIT_FORECAST_SOURCE_OR_CONFIRMED_POLICY')
            for header in SOURCE_HEADERS:
                parsed(row.get(header), 'string')
                lineage(SHEET, i, tab['headers'].index(header), row[header], row[header], '/retained/' + SHEET + '/' + header, 'RETAIN_FORECAST_SOURCE_EVIDENCE')
            obj['provenance'] = dict(source_system='ENTERPRISE_XLSX', source_record_id=row['来源记录'], source_version=row['来源版本'],
                observed_at=settings['planning_at'], import_batch_id='P2-' + tables['sha256'][:12], source_locator=SHEET + '!A' + str(i + 6))
            candidate['forecasts'].append(obj)
        except (KeyError, ValueError, TypeError) as exc:
            issue('FORECAST_MAPPING', str(exc), SHEET + '!A' + str(i + 6))
    try:
        converted = apply_net_forecasts(candidate)
        candidate['demand_lines'] = converted['demand_lines']
    except (KeyError, ValueError, TypeError) as exc:
        issue('FORECAST_NETTING', str(exc), SHEET)
