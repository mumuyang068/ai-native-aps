import type {Dict} from '../api/contracts';
import type {ProfessionalScope} from './professionalPolicyModel';

export function adoptedWorkbenchState(report:Dict,target:string){
 if(target==='baseline')return null;
 const a=report.context?.draft?.adopted??{};
 const inventory=(Array.isArray(report.inventory_candidates)?report.inventory_candidates:[]).find((v:Dict)=>a.inventory_policy_version&&v.native_policy?.policy_version===a.inventory_policy_version);
 return {versionId:a.forecast_version_id??'',inventoryId:inventory?.id??'',inventory,method:inventory?.result?.selected_method??'',
  useNet:a.has_forecast===true,useInventory:a.has_inventory===true,useInquiry:a.has_inquiry===true,
  backward:String(a.netting_windows?.backward??0),forward:String(a.netting_windows?.forward??0),inquiry:a.inquiry??null,reason:a.reason??''};
}

export function quoteSupplyView(quote:Dict):Dict{
 return ['HELD','COMMITTED_SIMULATED'].includes(quote.state)?quote.held_calculation??{}:quote.calculation??{};
}
export function quoteActionScope(scope:ProfessionalScope,quote:Dict|undefined,action:string):ProfessionalScope{
 if(action!=='quote_release'||!quote)return scope;
 const identity=quote.identity;
 if(!identity||identity.plan_id!==scope.planId||identity.data_version_id!==scope.versionId)throw Error('研究占用不属于当前企业计划。');
 return {planId:identity.plan_id,versionId:identity.data_version_id,baselineRunId:identity.baseline_run_id,runId:identity.run_id,target:identity.target};
}

export function advancedNettingParameters(template:Dict,backward:number,forward:number):Dict{
 if(template.revision!=='advanced-netting-v1'||(!template.forecast_version&&!template.forecast_version_ref)||!template.consumption_policy)throw Error('当前预测版本没有可用的确认抵扣模板，请刷新版本。');
 if([backward,forward].some(n=>!Number.isInteger(n)||n<0||n>366))throw Error('前后消费窗口须为0至366的整数天。');
 return {...template,consumption_policy:{...template.consumption_policy,backward_days:backward,forward_days:forward}};
}

export function forecastSeries(rows:Dict[],history:Dict[],selectedId:string):{unit:string;label:string;forecast:Dict[];history:Dict[]}|null{
 const selected=rows.find(r=>r.id===selectedId)??(!rows.length?history[0]:undefined);if(!selected)return null;
 const fields=['item_id','location_id','customer_id'] as const;
 const unit=selected.uom??selected.unit;
 const matches=(r:Dict)=>fields.every(k=>r[k]===selected[k])&&(r.uom??r.unit)===unit;
 const sorted=(a:Dict,b:Dict)=>Date.parse(a.bucket_start)-Date.parse(b.bucket_start);
 return {unit,label:fields.map(k=>selected[k]).join(' · '),
  forecast:rows.filter(matches).slice().sort(sorted),
  history:history.filter(matches).map<Dict>(r=>({...r,quantity:r.censored?null:r.quantity})).sort(sorted)};
}

export function quoteActions(state:string):string[]{
 return ({INQUIRY:['quote_calculate'],CALCULATED:['quote_offer'],OFFERED:['quote_hold'],
  HELD:['quote_release','quote_commit'],COMMITTED_SIMULATED:['quote_release']} as Record<string,string[]>)[state]??[];
}

export function researchActionAllowed(action:string,target:string,runState:string,businessAudit:unknown){
 if(target==='baseline')return true;
 return action.startsWith('quote_')&&runState==='SUCCEEDED'&&businessAudit==='PASS';
}

export function verifySourceResearch(value:Dict,scope:Pick<ProfessionalScope,'planId'|'versionId'>){
 const id=value.identity;
 if(!id||id.plan_id!==scope.planId||id.data_version_id!==scope.versionId||id.run_id!==null||id.baseline_run_id!==null||id.target!=='baseline'||value.simulation_only!==true||value.execution_authorized!==false)throw Error('研究资料不属于当前计划确认版本。');
 return value;
}
