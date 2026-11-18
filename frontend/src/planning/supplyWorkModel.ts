import type {Dict} from '../api/contracts';
import type {ReportScope} from './reportModel';
import {verifyReport} from './reportModel';
import {verifyAnalysis} from './compositeModel';
import {supports,capabilityReason} from './capabilityModel';

const ready=new Set(['PRESENT','EMPTY_CHECKED']);
export const supplyTagLabels:Record<string,string>={negative:'已核对负库存 · FAIL',qc:'采购待QC / 开账待检',frozen:'存在期初冻结',flow:'有物料流转',short:'同物料需求欠量',evidence:'证据未完成'};
export type SupplyWorkRow={id:string;itemId:string;locationId:string;name:string;uom:string;startAt:string;endAt:string;opening:number|null;inflow:number|null;outflow:number|null;ending:number|null;physicalOpening:number|null;frozenOpening:number|null;openingInspectionPending:number|null;otherNonnettable:number|null;qcWaiting:number|null;demandShort:number|null;tags:string[];status:string;validation:string;series?:Dict;raw:Dict};
/** Empty or unavailable data must never become a real zero. */
export function finiteSupplyQuantity(value:unknown):number|null{
 if(typeof value!=='number'&&typeof value!=='string')return null;
 if(typeof value==='string'&&!value.trim())return null;
 const number=Number(value);return Number.isFinite(number)?number:null;
}
export function materialSources(input:Dict,itemId:string,locationId:string):Dict[]{return (input.supply_sources??[]).filter((s:Dict)=>s.item_id===itemId&&s.location_id===locationId)}
/** A failed audit may retain a fully sourced violating event while marking the
 * whole series incomplete. Retain that fact without certifying the whole ledger.
 */
function sourcedNegativeEvent(event:Dict,series:Dict){
 const balance=finiteSupplyQuantity(event.balance),nativeBalance=finiteSupplyQuantity(event.native_onhand),quantity=finiteSupplyQuantity(event.quantity),record=event.native_record;
 if(balance===null||balance>=0||nativeBalance===null||nativeBalance>=0||quantity===null||!record||record.item!==series.item_id||record.location!==series.location_id||!event.reference||String(record.operationplan)!==String(event.reference)||finiteSupplyQuantity(record.onhand)!==nativeBalance||finiteSupplyQuantity(record.quantity)!==quantity)return false;
 const sources=Array.isArray(event.sources)?event.sources:[],validSha=(value:unknown)=>typeof value==='string'&&/^[a-f0-9]{64}$/i.test(value);
 return sources.some((s:Dict)=>s.artifact==='raw/fixed_batch_post.json'&&validSha(s.sha256)&&/^\/buffers\/\d+\/events\/\d+$/.test(s.pointer??''))&&sources.some((s:Dict)=>s.artifact==='raw/operationplanmaterial.json'&&validSha(s.sha256)&&/^\/\d+$/.test(s.pointer??''));
}
/** An old time-filtered response cannot stand in for a complete material work list. */
export function verifySupplyWorkbench(report:Dict,analysis:Dict,scope:ReportScope){
 verifyReport(report,scope);verifyAnalysis(analysis,scope);
 const start=Date.parse(analysis.planning_at),end=Date.parse(analysis.horizon_end);
 if(!Number.isFinite(start)||!Number.isFinite(end)||start>=end||Date.parse(analysis.window?.start_at)!==start||Date.parse(analysis.window?.end_at)!==end||Date.parse(report.range?.start_at)!==start||Date.parse(report.range?.end_at)!==end||report.identity.profile!==analysis.identity.profile)throw Error('物料工作区未取得同一模型的完整规划期，已停止展示。');
 return {report,analysis};
}

/** Report buckets and native series are two views of the same authenticated run.
 * No BOM traversal, stock allocation or procurement root-cause inference occurs here.
 */
