import {useEffect,useRef,useState} from 'react';
import * as echarts from 'echarts/core';
import {LineChart,BarChart} from 'echarts/charts';
import {GridComponent,TooltipComponent,LegendComponent,DataZoomComponent,AriaComponent,MarkLineComponent,MarkAreaComponent} from 'echarts/components';
import {SVGRenderer} from 'echarts/renderers';
import {registerTheme} from '@siemens/ix-echarts';
import {Button} from './Controls';
import './planning-chart.css';

echarts.use([LineChart,BarChart,GridComponent,TooltipComponent,LegendComponent,DataZoomComponent,AriaComponent,MarkLineComponent,MarkAreaComponent,SVGRenderer]);
export type ChartFocusRequest={identityKey:string;eventId:string;sequence:number};
/** ECharts positions are resolved by the original event identity, never tooltip text. */
export function chartEventPosition(option:echarts.EChartsCoreOption,eventId:string){
 const series=(Array.isArray(option.series)?option.series:option.series?[option.series]:[]) as {data?:unknown[]}[];
 for(let seriesIndex=0;seriesIndex<series.length;seriesIndex++){
  const dataIndex=series[seriesIndex].data?.findIndex(row=>!!row&&typeof row==='object'&&(row as {eventId?:unknown}).eventId===eventId)??-1;
  if(dataIndex>=0)return {seriesIndex,dataIndex};
 }
 return null;
}
export function focusedChartOption(option:echarts.EChartsCoreOption,eventId?:string):echarts.EChartsCoreOption{
 const position=eventId?chartEventPosition(option,eventId):null;if(!position)return option;
 const series=(Array.isArray(option.series)?option.series:[option.series]) as {data?:Record<string,unknown>[]}[];
 return {...option,tooltip:{...(option.tooltip as object),trigger:'item',alwaysShowContent:true},
  series:series.map((row,i)=>i===position.seriesIndex?{...row,data:row.data?.map((point,j)=>j===position.dataIndex?{...point,symbolSize:14}:point)}:row)};
}
type TimeWindow={start:string;end:string};
/** Read the displayed window only; chart zoom never changes the source events. */
export function chartTimeWindow(option:echarts.EChartsCoreOption):TimeWindow|null{
 const axis=(Array.isArray(option.xAxis)?option.xAxis[0]:option.xAxis) as {type?:string;min?:unknown;max?:unknown}|undefined;
 const zooms=(Array.isArray(option.dataZoom)?option.dataZoom:[option.dataZoom]) as {type?:string;start?:unknown;end?:unknown;labelFormatter?:(value:number)=>string}[];
 const zoom=zooms.find(row=>row?.type==='slider'),a=axis?.min,b=axis?.max;
 if(axis?.type!=='time'||typeof a!=='number'||typeof b!=='number'||!Number.isFinite(a)||!Number.isFinite(b)||a>=b||typeof zoom?.labelFormatter!=='function')return null;
 const start=zoom.start===undefined?0:zoom.start,end=zoom.end===undefined?100:zoom.end;
 if(typeof start!=='number'||typeof end!=='number'||!Number.isFinite(start)||!Number.isFinite(end)||start<0||end>100||start>=end)return null;
 return {start:zoom.labelFormatter(a+(b-a)*start/100),end:zoom.labelFormatter(a+(b-a)*end/100)};
}
type ZoomColors={primary:string;surface:string;border:string;selection:string;track:string};
export function timeZoomOption(option:echarts.EChartsCoreOption,colors:ZoomColors):echarts.EChartsCoreOption{
 if(!chartTimeWindow(option))return option;
 // A modal can initialize before inherited CSS tokens are attached. Empty paint
 // values make the slider and handles invisible; retain the approved light theme.
 colors={primary:colors.primary||'#006e93',surface:colors.surface||'#ffffff',border:colors.border||'#b2b8be',selection:colors.selection||'rgba(0, 216, 236, .2)',track:colors.track||'#eff0f1'};
 const zooms=(Array.isArray(option.dataZoom)?option.dataZoom:[option.dataZoom]) as Record<string,unknown>[];
 return {...option,grid:{...(option.grid as object),bottom:80},dataZoom:zooms.map(row=>row.type!=='slider'?{...row,filterMode:'none'}:{...row,filterMode:'none',
  height:28,bottom:14,handleSize:32,brushSelect:false,
  handleIcon:'path://M-9,-14H9V14H-9Z M-3,-6H-4V6H-3Z M4,-6H3V6H4Z',
  backgroundColor:colors.track,borderColor:colors.border,fillerColor:colors.selection,
  handleStyle:{color:colors.surface,borderColor:colors.primary,borderWidth:2},
  moveHandleSize:8,moveHandleStyle:{color:colors.primary,opacity:0.8},
  dataBackground:{lineStyle:{color:colors.border,width:1},areaStyle:{color:colors.track}},
  selectedDataBackground:{lineStyle:{color:colors.primary,width:1},areaStyle:{color:colors.selection}},
  textStyle:{color:colors.primary},emphasis:{handleStyle:{borderColor:colors.primary},moveHandleStyle:{opacity:1}}})};
}
type Props={controls?:boolean;compact?:boolean;updateMode?:'replace'|'merge';selectionMode?:'emphasis'|'select';focusRequest?:ChartFocusRequest;identityKey:string;label:string;option:echarts.EChartsCoreOption;onPick?:(id:string)=>void;selectedEventId?:string};

