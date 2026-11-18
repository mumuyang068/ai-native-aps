import type {Dict} from '../api/contracts';
import {capabilityReason,inputProfile,supports} from './capabilityModel';
import {operationAlternatives} from './routingCapacityModel';
import {siteInputToIso} from './analysisModel';

type Action={allowed:boolean;reason:string};
type Binding={target:string;baselineRunId:string};
export type PlanObjectContext={
 task:Dict|undefined;manufacture:Dict|undefined;sourceOrder:Dict|undefined;sourceReason:string;profile:string;
 actualResources:{load:Dict;resource:Dict|undefined;calendar:Dict|undefined;qualifications:Dict[]}[];
 alternatives:ReturnType<typeof operationAlternatives>;move:Action;capacity:Action;unavailability:Action;
};
const rows=(value:unknown):Dict[]=>Array.isArray(value)?value:[];
const deny=(reason:string):Action=>({allowed:false,reason});
const instantEqual=(a:unknown,b:unknown)=>typeof a==='string'&&typeof b==='string'&&Number.isFinite(Date.parse(a))&&Date.parse(a)===Date.parse(b);
const quantityKey=(value:unknown)=>{const m=/^\+?(\d+)(?:\.(\d+))?$/.exec(String(value));return m?m[1].replace(/^0+(?=\d)/,'')+'.'+(m[2]??'').replace(/0+$/,''):null};

/** The server owns calendar, routing and native feasibility checks. */
export function continuousEditorWindow(input:Dict,task:Dict,value:string):Dict{
 const start=siteInputToIso(value,input.site.timezone),span=Date.parse(task.end_at)-Date.parse(task.start_at);
 if(!start||!Number.isFinite(span)||span<=0)throw Error('请选择有效的工厂时间。');
 const a=Date.parse(start),b=a+span;
 if(a<Date.parse(input.planning_at))throw Error('只能调整规划时点以后的未执行窗口。');
 if(a<Date.parse(input.horizon.start_at)||b>Date.parse(input.horizon.end_at))throw Error('完整工序窗口必须在本次规划范围内。');
 return {kind:'proposed_window',reference:task.id,start_at:start};
}
export function editorTaskQuantityNotice(input:Dict,task:Dict):string{
 const identity=input.foundation_projection?.native_operation_map?.[task.native_operation];
 const progress=rows(input.foundation_projection?.continuity_scope?.wip_jobs).find(j=>j.recipe_id===identity?.recipe_id);
 return progress?`原工单 ${progress.original_job_quantity} · 已入库 ${progress.finished_received_quantity} · 剩余 ${progress.remaining_quantity}；仅调整剩余工序，数量保持不变。`:`本次工序数量 ${task.quantity}，数量保持不变；可调整未来窗口或有资格的设备。`;
}

