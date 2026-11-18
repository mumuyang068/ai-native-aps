import copy
import re


class ContextError(ValueError):
    pass


def bind_context(plan_id, target, view, selection, question, expected_run=None):
    projection = view.get('order_evidence') or {}
    run = view.get('run') or {}
    result = view.get('result') or {}
    enterprise = (view.get('context') or {}).get('enterprise_source') or {}
    if (run.get('state') != 'SUCCEEDED' or not run.get('solve_run_id')
            or projection.get('solve_run_id') != run['solve_run_id']
            or projection.get('source_result_hash') != result.get('result_hash')
            or enterprise.get('plan_id') != plan_id
            or (expected_run and expected_run != run['solve_run_id'])):
        raise ContextError('DIAGNOSIS_SCOPE_MISMATCH: 当前计划没有可绑定的同一运行证据，请刷新。')
    orders = projection['orders']
    selection = copy.deepcopy(selection or {})
    if set(selection) - {'type', 'id', 'order_id'}:
        raise ContextError('INVALID_SELECTION: 选择上下文字段无效。')
    kind, ref = selection.get('type'), selection.get('id')
    if selection and (kind not in {'order', 'operation', 'resource'} or not isinstance(ref, str)):
        raise ContextError('INVALID_SELECTION: 请选中订单、工序或资源。')
    def aliases(order):
        # Research demand rows can have no external order reference. Such an
        # absence is not an alias and must never enter a regex or match all text.
        return {v for v in (order['demand_id'], order.get('order_ref'))
                if isinstance(v, str) and v.strip()}
    if kind == 'order':
        candidates = [o for o in orders if ref in aliases(o)]
    elif kind == 'operation':
        candidates = [o for o in orders if any(e['id'] == ref for e in o['operations'])]
    elif kind == 'resource':
        candidates = [o for o in orders if any(e['record'].get('resource') == ref for e in o['resources'])]
    else:
        candidates = orders
    if not candidates:
        raise ContextError('SELECTION_NOT_IN_RUN: 所选对象没有当前运行的真实订单关联。')
    origin = selection.get('order_id')
    if origin:
        chosen = [o for o in candidates if origin in aliases(o)]
        if len(chosen) != 1:
            raise ContextError('SELECTION_ORDER_MISMATCH: 所选对象与订单没有可核实的原生关联。')
        candidates = chosen
    named = [o for o in orders if any(re.search(r'(?<![A-Za-z0-9_-])' + re.escape(a) + r'(?![A-Za-z0-9_-])', question) for a in aliases(o))]
    if named:
        matches = [o for o in candidates if o in named]
        if not matches:
            raise ContextError('SELECTION_ORDER_MISMATCH: 问题中的订单与当前对象不一致，请重新选择。')
        candidates = matches
    order_id = candidates[0]['demand_id'] if len(candidates) == 1 else None
    scope = {'plan_id': plan_id, 'target': target, 'run_id': run['solve_run_id'],
             'scenario_id': projection['scenario_id'], 'scenario_revision': projection['scenario_revision'],
             'data_version_id': enterprise.get('data_version_id'), 'result_hash': result['result_hash'],
             'projection_hash': projection['projection_hash']}
    return {'scope': scope, 'selection': selection, 'order_id': order_id,
            'candidates': [{'demand_id': o['demand_id'], 'order_ref': o['order_ref']} for o in candidates],
            'bundle': copy.deepcopy(view)}
