import {useEffect,useState,type ReactNode} from 'react';
import {useSearchParams} from 'react-router';
import {request} from '../api/client';
import type {Dict} from '../api/contracts';
import {Badge,Button,Definition,Dialog,ErrorNote,Loading} from '../components/Controls';
import {DataTable,type TableColumn} from '../components/DataTable';
import {qty,unit,businessLabel} from './planModel';
import {formatSiteTime} from './timeRange';
import {deliveryStatusLabel} from './orderModel';
import {evidenceLabels} from './orderInvestigationModel';
import {filterExceptions,verifyExceptions,rememberExceptionOrigin,type ExceptionScope,type ExceptionFilters} from './exceptionsModel';
import {ExceptionReviews} from './ExceptionReviews';
import {ExceptionWorkControls} from './ExceptionWorkControls';
import {workColumns} from './workViewModel';
import './decision-workbench.css';

type Props=ExceptionScope&{baselineRunId?:string;onInvestigate:(id:string)=>void;onPriority?:(d:Dict)=>void;priorityEditor?:ReactNode};
export function ExceptionsWorkbench(p:Props){
 const[search,setSearch]=useSearchParams(),[response,setResponse]=useState<{key:string;data:Dict}|null>(null),[error,setError]=useState(''),[retry,setRetry]=useState(0),[source,setSource]=useState<Dict|null>(null);
 const key=JSON.stringify([p.planId,p.versionId,p.runId,p.target,p.profile,p.resultHash]);
 useEffect(()=>{const c=new AbortController();setError('');setSource(null);const query=new URLSearchParams({data_version_id:p.versionId,run_id:p.runId,target:p.target});
  request<Dict>('/v1/enterprise-plans/'+p.planId+'/exceptions?'+query,{signal:c.signal}).then(v=>{if(!c.signal.aborted)setResponse({key,data:verifyExceptions(v,p)})}).catch(e=>{if(e.name!=='AbortError'&&!c.signal.aborted)setError(e.message)});return()=>c.abort();
 },[key,retry]);
 function change(field:string,value:string){setSearch(previous=>{const next=new URLSearchParams(previous);next.delete('exception_row');value?next.set('exception_'+field,value):next.delete('exception_'+field);return next})}
 if(error)return <section className="decision-workbench"><h2>异常与约束</h2><ErrorNote error={error}/><Button onClick={()=>setRetry(n=>n+1)}>重新读取当前运行</Button></section>;
 const data=response?.key===key?response.data:null;if(!data)return <Loading/>;
 const filters:ExceptionFilters=Object.fromEntries(['query','from','to','status','owner','type','evidence','item','location','kind','uom','quantityMin','plannedMin','plannedMax','shortMin'].map(k=>[k,search.get('exception_'+k)??'']));
 const rows=filterExceptions(data.demands,filters,data.timezone),selected=search.get('exception_row'),current=rows.find(r=>r.demand_id===selected)??(selected?null:rows[0]),time=(v:string|null|undefined)=>v?formatSiteTime(v,data.timezone):'未知 / 未排定';
 const types=[...new Set<string>(data.demands.flatMap((d:Dict)=>(d.constraints??[]).map((c:Dict)=>String(c.record.name))))];
 const table:TableColumn<Dict>[]=[
  {id:'demand',title:'需求行 / 产品',value:d=><>{d.order_ref??d.demand_id}<small>{d.item_id} · {d.location_id}</small></>,sortValue:d=>d.demand_id},
  {id:'priority',title:'需求优先级',numeric:true,value:d=>d.priority==null?'未知':String(d.priority)},
  {id:'due',title:'要求日期',value:d=>time(d.due_at),sortValue:d=>d.due_at},
  {id:'planned',title:'计划满足 / 需求',numeric:true,value:d=>qty(d.planned_quantity)+' / '+qty(d.requested_quantity)+' '+unit(d.uom)},
  {id:'short',title:'欠量',numeric:true,value:d=><span className={Number(d.unfulfilled_quantity)>0?'risk-text':''}>{qty(d.unfulfilled_quantity)} {unit(d.uom)}</span>},
  {id:'delivery',title:'已排部分日期',value:d=><>{Number(d.planned_quantity)>0?time(d.planned_delivery_at):'完全未排定'}<small>{deliveryStatusLabel(d.status)}</small></>},
  {id:'constraint',title:'需求级 Constraint',value:d=><>{evidenceLabels[d.constraint_status]??d.constraint_status}<small>{d.constraints.length?d.constraints.length+' 条原生记录':'无可展示记录'}</small></>}
 ];
 const openReview=()=>{setSearch(previous=>{const next=rememberExceptionOrigin(previous,p,current?.demand_id);next.set('analysis','review');if(current)next.set('review_focus',current.demand_id);return next})};
 return <section className="decision-workbench" aria-label="异常与约束工作清单">
  <header className="decision-heading"><h2>日常需求与异常</h2><Button icon="compare" onClick={openReview}>评审多个方案</Button></header>
  {p.priorityEditor}
  <p className="decision-endpoint">{data.endpoint?.label??'当前计划需求履约'} · {data.timezone}</p>
  <details className="decision-identity"><summary>{p.target==='baseline'?'原始基线':'隔离草案'} · {p.runId}</summary><Definition rows={[["计划",p.planId],["数据版本",p.versionId],["运行",p.runId],["模型",data.identity.profile],["结果摘要",data.identity.result_hash??'未提供'],["业务状态",businessLabel(data.validation?.business_status)]]}/></details>
  {data.validation?.known_fail&&<p className="risk-message">本次运行存在已知业务核验失败，记录供调查，不能作为合法执行方案。</p>}
  <ExceptionWorkControls scope={p} data={data} rows={rows} filters={filters} change={change} columns={table} onColumns={value=>change('columns',value)}/>
  <div className="decision-toolbar">
   <label>需求 / 物料<input aria-label="异常需求或物料筛选" value={filters.query} onChange={e=>change('query',e.target.value)} placeholder="需求行、订单引用、物料或地点"/></label>
   <label>交付异常<select aria-label="异常状态筛选" value={filters.status||'ALL'} onChange={e=>change('status',e.target.value)}><option value="ALL">全部需求</option><option value="ABNORMAL">存在异常</option><option value="SHORT">欠量（含未排定）</option><option value="UNPLANNED">完全未排定</option><option value="LATE">已排部分晚交</option><option value="CONSTRAINT">有需求级约束</option></select></label>
   <label>要求日期从<input aria-label="异常要求日期开始" type="date" value={filters.from} onChange={e=>change('from',e.target.value)}/></label>
   <label>至（含）<input aria-label="异常要求日期结束" type="date" value={filters.to} onChange={e=>change('to',e.target.value)}/></label>
   <label>约束对象<input aria-label="约束资源或物料筛选" value={filters.owner} onChange={e=>change('owner',e.target.value)} placeholder="原生 owner 中的资源或物料"/></label>
   <label>Constraint 类型<select aria-label="约束类型筛选" value={filters.type} onChange={e=>change('type',e.target.value)}><option value="">全部原生类型</option>{types.map(type=><option key={type}>{type}</option>)}</select></label>
   <label>需求级证据<select aria-label="约束证据状态筛选" value={filters.evidence} onChange={e=>change('evidence',e.target.value)}><option value="">全部证据状态</option>{Object.entries(evidenceLabels).map(([id,label])=><option key={id} value={id}>{id} · {label}</option>)}</select></label>
   <Button onClick={()=>setSearch(previous=>{const next=new URLSearchParams(previous);for(const field of Object.keys(filters).concat('row'))next.delete('exception_'+field);return next})}>清除筛选</Button>
  </div>
  <p className="decision-note">显示 {rows.length} / {data.demands.length} 条需求。日期按业务时区筛选；资源或物料仅查原生 Constraint.owner，不推导受影响客户。</p>
  <div className="decision-workspace"><div className="decision-main"><div className="decision-table-scroll" tabIndex={0} aria-label="异常需求表，可横向滚动"><DataTable rows={rows} columns={workColumns(table,search.get('exception_columns'))} rowId={d=>d.demand_id} selected={current?.demand_id} onSelect={d=>setSearch(previous=>{const next=new URLSearchParams(previous);next.set('exception_row',d.demand_id);next.set('exception_scope',JSON.stringify({planId:p.planId,versionId:p.versionId,runId:p.runId,target:p.target}));return next})} empty="当前筛选没有需求行。修改日期、对象或证据状态可继续调查。"/></div>
   <details className="decision-run-records"><summary>运行级 Problem · {evidenceLabels[data.evidence?.problems?.status]??data.evidence?.problems?.status}</summary><p>Problem 属于当前运行，未建立精确需求行归属。</p>{data.evidence?.problems?.reason&&<p>{data.evidence.problems.reason}</p>}{(data.problems??[]).map((r:Dict)=><div key={r.id}><strong>{r.record.name??r.record.description??r.id}</strong><Button onClick={()=>setSource(r)}>查看运行级来源</Button></div>)}{!(data.problems??[]).length&&<p>{data.evidence?.problems?.status==='EMPTY_CHECKED'?'已查询，运行级记录为空。':'当前没有可展示的运行级记录，不能解释为没有问题。'}</p>}</details>
   <details className="decision-run-records"><summary>未建立需求归属的 Constraint · {data.unlinked_constraints?.length??0} 条</summary><p>只按 record.demand 精确关联，未从 owner、同名物料或同刻任务推断。</p>{(data.unlinked_constraints??[]).map((r:Dict)=><div key={r.id}>{r.record.name} · {r.record.owner}<Button onClick={()=>setSource(r)}>查看未归属原件</Button></div>)}</details>
  </div><aside className="decision-inspector" aria-label="异常需求检查器"><h3>{current?current.order_ref??current.demand_id:'选择需求行'}</h3>{current?<>
   <p>{current.demand_id} · {current.item_id}</p><p>计划满足 {qty(current.planned_quantity)} {unit(current.uom)}，欠 {qty(current.unfulfilled_quantity)} {unit(current.uom)}。</p>{Number(current.unfulfilled_quantity)>0&&<p className="pending-note">未满足部分没有已验证完成日期。</p>}
   <Button className="primary" onClick={()=>p.onInvestigate(current.demand_id)}>调查此需求与时间链</Button>
   {p.onPriority&&<Button onClick={()=>p.onPriority!(current)}>模拟此需求优先级</Button>}
   <ExceptionReviews key={key+current.demand_id} scope={{planId:p.planId,versionId:p.versionId,baselineRunId:p.baselineRunId??p.runId}} demandId={current.demand_id} onReview={target=>{setSearch(previous=>{const next=rememberExceptionOrigin(previous,p,current.demand_id);next.set('analysis','review');next.set('review_focus',current.demand_id);if(target)next.set('review_drafts',target);return next})}}/>
   <h3>原生 Constraint · 精确需求行</h3><Badge>{evidenceLabels[current.constraint_status]??current.constraint_status}</Badge>
   {(current.constraints??[]).map((c:Dict)=><article className="decision-constraint" key={c.id}><h4>{c.record.name}</h4><p>{c.record.entity} · {c.record.owner}</p><p>{c.record.description}</p><small>需求 {c.record.demand}</small><small>{time(c.start_at??c.record.startdate)} → {time(c.end_at??c.record.enddate)}</small><Button onClick={()=>setSource(c)}>查看原生 Constraint 来源</Button></article>)}
   <p className="decision-note">约束可有多条，并列保留。未核验唯一根因或各条记录的欠量贡献。</p>
  </>:<p>选择表中的精确需求行，查看原生约束及来源，再进入既有订单调查。</p>}</aside></div>
  {(data.limits??[]).length>0&&<details className="decision-limits"><summary>当前调查范围</summary><ul>{data.limits.map((limit:any,i:number)=><li key={i}>{typeof limit==='string'?limit:JSON.stringify(limit)}</li>)}</ul></details>}
  {source&&<Dialog title="原生约束与记录来源" onClose={()=>setSource(null)}><Definition rows={[["运行",source.source?.run_id],["原件",source.source?.artifact],["SHA-256",source.source?.sha256],["记录位置",source.source?.pointer??'见原件记录']]}/><pre className="raw-json">{JSON.stringify(source.record,null,2)}</pre></Dialog>}
 </section>;
}
