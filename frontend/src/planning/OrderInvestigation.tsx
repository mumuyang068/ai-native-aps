import {useEffect,useRef,useState} from 'react';
import {Link,useSearchParams} from 'react-router';
import {request} from '../api/client';
import type {Dict,InputSnapshot,NativeEvidence,PlanView} from '../api/contracts';
import {Badge,Button,Definition,Dialog,ErrorNote,Loading,Tabs} from '../components/Controls';
import {NativeSource} from './OrderDetails';
import {qty,unit,businessLabel} from './planModel';
import {formatSiteTime} from './timeRange';
import {deliveryStatusLabel} from './orderModel';
import {DeliverySnapshot,DeliveryDifference} from '../scenarios/deliveryComparison';
import {buildOrderSupplyChain} from './supplyChainModel';
import {currentDemandMetadata,analysisSupport,evidenceLabels,evidenceStatus,investigationKey,verifyInvestigation,knownBusinessFail} from './orderInvestigationModel';
import './order-investigation.css';
import {analysisContextSearch} from './analysisModel';

type Props={planId:string;versionId:string;target:string;orderId:string;view:PlanView;base:PlanView;snapshot:InputSnapshot;orders:{id:string;label:string}[];scenarios:{id:string;label:string}[];onOrder:(id:string)=>void;onTarget:(id:string)=>void;onSupply:()=>void;onLocate:(id:string)=>void;onAnalysis:(mode:string,item?:string,location?:string)=>void};
const constraintLabels:Record<string,string>={overload:'资源容量不足','capacity overload':'资源容量不足','await supply':'等待已确认供应'};
export function OrderInvestigation(p:Props){
 const[search]=useSearchParams();
 const[tab,setTab]=useState('delivery'),[loaded,setLoaded]=useState<{key:string;data:Dict}|null>(null),[failure,setFailure]=useState<{key:string;code:string;message:string}|null>(null),[source,setSource]=useState<NativeEvidence|null>(null),[retry,setRetry]=useState(0);
 const cache=useRef(new Map<string,Dict>()),scope={planId:p.planId,versionId:p.versionId,target:p.target,orderId:p.orderId,view:p.view},key=investigationKey(scope);
 useEffect(()=>{
  const c=new AbortController();let active=true;setFailure(null);setSource(null);
  const cached=cache.current.get(key);
  if(cached){setLoaded({key,data:cached});return()=>{active=false;c.abort()};}
  const query=new URLSearchParams({data_version_id:p.versionId,run_id:p.view.run?.solve_run_id??'',order_line_id:p.orderId,target:p.target});
  request<Dict>('/v1/enterprise-plans/'+p.planId+'/order-investigation?'+query,{signal:c.signal}).then(v=>{
   if(!active)return;const data=verifyInvestigation(v,scope);cache.current.set(key,data);
   if(cache.current.size>12)cache.current.delete(cache.current.keys().next().value!);setLoaded({key,data});
  }).catch(e=>{if(active&&e.name!=='AbortError'){cache.current.delete(key);setLoaded(null);setFailure({key,code:e.code??'INVALID_EVIDENCE',message:e.message})}});
  return()=>{active=false;c.abort()};
 },[key,retry]);
 const error=failure?.key===key?failure:null,data=loaded?.key===key?loaded.data:null;
 const tz=p.view.context?.timezone??'Asia/Shanghai',time=(s:string|null|undefined)=>formatSiteTime(s,tz),input=p.snapshot.plan_input,support=analysisSupport(input.rules?.manufacturing_profile??'');
 const identity=<details className="investigation-identity"><summary>{p.orderId} · {p.view.run?.solve_run_id}</summary><Definition rows={[["计划",p.planId],["数据版本",p.versionId],["方案",p.target],["运行",p.view.run?.solve_run_id??'未运行'],["结果摘要",p.view.result?.result_hash??'未取得'],["证据投影",p.view.order_evidence?.projection_hash??'未取得']]}/></details>;
 if(error)return <section className="order-investigation" aria-label="订单调查"><h2>订单调查</h2>{identity}<Badge kind="bad">{error.code==='INVALID_SCHEDULE'||knownBusinessFail(p.view)?'FAIL · 存在业务违规':evidenceLabels[evidenceStatus(error.code)]}</Badge><ErrorNote error={error.message}/><p>未使用其他运行或旧缓存补齐当前记录。</p><Button onClick={()=>{cache.current.delete(key);setRetry(v=>v+1)}}>重新读取订单证据</Button></section>;
 if(!data)return <section className="order-investigation" aria-label="订单调查">{identity}<Loading/></section>;
 const order=data.order,d=order.delivery,short=Number(d.unfulfilled_quantity)>0,base=p.base.result?.deliveries.find(x=>x.demand_id===p.orderId),metadata=currentDemandMetadata(input,scope),original=metadata?.line,newInquiry=metadata?.origin==='RESEARCH_INQUIRY',newAssumption=metadata?.origin==='RESEARCH_ASSUMPTION'||newInquiry,originalDue=newAssumption?'原版本无此需求':time(base?.due_at);
 const chain=buildOrderSupplyChain({input,view:p.view,planId:p.planId,versionId:p.versionId,orderId:p.orderId}),difference=p.view.diff?.deliveries?.find((x:Dict)=>x.demand_id===p.orderId);
 const validation=p.view.interpretation??{},checks=Object.entries(validation).filter(([k,v])=>k.endsWith('_validation')&&v&&typeof v==='object') as [string,Dict][];
 const groups=[['operations','制造工序'],['manufacturing','制造工单 / 建议'],['purchases','订单原生关联采购'],['deliveries','需求交付记录']] as const;
 const recordUnit=(e:NativeEvidence)=>[...(p.view.result?.manufacture_suggestions??[]),...(p.view.result?.existing_manufacturing_orders??[])].find(m=>m.id===e.task?.manufacture_id||m.id===e.task?.id)?.uom??e.item_uom??'';
 const evidence=(entry:NativeEvidence)=> <Button className="quiet" onClick={()=>setSource(entry)}>查看来源 {entry.engine_ref??entry.id}</Button>;
 return <section className="order-investigation" aria-label="订单调查">
  <header><h2>订单调查</h2><Badge kind={short||d.status==='LATE'?'risk':'good'}>{deliveryStatusLabel(d.status)}</Badge></header>
  <label>调查需求行<select aria-label="调查需求行" value={p.orderId} onChange={e=>p.onOrder(e.target.value)}>{p.orders.map(o=>{
   const current=p.view.context?.order_summary.orders.find(line=>line.id===o.id),reference=current?current.order_ref:o.label;
   return <option key={o.id} value={o.id}>{typeof reference==='string'&&reference&&reference!==o.id?reference+' · '+o.id:o.id}</option>;
  })}</select></label>
  <label>调查方案<select aria-label="调查方案" value={p.target} onChange={e=>p.onTarget(e.target.value)}>{p.scenarios.map(s=><option key={s.id} value={s.id}>{s.label}</option>)}</select></label>
  {identity}
  <p className="detail-note">{p.view.context?.wip?.stage_endpoint?.label??'计划需求满足，尚非实际发货或客户收货。'}</p>
  <Tabs label="订单调查内容" tabs={[["delivery","交付"],["supply","供应"],["constraints","约束"],["changes","方案"]]} value={tab} onChange={setTab}/>
  <div role="tabpanel" aria-label={{delivery:'交付摘要',supply:'供应与生产',constraints:'约束与证据',changes:'方案差异'}[tab]}>
  {tab==='delivery'&&<>
   <Definition rows={[[newInquiry?"研究询价引用":newAssumption?"研究假设引用":"原始订单引用",newInquiry?original?.id:order.order_ref],["产品",original?.item_id??'未提供'],["原交期",originalDue],...(newAssumption?[[newInquiry?"请求交期（非正式订单）":"假设交期（研究来源）",time(original?.due_at)]] as [string,string][]:[]),["本方案交期",time(d.due_at)],["需求数量",qty(d.requested_quantity)+' '+unit(d.uom)],["计划满足",qty(d.planned_quantity)+' '+unit(d.uom)],["尚未满足",qty(d.unfulfilled_quantity)+' '+unit(d.uom)],["已排部分日期",Number(d.planned_quantity)>0?time(d.planned_delivery_at):'没有已排定部分'],["本方案整体业务状态",businessLabel(validation.business_status)]]}/>
   {short&&<p className="pending-note">尚欠 {qty(d.unfulfilled_quantity)} {unit(d.uom)}，该部分没有确定完成日期，不能换算为已知晚交时长。</p>}
   <p className="detail-note">原生关联和约束是本次计算证据，尚未证明完整或唯一根因。</p>
   <Button className="wide" onClick={()=>setTab('supply')}>追踪供应与生产</Button><Button className="wide" onClick={()=>setTab('constraints')}>调查约束与核验结果</Button>
   <h3>需求来源</h3>{order.demand_records.map((e:NativeEvidence)=><div key={e.id}>{evidence(e)}</div>)}
  </>}
  {tab==='supply'&&<>
   <h3>订单级供需时间链</h3><p>{chain.status==='UNSUPPORTED'?'UNSUPPORTED · 该profile尚未认证订单级时间链。':chain.status==='PRESENT_WITH_LIMITS'?'仅使用当前运行的原生pegging和上游引用。':'当前订单的供需关联不足，未补画路径。'}</p>
   <Button className="wide" onClick={p.onSupply}>打开原供需与生产时间链</Button>
   <p className="detail-note">下列任务有精确需求行原生引用；任务全量、pegging数值均不等于已认证的物料分配量。</p>
   {groups.map(([group,label])=><section key={group}><h3>{label} · {order[group]?.length??0}</h3>{(order[group]??[]).map((e:NativeEvidence)=><div className="investigation-record" key={e.id}><strong>{e.task?.operation_id??e.record.operation??e.engine_ref}</strong><span>任务全量 {qty(e.task?.quantity??e.record.quantity)} {unit(recordUnit(e))}</span><small>{time(e.task?.start_at??e.times?.startdate)} → {time(e.task?.end_at??e.times?.enddate)}</small>{group==='operations'&&e.task?.id&&<Button onClick={()=>p.onLocate(e.task!.id)}>定位原排程工序</Button>}{group==='operations'&&e.task?.id&&support.resources&&<Button onClick={()=>{p.onLocate(e.task!.id);p.onAnalysis('resources')}}>查看工序资源占用</Button>}{evidence(e)}</div>)}{!order[group]?.length&&<p className="detail-note">本运行未返回该类订单关联；不从同名物料或时间推断。</p>}</section>)}
   <h3>本计划确认采购 · 未证明全部归属此需求</h3>{(data.confirmed_supply_windows??[]).map((po:Dict)=><div className="investigation-record" key={po.id}><strong>{po.id} · {qty(po.quantity)} {unit(input.existing_orders.find((x:Dict)=>x.id===po.id)?.uom??'')}</strong><span>物理到货 {time(po.physical_receipt_at)}</span><span>QC后可用 {time(po.available_at)}</span><small>原单状态 {po.source_status}；当前订单原生采购关联 {order.purchases.some((e:NativeEvidence)=>e.engine_ref===po.id)?'存在，见上方来源':'未取得'}</small></div>)}
   <h3>运行级专业分析</h3><p className="detail-note">库存余额与资源负荷属于当前运行，不能据此分配给本订单。</p>
   <Button disabled={!support.inventory} onClick={()=>p.onAnalysis('inventory')}>查看本运行库存时间账</Button><Button disabled={!support.resources} onClick={()=>p.onAnalysis('resources')}>查看本运行资源占用</Button>
   {(!support.inventory||!support.resources)&&<p className="detail-note">未认证的专业分析保持不支持；原生物料和资源记录仍可逐条查看来源。</p>}
   <details><summary>订单明确关联的物料 / 资源记录</summary>{[...order.materials,...order.resources].map((e:NativeEvidence)=><div key={e.id}>{evidence(e)}{support.inventory&&e.record.item&&<Button onClick={()=>p.onAnalysis('inventory',e.record.item,e.record.location)}>进入该物料时间账</Button>}</div>)}</details>
  </>}
  {tab==='constraints'&&<>
   <h3>原生Constraint · 精确需求行关联</h3><Badge>{order.constraints.length?'PRESENT · '+order.constraints.length+' 条':'EMPTY_CHECKED · 本需求行记录为空'}</Badge>
   <p className="detail-note">全运行Constraint：{evidenceLabels[evidenceStatus(data.native_tables.constraint?.status)]}，{data.native_tables.constraint?.row_count??'未知'} 条。查询为空不代表没有约束。</p>
   {order.constraints.map((e:NativeEvidence)=><article className="investigation-record" key={e.id}><strong>{constraintLabels[e.record.name]??e.record.name} · {e.record.entity}</strong><small>原生类型 {e.record.name}</small><span>记录 {e.record.id} · 需求行 {e.record.demand}</span><span>关联对象 {e.record.owner}</span><p>{e.record.description}</p><small>{time(e.times?.startdate)} → {time(e.times?.enddate)}</small>{evidence(e)}</article>)}
   <h3>原生Problem · 运行级</h3><Badge>{evidenceStatus(data.native_tables.problem?.status)}</Badge><p>{data.native_tables.problem?.row_count??'未知'} 条。{data.problem_order_link.reason}</p>
   <details><summary>Problem运行级原件与来源</summary><pre className="raw-json">{JSON.stringify(data.native_tables.problem,null,2)}</pre></details>
   <h3>业务核验 · 独立于原生约束</h3>{checks.map(([name,value])=><details key={name}><summary>{name} · {value.status??'NOT_CHECKED'}</summary><pre className="raw-json">{JSON.stringify(value,null,2)}</pre></details>)}
   <Definition rows={[["排程核验",validation.schedule_check??'NOT_CHECKED'],["本方案整体业务状态",businessLabel(validation.business_status)]]}/>
   <h3>计算观察</h3><p>本需求行计划满足 {qty(d.planned_quantity)} {unit(d.uom)}，欠 {qty(d.unfulfilled_quantity)} {unit(d.uom)}。采购时点及制造变化可在“方案”核对。</p><p className="detail-note">原生约束并未证明各因素独立贡献、唯一根因或缺量的恢复日期。</p>
  </>}
  {tab==='changes'&&<>
   <h3>同一需求行交付</h3><h4>原始基线</h4><DeliverySnapshot value={base} uom={d.uom} timezone={tz}/><h4>当前方案</h4><DeliverySnapshot value={d} uom={d.uom} timezone={tz}/>
   {difference&&<DeliveryDifference row={difference} uom={d.uom}/>}<p>原交期 {originalDue}；本方案 {time(d.due_at)}。</p>
   {p.target==='baseline'?<p className="detail-note">当前为原始基线；选择已保存草案可比较。本页不自动创建或运行方案。</p>:<>
    <h3>本计划采购时点变化</h3>{(p.view.diff?.confirmed_supplies??[]).map((x:Dict)=><div className="investigation-record" key={x.reference}><strong>{x.reference}</strong><span>物理到货 {time(x.base.physical_receipt_at)} → {time(x.candidate.physical_receipt_at)}</span><span>QC后可用 {time(x.base.available_at)} → {time(x.candidate.available_at)}</span></div>)}
    <h3>本计划制造变化 · 非订单分配量</h3>{(p.view.diff?.operation_groups??[]).map((x:Dict,i:number)=><p key={i}>{x.operation_id} · {qty(x.base_quantity)} → {qty(x.candidate_quantity)} {unit(x.uom)}</p>)}
    <p>本需求行的原生采购关联：基线 {p.base.order_evidence?.orders.find(o=>o.demand_id===p.orderId)?.purchases.length??'未知'} 项 → 当前 {order.purchases.length} 项。缺失关联不从另一方案回填。</p>
    <Link className="button-link" to={'/plans/'+p.planId+'/compare/'+p.target+'?'+analysisContextSearch(search,p.target).toString()}>查看原方案比较</Link>
   </>}

  </>}
  </div>
  {source&&<Dialog title="原生记录来源" onClose={()=>setSource(null)}><NativeSource entry={source}/></Dialog>}
 </section>;
}
