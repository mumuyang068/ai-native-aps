import {describe,it,expect} from 'vitest';
import {businessViewUrl,verifyBusinessView,businessCalendarWindow,businessDetailQuery} from './businessViewModel';
describe('business facts identity and calendar',()=>{
 it('modal paging preserves the selected domain and dimensions independently of the Home page',()=>{
  const filters={item_id:'X',location_id:'A',kind:'NET_FORECAST',status:'open'};
  expect(businessDetailQuery(filters,{section:'manufacturing'},20)).toEqual({...filters,section:'manufacturing',offset:20,page_size:20});
  expect(businessDetailQuery(filters,{day:'2027-01-02'},0)).toEqual({...filters,section:'orders',start:'2027-01-02',end:'2027-01-03',offset:0,page_size:20});
  expect(businessDetailQuery(filters,{overdue:true},20).status).toBe('overdue');
  expect(businessDetailQuery(filters,{section:'orders'},20,{start:'2027-03-01',end:'2027-04-01'})).toEqual({...filters,section:'orders',start:'2027-03-01',end:'2027-04-01',offset:20,page_size:20});
  expect(businessDetailQuery(filters,{overdue:true},0,{start:'2027-03-01',end:'2027-04-01'})).not.toHaveProperty('start');
  const responseRange={start:'2027-03-01',end:'2027-04-01',field:'ORDER_DUE_DATE',end_exclusive:true};
  expect(businessDetailQuery(filters,{section:'orders'},0,responseRange)).not.toHaveProperty('field');
 });
 const scope={versionId:'DATA-a',planId:'PLAN-0123456789abcdef'};
 it('does not require a run and has a DATA-only route',()=>{
  expect(businessViewUrl(scope,{})).toContain('/business-view?data_version_id=DATA-a');
  expect(businessViewUrl({versionId:'DATA-a'},{})).toContain('/data-versions/DATA-a/business-view');
  expect(businessViewUrl(scope,{})).not.toContain('run_id');
 });
 it('rejects mixed versions, wrong plans and native results',()=>{
  const value={schema_version:'business-view/1',fact_kind:'CONFIRMED_BUSINESS_FACTS',identity:{data_version_id:'DATA-a',plan_id:scope.planId,run_id:null}};
  expect(()=>verifyBusinessView(value,scope)).not.toThrow();
  expect(()=>verifyBusinessView({...value,identity:{...value.identity,data_version_id:'DATA-b'}},scope)).toThrow();
  expect(()=>verifyBusinessView({...value,identity:{...value.identity,run_id:'API-abc'}},scope)).toThrow();
 });
 it('shows a rolling year across January and preserves narrow-screen navigation',()=>{
  const range={start:'2026-11-09',end:'2027-11-20'};
  const wide=businessCalendarWindow(range,900,'');
  expect(wide.from).toBe('2026-11-01');expect(wide.to).toBe('2027-10-31');expect(wide.next).toBe('2027-11');
  expect(businessCalendarWindow(range,350,'').span).toBe(3);
  expect(businessCalendarWindow({start:'2027-03-01',end:'2027-04-01'},900,'').to).toBe('2027-03-31');
  expect(businessCalendarWindow(range,900,'2027-11').to).toBe('2027-11-30');
 });
});
