import {useEffect,useState} from 'react';
import {useSearchParams} from 'react-router';
import {request} from '../api/client';
import type {Dict} from '../api/contracts';
import {Badge,Button,Definition,ErrorNote,Loading} from '../components/Controls';
import {qty,unit,runLabel,businessLabel} from './planModel';
import {formatSiteTime} from './timeRange';
import {evidenceLabels} from './orderInvestigationModel';
import {DeliverySnapshot} from '../scenarios/deliveryComparison';
import {scenarioPreview} from '../scenarios/scenarioPreview';
import {reviewComparable,reviewDeliveryRows,reviewInventoryRows,selectedReviewDrafts,verifyReview,type ReviewScope} from './scenarioReviewModel';
import {ProfessionalReviewSummary,type ReviewOpen} from './ProfessionalReviewSummary';
import {reviewDelta} from './professionalReviewModel';
import {ScenarioHandoff} from './ScenarioHandoff';
import {ReviewNotes} from './ReviewNotes';
import './decision-workbench.css';

type Props=ReviewScope&{onOpen:ReviewOpen};
export function ScenarioReview(p:Props){
 const[search,setSearch]=useSearchParams(),[response,setResponse]=useState<{key:string;data:Dict}|null>(null),[error,setError]=useState(''),[retry,setRetry]=useState(0);
 const chosen=search.get('review_drafts'),key=JSON.stringify([p.planId,p.versionId,p.baselineRunId,p.profile,p.endpoint,chosen]);
 useEffect(()=>{const c=new AbortController();setError('');const query=new URLSearchParams({data_version_id:p.versionId,baseline_run_id:p.baselineRunId});if(chosen!==null)query.set('draft_ids',chosen);
  request<Dict>('/v1/enterprise-plans/'+p.planId+'/scenario-review?'+query,{signal:c.signal}).then(v=>{if(!c.signal.aborted)setResponse({key,data:verifyReview(v,p)})}).catch(e=>{if(e.name!=='AbortError'&&!c.signal.aborted)setError(e.message)});return()=>c.abort();
 },[key,retry]);
 if(error)return <section className="decision-workbench"><h2>专业方案评审</h2><ErrorNote error={error}/><Button onClick={()=>setRetry(n=>n+1)}>重新读取同计划候选</Button><ReviewNotes key={key+'|'+(search.get('review_focus')??'')} initialObjectScope={search.get('review_focus')??''} scope={p} targets={chosen?.split(',').filter(Boolean)??[]}/></section>;
 const data=response?.key===key?response.data:null;if(!data)return <Loading/>;
 const selected=selectedReviewDrafts(chosen,data.available_drafts??[]),candidates:Dict[]=data.candidates.filter((c:Dict)=>c.target==='baseline'||selected.includes(c.target)),focus=search.get('review_focus')??'',rows=reviewDeliveryRows(candidates).filter(row=>!focus||row.demand_id===focus),inventory=reviewInventoryRows(candidates);
 const demandIds=[...new Set<string>(reviewDeliveryRows(candidates).map(row=>row.demand_id!))],time=(v:string)=>formatSiteTime(v,data.timezone);
 function toggle(id:string){const next=selected.includes(id)?selected.filter(x=>x!==id):[...selected,id].slice(0,3);setSearch(previous=>{const value=new URLSearchParams(previous);value.set('review_drafts',next.join(','));return value})}
 const unavailable=(candidate:Dict)=>!reviewComparable(candidate)?candidate.reason??'当前未满足比较门槛，请查看原方案与核验记录。':'此需求或单位不在该方案结果中';
 return <section className="decision-workbench scenario-review" aria-label="同计划多方案评审">
  <header className="decision-heading"><h2>专业方案评审</h2><Button icon="reload" onClick={()=>setRetry(n=>n+1)}>刷新候选状态</Button></header>
  <p className="decision-endpoint">{data.endpoint?.label??'当前计划需求履约'} · {data.timezone}</p>
  <details className="review-setup"><summary>候选、模拟条件与运行来源 · 最多 3 个</summary><details className="decision-identity"><summary>同一计划与原始基线 · {p.baselineRunId}</summary><Definition rows={[["计划",p.planId],["数据版本",p.versionId],["基线运行",p.baselineRunId],["认证模型",data.identity.profile],["业务终点",data.endpoint?.kind==='CUSTOMER_FG'?'客户成品需求':data.endpoint?.kind==='STAGE_OUTPUT'?'阶段产出需求':data.endpoint?.kind==='ORDER_DELIVERY'?'普通订单需求（规划未执行）':'业务终点未提供']]}/></details>
  <fieldset className="review-candidates"><legend>选择独立候选（最多 3 个，基线固定保留）</legend>{(data.available_drafts??[]).map((draft:Dict)=><label key={draft.id}><input type="checkbox" aria-label={'评审候选 '+draft.id} checked={selected.includes(draft.id)} disabled={!selected.includes(draft.id)&&selected.length>=3} onChange={()=>toggle(draft.id)}/><span>{draft.intent??draft.id}<small>{draft.id} · {runLabel(draft.calculation_state)}</small></span></label>)}{!data.available_drafts?.length&&<p>当前计划尚无已保存候选。可在原资源占用视图创建独立模拟草案，运行后回到此处评审。</p>}</fieldset>
  <p className="decision-note">候选共用本计划的原始基线，彼此不叠加。仅有效且口径相同的真实运行显示指标；当前评审不自动选择最优，也不下达采购或生产。</p>
  <div className="decision-table-scroll review-matrix" tabIndex={0} aria-label="多候选状态与条件，可横向滚动"><table><thead><tr><th scope="col">状态与条件</th>{candidates.map(c=><th scope="col" key={c.target}>{c.label??(c.target==='baseline'?'原始基线':c.target)}<small>{c.run_id??'尚未运行'}</small></th>)}</tr></thead><tbody>
   <tr><th scope="row">计算 / 业务状态</th>{candidates.map(c=><td key={c.target}><Badge kind={reviewComparable(c)?'good':c.calculation_state==='FAILED'?'bad':'risk'}>{runLabel(c.calculation_state)}</Badge><small>{businessLabel(c.business_status)}</small>{!reviewComparable(c)&&<p className="decision-note">{unavailable(c)}</p>}</td>)}</tr>
   <tr><th scope="row">独立模拟条件</th>{candidates.map(c=><td key={c.target}>{c.target==='baseline'?'受保护的原始基线':c.changes?.intent==='资源不可用模拟'?<><strong>{c.changes.resource_id??c.changes.reference}</strong><small>不可用 {time(c.changes.start_at)} → {time(c.changes.end_at)}</small><small>{data.timezone} · 扣除 {qty(Number(c.changes.removed_seconds)/60)} 分钟</small><small>原日历 {c.changes.before_calendar_id}</small></>:c.changes?<Definition rows={scenarioPreview(c.changes)}/>:<span>条件未取得，见原草案</span>}</td>)}</tr>
   <tr><th scope="row">Constraint 采集</th>{candidates.map(c=><td key={c.target}>{reviewComparable(c)?<>{evidenceLabels[c.constraints?.status]??c.constraints?.status??'NOT_COLLECTED'}{['PRESENT','EMPTY_CHECKED'].includes(c.constraints?.status)&&<small>{c.constraints.count} 条原生记录</small>}</>:<span>无有效指标</span>}</td>)}</tr>
   <tr><th scope="row">核验与来源</th>{candidates.map(c=><td key={c.target}>{c.validation&&Object.values(c.validation).some(v=>v!=null)?<details><summary>资源 / 材料 / 排程核验与来源</summary><pre className="raw-json">{JSON.stringify({identity:c.professional?.identity,validation:c.validation,evidence_files:c.professional?.evidence_files},null,2)}</pre></details>:<span>未通过比较门槛</span>}<Button onClick={()=>p.onOpen(c.target,'schedule')}>打开此方案与操作</Button>{reviewComparable(c)&&<Button onClick={()=>p.onOpen(c.target,'exceptions')}>查看此运行异常</Button>}</td>)}</tr>
  </tbody></table></div>
  </details>
  <ReviewNotes key={key+'|'+(search.get('review_focus')??'')} initialObjectScope={search.get('review_focus')??''} scope={p} targets={candidates.filter(c=>c.target!=='baseline').map(c=>c.target)}/>
  <ScenarioHandoff scope={p} candidates={candidates} timezone={data.timezone} search={search} onSearch={(field,value)=>setSearch(previous=>{const next=new URLSearchParams(previous);value?next.set(field,value):next.delete(field);return next})} onOpen={p.onOpen}/>
  <ProfessionalReviewSummary candidates={candidates} timezone={data.timezone} onOpen={p.onOpen} onDemand={id=>{setSearch(previous=>{const next=new URLSearchParams(previous);id?next.set('review_focus',id):next.delete('review_focus');return next});requestAnimationFrame(()=>{const el=document.getElementById('review-demand-lines') as HTMLDetailsElement|null;if(el){el.open=true;el.scrollIntoView({block:'start'})}})}}/>
  <details className="review-professional-section" id="review-demand-lines"><summary>精确需求行与原对象调查</summary>
  <div className="decision-heading review-section-heading"><h3>精确需求行履约</h3><label>聚焦需求<select aria-label="评审聚焦需求行" value={focus} onChange={e=>setSearch(previous=>{const next=new URLSearchParams(previous);e.target.value?next.set('review_focus',e.target.value):next.delete('review_focus');return next})}><option value="">全部需求行</option>{demandIds.map(id=><option key={id}>{id}</option>)}</select></label></div>
  {focus&&!demandIds.includes(focus)&&<p className="pending-note">所选需求行不在当前可比较结果中；可切回全部需求或选择其他候选。</p>}
  <div className="decision-table-scroll review-matrix" tabIndex={0} aria-label="多候选需求履约比较，可横向滚动"><table><thead><tr><th scope="col">需求 / 单位</th>{candidates.map(c=><th key={c.target} scope="col">{c.label??c.target}</th>)}</tr></thead><tbody>{rows.map(row=><tr key={row.id}><th scope="row">{row.demand_id}<small>{row.item_id} · {unit(row.uom)}</small></th>{candidates.map(c=><td key={c.target}>{row.values[c.target]?<><DeliverySnapshot value={row.values[c.target]} uom={row.uom} timezone={data.timezone}/><small>要求 {time(row.values[c.target].due_at)}</small><Button onClick={()=>p.onOpen(c.target,'investigate',row.demand_id)}>调查此需求</Button></>:<span className="decision-note">{unavailable(c)}</span>}</td>)}</tr>)}</tbody></table>{!rows.length&&<p className="empty-records">当前选择尚无可比较的需求履约指标。</p>}</div>
  </details><details className="review-professional-section"><summary>库存：同物料 / 地点 / 单位的期末净可用</summary><h3 className="review-section-heading">期末净可用库存</h3><div className="decision-table-scroll review-matrix" tabIndex={0} aria-label="多候选库存比较，可横向滚动"><table><thead><tr><th>物料 / 地点 / 单位</th>{candidates.map(c=><th key={c.target}>{c.label??c.target}</th>)}</tr></thead><tbody>{inventory.map(row=><tr key={row.id}><th>{row.item_id}<small>{row.location_id} · {unit(row.uom)}</small></th>{candidates.map(c=><td key={c.target}>{row.values[c.target]?<>{qty(row.values[c.target].ending_net_available)+' '+unit(row.uom)}{c.target!=='baseline'&&<small>较基线 {reviewDelta(row.values.baseline?.ending_net_available,row.values[c.target].ending_net_available)==null?'不可比':qty(reviewDelta(row.values.baseline?.ending_net_available,row.values[c.target].ending_net_available))+' '+unit(row.uom)}</small>}<Button onClick={()=>p.onOpen(c.target,'inventory',undefined,{item_id:row.item_id,location_id:row.location_id})}>下钻原生库存事件</Button></>:'未取得同口径库存指标'}</td>)}</tr>)}</tbody></table>{!inventory.length&&<p className="empty-records">当前没有同口径可比较库存；未采集的记录不显示为零。</p>}</div>
  </details>{(data.limits??[]).length>0&&<details className="decision-limits"><summary>评审适用范围</summary><ul>{data.limits.map((limit:any,i:number)=><li key={i}>{typeof limit==='string'?limit:JSON.stringify(limit)}</li>)}</ul></details>}
 </section>;
}
