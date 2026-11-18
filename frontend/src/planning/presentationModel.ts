import type {Dict,NativeEvidence,OrderTrace,PlanContext} from '../api/contracts';
import {qty,unit} from './planModel';
import {siteDate} from './timeRange';
export function demandEndpointKind(context:Pick<PlanContext,'wip'>|undefined){
 const endpoint=context?.wip?.stage_endpoint;
 if(endpoint?.kind==='CUSTOMER_FG'||endpoint?.scope==='BOUNDED_SYNTHETIC_FINISHED_CUSTOMER_DEMAND')return 'CUSTOMER_FG';
 return endpoint?'STAGE_OUTPUT':'ORDER_DELIVERY';
}
export function demandTabLabel(context:Pick<PlanContext,'wip'|'net_forecast_deliveries'>|undefined){if(context?.net_forecast_deliveries?.length)return demandEndpointKind(context)==='CUSTOMER_FG'?'FG需求（Firm / Net） ':'订单 / 净预测需求 ';return {CUSTOMER_FG:'客户FG需求 ',STAGE_OUTPUT:'阶段需求 ',ORDER_DELIVERY:'订单交付 '}[demandEndpointKind(context)]}
/** Keep each proposed output's item, location and unit; stage outputs are never added to FG. */
export function stageManufactureText(result:Dict|undefined){return [...(result?.manufacture_suggestions??[])].sort((a:Dict,b:Dict)=>Date.parse(a.start_at)-Date.parse(b.start_at)).map((row:Dict)=>row.item_id+' @ '+(row.location_id??'地点未提供')+' '+qty(row.quantity)+' '+unit(row.uom)).join('；')||'本次无制造建议'}
export function qualifiedResourceRows(report:Dict|undefined,reference?:string){
 const rows=Array.isArray(report?.assignments)?report.assignments:[];
 return rows.filter((r:Dict)=>!reference||r.native_reference===reference);
}
/** Static native assignment presentation; this does not grant dynamic analysis or draft actions. */
export function hasQualifiedResourceView(input:Dict){return ['machine-qualified-operator-v1','mixed-fixed-batch-confirmed-qualified-v1','mixed-fixed-batch-confirmed-cut-qualified-v1','finished-garment-confirmed-v1','enterprise-forecast-fg-v1','enterprise-accessory-supply-v1'].includes(input.rules?.manufacturing_profile)}
export function equipmentScheduleResources(resources:Dict[],input:Dict){return hasQualifiedResourceView(input)?resources.filter(r=>r.role==='MACHINE'):resources}
export function activeOrder(orders:Dict[],selected?:string|null){return selected?orders.find(o=>o.id===selected):orders.length===1?orders[0]:undefined}
export type TimedTask={id:string;start_at:string;end_at:string};
export function taskDaySpan(task:TimedTask,timezone:string){const start=siteDate(task.start_at,timezone),end=siteDate(task.end_at,timezone);return {start,days:Math.max(1,Math.round((Date.parse(end)-Date.parse(start))/86400000)+1)}}
export function layoutTaskBars<T extends TimedTask>(tasks:T[],start:number,end:number,trackWidth:number){
 const width=Math.max(24,trackWidth),range=end-start,ends:number[]=[],bars:{task:T;lane:number;left:number;hitWidth:number;barLeft:number;barWidth:number;showLabel:boolean}[]=[];
 if(range<=0)return {bars,lanes:0};
 for(const task of [...tasks].sort((a,b)=>Date.parse(a.start_at)-Date.parse(b.start_at)||a.id.localeCompare(b.id))){
  const a=Math.max(start,Date.parse(task.start_at)),b=Math.min(end,Date.parse(task.end_at));if(b<=a)continue;
  const barX=(a-start)/range*width,barWidth=(b-a)/range*width,hitWidth=Math.max(24,barWidth),left=Math.max(0,Math.min(width-hitWidth,barX-(hitWidth-barWidth)/2));
  let lane=ends.findIndex(x=>x<=left);if(lane<0)lane=ends.length;ends[lane]=left+hitWidth+2;
  bars.push({task,lane,left,hitWidth,barLeft:barX-left,barWidth,showLabel:barWidth>=68});
 }
 return {bars,lanes:ends.length};
}
export function supplyRows(context:PlanContext,input:Dict){
 const units=new Map<string,string>((input.items??[]).map((i:Dict)=>[i.id,i.base_uom]));
 return context.effective_constraints.filter(c=>c.id.startsWith('supply:')).map(c=>{
  const v=c.value as Dict,source=(input.supply_sources??[]).find((s:Dict)=>s.id===c.id.slice(7)&&s.item_id===v.item_id&&s.supplier_id===v.supplier_id),u=units.get(v.item_id),seconds=Number(v.lead_time_seconds);
  const span=Number.isFinite(seconds)?seconds%86400===0?`${seconds/86400}`:null:null;
  const leadTime=!Number.isFinite(seconds)?'尚未提供':source?.lead_time_basis==='CALENDAR_TIME'?(span!==null?span+' 个自然日':qty(seconds/3600)+' 小时（连续计时）'):(span!==null?span+' 天':qty(seconds/3600)+' 小时')+'（计时方式未注明）';
  return {id:c.id,item:v.item_id,supplier:v.supplier_id,leadTime,minimum:qty(v.minimum_order_quantity)+' '+(u?unit(u):'单位未提供'),multiple:qty(v.order_multiple)+' '+(u?unit(u):'单位未提供'),source:c.source};
 });
}
export function relatedQuantity(order:OrderTrace,kind:string,entry:NativeEvidence){
 const value=entry.task?.quantity??entry.record.quantity,u=entry.item_uom??entry.task?.uom??(kind==='operations'?order.manufacturing.find(m=>m.engine_ref===entry.owner_ref)?.task?.uom:undefined);
 const number=value==null?'—':qty(String(value).replace(/(\.\d*?)0+$/,'$1').replace(/\.$/,''));
 return number+(kind==='resources'?'': ' '+(u?unit(u):'单位未提供'));
}
export function chronologicalEntries(entries:NativeEvidence[]){return [...entries].sort((a,b)=>Date.parse(a.task?.start_at??a.times?.startdate??'')-Date.parse(b.task?.start_at??b.times?.startdate??''))}

export function distributionView(input:Record<string,any>,result:Record<string,any>,report?:Record<string,any>){
 const checked=report?.status==='PASS';
 return {suggestions:result.distribution_suggestions??[],deliveries:result.deliveries??[],
  locations:(input.inventory_positions??[]).map((r:Record<string,any>)=>({...r,
   ending:checked?(report?.summary?.locations??[]).find((x:Record<string,any>)=>x.location_id===r.location_id)?.ending??null:null}))};
}
