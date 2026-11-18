import {describe,expect,it} from 'vitest';
import {inventoryPolicyRows,inventoryPolicySelection,inventoryPolicyEventSearch,inventoryPolicyReturnSearch,policyScopeKey,verifyInventoryPolicyIdentity,type InventoryPolicyReport} from './inventoryPolicyModel';

const scope={planId:'P',versionId:'V',runId:'R',target:'baseline'};
const identity={plan_id:'P',data_version_id:'V',run_id:'R',target:'baseline',profile:'enterprise-forecast-fg-v1'};
const report={identity,timezone:'Asia/Shanghai',status:'PRESENT',mode:'POLICY_DIAGNOSIS_ONLY',rows:[
 {id:'B1',item_id:'FG',location_id:'FACTORY',uom:'件',policy_version:'POLICY-V1',start_at:'2026-11-01T00:00:00+08:00',end_at:'2026-11-06T00:00:00+08:00',status:'PRESENT',target_quantity:'10',ending_quantity:'7',ending_gap_quantity:'3',maximum_gap_quantity:'3',first_gap_at:'2026-11-02T00:00:00+08:00',event_ids:['R:E1'],events:[{id:'R:E1',at:'2026-11-02T00:00:00+08:00',kind:'DEMAND_CONSUMPTION',quantity:'-5',balance:'7',native_sequence:0,reference:'NATIVE-1',sources:[{artifact:'raw/fixed_batch_post.json',pointer:'/buffers/0/events/0',sha256:'a'.repeat(64)}]}],policy:{provenance:{source_locator:'库存目标策略!A6'}}},
 {id:'B2',item_id:'FG',location_id:'SHOP',uom:'件',policy_version:'POLICY-V1',start_at:'2026-11-06T00:00:00+08:00',end_at:'2026-12-01T00:00:00+08:00',status:'NOT_READY',target_quantity:'10',ending_quantity:null,ending_gap_quantity:null,maximum_gap_quantity:null,event_ids:[],events:[],policy:{}},
]} as InventoryPolicyReport;

describe('inventory policy evidence and navigation scope',()=>{
 it('requires exact plan, immutable version, run and target before exposing buckets',()=>{
  expect(verifyInventoryPolicyIdentity(report,scope)).toBe(report);
  for(const field of ['plan_id','data_version_id','run_id','target'])expect(()=>verifyInventoryPolicyIdentity({...report,identity:{...identity,[field]:'FOREIGN'}},scope)).toThrow();
 });
 it('filters exact dimensions and bucket while retaining unknown gaps',()=>{
  expect(inventoryPolicyRows(report.rows,{item:'FG',location:'SHOP',uom:'件',bucket:'B2'})).toEqual([report.rows[1]]);
  expect(inventoryPolicyRows(report.rows,{query:'policy-v1',location:'FACTORY'})).toEqual([report.rows[0]]);
  expect(inventoryPolicyRows(report.rows,{uom:'set'})).toEqual([]);
  expect(report.rows[1].ending_gap_quantity).toBeNull();
 });
 it('drops selections from another scope or filtered bucket and never picks a foreign event',()=>{
  expect(inventoryPolicySelection(report,scope,{}, {scope:policyScopeKey(scope),rowId:'B1',eventId:'R:E1'}).event?.id).toBe('R:E1');
  expect(inventoryPolicySelection(report,scope,{}, {scope:'OTHER',rowId:'B1',eventId:'R:E1'}).row).toBeNull();
  expect(inventoryPolicySelection(report,scope,{location:'SHOP'}, {scope:policyScopeKey(scope),rowId:'B1'}).row).toBeNull();
  expect(inventoryPolicySelection(report,scope,{}, {scope:policyScopeKey(scope),rowId:'B2',eventId:'R:E1'}).event).toBeNull();
 });
 it('opens the exact bucket/event and returns to the same policy filters',()=>{
  const previous=new URLSearchParams('target=FOREIGN&run_id=OLD&data_version_id=OLD&analysis=inventory-policy&policy_query=POLICY-V1&policy_location=FACTORY&policy_bucket=B1');
  const next=inventoryPolicyEventSearch(previous,report,scope,'B1','R:E1');
  expect(next.get('analysis')).toBe('inventory');expect(next.get('analysis_item')).toBe('FG');
  expect(next.get('analysis_location')).toBe('FACTORY');expect(next.get('analysis_event')).toBe('R:E1');
  expect(next.get('target')).toBe('baseline');expect(next.has('run_id')).toBe(false);expect(next.has('data_version_id')).toBe(false);
  expect(next.get('analysis_start')).toBe('2026-11-01T00:00:00+08:00');expect(next.get('analysis_end')).toBe('2026-11-06T00:00:00+08:00');
  const back=inventoryPolicyReturnSearch(next,scope);
  expect(back.get('analysis')).toBe('inventory-policy');expect(back.get('policy_query')).toBe('POLICY-V1');
  expect(back.get('policy_location')).toBe('FACTORY');expect(back.get('policy_bucket')).toBe('B1');
  expect(back.has('analysis_event')).toBe(false);expect(back.has('return_inventory_policy')).toBe(false);
 });
 it('refuses missing or unverified buckets and foreign event ids, clearing stale return selection after a run switch',()=>{
  for(const [row,event]of [['B1','FOREIGN'],['B2',''],['ABSENT','']])expect(()=>inventoryPolicyEventSearch(new URLSearchParams(),report,scope,row,event||undefined)).toThrow();
  const next=inventoryPolicyEventSearch(new URLSearchParams('policy_bucket=B1'),report,scope,'B1','R:E1');
  const back=inventoryPolicyReturnSearch(next,{...scope,runId:'NEW-RUN'});
  expect(back.has('policy_bucket')).toBe(false);expect(back.has('policy_event')).toBe(false);
 });
});
