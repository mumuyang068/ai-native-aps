import type {Dict} from '../api/contracts';
import type {EChartsCoreOption} from 'echarts/core';
import {formatSiteTime,midnightAt,siteDate} from './timeRange';
import {qty,unit} from './planModel';
import {verifyReport,reportRowId,reportRows,type ReportScope} from './reportModel';
export function fulfillmentGroupId(row:Dict){return JSON.stringify([row.item_id,row.location_id,row.uom,row.endpoint])}
export function fulfillmentGroups(rows:Dict[]){return [...new Map(rows.map(r=>[fulfillmentGroupId(r),r])).entries()].map(([id,row])=>({id,label:`${row.endpoint??row.item_id} · ${row.location_id} · ${unit(row.uom)}`,row}))}
export function fulfillmentAmount(value:unknown,uom:string){return typeof value!=='string'&&typeof value!=='number'||value===''?'未核验':qty(value)+' '+unit(uom)}
/** Select the owner's precomputed per-demand bucket values; no dates or allocations are recalculated. */
export function fulfillmentRowsForKind(rows:Dict[],kind:string,lines?:Dict[]):Dict[]{
 if(!['FIRM_ORDER','NET_FORECAST','INQUIRY'].includes(kind))return rows;
 const fields=['due_quantity','cumulative_due_quantity','planned_quantity','overdue_unfulfilled','cumulative_planned_quantity','not_yet_due_unfulfilled','unscheduled_quantity'];
 return rows.map(row=>{
  if(row.kind_totals){const components=(row.demand_components??[]).filter((c:Dict)=>c.demand_kind===kind),totals=row.kind_totals[kind];return {...row,...Object.fromEntries(fields.map(k=>[k,totals?totals[k]??null:'0'])),demand_components:components,demand_ids:components.map((c:Dict)=>c.demand_id),demand_kinds:[kind]};}
  const classification=lines?.filter(l=>fulfillmentGroupId(l)===fulfillmentGroupId(row))??rows.flatMap(r=>r.demand_components??[]);
  if(!classification.length||!Array.isArray(row.demand_components)||classification.some((c:Dict)=>!['FIRM_ORDER','NET_FORECAST'].includes(c.demand_kind)))return {...row,...Object.fromEntries(fields.map(k=>[k,null])),demand_ids:[],demand_kinds:[kind],kind_evidence_status:'NOT_COLLECTED'};
  const components:Dict[]=row.demand_components.filter((c:Dict)=>c.demand_kind===kind);
  const ready=(c:Dict)=>['PRESENT','EMPTY_CHECKED'].includes(c.delivery_evidence_status)&&['PRESENT','EMPTY_CHECKED'].includes(c.due_evidence_status);
  const sum=(key:string)=>components.every(c=>ready(c)&&c[key]!==null&&c[key]!==undefined&&c[key]!==''&&typeof c[key]!=='boolean'&&Number.isFinite(Number(c[key])))?components.reduce((a,c)=>a+Number(c[key]),0):null;
  // The owner emits sparse active components. A completed prior line can be absent
  // here while still contributing to cumulative delivery; do not invent its total.
  return {...row,...Object.fromEntries(fields.map(k=>[k,k==='cumulative_planned_quantity'?null:sum(k)])),demand_ids:components.map(c=>c.demand_id),demand_components:components,demand_kinds:[kind],kind_evidence_status:components.every(ready)?'PRESENT':'NOT_CHECKED'};
 });
}
export function matchingBaseline(data:Dict,scope:ReportScope,baselineRunId:string,timezone:string):Dict[]{
 verifyReport(data,{...scope,runId:baselineRunId,target:'baseline'});
 if(data.timezone!==timezone)throw Error('两侧企业时区不一致，未绘制比较。');return data.demand?.rows??[];
}
/** Keep the union of active periods. An empty candidate period can still carry
 * a real early baseline event; the candidate zero is its own certified value. */
