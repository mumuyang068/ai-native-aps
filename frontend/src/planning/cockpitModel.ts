import {supplyScopeKey} from './supplyWorkModel';
import type {Dict} from '../api/contracts';
import {supports} from './capabilityModel';
import {fulfillmentGroupId,fulfillmentRangeLabel} from './demandFulfillmentModel';
import {reportRowId,verifyReport,type ReportScope} from './reportModel';
import {formatSiteTime} from './timeRange';

export type CockpitScope=ReportScope&{profile:string};
export type CockpitAttentionItem={
 id:string;kind:'demand'|'inventory'|'purchase'|'resource'|'work';title:string;detail:string;
 at?:string;quantity?:number;uom?:string;source:'FACT'|'PLAN_RESULT'|'WORK_STATE';
 level:'risk'|'info'|'unknown';params:Record<string,string>;
};
export type CockpitAvailability='PRESENT'|'EMPTY_CHECKED'|'UNSUPPORTED'|'NOT_COLLECTED'|'UNREADABLE'|'INVALID_EVIDENCE'|'NOT_CHECKED'|'FAIL';
export function cockpitDemandEmptyMessage(status:CockpitAvailability){
 return status==='PRESENT'||status==='EMPTY_CHECKED'?'当前已核验的需求没有待调查缺口；可继续查看专业明细。':'需求证据不足，空清单不代表没有缺口。';
}

const ready=(value:unknown)=>value==='PRESENT'||value==='EMPTY_CHECKED';
const rows=(value:unknown):Dict[]=>Array.isArray(value)?value.filter(v=>v&&typeof v==='object'&&!Array.isArray(v)):[];
const text=(value:unknown):value is string=>typeof value==='string'&&value.trim().length>0;
const number=(value:unknown):number|null=>{
 if(typeof value!=='number'&&(typeof value!=='string'||!value.trim()))return null;
 const n=Number(value);return Number.isFinite(n)?n:null;
};
const dated=(value:unknown):value is string=>text(value)&&/(Z|[+-]\d\d:\d\d)$/.test(value)&&Number.isFinite(Date.parse(value));
const amount=(value:number)=>value.toLocaleString('zh-CN',{maximumFractionDigits:8});
const resourceAmount=(value:number)=>value.toLocaleString('zh-CN',{maximumFractionDigits:2});
const passing=(value:Dict|undefined)=>value?.status==='PASS'&&!(value.issues?.length)&&!(value.missing_evidence?.length);
const fail=(report:Dict,analysis:Dict|null)=>report.known_business_fail===true||
 [report.demand,report.resource,report.inventory,analysis?.resource_analysis,analysis?.inventory_analysis,analysis?.demand_analysis]
 .some(v=>v?.validation?.status==='FAIL')||analysis?.native_result_status?.constraint_check==='FAIL'||
 rows(report.demand?.lines).some(line=>line.delivery_validation_status==='FAIL');

/** A successful HTTP response is not proof that it belongs to this cockpit. */
export function verifyCockpit(value:Dict,scope:CockpitScope){
 if(![scope.planId,scope.versionId,scope.runId,scope.target,scope.profile].every(text))throw Error('驾驶舱的计划、版本、方案、运行或模型身份不完整。');
 verifyReport(value,scope);
 if(value.identity.profile!==scope.profile)throw Error('驾驶舱与当前计划的认证模型不一致。');
 return value;
}

function availability(value:Dict|undefined,certified:boolean):CockpitAvailability{
 if(value?.validation?.status==='FAIL')return 'FAIL';
 if(!certified||value?.status==='UNSUPPORTED')return 'UNSUPPORTED';
 if(!value)return 'NOT_COLLECTED';
 if(['UNREADABLE','INVALID_EVIDENCE','NOT_COLLECTED','NOT_CHECKED'].includes(value.status))return value.status;
 if(!ready(value.status)||!passing(value.validation))return 'NOT_CHECKED';
 return value.status;
}

