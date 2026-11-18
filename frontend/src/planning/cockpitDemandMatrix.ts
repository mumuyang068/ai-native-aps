import type {Dict} from '../api/contracts';
import {cockpitDemandMap,type DemandMapKind,type DemandMapLine,type DemandMapState} from './cockpitDemandMap';
import {addDays,midnightAt,siteDate} from './timeRange';

export type CoverageGrain='day'|'week';
export type CoveragePeriod={id:string;label:string;start:string;end:string};
export type CoverageCell={id:string;group:string;period:string|null;lines:DemandMapLine[];requested:number|null;planned:number|null;short:number|null;quantityValid:boolean;state:DemandMapState|'MIXED'};
export function coverageRisk(cell:CoverageCell){
 if(!cell.quantityValid||cell.requested===null||cell.requested<=0||cell.short===null)return {value:null,level:'unknown',label:'待核验'};
 const value=cell.short/cell.requested*100,level=value===0?'0':String(Math.min(5,Math.ceil(value/20)));
 return {value,level,label:value>0&&value<.1?'<0.1%':value.toLocaleString('zh-CN',{maximumFractionDigits:1})+'%'};
}
const validDate=(v:unknown):v is string=>typeof v==='string'&&/(Z|[+-]\d\d:\d\d)$/.test(v)&&Number.isFinite(Date.parse(v));
function bucket(date:string,grain:CoverageGrain){
 if(grain==='day')return date;
 const day=new Date(date+'T12:00:00Z').getUTCDay();return addDays(date,-((day+6)%7));
}
/** Bins original requirement dates only. Coverage is quantity, never proof of on-time completion. */
export function demandCoverageMatrix(report:Dict,kind:DemandMapKind,grain:CoverageGrain){
 const map=cockpitDemandMap(report,kind),lines=map.points.flatMap(p=>p.lines),timezone=report.timezone??'Asia/Shanghai';
 const dated=lines.flatMap(l=>l.at?[Date.parse(l.at)]:[]),start=validDate(report.range?.start_at)?Date.parse(report.range.start_at):dated.length?Math.min(...dated):null;
 const end=validDate(report.range?.end_at)?Date.parse(report.range.end_at):dated.length?Math.max(...dated)+1:null;
 const periods:CoveragePeriod[]=[];
 if(start!==null&&end!==null&&end>start){
  const first=siteDate(start,timezone),last=siteDate(end-1,timezone);
  for(let date=bucket(first,grain);date<=last;date=addDays(date,grain==='week'?7:1)){
   const next=addDays(date,grain==='week'?7:1),a=date<first?first:date,b=addDays(next,-1)>last?last:addDays(next,-1);
   const label=a===b?a.slice(5).replace('-','/'):a.slice(5).replace('-','/')+'–'+b.slice(5).replace('-','/');
   periods.push({id:date,label,start:new Date(Math.max(start,midnightAt(date,timezone))).toISOString(),end:new Date(Math.min(end,midnightAt(next,timezone))).toISOString()});
  }
 }
 const grouped=new Map<string,{group:string;period:string|null;lines:DemandMapLine[]}>();
 for(const line of lines){const period=line.at?bucket(siteDate(line.at,timezone),grain):null,id=JSON.stringify([line.group,period]);const cell=grouped.get(id)??{group:line.group,period,lines:[]};cell.lines.push(line);grouped.set(id,cell)}
 const raw:Dict[]=Array.isArray(report.demand?.lines)?report.demand.lines:[];
 const cells:CoverageCell[]=[...grouped].map(([id,cell])=>{
  const states=[...new Set(cell.lines.map(l=>l.state))];
  const quantityValid=cell.lines.every(l=>{
   const originals=raw.filter(r=>r.demand_id===l.id);
   return originals.length===1&&originals[0].delivery_validation_status==='PASS'&&['PRESENT','EMPTY_CHECKED'].includes(originals[0].delivery_evidence_status)&&
    l.requested!==null&&l.requested>=0&&l.planned!==null&&l.planned>=0&&l.short!==null&&l.short>=0&&Math.abs(l.planned+l.short-l.requested)<1e-6;
  });
  const sum=(key:'requested'|'planned'|'short')=>quantityValid?cell.lines.reduce((n,l)=>n+l[key]!,0):null;
  return {id,...cell,quantityValid,requested:sum('requested'),planned:sum('planned'),short:sum('short'),state:!quantityValid?'UNKNOWN':states.length===1?states[0]:'MIXED'};
 });
 return {products:map.products,periods,cells,lineCount:lines.length,hasUndated:cells.some(c=>c.period===null)};
}
