import type {Delivery,Dict,NativeEvidence,PlanView} from '../api/contracts';
import {supports} from './capabilityModel';
import {demandEndpointKind} from './presentationModel';

export type SupplyArgs={input:Dict;view:PlanView;planId:string;versionId:string;orderId:string};
export type SupplyNode={id:string;kind:'PURCHASE'|'STOCK'|'OPERATION'|'DEMAND';label:string;itemId:string;quantity:string|null;uom:string;status:string;startAt:string|null;endAt:string|null;physicalAt?:string|null;evidence:NativeEvidence[];taskId?:string;reference?:string;inventory?:{physical:string|null;frozen:string|null;other:string|null;net:string|null};materials?:NativeEvidence[];resources?:NativeEvidence[]};
export type SupplyLink={from:string;to:string;source:NativeEvidence};
export type PurchaseFact={reference:string;itemId:string;quantity:string|null;uom:string;physicalAt:string|null;availableAt:string|null;status:string};
export type SupplyModel={status:'PRESENT_WITH_LIMITS'|'INSUFFICIENT_EVIDENCE'|'NOT_CHECKED'|'UNSUPPORTED';nodes:SupplyNode[];links:SupplyLink[];notices:string[];purchaseFacts:PurchaseFact[];delivery?:Delivery;unfulfilledAt:null;runId:string;orderId:string;planningAt:string|null;dueAt:string|null;stageEndpoint:boolean};
export function validDate(value:unknown):string|null{return typeof value==='string'&&/T.*(?:Z|[+-]\d\d:\d\d)$/.test(value)&&Number.isFinite(Date.parse(value))?value:null;}
const quantity=(v:unknown)=>typeof v==='string'||typeof v==='number'?(/^\d+(?:\.\d+)?$/.test(String(v))?String(v):null):null;
function decimal(value:unknown){const q=quantity(value);if(q===null)return null;const [w,f='']=q.split('.');return {n:BigInt(w+f),scale:f.length};}
function netQuantity(a:unknown,b:unknown,c:unknown):string|null{
 const parts=[a,b,c].map(decimal);if(parts.some(p=>p===null))return null;
 const scale=Math.max(...parts.map(p=>p!.scale)),[x,y,z]=parts.map(p=>p!.n*10n**BigInt(scale-p!.scale));
 const n=x-y-z,sign=n<0?'-':'',digits=(n<0?-n:n).toString().padStart(scale+1,'0');
 return sign+(scale?(digits.slice(0,-scale)+'.'+digits.slice(-scale)).replace(/\.?0+$/,''):digits);
}
function plan(e:NativeEvidence):Dict{try{const v=typeof e.record.plan==='string'?JSON.parse(e.record.plan):e.record.plan;return v&&typeof v==='object'&&!Array.isArray(v)?v:{};}catch{return {};}}

