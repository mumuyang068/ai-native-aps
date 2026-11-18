import {describe,expect,it} from 'vitest';
import {planningPreparationCopy,readVersionPlanRecovery} from './DataVersionPage';

describe('version planning policy and uncertain creation recovery',()=>{
 it('uses current open demand and visible source wording for the continuity policy',()=>{
  const copy=planningPreparationCopy({policy_id:'APS-SIM-CONTINUITY-02-POLICY-V1',scope:'RESEARCH_SIMULATION_ONLY'});
  expect(copy.title).toContain('本次企业版本');expect(copy.intro).toContain('当前开放需求');expect(copy.details).toContain('可见来源');expect(copy.details).toContain('现有约束');
  expect(copy.details).not.toContain('45分钟');expect(copy.intro).not.toContain('全部确认需求');
 });
 it('keeps the 45 minute assumption exclusively on the exact Foundation policy and scope',()=>{
  expect(planningPreparationCopy({policy_id:'RP-FOUNDATION-01-20261024',scope:'RESEARCH_SIMULATION_ONLY'}).details).toContain('45分钟');
  for(const value of [{policy_id:'other-policy',scope:'RESEARCH_SIMULATION_ONLY'},{policy_id:'RP-FOUNDATION-01-20261024',scope:'OTHER_SCOPE'}])expect(planningPreparationCopy(value).details).not.toContain('45分钟');
 });
 it('keeps an unfamiliar policy and scope visible without borrowing Foundation assumptions',()=>{
  const copy=planningPreparationCopy({policy_id:'enterprise-approved-policy',scope:'CURRENT_VISIBLE_DEMAND'});
  expect(copy.policyId).toBe('enterprise-approved-policy');expect(copy.scopeLabel).toBe('CURRENT_VISIBLE_DEMAND');expect(copy.details).not.toContain('45分钟');
 });
 it('reads the current data version and all plans and recovers only that exact version',async()=>{
  const paths:string[]=[],versionId='DATA-aaaaaaaaaaaa',matching={id:'PLAN-aaaaaaaaaaaaaaaa',data_version_id:versionId},other={id:'PLAN-bbbbbbbbbbbbbbbb',data_version_id:'DATA-bbbbbbbbbbbb'};
  const state=await readVersionPlanRecovery(versionId,async path=>{paths.push(path);return path==='/v1/enterprise-plans'?[other,matching]:{id:versionId}});
  expect(paths.sort()).toEqual(['/v1/data-versions/'+versionId,'/v1/enterprise-plans'].sort());expect(state.existing).toBe(matching);expect(state.plans).toEqual([other,matching]);
 });
 it('does not treat another version or an absent plan as a completed creation',async()=>{
  const versionId='DATA-aaaaaaaaaaaa';
  const state=await readVersionPlanRecovery(versionId,async path=>path==='/v1/enterprise-plans'?[{id:'PLAN-bbbbbbbbbbbbbbbb',data_version_id:'DATA-bbbbbbbbbbbb'}]:{id:versionId});
  expect(state.existing).toBeNull();
  await expect(readVersionPlanRecovery(versionId,async path=>path==='/v1/enterprise-plans'?[]:{id:'DATA-bbbbbbbbbbbb'})).rejects.toThrow();
 });
});
