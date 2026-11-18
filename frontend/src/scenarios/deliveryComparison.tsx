import {delayLabel,deliveryStatusLabel} from '../planning/orderModel';
import {qty,unit} from '../planning/planModel';
import {formatSiteTime} from '../planning/timeRange';

type DeliveryState={planned_quantity?:unknown;unfulfilled_quantity?:unknown;status?:unknown;planned_delivery_at?:unknown;due_at?:unknown};
export type DeliveryChange={demand_id?:string;kind?:string;base?:DeliveryState|null;candidate?:DeliveryState|null;planned_delivery_shift_seconds?:number|null;changed?:boolean};
type DemandIdentityContext={net_forecast_deliveries?:{demand_id:string}[];net_forecast_demands?:{id:string}[];inquiry_summary?:{demands?:{id:string}[]};order_summary?:{orders?:{id?:unknown}[]}};
/** Net demand is proved by the read contract, never by an ID prefix or shared item. */
export function comparisonDemandLabel(id:string,contexts:(DemandIdentityContext|undefined)[],changeKind?:string){
 if(contexts.some(context=>context?.net_forecast_deliveries?.some(row=>row.demand_id===id)||context?.net_forecast_demands?.some(row=>row.id===id)))return '净预测计划需求（非客户订单）';
 if(contexts.some(context=>context?.inquiry_summary?.demands?.some(row=>row.id===id)))return '非执行新单询价（非正式订单）';
 if(contexts.some(context=>context?.order_summary?.orders?.some(row=>row.id===id)))return '客户订单需求';
 return changeKind==='ADDED'?'新增需求行（来源类型未核实）':changeKind==='REMOVED'?'移除需求行（来源类型未核实）':'需求来源类型未核实';
}

function number(value:unknown):number|undefined{
 if(typeof value!=='number'&&typeof value!=='string'||typeof value==='string'&&!value.trim())return undefined;
 const result=Number(value);return Number.isFinite(result)?result:undefined;
}
function date(value:unknown):number|null|undefined{
 if(value===null)return null;
 if(typeof value!=='string'||!value.trim())return undefined;
 const result=Date.parse(value);return Number.isFinite(result)?result:undefined;
}
function status(value:unknown){return typeof value==='string'&&value.trim()?value:undefined}
function numericChange(a:unknown,b:unknown){const left=number(a),right=number(b);return left!==undefined&&right!==undefined&&left!==right}
function short(value:DeliveryState){return (number(value.unfulfilled_quantity)??0)>0||value.status==='SHORT'}
function deliveryChanged(row:DeliveryChange){
 if(row.kind==='ADDED'||row.kind==='REMOVED')return true;
 const a=row.base,b=row.candidate;if(!a||!b)return false;
 const left=date(a.planned_delivery_at),right=date(b.planned_delivery_at),sa=status(a.status),sb=status(b.status);
 return numericChange(a.planned_quantity,b.planned_quantity)||numericChange(a.unfulfilled_quantity,b.unfulfilled_quantity)
  ||sa!==undefined&&sb!==undefined&&sa!==sb
  ||left!==undefined&&right!==undefined&&left!==right
  ||typeof row.planned_delivery_shift_seconds==='number'&&Number.isFinite(row.planned_delivery_shift_seconds)&&row.planned_delivery_shift_seconds!==0;
}
export function affectedDeliveryCount(rows:DeliveryChange[]){
 // The backend changed flag also covers due_at; count only actual delivery dimensions.
 return new Set(rows.filter(deliveryChanged).map(row=>row.demand_id).filter(id=>typeof id==='string'&&id.length>0)).size;
}

export function DeliverySnapshot({value,uom,timezone}:{value?:DeliveryState|null;uom:string;timezone:string}){
 if(!value)return <>无此需求行</>;
 const planned=number(value.planned_quantity),unfulfilled=number(value.unfulfilled_quantity),at=date(value.planned_delivery_at);
 const label=unit(uom||'单位未提供'),partial=short(value);
 const when=planned===0?'未排定':typeof at==='number'?
  (partial?'已排定部分预计 ':'预计日期 ')+formatSiteTime(value.planned_delivery_at as string,timezone):
  at===null?'尚无已排定日期':'日期未核验';
 return <>{when}<small>计划满足 {planned===undefined?'未提供':qty(planned)} {label} · 欠量 {unfulfilled===undefined?'未提供':qty(unfulfilled)} {label}</small>
  <small>{deliveryStatusLabel(status(value.status))}</small>
  {unfulfilled!==undefined&&unfulfilled>0&&<small>未满足的{qty(unfulfilled)}{label}暂无计划交付日期</small>}
  {partial&&unfulfilled===undefined&&<small>未满足部分日期未核验</small>}</>;
}

export function DeliveryDifference({row,uom}:{row:DeliveryChange;uom:string}){
 if(row.kind==='ADDED')return <>新增需求行</>;
 if(row.kind==='REMOVED')return <>当前方案中已无此需求行</>;
 const a=row.base,b=row.candidate;if(!a||!b)return <>差异未核验</>;
 const left=date(a.planned_delivery_at),right=date(b.planned_delivery_at),parts:string[]=[],label=unit(uom||'单位未提供');
 if(typeof left==='number'&&typeof right==='number'){
  const seconds=(right-left)/1000;
  parts.push(seconds===0?(short(a)||short(b)?'已排定部分日期未变':'预计日期未变'):
   (short(a)||short(b)?'已排定部分':'')+(seconds<0?'预计提前 ':'预计推迟 ')+delayLabel(seconds));
 }else if(typeof left==='number'&&right===null&&(number(b.planned_quantity)===0||b.status==='UNPLANNED'))parts.push('由已排定变为未排定');
 else if(left===null&&typeof right==='number'&&(number(a.planned_quantity)===0||a.status==='UNPLANNED'))parts.push('由未排定变为已排定');
 else if(left===null&&right===null)parts.push('尚无已排定日期');
 else parts.push('预计日期差异未核验');
 if(numericChange(a.planned_quantity,b.planned_quantity))parts.push('计划满足量'+qty(number(a.planned_quantity))+'→'+qty(number(b.planned_quantity))+label);
 if(numericChange(a.unfulfilled_quantity,b.unfulfilled_quantity)){
  const delta=number(b.unfulfilled_quantity)!-number(a.unfulfilled_quantity)!;
  parts.push((delta>0?'新增欠量':'欠量减少')+qty(Math.abs(delta))+label);
 }
 const sa=status(a.status),sb=status(b.status);
 if(sa!==undefined&&sb!==undefined&&sa!==sb)parts.push('状态：'+deliveryStatusLabel(sa)+'→'+deliveryStatusLabel(sb));
 return <>{parts.map((part,index)=><div key={index}>{part}</div>)}</>;
}
