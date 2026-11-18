import {describe,expect,it} from 'vitest';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import type {Dict} from '../api/contracts';
import {calendarWindows,resourceCalendarRows} from './resourceCalendarModel';
import {ResourceCalendar} from './ResourceCalendar';

const start='2026-11-02T09:00:00+08:00',end='2026-11-02T12:00:00+08:00';
const sha='a'.repeat(64);
const work=[{start_at:'2026-11-02T09:40:00+08:00',end_at:'2026-11-02T11:00:00+08:00'}];
function resource():Dict{return {id:'P1',role:'OPERATOR',capacity:'1',capacity_uom:'parallel_occupancy',calendar_id:'CAL-P1',calendar_status:'PRESENT',calendar_basis:'DECLARED_CANONICAL_INPUT',calendar_sources:[{artifact:'canonical_input.json',pointer:'/calendars/0',sha256:sha}],availability:work,event_status:'PRESENT'}}
const loads=[{id:'L1',resource_id:'P1',operation_id:'PROCESS-1',operation_task_id:'R:OP:1',start_at:'2026-11-02T09:40:00+08:00',end_at:'2026-11-02T10:20:00+08:00',load_quantity:'1'},
 {id:'L2',resource_id:'P1',operation_id:'PROCESS-2',operation_task_id:'R:OP:2',start_at:'2026-11-02T10:20:00+08:00',end_at:'2026-11-02T11:00:00+08:00',load_quantity:'1'}];
function data(){return {identity:{profile:'machine-qualified-operator-v1'},resource_analysis:{status:'PRESENT',validation:{status:'PASS'},resources:[resource()],intervals:loads,excluded_resources:[]}}}

describe('declared calendar and authenticated native load remain separate',()=>{
 it('clips declared work and complements it only inside the selected analysis window',()=>{
  const windows=calendarWindows(resource(),start,end);expect(windows.status).toBe('PRESENT');
  expect(windows.segments.map(s=>[s.kind,s.start_at,s.end_at])).toEqual([
   ['NONWORKING',new Date(start).toISOString(),new Date(work[0].start_at).toISOString()],
   ['WORKING',new Date(work[0].start_at).toISOString(),new Date(work[0].end_at).toISOString()],
   ['NONWORKING',new Date(work[0].end_at).toISOString(),new Date(end).toISOString()]]);
 });
 it('merges overlapping and adjacent declared windows without double counting them',()=>{
  const r=resource();r.availability=[...work,{start_at:'2026-11-02T10:40:00+08:00',end_at:'2026-11-02T11:20:00+08:00'},{start_at:'2026-11-02T11:20:00+08:00',end_at:end}];
  const out=calendarWindows(r,start,end);expect(out.segments.map(s=>s.kind)).toEqual(['NONWORKING','WORKING']);
  expect(out.segments[1].end_at).toBe(new Date(end).toISOString());
 });
 it.each(['NOT_COLLECTED','INVALID_EVIDENCE','UNREADABLE','UNSUPPORTED'])('does not turn %s calendar evidence into a nonworking or 24-hour work declaration',status=>{
  const out=calendarWindows({...resource(),calendar_status:status},start,end);expect(out.status).toBe(status);expect(out.segments).toEqual([]);
 });
 it('an explicitly empty checked calendar is different from a missing source',()=>{
  expect(calendarWindows({...resource(),calendar_status:'EMPTY_CHECKED',availability:[]},start,end).segments.map(s=>s.kind)).toEqual(['NONWORKING']);
  expect(calendarWindows({...resource(),calendar_sources:[]},start,end).status).toBe('NOT_COLLECTED');
 });
 it('does not trust malformed calendar dates, an inverted range, or a missing SHA',()=>{
  expect(calendarWindows({...resource(),availability:[{start_at:'bad',end_at:end}]},start,end).status).toBe('INVALID_EVIDENCE');
  expect(calendarWindows(resource(),end,start).segments).toEqual([]);
  expect(calendarWindows({...resource(),calendar_sources:[{artifact:'canonical_input.json',pointer:'/calendars/0'}]},start,end).status).toBe('NOT_COLLECTED');
 });
 it('uses only the supplied actual native loads and treats 10:20 handoff as half-open',()=>{
  const rows=resourceCalendarRows(data(),loads,start,end,'');expect(rows).toHaveLength(1);expect(rows[0].loads.map(l=>l.id)).toEqual(['L1','L2']);
  expect(resourceCalendarRows(data(),loads,loads[1].start_at,end,'')[0].loads.map(l=>l.id)).toEqual(['L2']);
  expect(resourceCalendarRows(data(),[],start,end,'')[0].loads).toEqual([]);
 });
 it('keeps pools and quantity-window resources out of named-person/continuous calendar lanes',()=>{
  const d=data();d.resource_analysis.resources.push({...resource(),id:'POOL',role:'OPERATOR_POOL'},{...resource(),id:'CUT',capacity_model:'WINDOW_QUANTITY'});
  expect(resourceCalendarRows(d,loads,start,end,'').map(r=>r.resource.id)).toEqual(['P1']);
  d.identity.profile='two-level-wip-v1';expect(resourceCalendarRows(d,loads,start,end,'')).toEqual([]);
 });
 it('does not hide known FAIL or relabel missing native load as empty',()=>{
  const d=data();d.resource_analysis.validation.status='FAIL';d.resource_analysis.resources[0].event_status='NOT_COLLECTED';
  const rows=resourceCalendarRows(d,[],start,end,'');expect(rows[0].validationStatus).toBe('FAIL');expect(rows[0].resource.event_status).toBe('NOT_COLLECTED');
 });
});

describe('resource/calendar workbench presentation',()=>{
 function render(d=data()){return renderToStaticMarkup(createElement(ResourceCalendar,{rows:resourceCalendarRows(d,loads,start,end,''),start,end,timezone:'Asia/Shanghai',selectedEvent:'L2',onResource:()=>{},onEvent:()=>{},onWindow:()=>{}}))}
 it('shows exact resource identity, declared calendar source and actual load without an invented efficiency score',()=>{
  const html=render();expect(html).toContain('P1');expect(html).toContain('具名操作员');expect(html).toContain('CAL-P1');expect(html).toContain('输入声明日历');expect(html).toContain('原生实际占用');expect(html).toContain('并行容量 1');expect(html).toContain(sha);expect(html).toContain('PROCESS-2');expect(html).not.toContain('利用率');
 });
 it('makes calendar gaps and missing evidence explicit rather than drawing fake work time',()=>{
  const d=data();d.resource_analysis.resources[0].calendar_status='NOT_COLLECTED';const html=render(d);expect(html).toContain('日历未采集');expect(html).not.toContain('data-calendar-kind="NONWORKING"');expect(html).not.toContain('data-calendar-kind="WORKING"');
 });
});
