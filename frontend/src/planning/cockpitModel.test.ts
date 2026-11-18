import {describe,it,expect} from 'vitest';
import type {Dict} from '../api/contracts';
import {cockpitAttention,cockpitDemandEmptyMessage,domainAvailability,verifyCockpit,cockpitDemandSummaries,cockpitSupplyRows,cockpitResourcePeriodId,type CockpitScope,type CockpitAvailability} from './cockpitModel';
import {fulfillmentRowsForKind} from './demandFulfillmentModel';

const scope:CockpitScope={planId:'PLAN-1',versionId:'DATA-1',runId:'API-1',target:'baseline',profile:'enterprise-accessory-supply-v1'};
const identity=()=>({plan_id:scope.planId,data_version_id:scope.versionId,run_id:scope.runId,target:scope.target,profile:scope.profile});
const pass=()=>({status:'PASS',issues:[],missing_evidence:[]});
function line(id='ORDER-1',quantity:unknown='4',uom='件'):Dict{return {demand_id:id,order_ref:id,item_id:'FG',location_id:'FACTORY',endpoint:'缝后阶段',uom,
 requested_quantity:'6',planned_quantity:'2',unfulfilled_quantity:quantity,due_at:'2026-11-06T18:00:00+08:00',
 planned_delivery_at:'2026-11-06T18:00:00+08:00',delivery_evidence_status:'PRESENT',delivery_validation_status:'PASS'};}
function report(lines:Dict[]=[]):Dict{return {identity:identity(),timezone:'Asia/Shanghai',grain:'week',known_business_fail:false,
 demand:{status:lines.length?'PRESENT':'EMPTY_CHECKED',delivery_evidence_status:lines.length?'PRESENT':'EMPTY_CHECKED',due_evidence_status:lines.length?'PRESENT':'EMPTY_CHECKED',validation:pass(),lines,
 rows:lines.flatMap(l=>[...['2026-11-02T00:00:00+08:00','2026-11-09T00:00:00+08:00']].map((start,i)=>({item_id:l.item_id,location_id:l.location_id,uom:l.uom,endpoint:l.endpoint,
  start_at:start,end_at:i===0?'2026-11-09T00:00:00+08:00':'2026-11-16T00:00:00+08:00',demand_ids:[l.demand_id],demand_components:[{demand_id:l.demand_id}]})))},
 resource:{status:'EMPTY_CHECKED',validation:pass(),rows:[]},inventory:{status:'EMPTY_CHECKED',validation:pass(),rows:[]},documents:{status:'EMPTY_CHECKED',documents:[]}};}
function analysis():Dict{return {identity:identity(),native_result_status:{constraint_check:'PASS'},
 inventory_analysis:{status:'EMPTY_CHECKED',validation:pass(),series:[]},resource_analysis:{status:'EMPTY_CHECKED',validation:pass(),resources:[],intervals:[]}};}
function context():Dict{return {enterprise_source:{plan_id:scope.planId,data_version_id:scope.versionId},
 plan_version:{solve_run_id:scope.runId,scenario_id:'ORIGINAL-SNAPSHOT'},manufacturing_profile:scope.profile,calculation_state:'SUCCEEDED',assessment:{constraint_check:'PASS'}};}
const task=(r=report(),a:Dict|null=null,c=context(),drafts:Dict[]=[])=>cockpitAttention(r,a,c,drafts,scope);
function po():Dict{return {id:'PO-40',external_ref:'确认原料采购',source_status:'confirmed',item_id:'FAB',location_id:'FACTORY',supply_source_id:'SUPPLY-1',
 quantity:'40',physical_receipt_at:'2026-11-03T09:00:00+08:00',available_at:'2026-11-04T09:00:00+08:00',provenance:{file_sha256:'a'.repeat(64)}};}
