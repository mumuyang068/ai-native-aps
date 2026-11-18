import {expect,it} from 'vitest';
import {scenarioPreview} from '../scenarios/scenarioPreview';
import {analysisContextSearch} from './analysisModel';
import {inventoryDecisionReviewSearch,inventoryPolicyEvidenceText,inventoryPolicyOutcomeRows,inventoryDecisionRequest,verifyInventoryDecisionReport,verifyInventoryDecisionPreview,verifyInventoryDecisionDraft,updateInventoryEditor,inventoryPolicyLines,inventoryDecisionSearch,inventoryDecisionReturn,type InventoryDecisionFields,type InventoryDecisionScope,type InventoryDecisionPreview,type InventoryDecisionRequest} from './inventoryDecisionModel';
const scope:InventoryDecisionScope={planId:'P',versionId:'V',baselineRunId:'R',runId:'R',target:'baseline',profile:'enterprise-accessory-supply-v1',sourceSha256:'a'.repeat(64),snapshotSha256:'b'.repeat(64)};
const policy={item_id:'LABEL',location_id:'FACTORY',uom:'piece',minimum:'2',maximum:'8',start_at:'2026-11-01T00:00:00+08:00',end_at:'2026-12-01T00:00:00+08:00'};
const option={id:'SMALL',label:'小批条件',source_id:'S',supplier_id:'SUP',item_id:'LABEL',location_id:'FACTORY',uom:'piece',minimum_order_quantity:'2',order_multiple:'2',maximum_order_quantity:'6',total_capacity_quantity:'12',lead_time_seconds:86400,qc_wait_seconds:86400,earliest_order_at:'2026-11-01T00:00:00+08:00',latest_order_at:'2026-11-27T00:00:00+08:00',effective_from:'2026-11-01T00:00:00+08:00',effective_to:'2026-12-01T00:00:00+08:00',fact_kind:'SYNTHETIC_RESEARCH_ASSUMPTION',provenance:{source_record_id:'TERM-1'}};
const identity={plan_id:'P',data_version_id:'V',baseline_run_id:'R',run_id:'R',target:'baseline',profile:scope.profile,source_sha256:scope.sourceSha256,snapshot_sha256:scope.snapshotSha256};
const report={identity,catalog:[option],current_policy:null,original_policy:null,selected_supply:null,source:{id:'S',supplier_id:'SUP',item_id:'LABEL',location_id:'FACTORY'},stock_facts:[{item_id:'LABEL',location_id:'FACTORY',uom:'piece',on_hand:'20',quality_blocked:'8',other_nonnettable:'0'}],simulation_only:true,business_permission:'NOT_CONFIRMED',execution_authorized:false};
const fields:InventoryDecisionFields={includePolicy:true,includeSource:true,minimum:'2',maximum:'8',sourceOptionId:'SMALL',reason:' 核对有来源补给与最低目标 '};
const input={planning_at:policy.start_at,horizon:{end_at:policy.end_at},inventory_buffer_policy:null};
function prepared(f=fields){return inventoryDecisionRequest(scope,report,input,'LABEL','FACTORY',f)}
const body:InventoryDecisionRequest={data_version_id:'V',baseline_run_id:'R',run_id:'R',target:'baseline',policy,source_option_id:'SMALL',reason:fields.reason.trim()};
const changes={action:'simulate_inventory_decision',conditions:{policy,source_option_id:'SMALL'},reason:body.reason,before:{policy:null,supply:null},after:{policy:{...policy,provenance:{source_record_id:'SIM'},semantics:'SOFT_MINIMUM_REPLENISH_TO_MAXIMUM'},supply:{...option,price_status:'NOT_PROVIDED',unit_price:null,currency:null}},simulation_only:true,business_permission:'NOT_CONFIRMED',execution_authorized:false,engine_executed:false};
const preview:InventoryDecisionPreview={identity,fingerprint:'c'.repeat(64),changes,simulation_only:true,engine_executed:false,calculation_submitted:false};
it('exact buffer and frozen source terms form one typed request without provenance or facts',()=>{
 expect(prepared()).toEqual({body,error:''});
 expect(inventoryDecisionRequest(scope,report,input,'LABEL','FACTORY',{...fields,includePolicy:false}).body).toEqual({...body,policy:undefined});
 expect(inventoryDecisionRequest(scope,report,input,'LABEL','FACTORY',{...fields,includeSource:false}).body).toEqual({...body,source_option_id:undefined});
});
it.each([{minimum:''},{minimum:'-1'},{maximum:'1'},{minimum:'2.000000001'},{minimum:'02'},{sourceOptionId:'OTHER'},{reason:' '},{includePolicy:false,includeSource:false}])('invalid explicit fields cannot preview %j',patch=>{expect(prepared({...fields,...patch}).body).toBeNull()});
it('explicit zero remains a value and decimal comparison is not rounded to integer',()=>{
 expect(prepared({...fields,minimum:'0',maximum:'0'}).body?.policy?.minimum).toBe('0');
 expect(prepared({...fields,minimum:'2.00000001',maximum:'2'}).body).toBeNull();
});
it.each([{target:'DRAFT-OLD'},{runId:'OTHER'},{profile:'two-level-wip-v1'}])('only the current completed enterprise baseline is writable %j',patch=>{
 expect(inventoryDecisionRequest({...scope,...patch},report,input,'LABEL','FACTORY',fields).body).toBeNull();
});
it('same item name at another location is not a usable source or stock fact',()=>{
 expect(inventoryDecisionRequest(scope,report,input,'LABEL','OTHER',fields).body).toBeNull();
 expect(()=>verifyInventoryDecisionReport({...report,identity:{...identity,data_version_id:'OTHER'}},scope,'LABEL','FACTORY')).toThrow();
 expect(()=>verifyInventoryDecisionReport({...report,catalog:[{...option,source_id:'OTHER'}]},scope,'LABEL','FACTORY')).toThrow();
});
it('real edits invalidate accepted previews and acknowledgement; no-op native input does not',()=>{
 const editor={fields,preview,acknowledged:true};
 expect(updateInventoryEditor(editor,{minimum:'2'})).toBe(editor);
 expect(updateInventoryEditor(editor,{maximum:'10'})).toEqual({fields:{...fields,maximum:'10'},preview:null,acknowledged:false});
});
it('preview must bind scope, source SHA, typed conditions, unknown prices and read-only state',()=>{
 expect(verifyInventoryDecisionPreview(preview,scope,body,report)).toEqual(preview);
 for(const v of [{...preview,identity:{...identity,source_sha256:'f'.repeat(64)}},{...preview,engine_executed:true},{...preview,changes:{...changes,conditions:{...changes.conditions,policy:{...policy,maximum:'9'}}}},{...preview,changes:{...changes,after:{...changes.after,supply:{...option,unit_price:0,currency:'CNY'}}}}])expect(()=>verifyInventoryDecisionPreview(v,scope,body,report)).toThrow();
});
it('saved draft is the same accepted candidate and NOT_RUN, never a historical result',()=>{
 const value={identity,fingerprint:preview.fingerprint,simulation_only:true,engine_executed:false,calculation_submitted:false,draft:{id:'DRAFT-NEW',state:'DRAFT',run_id:null,changes},run:{state:'NOT_RUN'}};
 expect(verifyInventoryDecisionDraft(value,scope,body,preview,report)).toBe('DRAFT-NEW');
 for(const v of [{...value,run:{state:'SUCCEEDED',solve_run_id:'OLD'}},{...value,draft:{...value.draft,run_id:'OLD'}},{...value,fingerprint:'d'.repeat(64)},{...value,draft:{...value.draft,changes:{...changes,after:{...changes.after,policy:{...policy,minimum:'1'}}}}}])expect(()=>verifyInventoryDecisionDraft(v,scope,body,preview,report)).toThrow();
});
it('policy target lines require exact buffer, complete events, current native PASS and interval overlap',()=>{
 const series={item_id:'LABEL',location_id:'FACTORY',uom:'piece',complete:true};
 const a=Date.parse('2026-11-02T00:00:00Z'),b=Date.parse('2026-11-07T00:00:00Z');
 expect(inventoryPolicyLines(policy,series,a,b,'PASS')).toEqual({minimum:2,maximum:8,start:a,end:b});
 expect(inventoryPolicyLines(policy,series,a,b,'NOT_CHECKED')).toBeNull();
 expect(inventoryPolicyLines(policy,{...series,complete:false},a,b,'PASS')).toBeNull();
 expect(inventoryPolicyLines(policy,{...series,location_id:'OTHER'},a,b,'PASS')).toBeNull();
 expect(inventoryPolicyLines({...policy,start_at:'2027-01-01T00:00:00Z'},series,a,b,'PASS')).toBeNull();
});
it('return restores original object and time only in same plan and version, stripping stale run events after draft',()=>{
 const previous=new URLSearchParams({analysis:'inventory',analysis_item:'LABEL',analysis_location:'FACTORY',analysis_start:'2026-11-03T00:00Z',analysis_event:'OLD',target:'baseline'});
 const next=inventoryDecisionSearch(previous,scope,'LABEL','FACTORY');
 expect(next.get('analysis')).toBe('inventory-decision');
 expect(inventoryDecisionReturn(next,scope).toString()).toBe(previous.toString());
 const candidate={...scope,target:'DRAFT',runId:'RUN-C'};
 const returned=inventoryDecisionReturn(next,candidate);
 expect(returned.get('analysis_item')).toBe('LABEL');expect(returned.get('analysis_start')).toBe(previous.get('analysis_start'));expect(returned.has('analysis_event')).toBe(false);expect(returned.get('target')).toBe('DRAFT');
 expect(inventoryDecisionReturn(next,{...candidate,versionId:'OTHER'}).get('analysis')).toBe('schedule');
});

