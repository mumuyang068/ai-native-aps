import {describe,it,expect} from 'vitest';
import {workingScale,routingOperationLabel} from './routingTimelineModel';
describe('calendar-backed resource display scale',()=>{
 const range=['2026-11-04T00:00:00Z','2026-11-05T00:00:00Z'];
 const windows=[{start_at:'2026-11-04T08:00:00Z',end_at:'2026-11-04T12:00:00Z'},{start_at:'2026-11-04T13:00:00Z',end_at:'2026-11-04T18:00:00Z'}];
 it('does not double count shared calendars or pretend lunch is work',()=>{const s=workingScale([...windows,...windows],range[0],range[1]);expect(s.total/3600000).toBe(9);expect(s.segments).toHaveLength(2);expect(s.point(Date.parse('2026-11-04T12:30:00Z'))).toBeCloseTo(400/9)});
 it('splits actual intervals at calendar breaks without lengthening them',()=>{const s=workingScale(windows,...range as [string,string]);const pieces=s.pieces('2026-11-04T11:00:00Z','2026-11-04T14:00:00Z');expect(pieces).toHaveLength(2);expect(pieces.reduce((n,p)=>n+p.width,0)).toBeCloseTo(200/9)});
 it('preserves real zero vs missing scale instead of inventing periods',()=>{const s=workingScale([],range[0],range[1]);expect(s.total).toBe(0);expect(s.pieces(range[0],range[1])).toEqual([])});
 it('uses source sequence and code without inventing a translated operation name',()=>{expect(routingOperationLabel({routing_revisions:[{operations:[{id:'unfamiliar-code',sequence:42}]}]},'unfamiliar-code')).toBe('工序 42 · unfamiliar-code');expect(routingOperationLabel({},'foreign')).toBe('foreign')});
});
