import type {Dict} from '../api/contracts';
export type Analysis=Dict;
export type Point={at:number;balance:number;eventId?:string};
/** Ledger transactions use [start,end); graph continuity can retain its endpoint. */
export function inventoryLedgerEvents(events:Dict[],start:string,end:string){const a=Date.parse(start),b=Date.parse(end);return events.filter(e=>Date.parse(e.at)>=a&&Date.parse(e.at)<b)}
export function analysisUnit(value:string|undefined){return ({set:'套',piece:'件',m:'米'} as Record<string,string>)[value??'']??value??'单位未提供'}
export const eventLabels:Record<string,string>={RECEIPT_QC_RELEASE:'已收物料预计QC可用',PURCHASE_AVAILABLE:'采购QC后可用',MANUFACTURE_OUTPUT:'制造产出',MANUFACTURE_CONSUMPTION:'制造领用',DISTRIBUTION_DISPATCH:'计划配送发运',DISTRIBUTION_RECEIPT:'计划配送到货',DEMAND_CONSUMPTION:'需求计划消耗',NATIVE_MATERIAL_EVENT:'原生物料事件'};
export function statusMessage(status:string){return ({PRESENT:'已取得原生事件',EMPTY_CHECKED:'已查询并确认没有对应事件',NOT_COLLECTED:'必要证据未采集，不能显示为0',UNSUPPORTED:'当前profile未认证此分析',UNREADABLE:'证据文件不可读取，请核对归档',INVALID_EVIDENCE:'来源摘要不一致，已停止展示'} as Record<string,string>)[status]??'证据状态未知'}
export function analysisIndex(a:Analysis){
 const byResource=new Map<string,Dict[]>(),byTask=new Map<string,Dict[]>(),series=new Map<string,Dict>();
 for(const r of a.resource_analysis.intervals??[]){byResource.set(r.resource_id,[...(byResource.get(r.resource_id)??[]),r]);if(r.operation_task_id)byTask.set(r.operation_task_id,[...(byTask.get(r.operation_task_id)??[]),r])}
 for(const s of a.inventory_analysis.series??[])series.set(s.id,s);
 return {byResource,byTask,series};
}
export function inventoryPoints(series:Dict,start:string,end:string):Point[]{
 const a=Date.parse(start),b=Date.parse(end);let balance=Number(series.opening);
 if(!Number.isFinite(balance)||!Number.isFinite(a)||!Number.isFinite(b)||a>=b)return [];
 const events=series.events??[];
 for(const e of events)if(Date.parse(e.at)<a)balance=Number(e.balance);
 if(!series.complete&&!events.length)return [];
 const points:Point[]=[{at:a,balance}];
 for(const e of events){const at=Date.parse(e.at);if(at>=a&&at<b){balance=Number(e.balance);points.push({at,balance,eventId:e.id})}}
 if(series.complete&&b<=Date.parse(series.verified_through)&&points.at(-1)!.at<b)points.push({at:b,balance});
 return points;
}
export function stepPath(points:Point[],x:(v:number)=>number,y:(v:number)=>number){return points.map((p,i)=>i?`H ${x(p.at)} V ${y(p.balance)}`:`M ${x(p.at)} ${y(p.balance)}`).join(' ')}
export function compareInventory(base:Analysis,current:Analysis,id:string){
 if(base.identity.plan_id!==current.identity.plan_id||base.identity.data_version_id!==current.identity.data_version_id)return null;
 const before=base.inventory_analysis.series.find((s:Dict)=>s.id===id),after=current.inventory_analysis.series.find((s:Dict)=>s.id===id);
 return before&&after?{before:before.ending,after:after.ending,eventsBefore:before.events.length,eventsAfter:after.events.length}:null;
}
export function eventRange(a:Analysis,mode:string){
 const rows:Dict[]=mode==='resources'?(a.resource_analysis.intervals??[]):(a.inventory_analysis.series??[]).flatMap((s:Dict)=>s.events);
 if(!rows.length)return {start_at:a.planning_at,end_at:a.horizon_end};
 const pad=mode==='resources'?1200000:3600000;
 return {start_at:new Date(Math.max(Date.parse(a.planning_at),Math.min(...rows.map(r=>Date.parse(r.start_at??r.at)))-pad)).toISOString(),end_at:new Date(Math.min(Date.parse(a.horizon_end),Math.max(...rows.map(r=>Date.parse(r.end_at??r.at)))+pad)).toISOString()};
}

