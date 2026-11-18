import type {Dict} from '../api/contracts';
import {formatSiteTime,localHourAt} from './timeRange';

export type ProfessionalScope={planId:string;versionId:string;baselineRunId:string;runId:string;target:string};
export function professionalIdentity(scope:ProfessionalScope){return {data_version_id:scope.versionId,baseline_run_id:scope.baselineRunId,run_id:scope.runId||scope.baselineRunId,target:scope.target}}
export function verifyProfessionalReport(value:Dict,scope:ProfessionalScope){
 const wanted={plan_id:scope.planId,...professionalIdentity(scope)};
 if(!value.identity||Object.entries(wanted).some(([k,v])=>value.identity[k]!==v)||value.simulation_only!==true||value.execution_authorized!==false)throw Error('专业政策记录不属于当前计划、版本、基线、运行与方案。');
 return value;
}
export function siteTimestamp(local:string,timezone:string){
 if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(local))throw Error('请明确填写日期和时间。');
 const [date,clock]=local.split('T'),[h,m]=clock.split(':').map(Number),value=localHourAt(date,h,timezone)+m*60000;
 if(formatSiteTime(value as unknown as string,timezone).replace(' ','T')!==local)throw Error('该时刻不符合企业时区。');
 return new Date(value).toISOString();
}
export function explicitNettingPolicy(data:Dict,version:string,source:string,ids:string[],dues:Record<string,string>){
 return {policy_version:version,approval_status:'RESEARCH_POLICY',source,timezone:data.timezone,
  dimensions:['item_id','location_id','uom','customer_id'],eligible_statuses:['OPEN'],bucket_grain:'EXPLICIT',early_buckets:0,late_buckets:0,
  excess_orders:'KEEP_FIRM',unmatched:'KEEP_FIRM',duplicates:'REJECT',negative:'REJECT',unknown_units:'REJECT',coverage:'GROSS_INCLUDES_FIRM_ORDERS',
  forecast_ids:ids,net_terms:{priority:20,minimum_shipment:'1',maximum_lateness_seconds:0,due_by_forecast:Object.fromEntries(ids.map(id=>[id,siteTimestamp(dues[id]??'',data.timezone)]))}};
}
export const inquiryStatus:Record<string,string>={CAN_PLAN_FULL:'当前条件下可计划满足',CAN_PLAN_PARTIAL:'当前条件下可部分满足',NEEDS_LATER_DATE:'可在更晚的试算日期计划满足',NO_FEASIBLE_SOLUTION_FOUND:'本次搜索未找到可行安排',REJECTED_BY_AUDIT_OR_COMMITMENT_PROTECTION:'业务核验或原订单保护不通过'};
export const nettingStatus:Record<string,string>={NETTED:'已按研究政策抵扣',NOT_SELECTED:'未选择',BUCKET_NOT_FULLY_COVERED:'预测桶超出规划范围，保持未知'};