/** Presence, certification and business legality remain separate from a zero value. */
export function domainAvailability(report:Dict,analysis:Dict|null){
 if(fail(report,analysis))return {demand:'FAIL',inventory:'FAIL',resource:'FAIL'} as const;
 const profile=report.identity?.profile;
 let demand=availability(report.demand,true);
 if(ready(demand)&&(!ready(report.demand.delivery_evidence_status)||!ready(report.demand.due_evidence_status)))demand='NOT_CHECKED';
 if(ready(demand)&&report.demand.delivery_performance&&!passing(report.demand.delivery_performance.validation))demand='NOT_CHECKED';
 let inventory=availability(analysis?.inventory_analysis,supports(profile,'analyses','inventory'));
 if(ready(inventory)&&rows(analysis?.inventory_analysis?.series).some(s=>s.complete!==true||!ready(s.event_status)))inventory='NOT_CHECKED';
 return {
  demand,
  inventory,
  resource:availability(report.resource,supports(profile,'analyses','resources'))
 };
}

function contextMatches(context:Dict,scope:CockpitScope){
 if(context.identity){verifyCockpit(context,scope);return context.manufacturing_profile===undefined||context.manufacturing_profile===scope.profile;}
 // The existing PlanContext has no synthesized identity: cross-check its owning fields.
 return context.enterprise_source?.plan_id===scope.planId&&context.enterprise_source?.data_version_id===scope.versionId&&
  context.plan_version?.solve_run_id===scope.runId&&context.manufacturing_profile===scope.profile&&
  (scope.target==='baseline'||context.plan_version?.scenario_id===scope.target);
}

function workItems(drafts:Dict[],scope:CockpitScope):CockpitAttentionItem[]{
 return rows(drafts).filter(d=>text(d.id)&&['DRAFT','RETAINED'].includes(d.state)&&!d.run_id&&
  (d.plan_id===undefined||d.plan_id===scope.planId)&&(d.data_version_id===undefined||d.data_version_id===scope.versionId))
  .map(d=>({id:'work:'+d.id,kind:'work',title:'未计算方案',
   detail:text(d.changes?.intent)?d.changes.intent+'，尚未计算；不是已批准或待发布任务。':'尚未计算，可进入原方案查看条件。',
   source:'WORK_STATE',level:'info',params:{target:d.id,analysis:'schedule'}}));
}

