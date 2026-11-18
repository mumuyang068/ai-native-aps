import {describe,it,expect} from 'vitest';
import {fulfillmentGroupId,fulfillmentGroups,fulfillmentChart,matchingBaseline,fulfillmentAmount,fulfillmentVisibleRows,fulfillmentRangeLabel} from './demandFulfillmentModel';
import {reportReturnSearch} from './reportModel';
const row={item_id:'SEWN',location_id:'FACTORY',uom:'piece',endpoint:'缝后阶段',start_at:'2026-11-05T00:00:00+08:00',end_at:'2026-11-06T00:00:00+08:00',due_quantity:'20',planned_quantity:'12',overdue_unfulfilled:'8',demand_ids:['ONE','TWO']};
describe('fulfillment view uses authenticated bucket quantities',()=>{
 it('keeps units, endpoints and locations in independent chart groups',()=>{
  expect(fulfillmentGroups([row,{...row,uom:'m'},{...row,endpoint:'FG'},{...row,location_id:'SHOP'}])).toHaveLength(4);
 });
 it('passes real quantities with stable bucket identity to chart selection',()=>{
  const o=fulfillmentChart([row],[], 'Asia/Shanghai');const series=o.series as any[];
  expect(series.map(s=>s.data[0].value)).toEqual([20,12,8]);expect(series[0].data[0].eventId).toBeTruthy();
  expect((o.xAxis as any).data[0]).toContain('11-05');
 });
 it('preserves unknown and does not turn missing delivery evidence into zero',()=>{
  const o=fulfillmentChart([{...row,planned_quantity:null,overdue_unfulfilled:null}],[],'Asia/Shanghai');
  expect((o.series as any[])[1].data[0].value).toBeNull();expect(fulfillmentAmount(null,'piece')).toBe('未核验');
 });
 it('refuses mixed groups rather than summing different units',()=>{
  expect(()=>fulfillmentChart([row,{...row,uom:'m'}],[],'Asia/Shanghai')).toThrow();
 });
 it('requires baseline to belong to the same plan version and timezone',()=>{
  const scope={planId:'P',versionId:'D',runId:'NEW',target:'C'};
  const base={identity:{plan_id:'P',data_version_id:'D',run_id:'BASE',target:'baseline'},timezone:'Asia/Shanghai',demand:{rows:[row]}};
  expect(matchingBaseline(base,scope,'BASE','Asia/Shanghai')).toEqual([row]);
  expect(()=>matchingBaseline({...base,identity:{...base.identity,plan_id:'OTHER'}},scope,'BASE','Asia/Shanghai')).toThrow();
  expect(()=>matchingBaseline(base,scope,'WRONG','Asia/Shanghai')).toThrow();
 });
 it('retains the original bucket filters through investigation and comparison returns',()=>{
  const p=new URLSearchParams({return_report:'demand',report_group:fulfillmentGroupId(row),report_compare:'1',report_line_query:'TWO',report_row:'BUCKET',report_scope:'SCOPE'});
  const back=reportReturnSearch(p,'baseline');expect(back.get('report_group')).toBe(p.get('report_group'));expect(back.get('report_line_query')).toBe('TWO');expect(back.get('report_compare')).toBe('1');
 });
 it('keeps an early baseline delivery date when candidate has no activity that day',()=>{
  const early={...row,start_at:'2026-11-03T00:00:00+08:00',end_at:'2026-11-04T00:00:00+08:00',due_quantity:'0',planned_quantity:'0',overdue_unfulfilled:'0'};
  const baseline=[{...early,planned_quantity:'10'}, {...row,planned_quantity:'0',overdue_unfulfilled:'0'}];
  const visible=fulfillmentVisibleRows([early,row],baseline,'',true);
  expect(visible).toHaveLength(2);expect(visible[0].planned_quantity).toBe('0');expect(visible[0].baseline_planned_quantity).toBe('10');
  expect(((fulfillmentChart(visible,baseline,'Asia/Shanghai').series as any[])[3].data).map((d:{value:number})=>d.value)).toEqual([10,0]);
 });
 it('overrides the iX category slider percentage handle with a finite pixel size',()=>{
  const o=fulfillmentChart([row],[],'Asia/Shanghai');const slider=(o.dataZoom as any[]).find(z=>z.type==='slider');
  expect(slider.handleSize).toBe(32);expect(slider.height).toBe(28);
 });
});

describe('compact fulfillment presentation retains business truth',()=>{
 it('labels the last known backlog at its exact event without adding a date',()=>{
  const o=fulfillmentChart([row],[],'Asia/Shanghai');const point=(o.series as any[])[2].data[0];
  expect(point.label.formatter).toBe('期末逾期未满足 8 件');expect(point.eventId).toBeTruthy();expect(point.value).toBe(8);
 });
 it('does not label unknown backlog as zero',()=>{
  const o=fulfillmentChart([{...row,overdue_unfulfilled:null}],[],'Asia/Shanghai');
  expect((o.series as any[])[2].data[0].label).toBeUndefined();
 });
 it('uses an inclusive day label only for whole-day half-open ranges',()=>{
  expect(fulfillmentRangeLabel({start_at:'2026-11-01T00:00:00+08:00',end_at:'2026-12-01T00:00:00+08:00'},'Asia/Shanghai')).toBe('2026/11/01—11/30');
  expect(fulfillmentRangeLabel({start_at:'2026-11-01T09:00:00+08:00',end_at:'2026-11-02T12:00:00+08:00'},'Asia/Shanghai')).toBe('2026-11-01 09:00—2026-11-02 12:00');
 });
});
