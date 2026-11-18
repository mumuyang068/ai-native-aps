import {demandKindLabel} from './reportExport';
import {useEffect,useState} from 'react';
import {request} from '../api/client';
import {Badge,Button,Definition,ErrorNote,Loading} from '../components/Controls';
import {DataTable,type TableColumn} from '../components/DataTable';
import {formatSiteTime} from './timeRange';
import {decisionQuantity,decisionRows,verifyDecisionIdentity,type DecisionFilters,type DecisionReport,type DemandDecisionScope,type DemandPlanningRow} from './demandDecisionModel';
import {forecastGrossPresentation,forecastGrossDetails} from './forecastGrossPresentation';
import {FirmDemand} from './FirmDemand';
import {ForecastOverride} from './ForecastOverride';
import {rememberForecastSelection,restoreForecastSelection} from './forecastSelection';
import './demand-decisions.css';

export type DemandForecastProps=DemandDecisionScope&{onInvestigate?:(demandId:string)=>void;onDraft?:(draftId:string)=>void;onSupply?:()=>void};

function savedFilters(key:string):DecisionFilters{
 try{const value=JSON.parse(sessionStorage.getItem(key)??'{}');return Object.fromEntries(['query','location','customer','uom','bucket'].filter(field=>typeof value?.[field]==='string').map(field=>[field,value[field]]))}catch{return {}}
}
function savedSelection(key:string):unknown{
 try{return JSON.parse(sessionStorage.getItem(key)??'null')}catch{return null}
}