function addPoDocument(r:Dict){r.inventory={status:'PRESENT',validation:pass(),rows:[{item_id:'FAB',location_id:'FACTORY',uom:'米'}]};r.documents={status:'PRESENT',documents:[{id:'PO:DOC:40',kind:'PO',reference:'PO-40',origin:'EXISTING',status:'confirmed',item_id:'FAB',location_id:'FACTORY',source_id:'SUPPLY-1',uom:'米'}]};return r;}

describe('cockpit scope and authority',()=>{
 it.each(['NOT_COLLECTED','UNREADABLE','INVALID_EVIDENCE','UNSUPPORTED','NOT_CHECKED','FAIL'] as CockpitAvailability[])('never describes %s as a checked absence of shortage',status=>{
  expect(cockpitDemandEmptyMessage(status)).toContain('空清单不代表没有缺口');
  expect(cockpitDemandEmptyMessage(status)).not.toContain('当前已核验');
 });
 it.each(['PRESENT','EMPTY_CHECKED'] as CockpitAvailability[])('only describes certified %s as a checked empty work list',status=>{
  expect(cockpitDemandEmptyMessage(status)).toContain('当前已核验');
 });
 it.each(['plan_id','data_version_id','run_id','target','profile'])('rejects a mismatched %s',key=>{
  const r=report();r.identity[key]='OTHER';expect(()=>verifyCockpit(r,scope)).toThrow();
 });
 it('rejects incomplete scope and a separately mismatched analysis',()=>{
  expect(()=>verifyCockpit(report(),{...scope,runId:''})).toThrow();const a=analysis();a.identity.run_id='OLD';expect(()=>task(report(),a)).toThrow();
 });
 it('does not treat an unknown quantity, blank, boolean or NaN as zero',()=>{
  for(const v of [null,undefined,'',true,'NaN']){const l=line('O');l.unfulfilled_quantity=v;expect(task(report([l]))).toEqual([]);}
 });
 it('FAIL has priority over present rows and successful calculation',()=>{
  const r=report([line()]);r.known_business_fail=true;const items=task(r);
  expect(items).toHaveLength(1);expect(items[0]).toMatchObject({kind:'work',level:'risk',source:'PLAN_RESULT'});
  expect(items[0].quantity).toBeUndefined();expect(domainAvailability(r,analysis())).toEqual({demand:'FAIL',inventory:'FAIL',resource:'FAIL'});
 });
 it('retains per-line validation FAIL even when the outer component says PASS',()=>{
  const r=report([line()]);r.demand.lines[0].delivery_validation_status='FAIL';expect(task(r)[0].id).toBe('work:business-fail');
 });
 it('a NOT_RUN current target cannot show a supplied old business result',()=>{
  const c=context();c.calculation_state='NOT_RUN';const items=task(report([line()]),null,c);
  expect(items).toHaveLength(1);expect(items[0]).toMatchObject({source:'WORK_STATE',level:'unknown'});expect(items[0].quantity).toBeUndefined();
  c.plan_version.solve_run_id=undefined;expect(task(report([line()]),null,c)[0].source).toBe('WORK_STATE');
 });
});

