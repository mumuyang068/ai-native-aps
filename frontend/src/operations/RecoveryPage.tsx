import {useEffect,useState} from 'react';
import {Link} from 'react-router';
import {Checkbox,Input} from 'antd';
import {request} from '../api/client';
import {useActionGuard} from '../app/useActionGuard';
import {Badge,Button,ErrorNote,Loading} from '../components/Controls';
import './recovery.css';

type Case={id:string;state:string;failure?:string;plan_id?:string;data_version_id?:string};
type NativeTask={run_id:string;phase:string;task_ref:string;source_ref:string;source_sha256:string;native:{name:string;status:string;submitted?:string;finished?:string}};
type Audit={id:string;inspection_id:string;at:string;operator:string;reason:string;message:string;runs:Case[]};
type Overview={worker:{state:string};queue:{id:string;state:string}[];cases:Case[];history:Audit[]};
type Inspection=Overview&{id:string;can_resume:boolean;checked_at:string;expires_at:string;native_tasks:NativeTask[];blocking_reasons:{code:string;message:string;run_id?:string}[]};
const statusNames:Record<string,string>={Done:'已完成',Failed:'已失败',Canceled:'已取消',Cancelled:'已取消',Waiting:'等待执行'};
const date=(v?:string)=>v?v.replace('T',' ').slice(0,19):'未提供';

