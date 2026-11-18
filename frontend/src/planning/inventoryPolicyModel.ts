import type {Dict} from '../api/contracts';
import type {DemandDecisionIdentity,DemandDecisionScope,DecisionQuantity} from './demandDecisionModel';

export type InventoryPolicyEvent={id:string;at:string;kind:string;quantity:DecisionQuantity;balance:DecisionQuantity;native_sequence:number;reference:string;sources:Dict[];operation_task_id?:string|null;[key:string]:unknown};
export type InventoryPolicyRow={
 id:string;item_id:string;location_id:string;uom:string;policy_version:string;status:string;reason?:string;
 start_at:string;end_at:string;target_quantity:DecisionQuantity;opening_quantity?:DecisionQuantity;inflow_quantity?:DecisionQuantity;
 consumption_quantity?:DecisionQuantity;ending_quantity:DecisionQuantity;minimum_quantity?:DecisionQuantity;
 ending_gap_quantity:DecisionQuantity;maximum_gap_quantity:DecisionQuantity;first_gap_at?:string|null;
 negative_inventory_status?:string;physical_opening?:DecisionQuantity;quality_blocked?:DecisionQuantity;other_nonnettable?:DecisionQuantity;inspection_pending_quantity?:DecisionQuantity;
 event_ids:string[];events:InventoryPolicyEvent[];predecessor_event_ids?:string[];sources?:Dict[];policy:Dict;opening_source?:Dict;
 [key:string]:unknown;
};
export type InventoryPolicyReport={identity:DemandDecisionIdentity;timezone:string;status:string;mode:string;reason?:string;known_business_fail?:boolean;rows:InventoryPolicyRow[];limits?:string[]};
export type InventoryPolicyFilters={query?:string;item?:string;location?:string;uom?:string;bucket?:string};
export type InventoryPolicyChoice={scope:string;rowId:string;eventId?:string};
export const policyScopeKey=(scope:DemandDecisionScope)=>JSON.stringify([scope.planId,scope.versionId,scope.runId,scope.target]);

export function verifyInventoryPolicyIdentity(report:InventoryPolicyReport,scope:DemandDecisionScope){
 const expected={plan_id:scope.planId,data_version_id:scope.versionId,run_id:scope.runId,target:scope.target};
 if(!report?.identity||!Array.isArray(report.rows)||Object.entries(expected).some(([key,value])=>report.identity[key as keyof DemandDecisionIdentity]!==value))throw Error('库存政策响应与当前计划、数据版本、方案或运行不一致，已停止展示。');
 return report;
}

export function inventoryPolicyRows(rows:InventoryPolicyRow[],filters:InventoryPolicyFilters){
 const query=filters.query?.trim().toLocaleLowerCase();
 return rows.filter(row=>(!filters.item||row.item_id===filters.item)&&(!filters.location||row.location_id===filters.location)&&
  (!filters.uom||row.uom===filters.uom)&&(!filters.bucket||row.id===filters.bucket)&&
  (!query||JSON.stringify([row.item_id,row.location_id,row.id,row.policy_version,row.policy?.provenance]).toLocaleLowerCase().includes(query)));
}

export function inventoryPolicySelection(report:InventoryPolicyReport,scope:DemandDecisionScope,filters:InventoryPolicyFilters,choice?:InventoryPolicyChoice|null){
 verifyInventoryPolicyIdentity(report,scope);
 const row=choice?.scope===policyScopeKey(scope)?inventoryPolicyRows(report.rows,filters).find(row=>row.id===choice.rowId)??null:null;
 return {row,event:row&&choice?.eventId?row.events.find(event=>event.id===choice.eventId&&row.event_ids.includes(event.id))??null:null};
}

export function inventoryPolicyEventSearch(previous:URLSearchParams,report:InventoryPolicyReport,scope:DemandDecisionScope,rowId:string,eventId?:string){
 verifyInventoryPolicyIdentity(report,scope);
 const row=report.rows.find(row=>row.id===rowId);
 if(!row||row.status!=='PRESENT'||(eventId&&!row.events.some(event=>event.id===eventId&&row.event_ids.includes(eventId))))throw Error('当前桶或原生事件不属于已核验的本次运行。');
 const next=new URLSearchParams(previous);
 next.set('target',scope.target);next.delete('run_id');next.delete('data_version_id');
 next.set('analysis','inventory');next.set('analysis_item',row.item_id);next.set('analysis_location',row.location_id);
 next.set('analysis_start',row.start_at);next.set('analysis_end',row.end_at);
 next.set('return_inventory_policy','1');next.set('policy_return_scope',policyScopeKey(scope));
 next.set('policy_row',row.id);next.set('policy_selection_scope',policyScopeKey(scope));
 if(eventId){next.set('analysis_event',eventId);next.set('policy_event',eventId)}else{next.delete('analysis_event');next.delete('policy_event')}
 next.delete('analysis_task');
 return next;
}

export function inventoryPolicyReturnSearch(previous:URLSearchParams,scope:DemandDecisionScope){
 const next=new URLSearchParams(previous);
 if(next.get('policy_return_scope')!==policyScopeKey(scope))for(const key of ['policy_bucket','policy_row','policy_event','policy_selection_scope'])next.delete(key);
 for(const key of ['return_inventory_policy','policy_return_scope','analysis_item','analysis_location','analysis_start','analysis_end','analysis_event','analysis_task'])next.delete(key);
 next.delete('run_id');next.delete('data_version_id');
 next.set('analysis','inventory-policy');next.set('target',scope.target);
 return next;
}
