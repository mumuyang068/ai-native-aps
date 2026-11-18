import {useEffect,useRef,useState} from 'react';
import {Link,useSearchParams} from 'react-router';
import {analysisContextSearch} from '../planning/analysisModel';
import {request} from '../api/client';
import type {PlanView} from '../api/contracts';
import type {PlanScope} from '../app/scope';
import {Badge,Button,Dialog,ErrorNote} from '../components/Controls';
import {describeScenario} from './scenarioModel';
import {useActionGuard} from '../app/useActionGuard';

export function ScenarioActions({scope,view,onRefresh,onTarget}:{scope:PlanScope;view:PlanView;onRefresh:()=>void;onTarget?:(id:string)=>void}){const[busy,setBusy]=useState(false),[error,setError]=useState(''),[note,setNote]=useState(''),[confirm,setConfirm]=useState(false),owner=useRef(scope);owner.current=scope;
 const[search]=useSearchParams();
 const state=describeScenario(view),draft=view.draft;
 if(draft?.worker_eligible===false)state.canRun=false;
 const visibleNote=note==='计算已提交，请等待真实结果。'?(view.run?.state==='SUCCEEDED'?'本次计算已完成，请核对业务结果。':view.run?.state==='FAILED'?'本次计算未成功，请核对失败信息。':['QUEUED','RUNNING'].includes(view.run?.state??'')?note:'已提交计算请求，正在读取任务状态。'):note;
 const capture=useActionGuard(scope.planId+'|'+scope.target);
 useEffect(()=>{setBusy(false);setError('');setNote('');setConfirm(false)},[scope.planId,scope.target]);
 async function doAction(kind:'run'|'retain'|'discard'){if(!draft||busy)return;const original=scope,owns=capture();setBusy(true);setError('');try{await request('/v1/drafts/'+draft.id+(kind==='run'?'/run':'/decision'),{method:'POST',scope:original,body:kind==='run'?{}:{decision:kind}});if(!owns())return;setNote(kind==='run'?'计算已提交，请等待真实结果。':kind==='retain'?'草案已保留，尚未下达采购或工单。':'草案已放弃，原数据与历史记录保留。');setConfirm(false);onRefresh()}catch(e:any){if(owns())setError(e.message)}finally{if(owns())setBusy(false)}}
 if(!draft)return null;
 return <div className="scenario-actions"><ErrorNote error={error}/>{visibleNote&&<p className="operation-note" role="status">{visibleNote}</p>}<div className="status-pair"><Badge>{state.decisionLabel}</Badge><Badge>{state.calculationLabel}</Badge></div><div className="proposal-actions">{state.canRun&&<Button className="primary" disabled={busy} onClick={()=>doAction('run')}>运行独立情景</Button>}{state.canRetain&&<Button disabled={busy} onClick={()=>doAction('retain')}>保留此方案</Button>}{state.canDiscard&&<Button className="danger" disabled={busy} onClick={()=>setConfirm(true)}>放弃此方案</Button>}{view.result&&<Link className="button-link" to={'/plans/'+scope.planId+'/compare/'+draft.id+'?'+analysisContextSearch(search,draft.id).toString()}>对照原始基线</Link>}{onTarget&&<Button disabled={busy} onClick={()=>onTarget('baseline')}>查看原始基线</Button>}</div>{view.run?.state==='FAILED'&&<p className="detail-note">此草案没有有效结果。当前没有草案失败重试接口；请保留记录，核对条件后提出新的独立草案。</p>}{confirm&&<Dialog title="放弃这份方案？" onClose={()=>!busy&&setConfirm(false)}><p>原始基线和历史记录继续保留。放弃后不能恢复为可运行草案；需要其他安排时，从基线重新提出变更。</p><ErrorNote error={error}/><Button className="danger" disabled={busy} onClick={()=>doAction('discard')}>确认放弃</Button></Dialog>}</div>
}
