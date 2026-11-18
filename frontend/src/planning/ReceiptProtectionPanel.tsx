import {useEffect,useRef,useState} from 'react';
import {useNavigate,useSearchParams} from 'react-router';
import type {Dict} from '../api/contracts';
import {request} from '../api/client';
import {useActionGuard} from '../app/useActionGuard';
import {Badge,Button,Definition,ErrorNote,Loading} from '../components/Controls';
import {DataTable,type TableColumn} from '../components/DataTable';
import {ProfessionalGrid,type GridEvidence} from '../components/ProfessionalGrid';
import {analysisContextSearch,analysisUnit} from './analysisModel';
import {formatSiteTime} from './timeRange';
import {receiptEvidenceText,receiptNumber,receiptObservedManufacturing,receiptQualityLabel,receiptQuantityText,receiptRequest,receiptReviewSearch,receiptStageDemand,receiptTargetSearch,updateReceiptEditor,verifyReceiptDraft,verifyReceiptPreview,verifyReceiptProtectionReport,type ReceiptEditor,type ReceiptProtectionScope} from './receiptProtectionModel';
import './receipt-protection.css';

export type ReceiptProtectionProps={scope:ReceiptProtectionScope;input:Dict;onTarget:(id:string,context?:URLSearchParams)=>void;onManufacturing:(reference:string)=>void;onResource:(id:string)=>void};
const nativeEventLabels:Record<string,string>={OPENING_INITIALIZATION:'原生期初初始化（非新增供应）',PURCHASE_RECEIPT:'确认采购QC后可用',QC_RELEASE_OF_RECEIVED_STOCK:'已收库存明确QC放行',MANUFACTURING_OUTPUT:'制造产出',CONSUMPTION_OR_DELIVERY:'制造领用 / 需求消耗'};
const label=(value:unknown)=>value===null||value===undefined||value===''?'未提供':String(value);
const amount=(value:unknown,uom:unknown)=>receiptNumber(value)===null?'未核验':receiptQuantityText(value)+' '+analysisUnit(typeof uom==='string'?uom:undefined);
const statusKind=(status:unknown)=>status==='FAIL'?'bad':status==='PASS'?'good':'risk';
function Audit({value}:{value:Dict|undefined}){return <Badge kind={statusKind(value?.status)}>{receiptEvidenceText(value?.status??'NOT_CHECKED')}</Badge>}

