import {useEffect,useMemo,useRef,useState} from 'react';
import {useSearchParams} from 'react-router';
import {forecastWorkspaceVersion,forecastWorkspaceSearch} from './forecastWorkspaceSelection';
import type {EChartsCoreOption} from 'echarts/core';
import type {Dict} from '../api/contracts';
import {request} from '../api/client';
import {useActionGuard} from '../app/useActionGuard';
import {Badge,Button,Definition,ErrorNote,Loading,Select} from '../components/Controls';
import {ForecastPeriodEditor} from './ForecastPeriodEditor';
import {DataTable,type TableColumn} from '../components/DataTable';
import {PlanningChart} from '../components/PlanningChart';
import {professionalIdentity,siteTimestamp,verifyProfessionalReport,type ProfessionalScope} from './professionalPolicyModel';
import {advancedNettingParameters,forecastSeries,quoteActions,researchActionAllowed,quoteSupplyView,quoteActionScope,adoptedWorkbenchState,verifySourceResearch} from './intelligenceWorkbenchModel';
import {formatSiteTime} from './timeRange';
import './intelligence-workbench.css';

type Mode='forecast-netting'|'order-inquiry'|'stock-replenishment';
type Props={scope:ProfessionalScope;mode:Mode;runState:string;sourceOnly?:boolean;onTarget:(id:string)=>void;onRefresh:()=>void;onCompare:()=>void;onReview:()=>void;onInventory:(item:string,location:string)=>void;onDemand:()=>void};
const titles:Record<Mode,string>={'forecast-netting':'需求预测与滚动计划','stock-replenishment':'库存策略与补货建议','order-inquiry':'ATP / CTP 与研究询价'};
const models=['Naive','SeasonalNaive','AutoETS','CrostonSBA','TSB'];
const text=(value:unknown)=>value===null||value===undefined?'未提供':typeof value==='object'?JSON.stringify(value):String(value);
const array=(value:unknown):Dict[]=>Array.isArray(value)?value:[];
const pct=(value:unknown)=>value!==null&&value!==undefined&&Number.isFinite(Number(value))?(Number(value)*100).toFixed(2)+'%':'无定义 / 未评价';
const methodNames:Record<string,string>={CURRENT_SOFT_MIN_MAX:'当前软 min/max',DETERMINISTIC_ORDER_UP_TO:'确定性补至目标',NORMAL_SERVICE_ORDER_UP_TO:'波动与服务目标'};
const stateNames:Record<string,string>={INQUIRY:'待计算',CALCULATED:'已计算',OFFERED:'已形成研究报价',HELD:'研究占用中',RELEASED:'已释放',EXPIRED:'已失效',COMMITTED_SIMULATED:'已模拟确认',QUEUED:'排队中',RUNNING:'计算中',SUCCEEDED:'已完成',FAILED:'失败',CONFIRMED_RESEARCH:'研究版本已确认',WITHDRAWN:'已撤回'};
const actionNames:Record<string,string>={quote_calculate:'计算可承诺供应',quote_offer:'形成研究报价',quote_hold:'建立研究占用',quote_release:'释放占用',quote_commit:'模拟确认'};

