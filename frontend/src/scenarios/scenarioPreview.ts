import type {Dict} from '../api/contracts';
import {editorIntentText} from '../planning/editorIntentModel';
export function scenarioTitle(c:Dict){return c.action==='set_foundation_forecast_gross'?'毛预测调整':c.intent??'独立方案'}
export function scenarioPreview(c:Dict):[string,string][]{
 if(c.action==='simulate_plan_editor')return [['调整对象',(c.edits??[]).map((e:Dict)=>e.reference).join('、')],...(c.events??[]).map((e:Dict):[string,string]=>[e.reference,['task','demand'].includes(e.kind)?editorIntentText(e,c.timezone??'UTC')+(e.changes?.time&&!c.timezone?'（UTC）':''):e.kind==='window'?e.before.start_at+' → '+e.after.start_at:e.kind==='proposed_window'?e.source_task.start_at+' → '+e.start_at:e.kind==='machine'?'拟采用 '+e.resource_id:e.kind==='optimized_schedule'?'联合调整 '+e.assignments.length+' 道工序':e.kind==='native_strategy'?e.solver+' · '+e.machine_search:(e.before??[]).join(' → ')+' / 候选 '+(e.after??[]).join(' → ')]),['理由',c.reason],['计算核验要求','原Worker求解和业务核验'],['制造与执行','未授权']];
 if(c.action==='simulate_professional_policy'&&c.supply){
  const rows:[string,string][]=[];
  if(c.supply.purchase){const p=c.supply.purchase;rows.push(['未收采购余额',p.reference],['物理到货条件',p.before.expected_receipt_at+' → '+p.after.expected_receipt_at],['QC后可用条件',p.before.available_at+' → '+p.after.available_at]);}
  if(c.supply.distribution){const r=c.supply.distribution;rows.push(['调拨路线',r.reference],['运输提前期条件',r.before.lead_time_seconds/3600+' → '+r.after.lead_time_seconds/3600+' 小时']);}
  return [...rows,['组合研究条件',[c.netting?'预测抵扣':'',c.inquiry?'新增订单评估':'',c.replenishment?'补货目标':''].filter(Boolean).join('、')||'仅供应条件'],['试算理由',c.parameters?.reason??'未提供'],['实收、质量与原工单','保持来源事实；原Worker计算与独立核验'],['正式订单、承诺和执行','未授权']];
 }
 if(c.action==='simulate_professional_policy')return [['研究条件',[c.netting?'预测抵扣':'',c.inquiry?'新增订单评估':'',c.replenishment?'补货目标':''].filter(Boolean).join('、')],['政策版本',c.netting?.policy?.policy_version??c.replenishment?.policy_version??'独立客户询价'],['研究依据',c.parameters?.netting?.source??c.parameters?.replenishment?.source??c.parameters?.reason??'未提供'],['试算理由',c.parameters?.reason??'未提供'],['原企业事实与硬约束','保持；原Worker计算与独立核验'],['原订单承诺','询价需逐期交付保护；未通过不可建议接单'],['正式订单、承诺和执行','未授权']];
 if(c.action==='set_foundation_forecast_gross')return [['产品 / 地点',c.bucket.item_id+' / '+c.bucket.location_id],['客户',c.bucket.customer_id],['完整原期间（含时区）',c.bucket.bucket_start+' → '+c.bucket.bucket_end+'（不含）'],['来源 Gross',c.before.gross_quantity+' '+c.bucket.uom],['候选 Gross',c.after.gross_quantity+' '+c.bucket.uom],['净预测 / 已抵扣','未知 / 未计算'],['假设理由',c.reason],['原Firm需求及企业事实','保持不变'],['规划准入','抵扣政策待确认，不能提交净预测计算'],['实际执行','未授权']];
 if(c.action==='simulate_distribution_leadtime')return [['精确调拨路线',c.conditions?.lane_id??c.reference??'未提供'],['运输提前期假设',String(c.before_lead_time_seconds/3600)+' → '+String(c.after_lead_time_seconds/3600)+' 小时（连续日历）'],['假设理由',c.reason??'未提供'],['原确认事实','采购、QC、库存和两目的地需求保持不变'],['计算状态','创建后NOT_RUN，原Worker另行计算'],['业务许可 / 实际执行','尚未确认 / 未授权']];
 if(c.action==='simulate_resource_capacity')return [['模拟设备',c.conditions?.resource_id??'未提供'],['假设不可用开始',c.conditions?.start_at??'未提供'],['假设不可用结束',c.conditions?.end_at??'未提供'],['扣减原可用时间',String(Number(c.resource_change?.removed_seconds)/3600)+' 小时'],['原日历',c.resource_change?.before_calendar_id??'未提供'],['情景日历',c.resource_change?.after_calendar_id??'未提供'],['假设理由',c.reason??'未提供'],['原企业事实','采购、收货、质量、工单及原日历保持不变'],['业务许可 / 实际执行','尚未确认 / 未授权']];
 if(c.action==='simulate_outstanding_receipt')return [['确认采购',c.reference??c.conditions?.reference??'未提供'],['假设范围','仅未来未收余额，已收与质量事实保持不变'],['未收余额',String(c.after?.remaining_quantity??'未核实')+' '+String(c.after?.uom??'单位未提供')],['原预计物理到货',c.before?.physical_receipt_at??'未提供'],['假设物理到货',c.after?.physical_receipt_at??'未提供'],['原QC后可用',c.before?.available_at??'未提供'],['假设QC后可用',c.after?.available_at??'未提供'],['保留QC等待',c.after?.qc_wait_seconds==null?'未提供':String(Number(c.after.qc_wait_seconds)/3600)+' 小时'],['受保护制造工单','身份、数量、状态和原窗口保持；冲突须单独报告'],['假设理由',c.reason??'未提供'],['计算状态','创建后NOT_RUN，需另行提交计算'],['业务许可 / 实际执行','尚未确认 / 未授权']];
 if(c.action==='simulate_inventory_decision'){
  const rows:[string,string][]=[],policy=c.after?.policy,supply=c.after?.supply;
  if(c.conditions?.policy&&policy)rows.push(['精确库存对象',policy.item_id+' @ '+policy.location_id+' · '+policy.uom],['最低 / 最高目标',policy.minimum+' / '+policy.maximum],['政策有效期（含时区）',policy.start_at+' → '+policy.end_at],['目标含义','最低为软目标，最高为发生补给时的目标，不保证实现']);
  if(c.conditions?.source_option_id&&supply)rows.push(['来源条款',supply.source_id+' / '+supply.id],['MOQ / 倍数 / 单笔上限',supply.minimum_order_quantity+' / '+supply.order_multiple+' / '+supply.maximum_order_quantity],['全可下单期总能力',supply.total_capacity_quantity+' '+supply.uom],['采购提前期 / QC等待',String(supply.lead_time_seconds/3600)+' / '+String(supply.qc_wait_seconds/3600)+' 小时'],['条款有效期（含时区）',supply.effective_from+' → '+supply.effective_to],['模型采购金额','未知：来源未提供单价与币种']);
  return [...rows,['假设理由',c.reason??'未提供'],['计算状态','创建后 NOT_RUN，需另行提交计算'],['业务许可','尚未确认'],['实际执行','未授权']];
 }
 if(c.action==='simulate_controlled_conditions'){
  const rows:[string,string][]=[];
  if(c.order)rows.push(['精确 Firm 需求行',c.order.demand_id],['需求 priority',String(c.order.before_priority)+' → '+String(c.order.after_priority)]);
  if(c.operator)rows.push(['具名 SEW 人员',c.operator.resource_id],['缺勤时段（含时区）',c.operator.start_at+' → '+c.operator.end_at],['输入声明日历',c.operator.before_calendar_id+' → '+c.operator.after_calendar_id],['扣减原声明可用时间',String(Number((Number(c.operator.removed_seconds)/60).toFixed(2)))+' 分钟']);
  return [...rows,['假设理由',c.reason??'未提供'],['计算状态','创建后 NOT_RUN，需另行提交计算'],['业务许可','尚未确认'],['实际执行','未授权']];
 }
 if(c.action==='add_firm_demand')return [['新增假设需求行',c.hypothesis.id],['订单 / 行',c.hypothesis.order_ref+' / '+c.hypothesis.line_ref],['客户',c.hypothesis.customer_id],['物料 / 地点 / 单位',c.hypothesis.item_id+' / '+c.hypothesis.location_id+' / '+c.hypothesis.uom],['Firm 数量',c.hypothesis.quantity],['要求交期（含时区）',c.hypothesis.due_at],['研究来源',c.hypothesis.research_identity],['假设理由',c.reason],['接单承诺与资源预留','未授权']];
 if(c.action==='enable_accessory_supply')return [['辅料来源',c.source_id],['模拟补给许可','未启用 → 仅此草案启用'],['冻结来源条款','保持原值'],['假设理由',c.reason],['原确认采购与企业版本','保持不变'],['采购与生产执行','未授权']];
 if(c.action==='set_forecast_gross_override')return [['预测桶',c.bucket.forecast_id],['Gross 假设',c.before.gross_quantity+' → '+c.after.gross_quantity],['净预测规则预览',c.before.net_quantity+' → '+c.after.net_quantity],['假设理由',c.reason],['原订单与确认事实','保持不变'],['实际执行','未授权']];
 if(c.action==='set_demand_priority')return [['精确需求行',c.demand_id],['订单需求优先级',String(c.before_priority)+' → '+String(c.after_priority)],['数值含义','数值越小越优先；与供应来源PRIORITY不同'],['原数量与交期','保持不变'],['实际执行','未授权']];
 if(c.intent==='资源不可用模拟')return [['模拟资源',c.resource_id??c.reference],['假设不可用窗口',c.start_at+' → '+c.end_at],['减少原可用时间',c.removed_seconds/60+' 分钟'],['原日历',c.before_calendar_id],['情景日历',c.after_calendar_id],['业务许可','尚未确认'],['实际执行','未授权']];
 if(c.intent==='供应来源条件模拟')return [['供应来源',c.source_id],['全期容量假设',c.before.capacity_quantity+' → '+c.after.capacity_quantity],['MOQ假设',c.before.minimum_order_quantity+' → '+c.after.minimum_order_quantity],['业务许可','尚未确认'],['实际执行','未授权']];
 if(c.intent==='修改交期')return [['订单行',c.demand_id],['原交期',c.before_due_at],['新交期',c.after_due_at]];
 if(c.intent==='approved工单窗口模拟')return [['模拟工单',c.reference],['原窗口',c.before.start_at+' → '+c.before.end_at],['假设窗口',c.after.start_at+' → '+c.after.end_at],['业务许可','尚未确认'],['实际执行','未授权']];
 if(c.intent==='确认采购延期模拟')return [['确认采购',c.reference],['原预计物理到货',c.before_receipt_at],['新预计物理到货',c.after_receipt_at],['原质检后可用',c.before_available_at],['新质检后可用',c.after_available_at],['保留质检等待',c.qc_wait_seconds/3600+' 小时']];
 return [['急单编号',c.order_id??'未提供'],['产品',c.item_id??'未提供'],['数量',c.quantity??'未提供'],['交期',c.due_at??'未提供'],['客户',c.customer_id??'未提供']];
}
