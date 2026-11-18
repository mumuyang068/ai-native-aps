import {describe,it,expect} from 'vitest';
import {workRows,verifyWork,executionLabel} from './rollingPlanningModel';
const scope={planId:'PLAN-a',versionId:'DATA-a',runId:'API-a',target:'baseline'};
describe('manufacturing work preserves plan and execution boundaries',()=>{
 it('rejects any mismatched scope without retaining old work',()=>{
  for(const key of ['plan_id','data_version_id','run_id','target'])expect(()=>verifyWork({identity:{plan_id:'PLAN-a',data_version_id:'DATA-a',run_id:'API-a',target:'baseline',[key]:'wrong'}},scope)).toThrow();
 });
 it('filters exact dimensions and overlapping periods without losing zero facts',()=>{
  const task={id:'API-a:OP:1',operation_id:'CUT',location_id:'FACTORY',item_id:'WIP',status:'proposed',resource_ids:['M1'],start_at:'2026-11-02T09:00:00+08:00',end_at:'2026-11-02T10:00:00+08:00'};
  expect(workRows([task],{resource:'M1',from:'2026-11-02T09:30:00+08:00'})).toEqual([task]);
  expect(workRows([task],{resource:'P1'})).toEqual([]);
  expect(workRows([task],{from:'2026-11-02T10:00:00+08:00'})).toEqual([]);
  expect(workRows([task],{from:'2026-11-02T09:30'},'Asia/Shanghai')).toEqual([task]);
  expect(workRows([task],{from:'2026-11-02T11:00',to:'2026-11-02T09:00'},'Asia/Shanghai')).toEqual([]);
 });
 it('never translates a planned end into completion or a percentage',()=>{
  expect(executionLabel({status:'NOT_COLLECTED'})).toContain('未采集');
  expect(executionLabel({status:'PRESENT',evidence_class:'SYNTHETIC_RESEARCH_ASSUMPTION'})).toContain('合成');
 });
});
