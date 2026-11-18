import {useEffect,useMemo,useState} from 'react';
import {useSearchParams} from 'react-router';
import {request} from '../api/client';
import type {Dict} from '../api/contracts';
import type {PlanScope} from '../app/scope';
import {Button,Definition,ErrorNote,Loading} from '../components/Controls';
import {DataTable,type TableColumn} from '../components/DataTable';
import {ConfirmedPurchases} from './ConfirmedPurchases';
import {supports,capabilityReason} from './capabilityModel';
import {analysisUnit,eventLabels,statusMessage} from './analysisModel';
import {formatSiteTime} from './timeRange';
import {qty} from './planModel';
import {supplyWorkRows,filterSupplyRows,supplyTagLabels,supplyScopeKey,supplySelection,supplyContextSearch,confirmedDocumentRows,supplyActionReason,materialSources,finiteSupplyQuantity,verifySupplyWorkbench,type SupplyWorkRow} from './supplyWorkModel';
import './supply-workbench.css';
import {ReceiptProtectionPanel} from './ReceiptProtectionPanel';
import {receiptProtectionProfile} from './receiptProtectionModel';
import {PurchaseWork} from './PurchaseWork';
import {purchaseMaterialContext} from './purchaseWorkModel';

type Props={planId:string;versionId:string;runId:string;target:string;baselineRunId:string;scope:PlanScope;input:Dict;sourceSha256?:string;snapshotSha256?:string;onTarget:(id:string,context?:URLSearchParams)=>void;onAccessory:()=>void;onAnalysis:(mode:'inventory'|'exceptions',item?:string,location?:string)=>void;onReview:()=>void;onManufacturing:(reference:string)=>void;onOperation?:(taskId:string)=>void;onResource:(id:string)=>void};
const ready=new Set(['PRESENT','EMPTY_CHECKED']);
const amount=(value:unknown,uom:string)=>{const n=finiteSupplyQuantity(value);return n===null?'未知':qty(n)+' '+analysisUnit(uom)};
function Evidence({status}:{status:string}){return <span className={'supply-evidence '+(!ready.has(status)?'incomplete':'')}>{statusMessage(status)}</span>}
export function SupplyOpeningLedger({row}:{row:SupplyWorkRow}){return <><div className="supply-opening-ledger"><Definition rows={[["业务开账物理量",amount(row.physicalOpening,row.uom)],["开账质量冻结",amount(row.frozenOpening,row.uom)],["开账待检",amount(row.openingInspectionPending,row.uom)],["开账其他不可用",amount(row.otherNonnettable,row.uom)],["开账净可用",amount(ready.has(row.status)?row.series?.opening:null,row.uom)],["本期间末采购待QC",amount(row.qcWaiting,row.uom)]]}/></div><p className="supply-work-note"><Evidence status={row.status}/>　物理量、冻结、待检及其他不可用为业务开账事实，不代表每个期间重新入库；开账待检不代表未来释放，不与期间末采购待QC相加。库存为本物料地点全量，不是指定订单分配量。</p></>}

