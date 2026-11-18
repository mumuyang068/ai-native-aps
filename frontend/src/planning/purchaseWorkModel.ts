import type {Dict} from '../api/contracts';
import type {ReportScope} from './reportModel';
import {finiteSupplyQuantity,supplyContextSearch,supplyScopeKey} from './supplyWorkModel';
import type {ExportField} from './reportExport';
export type PurchaseDocument={id:string;reference:string;itemId:string;itemName:string;locationId:string;supplierId:string;supplierName:string;sourceId:string;uom:string;origin:string;status:string;quantity:number|null;physicalAt:string;availableAt:string;received:null;remaining:null;pending:null;frozen:null;value:number|null;currency:string;priceSource:Dict|null;raw:Dict};
export type PurchaseGroup={id:string;supplierId:string;itemId:string;itemName:string;locationId:string;uom:string;origin:string;status:string;startAt:string;endAt:string;periodState:'IN_RANGE'|'UNKNOWN'|'OUTSIDE';quantity:number|null;unknownQuantityCount:number;money:Record<string,number>;unknownCostCount:number;documents:PurchaseDocument[]};
const ready=new Set(['PRESENT','EMPTY_CHECKED']);
const date=(v:unknown):v is string=>typeof v==='string'&&/(Z|[+-]\d\d:\d\d)$/.test(v)&&Number.isFinite(Date.parse(v));
const nonnegative=(v:unknown)=>{const n=finiteSupplyQuantity(v);return n!==null&&n>=0?n:null};
/** Prices need the exact source row, unit, currency, provenance and validity. */
function pricedSource(doc:Dict,input:Dict):Dict|null{
 const sources=(input.supply_sources??[]).filter((s:Dict)=>s.id===doc.source_id&&s.item_id===doc.item_id&&s.location_id===doc.location_id&&s.supplier_id===doc.supplier_id);
 const facts=(input.foundation_projection?.source_tables?.supply_sources??[]).filter((s:Dict)=>s.id===doc.source_id&&s.item_id===doc.item_id&&s.location_id===doc.location_id&&s.supplier_id===doc.supplier_id);
 if(sources.length!==1||facts.length!==1)return null;
 const source=sources[0],fact=facts[0],price=nonnegative(fact.unit_price),at=doc.physical_receipt_at??doc.at;
 if(source.provenance?.source_id!==fact.source_id||source.provenance?.source_sha256!==fact.source_sha256)return null;
 if(price===null||nonnegative(source.unit_price)!==price||typeof fact.currency!=='string'||!fact.currency.trim()||source.currency!==fact.currency||!doc.uom||fact.uom!==doc.uom||!fact.source_id||!/^[a-f0-9]{64}$/i.test(fact.source_sha256??'')||!date(at)||!date(fact.effective_from)||!date(fact.effective_to)||Date.parse(at)<Date.parse(fact.effective_from)||Date.parse(at)>=Date.parse(fact.effective_to))return null;
 if(doc.start_at&&(!date(doc.start_at)||Date.parse(doc.start_at)<Date.parse(fact.effective_from)||Date.parse(doc.start_at)>=Date.parse(fact.effective_to)))return null;
 return fact;
}
/** The workbench verifies report identities first. Generic planned documents do
 * not certify execution; authenticated receipts retain ReceiptProtectionPanel. */
