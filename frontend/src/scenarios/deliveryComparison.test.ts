import {describe,it,expect} from 'vitest';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {affectedDeliveryCount,DeliveryDifference,DeliverySnapshot,comparisonDemandLabel,type DeliveryChange} from './deliveryComparison';

const date='2026-11-05T18:00:00+08:00';
const full={planned_quantity:'10',unfulfilled_quantity:'0',status:'ON_TIME',planned_delivery_at:date,due_at:date};
const change=(candidate:Record<string,unknown>={},extra:Partial<DeliveryChange>={}):DeliveryChange=>({
 demand_id:'D1',kind:'EXISTING',base:{...full},candidate:{...full,...candidate},planned_delivery_shift_seconds:0,...extra,
});
const text=(row:DeliveryChange)=>renderToStaticMarkup(createElement(DeliveryDifference,{row,uom:'piece'}));
const snapshot=(value:DeliveryChange['candidate'])=>renderToStaticMarkup(createElement(DeliverySnapshot,{value,uom:'piece',timezone:'Asia/Shanghai'}));

describe('delivery impact from actual row dimensions',()=>{
 it('A counts a date-only shift and displays its direction',()=>{
  const row=change({planned_delivery_at:'2026-11-06T18:00:00+08:00'},{planned_delivery_shift_seconds:86400});
  expect(affectedDeliveryCount([row])).toBe(1);expect(text(row)).toContain('预计推迟 1 天');
 });
 it('B counts quantity deterioration at the same date, with scoped date wording',()=>{
  const row=change({planned_quantity:'2',unfulfilled_quantity:'8',status:'SHORT'});
  expect(affectedDeliveryCount([row])).toBe(1);
  expect(text(row)).toContain('已排定部分日期未变');expect(text(row)).toContain('计划满足量10→2件');
  expect(text(row)).toContain('新增欠量8件');expect(text(row)).not.toContain('预计交付不变');
  expect(snapshot(row.candidate)).toContain('已排定部分预计 2026-11-05 18:00');
  expect(snapshot(row.candidate)).toContain('未满足的8件暂无计划交付日期');
 });
 it('C counts valid business status changes even without quantity or date shifts',()=>{
  const row=change({status:'LATE'});expect(affectedDeliveryCount([row])).toBe(1);
  expect(text(row)).toContain('状态：预计按期→存在延期');
 });
 it('D leaves unchanged rows and equivalent numeric/date formatting unchanged',()=>{
  expect(affectedDeliveryCount([change(),change({planned_quantity:'10.000',planned_delivery_at:'2026-11-05T10:00:00Z'},{demand_id:'D2'})])).toBe(0);
 });
 it('does not count a due-date-only change through the broader backend changed flag',()=>{
  const row=change({due_at:'2026-11-06T18:00:00+08:00'},{changed:true});
  expect(affectedDeliveryCount([row])).toBe(0);
 });
 it('E counts one stable identity once when multiple fields change or a row repeats',()=>{
  const row=change({planned_quantity:'2',unfulfilled_quantity:'8',status:'SHORT',planned_delivery_at:'2026-11-06T18:00:00+08:00'});
  expect(affectedDeliveryCount([row,row])).toBe(1);
 });
 it('counts both rows when quantity changes offset each other in aggregate',()=>{
  const rows=[change({planned_quantity:'2',unfulfilled_quantity:'8'}),change({planned_quantity:'18',unfulfilled_quantity:'0'},{demand_id:'D2'})];
  expect(affectedDeliveryCount(rows)).toBe(2);
 });
 it('counts explicit added and removed rows',()=>{
  expect(affectedDeliveryCount([change({}, {kind:'ADDED',base:null}),change({}, {demand_id:'D2',kind:'REMOVED',candidate:null})])).toBe(2);
  expect(text(change({}, {kind:'REMOVED',candidate:null}))).toContain('当前方案中已无此需求行');
 });
 it('does not trust a false flag to hide a confirmed quantity difference',()=>{
  expect(affectedDeliveryCount([change({planned_quantity:'2'},{changed:false})])).toBe(1);
 });
 it('F does not count two unknown dates as a change just because delta is null',()=>{
  const empty={planned_quantity:'0',unfulfilled_quantity:'10',status:'UNPLANNED',planned_delivery_at:null};
  const row=change({}, {base:empty,candidate:{...empty},planned_delivery_shift_seconds:null});
  expect(affectedDeliveryCount([row])).toBe(0);expect(text(row)).toContain('尚无已排定日期');
  expect(snapshot(row.candidate)).toContain('未排定');expect(snapshot(row.candidate)).not.toContain('预计推迟');
 });
 it('keeps quantity evidence when both dates are unknown',()=>{
  const row=change({planned_quantity:'2',unfulfilled_quantity:'8',status:'SHORT',planned_delivery_at:null},
   {base:{...full,planned_delivery_at:null},planned_delivery_shift_seconds:null});
  expect(affectedDeliveryCount([row])).toBe(1);expect(text(row)).toContain('新增欠量8件');
  expect(text(row)).not.toContain('预计交付不变');
 });
 it('handles missing fields and invalid dates without treating them as zeros or throwing',()=>{
  const row=change({}, {base:{planned_quantity:'10'},candidate:{planned_quantity:'2',planned_delivery_at:'not-a-date'}});
  expect(affectedDeliveryCount([row])).toBe(1);expect(()=>snapshot(row.candidate)).not.toThrow();
  expect(snapshot(row.candidate)).toContain('日期未核验');expect(snapshot(row.candidate)).toContain('欠量 未提供');
  expect(text(row)).not.toContain('新增欠量');
 });
 it('does not infer numeric zero from missing or null quantities',()=>{
  const row=change({}, {base:{planned_quantity:null},candidate:{planned_quantity:'0'},planned_delivery_shift_seconds:null});
  expect(affectedDeliveryCount([row])).toBe(0);expect(snapshot(row.base)).toContain('计划满足 未提供');
 });
 it('distinguishes actually unplanned from newly scheduled and missing dates',()=>{
  const row=change({}, {candidate:{...full,planned_quantity:'0',unfulfilled_quantity:'10',status:'UNPLANNED',planned_delivery_at:null},planned_delivery_shift_seconds:null});
  expect(affectedDeliveryCount([row])).toBe(1);expect(text(row)).toContain('由已排定变为未排定');
  const newly=change({}, {base:row.candidate,planned_delivery_shift_seconds:null});
  expect(text(newly)).toContain('由未排定变为已排定');
 });
 it('G reproduces the captured V1-01 row values, counting only the second row',()=>{
  // Business fields captured from V1-01 product-comparison.json; original run/fixture is not rewritten.
  const rows=[change({}, {demand_id:'D16-ORDER-1-L1',changed:false}),
   change({planned_quantity:'2',unfulfilled_quantity:'8',status:'SHORT'},{demand_id:'D16-ORDER-2-L1',changed:true})];
  expect(affectedDeliveryCount(rows)).toBe(1);expect(affectedDeliveryCount([rows[0]])).toBe(0);
  expect(text(rows[1])).toContain('计划满足量10→2件');expect(snapshot(rows[1].candidate)).toContain('计划满足 2 件');
 });
});

it("keeps explicit Net and non-executive inquiry identities separate from Firm orders",()=>{
 expect(comparisonDemandLabel("N",[{net_forecast_demands:[{id:"N"}]}])).toContain("净预测");
 expect(comparisonDemandLabel("Q",[{inquiry_summary:{demands:[{id:"Q"}]}}])).toContain("非正式订单");
 expect(comparisonDemandLabel("F",[{order_summary:{orders:[{id:"F"}]}}])).toBe("客户订单需求");
});