it('the same draft overview presents actual policy and source terms rather than inventing a rush order',()=>{
 const rows=scenarioPreview(changes);
 expect(rows).toContainEqual(['精确库存对象','LABEL @ FACTORY · piece']);
 expect(rows).toContainEqual(['最低 / 最高目标','2 / 8']);
 expect(rows).toContainEqual(['来源条款','S / SMALL']);
 expect(rows).toContainEqual(['模型采购金额','未知：来源未提供单价与币种']);
 expect(rows.join(' ')).not.toMatch(/急单编号|undefined/);
});

it('undeclared policy or source changes cannot hitchhike on a single-condition preview',()=>{
 const onlyPolicy={...body,source_option_id:undefined},onlySource={...body,policy:undefined};
 expect(()=>verifyInventoryDecisionPreview({...preview,changes:{...changes,conditions:{policy}}},scope,onlyPolicy,report)).toThrow();
 expect(()=>verifyInventoryDecisionPreview({...preview,changes:{...changes,conditions:{source_option_id:'SMALL'}}},scope,onlySource,report)).toThrow();
});
it('missing stock evidence cannot be silently replaced with an empty stock panel',()=>{
 expect(()=>verifyInventoryDecisionReport({...report,stock_facts:undefined},scope,'LABEL','FACTORY')).toThrow();
});