export function ReceiptProtectionPanel({scope,input,onTarget,onManufacturing,onResource}:ReceiptProtectionProps){
 const navigate=useNavigate(),[search,setSearch]=useSearchParams(),[response,setResponse]=useState<{key:string;data:Dict}|null>(null),[failure,setFailure]=useState<{key:string;message:string}|null>(null),[retry,setRetry]=useState(0),[pending,setPending]=useState(''),[editorOpen,setEditorOpen]=useState(false);
 const[editor,setEditor]=useState<ReceiptEditor>({fields:{reference:'',newReceiptAt:'',reason:''},preview:null,acknowledged:false});
 const key=JSON.stringify(scope),data=response?.key===key?response.data:null,actionKey=JSON.stringify([key,editor.fields]),capture=useActionGuard(actionKey),busy=pending===actionKey,receipt=useRef<{key:string;id:string}|null>(null);
 useEffect(()=>{const c=new AbortController();setFailure(null);setResponse(null);setEditor({fields:{reference:'',newReceiptAt:'',reason:''},preview:null,acknowledged:false});setEditorOpen(false);
  request<Dict>('/v1/enterprise-plans/'+encodeURIComponent(scope.planId)+'/receipt-protection?'+new URLSearchParams({data_version_id:scope.versionId,run_id:scope.runId,target:scope.target}),{signal:c.signal}).then(value=>{if(!c.signal.aborted)setResponse({key,data:verifyReceiptProtectionReport(value,scope)})}).catch(error=>{if(!c.signal.aborted)setFailure({key,message:error instanceof Error?error.message:'当前收货与工单资料未能读取，请重试。'})});return()=>c.abort();
 },[key,retry]);
 const orders:Dict[]=data?.purchase_orders??[],poId=search.get('supply_po')??'',selected=orders.find(row=>row.id===poId)??(!poId?orders[0]:undefined),receipts:Dict[]=(data?.purchase_receipts??[]).filter((row:Dict)=>selected&&row.po_id===selected.id),tz=data?.timezone??input.site?.timezone??'Asia/Shanghai';
 const selectedReceipt=receipts.find(row=>row.receipt_reference===search.get('supply_receipt'));
 const gridEvidence:GridEvidence=data?.validation?.status==='FAIL'?'FAIL':data?.validation?.status==='PASS'?'PRESENT':'NOT_CHECKED';
 const sortNumber=(value:unknown)=>receiptNumber(value)??NaN,sortDate=(value:unknown)=>typeof value==='string'?Date.parse(value):NaN;
 const time=(value:unknown)=>typeof value==='string'&&Number.isFinite(Date.parse(value))?formatSiteTime(value,tz):'未提供 / 未确定';
 const prepared=data?receiptRequest(scope,data,editor.fields):{body:null,error:''},preview=editor.preview,writable=scope.target==='baseline'&&scope.runId===scope.baselineRunId;
 const material=data?.material_events??data?.native_events,events:Dict[]=material&&['PRESENT','EMPTY_CHECKED'].includes(material.status)&&Array.isArray(material.events)?material.events:[],moRows:Dict[]=data?.manufacturing_orders??[],stocks:Dict[]=data?.stock_facts??[];
 function edit(patch:Partial<ReceiptEditor['fields']>){setEditor(previous=>updateReceiptEditor(previous,patch));setFailure(null);receipt.current=null}
 function select(po:Dict){setSearch(previous=>{const p=new URLSearchParams(previous);p.set('supply_po',po.id);p.delete('supply_receipt');return p});setEditorOpen(false);edit({reference:po.id,newReceiptAt:po.expected_receipt_at,reason:''})}
 function prepare(){if(!selected)return;setSearch(previous=>{const p=new URLSearchParams(previous);p.set('supply_po',selected.id);return p});edit({reference:selected.id,newReceiptAt:selected.expected_receipt_at,reason:''});setEditorOpen(true);requestAnimationFrame(()=>document.getElementById('receipt-assumption')?.scrollIntoView({block:'start'}))}
 async function act(create:boolean){
  if(!prepared.body||!data||busy||(create&&(!preview||!editor.acknowledged)))return;
  const owns=capture(),body=prepared.body;setPending(actionKey);setFailure(null);
  try{const prefix='/v1/enterprise-plans/'+encodeURIComponent(scope.planId)+'/receipt-protection-';
   if(create&&preview){if(receipt.current?.key!==actionKey)receipt.current={key:actionKey,id:crypto.randomUUID().replaceAll('-','')};const result=await request<Dict>(prefix+'drafts',{method:'POST',body:{...body,source_sha256:preview.identity.source_sha256,fingerprint:preview.fingerprint,acknowledge_limits:true,request_id:receipt.current.id}});if(owns())onTarget(verifyReceiptDraft(result,scope,body,preview),receiptTargetSearch(search,result.draft.id))}
   else{setEditor(previous=>({...previous,preview:null,acknowledged:false}));const result=await request<Dict>(prefix+'preview',{method:'POST',body});if(owns())setEditor(previous=>({...previous,preview:verifyReceiptPreview(result,scope,body),acknowledged:false}))}
  }catch(error){if(owns()){setEditor(previous=>({...previous,preview:null,acknowledged:false}));setFailure({key,message:error instanceof Error?error.message:'未能核实预览或草案状态，请刷新计划。'})}}finally{if(owns())setPending('')}
 }
 const poColumns:TableColumn<Dict>[]=[
  {id:'reference',title:'确认采购 / 来源',sortValue:r=>r.external_ref??r.id,value:r=><><strong>{r.external_ref??r.id}</strong><small>{r.id} · {r.supply_source_id}</small></>},
  {id:'item',title:'物料 / 地点',sortValue:r=>r.item_id+' '+r.location_id,value:r=><>{r.item_id}<small>{r.location_id} · {r.supplier_id}</small></>},
  {id:'ordered',title:'原订购',numeric:true,sortValue:r=>sortNumber(r.ordered_quantity),value:r=>amount(r.ordered_quantity,r.uom)},
  {id:'received',title:'累计实收',numeric:true,sortValue:r=>sortNumber(r.received_quantity),value:r=>amount(r.received_quantity,r.uom)},
  {id:'remaining',title:'未收余额',numeric:true,sortValue:r=>sortNumber(r.remaining_quantity),value:r=>amount(r.remaining_quantity,r.uom)},
  {id:'physical',title:'余额预计物理到货',sortValue:r=>sortDate(r.expected_receipt_at),value:r=>time(r.expected_receipt_at)},
  {id:'available',title:'余额QC后可用',sortValue:r=>sortDate(r.available_at),value:r=>time(r.available_at)},
  {id:'status',title:'原单状态',sortValue:r=>r.source_status??'',value:r=><Badge>{r.source_status==='confirmed'?'已确认':label(r.source_status)}</Badge>}
 ];
 const receiptColumns:TableColumn<Dict>[]=[
  {id:'reference',title:'稳定收货引用',sortValue:r=>r.receipt_reference,value:r=><strong>{r.receipt_reference}</strong>},
  {id:'quantity',title:'物理实收',numeric:true,sortValue:r=>sortNumber(r.quantity),value:r=>amount(r.quantity,r.uom)},
  {id:'physical',title:'实际物理收货',sortValue:r=>sortDate(r.physical_receipt_at),value:r=>time(r.physical_receipt_at)},
  {id:'quality',title:'质量状态',sortValue:r=>receiptQualityLabel[r.quality_status]??'',value:r=><Badge kind={r.quality_status==='RELEASED'?'good':'risk'}>{receiptQualityLabel[r.quality_status]??'状态未核实'}</Badge>},
  {id:'release',title:'明确放行时间',sortValue:r=>sortDate(r.release_at),value:r=>time(r.release_at)}
 ];
 const moColumns:TableColumn<Dict>[]=[
  {id:'reference',title:'企业制造工单',value:r=><strong>{r.external_ref??r.id}<small>{r.id} · {r.item_id}</small></strong>},
  {id:'state',title:'状态 / 保护',value:r=><>{({confirmed:'已确认',approved:'已批准'} as Record<string,string>)[r.source_status]??label(r.source_status)}<small>{r.freeze_policy==='LOCKED'?'原窗口受保护':label(r.freeze_policy)}</small></>},
  {id:'quantity',title:'原工单数量',numeric:true,value:r=>amount(r.quantity,r.uom)},
  {id:'operations',title:'工序 / 资源 / 保护窗口',value:r=><>{(r.operations??[]).map((op:Dict,index:number)=><div key={index}>{op.operation_id} · {op.resource_id}<small>{time(op.start_at)} → {time(op.end_at)}</small></div>)}</>},
  {id:'observed',title:'本运行观察（非实际执行）',value:r=>{const observed=data?receiptObservedManufacturing(data,r.id):null;return observed?<>{amount(observed.order.quantity,observed.order.uom)} · {label(observed.order.source_status)}<small>{time(observed.order.start_at)} → {time(observed.order.end_at)}</small>{observed.loads.length?observed.loads.map((load:Dict,index:number)=><small key={index}>{load.resource_id} · 并行占用 {receiptQuantityText(load.resource_units)} · {time(load.start_at)} → {time(load.end_at)}</small>):<small>未取得精确资源占用观察，不按原窗口补造</small>}</>:<span>未取得此稳定工单的原生结果</span>}},
  {id:'open',title:'调查',value:r=><Button onClick={()=>onManufacturing(r.id)}>定位制造对象</Button>}
 ];
 const eventColumns:TableColumn<Dict>[]=[
  {id:'at',title:'实际事件时点 / 顺序',value:r=><>{time(r.at??r.date)}<small>同刻序号 {label(r.native_row_index??r.native_sequence??r.sequence)}</small></>},
  {id:'object',title:'物料 / 地点',value:r=><>{label(r.item_id??r.item)}<small>{label(r.location_id??r.location)}</small></>},
  {id:'kind',title:'事件性质 / 引用',value:r=><>{nativeEventLabels[r.event_kind]??label(r.kind??r.type??r.event_kind)}<small>{label(r.native_reference??r.reference??r.operationplan)}</small></>},
  {id:'quantity',title:'数量变化',numeric:true,value:r=>amount(r.quantity,r.uom??stocks.find((stock:Dict)=>stock.item_id===r.item_id&&stock.location_id===r.location_id)?.uom)},
  {id:'balance',title:'事件后净可用',numeric:true,value:r=>amount(r.balance??r.onhand,r.uom??stocks.find((stock:Dict)=>stock.item_id===r.item_id&&stock.location_id===r.location_id)?.uom)}
 ];
 function review(){setSearch(previous=>receiptReviewSearch(previous,scope))}
 function compare(){navigate('/plans/'+scope.planId+'/compare/'+scope.target+'?'+analysisContextSearch(search,scope.target).toString())}
 return <section className="supply-workbench receipt-protection" aria-label="采购收货与受保护制造作业">
  <header className="receipt-work-heading"><div><h2>物料与采购</h2><p>核对采购批次、已收质量状态与未收余额，再调查原制造安排。</p></div><div className="receipt-work-actions">{scope.target!=='baseline'&&<Button icon="compare" onClick={compare}>与原始基线比较</Button>}<Button onClick={review}>进入本方案评审</Button></div></header>
  <div className="receipt-current-scope"><Badge>{scope.target==='baseline'?'原始基线':'隔离模拟方案'}</Badge><span>{tz}</span><details><summary>当前计划、版本与运行来源</summary><Definition rows={[["计划",scope.planId],["企业版本",scope.versionId],["基线运行",scope.baselineRunId],["当前运行",scope.runId],["方案",scope.target]]}/>{data&&<pre className="raw-json">{JSON.stringify(data.identity,null,2)}</pre>}</details></div>
  <p className="receipt-note">{receiptStageDemand([{manufacturing_profile:input.rules?.manufacturing_profile}])?.description}</p>
  <ErrorNote error={failure?.key===key?failure.message:''}/>
  {!data?failure?.key===key?<Button onClick={()=>setRetry(n=>n+1)}>重新读取当前采购与工单</Button>:<Loading/>:<>
   {(data.validation?.status==='FAIL'||data.material_validation?.status==='FAIL'||data.protected_mo_validation?.status==='FAIL')&&<p className="receipt-hard-fail" role="alert">当前存在已知业务违规（FAIL）。保留原始工单与事件用于调查，不能作为合法执行方案。</p>}
   <nav className="receipt-section-navigation" aria-label="采购制造作业区域"><Button className="quiet" onClick={()=>document.getElementById('receipt-orders')?.scrollIntoView({block:'start'})}>采购批次与收货</Button><Button className="quiet" onClick={()=>document.getElementById('receipt-material')?.scrollIntoView({block:'start'})}>物料时间账</Button><Button className="quiet" onClick={()=>document.getElementById('receipt-protected-mo')?.scrollIntoView({block:'start'})}>受保护制造</Button></nav>
   <div className={'receipt-work-layout'+(editorOpen?' has-editor':'')}><div className="receipt-work-main">
    <section id="receipt-orders" className="receipt-business-section"><div className="receipt-section-heading"><h3>确认采购：已收事实与未收余额</h3><Audit value={data.validation}/></div><p className="receipt-note">原订购、累计实收、取消及未收余额分别核对。已收货不等于已放行；同一收货引用只入账一次，未收余额不重复计入期初。</p>
     <div className="receipt-table-scroll" tabIndex={0} aria-label="多笔确认采购，可横向滚动"><ProfessionalGrid scope={scope} rows={orders} columns={poColumns} rowId={row=>row.id} label="确认采购" selection={selected?{scope,id:selected.id}:null} onSelect={select} onOpen={select} evidenceStatus={gridEvidence} empty="已查询，本范围没有确认采购。"/></div>
     {!selected&&poId&&<p className="receipt-warning">原采购引用不在本次运行资料中，请重新选择，未借用其他计划的单据。</p>}
     {selected&&<section className="receipt-selected-order" aria-label="选中采购收货事实"><div className="receipt-section-heading"><h4>{selected.external_ref??selected.id} · 收货与质量</h4>{writable&&<Button className="primary" disabled={busy||selected.source_status!=='confirmed'||(receiptNumber(selected.remaining_quantity)??0)<=0} aria-expanded={editorOpen} aria-controls="receipt-assumption" onClick={prepare}>模拟未收余额延期</Button>}</div><Definition rows={[["实收已放行",amount(selected.released_received_quantity,selected.uom)],["实收待QC",amount(selected.qc_waiting_quantity,selected.uom)],["实收质量冻结",amount(selected.frozen_received_quantity,selected.uom)],["原取消数量",amount(selected.cancelled_quantity,selected.uom)]]}/><div className="receipt-table-scroll" tabIndex={0} aria-label="此采购的独立收货引用与质量状态，可横向滚动"><ProfessionalGrid scope={scope} rows={receipts} columns={receiptColumns} rowId={row=>JSON.stringify([row.po_id,row.receipt_reference])} label="此采购收货" selection={selectedReceipt?{scope,id:JSON.stringify([selectedReceipt.po_id,selectedReceipt.receipt_reference])}:null} onSelect={row=>setSearch(previous=>{const next=new URLSearchParams(previous);next.set('supply_receipt',row.receipt_reference);return next})} evidenceStatus={gridEvidence} empty="已查询，此采购尚无实收记录；不代表未来余额已可用。"/></div><span role="status">{selectedReceipt&&<>选中收货 {selectedReceipt.receipt_reference} · {amount(selectedReceipt.quantity,selectedReceipt.uom)} · {receiptQualityLabel[selectedReceipt.quality_status]??'质量未知'} · {time(selectedReceipt.physical_receipt_at)} 物理实收</>}</span><details className="receipt-source"><summary>原采购及每笔收货的来源</summary><pre className="raw-json">{JSON.stringify({purchase_order:selected,purchase_receipts:receipts},null,2)}</pre></details></section>}
    </section>
    <section id="receipt-material" className="receipt-business-section"><div className="receipt-section-heading"><h3>净可用库存与物料时间账</h3><Audit value={data.material_validation}/></div>
     <div className="receipt-opening-scroll" tabIndex={0} aria-label="业务期初库存构成，可横向滚动"><table><thead><tr><th>物料 / 地点</th><th>其他物理期初（不含下列实收）</th><th>质量冻结</th><th>其他不可用</th><th>明确净可用</th></tr></thead><tbody>{stocks.map((stock:Dict,index:number)=><tr key={index}><th>{stock.item_id}<small>{stock.location_id}</small></th><td>{amount(stock.on_hand,stock.uom)}</td><td>{amount(stock.quality_blocked,stock.uom)}</td><td>{amount(stock.other_nonnettable,stock.uom)}</td><td>{amount(stock.net_available,stock.uom)}</td></tr>)}</tbody></table></div>
     <p className="receipt-note">物理期初不含单列收货；已放行收货仅加入原生净开账一次，不再作为本期采购入流。原生初始化事件与未来QC可用入流分别保留；这些是物料地点全量，不代表指定订单分配。</p>
     {events.length?<div className="receipt-table-scroll" tabIndex={0} aria-label="原生物料时间事件，可横向滚动"><DataTable rows={events} columns={eventColumns} rowId={(row)=>String(row.id??row.native_row_index??row.native_sequence??events.indexOf(row))}/></div>:<p className="receipt-warning">{receiptEvidenceText(material?.status??'NOT_COLLECTED')}。{material?.status==='EMPTY_CHECKED'?'已核对本范围无对应事件。':'不将缺少原生事件解释为零耗用或零库存。'}</p>}
     <details className="receipt-source"><summary>完整物料事件、核验与来源SHA</summary><pre className="raw-json">{JSON.stringify({material_events:material,validation:data.material_validation},null,2)}</pre></details>
    </section>
    <section id="receipt-protected-mo" className="receipt-business-section"><div className="receipt-section-heading"><h3>已有制造工单与原窗口保护</h3><span className="receipt-joint-audit">联合核验 <Audit value={data.protected_mo_validation}/></span></div><p className="receipt-note">企业工单身份、数量、状态与窗口为原确认事实。供应冲突不能通过移动、缩短或改成proposed来消除；计划开工与完成不表示实际执行。</p><div className="receipt-table-scroll" tabIndex={0} aria-label="受保护制造工单与资源窗口，可横向滚动"><DataTable rows={moRows} columns={moColumns} rowId={row=>row.id} empty="已查询，本范围没有已有制造工单。"/></div>
     <div className="receipt-work-actions">{[...new Set<string>((data.observed_resource_loads??[]).map((load:Dict)=>load.resource_id).filter(Boolean))].map(id=><Button key={id} onClick={()=>onResource(id)}>调查资源 {id}</Button>)}</div><details className="receipt-source"><summary>工单保护核验及原生来源</summary><pre className="raw-json">{JSON.stringify({manufacturing_orders:moRows,observed_manufacturing_orders:data.observed_manufacturing_orders,observed_operations:data.observed_operations,observed_resource_loads:data.observed_resource_loads,protected_mo_validation:data.protected_mo_validation},null,2)}</pre></details>
    </section>
    <details className="receipt-source"><summary>当前业务边界、核验与原始来源</summary><pre className="raw-json">{JSON.stringify({validation:data.validation,limits:data.limits,evidence_files:data.evidence_files},null,2)}</pre></details>
   </div>
   {editorOpen&&writable&&<aside id="receipt-assumption" className="receipt-assumption" aria-label="未收余额独立试算"><div className="receipt-section-heading"><h3>准备未收余额延期假设</h3><Button className="quiet" icon="close" disabled={busy} aria-label="收起采购余额假设" onClick={()=>setEditorOpen(false)}/></div>
    <form aria-label="确认采购未收余额延期" aria-busy={busy} onSubmit={event=>{event.preventDefault();void act(false)}}><Definition rows={[["精确采购",editor.fields.reference],["修改范围",'仅此采购尚未收到的余额'],["事实性质",'PLANNING_ASSUMPTION · 隔离规划假设']]}/>
     <label>新预计物理到货（含时区）<input aria-label="未收余额新预计物理到货" required value={editor.fields.newReceiptAt} disabled={busy} onChange={event=>edit({newReceiptAt:event.target.value})} placeholder="2026-11-05T09:00:00+08:00"/></label><p className="receipt-note">工厂时区 {tz}。QC后可用由服务端沿原条件核对；不修改此前实收、放行与冻结记录。</p>
     <label>延期假设理由<textarea aria-label="未收余额延期假设理由" required rows={3} maxLength={1000} value={editor.fields.reason} disabled={busy} onChange={event=>edit({reason:event.target.value})}/></label>
     {!preview&&<p className="receipt-note" role="status">{prepared.error||'先核对原值与假设预览，再确认保存；预览不会计算。'}</p>}
     {preview&&<section className="receipt-preview" aria-label="采购余额只读预览"><h4>原值与候选条件</h4><Badge>只读预览 · 尚未计算</Badge><div className="receipt-preview-values"><div><h5>原始条件</h5><Definition rows={[["采购未收余额",amount(preview.changes.before.remaining_quantity,preview.changes.before.uom)],["预计物理到货",time(preview.changes.before.physical_receipt_at)],["QC后可用",time(preview.changes.before.available_at)],["已收数量（不改）",amount(preview.changes.before.received_quantity,preview.changes.before.uom)]]}/></div><div><h5>候选假设</h5><Definition rows={[["采购未收余额",amount(preview.changes.after.remaining_quantity,preview.changes.after.uom)],["假设物理到货",time(preview.changes.after.physical_receipt_at)],["QC后可用",time(preview.changes.after.available_at)],["事实性质","PLANNING_ASSUMPTION"]]}/></div></div><details className="receipt-source"><summary>完整原值与候选字段</summary><pre className="raw-json">{JSON.stringify({before:preview.changes.before,after:preview.changes.after},null,2)}</pre></details><details><summary>来源SHA与预览指纹</summary><Definition rows={[["来源SHA",preview.identity.source_sha256],["预览指纹",preview.fingerprint]]}/></details><label className="receipt-confirm"><input type="checkbox" aria-label="确认未收余额假设边界" disabled={busy} checked={editor.acknowledged} onChange={event=>setEditor(previous=>({...previous,acknowledged:event.target.checked}))}/><span>已核对本笔未收余额与日期。原已收事实和受保护制造安排不变；仅创建NOT_RUN模拟草案，不授予采购、生产或执行许可。</span></label></section>}
     <div className="receipt-form-actions"><Button type="submit" disabled={busy||!prepared.body}>{busy?'正在核对条件':preview?'重新预览差异':'预览未收余额差异'}</Button>{preview&&<Button className="primary" disabled={busy||!prepared.body||!editor.acknowledged} onClick={()=>void act(true)}>确认创建NOT_RUN草案</Button>}</div>
    </form>
   </aside>}
   </div>
  </>}
 </section>;
}
