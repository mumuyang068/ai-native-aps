import {useEffect,useRef,useState} from 'react';
import type {Dict} from '../api/contracts';
import {request} from '../api/client';
import {useActionGuard} from '../app/useActionGuard';
import {Badge,Button,Definition,ErrorNote,Loading,Select,Tabs} from '../components/Controls';
import {DataTable,type TableColumn} from '../components/DataTable';
import {formatSiteTime} from './timeRange';
import {explicitNettingPolicy,inquiryStatus,nettingStatus,professionalIdentity,siteTimestamp,verifyProfessionalReport,type ProfessionalScope} from './professionalPolicyModel';
import './professional-policy.css';
import {JointMaterialInvestigation} from './JointMaterialInvestigation';
import {JointSupplyComparison} from './JointSupplyComparison';
import {IntelligenceWorkbench} from './IntelligenceWorkbench';

type Mode='forecast-netting'|'order-inquiry'|'stock-replenishment';
const titles:Record<Mode,string>={'forecast-netting':'预测抵扣与联合规划','order-inquiry':'新订单评估','stock-replenishment':'库存与补货策略'};
type Props={scope:ProfessionalScope;mode:Mode;runState:string;onTarget:(id:string)=>void;onRefresh:()=>void;onCompare:()=>void;onReview:()=>void;onInventory:(item:string,location:string)=>void;onDemand:()=>void;materialItem?:string;materialLocation?:string;onMaterialSelect?:(item:string,location:string)=>void;onOperation?:(id:string)=>void;onInvestigateDemand?:(id:string)=>void};
const amount=(v:unknown)=>v==null?'待核对':String(v);

export function ProfessionalPolicy(p:Props){
 const [layer,setLayer]=useState('research');
 const independent=p.scope.target==='baseline'&&p.mode!=='order-inquiry';
 return <>{independent&&<Tabs label="研究与联合规划" tabs={[["research","独立研究"],["planning","联合规划"]]} value={layer} onChange={setLayer}/>}{independent&&layer==='planning'&&p.runState!=='SUCCEEDED'?<section className="professional-policy-workspace"><h2>联合规划</h2><p>当前基线尚无成功原生结果；历史资料、统计预测与库存策略仍可在独立研究中使用。</p><Button onClick={()=>setLayer('research')}>返回独立研究</Button></section>:<ProfessionalPolicyBody {...p} researchOnly={independent&&layer==='research'}/>}</>;
}

