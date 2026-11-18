import type {Dict} from '../api/contracts';
export type InventoryDecisionScope={planId:string;versionId:string;baselineRunId:string;runId:string;target:string;profile:string;sourceSha256?:string;snapshotSha256?:string};
export type InventoryDecisionFields={includePolicy:boolean;includeSource:boolean;minimum:string;maximum:string;sourceOptionId:string;reason:string};
export type InventoryDecisionPolicy={item_id:string;location_id:string;uom:string;minimum:string;maximum:string;start_at:string;end_at:string};
export type InventoryDecisionRequest={data_version_id:string;baseline_run_id:string;run_id:string;target:'baseline';policy?:InventoryDecisionPolicy;source_option_id?:string;reason:string};
export type InventoryDecisionPreview=Dict&{identity:Dict;fingerprint:string;changes:Dict;simulation_only:true;engine_executed:false;calculation_submitted:false};
export type InventoryDecisionEditor={fields:InventoryDecisionFields;preview:InventoryDecisionPreview|null;acknowledged:boolean};
const profile='enterprise-accessory-supply-v1';
const sha=(v:unknown)=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const quantity=(v:unknown)=>typeof v==='string'&&/^(0|[1-9][0-9]{0,11})(\.[0-9]{1,8})?$/.test(v);
function scaled(v:string){const[a,b='']=v.split('.');return BigInt(a)*100000000n+BigInt(b.padEnd(8,'0'))}
function same(value:unknown,want:unknown):boolean{
 if(value===want)return true;
 if(!value||!want||typeof value!=='object'||typeof want!=='object'||Array.isArray(value)!==Array.isArray(want))return false;
 const entries=Object.entries(want);return Object.keys(value).length===entries.length&&entries.every(([key,v])=>same((value as Dict)[key],v));
}
function identity(value:Dict,scope:InventoryDecisionScope){
 const want={plan_id:scope.planId,data_version_id:scope.versionId,baseline_run_id:scope.baselineRunId,run_id:scope.runId,target:scope.target,profile:scope.profile,...(scope.sourceSha256?{source_sha256:scope.sourceSha256}:{}),...(scope.snapshotSha256?{snapshot_sha256:scope.snapshotSha256}:{})};
 if(!value||Object.entries(want).some(([key,v])=>value[key]!==v)||!sha(value.source_sha256)||!sha(value.snapshot_sha256))throw Error('响应与当前计划、资料版本、运行或来源 SHA 不一致，请重新读取。');
}
export function verifyInventoryDecisionReport(value:Dict,scope:InventoryDecisionScope,itemId:string,locationId:string):Dict{
 identity(value?.identity,scope);
 if(scope.profile!==profile||!Array.isArray(value.catalog)||!value.catalog.length||value.simulation_only!==true||value.business_permission!=='NOT_CONFIRMED'||value.execution_authorized!==false)throw Error('当前资料没有已声明的库存与补给条件，不能创建假设。');
 if(!Array.isArray(value.stock_facts)||value.stock_facts.some((row:Dict)=>row.item_id!==itemId||row.location_id!==locationId)||!value.source||value.source.item_id!==itemId||value.source.location_id!==locationId||value.catalog.some((o:Dict)=>!o.id||!o.label||o.item_id!==itemId||o.location_id!==locationId||o.source_id!==value.source.id||o.fact_kind!=='SYNTHETIC_RESEARCH_ASSUMPTION'||!o.uom||!o.provenance)||new Set(value.catalog.map((o:Dict)=>o.id)).size!==value.catalog.length)throw Error('来源目录与精确物料、地点或冻结来源不一致，已停止展示。');
 return value;
}
export function inventoryDecisionRequest(scope:InventoryDecisionScope,report:Dict,input:Dict,itemId:string,locationId:string,fields:InventoryDecisionFields):{body:InventoryDecisionRequest|null;error:string}{
 const fail=(error:string)=>({body:null,error});
 if(scope.profile!==profile||scope.target!=='baseline'||!scope.baselineRunId||scope.runId!==scope.baselineRunId)return fail('请切回已完成计算的原始基线，准备一个新的独立候选。');
 try{verifyInventoryDecisionReport(report,scope,itemId,locationId)}catch(e){return fail(e instanceof Error?e.message:'当前范围未核实。')}
 if(!fields.includePolicy&&!fields.includeSource)return fail('至少选择库存目标或有来源的补给条件。');
 const reason=fields.reason.trim();if(!reason||reason.length>1000)return fail('请填写 1–1000 字的试算理由。');
 const body:InventoryDecisionRequest={data_version_id:scope.versionId,baseline_run_id:scope.baselineRunId,run_id:scope.runId,target:'baseline',reason};
 if(fields.includePolicy){
  if(!quantity(fields.minimum)||!quantity(fields.maximum)||scaled(fields.minimum)>scaled(fields.maximum))return fail('最低和最高目标须为非负明确数值，最多 8 位小数，且最低不高于最高。');
  const start=input.planning_at,end=input.horizon?.end_at;
  if(!start||!end||!Number.isFinite(Date.parse(start))||Date.parse(start)>=Date.parse(end))return fail('规划期间未完整核实，不能定义政策有效期。');
  body.policy={item_id:itemId,location_id:locationId,uom:report.catalog[0].uom,minimum:fields.minimum,maximum:fields.maximum,start_at:start,end_at:end};
 }
 if(fields.includeSource){const option=report.catalog.find((o:Dict)=>o.id===fields.sourceOptionId);if(!option)return fail('请选择当前冻结目录中的一个来源条款，不可临时填写供应事实。');body.source_option_id=option.id}
 return {body,error:''};
}
export function updateInventoryEditor(editor:InventoryDecisionEditor,patch:Partial<InventoryDecisionFields>):InventoryDecisionEditor{
 if(Object.entries(patch).every(([key,v])=>editor.fields[key as keyof InventoryDecisionFields]===v))return editor;
 return {fields:{...editor.fields,...patch},preview:null,acknowledged:false};
}
function policyMatches(value:Dict,want:InventoryDecisionPolicy){return !!value&&Object.entries(want).every(([key,v])=>key.endsWith('_at')?Date.parse(value[key])===Date.parse(v):value[key]===v)}
function verifyChanges(value:Dict,request:InventoryDecisionRequest,report:Dict){
 const conditions={...(request.policy?{policy:request.policy}:{}),...(request.source_option_id?{source_option_id:request.source_option_id}:{})};
 if(value?.action!=='simulate_inventory_decision'||!same(value.conditions,conditions)||value.reason!==request.reason||value.simulation_only!==true||value.business_permission!=='NOT_CONFIRMED'||value.execution_authorized!==false||value.engine_executed!==false)throw Error('预览条件、理由或模拟边界与当前表单不一致，请重新预览。');
 if(request.policy&&!policyMatches(value.after?.policy,request.policy))throw Error('候选库存目标、单位或有效期未完整核实。');
 if(!request.policy&&!same(value.after?.policy,value.before?.policy))throw Error('未声明修改的库存政策发生变化，已停止创建。');
 if(!request.source_option_id&&!same(value.after?.supply,value.before?.supply))throw Error('未声明修改的来源条款发生变化，已停止创建。');
 if(value.before?.catalog&&(!same(value.before.catalog,value.after?.catalog)||!same(value.before.catalog,report.catalog)))throw Error('冻结条款目录发生变化，已停止创建。');
 if(request.source_option_id){
  const option=report.catalog.find((o:Dict)=>o.id===request.source_option_id),supply=value.after?.supply;
  if(!option||!supply||!Object.entries(option).every(([key,v])=>same(supply[key],v))||supply.unit_price!==null||supply.currency!==null||supply.price_status!=='NOT_PROVIDED')throw Error('候选条款不属于冻结来源，或将未知单价误认为已知金额。');
 }
}
export function verifyInventoryDecisionPreview(value:Dict,scope:InventoryDecisionScope,request:InventoryDecisionRequest,report:Dict):InventoryDecisionPreview{
 identity(value?.identity,scope);
 if(!sha(value.fingerprint)||value.simulation_only!==true||value.engine_executed!==false||value.calculation_submitted!==false)throw Error('响应没有确认只读预览，已停止创建草案。');
 verifyChanges(value.changes,request,report);return value as InventoryDecisionPreview;
}
export function verifyInventoryDecisionDraft(value:Dict,scope:InventoryDecisionScope,request:InventoryDecisionRequest,preview:InventoryDecisionPreview,report:Dict):string{
 identity(value?.identity,scope);
 const draft=value?.draft;
 if(!same(value.identity,preview.identity)||value.fingerprint!==preview.fingerprint||value.simulation_only!==true||value.engine_executed!==false||value.calculation_submitted===true||value.run?.state!=='NOT_RUN'||value.run?.solve_run_id||!draft?.id||draft.state!=='DRAFT'||draft.run_id!==null)throw Error('保存结果不是本次预览对应的 NOT_RUN 草案，请刷新计划核对。');
 verifyChanges(draft.changes,request,report);
 if(!same(draft.changes.before,preview.changes.before)||!same(draft.changes.after,preview.changes.after))throw Error('保存后的原值或候选值与已接受预览不一致。');
 return draft.id;
}
export function inventoryPolicyLines(policy:Dict|null|undefined,series:Dict,a:number,b:number,auditStatus?:string){
 if(!policy||auditStatus!=='PASS'||series.complete!==true||policy.item_id!==series.item_id||policy.location_id!==series.location_id||policy.uom!==series.uom||!quantity(policy.minimum)||!quantity(policy.maximum)||scaled(policy.minimum)>scaled(policy.maximum))return null;
 const start=Math.max(a,Date.parse(policy.start_at)),end=Math.min(b,Date.parse(policy.end_at));if(!Number.isFinite(start)||!Number.isFinite(end)||start>=end)return null;
 return {minimum:Number(policy.minimum),maximum:Number(policy.maximum),start,end};
}
const transient=['run_id','data_version_id','analysis_event','analysis_task','analysis_calendar_window','analysis_return_event','analysis_return_task','policy_row','policy_event','policy_selection_scope','supply_scope','supply_doc','supply_event'];
export function inventoryDecisionSearch(previous:URLSearchParams,scope:InventoryDecisionScope,itemId:string,locationId:string){
 const next=new URLSearchParams(previous);
 if(!next.has('inventory_decision_return')){next.set('inventory_decision_return',previous.toString());next.set('inventory_decision_return_scope',JSON.stringify([scope.planId,scope.versionId,scope.runId,scope.target]))}
 next.set('analysis','inventory-decision');next.set('analysis_item',itemId);next.set('analysis_location',locationId);return next;
}
export function inventoryDecisionReturn(previous:URLSearchParams,scope:InventoryDecisionScope){
 let origin:unknown[]=[];try{const v=JSON.parse(previous.get('inventory_decision_return_scope')??'[]');if(Array.isArray(v))origin=v}catch{/* A stale origin is never adopted. */}
 const valid=origin[0]===scope.planId&&origin[1]===scope.versionId;
 const next=valid?new URLSearchParams(previous.get('inventory_decision_return')??''):new URLSearchParams({analysis:'schedule'});
 if(!valid||origin[2]!==scope.runId||origin[3]!==scope.target)for(const key of transient)next.delete(key);
 next.set('target',scope.target);next.delete('inventory_decision_return');next.delete('inventory_decision_return_scope');return next;
}

