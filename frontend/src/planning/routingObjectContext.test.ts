import {describe,expect,it} from 'vitest';
import {capacityReturn,routingScopeKey,routingViewContext,routingTaskSelection,routingObjectSource} from './routingCapacityModel';

const scope={planId:'PLAN-a',versionId:'DATA-a',runId:'API-a',target:'baseline'};
const input:any={planning_at:'2026-11-02T08:00:00+08:00',horizon:{end_at:'2026-11-06T18:00:00+08:00'},resources:[{id:'INSPECT-LINE',role:'MACHINE'},{id:'SEW-POOL',role:'MACHINE_POOL'},{id:'M1',role:'MACHINE',pool_id:'SEW-POOL'},{id:'M2',role:'MACHINE',pool_id:'SEW-POOL'},{id:'P1',role:'OPERATOR'}],routing_revisions:[{operations:[{id:'INSPECT',resource_id:'INSPECT-LINE'},{id:'SEW',resource_id:'SEW-POOL',machine_skill_id:'sewing'}]}],resource_skills:[{resource_id:'M1',skill_id:'sewing',priority:1,effective_from:'2026-11-01T00:00:00+08:00',effective_to:'2026-12-01T00:00:00+08:00'},{resource_id:'M2',skill_id:'sewing',priority:2,effective_from:'2026-11-01T00:00:00+08:00',effective_to:'2026-12-01T00:00:00+08:00'}]};
const tasks:any[]=[{id:'API-a:OP:11',manufacture_id:'API-a:MO:10',operation_id:'SEW',resource_id:'M1',quantity:'6',uom:'piece',start_at:'2026-11-05T09:00:00+08:00',end_at:'2026-11-05T10:00:00+08:00'},{id:'API-a:OP:16',manufacture_id:'API-a:MO:10',operation_id:'INSPECT',resource_id:'INSPECT-LINE',quantity:'6',uom:'piece',start_at:'2026-11-05T10:00:00+08:00',end_at:'2026-11-05T10:48:00+08:00'}];
const assignments=[{native_reference:'11',owner:'10',machine_id:'M1',operator_id:'P1'},{native_reference:'16',owner:'10',machine_id:'INSPECT-LINE',operator_id:'P1'}];
const saved=(values:Record<string,string>={})=>new URLSearchParams({rc_scope:JSON.stringify(scope),rc_task:'API-a:OP:16',rc_group:'API-a:MO:10',rc_resource:'INSPECT-LINE',rc_from:'2026-11-05T09:00:00+08:00',rc_to:'2026-11-05T11:00:00+08:00',...values});
const analysis:any={identity:{plan_id:'PLAN-a',data_version_id:'DATA-a',run_id:'API-a',target:'baseline'},resource_analysis:{status:'PRESENT',validation:{status:'PASS',issues:[]},resources:[{id:'INSPECT-LINE',event_status:'PRESENT'},{id:'P1',event_status:'PRESENT'}],intervals:[{id:'load16',operation_task_id:'API-a:OP:16',resource_id:'INSPECT-LINE'},{id:'person16',operation_task_id:'API-a:OP:16',resource_id:'P1'}]},evidence_files:[{path:'198.json',sha256:'a'.repeat(64)}]};

