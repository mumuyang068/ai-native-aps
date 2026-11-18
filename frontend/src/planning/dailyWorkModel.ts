import type {Dict} from '../api/contracts';
export function exactDemandReviews(notes:Dict[],scope:{planId:string;versionId:string;baselineRunId:string},demandId:string){return notes.filter(n=>n.object_scope===demandId&&n.identity?.plan_id===scope.planId&&n.identity?.data_version_id===scope.versionId&&n.identity?.baseline_run_id===scope.baselineRunId&&typeof n.identity?.target==='string'&&typeof n.identity?.candidate_run_id==='string'&&/^[a-f0-9]{64}$/i.test(n.fingerprint??''));}
import {siteDate} from './timeRange';
export function knownQuantity(value:unknown):number|null{
 if(value===null||value===undefined||typeof value==='boolean'||typeof value==='string'&&!value.trim())return null;
 const n=Number(value);return Number.isFinite(n)&&n>=0?n:null;
}
export function dailyWorkSummary(rows:Dict[]){
 const groups=new Map<string,{uom:string;kind:string;lines:number;requested:number;planned:number;unfulfilled:number;unknown:number}>();
 for(const row of rows){const uom=row.uom??'未知单位',kind=row.kind??'UNCLASSIFIED',key=JSON.stringify([uom,kind]);
  const group=groups.get(key)??{uom,kind,lines:0,requested:0,planned:0,unfulfilled:0,unknown:0};
  const requested=knownQuantity(row.requested_quantity),planned=knownQuantity(row.planned_quantity),short=knownQuantity(row.unfulfilled_quantity);
  group.lines++;group.requested+=requested??0;group.planned+=planned??0;group.unfulfilled+=short??0;
  if(requested===null||planned===null||short===null)group.unknown++;groups.set(key,group);
 }return [...groups.values()];
}
export function dailyWorkRange(at:string,grain:'day'|'week',zone:string){
 if(!Number.isFinite(Date.parse(at)))throw Error('缺少有效的作业观察日期。');
 const day=/^\d{4}-\d{2}-\d{2}$/.test(at)?at:siteDate(at,zone),start=new Date(day+'T00:00:00Z');
 if(grain==='week')start.setUTCDate(start.getUTCDate()-(start.getUTCDay()+6)%7);
 const end=new Date(start);if(grain==='week')end.setUTCDate(end.getUTCDate()+6);
 return {from:start.toISOString().slice(0,10),to:end.toISOString().slice(0,10)};
}
