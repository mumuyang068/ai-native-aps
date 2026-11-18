import {describe,expect,it} from 'vitest';
import {decisionQuantity,decisionRows,priorityValue,verifyDecisionIdentity,verifyPriorityDraft,scopedPrioritySelection} from './demandDecisionModel';

const scope={planId:'P',versionId:'V',runId:'R',target:'baseline'};
const identity={plan_id:'P',data_version_id:'V',run_id:'R',target:'baseline'};

describe('demand decision read scope and unknown quantities',()=>{
 it('requires the exact plan, immutable version, target and run before exposing records',()=>{
  const response={identity,rows:[]};expect(verifyDecisionIdentity(response,scope)).toBe(response);
  for(const key of ['plan_id','data_version_id','run_id','target'])expect(()=>verifyDecisionIdentity({...response,identity:{...identity,[key]:'foreign'}},scope)).toThrow();
  expect(()=>verifyDecisionIdentity({rows:[]},scope)).toThrow();
 });
 it('keeps unknown and invalid amounts distinct from a measured zero',()=>{
  for(const value of [null,undefined,'',NaN,Infinity,'unknown'])expect(decisionQuantity(value,'set')).toBe('未知');
  expect(decisionQuantity('0','set')).toBe('0 set');expect(decisionQuantity('1200.50','piece')).toBe('1,200.50 件');
 });
 it('filters only supplied dimensions, while preserving source and missing fields',()=>{
  const rows=[{id:'A',item_id:'FG',location_id:'L1',customer_id:'C1',uom:'set',source_version:'FORECAST-V1',value:null},{id:'B',item_id:'FG',location_id:'L2',customer_id:null,uom:'piece',value:'0'}];
  expect(decisionRows(rows,{query:'forecast-v1'})).toEqual([rows[0]]);
  expect(decisionRows(rows,{query:'FG',location:'L2',uom:'piece'})).toEqual([rows[1]]);
  expect(decisionRows(rows,{customer:'C1',location:'L2'})).toEqual([]);
  expect(rows[0].value).toBeNull();
 });
 it('requires an unrun isolated draft rather than navigating to an already solved or absent record',()=>{
  expect(verifyPriorityDraft({draft:{id:'DRAFT-A',state:'DRAFT',run_id:null}})).toBe('DRAFT-A');
  for(const draft of [null,{id:'DRAFT-A',state:'DRAFT',run_id:'R'},{id:'DRAFT-A',state:'RETAINED',run_id:null},{id:'',state:'DRAFT',run_id:null}])expect(()=>verifyPriorityDraft({draft})).toThrow();
 });
 it('uses a bounded integer demand priority and refuses unchanged or malformed values',()=>{
  expect(priorityValue('0',10)).toBe(0);expect(priorityValue('2147483647',10)).toBe(2147483647);
  for(const value of ['','-1','1.5','1e2','true','2147483648','10',' 1 '])expect(priorityValue(value,10)).toBeNull();
  expect(priorityValue('2',undefined)).toBeNull();
 });
});


it('drops a priority editor when leaving its precise exception plan/version/run/target',()=>{
 const selection={scope,demand:{demand_id:'ORDER-2',priority:20}};
 expect(scopedPrioritySelection(selection,scope,'exceptions')).toEqual(selection.demand);
 for(const key of ['planId','versionId','runId','target'])expect(scopedPrioritySelection(selection,{...scope,[key]:'foreign'},'exceptions')).toBeNull();
 for(const mode of ['availability','demand-planning','schedule','review'])expect(scopedPrioritySelection(selection,scope,mode)).toBeNull();
});
