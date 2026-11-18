import {useEffect,useRef,useState,type FormEvent} from 'react';
import {Link} from 'react-router';
import {ApiError,request} from '../api/client';
import {Badge,Button,Definition,Dialog,ErrorNote,Loading} from '../components/Controls';
import './LiveEnterprisePanel.css';

type Row=Record<string,unknown>;
export type LiveStatus=Row&{branch_id:string;enterprise_id:string;revision:number;as_of:string;event_count:number;actions:Row[];connector_id?:string};
export type AdoptionSelection={plan_id:string;target:string;order_id:string;action_type:'MANUFACTURE'|'PURCHASE';manufacture_id?:string;purchase_reference?:string};
type BoundPreview={fingerprint:string;payload:Row;summary:unknown;limits:unknown[];selection:AdoptionSelection;branch_id:string;expected_revision:number};
type Mode='advance'|'order'|'supply'|'checkpoint'|'restore'|'adoption'|null;
const idPattern=/^[A-Za-z0-9:_@.\/-]{1,200}$/;
const row=(value:unknown):Row=>value&&typeof value==='object'&&!Array.isArray(value)?value as Row:{};
const printable=(value:unknown)=>value===undefined||value===null?'未提供':typeof value==='string'?value:JSON.stringify(value);
const text=(value:unknown)=>typeof value==='string'&&!!value.trim();
function validTime(value:unknown):value is string{return typeof value==='string'&&/(?:Z|[+-]\d{2}:\d{2})$/.test(value)&&Number.isFinite(Date.parse(value))}
function positive(value:unknown){return /^(?:0|[1-9]\d*)(?:\.\d+)?$/.test(String(value))&&Number(value)>0}
function requireValue(ok:unknown,message:string):asserts ok{if(!ok)throw new Error(message)}
function nextDay(value:string){return new Date(Date.parse(value)+86400000).toISOString()}
function displayTime(value:unknown){if(!validTime(value))return printable(value);return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(value))}
function displayYearTime(value:unknown){if(!validTime(value))return printable(value);return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(value))}
function key(){return crypto.randomUUID().replaceAll('-','')}
function fault(error:unknown){return error instanceof Error?error.message:'操作未完成，请读取当前状态核对。'}
function statusName(value:unknown){return ({ADOPTED:'已采纳，等待执行',PENDING:'等待执行',RUNNING:'执行中',IN_PROGRESS:'执行中',COMPLETED:'已完成',CANCELLED:'已取消',BLOCKED:'执行受阻',DELAYED:'执行延期',FAILED:'执行失败',OPEN:'未履行',SHIPPED:'已发货',MANUFACTURE:'生产',PURCHASE:'采购'} as Record<string,string>)[String(value)]??printable(value)}
function eventName(value:unknown){return ({SalesOrderCreated:'新增客户订单',SalesOrderChanged:'订单变更',PurchaseOrderCreated:'采购下单',PurchaseETAChanged:'采购到货预期变化',GoodsReceived:'实际收货',QCReleased:'质量放行',QCHeld:'质量冻结',ResourceUnavailable:'资源不可用',ManufacturingTaskCreated:'制造任务释放',MaterialIssued:'实际领料',OperationStarted:'工序开始',OperationCompleted:'工序完成',FinishedGoodsReceived:'完工入库',ShipmentPosted:'实际发货'} as Record<string,string>)[String(value)]??printable(value)}
function objectRows(value:unknown):Row[]{return Array.isArray(value)?value.map(row):Object.entries(row(value)).map(([id,v])=>({id,...row(v)}))}
export function traceEvents(value:unknown):Row[]{
 const trace=row(value),events=[...objectRows(trace.supply_events),...objectRows(trace.events)],seen=new Set<string>();
 return events.filter(e=>{const id=String(e.event_id??'');if(id&&seen.has(id))return false;if(id)seen.add(id);return true})
  .sort((a,b)=>Date.parse(String(a.occurred_at))-Date.parse(String(b.occurred_at)));
}

