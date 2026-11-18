import type {Dict} from '../api/contracts';
import type {DemandDecisionScope} from './demandDecisionModel';
import {supports} from './capabilityModel';

export type AccessoryScope=DemandDecisionScope&{baselineRunId:string};
export type AccessoryOption=Dict&{source_id:string;supplier_id:string;item_id:string;location_id:string;uom:string;enabled:boolean;price_status:string};
export type AccessoryReport={identity:Dict;option:AccessoryOption;simulation_only:boolean;timezone?:string;reason?:string;purchase_orders?:Dict[]|null;validation?:Dict;status?:string};
function stable(value:unknown):string{return JSON.stringify(value&&typeof value==='object'&&!Array.isArray(value)?Object.fromEntries(Object.entries(value).sort(([a],[b])=>a.localeCompare(b)).map(([key,v])=>[key,JSON.parse(stable(v)??'null')])):value)??'null'}
function sameIdentity(value:Dict|undefined,scope:AccessoryScope){return !!value&&value.plan_id===scope.planId&&value.data_version_id===scope.versionId&&value.run_id===scope.runId&&value.baseline_run_id===scope.baselineRunId&&value.target===scope.target}
function sameOption(value:AccessoryOption|undefined,option:AccessoryOption,enabled:boolean){const frozen=(source:AccessoryOption)=>Object.fromEntries(Object.entries(source).filter(([key])=>key!=='enabled'));return !!value&&value.enabled===enabled&&stable(frozen(value))===stable(frozen(option))}
function sameChanges(value:Dict|undefined,option:AccessoryOption,reason:string){return !!value&&Object.keys(value).every(key=>['action','intent','source_id','reason','before','after','simulation_only','option_summary'].includes(key))&&value.action==='enable_accessory_supply'&&value.source_id===option.source_id&&value.reason===reason.trim()&&stable(value.before)===stable({enabled:false})&&stable(value.after)===stable({enabled:true})}
export function verifyAccessorySupply(value:Dict,scope:AccessoryScope):AccessoryReport{
 if(!sameIdentity(value?.identity,scope)||!supports(value.identity?.profile,'actions','accessory_supply')||value.simulation_only!==true||!value.option||['source_id','supplier_id','item_id','location_id','uom'].some(key=>typeof value.option[key]!=='string'||!value.option[key].trim())||typeof value.option.enabled!=='boolean'||value.option.fact_kind!=='SYNTHETIC_RESEARCH_ASSUMPTION'||value.option.permission!=='SIMULATION_ONLY')throw Error('辅料来源响应与当前计划、版本、运行、来源许可或方案不一致，请重新读取。');
 return value as AccessoryReport;
}
export function verifyAccessoryPreview(value:Dict,scope:AccessoryScope,option:AccessoryOption,reason:string):Dict{
 if(!sameIdentity(value?.identity,scope)||value.simulation_only!==true||value.calculation_submitted!==false||value.engine_executed!==false||!sameOption(value.original,option,false)||!sameOption(value.proposed,option,true)||!sameChanges(value.changes,option,reason))throw Error('预览未核实为同一来源的单项模拟启用，请重新核对。');
 return value;
}
export function verifyAccessoryDraft(value:Dict,scope:AccessoryScope,option:AccessoryOption,reason:string):string{
 const draft=value?.draft;
 if(!sameIdentity(value?.identity,scope)||value.simulation_only!==true||value.calculation_submitted!==false||value.run?.state!=='NOT_RUN'||!draft||typeof draft.id!=='string'||!draft.id||draft.state!=='DRAFT'||draft.run_id!==null||!sameChanges(draft.changes,option,reason))throw Error('响应未核实为本次来源的 NOT_RUN 独立草案，请刷新计划核对。');
 return draft.id;
}
export function accessoryPrice(option:AccessoryOption){return option.price_status==='PROVIDED'&&option.unit_price!=null&&option.currency?String(option.unit_price)+' '+option.currency:'未提供'}
const transient=['analysis_calendar_window','supply_doc','supply_event','supply_scope','analysis_event','analysis_task','policy_row','policy_event','policy_selection_scope','order_line_id','run_id','data_version_id'];
export function accessorySearch(previous:URLSearchParams,scope:AccessoryScope){
 const next=new URLSearchParams(previous);
 if(!next.has('accessory_return')){next.set('accessory_return',previous.toString());next.set('accessory_return_scope',JSON.stringify([scope.planId,scope.versionId,scope.runId,scope.target]))}
 next.set('analysis','accessory-supply');return next;
}
export function accessoryReturnSearch(previous:URLSearchParams,scope:AccessoryScope){
 let origin:unknown[]=[];try{const parsed:unknown=JSON.parse(previous.get('accessory_return_scope')??'[]');if(Array.isArray(parsed))origin=parsed}catch{/* Invalid origin is never borrowed. */}
 const samePlan=origin[0]===scope.planId&&origin[1]===scope.versionId;
 const next=samePlan?new URLSearchParams(previous.get('accessory_return')??''):new URLSearchParams({analysis:'schedule'});
 if(!samePlan||origin[2]!==scope.runId||origin[3]!==scope.target)for(const key of transient)next.delete(key);
 next.set('target',scope.target);next.delete('accessory_return');next.delete('accessory_return_scope');return next;
}
