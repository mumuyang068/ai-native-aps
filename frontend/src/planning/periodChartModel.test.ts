import {describe,it,expect} from 'vitest';
import {periodChartOption} from './periodChartModel';
import {fulfillmentRowsForKind} from './demandFulfillmentModel';

describe('professional period presentation',()=>{
 const row={resource_id:'P1',role:'OPERATOR',start_at:'2026-11-02T00:00:00+08:00',end_at:'2026-11-03T00:00:00+08:00',available_hours:'8',load_hours:null};
 it('keeps unknown load absent while exposing native capacity and exact bucket selection',()=>{
  const o=periodChartOption([row],'resource','Asia/Shanghai') as any;
  expect(o.series[0].data[0].value).toBeNull();expect(o.series[1].data[0].value).toBe(8);
  expect(o.series[0].data[0].eventId).toContain('P1');
 });
 it('marks only evidenced overload without turning unknown capacity into zero or changing reported quantities',()=>{
  const o=periodChartOption([{...row,load_hours:'10'},{...row,load_hours:'10',available_hours:null}],'resource','Asia/Shanghai') as any;
  expect(o.series[0].data[0].itemStyle.color).toBe('#b73535');expect(o.series[0].data[1].itemStyle).toBeUndefined();expect(o.series[0].data.map((d:any)=>d.value)).toEqual([10,10]);expect(o.series[1].lineStyle.type).toBe('dashed');
 });
 it('rejects mixed units or resources instead of plotting a false aggregate',()=>{
  expect(()=>periodChartOption([row,{...row,resource_id:'M2'}],'resource','Asia/Shanghai')).toThrow();
 });
 it('keeps a negative inventory ending signed in the native tooltip',()=>{
  const o=periodChartOption([{item_id:'I',location_id:'L',uom:'piece',ending:'-4'}],'inventory','Asia/Shanghai') as any;
  expect(o.tooltip.valueFormatter(-4)).toContain('-4');
 });
 it('uses owner totals for separate Firm Net Inquiry without discarding completed prior lines',()=>{
  const rows=[{item_id:'I',location_id:'L',uom:'件',endpoint:'I',kind_totals:{INQUIRY:{cumulative_due_quantity:'1',cumulative_planned_quantity:'1',planned_quantity:'0',due_quantity:'0',overdue_unfulfilled:'0',unscheduled_quantity:'0'}},demand_components:[]}];
  const [selected]=fulfillmentRowsForKind(rows,'INQUIRY',[]);expect(selected.cumulative_planned_quantity).toBe('1');expect(selected.unscheduled_quantity).toBe('0');
 });
});