describe('routing object context never borrows another operation or run',()=>{
 it('restores exact INSPECT task, real machine/person and period within the same scope',()=>{
  const value=routingViewContext(scope,input,tasks,assignments,saved());
  expect(value.task?.id).toBe('API-a:OP:16');expect(value.actual?.machine_id).toBe('INSPECT-LINE');expect(value.actual?.operator_id).toBe('P1');expect(value.rid).toBe('INSPECT-LINE');expect(value.start).toBe('2026-11-05T09:00:00+08:00');expect(value.issue).toBe('');
 });
 it.each(['planId','versionId','runId','target'])('does not restore any foreign %s object, resource, filter or period',field=>{
  const value=routingViewContext({...scope,[field]:'foreign'},input,tasks,assignments,saved({rc_query:'old-filter',rc_only:'0'}));
  expect(value.task).toBeUndefined();expect(value.rid).toBeUndefined();expect(value.query).toBe('');expect(value.onlyGroup).toBe(true);expect(value.start).not.toBe('2026-11-05T09:00:00+08:00');expect(value.issue).not.toBe('');
 });
 it('rejects an unknown explicit task instead of silently displaying the first SEW task',()=>{
  const value=routingViewContext(scope,input,tasks,assignments,saved({rc_task:'API-a:OP:999'}));expect(value.task).toBeUndefined();expect(value.actual).toBeUndefined();expect(value.issue).not.toBe('');
 });
 it('rejects task/parent mismatch and foreign-run task with the same native reference',()=>{
  expect(routingViewContext(scope,input,tasks,assignments,saved({rc_group:'other-parent'})).task).toBeUndefined();
  const foreign=[{...tasks[1],id:'API-other:OP:16'}];expect(routingViewContext(scope,input,foreign,assignments,new URLSearchParams()).task).toBeUndefined();
 });
 it('does not treat a run-local object without a scope token as current',()=>{
  const p=saved();p.delete('rc_scope');expect(routingViewContext(scope,input,tasks,assignments,p).task).toBeUndefined();
 });
 it('starts clean without a saved selection and reads legacy correctly bound scope keys',()=>{
  expect(routingViewContext(scope,input,tasks,assignments,new URLSearchParams()).task?.id).toBe('API-a:OP:11');
  const legacy=saved({rc_scope:JSON.stringify({...scope,baselineRunId:'API-a',sourceSha256:'a'.repeat(64),snapshotSha256:'b'.repeat(64)})});expect(routingViewContext(scope,input,tasks,assignments,legacy).task?.id).toBe('API-a:OP:16');
  expect(routingScopeKey({...scope,sourceSha256:'unused'} as any)).toBe(JSON.stringify(scope));
 });
 it('keeps a valid selected operation while filtering it out, and rejects invalid time ranges',()=>{
  const value=routingViewContext(scope,input,tasks,assignments,saved({rc_query:'nothing',rc_from:'invalid',rc_to:'invalid'}));expect(value.task?.id).toBe('API-a:OP:16');expect(value.filtered).toHaveLength(0);expect(Number.isFinite(Date.parse(value.start))).toBe(true);expect(value.issue).not.toBe('');
 });
 it('selects resources from the new operation rather than keeping the previous machine pool',()=>{
  expect(routingTaskSelection(input,tasks,assignments,'API-a:OP:16')).toEqual({group:'API-a:MO:10',task:'API-a:OP:16',resource:'INSPECT-LINE',machine:''});
  expect(routingTaskSelection(input,tasks,assignments,'API-a:OP:11')).toEqual({group:'API-a:MO:10',task:'API-a:OP:11',resource:'M1',machine:'M1'});
 });
 it('keeps actual INSPECT source distinct from an investigated M2 candidate resource',()=>{
  const value=routingObjectSource(scope,tasks[1],assignments,analysis,'M2');expect(value.status).toBe('PRESENT');expect(value.task?.id).toBe('API-a:OP:16');expect(value.native_assignment?.machine_id).toBe('INSPECT-LINE');expect(value.investigated_resource_id).toBe('M2');expect(value.loads).toEqual(analysis.resource_analysis.intervals);
 });
 it('does not publish another object source when the task, native assignment or response identity is missing',()=>{
  for(const value of [routingObjectSource(scope,undefined,assignments,analysis),routingObjectSource(scope,tasks[1],[],analysis),routingObjectSource(scope,tasks[1],assignments,{...analysis,identity:{...analysis.identity,run_id:'foreign'}})]){expect(value.status).toBe('NOT_CHECKED');expect(value.native_assignment).toBeUndefined();expect(value.loads).toEqual([]);}
 });
 it.each(['UNSUPPORTED','NOT_COLLECTED','UNREADABLE','INVALID_EVIDENCE'])('preserves %s resource evidence without certifying its stale load rows',status=>{
  const value=routingObjectSource(scope,tasks[1],assignments,{...analysis,resource_analysis:{...analysis.resource_analysis,status}});
  expect(value.status).toBe(status);expect(value.object_status).toBe('PRESENT');expect(value.native_assignment?.machine_id).toBe('INSPECT-LINE');expect(value.resource_evidence_status).toBe(status);expect(value.loads_verified).toBe(false);expect(value.loads).toEqual([]);
 });
 it('does not turn an absent resource certificate into PRESENT or zero load',()=>{
  const value=routingObjectSource(scope,tasks[1],assignments,{...analysis,resource_analysis:undefined});expect(value.status).toBe('NOT_CHECKED');expect(value.object_status).toBe('PRESENT');expect(value.loads_verified).toBe(false);expect(value.loads).toEqual([]);
 });
 it('keeps a known hard constraint FAIL ahead of source gaps and preserves the failed audit',()=>{
  const validation={status:'FAIL',issues:[{code:'RESOURCE_CAPACITY_EXCEEDED'}]};
  const value=routingObjectSource(scope,tasks[1],assignments,{...analysis,resource_analysis:{...analysis.resource_analysis,status:'UNREADABLE',validation}});
  expect(value.status).toBe('FAIL');expect(value.resource_evidence_status).toBe('UNREADABLE');expect(value.resource_validation).toEqual(validation);expect(value.loads).toEqual([]);expect(value.loads_verified).toBe(false);
 });
 it('does not erase a same-run resource FAIL when the object assignment is also missing',()=>{
  const failed={...analysis,resource_analysis:{...analysis.resource_analysis,validation:{status:'FAIL',issues:[{code:'MISSING_ASSIGNMENT'}]}}};
  const value=routingObjectSource(scope,tasks[1],[],failed);expect(value.status).toBe('FAIL');expect(value.object_status).toBe('NOT_CHECKED');expect(value.resource_validation.status).toBe('FAIL');expect(value.loads).toEqual([]);
 });
 it('requires a passing audit and the actual machine and person load evidence',()=>{
  const uncertified={...analysis,resource_analysis:{...analysis.resource_analysis,validation:{status:'NOT_CHECKED'}}};
  expect(routingObjectSource(scope,tasks[1],assignments,uncertified).status).toBe('NOT_CHECKED');
  const missingPerson={...analysis,resource_analysis:{...analysis.resource_analysis,intervals:analysis.resource_analysis.intervals.slice(0,1)}};
  const value=routingObjectSource(scope,tasks[1],assignments,missingPerson);expect(value.status).toBe('NOT_CHECKED');expect(value.loads).toEqual([]);
  const complete=routingObjectSource(scope,tasks[1],assignments,analysis);expect(complete.status).toBe('PRESENT');expect(complete.loads_verified).toBe(true);
 });
 it('leaves current-run request identity intact on a same-target return but clears leaf scope on candidate change',()=>{
  const p=saved({target:'baseline',rc_demand:'old-demand'});expect(capacityReturn(p,'baseline').get('rc_task')).toBe('API-a:OP:16');const changed=capacityReturn(p,'DRAFT-new');expect(changed.get('rc_task')).toBeNull();expect(changed.get('rc_demand')).toBeNull();
 });
});
