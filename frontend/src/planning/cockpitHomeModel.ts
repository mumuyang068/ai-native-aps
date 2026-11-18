import type {Dict} from '../api/contracts';
import {chartNumber,inventoryChartData} from './ixChartModel';
import {cockpitSupplyRows,type CockpitScope} from './cockpitModel';
import {fulfillmentGroupId} from './demandFulfillmentModel';
import {supports} from './capabilityModel';

export const homeRows=(value:unknown):Dict[]=>Array.isArray(value)?value:[];
export const homeReady=(value:Dict|undefined)=>!!value&&['PRESENT','EMPTY_CHECKED'].includes(value.status)&&value.validation?.status==='PASS'&&!value.validation.issues?.length&&!value.validation.missing_evidence?.length;
const bad=(value:Dict|null)=>!!value&&(value.known_business_fail===true||value.native_result_status?.constraint_check==='FAIL'||[value.demand,value.resource,value.inventory,value.inventory_analysis,value.resource_analysis].some(v=>v?.validation?.status==='FAIL'));

/** Combine disjoint certified report buckets for the same named resource only. */
export function homeResources(report:Dict):Dict[]{
 if(bad(report)||!supports(report.identity?.profile,'analyses','resources')||!homeReady(report.resource))return [];
 const groups=new Map<string,Dict>();
 for(const row of homeRows(report.resource.rows)){
  if(!['MACHINE','OPERATOR'].includes(row.role)||!row.resource_id)continue;
  const id=JSON.stringify([row.resource_id,row.role]),g=groups.get(id)??{id,resource_id:row.resource_id,role:row.role,load:0,available:0,known:true,interval_ids:[],periods:[],start_at:row.start_at,end_at:row.end_at};
  const a=Date.parse(row.start_at),b=Date.parse(row.end_at),load=chartNumber(row.load_hours),available=chartNumber(row.available_hours);
  if(!Number.isFinite(a)||!Number.isFinite(b)||a>=b||load===null||available===null||load<0||available<0||g.periods.some((p:number[])=>a<p[1]&&b>p[0])||load>0&&!row.interval_ids?.length)g.known=false;
  else{g.load+=load;g.available+=available;g.periods.push([a,b]);g.interval_ids.push(...homeRowsIds(row.interval_ids));if(a<Date.parse(g.start_at))g.start_at=row.start_at;if(b>Date.parse(g.end_at))g.end_at=row.end_at}
  groups.set(id,g);
 }
 const concern=(r:Dict)=>!r.known||r.available===0&&r.load>0;
 return [...groups.values()].map<Dict>(g=>({...g,interval_ids:[...new Set(g.interval_ids)],percent:g.known&&g.available>0?g.load/g.available*100:null})).sort((a,b)=>Number(concern(b))-Number(concern(a))||(b.percent??-1)-(a.percent??-1)||a.resource_id.localeCompare(b.resource_id));
}
const homeRowsIds=(v:unknown):string[]=>Array.isArray(v)?v.filter((x):x is string=>typeof x==='string'):[];

/** A received batch is one whole; raw amounts from unlike units never share an axis. */
export function homeQuality(po:Dict){
 const amounts=[po.released_received_quantity,po.qc_waiting_quantity,po.frozen_received_quantity].map(chartNumber),received=chartNumber(po.received_quantity);
 if(received===null||received<=0||amounts.some(n=>n===null||n<0)||Math.abs(amounts.reduce<number>((s,n)=>s+n!,0)-received)>1e-6)return null;
 return amounts.map(n=>({quantity:n!,percent:n!/received*100}));
}

/** Derive the selected-window balances using the existing ordered event projection. */
export function homeStocks(report:Dict,native:Dict|null):Dict[]{
 if(bad(report)||bad(native)||!supports(report.identity?.profile,'analyses','inventory')||!homeReady(native?.inventory_analysis))return [];
 const a=Date.parse(report.range?.start_at),b=Date.parse(report.range?.end_at);if(!Number.isFinite(a)||!Number.isFinite(b)||a>=b)return [];
 const risk=native?.inventory_analysis?.risk,risks=risk?.validation?.status==='PASS'?homeRows(risk.rows):[];
 return homeRows(native?.inventory_analysis.series).filter(s=>s.complete===true&&Date.parse(s.verified_through)>=b&&['PRESENT','EMPTY_CHECKED'].includes(s.event_status)&&s.item_id&&s.location_id&&s.uom).flatMap<Dict>(s=>{
  const observed={...s,events:homeRows(s.events).filter(e=>Date.parse(e.at)<b)},points=inventoryChartData(observed,a,b);if(!points.length||points.some(p=>p.value[1]===null))return [];
  const observation=risks.find(r=>r.item_id===s.item_id&&r.location_id===s.location_id&&r.uom===s.uom);
  return [{...observed,points,openingInWindow:points[0].value[1]!,endingInWindow:points.at(-1)!.value[1]!,minimumInWindow:Math.min(...points.map(p=>p.value[1]!)),risk:observation}];
 }).sort((x,y)=>Number(!!y.risk&&y.risk.category!=='NO_NEGATIVE_OBSERVED')-Number(!!x.risk&&x.risk.category!=='NO_NEGATIVE_OBSERVED')||x.item_id.localeCompare(y.item_id)||x.location_id.localeCompare(y.location_id));
}