/** Selected objects and assignments come only from this result; source references are explicit business keys. */
export function planObjectContext(input:Dict,result:Dict,taskId:string,binding:Binding):PlanObjectContext{
 const operations=rows(result.operations).filter(t=>t.id===taskId),task=operations.length===1?operations[0]:undefined;
 const parents=task?[...rows(result.existing_manufacturing_orders),...rows(result.manufacture_suggestions)].filter(m=>m.id===task.manufacture_id):[];
 const manufacture=parents.length===1?parents[0]:undefined,profile=inputProfile(input);
 let sourceOrder:Dict|undefined,sourceReason='';
 if(!task)sourceReason='所选工序不在当前运行中，或工序身份重复，不能借用其他工序。';
 else if(!manufacture)sourceReason='工序的制造父对象缺失或身份重复，不能关联原始工单。';
 else if(manufacture.origin==='EXISTING'){
  const references=[manufacture.source_reference,manufacture.external_ref,manufacture.engine_ref].filter((v):v is string=>typeof v==='string'&&v.length>0);
  const matches=references.map(ref=>rows(input.existing_orders).filter(o=>o.type==='MO'&&(o.id===ref||o.external_ref===ref)));
  if(!references.length||matches.some(m=>m.length!==1)||new Set(matches.map(m=>m[0])).size!==1)sourceReason='原始工单的显式来源缺失、冲突或不唯一；不能从任务编号推断来源。';
  else{
   sourceOrder=matches[0][0];
   if(['item_id','recipe_id'].some(k=>manufacture[k]!==undefined&&String(manufacture[k])!==String(sourceOrder![k]))||(manufacture.quantity!==undefined&&(quantityKey(manufacture.quantity)===null||quantityKey(manufacture.quantity)!==quantityKey(sourceOrder.quantity))))sourceReason='当前制造对象的产品、工艺或数量与原始工单不一致。';
   else if(manufacture.source_status!==(sourceOrder.source_status??sourceOrder.status))sourceReason='当前制造对象状态与原始工单状态不一致。';
   else if(!rows(sourceOrder.operations).some(o=>o.operation_id===task.operation_id&&o.resource_id===task.resource_id&&instantEqual(o.start_at,task.start_at)&&instantEqual(o.end_at,task.end_at)))sourceReason='所选工序的资源或时间与原始工单条件不一致。';
  }
 }else sourceReason='当前对象为制造建议，没有原始确认工单；其时间和设备由原规划规则计算。';
 const actualResources=task?rows(result.resource_loads).filter(l=>l.operation_id===task.id).map(load=>{
  const resources=rows(input.resources).filter(r=>r.id===load.resource_id),resource=resources.length===1?resources[0]:undefined;
  const calendarId=resource?.availability_calendar_id??resource?.calendar_id,calendars=rows(input.calendars).filter(c=>c.id===calendarId);
  return {load,resource,calendar:calendars.length===1?calendars[0]:undefined,qualifications:rows(input.resource_skills).filter(q=>q.resource_id===load.resource_id)};
 }):[];
 const alternatives=task?operationAlternatives(input,task.operation_id):[];
 const currentBaseline=!!task&&binding.target==='baseline'&&!!binding.baselineRunId&&task.id.startsWith(binding.baselineRunId+':');
 const identityReason=!task||!manufacture?'工序或制造对象身份尚未完整核对。':!currentBaseline?'仅可从当前基线运行创建独立调整候选。':manufacture.origin==='EXISTING'&&sourceReason?sourceReason:'';
 let move:Action;
 if(identityReason)move=deny(identityReason);
 else if(sourceOrder?.protected===true||(profile==='foundation-planning-projection-v1'&&manufacture?.origin==='EXISTING'))move=deny('当前工单受原保护合同约束（LOCKED）；完整 SIM 的 confirmed/approved 工单均不开放窗口移动。');
 else if(sourceReason)move=deny(sourceReason);
 else if(!supports(profile,'actions','approved_mo_move')||profile!=='fixed-batch-window-v1')move=deny(capabilityReason(profile,'actions','approved_mo_move')||'当前模型未认证工单窗口移动。');
 else if(!sourceOrder||manufacture?.origin!=='EXISTING'||sourceOrder.source_status!=='approved'||manufacture.source_status!=='approved')move=deny('仅已认证模型中的原始 approved 工单可模拟窗口；confirmed 工单保持原保护条件。');
 else if(sourceOrder.completed_quantity===undefined||!/^0(?:\.0+)?$/.test(String(sourceOrder.completed_quantity)))move=deny('必须明确确认原工单完成数量为零；已完成或进度未知时不可移动。');
 else if(!Number.isFinite(Date.parse(input.planning_at))||Date.parse(sourceOrder.operations[0].start_at)<Date.parse(input.planning_at))move=deny('原工单窗口已开始或规划时点未知，不在已认证的窗口模拟范围内。');
 else move={allowed:true,reason:'可选择服务端核对过的有界窗口，保留原工时、批量、资源、资格、换线和其他工单保护条件。'};
 const capacity=identityReason?deny(identityReason):supports(profile,'actions','resource_capacity')&&alternatives.some(a=>a.qualified)?{allowed:true,reason:'可对有资格的机器输入不可用时段；备用设备选择由原 Worker 在合法约束内完成。'}:deny(capabilityReason(profile,'actions','resource_capacity')||'当前工序没有已认证的合格可替代设备。');
 const unavailability=identityReason?deny(identityReason):!supports(profile,'actions','resource_unavailability')?deny(capabilityReason(profile,'actions','resource_unavailability')):rows(input.existing_orders).some(o=>o.type==='MO')?deny('当前模型含受保护的已有制造工单，原资源不可用合同不允许创建停机或缺勤草案。'):{allowed:true,reason:'从原设备或具名人员的日历中减少可用时段；保持原资格和其他工单保护条件。'};
 return {task,manufacture,sourceOrder,sourceReason,profile,actualResources,alternatives,move,capacity,unavailability};
}