function ProfessionalPolicyBody(p:Props&{researchOnly:boolean}){
 const key=JSON.stringify([p.scope,p.mode,p.runState]),[loaded,setLoaded]=useState<{key:string;value:Dict}|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[revision,setRevision]=useState(0);
 const[version,setVersion]=useState(''),[source,setSource]=useState(''),[reason,setReason]=useState(''),[ack,setAck]=useState(false);
 const[net,setNet]=useState(p.mode==='forecast-netting'),[inquire,setInquire]=useState(p.mode==='order-inquiry'),[stock,setStock]=useState(p.mode==='stock-replenishment');
 const[forecastIds,setForecastIds]=useState<string[]>([]),[dues,setDues]=useState<Record<string,string>>({});
 const[customer,setCustomer]=useState(''),[item,setItem]=useState(''),[location,setLocation]=useState(''),[unit,setUnit]=useState(''),[quantity,setQuantity]=useState(''),[due,setDue]=useState(''),[delivery,setDelivery]=useState('ALL_OR_NOTHING');
 const[usePurchase,setUsePurchase]=useState(false),[useRoute,setUseRoute]=useState(false),[purchaseRef,setPurchaseRef]=useState(''),[purchaseDate,setPurchaseDate]=useState(''),[laneId,setLaneId]=useState(''),[leadHours,setLeadHours]=useState('');
 const[stockRows,setStockRows]=useState<Dict[]>([]),[preview,setPreview]=useState<{key:string;value:Dict}|null>(null);
 const[nextDue,setNextDue]=useState(''),[nextPreview,setNextPreview]=useState<{key:string;value:Dict}|null>(null),[nextAck,setNextAck]=useState(false);
 const receipt=useRef<{key:string;id:string}|null>(null),inquiryId=useRef('INQUIRY-'+crypto.randomUUID().replaceAll('-','').slice(0,16));
 const actionKey=JSON.stringify([key,version,source,reason,net,inquire,stock,forecastIds,dues,customer,item,location,unit,quantity,due,delivery,stockRows,nextDue,usePurchase,useRoute,purchaseRef,purchaseDate,laneId,leadHours]),capture=useActionGuard(actionKey);
 const data=loaded?.key===key?loaded.value:null,pv=preview?.key===actionKey?preview.value:null,writable=p.scope.target==='baseline';
 const sourceOnly=p.researchOnly;
 const prefix='/v1/enterprise-plans/'+encodeURIComponent(p.scope.planId)+'/professional-policy';
 useEffect(()=>{
  if(sourceOnly)return;
  setLoaded(null);setError('');setBusy(false);setPreview(null);const c=new AbortController();
  request<Dict>(prefix+'?'+new URLSearchParams(professionalIdentity(p.scope)),{signal:c.signal}).then(value=>{
   if(c.signal.aborted)return;const report=verifyProfessionalReport(value,p.scope);setLoaded({key,value:report});
   setForecastIds(report.forecasts.map((f:Dict)=>f.id));setDues(Object.fromEntries(report.forecasts.map((f:Dict)=>[f.id,formatSiteTime(new Date(Date.parse(f.bucket_end)-6*3600000).toISOString(),report.timezone).replace(' ','T')])));
  }).catch(e=>{if(!c.signal.aborted)setError(e.message)});return()=>c.abort();
 },[key,revision,sourceOnly]);
 function resetPreview(){setPreview(null);setAck(false)}
 function parameters(){
  if(!data)throw Error('请先读取当前版本。');
  const s=source.trim();if(!s||!reason.trim())throw Error('请填写政策依据与试算理由。');
  if((net||stock)&&!version.trim())throw Error('请给本次研究政策指定版本。');
  const n=net?explicitNettingPolicy(data,version.trim(),s,forecastIds,dues):null;
  const q=inquire?{id:inquiryId.current,customer_id:customer,item_id:item,location_id:location,uom:unit,quantity,due_at:siteTimestamp(due,data.timezone),delivery_rule:delivery,coverage:'INCREMENTAL_OUTSIDE_GROSS',protect_existing:true,request_source:s}:null;
  const r=stock?{policy_version:version.trim(),approval_status:'RESEARCH_POLICY',source:s,rows:stockRows.map(row=>({...row,effective_from:data.horizon.start_at,effective_to:data.horizon.end_at}))}:null;
  return {netting:n,inquiry:q,replenishment:r,reason:reason.trim(),...((usePurchase||useRoute)?{supply:{purchase:usePurchase?{reference:purchaseRef,new_receipt_at:siteTimestamp(purchaseDate,data.timezone)}:null,distribution:useRoute?{lane_id:laneId,new_lead_time_seconds:Number(leadHours)*3600}:null}}:{})};
 }
 async function act(kind:'preview'|'create'|'run'){
  if(!data||busy)return;const owns=capture();setBusy(true);setError('');
  try{
   if(kind==='run'){
    if(!data.draft||data.draft.run_id)throw Error('该候选已提交或不是可运行草案。');
    await request('/v1/drafts/'+data.draft.id+'/run',{method:'POST',scope:{planId:p.scope.planId,target:p.scope.target},body:{}});
    if(owns()){p.onRefresh();setRevision(v=>v+1)}
   }else{
    const body={...professionalIdentity(p.scope),...parameters()};
    if(kind==='preview'){
     const value=await request<Dict>(prefix+'-preview',{method:'POST',body});
     if(owns()){verifyProfessionalReport(value,p.scope);setPreview({key:actionKey,value});setAck(false)}
    }else{
     if(!pv||!pv.worker_eligible||!ack)throw Error('请先核对当前预览，并确认只用于隔离研究。');
     if(receipt.current?.key!==actionKey)receipt.current={key:actionKey,id:crypto.randomUUID().replaceAll('-','')};
     const value=await request<Dict>(prefix+'-drafts',{method:'POST',body:{...body,fingerprint:pv.fingerprint,acknowledge_limits:true,request_id:receipt.current.id}});
     if(!owns())return;
     if(value.plan_id!==p.scope.planId||value.data_version_id!==p.scope.versionId||value.baseline_run_id!==p.scope.baselineRunId||value.draft?.changes?.confirmation?.fingerprint!==pv.fingerprint)throw Error('草案确认不属于当前预览。');
     p.onTarget(value.draft.id);
    }
   }
  }catch(e){if(owns())setError(e instanceof Error?e.message:'操作未完成，请核对当前记录。')}
  finally{if(owns())setBusy(false)}
 }
 async function nextProbe(create:boolean){
  if(!data||busy||!reason.trim())return;const owns=capture();setBusy(true);setError('');
  try{
   const body={...professionalIdentity(p.scope),due_at:siteTimestamp(nextDue,data.timezone),reason:reason.trim()},url='/v1/enterprise-plans/'+encodeURIComponent(p.scope.planId)+'/professional-inquiry-next-';
   if(!create){
    const value=await request<Dict>(url+'preview',{method:'POST',body});
    if(!owns())return;
    verifyProfessionalReport({...value,identity:value.origin_identity},p.scope);
    verifyProfessionalReport(value.preview,{...p.scope,target:'baseline',runId:p.scope.baselineRunId});
    setNextPreview({key:actionKey,value});setNextAck(false);
   }else{
    if(nextPreview?.key!==actionKey||!nextAck)throw Error('请先核对更晚交期的预览。');
    if(receipt.current?.key!=='next:'+actionKey)receipt.current={key:'next:'+actionKey,id:crypto.randomUUID().replaceAll('-','')};
    const value=await request<Dict>(url+'drafts',{method:'POST',body:{...body,fingerprint:nextPreview.value.preview.fingerprint,acknowledge_limits:true,request_id:receipt.current.id}});
    if(!owns())return;
    if(value.plan_id!==p.scope.planId||value.data_version_id!==p.scope.versionId||value.baseline_run_id!==p.scope.baselineRunId||value.draft?.changes?.confirmation?.fingerprint!==nextPreview.value.preview.fingerprint)throw Error('新交期候选身份或预览不一致。');
    p.onTarget(value.draft.id);
   }
  }catch(e){if(owns())setError(e instanceof Error?e.message:'更晚交期未能准备。')}
  finally{if(owns())setBusy(false)}
 }
 const time=(v:string)=>formatSiteTime(v,data?.timezone??'Asia/Shanghai'),changes=writable?pv?.changes:data?.changes;
 const netRows=changes?.netting?.rows??data?.forecasts.map((f:Dict)=>({...f,forecast_id:f.id,gross_quantity:String(f.gross_quantity),net_quantity:null,consumed_quantity:null,status:'POLICY_PENDING'}))??[];
 const columns:TableColumn<Dict>[]=[{id:'object',title:'预测对象',value:r=><>{r.item_id}<small>{r.location_id} · {r.customer_id} · {r.uom}</small></>},{id:'period',title:'来源期间（半开）',value:r=><>{time(r.bucket_start)}<small>至 {time(r.bucket_end)}</small></>},...(['gross_quantity','firm_quantity','consumed_quantity','net_quantity'] as const).map((id,index)=>({id,title:['Gross','匹配 Firm','Consumed','Net'][index],numeric:true,value:(r:Dict)=>amount(r[id])})),{id:'status',title:'处理结果',value:r=>nettingStatus[r.status]??'抵扣政策待确认'}];
 const result=data?.outcome,inquiryResult=result?.inquiry,protection=result?.original_commitments;
 function selectProduct(value:string){setItem(value);const match=data?.firm_demands.find((d:Dict)=>d.item_id===value&&(!customer||d.customer_id===customer));setLocation(match?.location_id??'');setUnit(match?.uom??'');resetPreview()}
 const stockOptions=data?data.source_terms.filter((r:Dict,i:number,all:Dict[])=>all.findIndex(s=>s.item_id===r.item_id&&s.location_id===r.location_id)===i).map((s:Dict)=>({value:s.item_id+'@'+s.location_id,label:s.item_id+' · '+s.location_id+' · '+data.stock_facts.find((r:Dict)=>r.item_id===s.item_id&&r.location_id===s.location_id)?.uom})):[];
 if(sourceOnly)return <IntelligenceWorkbench {...p} sourceOnly/>;
 if(data?.intelligence_available)return <IntelligenceWorkbench {...p}/>;
 return <section className="professional-policy-workspace" aria-label={titles[p.mode]}>
  <header className="professional-policy-heading"><div><h2>{titles[p.mode]}</h2><span>{writable?'当前企业版本 · 准备隔离研究':'独立研究候选'} · {data?.timezone??''}</span></div><div className="professional-policy-actions"><Button disabled={busy} icon="reload" onClick={()=>setRevision(v=>v+1)}>刷新</Button>{!writable&&<><Button onClick={()=>p.onTarget('baseline')}>返回原始基线</Button><Button onClick={p.onCompare}>比较原计划</Button><Button onClick={p.onReview}>非执行评审</Button></>}<Button onClick={p.onDemand}>原需求与预测</Button></div></header>
  <ErrorNote error={error}/>{!data?!error&&<Loading/>:<>
   {!writable&&<div className="professional-policy-state"><Badge kind={data.draft?.run_id?'':'risk'}>{p.runState==='NOT_RUN'?'尚未计算':p.runState}</Badge>{!data.draft?.run_id&&<Button disabled={busy} className="primary" onClick={()=>act('run')}>运行研究候选</Button>}<span>规划模拟，不创建正式订单、承诺或采购审批。</span></div>}
   {(p.mode==='forecast-netting'||changes?.netting)&&<div className="professional-policy-panel"><h3>来源与逐桶抵扣账</h3><div className="professional-policy-table" tabIndex={0} aria-label="专业数据，可横向滚动"><DataTable<Dict> rows={netRows} columns={columns} rowId={r=>r.forecast_id}/></div>{changes?.netting&&<details><summary>逐订单消费与未匹配订单</summary><div className="professional-policy-table" tabIndex={0} aria-label="专业数据，可横向滚动"><DataTable<Dict> rows={changes.netting.rows.flatMap((r:Dict)=>(r.consumptions??[]).map((c:Dict)=>({...c,forecast_id:r.forecast_id,key:r.forecast_id+'|'+c.demand_id})))} rowId={r=>r.key} columns={[{id:'f',title:'预测桶',value:r=>r.forecast_id},{id:'d',title:'Firm需求行',value:r=>r.demand_id},{id:'q',title:'订单量',value:r=>r.order_quantity},{id:'c',title:'抵扣量',value:r=>r.consumed_quantity}]}/></div><p>未匹配 {changes.netting.unmatched_firm_orders.length} 条：仍作为Firm独立规划，不丢弃。</p></details>}</div>}
   {!writable&&changes?.replenishment&&<section className="professional-policy-panel" aria-label="本候选补货目标"><h3>本候选补货目标</h3><div className="professional-policy-table" tabIndex={0} aria-label="专业数据，可横向滚动"><DataTable<Dict> rows={changes.replenishment.rows} rowId={r=>r.item_id+'@'+r.location_id} columns={[{id:'object',title:'物料 / 地点',value:r=><>{r.item_id}<small>{r.location_id} · {r.uom}</small></>},{id:'minimum',title:'最低目标（软）',value:r=>r.minimum+' '+r.uom},{id:'maximum',title:'补至目标（软）',value:r=>r.maximum+' '+r.uom},{id:'range',title:'生效范围',value:r=><>{time(r.effective_from)}<small>至 {time(r.effective_to)}</small></>}]}/></div><p>研究政策 {changes.replenishment.policy_version}；原采购MOQ、倍量、提前期、QC和来源容量继续有效。实际新增采购及到料时间可在“比较原计划”核对。</p></section>}
   {result&&<div className="professional-policy-panel"><h3>实际运行结论</h3>{result.status==='FAILED'&&<p className="risk-message" role="alert">本次计算失败，未形成可用评估结果。{String(result.failure_code??'').includes('PROTECTED_MO_DRIFT')?'受保护工单窗口或换线安排未通过，保留来源工单约束。':'请核对输入、服务状态与运行依据。'}</p>}<Definition rows={[["原生业务核验",result.business_audit??'尚未完成'],["原订单逐期交付保护",protection?.status??'尚未核验'],["正式ATP承诺",'未建立；供应评估有条件']]}/>{inquiryResult&&<><h3>{inquiryStatus[inquiryResult.status]??inquiryResult.status}</h3><Definition rows={[["要求数量 / 日期",inquiryResult.requested_quantity+' '+inquiryResult.uom+' / '+time(inquiryResult.due_at)],["要求日期前实际可排",inquiryResult.planned_by_requested_due+' '+inquiryResult.uom],["本次试算日期 / 可排量",time(inquiryResult.evaluated_due_at??inquiryResult.due_at)+' / '+(inquiryResult.planned_by_evaluated_due??inquiryResult.planned_by_requested_due)+' '+inquiryResult.uom],["未排数量",inquiryResult.unscheduled_quantity+' '+inquiryResult.uom]]}/><p>只按本次原生交付事件计算；未找到方案不等于证明全局无解。</p></>}{protection&&<details><summary>原需求受益、受损与交付时间</summary><div className="professional-policy-table" tabIndex={0} aria-label="专业数据，可横向滚动"><DataTable<Dict> rows={protection.impacts} rowId={r=>r.demand_id} columns={[{id:'id',title:'原需求',value:r=>r.demand_id},{id:'base',title:'原计划量',value:r=>r.base_planned},{id:'new',title:'候选量',value:r=>r.candidate_planned},{id:'delta',title:'变化',value:r=>r.delta},{id:'due',title:'交期前数量',value:r=>r.base_by_due+' → '+r.candidate_by_due}]}/></div></details>}{result.replenishment&&<p>采购及库存的变化请结合下方原始业务入口和完整比较核对；软目标不保证全部达到。</p>}<details><summary>运行、版本与完整依据</summary><pre className="raw-json">{JSON.stringify(result,null,2)}</pre></details></div>}
   {!writable&&inquiryResult&&result?.business_audit==='PASS'&&<section className="professional-policy-panel" aria-label="有界交期评估"><h3>其他交期的有界评估 · {inquiryResult.search?.attempt??1} / 3</h3><p>保留相同产品、数量、交付规则、原政策与原承诺保护。每个新日期都创建独立候选并实际计算；不宣称已找到全局最早日期。</p>{(inquiryResult.search?.attempt??1)<3?<><div className="professional-policy-fields"><label>更晚的试算日期<input disabled={busy} type="datetime-local" value={nextDue} onChange={e=>{setNextDue(e.target.value);setNextPreview(null);setNextAck(false)}}/></label><label>补充试算理由<input disabled={busy} value={reason} onChange={e=>{setReason(e.target.value);setNextPreview(null);setNextAck(false)}}/></label></div><Button disabled={busy||!nextDue||!reason.trim()} onClick={()=>nextProbe(false)}>预览更晚交期</Button>{nextPreview?.key===actionKey&&<div><p>将试算 {time(nextPreview.value.parameters.inquiry.due_at)}，第 {nextPreview.value.parameters.inquiry.search.attempt} 次；创建后仍需运行原Worker。</p><label className="professional-policy-ack"><input type="checkbox" checked={nextAck} onChange={e=>setNextAck(e.target.checked)}/>确认仅作非执行交期研究。</label><Button className="primary" disabled={!nextAck||busy} onClick={()=>nextProbe(true)}>创建更晚交期候选</Button></div>}</>:<p>本次询价的3次预算已用完，保留全部候选与结果，未证明其他日期不可行。</p>}</section>}
   {p.mode==='stock-replenishment'&&data.material_investigation&&<JointMaterialInvestigation report={data.material_investigation} timezone={data.timezone} item={p.materialItem??''} location={p.materialLocation??''} onSelect={(item,location)=>p.onMaterialSelect?.(item,location)} onInventory={p.onInventory} onOperation={id=>p.onOperation?.(id)} onDemand={id=>p.onInvestigateDemand?.(id)}/>}
   {result?.supply_comparison&&<JointSupplyComparison value={result.supply_comparison} timezone={data.timezone}/>}
   {writable&&<form className="professional-policy-editor" onSubmit={e=>{e.preventDefault();void act('preview')}} onChange={resetPreview}>
    <fieldset disabled={busy}><legend>本次研究条件</legend><details className="professional-policy-combine"><summary>组合其他专业条件</summary><div className="professional-policy-checks"><label><input type="checkbox" checked={net} onChange={e=>setNet(e.target.checked)}/>预测抵扣</label><label><input type="checkbox" checked={inquire} onChange={e=>setInquire(e.target.checked)}/>新增订单评估</label><label><input type="checkbox" checked={stock} onChange={e=>setStock(e.target.checked)}/>补货政策</label></div></details>
    <div className="professional-policy-fields">{(net||stock)&&<label>研究政策版本<input value={version} onChange={e=>setVersion(e.target.value)} placeholder="例如 NET-STOCK-R1" required/></label>}<label>政策或请求依据<input value={source} onChange={e=>setSource(e.target.value)} placeholder="明确的研究设定或客户请求来源" required/></label><label className="professional-policy-wide">试算理由<textarea value={reason} onChange={e=>setReason(e.target.value)} required maxLength={1000}/></label></div>
    {net&&<section><h3>预测抵扣</h3><p>精确产品、地点、客户和单位；仅OPEN订单，同一来源桶，不跨桶。超额和未匹配订单保持Firm；负值、重复与未知单位拒绝。净需求优先级20，最小交付1，延期窗口0。</p><div className="professional-policy-buckets">{data.forecasts.map((f:Dict)=><label key={f.id}><span><input type="checkbox" checked={forecastIds.includes(f.id)} onChange={e=>setForecastIds(ids=>e.target.checked?[...ids,f.id]:ids.filter(id=>id!==f.id))}/>{f.item_id} · {f.customer_id} · Gross {f.gross_quantity} {f.uom}<small>{time(f.bucket_start)} → {time(f.bucket_end)}</small></span><span>净需求要求时间<input type="datetime-local" aria-label={'净需求要求时间 '+f.id} value={dues[f.id]??''} onChange={e=>setDues(d=>({...d,[f.id]:e.target.value}))}/></span></label>)}</div></section>}
    {inquire&&<section><h3>新增订单评估</h3><p>只提出增量询价，原订单承诺必须逐期保护；不抵扣已定义Gross，不建立库存预留。</p><div className="professional-policy-fields"><label>客户<Select disabled={busy} label="询价客户" value={customer} onChange={v=>{setCustomer(v);setItem('');setLocation('');setUnit('');resetPreview()}} options={[{value:'',label:'选择已识别客户'},...data.customers.map((c:Dict)=>({value:c.id,label:c.name??c.id}))]}/></label><label>产品<Select disabled={busy} label="询价产品" value={item} onChange={selectProduct} options={[{value:'',label:'选择产品'},...Array.from(new Set<string>(data.firm_demands.filter((d:Dict)=>!customer||d.customer_id===customer).map((d:Dict)=>d.item_id))).map(v=>({value:v,label:v}))]}/></label><label>交付地点<input value={location} onChange={e=>setLocation(e.target.value)} required/></label><label>单位<input value={unit} onChange={e=>setUnit(e.target.value)} required/></label><label>数量<input inputMode="decimal" value={quantity} onChange={e=>setQuantity(e.target.value)} required/></label><label>客户要求交期<input type="datetime-local" value={due} onChange={e=>setDue(e.target.value)} required/></label><label>交付规则<Select disabled={busy} label="询价交付规则" value={delivery} onChange={v=>{setDelivery(v);resetPreview()}} options={[{value:'ALL_OR_NOTHING',label:'完整交付'},{value:'SPLIT_BY_NATIVE_EVENTS',label:'允许原生事件分批'}]}/></label></div></section>}
    {stock&&<section><h3>物料与地点的补货目标</h3><p>人工明确的全规划期软目标；沿用原MOQ、倍量、提前期、QC及供应容量，不增加未知库存。</p><div className="professional-policy-stock">{stockRows.map((row,index)=><div key={index}><Select disabled={busy} label={'补货对象 '+(index+1)} value={row.item_id+'@'+row.location_id} onChange={v=>{const s=data.source_terms.find((s:Dict)=>s.item_id+'@'+s.location_id===v),f=data.stock_facts.find((f:Dict)=>f.item_id===s?.item_id&&f.location_id===s?.location_id);setStockRows(rs=>rs.map((r,i)=>i===index?{...r,item_id:s.item_id,location_id:s.location_id,uom:f.uom}:r));resetPreview()}} options={stockOptions}/><label>最低目标<input aria-label={'最低目标 '+(index+1)} inputMode="decimal" value={row.minimum} onChange={e=>setStockRows(rs=>rs.map((r,i)=>i===index?{...r,minimum:e.target.value}:r))}/></label><label>补至目标<input aria-label={'补至目标 '+(index+1)} inputMode="decimal" value={row.maximum} onChange={e=>setStockRows(rs=>rs.map((r,i)=>i===index?{...r,maximum:e.target.value}:r))}/></label><Button disabled={busy} onClick={()=>setStockRows(rs=>rs.filter((_,i)=>i!==index))}>移除</Button></div>)}</div><Button onClick={()=>{const s=data.source_terms.find((s:Dict)=>!stockRows.some(r=>r.item_id===s.item_id&&r.location_id===s.location_id));if(s){const f=data.stock_facts.find((f:Dict)=>f.item_id===s.item_id&&f.location_id===s.location_id);setStockRows(rs=>[...rs,{item_id:s.item_id,location_id:s.location_id,uom:f.uom,minimum:'',maximum:''}]);resetPreview()}}} disabled={busy||stockRows.length>=stockOptions.length}>添加物料地点</Button></section>}
    {data.distribution_lanes?.length>0&&<section aria-label="采购与调拨条件试算"><h3>采购与调拨条件</h3><div className="professional-policy-checks"><label><input type="checkbox" checked={usePurchase} onChange={e=>setUsePurchase(e.target.checked)}/>未收采购余额延期</label><label><input type="checkbox" checked={useRoute} onChange={e=>setUseRoute(e.target.checked)}/>运输提前期延长</label></div><div className="professional-policy-fields">{usePurchase&&<><label>采购余额<Select disabled={busy} label="联合采购单" value={purchaseRef} onChange={setPurchaseRef} options={[{value:'',label:'选择确认采购'},...data.purchases.map((r:Dict)=>({value:r.id,label:r.id+' · 未收 '+r.remaining_quantity+' '+r.uom}))]}/></label><label>余额预计物理到货<input aria-label="联合采购到货" type="datetime-local" value={purchaseDate} onChange={e=>setPurchaseDate(e.target.value)}/></label></>}{useRoute&&<><label>调拨路线<Select disabled={busy} label="联合调拨路线" value={laneId} onChange={setLaneId} options={[{value:'',label:'选择已核对路线'},...data.distribution_lanes.map((r:Dict)=>({value:r.id,label:r.item_id+' · '+r.origin_location_id+' → '+r.destination_location_id}))]}/></label><label>运输提前期（小时）<input aria-label="联合运输提前期" inputMode="decimal" value={leadHours} onChange={e=>setLeadHours(e.target.value)}/></label></>}</div><p>仅改变候选规划条件。实收、冻结、已确认工单及源地点库存不变；不下达正式采购或调拨。</p></section>}
    <div className="professional-policy-actions"><Button type="submit" className="primary" disabled={busy||(!net&&!inquire&&!stock&&!usePurchase&&!useRoute)}>预览专业条件</Button></div></fieldset>
   </form>}
   {pv&&<div className="professional-policy-panel" aria-live="polite"><h3>{pv.worker_eligible?'预览已准备，可创建独立候选':'当前政策存在阻断，未创建候选'}</h3>{pv.changes.inquiry&&<Definition rows={[["新增询价",pv.changes.inquiry.added_demand.item_id+' · '+pv.changes.inquiry.added_demand.quantity+' '+pv.changes.inquiry.added_demand.uom],["目标日期",time(pv.changes.inquiry.added_demand.due_at)],["原承诺保护",'计算后逐期核对，未通过不能建议接单']]}/>}<label className="professional-policy-ack"><input type="checkbox" checked={ack} onChange={e=>setAck(e.target.checked)}/>仅用于隔离研究；研究政策不代表企业批准，预览不是计算结果。</label><Button disabled={!ack||!pv.worker_eligible||busy} className="primary" onClick={()=>act('create')}>创建独立候选</Button></div>}
   {(p.mode==='stock-replenishment'||stock)&&<div className="professional-policy-panel"><h3>既有供应条款与库存入口</h3><div className="professional-policy-table" tabIndex={0} aria-label="专业数据，可横向滚动"><DataTable<Dict> rows={data.source_terms} rowId={r=>r.id} columns={[{id:'source',title:'来源 / 对象',value:r=><>{r.id}<small>{r.item_id} · {r.location_id}</small></>},{id:'minimum',title:'MOQ / 倍量',value:r=>r.minimum_order_quantity+' / '+r.order_multiple},{id:'lead',title:'提前期 / QC',value:r=>(Number(r.lead_time_seconds)/3600)+' / '+(Number(r.receiving_qc?.wait_seconds)/3600)+' 小时'},{id:'capacity',title:'全期容量',value:r=>r.capacity?.quantity},{id:'investigate',title:'库存与采购',value:r=><Button onClick={()=>p.onInventory(r.item_id,r.location_id)}>调查原事件与单据</Button>}]}/></div></div>}
   <details><summary>当前身份、来源和政策依据</summary><pre className="raw-json">{JSON.stringify({identity:data.identity,source_policy_status:data.source_policy_status,changes:data.changes},null,2)}</pre></details>
  </>}
 </section>;
}