export function fulfillmentVisibleRows(current:Dict[],baseline:Dict[],query:string,active:boolean):Dict[]{
 const old=new Map(baseline.map(r=>[reportRowId(r),r]));
 const activeIds=new Set([...reportRows(current,'demand',query,active),...reportRows(baseline,'demand',query,active)].map(reportRowId));
 return current.filter(r=>activeIds.has(reportRowId(r))).map(r=>({...r,...(baseline.length?{baseline_planned_quantity:old.get(reportRowId(r))?.planned_quantity??null,baseline_overdue_unfulfilled:old.get(reportRowId(r))?.overdue_unfulfilled??null}:{})}));
}
export function fulfillmentChart(rows:Dict[],baseline:Dict[],timezone:string):EChartsCoreOption{
 if(fulfillmentGroups([...rows,...baseline]).length>1)throw Error('不同物料、地点、单位或阶段不能合为一个履约图。');
 const number=(v:unknown)=>v===null||v===undefined||v===''||!Number.isFinite(Number(v))?null:Number(v);
 const time=(v:string)=>formatSiteTime(v,timezone),byPeriod=new Map(baseline.map(r=>[r.start_at+'|'+r.end_at,r]));
 const definitions=[['due_quantity','到期需求','bar'],['planned_quantity','计划满足','bar'],['overdue_unfulfilled','逾期未满足','line']];
 const series=definitions.map(([field,name,type])=>({name,type,connectNulls:false,step:type==='line'?'end':undefined,data:rows.map(r=>({value:number(r[field]),eventId:reportRowId(r),...(field==='overdue_unfulfilled'&&r===rows.at(-1)&&number(r[field])!==null?{label:{show:true,formatter:'期末逾期未满足 '+qty(r[field])+' '+unit(r.uom),position:'top',align:'right',distance:12,fontSize:13,fontWeight:'bold',backgroundColor:'rgba(255,255,255,0.96)',padding:[5,8]}}:{})})),emphasis:{focus:'series'}}));
 if(baseline.length)for(const [field,name] of [['planned_quantity','基线计划满足'],['overdue_unfulfilled','基线逾期未满足']])series.push({name,type:'line',connectNulls:false,step:'end',lineStyle:{type:'dashed'},data:rows.map(r=>({value:number(byPeriod.get(r.start_at+'|'+r.end_at)?.[field]),eventId:reportRowId(r)})),emphasis:{focus:'series'}} as any);
 return {animation:false,aria:{enabled:true,description:'需求按要求时间归桶，计划满足按真实原生交付事件归桶。图表只改变调查焦点。'},legend:{top:4},tooltip:{trigger:'axis',renderMode:'richText',confine:true,valueFormatter:(v:unknown)=>number(v)===null?'未核验':String(v)+' '+unit(rows[0]?.uom)},grid:{top:72,left:58,right:44,bottom:64},xAxis:{type:'category',data:rows.map(r=>time(r.start_at).slice(5,16)),axisLabel:{hideOverlap:true}},yAxis:{type:'value',min:0,name:'数量（'+unit(rows[0]?.uom)+'）'},dataZoom:[{type:'inside',zoomOnMouseWheel:'ctrl'},{type:'slider',bottom:8,height:28,handleSize:32,brushSelect:false}],series};
}

/** Compact display only; source and request retain the precise half-open range. */
export function fulfillmentRangeLabel(range:{start_at:string;end_at:string},timezone:string){
 const a=formatSiteTime(range.start_at,timezone),b=formatSiteTime(range.end_at,timezone);
 if(Date.parse(range.start_at)===midnightAt(siteDate(range.start_at,timezone),timezone)&&Date.parse(range.end_at)===midnightAt(siteDate(range.end_at,timezone),timezone)){
  const end=formatSiteTime(new Date(Date.parse(range.end_at)-1).toISOString(),timezone);
  return a.slice(0,10).replaceAll('-','/')+'—'+(a.slice(0,4)===end.slice(0,4)?end.slice(5,10):end.slice(0,10)).replaceAll('-','/');
 }
 return a.slice(0,16)+'—'+b.slice(0,16);
}