/** Rendering only. Identity, evidence availability and units remain domain-owned. */
export function PlanningChart({controls=true,compact=false,updateMode='replace',selectionMode='emphasis',identityKey,label,option,onPick,selectedEventId,focusRequest}:Props){
 const [focusNotice,setFocusNotice]=useState(''),host=useRef<HTMLDivElement>(null),instance=useRef<echarts.EChartsType|null>(null),pick=useRef(onPick);
 const [viewportRange,setViewportRange]=useState<{identity:string;value:TimeWindow|null}|null>(null);
 const timeRange=chartTimeWindow(option),shownRange=timeRange&&(viewportRange?.identity===identityKey?viewportRange.value:timeRange);
 const focusedId=focusRequest?.identityKey===identityKey&&focusRequest.eventId===selectedEventId?selectedEventId:undefined;
 useEffect(()=>{pick.current=onPick},[onPick]);
 useEffect(()=>{
  if(!host.current)return;
  registerTheme(echarts);
  const chart=echarts.init(host.current,'theme-classic-light',{renderer:'svg'});instance.current=chart;
  const clicked=(event:unknown)=>{const value=(event as {data?:{eventId?:unknown}}).data;if(typeof value?.eventId==='string'&&value.eventId)pick.current?.(value.eventId)};
  chart.on('click',clicked);
  const zoomed=()=>setViewportRange({identity:identityKey,value:chartTimeWindow(chart.getOption())});chart.on('datazoom',zoomed);
  const observer=new ResizeObserver(()=>{if(!chart.isDisposed())chart.resize()});observer.observe(host.current);
  return()=>{observer.disconnect();chart.off('click',clicked);chart.off('datazoom',zoomed);chart.dispose();if(instance.current===chart)instance.current=null};
 },[identityKey]);
 useEffect(()=>{
  const chart=instance.current;if(!chart||!host.current)return;
  const css=getComputedStyle(host.current),color=(name:string)=>css.getPropertyValue(name).trim();
  const themed=timeZoomOption(option,{primary:color('--primary'),surface:color('--surface'),border:color('--control-border'),selection:color('--selection'),track:color('--panel')});
  chart.setOption(focusedChartOption(themed,focusedId),updateMode==='merge'?{notMerge:false,replaceMerge:['series']}:{notMerge:true});
  setViewportRange({identity:identityKey,value:chartTimeWindow(chart.getOption())});
 },[identityKey,option,focusedId,updateMode]);
 useEffect(()=>{
  const chart=instance.current;if(!chart)return;chart.dispatchAction({type:'downplay'});
  if(selectionMode==='select'){
   const series=(Array.isArray(option.series)?option.series:option.series?[option.series]:[]) as {data?:{eventId?:string}[]}[];
   series.forEach((row,seriesIndex)=>chart.dispatchAction({type:'unselect',seriesIndex,dataIndex:(row.data??[]).map((_,index)=>index)}));
   const target=selectedEventId?chartEventPosition(option,selectedEventId):null;
   if(target)chart.dispatchAction({type:'select',...target});
   return;
  }
  if(!selectedEventId)return;
  const all=(option.series??[]) as {data?:{eventId?:string}[]}[];
  all.forEach((series,seriesIndex)=>{const dataIndex=series.data?.findIndex(row=>row.eventId===selectedEventId)??-1;if(dataIndex>=0)chart.dispatchAction({type:'highlight',seriesIndex,dataIndex})});
 },[selectedEventId,identityKey,option,selectionMode]);
 useEffect(()=>{
  if(!focusRequest||focusRequest.identityKey!==identityKey)return;
  const chart=instance.current;if(!chart||chart.isDisposed())return;
  const position=chartEventPosition(option,focusRequest.eventId);
  if(!position){setFocusNotice('当前图表范围没有该事件，请核对所选物料和期间。');return}
  setFocusNotice('');
  chart.dispatchAction({type:'dataZoom',start:0,end:100});
  chart.dispatchAction({type:'highlight',...position});
  host.current?.focus({preventScroll:true});host.current?.scrollIntoView({block:'center',inline:'nearest'});
  const frame=requestAnimationFrame(()=>{if(instance.current===chart&&!chart.isDisposed())chart.dispatchAction({type:'showTip',...position})});
  return()=>cancelAnimationFrame(frame);
 },[focusRequest,identityKey]);
 const reset=()=>instance.current?.dispatchAction({type:'dataZoom',start:0,end:100});
 return <figure className={'planning-chart'+(compact?' planning-chart-compact':'')}>{controls&&<div className="planning-chart-actions">{!compact&&<span>{timeRange?'图例可切换；Ctrl＋滚轮缩放时间。':'图例可切换；Ctrl＋滚轮缩放，底部滑块调整范围。'}</span>}{!timeRange&&<Button onClick={reset}>复位图表视窗</Button>}</div>}<div ref={host} className="planning-chart-canvas" tabIndex={-1} role="img" aria-label={label}/>{timeRange&&<div className="planning-chart-time-range"><div><strong>时间缩放</strong><output aria-label="当前图表时间范围">{shownRange?`${shownRange.start} → ${shownRange.end}`:'查看范围未知'}</output><Button onClick={reset}>显示全部时间</Button></div><p>拖动两端把手放大时段，拖动中间选区前后查看。</p></div>}{focusNotice&&<p role="status">{focusNotice}</p>}<figcaption className={compact?'sr-only':undefined}>图表与下方业务明细使用同一数据。可通过明细表使用键盘查看与定位；图表缩放不改变规划结果。</figcaption></figure>;
}
