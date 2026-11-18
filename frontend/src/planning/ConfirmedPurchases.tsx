import {useRef,useState} from 'react';
import {Input} from 'antd';
import {request} from '../api/client';
import type {Dict,PlanView} from '../api/contracts';
import type {PlanScope} from '../app/scope';
import {useActionGuard} from '../app/useActionGuard';
import {Button,Definition,Dialog,ErrorNote} from '../components/Controls';
import {DataTable,type TableColumn} from '../components/DataTable';
import {qty,unit} from './planModel';
import {formatSiteTime} from './timeRange';
import {supports,capabilityReason} from './capabilityModel';

export function ConfirmedPurchases({scope,baselineRunId,dataVersion,rows,timezone,profile,onTarget,compact=false}:{scope:PlanScope;baselineRunId:string;dataVersion:string;rows:Dict[];timezone:string;compact?:boolean;profile?:string;onTarget:(id:string)=>void}){
 const[selected,setSelected]=useState<Dict|null>(null),[receipt,setReceipt]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),pending=useRef<{key:string;id:string}|null>(null),capture=useActionGuard(scope.planId+'|'+scope.target);
 const time=(v:string)=>formatSiteTime(v,timezone);
 async function create(){if(!selected||busy)return;const owns=capture(),key=JSON.stringify([scope.planId,dataVersion,baselineRunId,selected.id,receipt]);
  if(pending.current?.key!==key)pending.current={key,id:crypto.randomUUID().replaceAll('-','')};
  setBusy(true);setError('');try{const response=await request<{draft:PlanView['draft']}>('/v1/enterprise-plans/'+scope.planId+'/purchase-delay-drafts',{method:'POST',body:{data_version_id:dataVersion,baseline_run_id:baselineRunId,reference:selected.id,new_receipt_at:receipt,request_id:pending.current!.id}});if(owns()&&response.draft){setSelected(null);onTarget(response.draft.id)}}catch(e:any){if(owns())setError(e.message)}finally{if(owns())setBusy(false)}
 }
 const columns:TableColumn<Dict>[]=[
  {id:'identity',title:'确认采购 / 物料',value:p=><>{p.id}<small>{p.item_id} · {p.supplier_id}{p.supply_source_id&&' · '+p.supply_source_id}</small></>},
  {id:'quantity',title:'未收数量',numeric:true,value:p=>qty(p.quantity)+' '+unit(p.uom)},
  {id:'receipt',title:'预计物理到货',value:p=>time(p.physical_receipt_at)},
  {id:'available',title:'质检后可用',value:p=>time(p.available_at)},
  {id:'action',title:'状态 / 操作',value:p=><>已确认 · 未收货{scope.target==='baseline'&&supports(profile,'actions','purchase_delay')&&<Button onClick={()=>{setSelected(p);setReceipt(p.physical_receipt_at);setError('')}}>模拟延期</Button>}</>}
 ];
 return <>{compact?rows.map(p=><div key={p.id}><Definition rows={[["预计物理到货",time(p.physical_receipt_at)],["QC后可用",time(p.available_at)]]}/>{scope.target==='baseline'&&supports(profile,'actions','purchase_delay')&&<Button onClick={()=>{setSelected(p);setReceipt(p.physical_receipt_at);setError('')}}>模拟延期</Button>}</div>):<DataTable rows={rows} columns={columns} rowId={p=>p.id}/>}<p className="detail-note">预计物理到货与质检后可用分别列示；未收采购不计入资料库存。延期仅修改独立草案。</p>{!supports(profile,'actions','purchase_delay')&&<p className="detail-note">{capabilityReason(profile,'actions','purchase_delay')}</p>}
 {selected&&<Dialog title="模拟确认采购延期" onClose={()=>setSelected(null)}><Definition rows={[["确认采购",selected.id],["原预计物理到货",time(selected.physical_receipt_at)],["原质检后可用",time(selected.available_at)]]}/><label htmlFor="purchase-delay-receipt">新预计物理到货（含时区）</label><Input id="purchase-delay-receipt" value={receipt} onChange={e=>setReceipt(e.target.value)} placeholder="2026-11-04T09:00:00+08:00" disabled={busy}/><p>工厂时区：{timezone}。保留原质检等待和未收数量，创建后可运行草案查看影响。</p><ErrorNote error={error}/><Button className="primary" disabled={busy||!receipt.trim()} onClick={create}>{busy?'正在创建草案':'创建延期草案'}</Button></Dialog>}
 </>;
}
