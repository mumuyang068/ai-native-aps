import type {Dict} from '../api/contracts';
import {cockpitProducts} from './cockpitModel';
import {fulfillmentGroupId} from './demandFulfillmentModel';

export type DemandMapKind='FIRM_ORDER'|'NET_FORECAST'|'UNKNOWN';
export type DemandMapState='FULL_ON_TIME'|'FULL_LATE'|'PARTIALLY_PLANNED'|'NOT_PLANNED'|'UNKNOWN'|'COVERED';
export type DemandMapLine={id:string;reference:string;group:string;at:string|null;requested:number|null;planned:number|null;short:number|null;state:DemandMapState};
export type DemandMapPoint={id:string;group:string;at:string|null;state:DemandMapState;lines:DemandMapLine[]};
const list=(v:unknown):Dict[]=>Array.isArray(v)?v:[];
const number=(v:unknown)=>typeof v==='number'||typeof v==='string'&&v.trim()?Number.isFinite(Number(v))?Number(v):null:null;
const dated=(v:unknown):v is string=>typeof v==='string'&&/(Z|[+-]\d\d:\d\d)$/.test(v)&&Number.isFinite(Date.parse(v));
export const demandMapLabels:Record<DemandMapState,string>={FULL_ON_TIME:'全部按期排定',FULL_LATE:'迟后排全',PARTIALLY_PLANNED:'部分已排',NOT_PLANNED:'尚未排定',UNKNOWN:'待核验',COVERED:'已覆盖'};
export function demandMapLabel(state:DemandMapState,kind:DemandMapKind){
 return kind==='NET_FORECAST'?({PARTIALLY_PLANNED:'部分覆盖',NOT_PLANNED:'尚未覆盖'} as Partial<Record<DemandMapState,string>>)[state]??demandMapLabels[state]:demandMapLabels[state];
}
/** Positions are original due dates. A due marker is never a planned completion marker. */
export function cockpitDemandMap(report:Dict,kind:DemandMapKind){
 const allProducts=cockpitProducts(report),allowed=new Set(allProducts.map(p=>p.id)),source=list(report.demand?.lines);
 const performance=report.demand?.delivery_performance,certified=performance?.status==='PRESENT'&&performance.validation?.status==='PASS';
 const proof:Dict[]=certified?list(performance.groups).flatMap(g=>list(g.lines).map(l=>({...l,group:fulfillmentGroupId(g)}))):[];
 const start=dated(report.range?.start_at)?Date.parse(report.range.start_at):null,end=dated(report.range?.end_at)?Date.parse(report.range.end_at):null;
 const lines:DemandMapLine[]=source.filter(l=>allowed.has(fulfillmentGroupId(l))&&(kind==='UNKNOWN'?!['FIRM_ORDER','NET_FORECAST'].includes(l.demand_kind):l.demand_kind===kind)&&(!dated(l.due_at)||start===null||end===null||Date.parse(l.due_at)>=start&&Date.parse(l.due_at)<end)).map(l=>{
  const group=fulfillmentGroupId(l),requested=number(l.requested_quantity),planned=number(l.planned_quantity),short=number(l.unfulfilled_quantity);
  const matches=proof.filter(p=>p.demand_id===l.demand_id&&p.group===group),unique=source.filter(p=>p.demand_id===l.demand_id).length===1;
  let state:DemandMapState='UNKNOWN';
  if(kind==='FIRM_ORDER'&&dated(l.due_at)&&unique&&l.delivery_validation_status==='PASS'&&['PRESENT','EMPTY_CHECKED'].includes(l.delivery_evidence_status)&&matches.length===1&&['FULL_ON_TIME','FULL_LATE','PARTIALLY_PLANNED','NOT_PLANNED','UNKNOWN'].includes(matches[0].classification))state=matches[0].classification;
  if(kind==='NET_FORECAST'&&unique&&l.delivery_validation_status==='PASS'&&['PRESENT','EMPTY_CHECKED'].includes(l.delivery_evidence_status)&&requested!==null&&requested>0&&planned!==null&&planned>=0&&short!==null&&short>=0&&Math.abs(planned+short-requested)<1e-6)state=short===0?'COVERED':planned===0?'NOT_PLANNED':'PARTIALLY_PLANNED';
  return {id:l.demand_id,reference:l.order_ref??l.demand_id,group,at:dated(l.due_at)?l.due_at:null,requested,planned,short,state};
 });
 const points=new Map<string,DemandMapPoint>(),priority:DemandMapState[]=['UNKNOWN','NOT_PLANNED','PARTIALLY_PLANNED','FULL_LATE','FULL_ON_TIME','COVERED'];
 for(const line of lines){const id=JSON.stringify([line.group,line.at]),old=points.get(id);if(old){old.lines.push(line);if(priority.indexOf(line.state)<priority.indexOf(old.state))old.state=line.state}else points.set(id,{id,group:line.group,at:line.at,state:line.state,lines:[line]});}
 const used=new Set(lines.map(l=>l.group));
 return {products:allProducts.filter(p=>used.has(p.id)),points:[...points.values()].sort((a,b)=>(a.at?Date.parse(a.at):Infinity)-(b.at?Date.parse(b.at):Infinity)),lineCount:lines.length};
}
