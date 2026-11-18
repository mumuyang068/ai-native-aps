import type {Dict} from '../api/contracts';
import {reviewComparable,type ReviewScope} from './scenarioReviewModel';
import {reviewManufacturingGroups,reviewDocumentGroups,reviewResourceGroups} from './professionalReviewModel';
import type {ExportInput,ExportField} from './reportExport';
const known=(v:unknown)=>v===null||v===undefined||v===''?null:v;
const ready=(v:Dict|undefined)=>!!v&&['PRESENT','EMPTY_CHECKED'].includes(v.status)&&(!v.validation||v.validation.status==='PASS')&&!v.validation?.missing_evidence?.length;
function difference(a:unknown,b:unknown){return a==null||b==null?'UNKNOWN':JSON.stringify(a)===JSON.stringify(b)?'UNCHANGED':'CHANGED'}
const instant=(v:unknown)=>typeof v==='string'&&Number.isFinite(Date.parse(v))?Date.parse(v):known(v);
function demandCategory(d:Dict,baseline?:Dict,newId?:unknown){
 if(d.demand_kind==='INQUIRY')return 'INQUIRY';
 if(d.demand_kind==='NET_FORECAST')return 'NET_FORECAST';
 if(d.demand_kind==='FIRM_ORDER')return d.demand_id===newId?'NEW_FIRM_ASSUMPTION':baseline?.demand_kind==='FIRM_ORDER'?'ORIGINAL_FIRM':'UNKNOWN_FIRM_ORIGIN';
 return 'UNKNOWN_DEMAND_KIND';
}
function addedResearchInquiry(d:Dict,base:Dict){
 return d.demand_kind==='INQUIRY'&&reviewComparable(base)&&Array.isArray(base.deliveries)&&!base.deliveries.some((b:Dict)=>b.demand_id===d.demand_id);
}
function businessWindows(v:Dict|undefined,component:string){
 if(!v)return null;
 if(component==='resources'){
  const edges=new Map<number,number>();
  for(const r of v.records){const a=Date.parse(r.start_at),b=Date.parse(r.end_at),load=Number(r.load_quantity);if(!Number.isFinite(a)||!Number.isFinite(b)||known(r.load_quantity)==null||!Number.isFinite(load))return null;edges.set(a,(edges.get(a)??0)+load);edges.set(b,(edges.get(b)??0)-load)}
  const points=[...edges.keys()].sort((a,b)=>a-b),segments:number[][]=[];let load=0;
  for(let i=0;i<points.length-1;i++){load+=edges.get(points[i])!;if(!load)continue;const previous=segments.at(-1);if(previous&&previous[1]===points[i]&&previous[2]===load)previous[1]=points[i+1];else segments.push([points[i],points[i+1],load])}
  return segments;
 }
 return v.records.map((r:Dict)=>JSON.stringify([instant(r.start_at??r.at),instant(r.end_at),instant(r.available_at),known(r.quantity)==null?null:Number(r.quantity),known(r.load_quantity)==null?null:Number(r.load_quantity)])).sort();
}
export function handoffRows(c:Dict,base:Dict):Dict[]{
 if(!reviewComparable(c))return [{section:'evidence',category:c.calculation_state??'UNSUPPORTED',object_id:c.target,note:c.reason??'未取得有效运行；没有可交接的求解结果。'}];
 const rows:Dict[]=[];
 const newId=c.changes?.hypothesis?.id??c.changes?.added_demand?.id;
 for(const d of c.deliveries??[]){const b=(base.deliveries??[]).find((x:Dict)=>x.demand_id===d.demand_id&&x.uom===d.uom);rows.push({section:'sales',category:demandCategory(d,b,newId),object_id:d.demand_id,item_id:d.item_id,location_id:d.location_id,uom:d.uom,quantity:known(d.requested_quantity),planned_quantity:known(d.planned_quantity),unfulfilled_quantity:known(d.unfulfilled_quantity),due_at:known(d.due_at),planned_delivery_at:known(d.planned_delivery_at),change:addedResearchInquiry(d,base)?'ADDED_RESEARCH_INQUIRY':d.demand_id===newId?'NEW_ASSUMPTION':difference(b,d),baseline:b??null,records:[d]})}
 for(const b of base.deliveries??[])if(!(c.deliveries??[]).some((d:Dict)=>d.demand_id===b.demand_id&&d.uom===b.uom))rows.push({section:'sales',category:demandCategory(b,b),object_id:b.demand_id,item_id:b.item_id,location_id:b.location_id,uom:b.uom,quantity:null,planned_quantity:null,unfulfilled_quantity:null,change:'NOT_PRESENT_IN_SELECTED',baseline:b,records:[],note:'选定结果没有此需求行；不能以空白推算满足量或欠量为零，请核对预测抵扣与原来源。'});
 for(const [section,component,groups] of [['production','manufacturing',reviewManufacturingGroups([base,c])],['procurement','documents',reviewDocumentGroups([base,c])],['production','resources',reviewResourceGroups([base,c])]] as const){
  for(const row of groups){
   const value=row.values[c.target],before=row.values.baseline,currentReady=ready(c.professional?.[component]),baselineReady=ready(base.professional?.[component]);
   const amount=(v:Dict|undefined,checked:boolean)=>v?known(v.quantity??v.loadHours):checked?0:null;
   const quantityChange=difference(amount(before,baselineReady),amount(value,currentReady)),windowChange=difference(businessWindows(before,component),businessWindows(value,component));
   const absent=!value&&!!before,added=!!value&&!before&&baselineReady;
   const activityChange=absent?(currentReady?'REMOVED_CHECKED':'UNKNOWN'):added?'ADDED_CHECKED':!before||!value?'UNKNOWN':before.records.length!==value.records.length?'ACTIVITY_SPLIT_MERGE_CHANGED':windowChange==='CHANGED'?'WINDOW_OR_ACTIVITY_CHANGED':'UNCHANGED';
   const change=c.target==='baseline'?'BASELINE':absent?(currentReady?'REMOVED_CHECKED':'UNKNOWN'):added?'ADDED_CHECKED':quantityChange==='CHANGED'?'QUANTITY_CHANGED':quantityChange==='UNKNOWN'?'UNKNOWN':activityChange;
   rows.push({section,category:component,object_id:row.dimensions.operation_id??row.dimensions.resource_id??row.dimensions.item_id,...row.dimensions,quantity:component==='resources'?null:amount(value,currentReady),load_hours:component==='resources'?amount(value,currentReady):null,quantity_change:quantityChange,window_change:windowChange,activity_change:activityChange,baseline_record_count:before?.records.length??(baselineReady?0:null),selected_record_count:value?.records.length??(currentReady?0:null),change,baseline:before??null,records:value?.records??(currentReady?[]:null),note:(component==='manufacturing'?'按同工序/物料/地点/单位/状态对照；本运行临时任务引用不用于跨运行认定同一工单移动。':component==='documents'?'confirmed 与 proposed 分列；物理到货 at 与 QC 后 available_at 保留。':'资源原生占用，不据此推断对某客户订单的独立贡献。')+' 总量与窗口/记录拆合分别比较；忽略记录顺序、临时ID及来源SHA变化。'+(absent?(currentReady?'候选已核对无此组活动；保留原建议供核对。':'候选来源未取得；无法判定原建议消失。'):'')});
  }
  if(!ready(c.professional?.[component]))rows.push({section:'evidence',category:component,object_id:component,note:c.professional?.[component]?.reason??'来源或核验未取得，不按零处理。'});
 }
 for(const d of c.inventory??[]){const b=(base.inventory??[]).find((x:Dict)=>['item_id','location_id','uom'].every(k=>x[k]===d[k]));const quantityChange=difference(known(b?.ending_net_available)==null?null:Number(b.ending_net_available),known(d.ending_net_available)==null?null:Number(d.ending_net_available)),activityChange=known(b?.event_count)==null||known(d.event_count)==null?'UNKNOWN':Number(b.event_count)===Number(d.event_count)?'UNCHANGED':'EVENT_COUNT_CHANGED';rows.push({section:'production',category:'inventory',object_id:d.item_id,item_id:d.item_id,location_id:d.location_id,uom:d.uom,quantity:known(d.ending_net_available),quantity_change:quantityChange,activity_change:activityChange,baseline_record_count:known(b?.event_count),selected_record_count:known(d.event_count),change:quantityChange,baseline:b??null,records:[d],note:'比较口径：期末净可用；事件条数另列，未比较事件时序与数量明细，不能据事件数量变化推断库存恶化。物料关联不是订单归因。'})}
 const constraints=c.professional?.constraints??c.constraints;
 rows.push({section:'evidence',category:'constraints',object_id:c.target,note:constraints?.status??'NOT_COLLECTED',records:constraints?.records??null});
 for(const d of c.deliveries??[])if(d.unfulfilled_quantity==null||d.planned_delivery_at==null||Number(d.unfulfilled_quantity)>0)rows.push({section:'evidence',category:'unresolved_delivery',object_id:d.demand_id,note:Number(d.unfulfilled_quantity)>0?'仍有欠量；欠量没有已核验的完成日期。':'数量或计划完成日期未知。'});
 return rows;
}
export const handoffFields:ExportField[]=[['section','业务分区'],['category','对象类型'],['object_id','原对象身份'],['status','原状态'],['change','较基线观察'],['quantity_change','总数量或期末净量变化'],['window_change','业务窗口或资源连续占用变化'],['activity_change','记录拆合或事件条数变化'],['baseline_record_count','基线记录条数'],['selected_record_count','候选记录条数'],['item_id','物料'],['location_id','地点'],['uom','单位'],['quantity','需求/供应/制造/库存净量'],['planned_quantity','计划满足'],['unfulfilled_quantity','欠量'],['load_hours','容量时'],['due_at','要求交期'],['planned_delivery_at','已规划部分交期'],['baseline','基线同口径记录'],['records','选定方案原始记录'],['note','证据边界']].map(([key,label])=>({key,label,meaning:label+'；未知保留空值，同一物料或时点不构成订单归因。'}));
export function handoffExport(c:Dict,base:Dict,s:ReviewScope,timezone:string,filters:Dict):ExportInput{
 const status=reviewComparable(c)?'PRESENT':c.calculation_state??'UNSUPPORTED';
 return {scope:{planId:s.planId,versionId:s.versionId,runId:c.run_id??'',target:c.target},data:{identity:c.professional?.identity??{plan_id:s.planId,data_version_id:s.versionId,run_id:c.run_id,target:c.target,profile:s.profile},timezone,evidence_files:c.professional?.evidence_files??[],handoff:{validation:{status:reviewComparable(c)?'PASS':'NOT_CHECKED'}},handoff_context:{baseline_run_id:s.baselineRunId,baseline_evidence_files:base.professional?.evidence_files??null,hypothesis:c.changes??null,calculation_state:c.calculation_state,business_status:c.business_status,validation:c.validation,constraints:c.professional?.constraints??c.constraints,return_filters:filters,semantics:'规划评审建议；不是客户承诺、资源预留或采购/生产执行指令。临时任务不跨run认同；无订单pegging归因。'}},status,kind:'handoff',rows:handoffRows(c,base),fields:handoffFields,filters,sourceUrl:'/v1/enterprise-plans/'+encodeURIComponent(s.planId)+'/scenario-review?'+new URLSearchParams({data_version_id:s.versionId,baseline_run_id:s.baselineRunId,draft_ids:c.target==='baseline'?'':c.target})};
}
