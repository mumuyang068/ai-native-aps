import type {PlanView} from '../api/contracts';
import './editor-failure-details.css';

export function editorFailureObservation(view:PlanView){
 const details=view.failure_details,run=view.run;
 if(!details)return {status:'MISSING' as const};
 if(run?.state!=='FAILED'||!run.solve_run_id||typeof run.input_hash!=='string'||!run.input_hash||details.schema!=='native-editor-failure/1'||details.scope!=='NATIVE_FAILURE_OBSERVATION'||details.run_id!==run.solve_run_id||details.input_hash!==run.input_hash||details.schedule_certified!==false||details.infeasibility_proven!==false)return {status:'MISMATCH' as const};
 return details.capture_status==='OBSERVED'?{status:'OBSERVED' as const,details}:{status:'UNAVAILABLE' as const,details};
}

/** Native timestamps without an offset are site wall time; retain their exact seconds. */
const nativeTime=(value:string|undefined)=>value?value.replace('T',' '):'未提供';
const missingLabel=(value:string)=>({native_failure_capture:'未取得原生失败记录',native_problem_for_infeasible_operation:'原生工序报告不可行，但未提供对应问题原因'}[value]??value);

export function EditorFailureDetails({view,timezone}:{view:PlanView;timezone:string}){
 if(view.run?.state!=='FAILED'||(!view.failure_details&&!String(view.run.failure_code??'').startsWith('EDITOR_')))return null;
 const observation=editorFailureObservation(view);
 if(observation.status==='MISMATCH')return <section className="editor-failure-details" aria-label="本次原生失败诊断"><h3>本次计算问题</h3><p role="alert">诊断记录与当前运行或输入不一致，已停止展示。请由运行维护人员核对。</p></section>;
 if(observation.status==='MISSING')return <section className="editor-failure-details" aria-label="本次原生失败诊断"><h3>本次计算问题</h3><p>本次运行未提供原生失败诊断，无法定位具体工序或资源窗口。</p></section>;
 const d=observation.details,problems=[...(d.problems??[])].sort((a,b)=>Number(b.entity==='capacity')-Number(a.entity==='capacity')),operations=d.operations??[],resources=d.resources??[];
 return <section className="editor-failure-details" aria-label="本次原生失败诊断"><h3>本次计算的原生问题</h3><p>计算失败，候选没有可用排程。{d.code?`错误：${d.code}。`:''}</p>
  <p className="editor-failure-note">原生问题快照可能含既有问题，不能据此全部归因于本次调整或判定计划全局无解。未带时区的时间按 {timezone} 显示。</p>
  {observation.status==='UNAVAILABLE'?<p role="status">原生失败诊断不可用，具体问题与资源窗口未取得。</p>:<>
   <p>原生报告不可行的工序：{d.native_false_refs?.join('、')||'未提供'}</p>
   {problems.length?<ul className="editor-failure-problems">{problems.map((p,i)=><li key={i}><strong>{p.code} · {p.entity}</strong><span>对象：{[p.owner_name,p.owner_reference].filter(Boolean).join(' / ')||p.owner_type||'未提供'}</span><span>{p.description||'未提供问题描述'}</span><span>{nativeTime(p.start_at)} → {nativeTime(p.end_at)}</span></li>)}</ul>:<p>原生问题列表未提供具体原因。</p>}
   {operations.length>0&&<details><summary>相关原生工序 · {operations.length} 条</summary><ul>{operations.map((o,i)=><li key={o.reference+'|'+i}><strong>{o.reference} · {o.operation}</strong><span>{o.quantity} · {o.status} · {o.feasible===false?'原生报告不可行':o.feasible===true?'原生工序标志为可行':'原生标志未提供'}</span><span>{nativeTime(o.start)} → {nativeTime(o.end)}</span><span>资源：{o.resources?.join('、')||'未提供'}{o.owner?` · 父工序：${o.owner}`:''}</span></li>)}</ul></details>}
   {resources.length>0&&<details><summary>关联资源占用 · {resources.map(r=>r.resource_id).join('、')}</summary>{resources.map(r=><div key={r.resource_id}><h4>{r.resource_id}</h4>{r.loadplans?.length?<ul>{r.loadplans.map((l,i)=><li key={i}><strong>原生工序 {l.operation_reference} · 负荷 {l.quantity}</strong><span>{nativeTime(l.start_at)} → {nativeTime(l.end_at)}</span></li>)}</ul>:<p>未提供资源占用窗口。</p>}</div>)}</details>}
  </>}
  {!!d.missing_references?.length&&<p>未取得的原生引用：{d.missing_references.join('、')}</p>}
  {!!d.missing_evidence?.length&&<p>诊断缺项：{d.missing_evidence.map(missingLabel).join('；')}</p>}
 </section>;
}
