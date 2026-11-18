import {useEffect,useRef,useState} from 'react';
import type {Dict} from '../api/contracts';
import {request} from '../api/client';
import {Button,Definition,ErrorNote,Select} from '../components/Controls';
import {editorIdentityMatches,editorScopeRequest} from './editorDraftModel';

/** Model results are proposals. Only the existing run/review flow certifies them. */
export function OptimizationPanel({identity,endpoint,onCandidate}:{identity:Dict;endpoint:string;onCandidate:(e:Dict)=>void}){
 const[algorithm,setAlgorithm]=useState('vns'),[scope,setScope]=useState('setup_and_downstream'),[budget,setBudget]=useState('180'),[job,setJob]=useState<Dict|null>(null),[recovering,setRecovering]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const key=JSON.stringify(identity),owner=useRef(key);owner.current=key;
 useEffect(()=>{const c=new AbortController(),captured=key;setJob(null);setError('');setRecovering(true);request<Dict>(endpoint+'/optimization?'+new URLSearchParams(editorScopeRequest(identity)),{signal:c.signal}).then(v=>{if(!c.signal.aborted&&owner.current===captured){if(!editorIdentityMatches(v.identity,identity))throw Error('优化任务不属于当前基线。');setJob(v.job)}}).catch(e=>{if(!c.signal.aborted)setError(e.message)}).finally(()=>{if(!c.signal.aborted&&owner.current===captured)setRecovering(false)});return()=>c.abort()},[key]);
 useEffect(()=>{if(!job||!['QUEUED','RUNNING'].includes(job.state))return;const controller=new AbortController(),captured=key;const timer=setInterval(()=>{request<Dict>(endpoint+'/optimization/'+job.search_id+'?'+new URLSearchParams(editorScopeRequest(identity)),{signal:controller.signal}).then(v=>{if(!controller.signal.aborted&&owner.current===captured&&editorIdentityMatches(v.identity,identity))setJob(v)}).catch(e=>{if(e.name!=='AbortError'&&!controller.signal.aborted)setError(e.message)})},1500);return()=>{controller.abort();clearInterval(timer)}},[job?.search_id,job?.state,key]);
 async function start(){const captured=key;setError('');setBusy(true);try{const v=await request<Dict>(endpoint+'/optimization',{method:'POST',body:{...editorScopeRequest(identity),algorithm,scope,budget_seconds:Number(budget),request_id:crypto.randomUUID().replaceAll('-','')}});if(owner.current===captured){if(!editorIdentityMatches(v.identity,identity))throw Error('优化对象已经变化，请重新读取基线。');setJob(v)}}catch(e:any){if(owner.current===captured)setError(e.message)}finally{if(owner.current===captured)setBusy(false)}}
 const running=recovering||busy||!!job&&['QUEUED','RUNNING'].includes(job.state);
 return <section className="professional-editor-task" aria-label="联合排程优化"><h3>联合排程优化</h3><p>保护原逐行交付与已确认工单，先减少换线，再减少任务变动。物料、人员和日历保持约束。</p><ErrorNote error={error}/>
 <Select label="优化算法" value={algorithm} onChange={setAlgorithm} options={[{value:'vns',label:'关联工序联合重优化'},{value:'cp_sat',label:'CP-SAT 整体约束优化'},{value:'late_acceptance',label:'延迟接受搜索'},{value:'tabu',label:'禁忌搜索'},{value:'pyjobshop',label:'PyJobShop 边界对照'}]}/>
 <Select label="合法优化范围" value={scope} onChange={setScope} options={[{value:'setup_and_downstream',label:'换线工序及关联质检、包装'},{value:'setup_only',label:'仅换线工序，其他任务固定'}]}/>
 <Select label="优化计算预算" value={budget} onChange={setBudget} options={[{value:'30',label:'30 秒'},{value:'60',label:'60 秒'},{value:'180',label:'3 分钟'},{value:'600',label:'10 分钟'}]}/>
 <Button disabled={running} onClick={start}>{running?'正在生成候选':'开始联合优化'}</Button>
 {job&&<section aria-label="联合优化结果"><Definition rows={[["状态",({QUEUED:'等待优化',RUNNING:'正在优化',COMPLETED:'优化结束',FAILED:'优化失败',INTERRUPTED:'任务已中断'} as Record<string,string>)[job.state]??job.state],["范围",`${job.movable_jobs} 道可调整 / ${job.job_count} 道完整工序`],["原换线",`${Number(job.baseline_setup_seconds)/60} 分钟`],[job.model_scope==='RELAXATION_ONLY'?"对照模型换线":"模型候选换线",job.model_scope==='RELAXATION_ONLY'&&job.comparison_objective!=null?`${Number(job.comparison_objective)/60} 分钟（未认证）`:job.setup_seconds!=null?`${Number(job.setup_seconds)/60} 分钟`:'尚无可行候选'],["求解状态",job.model_status==='OPTIMAL'?'有限模型最优':job.model_status==='FEASIBLE'?'可行，尚未证明整体最优':job.model_status??'等待结果'],["耗时",job.elapsed_seconds!=null?`${Number(job.elapsed_seconds).toFixed(1)} 秒`:'预算内计算']]}/>
 {job.candidate_status==='RELAXATION_ONLY'?<p>该对照模型尚未精确表达物料及换线人员日历，不能提交为业务排程。</p>:job.edit?<><p>候选已通过模型筛查，尚待原规划计算与业务核验。</p><Button onClick={()=>onCandidate(job.edit)}>加入候选并核对调整</Button></>:job.state==='COMPLETED'?<p>本次预算内没有产生新的合法调整，原计划保留。</p>:null}
 {['FAILED','INTERRUPTED'].includes(job.state)&&<ErrorNote error={job.stop_reason}/>}</section>}
 </section>;
}
