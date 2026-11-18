import type {Dict} from '../api/contracts';
import {Button,Definition} from '../components/Controls';
import {fulfillmentAmount} from './demandFulfillmentModel';
import {formatSiteTime} from './timeRange';

type Props={mode:string;row:Dict;data:Dict;onOpen:(mode:string,fields?:Record<string,string>)=>void;onLocate:(id:string)=>void;onDemand:(id:string)=>void};
export function PeriodObjectDetails({mode,row,data,onOpen,onLocate,onDemand}:Props){
 const time=(v:unknown)=>typeof v==='string'?formatSiteTime(v,data.timezone):'未知 / 未收集';
 const amount=(v:unknown)=>fulfillmentAmount(v,row.uom);
 if(mode==='manufacturing')return <>
  <p>父制造单与工序任务分别核算；数量不是实际产出。</p>
  {(data.manufacturing.orders??[]).filter((m:Dict)=>row.manufacture_ids?.includes(m.id)).map((m:Dict)=><article key={m.id}>
   <strong>{m.enterprise_reference??m.native_reference}</strong>
   <Definition rows={[["产品 / 工艺",m.item_id+' / '+m.operation_id],["来源状态",m.status],["计划数量",fulfillmentAmount(m.quantity,m.uom)],["计划开始",time(m.start_at)],["计划结束",time(m.end_at)],["实际分配资源",m.resource_ids?.join('、')||'未收集'],["保护范围",m.protection_reason??'建议任务；本页仅调查']]}/>
   {m.operation_task_ids.map((id:string)=><Button key={id} onClick={()=>onLocate(id)}>定位工序 {id.split(':').at(-1)}</Button>)}
  </article>)}
 </>;
 if(mode==='constraints'||mode==='problems')return <>
  <Definition rows={[["原生类型",row.type],["对象",row.owner],["原生需求行",row.demand_id??'运行级记录'],["开始",time(row.start_at)],["结束",time(row.end_at)]]}/>
  <p>{row.description}</p>{mode==='problems'&&<p>本记录没有认证的订单级Problem归属。</p>}
  {row.demand_id&&<Button onClick={()=>onDemand(row.demand_id)}>调查需求 {row.demand_id}</Button>}
  {row.resource_id&&<Button onClick={()=>onOpen('resources',{analysis_resource:row.resource_id})}>查看实际资源与日历</Button>}
  {row.item_id&&<Button onClick={()=>onOpen('inventory',{analysis_item:row.item_id,analysis_location:row.location_id})}>查看物料事件</Button>}
  <p>原生观察不代表唯一瓶颈或独立欠量贡献。</p>
 </>;
 if(mode==='transit')return <>
  <Definition rows={[["物料",row.item_id],["路线",row.origin_location_id+' → '+row.destination_location_id],["期初计划在途",amount(row.in_transit_opening)],["期间发运",amount(row.dispatched_quantity)],["期间到达",amount(row.arrived_quantity)],["期末计划在途",amount(row.in_transit_ending)]]}/>
  {(data.documents.run_documents??[]).filter((d:Dict)=>d.kind==='DO'&&row.references?.includes(String(d.reference))).map((d:Dict)=><article key={d.id}><strong>DO {d.reference} · {d.status}</strong><p>{fulfillmentAmount(d.quantity,d.uom)} · {time(d.start_at)} → {time(d.at)}</p></article>)}
  <Button onClick={()=>onOpen('inventory',{analysis_item:row.item_id,analysis_location:row.origin_location_id,analysis_start:row.start_at,analysis_end:row.end_at})}>查看源地扣减</Button>
  <Button onClick={()=>onOpen('inventory',{analysis_item:row.item_id,analysis_location:row.destination_location_id,analysis_start:row.start_at,analysis_end:row.end_at})}>查看目的地到达</Button>
  <Button onClick={()=>onOpen('stock-replenishment',{supply_item:row.item_id,supply_location:row.origin_location_id})}>调查原供应条件与候选</Button>
 </>;
 if(mode==='inventory')return <>
  <Definition rows={[["物料 / 地点",row.item_id+' · '+row.location_id],["期初可用",amount(row.opening)],["期末可用",amount(row.ending)],["期初冻结",amount(row.quality_blocked_opening)],["已收待检余额",amount(row.receipt_qc_waiting_end)],["预计到货后待QC",amount(row.qc_waiting_end)],["收支核对",row.reconciliation?.status??'NOT_CHECKED']]}/>
  <details open><summary>期间分类收支</summary><Definition rows={[...Object.entries(row.inflows??{}).map(([k,v]):[string,string]=>['流入 · '+({PURCHASE_AVAILABLE:'采购可用',RECEIPT_QC_RELEASE:'实收预计QC放行',DISTRIBUTION_RECEIPT:'调拨到达',MANUFACTURE_OUTPUT:'制造产出',OTHER:'其他'} as Dict)[k],amount(v)]),...Object.entries(row.outflows??{}).map(([k,v]):[string,string]=>['流出 · '+({DISTRIBUTION_DISPATCH:'调拨发运',MANUFACTURE_CONSUMPTION:'制造耗用',DEMAND_CONSUMPTION:'需求消耗',OTHER:'其他'} as Dict)[k],amount(v)])]}/></details>
  {row.soft_targets?.map((p:Dict)=><p key={p.id}>软最低 {amount(p.minimum)} · 目标 {amount(p.maximum)} · {p.semantics==='ACTIVE_RESEARCH_SOFT_TARGET'?'本候选研究政策':'来源声明'} · {p.validation?.status}</p>)}
  <Button onClick={()=>onOpen('inventory',{analysis_item:row.item_id,analysis_location:row.location_id,analysis_start:row.start_at,analysis_end:row.end_at})}>下钻本期间原生事件</Button>
  <Button onClick={()=>onOpen('stock-replenishment',{supply_item:row.item_id,supply_location:row.location_id})}>原补货策略与供应候选</Button>
 </>;
 return null;
}
