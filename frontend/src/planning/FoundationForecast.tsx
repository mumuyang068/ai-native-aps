import {useEffect,useRef,useState} from 'react';
import type {Dict} from '../api/contracts';
import {request} from '../api/client';
import {useActionGuard} from '../app/useActionGuard';
import {Button,Definition,Dialog,ErrorNote,Loading} from '../components/Controls';
import {DataTable,type TableColumn} from '../components/DataTable';
import {filterFoundationForecast,foundationForecastInputKey,foundationForecastGross,verifyFoundationForecastPreview,verifyFoundationForecastReport,type FoundationForecastScope,type FoundationForecastReport,type FoundationForecastRow} from './foundationForecastModel';
import {formatSiteTime} from './timeRange';
import './foundationForecast.css';

export type FoundationForecastProps={scope:FoundationForecastScope;report?:FoundationForecastReport;onRefresh?:()=>void;onDraft?:(id:string)=>void};
export function FoundationForecast({scope,report:provided,onRefresh,onDraft}:FoundationForecastProps){
 const key=JSON.stringify(scope),capture=useActionGuard(key),[loaded,setLoaded]=useState<{key:string;report:FoundationForecastReport}|null>(null),[failure,setFailure]=useState(''),[loading,setLoading]=useState(false),[revision,setRevision]=useState(0);
 const [item,setItem]=useState(''),[location,setLocation]=useState(''),[period,setPeriod]=useState(''),[selection,setSelection]=useState<{key:string;row:FoundationForecastRow}|null>(null),[gross,setGross]=useState(''),[reason,setReason]=useState(''),[preview,setPreview]=useState<{input:string;value:Dict}|null>(null),[busy,setBusy]=useState(false),[message,setMessage]=useState('');
 const requestReceipt=useRef<{input:string;id:string}|null>(null),base=scope.baselineRunId??scope.runId;
 const bodyScope={data_version_id:scope.versionId,baseline_run_id:base,run_id:base,target:scope.target};
 useEffect(()=>{setBusy(false);setPreview(null);setSelection(null);setItem('');setLocation('');setPeriod('')},[key]);
 let report:FoundationForecastReport|null=null;
 try{report=provided?verifyFoundationForecastReport(provided,scope):loaded?.key===key?loaded.report:null}catch{/* The effect reports stale supplied data; no foreign rows are rendered. */}
 useEffect(()=>{
  const controller=new AbortController();setFailure('');setMessage('');
  if(provided){setLoading(false);try{verifyFoundationForecastReport(provided,scope)}catch(error){setFailure((error as Error).message)}return()=>controller.abort()}
  setLoading(true);setLoaded(null);const query=new URLSearchParams({data_version_id:scope.versionId,baseline_run_id:base,run_id:base,target:scope.target});
  void request<Dict>('/v1/enterprise-plans/'+encodeURIComponent(scope.planId)+'/foundation-forecast?'+query,{signal:controller.signal}).then(value=>{if(!controller.signal.aborted)setLoaded({key,report:verifyFoundationForecastReport(value,scope)})}).catch(error=>{if(!controller.signal.aborted)setFailure(error.message)}).finally(()=>{if(!controller.signal.aborted)setLoading(false)});
  return()=>controller.abort();
 },[key,provided,revision]);
 const row=selection?.key===key?selection.row:null,grossValue=row?foundationForecastGross(gross,row.source_gross_quantity):null,input=row?foundationForecastInputKey(scope,row.bucket,grossValue??gross,reason):'',currentPreview=preview?.input===input?preview.value:null;
 const valid=!!row&&scope.target==='baseline'&&grossValue!==null&&!!reason.trim()&&reason.length<=1000;
 function refresh(){setRevision(r=>r+1);onRefresh?.()}
 function edit(field:'gross'|'reason',value:string){if(field==='gross')setGross(value);else setReason(value);setPreview(null);setFailure('')}
 async function act(create:boolean){
  if(!valid||!row||busy||(create&&!currentPreview))return;
  const owns=capture(),originalInput=input;setBusy(true);setFailure('');
  const body={...bodyScope,bucket:row.bucket,gross_quantity:grossValue!,reason:reason.trim()};
  try{
   if(create){
    if(requestReceipt.current?.input!==originalInput)requestReceipt.current={input:originalInput,id:crypto.randomUUID().replaceAll('-','')};
    const response=await request<Dict>('/v1/enterprise-plans/'+encodeURIComponent(scope.planId)+'/foundation-forecast-drafts',{method:'POST',body:{...body,confirmation:currentPreview!.confirmation,request_id:requestReceipt.current.id}});
    if(!owns())return;
    const draft=response.draft;
    if(!draft?.id||draft.state!=='DRAFT'||draft.run_id!==null||draft.worker_eligible!==false||draft.changes?.confirmation?.sha256!==currentPreview!.confirmation.sha256)throw Error('候选状态未核实，请刷新历史记录。');
    requestReceipt.current=null;setSelection(null);setPreview(null);setMessage('Gross调整候选已保存；Net未计算。');refresh();onDraft?.(draft.id);
   }else{
    const response=await request<Dict>('/v1/enterprise-plans/'+encodeURIComponent(scope.planId)+'/foundation-forecast-preview',{method:'POST',body});
    if(owns())setPreview({input:originalInput,value:verifyFoundationForecastPreview(response,scope,row.bucket,grossValue!,reason)});
   }
  }catch(error){if(owns())setFailure((error as Error).message)}finally{if(owns())setBusy(false)}
 }
 async function discard(id:string){
  if(busy)return;const owns=capture();setBusy(true);setFailure('');
  try{await request('/v1/drafts/'+encodeURIComponent(id)+'/decision',{method:'POST',scope:{planId:scope.planId,target:scope.target},body:{decision:'discard'}});if(owns()){setLoaded(null);requestReceipt.current=null;setMessage('候选已放弃，来源预测和原计划保持原值。');if(scope.target===id)onDraft?.('baseline');refresh()}}catch(error){if(owns())setFailure((error as Error).message)}finally{if(owns())setBusy(false)}
 }
 const rows=report?filterFoundationForecast(report.rows,{item,location,period}):[],options=(field:'item_id'|'location_id'|'bucket_start')=>Array.from(new Set(report?.rows.map(r=>r[field])??[]));
 const columns:TableColumn<FoundationForecastRow>[]=[
  {id:'item',title:'产品 / 客户',sortValue:r=>r.item_id,value:r=><>{r.item_id}<small>{r.customer_id}</small></>},
  {id:'location',title:'地点',value:r=>r.location_id},
  {id:'period',title:'完整原期间',sortValue:r=>r.bucket_start,value:r=><>{formatSiteTime(r.bucket_start,report?.timezone??'Asia/Shanghai').slice(0,10)}<small>至 {formatSiteTime(r.bucket_end,report?.timezone??'Asia/Shanghai').slice(0,10)}（不含）</small>{r.outside_horizon_portion_preserved&&<small>包含规划窗口外期间</small>}</>},
  {id:'source',title:'来源 Gross',numeric:true,value:r=>r.source_gross_quantity+' '+r.uom},
  {id:'current',title:'当前 Gross',numeric:true,value:r=>r.gross_quantity+' '+r.uom},
  {id:'net',title:'Net',value:()=> '未知'},
  {id:'actions',title:'来源 / 操作',value:r=><><details><summary>查看来源</summary><Definition rows={[["预测桶",r.id],["版本",r.bucket.forecast_version_id],["来源",r.bucket.source_id+' · '+r.bucket.source_section],["源摘要",r.bucket.source_sha256],["源记录",r.bucket.entity_id]]}/></details>{scope.target==='baseline'&&<Button disabled={busy} onClick={()=>{setSelection({key,row:r});setGross(r.gross_quantity);setReason('');setPreview(null);setFailure('')}}>调整 Gross</Button>}</>}
 ];
 const firmColumns:TableColumn<Dict>[]=[{id:'id',title:'订单',value:r=>r.id},{id:'item',title:'产品',value:r=>r.item_id},{id:'customer',title:'客户 / 地点',value:r=>r.customer_id+' / '+r.location_id},{id:'quantity',title:'数量',numeric:true,value:r=>r.quantity+' '+r.uom},{id:'due',title:'交期',value:r=>r.due_at}];
 return <section className="foundation-forecast" aria-label="预测管理">
  <div className="foundation-forecast-heading"><h2>预测管理</h2><Button onClick={refresh} disabled={busy||loading}>刷新</Button></div>
  <ErrorNote error={failure}/>{loading&&!report&&<Loading/>}{message&&<p role="status">{message}</p>}
  {report&&<>
   <div className="foundation-forecast-overview"><Definition rows={[["来源 Gross",report.totals.source_gross_quantity+' '+report.totals.uom],["当前 Gross",report.totals.current_gross_quantity+' '+report.totals.uom],["Firm",report.firm_rows.length+' 条 · '+report.totals.firm_quantity+' '+report.totals.uom],["Net",'未知 · 抵扣政策待确认']]}/></div>
   <div className="foundation-forecast-filters">{(['item_id','location_id','bucket_start'] as const).map((field,index)=><label key={field}>{['产品','地点','原期间起点'][index]}<select aria-label={['预测产品','预测地点','预测原期间'][index]} value={[item,location,period][index]} onChange={e=>[setItem,setLocation,setPeriod][index](e.target.value)}><option value="">全部</option>{options(field).map(value=><option key={value} value={value}>{value}</option>)}</select></label>)}</div>
   <div className="foundation-forecast-table" tabIndex={0} aria-label="来源预测表格，可横向滚动"><DataTable rows={rows} columns={columns} rowId={r=>r.id} empty="当前筛选没有预测桶。"/></div>
   <details><summary>查看版本与来源</summary><Definition rows={[["数据版本",scope.versionId],["来源基线运行",report.source_run_id],["计划",scope.planId]]}/><p>Gross保留完整原期间，与Firm分开呈现；抵扣政策待确认，Net未计算。</p></details>
   {row&&<Dialog title={row.item_id+' · Gross调整'} onClose={()=>{if(!busy){setSelection(null);setPreview(null)}}}><form className="foundation-forecast-editor" onSubmit={e=>{e.preventDefault();void act(false)}}><p>{row.bucket_start} 至 {row.bucket_end} · {row.location_id} · {row.customer_id}</p><label>假设 Gross（{row.uom}）<input aria-label="预测调整 Gross" inputMode="decimal" value={gross} onChange={e=>edit('gross',e.target.value)} disabled={busy}/></label><label>调整原因<input aria-label="预测调整原因" value={reason} maxLength={1000} onChange={e=>edit('reason',e.target.value)} disabled={busy}/></label><p>调整保存为未计算候选；订单抵扣政策待确认，暂不能提交净预测计算。</p>{currentPreview&&<p role="status">来源 {currentPreview.before.gross_quantity} → 假设 {currentPreview.after.gross_quantity} {row.uom}；Net仍未知。</p>}<ErrorNote error={failure}/><div className="foundation-forecast-actions"><Button type="submit" disabled={!valid||busy}>预览调整</Button><Button disabled={!valid||!currentPreview||busy} onClick={()=>void act(true)}>保存未计算候选</Button></div></form></Dialog>}
   <h3>版本与调整历史</h3>{report.history.length===0?<p>当前版本尚无Gross调整候选。</p>:<div className="foundation-forecast-history">{report.history.map(h=><article key={h.draft_id}><h4>{h.bucket.item_id} · {h.bucket.bucket_start}</h4><p>来源 {h.before.gross_quantity} → 假设 {h.after.gross_quantity} {h.bucket.uom} · {h.state==='DISCARDED'?'已放弃':h.state==='RETAINED'?'已保留':'待决定'} · 未计算</p><p>原因：{h.reason}</p><details><summary>查看候选身份</summary><Definition rows={[["候选",h.draft_id],["源版本",h.bucket.forecast_version_id],["来源运行",h.source_run_id],["候选运行",'无 · 尚未计算']]}/></details>{h.state!=='DISCARDED'&&<Button disabled={busy} onClick={()=>void discard(h.draft_id)}>放弃此调整</Button>}</article>)}</div>}
   <details><summary>Firm订单（{report.firm_rows.length}条）</summary><div className="foundation-forecast-table"><DataTable rows={report.firm_rows} columns={firmColumns} rowId={r=>r.id}/></div></details>
  </>}
 </section>;
}
