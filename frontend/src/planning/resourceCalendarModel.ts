import type {Dict} from '../api/contracts';
import {supports} from './capabilityModel';
export type CalendarSegment={id:string;kind:'WORKING'|'NONWORKING';resource_id:string;start_at:string;end_at:string};
export type CalendarWindows={status:string;segments:CalendarSegment[]};
export type ResourceCalendarRow={resource:Dict;windows:CalendarWindows;loads:Dict[];validationStatus:string};

/** A complement is meaningful only for a known, sourced input declaration. */
export function calendarWindows(resource:Dict,start:string,end:string):CalendarWindows{
 const a=Date.parse(start),b=Date.parse(end),empty=(status:string)=>({status,segments:[]} as CalendarWindows);
 if(!Number.isFinite(a)||!Number.isFinite(b)||a>=b)return empty('INVALID_EVIDENCE');
 const status=resource.calendar_status??'NOT_COLLECTED';
 if(!['PRESENT','EMPTY_CHECKED'].includes(status))return empty(status);
 if(!resource.calendar_id||resource.calendar_basis!=='DECLARED_CANONICAL_INPUT'||!(resource.calendar_sources??[]).some((s:Dict)=>s.artifact==='canonical_input.json'&&/^\/calendars\/\d+$/.test(s.pointer??'')&&/^[a-f\d]{64}$/i.test(s.sha256??'')))return empty('NOT_COLLECTED');
 if(!Array.isArray(resource.availability))return empty('INVALID_EVIDENCE');
 const entries=resource.availability.map((w:Dict)=>[Date.parse(w.start_at),Date.parse(w.end_at)] as [number,number]);
 if(entries.some(([left,right])=>!Number.isFinite(left)||!Number.isFinite(right)||left>=right)||(status==='EMPTY_CHECKED'&&entries.length))return empty('INVALID_EVIDENCE');
 const work:[number,number][]=[];
 for(const [left,right] of entries.map(([l,r])=>[Math.max(a,l),Math.min(b,r)] as [number,number]).filter(([l,r])=>r>l).sort((x,y)=>x[0]-y[0])){
  const last=work.at(-1);if(last&&left<=last[1])last[1]=Math.max(last[1],right);else work.push([left,right]);
 }
 const segments:CalendarSegment[]=[];
 const add=(kind:CalendarSegment['kind'],left:number,right:number)=>{if(right>left)segments.push({id:resource.id+':CALENDAR:'+kind+':'+left+':'+right,resource_id:resource.id,kind,start_at:new Date(left).toISOString(),end_at:new Date(right).toISOString()})};
 let cursor=a;for(const[left,right]of work){add('NONWORKING',cursor,left);add('WORKING',left,right);cursor=right}add('NONWORKING',cursor,b);
 return {status,segments};
}

/** Loads come from the authenticated analysis response, not task-window inference. */
export function resourceCalendarRows(data:Dict,loads:Dict[],start:string,end:string,resourceId:string):ResourceCalendarRow[]{
 if(!supports(data.identity?.profile,'analyses','resources'))return [];
 const analysis=data.resource_analysis??{},a=Date.parse(start),b=Date.parse(end),excluded=new Set((analysis.excluded_resources??[]).map((r:Dict)=>r.id));
 const usable=['PRESENT','EMPTY_CHECKED'].includes(analysis.status);
 return (analysis.resources??[]).filter((r:Dict)=>['MACHINE','OPERATOR'].includes(r.role)&&r.capacity_model!=='WINDOW_QUANTITY'&&!excluded.has(r.id)&&(!resourceId||r.id===resourceId)).map((resource:Dict)=>({
  resource,windows:usable?calendarWindows(resource,start,end):{status:analysis.status??'NOT_COLLECTED',segments:[]},
  loads:usable?loads.filter(l=>l.resource_id===resource.id&&Date.parse(l.start_at)<b&&Date.parse(l.end_at)>a):[],validationStatus:analysis.validation?.status??'NOT_CHECKED'
 }));
}