export function RecoveryPage(){
 const[data,setData]=useState<Overview|null>(null),[inspection,setInspection]=useState<Inspection|null>(null),[error,setError]=useState(''),[note,setNote]=useState(''),[busy,setBusy]=useState(false),[operator,setOperator]=useState(''),[reason,setReason]=useState(''),[ack,setAck]=useState(false),[unknown,setUnknown]=useState(false);
 const capture=useActionGuard('operations-recovery');
 useEffect(()=>{const c=new AbortController();request<Overview>('/v1/operations/recovery',{signal:c.signal}).then(setData).catch(e=>{if(e.name!=='AbortError')setError(e.message)});return()=>c.abort()},[]);
 async function refresh(){if(busy)return;const owns=capture();setBusy(true);setError('');try{const value=await request<Overview>('/v1/operations/recovery');if(!owns())return;setData(value);if(inspection&&value.history.some(x=>x.inspection_id===inspection.id)){setInspection(null);setUnknown(false);setAck(false);setNote('处理记录已找到，请核对下方历史。本次没有重复发送确认。')}}catch(e:any){if(owns())setError(e.message)}finally{if(owns())setBusy(false)}}
 async function inspect(){if(busy)return;const owns=capture();setBusy(true);setError('');setNote('');try{const value=await request<Inspection>('/v1/operations/recovery/inspect',{method:'POST'});if(!owns())return;setInspection(value);setAck(false);setUnknown(false)}catch(e:any){if(owns())setError(e.message)}finally{if(owns())setBusy(false)}}
 async function resolve(){if(!inspection||busy||unknown)return;const owns=capture();setBusy(true);setError('');try{const value=await request<Audit>('/v1/operations/recovery/resolve',{method:'POST',body:{inspection_id:inspection.id,operator,reason,acknowledge_no_result:ack}});if(!owns())return;setNote(value.message);setInspection(null);setAck(false);setData(await request<Overview>('/v1/operations/recovery'))}catch(e:any){if(owns()){setError(e.message);if(e.code==='WRITE_STATUS_UNKNOWN')setUnknown(true)}}finally{if(owns())setBusy(false)}}
 const paused=data?.worker.state==='PAUSED_RECOVERY_REQUIRED';
 return <section className="recovery-page">
  <header className="recovery-heading"><div><h1>计算任务核对</h1><p>核实异常任务的原生状态后，人工决定是否恢复计算队列。</p></div><Button icon="reload" disabled={busy} onClick={refresh}>刷新处理记录</Button></header>
  <ErrorNote error={error}/>{note&&<p className="operation-note" role="status">{note}</p>}
  {!data?!error&&<Loading/>:<>
   <div className="recovery-status"><Badge kind={paused?'risk':'good'}>{paused?'计算队列已暂停':'计算队列正常'}</Badge><span>待核对 {data.cases.length} 项</span><span>等待计算 {data.queue.filter(x=>x.state==='QUEUED').length} 项</span></div>
   <div className="recovery-body">
    <section><h2>待核对任务</h2>{data.cases.length?<div className="recovery-table" tabIndex={0} aria-label="待核对计算任务，可横向滚动"><table><thead><tr><th>计算运行</th><th>状态</th><th>关联计划</th><th>企业数据版本</th></tr></thead><tbody>{data.cases.map(row=><tr key={row.id}><td>{row.id}</td><td>状态待核实 · 已暂停</td><td>{row.plan_id?<Link to={'/plans/'+row.plan_id+'?target=baseline'}>{row.plan_id}</Link>:'未提供'}</td><td>{row.data_version_id??'未提供'}</td></tr>)}</tbody></table></div>:<p className="recovery-empty">当前没有需要人工核对的暂停任务，可以返回生产计划继续工作。</p>}
     {paused&&<div className="recovery-read"><p>读取引擎当前队列，并按原始记录中的明确编号核对任务。资料不完整时继续暂停。</p><Button className="primary" icon="search" disabled={busy} onClick={inspect}>{busy?'正在核对…':'读取引擎状态'}</Button></div>}
    </section>
    {inspection&&<section className="recovery-inspection"><div className="recovery-section-title"><h2>本次核对依据</h2><Badge kind={inspection.can_resume?'good':'risk'}>{inspection.can_resume?'终态已核实 · 等待人工确认':'仍有阻断 · 继续暂停'}</Badge></div><p className="detail-note">读取时间 {date(inspection.checked_at)} UTC · 有效至 {date(inspection.expires_at)} UTC</p>
     {inspection.blocking_reasons.length>0&&<ul className="recovery-blocks">{inspection.blocking_reasons.map((x,i)=><li key={i}>{x.message}{x.run_id&&<small>{x.run_id}</small>}</li>)}</ul>}
     {inspection.native_tasks.length>0&&<div className="recovery-table" tabIndex={0} aria-label="原生任务核对结果，可横向滚动"><table><thead><tr><th>原生任务编号</th><th>操作</th><th>引擎状态</th><th>提交时间</th><th>结束时间</th></tr></thead><tbody>{inspection.native_tasks.map(t=><tr key={t.run_id+t.phase+t.task_ref}><td>{t.task_ref}</td><td>{t.phase==='solve'?'规划计算':'导入资料'}</td><td>{statusNames[t.native.status]??t.native.status}</td><td>{date(t.native.submitted)}</td><td>{date(t.native.finished)}</td></tr>)}</tbody></table></div>}
     <details className="recovery-sources"><summary>查看原始记录位置</summary>{inspection.native_tasks.map(t=><p key={t.run_id+t.phase}>{t.source_ref}<small>SHA-256 {t.source_sha256}</small></p>)}</details>
     {inspection.can_resume&&<form className="recovery-confirm" onSubmit={e=>{e.preventDefault();resolve()}}><h3>确认处理</h3><p>本次结束异常运行，保留失败与原始证据；不采用原生结果，不自动重算异常任务。恢复后，队列中 {inspection.queue.filter(x=>x.state==='QUEUED').length} 项等待任务将继续执行。</p><label htmlFor="recovery-operator">核对人<Input id="recovery-operator" value={operator} onChange={e=>setOperator(e.target.value)} required maxLength={80} disabled={busy||unknown}/></label><label htmlFor="recovery-reason">核对依据<Input.TextArea id="recovery-reason" value={reason} onChange={e=>setReason(e.target.value)} required maxLength={500} rows={3} disabled={busy||unknown}/></label><Checkbox checked={ack} onChange={e=>setAck(e.target.checked)} disabled={busy||unknown}>我已核对上述终态，确认本次不恢复计划结果，并恢复等待队列。</Checkbox><Button type="submit" className="primary" disabled={busy||unknown||!ack||!operator.trim()||!reason.trim()}>结束异常记录并恢复队列</Button>{unknown&&<p role="status">处理结果待核实。请先刷新处理记录，不要重复确认。</p>}</form>}
    </section>}
    <section className="recovery-history"><h2>处理记录</h2>{data.history.length?data.history.map(h=><article key={h.id}><div><strong>{h.operator}</strong><time>{date(h.at)} UTC</time></div><p>{h.message}</p><p>{h.reason}</p><details><summary>查看关联运行</summary>{h.runs.map(r=><p key={r.id}>{r.id} · 原状态 {r.state}</p>)}</details></article>):<p className="detail-note">尚无人工恢复记录。</p>}</section>
   </div>
  </>}
 </section>;
}