export function purchaseDocuments(report:Dict,input:Dict):PurchaseDocument[]{
 if(!ready.has(report.documents?.status))return [];
 return (report.documents.documents??[]).filter((d:Dict)=>d.kind==='PO').map((raw:Dict)=>{
  const priceSource=pricedSource(raw,input),quantity=nonnegative(raw.quantity),unitPrice=priceSource?nonnegative(priceSource.unit_price):null;
  const value=quantity!==null&&unitPrice!==null&&Number.isFinite(quantity*unitPrice)?quantity*unitPrice:null;
  return {id:raw.id,reference:raw.reference??raw.id,itemId:raw.item_id,itemName:(input.items??[]).find((i:Dict)=>i.id===raw.item_id)?.name??raw.item_id,locationId:raw.location_id??'',supplierId:raw.supplier_id??'',supplierName:(input.suppliers??[]).find((s:Dict)=>s.id===raw.supplier_id)?.name??raw.supplier_id??'供应商未知',sourceId:raw.source_id??'',uom:raw.uom??'',origin:raw.origin??'',status:raw.status??'',quantity,physicalAt:date(raw.physical_receipt_at??raw.at)?raw.physical_receipt_at??raw.at:'',availableAt:date(raw.available_at)?raw.available_at:'',received:null,remaining:null,pending:null,frozen:null,value,currency:priceSource?.currency??'',priceSource,raw};
 });
}
export function purchasePeriods(periods:Dict[]){return [...new Map(periods.filter(p=>date(p.start_at)&&date(p.end_at)&&Date.parse(p.start_at)<Date.parse(p.end_at)).map(p=>[JSON.stringify([p.start_at,p.end_at]),{start_at:p.start_at,end_at:p.end_at}])).values()].sort((a,b)=>Date.parse(a.start_at)-Date.parse(b.start_at))}
export function purchaseGroups(rows:PurchaseDocument[],periods:Dict[],basis:string):PurchaseGroup[]{
 const buckets=purchasePeriods(periods),groups=new Map<string,PurchaseGroup>();
 for(const row of rows){
  const at=basis==='available'?row.availableAt:row.physicalAt,bucket=buckets.find(p=>date(at)&&Date.parse(at)>=Date.parse(p.start_at)&&Date.parse(at)<Date.parse(p.end_at));
  const startAt=bucket?.start_at??'',endAt=bucket?.end_at??'',periodState=bucket?'IN_RANGE':'OUTSIDE';
  const state=date(at)?periodState:'UNKNOWN';
  const id=JSON.stringify([row.supplierId,row.itemId,row.locationId,row.uom,row.origin,row.status,startAt,endAt,state]);
  let group=groups.get(id);if(!group){group={id,supplierId:row.supplierId,itemId:row.itemId,itemName:row.itemName,locationId:row.locationId,uom:row.uom,origin:row.origin,status:row.status,startAt,endAt,periodState:state,quantity:0,unknownQuantityCount:0,money:{},unknownCostCount:0,documents:[]};groups.set(id,group)}
  group.documents.push(row);
  if(row.quantity===null){group.quantity=null;group.unknownQuantityCount++}else if(group.quantity!==null)group.quantity+=row.quantity;
  if(row.value===null)group.unknownCostCount++;else group.money[row.currency]=(group.money[row.currency]??0)+row.value;
 }
 for(const group of groups.values())if(group.unknownCostCount)group.money={};
 return [...groups.values()];
}
export function filterPurchases(rows:PurchaseDocument[],filter:Dict):PurchaseDocument[]{
 const query=String(filter.query??'').trim().toLocaleLowerCase();
 return rows.filter(r=>(!filter.supplier||r.supplierId===filter.supplier)&&(!filter.item||r.itemId===filter.item)&&(!filter.location||r.locationId===filter.location)&&(!filter.status||r.status===filter.status)&&(!filter.origin||r.origin===filter.origin)&&(!query||[r.reference,r.itemId,r.itemName,r.supplierId,r.supplierName,r.sourceId,r.locationId].some(v=>v.toLocaleLowerCase().includes(query))));
}
export function selectPurchase(rows:PurchaseDocument[],search:URLSearchParams,scope:ReportScope):PurchaseDocument|undefined{
 if(search.get('supply_scope')!==supplyScopeKey(scope))return undefined;
 const matches=rows.filter(r=>r.id===search.get('purchase_doc'));return matches.length===1?matches[0]:undefined;
}
export function purchaseMaterialContext(previous:URLSearchParams,scope:ReportScope,row:PurchaseDocument,buckets:Dict[]){
 const p=supplyContextSearch(previous,scope);
 for(const key of ['supply_query','supply_period','supply_bucket','supply_event','supply_doc_period'])p.delete(key);
 p.set('supply_view','material');p.set('supply_attention','all');p.set('supply_item',row.itemId);p.set('supply_location',row.locationId);p.set('supply_doc',row.id);
 const matches=buckets.filter(r=>r.item_id===row.itemId&&r.location_id===row.locationId&&r.uom===row.uom&&date(row.physicalAt)&&Date.parse(row.physicalAt)>=Date.parse(r.start_at)&&Date.parse(row.physicalAt)<Date.parse(r.end_at));
 if(matches.length===1)p.set('supply_bucket',matches[0].start_at);else p.set('supply_doc_period','UNRESOLVED');return p;
}
export const purchaseExportFields:ExportField[]=[['id','采购ID','当前运行采购文档完整身份'],['reference','采购引用','来源原生引用'],['itemId','物料','精确物料ID'],['locationId','地点','精确地点ID'],['supplierId','供应商','来源声明供应商ID'],['sourceId','供应来源','精确来源ID'],['origin','性质','EXISTING已有单据或PROPOSED规划建议'],['status','状态','来源单据状态'],['quantity','单据全量','未知为空，不等于实收或分配量'],['uom','单位','来源原始单位'],['physicalAt','计划物理到货','计划日期，不是实际收货'],['availableAt','计划QC后可用','计划日期，不是实际放行'],['received','实际已收','没有认证收货事实时未知为空'],['remaining','实际未收','没有认证收货事实时未知为空'],['pending','实际待检','未知为空，不使用计划待QC替代'],['frozen','实收冻结','未知为空，不使用库存期初冻结替代'],['value','模型采购价值','匹配单价币种单位来源及有效期间，排除税运持有成本'],['currency','币种','保留币种，不跨币种相加'],['priceSource','价格出处','匹配的完整Foundation来源表行'],['raw','原始单据与来源','当前报告原始采购及来源证据']].map(([key,label,meaning])=>({key,label,meaning}));
