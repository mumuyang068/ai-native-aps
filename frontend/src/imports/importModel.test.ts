import {it,expect} from 'vitest';
import {locateIssue,resolveVersionAction,canSave,cellCoordinates,prioritizeIssues} from './importModel';
it('puts planning blockers before optional warnings while keeping source order',()=>{const rows=[{severity:'WARNING',code:'CONTACT'},{severity:'BLOCKER',code:'ROUTE'},{severity:'CAPABILITY',code:'RESERVE'}] as any;expect(prioritizeIssues(rows).map(i=>i.code)).toEqual(['ROUTE','RESERVE','CONTACT'])});
it('locates original sheet and page using the actual header row',()=>{
 const task={source:{sheets:{工艺:{header_row:5,headers:['编号'],rows:Array(100).fill([null])}}}} as any;
 expect(locateIssue(task,{sheet:'工艺',cell:'J57'})).toEqual({sheet:'工艺',page:1,cell:'J57'});
 expect(cellCoordinates('AA7',5)).toEqual({row:1,column:26});
 expect(locateIssue(task,{sheet:'缺少的表',cell:'J7'})).toBeNull();
});
it('does not mark confirmed-but-blocked or unconfirmed data saveable',()=>{
 expect(canSave({readiness:{ready:false,confirmed:true}} as any)).toBe(false);
 expect(canSave({readiness:{ready:true,confirmed:false}} as any)).toBe(false);
 expect(canSave({readiness:{ready:true,confirmed:true}} as any)).toBe(true);
});
it('opens an existing plan rather than creating another one',()=>{
 const plans=[{id:'PLAN-aaaaaaaaaaaaaaaa',data_version_id:'DATA-aaaaaaaaaaaa'}] as any;
 expect(resolveVersionAction('DATA-aaaaaaaaaaaa',plans)).toEqual({kind:'OPEN',planId:'PLAN-aaaaaaaaaaaaaaaa'});
 expect(resolveVersionAction('DATA-bbbbbbbbbbbb',plans)).toEqual({kind:'CREATE',versionId:'DATA-bbbbbbbbbbbb'});
 expect(()=>resolveVersionAction('IMP-aaaaaaaaaaaa',plans)).toThrow();
});
