import {describe,it,expect} from 'vitest';
import {enterSupplyWork,rememberSupplyWork,returnSupplyWork} from './supplyWorkNavigation';
import {analysisContextSearch,taskAnalysisSearch} from './analysisModel';
import {accessorySearch,accessoryReturnSearch} from './accessorySupplyModel';
const scope={planId:'P',versionId:'V',runId:'R',target:'baseline'};
const before=new URLSearchParams('target=baseline&analysis=supply-work&supply_item=RAW&supply_period=2026-11-03&supply_query=cloth&supply_attention=qc&supply_doc=PO1&supply_event=E1&supply_scope=OLD');
describe('material work origin and scenario boundaries',()=>{
 it('restores the same run object and filters after inventory or review',()=>{
  const p=rememberSupplyWork(before,scope);p.set('analysis','inventory');
  expect(returnSupplyWork(p,scope)?.toString()).toBe(before.toString());
 });
 it('keeps static material filters but drops old event and document identity on a new candidate',()=>{
  const p=analysisContextSearch(rememberSupplyWork(before,scope),'D');
  const returned=returnSupplyWork(p,{...scope,target:'D',runId:'NEW'})!;
  expect(returned.get('analysis')).toBe('supply-work');expect(returned.get('target')).toBe('D');
  expect(returned.get('supply_item')).toBe('RAW');expect(returned.get('supply_attention')).toBe('qc');
  for(const k of ['supply_doc','supply_event','supply_scope'])expect(returned.has(k)).toBe(false);
 });
 it('does not restore another plan or version origin',()=>{
  const p=rememberSupplyWork(before,scope);
  expect(returnSupplyWork(p,{...scope,planId:'OTHER'})).toBeNull();
  expect(returnSupplyWork(p,{...scope,versionId:'OTHER'})).toBeNull();
 });
 it('rejects malformed origins without throwing',()=>expect(returnSupplyWork(new URLSearchParams('supply_return_scope=broken'),scope)).toBeNull());
 it('retains the first object when drilling through multiple views',()=>{
  const p=rememberSupplyWork(before,scope);p.set('analysis','review');
  expect(rememberSupplyWork(p,scope).get('supply_return')).toBe(before.toString());
 });
 it('preserves supply filters in common scenario navigation but invalidates transient selection',()=>{
  const p=analysisContextSearch(before,'D');
  expect(p.get('supply_query')).toBe('cloth');expect(p.get('supply_item')).toBe('RAW');
  for(const k of ['supply_doc','supply_event','supply_scope'])expect(p.has(k)).toBe(false);
 });
 it('accessory return does not restore a previous run native event',()=>{
  const p=accessorySearch(before,{...scope,baselineRunId:'R'});
  const returned=accessoryReturnSearch(p,{...scope,target:'D',runId:'NEW',baselineRunId:'R'});
  expect(returned.get('supply_item')).toBe('RAW');
  for(const k of ['supply_doc','supply_event','supply_scope'])expect(returned.has(k)).toBe(false);
 });
 it('a main-navigation re-entry starts the return origin at the newly selected material',()=>{
  const old=rememberSupplyWork(before,scope);old.set('analysis','inventory');
  const next=enterSupplyWork(old);next.set('supply_item','LABEL');
  const review=rememberSupplyWork(next,scope);review.set('analysis','review');
  expect(returnSupplyWork(review,scope)?.get('supply_item')).toBe('LABEL');
 });
 it('explicit task navigation supersedes a previous calendar segment',()=>{
  const p=taskAnalysisSearch(new URLSearchParams('analysis_calendar_window=OLD&analysis_event=E'),'TASK','PERSON');
  expect(p.get('analysis_task')).toBe('TASK');expect(p.has('analysis_calendar_window')).toBe(false);
 });
});

it('native operation drill retains exact purchase origin and clears it for another run',()=>{const q=new URLSearchParams(before);q.set('purchase_doc','PO1');q.set('purchase_group','G1');q.set('supply_doc_period','UNRESOLVED');q.set('supply_bucket','DATE');const p=rememberSupplyWork(q,scope);p.set('analysis','schedule');p.set('task','TASK');expect(returnSupplyWork(p,scope)?.toString()).toBe(q.toString());const changed=returnSupplyWork(p,{...scope,runId:'NEW',target:'D'})!;for(const k of ['purchase_doc','purchase_group','supply_doc_period','supply_bucket'])expect(changed.has(k)).toBe(false)});