export function verifyLiveStatus(value:unknown,branch?:string):LiveStatus{
 const s=row(value);requireValue(text(s.branch_id)&&idPattern.test(String(s.branch_id))&&(!branch||s.branch_id===branch)&&text(s.enterprise_id)&&Number.isInteger(s.revision)&&Number(s.revision)>=0&&validTime(s.as_of)&&Number.isInteger(s.event_count)&&Number(s.event_count)>=0&&Array.isArray(s.actions),'模拟企业身份、时间或修订不完整，请读取最新状态。');return s as LiveStatus;
}
type LiveEventRead={events:Row[];next_sequence:number;has_more:boolean;branch_id:string;source_as_of:string;pages:number};
function eventView(status:LiveStatus){
 const selected=row(status.selected_source);
 return JSON.stringify([status.branch_id,status.business_revision??status.revision,status.as_of,
  selected.checkpoint_id,selected.revision,selected.as_of,selected.revision_semantics]);
}
export async function readLiveEvents(value:unknown,read:(path:string)=>Promise<unknown>=request,onProgress?:(count:number,hasMore:boolean)=>void):Promise<LiveEventRead>{
 const status=verifyLiveStatus(value),endpoint='/v1/simulation/'+encodeURIComponent(status.branch_id),annual=!!status.year_mode,
  selected=row(status.selected_source),events:Row[]=[],seen=new Set<string>();
 let cursor=0,pages=0,hasMore=true;
 if(annual)requireValue(validTime(selected.as_of)&&text(selected.checkpoint_id)&&Number.isSafeInteger(selected.revision),'所选来源身份不完整，请读取最新状态。');
 while(hasMore){
  const page=row(await read(endpoint+'/events?'+new URLSearchParams({after_sequence:String(cursor),limit:'500'})));
  requireValue(Array.isArray(page.events),'实际事件分页响应不完整，请重新读取。');
  if(annual){
   const source=row(page.selected_source);
   requireValue(page.branch_id===status.branch_id&&page.business_revision===(status.business_revision??status.revision)&&
    ['checkpoint_id','revision','as_of','revision_semantics'].every(field=>source[field]===selected[field]),'事件分页的分支、经营修订或所选来源已变化，请重新读取。');
   requireValue(typeof page.has_more==='boolean'&&page.events.length<=500,'实际事件分页标记不完整，请重新读取。');
  }
  const rows=page.events.map(row);
  for(const event of rows){
   requireValue(text(event.event_id)&&!seen.has(String(event.event_id)),'事件分页包含重复或缺失编号，请重新读取。');
   requireValue(!annual||validTime(event.occurred_at)&&Date.parse(event.occurred_at)<=Date.parse(String(selected.as_of)),'事件超出所选来源时间，请重新读取。');
   seen.add(String(event.event_id));
  }
  const next=page.next_sequence??(annual?undefined:cursor+rows.length);
  hasMore=page.has_more===true;
  requireValue(Number.isSafeInteger(next)&&next===cursor+rows.length&&(!hasMore||rows.length>0),'实际事件分页游标没有前进，请重新读取。');
  events.push(...rows);cursor=Number(next);pages+=1;
  requireValue(!annual||events.length<=status.event_count&&(!hasMore||events.length<status.event_count),'实际事件分页数量超出当前经营范围，请重新读取。');
  onProgress?.(events.length,hasMore);
 }
 const current=verifyLiveStatus(await read(endpoint),status.branch_id);
 requireValue(eventView(current)===eventView(status),'读取期间经营修订或所选来源已变化，请重新读取。');
 return{events,next_sequence:cursor,has_more:false,branch_id:status.branch_id,source_as_of:String(selected.as_of??status.as_of),pages};
}
export function liveMutationBody(value:unknown,action:string,payload:Row,idempotencyKey:string,unknown=false){
 const s=verifyLiveStatus(value);requireValue(!unknown,'上次操作状态待核实，当前写入已暂停。');requireValue(/^[a-f0-9]{32}$/.test(idempotencyKey),'操作标识不完整，请重新核对。');
 const now=Date.parse(s.as_of),future=(t:unknown)=>validTime(t)&&Date.parse(t)>=now;
 if(action==='advance')requireValue(future(payload.to)&&Date.parse(String(payload.to))>now,'推进时间必须带时区并晚于当前模拟时间。');
 else if(action==='order')requireValue(text(payload.order_id)&&idPattern.test(String(payload.order_id))&&text(payload.customer_id)&&text(payload.product_id)&&positive(payload.quantity)&&Number.isSafeInteger(Number(payload.quantity))&&Number(payload.quantity)%20===0&&future(payload.due)&&Number.isInteger(payload.priority)&&Number(payload.priority)>=0&&Number(payload.priority)<=2147483647&&(!payload.release_not_before||future(payload.release_not_before)),'请填写准确订单、客户、产品、20件整批数量、优先级和带时区的合法交期。');
 else if(action==='supply'){
  if(payload.kind==='ResourceUnavailable')requireValue(text(payload.resource_id)&&future(payload.start)&&validTime(payload.end)&&Date.parse(payload.end)>Date.parse(String(payload.start)),'请填写准确资源与当前时点之后的有效不可用时段。');
  else if(payload.kind==='PurchaseETAChanged')requireValue(text(payload.po_id)&&future(payload.eta),'请填写准确采购引用与带时区的合法预计到货时间。');
  else if(payload.kind==='QCHeld')requireValue(text(payload.lot_id)&&positive(payload.quantity)&&['pending','available'].includes(String(payload.from_status)),'请填写准确批次、冻结数量和当前质量状态。');
  else throw new Error('当前入口不支持该经营扰动。');
 }else if(action==='checkpoint')requireValue(text(payload.label)&&String(payload.label).length<=100,'请填写检查点名称。');
 else if(action==='restore')requireValue(/^CP-[a-f0-9]{32}$/.test(String(payload.checkpoint_id)),'请填写合法检查点编号。');
 else if(action==='cancel')requireValue(text(payload.action_id),'请选择准确的采纳动作。');
 else if(action==='source-select')requireValue(['BASE','APS_INTERVENTION'].includes(String(s.year_mode))&&objectRows(s.checkpoints).some(point=>point.id===payload.checkpoint_id&&point.available===true),'请选择已经发生的年度来源月份或合法检查点。');
 else throw new Error('当前入口不支持该经营操作。');
 return{payload,expected_revision:s.revision,idempotency_key:idempotencyKey};
}
export function adoptionSelection(value:unknown):AdoptionSelection{
 const v=row(value);requireValue(/^PLAN-[a-f0-9]{16}$/.test(String(v.plan_id))&&/^(?:baseline|DRAFT-[a-f0-9]{16})$/.test(String(v.target))&&text(v.order_id),'请填写准确计划、方案与目标订单。');
 const base={plan_id:String(v.plan_id),target:String(v.target),order_id:String(v.order_id)};
 if(v.action_type==='MANUFACTURE'){requireValue(text(v.manufacture_id),'请选择该运行中的准确制造建议编号。');return{...base,action_type:'MANUFACTURE',manufacture_id:String(v.manufacture_id)}}
 requireValue(v.action_type==='PURCHASE'&&text(v.purchase_reference),'请填写该运行中的准确采购建议引用。');return{...base,action_type:'PURCHASE',purchase_reference:String(v.purchase_reference)};
}
export function bindLiveAdoptionPreview(value:unknown,selection:unknown,status:unknown):BoundPreview{
 const s=verifyLiveStatus(status),selected=adoptionSelection(selection),v=row(value),p=row(v.payload);
 requireValue(!s.year_mode||s.adoption_available===true,'当前来源仅供历史研究。请返回派生分支的当前经营时点后核对采纳。');
 requireValue(/^[a-f0-9]{64}$/.test(String(v.fingerprint))&&p.plan_id===selected.plan_id&&text(p.data_version_id)&&/^API-[a-f0-9]{16}$/.test(String(p.run_id))&&/^[a-f0-9]{64}$/.test(String(p.input_hash))&&p.order_id===selected.order_id&&Array.isArray(v.limits),'采纳预览的计划、运行或指纹不完整，请重新预览。');
 requireValue(v.selection&&JSON.stringify(adoptionSelection(v.selection))===JSON.stringify(selected)&&v.branch_id===s.branch_id&&p.branch_id===s.branch_id&&v.expected_revision===s.revision&&p.world_revision===s.revision&&p.action_type===selected.action_type,'采纳预览与当前分支、修订或所选方案不一致，请重新预览。');
 return{fingerprint:String(v.fingerprint),payload:p,summary:v.summary,limits:v.limits,selection:selected,branch_id:s.branch_id,expected_revision:s.revision};
}
export function liveAdoptionBody(preview:BoundPreview,selection:unknown,status:unknown,idempotencyKey:string,confirmed:boolean,unknown=false){
 const s=verifyLiveStatus(status),selected=adoptionSelection(selection);requireValue(!unknown&&confirmed===true,'请核对研究沙箱采纳范围；状态待核实时不能继续写入。');
 requireValue(!s.year_mode||s.adoption_available===true,'当前来源不能用于模拟采纳，请返回派生分支的当前经营时点。');
 requireValue(/^[a-f0-9]{32}$/.test(idempotencyKey)&&preview.branch_id===s.branch_id&&preview.expected_revision===s.revision&&JSON.stringify(preview.selection)===JSON.stringify(selected),'企业修订或所选动作已变化，请重新预览。');
 return{selection:selected,fingerprint:preview.fingerprint,confirmed:true,expected_revision:s.revision,idempotency_key:idempotencyKey};
}

