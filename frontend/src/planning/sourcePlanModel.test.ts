import {expect,it} from 'vitest';
import {sourceResources,sourceGantt,verifySourceWorkspace} from './sourcePlanModel';
it('source calendar hours union overlaps and do not invent load or pool hours',()=>{
 const input={horizon:{start_at:'2026-11-01T00:00:00Z',end_at:'2026-11-02T00:00:00Z'},calendars:[{id:'C',available_intervals:[{start_at:'2026-11-01T01:00:00Z',end_at:'2026-11-01T03:00:00Z'},{start_at:'2026-11-01T02:00:00Z',end_at:'2026-11-01T04:00:00Z'}]}],resources:[{id:'M',parallel_units:2,availability_calendar_id:'C'},{id:'P',role:'OPERATOR_POOL',availability_calendar_id:'C'},{id:'W',capacity_model:'WINDOW_QUANTITY',availability_calendar_id:'C'},{id:'U',parallel_units:1}]};
 expect(sourceResources(input).map(r=>r.available_hours)).toEqual([6,null,null,null]);
 expect(sourceResources(input).every(r=>r.planned_load===null)).toBe(true);
 expect(sourceGantt(input).intervals).toEqual([]);
});
it('source response cannot claim native result or another DATA',()=>{
 expect(()=>verifySourceWorkspace({schema_version:'source-workspace/1',basis:'CONFIRMED_PLAN_INPUT',identity:{plan_id:'P',data_version_id:'V',run_id:'API-x'}},'P','V')).toThrow();
});
it('original manufacturing window stays protected without generating personnel occupations',()=>{
 const data={horizon:{start_at:'2026-11-01T00:00:00Z',end_at:'2026-11-03T00:00:00Z'},resources:[{id:'M',role:'MACHINE'}],existing_orders:[{id:'MO-A',type:'MO',protected:true,resource_id:'M',quantity:20,uom:'件',item_id:'X',start_at:'2026-11-02T01:00:00Z',end_at:'2026-11-02T02:00:00Z'}]};
 const gantt=sourceGantt(data);expect(gantt.intervals).toHaveLength(1);expect(gantt.intervals[0].protected).toBe(true);expect(gantt.intervals[0].end-gantt.intervals[0].start).toBe(3600000);
});
it('uses explicitly declared source operations for real MO windows',()=>{
 const data={horizon:{start_at:'2026-11-01T00:00:00Z',end_at:'2026-11-03T00:00:00Z'},resources:[{id:'M',role:'MACHINE'},{id:'P',role:'OPERATOR'}],existing_orders:[{id:'MO-B',type:'MO',quantity:20,uom:'件',operations:[{operation_id:'SEW',resource_id:'M',start_at:'2026-11-02T01:00:00Z',end_at:'2026-11-02T02:00:00Z'}]}]};
 expect(sourceGantt(data).intervals.map(r=>r.rowId)).toEqual(['M']);
});