it('policy outcome preserves real quantities, unknown minimum time, and distinct first gap time',()=>{
 const rows=inventoryPolicyOutcomeRows({status:'PRESENT',goal_status:'BELOW_SOFT_TARGET',minimum_quantity:'0',ending_quantity:'4',ending_gap_quantity:'0',maximum_gap_quantity:'2',minimum_at:null,first_gap_at:'2026-11-03T00:00Z',gap_duration_seconds:null},'piece',v=>'TIME:'+v);
 expect(rows).toContainEqual(['期间最低净可用','0 件']);
 expect(rows).toContainEqual(['最低余额时点','未采集 / 未核验']);
 expect(rows).toContainEqual(['首次低于最低目标','TIME:2026-11-03T00:00Z']);
 expect(rows).toContainEqual(['期末目标差额','0 件']);
 expect(rows).toContainEqual(['低于目标累计时长','未采集 / 未核验']);
});
it('unverified outcomes never turn missing quantities or timing into zero or no gap',()=>{
 const rows=inventoryPolicyOutcomeRows({status:'NOT_CHECKED',goal_status:'NOT_CHECKED',minimum_quantity:null,ending_quantity:null,first_gap_at:null},'piece',v=>v);
 expect(rows.every(([,v])=>v==='未采集 / 未核验')).toBe(true);
 expect(inventoryPolicyOutcomeRows({status:'PRESENT',goal_status:'GOAL_MET',minimum_quantity:'2',first_gap_at:null},'piece',v=>v)).toContainEqual(['首次低于最低目标','未观察到']);
});