function demandItems(report:Dict,scope:CockpitScope):CockpitAttentionItem[]{
 const demand=report.demand;
 if(!ready(demand?.status)||!passing(demand.validation)||!ready(demand.delivery_evidence_status)||!ready(demand.due_evidence_status))return [];
 return rows(demand.lines).flatMap(line=>{
  const shortage=number(line.unfulfilled_quantity),performance=demand.delivery_performance;
  const lateProof=performance?.status==='PRESENT'&&passing(performance?.validation)&&line.demand_kind==='FIRM_ORDER'&&rows(demand.lines).filter(l=>l.demand_id===line.demand_id).length===1
   ?rows(performance.groups).filter(g=>fulfillmentGroupId(g)===fulfillmentGroupId(line)).flatMap(g=>rows(g.lines)).filter(p=>p.demand_id===line.demand_id):[];
  const late=shortage===0&&lateProof.length===1&&lateProof[0].classification==='FULL_LATE'&&dated(lateProof[0].completion_at),quantity=late?number(line.planned_quantity):shortage;
  if(quantity===null||quantity<=0||!text(line.demand_id)||!text(line.item_id)||!text(line.location_id)||!text(line.uom)||
     !dated(line.due_at)||line.delivery_validation_status!=='PASS'||!ready(line.delivery_evidence_status))return [];
  const matching=rows(demand.rows).filter(row=>fulfillmentGroupId(row)===fulfillmentGroupId(line)&&
   (row.demand_ids?.includes(line.demand_id)||rows(row.demand_components).some(c=>c.demand_id===line.demand_id)));
  // Prefer the due bucket, retaining the owner's half-open boundary and exact row identity.
  const row=matching.find(row=>dated(row.start_at)&&dated(row.end_at)&&Date.parse(row.start_at)<=Date.parse(line.due_at)&&Date.parse(line.due_at)<Date.parse(row.end_at));
  const params:Record<string,string>=row?{analysis:'reports',report:'demand',grain:report.grain==='week'?'week':'day',report_group:fulfillmentGroupId(row),
   report_row:String(reportRowId(row)),report_scope:JSON.stringify({planId:scope.planId,versionId:scope.versionId,runId:scope.runId,target:scope.target}),
   report_line_query:line.demand_id,report_active:'false'}:
   {analysis:'schedule',view:'investigate',order_line_id:line.demand_id,run_id:scope.runId,data_version_id:scope.versionId};
  const forecast=line.demand_kind==='NET_FORECAST';
   if(row&&['FIRM_ORDER','NET_FORECAST'].includes(line.demand_kind))params.report_demand_kind=line.demand_kind;
  return [{id:'demand:'+line.demand_id,kind:'demand' as const,title:late?(line.order_ref??line.item_id)+' · 需求迟后':forecast?line.item_id+' · 净预测未满足':(line.order_ref??line.item_id)+' · 需求未满足',
   detail:late?'确认需求；已排 '+amount(quantity)+' '+line.uom+'，原生交付事件核验为迟后排全；查看履约明细。':(line.demand_kind==='FIRM_ORDER'?'确认需求；':'')+amount(quantity)+' '+line.uom+' 未满足；缺量没有已验证完成日期。',at:line.due_at,quantity,uom:line.uom,
   source:'PLAN_RESULT' as const,level:'risk' as const,
   params}];
 });
}

function inventoryItems(analysis:Dict|null,scope:CockpitScope):CockpitAttentionItem[]{
 const inventory=analysis?.inventory_analysis;
 if(!supports(scope.profile,'analyses','inventory')||!ready(inventory?.status)||!passing(inventory.validation))return [];
 return rows(inventory.series).flatMap(series=>{
  if(series.complete!==true||!ready(series.event_status)||!text(series.item_id)||!text(series.location_id)||!text(series.uom))return [];
  // Native event order is authoritative, including events with equal timestamps. Do not re-sort or reconstruct balances.
  const event=rows(series.events).find(event=>dated(event.at)&&text(event.id)&&number(event.balance)!==null&&number(event.balance)!<0);
  if(!event)return [];
  const quantity=number(event.balance)!;
  return [{id:'inventory:'+series.id+':'+event.id,kind:'inventory' as const,title:series.item_id+' · 净可用不足',
   detail:series.location_id+' · 事件后净可用 '+amount(quantity)+' '+series.uom+'；仅为本运行物料事件。',
   at:event.at,quantity,uom:series.uom,source:'PLAN_RESULT' as const,level:'risk' as const,
   params:{analysis:'inventory',analysis_item:series.item_id,analysis_location:series.location_id,analysis_event:event.id}}];
 });
}

