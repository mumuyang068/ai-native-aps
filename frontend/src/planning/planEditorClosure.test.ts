import {describe,expect,it} from 'vitest';
import {continuousEditorWindow,editorTaskQuantityNotice} from './planEditorModel';

const input={planning_at:'2026-11-05T08:00:00+08:00',horizon:{start_at:'2026-11-05T08:00:00+08:00',end_at:'2026-11-06T18:00:00+08:00'},site:{timezone:'Asia/Shanghai'},foundation_projection:{native_operation_map:{'CONT-CONTINUE:J:SEW':{recipe_id:'CONT-CONTINUE:J',operation_id:'SEW'}},continuity_scope:{wip_jobs:[{recipe_id:'CONT-CONTINUE:J',original_job_quantity:20,finished_received_quantity:12,remaining_quantity:8}]}}};
const task={id:'TASK-1',native_operation:'CONT-CONTINUE:J:SEW',quantity:'8',start_at:'2026-11-05T09:00:00+08:00',end_at:'2026-11-05T10:04:00+08:00'};

describe('closure bounded editor',()=>{
 it('submits the selected native task and site timestamp without a quantity mutation',()=>{expect(continuousEditorWindow(input,task,'2026-11-05T13:00')).toEqual({kind:'proposed_window',reference:'TASK-1',start_at:'2026-11-05T05:00:00.000Z'})});
 it('rejects a past window, invalid timestamp and end outside the horizon',()=>{for(const value of ['2026-11-05T07:00','bad','2026-11-06T17:30'])expect(()=>continuousEditorWindow(input,task,value)).toThrow()});
 it('describes the remaining WIP tranche instead of presenting twenty as editable',()=>{const text=editorTaskQuantityNotice(input,task);expect(text).toContain('已入库 12');expect(text).toContain('剩余 8');expect(text).toContain('数量保持不变')});
 it('keeps ordinary proposal quantity immutable when no progress contract exists',()=>{expect(editorTaskQuantityNotice({},task)).toContain('8');expect(editorTaskQuantityNotice({},task)).toContain('数量保持不变')});
});
