import {useEffect,useState} from 'react';
import type {Dict} from '../api/contracts';
import {Button,ErrorNote,Select} from '../components/Controls';
import {siteDateTimeInput} from './analysisModel';
import {formatSiteTime} from './timeRange';
import {demandActionLabel,taskEditFromFields,editorTaskLabel,type TaskFields} from './editorIntentModel';

type Props={options:Dict;timezone:string;edits:Dict[];selectedTask?:string;resetToken:number;busy:boolean;onEdit:(e:Dict)=>void;onPendingChange:()=>void};
const empty:TaskFields={mode:'START_ANCHOR',start:'',end:'',quantity:'',resource:'',recipe:''};
/** Local form state proposes values; only the existing server contract can authorize or calculate them. */
export function ProfessionalTaskEditor(p:Props){
 const [id,setId]=useState(''),[fields,setFields]=useState<TaskFields>(empty),[error,setError]=useState('');
 const tasks:Dict[]=p.options.proposal_tasks??[],task=tasks.find(t=>t.id===id),meta=(p.options.task_options??[]).find((t:Dict)=>t.reference===id),edit=p.edits.find(e=>e.kind==='task'&&e.reference===id),editKey=JSON.stringify(edit);
 useEffect(()=>{if(p.selectedTask)setId(p.selectedTask)},[p.selectedTask]);
 useEffect(()=>{setError('');if(!task){setFields(empty);return}const c=edit?.changes??{},t=c.time;setFields({mode:t?.mode??meta?.time?.modes?.[0]??'START_ANCHOR',start:siteDateTimeInput(t?.start_at??task.start_at,p.timezone),end:siteDateTimeInput(t?.end_at??task.end_at,p.timezone),quantity:c.quantity??String(task.root_quantity??task.quantity),resource:c.resource_id??'',recipe:c.recipe_id??meta?.current_recipe_id??task.recipe_id??''})},[id,editKey,p.resetToken,p.options,p.timezone]);
 const change=(patch:Partial<TaskFields>)=>{p.onPendingChange();setFields(v=>({...v,...patch}));setError('')};
 const blocked=!meta?.editable,reasons: string[]=meta?.blocked_reasons??['当前建议缺少可编辑合同。'];
 function add(){if(!task||!meta?.editable||p.busy)return;try{const candidate=taskEditFromFields({...task,recipe_id:meta.current_recipe_id??task.recipe_id},fields,p.timezone);p.onEdit(candidate);setError('')}catch(e){setError((e as Error).message)}}
 return <section className="professional-editor-task" aria-label="制造建议联合编辑"><h3>制造建议联合编辑</h3>
  <label>选择基线制造建议<select aria-label="选择基线制造建议" value={id} disabled={p.busy} onChange={e=>setId(e.currentTarget.value)}><option value="">选择工序</option>{tasks.map(t=><option key={t.id} value={t.id}>{editorTaskLabel(t,p.timezone)}</option>)}</select></label>
  {task&&<><div className="professional-editor-source"><strong>{task.operation_id??task.native_operation}</strong><span>{meta?.source_status??'状态未知'} · 当前工序 {task.quantity} · 父建议 {task.root_quantity??task.quantity} {meta?.quantity?.uom??''}</span><span>{formatSiteTime(task.start_at,p.timezone)} → {formatSiteTime(task.end_at,p.timezone)}</span><span>资源：{(task.resources??[]).join('、')}</span></div>
   {blocked&&<p className="professional-editor-protected" role="status">不可编辑：{reasons.join('；')||'当前工序受保护。'}</p>}
   <fieldset disabled={p.busy||blocked} className="professional-editor-fields"><legend>拟调整条件 · {p.timezone}</legend>
    <label>时间方式<Select disabled={p.busy||blocked} label="工序时间调整方式" value={fields.mode} options={(meta?.time?.modes??[]).map((v:string)=>({value:v,label:({START_ANCHOR:'固定开始，重算完成',END_ANCHOR:'固定完成，重算开始',WINDOW:'限定开始与结束窗口'} as Record<string,string>)[v]??v}))} onChange={mode=>change({mode:mode as TaskFields['mode']})}/></label>
    {fields.mode!=='END_ANCHOR'&&<label>拟开始时间<input aria-label="工序拟开始时间" type="datetime-local" step="1" value={fields.start} onChange={e=>change({start:e.target.value})}/></label>}
    {fields.mode!=='START_ANCHOR'&&<label>拟结束时间<input aria-label="工序拟结束时间" type="datetime-local" step="1" value={fields.end} onChange={e=>change({end:e.target.value})}/></label>}
    <label>父制造建议数量 · {meta?.quantity?.uom??''}<input aria-label="制造建议目标数量" inputMode="decimal" value={fields.quantity} disabled={!meta?.quantity?.editable} onChange={e=>change({quantity:e.target.value})}/></label>
    <p className="professional-editor-field-note">{meta?.quantity?.editable?`作用于父建议及未锁子工序；最小 ${meta.quantity.minimum??'未声明'}，倍数 ${meta.quantity.multiple??'未声明'}，单笔最大 ${meta.quantity.maximum??'未声明'}。`:meta?.quantity?.reason??'数量受保护。'}</p>
    <label>替代设备<Select disabled={p.busy||blocked} label="制造建议目标设备" value={fields.resource} options={[{value:'',label:'保留当前设备'},...(meta?.resource_options??[]).map((r:Dict)=>({value:r.resource_id,label:r.resource_id+(r.qualified?'':' · '+(r.reason??'无资格')),disabled:!r.qualified}))]} onChange={resource=>change({resource})}/></label>
    <label>替代工艺<Select disabled={p.busy||blocked} label="制造建议目标工艺" value={fields.recipe} options={[...(!meta?.route_options?.some((r:Dict)=>r.recipe_id===fields.recipe)&&fields.recipe?[{value:fields.recipe,label:fields.recipe+' · 当前工艺'}]:[]),...(meta?.route_options??[]).map((r:Dict)=>({value:r.recipe_id,label:r.recipe_id+' · '+r.route_id+' / '+r.bom_id+(r.allowed?'':' · '+(r.reason??'不可采用')),disabled:!r.allowed}))]} onChange={recipe=>change({recipe})}/></label>
    <Button className="primary" disabled={p.busy||blocked} onClick={add}>加入同一调整集合</Button>
   </fieldset>
   <ErrorNote error={error}/><details><summary>影响范围与可选条件</summary><p>父建议：{meta?.root_reference??'未提供'} · {(meta?.affected_operation_ids??[]).length} 道关联工序</p><p>{(meta?.affected_operation_ids??[]).join('、')}</p>{(meta?.route_options??[]).map((r:Dict)=><p key={r.recipe_id}>{r.recipe_id} · {r.effective_from} → {r.effective_to}{r.reason?' · '+r.reason:''}</p>)}{(meta?.resource_options??[]).filter((r:Dict)=>!r.qualified).map((r:Dict)=><p key={r.resource_id}>{r.resource_id}：{r.reason??'无合法替代资格'}</p>)}</details>
  </>}
 </section>;
}

