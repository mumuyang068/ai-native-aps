import type {ReportScope} from './reportModel';

const savedKeys=['target','analysis','cockpit_view','facts_start','facts_end','facts_item_id','facts_location_id','facts_kind','facts_status','facts_offset','facts_section','cockpit_grain','cockpit_group','cockpit_focus','cockpit_compare','cockpit_kind','cockpit_stock','cockpit_from','cockpit_to','cockpit_domain','cockpit_phase','cockpit_status','cockpit_performance','cockpit_performance_group'] as const;
const identity=(s:ReportScope)=>JSON.stringify([s.planId,s.versionId,s.runId,s.target]);
export function cockpitSearch(previous:URLSearchParams){
 const p=new URLSearchParams();
 for(const k of savedKeys)if(previous.has(k))p.set(k,previous.get(k)!);
 p.set('target',previous.get('target')??'baseline');p.set('analysis','cockpit');return p;
}
/** Local query state only. No URL, archive path or business data is restored. */
export function cockpitDrill(previous:URLSearchParams,scope:ReportScope,focus:string,params:Record<string,string>,destination?:ReportScope){
 const origin=cockpitSearch(previous);origin.set('cockpit_focus',focus);
 const p=new URLSearchParams({target:scope.target,...params});
 if(destination&&(destination.planId!==scope.planId||destination.versionId!==scope.versionId||destination.target!==p.get('target')))throw Error('方案工作入口不属于当前计划或版本。');
 p.set('cockpit_return',JSON.stringify({scope:identity(scope),search:origin.toString(),...(destination?{destination:identity(destination)}:{})}));return p;
}
export function cockpitReturn(previous:URLSearchParams,scope:ReportScope,knownOrigins:ReportScope[]=[]):URLSearchParams|null{
 const text=previous.get('cockpit_return');if(!text||text.length>5000)return null;
 try{const value=JSON.parse(text);
  // Cross-target work links must bind both ends and an origin re-read from this plan's state.
  const origin=value.scope===identity(scope)?scope:value.destination===identity(scope)?knownOrigins.find(s=>s.planId===scope.planId&&s.versionId===scope.versionId&&identity(s)===value.scope):null;
  if(!origin||typeof value.search!=='string')return null;
  const p=new URLSearchParams(value.search);
  if([...p.keys()].some(k=>!savedKeys.includes(k as typeof savedKeys[number]))||p.get('analysis')!=='cockpit'||p.get('target')!==origin.target)return null;
  return p;
 }catch{return null}
}
export function cockpitPeriodParams(scope:ReportScope,grain:string,group:string,rowId:string,compare:boolean){
 return {analysis:'reports',report:'demand',grain,report_row:rowId,report_group:group,
  report_scope:JSON.stringify({planId:scope.planId,versionId:scope.versionId,runId:scope.runId,target:scope.target}),
  report_compare:compare?'1':'0',report_active:'false'};
}
export function cockpitLocatorError(search:URLSearchParams,scope:ReportScope){
 if(search.has('data_version_id')&&search.get('data_version_id')!==scope.versionId)return '链接的企业数据版本与当前计划不一致，已停止展示。';
 if(search.has('run_id')&&search.get('run_id')!==scope.runId)return '链接的运行不属于当前选中方案，已停止展示。';
 return '';
}
const lastKey='aps:cockpit:last-work:v1';
export function rememberCockpit(storage:Pick<Storage,'setItem'>,planId:string,target:string){
 if(!/^PLAN-[a-f0-9]{16}$/.test(planId)||!(target==='baseline'||/^DRAFT-[a-f0-9]{16}$/.test(target)))return;
 try{storage.setItem(lastKey,JSON.stringify({planId,target}))}catch{/* Storage may be unavailable. */}
}
export function previousCockpit(storage:Pick<Storage,'getItem'>):{planId:string;target:string}|null{
 try{const v=JSON.parse(storage.getItem(lastKey)??'null');return v&&/^PLAN-[a-f0-9]{16}$/.test(v.planId)&&(v.target==='baseline'||/^DRAFT-[a-f0-9]{16}$/.test(v.target))?v:null}catch{return null}
}