export function LiveEnterprisePanel({onSync}:{onSync:(connectorId:string)=>void}){
 const[open,setOpen]=useState(false),[busy,setBusy]=useState(false),[unknowns,setUnknowns]=useState<Record<string,string>>({});
 return <><Button className="live-enterprise-entry" icon="clock" onClick={()=>setOpen(true)}>模拟企业经营</Button>{open&&<Dialog title="模拟企业经营" size="840" className="live-enterprise-dialog" onClose={()=>{if(!busy)setOpen(false)}}><LiveEnterpriseWorkspace onSync={onSync} busy={busy} setBusy={setBusy} unknowns={unknowns} onUnknown={(branch,label)=>setUnknowns(current=>({...current,[branch]:label}))}/></Dialog>}</>;
}

function LiveEnterpriseWorkspace({onSync,busy,setBusy,unknowns,onUnknown}:{onSync:(id:string)=>void;busy:boolean;setBusy:(v:boolean)=>void;unknowns:Record<string,string>;onUnknown:(branch:string,label:string)=>void}){
 const[branches,setBranches]=useState<LiveStatus[]>([]),[branch,setBranch]=useState(''),[status,setStatus]=useState<LiveStatus|null>(null),[loaded,setLoaded]=useState(false),[error,setError]=useState(''),[note,setNote]=useState(''),[mode,setMode]=useState<Mode>(null),[events,setEvents]=useState<LiveEventRead|null>(null),[trace,setTrace]=useState<Row|null>(null),[traceOrder,setTraceOrder]=useState(''),[lastReceipt,setLastReceipt]=useState<unknown>(null),[preview,setPreview]=useState<BoundPreview|null>(null),[confirmed,setConfirmed]=useState(false);
 const[history,setHistory]=useState<Row|null>(null);
 const[advanceTo,setAdvanceTo]=useState(''),[order,setOrder]=useState({order_id:'',customer_id:'',product_id:'',quantity:'20',due:'',priority:'1',release_not_before:''}),[supply,setSupply]=useState({kind:'ResourceUnavailable',resource_id:'',start:'',end:'',po_id:'',eta:'',lot_id:'',quantity:'',from_status:'pending'}),[checkpointLabel,setCheckpointLabel]=useState(''),[restoreId,setRestoreId]=useState(''),[selection,setSelection]=useState({plan_id:'',target:'baseline',order_id:'',action_type:'MANUFACTURE',manufacture_id:'',purchase_reference:''});
 const alive=useRef(true),operation=useRef(false),selectedBranch=useRef(''),formRef=useRef<HTMLFormElement>(null);
 const endpoint='/v1/simulation/'+encodeURIComponent(branch),unknown=!!unknowns[branch],locked=busy||unknown;
 const sourceAsOf=row(status?.selected_source).as_of,yearMode=status?.year_mode;
 useEffect(()=>{alive.current=true;const abort=new AbortController();request<{branches:unknown[]}>('/v1/simulation/branches',{signal:abort.signal}).then(result=>{if(!alive.current)return;const values=result.branches.map(v=>verifyLiveStatus(v));setBranches(values);if(values[0]){setBranch(values[0].branch_id);selectedBranch.current=values[0].branch_id;setStatus(values[0]);setAdvanceTo(nextDay(values[0].as_of))}setLoaded(true)}).catch(e=>{if(alive.current&&e.name!=='AbortError'){setError(fault(e));setLoaded(true)}});return()=>{alive.current=false;abort.abort();setBusy(false)}},[setBusy]);
 useEffect(()=>{if(mode)formRef.current?.querySelector<HTMLInputElement>('input,select')?.focus()},[mode]);
 useEffect(()=>{setHistory(null);if(!branch||!yearMode||!validTime(sourceAsOf))return;const abort=new AbortController();request<Row>(endpoint+'/history?'+new URLSearchParams({as_of:sourceAsOf}),{signal:abort.signal}).then(value=>{if(alive.current&&selectedBranch.current===branch)setHistory(value)}).catch(e=>{if(alive.current&&e.name!=='AbortError')setError(fault(e))});return()=>abort.abort()},[branch,endpoint,sourceAsOf,yearMode]);
 async function run(fn:()=>Promise<void>,writeLabel?:string){if(operation.current)return;operation.current=true;setBusy(true);setError('');setNote('');const currentBranch=branch;try{await fn()}catch(e){if(alive.current){setError(fault(e));if(writeLabel&&e instanceof ApiError&&e.code==='WRITE_STATUS_UNKNOWN'){onUnknown(currentBranch,writeLabel);setPreview(null);setConfirmed(false)}if(e instanceof ApiError&&['REVISION_CONFLICT','STALE_PREVIEW','LIVE_REVISION_CONFLICT','SOURCE_VIEW_CHANGED','ADOPTION_PREVIEW_CHANGED'].includes(e.code)){setPreview(null);setConfirmed(false)}}}finally{operation.current=false;if(alive.current)setBusy(false)}}
 async function refresh(id=branch){const fresh=verifyLiveStatus(await request('/v1/simulation/'+encodeURIComponent(id)),id);if(!alive.current||selectedBranch.current!==id)return;setStatus(fresh);setBranches(values=>values.map(v=>v.branch_id===id?fresh:v));setEvents(null);setPreview(null);setConfirmed(false);return fresh}
 async function readEvents(){
  const fresh=await refresh();if(!fresh)return;
  try{
   const result=await readLiveEvents(fresh,request,(count,hasMore)=>{if(alive.current)setNote('已读取 '+count+' 条实际事件'+(hasMore?'，仍有后续，正在继续读取。':'，正在核对来源身份。'))});
   if(!alive.current||selectedBranch.current!==fresh.branch_id)return;
   setEvents(result);
   const ids=new Set(result.events.map(event=>event.event_id)),missing=fresh.actions.flatMap(action=>Array.isArray(action.actual_event_ids)?action.actual_event_ids:[]).filter(id=>!ids.has(id));
   setNote(missing.length?'当前来源已全部读完，但尚未覆盖 '+missing.length+' 条采纳动作实际事件；请核对是否正在查看历史来源，操作状态仍待核实。':'已读取当前来源全部 '+result.events.length+' 条实际事件。');
  }catch(error){if(alive.current)setNote('本次事件读取未完成，当前范围与操作结果仍待核实。');throw error}
 }
 function chooseBranch(id:string){selectedBranch.current=id;setBranch(id);setStatus(null);setMode(null);setEvents(null);setTrace(null);setPreview(null);setConfirmed(false);setLastReceipt(null);void run(async()=>{const fresh=await refresh(id);if(fresh)setAdvanceTo(nextDay(fresh.as_of))})}
 function chooseMode(next:Mode){setMode(mode===next?null:next);setPreview(null);setConfirmed(false);setError('')}
 async function mutate(action:string,payload:Row,label:string){if(!status||locked)return;let body;try{body=liveMutationBody(status,action,payload,key(),unknown)}catch(e){setError(fault(e));return}await run(async()=>{const receipt=await request(endpoint+'/'+action,{method:'POST',body});if(!alive.current)return;setLastReceipt(receipt);await refresh();setEvents(null);setTrace(null);setNote(label+'已返回，请核对当前经营状态与实际事件。')},label)}
 async function submit(event:FormEvent){event.preventDefault();if(mode==='advance')await mutate('advance',{to:advanceTo},'推进经营');if(mode==='order')await mutate('order',{...order,priority:Number(order.priority),...(!order.release_not_before?{release_not_before:undefined}:{})},'新增订单');if(mode==='supply'){const p=supply.kind==='ResourceUnavailable'?{kind:supply.kind,resource_id:supply.resource_id,start:supply.start,end:supply.end}:supply.kind==='PurchaseETAChanged'?{kind:supply.kind,po_id:supply.po_id,eta:supply.eta}:{kind:supply.kind,lot_id:supply.lot_id,quantity:supply.quantity,from_status:supply.from_status};await mutate('supply',p,'供应扰动')}if(mode==='checkpoint')await mutate('checkpoint',{label:checkpointLabel},'保存检查点');if(mode==='restore')await mutate('restore',{checkpoint_id:restoreId},'恢复检查点');if(mode==='adoption')await previewAdoption()}
 async function previewAdoption(){if(!status||locked)return;let selected;try{selected=adoptionSelection(selection)}catch(e){setError(fault(e));return}setPreview(null);setConfirmed(false);const baseline=status;await run(async()=>{const response=await request(endpoint+'/adoption-preview',{method:'POST',body:selected});if(alive.current)setPreview(bindLiveAdoptionPreview(response,selected,baseline))})}
 async function adopt(){if(!status||!preview||locked)return;let body;try{body=liveAdoptionBody(preview,selection,status,key(),confirmed,unknown)}catch(e){setError(fault(e));return}await run(async()=>{const receipt=await request(endpoint+'/adopt',{method:'POST',body});if(!alive.current)return;setLastReceipt(receipt);await refresh();setNote('模拟采纳已登记。推进经营后，再核对实际执行事件。')},'模拟采纳')}
 function updateSelection(name:string,value:string){setSelection(current=>({...current,[name]:value}));setPreview(null);setConfirmed(false)}
 const actions=status?.actions??[],orders=objectRows(status?.year_mode?row(history?.source_state).orders:status?.orders??status?.open_orders),blocks=objectRows(status?.execution_blocks),versionId=status?.current_version_id??status?.data_version_id;
 return <div className="live-enterprise-panel"><ErrorNote error={error}/>{note&&<p className="live-note" role="status">{note}</p>}{!loaded&&!error?<Loading/>:!branches.length?<div className="live-empty"><h3>尚无可用的模拟企业</h3><p>当前环境未提供持续经营分支。</p><Button disabled={busy} onClick={()=>run(async()=>{const response=await request<{branches:unknown[]}>('/v1/simulation/branches');const values=response.branches.map(v=>verifyLiveStatus(v));if(alive.current){setBranches(values);if(values[0]){setBranch(values[0].branch_id);selectedBranch.current=values[0].branch_id;setStatus(values[0])}}})}>重新读取</Button></div>:<>
  <div className="live-topline"><label>经营分支<select aria-label="模拟企业经营分支" value={branch} disabled={busy} onChange={e=>chooseBranch(e.target.value)}>{branches.map(s=><option key={s.branch_id} value={s.branch_id}>{s.branch_id} · {s.enterprise_id}</option>)}</select></label><Button icon="reload" disabled={busy} onClick={()=>run(async()=>{await refresh();setNote('已读取当前经营状态。')})}>读取最新状态</Button></div>
  {!status?<Loading/>:<><header className="live-heading"><div><h3>{status.enterprise_id}</h3><p>模拟时间 {(status.year_mode?displayYearTime:displayTime)(status.as_of)}（上海）</p></div><Badge>{'修订 '+status.revision+' · '+status.event_count+' 条事件'}</Badge></header>
   {!!status.year_mode&&<YearSourcePanel status={status} history={history} busy={locked} onSelect={checkpoint_id=>{void mutate('source-select',{checkpoint_id},'来源选择')}}/>}
   {unknown&&<section className="live-recovery" role="status"><strong>{unknowns[branch]}状态待核实</strong><p>响应中断，当前分支写入已暂停。读取最新状态和实际事件，核对本次动作；系统不会重新提交。</p><Button disabled={busy} onClick={()=>run(readEvents)}>只读核对状态与事件</Button></section>}
   <div className="live-main-actions" aria-label="经营、同步与规划操作"><Button className="primary" disabled={locked} onClick={()=>chooseMode('advance')}>推进模拟企业经营</Button><Button disabled={locked} onClick={()=>onSync(status.connector_id??'live-'+status.branch_id)}>{status.year_mode?'同步所选来源':'同步最新业务数据'}</Button>{typeof versionId==='string'?<Link className="button-link" to={'/data/'+versionId}>打开版本并运行规划</Link>:status.year_mode?<span className="live-context-note">此来源尚未同步保存版本。</span>:<Link className="button-link" to="/plans">打开原计划与规划</Link>}<Button disabled={locked||!!status.year_mode&&status.adoption_available!==true} onClick={()=>chooseMode('adoption')}>核对并模拟采纳</Button></div>
   <p className="live-context-note">经营推进、资料同步和 APS 规划分别执行；采纳后仍需按实际条件推进生产。</p>
   <div className="live-secondary-actions"><Button disabled={locked||status.year_mode==='BASE'} onClick={()=>chooseMode('order')}>新增订单</Button><Button disabled={locked||status.year_mode==='BASE'} onClick={()=>chooseMode('supply')}>施加供应扰动</Button><Button disabled={locked} onClick={()=>chooseMode('checkpoint')}>保存检查点</Button><Button disabled={locked||!!status.year_mode} onClick={()=>chooseMode('restore')}>恢复检查点</Button><Button disabled={busy} onClick={()=>run(readEvents)}>查看实际事件</Button></div>
   {mode&&<form ref={formRef} className="live-form" onSubmit={submit}><div className="live-section-heading"><h3>{({advance:'推进经营时间',order:'新增客户订单',supply:'供应与资源扰动',checkpoint:'保存经营检查点',restore:'恢复经营检查点',adoption:'选择需要采纳的原规划建议'} as Record<string,string>)[mode]}</h3><Button className="quiet" disabled={busy} onClick={()=>setMode(null)}>收起</Button></div>
    {mode==='advance'&&<><label>推进到（含时区）<input required value={advanceTo} onChange={e=>setAdvanceTo(e.target.value)} placeholder="2026-11-04T08:00:00+08:00" disabled={locked}/></label><Button disabled={locked} onClick={()=>setAdvanceTo(nextDay(status.as_of))}>填入下一天</Button></>}
    {mode==='order'&&<div className="live-fields">{([['order_id','订单编号'],['customer_id','客户编号'],['product_id','产品编号'],['quantity','数量（件，20件整批）'],['due','要求交期（含时区）'],['priority','优先级'],['release_not_before','最早释放时间（可选，含时区）']] as const).map(([name,label])=><label key={name}>{label}<input required={name!=='release_not_before'} value={order[name]} inputMode={name==='quantity'||name==='priority'?'numeric':undefined} disabled={locked} onChange={e=>setOrder(current=>({...current,[name]:e.target.value}))}/></label>)}</div>}
    {mode==='supply'&&<><label>扰动类型<select value={supply.kind} disabled={locked} onChange={e=>setSupply(current=>({...current,kind:e.target.value}))}><option value="ResourceUnavailable">资源不可用</option><option value="PurchaseETAChanged">采购预计到货变化</option><option value="QCHeld">批次质量冻结</option></select></label><div className="live-fields">{(supply.kind==='ResourceUnavailable'?[['resource_id','设备或人员编号'],['start','不可用开始（含时区）'],['end','不可用结束（含时区）']]:supply.kind==='PurchaseETAChanged'?[['po_id','采购订单引用'],['eta','新的预计到货（含时区）']]:[['lot_id','库存批次编号'],['quantity','冻结数量（来源单位）']]).map(([name,label])=><label key={name}>{label}<input required value={supply[name as keyof typeof supply]} disabled={locked} onChange={e=>setSupply(current=>({...current,[name]:e.target.value}))}/></label>)}{supply.kind==='QCHeld'&&<label>当前质量状态<select value={supply.from_status} disabled={locked} onChange={e=>setSupply(current=>({...current,from_status:e.target.value}))}><option value="pending">待检</option><option value="available">已放行</option></select></label>}</div></>}
    {mode==='checkpoint'&&<label>检查点名称<input required maxLength={100} value={checkpointLabel} disabled={locked} onChange={e=>setCheckpointLabel(e.target.value)}/></label>}
    {mode==='restore'&&<><label>检查点编号<input required value={restoreId} disabled={locked} onChange={e=>setRestoreId(e.target.value)} placeholder="CP-…"/></label><p className="live-context-note">仅恢复服务允许的合法检查点；经营历史不会回退。</p></>}
    {mode==='adoption'&&<div className="live-fields"><label>原计划编号<input required value={selection.plan_id} disabled={locked} onChange={e=>updateSelection('plan_id',e.target.value)} placeholder="PLAN-…"/></label><label>方案目标<input required value={selection.target} disabled={locked} onChange={e=>updateSelection('target',e.target.value)} placeholder="baseline 或 DRAFT-…"/></label><label>目标订单编号<input required value={selection.order_id} disabled={locked} onChange={e=>updateSelection('order_id',e.target.value)}/></label><label>采纳动作<select value={selection.action_type} disabled={locked} onChange={e=>updateSelection('action_type',e.target.value)}><option value="MANUFACTURE">生产建议</option><option value="PURCHASE">采购建议</option></select></label><label className="live-field-wide">{selection.action_type==='MANUFACTURE'?'准确制造建议编号':'准确采购建议引用'}<input required disabled={locked} value={selection.action_type==='MANUFACTURE'?selection.manufacture_id:selection.purchase_reference} onChange={e=>updateSelection(selection.action_type==='MANUFACTURE'?'manufacture_id':'purchase_reference',e.target.value)}/></label></div>}
    <div className="live-form-submit"><Button type="submit" className={mode==='adoption'?'':'primary'} disabled={locked} loading={busy}>{mode==='adoption'?'读取原建议并预览':({advance:'推进到指定时间',order:'新增这笔订单',supply:'提交这笔扰动',checkpoint:'保存检查点',restore:'核对并恢复'} as Record<string,string>)[mode]}</Button></div>
   </form>}
   {preview&&<section className="live-adoption-preview" aria-label="模拟采纳预览"><h3>{preview.selection.action_type==='MANUFACTURE'?'生产建议采纳预览':'采购建议采纳预览'}</h3><Definition rows={[["原计划",preview.payload.plan_id as string],["数据版本",preview.payload.data_version_id as string],["准确运行",preview.payload.run_id as string],["目标订单",preview.selection.order_id],["数量",printable(preview.payload.quantity)+' '+printable(preview.payload.uom??(preview.selection.action_type==='MANUFACTURE'?'件':'来源单位未提供'))],["经营修订",preview.expected_revision]]}/><AdoptionOperations payload={preview.payload}/>{preview.summary!==undefined&&<details><summary>查看原建议摘要</summary><pre>{JSON.stringify(preview.summary,null,2)}</pre></details>}{preview.limits.length>0&&<ul className="live-limits">{preview.limits.map((limit,i)=><li key={i}>{printable(limit)}</li>)}</ul>}<label className="live-ack"><input type="checkbox" checked={confirmed} disabled={locked} onChange={e=>setConfirmed(e.target.checked)}/>我已核对上述准确方案、数量与经营修订，选择仅在当前研究沙箱中模拟采纳。</label><div className="live-form-submit"><Button className="primary" disabled={locked||!confirmed} onClick={adopt}>确认模拟采纳</Button></div><p className="live-context-note">采纳记录不等于实际开工或完成。实际执行由库存、质量、资源和前序工序共同决定。</p></section>}
   <section className="live-actions-list"><div className="live-section-heading"><h3>已采纳动作与执行结果</h3><span>{actions.length} 项</span></div>{actions.length?actions.map((a,i)=><article key={String(a.action_id??i)}><div className="live-section-heading"><strong>{statusName(row(a.payload).action_type??a.action_type??a.kind)} · {printable(row(a.payload).order_id??a.order_id)}</strong><Badge kind={['BLOCKED','FAILED'].includes(String(a.status))?'risk':''}>{statusName(a.status)}</Badge></div><Definition rows={[["动作编号",printable(a.action_id)],["实际事件",Array.isArray(a.actual_event_ids)?a.actual_event_ids.length+' 条':'尚未提供'],["规划运行",printable(row(a.payload).run_id)]]}/><div className="live-secondary-actions"><Button disabled={busy} onClick={()=>{setTraceOrder(String(row(a.payload).order_id??''));void run(async()=>{const result=await request<Row>(endpoint+'/trace?'+new URLSearchParams({order_id:String(row(a.payload).order_id??'')}));if(alive.current)setTrace(result)})}}>查看订单链路</Button>{!['COMPLETED','CANCELLED'].includes(String(a.status))&&<Button disabled={locked||(Array.isArray(a.actual_event_ids)&&a.actual_event_ids.length>0)} onClick={()=>mutate('cancel',{action_id:a.action_id},'取消采纳动作')}>取消未执行动作</Button>}</div></article>):<p className="live-context-note">尚未采纳任何规划建议。</p>}</section>
   {blocks.length>0&&<details className="live-blocks"><summary>执行阻断记录（{blocks.length}）</summary><pre>{JSON.stringify(blocks,null,2)}</pre></details>}
   <section className="live-trace"><h3>订单履行追溯</h3><form className="live-trace-query" onSubmit={e=>{e.preventDefault();if(!traceOrder.trim())return;void run(async()=>{const result=await request<Row>(endpoint+'/trace?'+new URLSearchParams({order_id:traceOrder.trim()}));if(alive.current)setTrace(result)})}}><label>订单编号<input value={traceOrder} list="live-order-ids" onChange={e=>setTraceOrder(e.target.value)} disabled={busy} required/><datalist id="live-order-ids">{orders.map((o,i)=><option key={i} value={String(o.order_id??o.id)}/>)}</datalist></label><Button type="submit" disabled={busy||!traceOrder.trim()}>读取履行链路</Button></form>{trace&&<><Definition rows={[["目标订单",printable(row(trace.order).order_id??traceOrder)],["订单数量",printable(row(trace.order).quantity)],["实际发货",printable(row(trace.order).shipped)],["剩余未履行",printable(trace.remaining_quantity)]]}/><Events events={traceEvents(trace)}/></>}</section>
   {events&&<LiveEventSection result={events}/>}
   {lastReceipt!==null&&<details className="live-receipt"><summary>查看最近一次操作回执</summary><pre>{JSON.stringify(lastReceipt,null,2)}</pre></details>}
  </>}
 </>}</div>;
}

