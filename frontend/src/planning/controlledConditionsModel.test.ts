import {expect,it} from 'vitest';
import {scenarioPreview} from '../scenarios/scenarioPreview';
import type {Dict} from '../api/contracts';
import {controlledConditionsRequest,qualifiedSewOperators,firmConditionOrders,updateControlledEditor,verifyControlledPreview,verifyControlledDraft,type ControlledScope,type ControlledFields,type ControlledPreview} from './controlledConditionsModel';

const scope:ControlledScope={planId:'P-X',versionId:'V-X',baselineRunId:'R-X',runId:'R-X',target:'baseline',profile:'enterprise-accessory-supply-v1',sourceSha256:'a'.repeat(64),snapshotSha256:'b'.repeat(64)};
const input:Dict={planning_at:'2026-11-02T08:00:00+08:00',horizon:{end_at:'2026-11-09T18:00:00+08:00'},items:[{id:'FG',kind:'FINISHED'}],production_recipes:[{id:'CUT',routing_revision_id:'RC',output_item_id:'C'},{id:'SEW',routing_revision_id:'RS',output_item_id:'S'},{id:'FINISH',routing_revision_id:'RF',output_item_id:'FG'}],routing_revisions:[{id:'RC',operations:[{duration_model:'FIXED_BATCH'}]},{id:'RS',operations:[{duration_model:'FIXED_PLUS_PER_UNIT',operator_requirement:{pool_id:'SEW-POOL',skill_id:'TEE'}}]},{id:'RF',operations:[{duration_model:'FIXED_PLUS_PER_UNIT'}]}],resources:[{id:'PERSON-Z',role:'OPERATOR',pool_id:'SEW-POOL',availability_calendar_id:'CAL-OLD'},{id:'PERSON-WRONG-POOL',role:'OPERATOR',pool_id:'OTHER'},{id:'PERSON-EXPIRED',role:'OPERATOR',pool_id:'SEW-POOL'},{id:'PERSON-CUT',role:'OPERATOR',pool_id:'SEW-POOL'},{id:'POOL',role:'OPERATOR_POOL',pool_id:'SEW-POOL'}],resource_skills:[{resource_id:'PERSON-Z',skill_id:'TEE',effective_from:'2026-11-01T00:00:00+08:00',effective_to:'2026-11-10T00:00:00+08:00'},{resource_id:'PERSON-WRONG-POOL',skill_id:'TEE',effective_from:'2026-11-01T00:00:00+08:00',effective_to:'2026-11-10T00:00:00+08:00'},{resource_id:'PERSON-EXPIRED',skill_id:'TEE',effective_from:'2026-11-01T00:00:00+08:00',effective_to:'2026-11-03T00:00:00+08:00'},{resource_id:'PERSON-CUT',skill_id:'CUT',effective_from:'2026-11-01T00:00:00+08:00',effective_to:'2026-11-10T00:00:00+08:00'}],demand_lines:[{id:'D-EXACT',kind:'FIRM_ORDER',priority:7,order_ref:'ORDER-X'},{id:'FCAST',kind:'FORECAST',priority:2}]};
const fields:ControlledFields={includeOrder:true,demandId:'D-EXACT',priority:'2',includeOperator:true,resourceId:'PERSON-Z',start:'2026-11-03T08:00',end:'2026-11-03T10:00',reason:'  核对资源取舍  '};
const body={data_version_id:'V-X',baseline_run_id:'R-X',run_id:'R-X',target:'baseline' as const,order:{demand_id:'D-EXACT',priority:2},operator:{resource_id:'PERSON-Z',start_at:'2026-11-03T00:00:00.000Z',end_at:'2026-11-03T02:00:00.000Z'},reason:'核对资源取舍'};
const identity={plan_id:'P-X',data_version_id:'V-X',baseline_run_id:'R-X',run_id:'R-X',target:'baseline',profile:'enterprise-accessory-supply-v1',source_sha256:'a'.repeat(64),snapshot_sha256:'b'.repeat(64)};
const preview:ControlledPreview={identity,fingerprint:'c'.repeat(64),changes:{action:'simulate_controlled_conditions',conditions:{order:body.order,operator:body.operator},order:{demand_id:'D-EXACT',before_priority:7,after_priority:2},operator:{...body.operator,before_calendar_id:'CAL-OLD',after_calendar_id:'CAL-SIM',removed_seconds:7200},reason:body.reason,simulation_only:true,business_permission:'NOT_CONFIRMED',execution_authorized:false,engine_executed:false},simulation_only:true,engine_executed:false,calculation_submitted:false};

