import {describe,it,expect} from 'vitest';
import {compositeRows,verifyAnalysis,unavailabilityRefusal,loadAnalysisFitRange,filteredResourceLoads} from './compositeModel';
import type {Dict} from '../api/contracts';
import type {TaskRecord} from './planModel';

const start='2026-11-05T01:00:00Z',end='2026-11-05T04:00:00Z';
const tasks=[{id:'R:OP:1',operation_id:'SEW',name:'缝制',itemId:'FG',start_at:start,end_at:end},{id:'R:OP:2',operation_id:'CUT',name:'裁剪',itemId:'WIP',start_at:start,end_at:end}] as TaskRecord[];
function data():Dict{return {identity:{plan_id:'P',data_version_id:'V',run_id:'R',target:'baseline',profile:'machine-qualified-operator-v1'},resource_analysis:{resources:[{id:'M1',role:'MACHINE'},{id:'P1',role:'OPERATOR'},{id:'POOL',role:'OPERATOR_POOL'},{id:'CUT',role:'MACHINE',capacity_model:'WINDOW_QUANTITY'}],excluded_resources:[{id:'CUT'}],intervals:[{id:'LOAD-M1',resource_id:'M1',operation_task_id:'R:OP:1',start_at:start,end_at:end},{id:'LOAD-P1',resource_id:'P1',operation_task_id:'R:OP:1',start_at:start,end_at:end},{id:'OTHER-RUN',resource_id:'M1',operation_task_id:'OTHER:OP:1',start_at:start,end_at:end}]}}}
const range=[Date.parse(start),Date.parse(end)] as const;
describe('one time scale and exact native operation association',()=>{
 it('shares one exact operation with both the machine and named person occupancy',()=>{
  const rows=compositeRows(tasks,data(),...range,'缝制','');expect(rows.operations.map(t=>t.id)).toEqual(['R:OP:1']);expect(rows.loads.map(r=>r.id)).toEqual(['LOAD-M1','LOAD-P1']);
 });
 it('never links a similarly timed foreign run through name, material or time',()=>{
  const rows=compositeRows(tasks,data(),...range,'FG','');expect(rows.loads.some(r=>r.id==='OTHER-RUN')).toBe(false);
 });
 it('keeps actual orphan native records visible without fabricating an operation link',()=>{
  expect(compositeRows(tasks,data(),...range,'','').loads.some(r=>r.id==='OTHER-RUN')).toBe(true);
 });
 it('preserves quantity-window operation bars while excluding quantity capacity and personnel pools',()=>{
  const rows=compositeRows(tasks,data(),...range,'','');expect(rows.operations).toHaveLength(2);expect(rows.resources.map(r=>r.id)).toEqual(['M1','P1']);
 });
 it('filters operations by exact selected resource occupancy, with half-open time boundaries',()=>{
  expect(compositeRows(tasks,data(),...range,'','P1').operations.map(t=>t.id)).toEqual(['R:OP:1']);expect(compositeRows(tasks,data(),Date.parse(end),Date.parse(end)+3600000,'','').operations).toEqual([]);
 });
 it('does not inherit resource certification from an arbitrary profile',()=>{
  const v=data();v.identity.profile='finished-garment-confirmed-v1';expect(compositeRows(tasks,v,...range,'','').resources).toEqual([]);expect(compositeRows(tasks,v,...range,'','').loads).toEqual([]);
 });
 it('keeps SEW filtering identical in composite, native resource lanes and event rows when QC/PACK share a run',()=>{
  const operations=['SEW','QC','PACK'].map((operation_id,i)=>({id:'R:OP:'+i,operation_id,name:operation_id,itemId:'FG',resource_id:'M1',start_at:start,end_at:end})) as TaskRecord[];
  const value=data();value.resource_analysis.intervals=operations.flatMap(task=>['M1','P1'].map(resource_id=>({id:task.id+':'+resource_id,operation_task_id:task.id,operation_id:task.operation_id,resource_id,start_at:start,end_at:end})));
  const tableRows=filteredResourceLoads(operations,value.resource_analysis.intervals,...range,'SEW',''),composite=compositeRows(operations,value,...range,'SEW','');
  const nativeLanes=value.resource_analysis.resources.flatMap((r:Dict)=>tableRows.filter(load=>load.resource_id===r.id));
  expect(tableRows.map(r=>r.operation_id)).toEqual(['SEW','SEW']);expect(nativeLanes.map((r:Dict)=>r.id)).toEqual(composite.loads.map(r=>r.id));expect(nativeLanes.every((r:Dict)=>tableRows.some(row=>row.id===r.id))).toBe(true);
  expect(filteredResourceLoads(operations,value.resource_analysis.intervals,...range,'SEW','P1').map(r=>r.id)).toEqual(['R:OP:0:P1']);
 });
 it.each(['plan_id','data_version_id','run_id','target'])('refuses another %s identity',field=>{
  const v=data();v.identity[field]='other';expect(()=>verifyAnalysis(v,{planId:'P',versionId:'V',runId:'R',target:'baseline'})).toThrow('不属于');
 });
});
describe('professional inspector obeys the existing action contract',()=>{
 const resource={id:'P1',role:'OPERATOR',event_status:'PRESENT',calendar_id:'CAL-P1',availability:[{start_at:start,end_at:end}]};
 const legal={profile:'machine-qualified-operator-v1',target:'baseline',runId:'R',baselineRunId:'R',resource,start,end};
 it('offers named-person unavailability only for the current certified baseline and existing calendar',()=>expect(unavailabilityRefusal(legal)).toBe(''));
 it('explains refusals for candidates, unknown sources, pools and unsupported profiles',()=>{
  expect(unavailabilityRefusal({...legal,target:'DRAFT'})).toContain('原始基线');expect(unavailabilityRefusal({...legal,runId:'old'})).toContain('原始基线');
  expect(unavailabilityRefusal({...legal,profile:'enterprise-accessory-supply-v1'})).toContain('仅扣减');expect(unavailabilityRefusal({...legal,resource:{...resource,role:'OPERATOR_POOL'}})).toContain('人员池');expect(unavailabilityRefusal({...legal,resource:{...resource,event_status:'NOT_COLLECTED'}})).toContain('未取得');expect(unavailabilityRefusal({...legal,resource:{...resource,availability:[]}})).toContain('可用时段');
 });
});
describe('fit reads the full same-run source rather than a previously clipped interval list',()=>{
 const scope={planId:'P',versionId:'V',runId:'R',target:'baseline'};
 function full(){const value=data();value.planning_at='2026-11-05T00:00:00Z';value.horizon_end='2026-11-06T00:00:00Z';value.window={start_at:value.planning_at,end_at:value.horizon_end};value.resource_analysis.status='PRESENT';value.resource_analysis.validation={status:'PASS'};value.resource_analysis.intervals.push({id:'LATE',resource_id:'M1',operation_task_id:'R:OP:3',start_at:'2026-11-05T06:40:00Z',end_at:'2026-11-05T08:00:00Z'});return value}
 it('fetches without any previous range and fits both early and late operations',async()=>{
  let requested='';const range=await loadAnalysisFitRange(scope,'resources',async url=>{requested=url;return full()});
  const query=new URL('http://local'+requested).searchParams;expect(query.get('run_id')).toBe('R');expect(query.get('target')).toBe('baseline');expect(query.has('start')).toBe(false);expect(query.has('end')).toBe(false);
  expect(Date.parse(range.start_at)).toBeLessThan(Date.parse(start));expect(Date.parse(range.end_at)).toBeGreaterThan(Date.parse('2026-11-05T08:00:00Z'));
 });
 it('rejects a clipped or foreign-run response instead of calling it all events',async()=>{
  const clipped=full();clipped.window.start_at='2026-11-05T07:00:00Z';await expect(loadAnalysisFitRange(scope,'resources',async()=>clipped)).rejects.toThrow('完整规划期');
  const foreign=full();foreign.identity.run_id='OTHER';await expect(loadAnalysisFitRange(scope,'resources',async()=>foreign)).rejects.toThrow('不属于');
 });
});