describe('certified demand attention',()=>{
 it('includes a fully planned late Firm only with its exact certified delivery proof',()=>{
  const l=line('LATE','0');l.demand_kind='FIRM_ORDER';l.planned_quantity='6';
  const r=report([l]);r.demand.delivery_performance={status:'PRESENT',validation:pass(),groups:[{item_id:l.item_id,location_id:l.location_id,uom:l.uom,endpoint:l.endpoint,lines:[{demand_id:l.demand_id,classification:'FULL_LATE',completion_at:'2026-11-09T18:00:00+08:00'}]}]};
  expect(task(r)[0]).toMatchObject({id:'demand:LATE',kind:'demand',level:'risk',title:'LATE · 需求迟后',quantity:6});
  for(const classification of ['FULL_ON_TIME','UNKNOWN','FULL_LATE']){
   r.demand.delivery_performance.groups[0].lines.push({demand_id:l.demand_id,classification,completion_at:'2026-11-09T18:00:00+08:00'});
   expect(task(r)).toEqual([]);r.demand.delivery_performance.groups[0].lines.pop();
  }
  r.demand.lines.push({...l});expect(task(r)).toEqual([]);r.demand.lines.pop();
  r.demand.delivery_performance.groups[0].item_id='OTHER';expect(task(r)).toEqual([]);
 });
 it('does not turn a raw LATE label or an unknown proof into a certified risk claim',()=>{
  const l=line('LATE','0');l.demand_kind='FIRM_ORDER';l.planned_quantity='6';l.status='LATE';
  expect(task(report([l]))).toEqual([]);
  const r=report([l]);r.demand.delivery_performance={status:'NOT_COLLECTED',validation:{status:'NOT_CHECKED'},groups:[]};expect(task(r)).toEqual([]);
  expect(domainAvailability(r,null).demand).toBe('NOT_CHECKED');
  expect(cockpitDemandEmptyMessage(domainAvailability(r,null).demand)).toContain('空清单不代表没有缺口');
 });
 it('retains an overdue boundary UNKNOWN without treating raw LATE as certified FULL_LATE',()=>{
  const l=line('OVERDUE','0');l.demand_kind='FIRM_ORDER';l.planned_quantity='6';l.status='LATE';
  const r=report([l]);r.demand.delivery_performance={status:'PRESENT',validation:{status:'NOT_CHECKED'},groups:[{item_id:l.item_id,location_id:l.location_id,uom:l.uom,endpoint:l.endpoint,lines:[{demand_id:l.demand_id,classification:'UNKNOWN',reason_codes:['DUE_OUTSIDE_CERTIFIED_PLAN_RANGE'],completion_at:null}]}]};
  expect(task(r)).toEqual([]);expect(domainAvailability(r,null).demand).toBe('NOT_CHECKED');
 });
 it('keeps shortages separate per demand and unit with no artificial total',()=>{
  const items=task(report([line('PIECE','4','件'),line('FABRIC','8','米')]));
  expect(items.map(i=>[i.quantity,i.uom])).toEqual([[4,'件'],[8,'米']]);expect(items).toHaveLength(2);
 });
 it('links exactly to the due bucket and its real line, group and four-field report scope',()=>{
  const items=task(report([line()]));expect(items).toHaveLength(1);
  expect(items[0].params).toMatchObject({analysis:'reports',report:'demand',grain:'week',report_line_query:'ORDER-1'});
  expect(items[0].params.report_row).toContain('2026-11-02');expect(JSON.parse(items[0].params.report_scope)).toEqual({planId:scope.planId,versionId:scope.versionId,runId:scope.runId,target:scope.target});
  expect(items[0].detail).toContain('没有已验证完成日期');expect(items[0].at).toBe(line().due_at);
 });
 it('uses the following bucket for a due instant exactly on the exclusive boundary',()=>{
  const l=line();l.due_at='2026-11-09T00:00:00+08:00';expect(task(report([l]))[0].params.report_row).toContain('2026-11-09T00:00:00+08:00');
 });
 it('an authenticated empty delivery set still supports a wholly unplanned demand',()=>{
  const l=line('NET','88');l.delivery_evidence_status='EMPTY_CHECKED';l.planned_quantity='0';l.planned_delivery_at=null;
  expect(task(report([l]))[0]).toMatchObject({quantity:88,level:'risk'});
 });
 it('names net forecast by business item instead of its internal demand reference',()=>{
  const l=line('NET_FORECAST:LONG-INTERNAL-REFERENCE','88');l.demand_kind='NET_FORECAST';
  expect(task(report([l]))[0].title).toBe('FG · 净预测未满足');
 });
 it.each(['NOT_COLLECTED','INVALID_EVIDENCE','UNREADABLE','UNSUPPORTED'])('does not expose timed claims for %s evidence',status=>{
  const r=report([line()]);r.demand.delivery_evidence_status=status;expect(task(r)).toEqual([]);
 });
 it('requires line evidence and falls back to the exact original order when no bucket matches',()=>{
  const r=report([line()]);r.demand.lines[0].delivery_validation_status='NOT_CHECKED';expect(task(r)).toEqual([]);
  r.demand.lines[0].delivery_validation_status='PASS';r.demand.rows=[];
  expect(task(r)[0].params).toEqual({analysis:'schedule',view:'investigate',order_line_id:'ORDER-1',run_id:scope.runId,data_version_id:scope.versionId});
 });
 it.each(['2026-11-01T18:00:00+08:00','2026-11-16T00:00:00+08:00'])('retains a proven shortage with due %s outside the half-open report range',due=>{
  const l=line();l.due_at=due;const item=task(report([l]))[0];
  expect(item).toMatchObject({quantity:4,source:'PLAN_RESULT',at:due,params:{analysis:'schedule',view:'investigate',order_line_id:l.demand_id}});
  expect(item.params.report_row).toBeUndefined();expect(item.detail).toContain('没有已验证完成日期');
 });
});

