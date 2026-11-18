import {useEffect,useRef,useState} from 'react';
import type {Dict} from '../api/contracts';
import {request} from '../api/client';
import {useActionGuard} from '../app/useActionGuard';
import {Badge,Button,Definition,ErrorNote,Loading} from '../components/Controls';
import {AccessoryPurchaseObservations} from './AccessorySupply';
import {DynamicAnalysis} from './DynamicAnalysis';
import type {TaskRecord} from './planModel';
import {formatSiteTime} from './timeRange';
import {analysisUnit} from './analysisModel';
import {inventoryPolicyEvidenceText,inventoryPolicyOutcomeRows,inventoryDecisionRequest,updateInventoryEditor,verifyInventoryDecisionReport,verifyInventoryDecisionPreview,verifyInventoryDecisionDraft,type InventoryDecisionScope,type InventoryDecisionFields,type InventoryDecisionEditor} from './inventoryDecisionModel';
import './inventory-decision.css';

type Props={scope:InventoryDecisionScope;input:Dict;itemId:string;locationId:string;timezone:string;onTarget:(id:string)=>void;onReturn:()=>void;onInventory:()=>void;onReview:()=>void;onCompare:()=>void;onLocate:(id:string,origin?:URLSearchParams)=>void;tasks:TaskRecord[]};
const text=(v:unknown)=>v===null||v===undefined?'未提供':String(v);
const hours=(v:unknown)=>v===null||v===undefined||!Number.isFinite(Number(v))?'未提供':String(Number(v)/3600)+' 小时';
function netOpening(row:Dict){const values=[row.on_hand,row.quality_blocked,row.other_nonnettable];return values.some(v=>v===undefined||v===null||v===''||!Number.isFinite(Number(v)))?'未核实':String(Number(row.on_hand)-Number(row.quality_blocked)-Number(row.other_nonnettable))+' '+analysisUnit(row.uom)}
function Terms({option,timezone}:{option:Dict;timezone:string}){
 const time=(v:unknown)=>typeof v==='string'?formatSiteTime(v,timezone):'未提供';
 return <><Definition rows={[["MOQ / 采购倍数",text(option.minimum_order_quantity)+' / '+text(option.order_multiple)],["单笔上限 / 全期能力",text(option.maximum_order_quantity)+' / '+text(option.total_capacity_quantity)+' '+analysisUnit(option.uom)],["采购提前期 / QC 等待",hours(option.lead_time_seconds)+' / '+hours(option.qc_wait_seconds)]]}/><details><summary>条款有效期与允许下单范围</summary><Definition rows={[["条款有效",time(option.effective_from)+' → '+time(option.effective_to)],["允许下单",time(option.earliest_order_at)+' → '+time(option.latest_order_at)]]}/></details></>;
}
export function InventoryDecision({scope,input,itemId,locationId,timezone,onTarget,onReturn,onReview,onCompare,onLocate,tasks}:Props){
 const key=JSON.stringify([scope,itemId,locationId]),[response,setResponse]=useState<{key:string;value:Dict}|null>(null),[failure,setFailure]=useState<{key:string;message:string}|null>(null),[retry,setRetry]=useState(0),[pending,setPending]=useState('');
 const[editor,setEditor]=useState<InventoryDecisionEditor>({fields:{includePolicy:false,includeSource:false,minimum:'',maximum:'',sourceOptionId:'',reason:''},preview:null,acknowledged:false});
 const[editorOpen,setEditorOpen]=useState(false);
 const receipt=useRef<{key:string;id:string}|null>(null),data=response?.key===key?response.value:null,fields=editor.fields,actionKey=JSON.stringify([key,fields]),capture=useActionGuard(actionKey),busy=pending===actionKey;
 useEffect(()=>{
  setResponse(null);setFailure(null);setEditor({fields:{includePolicy:false,includeSource:false,minimum:'',maximum:'',sourceOptionId:'',reason:''},preview:null,acknowledged:false});
  const controller=new AbortController(),query=new URLSearchParams({data_version_id:scope.versionId,run_id:scope.runId,target:scope.target,item_id:itemId,location_id:locationId});
  request<Dict>('/v1/enterprise-plans/'+encodeURIComponent(scope.planId)+'/inventory-decision?'+query,{signal:controller.signal}).then(value=>{
   if(!controller.signal.aborted){const verified=verifyInventoryDecisionReport(value,scope,itemId,locationId);setResponse({key,value:verified});setEditor(previous=>({...previous,fields:{...previous.fields,minimum:verified.current_policy?.minimum??'',maximum:verified.current_policy?.maximum??''}}))}
  }).catch(e=>{if(!controller.signal.aborted)setFailure({key,message:e instanceof Error?e.message:'当前物料条件未能读取，请重新核对运行。'})});return()=>controller.abort();
 },[key,retry]);
 function edit(patch:Partial<InventoryDecisionFields>){setEditor(previous=>updateInventoryEditor(previous,patch));setFailure(null);receipt.current=null}
 const prepared=data?inventoryDecisionRequest(scope,data,input,itemId,locationId,fields):{body:null,error:''},preview=editor.preview,current=data?.current_policy,selectedOption=data?.catalog.find((o:Dict)=>o.id===fields.sourceOptionId),writable=scope.target==='baseline'&&scope.runId===scope.baselineRunId;
 async function act(create:boolean){
  if(!prepared.body||!data||busy||(create&&(!preview||!editor.acknowledged)))return;
  const owns=capture(),body=prepared.body;setPending(actionKey);setFailure(null);
  try{
   const prefix='/v1/enterprise-plans/'+encodeURIComponent(scope.planId)+'/inventory-decision-';
   if(create&&preview){
    if(receipt.current?.key!==actionKey)receipt.current={key:actionKey,id:crypto.randomUUID().replaceAll('-','')};
    const value=await request<Dict>(prefix+'drafts',{method:'POST',body:{...body,source_sha256:preview.identity.source_sha256,fingerprint:preview.fingerprint,acknowledge_limits:true,request_id:receipt.current.id}});
    if(owns())onTarget(verifyInventoryDecisionDraft(value,scope,body,preview,data));
   }else{
    setEditor(previous=>({...previous,preview:null,acknowledged:false}));
    const value=await request<Dict>(prefix+'preview',{method:'POST',body});
    if(owns())setEditor(previous=>({...previous,preview:verifyInventoryDecisionPreview(value,scope,body,data),acknowledged:false}));
   }
  }catch(e){if(owns()){setEditor(previous=>({...previous,preview:null,acknowledged:false}));setFailure({key,message:e instanceof Error?e.message:'预览或草案状态未核实，请重新读取。'})}}
  finally{if(owns())setPending('')}
 }
 const time=(v?:string)=>v?formatSiteTime(v,timezone):'未提供',audit=preview?.changes,stock=data?.stock_facts?.[0],outcome=data?.policy_outcome,qty=(value:unknown)=>value===null||value===undefined?'未核验':String(value)+' '+analysisUnit(data?.catalog?.[0]?.uom);
 const knownFail=data?.material_validation?.status==='FAIL'||data?.validation?.status==='FAIL';
 function jump(id:string){requestAnimationFrame(()=>{const element=document.getElementById(id);element?.scrollIntoView({block:'start'});element?.focus({preventScroll:true})})}
 function prepare(optionId?:string){if(optionId)edit({includeSource:true,sourceOptionId:optionId});setEditorOpen(true);jump('inventory-decision-editor')}
 return <section className="inventory-decision" aria-label="库存目标与补给决策">
  <header className="inventory-decision-heading"><div><h2>{itemId} @ {locationId}</h2><p>库存目标与补给 · {scope.target==='baseline'?'原始基线':'隔离模拟方案'} · {timezone}</p></div><div className="inventory-object-actions"><Button icon="left" disabled={busy} onClick={onReturn}>返回原物料与时间</Button>{writable&&<Button className="primary" disabled={!data||busy} aria-expanded={editorOpen} aria-controls="inventory-decision-editor" onClick={()=>prepare()}>{editorOpen?'定位试算输入':'准备独立试算'}</Button>}{!writable&&<Button icon="compare" onClick={onCompare}>与原始基线比较</Button>}</div></header>
  <ErrorNote error={failure?.key===key?failure.message:''}/>
  {!data?failure?.key===key?<Button onClick={()=>setRetry(n=>n+1)}>重新读取当前物料</Button>:<Loading/>:<>
   <nav className="inventory-object-navigation" aria-label="当前物料相关视图"><Button className="quiet" onClick={()=>jump('inventory-object-stock')}>库存与目标</Button><Button className="quiet" onClick={()=>jump('inventory-object-purchases')}>采购建议</Button><Button className="quiet" onClick={()=>jump('inventory-object-terms')}>来源条款</Button><Button className="quiet" onClick={onReview}>方案评审</Button></nav>
   {knownFail&&<p className="risk-message" role="status">此运行存在已知业务核验违规，判定为 FAIL；软目标达到或计算成功不能抵消违规。</p>}
   <div className={'inventory-decision-layout'+(editorOpen||!writable?' has-editor':'')}><div className="inventory-decision-main">
    <section id="inventory-object-stock" className="inventory-object-summary" tabIndex={-1}><div className="inventory-section-heading"><h3>库存与目标</h3>{outcome&&<Badge kind={outcome.status==='PRESENT'&&outcome.goal_status==='GOAL_MET'?'good':'risk'}>{outcome.status!=='PRESENT'?'目标结果未核验':outcome.goal_status==='GOAL_MET'?'最低库存软目标：已达到':outcome.goal_status==='BELOW_SOFT_TARGET'?'低于最低库存软目标':'目标状态未知'}</Badge>}</div>
     <Definition rows={[["期初净可用",stock?netOpening(stock):'未取得快照'],["质量冻结",stock?qty(stock.quality_blocked):'未核验'],["软最低 / 补给最高",current?current.minimum+' / '+current.maximum+' '+analysisUnit(current.uom):'未声明库存目标'],["规划期末净可用",outcome?.status==='PRESENT'?qty(outcome.ending_quantity):'见当前原生时间账'],["期间最低净可用",outcome?.status==='PRESENT'?qty(outcome.minimum_quantity):'未核验'],["期间最大目标差额",outcome?.status==='PRESENT'?qty(outcome.maximum_gap_quantity):'未声明目标 / 未核验']]}/>
     <p className="inventory-native-state"><Badge kind={data.validation?.status==='FAIL'?'bad':data.validation?.status==='PASS'?'':'risk'}>{inventoryPolicyEvidenceText(current,data.validation?.status)}</Badge>{current&&<span>最低为软目标，最高为发生补给时的目标，不保证实现。</span>}</p>
     <details className="inventory-object-detail"><summary>库存构成、目标时点与适用期间</summary>{data.stock_facts.length?data.stock_facts.map((row:Dict,index:number)=><Definition key={index} rows={[["期初物理",text(row.on_hand)+' '+analysisUnit(row.uom)],["质量冻结",text(row.quality_blocked)+' '+analysisUnit(row.uom)],["其他不可用",text(row.other_nonnettable)+' '+analysisUnit(row.uom)],["库存快照",time(row.as_of)]]}/>):<p>未取得当前物料的期初快照，不能按0显示。</p>}{current&&<Definition rows={[["政策有效期",time(current.start_at)+' → '+time(current.end_at)]]}/>}<Definition rows={inventoryPolicyOutcomeRows(outcome,data.catalog[0].uom,value=>time(value))}/><p className="inventory-decision-note">首次低于目标与最低余额时点分别核对。冻结不进入净可用，采购不并入期初；未知时点和时长不补推。</p></details>
    </section>
    <section className="inventory-object-timeline" aria-label="此物料库存时间与事件"><h3>库存时间与原生事件</h3><DynamicAnalysis planId={scope.planId} versionId={scope.versionId} runId={scope.runId} target={scope.target} baselineRunId={scope.baselineRunId} mode="inventory" embeddedInventory tasks={tasks} onLocate={onLocate} inventoryPolicy={current} inventoryPolicyAuditStatus={data.validation?.status}/></section>
    <section id="inventory-object-purchases" className="inventory-object-purchases" tabIndex={-1}><AccessoryPurchaseObservations orders={data.purchase_orders} validation={data.supply_validation} timezone={timezone}/></section>
    <section id="inventory-object-terms" className="inventory-object-terms" tabIndex={-1}><div className="inventory-section-heading"><h3>来源与冻结条款</h3><span>{data.source.id} · {data.source.supplier_id}</span></div><p className="inventory-decision-note">同一来源的合成条件目录；不是供应商承诺或多供应商择优。单价与币种未提供，采购金额未知。</p>
     <div className="inventory-terms-scroll" tabIndex={0} aria-label="冻结来源条款对照，可横向滚动"><table><thead><tr><th scope="col">条款</th><th scope="col">MOQ</th><th scope="col">倍数</th><th scope="col">单笔上限</th><th scope="col">全期能力</th><th scope="col">提前期 / QC</th><th scope="col">当前状态</th></tr></thead><tbody>{data.catalog.map((option:Dict)=><tr key={option.id}><th scope="row">{option.label}<small>{option.id}</small></th><td>{option.minimum_order_quantity}</td><td>{option.order_multiple}</td><td>{option.maximum_order_quantity}</td><td>{option.total_capacity_quantity}</td><td>{hours(option.lead_time_seconds)} / {hours(option.qc_wait_seconds)}</td><td>{data.selected_supply?.id===option.id?<Badge>当前候选条款</Badge>:writable?<Button className="text-button" disabled={busy} onClick={()=>prepare(option.id)}>用此条款准备试算</Button>:'未选用'}</td></tr>)}</tbody></table></div>
     <details className="inventory-object-detail"><summary>来源有效期、采购窗口与原始记录</summary>{data.catalog.map((option:Dict)=><div key={option.id}><h4>{option.label} · {option.id}</h4><Terms option={option} timezone={timezone}/><pre className="raw-json">{JSON.stringify(option.provenance,null,2)}</pre></div>)}</details>
    </section>
    <details className="inventory-object-detail"><summary>政策、供应与物料核验及来源证据</summary><pre className="raw-json">{JSON.stringify({policy:data.validation,policy_outcome:data.policy_outcome,supply:data.supply_validation,material:data.material_validation,stock_sources:data.stock_facts},null,2)}</pre></details>
   </div>
   {(editorOpen||!writable)&&<aside id="inventory-decision-editor" className="inventory-decision-editor" aria-label="当前物料独立试算操作" tabIndex={-1}><div className="inventory-section-heading"><h3>{writable?'准备独立试算':'当前候选与评审'}</h3>{writable&&<Button className="quiet" aria-label="收起试算输入" icon="close" disabled={busy} onClick={()=>{setEditorOpen(false);jump('inventory-object-stock')}}/>}</div>
    {writable?<form aria-label="库存目标与补给独立草案" aria-busy={busy} onSubmit={event=>{event.preventDefault();void act(false)}}>
     <fieldset disabled={busy} className="inventory-decision-fieldset"><legend>库存目标假设</legend><label className="inventory-decision-check"><input type="checkbox" checked={fields.includePolicy} onChange={event=>edit({includePolicy:event.target.checked})}/><span>为此物料 / 地点设置最低与最高目标</span></label>
      {fields.includePolicy&&<><div className="inventory-target-fields"><label>最低净可用目标 · {analysisUnit(data.catalog[0].uom)}<input aria-label="库存最低目标" type="text" inputMode="decimal" required value={fields.minimum} onChange={event=>edit({minimum:event.target.value})}/></label><label>补给到最高目标 · {analysisUnit(data.catalog[0].uom)}<input aria-label="库存最高目标" type="text" inputMode="decimal" required value={fields.maximum} onChange={event=>edit({maximum:event.target.value})}/></label></div><p className="inventory-decision-note">全规划期恒定目标：{time(input.planning_at)} → {time(input.horizon?.end_at)}。不改变物理库存、冻结量或企业放行记录。</p></>}
     </fieldset>
     <fieldset disabled={busy} className="inventory-decision-fieldset"><legend>有来源的补给条件</legend><label className="inventory-decision-check"><input type="checkbox" checked={fields.includeSource} onChange={event=>edit({includeSource:event.target.checked})}/><span>选择一组已冻结条款，供本草案重新试算</span></label>
      {fields.includeSource&&<><label className="inventory-source-select">原来源的条款目录<select aria-label="补给来源条款" required value={fields.sourceOptionId} onChange={event=>edit({sourceOptionId:event.target.value})}><option value="">选择有来源的条款</option>{data.catalog.map((o:Dict)=><option key={o.id} value={o.id}>{o.label} · {o.id}</option>)}</select></label>{selectedOption&&<Terms option={selectedOption} timezone={timezone}/>}<p className="inventory-decision-note">这是同一供应来源的合成条款选择，不是多供应商择优。全期总能力不按天重置；MOQ、倍数、单笔上限、提前期与 QC 均需由原生结果核验。</p>{selectedOption&&<details><summary>条款原件及来源</summary><pre className="raw-json">{JSON.stringify(selectedOption,null,2)}</pre></details>}</>}
     </fieldset>
     <label className="inventory-decision-reason">试算理由<textarea aria-label="库存补给试算理由" rows={2} required maxLength={1000} disabled={busy} value={fields.reason} onChange={event=>edit({reason:event.target.value})}/></label>
     {!preview&&<p className="inventory-decision-note" role="status">{prepared.error||'条件填写后先预览；预览不会提交计算。'}</p>}
     {preview&&audit&&<section className="inventory-decision-preview" aria-label="库存补给只读预览" aria-live="polite"><div className="inventory-preview-heading"><h3>原值与候选条件</h3><Badge>只读预览 · 尚未计算</Badge></div>
      <div className="inventory-diff-scroll" tabIndex={0} aria-label="库存补给条件差分，可横向滚动"><table><thead><tr><th scope="col">对象 / 字段</th><th scope="col">原值</th><th scope="col">候选值</th></tr></thead><tbody>
       {fields.includePolicy&&<><tr><th scope="row">{itemId} @ {locationId}<small>最低 / 最高目标 · {analysisUnit(data.catalog[0].uom)}</small></th><td>{audit.before?.policy?audit.before.policy.minimum+' / '+audit.before.policy.maximum:'未声明'}</td><td>{audit.after.policy.minimum+' / '+audit.after.policy.maximum}</td></tr><tr><th scope="row">政策有效期</th><td>{audit.before?.policy?time(audit.before.policy.start_at)+' → '+time(audit.before.policy.end_at):'未声明'}</td><td>{time(audit.after.policy.start_at)} → {time(audit.after.policy.end_at)}</td></tr></>}
       {fields.includeSource&&<><tr><th scope="row">来源条款</th><td>{audit.before?.supply?.id??'未选用目录条款'}</td><td>{audit.after.supply.label} · {audit.after.supply.id}<small>{audit.after.supply.source_id}</small></td></tr><tr><th scope="row">MOQ / 倍数 / 单笔上限 / 全期能力</th><td>{data.source.minimum_order_quantity} / {data.source.order_multiple} / {data.source.maximum_order_quantity??'未声明'} / {data.supply_option.total_capacity_quantity}</td><td>{audit.after.supply.minimum_order_quantity} / {audit.after.supply.order_multiple} / {audit.after.supply.maximum_order_quantity} / {audit.after.supply.total_capacity_quantity}</td></tr><tr><th scope="row">采购提前期 / QC 等待</th><td>{Number(data.source.lead_time_seconds)/3600} / {Number(data.source.receiving_qc?.wait_seconds)/3600} 小时</td><td>{Number(audit.after.supply.lead_time_seconds)/3600} / {Number(audit.after.supply.qc_wait_seconds)/3600} 小时</td></tr></>}
      </tbody></table></div><details><summary>预览指纹与来源核对</summary><Definition rows={[["输入来源 SHA",preview.identity.source_sha256],["预览指纹",preview.fingerprint]]}/></details>
      <label className="inventory-decision-check inventory-decision-ack"><input aria-label="确认库存补给试算边界" type="checkbox" disabled={busy} checked={editor.acknowledged} onChange={event=>setEditor(previous=>({...previous,acknowledged:event.target.checked}))}/><span>已核对精确物料、来源和差异。仅创建 NOT_RUN 模拟草案；企业许可待确认，未授权真实采购、库存调整或生产执行。</span></label>
     </section>}
     <div className="inventory-decision-actions"><Button type="submit" disabled={busy||!prepared.body}>{busy?'正在核对条件':preview?'重新预览条件':'预览库存与补给差异'}</Button>{preview&&<Button className="primary" disabled={busy||!editor.acknowledged||!prepared.body} onClick={()=>void act(true)}>确认创建 NOT_RUN 草案</Button>}</div>
    </form>:<><Definition rows={[["当前库存目标",current?current.minimum+' / '+current.maximum+' '+analysisUnit(current.uom):'未声明'],["当前来源条款",data.selected_supply?data.selected_supply.label+' · '+data.selected_supply.id:'未选用目录条款']]}/><p className="inventory-decision-note">当前候选只读。若准备另一组条件，请切回原始基线；计算不授予采购或生产执行权限。</p><div className="inventory-decision-actions"><Button onClick={onCompare}>比较原始基线</Button><Button onClick={onReview}>进入方案评审</Button></div></>}
   </aside>}
   </div>
  </>}
 </section>;
}
