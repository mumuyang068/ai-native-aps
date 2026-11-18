import {useEffect,useRef,useState} from 'react';
import type {Dict} from '../api/contracts';
import {request} from '../api/client';
import {useActionGuard} from '../app/useActionGuard';
import {Badge,Button,Definition,ErrorNote,Loading} from '../components/Controls';
import {DataTable,type TableColumn} from '../components/DataTable';
import {supports} from './capabilityModel';
import {decisionQuantity} from './demandDecisionModel';
import {formatSiteTime} from './timeRange';
import {businessLabel} from './planModel';
import {accessoryPrice,verifyAccessoryDraft,verifyAccessoryPreview,verifyAccessorySupply,type AccessoryOption,type AccessoryReport,type AccessoryScope} from './accessorySupplyModel';
import './demand-decisions.css';
import './accessory-supply.css';

export function AccessoryTerms({option,timezone}:{option:AccessoryOption;timezone:string}){
 const time=(value:unknown)=>typeof value==='string'?formatSiteTime(value,timezone):'未提供';
 const hours=(value:unknown)=>value!=null&&Number.isFinite(Number(value))?String(Number(value)/3600)+' 小时':'未提供';
 return <><Definition rows={[["来源",option.source_id],["供应商",option.supplier_id],["物料 / 地点",option.item_id+' / '+option.location_id],["最小采购量",decisionQuantity(option.minimum_order_quantity,option.uom)],["采购倍数",decisionQuantity(option.order_multiple,option.uom)],["全期供应能力",decisionQuantity(option.total_capacity_quantity,option.uom)],["提前期",hours(option.lead_time_seconds)],["到货后 QC 等待",hours(option.qc_wait_seconds)],["最早下单时点",time(option.earliest_order_at)],["最晚下单时点",time(option.latest_order_at)],["条款生效 / 结束",time(option.effective_from)+' / '+time(option.effective_to)],["模型单价",accessoryPrice(option)]]}/><p className="demand-decision-note">供应能力为整段可下单窗口共用总额度，不按天重复。物理到货后仍需完成声明的 QC 等待，才可参与制造领用。供应数量和时点由真实计算决定。</p></>;
}
export function AccessoryPurchaseObservations({orders,validation,timezone}:{orders:AccessoryReport['purchase_orders'];validation?:Dict;timezone:string}){
 const purchaseColumns:TableColumn<Dict>[]=[{id:'reference',title:'原生建议 / 来源',value:row=><>{row.reference}<small>{row.source_id}</small></>},{id:'quantity',title:'建议采购量',numeric:true,value:row=>decisionQuantity(row.quantity,row.uom)},{id:'physical',title:'物理到货',value:row=>row.physical_receipt_at?formatSiteTime(row.physical_receipt_at,timezone):'未核验'},{id:'available',title:'QC 后可用',value:row=>row.available_at?formatSiteTime(row.available_at,timezone):'未核验'},{id:'state',title:'原生状态 / 认证',value:row=><>{row.status??'未提供'}<small>{row.certification==='PASS'?'本运行限定认证通过':row.certification??'NOT_CHECKED'}</small></>}];
 return <section className="accessory-purchase-observations" aria-label="当前运行辅料原生采购建议"><h3>当前运行的辅料采购建议</h3>{!Array.isArray(orders)?<p className="pending-note">尚未取得本运行的原生建议采购认证，不能当作零采购或可执行供应。</p>:<div className="demand-decision-table" tabIndex={0} aria-label="辅料采购原生建议，可横向滚动"><DataTable rows={orders} columns={purchaseColumns} rowId={row=>row.reference} empty="已读取本运行摘要，没有辅料采购建议。"/></div>}{validation&&<details className="demand-decision-source"><summary>此运行供应合法性与原生证据</summary><pre className="raw-json">{JSON.stringify(validation,null,2)}</pre></details>}</section>;
}
type Props={scope:AccessoryScope;profile?:string;timezone:string;businessStatus?:string;onDraft:(id:string)=>void;onReturn:()=>void;onInventory:(itemId:string,locationId:string)=>void;onDemand:()=>void;onReview:()=>void};
export function AccessorySupply({scope,profile,timezone,businessStatus,onDraft,onReturn,onInventory,onDemand,onReview}:Props){
 const key=JSON.stringify([scope.planId,scope.versionId,scope.baselineRunId,scope.runId,scope.target]);
 const[response,setResponse]=useState<{key:string;data:AccessoryReport}|null>(null),[failure,setFailure]=useState<{key:string;message:string}|null>(null),[retry,setRetry]=useState(0),[editor,setEditor]=useState<{key:string;reason:string}|null>(null),[preview,setPreview]=useState<{key:string;input:string;data:Dict}|null>(null),[pending,setPending]=useState('');
 const capture=useActionGuard(key),receipt=useRef<{input:string;id:string}|null>(null),certified=supports(profile,'actions','accessory_supply');
 useEffect(()=>{
  if(!certified)return;const controller=new AbortController(),query=new URLSearchParams({data_version_id:scope.versionId,run_id:scope.runId,target:scope.target});setFailure(null);
  request<Dict>('/v1/enterprise-plans/'+encodeURIComponent(scope.planId)+'/accessory-supply?'+query,{signal:controller.signal}).then(value=>{if(!controller.signal.aborted)setResponse({key,data:verifyAccessorySupply(value,scope)})}).catch((error:unknown)=>{if(!controller.signal.aborted)setFailure({key,message:error instanceof Error?error.message:'无法读取当前来源条款，请重新读取。'})});return()=>controller.abort();
 },[key,retry,certified]);
 const data=response?.key===key?response.data:null,option=data?.option,busy=pending===key,open=editor?.key===key,reason=open?editor.reason.trim():'',input=JSON.stringify([key,option?.source_id,reason]),currentPreview=preview?.key===key&&preview.input===input?preview.data:null;
 const writable=certified&&scope.target==='baseline'&&scope.runId===scope.baselineRunId&&option?.enabled===false,valid=writable&&open&&reason.length>0&&reason.length<=1000;

 async function act(create:boolean){
  if(!valid||busy||!option||(create&&!currentPreview))return;
  const owns=capture();setPending(key);setFailure(null);
  const body={data_version_id:scope.versionId,baseline_run_id:scope.baselineRunId,run_id:scope.runId,target:scope.target,source_id:option.source_id,reason};
  try{
   if(create){if(receipt.current?.input!==input)receipt.current={input,id:crypto.randomUUID().replaceAll('-','')};const value=await request<Dict>('/v1/enterprise-plans/'+encodeURIComponent(scope.planId)+'/accessory-supply-drafts',{method:'POST',body:{...body,request_id:receipt.current.id}});if(owns()){const id=verifyAccessoryDraft(value,scope,option,reason);setEditor(null);setPreview(null);onDraft(id)}}
   else{const value=await request<Dict>('/v1/enterprise-plans/'+encodeURIComponent(scope.planId)+'/accessory-supply-preview',{method:'POST',body});if(owns())setPreview({key,input,data:verifyAccessoryPreview(value,scope,option,reason)})}
  }catch(error:unknown){if(owns())setFailure({key,message:error instanceof Error?error.message:'操作未核实，请刷新计划核对。'})}finally{if(owns())setPending('')}
 }
 return <section className="demand-decisions accessory-supply" aria-label="辅料来源与补给假设">
  <header className="demand-decision-heading"><h2>辅料来源与补给假设</h2><Button icon="left" onClick={onReturn}>返回原对象与筛选</Button></header>
  {!certified?<p className="pending-note">当前模型未认证辅料补给模拟，请返回原计划使用其已支持的操作。</p>:<>
   <details className="demand-decision-scope"><summary>{scope.target==='baseline'?'原始基线':'隔离模拟方案'} · {scope.runId} · {timezone}</summary><Definition rows={[["计划",scope.planId],["企业版本",scope.versionId],["当前运行",scope.runId],["原始基线",scope.baselineRunId],["认证模型",profile??'未提供']]}/></details>
   {businessStatus&&<p className="demand-decision-state"><Badge kind={businessStatus==='INVALID_SCHEDULE'?'bad':businessStatus==='FEASIBLE_ON_TIME'?'good':'risk'}>{businessLabel(businessStatus)}</Badge>{businessStatus==='INVALID_SCHEDULE'&&<span>本运行存在业务核验失败；来源认证不能替代整体计划合法性。</span>}</p>}
   <ErrorNote error={failure?.key===key?failure.message:''}/>
   {!data?failure?.key===key?<Button onClick={()=>setRetry(value=>value+1)}>重新读取来源</Button>:<Loading/>:<>
    <div className="demand-decision-state"><Badge>{option!.enabled?'模拟来源已启用':'模拟来源未启用'}</Badge><span>独立声明的研发合成供应条件</span></div>
    <p className="demand-decision-note">此来源允许隔离试算，尚未形成供应商确认采购、企业批准或实际到货。既有 confirmed 采购保持原事实。</p>
    <div className="accessory-supply-workspace"><div><h3>{option!.item_id} · {option!.location_id}</h3><AccessoryTerms option={option!} timezone={timezone}/><details className="demand-decision-source"><summary>供应条件原件与来源</summary><pre className="raw-json">{JSON.stringify({fact_kind:option!.fact_kind,permission:option!.permission,provenance:option!.provenance},null,2)}</pre></details></div>
     <aside className="demand-decision-inspector" aria-label="受控辅料补给操作"><h3>单项补给假设</h3>
      {writable?<><p>只在独立草案启用这条来源。容量、MOQ、倍数、提前期和 QC 条款保持原值。</p><Button aria-expanded={open} disabled={busy} onClick={()=>{setEditor(open?null:{key,reason:''});setPreview(null);setFailure(null)}}>{open?'收起假设':'模拟启用此来源'}</Button></>:<p>{scope.target==='baseline'?'当前来源已启用，不能重复创建同一启用动作。':'当前为独立候选的读取结果。若需另一项候选，请切回原始基线。'}</p>}
      {open&&<form className="demand-priority-editor" aria-label="辅料补给独立草案" onSubmit={event=>{event.preventDefault();void act(false)}}><label>假设理由<input aria-label="辅料补给假设理由" required maxLength={1000} value={editor.reason} disabled={busy} onChange={event=>{setEditor({key,reason:event.target.value});setPreview(null);setFailure(null)}}/></label>
       {currentPreview&&<div className="accessory-supply-preview" aria-live="polite"><h4>原值 / 拟议值</h4><Definition rows={[["来源",option!.source_id],["允许生成新补给建议","未启用 → 仅此隔离草案启用"],["固定来源条款","保持原值"],["原确认采购及企业版本","保持不变"],["采购与生产执行","未授权"]]}/><Badge>规则预览 · 尚未求解</Badge><p>预览不承诺增产数量、需求满足量或交付日期。创建后仍需通过原 Worker 计算及业务核验。</p></div>}
       <div className="demand-decision-actions"><Button type="submit" disabled={!valid||busy}>{busy?'正在核对':'预览补给假设'}</Button>{currentPreview&&<Button className="primary" disabled={!valid||busy} onClick={()=>void act(true)}>创建 NOT_RUN 草案</Button>}<Button disabled={busy} onClick={()=>{setEditor(null);setPreview(null)}}>取消</Button></div>
      </form>}
      <h4>当前运行的业务下钻</h4><div className="demand-decision-actions"><Button onClick={()=>onInventory(option!.item_id,option!.location_id)}>查看辅料库存时间账</Button><Button onClick={onDemand}>查看 Firm / Net 履约</Button><Button onClick={onReview}>进入同计划多候选评审</Button></div><p>来源条件不等于已经生成采购建议；新采购及其物理到货、QC 可用和制造影响，以当前运行的真实结果为准。</p>
     </aside>
    </div>
    <AccessoryPurchaseObservations orders={data.purchase_orders} validation={data.validation} timezone={timezone}/>
   </>}
  </>}
 </section>;
}
