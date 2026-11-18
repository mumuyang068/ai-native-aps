import {useRef,useState} from 'react';
import type {Dict} from '../api/contracts';
import {request} from '../api/client';
import {useActionGuard} from '../app/useActionGuard';
import {Badge,Button,Definition,Dialog,ErrorNote} from '../components/Controls';
import {formatSiteTime} from './timeRange';
import {controlledConditionsRequest,firmConditionOrders,qualifiedSewOperators,updateControlledEditor,verifyControlledPreview,verifyControlledDraft,type ControlledScope,type ControlledFields,type ControlledEditor} from './controlledConditionsModel';
import './controlled-conditions.css';

type Props={scope:ControlledScope;input:Dict;timezone:string;initialDemandId?:string;initialResourceId?:string;onTarget:(id:string)=>void;onClose:()=>void};
export function ControlledConditions({scope,input,timezone,initialDemandId,initialResourceId,onTarget,onClose}:Props){
 const orders=firmConditionOrders(input),operators=qualifiedSewOperators(input),initialOrder=orders.find(d=>d.id===initialDemandId),initialOperator=operators.find(r=>r.id===initialResourceId);
 const[editor,setEditor]=useState<ControlledEditor>(()=>({fields:{includeOrder:!!initialOrder,demandId:initialOrder?.id??'',priority:initialOrder?String(initialOrder.priority):'',includeOperator:!!initialOperator,resourceId:initialOperator?.id??'',start:'',end:'',reason:''},preview:null,acknowledged:false}));
 const[pending,setPending]=useState(''),[failure,setFailure]=useState<{key:string;message:string}|null>(null),receipt=useRef<{key:string;id:string}|null>(null);
 const fields=editor.fields,scopeKey=JSON.stringify(scope),actionKey=JSON.stringify([scopeKey,fields]),capture=useActionGuard(actionKey),busy=pending===actionKey;
 const prepared=controlledConditionsRequest(scope,input,fields,timezone),preview=editor.preview,order=orders.find(d=>d.id===fields.demandId),operator=operators.find(r=>r.id===fields.resourceId);
 function edit(patch:Partial<ControlledFields>){setEditor(previous=>updateControlledEditor(previous,patch));setFailure(null);receipt.current=null;}
 async function act(create:boolean){
  if(busy||!prepared.body||(create&&(!preview||!editor.acknowledged)))return;
  const owns=capture(),body=prepared.body;setPending(actionKey);setFailure(null);
  try{
   const prefix='/v1/enterprise-plans/'+encodeURIComponent(scope.planId)+'/controlled-conditions-';
   if(create&&preview){
    if(receipt.current?.key!==actionKey)receipt.current={key:actionKey,id:crypto.randomUUID().replaceAll('-','')};
    const response=await request<Dict>(prefix+'drafts',{method:'POST',body:{...body,source_sha256:preview.identity.source_sha256,fingerprint:preview.fingerprint,acknowledge_limits:true,request_id:receipt.current.id}});
    if(owns()){const id=verifyControlledDraft(response,scope,body,preview);setEditor(previous=>({...previous,preview:null,acknowledged:false}));onTarget(id)}
   }else{
    setEditor(previous=>({...previous,preview:null,acknowledged:false}));
    const response=await request<Dict>(prefix+'preview',{method:'POST',body});
    if(owns()){const value=verifyControlledPreview(response,scope,body,input);setEditor(previous=>({...previous,preview:value,acknowledged:false}))}
   }
  }catch(error:unknown){if(owns()){setEditor(previous=>({...previous,preview:null,acknowledged:false}));setFailure({key:actionKey,message:error instanceof Error?error.message:'条件状态未核实，请重新读取当前计划后预览。'})}}
  finally{if(owns())setPending('')}
 }
 const audit=preview?.changes,time=(v:string)=>formatSiteTime(v,timezone);
 const calendar=(input.calendars??[]).find((c:Dict)=>c.id===operator?.availability_calendar_id);
 const sourceRows=([['订单',fields.includeOrder?order:null],['具名人员',fields.includeOperator?operator:null],['人员原日历',fields.includeOperator?calendar:null]] as [string,Dict|null|undefined][]).filter(([,object])=>object).flatMap(([label,object])=>[[label+'来源',object!.provenance?.source_system??'未提供'],[label+'来源版本',object!.provenance?.source_version??'未提供'],[label+'原始记录',object!.provenance?.source_record_id??'未提供'],[label+'来源定位',object!.provenance?.source_locator??'未提供']] as [string,string][]);
 return <Dialog title="受控条件影响试算" onClose={()=>!busy&&onClose()}><form className="controlled-conditions" aria-label="受控条件影响试算" aria-busy={busy} onSubmit={event=>{event.preventDefault();void act(false)}}>
  <p className="controlled-intro">从原始基线准备独立候选。先预览输入差异，确认创建后再单独运行、比较和评审。</p>
  <details className="controlled-scope"><summary>当前计划与基线 · {timezone}</summary><Definition rows={[["企业计划",scope.planId],["数据版本",scope.versionId],["原始基线运行",scope.baselineRunId],["当前运行",scope.runId]]}/></details>
  <fieldset className="controlled-condition" disabled={busy}><legend>订单需求</legend>
   <label className="controlled-choice"><input type="checkbox" checked={fields.includeOrder} disabled={!orders.length} onChange={event=>edit({includeOrder:event.target.checked})}/><span>调整一条 Firm 订单优先级</span></label>
   {fields.includeOrder&&<div className="controlled-fields"><label className="controlled-wide">精确 Firm 需求行<select aria-label="受控 Firm 需求行" required value={fields.demandId} onChange={event=>{const selected=orders.find(d=>d.id===event.target.value);edit({demandId:event.target.value,priority:selected?String(selected.priority):''})}}><option value="">选择订单需求行</option>{orders.map(d=><option key={d.id} value={d.id}>{d.order_ref??d.id} · {d.id}</option>)}</select></label><label>新需求 priority<input aria-label="受控新需求优先级" type="number" inputMode="numeric" min="0" max="2147483647" step="1" required value={fields.priority} onChange={event=>edit({priority:event.target.value})}/></label><p className="controlled-original">原值 <strong>{order?.priority??'未选择'}</strong><span>数值越小越优先；与供应来源 PRIORITY 分开。</span></p></div>}
  </fieldset>
  <fieldset className="controlled-condition" disabled={busy}><legend>人员日历</legend>
   <label className="controlled-choice"><input type="checkbox" checked={fields.includeOperator} disabled={!operators.length} onChange={event=>edit({includeOperator:event.target.checked})}/><span>模拟一名合格 SEW 人员缺勤</span></label>
   {fields.includeOperator&&<><div className="controlled-fields"><label className="controlled-wide">具名人员<select aria-label="受控具名 SEW 人员" required value={fields.resourceId} onChange={event=>edit({resourceId:event.target.value})}><option value="">选择具名合格人员</option>{operators.map(r=><option key={r.id} value={r.id}>{r.id}{r.name?' · '+r.name:''}</option>)}</select></label><label>缺勤开始 · {timezone}<input aria-label="受控人员缺勤开始" type="datetime-local" step="1" required value={fields.start} onChange={event=>edit({start:event.target.value})}/></label><label>缺勤结束 · {timezone}<input aria-label="受控人员缺勤结束" type="datetime-local" step="1" required value={fields.end} onChange={event=>edit({end:event.target.value})}/></label></div><p className="controlled-note">输入声明日历：<strong>{operator?.availability_calendar_id??'待选择具名人员'}</strong>。只扣减与原声明可用时间重合的部分；原生负荷与实际出勤记录分别查看。</p></>}
   {!operators.length&&<p className="controlled-note">当前输入没有资格覆盖规划期、且匹配 SEW 工艺要求的具名人员。</p>}
  </fieldset>
  {sourceRows.length>0&&<details className="controlled-scope"><summary>原始对象来源</summary><Definition rows={sourceRows}/></details>}
  <label className="controlled-reason">试算理由<textarea aria-label="受控条件试算理由" required maxLength={1000} rows={2} disabled={busy} value={fields.reason} onChange={event=>edit({reason:event.target.value})}/></label>
  {!preview&&<p className="controlled-note" role="status">{prepared.error||'条件已填写，先预览精确对象与输入差异。'}</p>}
  {preview&&audit&&<section className="controlled-preview" aria-label="受控条件只读预览" aria-live="polite"><div className="controlled-preview-heading"><h3>本次输入差异</h3><Badge>只读预览 · 尚未计算</Badge></div>
   <div className="controlled-diff-scroll" tabIndex={0} aria-label="条件差分表，可横向滚动"><table><thead><tr><th scope="col">精确对象 / 字段</th><th scope="col">原值</th><th scope="col">候选值</th></tr></thead><tbody>
    {audit.order&&<tr><th scope="row">{audit.order.demand_id}<small>需求 priority</small></th><td>{audit.order.before_priority}</td><td className="controlled-changed">{audit.order.after_priority}<small>仅当前 Firm 需求行</small></td></tr>}
    {audit.operator&&<><tr><th scope="row">{audit.operator.resource_id}<small>输入声明日历</small></th><td>{audit.operator.before_calendar_id}</td><td className="controlled-changed">{audit.operator.after_calendar_id}</td></tr><tr><th scope="row">缺勤时段<small>{timezone}</small></th><td>保留时段外原声明</td><td className="controlled-changed">{time(audit.operator.start_at)} → {time(audit.operator.end_at)}<small>扣减 {Number((audit.operator.removed_seconds/60).toFixed(2))} 分钟声明可用时间</small></td></tr></>}
   </tbody></table></div>
   <p className="controlled-note">原确认采购、QC 等待、订单数量与交期、加班规则和企业冻结版本保持原值。预览未提交引擎计算；交付与负荷影响以候选运行结果为准。</p>
   <label className="controlled-choice controlled-ack"><input type="checkbox" aria-label="确认受控条件边界" checked={editor.acknowledged} disabled={busy} onChange={event=>setEditor(previous=>({...previous,acknowledged:event.target.checked}))}/><span>已核对对象与差异，确认只创建 NOT_RUN 模拟草案；业务许可待确认，未授权采购或生产执行。</span></label>
  </section>}
  <ErrorNote error={failure?.key===actionKey?failure.message:''}/>
  <div className="controlled-actions"><Button type="submit" disabled={busy||!prepared.body}>{busy?'正在核对条件':preview?'重新预览条件':'预览条件差异'}</Button>{preview&&<Button className="primary" disabled={busy||!prepared.body||!editor.acknowledged} onClick={()=>void act(true)}>{busy?'正在保存草案':'确认创建 NOT_RUN 草案'}</Button>}<Button disabled={busy} onClick={onClose}>取消</Button></div>
 </form></Dialog>;
}
