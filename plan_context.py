"""Read-only, source-linked business facts; no solver or enterprise policy inference."""
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from planning_adapter import decimal_text

SUPPORTED_OPERATIONS={
 'query_order_evidence':{'supported':True,'label':'查询订单的原生关联、交付与约束证据；不推断无证据的原因'},
 'query_compare':{'supported':True,'label':'查询当前计划、比较已计算的草案'},
 'change_due_date':{'supported':True,'label':'修改订单交期，提出基于基线的独立草案'},
 'add_urgent_order':{'supported':True,'label':'新增急单草案，关键信息必须确认'},
 'run_scenario':{'supported':True,'label':'人工确认后运行真实引擎，不自动重算'},
 'change_shifts':{'supported':False,'label':'班次、工作日历编辑尚未接入'},
 'change_resource_capacity':{'supported':False,'label':'资源产能编辑尚未接入'},
 'change_overtime_policy':{'supported':False,'label':'加班规则编辑尚未接入'},
 'publish_plan':{'supported':False,'label':'不提供正式计划修改、审批和发布'}
 ,'procurement_cost':{'supported':False,'label':'缺少供应价格与成本模型，不支持成本约束或优化'}
}

def supported_operations(data):
    from planning_capabilities import supports,profile_id
    value={k:dict(v) for k,v in SUPPORTED_OPERATIONS.items()}
    for key,feature in [('change_due_date','due_change'),('add_urgent_order','urgent_order')]:
        value[key]['supported']=supports(profile_id(data),'actions',feature)
        if not value[key]['supported']:value[key]['label']='当前模型尚未认证此草案变更。'
    return value

def quantities(rows,quantity_key='quantity'):
    grouped=defaultdict(Decimal)
    for row in rows:grouped[row['uom']]+=Decimal(row[quantity_key])
    return [{'quantity':decimal_text(q),'uom':unit} for unit,q in sorted(grouped.items())]

def manufacturing_by_item(rows):
    grouped=defaultdict(Decimal)
    for row in rows:grouped[(row['item_id'],row['location_id'],row['uom'])]+=Decimal(row['quantity'])
    return [{'item_id':item,'location_id':location,'uom':uom,'quantity':decimal_text(q)} for (item,location,uom),q in sorted(grouped.items())]