/** Read-only diagnostics from the current authenticated projection. A first gap
 * is not the time of the minimum, and absent duration is never inferred. */
export function inventoryPolicyOutcomeRows(outcome:Dict|undefined|null,uom:string,time:(v:string)=>string):[string,string][]{
 const unknown='未采集 / 未核验',present=outcome?.status==='PRESENT';
 const q=(field:string)=>present&&outcome?.[field]!==null&&outcome?.[field]!==undefined?String(outcome[field])+' '+({piece:'件',set:'套',m:'米'}[uom]??uom):unknown;
 const at=(field:string)=>present&&typeof outcome?.[field]==='string'&&Number.isFinite(Date.parse(outcome[field]))?time(outcome[field]):unknown;
 return [['期间最低净可用',q('minimum_quantity')],['最低余额时点',at('minimum_at')],['首次低于最低目标',present&&outcome?.goal_status==='GOAL_MET'&&!outcome.first_gap_at?'未观察到':at('first_gap_at')],['期末净可用',q('ending_quantity')],['期末目标差额',q('ending_gap_quantity')],['期间最大目标差额',q('maximum_gap_quantity')],['低于目标累计时长',present&&typeof outcome?.gap_duration_seconds==='number'&&Number.isFinite(outcome.gap_duration_seconds)?String(outcome.gap_duration_seconds/60)+' 分钟':unknown]];
}

export function inventoryPolicyEvidenceText(policy:Dict|null|undefined,status?:string){
 if(status==='FAIL')return '原生政策字段存在违规';
 if(status==='PASS')return policy?'已声明目标，原生字段核验通过':'未声明目标，原生缺省隔离已核验';
 return policy?'政策原生字段未核验':'缺省隔离尚未核验';
}

/** Both object-local review actions review this candidate, not another draft
 * chosen by the generic review defaults. Baseline retains those defaults. */
export function inventoryDecisionReviewSearch(previous:URLSearchParams,scope:InventoryDecisionScope){
 const next=new URLSearchParams(previous);next.set('analysis','review');next.set('target',scope.target);
 if(scope.target!=='baseline')next.set('review_drafts',scope.target);
 return next;
}
