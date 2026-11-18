import {useState} from 'react';
import type {Dict} from '../api/contracts';
import type {PlanScope} from '../app/scope';
import {useActionGuard} from '../app/useActionGuard';
import {request} from '../api/client';
import {Button,Definition,ErrorNote} from '../components/Controls';
import {ApprovedManufacturingMove} from './ApprovedManufacturingMove';
import {formatSiteTime} from './timeRange';
import {qty,unit} from './planModel';

export function ManufacturingOpportunities({scope,orderId,baselineRunId,dataVersion,orders,timezone,onTarget}:{scope:PlanScope;orderId:string;baselineRunId:string;dataVersion:string;orders:Dict[];timezone:string;onTarget:(id:string)=>void}){
 const[reply,setReply]=useState<Dict|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),capture=useActionGuard(scope.planId+'|'+orderId);
 async function find(){const owns=capture();setBusy(true);setError('');setReply(null);try{
  const r=await request<Dict>('/v1/enterprise-plans/'+scope.planId+'/manufacturing-opportunities?order_id='+encodeURIComponent(orderId));
  if(r.plan_id!==scope.planId||r.data_version_id!==dataVersion||r.baseline_run_id!==baselineRunId)throw new Error('计划或运行已变化，请刷新当前计划后再查找。');
  if(owns())setReply(r);
 }catch(e:any){if(owns())setError(e.message)}finally{if(owns())setBusy(false)}}
 const time=(v:string)=>formatSiteTime(v,timezone);
 return <section aria-label="工单调整机会"><h3>改善欠交的试算机会</h3><p className="detail-note">检查同产品较早的制造建议与较晚的已有工单。只寻找值得试算的窗口，不自动调整安排。</p><Button disabled={busy} onClick={find}>{busy?'正在核对计划证据':'查找工单调整机会'}</Button><ErrorNote error={error}/>
 {reply&&<><p>{reply.message}</p>{reply.candidates.map((c:Dict)=>{const order=orders.find(o=>o.id===c.reference);return <article key={c.reference+'|'+c.new_start_at}><Definition rows={[
 ['待试算工单',c.reference],['同产品较早建议',c.proposed_reference],['关联客户订单',c.supporting_orders.map((o:Dict)=>o.demand_id).join('、')],['期末额外成品',qty(c.ending_surplus_quantity)+' '+unit(c.uom)],['原窗口',time(c.before.start_at)+' → '+time(c.before.end_at)],['候选窗口',time(c.after.start_at)+' → '+time(c.after.end_at)]]}/><p>{c.reason}</p><details><summary>候选依据</summary><p>计算运行 {c.evidence.run_id}</p>{c.supporting_orders.map((o:Dict)=><p key={o.demand_id}>{o.demand_id}：{o.source.artifact} / {o.source.locator}</p>)}<p>其他已有工单保持保护：{c.protected_references.join('、')||'无其他已有工单'}。物料余量来自已核验的期末事件。</p></details>{order&&<ApprovedManufacturingMove key={c.reference+'|'+c.new_start_at} scope={scope} baselineRunId={reply.baseline_run_id} dataVersion={reply.data_version_id} order={order} timezone={timezone} initialStartAt={c.new_start_at} triggerLabel="检查并创建该模拟" onTarget={onTarget}/>}</article>})}{reply.truncated&&<p>仅展示前三个候选，没有进行无界搜索或最优排序。</p>}</>}
 </section>;
}
