import type {Dict} from '../api/contracts';
import {cockpitSupplyRows,verifyCockpit,type CockpitScope} from './cockpitModel';
import {routingScopeKey} from './routingCapacityModel';
import {fulfillmentGroupId} from './demandFulfillmentModel';
import {cockpitPeriodParams} from './cockpitNavigation';
import {reportRowId} from './reportModel';
import {supplyScopeKey} from './supplyWorkModel';
import {midnightAt,siteDate} from './timeRange';

export type WorkRow={id:string;domain:'purchase'|'manufacturing'|'distribution';title:string;detail:string;quantity:string;uom:string;status:string;start:string|null;end:string|null;phase:string;params:Record<string,string>};
const list=(x:unknown):Dict[]=>Array.isArray(x)?x:[];
const dated=(x:unknown):x is string=>typeof x==='string'&&/(Z|[+-]\d\d:\d\d)$/.test(x)&&Number.isFinite(Date.parse(x));
const pass=(x:Dict|undefined)=>x?.status==='PASS'&&!x.issues?.length&&!x.missing_evidence?.length;
const qty=(x:unknown)=>typeof x==='string'&&x.trim()&&Number.isFinite(Number(x))&&Number(x)>=0||typeof x==='number'&&Number.isFinite(x)&&x>=0;
function foundationPurchaseSource(report:Dict,result:Dict,po:Dict,original:Dict,scope:CockpitScope){
 const proof=po.source_selection,identity=proof?.scope,purchase=proof?.purchase;
 if(!pass(report.documents?.validation)||proof?.schema_revision!=='foundation-purchase-source-proof-v1'||proof.status!=='PRESENT'||!identity||!purchase||
    identity.run_id!==scope.runId||identity.data_version_id!==scope.versionId||identity.profile!==scope.profile||
    identity.input_hash!==result.input_hash||identity.input_hash!==report.identity.input_hash||identity.result_hash!==result.result_hash||
    identity.scenario_id!==result.scenario_id||identity.scenario_id!==report.identity.scenario_id||identity.scenario_revision!==result.scenario_revision||
    !/^[a-f0-9]{64}$/.test(identity.input_hash??'')||!/^[a-f0-9]{64}$/.test(identity.result_hash??'')||
    typeof identity.scenario_id!=='string'||!identity.scenario_id.trim()||!Number.isInteger(identity.scenario_revision)||identity.scenario_revision<1||
    !proof.source_id||proof.source_id!==po.source_id||!proof.capacity_resource||!proof.native_load_id||
    po.id!==scope.runId+':PO:'+po.reference||original.engine_ref!==po.reference||
    list(result.purchase_suggestions).filter(d=>d.id===po.id).length!==1)return false;
 if(['id','reference','item_id','location_id','supplier_id','quantity','uom','start_at','physical_receipt_at','available_at'].some(k=>purchase[k]!==po[k])||
    ![po.start_at,po.physical_receipt_at,po.available_at].every(dated)||po.start_at!==original.order_at||po.physical_receipt_at!==original.receipt_at)return false;
 const required=['canonical_input.json','PlanResult.json','raw/purchaseorder.json','raw/operationplanresource.json','raw/fixed_batch_post.json','raw/fixed_batch_post.xml','raw/operationplanmaterial.json'];
 const sources=list(proof.sources),files=list(report.evidence_files);
 return sources.length===required.length&&required.every(name=>{
  const proofRows=sources.filter(s=>s.artifact===name),actual=files.filter(s=>s.artifact===name);
  return proofRows.length===1&&actual.length===1&&/^[a-f0-9]{64}$/.test(proofRows[0].sha256??'')&&
   typeof proofRows[0].pointer==='string'&&proofRows[0].pointer.startsWith('/')&&proofRows[0].sha256===actual[0].sha256;
 });
}
export function cockpitQueueStatus(report:Dict,context:Dict,scope:CockpitScope,domain:string){
 if(report.known_business_fail)return 'FAIL';
 if(domain==='manufacturing')return pass(report.manufacturing?.validation)?report.manufacturing.status:report.manufacturing?.validation?.status??'NOT_COLLECTED';
 if(domain==='distribution')return scope.profile==='network-distribution-confirmed-supply-v1'?(pass(context.distribution_validation)?'PRESENT':context.distribution_validation?.status??'NOT_COLLECTED'):'UNSUPPORTED';
 if(scope.profile==='multi-receipt-protected-manufacturing-v1'&&context.receipt_protection?.profile===scope.profile)return 'PRESENT';
 return pass(report.inventory?.validation)&&['PRESENT','EMPTY_CHECKED'].includes(report.documents?.status)?'PRESENT':'UNSUPPORTED';
}
export function cockpitPerformanceDrill(data:Dict,scope:CockpitScope,group:Dict,line:Dict):Record<string,string>{
 const row=list(data.demand?.rows).find(r=>fulfillmentGroupId(r)===fulfillmentGroupId(group)&&Date.parse(r.start_at)<=Date.parse(line.due_at)&&Date.parse(line.due_at)<Date.parse(r.end_at));
 if(!row)return {analysis:'schedule',view:'investigate',order_line_id:line.demand_id,run_id:scope.runId,data_version_id:scope.versionId};
 return {...cockpitPeriodParams(scope,data.grain,fulfillmentGroupId(group),row?reportRowId(row):'',false),report_demand_kind:'FIRM_ORDER',report_line_query:line.demand_id};
}
/** Selection and routing only. Quantities, resource assignments and times remain owned by the original projections. */
export function cockpitWorkRows(report:Dict,context:Dict,result:Dict,scope:CockpitScope):WorkRow[]{
 verifyCockpit(report,scope);
 if(report.known_business_fail||[report.manufacturing,report.documents].some(d=>d?.validation?.status==='FAIL'))return [];
 const output:WorkRow[]=[];
 const authority={...report,documents:{...report.documents,documents:report.documents?.run_documents??report.documents?.documents}};
 for(const po of cockpitSupplyRows(authority,context,scope)){
  const remaining=po.remaining_quantity??po.quantity;
  if(qty(remaining)&&Number(remaining)>0)output.push({id:'queue:po:'+po.id,domain:'purchase',title:po.external_ref??po.id,detail:po.item_id+' · '+po.location_id+' · 未收',quantity:String(remaining),uom:po.uom,status:'confirmed',start:dated(po.physical_at)?po.physical_at:null,end:dated(po.available_at)?po.available_at:null,phase:'outstanding',params:po.action.params});
  for(const receipt of list(po.receipts).filter(r=>r.quality_status==='PENDING_QC'||r.quality_status==='FROZEN')){
   if(!receipt.receipt_reference||receipt.po_id!==po.id||!receipt.provenance||!qty(receipt.quantity)||receipt.uom!==po.uom)continue;
   output.push({id:'queue:receipt:'+receipt.receipt_reference,domain:'purchase',title:po.external_ref??po.id,detail:po.item_id+' · 收货 '+receipt.receipt_reference,quantity:String(receipt.quantity),uom:po.uom,status:receipt.quality_status,start:dated(receipt.physical_receipt_at)?receipt.physical_receipt_at:null,end:dated(receipt.release_at)?receipt.release_at:null,phase:'received',params:{...po.action.params,supply_receipt:receipt.receipt_reference}});
  }
 }
 // Proposed supply remains an unexecuted plan suggestion. Reuse the existing
 // material/source certification and exact PlanResult identity, never a name match.
 if(pass(report.inventory?.validation)&&['PRESENT','EMPTY_CHECKED'].includes(report.inventory?.status))for(const po of list(authority.documents?.documents).filter(d=>d.kind==='PO'&&d.origin==='PROPOSED'&&d.status==='proposed')){
  const original=list(result.purchase_suggestions).find(d=>d.id===po.id&&d.supplier_id===po.supplier_id&&d.item_id===po.item_id&&d.location_id===po.location_id&&d.uom===po.uom&&String(d.quantity)===String(po.quantity));
  if(!original||!po.id?.startsWith(scope.runId+':')||!po.source_id||!po.supplier_id||/unknown/i.test(po.supplier_id)||!qty(po.quantity)||!po.source)continue;
  if(scope.profile==='foundation-planning-projection-v1'?!foundationPurchaseSource(report,result,po,original,scope):original.supply_source_id!==po.source_id)continue;
  output.push({id:'queue:proposed-po:'+po.id,domain:'purchase',title:'采购建议 '+po.reference,detail:po.item_id+' · '+po.location_id+' · '+po.supplier_id,quantity:String(po.quantity),uom:po.uom,status:'proposed',start:dated(po.at)?po.at:null,end:dated(po.available_at)?po.available_at:null,phase:'suggestion',params:{analysis:'supply-work',supply_item:po.item_id,supply_location:po.location_id,supply_doc:po.id,supply_attention:'all',supply_scope:supplyScopeKey(scope)}});
 }
 if(pass(report.manufacturing?.validation)&&['PRESENT','EMPTY_CHECKED'].includes(report.manufacturing?.status)){
  const seen=new Set<string>();for(const task of list(report.manufacturing.tasks)){
   if(!task.id?.startsWith(scope.runId+':')||seen.has(task.id)||!task.operation_id||!qty(task.quantity)||!task.sources?.length)continue;seen.add(task.id);
   const params:Record<string,string>=scope.profile==='routing-alternate-capacity-v1'?{analysis:'manufacturing',rc_scope:routingScopeKey(scope),rc_task:task.id,rc_group:task.manufacture_id}:{analysis:'manufacturing',mo_scope:JSON.stringify([scope.planId,scope.versionId,scope.runId,scope.target]),mo_task:task.id};
   output.push({id:'queue:mo:'+task.id,domain:'manufacturing',title:task.operation_id,detail:task.item_id+' · '+(list(task.resource_ids).length?task.resource_ids.join(' / '):'资源身份待核实'),quantity:String(task.quantity),uom:task.uom,status:task.status,start:dated(task.start_at)?task.start_at:null,end:dated(task.end_at)?task.end_at:null,phase:'operation',params});
  }
 }
 const owns=context.enterprise_source?.plan_id===scope.planId&&context.enterprise_source?.data_version_id===scope.versionId&&context.plan_version?.solve_run_id===scope.runId&&context.manufacturing_profile===scope.profile&&(scope.target==='baseline'||context.plan_version.scenario_id===scope.target);
 if(owns&&scope.profile==='network-distribution-confirmed-supply-v1'&&pass(context.distribution_validation)){
  const seen=new Set<string>();for(const d of list(result.distribution_suggestions)){
   if(!d.id?.startsWith(scope.runId+':DO:')||seen.has(d.id)||!d.evidence_ids?.length||!d.lane_id||!d.origin_location_id||!d.destination_location_id||!qty(d.quantity))continue;seen.add(d.id);
   output.push({id:'queue:do:'+d.id,domain:'distribution',title:d.origin_location_id+' → '+d.destination_location_id,detail:d.item_id+' · 调拨 '+d.engine_ref,quantity:String(d.quantity),uom:d.uom,status:d.source_status,start:dated(d.ship_at)?d.ship_at:null,end:dated(d.receipt_at)?d.receipt_at:null,phase:'transfer',params:{analysis:'network-supply',net_do:d.id,net_lane:d.lane_id,net_location:d.destination_location_id,net_tab:'transfers'}});
  }
 }
 return output;
}
export function selectCockpitWork(rows:WorkRow[],domain:string,phase:string,status:string,start:string,end:string){
 const a=Date.parse(start),b=Date.parse(end);if(!Number.isFinite(a)||!Number.isFinite(b)||a>=b)throw Error('请选择有效的驾驶舱期间。');
 return rows.filter(r=>r.domain===domain&&(!status||r.status===status)).filter(r=>{
  if(r.domain==='manufacturing')return !r.start||!r.end||Date.parse(r.start)<b&&Date.parse(r.end)>a;
  const at=phase==='end'?r.end:r.start;
  // Missing dates stay visible as unknown. Frozen receipts have no claimed release date.
  return !at||Date.parse(at)>=a&&Date.parse(at)<b;
 }).sort((x,y)=>{const xat=phase==='end'?x.end:x.start,yat=phase==='end'?y.end:y.start;return !xat?(!yat?x.id.localeCompare(y.id):-1):!yat?1:Date.parse(xat)-Date.parse(yat)||x.id.localeCompare(y.id)});
}
/** Locate the original daily stock owner at this object's chosen business date. */
export function cockpitWorkDrill(row:WorkRow,data:Dict,context:Dict,phase:string):Record<string,string>{
 const at=phase==='end'?row.end:row.start;
 if(row.domain!=='purchase'||!row.params.supply_scope||!at||!dated(context.planning_at))return row.params;
 const day=midnightAt(siteDate(at,data.timezone),data.timezone),start=Math.max(day,Date.parse(context.planning_at));
 return {...row.params,supply_bucket:new Date(start).toISOString()};
}

