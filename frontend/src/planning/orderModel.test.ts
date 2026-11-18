import {expect,it} from 'vitest';
import {packLanes,selectionFor,delayLabel} from './orderModel';
it('shows a delay in exact business time instead of repeating fractional days',()=>{expect(delayLabel(9300)).toBe('2 小时 35 分钟');expect(delayLabel(387600)).toBe('4 天 11 小时 40 分钟')});
it('uses the same lane for adjacent intervals but separates overlaps without losing task IDs',()=>{
 const rows=[{id:'A',start_at:'2026-01-01T09:00Z',end_at:'2026-01-01T10:00Z'},{id:'B',start_at:'2026-01-01T10:00Z',end_at:'2026-01-01T11:00Z'},{id:'C',start_at:'2026-01-01T09:30Z',end_at:'2026-01-01T10:30Z'}];
 const p=packLanes(rows);expect(p.count).toBe(2);expect(p.lanes.get('A')).toBe(p.lanes.get('B'));expect(p.lanes.get('A')).not.toBe(p.lanes.get('C'));expect(p.lanes.size).toBe(3);
});
it('never carries an object selection into another plan or scenario',()=>{
 const s={key:'PLAN1|baseline',kind:'order' as const,id:'D1'};
 expect(selectionFor(s,'PLAN1|baseline')).toEqual(s);expect(selectionFor(s,'PLAN1|draft')).toBeNull();expect(selectionFor(s,'PLAN2|baseline')).toBeNull();
});