def build_plan_context(data,view,input_artifact_ref=None):
    from planning_capabilities import profile_id
    from accessory_supply import enabled as accessory_enabled, summary as accessory_summary
    from network_distribution import enabled as network_enabled
    from confirmed_supply import receipt_windows
    result=view.get('result');run=view.get('run',{})
    interpretation=view.get('interpretation') or {}
    coverage=[('reported_material_onhand_nonnegative' if x=='material_balance' else x)
              for x in interpretation.get('coverage',(result or {}).get('assessment',{}).get('coverage',[]))]
    validation_scope=interpretation.get('validation_scope') or {
        'checked':[],'not_checked':['current_independent_validation'],'unsupported':[],
        'material_status':'NOT_CHECKED','input_readback_status':'NOT_CHECKED'}
    artifact=input_artifact_ref or ('evidence/runs/'+run['solve_run_id']+'/canonical_input.json' if run.get('solve_run_id') else 'fixtures/baseline.json')
    foundation=data.get('foundation_projection')
    source_tables=foundation.get('source_tables',{}) if foundation else {}
    odoo_capture=next((r['odoo_bundle']['capture'] for r in source_tables.get('locations',[]) if r.get('odoo_bundle')),None)
    data_kind=('SYNTHETIC_FACTORY' if all(r.get('fact_kind')=='SYNTHETIC_FACT' for rows in source_tables.values() for r in rows) else 'CANONICAL_ENTERPRISE_FACTS') if foundation else data['site']['provenance']['source_system']
    def source(pointer,kind='CANONICAL_INPUT'):return {'kind':kind,'artifact_ref':artifact,'pointer':pointer,'data_kind':data_kind}
    rules=data.get('rules',{})
    overtime_value=rules.get('allow_overtime')
    overtime={'policy':'EXPLICITLY_FORBIDDEN' if overtime_value is False else 'EXPLICITLY_PERMITTED' if overtime_value is True else 'UNSPECIFIED',
              'scope':'CURRENT_SCENARIO','arrangement':'NO_SEPARATE_OVERTIME_DECLARATION',
              'source':source('/rules/allow_overtime') if isinstance(overtime_value,bool) else None,
              'explanation':'本次计划的明确规则；不表示企业永久禁止加班。' if isinstance(overtime_value,bool) else '未提供明确加班规则；不能从日历缺少加班时段推断禁令。'}
    constraints=[]
    labels={'finite_material':'物料受限','finite_capacity':'有限产能','allow_overtime':'允许加班','freeze_existing':'冻结已有供应订单'}
    for key,label in labels.items():
        if key in rules:constraints.append({'id':key,'label':label,'value':rules[key],'scope':'CURRENT_SCENARIO','source':source('/rules/'+key,'PLAN_RULE')})
    for i,row in enumerate(data['supply_sources']):
        constraints.append({'id':'supply:'+row['id'],'label':'采购提前期与批量规则','value':{k:row[k] for k in ['item_id','supplier_id','lead_time_seconds','minimum_order_quantity','order_multiple'] if k in row},'scope':'CURRENT_SCENARIO','source':source('/supply_sources/'+str(i))})
    resources=[{**{k:r[k] for k in ['id','parallel_units','availability_calendar_id','capacity_model','window_capacity_quantity','capacity_uom'] if k in r},'source':source('/resources/'+str(i))} for i,r in enumerate(data['resources'])]
    calendars=[{**r,'source':source('/calendars/'+str(i))} for i,r in enumerate(data['calendars'])]
    constraints.append({'id':'resource_calendars','label':'资源可用日历','value':[r['id'] for r in calendars],'scope':'PLANNING_HORIZON','source':source('/calendars')})
    original_demands={r['id']:r for r in source_tables.get('demand_lines',[])}
    source_indices={r['id']:i for i,r in enumerate(source_tables.get('demand_lines',[]))}
    foundation_firm=profile_id(data)=='foundation-planning-projection-v1'
    if foundation_firm:
        from p22.foundation_projection import firm_order_refs
        source_references=firm_order_refs(data)
    orders=[]
    for i,r in enumerate(data['demand_lines']):
        order={**{k:r[k] if k in r else original_demands[r['id']][k] for k in ['id','item_id','location_id','quantity','uom','due_at','priority']},'source':source('/demand_lines/'+str(i))}
        order['order_ref']=source_references[r['id']] if foundation_firm else r['order_ref']
        if foundation_firm:
            order['order_ref_source']=source('/foundation_projection/source_tables/demand_lines/'+str(source_indices[r['id']])+'/order_ref') if order['order_ref'] is not None else None
        orders.append(order)
    all_deliveries=result.get('deliveries',[]) if result else []
    from demand_forecast import enabled as forecast_enabled,net_forecasts
    forecast_active=forecast_enabled(data)
    forecast_summary=net_forecasts(data) if forecast_active else None
    forecast_ids={d['id'] for d in data['demand_lines'] if d.get('kind')=='NET_FORECAST'} if forecast_active else set()
    inquiry_ids=set()
    if foundation_firm and foundation.get('professional_overlay'):
        from p22.professional_policy import validate_overlay
        changes=validate_overlay(data)['changes']
        forecast_summary=changes.get('netting')
        forecast_ids={d['id'] for d in (forecast_summary or {}).get('generated_demands',[])}
        inquiry=changes.get('inquiry')
        inquiry_ids={inquiry['added_demand']['id']} if inquiry else set()
    orders=[o for o in orders if o['id'] not in forecast_ids|inquiry_ids]
    deliveries=[d for d in all_deliveries if d['demand_id'] not in forecast_ids|inquiry_ids]
    purchases=result.get('purchase_suggestions',[]) if result else []
    manufacturing=result.get('manufacture_suggestions',[]) if result else []
    existing=result.get('existing_manufacturing_orders',[]) if result else []
    risk_counts={status:sum(r['status']==status for r in deliveries) for status in ['ON_TIME','LATE','SHORT','UNPLANNED']}
    result_ref='evidence/runs/'+run['solve_run_id']+'/PlanResult.json' if result else None
    network={'profile':data['rules']['manufacturing_profile'],'locations':data['locations'],'routes':data['distribution_lanes'],'lead_time_option':data['distribution_lead_time_option'],'declared_inventory':data['inventory_positions'],'confirmed_supply_windows':receipt_windows(data),'endpoint':'MATERIAL_REPLENISHMENT_NOT_EXECUTION','pegging_status':'UNSUPPORTED'} if network_enabled(data) else None
    return {**({'network_supply':network} if network else {}),**({k:interpretation[k] for k in ['distribution','distribution_validation']} if interpretation.get('distribution_validation') else {}),**({'qualified_resource_validation':interpretation['qualified_resource_validation']} if interpretation.get('qualified_resource_validation') else {}),**({k:view['interpretation'][k] for k in ['execution_checkpoint','checkpoint_validation']} if view.get('interpretation',{}).get('execution_checkpoint') else {}),**({k:view['interpretation'][k] for k in ['wip','wip_validation']} if view.get('interpretation',{}).get('wip') else {}),**({k:view['interpretation'][k] for k in ['procurement','procurement_validation']} if view.get('interpretation',{}).get('procurement') else {}),**({'accessory_supply':{**accessory_summary(data),'validation':interpretation.get('accessory_supply')}} if accessory_enabled(data) else {}),'context_version':'1.1','manufacturing_profile':profile_id(data),'data_kind':'REAL_ODOO_ENTERPRISE_FACTS' if odoo_capture else 'SYNTHETIC_FACTORY',
            'plan_version':{'kind':'COMPUTED_SCENARIO' if result else 'UNCOMPUTED_SCENARIO','scenario_id':data['scenario_id'],
                'scenario_revision':data['scenario_revision'],'snapshot_id':data['snapshot_id'],'solve_run_id':run.get('solve_run_id'),
                'input_hash':result.get('input_hash') if result else None,'result_hash':result.get('result_hash') if result else None,
                'computed_at':result.get('computed_at') if result else None,'result_source':result_ref},
            'calculation_state':run.get('state','NOT_RUN'),'failure_code':run.get('failure_code'),'worker_state':run.get('worker_state'),'planning_at':data['planning_at'],'timezone':data['site']['timezone'],'horizon':data['horizon'],
            'forecast_summary':forecast_summary,'net_forecast_deliveries':[d for d in all_deliveries if d['demand_id'] in forecast_ids],
            'net_forecast_demands':[d for d in data['demand_lines'] if d['id'] in forecast_ids],
            'inquiry_summary':{'demands':[d for d in data['demand_lines'] if d['id'] in inquiry_ids],
                'formal_order_created':False,'formal_promise':False,'execution_authorized':False},
            'inquiry_deliveries':[d for d in all_deliveries if d['demand_id'] in inquiry_ids],
            'order_summary':{'order_count':len({r['order_ref'] for r in orders if r['order_ref'] is not None}),'line_count':len(orders),'quantities':quantities(orders),'orders':orders,'deliveries':deliveries,'risk_counts':risk_counts},
            'purchase_status':{'state':'PROPOSED' if result else 'NOT_COMPUTED','actual_execution_state':'NOT_CONNECTED','suggestions':purchases,'totals':quantities(purchases),'source':result_ref},
            'manufacturing_status':{'state':'EXISTING_AND_PROPOSED' if existing else 'PROPOSED' if result else 'NOT_COMPUTED','actual_execution_state':'SOURCE_VERSION_ACTUALS_AVAILABLE' if odoo_capture else 'NOT_CONNECTED','actual_source':({'system':'ODOO','company_id':odoo_capture['company_id'],'source_as_of':odoo_capture['captured_at'],'source_sha256':odoo_capture['source_sha256']} if odoo_capture else None),'suggestions':manufacturing,'totals':quantities(manufacturing+existing),
                'existing_orders':existing,'existing_totals':quantities(existing),'proposed_totals':quantities(manufacturing),
                'by_item':manufacturing_by_item(manufacturing+existing),'totals_scope':'MANUFACTURING_TASK_QUANTITIES_NOT_CUSTOMER_FG_TOTAL',
                'expected_completion_at':max((r['end_at'] for r in manufacturing+existing),default=None),'source':result_ref},
            'resource_calendars':{'resources':resources,'calendars':calendars,'scope':'PLANNING_HORIZON','overtime_inference':'Calendar availability alone does not establish an overtime prohibition.'},
            'overtime':overtime,'effective_constraints':constraints,'supported_operations':supported_operations(data),
            'assessment':({**result.get('assessment',{}),'coverage':coverage,
                           'validation_scope':validation_scope} if result else None),
            'validation_scope':validation_scope,
            'engine_provenance':view.get('interpretation',{}).get('engine_provenance'),
            'infeasibility_proven':False}

