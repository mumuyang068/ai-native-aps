import type {Dict} from '../api/contracts';
import type {ReportScope} from './reportModel';
import {taskMaterialEvents} from './analysisModel';

export const manufacturingDimensions=['operation_id','item_id','location_id','uom','status'] as const;
export const manufacturingStatus:Record<string,string>={proposed:'计划建议',approved:'已批准',confirmed:'已确认',completed:'已完成',closed:'已关闭'};
export function manufacturingBucketId(row:Dict){return JSON.stringify([...manufacturingDimensions.map(k=>row[k]),row.start_at,row.end_at])}
export function manufacturingRows(rows:Dict[],filters:Record<string,string>){
 return rows.filter(row=>manufacturingDimensions.every(k=>!filters[k]||row[k]===filters[k])&&(!filters.query||[...manufacturingDimensions.map(k=>row[k]),row.native_reference,row.enterprise_reference].some(v=>String(v??'').toLowerCase().includes(filters.query.trim().toLowerCase()))));
}
export function manufacturingTaskRows(tasks:Dict[],query:string){const term=query.trim().toLowerCase();return !term?tasks:tasks.filter(task=>[task.id,task.operation_id,task.native_reference,task.enterprise_reference,...(task.resource_ids??[])].some(v=>String(v??'').toLowerCase().includes(term)))}
/** Identity verification alone never certifies material provenance or a complete event series. */
export function manufacturingMaterialEvidence(data:Dict,taskId:string):{status:string;message:string;materials:Dict[]}{
 const value=data.inventory_analysis,validation=value?.validation;
 const blocked=(status:string,message:string)=>({status,message,materials:[]});
 if(validation?.status==='FAIL'||value?.status==='FAIL')return blocked('FAIL','库存业务核验失败（FAIL），暂不提供此任务的物料事件入口。');
 if(value?.status==='INVALID_EVIDENCE')return blocked('INVALID_EVIDENCE','库存来源证据无效（INVALID_EVIDENCE），暂不提供此任务的物料事件入口。');
 if(!value||!['PRESENT','EMPTY_CHECKED'].includes(value.status))return blocked(value?.status??'NOT_COLLECTED','库存来源证据未取得或不可用（'+(value?.status??'NOT_COLLECTED')+'），暂不提供此任务的物料事件入口。');
 if(validation?.missing_evidence?.length)return blocked('NOT_COLLECTED','库存必要来源缺失（NOT_COLLECTED），已有记录未核验，暂不提供物料事件入口。');
 if(validation?.status!=='PASS')return blocked(validation?.status??'NOT_CHECKED','库存证据核验未完成（'+(validation?.status??'NOT_CHECKED')+'），暂不提供此任务的物料事件入口。');
 if(!Array.isArray(value.series)||value.series.some((series:Dict)=>series.complete!==true||!['PRESENT','EMPTY_CHECKED'].includes(series.event_status)))return blocked('NOT_COLLECTED','库存事件序列不完整（NOT_COLLECTED），暂不提供此任务的物料事件入口。');
 const materials=taskMaterialEvents(data,taskId);
 return {status:materials.length?'PRESENT':'EMPTY_CHECKED',message:materials.length?'':'本运行已核对，未取得此工序精确关联的物料事件；不按同名物料或时间推断分配。',materials};
}
/** Exact leaf tasks only, with the same half-open event bucket as the native report. */
export function manufacturingBucketTasks(bucket:Dict|null,tasks:Dict[],kind='all'){
 if(!bucket)return [];
 const ids=kind==='started'?bucket.started_task_ids:kind==='finished'?bucket.finished_task_ids:bucket.task_ids;
 const inside=(value:string)=>Date.parse(value)>=Date.parse(bucket.start_at)&&Date.parse(value)<Date.parse(bucket.end_at);
 return tasks.filter(task=>(ids??[]).includes(task.id)&&manufacturingDimensions.every(k=>task[k]===bucket[k])&&(kind==='started'?inside(task.start_at):kind==='finished'?inside(task.end_at):inside(task.start_at)||inside(task.end_at)));
}
export function manufacturingScopeKey(scope:ReportScope){return JSON.stringify([scope.planId,scope.versionId,scope.runId,scope.target])}
export function manufacturingDrillSearch(previous:URLSearchParams,scope:ReportScope,mode:string,task:Dict,material?:Dict){
 const origin=new URLSearchParams(previous);origin.delete('manufacturing_return');origin.delete('manufacturing_return_scope');
 const p=new URLSearchParams(origin);p.set('manufacturing_return',origin.toString());p.set('manufacturing_return_scope',manufacturingScopeKey(scope));
 for(const key of [...p.keys()])if(key.startsWith('analysis_'))p.delete(key);
 p.set('analysis',mode==='inventory'?'inventory':mode==='schedule'?'schedule':'resources');
 p.set('analysis_task',task.id);
 if(mode==='composite')p.set('analysis_layout','composite');
 if(mode==='resources'&&task.resource_ids?.[0])p.set('analysis_resource',task.resource_ids[0]);
 if(material){p.set('analysis_item',material.series.item_id);p.set('analysis_location',material.series.location_id);p.set('analysis_event',material.event.id)}
 return p;
}
export function manufacturingReturnSearch(previous:URLSearchParams,scope:ReportScope){
 if(previous.get('manufacturing_return_scope')!==manufacturingScopeKey(scope))return null;
 const p=new URLSearchParams(previous.get('manufacturing_return')??'');p.set('target',scope.target);p.set('analysis','manufacturing');
 p.delete('manufacturing_return');p.delete('manufacturing_return_scope');return p;
}
