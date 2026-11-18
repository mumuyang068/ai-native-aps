import {describe,it,expect} from 'vitest';
import * as model from './reportModel';
import {equipmentScheduleResources} from './presentationModel';
import {analysisContextSearch} from './analysisModel';
it('retains bucket view and object context across existing report return paths',()=>{
 const q=new URLSearchParams('target=baseline&analysis=reports&report=inventory&bucket_view=numbers&bucket_page=1&bucket_selection=selected');
 expect(model.reportReturnSearch(q,'baseline').get('bucket_view')).toBe('numbers');
 expect(analysisContextSearch(q,'baseline').get('bucket_selection')).toBe('selected');
 expect(analysisContextSearch(q,'DRAFT-new').get('bucket_selection')).toBeNull();
});
it('retains unverified demand objects under the active-only filter',()=>{
 const row={item_id:'A',kind_evidence_status:'NOT_COLLECTED',due_quantity:null,planned_quantity:null,overdue_unfulfilled:null};
 expect(model.reportRows([row],'demand','',true)).toEqual([row]);
});
describe('period report identity and row selection',()=>{
 it('distinguishes confirmed and proposed MO rows and two different transit routes',()=>{
  const row={item_id:'CUT-TB-M',location_id:'FACTORY',uom:'套',operation_id:'CUT-TB-M',start_at:'A',end_at:'B',manufacture_ids:['MO:14'],status:'proposed'};
  expect(model.reportRowId(row)).not.toBe(model.reportRowId({...row,status:'confirmed',manufacture_ids:['MO:SIM-MO-A']}));
  const route={item_id:'FAB',uom:'米',origin_location_id:'A',destination_location_id:'B',start_at:'A',end_at:'B'};
  expect(model.reportRowId(route)).not.toBe(model.reportRowId({...route,origin_location_id:'C'}));
 });
 it('preserves month and clears stale run-local period selection across candidates',()=>{
  const p=new URLSearchParams({return_report:'manufacturing',grain:'month',report_row:'OLD',report_scope:'old',report_from:'2026-11-02T08:00'});
  const r=model.reportReturnSearch(p,'NEW');expect(r.get('grain')).toBe('month');expect(r.get('report')).toBe('manufacturing');
 });
 it('returns only to an authenticated source period and exact original run',()=>{
  const scope={planId:'P',versionId:'V',runId:'R',target:'baseline'};
  const source=new URLSearchParams({target:'baseline',analysis:'reports',report:'inventory',grain:'month',report_row:'ROW',report_scope:JSON.stringify(scope)});
  const drill=model.reportDrillSearch(source,scope,'inventory',{analysis_item:'FAB',analysis_location:'A'});
  expect(drill.get('analysis')).toBe('inventory');expect(model.reportOriginReturn(drill,[scope])?.get('report_row')).toBe('ROW');
  expect(model.reportOriginReturn(drill,[{...scope,runId:'OTHER'}])).toBeNull();
 });
 it('rejects a response from another run even if values match',()=>{
  expect(()=>model.verifyReport({identity:{plan_id:'P',data_version_id:'D',run_id:'old',target:'baseline'}},{planId:'P',versionId:'D',runId:'new',target:'baseline'})).toThrow();
 });
 it('does not hide zero-capacity or missing-utilization semantics',()=>{
  expect(model.ratioLabel(null)).toBe('不适用');expect(model.ratioLabel('0')).toBe('0%');
 });
 it('filters within rows without cancelling two different demand shortages',()=>{
  const rows=[{item_id:'X',due_quantity:'10',planned_quantity:'2',overdue_unfulfilled:'8'},{item_id:'Y',due_quantity:'0',planned_quantity:'8',overdue_unfulfilled:'0'}];
  expect(model.reportRows(rows,'demand','X',true)).toEqual([rows[0]]);
 });
 it('never treats document dates or unknown dates as manufacturing delivery',()=>{
  expect(model.documentDate({at:null})).toBeNull();expect(model.documentDate({at:'2026-11-05T18:00:00+08:00'})).toBe('2026-11-05T18:00:00+08:00');
 });
 it('preserves exact object, range and filters across investigation and comparison',()=>{
  const p=new URLSearchParams({return_report:'inventory',report_row:'WIP|FACTORY|day',report_query:'WIP',report_from:'2026-11-04T09:00',grain:'week',run_id:'old'});
  const r=model.reportReturnSearch(p,'DRAFT-new');expect(r.get('report_row')).toBe('WIP|FACTORY|day');expect(r.get('report_from')).toBe(p.get('report_from'));expect(r.get('run_id')).toBeNull();expect(r.get('target')).toBe('DRAFT-new');
  expect(model.hoursLabel('1.3333333333333333')).toBe('1.3333 小时');expect(model.hoursLabel(null)).toBe('未知');
 });
 it('keeps waiting-QC and in-transit periods even without a local stock movement',()=>{
  const qc={inflow:'0',outflow:'0',qc_waiting_end:'40'},transit={inflow:'0',outflow:'0',planned_in_transit_end:'20'};
  expect(model.reportRows([qc,transit,{}],'inventory','',true)).toEqual([qc,transit]);
 });
 it('keeps route dispatch or carried transit but hides an inactive empty route bucket',()=>{
  expect(model.reportRows([{dispatched_quantity:'30'},{in_transit_ending:'10'},{dispatched_quantity:'0',arrived_quantity:'0',in_transit_ending:'0'}],'transit','',true)).toHaveLength(2);
 });
 it('keeps joint qualified people in personnel detail, outside the equipment-only grid',()=>{
  const resources=[{id:'CUT',role:'MACHINE'},{id:'SEW',role:'MACHINE'},{id:'pool',role:'OPERATOR_POOL'},{id:'P1',role:'OPERATOR'}];
  expect(equipmentScheduleResources(resources,{rules:{manufacturing_profile:'mixed-fixed-batch-confirmed-qualified-v1'}}).map(r=>r.id)).toEqual(['CUT','SEW']);
 });
});
