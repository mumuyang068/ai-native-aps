import type {Dict} from '../api/contracts';
import {priorityValue} from './demandDecisionModel';
import {siteInputToIso} from './analysisModel';

export type ControlledScope={planId:string;versionId:string;baselineRunId:string;runId:string;target:string;profile:string;sourceSha256?:string;snapshotSha256?:string};
export type ControlledFields={includeOrder:boolean;demandId:string;priority:string;includeOperator:boolean;resourceId:string;start:string;end:string;reason:string};
export type ControlledRequest={data_version_id:string;baseline_run_id:string;run_id:string;target:'baseline';order?:{demand_id:string;priority:number};operator?:{resource_id:string;start_at:string;end_at:string};reason:string};
export type ControlledPreview={identity:Dict;fingerprint:string;changes:Dict;simulation_only:true;engine_executed:false;calculation_submitted:false};
export type ControlledEditor={fields:ControlledFields;preview:ControlledPreview|null;acknowledged:boolean};
const profile='enterprise-accessory-supply-v1';
const sha=(value:unknown)=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);

export function firmConditionOrders(input:Dict):Dict[]{return (input.demand_lines??[]).filter((d:Dict)=>d.kind==='FIRM_ORDER'&&typeof d.id==='string'&&Number.isInteger(d.priority));}

// Match the profile's CUT -> SEW -> FINISH recipe roles, then use its actual
// operator requirement. Neither an operation name nor a person ID is assumed.
export function qualifiedSewOperators(input:Dict):Dict[]{
 const routes:Dict[]=input.routing_revisions??[],recipes:Dict[]=input.production_recipes??[];
 const cut=recipes.filter(p=>routes.find(r=>r.id===p.routing_revision_id)?.operations?.some((o:Dict)=>o.duration_model==='FIXED_BATCH'));
 const finished=(input.items??[]).filter((i:Dict)=>i.kind==='FINISHED');
 if(cut.length!==1||finished.length!==1)return [];
 const finish=recipes.filter(p=>p.output_item_id===finished[0].id);
 if(finish.length!==1)return [];
 const sew=recipes.filter(p=>p.id!==cut[0].id&&p.id!==finish[0].id);
 if(sew.length!==1)return [];
 const operations=routes.find(r=>r.id===sew[0].routing_revision_id)?.operations,requirement=operations?.length===1?operations[0].operator_requirement:null;
 const start=Date.parse(input.planning_at),end=Date.parse(input.horizon?.end_at);
 if(!requirement?.pool_id||!requirement?.skill_id||!Number.isFinite(start)||!Number.isFinite(end))return [];
 return (input.resources??[]).filter((r:Dict)=>r.role==='OPERATOR'&&(r.capacity_model??'CONTINUOUS')==='CONTINUOUS'&&r.pool_id===requirement.pool_id&&(input.resource_skills??[]).some((s:Dict)=>s.resource_id===r.id&&s.skill_id===requirement.skill_id&&Date.parse(s.effective_from)<=start&&Date.parse(s.effective_to)>=end));
}

export function controlledConditionsRequest(scope:ControlledScope,input:Dict,fields:ControlledFields,timezone:string):{body:ControlledRequest|null;error:string}{
 const fail=(error:string)=>({body:null,error});
 if(scope.profile!==profile||scope.target!=='baseline'||!scope.versionId||!scope.baselineRunId||scope.runId!==scope.baselineRunId)return fail('请在此企业计划已完成计算的原始基线准备受控条件。');
 if(!fields.includeOrder&&!fields.includeOperator)return fail('至少选择订单优先级或具名人员缺勤中的一项。');
 const reason=fields.reason.trim();if(!reason||reason.length>1000)return fail('请填写 1–1000 字的试算理由。');
 const body:ControlledRequest={data_version_id:scope.versionId,baseline_run_id:scope.baselineRunId,run_id:scope.runId,target:'baseline',reason};
 if(fields.includeOrder){
  const order=firmConditionOrders(input).find(d=>d.id===fields.demandId);
  if(!order)return fail('请选择当前输入中的精确 Firm 订单需求行。');
  const priority=priorityValue(fields.priority,order.priority);
  if(priority===null)return fail('新优先级须为 0–2147483647 的整数，且与原值不同。');
  body.order={demand_id:order.id,priority};
 }
 if(fields.includeOperator){
  const operator=qualifiedSewOperators(input).find(r=>r.id===fields.resourceId);
  if(!operator)return fail('请选择具有本次 SEW 工艺要求、且资格覆盖规划期的合格具名人员。');
  const start=siteInputToIso(fields.start,timezone),end=siteInputToIso(fields.end,timezone);
  if(!start||!end||Date.parse(start)>=Date.parse(end))return fail('请填写有先后顺序、按业务时区唯一解释的缺勤时段。');
  body.operator={resource_id:operator.id,start_at:start,end_at:end};
 }
 return {body,error:''};
}

