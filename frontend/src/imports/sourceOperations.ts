export type SourceIdentity={id:string;enterprise_id:string};
export type SourceSync={id:string;request_id?:string;enterprise_id:string;status:string;revision?:number|null;groups:string[];readiness:{ready:boolean;blocking_issues:string[]};import_id?:string|null;version_id?:string|null;parent_version_id?:string|null;[key:string]:any};
const syncPattern=/^SYNC-[a-f0-9]{32}$/;
export function sourceWriteNeedsRecovery(error:unknown){return !(error instanceof ApiError)||error.code==='WRITE_STATUS_UNKNOWN';}
export function verifySourceSync(raw:unknown,source:SourceIdentity,requestId?:string):SourceSync{
 const value=raw as SourceSync;
 if(!value||!syncPattern.test(value.id)||value.enterprise_id!==source.enterprise_id||
   !['READY','BLOCKED','READING','COMMITTING','COMMITTED','UNCHANGED'].includes(value.status)||
   !Array.isArray(value.groups)||new Set(value.groups).size!==value.groups.length||value.groups.some(g=>typeof g!=='string')||
   (requestId&&value.id!==requestId&&value.request_id!==requestId))throw Error('来源或同步请求身份不一致，请读取原请求核对。');
 return value;
}
export function sourceConfirmation(sync:SourceSync,reviewer:string,groups:string[]){
 const name=reviewer.trim();
 if(sync.status!=='READY'||sync.readiness?.ready!==true||!sync.import_id||!Number.isInteger(sync.revision)||sync.revision!<1||
   !name||name.length>80||!sync.groups.length||groups.length!==sync.groups.length||new Set(groups).size!==groups.length||groups.some(g=>!sync.groups.includes(g)))throw Error('请核对可确认的来源、全部业务领域并填写核对人。');
 return {preview_id:sync.id,revision:sync.revision!,reviewer:name,groups};
}
export function sourceWorkspacePath(source:string,sync?:string){
 if(!/^live-[A-Za-z0-9_-]{1,60}$/.test(source)||(sync&&!syncPattern.test(sync)))throw Error('来源或同步身份无效。');
 return '/imports?'+new URLSearchParams({source,...(sync?{sync}:{})});
}
import {ApiError} from '../api/client';
