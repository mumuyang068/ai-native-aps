import {describe,it,expect} from 'vitest';
import {verifySourceSync,sourceConfirmation,sourceWorkspacePath,sourceWriteNeedsRecovery} from './sourceOperations';
import {ApiError} from '../api/client';

const requestId='SYNC-'+'1'.repeat(32),canonicalId='SYNC-'+'2'.repeat(32);
const source={id:'live-ODOO03',enterprise_id:'ODOO-INTEROP03'};
const preview={id:requestId,enterprise_id:source.enterprise_id,status:'READY',revision:2,groups:['items','demand_lines'],readiness:{ready:true,blocking_issues:[]},import_id:'IMP-a1'};

describe('real source operation identity and confirmation',()=>{
 it('distinguishes a definite refusal from an unacknowledged write',()=>{
  expect(sourceWriteNeedsRecovery(new ApiError(401,'TASK_LOCAL_AUTHENTICATION_REQUIRED','expired'))).toBe(false);
  expect(sourceWriteNeedsRecovery(new ApiError(422,'SYNC_TASK_NOT_FOUND','absent'))).toBe(false);
  expect(sourceWriteNeedsRecovery(new ApiError(0,'WRITE_STATUS_UNKNOWN','lost'))).toBe(true);
  expect(sourceWriteNeedsRecovery(new Error('response identity mismatch'))).toBe(true);
 });
 it('refuses a response belonging to another enterprise or another request',()=>{
  expect(()=>verifySourceSync({...preview,enterprise_id:'FOREIGN'},source,requestId)).toThrow();
  expect(()=>verifySourceSync({...preview,id:canonicalId},source,requestId)).toThrow();
 });
 it('accepts a proved same-source pending request alias but commits its canonical identity',()=>{
  const value=verifySourceSync({...preview,id:canonicalId,request_id:requestId},source,requestId);
  expect(sourceConfirmation(value,'Reviewer',['items','demand_lines'])).toEqual({preview_id:canonicalId,revision:2,reviewer:'Reviewer',groups:['items','demand_lines']});
 });
 it('requires ready evidence, all unique groups and a real reviewer before confirmation',()=>{
  expect(()=>sourceConfirmation({...preview,status:'BLOCKED'},'Reviewer',preview.groups)).toThrow();
  expect(()=>sourceConfirmation({...preview,readiness:{ready:false,blocking_issues:['MISSING']}},'Reviewer',preview.groups)).toThrow();
  expect(()=>sourceConfirmation(preview,'',preview.groups)).toThrow();
  expect(()=>sourceConfirmation(preview,'Reviewer',['items','items'])).toThrow();
  expect(()=>sourceConfirmation(preview,'Reviewer',['items'])).toThrow();
 });
 it('keeps recovery navigation scoped and refuses injected source or task paths',()=>{
  expect(sourceWorkspacePath(source.id,requestId)).toBe('/imports?source=live-ODOO03&sync='+requestId);
  expect(()=>sourceWorkspacePath('../other',requestId)).toThrow();
  expect(()=>sourceWorkspacePath(source.id,'SYNC-../other')).toThrow();
 });
});