describe('native inventory and resource projections',()=>{
 it('uses the first proven negative event, retaining native same-instant order and the original balance',()=>{
  const a=analysis();a.inventory_analysis={status:'PRESENT',validation:pass(),series:[{id:'WIP@FACTORY',item_id:'WIP',location_id:'FACTORY',uom:'套',complete:true,event_status:'PRESENT',
   events:[{id:'first',at:'2026-11-05T10:00:00+08:00',balance:'-3'},{id:'second',at:'2026-11-05T10:00:00+08:00',balance:'7'}]}]};
  expect(task(report(),a)[0]).toMatchObject({kind:'inventory',quantity:-3,at:'2026-11-05T10:00:00+08:00',params:{analysis_event:'first'}});
 });
 it('cannot infer zero or safety from missing or incomplete inventory events',()=>{
  const a=analysis();a.inventory_analysis={status:'NOT_COLLECTED',validation:{status:'NOT_CHECKED'},series:[{complete:false,event_status:'NOT_COLLECTED',events:[{id:'x',at:'2026-11-05T10:00:00+08:00',balance:'-3'}]}]};
  expect(task(report(),a)).toEqual([]);expect(domainAvailability(report(),a).inventory).toBe('NOT_COLLECTED');
 });
 it('does not extend inventory certification to the routing profile',()=>{
  const r=report(),a=analysis(),s={...scope,profile:'routing-alternate-capacity-v1'};r.identity.profile=s.profile;a.identity.profile=s.profile;
  expect(domainAvailability(r,a).inventory).toBe('UNSUPPORTED');
 });
 it('displays only source load and source ratio without inventing a congestion threshold',()=>{
  const r=report();r.resource={status:'PRESENT',validation:pass(),rows:[{resource_id:'P1',role:'OPERATOR',start_at:'2026-11-02T09:00:00+08:00',end_at:'2026-11-03T00:00:00+08:00',load_hours:'2',available_hours:'8',utilization_percent:'25',interval_ids:['actual-load'],operation_task_ids:['CUT']} ]};
  const i=task(r)[0];expect(i).toMatchObject({kind:'resource',quantity:2,level:'info'});expect(i.detail).toContain('25%');expect(i.detail).not.toContain('超载');
  r.resource.rows[0].utilization_percent=null;expect(task(r)[0].detail).toContain('利用率未核验');
  r.resource.rows[0].role='OPERATOR_POOL';expect(task(r)).toEqual([]);
 });
 it('rounds resource display to two places while retaining its source quantity and report values',()=>{
  const r=report();r.resource={status:'PRESENT',validation:pass(),rows:[{resource_id:'M1',role:'MACHINE',start_at:'2026-11-02T09:00:00+08:00',end_at:'2026-11-03T00:00:00+08:00',load_hours:'2.33333333',available_hours:'54',utilization_percent:'4.320988',interval_ids:['actual-load']}]};
  const item=task(r)[0];expect(item.detail).toContain('负荷 2.33 小时');expect(item.detail).toContain('4.32%');
  expect(item.quantity).toBe(2.33333333);expect(r.resource.rows[0].load_hours).toBe('2.33333333');expect(r.resource.rows[0].utilization_percent).toBe('4.320988');
 });
});