function purchaseItems(report:Dict,context:Dict,scope:CockpitScope):CockpitAttentionItem[]{
 if(!contextMatches(context,scope))return [];
 const receipts=context.receipt_protection;
 if(receipts&&receipts.profile!==scope.profile)return [];
 const raw=rows(receipts?.purchase_orders).length?rows(receipts.purchase_orders):
  rows(context.confirmed_supply_windows).length?rows(context.confirmed_supply_windows):rows(context.network_supply?.confirmed_supply_windows);
 const seen=new Set<string>();
 return raw.flatMap(po=>{
  if(!text(po.id)||seen.has(po.id)||po.source_status!=='confirmed'||!text(po.item_id)||!text(po.location_id)||
     !text(po.supply_source_id)||!po.provenance)return [];
  seen.add(po.id);
  const document=(ready(report.documents?.status)?rows(report.documents.documents):[]).find(row=>row.kind==='PO'&&row.origin==='EXISTING'&&row.status==='confirmed'&&
   row.reference===po.id&&row.item_id===po.item_id&&row.location_id===po.location_id&&row.source_id===po.supply_source_id);
  const uom=text(po.uom)?po.uom:document?.uom;
  if(!text(uom))return [];
  const remaining=number(Object.hasOwn(po,'remaining_quantity')?po.remaining_quantity:po.quantity),received=number(po.received_quantity),waiting=number(po.qc_waiting_quantity),frozen=number(po.frozen_received_quantity);
  const receiptPanel=scope.profile==='multi-receipt-protected-manufacturing-v1';
  if(!receiptPanel&&(!text(document?.id)||!supports(scope.profile,'analyses','inventory')||!ready(report.inventory?.status)||!rows(report.inventory?.rows).some(row=>row.item_id===po.item_id&&row.location_id===po.location_id&&row.uom===uom)))return [];
  const entries:CockpitAttentionItem[]=[],params:Record<string,string>=receiptPanel?{analysis:'supply-work',supply_po:po.id}:{analysis:'supply-work',supply_item:po.item_id,supply_location:po.location_id,supply_doc:document!.id,supply_attention:'all',supply_scope:supplyScopeKey(scope)};
  const physical=po.physical_receipt_at??po.expected_receipt_at;
  const assumed=scope.target!=='baseline'&&context.simulation?.simulation_only===true&&context.simulation.purchase_reference===po.id;
  const time=(value:string)=>text(report.timezone)?formatSiteTime(value,report.timezone):value;
  if(remaining!==null&&remaining>0&&dated(physical)&&dated(po.available_at)&&Date.parse(po.available_at)>=Date.parse(physical)){
   entries.push({id:'purchase:'+po.id+':outstanding',kind:'purchase',title:po.item_id+(assumed?' · 本方案到货假设':' · 确认采购待收'),
    detail:(assumed?'原确认采购未修改；本方案假设：':'')+(received!==null&&received>=0?'已收 '+amount(received)+' '+uom+'，':'')+'未收 '+amount(remaining)+' '+uom+'；预计物理到货 '+time(physical)+'，QC后计划可用 '+time(po.available_at)+'。',
    at:physical,quantity:remaining,uom,source:assumed?'WORK_STATE':'FACT',level:'info',params});
  }
  for(const [phase,quantity,title] of [['qc',waiting,'收货待QC'],['frozen',frozen,'收货冻结']] as const){
   if(quantity===null||quantity<=0)continue;
   entries.push({id:'purchase:'+po.id+':'+phase,kind:'purchase',title:po.item_id+' · '+title,
    detail:amount(quantity)+' '+uom+'，未计为净可用供应；查看原采购收货事实。',quantity,uom,source:'FACT',level:'info',params});
  }
  return entries;
 });
}

function resourceItems(report:Dict,scope:CockpitScope):CockpitAttentionItem[]{
 const resource=report.resource;
 if(!supports(scope.profile,'analyses','resources')||!ready(resource?.status)||!passing(resource.validation))return [];
 return rows(resource.rows).flatMap(row=>{
  const quantity=number(row.load_hours),ratio=number(row.utilization_percent);
  if(quantity===null||quantity<=0||row.role==='OPERATOR_POOL'||!text(row.resource_id)||!dated(row.start_at)||!dated(row.end_at)||
   !Array.isArray(row.interval_ids)||!row.interval_ids.some(text))return [];
  const available=number(row.available_hours),knownRatio=available!==null&&available>0&&ratio!==null&&ratio>=0;
  return [{id:'resource:'+row.resource_id+':'+row.start_at,kind:'resource' as const,title:row.resource_id+' · 期间计划负荷',
   detail:fulfillmentRangeLabel({start_at:row.start_at,end_at:row.end_at},report.timezone)+'；负荷 '+resourceAmount(quantity)+' 小时'+(knownRatio?'；负荷 / 可用 '+resourceAmount(ratio!)+'%。':'；利用率未核验。'),
   at:row.start_at,quantity,uom:'小时',source:'PLAN_RESULT' as const,level:'info' as const,
   params:{analysis:'resources',analysis_resource:row.resource_id,analysis_start:row.start_at,analysis_end:row.end_at,
    ...(text(row.interval_ids?.[0])?{analysis_event:row.interval_ids[0]}:{})}}];
 });
}

