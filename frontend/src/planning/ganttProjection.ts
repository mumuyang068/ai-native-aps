import type {Dict} from '../api/contracts';
import type {GanttRow,GanttInterval,GanttWindow,GanttLink} from '../components/ganttModel';
import type {TaskRecord,WorkspaceModel} from './planModel';
import {qty,unit,manufacturingLabel} from './planModel';
import {delayLabel,deliveryStatusLabel} from './orderModel';
import {compositeRows} from './compositeModel';
import {routingOperationLabel,workingScale} from './routingTimelineModel';
const windows=(xs:Dict[]|undefined):GanttWindow[]|null=>xs===undefined?null:xs.map(x=>({start:Date.parse(x.start_at),end:Date.parse(x.end_at)}));
const comparisonForTarget=(target:unknown):GanttInterval['comparison']=>target==='baseline'?'baseline':typeof target==='string'&&target.startsWith('DRAFT-')?'candidate':undefined;
export function taskInterval(t:TaskRecord,input:Dict,rowId:string,id=t.id):GanttInterval{
 const label=routingOperationLabel(input,t.operation_id)+' · '+qty(t.quantity)+' '+unit(t.uom);
 const nature=t.presentationNature??(t.origin==='EXISTING'?'planned':t.origin==='PROPOSED'?'proposed':'unknown');
 if(t.operation_kind==='AVAILABILITY_TRANSFER')return {id,taskId:t.id,rowId,start:Date.parse(t.start_at),end:Date.parse(t.end_at),label:'待入库 · '+qty(t.quantity)+' '+unit(t.uom),nature,details:[t.itemId,'计划入库可用转换；不代表实际FG入库','无设备或人员需求'],protected:true,protectedReason:'可用转换不是制造工序，不开放改量或换机。',feasibility:'UNKNOWN',kind:'availability_transfer'};
 return {id,taskId:t.id,rowId,start:Date.parse(t.start_at),end:Date.parse(t.end_at),label,nature,details:[t.itemId,t.resourceName,manufacturingLabel(t)],protected:t.origin==='EXISTING',protectedReason:t.origin==='EXISTING'?'已有工单受保护，请查看原状态与授权范围。':undefined,feasibility:'UNKNOWN',kind:'operation'};
}
function nativeTaskFeasibility(model:WorkspaceModel){
 const values=new Map<string,NonNullable<GanttInterval['feasibility']>>(),p=model.view?.order_evidence;
 if(!p||p.solve_run_id!==model.view.run?.solve_run_id||p.source_result_hash!==model.view.result?.result_hash)return values;
 for(const order of p.orders)for(const e of order.operations){const id=e.task?.id;if(!id||e.source?.solve_run_id!==p.solve_run_id)continue;const v=e.record.feasible;
  if(v===false||v==='false')values.set(id,'CONFLICT');else if((v===true||v==='true')&&values.get(id)!=='CONFLICT')values.set(id,'FEASIBLE');
 }return values;
}
export function scheduleProjection(model:WorkspaceModel,tasks:TaskRecord[],query:string){
 const resources=model.resources.filter(r=>!query.trim()||tasks.some(t=>t.resource_id===r.id));
 const rows:GanttRow[]=resources.map(r=>({id:r.id,label:r.name??r.id,detail:'计划工序 '+tasks.filter(t=>t.resource_id===r.id).length,calendar:windows(model.calendars.find(c=>c.id===r.availability_calendar_id)?.available_intervals)}));
 const ids=new Set(rows.map(r=>r.id)),feasibility=nativeTaskFeasibility(model),transfers=tasks.filter(t=>t.operation_kind==='AVAILABILITY_TRANSFER');if(transfers.length)rows.push({id:'availability-transfer',label:'计划入库可用转换',detail:'待入库 · 无设备/人员需求',calendar:null});
 return {rows,intervals:[...tasks.filter(t=>t.resource_id!==null&&ids.has(t.resource_id)).map(t=>({...taskInterval(t,model.input,t.resource_id!),feasibility:feasibility.get(t.id)??'UNKNOWN'})),...transfers.map(t=>taskInterval(t,model.input,'availability-transfer'))].map(x=>({...x,comparison:model.view?.draft?'candidate' as const:'baseline' as const}))};
}
/** Order rows are pegged to exact current-run task IDs, never inferred from an item or phase name. */
export function orderScheduleProjection(model:WorkspaceModel,tasks:TaskRecord[]){
 const p=model.view.order_evidence,valid=p&&p.solve_run_id===model.view.run?.solve_run_id&&p.source_result_hash===model.view.result?.result_hash;
 const rows:GanttRow[]=[],intervals:GanttInterval[]=[],assigned=new Set<string>(),byId=new Map(tasks.map(t=>[t.id,t])),feasibility=nativeTaskFeasibility(model);
 if(valid)for(const order of p.orders){
  const ids=new Set(order.operations.filter(e=>e.source?.solve_run_id===p.solve_run_id).map(e=>e.task?.id)),selected=[...ids].flatMap(id=>{const task=byId.get(id);return task?[task]:[]}),d=order.delivery,rowId='demand:'+order.demand_id;
  rows.push({id:rowId,label:order.order_ref+' · '+order.demand_id,detail:deliveryStatusLabel(d.status)+' · '+d.planned_quantity+'/'+d.requested_quantity,calendar:null,empty:selected.length?undefined:'当前运行没有关联的制造工序'});
  for(const t of selected){assigned.add(t.id);intervals.push({...taskInterval(t,model.input,rowId,t.id+'|'+order.demand_id),feasibility:feasibility.get(t.id)??'UNKNOWN',orderDelaySeconds:d.delay_seconds??undefined,details:[order.demand_id,'要求交期 '+d.due_at,deliveryStatusLabel(d.status),d.delay_seconds&&d.delay_seconds>0?'关联订单延期 '+delayLabel(d.delay_seconds):'关联订单未显示延期',manufacturingLabel(t)]})}
 }
 const unknown=tasks.filter(t=>!assigned.has(t.id));if(unknown.length){rows.push({id:'unallocated',label:'未分配到已核对订单',detail:valid?'无精确工序分配':'本运行订单分配证据不可用',calendar:null});for(const t of unknown)intervals.push(taskInterval(t,model.input,'unallocated'))}
 return {rows,intervals:intervals.map(x=>({...x,comparison:model.view?.draft?'candidate' as const:'baseline' as const}))};
}
export function compositeProjection(tasks:TaskRecord[],data:Dict,start:number,end:number,query:string,resourceId:string){
 const native=compositeRows(tasks,data,start,end,query,resourceId),byTask=new Map(tasks.map(t=>[t.id,t]));
 const rows:GanttRow[]=[...native.operations.map(t=>({id:'operation:'+t.id,label:t.name,detail:t.itemId+' · 工序窗口',calendar:null})),...native.resources.map(r=>({id:r.id,label:r.name??r.id,detail:(r.role==='OPERATOR'?'具名人员':'设备')+' · 容量 '+qty(r.capacity),calendar:windows(r.availability),empty:native.loads.some(l=>l.resource_id===r.id)?undefined:['PRESENT','EMPTY_CHECKED'].includes(r.event_status)?'本运行已查询，无匹配占用':'占用证据不足'}))];
 const intervals:GanttInterval[]=[...native.operations.map(t=>taskInterval(t,{},'operation:'+t.id)),...native.loads.flatMap(l=>{const t=byTask.get(l.operation_task_id);return t?[{...taskInterval(t,{},l.resource_id,l.id),start:Date.parse(l.start_at),end:Date.parse(l.end_at),eventId:l.id,kind:'resource' as const,details:[t.itemId,'负荷 '+qty(l.load_quantity),manufacturingLabel(t)]}]:[]})];
 return {rows,intervals:intervals.map(x=>({...x,comparison:comparisonForTarget(data.identity?.target)}))};
}
export function routingProjection(input:Dict,data:Dict,tasks:TaskRecord[],start:string,end:string,onlyGroup:boolean,groupId?:string,resourceId?:string){
 const intervals:Dict[]=data.resource_analysis?.intervals??[],taskMap=new Map(tasks.map(t=>[t.id,t]));
 const resources:Dict[]=(data.resource_analysis?.resources??[]).filter((r:Dict)=>!r.role?.endsWith('_POOL')&&(!onlyGroup||r.id===resourceId||intervals.some(l=>l.resource_id===r.id&&taskMap.get(l.operation_task_id)?.manufacture_id===groupId))).sort((a:Dict,b:Dict)=>Number(a.role==='OPERATOR')-Number(b.role==='OPERATOR'));
 const pool=(input.resources??[]).find((r:Dict)=>r.role==='MACHINE_POOL'),required=(input.routing_revisions??[]).flatMap((r:Dict)=>r.operations).find((o:Dict)=>o.resource_id===pool?.id)?.machine_skill_id;
 const rows:GanttRow[]=resources.map(r=>{
  const all=intervals.filter(l=>l.resource_id===r.id),q=(input.resource_skills??[]).find((q:Dict)=>q.resource_id===r.id&&q.skill_id===required&&q.priority>0&&Date.parse(q.effective_from)<=Date.parse(input.planning_at)&&Date.parse(q.effective_to)>=Date.parse(input.horizon.end_at));
  const candidate=(input.resources??[]).find((x:Dict)=>x.id===r.id)?.pool_id===pool?.id&&r.role==='MACHINE',checked=['PRESENT','EMPTY_CHECKED'].includes(r.event_status);
  const detail=candidate?(!q?'缺少设备资格 · 不可采用':`合格 · 优先 ${q.priority} · ${checked?(all.length?'实际采用':'本次未用'):'占用未核验'}`):`${r.role==='OPERATOR'?'具名人员':'实际设备'} · 容量 ${qty(r.capacity)}`;
  const hasVisible=all.some(l=>(!onlyGroup||taskMap.get(l.operation_task_id)?.manufacture_id===groupId)&&Date.parse(l.start_at)<Date.parse(end)&&Date.parse(l.end_at)>Date.parse(start));
  return {id:r.id,label:r.name??r.id,detail,calendar:windows(r.availability),empty:hasVisible?undefined:!checked?'占用证据不足':candidate&&!q?'无匹配资格':all.length?'占用在当前范围以外':candidate?'合格但本次未采用':'本运行已查询，无实际占用'};
 });
 const ids=new Set(rows.map(r=>r.id));
 const loads:GanttInterval[]=intervals.flatMap(l=>{const t=taskMap.get(l.operation_task_id);return t&&ids.has(l.resource_id)&&(!onlyGroup||t.manufacture_id===groupId)?[{...taskInterval(t,input,l.resource_id,l.id),start:Date.parse(l.start_at),end:Date.parse(l.end_at),eventId:l.id,kind:'resource' as const,chain:t.manufacture_id===groupId}]:[]});
 const chain=tasks.filter(t=>!!groupId&&t.manufacture_id===groupId).sort((a,b)=>Date.parse(a.start_at)-Date.parse(b.start_at));
 const links:GanttLink[]=chain.slice(1).flatMap((t,i)=>{const prev=chain[i];return prev.resource_id!==null&&t.resource_id!==null&&ids.has(prev.resource_id)&&ids.has(t.resource_id)?[{fromTask:prev.id,toTask:t.id,fromRow:prev.resource_id,toRow:t.resource_id,start:Date.parse(prev.end_at),end:Date.parse(t.start_at)}]:[]});
 const work=workingScale(resources.flatMap(r=>r.availability??[]),start,end);
 return {rows,intervals:loads.map(x=>({...x,comparison:comparisonForTarget(data.identity?.target)})),links,work,resources};
}