function receiptTask(c:Dict){const s={...scope,profile:'multi-receipt-protected-manufacturing-v1'},r=report();r.identity.profile=s.profile;return cockpitAttention(r,null,{...c,manufacturing_profile:s.profile,receipt_protection:{...c.receipt_protection,profile:s.profile}},[],s)}

describe('purchase facts and work state',()=>{
 it.each([undefined,'simulate_outstanding_receipt'])('labels a matching candidate purchase assumption separately from original receipt facts (%s)',action=>{
  const s={...scope,target:'DRAFT-candidate'},c=context(),r=addPoDocument(report());r.identity.target=s.target;c.plan_version.scenario_id=s.target;
  c.simulation={simulation_only:true,purchase_reference:'PO-40',...(action?{action}:{})};
  c.receipt_protection={profile:scope.profile,purchase_orders:[{...po(),uom:'米',remaining_quantity:'20',received_quantity:'20',qc_waiting_quantity:'6',frozen_received_quantity:'2',physical_receipt_at:'2026-11-05T09:00:00+08:00',available_at:'2026-11-06T09:00:00+08:00'}]};
  const items=cockpitAttention(r,null,c,[],s);
  expect(items[0]).toMatchObject({source:'WORK_STATE',quantity:20});expect(items[0].title).toContain('本方案到货假设');expect(items[0].detail).toContain('原确认采购未修改');
  expect(items.slice(1).map(i=>i.source)).toEqual(['FACT','FACT']);
  c.simulation={simulation_only:true,resource_id:'M1'};
  expect(cockpitAttention(r,null,c,[],s)[0].source).toBe('FACT');
 });
 it('keeps physical receipt distinct from QC availability and never counts a proposed PO',()=>{
  const c=context();c.confirmed_supply_windows=[po(),{...po(),id:'NEW',source_status:'proposed'}];
  const items=task(addPoDocument(report()),null,c);expect(items).toHaveLength(1);expect(items[0]).toMatchObject({kind:'purchase',source:'FACT',quantity:40,uom:'米',at:po().physical_receipt_at});
  expect(items[0].params).toMatchObject({supply_item:'FAB',supply_location:'FACTORY',supply_doc:'PO:DOC:40',supply_attention:'all',supply_scope:JSON.stringify([scope.planId,scope.versionId,scope.runId,scope.target])});expect(items[0].detail).toContain('2026-11-03 09:00');expect(items[0].detail).toContain('2026-11-04 09:00');
 });
 it('does not supply a guessed unit or consume PO facts from another run',()=>{
  const c=context();c.confirmed_supply_windows=[po()];expect(task(report(),null,c)).toEqual([]);
  c.plan_version.solve_run_id='OLD';expect(task(addPoDocument(report()),null,c)).toEqual([]);
 });
 it('partially received PO preserves remaining, QC-waiting and frozen quantities separately',()=>{
  const c=context();c.receipt_protection={profile:scope.profile,purchase_orders:[{...po(),uom:'米',ordered_quantity:'40',remaining_quantity:'20',qc_waiting_quantity:'6',frozen_received_quantity:'2'}]};
  const items=receiptTask(c);expect(items[0].params.supply_po).toBe('PO-40');expect(items.map(i=>i.quantity)).toEqual([20,6,2]);expect(items.map(i=>i.id)).toEqual(['purchase:PO-40:outstanding','purchase:PO-40:qc','purchase:PO-40:frozen']);
  c.receipt_protection.purchase_orders[0].remaining_quantity=null;expect(receiptTask(c).map(i=>i.quantity)).toEqual([6,2]);
 });
 it('displays the declared received quantity and never derives a missing one from ordered and remaining',()=>{
  const c=context();c.receipt_protection={profile:scope.profile,purchase_orders:[{...po(),uom:'米',ordered_quantity:'40',received_quantity:'20',remaining_quantity:'20',qc_waiting_quantity:'6',frozen_received_quantity:'2'}]};
  let items=receiptTask(c);expect(items[0].detail).toContain('已收 20 米，未收 20 米');expect(items.map(i=>i.quantity)).toEqual([20,6,2]);
  delete c.receipt_protection.purchase_orders[0].received_quantity;items=receiptTask(c);expect(items[0].detail).not.toContain('已收');expect(items[0].detail).toContain('未收 20 米');
 });
 it('omits a general purchase action without an exact source document and matching item-location row',()=>{const c=context();c.confirmed_supply_windows=[{...po(),uom:'米'}];const r=addPoDocument(report());delete r.documents.documents[0].id;expect(task(r,null,c)).toEqual([]);r.documents.documents[0].id='PO:DOC:40';r.inventory.rows=[];expect(task(r,null,c)).toEqual([])});
 it('NOT_RUN drafts are only work state, excluding discarded, already run and other-plan entries',()=>{
  const items=task(report(),null,context(),[{id:'DRAFT-1',state:'DRAFT',run_id:null,changes:{intent:'采购余量试算'}},
   {id:'DRAFT-2',state:'DISCARDED',run_id:null},{id:'DRAFT-3',state:'DRAFT',run_id:'API-3'},
   {id:'DRAFT-4',state:'DRAFT',run_id:null,plan_id:'OTHER'}]);
  expect(items).toHaveLength(1);expect(items[0]).toMatchObject({kind:'work',source:'WORK_STATE',params:{target:'DRAFT-1'}});expect(items[0].quantity).toBeUndefined();
 });
});

