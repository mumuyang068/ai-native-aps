import type {OrderEvidence,OrderTrace} from '../api/contracts';
export const deliveryStatusLabel=(v:string|undefined)=>({ON_TIME:'预计按期',LATE:'存在延期',SHORT:'缺量',UNPLANNED:'未排定'}[v??'']??v??'未提供');
export function zeroLatenessEvidence(order:OrderTrace,projection:OrderEvidence){return order.demand_records.find(e=>
 e.record.name===order.demand_id&&e.source.artifact==='demand.json'&&e.source.solve_run_id===projection.solve_run_id&&e.source.scenario_id===projection.scenario_id&&
 [0,'00:00:00','0 00:00:00'].includes(e.record.maxlateness));}
export type ObjectSelection={key:string;kind:'order'|'task';id:string};
export function selectionFor(selection:ObjectSelection|null,key:string){return selection?.key===key?selection:null}
export function delayLabel(seconds:number){const n=Math.abs(Math.round(seconds)),d=Math.floor(n/86400),h=Math.floor(n%86400/3600),m=Math.floor(n%3600/60);return [d?d+' 天':'',h?h+' 小时':'',m?m+' 分钟':''].filter(Boolean).join(' ')||'不足 1 分钟'}
export function packLanes(tasks:{id:string;start_at:string;end_at:string}[]){
 const ends:number[]=[],lanes=new Map<string,number>();
 for(const task of [...tasks].sort((a,b)=>Date.parse(a.start_at)-Date.parse(b.start_at)||a.id.localeCompare(b.id))){
  const start=Date.parse(task.start_at),end=Date.parse(task.end_at);let lane=ends.findIndex(e=>e<=start);
  if(lane<0)lane=ends.length;ends[lane]=end;lanes.set(task.id,lane);
 }
 return {lanes,count:ends.length};
}
