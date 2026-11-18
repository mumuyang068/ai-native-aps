import {useRef,useState} from 'react';
import {request} from '../api/client';
import type {Dict,PlanView} from '../api/contracts';
import type {PlanScope} from '../app/scope';
import {useActionGuard} from '../app/useActionGuard';
import {Button,Definition,Dialog,ErrorNote} from '../components/Controls';
import {formatSiteTime} from './timeRange';
import {removedAvailability,siteDateTimeInput,siteInputToIso} from './analysisModel';

export function ResourceUnavailability({scope,baselineRunId,versionId,resource,timezone,initialStart,initialEnd,onTarget}:{scope:PlanScope;baselineRunId:string;versionId:string;resource:Dict;timezone:string;initialStart:string;initialEnd:string;onTarget:(id:string)=>void}){
 const[open,setOpen]=useState(false),[start,setStart]=useState(''),[end,setEnd]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const pending=useRef<{key:string;id:string}|null>(null),capture=useActionGuard(scope.planId+'|'+scope.target);
 const a=siteInputToIso(start,timezone),b=siteInputToIso(end,timezone),removed=a&&b?removedAvailability(resource.availability??[],a,b):0;
 async function create(){if(busy||!a||!b||removed<=0)return;const owns=capture(),key=JSON.stringify([scope.planId,versionId,baselineRunId,resource.id,a,b]);if(pending.current?.key!==key)pending.current={key,id:crypto.randomUUID().replaceAll('-','')};setBusy(true);setError('');try{
  const response=await request<{draft:PlanView['draft']}>('/v1/enterprise-plans/'+scope.planId+'/resource-unavailability-drafts',{method:'POST',body:{data_version_id:versionId,baseline_run_id:baselineRunId,resource_id:resource.id,start_at:a,end_at:b,request_id:pending.current!.id}});
  if(owns()){if(!response.draft||response.draft.run_id)throw Error('响应未提供尚未计算的独立草案，请刷新核对。');setOpen(false);onTarget(response.draft.id)}
 }catch(e:any){if(owns())setError(e.message)}finally{if(owns())setBusy(false)}}
 function show(){setStart(siteDateTimeInput(initialStart,timezone));setEnd(siteDateTimeInput(initialEnd,timezone));setError('');setOpen(true)}
 return <><Button onClick={show}>{resource.role==='OPERATOR'?'模拟此人员缺勤':'模拟此设备停机'}</Button>{open&&<Dialog title={resource.role==='OPERATOR'?'指定人员缺勤模拟':'指定设备停机模拟'} onClose={()=>!busy&&setOpen(false)}>
  <Definition rows={[["资源",resource.id],["原可用日历",resource.calendar_id],["所属基线运行",baselineRunId],["业务时区",timezone]]}/>
  <div className="resource-unavailability-fields"><label>不可用开始<input type="datetime-local" step="1" aria-label="资源不可用开始" value={start} disabled={busy} onChange={e=>setStart(e.target.value)}/></label><label>不可用结束<input type="datetime-local" step="1" aria-label="资源不可用结束" value={end} disabled={busy} onChange={e=>setEnd(e.target.value)}/></label></div>
  <p>日期按 {timezone} 解释，仅从此资源的原可用日历中扣除重合时段，不增加加班，不调整其他资源或确认采购。</p>
  {a&&b&&Date.parse(a)<Date.parse(b)?<p className="resource-reduction-preview">预览：{formatSiteTime(a,timezone)} → {formatSiteTime(b,timezone)}，预计减少原可用时间 {Number((removed/60).toFixed(2))} 分钟。最终范围由服务端核对。</p>:<p role="status">请输入有先后顺序、且时区含义唯一的有效日期。</p>}
  <p>创建后为尚未计算的独立草案；需另行运行并比较结果。模拟许可不等于企业批准或现场执行授权。</p><ErrorNote error={error}/>
  <Button className="primary" disabled={busy||!a||!b||removed<=0} onClick={create}>{busy?'正在创建':'创建资源不可用草案'}</Button>
 </Dialog>}</>;
}
