import type {Dict} from '../api/contracts';
import type {ExportInput} from './reportExport';
/** Server CURRENT_REVIEW is relative to the note's own candidate, not the UI selection. */
export function reviewNotePosition(note:Dict,target:string,gate:Dict|undefined,scope:{planId:string;versionId:string;baselineRunId:string;profile?:string}){
 const present=(value:unknown)=>typeof value==='string'&&value.length>0,proof=(value:unknown)=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
 const bound=(identity:Dict|undefined,expectedTarget?:string)=>identity&&['plan_id','data_version_id','baseline_run_id','profile','target','candidate_run_id'].every(k=>present(identity[k]))&&identity.plan_id===scope.planId&&identity.data_version_id===scope.versionId&&identity.baseline_run_id===scope.baselineRunId&&(!scope.profile||identity.profile===scope.profile)&&(!expectedTarget||identity.target===expectedTarget);
 const identity=note.identity;
 if(!bound(identity)||!proof(note.fingerprint))return 'UNVERIFIED';
 if(identity.target!==target)return 'OTHER_CANDIDATE';
 if(!gate&&note.review_state==='HISTORY_NEEDS_RECHECK')return 'SELECTED_HISTORY';
 if(!bound(gate?.identity,target)||!proof(gate?.fingerprint)||gate?.identity.profile!==identity.profile)return 'UNVERIFIED';
 return gate!.identity.candidate_run_id===identity.candidate_run_id&&gate!.fingerprint===note.fingerprint&&note.review_state==='CURRENT_REVIEW'?'CURRENT_SELECTED':'SELECTED_HISTORY';
}
export function reviewNoteCanExport(note:Dict){return !!note.identity&&['plan_id','data_version_id','baseline_run_id','candidate_run_id','target','profile'].every(k=>typeof note.identity[k]==='string'&&!!note.identity[k])&&Array.isArray(note.evidence?.baseline)&&Array.isArray(note.evidence?.candidate)}
/** Keep the original review identity, including both sides of its comparison. */
export function reviewNoteExport(note:Dict):ExportInput{
 const identity={...note.identity,run_id:note.identity.candidate_run_id};
 const definitions=[['id','评审记录'],['identity','原始计划版本与基线候选身份'],['recommendation','讨论建议'],['reason','理由'],['object_scope','对象范围'],['next_action','后续核对'],['reviewer','本机填写人'],['created_at','记录时间ISO8601'],['fingerprint','原比较来源指纹'],['evidence','按基线和候选分别记录的来源'],['unknowns','当时确认的未知边界'],['acknowledge_limits','已确认理解边界'],['execution_authorized','执行授权']];
 return {scope:{planId:identity.plan_id,versionId:identity.data_version_id,runId:identity.run_id,target:identity.target},kind:'review-note',status:'PRESENT',
  data:{identity,timezone:'UTC',evidence_files:[...(note.evidence.baseline??[]),...(note.evidence.candidate??[])],review_state:note.review_state,execution_authorized:false},
  rows:[note],fields:definitions.map(([key,label])=>({key,label,meaning:label+'；原评审记录，日期保持原时区与单位，非执行批准'})),
  filters:{review_state:note.review_state,review_identity:note.identity,review_evidence:note.evidence,acknowledged_unknowns:note.unknowns??[],non_executable:true},
  sourceUrl:'/v1/enterprise-plans/'+encodeURIComponent(identity.plan_id)+'/review-notes?'+new URLSearchParams({data_version_id:identity.data_version_id,baseline_run_id:identity.baseline_run_id})};
}
