import {demandKindLabel} from './reportExport';
import type {Dict} from '../api/contracts';
import {reviewComparable} from './scenarioReviewModel';

export type ProfessionalRow={id:string;dimensions:Dict;values:Record<string,Dict>};
const finite=(v:unknown)=>v!==null&&v!==undefined&&v!==''&&Number.isFinite(Number(v));
function collect(candidates:Dict[],component:(candidate:Dict)=>Dict|undefined,source:(value:Dict)=>Dict[],dimensions:string[],measure:(row:Dict)=>Dict):ProfessionalRow[]{
 const rows=new Map<string,ProfessionalRow>();
 for(const c of candidates.filter(reviewComparable)){
  const value=component(c);if(!value||!['PRESENT','EMPTY_CHECKED'].includes(value.status)||(value.validation&&value.validation.status!=='PASS')||value.validation?.missing_evidence?.length)continue;
  for(const record of source(value)){
   const identity=Object.fromEntries(dimensions.map(k=>[k,record[k]??null])),id=JSON.stringify(dimensions.map(k=>identity[k]));
   if(!rows.has(id))rows.set(id,{id,dimensions:identity,values:{}});
   const row=rows.get(id)!,measurement=measure(record),old=row.values[c.target];
   if(!old)row.values[c.target]={...measurement,records:[record]};
   else{for(const k of Object.keys(measurement))old[k]=finite(old[k])&&finite(measurement[k])?Number(old[k])+Number(measurement[k]):null;old.records.push(record)}
  }
 }
 return [...rows.values()];
}
export function reviewDemandGroups(candidates:Dict[]){
 return collect(candidates,c=>({status:'PRESENT',lines:c.deliveries??[]}),v=>v.lines,['demand_kind','item_id','location_id','uom'],r=>({requested:finite(r.requested_quantity)?Number(r.requested_quantity):null,planned:finite(r.planned_quantity)?Number(r.planned_quantity):null,short:finite(r.unfulfilled_quantity)?Number(r.unfulfilled_quantity):null}));
}
export function reviewManufacturingGroups(candidates:Dict[]){
 return collect(candidates,c=>c.professional?.manufacturing,v=>v.tasks??[],['operation_id','item_id','location_id','uom','status'],r=>({quantity:finite(r.quantity)?Number(r.quantity):null}));
}
export function reviewDocumentGroups(candidates:Dict[]){
 return collect(candidates,c=>c.professional?.documents,v=>(v.documents??[]).filter((d:Dict)=>d.kind!=='MO'),['kind','item_id','location_id','uom','status','source_id','supplier_id'],r=>({quantity:finite(r.quantity)?Number(r.quantity):null}));
}
export function reviewResourceGroups(candidates:Dict[]){
 return collect(candidates,c=>c.professional?.resources,v=>v.intervals??[],['resource_id'],r=>({loadHours:finite(r.load_quantity)&&Number.isFinite(Date.parse(r.start_at))&&Number.isFinite(Date.parse(r.end_at))?(Date.parse(r.end_at)-Date.parse(r.start_at))*Number(r.load_quantity)/3600000:null}));
}
export function professionalZero(candidate:Dict,component:string){const value=candidate.professional?.[component];return reviewComparable(candidate)&&!!value&&['PRESENT','EMPTY_CHECKED'].includes(value.status)&&(!value.validation||value.validation.status==='PASS')}
export function professionalQuantity(row:ProfessionalRow,candidate:Dict,component:string){const value=row.values[candidate.target];return value?(finite(value.quantity)?Number(value.quantity):null):professionalZero(candidate,component)?0:null}
export function professionalWindow(records:Dict[]){
 if(!records.length||records.some(r=>!Number.isFinite(Date.parse(r.start_at??r.at))||!Number.isFinite(Date.parse(r.end_at??r.at))))return null;
 return {start:Math.min(...records.map(r=>Date.parse(r.start_at??r.at))),end:Math.max(...records.map(r=>Date.parse(r.end_at??r.at)))};
}
export function reviewDelta(before:unknown,after:unknown){return finite(before)&&finite(after)?Number(after)-Number(before):null}
export function reviewDemandKind(kind:unknown){return kind==='FIRM_ORDER'?demandKindLabel(kind):kind==='NET_FORECAST'?'Net · 净预测（非客户订单）':kind?'其他需求 · '+kind:'需求类型未采集'}
