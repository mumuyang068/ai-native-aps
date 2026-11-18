import {useEffect,useState} from 'react';
import {request} from '../api/client';
import {Badge,Button,Definition,ErrorNote,Loading} from '../components/Controls';
import {DataTable,type TableColumn} from '../components/DataTable';
import {formatSiteTime} from './timeRange';
import {decisionQuantity,decisionRows,verifyDecisionIdentity,type DecisionFilters,type DecisionReport,type DemandDecisionScope,type InventoryAvailabilityRow} from './demandDecisionModel';
import './demand-decisions.css';

export type InventoryAvailabilityProps=DemandDecisionScope&{onAnalysis?:(itemId:string,locationId:string)=>void};

export function InventoryAvailability(p:InventoryAvailabilityProps){
 const key=JSON.stringify([p.planId,p.versionId,p.runId,p.target]);
 const[response,setResponse]=useState<{key:string;data:DecisionReport<InventoryAvailabilityRow>}|null>(null),[failure,setFailure]=useState<{key:string;message:string}|null>(null),[retry,setRetry]=useState(0),[filters,setFilters]=useState<DecisionFilters>({}),[selection,setSelection]=useState<{key:string;id:string}|null>(null);
 useEffect(()=>{
  const controller=new AbortController(),query=new URLSearchParams({data_version_id:p.versionId,run_id:p.runId,target:p.target});
  setFailure(null);setSelection(null);
  request<DecisionReport<InventoryAvailabilityRow>>('/v1/enterprise-plans/'+encodeURIComponent(p.planId)+'/inventory-availability?'+query,{signal:controller.signal})
   .then(value=>{if(!controller.signal.aborted)setResponse({key,data:verifyDecisionIdentity(value,p)})})
   .catch((error:unknown)=>{if(!controller.signal.aborted)setFailure({key,message:error instanceof Error?error.message:'无法读取当前运行的库存可用性。'})});
  return()=>controller.abort();
 },[key,retry]);
 const error=failure?.key===key?failure.message:'',data=response?.key===key?response.data:null;
 if(error)return <section className="demand-decisions"><h2>库存质量与可用性</h2><ErrorNote error={error}/><Button onClick={()=>setRetry(value=>value+1)}>重新读取当前运行</Button></section>;
 if(!data)return <Loading/>;
 const ready=['PRESENT','EMPTY_CHECKED'].includes(data.status),rows=decisionRows(data.rows,filters),current=selection?.key===key?rows.find(row=>row.id===selection.id)??null:null;
 const values=(field:'location_id'|'uom')=>[...new Set(data.rows.map(row=>row[field]).filter(Boolean))];
 const change=(field:keyof DecisionFilters,value:string)=>{setFilters(previous=>({...previous,[field]:value}));setSelection(null)};
 const columns:TableColumn<InventoryAvailabilityRow>[]=[
  {id:'item',title:'物料 / 地点 / 单位',value:row=><>{row.item_id}<small>{row.location_id} · {row.uom}</small></>,sortValue:row=>row.item_id},
  ...([['physical_onhand','实物在库'],['qc_waiting','QC 待检'],['quality_blocked','质量冻结'],['other_nonnettable','其他不可用'],['released_net_available','有来源放行净可用'],['planning_net_available','规划净可用'],['future_planned_supply','未来计划产出']] as const).map(([id,title])=>({id,title,numeric:true,value:(row:InventoryAvailabilityRow)=>decisionQuantity(row[id],row.uom)})),
  {id:'release',title:'质量状态输入记录',value:row=><>{row.release_evidence_status==='PRESENT'?'已取得输入记录':row.release_evidence_status==='NOT_COLLECTED'?'未采集':row.release_evidence_status??'未提供'}<small>{row.declaration_scope==='SYNTHETIC_CONFIRMED_RELEASE'?'合成模拟放行输入':row.declaration_scope==='NO_RELEASE'?'无放行声明':row.declaration_scope??'声明范围未提供'}</small><small>{row.actual_quality_release_verified===true?'有来源核验':'实际放行未核验'}</small></>},
 ];
 return <section className="demand-decisions" aria-label="库存质量与可用性工作区">
  <header className="demand-decision-heading"><h2>库存质量与可用性</h2><Button icon="reload" onClick={()=>setRetry(value=>value+1)}>刷新当前运行</Button></header>
  <details className="demand-decision-scope"><summary>{p.target==='baseline'?'原始基线':'隔离模拟方案'} · {p.runId} · {data.timezone}</summary><Definition rows={[["计划",p.planId],["数据版本",p.versionId],["运行",p.runId],["认证模型",data.identity.profile??'未提供']]}/></details>
  <div className="demand-decision-state"><Badge kind={data.known_business_fail?'bad':ready?'':'risk'}>{data.status}</Badge>{data.reason&&<span>{data.reason}</span>}</div>
  {data.known_business_fail&&<p className="risk-message">当前运行存在已知业务核验失败；库存与未来供应记录仅供核对和调查。</p>}
  {!ready?<p className="pending-note">{data.reason??'当前模型或运行尚未认证库存质量可用性。请选择已支持的库存详情，或核对原始来源。'}</p>:<>
   <div className="demand-decision-toolbar">
    <label>查找物料 / 来源<input aria-label="库存可用性物料筛选" value={filters.query??''} onChange={event=>change('query',event.target.value)} placeholder="物料、地点或来源版本"/></label>
    <label>地点<select aria-label="库存可用性地点筛选" value={filters.location??''} onChange={event=>change('location',event.target.value)}><option value="">全部地点</option>{values('location_id').map(value=><option key={value}>{value}</option>)}</select></label>
    <label>单位<select aria-label="库存可用性单位筛选" value={filters.uom??''} onChange={event=>change('uom',event.target.value)}><option value="">全部单位</option>{values('uom').map(value=><option key={value}>{value}</option>)}</select></label>
    <Button onClick={()=>{setFilters({});setSelection(null)}}>清除筛选</Button>
   </div>
   <p className="demand-decision-note">显示 {rows.length} / {data.rows.length} 条物料状态。实际质量放行、现货净可用与未来计划产出分别核对；计划 QC / PACK 完成不构成实际质量放行。</p>
   <div className="demand-decision-workspace"><div className="demand-decision-table" tabIndex={0} aria-label="库存质量可用性表，可横向滚动"><DataTable rows={rows} columns={columns} rowId={row=>row.id} selected={current?.id} onSelect={row=>setSelection({key,id:row.id})} empty="当前筛选没有库存可用性记录。可清除物料、地点或单位筛选。"/></div>
    <aside className="demand-decision-inspector" aria-label="库存质量可用性检查器"><h3>{current?current.item_id+' · '+current.location_id:'库存状态与来源'}</h3>{current?<>
     <Definition rows={[["状态生效时点",current.effective_at?formatSiteTime(current.effective_at,data.timezone):'未知 / 见来源'],["实物在库",decisionQuantity(current.physical_onhand,current.uom)],["QC 待检",decisionQuantity(current.qc_waiting,current.uom)],["质量冻结",decisionQuantity(current.quality_blocked,current.uom)],["其他不可用",decisionQuantity(current.other_nonnettable,current.uom)],["有来源放行净可用",decisionQuantity(current.released_net_available,current.uom)],["规划净可用",decisionQuantity(current.planning_net_available,current.uom)],["未来计划产出",decisionQuantity(current.future_planned_supply,current.uom)],["质量状态输入记录",current.release_evidence_status==='PRESENT'?'已取得输入记录':current.release_evidence_status??'未提供'],["放行声明引用",current.release_reference??'未提供'],["实际质量放行",current.actual_quality_release_verified===true?'有来源核验':'未核验'],["声明范围",current.declaration_scope==='SYNTHETIC_CONFIRMED_RELEASE'?'合成模拟放行输入，不代表实际质检放行':current.declaration_scope==='NO_RELEASE'?'未声明放行':current.declaration_scope??'见来源']]}/>
     {current.released_net_available===null&&<p className="pending-note">有来源放行净可用尚未核实，保留为未知。</p>}
     {p.onAnalysis&&<Button onClick={()=>p.onAnalysis?.(current.item_id,current.location_id)}>查看同一运行的物料事件</Button>}
     <details className="demand-decision-source"><summary>实物库存与质量状态来源</summary><pre className="raw-json">{JSON.stringify(current.sources??null,null,2)}</pre></details>
     <details className="demand-decision-source"><summary>未来计划供应记录</summary><p>下列供应来自当前运行的计划结果；实际制造、检验与出库状态需另有来源。</p><pre className="raw-json">{JSON.stringify(current.planned_supply_records??null,null,2)}</pre></details>
     <details className="demand-decision-source"><summary>完整库存可用性记录</summary><pre className="raw-json">{JSON.stringify(current,null,2)}</pre></details>
    </>:<p>选择一条物料状态，分别核对实物库存、质量冻结、放行来源和本运行的未来计划供应。</p>}</aside>
   </div>
  </>}
  {!!data.limits?.length&&<details className="demand-decision-limits"><summary>库存可用性适用范围</summary><ul>{data.limits.map((value,index)=><li key={index}>{typeof value==='string'?value:JSON.stringify(value)}</li>)}</ul></details>}
 </section>;
}