/** Exact native task identity only. These are run-level events, not order allocations. */
export function taskMaterialEvents(a:Analysis,taskId:string|undefined){
 if(!taskId)return [];
 return (a.inventory_analysis.series??[]).flatMap((series:Dict)=>(series.events??[])
  .filter((event:Dict)=>event.operation_task_id===taskId).map((event:Dict)=>({series,event})));
}
export function siteDateTimeInput(iso:string,timezone:string){
 const p=Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:timezone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(new Date(iso)).map(p=>[p.type,p.value]));
 return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}:${p.second}`;
}
/** Reject missing and ambiguous local times rather than using the browser timezone. */
export function siteInputToIso(value:string,timezone:string):string|null{
 const text=value.length===16?value+':00':value;
 if(!/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$/.test(text))return null;
 const wall=Date.parse(text+'Z');if(!Number.isFinite(wall))return null;
 const offsets=new Set<number>();
 for(let hours=-36;hours<=36;hours+=6){const candidate=wall+hours*3600000;offsets.add(Date.parse(siteDateTimeInput(new Date(candidate).toISOString(),timezone)+'Z')-candidate)}
 const matches=[...offsets].map(offset=>wall-offset).filter(at=>siteDateTimeInput(new Date(at).toISOString(),timezone)===text);
 return matches.length===1?new Date(matches[0]).toISOString():null;
}
export function removedAvailability(windows:Dict[],start:string,end:string){
 const a=Date.parse(start),b=Date.parse(end);if(!Number.isFinite(a)||!Number.isFinite(b)||a>=b)return 0;
 return windows.reduce((total:number,w:Dict)=>total+Math.max(0,Math.min(b,Date.parse(w.end_at))-Math.max(a,Date.parse(w.start_at)))/1000,0);
}
const originFields=['resource','task','event','start','end'] as const;
export function rememberAnalysisOrigin(previous:URLSearchParams,origin:{resource?:string;task?:string;event?:string;start:string;end:string}){
 const p=new URLSearchParams(previous);p.set('return_analysis','resources');
 for(const k of originFields){const v=origin[k];if(v)p.set('analysis_return_'+k,v);else p.delete('analysis_return_'+k)}
 return p;
}
export function analysisReturnSearch(previous:URLSearchParams,target:string){
 const p=new URLSearchParams(previous);p.set('target',target);p.set('analysis','resources');
 for(const k of originFields){const v=p.get('analysis_return_'+k);if(v)p.set('analysis_'+k,v);else p.delete('analysis_'+k);p.delete('analysis_return_'+k)}
 p.delete('return_analysis');p.delete('analysis_item');p.delete('analysis_location');return p;
}
/** Route-local navigation only; never carry another run_id into a new scenario. */
export function analysisContextSearch(previous:URLSearchParams,target:string){
 const p=new URLSearchParams({target});
 for(const[k,v]of previous)if(k==='analysis'||k.startsWith('cockpit_')||k.startsWith('analysis_')||k.startsWith('exception_')||k.startsWith('review_')||k.startsWith('handoff_')||k.startsWith('mf_')||k.startsWith('manufacturing_return')||['professional_return','return_decision','return_analysis','return_report','report','grain','view','order_line_id','accessory_return','accessory_return_scope','inventory_decision_return','inventory_decision_return_scope','work_kind','work_status','work_sort','work_desc','work_columns'].includes(k)||k.startsWith('report_')||k.startsWith('bucket_')||k.startsWith('supply_')||k.startsWith('rc_')||k==='return_routing')p.set(k,v);
 if(previous.get('target')!==target)for(const k of ['bucket_selection','report_row','report_scope','report_chart_group','cockpit_return','cockpit_focus','analysis_calendar_window','analysis_event','analysis_task','analysis_return_event','analysis_return_task','supply_doc','supply_event','supply_scope','rc_task','rc_scope','rc_group'])p.delete(k);
 return p;
}
/** An explicit task selection supersedes the previous run's event and filter. */
export function taskAnalysisSearch(previous:URLSearchParams,taskId:string,resourceId:string){
 const p=new URLSearchParams(previous);p.set('analysis','resources');p.set('analysis_task',taskId);p.set('analysis_resource',resourceId);
 for(const k of ['bucket_selection','report_row','report_scope','report_chart_group','cockpit_return','cockpit_focus','analysis_calendar_window','analysis_event','analysis_start','analysis_end','analysis_item','analysis_location'])p.delete(k);
 return p;
}