export function ProfessionalDemandEditor(p:Pick<Props,'options'|'edits'|'resetToken'|'busy'|'onEdit'|'timezone'|'onPendingChange'>){
 const [ids,setIds]=useState<string[]>([]),[action,setAction]=useState('UNPLAN'),[query,setQuery]=useState('');
 const existing=p.edits.find(e=>e.kind==='demand'),existingKey=JSON.stringify(existing),rows:Dict[]=p.options.demand_options??[];
 useEffect(()=>{setIds(existing?.demand_ids??[]);setAction(existing?.action??'UNPLAN');setQuery('')},[existingKey,p.resetToken]);
 const visible=rows.filter(r=>!query||[r.demand_id,r.order_ref].some(v=>String(v??'').toLowerCase().includes(query.toLowerCase()))),allowed=ids.length>0&&ids.every(id=>rows.find(r=>r.demand_id===id)?.actions?.includes(action));
 return <section className="professional-editor-task" aria-label="客户订单联合重排"><h3>客户订单重排</h3><p>选择一个或多个需求。撤排只处理可撤销建议供应；原交期、确认供应和执行事实保持。</p>
  <label>查找订单<input aria-label="查找重排订单" type="search" value={query} disabled={p.busy} onChange={e=>setQuery(e.target.value)}/></label>
  <div className="professional-editor-demand-list" role="group" aria-label="选择重排需求">{visible.map(r=><label key={r.demand_id}><input type="checkbox" checked={ids.includes(r.demand_id)} disabled={p.busy||!r.actions?.length} onChange={e=>{p.onPendingChange();setIds(old=>e.target.checked?[...old,r.demand_id]:old.filter(id=>id!==r.demand_id))}}/><span><strong>{r.order_ref??r.demand_id} · {r.demand_id}</strong><small>{r.quantity} · 交期 {formatSiteTime(r.source_due_at,p.timezone)} · 最小交付 {r.minimum_shipment} · 最大延期 {r.maximum_lateness_seconds} 秒</small>{r.blocked_reasons?.length>0&&<small>{r.blocked_reasons.join('；')}</small>}</span></label>)}</div>
  <span>已选择 {ids.length} 个需求</span><Select label="订单重排方式" value={action} disabled={p.busy} options={Object.entries(demandActionLabel).map(([value,label])=>({value,label,disabled:ids.length>0&&!ids.every(id=>rows.find(r=>r.demand_id===id)?.actions?.includes(value))}))} onChange={value=>{p.onPendingChange();setAction(value)}}/>
  <Button disabled={p.busy||!allowed} onClick={()=>p.onEdit({kind:'demand',reference:'customer-demands',action,demand_ids:[...ids].sort()})}>加入订单重排</Button>
 </section>;
}
