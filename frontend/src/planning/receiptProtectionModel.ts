import type {Dict} from '../api/contracts';
import {analysisContextSearch} from './analysisModel';
import {rememberSupplyWork} from './supplyWorkNavigation';

export const receiptProtectionProfile='multi-receipt-protected-manufacturing-v1';
export type ReceiptProtectionScope={planId:string;versionId:string;runId:string;target:string;baselineRunId:string;sourceSha256?:string;snapshotSha256?:string};
export type ReceiptFields={reference:string;newReceiptAt:string;reason:string};
export type ReceiptRequest={data_version_id:string;baseline_run_id:string;run_id:string;target:'baseline';reference:string;new_receipt_at:string;reason:string};
export type ReceiptEditor={fields:ReceiptFields;preview:Dict|null;acknowledged:boolean};
const sha=(value:unknown)=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const dated=(value:unknown):value is string=>typeof value==='string'&&/(Z|[+-]\d\d:\d\d)$/.test(value)&&Number.isFinite(Date.parse(value));
const quantity=(value:unknown)=>typeof value==='string'&&/^(0|[1-9][0-9]{0,11})(\.[0-9]{1,8})?$/.test(value);
export function receiptNumber(value:unknown):number|null{if(typeof value!=='number'&&(typeof value!=='string'||!value.trim()))return null;const n=Number(value);return Number.isFinite(n)?n:null}
/** Display only. The original decimal bytes remain in evidence and requests. */
export function receiptQuantityText(value:unknown){const n=receiptNumber(value);return n===null?'未核验':new Intl.NumberFormat('en-US',{maximumFractionDigits:8}).format(Object.is(n,-0)?0:n)}
/** This model's output endpoint is a manufacturing stage, never proof of
 * complete-garment shipping. Demand type is not inferred from IDs. */