export function IntelligenceWorkbench(p:Props){
 const [forecastSearch,setForecastSearch]=useSearchParams(),forecastScope=JSON.stringify([p.scope,p.sourceOnly===true]);
 const scopeKey=JSON.stringify([p.scope,p.mode,p.runState,p.sourceOnly]);
 const [loaded,setLoaded]=useState<{key:string;value:Dict}|null>(null),[revision,setRevision]=useState(0),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false);
 const [datasetId,setDatasetId]=useState(''),[model,setModel]=useState('SeasonalNaive'),[cutoff,setCutoff]=useState(''),[issue,setIssue]=useState(''),[horizon,setHorizon]=useState('10');
 const [versionId,setVersionId]=useState(''),[rowId,setRowId]=useState(''),[actor,setActor]=useState(''),[reason,setReason]=useState(''),[restoreId,setRestoreId]=useState('');
 const [sourceId,setSourceId]=useState(''),[service,setService]=useState('0.95'),[inventoryId,setInventoryId]=useState(''),[inventoryMethod,setInventoryMethod]=useState('NORMAL_SERVICE_ORDER_UP_TO');
 const [quoteId,setQuoteId]=useState(''),[itemId,setItemId]=useState(''),[locationId,setLocationId]=useState(''),[customerId,setCustomerId]=useState(''),[uom,setUom]=useState(''),[quantity,setQuantity]=useState(''),[due,setDue]=useState(''),[partial,setPartial]=useState(false),[supplyPolicy,setSupplyPolicy]=useState('CONFIRMED_ONLY'),[expiry,setExpiry]=useState('');
 const [useNet,setUseNet]=useState(p.mode==='forecast-netting'),[useInventory,setUseInventory]=useState(p.mode==='stock-replenishment'),[useInquiry,setUseInquiry]=useState(p.mode==='order-inquiry'),[backward,setBackward]=useState('0'),[forward,setForward]=useState('0');
 const [preview,setPreview]=useState<{key:string;value:Dict}|null>(null),[ack,setAck]=useState(false);
 const receipts=useRef(new Map<string,string>()),inquiryId=useRef('INQUIRY-'+crypto.randomUUID().replaceAll('-','').slice(0,16));
 const data=loaded?.key===scopeKey?loaded.value:null,context=data?.context??{},timezone=context.timezone??'Asia/Shanghai',writable=p.scope.target==='baseline';
 const quoteWritable=!p.sourceOnly&&researchActionAllowed('quote_create',p.scope.target,p.runState,context.outcome?.business_audit);
 const versions=array(data?.forecast_versions),datasets=array(data?.datasets),inventories=array(data?.inventory_candidates),quotes=array(data?.quotes),jobs=array(data?.jobs);
 const selectedVersion=versions.find(v=>v.id===versionId),selectedDataset=datasets.find(d=>d.id===datasetId),selectedInventory=inventories.find(v=>v.id===inventoryId),selectedQuote=quotes.find(q=>q.id===quoteId);
 const quoteView=selectedQuote?quoteSupplyView(selectedQuote):{};
 const forecastRows=array(selectedVersion?.rows),selectedRow=forecastRows.find(r=>r.id===rowId),template=data?.netting_templates?.[versionId];
 const prefix='/v1/enterprise-plans/'+encodeURIComponent(p.scope.planId)+'/intelligence';
 const policyPrefix='/v1/enterprise-plans/'+encodeURIComponent(p.scope.planId)+'/professional-policy';
 const actionKey=JSON.stringify([scopeKey,versionId,inventoryId,useNet,useInventory,useInquiry,backward,forward,reason,itemId,locationId,customerId,uom,quantity,due,partial,supplyPolicy]);
 const capture=useActionGuard(scopeKey),pv=preview?.key===actionKey?preview.value:null;
 const verify=(value:Dict)=>p.sourceOnly?verifySourceResearch(value,p.scope):verifyProfessionalReport(value,p.scope);
 const time=(value:unknown)=>typeof value==='string'&&value?formatSiteTime(value,timezone):'未提供';
 const local=(value:unknown)=>typeof value==='string'&&value?formatSiteTime(value,timezone).replace(' ','T'):'';
 const showError=(e:unknown)=>setError(e instanceof Error?e.message:'操作未完成，请刷新核对当前记录。');

 useEffect(()=>{
  const controller=new AbortController();setError('');setPreview(null);setAck(false);
  request<Dict>(prefix+(p.sourceOnly?'/source?'+new URLSearchParams({data_version_id:p.scope.versionId}):'?'+new URLSearchParams(professionalIdentity(p.scope))),{signal:controller.signal}).then(value=>{
   if(controller.signal.aborted)return;
   const report=verify(value);setLoaded({key:scopeKey,value:report});
   const ds=array(report.datasets),vs=array(report.forecast_versions),is=array(report.inventory_candidates),qs=array(report.quotes);
   setDatasetId(id=>ds.some(d=>d.id===id)?id:ds[0]?.id??'');
   const adopted=adoptedWorkbenchState(report,p.scope.target);
   setVersionId(id=>adopted?adopted.versionId:vs.some(v=>v.id===id)?id:forecastWorkspaceVersion(forecastSearch,forecastScope,vs));
   setInventoryId(id=>adopted?adopted.inventoryId:is.some(v=>v.id===id)?id:is.at(-1)?.id??'');
   if(adopted){setUseNet(adopted.useNet);setUseInventory(adopted.useInventory);setUseInquiry(adopted.useInquiry);setBackward(adopted.backward);setForward(adopted.forward);setReason(adopted.reason);setInventoryMethod(adopted.method);setSourceId(adopted.inventory?.source_id??'');
    const q=adopted.inquiry;setItemId(q?.item_id??'');setLocationId(q?.location_id??'');setCustomerId(q?.customer_id??'');setUom(q?.uom??'');setQuantity(q?.quantity??'');setDue(q?.due_at?local(q.due_at):'');setPartial(q?.delivery_rule==='SPLIT_BY_NATIVE_EVENTS');setSupplyPolicy(q?'RESEARCH_CTP':'CONFIRMED_ONLY');}

   setQuoteId(id=>qs.some(q=>q.id===id)?id:qs.at(-1)?.id??'');
  }).catch(e=>{if(!controller.signal.aborted)showError(e)});
  return()=>controller.abort();
 },[scopeKey,revision]);
 useEffect(()=>{setBusy(false);setNotice('')},[scopeKey]);
 useEffect(()=>{
  if(!selectedDataset)return;setCutoff(local(selectedDataset.training_cutoff));setIssue(local(selectedDataset.issue_time));
 },[datasetId,selectedDataset?.training_cutoff,selectedDataset?.issue_time]);
 useEffect(()=>{setRowId(id=>forecastSearch.get('forecast_scope')===forecastScope&&forecastSearch.get('forecast_version')===versionId&&forecastRows.some(r=>r.id===forecastSearch.get('forecast_row'))?forecastSearch.get('forecast_row')!:forecastRows.some(r=>r.id===id)?id:forecastRows[0]?.id??'')},[versionId,forecastRows.length]);
 useEffect(()=>{
  if(!data||busy||!jobs.some(j=>['QUEUED','RUNNING'].includes(j.state)))return;
  const timer=window.setTimeout(()=>setRevision(v=>v+1),3000);return()=>window.clearTimeout(timer);
 },[data,busy]);
 useEffect(()=>{setPreview(null);setAck(false)},[actionKey]);

 function selectForecastVersion(id:string){setVersionId(id);setForecastSearch(old=>forecastWorkspaceSearch(old,forecastScope,id))}
 function selectForecastRow(id:string){setRowId(id);setForecastSearch(old=>forecastWorkspaceSearch(old,forecastScope,versionId,id))}
 function requestId(key:string){let id=receipts.current.get(key);if(!id){id=crypto.randomUUID().replaceAll('-','');receipts.current.set(key,id)}return id}
 function requireReason(){if(!actor.trim()||!reason.trim())throw Error('请填写修改人和本次操作理由。');return {actor:actor.trim(),reason:reason.trim()}}
 async function action(name:string,payload:Dict){
  if(!data||busy)return;
  if(!researchActionAllowed(name,p.scope.target,p.runState,context.outcome?.business_audit)){
   setError(name.startsWith('quote_')?'当前运行 '+p.runState+' 尚未通过原生业务审计，请先运行并核对本方案。':'预测与库存政策修改请返回原始基线。');return;
  }
  const owns=capture();setBusy(true);setError('');setNotice('');
  try{
   const key=JSON.stringify([scopeKey,name,payload]);
   const actionScope=quoteActionScope(p.scope,selectedQuote?.id===payload.quote_id?selectedQuote:undefined,name);
   const value=await request<Dict>(prefix+(p.sourceOnly?'/source-actions':'/actions'),{method:'POST',body:{...(p.sourceOnly?{data_version_id:p.scope.versionId}:professionalIdentity(actionScope)),action:name,payload,request_id:requestId(key)}});
   if(!owns())return;p.sourceOnly?verifySourceResearch(value,p.scope):verifyProfessionalReport(value,actionScope);
   if(typeof value.version_id==='string')selectForecastVersion(value.version_id);
   if(typeof value.inventory_id==='string')setInventoryId(value.inventory_id);
   if(typeof value.quote?.id==='string')setQuoteId(value.quote.id);
   setNotice('操作已保存，已重新读取当前研究记录。');setRevision(v=>v+1);
  }catch(e){if(owns())showError(e)}finally{if(owns())setBusy(false)}
 }
 function versionAction(name:string,extra:Dict={}){
  try{void action(name,{version_id:versionId,...extra,...requireReason()})}catch(e){showError(e)}
 }
 function inquiryRequest(){
  if(!itemId||!locationId||!customerId||!uom||!quantity||!Number.isFinite(Number(quantity))||Number(quantity)<=0)throw Error('请明确选择产品、地点、客户、单位，并填写大于0的数量。');
  return {item_id:itemId,location_id:locationId,customer_id:customerId,uom,quantity,due_at:siteTimestamp(due,timezone),allow_partial:partial,supply_policy:supplyPolicy};
 }
 function nativeParameters(){
  if(!reason.trim())throw Error('请填写联合试算理由。');
  if(!useNet&&!useInventory&&!useInquiry)throw Error('请至少选择一项联合试算条件。');
  if(useNet&&!template)throw Error('请先确认一个有效的研究预测版本。');
  if(useInventory&&!selectedInventory?.native_policy)throw Error('请选择有原生目标政策的补货候选。');
  const q=useInquiry?inquiryRequest():null;
  return {netting:useNet?advancedNettingParameters(template,Number(backward),Number(forward)):null,
   replenishment:useInventory?selectedInventory!.native_policy:null,
   inquiry:q?{id:inquiryId.current,customer_id:q.customer_id,item_id:q.item_id,location_id:q.location_id,uom:q.uom,quantity:q.quantity,due_at:q.due_at,delivery_rule:q.allow_partial?'SPLIT_BY_NATIVE_EVENTS':'ALL_OR_NOTHING',coverage:'INCREMENTAL_OUTSIDE_GROSS',protect_existing:true,request_source:reason.trim()}:null,reason:reason.trim()};
 }
 async function nativeAction(kind:'preview'|'draft'|'run'){
  if(!data||busy)return;const owns=capture();setBusy(true);setError('');setNotice('');
  try{
   if(p.sourceOnly)throw Error('请在联合规划入口采用研究条件，并通过原始基线身份及业务审计检查。');
   if(kind==='run'){
    if(writable||!context.draft?.id||context.draft.run_id)throw Error('当前不是尚未运行的研究草案。');
    await request('/v1/drafts/'+encodeURIComponent(context.draft.id)+'/run',{method:'POST',scope:{planId:p.scope.planId,target:p.scope.target},body:{}});
    if(owns()){p.onRefresh();setRevision(v=>v+1)}return;
   }
   if(!writable)throw Error('请返回原始基线准备新候选。');
   const body={...professionalIdentity(p.scope),...nativeParameters()};
   if(kind==='preview'){
    const value=await request<Dict>(policyPrefix+'-preview',{method:'POST',body});
    if(owns()){verifyProfessionalReport(value,p.scope);setPreview({key:actionKey,value});setAck(false)}
   }else{
    if(!pv?.worker_eligible||!ack)throw Error('请先核对本次预览并确认研究条件。');
    const value=await request<Dict>(policyPrefix+'-drafts',{method:'POST',body:{...body,fingerprint:pv.fingerprint,acknowledge_limits:true,request_id:requestId('draft:'+actionKey+pv.fingerprint)}});
    if(!owns())return;
    if(value.plan_id!==p.scope.planId||value.data_version_id!==p.scope.versionId||value.baseline_run_id!==p.scope.baselineRunId||value.draft?.changes?.confirmation?.fingerprint!==pv.fingerprint)throw Error('草案身份与当前预览不一致，请刷新核对。');
    p.onTarget(value.draft.id);
   }
  }catch(e){if(owns())showError(e)}finally{if(owns())setBusy(false)}
 }

 const history=array(selectedDataset?.history_rows),chartSeries=forecastSeries(forecastRows,history,rowId);
 const chart=useMemo<EChartsCoreOption|null>(()=>!chartSeries?null:{animation:false,tooltip:{trigger:'axis',renderMode:'richText',formatter:(input:unknown)=>{
   const points=(Array.isArray(input)?input:[input]) as {value?:unknown[];seriesName?:string}[];
   const stamp=Number(points[0]?.value?.[0]);
   return [Number.isFinite(stamp)?formatSiteTime(new Date(stamp).toISOString(),timezone):'',...points.map(point=>(point.seriesName??'数量')+'：'+(point.value?.[1]==null?'缺测':String(point.value[1]))+' '+chartSeries.unit)].join('\n');
  }},legend:{top:0},grid:{left:64,right:20,top:44,bottom:52},
  xAxis:{type:'time',axisLabel:{hideOverlap:true,formatter:(value:number)=>formatSiteTime(new Date(value).toISOString(),timezone).slice(0,10)}},yAxis:{type:'value',name:chartSeries.unit,min:0},dataZoom:[{type:'inside'},{type:'slider',height:18,bottom:5}],
  series:[{type:'line',name:selectedDataset?.observation_kind==='HISTORICAL_ORDER'?'订单历史':selectedDataset?.observation_kind==='SHIPMENT'?'出货历史':'观测需求',connectNulls:false,showSymbol:false,data:chartSeries.history.map(r=>[Date.parse(r.bucket_start),r.quantity==null?null:Number(r.quantity)])},
   {type:'line',name:'本版 Gross',showSymbol:true,data:chartSeries.forecast.map(r=>({value:[Date.parse(r.bucket_start),Number(r.gross_quantity??r.quantity)],eventId:r.id}))}]},[selectedVersion,selectedDataset,rowId,timezone]);
 const netRows=array(pv?.changes?.netting?.rows??context.draft?.changes?.netting?.rows);
 const displayedForecast:Dict[]=forecastRows.map(r=>({...r,netting:netRows.find(n=>(n.forecast_id??n.id)===r.id)}));
 const objectColumns:TableColumn<Dict>[]=[{id:'object',title:'产品 / 地点 / 客户',value:r=><>{r.item_id}<small>{r.location_id} · {r.customer_id} · {r.uom??r.unit}</small></>},{id:'bucket',title:'期间（半开）',value:r=><>{time(r.bucket_start)}<small>至 {time(r.bucket_end)}</small></>}];
 const originalOutcome=context.outcome,protection=originalOutcome?.original_commitments;
 const sourceTerms=array(context.source_terms),stockFacts=array(context.stock_facts),items=array(context.items),customers=array(context.customers);
 const selectedItem=items.find(r=>(r.id??r.item_id)===itemId);
 const locationOptions=[...new Set([...stockFacts,...sourceTerms,...forecastRows].filter(r=>!itemId||r.item_id===itemId).map(r=>r.location_id).filter(Boolean))] as string[];
 const chosenSource=sourceTerms.find(r=>(r.id??r.source_id)===sourceId);
 const metrics=selectedVersion?.backtest?.aggregate_by_unit_model??selectedVersion?.backtest?.units;
 const metricRows:Dict[]=metrics?Object.entries(metrics).flatMap(([unit,values])=>Object.entries((values as Dict).models??values as Dict).filter(([,v])=>v&&typeof v==='object'&&'wape' in (v as Dict)).map(([method,values])=>({unit,method,...values as Dict}))):[];
 const adoptedPolicy=context.draft?.adopted?.inventory_policy;
 const policyRows=array(writable?selectedInventory?.native_policy?.rows:adoptedPolicy?.rows);
 const appliedMethod=selectedInventory?.result?.candidates?.find((c:Dict)=>c.method===selectedInventory.result.selected_method&&policyRows.length===1&&String(c.minimum)===String(policyRows[0].minimum)&&String(c.maximum)===String(policyRows[0].maximum))?.method;

 return <section className="intelligence-workbench" aria-label={titles[p.mode]}>
  <header className="intelligence-heading"><div><h2>{titles[p.mode]}</h2><p>{writable?'原始基线 · 选择研究条件':'研究候选'} · {timezone}</p></div><div className="intelligence-actions"><Button icon="reload" disabled={busy} onClick={()=>setRevision(v=>v+1)}>刷新</Button>{!writable&&<><Button onClick={()=>p.onTarget('baseline')}>返回原始基线</Button><Button onClick={p.onCompare}>比较原计划</Button><Button onClick={p.onReview}>非执行评审</Button></>}<Button onClick={p.onDemand}>需求与履约</Button></div></header>
  <ErrorNote error={error}/>{notice&&<p className="intelligence-notice" role="status">{notice}</p>}
  {!data?!error&&<Loading/>:<>
   {!writable&&<section className="intelligence-run" aria-label="研究候选运行"><Badge>{p.runState}</Badge><span>{text(context.draft?.id)}</span>{context.draft&&!context.draft.run_id&&<Button className="primary" disabled={busy} onClick={()=>void nativeAction('run')}>运行研究候选</Button>}<Button onClick={p.onReview}>查看本方案评审</Button></section>}

   {p.mode==='forecast-netting'&&<>
    <section className="intelligence-panel" aria-label="统计预测计算"><div className="intelligence-section-title"><h3>历史与统计基线</h3><span>原 Gross 保留在来源版本</span></div>
     <form onSubmit={e=>{e.preventDefault();try{const n=Number(horizon);if(!Number.isInteger(n)||n<1||n>20)throw Error('预测期数须为1至20的整数。');void action('forecast_start',{dataset_id:datasetId,model,training_cutoff:siteTimestamp(cutoff,timezone),issue_time:siteTimestamp(issue,timezone),horizon:n})}catch(e){showError(e)}}}>
      <fieldset disabled={busy||!writable}><div className="intelligence-fields"><label>研究历史集<Select label="研究历史集" value={datasetId} onChange={setDatasetId} options={datasets.map(d=>({value:d.id,label:d.id+' · '+text(d.rows)+'条 · '+text(d.classification)}))}/></label><label>预测方法<Select label="预测方法" value={model} onChange={setModel} options={models.map(m=>({value:m,label:m}))}/></label><label>未来期数（{selectedDataset?.freq==='D'?'天':selectedDataset?.freq==='MS'?'月':'周'}）<input required type="number" min="1" max="20" step="1" value={horizon} onChange={e=>setHorizon(e.target.value)}/></label><label>训练截止<input required type="datetime-local" value={cutoff} onChange={e=>setCutoff(e.target.value)}/></label><label>签发时刻<input required type="datetime-local" value={issue} onChange={e=>setIssue(e.target.value)}/></label><div className="intelligence-submit"><Button type="submit" className="primary" disabled={!datasetId} loading={busy}>计算统计预测</Button></div></div></fieldset>
     </form>
     {selectedDataset&&<p>{text(selectedDataset.series)}条独立序列；{selectedDataset.observation_kind==='HISTORICAL_ORDER'?'订单历史':selectedDataset.observation_kind==='SHIPMENT'?'出货历史':'观测需求'}；{text(selectedDataset.classification)}。历史缺口、晚到观测与缺货删失按数据合同处理。</p>}
     <details className="intelligence-evidence"><summary>原来源 Gross（保留原政策状态）</summary><div className="intelligence-table" tabIndex={0} aria-label="原来源预测，可横向滚动"><DataTable rows={array(context.original_gross)} rowId={r=>r.id} columns={[...objectColumns,{id:'gross',title:'原 Gross',numeric:true,value:r=>text(r.gross_quantity)},{id:'policy',title:'原抵扣政策',value:r=>text(r.netting_policy??r.policy_id)}]} empty="当前概览未提供原来源预测记录。"/></div></details>
     {!!jobs.length&&<div className="intelligence-table" tabIndex={0} aria-label="预测计算任务，可横向滚动"><DataTable rows={jobs} rowId={r=>r.id} columns={[{id:'id',title:'计算任务',value:r=>r.id},{id:'model',title:'方法',value:r=>r.model},{id:'state',title:'状态',value:r=>stateNames[r.state]??r.state},{id:'elapsed',title:'算法耗时（秒）',numeric:true,value:r=>text(r.elapsed_seconds)},{id:'version',title:'输出版本 / 失败原因',value:r=>r.error??r.version_id??'等待计算'}]}/></div>}
    </section>
    <section className="intelligence-panel" aria-label="预测版本管理"><h3>预测版本与逐桶需求</h3>
     <div className="intelligence-table" tabIndex={0} aria-label="预测版本，可横向滚动"><DataTable rows={versions} rowId={r=>r.id} selected={versionId} onSelect={v=>selectForecastVersion(v.id)} empty="尚无统计预测版本。选择历史集并计算。" columns={[{id:'id',title:'版本',value:r=>r.id},{id:'model',title:'方法',value:r=>r.model??r.rows?.[0]?.model??'组合方法'},{id:'status',title:'当前状态',value:r=>stateNames[r.effective_status??r.status]??r.effective_status??r.status},{id:'rows',title:'期间行数',numeric:true,value:r=>Array.isArray(r.rows)?r.rows.length:'未知'},{id:'cutoff',title:'训练截止',value:r=>time(r.training_cutoff??r.rows?.[0]?.training_cutoff)},{id:'issue',title:'签发时刻',value:r=>time(r.issue_time??r.rows?.[0]?.issue_time)}]}/></div>
      {chart&&<div className="intelligence-chart"><div className="intelligence-chart-heading"><strong>{chartSeries?.label}</strong><span>{chartSeries?.unit} · 缺货删失保留空缺</span></div><PlanningChart compact identityKey={scopeKey+'|'+versionId+'|'+chartSeries?.label+'|'+chartSeries?.unit} label="同产品地点客户单位的历史需求与Gross预测" option={chart} selectedEventId={rowId} onPick={selectForecastRow}/></div>}
     {selectedVersion&&<>
      <ForecastPeriodEditor key={scopeKey} versionId={versionId} rows={displayedForecast} selectedId={rowId} onSelect={selectForecastRow} writable={writable} busy={busy} canSave={!!template&&!!actor.trim()&&!!reason.trim()} onSave={body=>versionAction('forecast_override',body)}/>
      {selectedRow&&<details className="intelligence-evidence"><summary>所选期间的来源、版本与抵扣明细</summary><Definition rows={[["行标识",selectedRow.id],["训练截止",time(selectedRow.training_cutoff)],["签发时刻",time(selectedRow.issue_time)],["政策版本",text(selectedRow.policy_version)],["训练摘要",text(selectedRow.training_digest)]]}/><pre>{JSON.stringify({source_refs:selectedRow.source_refs,allocations:netRows.find(r=>(r.forecast_id??r.id)===rowId)?.consumptions??netRows.find(r=>(r.forecast_id??r.id)===rowId)?.allocations??null},null,2)}</pre></details>}
      <details className="intelligence-evidence"><summary>回测误差与区间覆盖</summary>{metricRows.length?<div className="intelligence-table" tabIndex={0} aria-label="按单位的回测误差，可横向滚动"><DataTable rows={metricRows} rowId={r=>r.unit+'|'+r.method} columns={[{id:'unit',title:'单位',value:r=>r.unit},{id:'model',title:'方法',value:r=>r.method},{id:'wape',title:'WAPE',numeric:true,value:r=>pct(r.wape)},{id:'mase',title:'平均MASE',numeric:true,value:r=>text(r.mean_mase)},{id:'bias',title:'Bias',numeric:true,value:r=>pct(r.bias_ratio)},{id:'coverage',title:'区间实测覆盖',numeric:true,value:r=>pct(r.coverage)},{id:'n',title:'已评分观测',numeric:true,value:r=>text(r.n)}]}/></div>:<p>本版本没有可展示的逐模型回测摘要，请核对关联计算记录。</p>}<p>各单位分别评价；覆盖是实测比例。零实际量或零训练尺度的指标保留无定义。</p></details>
      <fieldset className="intelligence-version-editor" disabled={busy||!writable}><legend>版本操作</legend><div className="intelligence-fields"><label>修改人<input value={actor} maxLength={100} onChange={e=>setActor(e.target.value)}/></label><label className="intelligence-wide">修改或确认理由<textarea value={reason} maxLength={2000} onChange={e=>setReason(e.target.value)}/></label></div><div className="intelligence-actions"><Button className="primary" disabled={!!template||!actor.trim()||!reason.trim()} onClick={()=>versionAction('forecast_confirm')}>确认研究预测版本</Button><Button disabled={!template||!actor.trim()||!reason.trim()} onClick={()=>versionAction('forecast_withdraw')}>撤回当前确认</Button></div>
       <div className="intelligence-subsection"><h4>恢复历史版本</h4><div className="intelligence-fields"><label>恢复来源<Select label="恢复来源版本" value={restoreId} onChange={setRestoreId} options={versions.filter(v=>v.id!==versionId).map(v=>({value:v.id,label:v.id}))}/></label><div className="intelligence-submit"><Button disabled={!restoreId||!actor.trim()||!reason.trim()} onClick={()=>versionAction('forecast_restore',{restore_version_id:restoreId})}>以历史值创建恢复版本</Button></div></div></div>
      </fieldset>
     </>}
    </section>
   </>}

   {p.mode==='stock-replenishment'&&<>
    <section className="intelligence-panel" aria-label="生成库存策略"><h3>从来源条款生成补货候选</h3><form onSubmit={e=>{e.preventDefault();if(!reason.trim()){setError('请填写本次补货研究理由。');return}void action('inventory_recommend',{source_id:sourceId,service_level:Number(service),reason:reason.trim(),method:inventoryMethod})}}><fieldset disabled={busy||!writable}><div className="intelligence-fields"><label>物料 / 地点 / 来源<Select label="补货来源" value={sourceId} onChange={setSourceId} options={sourceTerms.map(s=>({value:s.id??s.source_id,label:s.item_id+' · '+s.location_id+' · '+(s.id??s.source_id)}))}/></label><label>采用策略<Select label="采用库存策略" value={inventoryMethod} onChange={setInventoryMethod} options={Object.entries(methodNames).map(([value,label])=>({value,label}))}/></label><label>周期服务目标<input required type="number" min="0.5" max="0.999" step="0.01" value={service} onChange={e=>setService(e.target.value)}/></label><label className="intelligence-wide">研究理由<textarea required value={reason} maxLength={2000} onChange={e=>setReason(e.target.value)}/></label><div className="intelligence-submit"><Button type="submit" className="primary" disabled={!sourceId} loading={busy}>计算补货策略</Button></div></div></fieldset></form>
     {chosenSource&&<Definition rows={[["MOQ / 倍量",text(chosenSource.minimum_order_quantity)+' / '+text(chosenSource.order_multiple)],["采购提前期 / QC等待（秒）",text(chosenSource.lead_time_seconds)+' / '+text(chosenSource.receiving_qc?.wait_seconds??chosenSource.qc_wait_seconds)],["来源容量",text(chosenSource.capacity?.quantity??chosenSource.total_capacity_quantity??chosenSource.capacity_quantity)],["条款来源",text(chosenSource.source_ref??chosenSource.provenance?.entity_id)]]}/>}<p>服务目标与实际达成分别保留，补货建议需再通过原生供需计划。</p>
    </section>
    <section className="intelligence-panel" aria-label="比较库存策略"><h3>基线、确定性与波动策略比较</h3><label className="intelligence-selector">研究记录<Select label="库存研究记录" value={inventoryId} onChange={setInventoryId} options={inventories.map(v=>({value:v.id,label:v.id+' · '+v.source_id}))}/></label>
     <div className="intelligence-table" tabIndex={0} aria-label="补货策略比较，可横向滚动"><DataTable rows={array(selectedInventory?.result?.candidates)} rowId={r=>r.id??r.method} empty="选择来源并计算后，将在此保留可比较的策略。" columns={[{id:'method',title:'策略',value:r=>methodNames[r.method]??r.method},{id:'safety',title:'安全库存',numeric:true,value:r=>text(r.safety_stock??r.metrics?.safety_stock_quantity)},{id:'rop',title:'订货点',numeric:true,value:r=>text(r.reorder_point)},{id:'order',title:'补至位置',numeric:true,value:r=>text(r.order_up_to)},{id:'quantity',title:'来源约束后建议量',numeric:true,value:r=>text(r.recommendation?.quantity)},{id:'gap',title:'目标未补足',numeric:true,value:r=>text(r.recommendation?.unfilled_target_quantity)},{id:'service',title:'目标 / 实际服务',value:r=>pct(r.metrics?.target_cycle_service_level)+' / '+pct(r.metrics?.achieved_service_level)},{id:'status',title:'适用性 / 原生验证',value:r=>!writable&&r.method===appliedMethod&&originalOutcome?.business_audit==='PASS'?'本运行已核验':({SERVICE_ASSUMPTIONS_REQUIRED:'正态假设未确认',NOT_EXECUTED:'未进入原生计算',NOT_APPLICABLE:'不适用'} as Record<string,string>)[r.reason_code??r.status??r.native_feasibility]??r.reason_code??r.status??r.native_feasibility??'待核对'}]}/></div>
     {!!policyRows.length&&<><h4>{p.sourceOnly?'本次研究软目标':'本次可采用的原生软目标'}</h4><div className="intelligence-table" tabIndex={0} aria-label="本次原生库存目标，可横向滚动"><DataTable rows={policyRows} rowId={r=>r.item_id+'@'+r.location_id} columns={[{id:'item',title:'物料 / 地点',value:r=><>{r.item_id}<small>{r.location_id} · {r.uom}</small></>},{id:'min',title:'最低目标',numeric:true,value:r=>r.minimum},{id:'max',title:'补至目标',numeric:true,value:r=>r.maximum},{id:'range',title:'生效期间',value:r=><>{time(r.effective_from)}<small>至 {time(r.effective_to)}</small></>}]} onSelect={r=>p.onInventory(r.item_id,r.location_id)}/></div></>}
     {selectedInventory&&<details className="intelligence-evidence"><summary>波动、提前期、成本与来源依据</summary><pre>{JSON.stringify({status:selectedInventory.result?.status,reason:selectedInventory.result?.reason_code,lineage:selectedInventory.result?.lineage,stock_position:selectedInventory.result?.stock_position,costs:selectedInventory.result?.costs,algorithm_receipt:selectedInventory.result?.algorithm_receipt},null,2)}</pre></details>}
    </section>
   </>}

   {p.mode==='order-inquiry'&&<section className="intelligence-panel" aria-label="研究询价与供应占用"><h3>询价、报价与研究占用</h3><p>询价不占用供应。有效保留与模拟确认保留精确来源和到期时刻，仅作用于研究方案。</p>
    {!quoteWritable&&<p role="status">当前运行状态 {p.runState}；需完成本方案原生计算并通过业务审计后，才能登记、计算或变更研究询价。</p>}
    <div className="intelligence-table" tabIndex={0} aria-label="询价生命周期，可横向滚动"><DataTable rows={quotes} rowId={r=>r.id} selected={quoteId} onSelect={r=>setQuoteId(r.id)} empty="尚无研究询价。填写下方请求后可计算供应。" columns={[{id:'id',title:'询价',value:r=>r.id},{id:'item',title:'产品 / 客户',value:r=><>{r.request?.item_id}<small>{r.request?.customer_id} · {r.request?.location_id}</small></>},{id:'quantity',title:'请求数量',numeric:true,value:r=>text(r.request?.quantity)+' '+text(r.request?.uom)},{id:'due',title:'要求时间',value:r=>time(r.request?.due_at)},{id:'state',title:'当前状态',value:r=>stateNames[r.state]??r.state},{id:'promise',title:'可承诺研究数量',numeric:true,value:r=>text(r.calculation?.promised_quantity)},{id:'expiry',title:'占用到期',value:r=>r.expires_at?time(r.expires_at):'未占用'}]}/></div>
    {selectedQuote&&<div className="intelligence-subsection"><h4>{selectedQuote.id} · {stateNames[selectedQuote.state]??selectedQuote.state}</h4><Definition rows={[["供应计算状态",text(quoteView.status)],["可用数量",text(quoteView.available_quantity)],["研究可承诺数量",text(quoteView.promised_quantity)],["缺量",text(quoteView.shortfall_quantity)]]}/>
     <div className="intelligence-table" tabIndex={0} aria-label="可承诺供应精确来源，可横向滚动"><DataTable rows={array(quoteView.source_slices)} rowId={r=>r.event_id??r.id??r.source_id??JSON.stringify(r)} columns={[{id:'source',title:'供应来源',value:r=>r.event_id??r.source_id??r.id??r.source_ref},{id:'kind',title:'来源类型',value:r=>r.kind??r.source_kind},{id:'quantity',title:'分配量',numeric:true,value:r=>text(r.quantity)},{id:'at',title:'可用时刻',value:r=>time(r.available_at??r.at)},{id:'ref',title:'来源记录',value:r=>text(r.source_refs??r.source_ref)}]} empty="尚无可采用的供应切片。"/></div>
     {selectedQuote.state==='OFFERED'&&<label className="intelligence-selector">研究占用到期时刻<input disabled={busy||!quoteWritable} type="datetime-local" value={expiry} onChange={e=>setExpiry(e.target.value)}/></label>}
     <div className="intelligence-actions">{quoteActions(selectedQuote.state).map(name=><Button key={name} className={name==='quote_calculate'?'primary':''} disabled={busy||!quoteWritable||(name!=='quote_release'&&selectedQuote.identity?.target!==p.scope.target)||(name==='quote_offer'&&Number(quoteView.promised_quantity??0)<=0)||(name==='quote_hold'&&!expiry)} onClick={()=>{try{void action(name,{quote_id:quoteId,...(name==='quote_hold'?{expires_at:siteTimestamp(expiry,timezone)}:{})})}catch(e){showError(e)}}}>{actionNames[name]}</Button>)}<Button disabled={busy||!quoteWritable} onClick={()=>void action('quote_expire',{})}>核对并处理到期占用</Button></div>
     <details className="intelligence-evidence"><summary>请求、候选供应与状态历史</summary><pre>{JSON.stringify({request:selectedQuote.request,identity:selectedQuote.identity,snapshot_sha256:selectedQuote.snapshot_sha256,candidate_source_slices:quoteView.candidate_source_slices,events:selectedQuote.events},null,2)}</pre></details>
    </div>}
   </section>}

   <section className="intelligence-panel intelligence-joint" aria-label="联合原生规划"><h3>{p.mode==='order-inquiry'?'新增请求与有限制造试算':'采用研究条件，进入联合原生规划'}</h3>{p.sourceOnly&&<p>研究记录独立保存；采用条件请切换到联合规划，仍须通过原始基线身份及业务审计检查。</p>}
    <fieldset disabled={busy||p.sourceOnly}><fieldset disabled={!writable}><div className="intelligence-checks"><label><input type="checkbox" checked={useNet} onChange={e=>setUseNet(e.target.checked)}/>已确认预测与抵扣</label><label><input type="checkbox" checked={useInventory} onChange={e=>setUseInventory(e.target.checked)}/>库存政策候选</label><label><input type="checkbox" checked={useInquiry} onChange={e=>setUseInquiry(e.target.checked)}/>新增客户需求</label></div>
     {useNet&&<div className="intelligence-subsection"><h4>预测版本与有界消费</h4><div className="intelligence-fields"><label>有效预测版本<Select label="联合规划预测版本" value={versionId} onChange={selectForecastVersion} options={versions.filter(v=>data.netting_templates?.[v.id]).map(v=>({value:v.id,label:v.id}))}/></label><label>向前消费窗口（天）<input type="number" min="0" max="366" step="1" value={backward} onChange={e=>setBackward(e.target.value)}/></label><label>向后消费窗口（天）<input type="number" min="0" max="366" step="1" value={forward} onChange={e=>setForward(e.target.value)}/></label></div><p>{template?'当前模板绑定已确认版本，按产品、地点、客户与单位抵扣。':'尚未选择有效确认版本，请先在预测工作区确认。'}</p></div>}
     {useInventory&&<div className="intelligence-subsection"><h4>采用库存政策</h4><label className="intelligence-selector">库存候选<Select label="联合规划库存候选" value={inventoryId} onChange={setInventoryId} options={inventories.filter(v=>v.native_policy).map(v=>({value:v.id,label:v.id+' · '+v.source_id}))}/></label><p>{selectedInventory?.native_policy?text(selectedInventory.native_policy.policy_version):'尚无可采用的补货候选，请先计算库存策略。'}</p></div>}
    </fieldset>
     {(useInquiry||p.mode==='order-inquiry')&&<fieldset className="intelligence-subsection" disabled={!quoteWritable}><h4>明确客户请求</h4><div className="intelligence-fields"><label>产品<Select label="询价产品" value={itemId} onChange={id=>{setItemId(id);const item=items.find(r=>(r.id??r.item_id)===id);setUom(item?.base_uom??item?.uom??'');setLocationId('')}} options={items.map(r=>({value:r.id??r.item_id,label:(r.id??r.item_id)+(r.name?' · '+r.name:'')}))}/></label><label>客户<Select label="询价客户" value={customerId} onChange={setCustomerId} options={customers.map(r=>({value:r.id??r.customer_id,label:(r.id??r.customer_id)+(r.name?' · '+r.name:'')}))}/></label><label>交付地点<Select label="询价交付地点" value={locationId} onChange={setLocationId} options={locationOptions.map(id=>({value:id,label:id}))}/></label><label>数量<input type="number" min="0.000001" step="any" value={quantity} onChange={e=>setQuantity(e.target.value)}/></label><label>单位<input value={uom} readOnly={!!selectedItem?.base_uom||!!selectedItem?.uom} onChange={e=>setUom(e.target.value)}/></label><label>客户要求时间<input type="datetime-local" value={due} onChange={e=>setDue(e.target.value)}/></label><label>供应范围<Select label="询价供应范围" value={supplyPolicy} onChange={setSupplyPolicy} options={[{value:'CONFIRMED_ONLY',label:'ATP · 仅已有确认供应'},{value:'RESEARCH_CTP',label:'CTP · 研究候选供应'}]}/></label><label className="intelligence-checkbox"><input type="checkbox" checked={partial} onChange={e=>setPartial(e.target.checked)}/>接受有来源的分批交付</label></div>
      {p.mode==='order-inquiry'&&<div className="intelligence-actions"><Button disabled={busy||!quoteWritable||!itemId||!customerId||!locationId||!quantity||!due} onClick={()=>{try{void action('quote_create',{request:inquiryRequest()})}catch(e){showError(e)}}}>登记研究询价</Button><span>有限制造能力需采用相同请求创建原生候选并实际计算。</span></div>}
     </fieldset>}
     <fieldset disabled={!writable}><label className="intelligence-reason">联合试算理由<textarea value={reason} maxLength={2000} onChange={e=>setReason(e.target.value)}/></label><div className="intelligence-actions"><Button className="primary" disabled={busy||p.sourceOnly||!writable||!reason.trim()} onClick={()=>void nativeAction('preview')}>预览联合计划条件</Button></div></fieldset>
    </fieldset>
    {pv&&<div className="intelligence-preview"><h4>本次预览</h4><Badge kind={pv.worker_eligible?'':'risk'}>{pv.worker_eligible?'条件可进入原生试算':'条件尚不能运行'}</Badge>{pv.blocking_code&&<p>{pv.blocking_code}</p>}{!!array(pv.changes?.netting?.rows).length&&<div className="intelligence-table" tabIndex={0} aria-label="联合预览Gross Consumed Net，可横向滚动"><DataTable rows={array(pv.changes.netting.rows)} rowId={r=>r.forecast_id??r.id} columns={[...objectColumns,{id:'g',title:'Gross',numeric:true,value:r=>r.gross_quantity},{id:'c',title:'Consumed',numeric:true,value:r=>r.consumed_quantity},{id:'n',title:'Net',numeric:true,value:r=>r.net_quantity}]}/></div>}<details className="intelligence-evidence"><summary>条件与来源核对</summary><pre>{JSON.stringify(pv,null,2)}</pre></details>{pv.worker_eligible&&<><label className="intelligence-checkbox"><input type="checkbox" checked={ack} onChange={e=>setAck(e.target.checked)}/>确认使用上述来源与研究条件创建独立草案。</label><Button className="primary" disabled={!ack||busy} onClick={()=>void nativeAction('draft')}>创建尚未运行的研究草案</Button></>}</div>}
   </section>

   {originalOutcome&&<section className="intelligence-panel" aria-label="本次原生规划结果"><h3>当前运行的联合业务结果</h3>{originalOutcome.failure_code&&<details className="intelligence-evidence"><summary>计算失败原因</summary><pre>{text(originalOutcome.failure_code)}</pre></details>}<Definition rows={[["运行结论",text(originalOutcome.status)],["原生业务审计",text(originalOutcome.business_audit)],["原需求逐行保护",text(protection?.status)]]}/>{originalOutcome.inquiry&&<Definition rows={[["新增请求评估",text(originalOutcome.inquiry.status)],["要求日期前可排数量",text(originalOutcome.inquiry.planned_by_requested_due)],["未排数量",text(originalOutcome.inquiry.unscheduled_quantity)]]}/>}<div className="intelligence-actions"><Button onClick={p.onCompare}>比较交付、采购与库存</Button><Button onClick={p.onReview}>查看非执行评审</Button></div>{protection&&<details className="intelligence-evidence"><summary>原需求逐行交付保护</summary><div className="intelligence-table" tabIndex={0} aria-label="原需求逐行保护，可横向滚动"><DataTable rows={array(protection.impacts)} rowId={r=>r.demand_id} columns={[{id:'id',title:'原需求',value:r=>r.demand_id},{id:'base',title:'原计划数量',numeric:true,value:r=>r.base_planned},{id:'new',title:'新计划数量',numeric:true,value:r=>r.candidate_planned},{id:'due',title:'交期前数量',value:r=>text(r.base_by_due)+' → '+text(r.candidate_by_due)},{id:'delta',title:'变化',numeric:true,value:r=>r.delta}]}/></div></details>}</section>}
  </>}
 </section>;
}
