import type {Dict} from '../api/contracts';
import {Badge} from '../components/Controls';
import {DataTable,type TableColumn} from '../components/DataTable';
import {distributionView} from './presentationModel';
import {formatSiteTime} from './timeRange';
import {qty,unit} from './planModel';
import {deliveryStatusLabel} from './orderModel';
import './distribution-plan.css';

export function DistributionPlan({input,result,report,timezone}:{input:Dict;result:Dict;report?:Dict;timezone:string}){
 const data=distributionView(input,result,report),amount=(q:unknown,u:string)=>qty(String(q))+' '+unit(u),time=(v:string)=>formatSiteTime(v,timezone);
 const stocks:TableColumn<Dict>[]=[{id:'location',title:'地点',value:r=>r.location_id},{id:'item',title:'物料',value:r=>r.item_id},
  {id:'opening',title:'期初可用',value:r=>amount(r.on_hand,r.uom)},{id:'ending',title:'本方案期末',value:r=>r.ending===null?'尚未核实':amount(r.ending,r.uom)},
  {id:'asof',title:'库存时点',value:r=>time(r.as_of)}];
 const transfers:TableColumn<Dict>[]=[{id:'ref',title:'建议配送',value:r=>r.engine_ref},{id:'from',title:'起运地点',value:r=>r.origin_location_id??'来源未核实'},
  {id:'to',title:'目的地点',value:r=>r.destination_location_id},{id:'quantity',title:'建议数量',value:r=>amount(r.quantity,r.uom)},
  {id:'ship',title:'计划发运',value:r=>time(r.ship_at)},{id:'receipt',title:'预计到货',value:r=>time(r.receipt_at)},
  {id:'status',title:'业务状态',value:r=>r.source_status==='proposed'?'配送建议 · 未发运':'状态需核对'}];
 const orders:TableColumn<Dict>[]=[{id:'id',title:'需求行',value:r=>r.demand_id},{id:'qty',title:'需求',value:r=>amount(r.requested_quantity,r.uom)},
  {id:'planned',title:'计划满足',value:r=>amount(r.planned_quantity,r.uom)},{id:'short',title:'未满足',value:r=>amount(r.unfulfilled_quantity,r.uom)},
  {id:'due',title:'要求时间',value:r=>time(r.due_at)},{id:'date',title:'计划满足日期',value:r=>r.planned_delivery_at?time(r.planned_delivery_at):'未排定，无完成日期'},
  {id:'state',title:'需求状态',value:r=>deliveryStatusLabel(r.status)}];
 const ledger=(report?.summary?.locations??[]).flatMap((l:Dict)=>(l.events??[]).map((e:Dict,i:number)=>({...e,id:l.location_id+':'+i,location_id:l.location_id,uom:l.uom})));
 const columns:TableColumn<Dict>[]=[{id:'location',title:'地点',value:r=>r.location_id},{id:'at',title:'时间',value:r=>time(r.at)},
  {id:'ref',title:'来源',value:r=>r.kind==='OPENING_SNAPSHOT'?'期初快照':r.order_type==='DO'?'配送 '+r.reference:'需求满足 '+r.reference},
  {id:'qty',title:'数量变化',value:r=>amount(r.quantity,r.uom)},{id:'balance',title:'可用余额',value:r=>amount(r.balance,r.uom)}];
 return <section className="distribution-plan" aria-label="配送与地点库存">
  <h2>配送与地点库存</h2><Badge kind={report?.status==='PASS'?'good':report?.status==='FAIL'?'bad':'risk'}>{report?.status==='PASS'?'本次地点库存与运输核验通过':report?.status==='FAIL'?'本方案存在配送或库存违规':'必要的配送或库存证据不足'}</Badge>
  <p>以下为模拟配送建议，未实际发运；预计到货不代表已经收货。</p><p>路径 {input.distribution_lanes?.[0]?.id??'尚未核实'} · 运输提前期 {input.distribution_lanes?.[0]?.lead_time_seconds==null?'尚未核实':qty(input.distribution_lanes[0].lead_time_seconds/3600)+' 小时（连续自然时间）'}</p>
  <h3>地点库存</h3><DataTable rows={data.locations} columns={stocks} rowId={r=>r.id}/>
  <h3>配送建议</h3><DataTable rows={data.suggestions} columns={transfers} rowId={r=>r.id} empty="本方案没有配送建议；异地库存仍在原地点，不是目的地现货。"/>
  <h3>目的地需求</h3><DataTable rows={data.deliveries} columns={orders} rowId={r=>r.demand_id}/>
  <details><summary>查看逐地点物料时间账</summary><p>期初余额单独计入；发运扣减起运库存，到货后才增加目的地可用量。</p><DataTable rows={ledger} columns={columns} rowId={r=>r.id}/><h3>本方案在途变化</h3><DataTable<Dict> rows={report?.summary?.in_transit_timeline??[]} columns={[{id:'at',title:'时间',value:r=>time(r.at)},{id:'stock',title:'两地可用量合计',value:r=>amount(r.available_in_locations,report?.summary?.uom)},{id:'transit',title:'计划在途（不属于两地现货）',value:r=>amount(r.planned_in_transit,report?.summary?.uom)},{id:'used',title:'累计需求满足',value:r=>amount(r.demand_consumed,report?.summary?.uom)}]} rowId={r=>r.at}/></details>
 </section>;
}
