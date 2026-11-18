import {describe,it,expect} from 'vitest';
import {supports,profileCapabilities,inputProfile} from './capabilityModel';
import {verifyReview} from './scenarioReviewModel';
describe('same explicit certification contract as the planning service',()=>{
 it('uses the same foundation revision for Home, review and permitted priority actions',()=>{
  const profile=inputProfile({foundation_projection:{schema_revision:'foundation-planning-projection-v1'},rules:{}});
  const scope={planId:'PLAN',versionId:'DATA',baselineRunId:'API',profile};
  const value={identity:{plan_id:'PLAN',data_version_id:'DATA',baseline_run_id:'API',profile},candidates:[{target:'baseline',run_id:'API'}]};
  expect(profile).toBe('foundation-planning-projection-v1');expect(verifyReview(value,scope)).toBe(value);
  expect(supports(profile,'actions','demand_priority')).toBe(true);
  expect(supports(profile,'actions','approved_mo_move')).toBe(false);
  expect(()=>verifyReview(value,{...scope,profile:'ordinary-routing-v1'})).toThrow(/认证模型/);
 });
 it('retains old and unknown profile identities without borrowing foundation permissions',()=>{
  expect(inputProfile({rules:{manufacturing_profile:'enterprise-accessory-supply-v1'}})).toBe('enterprise-accessory-supply-v1');
  expect(inputProfile({rules:{}})).toBe('ordinary-routing-v1');
  expect(supports(inputProfile({foundation_projection:{schema_revision:'unknown'}}),'actions','demand_priority')).toBe(false);
 });
 it('allows the original P4 resource analysis and controlled unavailable window',()=>{expect(supports('mixed-fixed-batch-confirmed-qualified-v1','analyses','resources')).toBe(true);expect(supports('mixed-fixed-batch-confirmed-qualified-v1','actions','resource_unavailability')).toBe(true)});
 it('allows D16 pegging without generalizing it to P4',()=>{expect(supports('two-level-confirmed-supply-v1','analyses','order_pegging')).toBe(true);expect(supports('mixed-fixed-batch-confirmed-qualified-v1','analyses','order_pegging')).toBe(false)});
 it('rejects new CUT and FG dynamic analysis and absence inheritance',()=>{for(const profile of ['mixed-fixed-batch-confirmed-cut-qualified-v1','finished-garment-confirmed-v1']){expect(supports(profile,'analyses','resources')).toBe(false);expect(supports(profile,'analyses','inventory')).toBe(false);expect(supports(profile,'analyses','order_pegging')).toBe(false);expect(supports(profile,'actions','resource_unavailability')).toBe(false)}});
 it('rejects unknown profiles and keys; a missing input profile maps only to ordinary routing',()=>{expect(profileCapabilities('unknown').known).toBe(false);expect(supports('unknown','analyses','exceptions')).toBe(false);expect(supports(undefined,'analyses','exceptions')).toBe(true);expect(supports(undefined,'actions','resource_unavailability')).toBe(false);expect(supports('ordinary-routing-v1','actions','invented')).toBe(false)});
});
