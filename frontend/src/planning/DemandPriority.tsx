import {useRef,useState} from 'react';
import type {Dict} from '../api/contracts';
import type {PlanScope} from '../app/scope';
import {useActionGuard} from '../app/useActionGuard';
import {request} from '../api/client';
import {Badge,Button,Definition,ErrorNote} from '../components/Controls';
import {priorityValue,verifyPriorityDraft} from './demandDecisionModel';
import './demand-decisions.css';

export type PriorityDemand={demand_id:string;priority:number|null|undefined;order_ref?:string;item_id?:string;location_id?:string;uom?:string;due_at?:string};
export type DemandPriorityProps={scope:PlanScope;versionId:string;baselineRunId:string;demand:PriorityDemand;onTarget:(id:string)=>void};

export function DemandPriority({scope,versionId,baselineRunId,demand,onTarget}:DemandPriorityProps){
 const key=JSON.stringify([scope.planId,scope.target,versionId,baselineRunId,demand.demand_id,demand.priority]);
 const[editor,setEditor]=useState<{key:string;value:string}|null>(null),[pendingKey,setPendingKey]=useState(''),[failure,setFailure]=useState<{key:string;message:string}|null>(null);
 const capture=useActionGuard(key),pending=useRef<{key:string;id:string}|null>(null),open=editor?.key===key,busy=pendingKey===key;
 const available=scope.target==='baseline'&&!!versionId&&!!baselineRunId&&!!demand.demand_id&&Number.isInteger(demand.priority),next=open?priorityValue(editor.value,demand.priority):null;
 async function create(){
  if(busy||!available||next===null)return;
  const owns=capture(),requestKey=JSON.stringify([key,next]);
  if(pending.current?.key!==requestKey)pending.current={key:requestKey,id:crypto.randomUUID().replaceAll('-','')};
  setPendingKey(key);setFailure(null);
  try{
   const response=await request<Dict>('/v1/enterprise-plans/'+scope.planId+'/demand-priority-drafts',{method:'POST',body:{data_version_id:versionId,baseline_run_id:baselineRunId,demand_id:demand.demand_id,priority:next,request_id:pending.current.id}});
   if(owns()){const id=verifyPriorityDraft(response);setEditor(null);onTarget(id)}
  }catch(error:unknown){if(owns())setFailure({key,message:error instanceof Error?error.message:'草案状态未核实，请刷新当前计划。'})}
  finally{if(owns())setPendingKey('')}
 }
 return <div className="demand-priority">
  <Button disabled={!available||busy} onClick={()=>{setEditor(open?null:{key,value:String(demand.priority)});setFailure(null)}} aria-expanded={open}>模拟需求优先级</Button>
  {!available&&<p className="demand-decision-note">{scope.target!=='baseline'?'请返回原始基线，为指定需求创建独立候选。':'当前缺少原优先级、数据版本或基线运行，不能创建此需求的模拟草案。'}</p>}
  {open&&<form className="demand-priority-editor" onSubmit={event=>{event.preventDefault();void create()}} aria-label="需求优先级草案">
   <h3>模拟此需求优先级</h3>
   <Definition rows={[["订单 / 需求",demand.order_ref??demand.demand_id],["精确需求行",demand.demand_id],["原优先级",String(demand.priority)],["数据版本",versionId],["原始基线运行",baselineRunId]]}/>
   <label>新需求优先级<input aria-label="新需求优先级" type="number" min="0" max="2147483647" step="1" inputMode="numeric" required disabled={busy} value={editor.value} onChange={event=>setEditor({key,value:event.target.value})}/></label>
   <p className="demand-decision-note">数值越小，需求优先级越高。此候选只调整当前需求行的 priority；创建后为 <Badge>NOT_RUN · 尚未计算</Badge>，在原方案控件提交计算后，再查看各需求的实际取舍。</p>
   <ErrorNote error={failure?.key===key?failure.message:''}/>
   <div className="demand-decision-actions"><Button className="primary" type="submit" disabled={busy||next===null}>{busy?'正在创建草案':'创建需求优先级草案'}</Button><Button disabled={busy} onClick={()=>setEditor(null)}>取消</Button></div>
  </form>}
 </div>;
}