export function buildOrderSupplyChain(a:SupplyArgs):SupplyModel{
 const {input,view,orderId,planId,versionId}=a,p=view.order_evidence,run=view.run?.solve_run_id??'';
 const empty:SupplyModel={status:'NOT_CHECKED',nodes:[],links:[],notices:[],purchaseFacts:[],unfulfilledAt:null,runId:run,orderId,planningAt:validDate(input.planning_at),dueAt:null,stageEndpoint:demandEndpointKind(view.context)==='STAGE_OUTPUT'};
 const scope=view.context?.enterprise_source;
 if(!p||!view.result||!run||p.solve_run_id!==run||p.source_result_hash!==view.result.result_hash||p.scenario_id!==view.result.scenario_id||scope?.plan_id!==planId||scope?.data_version_id!==versionId){empty.notices.push('计划、数据版本或运行证据不一致，未绘制时间链。');return empty;}
 if(!supports(input.rules?.manufacturing_profile,'analyses','order_pegging')){empty.status='UNSUPPORTED';empty.notices.push('该专业模型尚未完成本视图验证，请继续使用原有关联业务明细。');return empty;}
 const order=p.orders.find(o=>o.demand_id===orderId);
 if(!order||order.delivery.demand_id!==orderId){empty.notices.push('当前运行没有所选订单行的证据。');return empty;}
 const actual=view.result.deliveries.find(d=>d.demand_id===orderId);
 if(!actual||['requested_quantity','planned_quantity','unfulfilled_quantity','due_at','planned_delivery_at','status','uom'].some(k=>(actual as Dict)[k]!==(order.delivery as Dict)[k])){empty.notices.push('订单证据与当前计算结果不一致，未绘制时间链。');return empty;}
 const model={...empty,status:'PRESENT_WITH_LIMITS' as SupplyModel['status'],delivery:order.delivery,dueAt:validDate(order.delivery.due_at)};
 const notes=new Set<string>();
 const valid=(e:NativeEvidence)=>e.source?.solve_run_id===run&&e.source?.scenario_id===p.scenario_id&&/^[a-f0-9]{64}$/i.test(e.source?.sha256??'');
 const qualified=(entries:NativeEvidence[])=>(entries??[]).filter(e=>{
  const peg=plan(e).pegging;
  const matches=valid(e)&&peg&&typeof peg==='object'&&!Array.isArray(peg)&&Number(peg[orderId])>0;
  if(!matches)notes.add('部分记录缺少当前订单行、运行或文件摘要依据，未用于连线。');return matches;
 });
 const ops=qualified(order.operations),parents=qualified(order.manufacturing),purchases=qualified(order.purchases),deliveries=qualified(order.deliveries??[]);
 const nodes=new Map<string,SupplyNode>(),refs=new Map<string,string>(),ambiguous=new Set<string>();
 function register(ref:string,id:string){if(refs.has(ref)&&refs.get(ref)!==id){refs.delete(ref);ambiguous.add(ref);}else if(!ambiguous.has(ref))refs.set(ref,id);}
 const inputPOs=(input.existing_orders??[]).filter((r:Dict)=>r.type==='PO');
 for(const po of inputPOs){
  const window=(view.interpretation?.confirmed_supply_windows??[]).find((w:Dict)=>w.id===po.id&&w.item_id===po.item_id&&w.supplier_id===po.supplier_id&&w.location_id===po.location_id&&Number(w.quantity)===Number(po.remaining_quantity));
  model.purchaseFacts.push({reference:po.id,itemId:po.item_id,quantity:quantity(po.remaining_quantity),uom:po.uom,status:po.source_status,
   physicalAt:validDate(window?.physical_receipt_at),availableAt:validDate(window?.available_at)});
 }
 for(const e of ops){
  const parent=parents.find(m=>m.engine_ref===e.owner_ref),recipe=(input.production_recipes??[]).find((r:Dict)=>r.id===parent?.record.operation),itemId=parent?.record.item??recipe?.output_item_id??'';
  const item=(input.items??[]).find((i:Dict)=>i.id===itemId),id=e.id;
  nodes.set(id,{id,kind:'OPERATION',label:e.task?.operation_id??e.record.operation??'制造工序',itemId,quantity:quantity(e.record.quantity),uom:item?.base_uom??e.item_uom??'',status:e.record.status??'未提供',startAt:validDate(e.task?.start_at??e.times?.startdate),endAt:validDate(e.task?.end_at??e.times?.enddate),evidence:[e],taskId:e.task?.id,reference:e.engine_ref,
   materials:order.materials.filter(m=>valid(m)&&String(m.record.operationplan)===e.engine_ref),resources:order.resources.filter(r=>valid(r)&&String(r.record.operationplan)===e.engine_ref)});
  register(e.engine_ref,id);
 }
 // A parent is an aggregation, not an additional material-producing task.
 // Collapse it only when this validated single-operation profile has one child.
 for(const parent of parents){const children=ops.filter(o=>o.owner_ref===parent.engine_ref);if(children.length===1)register(parent.engine_ref,children[0].id);}
 for(const e of purchases){
  const f=model.purchaseFacts.find(f=>f.reference===e.engine_ref&&f.itemId===e.record.item),id=e.id;
  nodes.set(id,{id,kind:'PURCHASE',label:e.record.status==='confirmed'?'已确认采购':'采购建议 · 未认证',itemId:e.record.item??'',quantity:quantity(e.record.quantity),uom:f?.uom??e.item_uom??'',status:e.record.status??'未提供',startAt:f?.physicalAt??validDate(e.times?.enddate),endAt:f?.availableAt??null,physicalAt:f?.physicalAt??validDate(e.times?.enddate),reference:e.engine_ref,evidence:[e]});register(e.engine_ref,id);
 }
 const demandId='demand:'+orderId;
 nodes.set(demandId,{id:demandId,kind:'DEMAND',label:model.stageEndpoint?'阶段需求满足':'需求满足',itemId:(input.demand_lines??[]).find((d:Dict)=>d.id===orderId)?.item_id??'',quantity:quantity(order.delivery.planned_quantity),uom:order.delivery.uom,status:order.delivery.status,startAt:validDate(order.delivery.planned_delivery_at),endAt:validDate(order.delivery.planned_delivery_at),evidence:order.demand_records.filter(valid)});
 for(const e of deliveries)register(e.engine_ref,demandId);
 function resolve(ref:string):string|undefined{
  if(ambiguous.has(ref))return undefined;
  if(refs.has(ref))return refs.get(ref);
  const buffers=(p!.buffer_records??[]).filter(b=>valid(b)&&b.record.name===ref);
  if(p!.buffer_evidence_status!=='PRESENT'||buffers.length!==1)return undefined;
  const b=buffers[0],stocks=(input.inventory_positions??[]).filter((s:Dict)=>s.item_id===b.record.item&&s.location_id===b.record.location);
  if(stocks.length!==1)return undefined;
  const s=stocks[0],net=netQuantity(s.on_hand,s.quality_blocked,s.other_nonnettable),id='stock:'+s.id;
  if(net!==null&&Number(net)!==Number(b.record.onhand)){notes.add('原生缓冲区期初与业务净库存不一致，未连接该库存。');return undefined;}
  nodes.set(id,{id,kind:'STOCK',label:(input.items??[]).find((i:Dict)=>i.id===s.item_id)?.kind==='SEMI'?'已有独立半成品':'已有净库存',itemId:s.item_id,quantity:net,uom:s.uom,status:net===null?'净可用未核验':'仅净可用参与规划',startAt:validDate(s.as_of),endAt:validDate(s.as_of),reference:ref,evidence:[b],inventory:{physical:quantity(s.on_hand),frozen:quantity(s.quality_blocked),other:quantity(s.other_nonnettable),net}});
  register(ref,id);return id;
 }
 const links=new Map<string,SupplyLink>();
 for(const e of [...parents,...ops,...purchases,...deliveries]){
  const to=resolve(e.engine_ref),upstream=plan(e).upstream_opplans;
  if(!Array.isArray(upstream))continue;
  for(const row of upstream){if(!Array.isArray(row)||typeof row[0]!=='string')continue;const from=resolve(row[0]);
   if(!from||!to){notes.add('存在尚不能解析的原生上游引用，未补画供需连线。');continue;}
   if(from===to)continue;
   links.set(from+'|'+to,{from,to,source:e});
  }
 }
 model.nodes=[...nodes.values()].sort((x,y)=>(x.kind==='DEMAND'?1:0)-(y.kind==='DEMAND'?1:0)||(Date.parse(x.startAt??'')||0)-(Date.parse(y.startAt??'')||0)||x.id.localeCompare(y.id));
 model.links=[...links.values()];if(!model.links.length)model.status='INSUFFICIENT_EVIDENCE';
 if(p.missing_tables.length)notes.add('部分原始表未采集：'+p.missing_tables.join('、')+'，当前关联不完整。');
 model.notices=[...notes];return model;
}