export function updateControlledEditor(editor:ControlledEditor,patch:Partial<ControlledFields>):ControlledEditor{
 if(Object.entries(patch).every(([key,value])=>editor.fields[key as keyof ControlledFields]===value))return editor;
 return {fields:{...editor.fields,...patch},preview:null,acknowledged:false};
}

function verifyIdentity(value:Dict,scope:ControlledScope){
 const expected={plan_id:scope.planId,data_version_id:scope.versionId,baseline_run_id:scope.baselineRunId,run_id:scope.runId,target:scope.target,profile:scope.profile,...(scope.sourceSha256?{source_sha256:scope.sourceSha256}:{}),...(scope.snapshotSha256?{snapshot_sha256:scope.snapshotSha256}:{})};
 if(!value||Object.entries(expected).some(([key,want])=>value[key]!==want)||!sha(value.source_sha256)||!sha(value.snapshot_sha256))throw Error('预览来源与当前计划、数据版本或基线运行不一致，请重新打开并预览。');
}
function equalField(key:string,value:unknown,want:unknown){return ['start_at','end_at'].includes(key)?typeof value==='string'&&typeof want==='string'&&Number.isFinite(Date.parse(value))&&Date.parse(value)===Date.parse(want):value===want;}
function sameFields(value:Dict|undefined,expected:Dict|undefined){return expected?!!value&&Object.keys(value).length===Object.keys(expected).length&&Object.entries(expected).every(([key,want])=>equalField(key,value[key],want)):value===undefined;}
function verifyChanges(changes:Dict,request:ControlledRequest,input?:Dict){
 const conditions=changes?.conditions;
 if(changes?.action!=='simulate_controlled_conditions'||!conditions||Object.keys(conditions).some(k=>!['order','operator'].includes(k))||!sameFields(conditions.order,request.order)||!sameFields(conditions.operator,request.operator)||changes.reason!==request.reason||changes.simulation_only!==true||changes.engine_executed!==false||changes.execution_authorized!==false||changes.business_permission!=='NOT_CONFIRMED')throw Error('预览条件与当前表单不一致，请重新预览后确认。');
 if(request.order?changes.order?.demand_id!==request.order.demand_id||changes.order?.after_priority!==request.order.priority||!Number.isInteger(changes.order?.before_priority)||changes.order.before_priority===changes.order.after_priority:changes.order!==null)throw Error('订单优先级差异未完整核实，请重新预览。');
 if(request.operator?!Object.entries(request.operator).every(([key,want])=>equalField(key,changes.operator?.[key],want))||!changes.operator?.before_calendar_id||!changes.operator?.after_calendar_id||!(changes.operator?.removed_seconds>0):changes.operator!==null)throw Error('人员日历扣减差异未完整核实，请重新预览。');
 if(input&&request.order&&firmConditionOrders(input).find(d=>d.id===request.order!.demand_id)?.priority!==changes.order.before_priority)throw Error('原需求优先级与冻结输入不一致，请重新读取计划。');
 if(input&&request.operator&&qualifiedSewOperators(input).find(r=>r.id===request.operator!.resource_id)?.availability_calendar_id!==changes.operator.before_calendar_id)throw Error('原人员日历与冻结输入不一致，请重新读取计划。');
}
export function verifyControlledPreview(value:Dict,scope:ControlledScope,request:ControlledRequest,input?:Dict):ControlledPreview{
 verifyIdentity(value?.identity,scope);
 if(!sha(value.fingerprint)||value.simulation_only!==true||value.engine_executed!==false||value.calculation_submitted!==false)throw Error('响应未确认只读预览状态，已停止创建草案。');
 verifyChanges(value.changes,request,input);return value as ControlledPreview;
}
export function verifyControlledDraft(value:Dict,scope:ControlledScope,request:ControlledRequest,preview:ControlledPreview):string{
 verifyIdentity(value?.identity,scope);
 if(Object.entries(preview.identity).some(([key,want])=>value.identity[key]!==want)||value.fingerprint!==preview.fingerprint||value.simulation_only!==true||value.engine_executed!==false||value.calculation_submitted===true||value.run?.state!=='NOT_RUN'||value.run?.solve_run_id||typeof value.draft?.id!=='string'||!value.draft.id||value.draft.state!=='DRAFT'||value.draft.run_id!==null)throw Error('响应未提供本预览对应的 NOT_RUN 独立草案，请刷新当前计划核对。');
 verifyChanges(value.draft.changes,request);
 for(const part of ['order','operator']){
  const before=preview.changes[part],after=value.draft.changes[part];
  if(before&&(!after||Object.entries(before).some(([key,want])=>!['confirmation','before_intervals','after_intervals','provenance'].includes(key)&&!equalField(key,after[key],want))))throw Error('保存后的对象审计与本次预览不一致，请刷新计划核对。');
 }
 return value.draft.id;
}
