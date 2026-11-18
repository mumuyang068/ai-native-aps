import {useState} from 'react';
import type {Dict} from '../api/contracts';
import {request} from '../api/client';
import {Button,ErrorNote} from '../components/Controls';
import {exactDemandReviews} from './dailyWorkModel';

/** Same non-executable review Owner; free-text scopes are never guessed into demand links. */
export function ExceptionReviews({scope,demandId,onReview}:{scope:{planId:string;versionId:string;baselineRunId:string};demandId:string;onReview:(target?:string)=>void}){
 const [data,setData]=useState<{key:string;notes:Dict[]}|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const key=JSON.stringify([scope.planId,scope.versionId,scope.baselineRunId,demandId]);
 async function load(){if(busy)return;setBusy(true);setError('');try{
  const q=new URLSearchParams({data_version_id:scope.versionId,baseline_run_id:scope.baselineRunId,draft_ids:''});
  const v=await request<Dict>('/v1/enterprise-plans/'+scope.planId+'/review-notes?'+q);
  if(v.identity?.plan_id!==scope.planId||v.identity?.data_version_id!==scope.versionId||v.identity?.baseline_run_id!==scope.baselineRunId||!Array.isArray(v.notes))throw Error('评审记录不属于当前计划基线。');
  setData({key,notes:exactDemandReviews(v.notes,scope,demandId)});
 }catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 const notes=data?.key===key?data.notes:null;
 return <section aria-label="需求对应的方案评审"><h3>评审与后续</h3><Button disabled={busy} onClick={()=>void load()}>{busy?'正在核对':'查看此需求的评审记录'}</Button><ErrorNote error={error}/>{notes&&(notes.length?notes.map(n=><article key={n.id}><strong>{n.recommendation==='DEFER'?'暂缓':n.recommendation==='RECOMMEND_DISCUSSION'?'建议优先评审':'待讨论'}</strong><small>{n.review_state==='CURRENT_REVIEW'?'对应方案已有讨论记录':'历史意见，需复核当前运行'} · {n.reviewer}</small><p>{n.reason}</p><p>后续：{n.next_action}</p><Button onClick={()=>onReview(n.identity.target)}>查看原方案评审</Button></article>):<p>没有以此需求行精确登记的评审意见。范围文字和其他方案意见不自动视为问题已处理。</p>)}<Button onClick={()=>onReview()}>进入现有方案评审</Button></section>;
}
