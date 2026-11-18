import {it,expect} from 'vitest';
import {currentDemandMetadata} from './orderInvestigationModel';

const due='2026-12-10T18:00:00+08:00';
function fixture(){
 const line={id:'inquiry-real-id',item_id:'FG-TB-M',location_id:'FACTORY',customer_id:'CUST-01',uom:'件',quantity:'20',due_at:due,kind:'INQUIRY',provenance:{research_inquiry:'inquiry-real-id',scope:'NON_BINDING_SIMULATION'}};
 const view:any={draft:{id:'D',run_id:'R',changes:{action:'simulate_professional_policy',simulation_only:true,execution_authorized:false,inquiry:{request:{id:line.id},added_demand:line}}},run:{solve_run_id:'R'},context:{enterprise_source:{plan_id:'P',data_version_id:'V'}},result:{deliveries:[{demand_id:line.id,requested_quantity:'20',uom:'件',due_at:due}]}};
 return {line,view,scope:{planId:'P',versionId:'V',target:'D',view,orderId:line.id}};
}
it('restores only the actual scoped native inquiry metadata without making it Firm',()=>{
 const {line,scope}=fixture(),input={demand_lines:[]};
 expect(currentDemandMetadata(input,scope)).toEqual({line,origin:'RESEARCH_INQUIRY'});
 expect(input.demand_lines).toEqual([]);
});
it('refuses stale or mismatched research metadata',()=>{
 for(const mutation of ['target','plan','version','run','kind','source','request','quantity','unit','due']){
  const {line,view,scope}=fixture();
  if(mutation==='target')scope.target='other';
  if(mutation==='plan')scope.planId='other';
  if(mutation==='version')scope.versionId='other';
  if(mutation==='run')view.draft.run_id='other';
  if(mutation==='kind')line.kind='FIRM_ORDER';
  if(mutation==='source')line.provenance.scope='UNKNOWN';
  if(mutation==='request')view.draft.changes.inquiry.request.id='other';
  if(mutation==='quantity')view.result.deliveries[0].requested_quantity='21';
  if(mutation==='unit')view.result.deliveries[0].uom='套';
  if(mutation==='due')view.result.deliveries[0].due_at='2026-12-11T18:00:00+08:00';
  expect(currentDemandMetadata({demand_lines:[]},scope),mutation).toBeNull();
 }
});
