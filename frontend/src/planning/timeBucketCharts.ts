import type {EChartsCoreOption} from 'echarts/core';
import {readReportCell,reportCellId,type TimeBucketReportData} from './timeBucketReportModel';
import {reportMetricChartStyle} from '../design/chartSemantics';
/** Native multi-grid, bars and lines. No aggregation and no per-cell chart instances. */
export function timeBucketChartOption(report:TimeBucketReportData,visibleObjectKeys:string[],reducedMotion=false):EChartsCoreOption{
 const objects=visibleObjectKeys.map(key=>report.objects.find(o=>o.key===key)).filter(o=>!!o),metrics=report.metrics.filter(m=>m.chart),rowHeight=116;
 return {animation:!reducedMotion,animationDuration:220,animationDurationUpdate:160,aria:{enabled:true},
  tooltip:{trigger:'axis',confine:true,renderMode:'richText',transitionDuration:.12,formatter:(params:unknown)=>{
   const rows=(Array.isArray(params)?params:[params]) as {seriesName:string;data:{raw:unknown;unit:string;bucketLabel:string;objectLabel:string}}[];
   return rows.length?[rows[0].data.objectLabel+' · '+rows[0].data.bucketLabel,...rows.map(p=>p.seriesName+'  '+(p.data.raw==null?'未知':String(p.data.raw))+' '+p.data.unit)].join('\n'):'';
  }},legend:{top:0,data:metrics.map(m=>m.label)},
  grid:objects.map((_,i)=>({left:220,right:36,top:54+i*rowHeight,height:78})),
  xAxis:objects.map((_,i)=>({type:'category',gridIndex:i,position:i===0?'top':'bottom',data:report.buckets.map(b=>b.label+(b.partial?' *':'')),axisLabel:{show:i===0||i===objects.length-1,hideOverlap:true},axisTick:{show:i===0||i===objects.length-1},axisLine:{show:i===0||i===objects.length-1},boundaryGap:true})),
  yAxis:objects.map((o,i)=>({type:'value',gridIndex:i,name:o.label+'\n'+o.unit+' · 独立量程',nameLocation:'middle',nameRotate:0,nameGap:115,nameTextStyle:{align:'center',width:148,overflow:'break',lineHeight:18},axisLabel:{fontSize:10},splitNumber:2,splitLine:{lineStyle:{opacity:.2}}})),
  series:objects.flatMap((o,i)=>metrics.map(m=>({name:m.label,type:m.chart,...reportMetricChartStyle(m.id,m.direction===-1),xAxisIndex:i,yAxisIndex:i,barMaxWidth:22,step:m.chart==='line'?'end':undefined,connectNulls:false,symbolSize:4,emphasis:{focus:'series'},data:report.buckets.map(b=>{const raw=readReportCell(o,b.id,m.id);return {value:raw==null?null:Number(raw)*(m.direction??1),raw,unit:m.unit??o.unit,bucketLabel:b.label,objectLabel:o.label,eventId:reportCellId(o.key,b.id,m.id)}})}))),
  dataZoom:[{type:'slider',xAxisIndex:objects.map((_,i)=>i),bottom:6,height:22,handleSize:24,filterMode:'none'},{type:'inside',xAxisIndex:objects.map((_,i)=>i),zoomOnMouseWheel:'ctrl',filterMode:'none'}]};
}