it('SEW candidates follow the actual route pool, skill and whole-horizon qualification',()=>{
 expect(qualifiedSewOperators(input).map(r=>r.id)).toEqual(['PERSON-Z']);
 expect(firmConditionOrders(input).map(r=>r.id)).toEqual(['D-EXACT']);
 const changed=structuredClone(input);changed.routing_revisions[1].operations[0].operator_requirement!.skill_id='CUT';
 expect(qualifiedSewOperators(changed).map(r=>r.id)).toEqual(['PERSON-CUT']);
});

it('request carries both exact conditions and interprets dates in the business timezone',()=>{
 expect(controlledConditionsRequest(scope,input,fields,'Asia/Shanghai')).toEqual({body,error:''});
 expect(controlledConditionsRequest(scope,input,{...fields,includeOrder:false},'Asia/Shanghai').body).toEqual({...body,order:undefined});
 expect(controlledConditionsRequest(scope,input,{...fields,includeOperator:false,start:'',end:''},'Asia/Shanghai').body).toEqual({...body,operator:undefined});
});

it.each([
 [{includeOrder:false,includeOperator:false},'至少选择'],[{demandId:'FCAST'},'Firm'],[{priority:'7'},'不同'],[{priority:'-1'},'优先级'],[{resourceId:'PERSON-CUT'},'合格'],[{start:''},'时段'],[{end:'2026-11-03T07:00'},'时段'],[{reason:'   '},'理由']
])('invalid conditions cannot produce a preview request: %j',(patch,message)=>{
 const result=controlledConditionsRequest(scope,input,{...fields,...patch},'Asia/Shanghai');expect(result.body).toBeNull();expect(result.error).toContain(message);
});

it.each([{target:'draft-X'},{runId:'OTHER'},{profile:'finished-garment-confirmed-v1'}])('controlled action remains confined to this enterprise baseline: %j',patch=>{
 expect(controlledConditionsRequest({...scope,...patch},input,fields,'Asia/Shanghai').body).toBeNull();
});

it.each([{end:''},{start:'',end:''},{}])('unchanged partial native date input preserves the editor reference: %j',patch=>{
 const editor={fields:{...fields,start:'',end:''},preview:null,acknowledged:false};
 expect(updateControlledEditor(editor,patch)).toBe(editor);
});

it('unchanged field values preserve an accepted preview and acknowledgement',()=>{
 const editor={fields,preview,acknowledged:true};
 expect(updateControlledEditor(editor,{priority:'2',includeOperator:true})).toBe(editor);
});

it('an actual field change invalidates the preview and acknowledgement even with unchanged fields in the patch',()=>{
 const updated=updateControlledEditor({fields,preview,acknowledged:true},{priority:'0',end:fields.end});
 expect(updated.fields.priority).toBe('0');expect(updated.preview).toBeNull();expect(updated.acknowledged).toBe(false);
});

it('only a read-only preview bound to the current scope and conditions may be confirmed',()=>{
 expect(verifyControlledPreview(preview,scope,body)).toEqual(preview);
 for(const changed of [{...preview,identity:{...identity,run_id:'OTHER'}},{...preview,identity:{...identity,source_sha256:'d'.repeat(64)}},{...preview,engine_executed:true},{...preview,calculation_submitted:true},{...preview,changes:{...preview.changes,conditions:{...preview.changes.conditions,order:{demand_id:'OTHER',priority:2}}}}])expect(()=>verifyControlledPreview(changed,scope,body)).toThrow();
});

