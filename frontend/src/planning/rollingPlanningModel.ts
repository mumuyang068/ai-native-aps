import type {Dict} from '../api/contracts';
import type {ReportScope} from './reportModel';
import {siteInputToIso} from './analysisModel';
export function verifyWork(data:Dict,scope:ReportScope){
 for(const [key,value] of Object.entries({plan_id:scope.planId,data_version_id:scope.versionId,run_id:scope.runId,target:scope.target}))if(data.identity?.[key]!==value)throw Error('制造作业响应身份不一致：'+key);
 return data;
}
export function workRows(tasks:Dict[],filters:Record<string,string>,timezone='Asia/Shanghai'){
 const instant=(v:string)=>!v||/[Zz]|[+-]\d\d:\d\d$/.test(v)?v:siteInputToIso(v,timezone);
 const from=instant(filters.from),to=instant(filters.to);
 if((filters.from&&!from)||(filters.to&&!to)||(from&&to&&Date.parse(from)>=Date.parse(to)))return [];
 return tasks.filter(t=>(!filters.query||[t.id,t.operation_id,t.item_id,t.enterprise_reference,t.native_reference].some(v=>String(v??'').toLowerCase().includes(filters.query.toLowerCase())))&&
  (!filters.operation||t.operation_id===filters.operation)&&(!filters.location||t.location_id===filters.location)&&(!filters.status||t.status===filters.status)&&
  (!filters.resource||t.resource_ids?.includes(filters.resource))&&(!from||Date.parse(t.end_at)>Date.parse(from))&&(!to||Date.parse(t.start_at)<Date.parse(to)));
}
export function executionLabel(execution:Dict){return execution.status!=='PRESENT'?'实际执行记录未采集':execution.evidence_class==='SYNTHETIC_RESEARCH_ASSUMPTION'?'合成执行假设 · 非现场实际':'有来源的确认执行记录'}
