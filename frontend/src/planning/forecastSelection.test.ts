import {describe,expect,it} from 'vitest';
import {rememberForecastSelection,restoreForecastSelection} from './forecastSelection';
import type {DecisionReport,DemandPlanningRow} from './demandDecisionModel';

const scope={planId:'P',versionId:'V',runId:'R',target:'baseline'};
const provenance={source_system:'MANUAL',source_record_id:'F1',source_version:'S1',observed_at:'2026-11-01T00:00:00+08:00',import_batch_id:'B1',source_locator:'sheet#1'};
const row:DemandPlanningRow={id:'F1',forecast_id:'F1',version_id:'FV1',start_at:'2026-11-01T00:00:00+08:00',end_at:'2026-12-01T00:00:00+08:00',item_id:'FG',location_id:'L',customer_id:'C',uom:'件',gross_forecast:'100',firm_open_orders:'12',forecast_consumed:'12',net_forecast:'88',effective_planning_demand:'100',planned_quantity:'20',firm_demand_ids:['O1'],forecast_demand_ids:['N1'],provenance};
const report:DecisionReport<DemandPlanningRow>={identity:{plan_id:'P',data_version_id:'V',run_id:'R',target:'baseline'},timezone:'Asia/Shanghai',status:'PRESENT',rows:[row]};

describe('forecast bucket return selection',()=>{
 it('restores the exact current row after unmount without storing quantities, editor or preview',()=>{
  const saved=JSON.parse(JSON.stringify(rememberForecastSelection(scope,row)));
  expect(Object.keys(saved).sort()).toEqual(['bucket','data_version_id','plan_id','row_id']);
  expect(restoreForecastSelection(saved,scope,report)).toBe(row);
  const candidateScope={...scope,runId:'NEW-RUN',target:'DRAFT-A'};
  const candidate={...row,gross_forecast:'16',net_forecast:'4',planned_quantity:'4'};
  const currentReport={...report,identity:{...report.identity,run_id:'NEW-RUN',target:'DRAFT-A'},rows:[candidate]};
  expect(restoreForecastSelection(saved,candidateScope,currentReport)).toBe(candidate);
  expect(saved.bucket).not.toHaveProperty('gross_forecast');
 });
 it('never binds a saved selection to a foreign plan/version or a stale run report',()=>{
  const saved=rememberForecastSelection(scope,row);
  for(const key of ['planId','versionId'])expect(restoreForecastSelection(saved,{...scope,[key]:'FOREIGN'},report)).toBeNull();
  for(const key of ['plan_id','data_version_id','run_id','target'])expect(restoreForecastSelection(saved,scope,{...report,identity:{...report.identity,[key]:'FOREIGN'}})).toBeNull();
 });
 it('checks the live row id, full bucket dimensions and all source fields',()=>{
  const saved=rememberForecastSelection(scope,row);
  for(const key of ['id','forecast_id','version_id','item_id','location_id','customer_id','uom','start_at','end_at'])expect(restoreForecastSelection(saved,scope,{...report,rows:[{...row,[key]:'CHANGED'}]})).toBeNull();
  for(const key of Object.keys(provenance))expect(restoreForecastSelection(saved,scope,{...report,rows:[{...row,provenance:{...provenance,[key]:'CHANGED'}}]})).toBeNull();
  expect(restoreForecastSelection(saved,scope,{...report,rows:[]})).toBeNull();
  expect(restoreForecastSelection(saved,scope,{...report,rows:[row,{...row}]})).toBeNull();
 });
 it('rejects malformed persisted selections and rows without source identity',()=>{
  const saved=rememberForecastSelection(scope,row)!;
  for(const malformed of [null,{},'F1',{...saved,bucket:null},{...saved,row_id:'OTHER'},{...saved,preview:{net:'4'}}])expect(restoreForecastSelection(malformed,scope,report)).toBeNull();
  expect(rememberForecastSelection(scope,{...row,provenance:null})).toBeNull();
  expect(rememberForecastSelection(scope,{...row,id:'ANOTHER-ROW'})).toBeNull();
 });
});
