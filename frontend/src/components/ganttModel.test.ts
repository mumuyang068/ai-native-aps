import {describe,it,expect} from 'vitest';
import {ganttScale,packGantt,calendarGaps,selectionIndices,ganttTimeIntent,type GanttInterval} from './ganttModel';
const interval=(id:string,start:number,end:number,taskId=id):GanttInterval=>({id,taskId,rowId:'machine',start,end,label:'同名检验',details:[]});
describe('native Gantt display projection',()=>{
 it('keeps a zero-duration availability transfer as a protected milestone without inventing occupancy',()=>{
  const x:GanttInterval={...interval('receipt',50,50),rowId:'availability-transfer',kind:'availability_transfer',protected:true};
  const p=packGantt([x],ganttScale(0,100),[0,100],500);expect(p.bars).toHaveLength(1);expect(p.bars[0]).toMatchObject({start:50,end:50,left:50,right:50,protected:true});expect(x.end-x.start).toBe(0);
 });
 it('turns a move and each resize into the same explicit time intent without mutating native intervals',()=>{
  const x=interval('a',3600000,7200000);expect(ganttTimeIntent(x,'move',60000)).toEqual({mode:'START_ANCHOR',start_at:'1970-01-01T01:01:00.000Z'});
  expect(ganttTimeIntent(x,'end',60000)).toEqual({mode:'WINDOW',start_at:'1970-01-01T01:00:00.000Z',end_at:'1970-01-01T02:01:00.000Z'});
  expect(ganttTimeIntent(x,'start',60000).end_at).toBe('1970-01-01T02:00:00.000Z');expect(x.start).toBe(3600000);
  expect(()=>ganttTimeIntent(x,'start',3600000)).toThrow();
 });
 it('keeps adjacent short tasks on the same resource lane at every zoom level',()=>{
  const xs=[interval('a',3600000,4800000),interval('b',4800000,6000000)];
  const p=packGantt(xs,ganttScale(0,86400000),[0,86400000],500);
  expect(p.bars.map(x=>x.end-x.start)).toEqual([1200000,1200000]);
  expect(p.bars.map(x=>x.lane)).toEqual([0,0]);expect(xs[0].end).toBe(4800000);
  expect(packGantt(xs,ganttScale(0,86400000),[0,86400000],200).bars.map(x=>x.lane)).toEqual([0,0]);
 });
 it('separates actual overlapping windows without adding lanes for hit targets',()=>{
  const xs=[interval('a',0,30),interval('b',20,40),interval('c',30,50)];
  const p=packGantt(xs,ganttScale(0,100),[0,100],200);
  expect(p.bars.map(x=>x.lane)).toEqual([0,1,0]);
  expect(p.lanes.get('machine')).toHaveLength(2);
 });
 it('exposes nearby short tasks for disambiguation instead of silently picking an overlapping hit target',()=>{
  const xs=[interval('a',10,11),interval('b',12,13),{...interval('other',12,13),rowId:'person'},interval('far',80,81)];
  const p=packGantt(xs,ganttScale(0,100),[0,100],200);
  expect(p.bars.find(x=>x.id==='a')).toMatchObject({lane:0,nearbyIds:['a','b']});
  expect(p.bars.find(x=>x.id==='far')).toMatchObject({lane:0,nearbyIds:['far']});
  const zoomed=packGantt(xs,ganttScale(0,100),[9,14],500);
  expect(zoomed.bars.find(x=>x.id==='a')).toMatchObject({lane:0,nearbyIds:['a']});
 });
 it('splits folded work periods without extending native occupancy into breaks',()=>{
  const s=ganttScale(0,100,[{start:10,end:30},{start:50,end:70}]);
  expect(s.pieces(20,60)).toEqual([{start:20,end:30,left:10,right:20},{start:50,end:60,left:20,right:30}]);
  expect(s.at(30)).toBe(60);expect(s.point(40)).toBe(20);
 });
 it('does not turn missing calendars into verified unavailability',()=>{
  expect(calendarGaps(null,0,100)).toBeNull();expect(calendarGaps([],0,100)).toEqual([{start:0,end:100}]);
  expect(calendarGaps([{start:20,end:40},{start:30,end:60}],0,100)).toEqual([{start:0,end:20},{start:60,end:100}]);
 });
 it('selects unique task identities across equipment and people, never by phase name',()=>{
  const xs=[interval('machine-a',0,10,'run:a'),interval('person-a',0,10,'run:a'),interval('machine-b',10,20,'run:b')];
  expect(selectionIndices(xs,'run:a')).toEqual([0,1]);expect(selectionIndices(xs,'other-run:a')).toEqual([]);
 });
 it('clips only presentation geometry, retains full native times for evidence',()=>{
  const p=packGantt([interval('a',0,100)],ganttScale(0,100),[20,40],500);
  expect(p.bars[0]).toMatchObject({start:0,end:100,left:20,right:40});
 });
 it('uses real epoch time in continuous mode so ECharts can choose calendar ticks',()=>{
  const s=ganttScale(1000,3000);expect(s.point(1500)).toBe(1500);expect(s.at(2000)).toBe(2000);expect(s.min).toBe(1000);expect(s.max).toBe(3000);
 });
});
