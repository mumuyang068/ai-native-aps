import {useEffect,useMemo,useRef,useState} from 'react';
import {Link,useSearchParams} from 'react-router';
import type {Dict,PlanState,PlanView} from '../api/contracts';
import {request} from '../api/client';
import {Badge,Button,Definition,Dialog,ErrorNote,Loading,Tabs} from '../components/Controls';
import {BusinessFacts} from './BusinessFacts';
import {DemandFulfillment} from './DemandFulfillment';
import {CockpitDemandSnapshot,CockpitSupplyPanel,CockpitInventoryPanel,CockpitResourcePanel,CockpitTransitPanel} from './CockpitPanels';
import {CockpitDeliveryPerformance} from './CockpitWork';
import {CockpitComparison} from './CockpitComparison';
import {homeRows} from './cockpitHomeModel';
import {cockpitWorkRows,cockpitWorkDrill,selectCockpitWork} from './cockpitWorkModel';
import {cockpitPerformanceDrill} from './cockpitWorkModel';
import {siteInputToIso} from './analysisModel';
import {fulfillmentAmount} from './demandFulfillmentModel';
import {reportRowId} from './reportModel';
import {supports} from './capabilityModel';
import {businessLabel,runLabel} from './planModel';
import {formatSiteTime} from './timeRange';
import {cockpitAttention,cockpitDemandEmptyMessage,verifyCockpit,domainAvailability,cockpitSupplyRows,cockpitResourceRows,cockpitProducts,type CockpitScope} from './cockpitModel';
import {cockpitDrill,cockpitLocatorError} from './cockpitNavigation';
import './cockpit.css';
import './cockpit-home.css';

