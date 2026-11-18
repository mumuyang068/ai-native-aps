import type {Dict} from '../api/contracts';
export type ReportScope={planId:string;versionId:string;runId:string;target:string};
export function verifyReport(value:Dict,scope:ReportScope){
 const expected={plan_id:scope.planId,data_version_id:scope.versionId,run_id:scope.runId,target:scope.target};
 if(!value?.identity||Object.entries(expected).some(([k,v])=>value.identity[k]!==v))throw Error('期间报表与当前计划、版本、方案或运行不一致。');
 return value;
}
export function ratioLabel(v:unknown){return v===null||v===undefined?'不适用':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:2})+'%'}
export function hoursLabel(v:unknown){return v===null||v===undefined?'未知':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:4})+' 小时'}
export function reportReturnSearch(previous:URLSearchParams,target:string){
 const p=new URLSearchParams({target,analysis:'reports',report:previous.get('return_report')??previous.get('report')??'demand',grain:['week','month'].includes(previous.get('grain')??'')?previous.get('grain')!:'day'});
 for(const k of ['report_demand_kind','cockpit_return','report_row','report_query','report_active','report_from','report_to','report_scope','report_group','report_compare','report_line_query','work_kind','work_status','work_sort','work_desc','work_columns'])if(previous.has(k))p.set(k,previous.get(k)!);
 for(const[k,v]of previous)if(k.startsWith('bucket_'))p.set(k,v);return p;
}
export function documentDate(row:Dict):string|null{return typeof row.at==='string'&&Number.isFinite(Date.parse(row.at))?row.at:null}
export function reportRows(rows:Dict[],mode:string,query:string,active:boolean){
 const term=query.trim().toLocaleLowerCase();return rows.filter(r=>{
  if(term&&![r.item_id,r.location_id,r.resource_id,r.reference,r.supplier_id,r.source_id,r.kind,r.status,r.operation_id,r.resource_ids?.join(' '),r.type,r.owner,r.demand_id,r.description].some(v=>String(v??'').toLocaleLowerCase().includes(term)))return false;
  if(!active||['documents','manufacturing','constraints','problems'].includes(mode))return true;
  if(mode==='demand'&&['NOT_COLLECTED','NOT_CHECKED'].includes(r.kind_evidence_status))return true;
  return (mode==='demand'?['due_quantity','planned_quantity','overdue_unfulfilled']:mode==='resource'?['load_hours']:mode==='transit'?['dispatched_quantity','arrived_quantity','in_transit_opening','in_transit_ending']:['inflow','outflow','qc_waiting_end','receipt_qc_waiting_end','planned_in_transit_end']).some(k=>Number.isFinite(Number(r[k]))&&Number(r[k])!==0);
 });
}
export function reportRowId(r:Dict){return r.id??(r.manufacture_ids?JSON.stringify(['MO_PERIOD',r.item_id,r.location_id,r.uom,r.operation_id,r.status,r.start_at,r.end_at]):r.origin_location_id!==undefined?JSON.stringify(['TRANSIT_PERIOD',r.item_id,r.uom,r.origin_location_id,r.destination_location_id,r.start_at,r.end_at]):null)??[r.item_id,r.location_id,r.resource_id,...(r.endpoint!==undefined?[r.uom,r.endpoint]:[]),r.start_at,r.end_at].map(x=>x??'').join('|')}

/** Keep a source report as navigation history, never as the new run's selection. */
export function reportDrillSearch(previous:URLSearchParams,scope:ReportScope,mode:string,fields:Record<string,string>={}){
 const p=new URLSearchParams(previous);
 if(previous.get('analysis')==='reports')p.set('report_origin',JSON.stringify({scope,search:previous.toString()}));
 p.set('return_report',previous.get('report')??'demand');p.set('analysis',mode);
 if(mode==='reports'&&fields.report&&fields.report!==previous.get('report'))p.delete('report_row');
 for(const key of ['analysis_event','analysis_task','analysis_item','analysis_location','analysis_resource'])p.delete(key);
 for(const [key,value] of Object.entries(fields))if(key.startsWith('analysis_')||key.startsWith('supply_')||key.startsWith('report_')||key==='report')p.set(key,value);
 return p;
}
export function reportOriginReturn(previous:URLSearchParams,allowed:ReportScope[]):URLSearchParams|null{
 try{
  const origin=JSON.parse(previous.get('report_origin')??'null');
  if(!origin||typeof origin.search!=='string'||!allowed.some(scope=>JSON.stringify(scope)===JSON.stringify(origin.scope)))return null;
  const p=new URLSearchParams(origin.search);
  if(p.get('analysis')!=='reports'||p.get('target')!==origin.scope.target)return null;
  if(p.get('report_scope')!==JSON.stringify(origin.scope))p.delete('report_row');
  p.delete('report_origin');return p;
 }catch{return null}
}
