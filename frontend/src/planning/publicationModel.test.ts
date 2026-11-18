import {describe,it,expect} from 'vitest';
import {bindPublicationPreview,bindPublicationRecord} from './publicationModel';
const scope={planId:'PLAN-1',versionId:'DATA-1',runId:'RUN-1'};
const command={command_id:'PUB-1',plan_id:'PLAN-1',data_version_id:'DATA-1',run_id:'RUN-1',test_company_id:3};
describe('external publication evidence',()=>{
 it('binds a preview to the exact selected native run',()=>{expect(bindPublicationPreview({fingerprint:'a'.repeat(64),command},scope).command).toEqual(command)});
 it('rejects stale data and foreign run previews',()=>{for(const key of ['plan_id','data_version_id','run_id'])expect(()=>bindPublicationPreview({fingerprint:'a'.repeat(64),command:{...command,[key]:'OTHER'}},scope)).toThrow()});
 it('keeps an accepted draft distinct from actual execution',()=>{const r=bindPublicationRecord({command_id:'PUB-1',command,state:'ACCEPTED',receipt:{command_id:'PUB-1',state:'ACCEPTED',external_document_id:'mrp.production:2',external_state:'draft'}},scope);expect(r.state).toBe('ACCEPTED')});
 it('rejects a receipt belonging to another command',()=>{expect(()=>bindPublicationRecord({command_id:'PUB-1',command,state:'ACCEPTED',receipt:{command_id:'PUB-OTHER'}},scope)).toThrow()});
});
