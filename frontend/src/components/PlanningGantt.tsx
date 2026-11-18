import {useEffect,useMemo,useRef,useState} from 'react';
import * as ec from 'echarts/core';
import {CustomChart} from 'echarts/charts';
import {GridComponent,TooltipComponent,DataZoomComponent,AriaComponent} from 'echarts/components';
import {CanvasRenderer} from 'echarts/renderers';
import {registerTheme} from '@siemens/ix-echarts';
import {Button,Dialog} from './Controls';
import {formatSiteTime} from '../planning/timeRange';
import {calendarGaps,ganttScale,packGantt,selectionIndices,ganttTimeIntent,type GanttRow,type GanttInterval,type GanttLink,type GanttWindow,type GanttEditRequest} from './ganttModel';
import {ganttAppearance,ganttLegend,ganttStatusText} from './ganttSemantics';
import {chartColors} from '../design/chartSemantics';
import './planning-gantt.css';

ec.use([CustomChart,GridComponent,TooltipComponent,DataZoomComponent,AriaComponent,CanvasRenderer]);
registerTheme(ec);
type Props={identityKey:string;label:string;rows:GanttRow[];intervals:GanttInterval[];start:number;end:number;timezone:string;folded?:GanttWindow[];links?:GanttLink[];blocked?:{rowId:string;start:number;end:number}[];deadlines?:number[];selectedTask?:string|null;selectedEvent?:string;selectedResource?:string;onSelect:(x:GanttInterval)=>void;onResource?:(id:string)=>void;onFocus?:(x:GanttInterval)=>void;onRange?:(start:string,end:string)=>void;onEdit?:(request:GanttEditRequest)=>void};
/** Original run data stays read-only. Pointer gestures emit an unsaved intent to the existing Editor. */
export function PlanningGantt(p:Props){
 const host=useRef<HTMLDivElement>(null),chart=useRef<ec.ECharts|null>(null),latest=useRef(p),rendered=useRef<GanttInterval[]>([]);latest.current=p;
 const [width,setWidth]=useState(900),[measuredHeight,setMeasuredHeight]=useState(400),[view,setView]=useState<[number,number]|null>(null),[list,setList]=useState<boolean|string[]>(false);
 const [ghost,setGhost]=useState<{left:number;top:number;width:number;text:string}|null>(null),bounds=useRef<{x:GanttInterval;left:number;right:number;top:number;bottom:number}[]>([]),gesture=useRef<{bar:typeof bounds.current[number];pointer:number;origin:number;mode:'move'|'start'|'end';time?:GanttEditRequest['time'];moved:boolean}|null>(null),suppressClick=useRef(0);
 const scale=useMemo(()=>ganttScale(p.start,p.end,p.folded),[p.start,p.end,p.folded]);
 const range:[number,number]=view??[scale.min,scale.max];
 const focus=useRef<(x:GanttInterval)=>void>(()=>{});focus.current=x=>{if(p.onFocus){p.onFocus(x);return}const pieces=scale.pieces(x.start,x.end);if(pieces.length){const a=pieces[0].left,b=pieces.at(-1)!.right,pad=Math.max(300000,(b-a)*.3);setView([Math.max(scale.min,a-pad),Math.min(scale.max,b+pad)])}};
 const packed=useMemo(()=>packGantt(p.intervals,scale,range,Math.max(100,width-(width<600?124:200)-28)),[p.intervals,scale,range[0],range[1],width]);
 const lanes=p.rows.flatMap(r=>Array.from({length:Math.max(1,packed.lanes.get(r.id)?.length??1)},(_,lane)=>({row:r,lane,key:JSON.stringify([r.id,lane])})));
 const height=Math.max(230,Math.min(560,lanes.length*48+112));
 useEffect(()=>{setView(null);setList(false)},[p.identityKey,p.start,p.end]);
 useEffect(()=>{
  const el=host.current;if(!el)return;
  const c=ec.init(el,'theme-classic-light',{renderer:'canvas',devicePixelRatio:Math.min(window.devicePixelRatio,2)});chart.current=c;
  c.on('click',(event:any)=>{
   if(performance.now()<suppressClick.current)return;
   if(event.seriesId==='intervals'&&event.data?.source){const x=event.data.source;if(x.nearbyIds.length>1)setList(x.nearbyIds);else latest.current.onSelect(x)}
   if(event.componentType==='yAxis'){
    try{latest.current.onResource?.(JSON.parse(String(event.value))[0])}catch{/* Invalid axis payload is not a business reference. */}
   }
  });
  const cancel=()=>{gesture.current=null;setGhost(null)};
  const down=(event:PointerEvent)=>{
   if(!latest.current.onEdit||latest.current.folded||event.button!==0)return;
   const rect=el.getBoundingClientRect(),x=event.clientX-rect.left,y=event.clientY-rect.top;
   const bar=bounds.current.find(b=>x>=b.left&&x<=b.right&&y>=b.top&&y<=b.bottom);if(!bar||bar.x.protected||bar.x.kind==='resource')return;
   const source=rendered.current.find(v=>v.id===bar.x.id) as any;if(source?.nearbyIds?.length>1)return;
   const sourceLeft=Number(c.convertToPixel({xAxisIndex:0},bar.x.start)),sourceRight=Number(c.convertToPixel({xAxisIndex:0},bar.x.end));
   const mode=bar.right-bar.left>32?(x-bar.left<8&&Math.abs(sourceLeft-bar.left)<1?'start':bar.right-x<8&&Math.abs(sourceRight-bar.right)<1?'end':'move'):'move';
   gesture.current={bar,pointer:event.pointerId,origin:x,mode,moved:false};el.setPointerCapture(event.pointerId);event.preventDefault();event.stopImmediatePropagation();
  };
  const move=(event:PointerEvent)=>{const g=gesture.current;if(!g||g.pointer!==event.pointerId)return;const x=event.clientX-el.getBoundingClientRect().left,delta=x-g.origin;if(Math.abs(delta)<4&&!g.moved)return;g.moved=true;event.preventDefault();event.stopImmediatePropagation();
   const a=Number(c.convertFromPixel({xAxisIndex:0},g.origin)),b=Number(c.convertFromPixel({xAxisIndex:0},x));try{g.time=ganttTimeIntent(g.bar.x,g.mode,b-a);const left=g.mode==='end'?g.bar.left:g.bar.left+delta,right=g.mode==='start'?g.bar.right:g.bar.right+delta;setGhost({left,top:g.bar.top,width:Math.max(4,right-left),text:(g.mode==='move'?'拟开始 ':'拟窗口 ')+formatSiteTime(g.time.start_at??g.time.end_at!,latest.current.timezone)})}catch{g.time=undefined;setGhost(null)}};
  const up=(event:PointerEvent)=>{const g=gesture.current;if(!g||g.pointer!==event.pointerId)return;event.preventDefault();event.stopImmediatePropagation();suppressClick.current=performance.now()+400;if(el.hasPointerCapture(event.pointerId))el.releasePointerCapture(event.pointerId);cancel();latest.current.onSelect(g.bar.x);if(g.moved&&g.time)latest.current.onEdit?.({taskId:g.bar.x.taskId,time:g.time})};
  const escape=(event:KeyboardEvent)=>{if(event.key==='Escape')cancel()};
  el.addEventListener('pointerdown',down,true);el.addEventListener('pointermove',move,true);el.addEventListener('pointerup',up,true);el.addEventListener('pointercancel',cancel,true);document.addEventListener('keydown',escape);
  c.on('dblclick',(event:any)=>{if(event.seriesId==='intervals'&&event.data?.source){const x=event.data.source;if(x.nearbyIds.length>1)setList(x.nearbyIds);else focus.current(x)}});
  // ECharts toggles on a second click; the business Owner's selection is authoritative.
  c.on('selectchanged',()=>{
   c.dispatchAction({type:'unselect',seriesId:'intervals'},{silent:true});
   const indices=selectionIndices(rendered.current,latest.current.selectedTask);
   if(indices.length)c.dispatchAction({type:'select',seriesId:'intervals',dataIndex:indices},{silent:true});
  });
  c.on('datazoom',(event:any)=>{
   const batch=event.batch??[event];if(batch.every((x:any)=>x.dataZoomId==='rowSlider'||x.dataZoomId==='rowInside'))return;
   const option=c.getOption() as any,z=option.dataZoom?.find((x:any)=>x.id==='timeSlider');
   if(!z||!Number.isFinite(z.startValue)||!Number.isFinite(z.endValue))return;
   setView([z.startValue,z.endValue]);
  });
  let alive=true;const resize=new ResizeObserver(()=>{if(!alive||c.isDisposed())return;setWidth(el.clientWidth);setMeasuredHeight(el.clientHeight);c.resize()});resize.observe(el);setWidth(el.clientWidth);setMeasuredHeight(el.clientHeight);
  return()=>{alive=false;resize.disconnect();el.removeEventListener('pointerdown',down,true);el.removeEventListener('pointermove',move,true);el.removeEventListener('pointerup',up,true);el.removeEventListener('pointercancel',cancel,true);document.removeEventListener('keydown',escape);gesture.current=null;c.dispatchAction({type:'hideTip'});c.dispose();chart.current=null};
 },[p.identityKey]);
 useEffect(()=>{
  const c=chart.current,el=host.current;if(!c||!el||!scale.total)return;
  const left=width<600?124:200,rowIndex=new Map(lanes.map((l,i)=>[l.key,i])),colors=chartColors();
  const entries=packed.bars.map(x=>({id:JSON.stringify([x.id,x.piece]),name:JSON.stringify([x.id,x.piece]),source:x,value:[rowIndex.get(JSON.stringify([x.rowId,x.lane])),x.left,x.right]}));
  rendered.current=packed.bars;
  const background:any[]=[];
  for(const [index,l] of lanes.entries())for(const gap of calendarGaps(l.row.calendar,p.start,p.end)??[])for(const w of scale.pieces(gap.start,gap.end))background.push([index,w.left,w.right,0]);
  for(const [index,l] of lanes.entries())if(l.row.id===p.selectedResource)background.push([index,scale.min,scale.max,2]);
  for(const b of p.blocked??[])for(const [index,l] of lanes.entries())if(l.row.id===b.rowId)for(const w of scale.pieces(b.start,b.end))background.push([index,w.left,w.right,1]);
  const clip=(params:any,shape:any)=>ec.graphic.clipRectByRect(shape,params.coordSys);
  const fmt=(value:number)=>formatSiteTime(new Date(scale.at(value)).toISOString(),p.timezone).slice(5,16);
  const prev:any=c.getOption(),oldY=prev?.dataZoom?.find((z:any)=>z.id==='rowSlider');
  const first=typeof oldY?.startValue==='number'?Math.min(oldY.startValue,Math.max(0,lanes.length-1)):0;
  const visible=Math.max(1,Math.floor((measuredHeight-112)/36)),last=Math.min(first+visible-1,lanes.length-1);
  let pending=performance.now();
  c.off('finished');c.on('finished',()=>{if(pending){el.dataset.renderMs=(performance.now()-pending).toFixed(2);pending=0}});
  c.setOption({useUTC:true,animation:!matchMedia('(prefers-reduced-motion: reduce)').matches,animationDuration:180,animationDurationUpdate:140,stateAnimation:{duration:120},aria:{enabled:true,description:p.label+'。任务列表提供键盘入口；时间调整需要预览及规划计算。'},
   grid:{left,right:28,top:54,bottom:58},
   tooltip:{trigger:'item',renderMode:'richText',confine:true,showDelay:0,hideDelay:80,transitionDuration:.12,formatter:(e:any)=>{const x:typeof packed.bars[number]=e.data?.source;if(!x)return '';const row=p.rows.find(r=>r.id===x.rowId)?.label??'';if(x.nearbyIds.length>1){const peers=p.intervals.filter(y=>x.nearbyIds.includes(y.id));return [row+' · '+peers.length+' 项临近工序',...peers.slice(0,4).map(y=>formatSiteTime(new Date(y.start).toISOString(),p.timezone).slice(5,16)+' · '+y.label),peers.length>4?'…':'','点击分别查看、定位'].filter(Boolean).join('\n')}return [x.label,row,ganttStatusText(x),...x.details,x.protectedReason??'',x.feasibility==='CONFLICT'?'原生记录存在约束冲突':x.feasibility==='FEASIBLE'?'原生工序可行':'工序可行性未单独提供',formatSiteTime(new Date(x.start).toISOString(),p.timezone)+' → '+formatSiteTime(new Date(x.end).toISOString(),p.timezone)].filter(Boolean).join('\n')}},
   xAxis:{type:p.folded?'value':'time',min:scale.min,max:scale.max,position:'top',axisLabel:{hideOverlap:true,formatter:fmt},axisTick:{show:false},axisLine:{show:false},splitLine:{lineStyle:{color:'#e2e7eb'}}},
   yAxis:{type:'category',inverse:true,data:lanes.map(l=>l.key),triggerEvent:true,axisTick:{show:false},axisLine:{show:false},axisLabel:{width:left-20,overflow:'truncate',lineHeight:17,formatter:(id:string)=>{const l=lanes[rowIndex.get(id)??-1];return l&&!l.lane?l.row.label+'\n'+(l.row.detail??''):''}},splitArea:{show:true,areaStyle:{color:['#fff','#f7f9fa']}},splitLine:{show:true,lineStyle:{color:'#e1e6e9'}}},
   dataZoom:[{id:'timeSlider',type:'slider',xAxisIndex:0,filterMode:'weakFilter',startValue:range[0],endValue:range[1],bottom:12,height:18,showDataShadow:false,showDetail:false,brushSelect:false},{id:'timeInside',type:'inside',xAxisIndex:0,filterMode:'weakFilter',zoomOnMouseWheel:'ctrl',moveOnMouseMove:true,moveOnMouseWheel:false},{id:'rowSlider',type:'slider',yAxisIndex:0,filterMode:'weakFilter',right:4,top:54,bottom:58,width:10,startValue:first,endValue:Math.max(first,last),showDataShadow:false,showDetail:false,brushSelect:false,zoomLock:true},{id:'rowInside',type:'inside',yAxisIndex:0,filterMode:'weakFilter',zoomLock:true,zoomOnMouseWheel:false,moveOnMouseWheel:true,moveOnMouseMove:false}],
   series:[{id:'calendar',type:'custom',silent:true,animation:false,encode:{x:[1,2],y:0},data:background,renderItem:(params:any,api:any)=>{const a=api.coord([api.value(1),api.value(0)]),b=api.coord([api.value(2),api.value(0)]),h=Math.abs(api.size([0,1])[1]),rect=clip(params,{x:a[0],y:a[1]-h/2,width:b[0]-a[0],height:h});return rect?{type:'rect',shape:rect,style:{fill:api.value(3)===2?'#ccebf0':api.value(3)?colors.unavailable:'#e6e9ec',opacity:api.value(3)===2?.3:.7}}:null}},
    {id:'intervals',type:'custom',selectedMode:'multiple',encode:{x:[1,2],y:0},data:entries,renderItem:(params:any,api:any)=>{
     const x=entries[params.dataIndex].source,a=api.coord([api.value(1),api.value(0)]),b=api.coord([api.value(2),api.value(0)]),h=Math.min(26,Math.abs(api.size([0,1])[1])*.6);
     const rect=clip(params,{x:a[0],y:a[1]-h/2,width:Math.max(1,b[0]-a[0]),height:h});if(!rect)return;
     const hit=clip(params,{x:(a[0]+b[0])/2-Math.max(24,b[0]-a[0])/2,y:a[1]-Math.max(16,h/2),width:Math.max(24,b[0]-a[0]),height:Math.max(32,h)});
     const appearance=ganttAppearance(x),color=appearance.fill,current=x.id===p.selectedEvent,stroke=current?'#172b3a':appearance.outline;
     const cx=(a[0]+b[0])/2,cy=a[1],short=b[0]-a[0]<6;
     const marker=short&&cx>=params.coordSys.x&&cx<=params.coordSys.x+params.coordSys.width?[{type:'polygon',shape:{points:[[cx-4,cy],[cx,cy-5],[cx+4,cy],[cx,cy+5]]},style:{fill:color,stroke:appearance.protected?'#243746':'#fff',lineWidth:appearance.protected?2:1},emphasis:{style:{stroke:'#172b3a',lineWidth:2}},select:{style:{stroke:'#172b3a',lineWidth:3}}}]:[];
     const flags:any[]=[];
     if(appearance.candidate&&rect.width>10)flags.push({type:'rect',silent:true,shape:{x:rect.x+3,y:rect.y+3,width:rect.width-6,height:Math.max(1,rect.height-6)},style:{fill:'transparent',stroke:'#fff',lineWidth:1}});
     if(appearance.protected&&rect.width>6)flags.push({type:'line',silent:true,shape:{x1:rect.x+3,x2:rect.x+3,y1:rect.y+3,y2:rect.y+rect.height-3},style:{stroke:'#fff',lineWidth:2}});
     if(appearance.risk){flags.push({type:'line',silent:true,shape:{x1:rect.x,x2:rect.x+rect.width,y1:rect.y+rect.height+2,y2:rect.y+rect.height+2},style:{stroke:appearance.riskColor,lineWidth:3}});flags.push({type:'text',silent:true,style:{x:Math.min(rect.x+rect.width,params.coordSys.x+params.coordSys.width-8),y:rect.y-1,text:'!',font:'bold 12px sans-serif',fill:appearance.riskColor,backgroundColor:'#fff',padding:[0,2],align:'right',verticalAlign:'bottom'}})}
     return {type:'group',children:[{type:'rect',shape:hit,style:{fill:'transparent'}},{type:'rect',shape:{...rect,r:1},style:{fill:color,stroke,lineWidth:current?3:appearance.protected?2:1},emphasis:{style:{stroke:'#172b3a',lineWidth:2}},select:{style:{stroke:'#172b3a',lineWidth:3}},textContent:{type:'text',style:{text:rect.width>80?(appearance.protected?'锁 ':'')+(current?'● ':'')+x.label:'',fill:'#fff',font:'12px sans-serif',width:Math.max(0,rect.width-10),overflow:'truncate'}},textConfig:{position:'inside'}},...marker,...flags]};
    }},
    {id:'relations',type:'custom',silent:true,animation:false,encode:{x:[1,2],y:0},data:(p.links??[]).map(l=>[rowIndex.get(JSON.stringify([l.fromRow,packed.bars.find(x=>x.taskId===l.fromTask&&x.rowId===l.fromRow)?.lane??0]))??0,scale.point(l.start),scale.point(l.end)]),renderItem:(params:any,api:any)=>{const l=(p.links??[])[params.dataIndex],from=packed.bars.find(x=>x.taskId===l.fromTask&&x.rowId===l.fromRow),to=packed.bars.find(x=>x.taskId===l.toTask&&x.rowId===l.toRow);if(!from||!to)return;const a=api.coord([scale.point(l.start),rowIndex.get(JSON.stringify([l.fromRow,from.lane]))]),b=api.coord([scale.point(l.end),rowIndex.get(JSON.stringify([l.toRow,to.lane]))]);const r=params.coordSys;if(a[0]<r.x||b[0]>r.x+r.width||a[1]<r.y||b[1]>r.y+r.height)return;return {type:'polyline',shape:{points:[a,[a[0]+6,a[1]],[a[0]+6,b[1]],b]},style:{stroke:'#547484',lineWidth:1,lineDash:[3,3],fill:null}}}},
    {id:'deadlines',type:'custom',silent:true,animation:false,encode:{x:1,y:-1},data:[...(p.deadlines??[]).map(v=>[0,scale.point(v),0]),...(p.folded??[]).slice(1).map(s=>[0,scale.point(s.start),1])],renderItem:(params:any,api:any)=>{const a=api.coord([api.value(1),0]),r=params.coordSys;if(a[0]<r.x||a[0]>r.x+r.width)return;return {type:'line',shape:{x1:a[0],x2:a[0],y1:r.y,y2:r.y+r.height},style:{stroke:api.value(2)?colors.baseline:colors.warning,lineDash:[3,3],opacity:.6}}}}]}, {notMerge:true});
  const indices=selectionIndices(entries.map(x=>x.source),p.selectedTask);if(indices.length)c.dispatchAction({type:'select',seriesId:'intervals',dataIndex:indices});
  bounds.current=entries.map(e=>{const a=c.convertToPixel({gridIndex:0},[e.value[1],e.value[0]]) as number[],b=c.convertToPixel({gridIndex:0},[e.value[2],e.value[0]]) as number[];return {x:e.source,left:a[0],right:b[0],top:Math.max(54,a[1]-16),bottom:Math.min(el.clientHeight-58,a[1]+16)}}).filter(b=>b.left>=left-1&&b.right<=width-27&&b.bottom>b.top);
 },[p.identityKey,p.rows,p.intervals,p.start,p.end,p.timezone,p.folded,p.links,p.blocked,p.deadlines,p.selectedResource,p.selectedEvent,view,width,measuredHeight]);
 useEffect(()=>{
  const x=p.intervals.find(x=>p.selectedEvent?x.id===p.selectedEvent:x.taskId===p.selectedTask);if(!x)return;
  const pieces=scale.pieces(x.start,x.end);if(!pieces.length)return;
  const a=pieces[0].left,b=pieces.at(-1)!.right;if(a<range[1]&&b>range[0])return;
  const span=Math.max(b-a,range[1]-range[0]),left=Math.max(scale.min,Math.min(scale.max-span,(a+b-span)/2));setView([left,Math.min(scale.max,left+span)]);
 },[p.selectedTask,p.selectedEvent,p.identityKey]);
 useEffect(()=>{
  const c=chart.current;if(!c||c.isDisposed())return;
  c.dispatchAction({type:'unselect',seriesId:'intervals'});
  const indices=selectionIndices(packed.bars,p.selectedTask);if(indices.length)c.dispatchAction({type:'select',seriesId:'intervals',dataIndex:indices});
  const selected=packed.bars.find(x=>p.selectedEvent?x.id===p.selectedEvent:x.taskId===p.selectedTask);
  if(selected){const index=lanes.findIndex(l=>l.row.id===selected.rowId&&l.lane===selected.lane),options:any=c.getOption(),y=options?.dataZoom?.find((z:any)=>z.id==='rowSlider');if(y&&(index<y.startValue||index>y.endValue)){const span=Math.max(0,y.endValue-y.startValue),start=Math.max(0,Math.min(lanes.length-1-span,index-Math.floor(span/2)));c.dispatchAction({type:'dataZoom',dataZoomId:'rowSlider',startValue:start,endValue:Math.min(lanes.length-1,start+span)})}}
 },[p.selectedTask,p.selectedEvent,packed]);
 const legend=ganttLegend(p.intervals,p.rows,p.start,p.end,!!p.blocked?.length);
 const stamp=(x:number)=>formatSiteTime(new Date(x).toISOString(),p.timezone);
 return <div className="planning-gantt" data-identity={p.identityKey}>
  <div className="planning-gantt-tools"><span>{p.rows.length} 行 · {p.intervals.length} 个区间</span>{p.onEdit&&<Button disabled={!p.selectedTask} onClick={()=>p.selectedTask&&p.onEdit?.({taskId:p.selectedTask})}>编辑选中工序</Button>}<Button onClick={()=>setView(null)} disabled={!view}>复位视窗</Button>{view&&p.onRange&&<Button onClick={()=>p.onRange?.(new Date(scale.at(range[0])).toISOString(),new Date(scale.at(range[1])).toISOString())}>应用查看范围</Button>}<Button onClick={()=>setList(true)}>任务与资源列表</Button></div>
  <div className="planning-gantt-surface"><div ref={host} className="planning-gantt-chart" style={{height}} role="img" aria-label={p.label} data-task-count={new Set(p.intervals.map(x=>x.taskId)).size} data-interval-count={p.intervals.length}/>{ghost&&<div className="planning-gantt-ghost" style={{left:ghost.left,top:ghost.top,width:ghost.width,height:32}}><span>{ghost.text} · 未保存</span></div>}</div>
  {legend.length>0&&<ul className="planning-gantt-legend" aria-label="当前甘特图例">{legend.map(item=><li key={item.key}><i aria-hidden="true" className={item.shape??'fill'} style={item.fill?{backgroundColor:item.fill}:undefined}>{item.shape==='lock'?'锁':item.shape==='risk'?'!':''}</i>{item.label}</li>)}</ul>}
  {!p.rows.length&&<p>没有匹配的排程记录。</p>}
  {p.rows.some(r=>r.empty)&&<div className="planning-gantt-evidence">{p.rows.filter(r=>r.empty).map(r=><span key={r.id}>{r.label}：{r.empty}</span>)}</div>}
  <p className="planning-gantt-hint">Ctrl + 滚轮缩放，拖动空白平移；滚轮查看行。◆ 为短工序，双击放大。{p.onEdit?'拖动建议工序或两端边缘提出调时，松开后核对；Esc 取消。已有工单保持保护。':'密集处点击选择工序。'} 条形颜色表示任务性质；锁线表示保护，红色下划线与 ! 表示风险。</p>
  {list&&<Dialog title={Array.isArray(list)?'该处的 '+list.length+' 项工序':'任务与资源'} size="600" onClose={()=>setList(false)}><div className="planning-gantt-list">{p.rows.filter(r=>!Array.isArray(list)||p.intervals.some(x=>x.rowId===r.id&&list.includes(x.id))).map(r=><section key={r.id}><Button disabled={!p.onResource} onClick={()=>{p.onResource?.(r.id);setList(false)}}>{r.label}</Button><p>{r.detail}{r.empty?' · '+r.empty:''}</p>{p.intervals.filter(x=>x.rowId===r.id&&(!Array.isArray(list)||list.includes(x.id))).map(x=><div className="planning-gantt-list-entry" key={x.id}><Button aria-label={x.label+" · "+stamp(x.start)+" 至 "+stamp(x.end)} aria-pressed={x.eventId?p.selectedEvent===x.id:x.taskId===p.selectedTask} onClick={()=>{p.onSelect(x);focus.current(x);setList(false)}}>{x.id===p.selectedEvent?"当前占用 · ":""}{x.label}</Button><p>{stamp(x.start)} → {stamp(x.end)}</p><small>{ganttStatusText(x)} · {x.details.join(' · ')}</small></div>)}</section>)}</div></Dialog>}
 </div>;
}
