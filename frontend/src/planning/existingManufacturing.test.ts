import {expect,it} from 'vitest';
import {buildWorkspaceView,manufacturingLabel} from './planModel';
import type {InputSnapshot,PlanView} from '../api/contracts';
it('keeps existing MO identity, status and suggestions distinct in the same workspace',()=>{
 const context={enterprise_source:{plan_id:'P',data_version_id:'V',snapshot_sha256:'S'}};
 const view={context,result:{operations:[{id:'OP-A',manufacture_id:'MO-A',operation_id:'A'},{id:'OP-B',manufacture_id:'MO-B',operation_id:'B'}],
  manufacture_suggestions:[{id:'MO-B',item_id:'B',recipe_id:'B',uom:'piece',origin:'PROPOSED',source_status:'proposed'}],
  existing_manufacturing_orders:[{id:'MO-A',item_id:'A',recipe_id:'A',uom:'piece',origin:'EXISTING',source_status:'approved',external_ref:'ERP-A'}]}} as unknown as PlanView;
 const model=buildWorkspaceView(view,view,{data_version:{id:'V'},plan_input:{resources:[],calendars:[]}} as InputSnapshot);
 const original=model.tasks.find(t=>t.id==='OP-A')!;
 expect(original.externalRef).toBe('ERP-A');expect(manufacturingLabel(original)).toBe('已有工单 · approved');
 expect(manufacturingLabel(model.tasks.find(t=>t.id==='OP-B')!)).toBe('制造建议 · proposed');
});
