import contract from '../../../planning-capabilities.json';
type Area='analyses'|'actions';
type Rule={profiles:string[];reason:string;evidence_required?:string[]};
const rules=contract as {profiles:string[];analyses:Record<string,Rule>;actions:Record<string,Rule>};
/** Input identity only; evidence and each action's permission remain separate gates. */
export function inputProfile(input?:{foundation_projection?:{schema_revision?:string};rules?:{manufacturing_profile?:string}}|null){return input?.foundation_projection?.schema_revision??input?.rules?.manufacturing_profile??'ordinary-routing-v1'}
/** One explicit certification source shared with the service. Evidence is checked separately. */
export function supports(profile:string|undefined,area:Area,key:string){const id=profile??'ordinary-routing-v1';return rules.profiles.includes(id)&&!!rules[area][key]?.profiles.includes(id)}
export function capabilityReason(profile:string|undefined,area:Area,key:string){return supports(profile,area,key)?'':rules[area][key]?.reason??'当前模型未认证此能力。'}
export function profileCapabilities(profile:string|undefined){const id=profile??'ordinary-routing-v1';return {profile:id,known:rules.profiles.includes(id),analyses:Object.fromEntries(Object.entries(rules.analyses).map(([key,rule])=>[key,{supported:supports(id,'analyses',key),reason:rule.reason,evidence_required:rule.evidence_required??[]}])),actions:Object.fromEntries(Object.entries(rules.actions).map(([key,rule])=>[key,{supported:supports(id,'actions',key),reason:rule.reason,evidence_required:rule.evidence_required??[]}]))}}
