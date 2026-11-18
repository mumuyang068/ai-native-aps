import {describe,expect,it} from 'vitest';
import type {Dict} from '../api/contracts';
import {scenarioPreview} from '../scenarios/scenarioPreview';
import {receiptNumber,receiptObservedManufacturing,receiptQuantityText,receiptRequest,receiptReviewSearch,receiptStageDemand,receiptTargetSearch,updateReceiptEditor,verifyReceiptDraft,verifyReceiptPreview,verifyReceiptProtectionReport,type ReceiptProtectionScope} from './receiptProtectionModel';

const scope:ReceiptProtectionScope={planId:'P',versionId:'V',baselineRunId:'R',runId:'R',target:'baseline',sourceSha256:'a'.repeat(64),snapshotSha256:'b'.repeat(64)};
const identity={plan_id:'P',data_version_id:'V',baseline_run_id:'R',run_id:'R',target:'baseline',profile:'multi-receipt-protected-manufacturing-v1',source_sha256:scope.sourceSha256,snapshot_sha256:scope.snapshotSha256};
const provenance={source_system:'FROZEN_TEST',source_record_id:'ROW-1',source_version:'V1'};
const po={type:'PO',id:'PO-1',external_ref:'ENTERPRISE-PO-1',item_id:'RAW',location_id:'FACTORY',supplier_id:'SUP',supply_source_id:'SOURCE',ordered_quantity:'40',received_quantity:'20',cancelled_quantity:'0',remaining_quantity:'20',uom:'m',source_status:'confirmed',freeze_policy:'LOCKED',expected_receipt_at:'2026-11-03T09:00:00+08:00',available_at:'2026-11-04T09:00:00+08:00',ordered_at:'2026-10-25T09:00:00+08:00',provenance};
const receipt={po_id:'PO-1',receipt_reference:'RCP-1',quantity:'12',uom:'m',physical_receipt_at:'2026-11-01T09:00:00+08:00',quality_status:'RELEASED',release_at:'2026-11-01T18:00:00+08:00',provenance};
const report={identity,purchase_orders:[po,{...po,id:'PO-2',external_ref:'ENTERPRISE-PO-2',received_quantity:'0',remaining_quantity:'40'}],purchase_receipts:[receipt],manufacturing_orders:[],stock_facts:[],simulation_only:true,business_permission:'NOT_CONFIRMED',execution_authorized:false};
const fields={reference:'PO-1',newReceiptAt:'2026-11-05T09:00:00+08:00',reason:' 模拟未收余额延期 '};
const request={data_version_id:'V',baseline_run_id:'R',run_id:'R',target:'baseline' as const,reference:'PO-1',new_receipt_at:fields.newReceiptAt,reason:fields.reason.trim()};
const before={...po,qc_wait_seconds:86400,physical_receipt_at:po.expected_receipt_at,planning_fact_kind:'CONFIRMED_ENTERPRISE_SNAPSHOT'};
const after={...before,expected_receipt_at:fields.newReceiptAt,physical_receipt_at:fields.newReceiptAt,available_at:'2026-11-06T09:00:00+08:00',planning_fact_kind:'PLANNING_ASSUMPTION'};
const changes={action:'simulate_outstanding_receipt',intent:'未收采购余额到货条件试算',conditions:{reference:request.reference,new_receipt_at:request.new_receipt_at},reason:request.reason,before,after,confirmation:null,simulation_only:true,business_permission:'NOT_CONFIRMED',execution_authorized:false,engine_executed:false};
const preview={identity,fingerprint:'c'.repeat(64),changes,simulation_only:true,engine_executed:false,calculation_submitted:false};
const clone=<T,>(value:T):T=>structuredClone(value);
describe('multi-receipt future-balance UI contract',()=>{
 it('uses one exact confirmed PO balance; no received or enterprise facts enter the request',()=>{
  expect(verifyReceiptProtectionReport(report,scope)).toBe(report);
  expect(receiptRequest(scope,report,fields)).toEqual({body:request,error:''});
  expect(Object.keys(request)).not.toContain('received_quantity');
 });
 it.each(['plan_id','data_version_id','run_id','target','baseline_run_id','source_sha256','snapshot_sha256','profile'])('rejects mismatched %s identity',key=>{expect(()=>verifyReceiptProtectionReport({...report,identity:{...identity,[key]:'OTHER'}},scope)).toThrow()});
 it('does not turn missing receipt records or unknown quantities into an empty success',()=>{
  for(const changed of [{...report,purchase_receipts:null},{...report,stock_facts:null},{...report,purchase_orders:[{...po,received_quantity:null}]},{...report,purchase_orders:[{...po,supply_source_id:''}]}])expect(()=>verifyReceiptProtectionReport(changed,scope)).toThrow();
 });
 it('requires stable PO and receipt identity and valid original sources',()=>{
  for(const r of [{...receipt,po_id:'OTHER'},{...receipt,provenance:null},{...receipt,quality_status:'UNKNOWN'},{...receipt,physical_receipt_at:'2026-11-01T09:00:00'}])expect(()=>verifyReceiptProtectionReport({...report,purchase_receipts:[r]},scope)).toThrow();
  expect(()=>verifyReceiptProtectionReport({...report,purchase_receipts:[receipt,receipt]},scope)).toThrow();
  expect(()=>verifyReceiptProtectionReport({...report,purchase_orders:[po,po]},scope)).toThrow();
 });
 it('retains explicit zero and negative native balances while preserving missing values',()=>{
  expect(receiptNumber('0')).toBe(0);expect(receiptNumber('-8')).toBe(-8);
  for(const v of [null,undefined,'','  ',false,{}])expect(receiptNumber(v)).toBeNull();
 });
 it('renders native scientific zero and decimal tails without mutating source values',()=>{
  const values=['0E-8','20.00000000','-8.00000000','0.00000001','-0E-8'];
  expect(values.map(receiptQuantityText)).toEqual(['0','20','-8','0.00000001','0']);
  expect(receiptQuantityText(null)).toBe('未核验');expect(receiptQuantityText('')).toBe('未核验');expect(receiptQuantityText('NaN')).toBe('未核验');
  expect(values).toEqual(['0E-8','20.00000000','-8.00000000','0.00000001','-0E-8']);
 });
 it('the explicit profile or typed action proves a stage endpoint but never a demand kind by ID',()=>{
  expect(receiptStageDemand([{manufacturing_profile:identity.profile}])?.label).toBe('阶段需求（分类未采集）');
  expect(receiptStageDemand([],changes.action)?.description).toContain('不代表完整成衣交付');
  expect(receiptStageDemand([{receipt_protection:{profile:identity.profile}}])?.label).not.toContain('客户订单');
  expect(receiptStageDemand([{order_summary:{orders:[{id:'D16-NET-FIRM-1'}]}}])).toBeNull();
 });
 it.each([{target:'OLD-DRAFT'},{runId:'OLD-RUN'},{baselineRunId:'OLD-BASE'}])('historical/candidate scope cannot write %j',patch=>{expect(receiptRequest({...scope,...patch},report,fields).body).toBeNull()});
 it.each([{reference:'OTHER'},{reason:' '},{newReceiptAt:'2026-11-02T09:00:00+08:00'},{newReceiptAt:po.expected_receipt_at},{newReceiptAt:'2026-11-05T09:00:00'}])('invalid or unauthorized candidate cannot preview %j',patch=>{expect(receiptRequest(scope,report,{...fields,...patch}).body).toBeNull()});
 it('closed/fully received documents have no future balance action',()=>{
  for(const changed of [{...po,source_status:'closed'},{...po,remaining_quantity:'0'}])expect(receiptRequest(scope,{...report,purchase_orders:[changed]},fields).body).toBeNull();
 });
 it('real field edits invalidate preview and acknowledgement; a no-op preserves them',()=>{
  const editor={fields,preview,acknowledged:true};expect(updateReceiptEditor(editor,{reference:'PO-1'})).toBe(editor);
  expect(updateReceiptEditor(editor,{reference:'PO-2'})).toEqual({fields:{...fields,reference:'PO-2'},preview:null,acknowledged:false});
 });
 it('accepts same instant normalized to factory timezone; does not replace QC timing',()=>{
  const normalized=clone(preview);normalized.changes.conditions.new_receipt_at='2026-11-05T01:00:00Z';
  expect(verifyReceiptPreview(normalized,scope,request)).toBe(normalized);
  const incorrect=clone(preview);incorrect.changes.after.available_at=fields.newReceiptAt;expect(()=>verifyReceiptPreview(incorrect,scope,request)).toThrow();
 });
 it('rejects silent received quantities, source, status, original total, or protected business changes',()=>{
  for(const patch of [{received_quantity:'0'},{ordered_quantity:'20'},{remaining_quantity:'40'},{supplier_id:'OTHER'},{source_status:'proposed'},{qc_wait_seconds:0},{unexpected_fact:'x'}]){
   const bad=clone(preview) as Dict;Object.assign(bad.changes.after,patch);expect(()=>verifyReceiptPreview(bad,scope,request)).toThrow();
  }
 });
 it('read-only preview has no computed result or automatic authorization',()=>{
  expect(verifyReceiptPreview(preview,scope,request)).toBe(preview);
  for(const patch of [{engine_executed:true},{calculation_submitted:true},{fingerprint:'bad'},{simulation_only:false}])expect(()=>verifyReceiptPreview({...preview,...patch},scope,request)).toThrow();
  expect(()=>verifyReceiptPreview({...preview,changes:{...changes,execution_authorized:true}},scope,request)).toThrow();
 });
 it('accepts only a new NOT_RUN draft bound to the exact accepted preview',()=>{
  const value={identity,fingerprint:preview.fingerprint,simulation_only:true,engine_executed:false,calculation_submitted:false,draft:{id:'NEW',state:'DRAFT',run_id:null,changes},run:{state:'NOT_RUN'}};
  expect(verifyReceiptDraft(value,scope,request,preview)).toBe('NEW');
  for(const v of [{...value,run:{state:'SUCCEEDED',solve_run_id:'OLD'}},{...value,draft:{...value.draft,run_id:'OLD'}},{...value,fingerprint:'d'.repeat(64)},{...value,draft:{...value.draft,changes:{...changes,after:{...after,remaining_quantity:'40'}}}}])expect(()=>verifyReceiptDraft(v,scope,request,preview)).toThrow();
 });
 it('keeps stable PO return context while discarding old native event and selecting only this review candidate',()=>{
  const search=new URLSearchParams('target=baseline&analysis=supply-work&supply_po=PO-1&supply_event=OLD&supply_scope=OLD&run_id=R');
  const next=receiptTargetSearch(search,'NEW');expect(next.get('supply_po')).toBe('PO-1');expect(next.get('analysis')).toBe('supply-work');expect(next.has('supply_event')).toBe(false);expect(next.has('run_id')).toBe(false);
  const review=receiptReviewSearch(next,{...scope,target:'NEW',runId:'NEW-R'});expect(review.get('review_drafts')).toBe('NEW');expect(review.get('supply_return')).toBe(next.toString());
 });
 it('scenario compare describes received-fact protection and does not fall through to fictitious urgent demand',()=>{
  const rows=scenarioPreview(changes);expect(rows.find(r=>r[0]==='未收余额')?.[1]).toBe('20 m');expect(rows.some(r=>r[0]==='急单编号')).toBe(false);expect(rows.some(r=>r[1].includes('已收与质量事实保持不变'))).toBe(true);
 });
 it('only stable enterprise reference and exact operation IDs connect manufacturing and resource observations',()=>{
  const observed={observed_manufacturing_orders:[{id:'RUN:MO:1',engine_ref:'MO-1',item_id:'FG'}],observed_operations:[{id:'RUN:OP:1',manufacture_id:'RUN:MO:1'}],observed_resource_loads:[{operation_id:'RUN:OP:1',resource_id:'M1'},{operation_id:'OTHER',resource_id:'P2'}]};
  expect(receiptObservedManufacturing(observed,'MO-1')?.loads).toEqual([{operation_id:'RUN:OP:1',resource_id:'M1'}]);
  expect(receiptObservedManufacturing(observed,'FG')).toBeNull();
  expect(receiptObservedManufacturing({...observed,observed_manufacturing_orders:[...observed.observed_manufacturing_orders,...observed.observed_manufacturing_orders]},'MO-1')).toBeNull();
 });
});
