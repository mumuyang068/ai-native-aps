import type {Dict} from '../api/contracts';
import type {EChartsCoreOption} from 'echarts/core';
import {formatSiteTime} from './timeRange';
import {reportRowId} from './reportModel';
import {unit} from './planModel';
import {chartColors,chartSeriesStyle,reportMetricChartStyle} from '../design/chartSemantics';

/** Native ECharts bars/line; this is a business projection, not a new chart engine. */
export function periodChartOption(rows:Dict[],mode:'resource'|'inventory',timezone:string):EChartsCoreOption{
 const keys=new Set(rows.map(r=>mode==='resource'?r.resource_id:JSON.stringify([r.item_id,r.location_id,r.uom])));
 if(keys.size>1)throw Error('图表必须选择同一资源，或同一物料、地点及单位。');
 const number=(v:unknown)=>v===null||v===undefined||v===''||typeof v==='boolean'||!Number.isFinite(Number(v))?null:Number(v);
 const uom=mode==='resource'?'容量小时':unit(rows[0]?.uom);
 const definitions=mode==='resource'?[['load_hours','计划负荷','bar',1],['available_hours','日历可用','line',1]]:[['inflow','合法流入','bar',1],['outflow','合法流出','bar',-1],['ending','期末净量','line',1]];
 return {animationDuration:160,animationDurationUpdate:120,aria:{enabled:true,label:{description:rows.map(r=>formatSiteTime(r.start_at,timezone)+'：'+definitions.map(([field,name])=>name+' '+(number(r[String(field)])===null?'未核验':r[String(field)]+' '+uom)).join('，')).join('；')}},
  legend:{top:2},tooltip:{trigger:'axis',confine:true,valueFormatter:(v:unknown)=>number(v)===null?'未核验':Number(v)+' '+uom},
  grid:{left:60,right:24,top:48,bottom:42},xAxis:{type:'category',data:rows.map(r=>formatSiteTime(r.start_at,timezone).slice(5,16)),axisLabel:{hideOverlap:true}},
  yAxis:{type:'value',name:uom},series:definitions.map(([field,name,type,sign])=>({name,type,...(field==='available_hours'?{...chartSeriesStyle('baseline','baseline'),symbol:'emptyRect'}:reportMetricChartStyle(String(field),Number(sign)===-1)),
   ...(mode==='inventory'&&type==='bar'?{stack:'flow'}:{}),connectNulls:false,
   data:rows.map(r=>({eventId:reportRowId(r),value:number(r[String(field)])===null?null:Number(r[String(field)])*Number(sign),...(mode==='resource'&&field==='load_hours'&&number(r.load_hours)!==null&&number(r.available_hours)!==null&&Number(r.load_hours)>Number(r.available_hours)?{itemStyle:{color:chartColors().danger,decal:{symbol:'rect',color:'#ffffff',rotation:-Math.PI/4,dashArrayX:[1,0],dashArrayY:[1,4]}}}: {})})),
   emphasis:{focus:'series'},barMaxWidth:26})) as any};
}
