import {describe,it,expect} from 'vitest';
import {replaceEdit,removeEdit,editorIdentityMatches,editorScopeRequest} from './editorDraftModel';
import {scenarioPreview} from '../scenarios/scenarioPreview';
describe('manufacturing hypothesis list',()=>{
 it('updates a stable business object once and undoes only that object',()=>{
  let edits=replaceEdit([], {kind:'window',reference:'B',start_at:'one'});
  edits=replaceEdit(edits,{kind:'window',reference:'C',start_at:'two'});
  edits=replaceEdit(edits,{kind:'window',reference:'B',start_at:'three'});
  expect(edits.map(e=>e.reference)).toEqual(['B','C']);expect(edits[0].start_at).toBe('three');
  expect(removeEdit(edits,'window','B')).toEqual([edits[1]]);
 });
 it('does not accept a different run or target as a matching preview',()=>{
  const s={plan_id:'P',data_version_id:'V',baseline_run_id:'R',run_id:'R',target:'baseline'};
  expect(editorIdentityMatches(s,s)).toBe(true);
  expect(editorIdentityMatches({...s,run_id:'X'},s)).toBe(false);
  expect(editorIdentityMatches({...s,target:'DRAFT'},s)).toBe(false);
  expect(Object.keys(editorScopeRequest(s))).toEqual(['data_version_id','baseline_run_id','run_id','target']);
 });
 it('shows the edited business objects instead of empty urgent-order fields',()=>{
  const value=scenarioPreview({action:'simulate_plan_editor',edits:[{reference:'B'},{reference:'C'}],events:[{kind:'window',reference:'B',before:{start_at:'one'},after:{start_at:'two'}}],reason:'joint'});
  expect(value).toContainEqual(['调整对象','B、C']);expect(value).toContainEqual(['B','one → two']);
  expect(value.some(([label])=>label==='急单编号')).toBe(false);
 });
});
it('renders a complete-enterprise proposed window without treating it as sequence',()=>{
 const value=scenarioPreview({action:'simulate_plan_editor',edits:[{reference:'TASK-1'}],events:[{kind:'proposed_window',reference:'TASK-1',source_task:{start_at:'old'},start_at:'new'}],reason:'full enterprise'});
 expect(value).toContainEqual(['TASK-1','old → new']);
 expect(value).toContainEqual(['计算核验要求','原Worker求解和业务核验']);
 expect(value.some(([label])=>label==='计算状态')).toBe(false);
});
it('renders compound optimization and native algorithm intents without sequence fields',()=>{
 const value=scenarioPreview({action:'simulate_plan_editor',edits:[{reference:'optimization'}],events:[{kind:'optimized_schedule',reference:'optimization',assignments:[{reference:'A'}]},{kind:'native_strategy',reference:'optimization',solver:'heuristic_2',machine_search:'PRIORITY'}],reason:'research'});
 expect(value).toContainEqual(['optimization','联合调整 1 道工序']);
 expect(value).toContainEqual(['optimization','heuristic_2 · PRIORITY']);
});
it('renders normalized task and demand intents before any native run exists',()=>{
 const value=scenarioPreview({action:'simulate_plan_editor',reason:'compound',edits:[{reference:'T1'},{reference:'customer-demands'}],events:[{kind:'task',reference:'T1',changes:{quantity:'15',resource_id:'M2'}},{kind:'demand',reference:'customer-demands',action:'BACKWARD',demand_ids:['D1','D2']}]});
 expect(value).toContainEqual(['T1','供应数量 15 · 设备 M2']);expect(value.some(([id,text])=>id==='customer-demands'&&text.includes('D1、D2'))).toBe(true);
});