export function DemandForecast(p:DemandForecastProps){
 const key=JSON.stringify([p.planId,p.versionId,p.runId,p.target]);
 const filterKey='forecast-filters:'+JSON.stringify([p.planId,p.versionId]);
 const selectionKey='forecast-selection:'+JSON.stringify([p.planId,p.versionId]);
 const[response,setResponse]=useState<{key:string;data:DecisionReport<DemandPlanningRow>}|null>(null),[failure,setFailure]=useState<{key:string;message:string}|null>(null),[retry,setRetry]=useState(0),[filterState,setFilterState]=useState(()=>({key:filterKey,value:savedFilters(filterKey)})),[selection,setSelection]=useState<{key:string;id:string}|null>(null);
 const filters=filterState.key===filterKey?filterState.value:savedFilters(filterKey);
 const setFilters=(value:DecisionFilters)=>{setFilterState({key:filterKey,value});try{sessionStorage.setItem(filterKey,JSON.stringify(value))}catch{/* The current view still works when browser storage is unavailable. */}};
 const choose=(row:DemandPlanningRow)=>{setSelection({key,id:row.id});const saved=rememberForecastSelection(p,row);try{if(saved)sessionStorage.setItem(selectionKey,JSON.stringify(saved));else sessionStorage.removeItem(selectionKey)}catch{/* The current selection remains usable without browser storage. */}};
 const clearSelection=()=>{setSelection(null);try{sessionStorage.removeItem(selectionKey)}catch{/* No persistent selection is available. */}};
 useEffect(()=>{
  const controller=new AbortController(),query=new URLSearchParams({data_version_id:p.versionId,run_id:p.runId,target:p.target});
  setFailure(null);setSelection(null);
  request<DecisionReport<DemandPlanningRow>>('/v1/enterprise-plans/'+encodeURIComponent(p.planId)+'/demand-planning?'+query,{signal:controller.signal})
   .then(value=>{if(!controller.signal.aborted){const verified=verifyDecisionIdentity(value,p),restored=restoreForecastSelection(savedSelection(selectionKey),p,verified);setResponse({key,data:verified});setSelection(restored?{key,id:restored.id}:null)}})
   .catch((error:unknown)=>{if(!controller.signal.aborted)setFailure({key,message:error instanceof Error?error.message:'无法读取当前运行的需求规划记录。'})});
  return()=>controller.abort();
 },[key,retry]);
 const error=failure?.key===key?failure.message:'',data=response?.key===key?response.data:null;
 if(error)return <section className="demand-decisions"><h2>预测与净需求</h2><ErrorNote error={error}/><Button onClick={()=>setRetry(value=>value+1)}>重新读取当前运行</Button></section>;
 if(!data)return <Loading/>;
 const ready=['PRESENT','EMPTY_CHECKED'].includes(data.status),rows=decisionRows(data.rows,filters),current=selection?.key===key?rows.find(row=>row.id===selection.id)??null:null;
 const time=(value:string|null|undefined)=>value?formatSiteTime(value,data.timezone):'未知 / 未提供';
 const buckets=[...new Map(data.rows.map(row=>[[row.start_at,row.end_at].join('|'),row] as const)).entries()];
 const values=(field:'location_id'|'customer_id'|'uom')=>[...new Set(data.rows.map(row=>row[field]).filter((value):value is string=>typeof value==='string'&&!!value))];
 const change=(field:keyof DecisionFilters,value:string)=>{setFilters({...filters,[field]:value});clearSelection()};
 const scenario=p.target!=='baseline',grossPresentation=forecastGrossPresentation(data.rows,p.target);
 const grossColumns:TableColumn<DemandPlanningRow>[]=scenario?[
  {id:'source_confirmed_gross_quantity',title:'来源确认 Gross',numeric:true,value:row=>decisionQuantity(row.source_confirmed_gross_quantity,row.uom)},
  {id:'scenario_gross_quantity',title:grossPresentation.column,numeric:true,value:row=><>{decisionQuantity(row.scenario_gross_quantity,row.uom)}{row.gross_overridden===false&&<small>来源 Gross 未改</small>}{row.gross_overridden===true&&<small>Gross 覆盖假设</small>}</>},
 ]:[{id:'gross_forecast',title:'原始预测 Gross',numeric:true,value:row=>decisionQuantity(row.gross_forecast,row.uom)}];
 const columns:TableColumn<DemandPlanningRow>[]=[
  {id:'period',title:'原始预测桶（半开）',value:row=><>{time(row.start_at)}<small>至 {time(row.end_at)} · 不含结束</small></>},
  {id:'item',title:'物料 / 地点 / 客户',value:row=><>{row.item_id}<small>{row.location_id} · {row.customer_id??'客户层级见匹配策略'}</small></>,sortValue:row=>row.item_id},
  ...grossColumns,
  ...([['firm_open_orders','Firm 规划需求'],['forecast_consumed','Firm 已抵扣'],['net_forecast','净预测 Net'],['effective_planning_demand','有效规划需求'],['planned_quantity','实际已规划量']] as const).map(([id,title])=>({id,title,numeric:true,value:(row:DemandPlanningRow)=>decisionQuantity(row[id],row.uom)})),
 ];
 return <section className="demand-decisions" aria-label="预测与净需求工作区">
  <header className="demand-decision-heading"><h2>预测与净需求</h2><Button icon="reload" onClick={()=>setRetry(value=>value+1)}>刷新当前运行</Button>{p.onSupply&&<Button onClick={p.onSupply}>调查辅料来源与补给</Button>}</header>
  <details className="demand-decision-scope"><summary>{p.target==='baseline'?'原始基线':'隔离模拟方案'} · {p.runId} · {data.timezone}</summary><Definition rows={[["计划",p.planId],["数据版本",p.versionId],["运行",p.runId],["认证模型",data.identity.profile??'未提供']]}/></details>
  <div className="demand-decision-state"><Badge kind={data.known_business_fail?'bad':ready?'':'risk'}>{data.status}</Badge>{data.reason&&<span>{data.reason}</span>}</div>
  {data.known_business_fail&&<p className="risk-message">当前运行存在已知业务核验失败；保留原始需求与规划记录供调查。</p>}
  {!ready?<p className="pending-note">{data.reason??'当前模型或运行尚无可读取的预测净需求记录。请查看来源与适用范围后，选择已认证计划。'}</p>:<>
   <FirmDemand key={key} scope={p} profile={data.identity.profile} onDraft={p.onDraft}/><div className="demand-decision-toolbar">
    <label>查找需求 / 来源版本<input aria-label="预测需求或来源版本筛选" value={filters.query??''} onChange={event=>change('query',event.target.value)} placeholder="物料、需求行、客户、预测版本"/></label>
    <label>地点<select aria-label="预测地点筛选" value={filters.location??''} onChange={event=>change('location',event.target.value)}><option value="">全部地点</option>{values('location_id').map(value=><option key={value}>{value}</option>)}</select></label>
    <label>客户<select aria-label="预测客户筛选" value={filters.customer??''} onChange={event=>change('customer',event.target.value)}><option value="">全部来源层级</option>{values('customer_id').map(value=><option key={value}>{value}</option>)}</select></label>
    <label>单位<select aria-label="预测单位筛选" value={filters.uom??''} onChange={event=>change('uom',event.target.value)}><option value="">全部单位</option>{values('uom').map(value=><option key={value}>{value}</option>)}</select></label>
    <label className="demand-bucket-filter">原始预测时间桶<select aria-label="原始预测时间桶筛选" value={filters.bucket??''} onChange={event=>change('bucket',event.target.value)}><option value="">全部原始时间桶</option>{buckets.map(([id,row])=><option key={id} value={id}>{time(row.start_at)} 至 {time(row.end_at)}</option>)}</select></label>
    <Button onClick={()=>{setFilters({});clearSelection()}}>清除筛选</Button>
   </div>
   <p className="demand-decision-note">显示 {rows.length} / {data.rows.length} 个原始时间桶。{grossPresentation.note}原始时间桶按来源展示，未认证跨桶消费与日周分摊。</p>
   <div className="demand-decision-workspace"><div className="demand-decision-table" tabIndex={0} aria-label="预测净需求表，可横向滚动"><DataTable rows={rows} columns={columns} rowId={row=>row.id} selected={current?.id} onSelect={choose} empty="当前筛选没有原始预测桶。可清除对象或时间桶筛选。"/></div>
    <aside className="demand-decision-inspector" aria-label="预测来源与抵扣检查器"><h3>{current?current.item_id+' · '+current.location_id:'预测来源与抵扣'}</h3>{current?<>
     <Definition rows={[["原始预测桶",time(current.start_at)+' → '+time(current.end_at)+'（不含）'],["单位",current.uom],["来源版本",current.source_version??'见原始来源'],["匹配层级",current.match_level??'见版本匹配策略'],...forecastGrossDetails(current,scenario),["Firm 规划需求",decisionQuantity(current.firm_open_orders,current.uom)],["Firm 已抵扣",decisionQuantity(current.forecast_consumed,current.uom)],["净预测 Net",decisionQuantity(current.net_forecast,current.uom)],["有效规划需求",decisionQuantity(current.effective_planning_demand,current.uom)],["实际已规划量",decisionQuantity(current.planned_quantity,current.uom)]]}/>
     <ForecastOverride key={key+current.id} scope={p} profile={data.identity.profile} row={current} onDraft={p.onDraft}/>
     <h4>同一运行的需求身份</h4>
     {(current.firm_demand_ids??[]).map(id=><p key={'firm:'+id}><strong>{demandKindLabel('FIRM_ORDER',current.consumptions?.find(c=>c.demand_id===id)?.provenance)}</strong> · {id}{p.onInvestigate&&<Button onClick={()=>p.onInvestigate?.(id)}>调查 Firm 需求</Button>}</p>)}
     {(current.forecast_demand_ids??[]).map(id=><p key={'forecast:'+id}><strong>净预测计划需求</strong> · {id}{p.onInvestigate&&<Button onClick={()=>p.onInvestigate?.(id)}>调查净预测需求</Button>}</p>)}
     {!(current.firm_demand_ids?.length||current.forecast_demand_ids?.length)&&<p className="demand-decision-note">当前记录没有提供可下钻的精确原生需求身份。</p>}
     <details className="demand-decision-source"><summary>预测原始来源与订单消费记录</summary><pre className="raw-json">{JSON.stringify({sources:current.sources??null,consumptions:current.consumptions??null,validation:current.validation??null},null,2)}</pre></details>
     <details className="demand-decision-source"><summary>完整时间桶记录</summary><pre className="raw-json">{JSON.stringify(current,null,2)}</pre></details>
    </>:<p>选择一个原始预测桶，核对预测版本、订单消费记录与同一运行中的订单 / 净预测需求。</p>}</aside>
   </div>
  </>}
  {data.policy&&<details className="demand-decision-source"><summary>当前版本的匹配与覆盖策略</summary><pre className="raw-json">{JSON.stringify(data.policy,null,2)}</pre></details>}
  {!!data.limits?.length&&<details className="demand-decision-limits"><summary>需求规划适用范围</summary><ul>{data.limits.map((value,index)=><li key={index}>{typeof value==='string'?value:JSON.stringify(value)}</li>)}</ul></details>}
 </section>;
}
