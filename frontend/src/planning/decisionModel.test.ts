import {describe,it,expect} from 'vitest';
import {filterExceptions,verifyExceptions,exceptionInvestigationSearch,decisionReturnSearch} from './exceptionsModel';
import {selectedReviewDrafts,verifyReview,reviewDeliveryRows,reviewInventoryRows,reviewComparable} from './scenarioReviewModel';
import {evidenceStatus} from './orderInvestigationModel';
const sha='a'.repeat(64),scope={planId:'P',versionId:'V',runId:'R',target:'baseline',profile:'two-level-confirmed-supply-v1'};
const source={artifact:'constraint.json',sha256:sha,run_id:'R'};
const constraint={id:'C',record:{demand:'D1',owner:'SEW',name:'capacity overload',entity:'resource'},source};
const response=()=>({identity:{plan_id:'P',data_version_id:'V',run_id:'R',target:'baseline',profile:scope.profile},timezone:'Asia/Shanghai',demands:[{demand_id:'D1',order_ref:'ORDER-1',item_id:'FG',uom:'set',requested_quantity:'20',planned_quantity:'10',unfulfilled_quantity:'10',due_at:'2026-10-20T16:30:00Z',planned_delivery_at:'2026-10-20T08:00:00Z',status:'PARTIAL',constraints:[structuredClone(constraint)],constraint_status:'PRESENT'},{demand_id:'D2',order_ref:'ORDER-2',item_id:'FG',uom:'set',planned_quantity:'0',unfulfilled_quantity:'20',due_at:'2026-10-19T08:00:00Z',status:'UNPLANNED',constraints:[],constraint_status:'NOT_COLLECTED'}],evidence:{constraints:{status:'PRESENT',source},problems:{status:'EMPTY_CHECKED',relation_scope:'RUN_LEVEL_ONLY'}},problems:[],unlinked_constraints:[]});

describe('exact native exception evidence and filters',()=>{
 it('retains NOT_CHECKED as unverified rather than invalid or empty evidence',()=>{expect(evidenceStatus('NOT_CHECKED')).toBe('NOT_CHECKED')});
 it('filters a site-local due date, short status and exact native owner/type together',()=>{
  const data=response();expect(filterExceptions(data.demands,{from:'2026-10-21',to:'2026-10-21',status:'SHORT',owner:'SEW',type:'capacity overload',query:'order-1',evidence:'PRESENT'},data.timezone).map(r=>r.demand_id)).toEqual(['D1']);
  expect(filterExceptions(data.demands,{owner:'SEW',evidence:'NOT_COLLECTED'},data.timezone)).toHaveLength(0);
 });
 it('keeps partial, wholly unplanned, unknown evidence and empty checked distinct',()=>{
  expect(filterExceptions(response().demands,{status:'UNPLANNED'},'Asia/Shanghai').map(r=>r.demand_id)).toEqual(['D2']);
  expect(filterExceptions(response().demands,{status:'LATE'},'Asia/Shanghai')).toHaveLength(0);
  expect(filterExceptions(response().demands,{evidence:'EMPTY_CHECKED'},'Asia/Shanghai')).toHaveLength(0);
 });
 it('accepts exact run and demand; rejects owner-based assignment and foreign source',()=>{
  expect(verifyExceptions(response(),scope).demands).toHaveLength(2);
  const wrong=response();wrong.demands[0].constraints[0].record.demand='D2';expect(()=>verifyExceptions(wrong,scope)).toThrow();
  const foreign=response();foreign.identity.run_id='other';expect(()=>verifyExceptions(foreign,scope)).toThrow();
  const stale=response();stale.demands[0].constraints[0].source={...source,run_id:'other'};expect(()=>verifyExceptions(stale,scope)).toThrow();
 });
 it('round-trips exact original run, selected demand, all filters and date range',()=>{
  const origin=new URLSearchParams('target=baseline&analysis=exceptions&exception_query=FG&exception_from=2026-10-20&exception_to=2026-10-22');
  const next=exceptionInvestigationSearch(origin,'D1',scope);expect(next.get('analysis')).toBe('schedule');expect(next.get('run_id')).toBe('R');
  const back=decisionReturnSearch(next);expect(back.get('analysis')).toBe('exceptions');expect(back.get('target')).toBe('baseline');expect(back.get('exception_row')).toBe('D1');expect(back.get('exception_query')).toBe('FG');expect(back.get('exception_from')).toBe('2026-10-20');expect(back.get('run_id')).toBe('R');
 });
});

