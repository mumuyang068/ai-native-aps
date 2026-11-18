import {useEffect,useState,type FormEvent} from 'react';
import {Button,ErrorNote} from '../components/Controls';
import {assistantRequest} from './assistantModel';

type Status={configured:boolean;authenticated:boolean;access_mode?:string;principal?:{expires_at:string}|null};

export function OperatorSession({planId='',onAuthorized}:{planId?:string;onAuthorized:()=>void}){
 const [status,setStatus]=useState<Status|null>(null),[credential,setCredential]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
 useEffect(()=>{let current=true;const read=()=>assistantRequest<Status>('/v1/agent-operator-session',planId).then(value=>{if(current)setStatus(value)}).catch(()=>{if(current)setStatus({configured:false,authenticated:false})});void read();const timer=setInterval(()=>void read(),30000);return()=>{current=false;clearInterval(timer)}},[planId]);
 async function authenticate(event:FormEvent){event.preventDefault();if(!credential.trim()||busy)return;setBusy(true);setError('');const value=credential;setCredential('');try{const next=await assistantRequest<Status>('/v1/agent-operator-session',planId,{credential:value});setStatus(next);if(next.authenticated)onAuthorized()}catch(e){setError(e instanceof Error?e.message:String(e))}finally{setBusy(false)}}
 if(!status)return null;
 if(status.authenticated)return <p className="assistant-source-note" role="status">研究确认身份已验证；每项动作仍需单独核对。</p>;
 return <details className="assistant-operator"><summary>验证研究确认身份</summary><p>查询和研究提议可继续。确认动作前，请使用本次工作环境提供的确认凭据；这不授予正式采购、锁库或发布权限。</p>{!status.configured?<p role="status">当前未配置研究确认渠道，请联系本次环境负责人。</p>:<form onSubmit={authenticate}><label htmlFor="assistant-operator-credential">研究确认凭据<input id="assistant-operator-credential" type="password" autoComplete="off" value={credential} onChange={event=>setCredential(event.target.value)} disabled={busy}/></label><Button type="submit" disabled={!credential.trim()||busy} loading={busy}>验证身份</Button></form>}{error&&<ErrorNote error={error}/>}</details>;
}

export function ResearchAccess({children}:{children:React.ReactNode}){
 const [status,setStatus]=useState<Status|null>(null),[error,setError]=useState('');
 function read(){void assistantRequest<Status>('/v1/agent-operator-session','').then(value=>{setStatus(value);setError('')}).catch(e=>setError(e instanceof Error?e.message:String(e)))}
 useEffect(()=>{read();const timer=setInterval(read,30000);return()=>clearInterval(timer)},[]);
 if(error)return <main className="assistant-access"><h1>计划工作台</h1><ErrorNote error={error}/><Button onClick={read}>重新连接</Button></main>;
 if(!status)return <main className="assistant-access" role="status">正在连接工作台…</main>;
 if(status.access_mode==='TASK_LOCAL_AGENT_ACCESS_READY'&&!status.authenticated)return <main className="assistant-access"><h1>计划工作台</h1><OperatorSession onAuthorized={read}/></main>;
 return <>{children}</>;
}
