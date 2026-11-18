import copy
import hashlib
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

CORE = ('get_order_delivery', 'trace_order_tasks', 'get_order_constraints')
EXTRA = ('get_operation_details', 'get_purchase_tasks', 'get_resource_loads', 'get_material_movements')
READ_TOOLS = CORE + EXTRA
LABELS = {'get_order_delivery': '读取订单交付', 'trace_order_tasks': '追踪制造与采购关联',
          'get_order_constraints': '核对订单约束', 'get_operation_details': '检查关联工序',
          'get_purchase_tasks': '检查关联采购', 'get_resource_loads': '检查资源占用',
          'get_material_movements': '检查物料流转'}
CONSTRAINTS = {'purchasing lead time': '采购提前期限制', 'capacity overload': '资源产能约束',
               'await supply': '等待供应', 'operation before current': '早于规划时点的提前期限制',
               'operation before fence': '释放围栏限制'}


class ToolError(ValueError):
    pass


def read_tool(bound, name):
    if name not in READ_TOOLS:
        raise ToolError('DIAGNOSIS_READ_ONLY: 诊断仅开放只读工具。')
    projection = bound['bundle']['order_evidence']
    order = next((o for o in projection['orders'] if o['demand_id'] == bound['order_id']), None)
    if not order:
        raise ToolError('ORDER_REQUIRED: 请先明确调查订单。')
    facts = []
    zone=bound['bundle'].get('context',{}).get('timezone','Asia/Shanghai')
    def when(value):
        if not value:return '未排定'
        try:
            dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
            return (dt.replace(tzinfo=ZoneInfo(zone)) if dt.tzinfo is None else dt.astimezone(ZoneInfo(zone))).strftime('%Y-%m-%d %H:%M')
        except ValueError:return str(value)
    def add(kind, data, source, text, action=None):
        if source.get('solve_run_id') != bound['scope']['run_id']:
            raise ToolError('EVIDENCE_RUN_MISMATCH: 来源不属于固定运行。')
        value = {'kind': kind, 'data': copy.deepcopy(data), 'source': copy.deepcopy(source), 'text': text, 'action': action}
        value['id'] = 'EV-' + hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
        facts.append(value)
    def action(kind, ref):
        return {'type': kind, 'id': ref, 'run_id': bound['scope']['run_id'], 'order_id': order['demand_id']}
    if name == 'get_order_delivery':
        delivery = order['delivery']
        source = (order['demand_records'][0]['source'] if order['demand_records'] else
                  {'solve_run_id': bound['scope']['run_id'], 'artifact': 'PlanResult.json',
                   'locator': 'deliveries/' + order['demand_id'], 'sha256': bound['scope']['result_hash']})
        add('DELIVERY', delivery, source,
            f"订单 {order['order_ref']}：要求交付 {when(delivery['due_at'])}，预计交付 {when(delivery.get('planned_delivery_at'))}；"
            + {'LATE': '本次预计延期', 'ON_TIME': '本次预计按期','UNPLANNED':'本次未排定','SHORT':'本次缺量'}.get(delivery['status'], delivery['status'])
            + (f"；计划交付 {delivery['planned_quantity']} { {'piece':'件','m':'米'}.get(delivery['uom'],delivery['uom'])}，欠交 {delivery['unfulfilled_quantity']} { {'piece':'件','m':'米'}.get(delivery['uom'],delivery['uom'])}" if all(k in delivery for k in ['planned_quantity','unfulfilled_quantity','uom']) else '') + '。',
            action('focus_order', order['demand_id']))
        required = ['demand']
    else:
        groups, kind, required = {
            'trace_order_tasks': (['manufacturing', 'operations', 'purchases'], 'RELATION', ['manufacturingorder', 'workorder', 'purchaseorder']),
            'get_order_constraints': (['constraints'], 'ENGINE_CONSTRAINT', ['constraint']),
            'get_operation_details': (['operations'], 'OPERATION', ['workorder']),
            'get_purchase_tasks': (['purchases'], 'PURCHASE', ['purchaseorder']),
            'get_resource_loads': (['resources'], 'RESOURCE_RELATION', ['operationplanresource']),
            'get_material_movements': (['materials'], 'MATERIAL_RELATION', ['operationplanmaterial'])}[name]
        for group in groups:
            for entry in order[group]:
                r, source = entry['record'], entry['source']
                ref = entry.get('engine_ref') or r.get('operationplan') or source.get('native_record_id')
                destination = action('focus_operation', entry['id']) if group == 'operations' else action('focus_order', order['demand_id'])
                if group == 'constraints':
                    owner=r.get('owner','未提供');purchase=re.fullmatch(r'Purchase (.+) @ (.+) from (.+)',owner)
                    subject=f'物料 {purchase[1]}，供应商 {purchase[3]}' if purchase else owner
                    text = f"本次引擎报告{CONSTRAINTS.get(r.get('name'), '其他规划约束')}，对象：{subject}。"
                elif group == 'resources':
                    text = f"关联资源 {r.get('resource')}，任务 {r.get('operationplan')}；此记录证明资源占用关联，不证明资源是延期原因。"
                elif group == 'operations':
                    times = entry.get('task') or entry.get('times') or {}
                    text = f"关联工序 {str(r.get('operation')).split(':')[-1]}；{when(times.get('start_at', times.get('startdate', r.get('startdate'))))} 至 {when(times.get('end_at', times.get('enddate', r.get('enddate'))))}。"
                elif group == 'purchases':
                    text = f"关联采购任务 {ref}，物料 {r.get('item')}，供应商 {r.get('supplier', '未提供')}；任务总量 {r.get('quantity', '未提供')} {entry.get('item_uom') or (entry.get('task') or {}).get('uom', '单位未提供')}，不等于本订单分配量。"
                elif group == 'materials':
                    text = f"关联物料 {r.get('item')}，任务 {r.get('operationplan')}，带符号任务流转量 {r.get('quantity')} {entry.get('item_uom', '单位未提供')}；不等于本订单分配量。"
                else:
                    text = f"关联制造任务 {ref}，使用原生需求关联；任务总量不等于单笔订单分配量。"
                add(kind, r, source, text, destination)
    missing = sorted(set(required) & set(projection.get('missing_tables', [])))
    return {'tool': name, 'label': LABELS[name], 'coverage': 'PARTIAL' if missing and facts else 'UNAVAILABLE' if missing else 'PRESENT' if facts else 'EMPTY',
            'missing_tables': missing, 'evidence': facts, 'order_id': order['demand_id'],
            'scope': copy.deepcopy(bound['scope'])}
