import type {Dict} from '../api/contracts';
import type {ReportScope} from './reportModel';
import type {TaskRecord} from './planModel';
import {capabilityReason,supports} from './capabilityModel';
import {eventRange,removedAvailability} from './analysisModel';

export function verifyAnalysis(value:Dict,scope:ReportScope){
 if(!value?.identity||Object.entries({plan_id:scope.planId,data_version_id:scope.versionId,run_id:scope.runId,target:scope.target}).some(([key,v])=>value.identity[key]!==v))throw Error('分析响应不属于当前计划、版本、方案与运行，已停止展示。');
 return value;
}
/** Fit must read the full authenticated run; a filtered response cannot reveal earlier events. */
export async function loadAnalysisFitRange(scope:ReportScope,mode:'resources'|'inventory',read:(url:string)=>Promise<Dict>){
 const query=new URLSearchParams({data_version_id:scope.versionId,run_id:scope.runId,target:scope.target});
 const value=verifyAnalysis(await read('/v1/enterprise-plans/'+encodeURIComponent(scope.planId)+'/analysis?'+query),scope),component=mode==='resources'?value.resource_analysis:value.inventory_analysis;
 if(!supports(value.identity.profile,'analyses',mode)||!['PRESENT','EMPTY_CHECKED'].includes(component?.status)||component.validation?.status==='NOT_CHECKED'||component.validation?.missing_evidence?.length)throw Error('当前运行缺少已认证分析证据，无法确定全部事件范围。');
 if(Date.parse(value.window?.start_at)!==Date.parse(value.planning_at)||Date.parse(value.window?.end_at)!==Date.parse(value.horizon_end))throw Error('响应没有覆盖当前完整规划期，未据此缩放。');
 return eventRange(value,mode);
}
/** Link occupancy only by the authenticated native operation_task_id, never item/time. */
export function filteredResourceLoads(tasks:TaskRecord[],intervals:Dict[],start:number,end:number,query:string,resourceId:string){
 const term=query.trim().toLocaleLowerCase(),matchingTasks=new Set(tasks.filter(t=>!term||[t.id,t.operation_id,t.name,t.itemId,t.manufacture_id,t.resource_id,t.resourceName].some(v=>String(v??'').toLocaleLowerCase().includes(term))).map(t=>t.id));
 return intervals.filter(x=>(!resourceId||x.resource_id===resourceId)&&Date.parse(x.start_at)<end&&Date.parse(x.end_at)>start&&(!term||matchingTasks.has(x.operation_task_id)));
}
export function compositeRows(tasks:TaskRecord[],analysis:Dict,start:number,end:number,query:string,resourceId:string){
 const term=query.trim().toLocaleLowerCase(),certified=supports(analysis.identity.profile,'analyses','resources');
 const excluded=new Set((analysis.resource_analysis.excluded_resources??[]).map((r:Dict)=>r.id));
 const resources:Dict[]=certified?(analysis.resource_analysis.resources??[]).filter((r:Dict)=>['MACHINE','OPERATOR'].includes(r.role)&&r.capacity_model!=='WINDOW_QUANTITY'&&!excluded.has(r.id)&&(!resourceId||r.id===resourceId)):[];
 const resourceIds=new Set(resources.map(r=>r.id)),loads:Dict[]=certified?filteredResourceLoads(tasks,(analysis.resource_analysis.intervals??[]).filter((x:Dict)=>resourceIds.has(x.resource_id)),start,end,query,resourceId):[];
 const operations=tasks.filter(t=>Date.parse(t.start_at)<end&&Date.parse(t.end_at)>start&&(!term||[t.id,t.operation_id,t.name,t.itemId,t.manufacture_id,t.resource_id,t.resourceName].some(v=>String(v??'').toLocaleLowerCase().includes(term)))&&(!resourceId||loads.some(x=>x.operation_task_id===t.id)));
 return {operations,loads,resources:resources.filter(r=>!term||loads.some(x=>x.resource_id===r.id))};
}
export function unavailabilityRefusal({profile,target,runId,baselineRunId,resource,start,end}:{profile:string;target:string;runId:string;baselineRunId:string;resource?:Dict;start:string;end:string}){
 if(!supports(profile,'actions','resource_unavailability'))return capabilityReason(profile,'actions','resource_unavailability');
 if(target!=='baseline'||runId!==baselineRunId)return '此动作只允许当前原始基线；请返回原始基线创建另一项独立假设。';
 if(!resource||!['MACHINE','OPERATOR'].includes(resource.role))return '请选择已认证的连续设备或具名操作员；人员池与数量窗口不允许此动作。';
 if(resource.capacity_model==='WINDOW_QUANTITY'||!['PRESENT','EMPTY_CHECKED'].includes(resource.event_status))return '此资源未取得已认证的连续占用证据，不能创建不可用假设。';
 if(!resource.calendar_id||removedAvailability(resource.availability??[],start,end)<=0)return '当前窗口没有可扣减的原日历可用时段，请调整时间范围。';
 return '';
}
