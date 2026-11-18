import {describe,it,expect} from 'vitest';
import type {Dict} from '../api/contracts';
import {cockpitWorkRows,selectCockpitWork,cockpitQueueStatus,cockpitPerformanceDrill,cockpitWorkDrill,type WorkRow} from './cockpitWorkModel';
import {cockpitDrill,cockpitReturn} from './cockpitNavigation';
const scope={planId:'PLAN-1',versionId:'DATA-1',runId:'API-1',target:'baseline',profile:'routing-alternate-capacity-v1'};
const pass={status:'PASS',issues:[],missing_evidence:[]};
const input=()=>({identity:{plan_id:scope.planId,data_version_id:scope.versionId,run_id:scope.runId,target:scope.target,profile:scope.profile},known_business_fail:false,manufacturing:{status:'PRESENT',validation:pass,tasks:[{id:'API-1:OP:2',manufacture_id:'API-1:MO:1',operation_id:'INSPECT',quantity:'16',uom:'件',item_id:'STAGE',location_id:'FACTORY',start_at:'2026-11-05T10:20:00+08:00',end_at:'2026-11-05T11:08:00+08:00',status:'proposed',resource_ids:['INSPECT-LINE','P1'],sources:[{artifact:'PlanResult.json'}]}]}} as Dict);
const a='2026-11-01T00:00:00+08:00',b='2026-12-01T00:00:00+08:00';
describe('cockpit original-owner queues',()=>{
 it('uses leaf task and actual resource identities with a scoped original owner route',()=>{const r=cockpitWorkRows(input(),{}, {},scope);expect(r).toHaveLength(1);expect(r[0].detail).toContain('INSPECT-LINE / P1');expect(r[0].params).toMatchObject({analysis:'manufacturing',rc_task:'API-1:OP:2',rc_group:'API-1:MO:1'});expect(r[0].detail).not.toContain('M2');});
 it.each(['plan_id','data_version_id','run_id','target','profile'])('rejects wrong %s rather than showing old rows',field=>{const d=input();d.identity[field]='OTHER';expect(()=>cockpitWorkRows(d,{}, {},scope)).toThrow();});
 it('keeps a known business FAIL ahead of available records',()=>{const d=input();d.known_business_fail=true;expect(cockpitWorkRows(d,{}, {},scope)).toEqual([]);});
 it('requires original manufacturing PASS and exact unique task source',()=>{const d=input();d.manufacturing.validation={status:'NOT_CHECKED'};expect(cockpitWorkRows(d,{}, {},scope)).toEqual([]);d.manufacturing.validation=pass;d.manufacturing.tasks.push({...d.manufacturing.tasks[0]},{...d.manufacturing.tasks[0],id:'OTHER:OP:3'});expect(cockpitWorkRows(d,{}, {},scope)).toHaveLength(1);});
 it('keeps half-open point events and intersecting operations, unknown dates visible',()=>{const mo=cockpitWorkRows(input(),{}, {},scope)[0],po:WorkRow={...mo,id:'PO',domain:'purchase',start:a,end:b},unknown:WorkRow={...po,id:'UNKNOWN',start:null,end:null};expect(selectCockpitWork([po,unknown],'purchase','start','',a,b).map(r=>r.id)).toEqual(['UNKNOWN','PO']);expect(selectCockpitWork([po],'purchase','end','',a,b)).toEqual([]);expect(selectCockpitWork([mo],'manufacturing','start','proposed','2026-11-05T10:30:00+08:00','2026-11-05T10:40:00+08:00')).toHaveLength(1);});
 it('rejects invalid date windows and filters statuses without adding quantities',()=>{expect(()=>selectCockpitWork([],'purchase','start','',b,a)).toThrow();const r=cockpitWorkRows(input(),{}, {},scope);expect(selectCockpitWork(r,'manufacturing','start','confirmed',a,b)).toEqual([]);expect(selectCockpitWork(r,'manufacturing','start','',r[0].end!,b)).toEqual([]);});
 it('does not fabricate transfers without the original distribution PASS',()=>{const d=input();expect(cockpitWorkRows(d,{}, {distribution_suggestions:[{id:'API-1:DO:5',quantity:'20'}]},scope).filter(x=>x.domain==='distribution')).toEqual([]);});
 it('restores bounded date/domain/status/category and exact focus on return',()=>{const q=new URLSearchParams({target:'baseline',analysis:'cockpit',cockpit_from:a,cockpit_to:b,cockpit_domain:'manufacturing',cockpit_status:'proposed',cockpit_performance:'PARTIALLY_PLANNED'});const drilled=cockpitDrill(q,scope,'queue:mo:API-1:OP:2',{analysis:'manufacturing',rc_task:'API-1:OP:2'});const restored=cockpitReturn(drilled,scope)!;expect(restored.get('cockpit_domain')).toBe('manufacturing');expect(restored.get('cockpit_focus')).toBe('queue:mo:API-1:OP:2');expect(restored.get('cockpit_from')).toBe(a);expect(restored.get('cockpit_performance')).toBe('PARTIALLY_PLANNED');expect(cockpitReturn(drilled,{...scope,runId:'OTHER'})).toBeNull();});
 it('selects the real due bucket for an exact Firm line rather than the first sparse zero bucket',()=>{const group={item_id:'FG',location_id:'FACTORY',uom:'件',endpoint:'FG'},first={...group,start_at:a,end_at:'2026-11-02T00:00:00+08:00',demand_ids:['F1']},due={...group,start_at:'2026-11-02T00:00:00+08:00',end_at:'2026-11-09T00:00:00+08:00',demand_ids:['F1']},d={grain:'week',demand:{rows:[first,due]}};const result=cockpitPerformanceDrill(d,scope,group,{demand_id:'F1',due_at:'2026-11-06T18:00:00+08:00'});expect(result.report_row).toContain('2026-11-02');expect(result.report_line_query).toBe('F1');});
 it('never labels unsupported or missing evidence as a zero queue',()=>{const r=input();r.manufacturing.validation={status:'NOT_CHECKED'};expect(cockpitQueueStatus(r,{},scope,'manufacturing')).toBe('NOT_CHECKED');expect(cockpitQueueStatus(r,{},scope,'distribution')).toBe('UNSUPPORTED');});
 it('locates the original daily PO bucket by chosen QC date, independently from the physical date',()=>{const task=cockpitWorkRows(input(),{}, {},scope)[0],row:WorkRow={...task,domain:'purchase',start:'2026-11-03T09:00:00+08:00',end:'2026-11-04T09:00:00+08:00',params:{analysis:'supply-work',supply_scope:'bound'}};expect(cockpitWorkDrill(row,{timezone:'Asia/Shanghai'},{planning_at:a},'end').supply_bucket).toBe('2026-11-03T16:00:00.000Z');expect(cockpitWorkDrill(row,{timezone:'Asia/Shanghai'},{planning_at:a},'start').supply_bucket).toBe('2026-11-02T16:00:00.000Z');});
 it('keeps a sourced proposed PO distinct and visible with unknown dates; refuses changed quantities and unknown suppliers',()=>{const r=input(),po={id:'API-1:PO:8',kind:'PO',origin:'PROPOSED',status:'proposed',reference:'8',item_id:'RAW',location_id:'FACTORY',quantity:'20',uom:'m',source_id:'S1',supplier_id:'SUP',source:{artifact:'PlanResult.json'}};r.documents={status:'PRESENT',documents:[],run_documents:[po]};r.inventory={status:'PRESENT',validation:pass};const result={purchase_suggestions:[{...po,supply_source_id:'S1'}]};const rows=cockpitWorkRows(r,{},result,scope).filter(x=>x.domain==='purchase');expect(rows).toHaveLength(1);expect(rows[0]).toMatchObject({status:'proposed',start:null,end:null});expect(selectCockpitWork(rows,'purchase','end','',a,b)).toHaveLength(1);result.purchase_suggestions[0].quantity='40';expect(cockpitWorkRows(r,{},result,scope).filter(x=>x.domain==='purchase')).toEqual([]);po.supplier_id='Unknown supplier';result.purchase_suggestions[0]={...po,supply_source_id:'S1'};expect(cockpitWorkRows(r,{},result,scope).filter(x=>x.domain==='purchase')).toEqual([]);});
});

