import {Link} from 'react-router';
import type {PlanRecord,PlanState} from '../api/contracts';
import {Button,Definition,ErrorNote} from '../components/Controls';
import {ReviewNotes} from './ReviewNotes';
export function WorkspaceReadError({planId,target,error,context,onRetry}:{planId:string;target:string;error:string;context?:{plan:PlanRecord;state:PlanState};onRetry:()=>void}){
 const candidate=context?.state.drafts.find(d=>d.id===target),base=context?.state.baseline.run?.solve_run_id??'';
 const scope=context?{planId,versionId:context.plan.data_version_id,baselineRunId:base}:null;
 return <section className="workspace-empty" aria-label="所选方案读取状态"><h1>暂时无法读取所选{target==='baseline'?'计划':'候选方案'}</h1>
 <Definition rows={[["计划",planId],["所选方案",target==='baseline'?'原始基线':target],...(context?[["数据版本",context.plan.data_version_id],["原基线运行",base||'未提交'],["候选运行",candidate?.run_id??'未取得']] as [string,string][]:[])]}/>
 <ErrorNote error={error}/><p>重新读取只查询原档案，不启动计算。当前候选的数量和交期尚未核验。</p><div className="assistant-links"><Button onClick={onRetry}>重新读取所选方案</Button><Link className="button-link" to={'/plans/'+planId+'?target=baseline&analysis=cockpit'}>返回本计划首页</Link><Link to="/plans">选择其他计划</Link></div>
 {scope&&base&&<section aria-label="只读历史证据"><h2>历史评审只读入口</h2><ReviewNotes scope={scope} targets={candidate?[candidate.id]:[]} readOnly/></section>}
 </section>;
}
