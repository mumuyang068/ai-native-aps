import {it,expect} from 'vitest';
import {buildWorkspaceView,filterTasks,taskExtent,canRecoverUnsubmittedBaseline} from './planModel';
const identity={plan_id:'PLAN-aaaaaaaaaaaaaaaa',data_version_id:'DATA-aaaaaaaaaaaa',snapshot_sha256:'abc'};
const context={enterprise_source:identity,resource_calendars:{resources:[],calendars:[]},order_summary:{orders:[{id:'D1',order_ref:'O1',item_id:'X'},{id:'D2',order_ref:'O2',item_id:'X'}]}};
const snapshot={data_version:{id:identity.data_version_id},plan_input:{items:[],resources:[],routing_revisions:[],production_recipes:[]}} as any;
const result={operations:[{id:'OP1',manufacture_id:'M1',operation_id:'SAME',resource_id:'R',quantity:'10',start_at:'2026-10-14T09:00:00+08:00',end_at:'2026-10-14T10:00:00+08:00'},{id:'OP2',manufacture_id:'M2',operation_id:'SAME',resource_id:'R',quantity:'2',start_at:'2026-10-14T10:00:00+08:00',end_at:'2026-10-14T11:00:00+08:00'}],manufacture_suggestions:[{id:'M1',item_id:'X',uom:'piece'},{id:'M2',item_id:'X',uom:'m'}],deliveries:[],purchase_suggestions:[]};
it('preserves repeated operations as distinct tasks and derives units by manufacture ID',()=>{const m=buildWorkspaceView({context} as any,{context,result} as any,snapshot);expect(m.tasks.map(t=>t.id)).toEqual(['OP1','OP2']);expect(m.tasks.map(t=>t.uom)).toEqual(['piece','m']);expect(m.tasks.every(t=>t.orderAllocation===null)).toBe(true)});
it('uses the same filter input for schedule and manufacturing records',()=>{const m=buildWorkspaceView({context} as any,{context,result} as any,snapshot);expect(filterTasks(m,'SAME')).toHaveLength(2);expect(filterTasks(m,'OP2')).toHaveLength(1);expect(filterTasks(m,'not-present')).toHaveLength(0);expect(filterTasks(m,'  ')).toHaveLength(2)});
it('rejects cross-plan results and mismatched immutable snapshots',()=>{expect(()=>buildWorkspaceView({context} as any,{context:{...context,enterprise_source:{...identity,plan_id:'PLAN-bbbbbbbbbbbbbbbb'}},result} as any,snapshot)).toThrow();expect(()=>buildWorkspaceView({context} as any,{context,result} as any,{...snapshot,data_version:{id:'DATA-bbbbbbbbbbbb'}})).toThrow()});
it('fits the latest end even when an earlier task runs longer',()=>{const tasks=[{start_at:'2026-10-14T09:00:00+08:00',end_at:'2026-10-17T18:00:00+08:00'},{start_at:'2026-10-15T09:00:00+08:00',end_at:'2026-10-15T18:00:00+08:00'}];expect(taskExtent(tasks)).toEqual({start:tasks[0].start_at,end:tasks[0].end_at});expect(taskExtent([])).toBeNull()});
it('offers explicit first submission only for an unsubmitted baseline without an accepted run',()=>{
 const pending={error:'BASELINE_NOT_READY'};
 expect(canRecoverUnsubmittedBaseline('baseline',{state:'NOT_SUBMITTED'},pending)).toBe(true);
 for(const state of ['QUEUED','RUNNING','SUCCEEDED','FAILED',undefined])expect(canRecoverUnsubmittedBaseline('baseline',{state},pending)).toBe(false);
 expect(canRecoverUnsubmittedBaseline('DRAFT-1',{state:'NOT_SUBMITTED'},pending)).toBe(false);
 expect(canRecoverUnsubmittedBaseline('baseline',{state:'NOT_SUBMITTED',solve_run_id:'API-existing'},pending)).toBe(false);
 expect(canRecoverUnsubmittedBaseline('baseline',{state:'NOT_SUBMITTED'},{...pending,run:{state:'QUEUED',solve_run_id:'API-existing'}})).toBe(false);
 expect(canRecoverUnsubmittedBaseline('baseline',{state:'NOT_SUBMITTED'},{error:'COMPUTE_UNAVAILABLE'})).toBe(false);
});