it('a confirmed response navigates only to its own saved NOT_RUN draft',()=>{
 const response={identity,fingerprint:preview.fingerprint,simulation_only:true,engine_executed:false,calculation_submitted:false,draft:{id:'DRAFT-X',state:'DRAFT',run_id:null,changes:preview.changes},run:{state:'NOT_RUN'}};
 expect(verifyControlledDraft(response,scope,body,preview)).toBe('DRAFT-X');
 for(const changed of [{...response,run:{state:'SUCCEEDED'}},{...response,draft:{...response.draft,run_id:'R-DRAFT'}},{...response,identity:{...identity,plan_id:'OTHER'}},{...response,fingerprint:'d'.repeat(64)},{...response,draft:{...response.draft,changes:{...preview.changes,order:{...preview.changes.order,after_priority:9}}}}])expect(()=>verifyControlledDraft(changed,scope,body,preview)).toThrow();
});

it('controlled priority and absence show their exact objects and preserve both audits',()=>{
 const rows=scenarioPreview({action:'simulate_controlled_conditions',reason:'核对资源取舍',order:{demand_id:'D-EXACT',before_priority:7,after_priority:2},operator:{resource_id:'PERSON-Z',start_at:'2026-11-03T08:00:00+08:00',end_at:'2026-11-03T10:00:00+08:00',removed_seconds:7200,before_calendar_id:'CAL-OLD',after_calendar_id:'CAL-SIM'}});
 expect(rows).toContainEqual(['精确 Firm 需求行','D-EXACT']);
 expect(rows).toContainEqual(['需求 priority','7 → 2']);
 expect(rows).toContainEqual(['具名 SEW 人员','PERSON-Z']);
 expect(rows).toContainEqual(['缺勤时段（含时区）','2026-11-03T08:00:00+08:00 → 2026-11-03T10:00:00+08:00']);
 expect(rows).toContainEqual(['输入声明日历','CAL-OLD → CAL-SIM']);
 expect(rows).toContainEqual(['扣减原声明可用时间','120 分钟']);
 expect(rows.join(' ')).not.toMatch(/急单编号|undefined/);
});

it('operator-only controlled conditions do not invent an order or execution result',()=>{
 const rows=scenarioPreview({action:'simulate_controlled_conditions',reason:'人员假设',order:null,operator:{resource_id:'PERSON-Z',start_at:'A',end_at:'B',removed_seconds:3600,before_calendar_id:'CAL-OLD',after_calendar_id:'CAL-SIM'}});
 expect(rows).toContainEqual(['实际执行','未授权']);
 expect(rows.join(' ')).not.toMatch(/急单编号|需求 priority|undefined/);
});

it('backend local-offset timestamps preserve the same selected absence instants',()=>{
 const normalized={...preview,changes:{...preview.changes,conditions:{...preview.changes.conditions,operator:{resource_id:'PERSON-Z',start_at:'2026-11-03T08:00:00+08:00',end_at:'2026-11-03T10:00:00+08:00'}},operator:{...preview.changes.operator,start_at:'2026-11-03T08:00:00+08:00',end_at:'2026-11-03T10:00:00+08:00'}}};
 expect(()=>verifyControlledPreview(normalized,scope,body)).not.toThrow();
});

it('draft audit must preserve the exact original priority and calendar accepted in preview',()=>{
 const response={identity,fingerprint:preview.fingerprint,simulation_only:true,engine_executed:false,draft:{id:'DRAFT-X',state:'DRAFT',run_id:null,changes:{...preview.changes,order:{...preview.changes.order,before_priority:99}}},run:{state:'NOT_RUN'}};
 expect(()=>verifyControlledDraft(response,scope,body,preview)).toThrow();
});