describe('cockpit compatible summaries and owner bucket selection',()=>{
 it('preserves known demand kind on risk drill without guessing an unclassified kind',()=>{const f=line('F'),n=line('N'),u=line('U');f.demand_kind='FIRM_ORDER';n.demand_kind='NET_FORECAST';const items=task(report([f,n,u]));expect(items.find(i=>i.id==='demand:F')?.params.report_demand_kind).toBe('FIRM_ORDER');expect(items.find(i=>i.id==='demand:N')?.params.report_demand_kind).toBe('NET_FORECAST');expect(items.find(i=>i.id==='demand:U')?.params.report_demand_kind).toBeUndefined()});
 it('separates firm from net forecast without sharing an absolute chart scale or merging units',()=>{
  const f=line('F','4'),n=line('N','88'),m=line('M','2','米');f.demand_kind='FIRM_ORDER';n.demand_kind='NET_FORECAST';n.requested_quantity='88';n.planned_quantity='0';m.demand_kind='FIRM_ORDER';
  const x=cockpitDemandSummaries(report([f,n,m]));expect(x).toHaveLength(3);expect(x.map(g=>[g.kind,g.uom,g.short])).toEqual([['FIRM_ORDER','件',4],['NET_FORECAST','件',88],['FIRM_ORDER','米',2]]);
 });
 it('does not infer Firm from names or turn unknown quantities and duplicate lines into a verified total',()=>{
  const l=line('FIRM-NAME');expect(cockpitDemandSummaries(report([l]))[0].kind).toBe('UNKNOWN');
  l.planned_quantity=null;expect(cockpitDemandSummaries(report([l]))[0].planned).toBeNull();expect(cockpitDemandSummaries(report([l,l]))[0].requested).toBeNull();
 });
 it('keeps incompatible stage endpoints separate and retains FAIL priority',()=>{
  const a=line('A'),b=line('B');b.endpoint='包装';expect(cockpitDemandSummaries(report([a,b]))).toHaveLength(2);
  const r=report([a]);r.known_business_fail=true;expect(cockpitDemandSummaries(r)).toEqual([]);
 });
 it('selects exact owner-computed bucket fields without moving a date or recalculating fulfillment',()=>{
  const component=(kind:string,planned:string,short:string)=>({demand_id:kind,demand_kind:kind,due_quantity:'6',planned_quantity:planned,overdue_unfulfilled:short,cumulative_planned_quantity:planned,not_yet_due_unfulfilled:'0',unscheduled_quantity:short,delivery_evidence_status:'PRESENT',due_evidence_status:'PRESENT'});
  const row={id:'owner-row',start_at:'2026-11-02T00:00:00+08:00',due_quantity:'94',planned_quantity:'2',overdue_unfulfilled:'92',demand_components:[component('FIRM_ORDER','2','4'),component('NET_FORECAST','0','88')]};
  const [firm]=fulfillmentRowsForKind([row],'FIRM_ORDER');expect(firm).toMatchObject({id:'owner-row',start_at:row.start_at,due_quantity:6,planned_quantity:2,overdue_unfulfilled:4,demand_ids:['FIRM_ORDER']});
  expect(fulfillmentRowsForKind([row],'NET_FORECAST')[0].overdue_unfulfilled).toBe(88);expect(row.overdue_unfulfilled).toBe('92');expect(fulfillmentRowsForKind([row],'all')[0]).toBe(row);
 });
 it('does not interpret missing components, unclassified lines or missing bucket amounts as zero',()=>{
  expect(fulfillmentRowsForKind([{due_quantity:'10'}],'FIRM_ORDER')[0].due_quantity).toBeNull();
  expect(fulfillmentRowsForKind([{demand_components:[{demand_kind:null,planned_quantity:'4'}]}],'FIRM_ORDER')[0].planned_quantity).toBeNull();
  expect(fulfillmentRowsForKind([{demand_components:[{demand_kind:'FIRM_ORDER',planned_quantity:null,delivery_evidence_status:'PRESENT',due_evidence_status:'PRESENT'}]}],'FIRM_ORDER')[0].planned_quantity).toBeNull();
 });
 it('purchase visualization uses the same verified PO actions and retains QC versus frozen fields',()=>{
  const c=context();c.receipt_protection={profile:scope.profile,purchase_orders:[{...po(),uom:'米',received_quantity:'10',remaining_quantity:'30',released_received_quantity:'4',qc_waiting_quantity:'4',frozen_received_quantity:'2'}]};
  const x=cockpitSupplyRows(addPoDocument(report()),c,scope);expect(x).toHaveLength(1);expect(x[0]).toMatchObject({physical_at:po().physical_receipt_at,available_at:po().available_at,released_received_quantity:'4',qc_waiting_quantity:'4',frozen_received_quantity:'2'});
  c.plan_version.solve_run_id='OTHER';expect(cockpitSupplyRows(addPoDocument(report()),c,scope)).toEqual([]);
 });
});