export function SupplyWorkbench(props:Props){
 if(props.input.rules?.manufacturing_profile===receiptProtectionProfile)return <ReceiptProtectionPanel scope={{planId:props.planId,versionId:props.versionId,runId:props.runId,target:props.target,baselineRunId:props.baselineRunId,sourceSha256:props.sourceSha256,snapshotSha256:props.snapshotSha256}} input={props.input} onTarget={props.onTarget} onManufacturing={props.onManufacturing} onResource={props.onResource}/>;
 return <LegacySupplyWorkbench {...props}/>;
}
function LegacySupplyWorkbench({planId,versionId,runId,target,baselineRunId,scope,input,onTarget,onAccessory,onAnalysis,onReview,onOperation}:Props){
 const[search,setSearch]=useSearchParams(),[response,setResponse]=useState<{key:string;report:Dict;analysis:Dict}|null>(null),[failure,setFailure]=useState<{key:string;message:string}|null>(null),[retry,setRetry]=useState(0);
 const grain=search.get('supply_grain')==='week'?'week':'day',view=search.get('supply_view')==='purchase'?'purchase':'material';
 const bound={planId,versionId,runId,target},key=supplyScopeKey(bound),requestKey=key+':'+grain,data=response?.key===requestKey?response:null;
 const query=search.get('supply_query')??'',attention=search.get('supply_attention')??'attention',period=search.get('supply_period')??'';
 useEffect(()=>{
  const controller=new AbortController(),params=new URLSearchParams({data_version_id:versionId,run_id:runId,target});setFailure(null);
  Promise.all([request<Dict>('/v1/enterprise-plans/'+planId+'/reports?'+params+'&grain='+grain,{signal:controller.signal}),request<Dict>('/v1/enterprise-plans/'+planId+'/analysis?'+params,{signal:controller.signal})]).then(([report,analysis])=>{
   verifySupplyWorkbench(report,analysis,bound);if(!controller.signal.aborted)setResponse({key:requestKey,report,analysis});
  }).catch((error:unknown)=>{if(!controller.signal.aborted)setFailure({key:requestKey,message:error instanceof Error?error.message:'当前运行资料读取失败。'})});return()=>controller.abort();
 },[planId,versionId,runId,target,grain,retry]);
 const allRows=useMemo(()=>data?supplyWorkRows(data.report,data.analysis,input):[],[data,input]);
 const rows=useMemo(()=>filterSupplyRows(allRows,query,attention,period),[allRows,query,attention,period]);
 function filter(changes:Record<string,string>){setSearch(previous=>{const p=new URLSearchParams(previous);for(const[k,v]of Object.entries(changes)){if(v)p.set(k,v);else p.delete(k)}for(const k of ['supply_doc','supply_event','supply_bucket','supply_doc_period'])p.delete(k);p.set('supply_scope',key);return p})}
 function pickRow(row:SupplyWorkRow){setSearch(previous=>{const p=supplyContextSearch(previous,bound);p.set('supply_item',row.itemId);p.set('supply_location',row.locationId);p.set('supply_bucket',row.startAt);p.delete('supply_doc_period');p.delete('supply_doc');p.delete('supply_event');return p})}
 function pickRecord(kind:'doc'|'event',id:string){setSearch(previous=>{const p=supplyContextSearch(previous,bound);p.set('supply_'+kind,id);return p})}
 const selectedItem=search.get('supply_item'),selectedLocation=search.get('supply_location'),selectedBucket=search.get('supply_bucket');
 // A scoped business-date link can use UTC or the site's offset for the same instant.
 const selected=search.get('supply_doc_period')==='UNRESOLVED'?undefined:rows.find(row=>(!selectedItem||row.itemId===selectedItem)&&(!selectedLocation||row.locationId===selectedLocation)&&(!selectedBucket||Date.parse(row.startAt)===Date.parse(selectedBucket)))??(!selectedItem&&!selectedLocation?rows[0]:undefined);
 const selectedIds=supplySelection(search,bound);
 if(failure?.key===requestKey)return <section className="supply-workbench" aria-label="物料与采购工作区"><h2>物料与采购</h2><ErrorNote error={failure.message}/><p>没有借用其他版本或运行补齐当前结果。</p><Button onClick={()=>setRetry(value=>value+1)}>重新读取当前运行</Button></section>;
 if(!data)return <Loading/>;
 const {report,analysis}=data,tz=report.timezone??input.site?.timezone??'Asia/Shanghai',time=(value:unknown)=>typeof value==='string'&&Number.isFinite(Date.parse(value))?formatSiteTime(value,tz):'未知';
 const profile=analysis.identity.profile,inventory=analysis.inventory_analysis,knownFail=report.known_business_fail||inventory?.validation?.status==='FAIL';
 const periods=[...new Map(allRows.map(row=>[row.startAt,{start:row.startAt,end:row.endAt}])).values()];
 const docs=(report.documents?.documents??[]).filter((doc:Dict)=>selected&&doc.item_id===selected.itemId&&doc.location_id===selected.locationId);
 const document=docs.find((doc:Dict)=>doc.id===selectedIds.documentId);
 const events=(selected?.series?.events??[]).filter((event:Dict)=>selected&&Date.parse(event.at)>=Date.parse(selected.startAt)&&Date.parse(event.at)<Date.parse(selected.endAt));
 const event=events.find((row:Dict)=>row.id===selectedIds.eventId);
 const sources=selected?materialSources(input,selected.itemId,selected.locationId):[];
 const confirmedRows=confirmedDocumentRows(document?[document]:docs);
 const actionReason=scope.planId!==planId||scope.target!==target?'当前操作范围与读取的计划或方案不一致，请返回计划重新打开。':supplyActionReason(bound,baselineRunId,profile)||(!ready.has(report.documents?.status)?'当前运行的供应单据未取得，不能创建延期假设。':'');
 const option=input.accessory_supply_option,accessorySource=(input.supply_sources??[]).find((source:Dict)=>source.id===option?.source_id);
 const isAccessory=!!selected&&accessorySource?.item_id===selected.itemId&&accessorySource?.location_id===selected.locationId;
 const accessoryReason=!supports(profile,'actions','accessory_supply')?capabilityReason(profile,'actions','accessory_supply'):target!=='baseline'?'当前为候选，请切回原始基线创建另一项独立来源假设。':runId!==baselineRunId?'当前运行不是基线运行。':!isAccessory?'当前物料没有已认证的辅料补给动作。':option?.enabled!==false?'此模拟来源已启用，不能重复启用。':'';
 const columns:TableColumn<SupplyWorkRow>[]=[
  {id:'item',title:'物料 / 地点',sortValue:row=>row.itemId+' '+row.locationId,value:row=><>{row.name}<small>{row.itemId} · {row.locationId}</small></>},
  {id:'period',title:'业务期间',sortValue:row=>row.startAt,value:row=><>{time(row.startAt)}<small>至 {time(row.endAt)} 前</small></>},
  {id:'opening',title:'期初净可用',numeric:true,sortValue:row=>row.opening??Number.NEGATIVE_INFINITY,value:row=>amount(row.opening,row.uom)},
  {id:'inflow',title:'可用入流',numeric:true,sortValue:row=>row.inflow??Number.NEGATIVE_INFINITY,value:row=>amount(row.inflow,row.uom)},
  {id:'outflow',title:'计划耗用',numeric:true,sortValue:row=>row.outflow??Number.NEGATIVE_INFINITY,value:row=>amount(row.outflow,row.uom)},
  {id:'ending',title:'期间末净可用',numeric:true,sortValue:row=>row.ending??Number.NEGATIVE_INFINITY,value:row=>amount(row.ending,row.uom)},
  {id:'attention',title:'关注依据',value:row=><div className="supply-tags">{row.tags.length?row.tags.map(tag=><span key={tag} className={'supply-tag '+tag}>{supplyTagLabels[tag]}</span>):<span className="supply-neutral">无已证实的关注项</span>}</div>}
 ];
 const docColumns:TableColumn<Dict>[]=[
  {id:'document',title:'单据 / 来源',value:row=><>{row.reference??row.id}<small>{row.source_id??'来源未提供'}{row.supplier_id?' · '+row.supplier_id:''}</small></>},
  {id:'state',title:'业务性质',value:row=><>{row.origin==='EXISTING'?'已有企业单据':'规划建议'}<small>{row.kind} · {row.status??'状态未知'}</small></>},
  {id:'quantity',title:'单据全量',numeric:true,value:row=>amount(row.quantity,row.uom)},
  {id:'receipt',title:'计划物理到货 / 产出',value:row=>time(row.at)},
  {id:'available',title:'计划QC后可用',value:row=>row.kind==='PO'?time(row.available_at):'不适用'}
 ];
 const eventColumns:TableColumn<Dict>[]=[
  {id:'time',title:'时点 / 原生顺序',value:row=><>{time(row.at)}<small>同刻序号 {row.native_sequence??'未提供'}</small></>},
  {id:'event',title:'物料事件',value:row=><>{eventLabels[row.kind]??row.kind}<small>{row.reference??'原生引用未提供'}</small></>},
  {id:'quantity',title:'增减数量',numeric:true,value:row=>(Number(row.quantity)>0?'+':'')+amount(row.quantity,selected?.uom??'')},
  {id:'balance',title:'事件后净可用',numeric:true,value:row=>amount(row.balance,selected?.uom??'')},
  {id:'state',title:'原单 / 原生事件状态',value:row=><>{row.order_status??'未知'}<small>{row.native_flow_status??'未提供'}</small></>}
 ];
 return <section className="supply-workbench" aria-label="物料与采购工作区">
  <header className="supply-work-heading"><div><h2>物料与采购</h2><p>从期间净供需，进入来源、单据与真实物料事件。</p></div><div className="supply-work-actions"><Button onClick={()=>onAnalysis('exceptions',selected?.itemId,selected?.locationId)}>查看异常与约束</Button><Button onClick={onReview}>进入方案评审</Button></div></header>
  <div className="supply-current-scope"><span>{target==='baseline'?'原始基线':'隔离模拟方案'}</span><span>{tz}</span><details><summary>当前计划与运行</summary><Definition rows={[["计划",planId],["企业版本",versionId],["当前运行",runId],["方案",target],["认证模型",profile]]}/></details></div>
  <div className="supply-view-controls"><label>作业视角<select aria-label="物料与采购视角" value={view} onChange={e=>filter({supply_view:e.target.value})}><option value="material">物料期间与事件</option><option value="purchase">采购期间与单据</option></select></label><label>期间粒度<select aria-label="物料采购期间粒度" value={grain} onChange={e=>filter({supply_grain:e.target.value,supply_period:'',supply_bucket:'',purchase_period:'',purchase_group:'',purchase_doc:''})}><option value="day">按日</option><option value="week">按周</option></select></label></div>
  {knownFail&&<p className="supply-hard-fail" role="alert">当前运行存在已知业务违规（FAIL）。以下保留原始数量与证据；引擎完成不代表计划合法。</p>}
  {!knownFail&&inventory?.validation?.status!=='PASS'&&<p className="supply-pending">物料业务核验尚未完成（{inventory?.validation?.status??'NOT_CHECKED'}）。已读取的事件不等于已完成整体合法性核验。</p>}
  {!ready.has(report.inventory?.status)&&<p className="supply-pending"><Evidence status={report.inventory?.status??'NOT_COLLECTED'}/>{report.inventory?.reason??inventory?.reason}</p>}
  {view==='purchase'?<PurchaseWork report={report} input={input} scope={bound} search={search} onSearch={next=>setSearch(next)} onMaterial={row=>setSearch(purchaseMaterialContext(search,bound,row,report.inventory?.rows??[]))}/>:<>
  <form className="supply-work-filters" onSubmit={e=>e.preventDefault()}>
   <label>查找物料 / 地点<input type="search" aria-label="物料采购查询" value={query} placeholder="名称、物料号或地点" onChange={e=>filter({supply_query:e.target.value,supply_item:'',supply_location:''})}/></label>
   <label>关注范围<select aria-label="物料关注范围" value={attention} onChange={e=>filter({supply_attention:e.target.value,supply_item:'',supply_location:''})}><option value="attention">有事件或关注</option><option value="all">全部已核对期间</option><option value="qc">采购待QC / 开账待检</option><option value="frozen">期初冻结</option><option value="flow">物料流转</option><option value="short">同物料需求欠量</option><option value="negative">已核对负库存</option><option value="evidence">证据未完成</option></select></label>
   <label>业务期间<select aria-label="物料业务期间" value={period} onChange={e=>filter({supply_period:e.target.value,supply_item:'',supply_location:''})}><option value="">全规划期 · {grain==='week'?'按周':'按日'}列示</option>{periods.map(value=><option key={value.start} value={value.start}>{time(value.start).slice(0,10)}</option>)}</select></label>
   <Button onClick={()=>filter({supply_query:'',supply_attention:'all',supply_period:'',supply_item:'',supply_location:''})}>显示全部</Button>
  </form>
  <details className="supply-reading-note"><summary>期间余额与关注项含义</summary><p className="supply-work-note">每行是一个物料、地点和期间，期末指结束时点之前。零余额本身不判为短缺；需求欠量只指同物料需求侧结果，不代表采购根因或订单分配。</p></details>
  <div className="supply-list-scroll" tabIndex={0} aria-label="物料期间工作清单，可横向滚动"><DataTable rows={rows} columns={columns} rowId={row=>row.id} selected={selected?.id} onSelect={pickRow} empty={ready.has(report.inventory?.status)?'当前筛选没有已核对的期间记录。':'物料证据未完成，不能按零库存显示。'}/></div>
  {search.get('supply_doc_period')==='UNRESOLVED'&&<p className="supply-pending">此采购单据未能唯一对应当前期间的物料事件。请先在清单选择期间；原采购单据仍可在采购视角查看。</p>}{rows.length>0&&!selected&&<p className="supply-pending">原选中的物料、地点或期间不在当前筛选与运行内。请选择清单中的真实记录。</p>}
  {selected&&<section className="supply-object-workspace" aria-label="选中物料与供应调查">
   <div className="supply-object-main"><header className="supply-object-title"><div><h3>{selected.name}</h3><span>{selected.itemId} @ {selected.locationId}</span></div><Button onClick={()=>onAnalysis('inventory',selected.itemId,selected.locationId)}>打开库存时间账</Button></header>
    <SupplyOpeningLedger row={selected}/>
    <section className="supply-documents"><h4>当前物料的全规划期单据</h4><p className="supply-work-note">确认采购与规划建议分别列示。单据全量不等于实际耗用、指定订单分配或已经执行。</p><Evidence status={report.documents?.status??'NOT_COLLECTED'}/><div className="supply-detail-scroll" tabIndex={0} aria-label="供应与制造单据，可横向滚动"><DataTable rows={docs} columns={docColumns} rowId={row=>row.id} selected={document?.id} onSelect={row=>pickRecord('doc',row.id)} empty={ready.has(report.documents?.status)?'已查询，本物料地点没有对应单据。':'单据证据未取得，不能理解为没有供应。'}/></div></section>
    <section className="supply-events"><h4>所选期间的原生物料事件</h4><p className="supply-work-note">{time(selected.startAt)} 至 {time(selected.endAt)} 前。同一时刻保留原生先后顺序，初始化库存不重复计为新增供应。</p><div className="supply-detail-scroll" tabIndex={0} aria-label="物料事件与余额，可横向滚动"><DataTable rows={events} columns={eventColumns} rowId={row=>row.id} selected={event?.id} onSelect={row=>pickRecord('event',row.id)} empty={ready.has(selected.status)?'已查询，所选期间没有对应原生事件。':'事件未采集，不显示为零耗用或零流入。'}/></div></section>
   </div>
   <aside className="supply-work-inspector" aria-label="供应对象与受控操作"><h3>来源与单据检查</h3>
    {sources.length?sources.map((source:Dict)=><section key={source.id} className="supply-source"><h4>{(input.suppliers??[]).find((s:Dict)=>s.id===source.supplier_id)?.name??source.supplier_id}</h4><Definition rows={[["来源",source.id],["最小采购量",amount(source.minimum_order_quantity,selected.uom)],["采购倍数",amount(source.order_multiple,selected.uom)],["日历提前期",source.lead_time_seconds==null?'未提供':qty(Number(source.lead_time_seconds)/3600)+' 小时'],["到货后QC等待",source.receiving_qc?.wait_seconds==null?'未提供':qty(Number(source.receiving_qc.wait_seconds)/3600)+' 小时']]}/><details><summary>来源条款与业务出处</summary><pre className="raw-json">{JSON.stringify(source,null,2)}</pre></details></section>):<p>当前物料没有声明的采购来源。未由同名物料或BOM推测补给路径。</p>}
    {document&&<div className="supply-record-inspector"><h4>{document.origin==='EXISTING'?'已有企业单据':'当前运行规划建议'}</h4><Definition rows={[["单据引用",document.reference??document.id],["状态",document.status??'未知'],["全量",amount(document.quantity,document.uom)],["物理到货 / 产出",time(document.at)],["QC后可用",document.kind==='PO'?time(document.available_at):'不适用']]}/>{document.origin!=='EXISTING'&&<p className="supply-work-note">建议不等于采购承诺；其供应认证与整体业务合法性需分别核对。</p>}<details><summary>选中单据及来源</summary><pre className="raw-json">{JSON.stringify(document,null,2)}</pre></details></div>}
    {event&&<div className="supply-record-inspector"><h4>选中的原生事件</h4><Definition rows={[["事件",eventLabels[event.kind]??event.kind],["引用",event.reference??'未提供'],["时点",time(event.at)],["事件后净可用",amount(event.balance,selected.uom)],...(event.physical_receipt_at?[["计划采购物理到货",time(event.physical_receipt_at)],["计划QC后可用",time(event.available_at)]] as [string,string][]:[])]}/>{event.kind==='MANUFACTURE_CONSUMPTION'&&event.operation_task_id&&onOperation&&<Button onClick={()=>onOperation(event.operation_task_id)}>查看实际耗用工序</Button>}<details><summary>原生事件与来源SHA</summary><pre className="raw-json">{JSON.stringify(event,null,2)}</pre></details></div>}
    <section className="supply-controlled-actions"><h4>当前对象可用的规划动作</h4>{confirmedRows.length>0?<>{actionReason?<><Button disabled>模拟确认采购延期</Button><p className="supply-work-note">{actionReason}</p>{target!=='baseline'&&<Button onClick={()=>onTarget('baseline',supplyContextSearch(search,{...bound,target:'baseline',runId:baselineRunId}))}>返回原始基线</Button>}</>:<ConfirmedPurchases compact scope={scope} baselineRunId={baselineRunId} dataVersion={versionId} rows={confirmedRows} timezone={tz} profile={profile} onTarget={id=>onTarget(id,supplyContextSearch(search,{...bound,target:id,runId:''}))}/>}</>:<p className="supply-work-note">未选中有明确身份、来源和到货/QC时点的已有确认采购，不能从此对象创建采购延期。</p>}
     <Button disabled={!!accessoryReason} onClick={onAccessory}>调查并模拟辅料补给</Button>{accessoryReason&&<p className="supply-work-note">{accessoryReason}</p>}{isAccessory&&!accessoryReason&&<p className="supply-work-note">进入已有来源预览，保留原确认采购、MOQ、全期能力和QC条件；不会自动创建供应或批准采购。</p>}
    </section>
    <details><summary>本运行核验与文件SHA</summary><pre className="raw-json">{JSON.stringify({identity:analysis.identity,status:inventory?.status,validation:inventory?.validation,period:selected.raw,series_sources:selected.series?.sources,evidence_files:analysis.evidence_files},null,2)}</pre></details>
   </aside>
  </section>}
  </>}
 </section>;
}
