import type {Dict} from '../api/contracts';
import type {ReportScope} from './reportModel';
export type WorkView={id:string;name:string;scope:ReportScope;params:Record<string,string>;savedAt:string};
const KEY='aps.personal-workviews.v1';
const fields=['report_demand_kind','report','grain','report_query','report_active','report_from','report_to','report_row','report_group','report_compare','report_line_query','work_kind','work_status','work_sort','work_desc','work_columns','exception_query','exception_from','exception_to','exception_status','exception_owner','exception_type','exception_evidence','exception_item','exception_location','exception_kind','exception_uom','exception_quantityMin','exception_plannedMin','exception_plannedMax','exception_shortMin','exception_columns','exception_row'];
const workspaces=['reports','exceptions','demand-planning','supply-work','inventory-policy'];
fields.push('report_issue_type','report_issue_owner','report_chart_group');
const supplyFields=['supply_view','supply_doc_period','supply_grain','supply_query','supply_attention','supply_period','supply_item','supply_location','supply_bucket','supply_doc','supply_event','purchase_query','purchase_supplier','purchase_item','purchase_location','purchase_period','purchase_status','purchase_origin','purchase_columns','purchase_group','purchase_doc','purchase_basis'];
fields.push(...supplyFields);
const supplyObjects=['purchase_group','supply_doc_period','purchase_doc','supply_doc','supply_event','supply_bucket','supply_item','supply_location'];
const supplyScope=(scope:ReportScope)=>JSON.stringify([scope.planId,scope.versionId,scope.runId,scope.target]);
const sortFields=['quantity','planned_quantity','due_quantity','overdue_unfulfilled','ending','load_hours','reference','item_id','resource_id','at','start_at'];
export function captureWorkView(name:string,search:URLSearchParams,scope:ReportScope):WorkView{
 if(!name.trim()||name.length>80)throw Error('请填写80字以内的视图名称。');
 const params:Record<string,string>={};for(const k of fields)if(search.has(k))params[k]=search.get(k)!;
 if(workspaces.includes(search.get('analysis')??''))params.analysis=search.get('analysis')!;
 if(search.get('report_scope')!==JSON.stringify(scope))delete params.report_row;
 if(search.get('exception_scope')!==JSON.stringify(scope))delete params.exception_row;
 if(search.get('supply_scope')!==supplyScope(scope))for(const k of supplyObjects)delete params[k];
 if(params.work_sort&&!sortFields.includes(params.work_sort))delete params.work_sort;
 return {id:crypto.randomUUID(),name:name.trim(),scope:{...scope},params,savedAt:new Date().toISOString()};
}
export function restoreWorkView(view:WorkView,scope:ReportScope){
 const stale=Object.keys(scope).some(k=>scope[k as keyof ReportScope]!==view.scope[k as keyof ReportScope]);
 const search=new URLSearchParams({target:scope.target,analysis:workspaces.includes(view.params.analysis)?view.params.analysis:'reports'});
 for(const k of fields)if(typeof view.params[k]==='string'&&(!stale||!['report_row','exception_row',...supplyObjects].includes(k)))search.set(k,view.params[k]);
 search.set('report_scope',JSON.stringify(scope));search.set('exception_scope',JSON.stringify(scope));search.set('supply_scope',supplyScope(scope));return {search,stale};
}
type Reader={getItem:(key:string)=>string|null};type Writer={setItem:(key:string,value:string)=>void};
export function readWorkViews(storage:Reader):{views:WorkView[];error:string}{
 try{const value=storage.getItem(KEY);if(!value)return{views:[],error:''};const parsed=JSON.parse(value);
  if(parsed.version!==1||!Array.isArray(parsed.views)||parsed.views.length>30||parsed.views.some((v:WorkView)=>!v?.id||!v.name||!v.scope||!v.params||!['planId','versionId','runId','target'].every(k=>typeof v.scope[k as keyof ReportScope]==='string')))throw Error();
  return {views:parsed.views,error:''};
 }catch{return {views:[],error:'已保存的工作视图无法读取；未恢复其中的对象。可另存当前视图。'}}
}
export function writeWorkViews(storage:Writer,views:WorkView[]){if(views.length>30)throw Error('本机最多保存30个视图。');storage.setItem(KEY,JSON.stringify({version:1,views}));}
export function workColumns<T extends {id:string}>(columns:T[],selection:string|null){
 if(!selection)return columns;const ids=selection.split(',');return columns.filter((c,i)=>i===0||c.id==='reference'||ids.includes(c.id));
}
export function filterWorkRows(rows:Dict[],kind:string,status:string,sort:string,desc:boolean){
 const filtered=rows.filter(r=>(!kind||r.kind===kind)&&(!status||r.status===status));if(!sortFields.includes(sort))return filtered;
 const numeric=['quantity','planned_quantity','due_quantity','overdue_unfulfilled','ending','load_hours'].includes(sort);
 return filtered.slice().sort((a,b)=>{const av=a[sort],bv=b[sort];if(av==null)return bv==null?0:1;if(bv==null)return -1;const n=numeric?Number(av)-Number(bv):String(av).localeCompare(String(bv));return (desc?-1:1)*n});
}
