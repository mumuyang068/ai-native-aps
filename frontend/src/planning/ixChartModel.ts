import type {EChartsCoreOption} from 'echarts/core';
import type {Dict,PlanView} from '../api/contracts';
import {inventoryPoints,eventLabels,analysisUnit} from './analysisModel';
import {inventoryPolicyLines} from './inventoryDecisionModel';
import {formatSiteTime} from './timeRange';
import {chartColors,chartSeriesStyle} from '../design/chartSemantics';

export function chartNumber(value:unknown):number|null{return value!==null&&value!==undefined&&value!==''&&typeof value!=='boolean'&&Number.isFinite(Number(value))?Number(value):null}
export type InventoryDatum={value:[number,number|null];eventId?:string;reference?:string;eventLabel?:string;nativeSequence?:unknown};
/** Preserve existing projection order including repeated timestamps. Never sort
 * by balance or add a millisecond to make a supply appear before consumption. */
export function inventoryChartData(series:Dict,a:number,b:number):InventoryDatum[]{
 if(chartNumber(series.opening)===null||!Number.isFinite(a)||!Number.isFinite(b)||a>=b)return [];
 const events:Dict[]=series.events??[],byId=new Map(events.map(e=>[e.id,e]));
 return inventoryPoints(series,new Date(a).toISOString(),new Date(b).toISOString()).map(point=>{
  const event=point.eventId?byId.get(point.eventId):undefined;
  const balance=event?chartNumber(event.balance):chartNumber(point.balance);
  return {value:[point.at,balance],...(event?{eventId:point.eventId,reference:String(event.reference??''),eventLabel:eventLabels[event.kind]??'原生物料事件',nativeSequence:event.native_sequence}:{})};
 });
}
const safeTime=(value:number,timezone:string)=>Number.isFinite(value)?formatSiteTime(new Date(value).toISOString(),timezone):'时点未知';
export function inventoryChartOption(series:Dict,base:Dict|undefined,a:number,b:number,timezone:string,policy?:Dict|null,policyAuditStatus?:string):EChartsCoreOption{
 const points=inventoryChartData(series,a,b),old=base?inventoryChartData(base,a,b):[],targets=inventoryPolicyLines(policy,series,a,b,policyAuditStatus),unit=analysisUnit(series.uom);
 const tooltip=(params:unknown)=>((Array.isArray(params)?params:[params]) as {seriesName?:string;data?:InventoryDatum}[]).map(p=>{const d=p.data;if(!d?.value)return '';return `${p.seriesName??''}\n${safeTime(d.value[0],timezone)}\n净可用 ${d.value[1]===null?'未知':d.value[1]+' '+unit}${d.eventLabel?'\n'+d.eventLabel+' · 同刻序号 '+String(d.nativeSequence??'未知'):''}${d.reference?'\n'+d.reference:''}`}).filter(Boolean).join('\n\n');
 const main:Dict={type:'line',name:base?'候选方案净可用':'当前方案净可用',...chartSeriesStyle(base?'proposed':'planned',base?'candidate':'normal'),step:'end',connectNulls:false,symbolSize:8,showSymbol:true,data:points,emphasis:{focus:'series'}};
 if(targets){main.markArea={silent:true,itemStyle:{color:chartColors().warning,opacity:0.07},data:[[{name:'已核验目标区间',xAxis:targets.start,yAxis:targets.minimum},{xAxis:targets.end,yAxis:targets.maximum}]]};main.markLine={silent:true,symbol:'none',lineStyle:{type:'dashed',color:chartColors().warning},data:[[{name:'软最低目标',coord:[targets.start,targets.minimum]},{coord:[targets.end,targets.minimum]}],[{name:'补给最高目标',coord:[targets.start,targets.maximum]},{coord:[targets.end,targets.maximum]}]]}}
 return {animation:false,aria:{enabled:true,description:`${series.item_id} ${series.location_id} 净可用库存，单位${unit}。事件顺序与下方明细表一致；这是原生计划量，不代表实际收发。`},tooltip:{trigger:'axis',renderMode:'richText',confine:true,formatter:tooltip},legend:{top:4},grid:{top:48,left:58,right:24,bottom:72},xAxis:{type:'time',min:a,max:b,axisLabel:{formatter:(v:number)=>safeTime(v,timezone).slice(5)}},yAxis:{type:'value',name:`净可用（${unit}）`,scale:true},dataZoom:[{type:'inside',zoomOnMouseWheel:'ctrl',moveOnMouseMove:'shift'},{type:'slider',bottom:8,height:20,labelFormatter:(v:number)=>safeTime(v,timezone).slice(5)}],series:[...(old.length?[{type:'line',name:'原始基线净可用',...chartSeriesStyle('baseline','baseline'),step:'end',connectNulls:false,showSymbol:true,symbolSize:5,data:old}]:[]),main]};
}

