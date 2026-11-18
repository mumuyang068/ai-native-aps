import type {Dict} from '../api/contracts';
export type PublicationScope={planId:string;versionId:string;runId:string};
function same(command:Dict,scope:PublicationScope){return command&&command.plan_id===scope.planId&&command.data_version_id===scope.versionId&&command.run_id===scope.runId}
export function bindPublicationPreview(value:Dict,scope:PublicationScope){
 if(!same(value?.command,scope)||!/^([a-f0-9]{64})$/.test(value?.fingerprint??''))throw Error('发布预览与所选计划、资料或运行不一致，请重新核对。');
 return value;
}
export function bindPublicationRecord(value:Dict,scope:PublicationScope){
 if(!same(value?.command,scope)||value.command_id!==value.command.command_id||
  !['SUBMITTED','UNKNOWN','ACCEPTED','REJECTED','PARTIALLY_EXECUTED','EXECUTED','RECONCILIATION_REQUIRED'].includes(value.state)||
  (value.receipt&&value.receipt.command_id!==value.command_id))throw Error('发布回执的业务身份不一致，请核对原命令。');
 return value;
}