export function receiptStageDemand(contexts:(Dict|undefined)[],action?:string){
 const matched=action==='simulate_outstanding_receipt'||contexts.some(c=>[receiptProtectionProfile,'routing-alternate-capacity-v1'].includes(c?.manufacturing_profile)||c?.receipt_protection?.profile===receiptProtectionProfile);
 return matched?{label:'阶段需求（分类未采集）',description:'本计划核对原料、独立WIP与下游阶段产出。阶段需求满足不代表完整成衣交付或客户实际发货；当前需求分类尚未采集。'}:null;
}
function equal(a:unknown,b:unknown):boolean{if(a===b)return true;if(!a||!b||typeof a!=='object'||typeof b!=='object'||Array.isArray(a)!==Array.isArray(b))return false;const keys=Object.keys(a);return keys.length===Object.keys(b).length&&keys.every(k=>equal((a as Dict)[k],(b as Dict)[k]))}
function verifyIdentity(value:Dict,scope:ReceiptProtectionScope){
 const expected={plan_id:scope.planId,data_version_id:scope.versionId,baseline_run_id:scope.baselineRunId,run_id:scope.runId,target:scope.target,profile:receiptProtectionProfile,...(scope.sourceSha256?{source_sha256:scope.sourceSha256}:{}),...(scope.snapshotSha256?{snapshot_sha256:scope.snapshotSha256}:{})};
 if(!value||Object.entries(expected).some(([key,v])=>value[key]!==v)||!sha(value.source_sha256)||!sha(value.snapshot_sha256))throw Error('采购收货资料与当前计划、版本、运行或来源不一致，请重新读取。');
}
function verifyAuthority(value:Dict){if(value.simulation_only!==true||value.business_permission!=='NOT_CONFIRMED'||value.execution_authorized!==false)throw Error('返回结果未确认隔离模拟边界，已停止操作。')}
export function verifyReceiptProtectionReport(value:Dict,scope:ReceiptProtectionScope){
 verifyIdentity(value?.identity,scope);verifyAuthority(value);
 if(!Array.isArray(value.purchase_orders)||!Array.isArray(value.purchase_receipts)||!Array.isArray(value.manufacturing_orders)||!Array.isArray(value.stock_facts))throw Error('当前收货、工单或库存事实不完整，不能按空记录显示。');
 const ids=new Set<string>();for(const po of value.purchase_orders){if(!po.id||ids.has(po.id)||!po.item_id||!po.location_id||!po.uom||!po.supply_source_id||!po.provenance||!['ordered_quantity','received_quantity','remaining_quantity'].every(key=>quantity(po[key])))throw Error('确认采购身份、余额、单位或来源未完整核对。');ids.add(po.id)}
 const receipts=new Set<string>();for(const row of value.purchase_receipts){const key=JSON.stringify([row.po_id,row.receipt_reference]);if(!ids.has(row.po_id)||!row.receipt_reference||receipts.has(key)||!quantity(row.quantity)||!row.uom||!dated(row.physical_receipt_at)||!['RELEASED','PENDING_QC','FROZEN'].includes(row.quality_status)||!row.provenance)throw Error('收货引用、采购归属、质量状态或来源未完整核对。');receipts.add(key)}
 return value;
}
export function receiptRequest(scope:ReceiptProtectionScope,report:Dict,fields:ReceiptFields):{body:ReceiptRequest|null;error:string}{
 const fail=(error:string)=>({body:null,error});
 if(scope.target!=='baseline'||scope.runId!==scope.baselineRunId)return fail('请切回当前原始基线，再准备另一项独立采购余量假设。');
 try{verifyReceiptProtectionReport(report,scope)}catch(error){return fail(error instanceof Error?error.message:'当前采购资料未核实。')}
 const po=report.purchase_orders.find((row:Dict)=>row.id===fields.reference),reason=fields.reason.trim();
 if(!po||po.source_status!=='confirmed'||(receiptNumber(po.remaining_quantity)??0)<=0)return fail('请选择有明确来源、尚有未收余额的确认采购。');
 if(!dated(fields.newReceiptAt))return fail('请填写含时区的有效预计物理到货时间。');
 if(!dated(po.expected_receipt_at)||Date.parse(fields.newReceiptAt)<=Date.parse(po.expected_receipt_at))return fail('本次只模拟未收余额延期，新时点必须晚于原预计物理到货。');
 if(!reason||reason.length>1000)return fail('请填写1–1000字的延期假设理由。');
 return {body:{data_version_id:scope.versionId,baseline_run_id:scope.baselineRunId,run_id:scope.runId,target:'baseline',reference:po.id,new_receipt_at:fields.newReceiptAt,reason},error:''};
}
export function updateReceiptEditor(editor:ReceiptEditor,patch:Partial<ReceiptFields>):ReceiptEditor{if(Object.entries(patch).every(([key,value])=>editor.fields[key as keyof ReceiptFields]===value))return editor;return {fields:{...editor.fields,...patch},preview:null,acknowledged:false}}
function verifyChanges(value:Dict,request:ReceiptRequest){
 if(value?.action!=='simulate_outstanding_receipt'||value.conditions?.reference!==request.reference||Object.keys(value.conditions??{}).length!==2||!dated(value.conditions.new_receipt_at)||Date.parse(value.conditions.new_receipt_at)!==Date.parse(request.new_receipt_at)||value.reason!==request.reason||!value.before||!value.after||value.engine_executed!==false)throw Error('预览不是本次指定采购余量和日期的假设，请重新核对。');
 verifyAuthority(value);
 const {before,after}=value,mutable=new Set(['expected_receipt_at','available_at','physical_receipt_at','planning_fact_kind']);
 if(before.id!==request.reference||after.id!==request.reference||before.planning_fact_kind!=='CONFIRMED_ENTERPRISE_SNAPSHOT'||after.planning_fact_kind!=='PLANNING_ASSUMPTION'||!dated(after.physical_receipt_at)||Date.parse(after.physical_receipt_at)!==Date.parse(request.new_receipt_at)||!dated(after.available_at)||receiptNumber(after.qc_wait_seconds)===null||Date.parse(after.available_at)-Date.parse(after.physical_receipt_at)!==Number(after.qc_wait_seconds)*1000||Object.keys(before).some(key=>!mutable.has(key)&&!equal(before[key],after[key]))||Object.keys(after).some(key=>!mutable.has(key)&&!(key in before)))throw Error('候选修改了已收事实、采购身份或QC规则，已停止创建。');
}
export function verifyReceiptPreview(value:Dict,scope:ReceiptProtectionScope,request:ReceiptRequest){
 verifyIdentity(value?.identity,scope);
 if(value.simulation_only!==true||!sha(value.fingerprint)||value.engine_executed!==false||value.calculation_submitted!==false)throw Error('返回结果不是只读预览，已停止创建草案。');
 verifyChanges(value.changes,request);return value;
}
export function verifyReceiptDraft(value:Dict,scope:ReceiptProtectionScope,request:ReceiptRequest,preview:Dict){
 verifyIdentity(value?.identity,scope);const draft=value?.draft;
 if(value.simulation_only!==true||!equal(value.identity,preview.identity)||value.fingerprint!==preview.fingerprint||value.engine_executed!==false||value.calculation_submitted===true||value.run?.state!=='NOT_RUN'||value.run?.solve_run_id||!draft?.id||draft.state!=='DRAFT'||draft.run_id!==null)throw Error('保存结果不是已确认预览对应的NOT_RUN独立草案，请刷新计划核对。');
 verifyChanges(draft.changes,request);if(!equal(draft.changes.before,preview.changes.before)||!equal(draft.changes.after,preview.changes.after))throw Error('草案的原值或候选条件与已确认预览不同。');return draft.id as string;
}
export function receiptReviewSearch(previous:URLSearchParams,scope:ReceiptProtectionScope){const next=rememberSupplyWork(previous,scope);next.set('analysis','review');if(scope.target!=='baseline')next.set('review_drafts',scope.target);return next}
export function receiptTargetSearch(previous:URLSearchParams,target:string){const next=analysisContextSearch(previous,target);next.set('analysis','supply-work');return next}
/** The enterprise reference is the only bridge to run-local manufacturing IDs.
 * Same item, quantity or dates never establish that identity. */
export function receiptObservedManufacturing(report:Dict,reference:string){
 const matches=(report.observed_manufacturing_orders??[]).filter((row:Dict)=>row.engine_ref===reference);
 if(matches.length!==1)return null;const order=matches[0];
 const operations=(report.observed_operations??[]).filter((row:Dict)=>row.manufacture_id===order.id),ids=new Set(operations.map((row:Dict)=>row.id));
 const loads=(report.observed_resource_loads??[]).filter((row:Dict)=>ids.has(row.operation_id));
 return {order,operations,loads};
}
export const receiptQualityLabel:Record<string,string>={RELEASED:'已放行',PENDING_QC:'待QC',FROZEN:'质量冻结'};
export function receiptEvidenceText(status:unknown){return ({PASS:'核验通过',FAIL:'硬约束违规',PRESENT:'记录已取得',EMPTY_CHECKED:'已查无记录',NOT_CHECKED:'必要核验未完成',NOT_COLLECTED:'必要证据未采集',UNSUPPORTED:'尚未认证此分析',UNREADABLE:'证据不可读取',INVALID_EVIDENCE:'来源证据无效'} as Record<string,string>)[String(status)]??'状态未知'}