export function YearSourcePanel({status,history,busy,onSelect}:{status:LiveStatus;history:Row|null;busy:boolean;onSelect:(id:string)=>void}){
 const selected=row(status.selected_source),state=row(history?.source_state),systems=objectRows(history?.systems),points=objectRows(status.checkpoints);
 const groups:{key:string;title:string;columns:[string,string][]}[]=[
  {key:'orders',title:'客户订单与实际发货',columns:[['id','订单'],['product_id','产品'],['quantity','订单件数'],['shipped','实际发货件数'],['due','要求交期']]},
  {key:'purchase_orders',title:'采购订单与收货',columns:[['id','采购订单'],['item_id','物料'],['quantity','采购数量'],['received','已收货'],['outstanding','未收货'],['unit','单位'],['eta','预计到货']]},
  {key:'lots',title:'库存与质量状态',columns:[['id','批次'],['item_id','物料'],['physical','现存'],['available','可用'],['pending','待检'],['held','冻结'],['unit','单位']]},
  {key:'manufacturing_tasks',title:'制造与在制',columns:[['id','任务'],['order_id','订单'],['product_id','产品'],['quantity','任务件数'],['completed_quantity','完工件数'],['status','状态']]},
  {key:'finished_goods',title:'成品当前余额',columns:[['id','制造任务'],['physical','成品件数']]},
  {key:'shipments',title:'实际发货记录',columns:[['id','发货记录'],['order_id','订单'],['job_id','制造任务'],['quantity','件数']]}
 ];
 return <section className="live-year-source" aria-label="年度来源月份与业务资料"><h3>来源月份与检查点</h3><p className="live-context-note">月份来源取次月 1 日零点，查看当时已发生且已获知的业务记录。</p><Definition rows={[["当前经营日期",displayYearTime(status.business_as_of??status.as_of)],["正在查看来源",displayYearTime(selected.as_of)]]}/><label>来源时点<select aria-label="年度来源检查点" value={String(selected.checkpoint_id??'CURRENT')} disabled={busy} onChange={event=>onSelect(event.target.value)}>{points.map(point=><option key={String(point.id)} value={String(point.id)} disabled={point.available!==true}>{point.id==='CURRENT'?'当前经营时点':printable(point.id)} · {displayYearTime(point.as_of)}</option>)}</select></label>{!history?<p className="live-context-note">正在读取所选来源资料。</p>:<><div className="live-year-systems">{systems.map(system=><p key={String(system.system)}><strong>{printable(system.system)}</strong> · {printable(system.records)} 条来源记录 · 截止 {displayYearTime(system.as_of)}</p>)}</div>{groups.map(group=>{const rows=objectRows(state[group.key]);return <details key={group.key}><summary>{group.title}（{rows.length}）</summary>{rows.length?<div className="live-table-scroll" role="region" aria-label={group.title+'，可横向滚动'} tabIndex={0}><table className="live-table"><thead><tr>{group.columns.map(([key,label])=><th key={key}>{label}</th>)}</tr></thead><tbody>{rows.map((record,index)=><tr key={String(record.id??index)}>{group.columns.map(([key])=><td key={key}>{['due','eta'].includes(key)?displayYearTime(record[key]):key==='status'?statusName(record[key]):printable(record[key])}</td>)}</tr>)}</tbody></table></div>:<p className="live-context-note">所选来源没有此类记录。</p>}</details>})}</>}</section>;
}

