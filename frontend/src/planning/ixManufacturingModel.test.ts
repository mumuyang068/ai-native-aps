import {describe,it,expect} from 'vitest';
import input from '../../../fixtures/p416-routing-capacity.json';
import {operationAlternatives} from './routingCapacityModel';
import {workingScale,workingViewport} from './routingTimelineModel';

describe('iX manufacturing display preserves the selected business object',()=>{
 it('does not borrow SEW machine qualification for independent CUT or inspection',()=>{
  const operations=input.routing_revisions.flatMap(r=>r.operations);
  const sew=operations.find(o=>o.resource_id==='P416-SEW-POOL')??operations.find(o=>o.machine_skill_id);
  expect(sew).toBeDefined();expect(operationAlternatives(input,sew!.id).map(x=>x.resource.id)).toEqual(['P416-SEW-M1','P416-SEW-M2','P416-SEW-M3']);
  for(const op of operations.filter(o=>o.id!==sew!.id))expect(operationAlternatives(input,op.id)).toEqual([]);
  expect(operationAlternatives(input,'foreign-operation')).toEqual([]);
 });
 const windows=[{start_at:'2026-11-04T08:00:00Z',end_at:'2026-11-04T12:00:00Z'},{start_at:'2026-11-04T13:00:00Z',end_at:'2026-11-04T18:00:00Z'}];
 const start='2026-11-04T08:00:00Z',end='2026-11-04T18:00:00Z';
 it('zooms around a work-time anchor and excludes the lunch break',()=>{
  const zoom=workingViewport(windows,start,end,start,end,.5,.5)!;
  expect(workingScale(windows,zoom.start,zoom.end).total).toBe(4.5*3600000);
  expect(zoom.start).toBe('2026-11-04T10:15:00.000Z');expect(zoom.end).toBe('2026-11-04T15:45:00.000Z');
 });
 it('pans in the same declared work windows without inventing calendar capacity',()=>{
  const zoom=workingViewport(windows,start,end,start,end,.5,0)!;
  const pan=workingViewport(windows,start,end,zoom.start,zoom.end,1,.5,1)!;
  expect(pan.end).toBe('2026-11-04T18:00:00.000Z');expect(workingScale(windows,pan.start,pan.end).total).toBe(4.5*3600000);
 });
 it('clamps the view to the declared planning horizon',()=>{const v=workingViewport(windows,start,end,start,end,3)!;expect(v.start).toBe('2026-11-04T08:00:00.000Z');expect(v.end).toBe('2026-11-04T18:00:00.000Z')});
 it('does not make up a range when calendar evidence is absent',()=>{expect(workingViewport([],start,end,start,end,.5)).toBeNull();expect(workingViewport(windows,start,end,start,end,NaN)).toBeNull()});
});