/** Bounded projections only: no stock balance, demand allocation or utilization calculation. */
export function cockpitAttention(report:Dict,analysis:Dict|null,context:Dict,drafts:Dict[],scope:CockpitScope):CockpitAttentionItem[]{
 verifyCockpit(report,scope);if(analysis)verifyCockpit(analysis,scope);
 const work=workItems(drafts,scope),boundContext=contextMatches(context,scope);
 if(fail(report,analysis)||(boundContext&&context.assessment?.constraint_check==='FAIL'))return [{
  id:'work:business-fail',kind:'work',title:'当前运行存在业务违规',detail:'已知硬约束核验失败；当前结果不可作为合法计划，先查看异常与约束。',
  source:'PLAN_RESULT',level:'risk',params:{analysis:'exceptions'}},...work];
 const uncomputedOwner=context.enterprise_source?.plan_id===scope.planId&&context.enterprise_source?.data_version_id===scope.versionId&&
  context.manufacturing_profile===scope.profile&&(scope.target==='baseline'||context.plan_version?.scenario_id===scope.target);
 if((boundContext||uncomputedOwner)&&text(context.calculation_state)&&context.calculation_state!=='SUCCEEDED')return [{
  id:'work:calculation:'+context.calculation_state,kind:'work',title:context.calculation_state==='NOT_RUN'?'当前方案尚未计算':'当前方案尚无有效完成结果',
  detail:'不沿用其他方案或旧运行的业务数值，请进入原计划查看计算状态。',source:'WORK_STATE',level:'unknown',params:{analysis:'schedule'}},...work];
 const items=[...demandItems(report,scope),...inventoryItems(analysis,scope),...purchaseItems(report,context,scope),...resourceItems(report,scope),...work];
 const rank={risk:0,unknown:1,info:2};
 return items.sort((a,b)=>rank[a.level]-rank[b.level]||(a.at&&b.at?Date.parse(a.at)-Date.parse(b.at):a.at?-1:b.at?1:0));
}

export type CockpitDemandSummary={id:string;kind:string;item:string;location:string;uom:string;endpoint:string;count:number;requested:number|null;planned:number|null;short:number|null};
/** Sums only compatible original line quantities; no new allocation or due-date calculation. */
export function cockpitDemandSummaries(report:Dict):CockpitDemandSummary[]{
 if(domainAvailability(report,null).demand==='FAIL'||!ready(report.demand?.status)||!passing(report.demand?.validation))return [];
 const groups=new Map<string,Dict[]>();
 for(const line of rows(report.demand.lines)){
  if(!text(line.demand_id)||!text(line.item_id)||!text(line.location_id)||!text(line.uom))continue;
  const id=JSON.stringify([fulfillmentGroupId(line),line.demand_kind??'UNKNOWN']);groups.set(id,[...(groups.get(id)??[]),line]);
 }
 return [...groups].map(([id,lines])=>{
  const known=new Set(lines.map(l=>l.demand_id)).size===lines.length&&lines.every(l=>l.delivery_validation_status==='PASS'&&ready(l.delivery_evidence_status));
  const sum=(key:string)=>known&&lines.every(l=>number(l[key])!==null)?lines.reduce((a,l)=>a+number(l[key])!,0):null;
  const l=lines[0];return {id,kind:l.demand_kind??'UNKNOWN',item:l.item_id,location:l.location_id,uom:l.uom,endpoint:l.endpoint??l.item_id,count:lines.length,requested:sum('requested_quantity'),planned:sum('planned_quantity'),short:sum('unfulfilled_quantity')};
 });
}

