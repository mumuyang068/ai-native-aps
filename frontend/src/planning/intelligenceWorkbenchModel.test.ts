import {describe,it,expect} from 'vitest';
import {advancedNettingParameters,forecastSeries,quoteActions,researchActionAllowed,quoteSupplyView,quoteActionScope,adoptedWorkbenchState} from './intelligenceWorkbenchModel';

describe('intelligence workbench contracts',()=>{
 it('keeps a forecast chart on the exact item, customer, location and unit',()=>{
  const row={id:'A',item_id:'FG',location_id:'F',customer_id:'C',uom:'件',bucket_start:'2026-11-09T00:00:00+08:00',gross_quantity:'12'};
  const historical={...row,bucket_start:'2026-10-19T00:00:00+08:00',quantity:'0',censored:true};
  const result=forecastSeries([row,{...row,id:'B',customer_id:'OTHER',gross_quantity:'99'},{...row,id:'M',uom:'米',gross_quantity:'100'}],[historical,{...historical,uom:'米',censored:false,quantity:'80'}],'A');
  expect(result?.forecast.map(r=>r.gross_quantity)).toEqual(['12']);
  expect(result?.history.map(r=>r.quantity)).toEqual([null]);
  expect(result?.unit).toBe('件');
 });
 it('copies only bounded consumption windows without altering frozen forecast payload',()=>{
  const template={revision:'advanced-netting-v1',forecast_version:{id:'FV1',rows:[{id:'ROW1',gross_quantity:'12'}]},consumption_policy:{version_id:'PV1',backward_days:0,forward_days:0},net_terms:{due_at:'2026-12-01'}};
  const changed=advancedNettingParameters(template,3,7);
  expect(changed.forecast_version).toEqual(template.forecast_version);
  expect(changed.consumption_policy.backward_days).toBe(3);
  expect(changed.consumption_policy.forward_days).toBe(7);
  expect(template.consumption_policy.backward_days).toBe(0);
  expect(()=>advancedNettingParameters(template,-1,0)).toThrow();
  expect(()=>advancedNettingParameters(template,0,367)).toThrow();
  expect(()=>advancedNettingParameters({...template,revision:'old'},0,0)).toThrow();
 });
 it('does not offer hold or simulated commit before the quote lifecycle permits it',()=>{
  expect(quoteActions('INQUIRY')).toEqual(['quote_calculate']);
  expect(quoteActions('CALCULATED')).toEqual(['quote_offer']);
  expect(quoteActions('OFFERED')).toEqual(['quote_hold']);
  expect(quoteActions('HELD')).toEqual(['quote_release','quote_commit']);
  expect(quoteActions('EXPIRED')).toEqual([]);
  expect(quoteActions('UNKNOWN')).toEqual([]);
 });
 it('allows quote actions on audited current candidates while policy writes stay on baseline',()=>{
  for(const action of ['quote_create','quote_calculate','quote_offer','quote_hold','quote_release','quote_commit','quote_expire']){
   expect(researchActionAllowed(action,'DRAFT-current','SUCCEEDED','PASS')).toBe(true);
   expect(researchActionAllowed(action,'DRAFT-current','NOT_RUN',null)).toBe(false);
   expect(researchActionAllowed(action,'DRAFT-current','FAILED','PASS')).toBe(false);
   expect(researchActionAllowed(action,'DRAFT-current','SUCCEEDED','FAIL')).toBe(false);
   expect(researchActionAllowed(action,'baseline','SUCCEEDED','PASS')).toBe(true);
   expect(researchActionAllowed(action,'baseline','SUCCEEDED',null)).toBe(true);
  }
  for(const action of ['forecast_start','forecast_confirm','forecast_override','forecast_restore','forecast_withdraw','inventory_recommend']){
   expect(researchActionAllowed(action,'DRAFT-current','SUCCEEDED','PASS')).toBe(false);
   expect(researchActionAllowed(action,'baseline','SUCCEEDED','PASS')).toBe(true);
  }
 });
});

it('uses actual held slices after competing supply changes and releases only original quote scope',()=>{
 const scope={planId:'PLAN-one',versionId:'DATA-one',baselineRunId:'API-base',runId:'API-base',target:'baseline'};
 const quote={id:'Q',state:'HELD',identity:{plan_id:'PLAN-one',data_version_id:'DATA-one',baseline_run_id:'API-base',run_id:'API-old',target:'DRAFT-discarded'},calculation:{source_slices:[{event_id:'A'}],promised_quantity:'6'},held_calculation:{source_slices:[{event_id:'B'}],promised_quantity:'6'}};
 expect(quoteSupplyView(quote).source_slices).toEqual([{event_id:'B'}]);
 expect(quoteActionScope(scope,quote,'quote_release').runId).toBe('API-old');
 expect(quoteActionScope(scope,quote,'quote_hold')).toEqual(scope);
 expect(()=>quoteActionScope(scope,{...quote,identity:{...quote.identity,plan_id:'OTHER'}},'quote_release')).toThrow();
 expect(quoteSupplyView({...quote,state:'OFFERED'}).source_slices).toEqual([{event_id:'A'}]);
});

it('restores the actual candidate conditions rather than the oldest research records',()=>{
 const report={forecast_versions:[{id:'OLD'},{id:'ADOPTED'}],inventory_candidates:[{id:'I-OLD',native_policy:{policy_version:'OLD'}},{id:'I-NEW',native_policy:{policy_version:'P-NEW'},result:{selected_method:'DETERMINISTIC_ORDER_UP_TO'}}],context:{draft:{adopted:{has_forecast:true,has_inventory:true,has_inquiry:true,forecast_version_id:'ADOPTED',inventory_policy_version:'P-NEW',netting_windows:{backward:7,forward:3},inquiry:{quantity:'20'},reason:'actual'}}}};
 expect(adoptedWorkbenchState(report,'baseline')).toBeNull();
 expect(adoptedWorkbenchState(report,'DRAFT-x')).toMatchObject({versionId:'ADOPTED',inventoryId:'I-NEW',method:'DETERMINISTIC_ORDER_UP_TO',backward:'7',forward:'3'});
 expect(adoptedWorkbenchState({...report,inventory_candidates:[]},'DRAFT-x')?.inventoryId).toBe('');
});