const review=()=>({identity:{plan_id:'P',data_version_id:'V',baseline_run_id:'R',profile:'profile'},endpoint:{kind:'STAGE_OUTPUT',item_id:'STAGE'},available_drafts:[{id:'A'},{id:'B'},{id:'C'},{id:'D'}],candidates:[{target:'baseline',run_id:'R',comparable:true,calculation_state:'SUCCEEDED',business_status:'FEASIBLE_ON_TIME',validation:{material:{status:'PASS'},schedule:{status:'PASS'},resources:null},deliveries:[{demand_id:'D1',item_id:'STAGE',uom:'set',planned_quantity:'20'}],inventory:[{item_id:'STAGE',location_id:'L',uom:'set',ending_net_available:'0'}]},{target:'A',run_id:null,comparable:false,calculation_state:'NOT_RUN',deliveries:[{demand_id:'D1',uom:'set',planned_quantity:'999'}]},{target:'B',run_id:'RB',comparable:true,calculation_state:'SUCCEEDED',business_status:'FEASIBLE_ON_TIME',validation:{material:{status:'PASS'},schedule:{status:'PASS'},resources:null},deliveries:[{demand_id:'D1',uom:'piece',planned_quantity:'5'}],inventory:[{item_id:'STAGE',location_id:'L',uom:'piece',ending_net_available:'5'}]}]});
describe('bounded same-plan scenario review',()=>{
 it('defaults to two saved candidates; explicit empty, duplicate and max-three choices are stable',()=>{expect(selectedReviewDrafts(null,review().available_drafts)).toEqual(['A','B']);expect(selectedReviewDrafts('',review().available_drafts)).toEqual([]);expect(selectedReviewDrafts('C,A,C,B,D',review().available_drafts)).toEqual(['C','A','B']);expect(selectedReviewDrafts('unknown',review().available_drafts)).toEqual([])});
 it('rejects another version, baseline, profile or endpoint before showing measures',()=>{const scope={planId:'P',versionId:'V',baselineRunId:'R',profile:'profile',endpoint:{kind:'STAGE_OUTPUT',item_id:'STAGE'}};expect(verifyReview(review(),scope)).toBeTruthy();for(const key of ['plan_id','data_version_id','baseline_run_id','profile']){const v=review();(v.identity as any)[key]='wrong';expect(()=>verifyReview(v,scope)).toThrow()}const v=review();v.endpoint.kind='CUSTOMER_FG';expect(()=>verifyReview(v,scope)).toThrow()});
 it('never exposes NOT_RUN measures and separates different units instead of summing',()=>{const data=review(),rows=reviewDeliveryRows(data.candidates);expect(rows).toHaveLength(2);expect(rows.find(r=>r.uom==='set')?.values.A).toBeUndefined();expect(rows.find(r=>r.uom==='set')?.values.B).toBeUndefined();expect(rows.find(r=>r.uom==='piece')?.values.B.planned_quantity).toBe('5');expect(reviewInventoryRows(data.candidates)).toHaveLength(2)});
 it('withholds measures for an explicit hard validation FAIL even if the payload says comparable',()=>{const data=review();(data.candidates[2] as any).validation={material:{status:'FAIL'}};expect(reviewDeliveryRows(data.candidates).some(row=>row.values.B)).toBe(false);expect(reviewInventoryRows(data.candidates).some(row=>row.values.B)).toBe(false)});
});


describe('complete validation is required before scenario metrics are visible',()=>{
 it('accepts only explicitly checked recognized business outcomes',()=>{
  for(const business_status of ['FEASIBLE_ON_TIME','LATE_DELIVERY','PARTIAL_OR_UNPLANNED']){
   const candidate={...review().candidates[0],business_status};expect(reviewComparable(candidate)).toBe(true);
   const qualified={...candidate,validation:{...candidate.validation,resources:{status:'PASS'}}};expect(reviewComparable(qualified)).toBe(true);
  }
 });
 it('keeps legitimate late delivery comparable and rejects the undefined delayed status',()=>{
  const candidate={...review().candidates[0],business_status:'LATE_DELIVERY'};
  expect(reviewComparable(candidate)).toBe(true);
  expect(reviewDeliveryRows([candidate])).toHaveLength(1);
  expect(reviewInventoryRows([candidate])).toHaveLength(1);
  expect(reviewComparable({...candidate,business_status:'FEASIBLE_DELAYED'})).toBe(false);
 });
 it('rejects incomplete or missing business status despite a comparable claim',()=>{
  for(const business_status of ['VALIDATION_INCOMPLETE','UNSUPPORTED','NOT_CHECKED',undefined]){
   const data=review();(data.candidates[2] as any).business_status=business_status;
   expect(reviewComparable(data.candidates[2])).toBe(false);
   expect(reviewDeliveryRows(data.candidates).some(row=>row.values.B)).toBe(false);
   expect(reviewInventoryRows(data.candidates).some(row=>row.values.B)).toBe(false);
  }
 });
 it('rejects explicitly unverified material, schedule or qualified resources without erasing the evidence',()=>{
  for(const key of ['material','schedule','resources'])for(const status of ['NOT_CHECKED','UNSUPPORTED']){
   const data=review(),candidate=data.candidates[2] as any;candidate.validation[key]={status,reason:'required evidence missing'};
   expect(reviewComparable(candidate)).toBe(false);
   expect(reviewDeliveryRows(data.candidates).some(row=>row.values.B)).toBe(false);
   expect(reviewInventoryRows(data.candidates).some(row=>row.values.B)).toBe(false);
   expect(candidate.validation[key]).toEqual({status,reason:'required evidence missing'});
  }
 });
 it('rejects absent validation and missing required keys rather than assuming PASS',()=>{
  const candidate=review().candidates[0] as any;delete candidate.validation;expect(reviewComparable(candidate)).toBe(false);
  for(const key of ['material','schedule','resources']){const c=review().candidates[0] as any;delete c.validation[key];expect(reviewComparable(c)).toBe(false)}
  for(const key of ['material','schedule']){const c=review().candidates[0] as any;c.validation[key]=null;expect(reviewComparable(c)).toBe(false)}
 });
});
