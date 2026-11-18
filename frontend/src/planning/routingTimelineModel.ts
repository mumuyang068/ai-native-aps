import type {Dict} from '../api/contracts';
export type WorkSegment={start:number;end:number;offset:number};
/** A display scale only: union of declared availability, never a capacity calculation. */
export function workingScale(windows:Dict[],start:string,end:string){
 const a=Date.parse(start),b=Date.parse(end),merged:{start:number;end:number}[]=[];
 for(const w of windows.map(w=>({start:Math.max(a,Date.parse(w.start_at)),end:Math.min(b,Date.parse(w.end_at))})).filter(w=>w.end>w.start).sort((x,y)=>x.start-y.start)){
  const last=merged.at(-1);if(last&&w.start<=last.end)last.end=Math.max(last.end,w.end);else merged.push({...w});
 }
 let total=0;const segments:WorkSegment[]=merged.map(w=>{const offset=total;total+=w.end-w.start;return {...w,offset}});
 const point=(at:number)=>{if(!total)return 0;let elapsed=0;for(const s of segments){if(at<=s.start)break;elapsed+=Math.max(0,Math.min(at,s.end)-s.start)}return elapsed/total*100};
 const pieces=(startAt:string,endAt:string)=>segments.flatMap(s=>{const l=Math.max(s.start,Date.parse(startAt)),r=Math.min(s.end,Date.parse(endAt));return r>l?[{left:point(l),width:point(r)-point(l),start:l,end:r}]:[]});
 const at=(fraction:number)=>{const offset=Math.max(0,Math.min(1,fraction))*total;for(const s of segments){if(offset<=s.offset+s.end-s.start)return s.start+offset-s.offset}return segments.at(-1)?.end??NaN};
 return {segments,total,point,pieces,at};
}
export function routingOperationLabel(input:Dict,operationId:string){
 const op=(input.routing_revisions??[]).flatMap((r:Dict)=>r.operations??[]).find((o:Dict)=>o.id===operationId);
 return (op?.sequence!=null?'工序 '+op.sequence+' · ':'')+(op?.name||operationId);
}

/** Only changes the view's working-time window. Does not edit capacity or operation times. */
export function workingViewport(windows:Dict[],horizonStart:string,horizonEnd:string,start:string,end:string,factor:number,anchor=.5,pan=0){
 const scale=workingScale(windows,horizonStart,horizonEnd);
 if(!scale.total||!Number.isFinite(factor)||factor<=0)return null;
 const left=scale.point(Date.parse(start))/100*scale.total,right=scale.point(Date.parse(end))/100*scale.total;
 if(right<=left)return null;
 const oldSpan=right-left,span=Math.max(Math.min(300000,scale.total),Math.min(scale.total,oldSpan*factor));
 const pivot=left+oldSpan*Math.max(0,Math.min(1,anchor));
 const a=Math.max(0,Math.min(scale.total-span,pivot-span*Math.max(0,Math.min(1,anchor))+pan*oldSpan));
 return {start:new Date(scale.at(a/scale.total)).toISOString(),end:new Date(scale.at((a+span)/scale.total)).toISOString()};
}
