import {useState} from 'react';
import type {Dict,ImportTask} from '../api/contracts';
import {Button,Badge} from '../components/Controls';

export function SourceFiles({files,importId}:{files:Dict[];importId:string}){
 return <div className="source-package-files">{files.map(f=><div className="source-package-file" key={f.sha256}>
  <strong>{f.source_system} · {f.filename}</strong><span>业务快照：{f.business_as_of??'缺少依据'}</span>
  <span>状态有效：{f.state_effective_at??'缺少依据'}</span><span>导出时间：{f.exported_at??'缺少依据'}</span>
  <span>来源版本：{f.source_version??'未提供'}</span><code>{f.sha256}</code>
  <a href={'/v1/imports/'+importId+'/originals/'+f.sha256} download>下载这份原件</a>
 </div>)}</div>
}
export function SourcePackagePanel({task,reviewer,setReviewer,disabled,onConfirm}:{task:ImportTask;reviewer:string;setReviewer:(s:string)=>void;disabled:boolean;onConfirm:(change:Dict)=>Promise<unknown>}){
 const [selected,setSelected]=useState<Record<string,string>>({}),[reason,setReason]=useState('');
 if(!task.source_package)return null;
 const recon=task.reconciliation??{},label=recon.status==='READY'?'来源关联与快照核对通过':recon.status==='NOT_READY_FOR_PLANNING'?'当前事实暂不能进入规划':'来源关联等待核对';
 return <section className="source-package-panel" aria-label="多来源关联与规划时点">
  <h2>来源与规划快照</h2><Badge kind={recon.status==='READY'?'good':'risk'}>{label}</Badge><p>规划时点：{recon.planning_as_of??'缺少依据'}。导出时间与业务快照分别保留。</p>
  <SourceFiles files={task.source_package.files} importId={task.id}/>
  <h3>核对跨系统物料身份</h3><p className="detail-note">同码或原件对照只产生候选。核对后确认关联；不会改变原始物料编码。</p>
  <div className="source-package-review"><label>关联核对人<input aria-label="关联核对人" value={reviewer} onChange={e=>setReviewer(e.target.value)} maxLength={80} disabled={disabled}/></label><label>关联依据<input aria-label="关联依据" value={reason} onChange={e=>setReason(e.target.value)} maxLength={500} placeholder="核对的主档、对照记录或业务依据" disabled={disabled}/></label></div>
  <div className="source-package-associations">{(task.material_associations??[]).map(a=>{const key=a.source_system+':'+a.source_code;return <div className="source-package-association" key={key}>
   <div><strong>{a.source_system} · {a.source_code}</strong><p>{a.reason}</p>{a.evidence?.map((e:Dict,i:number)=><small key={i}>对照依据：{e.source_sheet}!{e.source_cell} · {e.record_key} · {e.reason}</small>)}</div>
   <label>统一物料编码<select aria-label={'统一物料 '+a.source_code} value={selected[key]??a.target_id??''} onChange={e=>setSelected({...selected,[key]:e.target.value})} disabled={disabled}><option value="">请选择ERP物料身份</option>{(recon.material_targets??[]).map((id:string)=><option value={id} key={id}>{id}</option>)}</select></label>
   <Button disabled={disabled||!reviewer.trim()||!reason.trim()||!(selected[key]??a.target_id)} onClick={()=>onConfirm({kind:'material_association',source_system:a.source_system,source_code:a.source_code,target_id:selected[key]??a.target_id,reviewer,reason})}>{a.confirmed?'重新确认关联':'确认关联'}</Button><span>{a.confirmed?'已核对 · '+a.reviewer:'待核对'}</span>
  </div>})}</div>
  <h3>库存余额与可用量</h3><div className="source-package-files">{(recon.inventory??[]).map((r:Dict)=><div className="source-package-file" key={r.id}><strong>{r.source_item_id} → {r.item_id??'关联待核对'}</strong><span>实物 {r.physical_quantity??'未核验'} · 质量冻结 {r.quality_blocked??'未核验'} · 其他不可用 {r.other_nonnettable??'未核验'}</span><span>净可用 {r.net_available??'未核验'} {r.uom} · 余额时点 {r.as_of}</span></div>)}</div>
  {(recon.purchases??[]).map((p:Dict)=><div className="source-package-purchase" key={p.reference}><strong>采购 {p.reference}</strong><p>订购 {p.ordered_quantity} · 已收 {p.received_quantity} · 未收 {p.remaining_quantity}</p><p>{p.explanation}</p>{p.receipts?.length>0&&<p>关联记录中的库存实物 {p.physical_stock_quantity??'未核验'}，质量冻结 {p.quality_blocked??'未核验'}，净可用 {p.net_available??'未核验'}；收货与库存{p.stock_counted_once?'已核对为同一事实':'关联尚未核实'}。</p>}</div>)}
 </section>
}
