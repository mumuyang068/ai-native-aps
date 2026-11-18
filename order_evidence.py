"""Versioned read-only projection of explicit frePPLe references, never a solver.

Native pegging values are preserved without asserting raw material allocations.
Constraint windows describe engine diagnostics, not proposed purchase dates.
"""
import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo

TABLES=('demand','manufacturingorder','workorder','purchaseorder','deliveryorder',
        'operationplanmaterial','operationplanresource','constraint')


def rows(value):
    return value if isinstance(value,list) else value.get('rows',value.get('results',[]))


def native_plan(row):
    value=row.get('plan',{})
    if isinstance(value,str):
        try:value=json.loads(value)
        except ValueError:return {}
    return value if isinstance(value,dict) else {}


def project_order_evidence(data,result,raw,source_hashes):
    from planning_capabilities import profile_id
    foundation=profile_id(data)=='foundation-planning-projection-v1'
    if foundation:
        from p22.foundation_projection import firm_order_refs
        source_references=firm_order_refs(data)
    run=result['solve_run_id']
    units={x['id']:x['base_uom'] for x in data.get('items',[])}
    normalized={x['id']:x for table in ['operations','manufacture_suggestions','existing_manufacturing_orders','purchase_suggestions'] for x in result.get(table,[])}
    def source(table,row,index):
        ref=str(row.get('reference',row.get('id',row.get('name',index))))
        return {'solve_run_id':run,'scenario_id':result['scenario_id'],
                'scenario_revision':result['scenario_revision'],'artifact':table+'.json',
                'locator':'row:'+str(index),'native_record_id':ref,
                'evidence_id':run+':ENGINE:'+table+':'+ref,'sha256':source_hashes.get(table)}
    def native(table,row,index):
        times={}
        for key in ['startdate','enddate','flowdate','deliverydate','due']:
            if row.get(key):
                dt=datetime.fromisoformat(row[key].replace('Z','+00:00'))
                times[key]=(dt.replace(tzinfo=ZoneInfo(data['site']['timezone'])) if dt.tzinfo is None else dt).isoformat()
        return {'record':row,'times':times,'source':source(table,row,index)}
    orders=[]
    for delivery in result['deliveries']:
        did=delivery['demand_id'];demand=next((x for x in data['demand_lines'] if x['id']==did),{})
        groups={'manufacturing':[],'operations':[],'purchases':[],'deliveries':[]};seen=set();refs=set()
        for table,group in [('workorder','operations'),('manufacturingorder','manufacturing'),('purchaseorder','purchases'),('deliveryorder','deliveries')]:
            for i,row in enumerate(rows(raw.get(table,[]))):
                peg=native_plan(row).get('pegging',{})
                if not isinstance(peg,dict) or did not in peg:continue
                ref=str(row['reference']);key=(group,ref)
                actual_group='operations' if table=='manufacturingorder' and row.get('owner') is not None else group
                if table=='workorder' and row.get('owner') is None:actual_group='manufacturing'
                key=(actual_group,ref)
                if key in seen:continue
                seen.add(key);refs.add(ref)
                kind={'manufacturing':'MO','operations':'OP','purchases':'PO','deliveries':'DO'}[actual_group]
                entry={**native(table,row,i),'id':run+':'+kind+':'+ref,'engine_ref':ref,
                       'task':normalized.get(run+':'+kind+':'+ref),
                       'item_uom':units.get(row.get('item')),
                       'owner_ref':str(row['owner']) if row.get('owner') is not None else None,
                       'relation_kind':'NATIVE_DEMAND_PEGGING','native_pegging_quantity':str(peg[did]),
                       'quantity_semantics':'NATIVE_PEGGING_VALUE_NOT_MATERIAL_ALLOCATION'}
                groups[actual_group].append(entry)
        constraints=[native('constraint',r,i) for i,r in enumerate(rows(raw.get('constraint',[]))) if r.get('demand')==did]
        materials=[{**native('operationplanmaterial',r,i),'item_uom':units.get(r.get('item')),'relation_kind':'NATIVE_TASK_REFERENCE','quantity_scope':'FULL_TASK_FLOW_NOT_ORDER_ALLOCATION'} for i,r in enumerate(rows(raw.get('operationplanmaterial',[]))) if str(r.get('operationplan')) in refs]
        resources=[{**native('operationplanresource',r,i),'relation_kind':'NATIVE_TASK_REFERENCE','quantity_scope':'FULL_TASK_LOAD_NOT_DELAY_CAUSE'} for i,r in enumerate(rows(raw.get('operationplanresource',[]))) if str(r.get('operationplan')) in refs]
        demand_records=[native('demand',r,i) for i,r in enumerate(rows(raw.get('demand',[]))) if r.get('name')==did]
        order_ref=source_references[did] if foundation else demand.get('order_ref',did)
        orders.append({'demand_id':did,'order_ref':order_ref,'delivery':delivery,
                       **groups,'materials':materials,'resources':resources,'constraints':constraints,
                       'demand_records':demand_records,
                       'cause_status':'ENGINE_CONSTRAINTS_REPORTED' if constraints else 'INSUFFICIENT_EVIDENCE',
                       'limitations':['原生关联不等于延期原因；共享任务的全量流转不等于该订单的分配数量。',
                                      '约束记录可能不完整，不能证明唯一原因或所有替代方案均无解。']})
    value={'schema_version':'order-evidence/1','solve_run_id':run,'scenario_id':result['scenario_id'],
           'scenario_revision':result['scenario_revision'],'source_result_hash':result['result_hash'],
           'missing_tables':[t for t in TABLES if t not in raw], 'source_hashes':source_hashes,'orders':orders}
    # Optional same-run buffer identities. These are buffer-level observations,
    # never evidence of an order's inventory allocation or receipt history.
    value.update(buffer_records=[],buffer_evidence_status='NOT_COLLECTED')
    if 'fixed_batch_post' in raw:
        capture=raw['fixed_batch_post'];value['buffer_evidence_status']='NOT_CHECKED'
        if (isinstance(capture,dict) and capture.get('run_id')==run and capture.get('phase')=='post'
            and result.get('input_hash') and capture.get('input_hash')==result['input_hash']
            and source_hashes.get('fixed_batch_post') and isinstance(capture.get('buffers'),list)):
            for i,buffer in enumerate(capture['buffers']):
                if not isinstance(buffer,dict) or not all(isinstance(buffer.get(k),str) for k in ['name','item','location']):continue
                s=source('fixed_batch_post',buffer,i);s['locator']='/buffers/'+str(i)
                value['buffer_records'].append({'record':buffer,'source':s})
            value['buffer_evidence_status']='PRESENT'
    value['projection_hash']=hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return value


