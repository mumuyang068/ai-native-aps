import type {ReactNode} from 'react';
import type {Dict} from '../api/contracts';
import {Button,Definition,Select} from '../components/Controls';
import {DataTable} from '../components/DataTable';
import {formatSiteTime} from './timeRange';

type Props={report:Dict;timezone:string;item:string;location:string;onSelect:(item:string,location:string)=>void;onInventory:(item:string,location:string)=>void;onOperation:(id:string)=>void;onDemand:(id:string)=>void};
const value=(v:unknown)=>v==null?'未知':String(v);
const quality:Record<string,string>={RELEASED:'已放行',PENDING_QC:'待检',FROZEN:'冻结'};
export function JointMaterialInvestigation(p:Props){
 const rows:Dict[]=p.report.objects??[],object=rows.find(r=>r.item_id===p.item&&r.location_id===p.location)??rows[0];
 if(!object)return null;
 const time=(v:string|null)=>v?formatSiteTime(v,p.timezone):'未知';
 const table=(label:string,children:ReactNode)=><div className="professional-policy-table" tabIndex={0} aria-label={label+'，可横向滚动'}>{children}</div>;
 return <section className="professional-policy-panel" aria-label="同运行物料供应调查">
  <div className="professional-policy-heading"><h3>物料供应调查</h3><Select label="供应调查物料地点" value={object.item_id+'@'+object.location_id} options={rows.map(r=>({value:r.item_id+'@'+r.location_id,label:r.item_id+' · '+r.location_id+' · '+r.uom}))} onChange={v=>{const r=rows.find(r=>r.item_id+'@'+r.location_id===v);if(r)p.onSelect(r.item_id,r.location_id)}}/></div>
  <Definition rows={[["期间最低 / 期末可用",value(object.minimum_available)+' / '+value(object.end_available)+' '+object.uom],["实收质量",'放行 '+object.receipt_quantities.RELEASED+' · 待检 '+object.receipt_quantities.PENDING_QC+' · 冻结 '+object.receipt_quantities.FROZEN+' '+object.uom]]}/>
  <Button onClick={()=>p.onInventory(object.item_id,object.location_id)}>打开该物料地点的库存事件</Button>
  <h4>确认采购：实收与未收余额</h4>{table('确认采购余额',<DataTable<Dict> rows={object.confirmed_balances} rowId={r=>r.id} columns={[{id:'id',title:'采购单',value:r=>r.id},{id:'q',title:'订购 / 已收 / 未收',value:r=>r.ordered_quantity+' / '+r.received_quantity+' / '+r.remaining_quantity+' '+object.uom},{id:'physical',title:'未收余额物理到货',value:r=>time(r.expected_receipt_at)},{id:'qc',title:'QC后可用',value:r=>time(r.available_at)}]}/>)}
  {object.receipts.length>0&&<><h4>实际收货快照</h4>{table('收货及质量',<DataTable<Dict> rows={object.receipts} rowId={r=>r.receipt_reference} columns={[{id:'ref',title:'收货记录',value:r=>r.receipt_reference},{id:'quantity',title:'数量',value:r=>r.quantity+' '+r.uom},{id:'quality',title:'质量状态',value:r=>quality[r.quality_status]??r.quality_status},{id:'release',title:'放行时点',value:r=>(r.quality_status==='PENDING_QC'&&r.release_at?'预计 ':'')+time(r.release_at)}]}/>)}</>}
  <h4>跨地点供应</h4>{table('调拨在途区间',<DataTable<Dict> rows={object.distribution??[]} rowId={r=>r.reference} columns={[{id:'route',title:'起点 → 终点',value:r=>r.origin+' → '+r.destination},{id:'quantity',title:'计划调拨',value:r=>r.quantity+' '+object.uom},{id:'start',title:'发运扣减',value:r=>time(r.startdate)},{id:'end',title:'到达可用',value:r=>time(r.enddate)}]} empty={object.distribution===null?'调拨证据缺失':'本次未形成该物料的调拨建议'}/>)}
  <details><summary>其他地点、合法路线与供应条款</summary>{table('其他地点库存',<DataTable<Dict> rows={object.other_location_stock} rowId={r=>r.id} columns={[{id:'loc',title:'地点',value:r=>r.location_id},{id:'physical',title:'物理 / 可用',value:r=>value(r.physical)+' / '+value(r.net_available)+' '+r.uom},{id:'state',title:'认知状态',value:r=>r.knowledge}]}/>)}{object.lanes.map((r:Dict)=><p key={r.id}>{r.origin_location_id} → {r.destination_location_id} · {Number(r.lead_time_seconds)/3600}小时 · 批量 {r.minimum_transfer_quantity} / {r.transfer_multiple} {r.uom} · 运输成本未知</p>)}</details>
  <details><summary>本次原生工序与关联需求</summary><p>{object.relation_limit}</p>{table('实际工序领料',<DataTable<Dict> rows={object.consumptions} rowId={r=>String(r.id??r.operationplan)+'|'+r.flowdate} columns={[{id:'op',title:'原生工序',value:r=><Button className="quiet" onClick={()=>p.onOperation(r.operation_id)}>{r.operation_code??r.operationplan}</Button>},{id:'q',title:'带符号领用量',value:r=>r.quantity+' '+object.uom},{id:'at',title:'原生领用时点',value:r=>time(r.flowdate)}]}/>)}<div className="professional-policy-actions">{object.demands.map((d:Dict)=><Button key={d.demand_id} className="quiet" onClick={()=>p.onDemand(d.demand_id)}>{d.demand_id}</Button>)}</div></details>
 </section>;
}
