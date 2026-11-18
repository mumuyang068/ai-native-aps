import {describe,it,expect} from 'vitest';
import {buildTimeBucketRows,reportObjectKey,resolveReportSelection,reportCellId,readReportCell,reportSelectionSearch,timeBucketColumnWidths,timeBucketCellLabel,type TimeBucketReportData} from './timeBucketReportModel';
import {timeBucketChartOption} from './timeBucketCharts';
import {reportDemandKindRows} from './reportMetricDefinitions';

const scope={planId:'P',versionId:'D',runId:'R',target:'baseline'};
const report:TimeBucketReportData={scopeKey:JSON.stringify(scope),buckets:[{id:'B',label:'11月',start:'2026-11-01',end:'2026-12-01'}],objects:[{key:'I-L',label:'I · L',unit:'件',cells:{B:{ending:'9007199254740993.125',inflow:null,outflow:'0'}}}],metrics:[{id:'inflow',label:'流入',kind:'flow',chart:'bar'},{id:'outflow',label:'流出',kind:'flow',chart:'bar',direction:-1},{id:'ending',label:'期末',kind:'balance',chart:'line'}]};
describe('owner-provided bucket report projection',()=>{
 it('retains exact Decimal strings and unknown rather than calculating totals',()=>{
  const rows=buildTimeBucketRows(report,report.metrics);expect(rows).toHaveLength(3);
  expect(rows.find(r=>r.metric.id==='ending')?.values.B).toBe('9007199254740993.125');
  expect(readReportCell(report.objects[0],'B','inflow')).toBeNull();
 });
 it('separates units and endpoints in stable keys',()=>{
  expect(reportObjectKey({item_id:'I',location_id:'L',uom:'件',endpoint:'A'})).not.toBe(reportObjectKey({item_id:'I',location_id:'L',uom:'套',endpoint:'A'}));
  expect(reportObjectKey({item_id:'I',uom:'件',endpoint:'A'})).not.toBe(reportObjectKey({item_id:'I',uom:'件',endpoint:'B'}));
 });
 it('rejects stale run or removed bucket selection',()=>{
  const s={scopeKey:report.scopeKey,objectKey:'I-L',bucketId:'B',metricId:'ending'};
  expect(resolveReportSelection(s,report)).toEqual(s);
  expect(resolveReportSelection({...s,scopeKey:'other-run'},report)).toBeNull();
  expect(resolveReportSelection({...s,bucketId:'old'},report)).toBeNull();
 });
 it('plots all visible objects with original selection IDs and absent unknown values',()=>{
  const data={...report,objects:[...report.objects,{...report.objects[0],key:'J-L',label:'J · L'}]};
  const o=timeBucketChartOption(data,['I-L','J-L']) as any;
  expect(o.grid).toHaveLength(2);expect(o.series).toHaveLength(6);
  expect(o.series[0].data[0].value).toBeNull();
  expect(o.series[0].data[0].eventId).toBe(reportCellId('I-L','B','inflow'));
  expect(o.series[3].data[0].eventId).toBe(reportCellId('J-L','B','inflow'));
  expect(o.dataZoom[0].handleSize).toBe(24); // iX theme currently supplies undefined%.
 });
 it('never fills a missing Net bucket or merges another object into it',()=>{
  const d={...report,metrics:[{id:'net',label:'净预测',kind:'flow' as const,chart:'bar' as const}]};
  expect(buildTimeBucketRows(d,d.metrics)[0].values.B).toBeNull();
  expect((timeBucketChartOption(d,['I-L']) as any).series[0].data[0].value).toBeNull();
 });
 it('does not interpret absent netting evidence as zero and isolates owner unclassified totals',()=>{
  const row={kind_totals:{FIRM_ORDER:{due_quantity:'4'},UNCLASSIFIED:{due_quantity:'2'}},demand_components:[{demand_id:'X',demand_kind:null}]};
  expect(reportDemandKindRows([row],'NET_FORECAST',[{demand_kind:'FIRM_ORDER'}])[0].due_quantity).toBeNull();
  expect(reportDemandKindRows([row],'UNCLASSIFIED',[])[0].due_quantity).toBe('2');
 });
 it('keeps missing Net local to the full object key and clears all other-kind fields',()=>{
  const a={item_id:'A',uom:'件',kind_totals:{FIRM_ORDER:{due_quantity:'4'}},not_yet_due_unfulfilled:'99'};
  const b={item_id:'B',uom:'米',kind_totals:{NET_FORECAST:{due_quantity:'2'}}};
  expect(reportDemandKindRows([a,b],'NET_FORECAST',[])[0].due_quantity).toBeNull();
  expect(reportDemandKindRows([a,b],'NET_FORECAST',[])[0].not_yet_due_unfulfilled).toBeNull();
 });
 it('builds a single navigation payload containing selection and prior context',()=>{
  const selected={scopeKey:report.scopeKey,objectKey:'I-L',bucketId:'B',metricId:'ending'};
  const q=reportSelectionSearch(new URLSearchParams('report=inventory&bucket_view=numbers'),selected);
  q.set('report_row','native-row');expect(JSON.parse(q.get('bucket_selection')!)).toEqual(selected);expect(q.get('bucket_view')).toBe('numbers');
 });
 it('reserves the first complete time bucket beside both pinned identity columns in a 390px layout',()=>{
  const container=370,widths=timeBucketColumnWidths(container);
  expect(widths.object).toBe(128);expect(widths.metric).toBe(108);expect(widths.bucket).toBeGreaterThanOrEqual(90);
  expect(widths.object+widths.metric+widths.bucket+18).toBeLessThanOrEqual(container);
  expect(timeBucketColumnWidths(300).object+timeBucketColumnWidths(300).metric+timeBucketColumnWidths(300).bucket+18).toBeLessThanOrEqual(300);
  expect(timeBucketColumnWidths(1024)).toEqual({object:245,metric:175,bucket:142});
 });
 it('preserves complete object, date boundaries, metric and Decimal value in compact cell labels',()=>{
  const object=report.objects[0],bucket=report.buckets[0],metric=report.metrics[2];
  const label=timeBucketCellLabel(object,bucket,metric,object.cells.B.ending);
  for(const text of [object.label,object.unit,bucket.label,bucket.start,bucket.end,metric.label,'9007199254740993.125'])expect(label).toContain(text);
  const original=JSON.stringify(report),rows=buildTimeBucketRows(report,report.metrics);
  timeBucketColumnWidths(370);
  expect(JSON.stringify(report)).toBe(original);expect(Object.keys(rows[2].values)).toEqual(report.buckets.map(b=>b.id));
  expect(rows[2].values.B).toBe('9007199254740993.125');
 });
});