type DemandComparisonRow={id:string;uom:string;before:number|null;after:number|null;shortBefore:number|null;shortAfter:number|null};
export function networkDemandChartRows(base:PlanView,view:PlanView):{status:'PRESENT'|'NOT_CHECKED';reason?:string;rows:DemandComparisonRow[]}{
 const b=base.context?.enterprise_source,c=view.context?.enterprise_source;
 if(!b?.plan_id||!b.data_version_id||b.plan_id!==c?.plan_id||b.data_version_id!==c.data_version_id)return {status:'NOT_CHECKED',reason:'计划或数据版本身份未完整核实，不绘制方案比较。',rows:[]};
 if(!base.result||!view.result)return {status:'NOT_CHECKED',reason:'两侧运行结果未齐备，不把未运行视为0。',rows:[]};
 const before=base.result.deliveries??[],after=view.result.deliveries??[];
 const ids=[...new Set([...before,...after].map(r=>r.demand_id))],rows:DemandComparisonRow[]=[];
 for(const id of ids){const xs=before.filter(r=>r.demand_id===id),ys=after.filter(r=>r.demand_id===id),x=xs[0],y=ys[0];if(xs.length>1||ys.length>1||!id||!(x?.uom??y?.uom)||(x&&y&&x.uom!==y.uom))return {status:'NOT_CHECKED',reason:'需求行身份或单位不能可靠对齐，保留原始业务明细。',rows:[]};rows.push({id,uom:x?.uom??y.uom,before:chartNumber(x?.planned_quantity),after:chartNumber(y?.planned_quantity),shortBefore:chartNumber(x?.unfulfilled_quantity),shortAfter:chartNumber(y?.unfulfilled_quantity)})}
 return {status:'PRESENT',rows};
}
export function demandComparisonOption(rows:DemandComparisonRow[],uom:string):EChartsCoreOption{
 const unit=analysisUnit(uom),names=['基线计划满足','候选计划满足','基线尚未满足','候选尚未满足'];
 return {animation:false,aria:{enabled:true,description:`同计划同版本需求行比较，单位${unit}。缺失数量不显示为0。基线为斜纹空心柱，候选为实心柱。`},tooltip:{trigger:'axis',renderMode:'richText',confine:true,valueFormatter:(v:unknown)=>chartNumber(v)===null?'未知':String(v)+' '+unit},legend:{top:4},grid:{top:58,left:58,right:20,bottom:78},xAxis:{type:'category',data:rows.map(r=>r.id),axisLabel:{hideOverlap:true}},yAxis:{type:'value',name:`数量（${unit}）`,min:0},dataZoom:[{type:'inside',zoomOnMouseWheel:'ctrl',moveOnMouseMove:'shift'},{type:'slider',bottom:8,height:20}],series:(['before','after','shortBefore','shortAfter'] as const).map((key,i)=>({type:'bar',name:names[i],...chartSeriesStyle(i>=2?'warning':i===0?'baseline':'proposed',i%2?'candidate':'baseline'),data:rows.map(r=>({value:r[key],eventId:r.id})),emphasis:{focus:'series'}}))};
}