export type HomeDelta={id:string;domain:string;label:string;object:string;uom:string;before:number;after:number;delta:number;category?:string;params:Record<string,string>};
export function homeComparison(current:Dict,baseline:Dict|null,native:Dict|null,baseNative:Dict|null,context:Dict,baseContext:Dict,scope:CockpitScope){
 const rows:HomeDelta[]=[],notes:string[]=[];
 const same=(a:unknown,b:unknown)=>typeof a==='string'&&a===b;
 if(!baseline)return {rows,notes:['基线结果尚未读取'],valid:false};
 const x=current.identity??{},y=baseline.identity??{};
 if(bad(current)||bad(baseline)||bad(native)||bad(baseNative))return {rows,notes:['运行存在业务违规，停止绘制差异'],valid:false};
 if(!same(x.plan_id,y.plan_id)||x.plan_id!==scope.planId||!same(x.data_version_id,y.data_version_id)||x.data_version_id!==scope.versionId||!same(x.profile,y.profile)||y.target!=='baseline'||x.target!==scope.target||x.run_id!==scope.runId||!y.run_id||y.run_id===x.run_id||!same(current.timezone,baseline.timezone)||current.grain!==baseline.grain||!same(current.range?.start_at,baseline.range?.start_at)||!same(current.range?.end_at,baseline.range?.end_at))return {rows,notes:['计划、版本、运行或观察范围无法对齐'],valid:false};
 const compare=(domain:string,before:Dict[],after:Dict[],key:(r:Dict)=>string,value:(r:Dict)=>number|null,label:(r:Dict)=>string,unit:(r:Dict)=>string,params:(r:Dict)=>Record<string,string>,category?:(r:Dict)=>string)=>{
  const keys=[...new Set([...before,...after].map(key))];
  for(const id of keys){const xs=before.filter(r=>key(r)===id),ys=after.filter(r=>key(r)===id);if(xs.length!==1||ys.length!==1){notes.push(domain+'：对象或摘要范围变化，未按零补齐');continue}const a=value(xs[0]),b=value(ys[0]),uom=unit(ys[0]);if(a===null||b===null||!uom||unit(xs[0])!==uom){notes.push(domain+'：数量、单位或时点待核验');continue}rows.push({id:domain+':'+id,domain,object:label(ys[0]),label:label(ys[0]),uom,before:a,after:b,delta:b-a,category:category?.(ys[0]),params:params(ys[0])})}
 };
 if(homeReady(current.demand)&&homeReady(baseline.demand)){
  const lines=(r:Dict)=>homeRows(r.demand.lines).filter(l=>l.delivery_validation_status==='PASS'&&['PRESENT','EMPTY_CHECKED'].includes(l.delivery_evidence_status));
  compare('需求',lines(baseline),lines(current),l=>JSON.stringify([l.demand_id,fulfillmentGroupId(l),l.demand_kind]),l=>chartNumber(l.unfulfilled_quantity),l=>(l.demand_kind==='NET_FORECAST'?l.item_id+' · 净预测':l.order_ref??l.item_id)+' · 未排',l=>l.uom,l=>({analysis:'reports',report:'demand',report_line_query:l.demand_id,report_group:fulfillmentGroupId(l),...(['FIRM_ORDER','NET_FORECAST'].includes(l.demand_kind)?{report_demand_kind:l.demand_kind}:{})}),l=>['FIRM_ORDER','NET_FORECAST'].includes(l.demand_kind)?l.demand_kind:'UNKNOWN');
 }else notes.push('需求：两侧证据未齐备');
 const baseScope={...scope,runId:y.run_id,target:'baseline'};
 const poX=cockpitSupplyRows(current,context,scope),poY=cockpitSupplyRows(baseline,baseContext,baseScope);
 if(poX.length||poY.length){
  const key=(p:Dict)=>JSON.stringify([p.id,p.item_id,p.location_id,p.uom]);
  compare('采购',poY,poX,key,p=>chartNumber(p.remaining_quantity??p.quantity),p=>(p.external_ref??p.id)+' · 未收',p=>p.uom,p=>p.action.params);
  compare('采购',poY,poX,p=>key(p)+':qc',p=>Number.isFinite(Date.parse(p.available_at))?Date.parse(p.available_at)/3600000:null,p=>(p.external_ref??p.id)+' · QC可用时点',()=> '小时',p=>p.action.params);
 }
 const nativeMatches=(n:Dict|null,identity:Dict)=>!!n&&['plan_id','data_version_id','run_id','target','profile'].every(k=>same(n.identity?.[k],identity[k]));
 if(nativeMatches(native,x)&&nativeMatches(baseNative,y)&&homeReady(native?.inventory_analysis)&&homeReady(baseNative?.inventory_analysis)){
  compare('库存',homeStocks(baseline,baseNative),homeStocks(current,native),s=>JSON.stringify([s.item_id,s.location_id,s.uom]),s=>chartNumber(s.endingInWindow),s=>s.item_id+' · '+s.location_id+' · 期末',s=>s.uom,s=>({analysis:'inventory',analysis_item:s.item_id,analysis_location:s.location_id}));
 }else notes.push('库存：两侧事件证据未齐备或未认证');
 if(homeReady(current.resource)&&homeReady(baseline.resource))compare('资源',homeResources(baseline),homeResources(current),r=>r.id,r=>r.known?r.load:null,r=>r.resource_id+' · 负荷',()=> '小时',r=>({analysis:'resources',analysis_resource:r.resource_id,analysis_start:r.start_at,analysis_end:r.end_at}));
 else notes.push('资源：两侧容量证据未齐备或未认证');
 return {rows,notes:[...new Set(notes)],valid:true};
}
