import {describe,it,expect} from 'vitest';
import type {PlanView} from '../api/contracts';
import {chartNumber,inventoryChartData,inventoryChartOption,networkDemandChartRows,demandComparisonOption} from './ixChartModel';

const start=Date.parse('2026-11-02T08:00:00+08:00'),end=Date.parse('2026-11-06T18:00:00+08:00'),at='2026-11-05T18:00:00+08:00';
const inventory={item_id:'WIP',location_id:'FACTORY',uom:'set',opening:'12',complete:true,verified_through:new Date(end).toISOString(),events:[{id:'output',at,kind:'MANUFACTURE_OUTPUT',native_sequence:8,balance:'32',reference:'MO-A'},{id:'consume1',at,kind:'MANUFACTURE_CONSUMPTION',native_sequence:9,balance:'22',reference:'MO-B'},{id:'consume2',at,kind:'MANUFACTURE_CONSUMPTION',native_sequence:10,balance:'12',reference:'MO-C'}]};
const view=(plan='p',version='v')=>({context:{enterprise_source:{plan_id:plan,data_version_id:version}},result:{deliveries:[{demand_id:'D1',uom:'piece',planned_quantity:'10',unfulfilled_quantity:'0'},{demand_id:'D2',uom:'piece',planned_quantity:'2',unfulfilled_quantity:'8'}]}} as unknown as PlanView);

describe('mature chart data keeps APS business evidence semantics',()=>{
 it('keeps a single net opening and all same-time native events in source order',()=>{
  const rows=inventoryChartData(inventory,start,end);
  expect(rows[0].value).toEqual([start,12]);expect(rows.filter(p=>p.eventId).map(p=>[p.eventId,p.value[1],p.nativeSequence])).toEqual([['output',32,8],['consume1',22,9],['consume2',12,10]]);
  expect(new Set(rows.filter(p=>p.eventId).map(p=>p.value[0])).size).toBe(1);expect(rows.every(p=>p.value[0]>=start)).toBe(true);expect(rows.at(-1)?.value).toEqual([end,12]);
 });
 it('does not use a boundary-time supply as the pre-event opening',()=>{
  const rows=inventoryChartData({...inventory,events:[{id:'receipt',at:new Date(start).toISOString(),balance:'40'}]},start,end);
  expect(rows[0].value[1]).toBe(12);expect(rows[1]).toMatchObject({eventId:'receipt',value:[start,40]});
 });
 it('uses the last known pre-window balance when filtering',()=>{
  const a=Date.parse('2026-11-06T09:00:00+08:00');expect(inventoryChartData(inventory,a,end)[0].value).toEqual([a,12]);
 });
 it('keeps an unknown balance as null, including its unverified tail',()=>{
  const rows=inventoryChartData({...inventory,events:[{id:'unknown',at,balance:null}],complete:false},start,end);
  expect(rows.at(-1)).toMatchObject({eventId:'unknown',value:[Date.parse(at),null]});expect(rows.at(-1)?.value[0]).not.toBe(end);
  expect(inventoryChartData({...inventory,opening:null},start,end)).toEqual([]);expect(chartNumber('')).toBeNull();expect(chartNumber(false)).toBeNull();expect(chartNumber('0')).toBe(0);
 });
 it('keeps prior 1971 initialization out of the new planning period',()=>{
  const rows=inventoryChartData({...inventory,opening:'12',events:[{id:'initial',at:'1971-01-01T00:00:00Z',balance:'12'},...inventory.events]},start,end);
  expect(rows[0].value).toEqual([start,12]);expect(rows.some(p=>p.eventId==='initial')).toBe(false);
 });
 it('uses time-axis steps and direct event IDs, with no interpolation across nulls',()=>{
  const option:any=inventoryChartOption(inventory,undefined,start,end,'Asia/Shanghai');expect(option.xAxis.type).toBe('time');expect(option.xAxis.min).toBe(start);expect(option.series[0]).toMatchObject({step:'end',connectNulls:false});expect(option.series[0].data[1].eventId).toBe('output');
  expect(option.tooltip.renderMode).toBe('richText');expect(option.tooltip.formatter([{seriesName:'当前方案',data:option.series[0].data[1]}])).toContain('2026-11-05 18:00');
 });
 it('formats the zoom range in the same business timezone as inventory events',()=>{
  const option:any=inventoryChartOption(inventory,undefined,start,end,'Asia/Shanghai');
  expect(option.dataZoom[1].labelFormatter(start)).toBe('11-02 08:00');
  expect(option.dataZoom[1].labelFormatter(end)).toBe('11-06 18:00');
 });
 it('keeps baseline and candidate inventory traces distinct beyond hue with identical native point order',()=>{
  const option:any=inventoryChartOption(inventory,inventory,start,end,'Asia/Shanghai');
  expect(option.series[0].lineStyle.type).toBe('dashed');expect(option.series[1].lineStyle.type).toBe('solid');expect(option.series[0].symbol).toBe('emptyCircle');expect(option.series[1].symbol).toBe('diamond');
  expect(option.series[0].data).toEqual(option.series[1].data);
 });
 it('only draws policy target band after existing independent audit passes',()=>{
  const policy={item_id:'WIP',location_id:'FACTORY',uom:'set',minimum:'5',maximum:'30',start_at:new Date(start).toISOString(),end_at:new Date(end).toISOString()};
  const checked:any=inventoryChartOption(inventory,undefined,start,end,'Asia/Shanghai',policy,'PASS');expect(checked.series[0].markLine.data).toHaveLength(2);
  const missing:any=inventoryChartOption(inventory,undefined,start,end,'Asia/Shanghai',policy,'NOT_CHECKED');expect(missing.series[0].markLine).toBeUndefined();
 });
 it('rejects comparison across plan or version, and never fills a missing run with zero',()=>{
  expect(networkDemandChartRows(view(),view('other')).status).toBe('NOT_CHECKED');expect(networkDemandChartRows(view(),view('p','other')).rows).toEqual([]);expect(networkDemandChartRows(view(),{...view(),result:undefined}).rows).toEqual([]);
 });
 it('compares each stable demand line and preserves known zero versus unknown',()=>{
  const base=view(),candidate=view();base.result!.deliveries[1].planned_quantity='10';base.result!.deliveries[1].unfulfilled_quantity='0';delete candidate.result!.deliveries[0].unfulfilled_quantity;
  const rows=networkDemandChartRows(base,candidate).rows;expect(rows[1]).toMatchObject({id:'D2',before:10,after:2,shortBefore:0,shortAfter:8});expect(rows[0].shortBefore).toBe(0);expect(rows[0].shortAfter).toBeNull();
  const option:any=demandComparisonOption(rows,'piece');expect(option.yAxis.name).toBe('数量（件）');expect(option.series[0].data[1].eventId).toBe('D2');expect(option.series[3].data[0].value).toBeNull();
 });
 it('refuses duplicate line IDs or unit mismatch rather than combining quantities',()=>{
  const a=view(),b=view();b.result!.deliveries[0].uom='m';expect(networkDemandChartRows(a,b).status).toBe('NOT_CHECKED');
  const duplicate=view();duplicate.result!.deliveries.push({...duplicate.result!.deliveries[0]});expect(networkDemandChartRows(a,duplicate).status).toBe('NOT_CHECKED');
 });
});