export function AdoptionOperations({payload}:{payload:Row}){
 if(payload.action_type==='PURCHASE')return <Definition rows={[["采购建议引用",printable(payload.purchase_reference)],["物料编号",printable(payload.item_id)],["供应商编号",printable(payload.supplier_id)],["预计到货（上海）",displayTime(payload.expected_receipt_at)],["来源单位",printable(payload.uom)]]}/>;
 const operations=objectRows(payload.operations);if(!operations.length)return <p className="live-context-note">采购建议及其来源条件见原建议摘要。</p>;
 return <div className="live-table-scroll" role="region" aria-label="采纳工序、具名人员与计划时间，可横向滚动" tabIndex={0}><table className="live-table"><thead><tr><th>工序</th><th>设备 / 具名人员</th><th>数量</th><th>计划开始（上海）</th><th>计划结束（上海）</th></tr></thead><tbody>{operations.map((op,i)=><tr key={String(op.id??op.operation_id??i)}><th>{printable(op.operation_id??op.id)}</th><td>{printable(op.resource_id??op.machine_id)}<small>{printable(op.operator_id??op.person_id??op.operator_ids)}</small></td><td>{printable(op.quantity??payload.quantity)} {printable(op.uom??payload.uom??'件')}</td><td>{displayTime(op.start_at??op.start)}</td><td>{displayTime(op.end_at??op.end)}</td></tr>)}</tbody></table></div>;
}
export function LiveEventSection({result}:{result:LiveEventRead}){
 return <section className="live-event-section"><div className="live-section-heading"><h3>实际经营事件</h3><span>已读取 {result.events.length} 条</span></div><p className="live-context-note" role="status">{result.has_more?'仍有后续事件，当前结果未完整。':'当前来源已全部读完。'} 分支 {result.branch_id}，来源截止 {displayYearTime(result.source_as_of)}。</p><Events events={result.events}/></section>;
}
function Events({events}:{events:Row[]}){
 if(!events.length)return <p className="live-context-note">当前读取范围没有实际事件。</p>;
 return <div className="live-table-scroll" role="region" aria-label="实际事件明细，可横向滚动" tabIndex={0}><table className="live-table"><thead><tr><th>发生时间（上海）</th><th>事件</th><th>相关业务</th><th>数量</th></tr></thead><tbody>{events.map((e,i)=>{const p=row(e.payload);return <tr key={String(e.event_id??i)}><td>{displayTime(e.occurred_at??e.at)}</td><th>{eventName(e.kind)}</th><td>{printable(p.order_id??p.job_id??p.po_id??p.lot_id??p.resource_id)}<details><summary>事件详情</summary><pre>{JSON.stringify(e,null,2)}</pre></details></td><td>{printable(p.quantity)} {printable(p.unit??'')}</td></tr>})}</tbody></table></div>;
}
