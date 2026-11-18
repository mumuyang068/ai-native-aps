"""One bounded opportunity rule; candidates are hypotheses, never solutions."""
import copy,hashlib,json
from datetime import datetime
from decimal import Decimal
from approved_simulation import apply,AUTHORITY
from drafts import DraftError
from fixed_batch import enabled,joint_enabled
from planning_adapter import validate_input,InputError

LIMIT=3
def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def at(value):return datetime.fromisoformat(value)
def q(value):return Decimal(str(value))

def discover(data,view,demand_id):
    """Read baseline only. An existing proposed window supplies the hypothesis.

    Positive native demand pegging establishes the earlier customer link, not a
    material allocation or a cause. An independent solve must test any benefit.
    """
    answer={'status':'NO_OPPORTUNITY_FOUND','target_order_id':demand_id,'candidates':[],
            'candidate_limit':LIMIT,'truncated':False,**AUTHORITY}
    def stop(status,message):return {**answer,'status':status,'message':message}
    if not enabled(data) or joint_enabled(data):return stop('UNSUPPORTED_PROFILE','仅支持已验收的固定批量窗口模型。')
    validate_input(data)
    goal=next((r for r in data['demand_lines'] if r['id']==demand_id),None)
    if goal is None:raise DraftError('UNKNOWN_DEMAND: 当前计划没有该订单行')
    run=view.get('run',{});result=view.get('result',{});projection=view.get('order_evidence',{})
    checks=view.get('interpretation',{})
    identity={'snapshot_id':data['snapshot_id'],'scenario_id':data['scenario_id'],
              'scenario_revision':data['scenario_revision'],'input_hash':digest(data)}
    if (run.get('state')!='SUCCEEDED' or not result.get('solve_run_id')
        or result.get('solve_run_id')!=run.get('solve_run_id')
        or any(result.get(k)!=v or run.get(k)!=v for k,v in identity.items())
        or result.get('result_hash')!=digest({k:v for k,v in result.items() if k!='result_hash'})
        or projection.get('source_result_hash')!=result['result_hash']
        or any(projection.get(k)!=result.get(k) for k in ['solve_run_id','scenario_id','scenario_revision'])):
        return stop('INSUFFICIENT_EVIDENCE','计划、运行或证据版本不一致，不能生成候选。')
    answer['baseline_run_id']=result['solve_run_id']
    if (checks.get('mapping_check')!='PASS' or checks.get('schedule_check')!='PASS'
        or any(checks.get(k,{}).get('status')!='PASS' for k in
               ['material_validation','native_manufacturing_validation','native_model_validation'])
        or projection.get('missing_tables')):
        return stop('INSUFFICIENT_EVIDENCE','缺少已核验的物料、窗口、工单保护或原生关联证据。')
    delivery=next((r for r in result['deliveries'] if r['demand_id']==demand_id),None)
    if delivery is None:return stop('INSUFFICIENT_EVIDENCE','当前运行缺少该订单交付结果。')
    if q(delivery['unfulfilled_quantity'])<=0:return stop('NO_SHORTFALL','当前订单没有欠交，不启动此类机会发现。')
    recipes={r['id']:r for r in data['production_recipes']}
    routes={r['id']:r for r in data['routing_revisions']}
    goal_recipes=[r for r in recipes.values() if (r['output_item_id'],r['location_id'])==(goal['item_id'],goal['location_id'])]
    if len(goal_recipes)!=1:return stop('INSUFFICIENT_EVIDENCE','欠交产品没有唯一受支持的工艺，不能猜测候选窗口。')
    target_op=routes[goal_recipes[0]['routing_revision_id']]['operations'][0]
    demands={r['id']:r for r in data['demand_lines']}
    ending={(r['item_id'],r['location_id']):r['quantity'] for r in checks['material_validation']['ending_balances']}
    loaded={r['engine_ref']:r for r in result.get('existing_manufacturing_orders',[])}
    proposals=result.get('manufacture_suggestions',[]);found={}
    for mo in data['existing_orders']:
        if mo['source_status']!='approved':continue
        operation=mo['operations'][0];native=loaded.get(mo['id'])
        if not native or native['source_status']!='approved':continue
        if operation['resource_id']!=target_op['resource_id']:continue
        # The window freed by this hypothesis must at least fit a goal batch in
        # time; neither this test nor a shared resource proves a delay cause.
        if at(operation['end_at'])>at(goal['due_at']):continue
        if (at(operation['end_at'])-at(operation['start_at'])).total_seconds()<q(target_op['fixed_seconds']):continue
        surplus=ending.get((mo['item_id'],mo['location_id']))
        if surplus is None or q(surplus)<q(mo['quantity']):continue
        for proposal in proposals:
            if proposal.get('source_status')!='proposed' or proposal.get('origin')!='PROPOSED':continue
            if any(proposal[k]!=mo[k] for k in ['item_id','location_id','recipe_id','uom']):continue
            if q(proposal['quantity'])!=q(mo['quantity']) or at(proposal['end_at'])>at(operation['start_at']):continue
            tasks=[r for r in result['operations'] if r['manufacture_id']==proposal['id']]
            if len(tasks)!=1 or tasks[0]['resource_id']!=operation['resource_id']:continue
            if any(at(tasks[0][k])!=at(proposal[k]) for k in ['start_at','end_at']):continue
            supporting=[]
            for order in projection['orders']:
                demand=demands.get(order['demand_id'])
                if not demand or (demand['item_id'],demand['location_id'])!=(mo['item_id'],mo['location_id']):continue
                if not at(proposal['end_at'])<=at(demand['due_at'])<at(operation['end_at']):continue
                links=[r for r in order['manufacturing'] if r['id']==proposal['id']
                       and r['relation_kind']=='NATIVE_DEMAND_PEGGING' and q(r['native_pegging_quantity'])>0
                       and r.get('source',{}).get('sha256')]
                if links:supporting.append({'demand_id':demand['id'],'due_at':demand['due_at'],'source':links[0]['source']})
            if not supporting:continue
            try:
                candidate=copy.deepcopy(data)
                change=apply(candidate,mo['id'],proposal['start_at'])
                validate_input(candidate)
            except (DraftError,InputError):continue
            if at(change['after']['end_at'])!=at(proposal['end_at']):continue
            key=(mo['id'],proposal['start_at'])
            found[key]={'reference':mo['id'],'item_id':mo['item_id'],'uom':mo['uom'],
                'new_start_at':proposal['start_at'],'before':operation,'after':change['after'],
                'proposed_reference':proposal['engine_ref'],'supporting_orders':supporting,
                'ending_surplus_quantity':surplus,'target_unfulfilled_quantity':delivery['unfulfilled_quantity'],
                'effect_status':'UNTESTED',**AUTHORITY,
                'protected_references':sorted(r['id'] for r in data['existing_orders'] if r['id']!=mo['id']),
                'evidence':{'target_delivery':delivery['evidence_ids'],'approved':native['evidence_ids'],
                    'proposed':proposal['evidence_ids'],'customer_links':[r['source'] for r in supporting],
                    'window_input_pointer':'/calendars','stock_check':'material_validation.ending_balances',
                    'input_hash':result['input_hash'],'run_id':result['solve_run_id']},
                'reason':'同产品的较早制造建议关联客户需求，已有 approved 工单较晚且期末有额外成品；可检验用已有工单覆盖该窗口并释放较晚窗口。尚未证明能改善欠交。'}
    candidates=[found[k] for k in sorted(found,key=lambda k:(at(k[1]),k[0]))]
    answer.update(candidates=candidates[:LIMIT],truncated=len(candidates)>LIMIT)
    return {**answer,'status':'CANDIDATES_FOUND' if candidates else 'NO_OPPORTUNITY_FOUND',
            'message':'候选尚未试算，需通过独立情景核对交付与保护条件。' if candidates else '在本次限定规则与已取得证据内未发现候选；不代表不存在其他可行方案。'}
