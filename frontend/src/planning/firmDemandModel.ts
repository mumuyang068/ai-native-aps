import type {Dict} from '../api/contracts';
import type {DemandDecisionScope} from './demandDecisionModel';
const textKeys=['id','order_ref','line_ref','customer_id','item_id','location_id','uom','research_identity'];
const positive=(v:unknown)=>typeof v==='string'&&/^(0|[1-9][0-9]{0,11})(\.[0-9]{1,8})?$/.test(v)&&Number(v)>0;
export function firmInputValid(h:Dict,reason:string,permission:boolean){return permission&&reason.trim().length>0&&reason.trim().length<=1000&&textKeys.every(k=>typeof h[k]==='string'&&!!h[k].trim()&&h[k].length<=200)&&positive(h.quantity)&&positive(h.minimum_shipment)&&Number(h.minimum_shipment)<=Number(h.quantity)&&Number.isInteger(h.priority)&&h.priority>=0&&h.priority<=2147483647&&Number.isInteger(h.maximum_lateness_seconds)&&h.maximum_lateness_seconds>=0&&h.maximum_lateness_seconds<=2147483647&&typeof h.due_at==='string'&&/(Z|[+-]\d{2}:\d{2})$/.test(h.due_at)&&Number.isFinite(Date.parse(h.due_at))&&h.evidence_status==='SYNTHETIC_RESEARCH_ASSUMPTION'}
export function verifyFirmResponse(v:Dict,s:DemandDecisionScope,h:Dict,create:boolean){
 if(!v.identity||Object.entries({plan_id:s.planId,data_version_id:s.versionId,run_id:s.runId,baseline_run_id:s.runId,target:s.target}).some(([k,x])=>v.identity[k]!==x)||v.calculation_submitted!==false)throw Error('新增需求响应与当前计划、版本或基线不一致，请刷新核对。');
 const actual=create?v.draft?.changes?.hypothesis:v.input_hypothesis;
 if(!actual||Object.entries(h).some(([k,x])=>actual[k]!==x))throw Error('新增需求身份或输入与审阅内容不一致。');
 if(create?!(v.run?.state==='NOT_RUN'&&v.draft?.state==='DRAFT'&&v.draft?.run_id===null&&typeof v.draft?.id==='string'):!(v.engine_executed===false&&v.simulation_only===true))throw Error('响应未核实为未求解的独立假设。');
 return v;
}
