import type {Dict} from '../api/contracts';
import {analysisContextSearch} from './analysisModel';

/** Review objects own only their destination's filters; inventory is never a manufacturing target. */
export function reviewObjectSearch(previous:URLSearchParams,target:string,mode:string,demandId?:string,context?:Dict){
 const p=analysisContextSearch(previous,target);
 if(p.get('return_decision')&&p.get('return_decision')!=='review')p.set('review_return_decision',p.get('return_decision')!);
 p.set('return_decision','review');p.set('analysis',mode==='investigate'?'schedule':mode==='documents'?'reports':mode);
 if(mode==='documents')p.set('report','documents');
 if(mode==='manufacturing'){
  for(const key of [...p.keys()])if(key.startsWith('mf_'))p.delete(key);
  for(const key of ['operation_id','item_id','location_id','uom','status'])if(context?.[key])p.set('mf_'+key,context[key]);
 }else if(mode==='inventory'){
  for(const key of ['analysis_resource','analysis_operation','analysis_layout','analysis_task','analysis_event','analysis_item','analysis_location'])p.delete(key);
  if(context?.item_id)p.set('analysis_item',context.item_id);
  if(context?.location_id)p.set('analysis_location',context.location_id);
 }else if(mode==='resources'){
  for(const key of ['analysis_item','analysis_location','analysis_task','analysis_event','analysis_resource'])p.delete(key);
  if(context?.resource_id)p.set('analysis_resource',context.resource_id);
 }
 if(demandId){p.set('order_line_id',demandId);p.set('view','investigate')}else{p.delete('view');p.delete('order_line_id')}
 return p;
}

export type ReviewScope={planId:string;versionId:string;baselineRunId:string;profile?:string;endpoint?:Dict};
export function selectedReviewDrafts(value:string|null,available:Dict[]){
 const selected=value===null?available.slice(0,2).map(d=>d.id):value.split(',').filter(Boolean);
 return [...new Set<string>(selected)].filter(id=>available.some(d=>d.id===id)).slice(0,3);
}
export function verifyReview(value:Dict,scope:ReviewScope){
 const expected={plan_id:scope.planId,data_version_id:scope.versionId,baseline_run_id:scope.baselineRunId,...(scope.profile?{profile:scope.profile}:{})};
 if(!value.identity||Object.entries(expected).some(([key,v])=>value.identity[key]!==v)||!Array.isArray(value.candidates)||!value.candidates.some((c:Dict)=>c.target==='baseline'&&c.run_id===scope.baselineRunId))throw Error('评审数据与当前计划、版本、基线运行或认证模型不一致。');
 if(scope.endpoint&&Object.entries(scope.endpoint).some(([key,v])=>value.endpoint?.[key]!==v))throw Error('业务终点不同，不能并列比较阶段产出与成品交付。');
 for(const candidate of value.candidates){
  if(candidate.professional&&Object.entries({plan_id:scope.planId,data_version_id:scope.versionId,run_id:candidate.run_id,target:candidate.target,...(scope.profile?{profile:scope.profile}:{})}).some(([key,v])=>candidate.professional.identity?.[key]!==v))throw Error('专业评审证据与候选的计划、版本、方案、运行或认证模型不一致。');
 }
 return value;
}
export function reviewComparable(candidate:Dict){
 const validation=candidate.validation;
 const policy=candidate.professional?.policy_assessment;
 if(policy&&(policy.status!=='AUDITED'||policy.business_audit!=='PASS'||policy.original_commitments?.status!=='PASS'))return false;
 const checked=validation?.material?.status==='PASS'&&validation?.schedule?.status==='PASS'&&(validation?.resources===null||validation?.resources?.status==='PASS');
 return candidate.comparable===true&&candidate.calculation_state==='SUCCEEDED'&&!!candidate.run_id&&['FEASIBLE_ON_TIME','LATE_DELIVERY','PARTIAL_OR_UNPLANNED'].includes(candidate.business_status)&&checked&&!Object.values(validation??{}).some((v:any)=>v==='FAIL'||v?.status==='FAIL');
}
type ReviewRow={id:string;demand_id?:string;item_id?:string;location_id?:string;uom:string;values:Record<string,Dict>};
export function reviewDeliveryRows(candidates:Dict[]):ReviewRow[]{
 const rows=new Map<string,ReviewRow>();
 for(const candidate of candidates.filter(reviewComparable))for(const d of candidate.deliveries??[]){
  const id=JSON.stringify([d.demand_id,d.uom]);if(!rows.has(id))rows.set(id,{id,demand_id:d.demand_id,item_id:d.item_id,uom:d.uom,values:{}});rows.get(id)!.values[candidate.target]=d;
 }
 return [...rows.values()];
}
export function reviewInventoryRows(candidates:Dict[]):ReviewRow[]{
 const rows=new Map<string,ReviewRow>();
 for(const candidate of candidates.filter(reviewComparable))for(const d of candidate.inventory??[]){
  const id=JSON.stringify([d.item_id,d.location_id,d.uom]);if(!rows.has(id))rows.set(id,{id,item_id:d.item_id,location_id:d.location_id,uom:d.uom,values:{}});rows.get(id)!.values[candidate.target]=d;
 }
 return [...rows.values()];
}