type Props={scope:CockpitScope;view:PlanView;state:PlanState;onRefresh:()=>void};
export function PlanCockpit({scope,view,state,onRefresh}:Props){
 const [search,setSearch]=useSearchParams(),error=cockpitLocatorError(search,scope);
 const mode=search.get('cockpit_view')==='plan'?'plan':'business';
 if(error)return <ErrorNote error={error}/>;
 const changeView=(value:string)=>setSearch(previous=>{const p=new URLSearchParams(previous);p.set('cockpit_view',value);return p});
 return mode==='plan'?<><div className="home-results-heading"><Button icon="left" onClick={()=>changeView('business')}>返回驾驶舱</Button><h2>计划结果</h2></div><PlanResultCockpit scope={scope} view={view} state={state} onRefresh={onRefresh}/></>:<BusinessFacts scope={{planId:scope.planId,versionId:scope.versionId}} home={{view,state,target:scope.target,onPlanResults:()=>changeView('plan')}}/>;
}
function PlanResultCockpit({scope,view,state,onRefresh}:Props){
 const[search,setSearch]=useSearchParams(),[help,setHelp]=useState(false),[performance,setPerformance]=useState(false),[retry,setRetry]=useState(0);
 const grain=search.get('cockpit_grain')==='day'?'day':'week',compare=scope.target!=='baseline'&&search.get('cockpit_compare')!=='0';
 const from=search.get('cockpit_from')??'',to=search.get('cockpit_to')??'';
 const key=JSON.stringify([scope,grain,from,to]),[report,setReport]=useState<{key:string;value:Dict}|null>(null),[analysis,setAnalysis]=useState<{key:string;value:Dict}|null>(null),[base,setBase]=useState<{key:string;value:Dict}|null>(null),[baseAnalysis,setBaseAnalysis]=useState<{key:string;value:Dict}|null>(null);
 const[errors,setErrors]=useState<Record<string,string>>({});
 const root=useRef<HTMLElement>(null),error=cockpitLocatorError(search,scope),successful=view.run?.state==='SUCCEEDED'&&!!view.result;
 const baseRun=state.baseline.run?.solve_run_id??'',baseKey=JSON.stringify([key,compare,baseRun]);
 useEffect(()=>{const c=new AbortController();setErrors({});if(!successful||error)return;
  const query=new URLSearchParams({data_version_id:scope.versionId,run_id:scope.runId,target:scope.target,grain});
  if(from)query.set('start',from);if(to)query.set('end',to);
  request<Dict>('/v1/enterprise-plans/'+scope.planId+'/reports?'+query,{signal:c.signal}).then(v=>{verifyCockpit(v,scope);if(!c.signal.aborted)setReport({key,value:v})}).catch(e=>{if(!c.signal.aborted)setErrors(x=>({...x,report:e.message}))});
  const analysisQuery=new URLSearchParams(query);analysisQuery.delete('grain');
  if(supports(scope.profile,'analyses','inventory')||supports(scope.profile,'analyses','resources'))request<Dict>('/v1/enterprise-plans/'+scope.planId+'/analysis?'+analysisQuery,{signal:c.signal}).then(v=>{verifyCockpit(v,scope);if(!c.signal.aborted)setAnalysis({key,value:v})}).catch(e=>{if(!c.signal.aborted)setErrors(x=>({...x,analysis:e.message}))});
  return()=>c.abort();
 },[key,retry,successful,error]);
 useEffect(()=>{const c=new AbortController();if(!compare||!successful||error||!baseRun)return;setErrors(x=>({...x,comparison:'',baseAnalysis:''}));
  const query=new URLSearchParams({data_version_id:scope.versionId,run_id:baseRun,target:'baseline',grain});
  if(from)query.set('start',from);if(to)query.set('end',to);
  request<Dict>('/v1/enterprise-plans/'+scope.planId+'/reports?'+query,{signal:c.signal}).then(v=>{verifyCockpit(v,{...scope,runId:baseRun,target:'baseline'});if(!c.signal.aborted)setBase({key:baseKey,value:v})}).catch(e=>{if(!c.signal.aborted)setErrors(x=>({...x,comparison:e.message}))});
  if(supports(scope.profile,'analyses','inventory')){const q=new URLSearchParams(query);q.delete('grain');request<Dict>('/v1/enterprise-plans/'+scope.planId+'/analysis?'+q,{signal:c.signal}).then(v=>{verifyCockpit(v,{...scope,runId:baseRun,target:'baseline'});if(!c.signal.aborted)setBaseAnalysis({key:baseKey,value:v})}).catch(e=>{if(!c.signal.aborted)setErrors(x=>({...x,baseAnalysis:e.message}))})}
  return()=>c.abort();
 },[baseKey,retry,successful,error]);
 const data=successful&&!error&&report?.key===key?report.value:null,native=successful&&!error&&analysis?.key===key?analysis.value:null;
 const items=useMemo(()=>data?cockpitAttention(data,native,{...view.context,confirmed_supply_windows:view.interpretation?.confirmed_supply_windows},state.drafts,scope):[],[data,native,view.context,view.interpretation,state.drafts,key]);
 useEffect(()=>{if(!data)return;const id=search.get('cockpit_focus');if(!id)return;const frame=requestAnimationFrame(()=>{const element=[...(root.current?.querySelectorAll<HTMLElement>('[data-cockpit-id]')??[])].find(el=>el.dataset.cockpitId===id);element?.focus({preventScroll:true})});return()=>cancelAnimationFrame(frame)},[data,items,search.get('cockpit_focus')]);
 function open(id:string,params:Record<string,string>){
  const draft=params.target&&params.target!==scope.target?state.drafts.find(d=>d.id===params.target&&!d.run_id&&['DRAFT','RETAINED'].includes(d.state)):null;
  setSearch(cockpitDrill(search,scope,id,params,draft?{...scope,target:draft.id,runId:''}:undefined));
 }
 const time=(v:string|undefined)=>v?formatSiteTime(v,data?.timezone??view.context?.timezone??state.timezone):'时间待核实';
 if(error)return <section className="plan-cockpit"><ErrorNote error={error}/><Button onClick={()=>setSearch({target:scope.target,analysis:'cockpit'})}>查看当前方案的有效运行</Button></section>;
 if(!successful)return <section className="plan-cockpit cockpit-pending"><Badge kind={view.run?.state==='FAILED'?'bad':''}>{runLabel(view.run?.state)}</Badge><h2>{view.run?.state==='UNAVAILABLE'?'当前结果暂不可核验':view.run?.state==='FAILED'?'本次计算未完成':view.run?.state==='NOT_RUN'?'这个方案尚未计算':'尚无可展示的计划结果'}</h2><p>{view.run?.state==='UNAVAILABLE'?'结果读取或来源核验受限，不能据此判定尚未计算或业务无解。请查看状态原因与原始运行身份。':view.run?.state==='FAILED'?'本次运行失败，尚无有效计划结果，未证明业务无解。原始基线仍保留，可进入原计划核对。':'当前方案尚无有效完成结果；不会沿用另一方案的数量与日期。请从原计划入口继续。'}</p>{(view.business_summary||view.error)&&<details><summary>查看状态原因与来源</summary><p>{view.business_summary??view.error}</p></details>}<div className="cockpit-actions"><Button onClick={onRefresh}>刷新当前状态</Button><Button onClick={()=>setSearch({target:scope.target,analysis:'schedule'})}>打开原计划与运行操作</Button>{scope.target!=='baseline'&&<Button onClick={()=>setSearch({target:'baseline',analysis:'cockpit'})}>查看原始基线</Button>}</div></section>;
 if(!data)return <section className="plan-cockpit"><ErrorNote error={errors.report??''}/>{errors.report?<><Button onClick={()=>setRetry(x=>x+1)}>重新读取当前计划</Button><Button onClick={()=>setSearch({target:scope.target,analysis:'cockpit'})}>恢复全规划期</Button></>:<Loading/>}</section>;
 const products=cockpitProducts(data),group=products.some(g=>g.id===search.get('cockpit_group'))?search.get('cockpit_group')!:products[0]?.id??'',kind=search.get('cockpit_kind')??(products.find(p=>p.id===group)?.firm?'FIRM_ORDER':products.find(p=>p.id===group)?.forecast?'NET_FORECAST':'all');
 const availability=domainAvailability(data,native),failed=Object.values(availability).includes('FAIL'),contextForPanels={...view.context,confirmed_supply_windows:view.interpretation?.confirmed_supply_windows},baseContext={...state.baseline.context,confirmed_supply_windows:state.baseline.interpretation?.confirmed_supply_windows};
 const work=items.filter(i=>i.kind==='work'),risks=items.filter(i=>i.kind!=='work'&&i.level==='risk');
 const purchases=cockpitSupplyRows(data,contextForPanels,scope),resources=cockpitResourceRows(data,scope),allWork=failed?[]:cockpitWorkRows(data,contextForPanels,view.result!,scope);
 const purchaseWork=selectCockpitWork(allWork,'purchase','start','',data.range.start_at,data.range.end_at),transitActions:Record<string,Record<string,string>>={}; for(const d of homeRows(view.result?.distribution_suggestions)){const row=allWork.find(r=>r.domain==='distribution'&&r.params.net_do===d.id);if(row)transitActions[String(d.engine_ref)]=row.params}
 const allRecent=[...selectCockpitWork(allWork,'manufacturing','start','',data.range.start_at,data.range.end_at),...selectCockpitWork(allWork,'distribution','start','',data.range.start_at,data.range.end_at)],recent=allRecent.slice(0,4);
 function change(values:Record<string,string>){setSearch(p=>{const n=new URLSearchParams(p);for(const[k,v]of Object.entries(values)){v?n.set(k,v):n.delete(k)}return n})}
 function chooseKind(value:string,groupId?:string){setSearch(p=>{p.set('cockpit_kind',['FIRM_ORDER','NET_FORECAST'].includes(value)?value:'all');if(groupId){p.set('cockpit_group',groupId);p.set('cockpit_performance_group',groupId);p.delete('cockpit_performance')}p.delete('cockpit_focus');return p})}
 function entry(i:typeof items[number]){return <li key={i.id} className={'cockpit-item '+i.level}><button data-cockpit-id={i.id} onClick={()=>open(i.id,i.params)}><div><strong>{i.title}</strong><span className="cockpit-item-detail">{i.detail}</span>{i.at&&<time>{time(i.at)}</time>}</div><span className="cockpit-item-arrow" aria-hidden="true">→</span></button></li>}
 const focus=(id:string)=>{const node=root.current?.querySelector<HTMLElement>('#'+id);if(node){node.scrollIntoView({block:'nearest'});node.focus({preventScroll:true})}else setHelp(true)};
 const status=(s:string)=>s==='UNSUPPORTED'?'未认证':s==='EMPTY_CHECKED'?'已核对无记录':['PRESENT'].includes(s)?'已有计划记录':s==='FAIL'?'核验失败':'证据待核实';
 const compareUrl='#/plans/'+scope.planId+'/compare/'+scope.target+'?'+cockpitDrill(search,scope,'comparison',{}).toString();
 return <section ref={root} className="plan-cockpit cockpit-composed home-refined" aria-label="当前计划驾驶舱">
  <div className="cockpit-context"><div><Badge kind={failed?'bad':view.interpretation?.business_status==='PARTIAL_OR_UNPLANNED'?'risk':''}>{failed?'业务核验未通过':businessLabel(view.interpretation?.business_status)}</Badge><span>{data.timezone}</span></div><div><Link to={'/data/'+scope.versionId}>确认资料</Link><Button className="quiet" onClick={()=>open('manufacturing',{analysis:scope.profile==='network-distribution-confirmed-supply-v1'?'network-supply':'manufacturing'})}>{scope.profile==='network-distribution-confirmed-supply-v1'?'地点与配送':'制造安排'}</Button><Button className="quiet" icon="info" onClick={()=>setHelp(true)}>分析范围与来源</Button></div></div>
  <CockpitWindow key={key} data={data} onChange={change}/>
  {failed&&<ErrorNote error="这份运行存在已知业务违规。以下结果只供调查，不能作为可执行安排。"/>}
  <nav className="home-domain-strip" aria-label="计划领域概况"><button onClick={()=>focus('home-demand')}>需求 <strong>{status(availability.demand)}</strong><span>{items.filter(i=>i.kind==='demand').length}条缺口待调查</span></button><button onClick={()=>focus('home-purchase')}>采购与QC <strong>{purchases.length?purchases.length+'笔关注采购':'摘要待核实或未认证'}</strong></button><button onClick={()=>focus('home-inventory')}>库存 <strong>{status(availability.inventory)}</strong></button><button onClick={()=>focus('home-resources')}>资源 <strong>{resources.length?new Set(resources.map(r=>r.resource_id)).size+'项计划占用':availability.resource==='EMPTY_CHECKED'?'本计划无占用记录':status(availability.resource)}</strong></button>{scope.profile==='network-distribution-confirmed-supply-v1'&&<button onClick={()=>focus('home-transit')}>配送 <strong>{status(native?.inventory_analysis?.transit?.status??'NOT_CHECKED')}</strong></button>}</nav>
  <div className="home-primary"><div id="home-demand" tabIndex={-1}><CockpitDemandSnapshot compact data={data} selectedGroup={group} selectedKind={kind} onKind={chooseKind} onPerformance={()=>setPerformance(true)} onInspect={(id,kind,group)=>{const line=(data.demand?.lines??[]).find((l:Dict)=>l.demand_id===id);open('matrix:'+id,kind==='FIRM_ORDER'&&line?cockpitPerformanceDrill(data,scope,line,line):{analysis:'reports',report:'demand',report_line_query:id,report_group:group,...(kind==='NET_FORECAST'?{report_demand_kind:kind}:{})})}}/></div>
  <section className="cockpit-business-panel home-attention" aria-label="主要风险与下一步"><div className="cockpit-section-title"><h2>主要风险与下一步</h2><span>{risks.length}项</span></div>{risks.length?<ul className="cockpit-work-list">{risks.slice(0,3).map(entry)}</ul>:<p className="cockpit-empty">{failed?'请先调查业务核验问题。':cockpitDemandEmptyMessage(availability.demand)}</p>}{risks.length>3&&<Button className="quiet" onClick={()=>open('all-demands',{analysis:'reports',report:'demand',grain})}>查看其余 {risks.length-3} 项</Button>}</section></div>
  <ErrorNote error={errors.analysis??''}/>
  {!failed&&<div className="home-domain-grid"><CockpitSupplyPanel key={key} data={data} context={contextForPanels} scope={scope} open={open} workRows={purchaseWork} suggestionCount={view.result?.purchase_suggestions?.length??0}/><CockpitInventoryPanel key={key} data={data} native={native} scope={scope} open={open}/><CockpitResourcePanel key={key} data={data} scope={scope} open={open}/><CockpitTransitPanel key={key} native={native} scope={scope} timezone={data.timezone} open={open} actions={transitActions}/></div>}
  <div className="home-secondary"><CockpitComparison key={key} demandKind={kind} data={data} base={base?.key===baseKey?base.value:null} native={native} baseNative={baseAnalysis?.key===baseKey?baseAnalysis.value:null} context={contextForPanels} baseContext={baseContext} scope={scope} pending={compare&&base?.key!==baseKey&&!errors.comparison&&!!baseRun} enabled={compare} error={errors.comparison??''} onEnable={()=>change({cockpit_compare:'1'})} open={open} compareUrl={compareUrl}/>
  <section className="cockpit-business-panel home-recent" aria-label="近期工作与方案"><div className="cockpit-section-title"><h2>近期工作与方案</h2><span>显示{recent.length}/{allRecent.length}项工序或配送 · {work.length}个未计算方案</span></div><ul className="home-object-links">{recent.map(r=><li key={r.id}><button data-cockpit-id={r.id} onClick={()=>open(r.id,cockpitWorkDrill(r,data,contextForPanels,'start'))}><span><strong>{r.title}</strong><small>{r.detail}</small></span><small>{fulfillmentAmount(r.quantity,r.uom)}<br/>{r.start?time(r.start):'时间待核实'}</small></button></li>)}</ul>{allRecent.length>4&&<Button className="quiet" onClick={()=>open("all-recent",{analysis:scope.profile==="network-distribution-confirmed-supply-v1"?"network-supply":"manufacturing"})}>查看全部工序或配送</Button>}{work.length?<ul className="cockpit-work-list">{work.slice(0,2).map(entry)}</ul>:!recent.length&&<p className="cockpit-empty">当前范围内没有已认证的工序或配送待办，也没有未计算方案。</p>}</section></div>
  {['demand','inventory','resource'].some(k=>!['PRESENT','EMPTY_CHECKED','UNSUPPORTED'].includes((availability as Dict)[k]))&&<p role="status" className="home-evidence-notice">部分领域证据待核实；缺失记录没有作为零风险或零数量。<Button className="quiet" onClick={()=>setHelp(true)}>查看范围</Button></p>}
  {performance&&<Dialog title="需求履约分析" size="840" centered onClose={()=>setPerformance(false)}><CockpitDeliveryPerformance data={data} scope={scope} selectedGroup={group} search={search} change={change} open={open}/><Button className="primary" onClick={()=>open('demand-details',{analysis:'reports',report:'demand',report_group:group,report_demand_kind:kind,grain})}>打开完整需求与期间分析</Button></Dialog>}
  {help&&<Dialog title="本计划的分析范围与来源" size="600" centered onClose={()=>setHelp(false)}><Definition rows={[["需求",status(availability.demand)],["地点库存",status(availability.inventory)],["资源容量占用",resources.length?new Set(resources.map(r=>r.resource_id)).size+"项具名资源；见首页资源容量占用":availability.resource==="EMPTY_CHECKED"?"当前计划已核对无资源占用记录，不显示容量图":status(availability.resource)],["配送与在途",scope.profile==="network-distribution-confirmed-supply-v1"?status(native?.inventory_analysis?.transit?.status??"NOT_CHECKED"):"当前计划不适用；跨地点供需计划展示配送区间"]]}/><details><summary>运行身份与来源</summary><Definition rows={[["企业计划",scope.planId],["企业版本",scope.versionId],["当前运行",scope.runId],["方案",scope.target],["模型",scope.profile],["需求证据",availability.demand],["库存证据",availability.inventory],["资源证据",availability.resource]]}/></details><p>计划满足不代表实际交付。阶段、地点和单位分别核对；无实际关联证据时，不将库存或资源事件归属到某条需求。</p>{errors.baseAnalysis&&<p role="status">{errors.baseAnalysis}</p>}</Dialog>}
 </section>;
}

