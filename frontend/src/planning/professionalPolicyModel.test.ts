import {describe,it,expect} from 'vitest';
import {professionalIdentity,verifyProfessionalReport,siteTimestamp,explicitNettingPolicy} from './professionalPolicyModel';
const scope={planId:'PLAN-a',versionId:'DATA-a',baselineRunId:'API-a',runId:'API-b',target:'DRAFT-a'};
describe('professional policy scope and explicit research contract',()=>{
 it('rejects every foreign identity without showing stale rows',()=>{
  const identity={plan_id:scope.planId,...professionalIdentity(scope)},good={identity,simulation_only:true,execution_authorized:false};
  expect(verifyProfessionalReport(good,scope)).toBe(good);
  for(const key of Object.keys(identity))expect(()=>verifyProfessionalReport({...good,identity:{...identity,[key]:'foreign'}},scope)).toThrow();
  expect(()=>verifyProfessionalReport({...good,execution_authorized:true},scope)).toThrow();
 });
 it('never converts an unspecified date or browser timezone to a promise',()=>{
  expect(siteTimestamp('2026-11-25T18:00','Asia/Shanghai')).toBe('2026-11-25T10:00:00.000Z');
  expect(()=>siteTimestamp('','Asia/Shanghai')).toThrow();
 });
 it('keeps an explicit policy, exact dimensions, no cross-bucket default and selected dates only',()=>{
  const p=explicitNettingPolicy({timezone:'Asia/Shanghai'},'P1','research',['f1'],{f1:'2026-11-30T18:00',unused:'2026-12-31T18:00'});
  expect(p.approval_status).toBe('RESEARCH_POLICY');expect(p.early_buckets).toBe(0);expect(p.late_buckets).toBe(0);
  expect(Object.keys(p.net_terms.due_by_forecast)).toEqual(['f1']);expect(p.net_terms.minimum_shipment).toBe('1');
 });
});