function foundationProposal(){
 // Explicit contract fixture. The real five-PO archive is checked by the
 // Python report tests and the task's actual API/browser workflow separately.
 const foundationScope={...scope,profile:'foundation-planning-projection-v1'},r=input();r.identity={...r.identity,profile:foundationScope.profile,input_hash:'a'.repeat(64),scenario_id:'SCENARIO-FIXTURE'};
 const po:Dict={id:'API-1:PO:8',kind:'PO',origin:'PROPOSED',status:'proposed',reference:'8',item_id:'RAW',location_id:'FACTORY',quantity:'20',uom:'m',source_id:'SOURCE-EXPLICIT',supplier_id:'SUP',start_at:'2026-11-03T09:00:00+08:00',physical_receipt_at:'2026-11-04T09:00:00+08:00',available_at:'2026-11-05T09:00:00+08:00',at:'2026-11-04T09:00:00+08:00',source:{artifact:'PlanResult.json',pointer:'/purchase_suggestions/0'}};
 const artifacts=['canonical_input.json','PlanResult.json','raw/purchaseorder.json','raw/operationplanresource.json','raw/fixed_batch_post.json','raw/fixed_batch_post.xml','raw/operationplanmaterial.json'];
 r.evidence_files=artifacts.map(artifact=>({artifact,sha256:'c'.repeat(64)}));
 const result:Dict={scenario_id:'SCENARIO-FIXTURE',scenario_revision:1,input_hash:'a'.repeat(64),result_hash:'b'.repeat(64),purchase_suggestions:[{id:po.id,engine_ref:'8',item_id:po.item_id,location_id:po.location_id,quantity:po.quantity,uom:po.uom,supplier_id:po.supplier_id,order_at:po.start_at,receipt_at:po.physical_receipt_at}]};
 po.source_selection={schema_revision:'foundation-purchase-source-proof-v1',status:'PRESENT',scope:{run_id:scope.runId,data_version_id:scope.versionId,profile:foundationScope.profile,scenario_id:result.scenario_id,scenario_revision:1,input_hash:result.input_hash,result_hash:result.result_hash},purchase:Object.fromEntries(['id','reference','item_id','location_id','supplier_id','quantity','uom','start_at','physical_receipt_at','available_at'].map(key=>[key,po[key]])),source_id:po.source_id,capacity_resource:'ACTUAL-NATIVE-CAPACITY',native_load_id:'17',sources:r.evidence_files.map((s:Dict)=>({...s,pointer:'/0'}))};
 r.documents={status:'PRESENT',validation:pass,documents:[],run_documents:[po]};r.inventory={status:'PRESENT',validation:pass};
 return {r,po,result,scope:foundationScope};
}
describe('foundation read-only purchase source proof',()=>{
 it('accepts the explicit native proof without modifying immutable PlanResult and retains actual order time',()=>{
  const {r,po,result,scope}=foundationProposal(),before=structuredClone(result),rows=cockpitWorkRows(r,{},result,scope).filter(x=>x.domain==='purchase');
  expect(rows).toHaveLength(1);expect(rows[0]).toMatchObject({start:po.physical_receipt_at,end:po.available_at,status:'proposed'});expect(result).toEqual(before);expect(result.purchase_suggestions[0].supply_source_id).toBeUndefined();
 });
 it.each(['run_id','data_version_id','profile','scenario_id','scenario_revision','input_hash','result_hash'])('refuses foreign proof %s',field=>{
  const {r,po,result,scope}=foundationProposal();po.source_selection.scope[field]='FOREIGN';expect(cockpitWorkRows(r,{},result,scope).filter(x=>x.domain==='purchase')).toEqual([]);
 });
 it.each(['id','reference','item_id','location_id','supplier_id','quantity','uom','start_at','physical_receipt_at','available_at'])('refuses altered proof purchase %s',field=>{
  const {r,po,result,scope}=foundationProposal();po.source_selection.purchase[field]='ALTERED';expect(cockpitWorkRows(r,{},result,scope).filter(x=>x.domain==='purchase')).toEqual([]);
 });
 it.each(['missing','label_only','unknown','sha','missing_artifact','duplicate','source_id','unlisted_sha','duplicate_result','missing_dates'])('keeps %s source evidence out of the certified queue',change=>{
  const {r,po,result,scope}=foundationProposal();
  if(change==='missing')delete po.source_selection;
  else if(change==='label_only')po.source_selection={status:'PRESENT',source_id:po.source_id};
  else if(change==='unknown')po.source_selection.status='NOT_CHECKED';
  else if(change==='sha')po.source_selection.sources[0].sha256='f'.repeat(64);
  else if(change==='missing_artifact')po.source_selection.sources.pop();
  else if(change==='duplicate')po.source_selection.sources.push(po.source_selection.sources[0]);
  else if(change==='source_id')po.source_selection.source_id='ANOTHER';
  else if(change==='unlisted_sha')r.evidence_files=[];
  else if(change==='duplicate_result')result.purchase_suggestions.push(structuredClone(result.purchase_suggestions[0]));
  else{po.start_at=undefined;po.source_selection.purchase.start_at=undefined;result.purchase_suggestions[0].order_at=undefined;}
  expect(cockpitWorkRows(r,{},result,scope).filter(x=>x.domain==='purchase')).toEqual([]);
 });
 it('never extends the foundation proof to another profile',()=>{const f=foundationProposal();f.scope.profile='routing-alternate-capacity-v1';f.r.identity.profile=f.scope.profile;expect(cockpitWorkRows(f.r,{},f.result,f.scope).filter(x=>x.domain==='purchase')).toEqual([])});
});
