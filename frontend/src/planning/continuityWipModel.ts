/** Presentation only; quantities remain owned by the same-run continuity audit. */
import type {ContinuityWipContext,Dict} from '../api/contracts';

function quantity(value:unknown){return (typeof value==='number'&&Number.isFinite(value)||typeof value==='string'&&value.trim()!==''&&Number.isFinite(Number(value)))?String(value):'未提供'}
const usage:Record<string,string>={NOT_USED:'原生规划尚未采用该 WIP',PARTIALLY_USED:'原生规划仅使用部分 WIP',USED_IN_NATIVE_PLAN:'原生规划已使用该 WIP，实际续排完成尚未认证'};
export function continuityWipModel(value:ContinuityWipContext|null|undefined){
 if(!value)return null;
 const evidence=value.status==='INVALID_EVIDENCE'||value.status==='NOT_COLLECTED'?'当前运行的 WIP 审计证据不完整':value.audit_status==='FAIL'?'WIP 守恒审计未通过':value.status==='EMPTY_CHECKED'?'本次审计没有 WIP 记录':'已读取本次原生规划的 WIP 守恒审计';
 const jobs=(value.status==='INVALID_EVIDENCE'?[]:value.rows).map(row=>({jobId:row.job_id??'工单未提供',orderId:row.order_id??'订单未提供',opening:quantity(row.opening),used:quantity(row.used),remaining:quantity(row.remaining),
  status:row.evidence_status==='AUDITED'?(usage[row.continuation_status]??'尚无完整的 WIP 使用核验'):'尚无完整的 WIP 使用核验',
  prefix:Array.isArray(row.completed_prefix)?row.completed_prefix.map((op:Dict)=>({id:String(op.operation_id??'工序未提供'),quantity:quantity(op.quantity),completedAt:op.actual_end})):null,
  materials:Array.isArray(row.issued_materials)?row.issued_materials.map((issue:Dict)=>({id:String(issue.id??issue.lot_id??'领料记录未提供'),lotId:String(issue.lot_id??'批次未提供'),quantity:quantity(issue.quantity)+(issue.unit?' '+String(issue.unit):'（单位未提供）')})):null,
  source:row.source}));
 return {runId:value.run_id??'未提供',sourceAsOf:value.source_as_of,evidence,certification:'WIP 续排执行仍未认证',jobs,countLabel:['PRESENT','EMPTY_CHECKED'].includes(value.status)?jobs.length+' 笔':'记录未提供',
  protection:value.execution_protection?.status==='SERVER_ENFORCED'?'服务端在采纳和执行前核对已有任务、剩余需求与原生批次，阻止重复领料或重复制造。':'尚未取得服务端执行保护证据。'};
}
