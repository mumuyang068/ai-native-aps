import type {Dict,InputSnapshot,Operation,PlanView} from '../api/contracts';
export type TaskRecord=Operation&{uom:string;itemId:string;recipeId:string;name:string;resourceName:string;orderAllocation:null;origin?:string;sourceStatus?:string;externalRef?:string;presentationNature?:'planned'|'proposed'|'unknown'};
export type WorkspaceModel={tasks:TaskRecord[];input:Dict;snapshot:InputSnapshot;view:PlanView;resources:Dict[];calendars:Dict[]};
export function taskExtent(tasks:Pick<TaskRecord,'start_at'|'end_at'>[]){if(!tasks.length)return null;return {start:tasks.reduce((a,b)=>Date.parse(a.start_at)<Date.parse(b.start_at)?a:b).start_at,end:tasks.reduce((a,b)=>Date.parse(a.end_at)>Date.parse(b.end_at)?a:b).end_at}}
export function buildWorkspaceView(baseline:PlanView,selected:PlanView,snapshot:InputSnapshot):WorkspaceModel{
 const base=baseline.context?.enterprise_source,next=selected.context?.enterprise_source;
 if(!base||!next||base.plan_id!==next.plan_id||base.data_version_id!==next.data_version_id||base.snapshot_sha256!==next.snapshot_sha256)throw Error('计划身份或数据版本不一致，已停止展示。');
 if(snapshot.data_version.id!==base.data_version_id)throw Error('输入快照不属于当前数据版本，已停止展示。');
 const input=snapshot.plan_input,manufactures=new Map([...(selected.result?.manufacture_suggestions??[]),...(selected.result?.existing_manufacturing_orders??[])].map(r=>[r.id,r]));
 const existing=new Set((selected.result?.existing_manufacturing_orders??[]).map(r=>r.id)),proposed=new Set((selected.result?.manufacture_suggestions??[]).map(r=>r.id));
 const tasks=(selected.result?.operations??[]).map(o=>{const m=manufactures.get(o.manufacture_id),presentationNature:TaskRecord['presentationNature']=existing.has(o.manufacture_id)?'planned':proposed.has(o.manufacture_id)?'proposed':'unknown';return {...o,uom:m?.uom??'单位未提供',itemId:m?.item_id??'',recipeId:m?.recipe_id??'',name:o.operation_kind==='AVAILABILITY_TRANSFER'?'计划入库可用转换 · '+o.operation_id:o.operation_id,resourceName:o.resource_id??(o.operation_kind==='AVAILABILITY_TRANSFER'?'待入库 · 无资源需求':'资源未提供'),orderAllocation:null,origin:m?.origin,sourceStatus:m?.source_status,externalRef:m?.external_ref,presentationNature}}).sort((a,b)=>Date.parse(a.start_at)-Date.parse(b.start_at)||a.id.localeCompare(b.id));
 return {tasks,input,snapshot,view:selected,resources:input.resources??selected.context?.resource_calendars.resources??[],calendars:input.calendars??selected.context?.resource_calendars.calendars??[]};
}
export function filterTasks(model:WorkspaceModel,query:string):TaskRecord[]{const q=query.trim().toLowerCase();return model.tasks.filter(t=>!q||[t.id,t.operation_id,t.resource_id,t.itemId,t.manufacture_id,t.name,t.resourceName].some(v=>String(v??'').toLowerCase().includes(q)))}
export const unit=(v:string)=>({piece:'件',m:'米'}[v]??v);
export const manufacturingLabel=(t:Pick<TaskRecord,'origin'|'sourceStatus'>)=>t.origin==='EXISTING'?`已有工单 · ${t.sourceStatus??'状态未提供'}`:'制造建议 · proposed';
export function qty(v:string|number|null|undefined){if(v===null||v===undefined)return '—';const [whole,decimal]=String(v).split('.');return whole.replace(/\B(?=(\d{3})+(?!\d))/g,',')+(decimal?'.'+decimal:'')}
export const totalText=(rows:{quantity:string;uom:string}[]|undefined)=>rows?.map(r=>qty(r.quantity)+' '+unit(r.uom)).join(' / ')||'无';
export const businessLabel=(status:string|undefined)=>({FEASIBLE_ON_TIME:'预计按期',LATE_DELIVERY:'存在延期',PARTIAL_OR_UNPLANNED:'缺量或未排定',INVALID_SCHEDULE:'结果存在约束问题',DATA_INSUFFICIENT:'数据不足',SOLVE_FAILED:'求解失败',PENDING:'等待计算'}[status??'']??'等待读取结果');
export const runLabel=(state:string|undefined)=>({NOT_RUN:'尚未计算',NOT_SUBMITTED:'尚未提交',UNAVAILABLE:'结果暂不可核验',QUEUED:'等待计算',RUNNING:'计算中',SUCCEEDED:'计算完成',FAILED:'计算失败'}[state??'']??'尚未计算');
export function canRecoverUnsubmittedBaseline(target:string,planRun:{state?:string;solve_run_id?:string}|undefined,baseline:PlanView){
 return target==='baseline'&&planRun?.state==='NOT_SUBMITTED'&&!planRun.solve_run_id&&baseline.error==='BASELINE_NOT_READY'&&!baseline.run?.solve_run_id&&!baseline.result;
}
