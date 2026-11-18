import {describe,it,expect} from 'vitest';
import input from '../../../fixtures/p416-routing-capacity.json';
import {routingProjection,scheduleProjection,orderScheduleProjection} from './ganttProjection';
const a='2026-11-04T08:00:00+08:00',b='2026-11-04T12:00:00+08:00';
const t:any={id:'run:OP:1',manufacture_id:'run:MO:1',operation_id:'P416-SEW',resource_id:'P416-SEW-M1',start_at:a,end_at:b,quantity:'4',uom:'piece',origin:'EXISTING'};
const resource=(id:string,status='PRESENT')=>({id,role:id==='P416-P1'?'OPERATOR':'MACHINE',capacity:'1',event_status:status,availability:[{start_at:a,end_at:b}]});
describe('existing Gantt Owners',()=>{
 it('shows a receipt availability transfer separately without assigning a synthetic resource',()=>{
  const transfer={...t,id:'receipt',operation_kind:'AVAILABILITY_TRANSFER',resource_id:null,start_at:a,end_at:a};
  const p=scheduleProjection({input,resources:[],calendars:[]} as any,[transfer],'');expect(p.rows[0].label).toBe('计划入库可用转换');expect(p.intervals[0]).toMatchObject({kind:'availability_transfer',start:Date.parse(a),end:Date.parse(a),protected:true});expect(transfer.resource_id).toBeNull();
 });
 it('uses exact pegged task identities and preserves shared order views without changing the task',()=>{
  const source={solve_run_id:'run'},order=(id:string)=>({demand_id:id,order_ref:id,operations:[{task:{id:t.id},source,record:{feasible:true}}],delivery:{status:'LATE',planned_quantity:'4',requested_quantity:'4',due_at:a,delay_seconds:60}});
  const model:any={input,view:{run:{solve_run_id:'run'},result:{result_hash:'hash'},order_evidence:{solve_run_id:'run',source_result_hash:'hash',orders:[order('D1'),order('D2')]}}};
  const p=orderScheduleProjection(model,[t]);expect(p.rows).toHaveLength(2);expect(p.intervals.map(x=>x.taskId)).toEqual([t.id,t.id]);expect(p.intervals.every(x=>x.feasibility==='FEASIBLE'&&x.orderDelaySeconds===60)).toBe(true);
  model.view.order_evidence.source_result_hash='foreign';expect(orderScheduleProjection(model,[t]).rows[0].id).toBe('unallocated');expect(t.start_at).toBe(a);
 });
 it('does not duplicate actual equipment occupancy into qualified alternative M2',()=>{
  const resources=[resource('P416-SEW-M1'),resource('P416-SEW-M2','EMPTY_CHECKED'),resource('P416-P1')],intervals=[{id:'machine',operation_task_id:t.id,resource_id:'P416-SEW-M1',start_at:a,end_at:b},{id:'person',operation_task_id:t.id,resource_id:'P416-P1',start_at:a,end_at:b}];
  const p=routingProjection(input,{resource_analysis:{resources,intervals}},[t],a,b,false,t.manufacture_id,'P416-SEW-M2');
  expect(p.intervals.map(x=>x.rowId)).toEqual(['P416-SEW-M1','P416-P1']);expect(p.rows.find(x=>x.id==='P416-SEW-M2')?.empty).toBe('合格但本次未采用');expect(p.intervals.every(x=>x.taskId===t.id&&x.protected)).toBe(true);
 });
 it('keeps actual load windows distinct from the operation window',()=>{
  const load={id:'machine',operation_task_id:t.id,resource_id:t.resource_id,start_at:'2026-11-04T09:00:00+08:00',end_at:b};
  const p=routingProjection(input,{resource_analysis:{resources:[resource(t.resource_id)],intervals:[load]}},[t],a,b,false);
  expect(p.intervals[0].start).toBe(Date.parse(load.start_at));expect(t.start_at).toBe(a);
 });
 it('does not fabricate loads for orphan, foreign, or missing native task identities',()=>{
  const p=routingProjection(input,{resource_analysis:{resources:[resource(t.resource_id)],intervals:[{id:'orphan',operation_task_id:'other:OP:1',resource_id:t.resource_id,start_at:a,end_at:b}]}},[t],a,b,false);
  expect(p.intervals).toEqual([]);
 });
 it('preserves absent calendar evidence on the continuous schedule',()=>{
  const p=scheduleProjection({input,resources:[{id:t.resource_id}],calendars:[]} as any,[t],'');expect(p.rows[0].calendar).toBeNull();expect(p.intervals[0].start).toBe(Date.parse(a));
 });
});
