import {useEffect,useState} from 'react';
import {Link} from 'react-router';
import {request} from '../api/client';
import type {Dict} from '../api/contracts';
import {Button,ErrorNote,Loading} from '../components/Controls';
import type {AssistantIdentity} from './assistantModel';
import {formatSiteTime} from '../planning/timeRange';
export function AssistantHistory({identity,sessionId}:{identity:AssistantIdentity;sessionId:string}){
 const[offset,setOffset]=useState(0),[loaded,setLoaded]=useState<{key:string;data:Dict}|null>(null),[error,setError]=useState('');
 const key=JSON.stringify([identity.plan_id,identity.data_version_id,identity.baseline_run_id,sessionId,offset]);
 useEffect(()=>{const c=new AbortController();setError('');const q=new URLSearchParams({plan_id:identity.plan_id,data_version_id:identity.data_version_id,baseline_run_id:identity.baseline_run_id,session_id:sessionId,offset:String(offset),limit:'8'});
 request<Dict>('/v1/agent-tasks/history?'+q,{signal:c.signal}).then(data=>{if(data.identity?.plan_id!==identity.plan_id||data.identity?.data_version_id!==identity.data_version_id||data.identity?.baseline_run_id!==identity.baseline_run_id)throw Error('历史任务不属于当前计划版本。');if(!c.signal.aborted)setLoaded({key,data})}).catch(e=>{if(!c.signal.aborted)setError(e.message)});return()=>c.abort();
 },[key]);
 const data=loaded?.key===key?loaded.data:null;
 return <section className="assistant-authorized-history" aria-label="本计划已授权历史研究任务"><h3>本计划的历史研究任务</h3><p>在当前资料版本与原基线内查找。历史任务的确认仍需原授权，不因可查看而取得执行权。</p><ErrorNote error={error}/>{!data&&!error?<Loading/>:data&&<><ul>{data.items.map((t:Dict)=><li key={t.id}>{t.mode==='external'?<Link to={'/plans/'+identity.plan_id+'?'+new URLSearchParams({target:t.identity.target,analysis:'cockpit',assistant:'1',assistant_task:t.id})}>{t.request_summary}</Link>:<strong>{t.request_summary} · 旧模式任务需在原会话继续</strong>}<small>{t.state} · {t.session_relation==='CURRENT_SESSION'?'本会话':'其他会话保存'} · {t.updated_at?formatSiteTime(t.updated_at,'Asia/Shanghai')+' 上海时间':'未记录时间'}</small></li>)}</ul>{!data.items.length&&<p>此计划版本与基线内没有可见历史任务。</p>}<div className="assistant-task-tools">{offset>0&&<Button onClick={()=>setOffset(Math.max(0,offset-8))}>上一页历史任务</Button>}{data.page.next_offset!=null&&<Button onClick={()=>setOffset(data.page.next_offset)}>下一页历史任务</Button>}</div></>}</section>;
}
