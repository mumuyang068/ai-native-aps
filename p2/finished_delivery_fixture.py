"""Prepare independently versioned P403 synthetic source workbooks, offline only.

This emits source cells for the existing import/confirmation pipeline. It never
imports, confirms, saves a version, changes a plan, calls a solver or invents output.
"""
from io import BytesIO
import json


def workbook_bytes(data):
    from openpyxl import Workbook
    from p2.preflight import PROFILE
    book = Workbook()
    book.remove(book.active)
    version = data['snapshot_id']
    def source(ref):
        return [ref, version, 'P403 independent synthetic source slice; see 使用说明 for retained facts and changed assumptions; no enterprise execution evidence']
    def tab(name, heads, rows):
        w = book.create_sheet(name)
        w.append(['P403 independent synthetic finished-garment source slice'])
        for _ in range(3):
            w.append([None])
        w.append(heads)
        for row in rows:
            w.append(row)
    def encoded(value):
        return json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value
    settings = []
    for key in PROFILE['settings']:
        try:
            value = data
            for part in key.split('.'):
                value = value[part]
        except KeyError:
            if key == 'p2.allow_demo_customer':
                value = False
            elif key == 'p2.no_unlisted_supply_or_reservations':
                value = True
            else:
                continue
        settings.append([key, encoded(value), *source(key)])
    tab('工厂参数', ['字段路径', '确认值', '来源记录', '来源版本', '来源说明'], settings)
    extra_fields = {'产能模型': 'capacity_model', '窗口产能': 'window_capacity_quantity', '产能单位': 'capacity_uom',
                    '最大提前秒': 'maxearly_seconds', '最大制造批量': 'manufacturing_maximum_quantity',
                    '资源角色': 'role', '所属人员池': 'pool_id'}
    for name, conf in PROFILE['sheets'].items():
        heads = [c['header'] for c in conf['columns']] + conf['extras'] + conf['source_columns'] + conf.get('optional_headers', [])
        rows = []
        for obj in data[conf['entity']]:
            children = obj[conf['nested']] if conf['nested'] else [None]
            for child in children:
                values = {c['header']: encoded(obj.get(c['field']) if '.' not in c['field'] else child.get(c['field'].split('.', 1)[1])) for c in conf['columns']}
                values.update({h: obj.get(f) for h, f in extra_fields.items()})
                if name == '订单':
                    values.update(客户编码=obj['customer_id'], 联系人='P403 synthetic slice reviewer')
                if name == '库存':
                    values.update({h: 0 for h in conf['extras']})
                if name == '工艺路线':
                    rq = child.get('operator_requirement', {})
                    values.update(人员池编码=rq.get('pool_id'), 必需技能=rq.get('skill_id'), 所需人数=rq.get('quantity'))
                values.update(dict(zip(conf['source_columns'], source(conf['entity'] + '/' + obj['id']))))
                rows.append([values.get(h) for h in heads])
        tab(name, heads, rows)
    sources = {s['id']: s for s in data['supply_sources']}
    for name, conf in PROFILE['auxiliary'].items():
        heads = conf['headers'] + conf.get('optional_headers', [])
        rows = []
        if name == '技能主档':
            rows = [[s['id'], *source('skills/' + s['id'])] for s in data['skills']]
        elif name == '人员技能资格':
            rows = [[s['resource_id'], s['skill_id'], s['priority'], s['effective_from'], s['effective_to'], *source(s['resource_id'] + '/' + s['skill_id'])] for s in data['resource_skills']]
        elif name == '停工日历':
            rows = [[c['id'], c['timezone'], *source(c['id'])] for c in data['calendars'] if not c['available_intervals']]
        elif name == '库存质量状态':
            for inv in data['inventory_positions']:
                q = inv.get('quality_availability')
                if q:
                    rows.append([inv['id'], q['inspection_pending_quantity'], q['released_quantity'],inv['uom'],q['release_reference'], q['evidence_kind'],q['effective_at'], *source('inventory-quality/' + inv['id'])])
        elif name == '采购台账':
            for po in data['existing_orders']:
                values = dict(采购单号=po['id'], 物料编码=po['item_id'], 供应商编码=po['supplier_id'], 订购量=po['ordered_quantity'],
                              已收量=po['received_quantity'], 未收量=po['remaining_quantity'], 单位=po['uom'], 状态=po['source_status'],
                              预计到货=po['expected_receipt_at'], 库存已含收货=po['inventory_includes_receipt'], 供应来源编码=po['supply_source_id'],
                              下单时间=po['ordered_at'], 计划采购开始=po.get('planned_start_at'),
                              质检等待秒=sources[po['supply_source_id']]['receiving_qc']['wait_seconds'], 质检后可用时间=po['available_at'])
                values.update(dict(zip(['来源记录', '来源版本', '来源说明'], source(po['id']))))
                rows.append([values.get(h) for h in heads])
        tab(name, heads, rows)
    tab('使用说明', ['项目', '内容'], [[a['id'], a['value']] for a in data['assumptions']])
    stream = BytesIO()
    book.save(stream)
    return stream.getvalue()