def quantity_text(rows):
    units={'piece':'件','m':'米'}
    return '、'.join(r['quantity']+' '+units.get(r['uom'],r['uom']) for r in rows) or '无'

def time_text(value):return value[:16].replace('T',' ') if value else '未排定'

def constraint_text(context):
    parts=[]
    values={r['id']:r['value'] for r in context['effective_constraints']}
    if values.get('finite_material'):parts.append('物料受限')
    if values.get('finite_capacity'):parts.append('有限产能')
    policy=context['overtime']['policy']
    parts.append('本次计划明确禁止加班（来源：计划规则）' if policy=='EXPLICITLY_FORBIDDEN' else '本次规则允许加班' if policy=='EXPLICITLY_PERMITTED' else '没有明确的禁加班规则，日历不能单独证明禁令')
    parts.append('资源按已配置日历运行（来源：资源日历）')
    return '；'.join(parts)

def business_summary(context):
    order=context['order_summary']
    lines=[f"当前计划有 {order['order_count']} 笔订单、{order['line_count']} 个订单行，共 {quantity_text(order['quantities'])}。"]
    if context.get('forecast_summary'):
        lines.append('另有显式净预测计划需求；不计入真实客户订单数，gross/consumed/net见需求规划来源。')
    if context['calculation_state']=='FAILED':
        failure=str(context.get('failure_code') or '请检查计算日志')
        if context.get('manufacturing_profile')=='foundation-planning-projection-v1':
            reason=('已有工单的原生占用时间与冻结安排不一致，保护检查已停止计算。' if 'PROTECTED_MO_DRIFT' in failure else
                    '模型导入在提交原生计算前被中止，尚未生成计划结果。' if 'LOCAL_INPUT_LOADING_INTERRUPTED' in failure else
                    '模型载入或原生计算未完成，尚无可用结果。')
            lines.append(reason+'完整原因与运行身份可在“来源”中查看；本次未证明业务无解。')
        else:lines.append('本次计算失败，未返回有效计划，未证明无解。原因记录：'+failure+'。')
    elif context['calculation_state']=='QUEUED' and context.get('worker_state')=='PAUSED_RECOVERY_REQUIRED':
        lines.append('计算队列已暂停，需要核对已有引擎任务是否结束；本次尚未开始计算，不会盲目加载新数据。')
    elif context['calculation_state']!='SUCCEEDED':
        lines.append('该情景尚未计算完成，暂无采购建议、预计完成时间和交期风险结论。')
    else:
        if context.get('manufacturing_status',{}).get('actual_execution_state')=='SOURCE_VERSION_ACTUALS_AVAILABLE':
            status=context['manufacturing_status']
            lines.append('已有制造安排 '+quantity_text(status['existing_totals'])+'，新增制造建议 '+quantity_text(status['proposed_totals'])+'，建议采购 '+quantity_text(context['purchase_status']['totals'])+'。')
        elif context.get('manufacturing_profile') in {'finished-garment-confirmed-v1','enterprise-forecast-fg-v1','enterprise-accessory-supply-v1'}:
            production='、'.join(r['item_id']+' @ '+r['location_id']+' '+quantity_text([r]) for r in context['manufacturing_status']['by_item']) or '无'
            lines.append('建议采购 '+quantity_text(context['purchase_status']['totals'])+'，各阶段制造：'+production+'；不同阶段不相加为成品数量。')
        else:lines.append('建议采购 '+quantity_text(context['purchase_status']['totals'])+'，建议制造 '+quantity_text(context['manufacturing_status']['totals'])+'。')
        lines.append('制造预计完成：'+time_text(context['manufacturing_status']['expected_completion_at'])+'（'+context['timezone']+'）。')
        counts=order['risk_counts']
        lines.append(f"交期：{counts['ON_TIME']} 个订单行按期、{counts['LATE']} 个延期、{counts['SHORT']+counts['UNPLANNED']} 个缺量或未排定。")
        for row in order['deliveries']:
            if row['status']!='ON_TIME':lines.append(row['demand_id']+' 预计交付 '+time_text(row['planned_delivery_at'])+'，要求 '+time_text(row['due_at'])+'。')
        if context['assessment'] and context['assessment']['status']=='BLOCKED':lines.append('结果存在已核对约束违规，不能当作可用计划。')
        if counts['LATE'] or counts['SHORT'] or counts['UNPLANNED']:lines.append('这仅说明该次返回方案的表现，未证明无解。')
        lines.append('实际执行见当前确认版本的 Odoo 来源记录；未来计划不代表已经执行。' if context.get('manufacturing_status',{}).get('actual_execution_state')=='SOURCE_VERSION_ACTUALS_AVAILABLE' else '采购和制造均为建议，执行状态尚未接入。')
    lines.append('主要有效约束：'+constraint_text(context)+'。')
    return '\n'.join(lines)