export function supplyWorkRows(report:Dict,analysis:Dict,input:Dict):SupplyWorkRow[]{
 if(!ready.has(report.inventory?.status))return [];
 return (report.inventory.rows??[]).map((r:Dict)=>{
  const series=(analysis.inventory_analysis?.series??[]).find((s:Dict)=>s.item_id===r.item_id&&s.location_id===r.location_id&&s.uom===r.uom);
  const status=series?.event_status??'NOT_COLLECTED',available=!!series&&ready.has(analysis.inventory_analysis?.status)&&ready.has(status);
  const number=(key:string)=>available?finiteSupplyQuantity(r[key]):null;
  const opening=number('opening'),inflow=number('inflow'),outflow=number('outflow'),ending=number('ending'),qcWaiting=number('qc_waiting_end');
  const physicalOpening=available?finiteSupplyQuantity(series?.physical_opening):null,frozenOpening=available?finiteSupplyQuantity(series?.quality_blocked):null;
  const openingInspectionPending=available?finiteSupplyQuantity(series?.inspection_pending_quantity):null,otherNonnettable=available?finiteSupplyQuantity(series?.other_nonnettable):null;
  const sameDemand=(report.demand?.rows??[]).filter((d:Dict)=>d.item_id===r.item_id&&d.location_id===r.location_id&&d.uom===r.uom&&d.start_at===r.start_at&&d.end_at===r.end_at);
  const shortQuantities=sameDemand.map((d:Dict)=>finiteSupplyQuantity(d.overdue_unfulfilled));
  const demandShort=ready.has(report.demand?.status)&&shortQuantities.length&&shortQuantities.every((v:number|null)=>v!==null)?shortQuantities.reduce((sum:number,v:number)=>sum+v,0):null;
  const events=(series?.events??[]).filter((e:Dict)=>Date.parse(e.at)>=Date.parse(r.start_at)&&Date.parse(e.at)<Date.parse(r.end_at));
  const negative=available&&(ending!==null&&ending<0||events.some((e:Dict)=>{const balance=finiteSupplyQuantity(e.balance);return balance!==null&&balance<0}))||!!series&&analysis.inventory_analysis?.status==='PRESENT'&&analysis.inventory_analysis?.validation?.status==='FAIL'&&events.some((e:Dict)=>sourcedNegativeEvent(e,series));
  const validation=negative||report.inventory.validation?.status==='FAIL'||analysis.inventory_analysis?.validation?.status==='FAIL'?'FAIL':!available||series?.complete===false?'NOT_CHECKED':analysis.inventory_analysis?.validation?.status??'NOT_CHECKED';
  const tags=[...(negative?['negative']:[]),...(qcWaiting!==null&&qcWaiting>0||openingInspectionPending!==null&&openingInspectionPending>0?['qc']:[]),...(frozenOpening!==null&&frozenOpening>0?['frozen']:[]),...(inflow!==null&&inflow>0||outflow!==null&&outflow>0?['flow']:[]),...(demandShort!==null&&demandShort>0?['short']:[]),...(!available||series?.complete===false||validation==='NOT_CHECKED'?['evidence']:[])];
  const item=(input.items??[]).find((i:Dict)=>i.id===r.item_id);
  return {id:JSON.stringify([r.item_id,r.location_id,r.start_at,r.end_at]),itemId:r.item_id,locationId:r.location_id,name:item?.name??r.item_id,uom:r.uom,startAt:r.start_at,endAt:r.end_at,opening,inflow,outflow,ending,physicalOpening,frozenOpening,openingInspectionPending,otherNonnettable,qcWaiting,demandShort,tags,status,validation,series,raw:r};
 });
}
export function filterSupplyRows(rows:SupplyWorkRow[],query:string,attention:string,period=''){
 const term=query.trim().toLocaleLowerCase();
 return rows.filter(row=>(!term||[row.itemId,row.name,row.locationId].some(v=>String(v??'').toLocaleLowerCase().includes(term)))&&(!period||row.startAt===period)&&(attention==='all'||attention==='attention'?attention==='all'||row.tags.length>0:row.tags.includes(attention)));
}
export function supplyScopeKey(scope:ReportScope){return JSON.stringify([scope.planId,scope.versionId,scope.runId,scope.target])}
export function supplySelection(search:URLSearchParams,scope:ReportScope){return search.get('supply_scope')===supplyScopeKey(scope)?{documentId:search.get('supply_doc')??'',eventId:search.get('supply_event')??''}:{documentId:'',eventId:''}}
/** Preserve the originating work view, but never a previous run's event or document. */
export function supplyContextSearch(previous:URLSearchParams,scope:ReportScope){
 const p=new URLSearchParams(previous),changedTarget=(previous.get('target')??'baseline')!==scope.target,changedRun=previous.has('supply_scope')&&previous.get('supply_scope')!==supplyScopeKey(scope);p.set('target',scope.target);p.set('analysis','supply-work');
 if(changedTarget||changedRun)p.delete('run_id');
 if(p.get('supply_scope')!==supplyScopeKey(scope)){p.delete('supply_doc');p.delete('supply_event')}
 p.set('supply_scope',supplyScopeKey(scope));return p;
}
/** This is an adapter for the existing confirmed-PO draft dialog, not a new action. */
export function confirmedDocumentRows(documents:Dict[]):Dict[]{return documents.filter(d=>d.kind==='PO'&&d.origin==='EXISTING'&&d.status==='confirmed'&&d.reference&&d.source_id&&d.supplier_id&&(finiteSupplyQuantity(d.quantity)??0)>0&&Number.isFinite(Date.parse(d.at))&&Number.isFinite(Date.parse(d.available_at))).map(d=>({id:d.reference,item_id:d.item_id,location_id:d.location_id,quantity:d.quantity,uom:d.uom,supplier_id:d.supplier_id,supply_source_id:d.source_id,physical_receipt_at:d.at,available_at:d.available_at}))}
export function supplyActionReason(scope:ReportScope,baselineRunId:string,profile:string|undefined){
 if(scope.target!=='baseline')return '当前为隔离方案。请切回原始基线，再创建另一项独立候选。';
 if(scope.runId!==baselineRunId)return '当前运行不是已确认的基线运行，不能从历史结果创建候选。';
 return supports(profile,'actions','purchase_delay')?'':capabilityReason(profile,'actions','purchase_delay');
}
