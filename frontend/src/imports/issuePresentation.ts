import type {Issue} from '../api/contracts';
const CAPABILITY:Record<string,{title:string;message:string;resolution:string}>={
 UNSUPPORTED_RESERVATION:{title:'已预留库存暂不能参与排产',message:'库存中有指定用途的预留数量。当前系统还不能把预留库存与对应订单、释放条件一起计算，本次资料暂不能用于生成计划。',resolution:'保留原记录，并核对预留对应的订单和释放条件。该能力接入前不能生成计划；人工确认或直接扣减库存都不能解除此阻断。'},
 UNSUPPORTED_OPEN_PURCHASE:{title:'未完成采购暂不能纳入供需计算',message:'采购台账中有尚未完成的采购。当前系统还不能把这些在途供应纳入计算，继续排产可能造成重复采购或库存判断错误。',resolution:'保留采购记录，并核对未到货数量、预计到货时间及状态。该能力接入前不能生成计划，不能删除真实记录或把未到货数量加进现有库存。'},
 SUPPLY_VALIDITY_UNSUPPORTED:{title:'供应关系有效期尚未确认',message:'当前供应日期无法保证覆盖本次规划时段，可能影响实际到货建议。',resolution:'保留供应记录，核对生效和失效日期；无法确认有效性时不能生成计划。'}
};
export function presentIssue(issue:Issue){const known=CAPABILITY[issue.code];return {title:known?.title??issue.message,message:known?.message??'',resolution:known?.resolution??issue.resolution,blocked:issue.severity!=='WARNING',label:issue.severity==='WARNING'?'提示':issue.severity==='CAPABILITY'?'暂不能排产':'需核对资料'};}