def condition_decision(context,condition):
    if condition=='NO_OVERTIME':
        policy=context['overtime']['policy']
        status='ALREADY_SATISFIED' if policy=='EXPLICITLY_FORBIDDEN' else 'NOT_SATISFIED' if policy=='EXPLICITLY_PERMITTED' else 'UNKNOWN'
        message='当前计划已明确禁止加班（来源：本次计划规则），这个条件已经满足，无需修改，也不会重复计算。该规则只适用于当前情景。' if status=='ALREADY_SATISFIED' else '当前规则允许加班，尚未满足禁止加班的条件；加班规则编辑尚未接入。' if status=='NOT_SATISFIED' else '当前缺少明确的加班规则；日历没有加班时段不能证明禁止加班。'
        return {'condition':condition,'status':status,'message':message,'source':context['overtime']['source']}
    values={r['id']:r for r in context['effective_constraints']}
    key={'FINITE_CAPACITY':'finite_capacity','FINITE_MATERIAL':'finite_material'}.get(condition)
    row=values.get(key)
    status='ALREADY_SATISFIED' if row and row['value'] is True else 'UNKNOWN' if not row else 'NOT_SATISFIED'
    return {'condition':condition,'status':status,'message':('当前计划已经采用'+row['label']+'，无需修改，也不会重复计算。') if status=='ALREADY_SATISFIED' else '当前条件未明确满足；相关约束编辑尚未接入。','source':row['source'] if row else None}

def due_change_is_noop(data,args):
    row=next((r for r in data['demand_lines'] if r['id']==args.get('demand_id')),None)
    if not row:return False
    shift=args.get('shift_days');due=args.get('due_at')
    if (shift is None)==(due is None):return False
    try:
        current=datetime.fromisoformat(row['due_at'])
        target=datetime.fromisoformat(due.replace('Z','+00:00')) if due is not None else current+timedelta(days=shift)
        return target.tzinfo is not None and target==current
    except (TypeError,ValueError):return False

def unsupported_message(capability):
    missing={'SHIFT_CHANGE':'尚未接入班次和工作日历编辑','RESOURCE_CAPACITY_CHANGE':'尚未接入资源产能编辑',
             'OVERTIME_POLICY_CHANGE':'尚未接入加班规则编辑','PUBLISH_OR_APPROVE':'不提供正式计划修改、审批或发布',
             'CHAINED_SCENARIO_CHANGE':'尚未支持在现有草案上叠加修改，新的情景都基于原始基线'}.get(capability,'该操作未接入当前工具')
    return missing+'。当前可以查询和比较计划、修改订单交期、新增急单；条件核对后可人工点击运行独立情景，使用真实引擎计算。'
