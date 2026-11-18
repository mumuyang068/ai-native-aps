import {useState,useRef} from 'react';
import type {Dict,PlanView} from '../api/contracts';
import type {PlanScope} from '../app/scope';
import {request} from '../api/client';
import {useActionGuard} from '../app/useActionGuard';
import {Button,Dialog,Definition,ErrorNote} from '../components/Controls';

export function SupplyTermsSimulation({scope,baselineRunId,dataVersion,input,onTarget}:{scope:PlanScope;baselineRunId:string;dataVersion:string;input:Dict;onTarget:(id:string)=>void}){
 const[open,setOpen]=useState(false),[sourceId,setSourceId]=useState(input.supply_sources[0].id),[capacity,setCapacity]=useState(''),[moq,setMoq]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const capture=useActionGuard(scope.planId+'|'+scope.target),pending=useRef<{key:string;id:string}|null>(null),source=input.supply_sources.find((s:Dict)=>s.id===sourceId);
 function select(id:string){const s=input.supply_sources.find((x:Dict)=>x.id===id);setSourceId(id);setCapacity(s.capacity.quantity);setMoq(s.minimum_order_quantity);setError('')}
 async function create(){if(busy)return;const owns=capture(),key=JSON.stringify([scope.planId,baselineRunId,sourceId,capacity,moq]);if(pending.current?.key!==key)pending.current={key,id:crypto.randomUUID().replaceAll('-','')};setBusy(true);setError('');try{
  const r=await request<{draft:PlanView['draft']}>('/v1/enterprise-plans/'+scope.planId+'/source-terms-drafts',{method:'POST',body:{data_version_id:dataVersion,baseline_run_id:baselineRunId,source_id:sourceId,capacity_quantity:capacity,minimum_order_quantity:moq,request_id:pending.current!.id}});
  if(owns()&&r.draft){setOpen(false);onTarget(r.draft.id)}
 }catch(e:any){if(owns())setError(e.message)}finally{if(owns())setBusy(false)}}
 return <><Button onClick={()=>{select(sourceId);setOpen(true)}}>模拟供应条件</Button>{open&&<Dialog title="PRIORITY 供应来源条件模拟" onClose={()=>!busy&&setOpen(false)}>
  <label>目标供应来源<select aria-label="模拟供应来源" value={sourceId} disabled={busy} onChange={e=>select(e.target.value)}>{input.supply_sources.map((s:Dict)=><option key={s.id} value={s.id}>{s.id} · {s.supplier_id} · 优先级{s.priority}</option>)}</select></label>
  <Definition rows={[["原全期供应能力",source.capacity.quantity+' '+source.capacity.uom],["原最小采购量",source.minimum_order_quantity],["采购倍数",source.order_multiple],["QC等待",source.receiving_qc.wait_seconds/3600+' 小时'],["模型单价",source.unit_price+' '+source.currency]]}/>
  <div className="supply-terms-fields"><label>假设全期供应能力<input aria-label="假设全期供应能力" inputMode="decimal" value={capacity} disabled={busy} onChange={e=>setCapacity(e.target.value)}/></label>
  <label>假设MOQ<input aria-label="假设最小采购量" inputMode="decimal" value={moq} disabled={busy} onChange={e=>setMoq(e.target.value)}/></label></div>
  <p>仅在独立情景调整选中来源的容量和MOQ假设。confirmed与proposed采购仍共用全期容量，原采购、工单、来源、价格、QC及企业版本保持。供应商许可尚未确认。</p>
  <ErrorNote error={error}/><Button className="primary" disabled={busy||!capacity||!moq||(capacity===source.capacity.quantity&&moq===source.minimum_order_quantity)} onClick={create}>{busy?'正在创建':'创建供应条件模拟草案'}</Button>
 </Dialog>}</>;
}