export type CockpitProduct={id:string;item:string;location:string;uom:string;endpoint:string;count:number;parts:CockpitDemandSummary[];firm?:CockpitDemandSummary;forecast?:CockpitDemandSummary;unclassified:CockpitDemandSummary[];attentionDue:string|null;needsCheck:boolean};
/** Demand objects within one report only; unlike units, locations and stage endpoints never merge. */
export function cockpitProducts(report:Dict):CockpitProduct[]{
 const grouped=new Map<string,CockpitDemandSummary[]>();
 for(const part of cockpitDemandSummaries(report)){
  const id=JSON.parse(part.id)[0] as string;grouped.set(id,[...(grouped.get(id)??[]),part]);
 }
 const products=[...grouped].map(([id,parts])=>{
  const first=parts[0],firm=parts.find(p=>p.kind==='FIRM_ORDER'),forecast=parts.find(p=>p.kind==='NET_FORECAST'),unclassified=parts.filter(p=>!['FIRM_ORDER','NET_FORECAST'].includes(p.kind));
  const lines=rows(report.demand?.lines).filter(l=>fulfillmentGroupId(l)===id),gaps=lines.filter(l=>number(l.unfulfilled_quantity)!>0);
  const urgent=gaps.filter(l=>l.demand_kind==='FIRM_ORDER'),dates=(urgent.length?urgent:gaps).map(l=>l.due_at).filter(dated).sort((a,b)=>Date.parse(a)-Date.parse(b));
  return {id,item:first.item,location:first.location,uom:first.uom,endpoint:first.endpoint,count:parts.reduce((n,p)=>n+p.count,0),parts,firm,forecast,unclassified,attentionDue:dates[0]??null,needsCheck:unclassified.length>0||lines.some(l=>!dated(l.due_at))||parts.some(p=>[p.requested,p.planned,p.short].some(v=>v===null))};
 });
 const priority=(p:CockpitProduct)=>p.firm?.short!=null&&p.firm.short>0?0:p.unclassified.some(k=>k.short!=null&&k.short>0)?1:p.needsCheck?2:p.forecast?.short!=null&&p.forecast.short>0?3:4;
 return products.sort((a,b)=>priority(a)-priority(b)||(a.attentionDue&&b.attentionDue?Date.parse(a.attentionDue)-Date.parse(b.attentionDue):0)||a.item.localeCompare(b.item));
}

export function cockpitSupplyRows(report:Dict,context:Dict,scope:CockpitScope):Dict[]{
 const actions=purchaseItems(report,context,scope),raw=rows(context.receipt_protection?.purchase_orders).length?rows(context.receipt_protection.purchase_orders):rows(context.confirmed_supply_windows).length?rows(context.confirmed_supply_windows):rows(context.network_supply?.confirmed_supply_windows);
 return raw.flatMap(po=>{
  const action=actions.find(a=>['outstanding','qc','frozen'].some(k=>a.id==='purchase:'+po.id+':'+k));
  return action?[{...po,uom:action.uom,action,physical_at:po.physical_receipt_at??po.expected_receipt_at,assumed:context.simulation?.simulation_only===true&&context.simulation.purchase_reference===po.id}]:[];
 });
}

export function cockpitResourceRows(report:Dict,scope:CockpitScope):Dict[]{
 if(!supports(scope.profile,'analyses','resources')||!ready(report.resource?.status)||!passing(report.resource?.validation))return [];
 return rows(report.resource.rows).filter(r=>r.role!=='OPERATOR_POOL'&&number(r.load_hours)!>0&&Array.isArray(r.interval_ids)&&r.interval_ids.length&&dated(r.start_at)&&dated(r.end_at));
}
export function cockpitResourcePeriodId(row:Dict){return JSON.stringify([row.resource_id,row.start_at,row.end_at])}