it('native default isolation never masquerades as a declared zero policy',()=>{
 expect(inventoryPolicyEvidenceText(null,'PASS')).toBe('未声明目标，原生缺省隔离已核验');
 expect(inventoryPolicyEvidenceText({...policy,minimum:'0',maximum:'0'},'PASS')).toBe('已声明目标，原生字段核验通过');
 expect(inventoryPolicyEvidenceText(null,'NOT_CHECKED')).toBe('缺省隔离尚未核验');
 expect(inventoryPolicyEvidenceText(policy,'FAIL')).toBe('原生政策字段存在违规');
});
it('minimum_at and first_gap_at remain separate authenticated observations',()=>{
 const rows=inventoryPolicyOutcomeRows({status:'PRESENT',goal_status:'BELOW_SOFT_TARGET',minimum_quantity:'0',minimum_at:'2026-11-05T12:00Z',first_gap_at:'2026-11-03T00:00Z'},'piece',v=>'TIME:'+v);
 expect(rows).toContainEqual(['最低余额时点','TIME:2026-11-05T12:00Z']);
 expect(rows).toContainEqual(['首次低于最低目标','TIME:2026-11-03T00:00Z']);
});

it('comparison round-trip retains the inventory object, time and safe origin in the same plan',()=>{
 const original=new URLSearchParams({target:'baseline',analysis:'inventory',analysis_item:'LABEL',analysis_location:'FACTORY',analysis_start:'2026-11-03T00:00Z',analysis_end:'2026-11-09T00:00Z',analysis_event:'OLD-EVENT'});
 const work=inventoryDecisionSearch(original,scope,'LABEL','FACTORY');
 const compare=analysisContextSearch(work,'DRAFT-X'),back=analysisContextSearch(compare,'DRAFT-X');
 expect(back.get('analysis')).toBe('inventory-decision');
 expect(back.get('analysis_item')).toBe('LABEL');expect(back.get('analysis_location')).toBe('FACTORY');
 expect(back.get('analysis_start')).toBe(original.get('analysis_start'));expect(back.get('analysis_end')).toBe(original.get('analysis_end'));
 expect(back.get('inventory_decision_return_scope')).toBe(work.get('inventory_decision_return_scope'));
 expect(back.has('analysis_event')).toBe(false);
 const returned=inventoryDecisionReturn(back,{...scope,target:'DRAFT-X',runId:'R-CANDIDATE'});
 expect(returned.get('analysis')).toBe('inventory');expect(returned.get('analysis_item')).toBe('LABEL');expect(returned.get('target')).toBe('DRAFT-X');expect(returned.has('analysis_event')).toBe(false);
 expect(inventoryDecisionReturn(back,{...scope,planId:'OTHER'}).get('analysis')).toBe('schedule');
 expect(inventoryDecisionReturn(back,{...scope,versionId:'OTHER'}).has('analysis_item')).toBe(false);
});

it('both inventory review entry points select the current candidate while preserving object time and origin',()=>{
 const original=new URLSearchParams({analysis:'inventory',target:'baseline',analysis_item:'LABEL',analysis_location:'FACTORY',analysis_start:'2026-11-03T00:00Z',analysis_end:'2026-11-09T00:00Z'});
 const work=inventoryDecisionSearch(original,scope,'LABEL','FACTORY');work.set('target','DRAFT-CURRENT');work.set('review_drafts','DRAFT-OTHER');
 const review=inventoryDecisionReviewSearch(work,{...scope,target:'DRAFT-CURRENT',runId:'R-CURRENT'});
 expect(review.get('analysis')).toBe('review');expect(review.get('target')).toBe('DRAFT-CURRENT');expect(review.get('review_drafts')).toBe('DRAFT-CURRENT');
 for(const key of ['analysis_item','analysis_location','analysis_start','analysis_end','inventory_decision_return','inventory_decision_return_scope'])expect(review.get(key)).toBe(work.get(key));
 expect(inventoryDecisionReturn(review,{...scope,target:'DRAFT-CURRENT',runId:'R-CURRENT'}).get('analysis_item')).toBe('LABEL');
 expect(inventoryDecisionReturn(review,{...scope,planId:'OTHER'}).get('analysis')).toBe('schedule');
});
it('baseline inventory review keeps the existing review selection rules',()=>{
 const original=new URLSearchParams({analysis:'inventory-decision',target:'baseline',analysis_item:'LABEL',review_drafts:'DRAFT-OLD'});
 expect(inventoryDecisionReviewSearch(original,scope).get('review_drafts')).toBe('DRAFT-OLD');
 original.delete('review_drafts');expect(inventoryDecisionReviewSearch(original,scope).has('review_drafts')).toBe(false);
});