function CockpitWindow({data,onChange}:{data:Dict;onChange:(values:Record<string,string>)=>void}){
 const local=(v:string)=>formatSiteTime(v,data.timezone).replace(' ','T');
 const[start,setStart]=useState(local(data.range.start_at)),[end,setEnd]=useState(local(data.range.end_at)),[error,setError]=useState('');
 function apply(){const a=siteInputToIso(start,data.timezone),b=siteInputToIso(end,data.timezone);if(!a||!b||Date.parse(a)>=Date.parse(b)){setError('开始和结束须为有效时间，且结束晚于开始。');return}onChange({cockpit_from:a,cockpit_to:b,cockpit_focus:''})}
 return <div className="cockpit-window"><details><summary>观察期间 · {formatSiteTime(data.range.start_at,data.timezone)} — {formatSiteTime(data.range.end_at,data.timezone)}</summary><div><label>开始<input aria-label="驾驶舱开始时间" type="datetime-local" value={start} onChange={e=>setStart(e.target.value)}/></label><label>结束<input aria-label="驾驶舱结束时间" type="datetime-local" value={end} onChange={e=>setEnd(e.target.value)}/></label><Button onClick={apply}>应用期间</Button><Button className="quiet" onClick={()=>onChange({cockpit_from:'',cockpit_to:'',cockpit_focus:''})}>全规划期</Button></div>{error&&<p role="alert">{error}</p>}</details><span>需求按交期筛选；工序按区间，采购与调拨按所选时点</span></div>;
}
