import {it,expect} from 'vitest';
import {prepareExport} from './reportExport';
import {reviewNoteExport,reviewNotePosition,reviewNoteCanExport} from './reviewNoteModel';
it('导出保留原始基线、候选、分侧来源和已确认的未知边界',async()=>{
 const n={id:'R',identity:{plan_id:'P',data_version_id:'V',baseline_run_id:'API-base',candidate_run_id:'API-next',target:'DRAFT-next',profile:'enterprise-accessory-supply-v1'},evidence:{baseline:[{artifact:'canonical_input.json',sha256:'a'.repeat(64)}],candidate:[{artifact:'canonical_input.json',sha256:'b'.repeat(64)}]},unknowns:['NOT_CHECKED'],review_state:'HISTORY_NEEDS_RECHECK',execution_authorized:false,created_at:'2026-10-21T00:00:00+00:00'};
 const result=await prepareExport(reviewNoteExport(n));expect(result.csv).toContain('API-base');expect(result.csv).toContain('NOT_CHECKED');expect(result.metadata.identity.baseline_run_id).toBe('API-base');expect(result.metadata.current_filters.review_evidence.baseline[0].sha256).toBe('a'.repeat(64));expect(result.metadata.current_filters.review_state).toBe('HISTORY_NEEDS_RECHECK');
});
const scope={planId:'P',versionId:'V',baselineRunId:'base',profile:'profile'};
const gate={identity:{plan_id:'P',data_version_id:'V',baseline_run_id:'base',profile:'profile',target:'new',candidate_run_id:'new-run'},fingerprint:'a'.repeat(64)};
const note={identity:{plan_id:'P',data_version_id:'V',baseline_run_id:'base',profile:'profile',target:'new',candidate_run_id:'new-run'},fingerprint:'a'.repeat(64),review_state:'CURRENT_REVIEW'};
it('shows current only for the selected candidate, current run and exact proof',()=>{expect(reviewNotePosition(note,'new',gate,scope)).toBe('CURRENT_SELECTED')});
it('does not relabel an old candidate CURRENT_REVIEW as the selected candidate review',()=>{expect(reviewNotePosition({...note,identity:{...note.identity,target:'old',candidate_run_id:'old-run'}},'new',gate,scope)).toBe('OTHER_CANDIDATE')});
it('keeps older runs and changed evidence as selected-candidate history',()=>{expect(reviewNotePosition({...note,identity:{...note.identity,candidate_run_id:'old-run'}},'new',gate,scope)).toBe('SELECTED_HISTORY');expect(reviewNotePosition({...note,fingerprint:'b'.repeat(64)},'new',gate,scope)).toBe('SELECTED_HISTORY');expect(reviewNotePosition(note,'new',undefined,scope)).toBe('UNVERIFIED')});
it('does not trust notes from another plan or version',()=>{expect(reviewNotePosition({...note,identity:{...note.identity,data_version_id:'foreign'}},'new',gate,scope)).toBe('UNVERIFIED')});
it('reads identity-bound historical opinions with no current certification gate',()=>{
 expect(reviewNotePosition({...note,review_state:'HISTORY_NEEDS_RECHECK'},'new',undefined,scope)).toBe('SELECTED_HISTORY');
 expect(reviewNotePosition({...note,review_state:'HISTORY_NEEDS_RECHECK',identity:{...note.identity,baseline_run_id:'foreign'}},'new',undefined,scope)).toBe('UNVERIFIED');
});

it('rejects absent gate/run/proof instead of treating undefined values as equal',()=>{expect(reviewNotePosition({...note,identity:{...note.identity,candidate_run_id:undefined},fingerprint:undefined},'new',undefined,scope)).toBe('UNVERIFIED');expect(reviewNotePosition(note,'new',{...gate,identity:{...gate.identity,plan_id:'foreign'}},scope)).toBe('UNVERIFIED');expect(reviewNotePosition(note,'new',{...gate,identity:{...gate.identity,profile:'foreign'}},{...scope,profile:undefined})).toBe('UNVERIFIED')});
it('keeps incomplete history readable without creating an unsafe export',()=>{expect(reviewNoteCanExport({})).toBe(false);expect(reviewNoteCanExport({...note,evidence:{baseline:[],candidate:[]}})).toBe(true);expect(reviewNoteCanExport(note)).toBe(false)});
