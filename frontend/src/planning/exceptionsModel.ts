import type {Dict} from '../api/contracts';
import {siteDate} from './timeRange';
import {investigationSearch} from './orderInvestigationModel';
import {knownQuantity} from './dailyWorkModel';

export type ExceptionScope={planId:string;versionId:string;runId:string;target:string;profile?:string;resultHash?:string};
export type ExceptionFilters={query?:string;from?:string;to?:string;status?:string;owner?:string;type?:string;evidence?:string;item?:string;location?:string;kind?:string;uom?:string;quantityMin?:string;plannedMin?:string;plannedMax?:string;shortMin?:string};
export function verifyExceptions(value:Dict,scope:ExceptionScope){
 const expected={plan_id:scope.planId,data_version_id:scope.versionId,run_id:scope.runId,target:scope.target,...(scope.profile?{profile:scope.profile}:{}),...(scope.resultHash?{result_hash:scope.resultHash}:{})};
 if(!value.identity||Object.entries(expected).some(([key,v])=>value.identity[key]!==v)||!Array.isArray(value.demands))throw Error('异常记录与当前计划、版本、方案或运行不一致，已停止展示。');
 const reliable=(source:Dict|undefined)=>!!source&&source.run_id===scope.runId&&!!source.artifact&&/^[a-f0-9]{64}$/i.test(source.sha256??'');
 for(const demand of value.demands)for(const c of demand.constraints??[])if(c.record?.demand!==demand.demand_id||!reliable(c.source))throw Error('Constraint 缺少精确需求行或当前运行的可靠来源，未建立订单归属。');
 for(const c of [...(value.unlinked_constraints??[]),...(value.problems??[])])if(!reliable(c.source))throw Error('运行级记录的来源与当前运行不一致。');
 return value;
}
export function filterExceptions(rows:Dict[],filter:ExceptionFilters,timezone:string){
 const query=(filter.query??'').trim().toLocaleLowerCase();
 return rows.filter(row=>{
  const planned=Number(row.planned_quantity),short=Number(row.unfulfilled_quantity),constraints:Dict[]=row.constraints??[];
  const late=!!row.planned_delivery_at&&Date.parse(row.planned_delivery_at)>Date.parse(row.due_at);
  const due=Number.isFinite(Date.parse(row.due_at))?siteDate(row.due_at,timezone):'';
  if(filter.item&&row.item_id!==filter.item||filter.location&&row.location_id!==filter.location||filter.kind&&row.kind!==filter.kind||filter.uom&&row.uom!==filter.uom)return false;
  for(const [field,key,maximum] of [['quantityMin','requested_quantity',false],['plannedMin','planned_quantity',false],['plannedMax','planned_quantity',true],['shortMin','unfulfilled_quantity',false]] as const){
   const raw=filter[field];if(raw===undefined||raw==='')continue;
   const limit=knownQuantity(raw),value=knownQuantity(row[key]);if(limit===null||value===null||(maximum?value>limit:value<limit))return false;
  }
  if(filter.from&&(!due||due<filter.from)||filter.to&&(!due||due>filter.to))return false;
  if(query&&![row.demand_id,row.order_ref,row.item_id,row.location_id].some(v=>String(v??'').toLocaleLowerCase().includes(query)))return false;
  if(filter.owner&&!constraints.some(c=>String(c.record.owner??'').toLocaleLowerCase().includes(filter.owner!.toLocaleLowerCase())))return false;
  if(filter.type&&!constraints.some(c=>c.record.name===filter.type))return false;
  if(filter.evidence&&filter.evidence!==row.constraint_status)return false;
  return !filter.status||filter.status==='ALL'||filter.status==='SHORT'&&short>0||filter.status==='UNPLANNED'&&short>0&&planned===0||filter.status==='LATE'&&late||filter.status==='CONSTRAINT'&&constraints.length>0||filter.status==='ABNORMAL'&&(short>0||late||constraints.length>0||row.status!=='ON_TIME');
 });
}
export function rememberExceptionOrigin(previous:URLSearchParams,scope:ExceptionScope,demandId?:string){
 const p=new URLSearchParams(previous);p.set('return_decision','exceptions');p.set('exception_target',scope.target);p.set('exception_run',scope.runId);p.set('exception_version',scope.versionId);if(demandId)p.set('exception_row',demandId);return p;
}
export function exceptionInvestigationSearch(previous:URLSearchParams,demandId:string,scope:ExceptionScope){
 const p=investigationSearch(rememberExceptionOrigin(previous,scope,demandId),demandId);p.set('target',scope.target);p.set('run_id',scope.runId);p.set('data_version_id',scope.versionId);return p;
}
export function decisionReturnSearch(previous:URLSearchParams){
 const p=new URLSearchParams(previous),mode=p.get('return_decision')==='review'?'review':'exceptions';p.set('analysis',mode);
 if(mode==='exceptions'){
  if(p.get('exception_target'))p.set('target',p.get('exception_target')!);
  if(p.get('exception_run'))p.set('run_id',p.get('exception_run')!);
  if(p.get('exception_version'))p.set('data_version_id',p.get('exception_version')!);
 }
 for(const key of ['view','order_line_id','analysis_event','analysis_task','analysis_return_event','analysis_return_task'])p.delete(key);
 p.delete('return_decision');if(mode==='review'&&p.get('review_return_decision')){p.set('return_decision',p.get('review_return_decision')!);p.delete('review_return_decision')}return p;
}
