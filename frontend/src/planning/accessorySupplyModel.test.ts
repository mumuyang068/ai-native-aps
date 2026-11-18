import {describe,expect,it} from 'vitest';
import {accessoryReturnSearch,accessorySearch,accessoryPrice,verifyAccessoryDraft,verifyAccessoryPreview,verifyAccessorySupply,type AccessoryOption} from './accessorySupplyModel';
import {analysisContextSearch} from './analysisModel';
const scope={planId:'P',versionId:'V',runId:'R',baselineRunId:'R',target:'baseline'};
const identity={plan_id:'P',data_version_id:'V',baseline_run_id:'R',run_id:'R',target:'baseline',profile:'enterprise-accessory-supply-v1'};
const option:AccessoryOption={source_id:'SRC-LABEL',supplier_id:'SYN-LABEL',item_id:'LABEL',location_id:'FACTORY',uom:'个',minimum_order_quantity:'20',order_multiple:'10',lead_time_seconds:86400,qc_wait_seconds:86400,total_capacity_quantity:'40',earliest_order_at:'2026-11-01T08:00:00+08:00',latest_order_at:'2026-11-08T08:00:00+08:00',enabled:false,fact_kind:'SYNTHETIC_RESEARCH_ASSUMPTION',permission:'SIMULATION_ONLY',provenance:{source_record_id:'SYN-1'},price_status:'NOT_PROVIDED'};
const report={identity,option,simulation_only:true};
const changes={action:'enable_accessory_supply',source_id:option.source_id,reason:'核验辅料补给',before:{enabled:false},after:{enabled:true}};
const preview={identity,original:option,proposed:{...option,enabled:true},changes,simulation_only:true,engine_executed:false,calculation_submitted:false};

describe('single sourced accessory decision guards',()=>{
 it('requires exact plan version run baseline and target without borrowing historical records',()=>{
  expect(verifyAccessorySupply(report,scope)).toBe(report);
  for(const key of ['plan_id','data_version_id','baseline_run_id','run_id','target'])expect(()=>verifyAccessorySupply({...report,identity:{...identity,[key]:'foreign'}},scope)).toThrow();
  expect(()=>verifyAccessorySupply({...report,option:null},scope)).toThrow();
  expect(()=>verifyAccessorySupply({...report,identity:{...identity,profile:'enterprise-forecast-fg-v1'}},scope)).toThrow();
  expect(()=>verifyAccessorySupply({...report,option:{...option,permission:'EXECUTION_APPROVED'}},scope)).toThrow();
 });
 it('retains unknown price instead of replacing it with zero',()=>{
  expect(accessoryPrice(option)).toBe('未提供');
  expect(accessoryPrice({...option,price_status:'NOT_PROVIDED',unit_price:'0',currency:'CNY'})).toBe('未提供');
 });
 it('previews only enabling the same source with unchanged frozen terms and no calculation',()=>{
  expect(verifyAccessoryPreview(preview,scope,option,'核验辅料补给')).toBe(preview);
  for(const field of ['source_id','supplier_id','item_id','location_id','uom','minimum_order_quantity','order_multiple','lead_time_seconds','qc_wait_seconds','total_capacity_quantity','earliest_order_at','latest_order_at','provenance'])expect(()=>verifyAccessoryPreview({...preview,proposed:{...preview.proposed,[field]:'changed'}},scope,option,'核验辅料补给')).toThrow();
  expect(()=>verifyAccessoryPreview({...preview,engine_executed:true},scope,option,'核验辅料补给')).toThrow();
  expect(()=>verifyAccessoryPreview({...preview,changes:{...changes,reason:'另一个理由'}},scope,option,'核验辅料补给')).toThrow();
 });
 it('accepts only a fresh matching NOT_RUN draft and rejects protocol-only success',()=>{
  const response={identity,simulation_only:true,calculation_submitted:false,run:{state:'NOT_RUN'},draft:{id:'DRAFT-NEW',state:'DRAFT',run_id:null,changes}};
  expect(verifyAccessoryDraft(response,scope,option,'核验辅料补给')).toBe('DRAFT-NEW');
  for(const mutation of [{run:{state:'SUCCEEDED'}},{calculation_submitted:true},{draft:{...response.draft,run_id:'HISTORICAL'}},{draft:{...response.draft,changes:{...changes,source_id:'OTHER'}}},{identity:{...identity,data_version_id:'OTHER'}}])expect(()=>verifyAccessoryDraft({...response,...mutation},scope,option,'核验辅料补给')).toThrow();
 });
 it('rejects fields outside a single enable action including invented quantity or dates',()=>{
  expect(()=>verifyAccessoryPreview({...preview,changes:{...changes,quantity:'80'}},scope,option,'核验辅料补给')).toThrow();
  expect(()=>verifyAccessoryPreview({...preview,changes:{...changes,earliest_order_at:'2026-11-02T00:00:00+08:00'}},scope,option,'核验辅料补给')).toThrow();
 });
 it('returns to exact originating object and filter within the same run',()=>{
  const before=new URLSearchParams('target=baseline&analysis=inventory-policy&policy_item=LABEL&policy_row=B1&policy_event=E1&policy_selection_scope=scope');
  expect(accessoryReturnSearch(accessorySearch(before,scope),scope).toString()).toBe(before.toString());
 });
 it('clears old run-specific selections on candidate switch but keeps material filters',()=>{
  const before=new URLSearchParams('target=baseline&analysis=inventory&analysis_item=LABEL&analysis_event=E1&analysis_task=T1&policy_row=B1');
  const next=accessoryReturnSearch(accessorySearch(before,scope),{...scope,target:'DRAFT-NEW',runId:'NEW'});
  expect(next.get('target')).toBe('DRAFT-NEW');expect(next.get('analysis_item')).toBe('LABEL');
  for(const key of ['analysis_event','analysis_task','policy_row'])expect(next.has(key)).toBe(false);
 });
 it('retains original filters through draft creation and run-level material drilldown',()=>{
  const before=new URLSearchParams('target=baseline&analysis=inventory-policy&policy_item=FG&policy_bucket=W2&policy_event=E');
  const opened=accessorySearch(before,scope),candidate=analysisContextSearch(opened,'DRAFT-NEW');
  candidate.set('analysis','inventory');candidate.set('analysis_item','LABEL');
  const reopened=accessorySearch(candidate,{...scope,target:'DRAFT-NEW',runId:'NEW'});
  const result=accessoryReturnSearch(reopened,{...scope,target:'DRAFT-NEW',runId:'NEW'});
  expect(result.get('analysis')).toBe('inventory-policy');expect(result.get('policy_item')).toBe('FG');expect(result.get('policy_bucket')).toBe('W2');expect(result.has('policy_event')).toBe(false);
 });
});
