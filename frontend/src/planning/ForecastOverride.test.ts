import {describe,expect,it} from 'vitest';
import {forecastBucket,forecastGross,verifyForecastDraft,verifyForecastPreview} from './ForecastOverride';
import type {DemandPlanningRow} from './demandDecisionModel';

const scope={planId:'P',versionId:'V',runId:'R',target:'baseline'};
const provenance={source_system:'MANUAL',source_record_id:'F1',source_version:'S1',observed_at:'2026-11-01T00:00:00+08:00',import_batch_id:'B1',source_locator:'sheet#1'};
const row:DemandPlanningRow={id:'F1',forecast_id:'F1',version_id:'FV1',start_at:'2026-11-01T00:00:00+08:00',end_at:'2026-12-01T00:00:00+08:00',item_id:'FG',location_id:'L',customer_id:'C',uom:'件',gross_forecast:'100',firm_open_orders:'12',forecast_consumed:'12',net_forecast:'88',effective_planning_demand:'100',planned_quantity:'90',firm_demand_ids:['O1'],forecast_demand_ids:['N1'],provenance};
const bucket={forecast_id:'F1',forecast_version_id:'FV1',item_id:'FG',location_id:'L',customer_id:'C',uom:'件',bucket_start:row.start_at,bucket_end:row.end_at,provenance};
const preview={identity:{plan_id:'P',data_version_id:'V',baseline_run_id:'R',run_id:'R',target:'baseline'},bucket,before:{gross_quantity:'100',consumed_quantity:'12',net_quantity:'88'},after:{gross_quantity:'140',consumed_quantity:'12',net_quantity:'128'},reason:'活动假设',engine_executed:false,calculation_submitted:false};

describe('exact forecast decision guards',()=>{
 it('requires all source dimensions and never derives identity from a display label',()=>{
  expect(forecastBucket(row)).toEqual(bucket);
  for(const key of ['forecast_id','version_id','item_id','location_id','customer_id','uom','provenance'])expect(forecastBucket({...row,[key]:null})).toBeNull();
  expect(forecastBucket({...row,provenance:{...provenance,unexpected:'extra'}})).toBeNull();
 });
 it('keeps exact decimals and rejects unchanged, negative or oversized gross values',()=>{
  expect(forecastGross('140.00000001','100')).toBe('140.00000001');
  expect(forecastGross('0','100')).toBe('0');
  for(const value of ['100','100.000','-1','1e2','01','1.123456789','1000000000000','',' 5'])expect(forecastGross(value,'100')).toBeNull();
 });
 it('binds previews to the precise bucket, baseline and edited gross',()=>{
  expect(verifyForecastPreview(preview,scope,bucket,'140','活动假设')).toBe(preview);
  for(const key of ['plan_id','data_version_id','baseline_run_id','run_id','target'])expect(()=>verifyForecastPreview({...preview,identity:{...preview.identity,[key]:'foreign'}},scope,bucket,'140','活动假设')).toThrow();
  for(const key of ['forecast_id','forecast_version_id','item_id','location_id','customer_id','uom','bucket_start','bucket_end'])expect(()=>verifyForecastPreview({...preview,bucket:{...bucket,[key]:'foreign'}},scope,bucket,'140','活动假设')).toThrow();
  expect(()=>verifyForecastPreview({...preview,engine_executed:true},scope,bucket,'140','活动假设')).toThrow();
  expect(()=>verifyForecastPreview(preview,scope,bucket,'150','活动假设')).toThrow();
  expect(()=>verifyForecastPreview(preview,scope,bucket,'140','新理由')).toThrow();
 });
 it('navigates only to a matching NOT_RUN draft with a retained forecast decision',()=>{
  const response={identity:preview.identity,calculation_submitted:false,run:{state:'NOT_RUN'},draft:{id:'DRAFT-A',state:'DRAFT',run_id:null,changes:{action:'set_forecast_gross_override',bucket,gross_quantity:'140',reason:'活动假设'}}};
  expect(verifyForecastDraft(response,scope,bucket,'140','活动假设')).toBe('DRAFT-A');
  for(const mutation of [{run:{state:'SUCCEEDED'}},{calculation_submitted:true},{draft:{...response.draft,run_id:'foreign'}},{identity:{...preview.identity,run_id:'foreign'}}])expect(()=>verifyForecastDraft({...response,...mutation},scope,bucket,'140','活动假设')).toThrow();
 });
});
