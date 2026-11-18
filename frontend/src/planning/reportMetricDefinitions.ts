import type {Dict} from '../api/contracts';
import {fulfillmentRowsForKind} from './demandFulfillmentModel';
import {reportObjectKey,type ReportMetric,type TimeBucketReportData,type ReportObject} from './timeBucketReportModel';
const m=(id:string,label:string,kind:ReportMetric['kind'],chart?:ReportMetric['chart'],extra:Partial<ReportMetric>={}):ReportMetric=>({id,label,kind,chart,...extra});
export function reportDemandKindRows(rows:Dict[],kind:string,lines:Dict[]=[]):Dict[]{
 const fields=['due_quantity','planned_quantity','overdue_unfulfilled','cumulative_due_quantity','cumulative_planned_quantity','unscheduled_quantity','not_yet_due_unfulfilled'];
 if(kind==='NET_FORECAST')return rows.map(row=>{
  const key=reportObjectKey(row),hasNet=lines.some(l=>reportObjectKey(l)===key&&l.demand_kind==='NET_FORECAST')||rows.some(r=>reportObjectKey(r)===key&&r.kind_totals?.NET_FORECAST);
  return hasNet?fulfillmentRowsForKind([row],kind,lines)[0]:{...row,...Object.fromEntries(fields.map(k=>[k,null])),demand_components:[],demand_ids:[],kind_evidence_status:'NOT_COLLECTED'};
 });
 if(kind==='UNCLASSIFIED')return rows.map(r=>({...r,...Object.fromEntries(fields.map(k=>[k,r.kind_totals?.UNCLASSIFIED?.[k]??null])),demand_components:(r.demand_components??[]).filter((c:Dict)=>!c.demand_kind||c.demand_kind==='UNCLASSIFIED'),demand_ids:(r.demand_components??[]).filter((c:Dict)=>!c.demand_kind||c.demand_kind==='UNCLASSIFIED').map((c:Dict)=>c.demand_id)}));
 return fulfillmentRowsForKind(rows,kind,lines);
}
export const inventoryPlanMetrics:ReportMetric[]=[m('opening','期初净可用','balance'),m('inflow','期间流入','flow','bar',{default:true}),m('outflow','期间流出','flow','bar',{direction:-1,default:true}),m('ending','期末净可用','balance','line',{default:true}),m('inflows.PURCHASE_AVAILABLE','采购可用','flow'),m('inflows.RECEIPT_QC_RELEASE','实收预计QC放行','flow'),m('inflows.MANUFACTURE_OUTPUT','制造产出','flow'),m('inflows.DISTRIBUTION_RECEIPT','调拨到达','flow'),m('outflows.MANUFACTURE_CONSUMPTION','制造耗用','flow'),m('outflows.DEMAND_CONSUMPTION','需求消耗','flow'),m('outflows.DISTRIBUTION_DISPATCH','配送发运','flow'),m('quality_blocked_opening','声明期初冻结','snapshot'),m('receipt_qc_waiting_end','已收待QC','balance'),m('qc_waiting_end','未收预计待QC','balance'),m('planned_in_transit_end','计划在途','balance')];
export const demandPlanMetrics:ReportMetric[]=[m('due_quantity','期间到期需求','flow','bar',{default:true}),m('planned_quantity','期间计划满足','flow','bar',{default:true}),m('overdue_unfulfilled','桶末逾期未满足','balance','line',{default:true}),m('cumulative_due_quantity','累计到期需求','balance'),m('cumulative_planned_quantity','累计计划满足','balance'),m('unscheduled_quantity','当前运行未排缺量','snapshot')];
export const historyDemandMetrics:ReportMetric[]=[m('new_orders','新增订单','flow',undefined,{unit:'笔'}),m('new_quantity','新生效需求','flow','bar',{default:true}),m('revision_quantity','需求修订净变化','flow'),m('shipped','实际发货','flow','bar',{default:true}),m('opening_open','期初未履行','balance'),m('ending_open','期末未履行','balance','line',{default:true}),m('overdue','期末逾期未履行','balance'),m('effective_demand','有效累计需求','balance')];
export const historyInventoryMetrics:ReportMetric[]=[m('opening_physical','期初物理库存','balance'),m('physical_in','实际入库','flow','bar',{default:true}),m('physical_out','实际出库','flow','bar',{default:true,direction:-1}),m('adjustment','有据调整','flow'),m('ending_physical','期末物理库存','balance','line',{default:true}),m('ending_pending','期末待检','balance'),m('ending_held','期末冻结','balance'),m('ending_available','期末可用','balance'),m('batches','变动批次','flow',undefined,{unit:'个'})];
function pathValue(row:Dict,path:string):string|number|null{let value:unknown=row;for(const k of path.split('.'))value=value&&typeof value==='object'?(value as Dict)[k]:null;return typeof value==='string'||typeof value==='number'?value:null}
export function planningBucketReport(scopeKey:string,rows:Dict[],mode:'inventory'|'demand'):TimeBucketReportData{
 const metrics=mode==='inventory'?inventoryPlanMetrics:demandPlanMetrics,objects=new Map<string,ReportObject>(),buckets=new Map<string,TimeBucketReportData['buckets'][number]>();
 for(const row of rows){
  const key=reportObjectKey(row),bucketId=JSON.stringify([row.start_at,row.end_at]);
  if(!buckets.has(bucketId))buckets.set(bucketId,{id:bucketId,start:row.start_at,end:row.end_at,label:String(row.start_at).slice(0,10)});
  let object=objects.get(key);if(!object){object={key,label:[row.item_id,row.location_id,row.endpoint].filter(Boolean).join(' · '),unit:row.uom,dimensions:row,cells:{},sourceRows:{}};objects.set(key,object)}
  if(object.cells[bucketId])throw Error('对象与期间重复，不能覆盖原报表记录。');
  object.cells[bucketId]=Object.fromEntries(metrics.map(metric=>[metric.id,pathValue(row,metric.id)]));object.sourceRows![bucketId]=row;
 }
 return {scopeKey,metrics,objects:[...objects.values()],buckets:[...buckets.values()].sort((a,b)=>a.start.localeCompare(b.start))};
}
export function historyBucketReport(scopeKey:string,data:Dict,domain:'demand'|'inventory'):TimeBucketReportData{
 const metrics=domain==='demand'?historyDemandMetrics:historyInventoryMetrics;
 return {scopeKey,metrics,buckets:data.buckets??[],objects:(data.groups??[]).map((g:Dict)=>({key:reportObjectKey(g.dimension),label:[g.dimension.item_id,g.dimension.location_id,g.dimension.customer_id].filter(Boolean).join(' · '),unit:g.dimension.uom,dimensions:g.dimension,cells:Object.fromEntries((data.buckets??[]).map((b:Dict,i:number)=>[b.id,g.cells[i]??{}])),states:Object.fromEntries((data.buckets??[]).map((b:Dict,i:number)=>[b.id,g.cells[i]?.status]))}))};
}
