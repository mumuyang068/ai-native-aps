import {describe,it,expect} from 'vitest';
import {analysisContextSearch} from './analysisModel';
import {reviewObjectSearch} from './scenarioReviewModel';

describe('professional decision navigation retains its exact origin',()=>{
 it('retains exception filters, selected demand and original run through same-target review',()=>{
  const p=new URLSearchParams('target=baseline&analysis=exceptions&exception_status=SHORT&exception_from=2026-10-20&exception_row=D1&exception_run=R1&exception_target=baseline&return_decision=exceptions&review_drafts=A,B&review_focus=D1');
  const next=analysisContextSearch(p,'baseline');
  expect(next.get('exception_status')).toBe('SHORT');
  expect(next.get('exception_from')).toBe('2026-10-20');
  expect(next.get('exception_row')).toBe('D1');
  expect(next.get('exception_run')).toBe('R1');
  expect(next.get('return_decision')).toBe('exceptions');
  expect(next.get('review_drafts')).toBe('A,B');
  expect(next.get('review_focus')).toBe('D1');
 });
 it('preserves the origin but removes old-run task and event when switching candidates',()=>{
  const next=analysisContextSearch(new URLSearchParams('target=A&exception_run=R1&exception_target=baseline&exception_owner=SEW&analysis_task=old&analysis_event=event&review_focus=D1'),'B');
  expect(next.get('exception_run')).toBe('R1');
  expect(next.get('exception_target')).toBe('baseline');
  expect(next.get('exception_owner')).toBe('SEW');
  expect(next.get('review_focus')).toBe('D1');
  expect(next.has('analysis_task')).toBe(false);
  expect(next.has('analysis_event')).toBe(false);
 });
});

describe('review destination filter ownership',()=>{
 it('opens LABEL inventory without turning a purchased accessory into a manufacturing filter',()=>{
  const next=reviewObjectSearch(new URLSearchParams('target=baseline&analysis=review&review_drafts=A&review_focus=NET'),'A','inventory',undefined,{item_id:'LABEL',location_id:'L'});
  expect(next.get('analysis')).toBe('inventory');expect(next.get('analysis_item')).toBe('LABEL');expect(next.get('analysis_location')).toBe('L');expect([...next.keys()].some(k=>k.startsWith('mf_'))).toBe(false);expect(next.get('return_decision')).toBe('review');expect(next.get('review_focus')).toBe('NET');
 });
 it('keeps legitimate manufacturing context unchanged across inventory and resource drilldowns',()=>{
  const origin=new URLSearchParams('target=A&analysis=review&mf_item_id=FG&mf_operation_id=SEW&mf_bucket=B&mf_task=T&analysis_resource=P1&analysis_task=OLD&analysis_event=OLD');
  const inventory=reviewObjectSearch(origin,'A','inventory',undefined,{item_id:'LABEL',location_id:'L'});expect(inventory.get('mf_item_id')).toBe('FG');expect(inventory.get('mf_bucket')).toBe('B');expect(inventory.get('analysis_resource')).toBeNull();expect(inventory.get('analysis_task')).toBeNull();
  const resource=reviewObjectSearch(inventory,'A','resources',undefined,{resource_id:'P2',item_id:'LABEL',location_id:'L'});expect(resource.get('analysis_resource')).toBe('P2');expect(resource.get('analysis_item')).toBeNull();expect(resource.get('analysis_location')).toBeNull();expect(resource.get('mf_item_id')).toBe('FG');expect(resource.get('mf_operation_id')).toBe('SEW');expect(resource.get('mf_task')).toBe('T');
 });
 it('opens the selected SEW business group and discards stale manufacturing selections and dimensions',()=>{
  const origin=new URLSearchParams('target=baseline&analysis=review&mf_item_id=LABEL&mf_operation_id=CUT&mf_location_id=OLD&mf_uom=set&mf_status=confirmed&mf_scope=old&mf_bucket=B&mf_task=T&mf_task_query=old&mf_from=2026-01-01');
  const next=reviewObjectSearch(origin,'A','manufacturing',undefined,{operation_id:'SEW',item_id:'FG',location_id:'L',uom:'piece',status:'proposed'});
  expect(next.get('analysis')).toBe('manufacturing');expect(next.get('mf_operation_id')).toBe('SEW');expect(next.get('mf_item_id')).toBe('FG');expect(next.get('mf_location_id')).toBe('L');expect(next.get('mf_uom')).toBe('piece');expect(next.get('mf_status')).toBe('proposed');for(const key of ['mf_scope','mf_bucket','mf_task','mf_task_query','mf_from'])expect(next.has(key)).toBe(false);
  const noUnit=reviewObjectSearch(origin,'A','manufacturing',undefined,{operation_id:'SEW',item_id:'FG'});expect(noUnit.has('mf_location_id')).toBe(false);expect(noUnit.has('mf_uom')).toBe(false);expect(noUnit.has('mf_status')).toBe(false);
 });
});
