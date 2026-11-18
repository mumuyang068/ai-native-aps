import type {PlanView} from '../api/contracts';
import {qty,unit,totalText} from '../planning/planModel';
import {zeroLatenessEvidence} from '../planning/orderModel';

export function UnplannedExplanation({base,view}:{base:PlanView;view:PlanView}){
 const orders=view.result?.deliveries.filter(d=>d.status==='UNPLANNED')??[],diff=view.diff,p=view.order_evidence;
 if(!orders.length)return null;
 if(!base.run?.solve_run_id||!view.run?.solve_run_id||diff?.base_run_id!==base.run.solve_run_id||diff?.candidate_run_id!==view.run.solve_run_id)
  return <p className="detail-note" role="status">尚未核对到同一组基线和方案运行，未排定原因对照暂不可用。</p>;
 const evidenceMatches=!!p&&p.solve_run_id===view.run.solve_run_id&&p.source_result_hash===view.result?.result_hash;
 const totals=base.context?.manufacturing_status.totals,nextTotals=view.context?.manufacturing_status.totals;
 return <section className="source-section" aria-label="未排定订单业务解释">
  <h2 className="section-title">未排定订单说明</h2>
  <h3>已确认事实</h3>
  <p className="detail-note">{view.draft?.changes.supply?.purchase?'本候选声明了未收采购余额延期，物理到货与QC后可用时间见联合供应链对照；这不等于已证明该订单的唯一缺料原因。':diff.confirmed_supplies?.length?'同一情景的确认采购物理到货与质检后可用时间变化见下方对照。这些是计划级变化，不等于已证明的本订单供给分配或瓶颈。':'当前对照没有提供确认采购时间变更记录，不能假定发生了供应延期。'}</p>
  <h3>计算观察结果</h3>
  <p className="detail-note">本次 {orders.length} 个订单行未排定，尚无预计交付时间。{totals&&nextTotals?'制造建议由 '+totalText(totals)+' 变为 '+totalText(nextTotals)+'。':''}没有采购建议不代表需求已满足。</p>
  <ul>{orders.map(d=><li key={d.demand_id}>{d.demand_id}：计划交付 {qty(d.planned_quantity)} {unit(d.uom)}，欠交 {qty(d.unfulfilled_quantity)} {unit(d.uom)}。</li>)}</ul>
  <h3>尚未确认的原因</h3>
  {orders.map(d=>{const order=evidenceMatches?p!.orders.find(o=>o.demand_id===d.demand_id):undefined;
   return <p className="detail-note" key={d.demand_id}>{d.demand_id}：{!order||p!.missing_tables.includes('constraint')?'约束证据尚未完整核对，当前无法确认具体瓶颈。':order.constraints.length?'引擎返回 '+order.constraints.length+' 条订单约束，可在订单详情核对；尚未证明完整或唯一瓶颈。':'未返回该订单的明确约束记录，不代表不存在约束；当前无法确认具体瓶颈。'}{order&&zeroLatenessEvidence(order,p!)?' 原生订单记录显示本次不允许晚交，该规则不能单独证明具体原因。':''}</p>;
  })}
  <p className="detail-note">供应时间变化、资源关联和未排定结果不能单独证明因果。未排定不能换算为确定的晚交时长，也未证明所有其他方案无解。原生来源可在工作台选中订单后查看“来源与证据”。</p>
 </section>;
}
