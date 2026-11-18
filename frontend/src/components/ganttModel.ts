export type GanttWindow={start:number;end:number};
export type GanttRow={id:string;label:string;detail?:string;empty?:string;calendar:GanttWindow[]|null};
export type GanttInterval=GanttWindow&{id:string;taskId:string;rowId:string;eventId?:string;label:string;details:string[];nature?:'planned'|'proposed'|'actual'|'unknown';comparison?:'baseline'|'candidate';protected?:boolean;protectedReason?:string;feasibility?:'FEASIBLE'|'CONFLICT'|'UNKNOWN';orderDelaySeconds?:number;chain?:boolean;kind?:'operation'|'resource'|'availability_transfer'};
export type GanttEditRequest={taskId:string;time?:{mode:'START_ANCHOR'|'END_ANCHOR'|'WINDOW';start_at?:string;end_at?:string}};
export function ganttTimeIntent(x:GanttWindow,gesture:'move'|'start'|'end',delta:number):NonNullable<GanttEditRequest['time']>{
 const shift=Math.round(delta/60000)*60000,start=gesture==='end'?x.start:x.start+shift,end=gesture==='start'?x.end:x.end+shift;
 if(!Number.isFinite(start)||!Number.isFinite(end)||start>=end)throw Error('结束时间必须晚于开始时间。');
 return gesture==='move'?{mode:'START_ANCHOR',start_at:new Date(start).toISOString()}:{mode:'WINDOW',start_at:new Date(start).toISOString(),end_at:new Date(end).toISOString()};
}
export type GanttLink={fromTask:string;toTask:string;fromRow:string;toRow:string;start:number;end:number};
export function calendarGaps(windows:GanttWindow[]|null,start:number,end:number):GanttWindow[]|null{
 if(windows===null)return null;
 const gaps:GanttWindow[]=[];let cursor=start;
 for(const w of [...windows].sort((a,b)=>a.start-b.start)){
  const a=Math.max(start,w.start),b=Math.min(end,w.end);if(b<=a)continue;
  if(a>cursor)gaps.push({start:cursor,end:a});cursor=Math.max(cursor,b);
 }
 if(cursor<end)gaps.push({start:cursor,end});return gaps;
}
/** Folded segments come from the existing workingScale Owner; this only maps pixels. */
export function ganttScale(start:number,end:number,folded?:GanttWindow[]){
 let total=0;const segments=(folded??[{start,end}]).map(w=>({...w,offset:0}));
 for(const s of segments){s.offset=total;total+=s.end-s.start}
 const point=(at:number)=>{if(!folded)return at;let elapsed=0;for(const s of segments){if(at<=s.start)break;elapsed+=Math.max(0,Math.min(at,s.end)-s.start)}return elapsed};
 const at=(offset:number)=>{if(!folded)return offset;for(const s of segments)if(offset<=s.offset+s.end-s.start)return s.start+Math.max(0,offset-s.offset);return segments.at(-1)?.end??start};
 const pieces=(a:number,b:number)=>segments.flatMap(s=>{const l=Math.max(a,s.start),r=Math.min(b,s.end);return r>l?[{start:l,end:r,left:point(l),right:point(r)}]:[]});
 return {total,min:folded?0:start,max:folded?total:end,point,at,pieces};
}
export type GanttScale=ReturnType<typeof ganttScale>;
/** Lanes separate actual time overlaps, never enlarged pointer targets. */
export function packGantt(intervals:GanttInterval[],scale:GanttScale,view:[number,number],width:number){
 const ends=new Map<string,number[]>(),bars:(GanttInterval&{left:number;right:number;lane:number;piece:number})[]=[];
 for(const x of [...intervals].sort((a,b)=>a.start-b.start||a.id.localeCompare(b.id))){
  const milestone=x.kind==='availability_transfer'&&x.start===x.end,point=scale.point(x.start),source=milestone&&point>=view[0]&&point<=view[1]?[{start:x.start,end:x.end,left:point,right:point}]:scale.pieces(x.start,x.end);
  const pieces=source.map((p,piece)=>({...p,piece,left:Math.max(view[0],p.left),right:Math.min(view[1],p.right)})).filter(p=>p.right>p.left||milestone&&p.right===p.left);if(!pieces.length)continue;
  const lanes=ends.get(x.rowId)??[];let lane=lanes.findIndex(e=>e<=x.start);if(lane<0)lane=lanes.length;
  lanes[lane]=x.end;ends.set(x.rowId,lanes);
  for(const p of pieces)bars.push({...x,left:p.left,right:p.right,lane,piece:p.piece});
 }
 // Preserve usable pointer targets without inventing additional resource rows.
 // Ambiguous targets are disclosed to the user before a business object is selected.
 const hitSpan=24/Math.max(100,width)*(view[1]-view[0]);
 const hitBounds=(x:typeof bars[number])=>{const pad=Math.max(0,hitSpan-(x.right-x.left))/2;return [x.left-pad,x.right+pad]};
 const resolved=bars.map(x=>{
  const [a,b]=hitBounds(x);
  const nearbyIds=x.right-x.left>=hitSpan?[x.id]:[...new Set(bars.filter(y=>{
   if(y.rowId!==x.rowId||y.lane!==x.lane)return false;
   const [c,d]=hitBounds(y);return c<b&&d>a;
  }).map(y=>y.id))];
  return {...x,nearbyIds};
 });
 return {bars:resolved,lanes:ends};
}
export function selectionIndices(entries:Pick<GanttInterval,'taskId'>[],selected?:string|null){return entries.flatMap((e,i)=>e.taskId===selected?[i]:[])}
