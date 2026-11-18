import type {Dict,OrderTrace,PlanView} from '../api/contracts';
import type {ObjectSelection} from './orderModel';
import {supports} from './capabilityModel';
export type InvestigationScope={planId:string;versionId:string;target:string;orderId:string;view:PlanView};
export type OrderFilter='ALL'|'SHORT'|'UNPLANNED'|'LATE'|'ABNORMAL';
export const evidenceLabels:Record<string,string>={PRESENT:'已取得记录',EMPTY_CHECKED:'已查询 · 记录为空',NOT_COLLECTED:'未采集',NOT_CHECKED:'尚未核验',UNSUPPORTED:'不支持',UNREADABLE:'原件不可读取',INVALID_EVIDENCE:'证据身份或内容不一致'};
export function evidenceStatus(code?:string){
 if(code==='EMPTY')return 'EMPTY_CHECKED';
 if(code&&code in evidenceLabels)return code;
 if(code?.includes('UNREADABLE'))return 'UNREADABLE';
 if(code?.includes('UNSUPPORTED'))return 'UNSUPPORTED';
 if(code?.includes('NOT_COLLECTED'))return 'NOT_COLLECTED';
 return code?'INVALID_EVIDENCE':'NOT_COLLECTED';
}
export function filterOrders(orders:OrderTrace[],query:string,filter:OrderFilter){
 const q=query.trim().toLocaleLowerCase();return orders.filter(o=>{
  const d=o.delivery,planned=Number(d.planned_quantity),short=Number(d.unfulfilled_quantity);
  const late=!!d.planned_delivery_at&&Number.isFinite(Date.parse(d.planned_delivery_at))&&Date.parse(d.planned_delivery_at)>Date.parse(d.due_at);
  const match=filter==='ALL'||filter==='SHORT'&&planned>0&&short>0||filter==='UNPLANNED'&&planned===0&&short>0||filter==='LATE'&&late||filter==='ABNORMAL'&&(short>0||late||o.constraints.length>0||!['ON_TIME'].includes(d.status));
  return match&&(!q||[o.demand_id,o.order_ref].some(s=>s.toLocaleLowerCase().includes(q)));
 });
}
export function investigationKey(s:InvestigationScope){return JSON.stringify([s.planId,s.versionId,s.target,s.view.run?.solve_run_id,s.view.result?.result_hash,s.view.order_evidence?.projection_hash,s.orderId])}
export function verifyInvestigation(value:Dict,s:InvestigationScope):Dict&{order:OrderTrace}{
 const {view}=s,p=view.order_evidence,r=view.result,expected={plan_id:s.planId,data_version_id:s.versionId,target:s.target,run_id:view.run?.solve_run_id,result_hash:r?.result_hash,projection_hash:p?.projection_hash,scenario_id:p?.scenario_id,scenario_revision:r?.scenario_revision};
 const actual=r?.deliveries.find(d=>d.demand_id===s.orderId);
 const source=view.context?.enterprise_source;
 if(source?.plan_id!==s.planId||source?.data_version_id!==s.versionId||!r||!p||!actual||!value?.scope||Object.entries(expected).some(([k,v])=>v===undefined||value.scope[k]!==v)||value.order?.demand_id!==s.orderId||p.solve_run_id!==view.run?.solve_run_id||p.source_result_hash!==r.result_hash)throw Error('订单证据与当前计划、版本、方案或运行不一致，已停止展示。');
 if(['requested_quantity','planned_quantity','unfulfilled_quantity','due_at','planned_delivery_at','status','uom'].some(k=>(actual as Dict)[k]!==value.order.delivery?.[k]))throw Error('订单交付证据与当前PlanResult不一致。');
 for(const group of ['demand_records','manufacturing','operations','purchases','materials','resources','constraints'])for(const e of value.order[group]??[]){
  if(e.source?.solve_run_id!==expected.run_id||e.source?.scenario_id!==expected.scenario_id||!(/^[a-f0-9]{64}$/i.test(e.source?.sha256??''))||group==='constraints'&&e.record?.demand!==s.orderId)throw Error('原生记录缺少本运行的可靠来源或需求行身份。');
 }
 return value as Dict&{order:OrderTrace};
}
export function analysisSupport(profile:string){return {resources:supports(profile,'analyses','resources'),inventory:supports(profile,'analyses','inventory')}}
export function knownBusinessFail(view:PlanView){const v=view.interpretation??{};return v.business_status==='INVALID_SCHEDULE'||v.schedule_check==='FAIL'||Object.entries(v).some(([k,x])=>k.endsWith('_validation')&&x?.status==='FAIL')}
export function isLocatedOrderTask(key:string,orderId:string,selection:ObjectSelection|null,origin:{key:string;id:string}|null){return selection?.key===key&&selection.kind==='task'&&origin?.key===key&&origin.id===orderId}
export function investigationSearch(previous:URLSearchParams,orderId:string){const next=new URLSearchParams(previous);if(previous.get('analysis')==='reports')next.set('return_report',previous.get('report')??'demand');next.set('view','investigate');next.set('order_line_id',orderId);next.set('analysis','schedule');return next}

/** Snapshot rows remain original facts; only the exact selected draft may supply added research metadata. */
export function currentDemandMetadata(input:Dict,scope:InvestigationScope):{line:Dict;origin:'SNAPSHOT'|'RESEARCH_ASSUMPTION'|'RESEARCH_INQUIRY'}|null{
 const original=input.demand_lines?.find((line:Dict)=>line.id===scope.orderId);
 if(original)return {line:original,origin:'SNAPSHOT'};
 const {view}=scope,draft=view.draft,source=view.context?.enterprise_source,changes=draft?.changes,added=changes?.added_demand;
 if(changes?.action==='simulate_professional_policy'){
  const inquiry=changes.inquiry,line=inquiry?.added_demand,delivery=view.result?.deliveries.find(d=>d.demand_id===scope.orderId);
  if(scope.target==='baseline'||draft?.id!==scope.target||source?.plan_id!==scope.planId||source?.data_version_id!==scope.versionId||!view.run?.solve_run_id||draft?.run_id!==view.run.solve_run_id||changes.simulation_only!==true||changes.execution_authorized!==false||inquiry?.request?.id!==scope.orderId||line?.id!==scope.orderId||line?.kind!=='INQUIRY'||line?.provenance?.scope!=='NON_BINDING_SIMULATION'||line?.provenance?.research_inquiry!==scope.orderId||!delivery||line.quantity!==delivery.requested_quantity||line.uom!==delivery.uom||!Number.isFinite(Date.parse(line.due_at))||Date.parse(line.due_at)!==Date.parse(delivery.due_at))return null;
  return {line,origin:'RESEARCH_INQUIRY'};
 }
 if(scope.target==='baseline'||draft?.id!==scope.target||source?.plan_id!==scope.planId||source?.data_version_id!==scope.versionId||!view.run?.solve_run_id||draft?.run_id!==view.run.solve_run_id||changes?.action!=='add_firm_demand'||changes?.hypothesis?.id!==scope.orderId||added?.id!==scope.orderId||added?.kind!=='FIRM_ORDER'||added?.provenance?.source_system!=='SYNTHETIC_RESEARCH_ASSUMPTION'||!view.result?.deliveries.some(d=>d.demand_id===scope.orderId))return null;
 return {line:added,origin:'RESEARCH_ASSUMPTION'};
}
