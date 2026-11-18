import type {Dict} from '../api/contracts';
export type FoundationForecastScope={planId:string;versionId:string;runId:string;target:string;baselineRunId?:string};
export type FoundationForecastBucket=Record<string,string>;
export type FoundationForecastRow=Dict&{id:string;item_id:string;location_id:string;customer_id:string;uom:string;bucket_start:string;bucket_end:string;bucket:FoundationForecastBucket;source:Dict;source_gross_quantity:string;gross_quantity:string;net_quantity:null;consumed_quantity:null;policy_status:string};
export type FoundationForecastReport=Dict&{identity:Dict;rows:FoundationForecastRow[];firm_rows:Dict[];history:Dict[];totals:Dict};
export function filterFoundationForecast(rows:FoundationForecastRow[],filters:{item?:string;location?:string;period?:string}){return rows.filter(r=>(!filters.item||r.item_id===filters.item)&&(!filters.location||r.location_id===filters.location)&&(!filters.period||r.bucket_start===filters.period))}
function verifyIdentity(value:Dict,scope:FoundationForecastScope){
 const i=value?.identity,base=scope.baselineRunId??scope.runId;
 if(!i||i.plan_id!==scope.planId||i.data_version_id!==scope.versionId||i.baseline_run_id!==base||i.run_id!==base||i.target!==scope.target)throw Error('预测报告与当前计划、版本、基线或候选不一致，请刷新。');
 if(value.worker_eligible!==false||value.candidate_run_id!==null)throw Error('预测候选状态未核实。');
}
export function verifyFoundationForecastReport(value:Dict,scope:FoundationForecastScope):FoundationForecastReport{
 verifyIdentity(value,scope);
 if(!Array.isArray(value.rows)||!Array.isArray(value.firm_rows)||!Array.isArray(value.history)||value.policy_status!=='NET_UNKNOWN'||value.rows.some((r:Dict)=>r.net_quantity!==null||r.consumed_quantity!==null)||value.totals?.net_quantity!==null)throw Error('预测的来源值或未计算状态未核实。');
 return value as FoundationForecastReport;
}
function same(actual:Dict,expected:Dict){return !!actual&&Object.keys(actual).length===Object.keys(expected).length&&Object.entries(expected).every(([k,v])=>actual[k]===v)}
export function verifyFoundationForecastPreview(value:Dict,scope:FoundationForecastScope,bucket:FoundationForecastBucket,gross:string,reason:string){
 verifyIdentity(value,scope);
 if(!same(value.bucket,bucket)||value.gross_quantity!==gross||value.after?.gross_quantity!==gross||value.after?.net_quantity!==null||value.reason!==reason.trim()||value.engine_executed!==false||value.calculation_submitted!==false||!(/^[a-f0-9]{64}$/.test(value.confirmation?.sha256??'')))throw Error('预测预览与当前桶或输入不一致，请重新预览。');
 return value;
}
export function foundationForecastInputKey(scope:FoundationForecastScope,bucket:FoundationForecastBucket,gross:string,reason:string){return JSON.stringify([scope.planId,scope.versionId,scope.baselineRunId??scope.runId,scope.runId,scope.target,bucket,gross,reason.trim()])}
export function foundationForecastGross(value:string,before:string):string|null{
 if(!/^(0|[1-9][0-9]{0,11})(\.[0-9]{1,8})?$/.test(value))return null;
 function normalize(v:string){const[whole,fraction='']=v.split('.'),tail=fraction.replace(/0+$/,'');return whole+(tail?'.'+tail:'')}
 const normalized=normalize(value);return normalized===normalize(before)?null:normalized;
}
