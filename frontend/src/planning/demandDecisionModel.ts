import type {Dict} from '../api/contracts';
import {qty,unit} from './planModel';

export type DemandDecisionScope={planId:string;versionId:string;runId:string;target:string};
export type DemandDecisionIdentity={plan_id:string;data_version_id:string;run_id:string;target:string;profile?:string};
export type DecisionQuantity=string|number|null;
export type DemandPlanningRow={
 id:string;start_at:string;end_at:string;item_id:string;location_id:string;customer_id?:string|null;uom:string;
 gross_forecast:DecisionQuantity;firm_open_orders:DecisionQuantity;forecast_consumed:DecisionQuantity;
 net_forecast:DecisionQuantity;effective_planning_demand:DecisionQuantity;planned_quantity:DecisionQuantity;
 firm_demand_ids:string[];forecast_demand_ids:string[];source_version?:string;match_level?:string;
 sources?:Dict[];consumptions?:Dict[];validation?:Dict;[key:string]:unknown;
};
export type InventoryAvailabilityRow={
 id:string;item_id:string;location_id:string;uom:string;physical_onhand:DecisionQuantity;qc_waiting:DecisionQuantity;
 quality_blocked:DecisionQuantity;other_nonnettable:DecisionQuantity;released_net_available:DecisionQuantity;
 planning_net_available:DecisionQuantity;future_planned_supply:DecisionQuantity;release_evidence_status:string;
 actual_quality_release_verified:boolean;declaration_scope?:string;effective_at?:string|null;release_reference?:string|null;
 sources?:Dict[];planned_supply_records?:Dict[];[key:string]:unknown;
};
export type DecisionReport<Row>={
 identity:DemandDecisionIdentity;timezone:string;status:string;reason?:string;rows:Row[];
 limits?:unknown[];known_business_fail?:boolean;policy?:Dict;[key:string]:unknown;
};
export type DecisionFilters={query?:string;location?:string;customer?:string;uom?:string;bucket?:string};

export function verifyDecisionIdentity<T extends Dict>(value:T,scope:DemandDecisionScope):T{
 const expected={plan_id:scope.planId,data_version_id:scope.versionId,run_id:scope.runId,target:scope.target};
 if(!value?.identity||Object.entries(expected).some(([key,v])=>value.identity[key]!==v)||!Array.isArray(value.rows))throw Error('需求决策记录与当前计划、数据版本、方案或运行不一致，已停止展示。');
 return value;
}
export function decisionQuantity(value:unknown,uom?:string|null){
 if((typeof value!=='number'&&typeof value!=='string')||String(value).trim()===''||!Number.isFinite(Number(value)))return '未知';
 return qty(value)+' '+(uom?unit(uom):'单位未提供');
}
export function decisionRows<T extends Dict>(rows:T[],filters:DecisionFilters):T[]{
 const query=(filters.query??'').trim().toLocaleLowerCase();
 return rows.filter(row=>(!query||[row.id,row.item_id,row.location_id,row.customer_id,row.source_version,...(row.firm_demand_ids??[]),...(row.forecast_demand_ids??[])].some(value=>String(value??'').toLocaleLowerCase().includes(query)))
  &&(!filters.location||row.location_id===filters.location)&&(!filters.customer||row.customer_id===filters.customer)
  &&(!filters.uom||row.uom===filters.uom)&&(!filters.bucket||[row.start_at,row.end_at].join('|')===filters.bucket));
}
export function priorityValue(value:string,before:number|undefined|null):number|null{
 if(!Number.isInteger(before)||!Number.isFinite(before)||!/^\d+$/.test(value))return null;
 const parsed=Number(value);return Number.isSafeInteger(parsed)&&parsed>=0&&parsed<=2147483647&&parsed!==before?parsed:null;
}
export function verifyPriorityDraft(response:Dict):string{
 const draft=response?.draft;
 if(!draft||typeof draft.id!=='string'||!draft.id||draft.state!=='DRAFT'||draft.run_id!==null)throw Error('响应未提供 NOT_RUN 独立草案，请刷新当前计划核对。');
 return draft.id;
}


export function scopedPrioritySelection<T>(selection:{scope:DemandDecisionScope;demand:T}|null,current:DemandDecisionScope,mode:string):T|null{
 if(mode!=='exceptions'||!selection||(['planId','versionId','runId','target'] as const).some(key=>selection.scope[key]!==current[key]))return null;
 return selection.demand;
}
