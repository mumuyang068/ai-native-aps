import {describe,expect,it} from 'vitest';
import {taskEditFromFields,mergeTaskTime,editorIntentText,editorTaskLabel,editorTaskReference} from './editorIntentModel';
const task={id:'run:task:1',start_at:'2026-11-05T01:00:00Z',end_at:'2026-11-05T02:00:00Z',quantity:'20',root_quantity:'20',resources:['M1'],recipe_id:'R1'};
describe('one professional editor intent',()=>{
 it('maps the exact Gantt operation to its opaque edit selector and never guesses from shared root membership',()=>{
  const first={id:'TASK20',operation_task_id:'run:OP:113',affected_operation_ids:['run:OP:110','run:OP:113']},second={id:'TASK15',operation_task_id:'run:OP:114',affected_operation_ids:['run:OP:110','run:OP:114']},options={proposal_tasks:[first,second]};
  expect(editorTaskReference(options,'run:OP:113')).toBe('TASK20');expect(editorTaskReference(options,'TASK15')).toBe('TASK15');expect(editorTaskReference(options,'run:OP:110')).toBeNull();expect(editorTaskReference({proposal_tasks:[first,{...first,id:'duplicate'}]},'run:OP:113')).toBeNull();
 });
 it('distinguishes otherwise identical suggestions by exact site start and root reference',()=>{
  const first=editorTaskLabel({...task,operation_id:'CUT',root_native_reference:'107'},'Asia/Shanghai'),second=editorTaskLabel({...task,operation_id:'CUT',root_native_reference:'108'},'Asia/Shanghai');expect(first).toContain('09:00');expect(first).toContain('供应 107');expect(first).not.toBe(second);
 });
 it('retains quantity and route when a Gantt gesture changes time',()=>{
  const edits=mergeTaskTime([{kind:'task',reference:task.id,changes:{quantity:'15',recipe_id:'R2'}}],task.id,{mode:'START_ANCHOR',start_at:'2026-11-05T03:00:00Z'});
  expect(edits).toEqual([{kind:'task',reference:task.id,changes:{quantity:'15',recipe_id:'R2',time:{mode:'START_ANCHOR',start_at:'2026-11-05T03:00:00Z'}}}]);
 });
 it('makes a compound exact site-time intent and omits unchanged fields',()=>{
  const value=taskEditFromFields(task,{mode:'WINDOW',start:'2026-11-05T11:00:00',end:'2026-11-05T13:00:00',quantity:'15',resource:'M2',recipe:'R2'},'Asia/Shanghai');
  expect(value.changes).toEqual({time:{mode:'WINDOW',start_at:'2026-11-05T03:00:00.000Z',end_at:'2026-11-05T05:00:00.000Z'},quantity:'15',resource_id:'M2',recipe_id:'R2'});
  expect(taskEditFromFields(task,{mode:'START_ANCHOR',start:'2026-11-05T09:00:00',end:'2026-11-05T10:00:00',quantity:'20.0',resource:'M1',recipe:'R1'},'Asia/Shanghai').changes).toEqual({});
 });
 it('rejects malformed quantities and reversed windows without making a candidate',()=>{
  for(const quantity of ['-1','NaN','Infinity','1e2'])expect(()=>taskEditFromFields(task,{mode:'WINDOW',start:'2026-11-05T11:00:00',end:'2026-11-05T10:00:00',quantity,resource:'',recipe:''},'Asia/Shanghai')).toThrow();
 });
 it('presents task and multiple-demand edits without a legacy sequence fallback',()=>{
  expect(editorIntentText({kind:'task',reference:task.id,changes:{quantity:'15',resource_id:'M2'}},'Asia/Shanghai')).toContain('15');
  expect(editorIntentText({kind:'demand',reference:'customer-demands',action:'UNPLAN',demand_ids:['D1','D2']},'Asia/Shanghai')).toContain('D1、D2');
 });
});
