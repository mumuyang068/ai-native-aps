import {describe,it,expect} from 'vitest';
import {readFileSync} from 'node:fs';
import {chartDefaults,chartColors,chartCategory,chartSeriesStyle,reportMetricChartStyle} from './chartSemantics';
const contrastWhite=(hex:string)=>{const rgb=[1,3,5].map(i=>parseInt(hex.slice(i,i+2),16)/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4),lum=rgb.reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);return 1.05/(lum+.05)};
describe('shared professional chart semantic contract',()=>{
 it('keeps CSS tokens and DOM-free chart fallbacks identical with WCAG text and graphic contrast on the approved light surface',()=>{
  const css=readFileSync(new URL('./tokens.css',import.meta.url),'utf8');
  for(const [key,color] of Object.entries(chartDefaults)){expect(css).toContain('--chart-'+key+':'+color);expect(contrastWhite(color)).toBeGreaterThanOrEqual(4.5)}
  expect(chartColors()).toEqual(chartDefaults);
 });
 it('does not change category paint when lists are filtered or reordered, and reserves risk colors',()=>{
  const before=['A','B','C'].map(chartCategory),after=['C','A'].map(chartCategory);expect(after).toEqual([before[2],before[0]]);
  expect(before).not.toContain(chartDefaults.danger);expect(before).not.toContain(chartDefaults.success);
 });
 it('distinguishes baseline and candidate with pattern, outline and symbol in addition to hue',()=>{
  const baseline=chartSeriesStyle('baseline','baseline'),candidate=chartSeriesStyle('proposed','candidate');
  expect(baseline.itemStyle.color).toBe('#ffffff');expect(baseline.itemStyle.decal).toBeDefined();expect(baseline.lineStyle.type).toBe('dashed');
  expect(candidate.itemStyle.decal).toBeUndefined();expect(candidate.itemStyle.color).toBe(chartDefaults.proposed);expect(candidate.symbol).not.toBe(baseline.symbol);
 });
 it('separates observed actual, native plan and risk while preserving outgoing flow identity',()=>{
  expect(reportMetricChartStyle('shipped').lineStyle).toMatchObject({color:chartDefaults.actual,type:'solid'});
  expect(reportMetricChartStyle('planned_quantity').lineStyle).toMatchObject({color:chartDefaults.planned,type:'dashed'});
  expect(reportMetricChartStyle('overdue').itemStyle.color).toBe(chartDefaults.danger);
  expect(reportMetricChartStyle('physical_out',true).itemStyle.decal).toBeDefined();expect(reportMetricChartStyle('physical_in').itemStyle.decal).toBeUndefined();
 });
});