describe('sparse owner periods and repeated native intervals',()=>{
 it('never resets prior delivered cumulative quantity to zero when the owner omits inactive completed lines',()=>{const row={item_id:'FG',location_id:'FACTORY',uom:'件',endpoint:'缝后阶段',cumulative_planned_quantity:'6',due_quantity:'0',planned_quantity:'0',overdue_unfulfilled:'0',demand_components:[]};const l=line();l.demand_kind='FIRM_ORDER';const x=fulfillmentRowsForKind([row],'FIRM_ORDER',[l])[0];expect(x.cumulative_planned_quantity).toBeNull();expect(x.planned_quantity).toBe(0);expect(row.cumulative_planned_quantity).toBe('6')});
 it('uses distinct period identities for two bars sharing one native interval',()=>{const a={resource_id:'M1',start_at:'2026-11-02T08:00:00+08:00',end_at:'2026-11-03T00:00:00+08:00',interval_ids:['same-load']};const b={...a,start_at:'2026-11-03T00:00:00+08:00',end_at:'2026-11-04T00:00:00+08:00'};expect(cockpitResourcePeriodId(a)).not.toBe(cockpitResourcePeriodId(b));expect([a,b].find(r=>cockpitResourcePeriodId(r)===cockpitResourcePeriodId(b))).toBe(b)});
});