def find_order(projection,identifier):
    exact=[o for o in projection['orders'] if o['demand_id']==identifier]
    matches=exact or [o for o in projection['orders'] if o['order_ref']==identifier]
    if len(matches)!=1:raise KeyError(identifier)
    return matches[0]


def explain_order(projection,identifier):
    o=find_order(projection,identifier);d=o['delivery'];when=lambda v:str(v or '尚无预计日期').replace('T',' ')
    text=f"订单 {o['order_ref']}（{o['demand_id']}），当前运行 {projection['solve_run_id']}。\n要求交付 {when(d['due_at'])}；预计交付 {when(d['planned_delivery_at'])}，状态 { {'LATE':'存在延期','ON_TIME':'预计按期','UNPLANNED':'未排定','SHORT':'缺量'}.get(d['status'],d['status'])}。"
    if all(k in d for k in ['planned_quantity','unfulfilled_quantity','uom']):
        u={'piece':'件','m':'米'}.get(d['uom'],d['uom'])
        text+=f"\n计划交付 {d['planned_quantity']} {u}，欠交 {d['unfulfilled_quantity']} {u}。"
    if d['status']=='UNPLANNED':text+='\n本次没有排出交付时间，不能换算成确定的晚交时长；未证明所有方案无解。'
    text+=f"\n原生关联：{len(o['manufacturing'])} 项制造建议、{len(o['operations'])} 个工序、{len(o['purchases'])} 项采购建议。"
    if d['status']=='ON_TIME':text+='\n当前计算没有显示该订单延期。'
    if o['constraints']:
        text+='\n引擎返回以下订单级约束诊断：'
        labels={'purchasing lead time':'采购提前期限制','capacity overload':'资源产能约束','await supply':'等待供应','operation before current':'早于规划时点的提前期限制','operation before fence':'释放围栏限制'}
        for c in o['constraints']:
            r=c['record'];s=c['source']
            text+=f"\n• {labels.get(r.get('name'),r.get('name','未命名约束'))}；对象 {r.get('owner','未提供')}；{r.get('description','')}。诊断区间 {when(r.get('startdate'))} 至 {when(r.get('enddate'))}。来源 {s['artifact']}#{s['locator']}，原生记录 {s['native_record_id']}。"
        text+='\n这些是引擎记录的约束，不证明唯一或完整原因。诊断区间不是实际采购下单日期；采购日期应查看关联采购建议。'
    else:text+='\n当前没有足够证据确认具体原因。当前运行没有返回该订单的明确约束记录；这不代表不存在约束。'
    resources=sorted({str(r['record']['resource']) for r in o['resources']})
    if resources:text+='\n关联资源：'+'、'.join(resources)+'（来源 operationplanresource.json 的任务引用）；仅证明资源关联，不能据此断言资源导致延期。'
    units={'piece':'件','m':'米'}
    for oplan in o['operations']:
        r=oplan['record'];s=oplan['source'];t=oplan.get('task') or {}
        text+=f"\n• 工序 {r.get('operation')}，任务 {oplan['engine_ref']}，上级制造 {oplan['owner_ref']}；{when(t.get('start_at',r.get('startdate')))} → {when(t.get('end_at',r.get('enddate')))}；来源 {s['artifact']}#{s['locator']}。"
    for purchase in o['purchases']:
        r=purchase['record'];s=purchase['source'];t=purchase.get('task') or {};u=t.get('uom',purchase.get('item_uom'))
        text+=f"\n• 关联采购 {purchase['engine_ref']}：{r.get('item')}，任务总量 {r.get('quantity')} {units.get(u,u or '单位未提供')}；建议下单 {when(t.get('order_at',r.get('startdate')))}，预计到料 {when(t.get('receipt_at',r.get('enddate')))}；来源 {s['artifact']}#{s['locator']}。"
    for material in o['materials']:
        r=material['record'];s=material['source'];u=material.get('item_uom')
        text+=f"\n• 物料 {r.get('item')}，任务 {r.get('operationplan')}，带符号流转量 {r.get('quantity')} {units.get(u,u or '单位未提供')}（负数消耗、正数产出）；来源 {s['artifact']}#{s['locator']}。"
    if o['purchases'] or o['materials']:text+='\n上述采购和物料数量为关联任务全量，共享任务不能直接分摊给本订单；原生 pegging 数值没有冒充原料分配量。'
    if o['demand_records']:
        s=o['demand_records'][0]['source'];text+=f"\n交付来源 {s['artifact']}#{s['locator']}，原生记录 {s['native_record_id']}。"
    if projection['missing_tables']:text+='\n缺少原生表：'+'、'.join(projection['missing_tables'])+'，证据覆盖不完整。'
    return text
