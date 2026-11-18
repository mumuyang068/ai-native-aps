import {useEffect,useRef,useState} from 'react';
import {Link,useNavigate} from 'react-router';
import {request,ApiError} from '../api/client';
import type {ImportTask} from '../api/contracts';
import {Badge,Button,Definition,ErrorNote,Loading} from '../components/Controls';
import {useActionGuard} from '../app/useActionGuard';
import {CanonicalFacts,domainNames} from './CanonicalFacts';
import {OperatorSession} from '../assistant/OperatorSession';
import {CheckpointComparison} from './CheckpointComparison';
import {sourceConfirmation,sourceWorkspacePath,verifySourceSync,sourceWriteNeedsRecovery,type SourceIdentity,type SourceSync} from './sourceOperations';
import './DemoConnectorPanel.css';
import './source-workspace.css';

type RealSource=SourceIdentity&{name:string;company_id:number;source_kind:string;status:string;configured:boolean;current_version_id:string|null;
 connection:{status:string;checked_at:string|null};last_read_at?:string|null;readiness:{ready:boolean;status?:string;blocking_issues?:string[]};
 professional_policy:{id:string;status:string};configured_scope?:{sale_ids?:number[];product_ids?:number[];bom_id?:number};source_scope:Record<string,unknown>|null;last_sync:SourceSync|null;preview:SourceSync|null;history:SourceSync[]};
type Change={model:string;id:number;classification:string;fields:Record<string,{previous:unknown;current:unknown}>;source_known_at:string;previous_revision:string|null;current_revision:string|null};
const labels:Record<string,string>={READY:'待业务确认',COMMITTING:'正在确认版本',REJECTED:'来源拒绝访问',BLOCKED:'存在阻断',COMMITTED:'已确认版本',UNCHANGED:'来源没有变化',READING:'正在读取来源',SYNCED:'已有确认版本',NOT_SYNCED:'尚无确认版本',CONNECTED:'检测时可访问',UNAVAILABLE:'检测时不可访问',NOT_CHECKED:'尚未检测',ADDED:'新增',CHANGED:'变化',REMOVED:'不再出现在本次范围'};
const text=(v:unknown)=>v===null||v===undefined?'未提供':typeof v==='object'?JSON.stringify(v):String(v);
const time=(v?:string|null)=>v?new Date(v).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'未提供';

export function SourceWorkspace({sourceId,syncId}:{sourceId:string;syncId:string|null}){
 const navigate=useNavigate(),[sources,setSources]=useState<RealSource[]|null>(null),[sync,setSync]=useState<SourceSync|null>(null),
  [error,setError]=useState(''),[busy,setBusy]=useState(false),[unknown,setUnknown]=useState(false),[reviewer,setReviewer]=useState(''),
  [groups,setGroups]=useState<string[]>([]),[facts,setFacts]=useState<ImportTask|null>(null),[model,setModel]=useState(''),[page,setPage]=useState(0);
 const[comparisonPrevious,setComparisonPrevious]=useState('');
 const busyRef=useRef(false),chosen=sources?.find(s=>s.id===sourceId),capture=useActionGuard(sourceId);
 const route=sourceId+'|'+(syncId??''),currentRoute=useRef(route),pendingRoute=useRef<string|null>(null);currentRoute.current=route;
 function captureSync(expected=syncId){const alive=capture(),key=sourceId+'|'+(expected??'');return()=>alive()&&currentRoute.current===key;}
 const workspace=useRef<HTMLElement|null>(null);
 useEffect(()=>setComparisonPrevious(''),[chosen?.id,chosen?.current_version_id]);
 useEffect(()=>{workspace.current?.scrollIntoView({block:'start'});},[sourceId]);
 async function refresh(){const value=await request<{connectors:RealSource[]}>('/v1/connectors');setSources(value.connectors.filter(s=>s.source_kind==='REAL_ODOO'));return value;}
 useEffect(()=>{let active=true;busyRef.current=false;setBusy(false);setError('');setSync(null);setFacts(null);setUnknown(false);setGroups([]);
  request<{connectors:RealSource[]}>('/v1/connectors').then(value=>{if(!active)return;const real=value.connectors.filter(s=>s.source_kind==='REAL_ODOO');setSources(real);if(sourceId==='1'&&real.length)navigate(sourceWorkspacePath(real[0].id),{replace:true});}).catch(e=>{if(active)setError(e.message)});return()=>{active=false};},[sourceId]);
 useEffect(()=>{if(pendingRoute.current!==route){busyRef.current=false;setBusy(false);setUnknown(false);setSync(null);setFacts(null);setGroups([]);}
  if(!chosen||!syncId||(busyRef.current&&pendingRoute.current===route))return;
  let active=true;const controller=new AbortController(),current=captureSync(),owns=()=>active&&current();setBusy(true);setSync(null);setFacts(null);setError('');
  request('/v1/connectors/'+encodeURIComponent(chosen.id)+'/syncs/'+encodeURIComponent(syncId),{signal:controller.signal}).then(raw=>{if(owns()){setSync(verifySourceSync(raw,chosen,syncId));setUnknown(false);}}).catch(e=>{if(owns()){setError(e.message);setUnknown(e.code!=='SYNC_TASK_NOT_FOUND');}}).finally(()=>{if(owns())setBusy(false);});
  return()=>{active=false;controller.abort();};
 },[chosen?.id,syncId]);
 async function run(action:()=>Promise<void>,expected=syncId){if(busyRef.current)return;const owns=captureSync(expected),key=sourceId+'|'+(expected??'');pendingRoute.current=key;busyRef.current=true;setBusy(true);setError('');try{await action();}catch(e:any){if(owns())setError(e.message);}finally{if(owns()){busyRef.current=false;setBusy(false);pendingRoute.current=null;}}}
 async function synchronize(){if(!chosen)return;const identifier='SYNC-'+crypto.randomUUID().replaceAll('-',''),owns=captureSync(identifier);
  navigate(sourceWorkspacePath(chosen.id,identifier));setSync(null);setFacts(null);setGroups([]);setReviewer('');setModel('');setPage(0);
  await run(async()=>{try{const raw=await request('/v1/connectors/'+chosen.id+'/preview',{method:'POST',body:{checkpoint:'CURRENT',request_id:identifier}});if(!owns())return;setSync(verifySourceSync(raw,chosen,identifier));setUnknown(false);await refresh();}catch(e){if(owns())setUnknown(sourceWriteNeedsRecovery(e));throw e;}},identifier);
 }
 async function recover(){if(!chosen||!syncId)return;const owns=captureSync();await run(async()=>{try{const raw=await request('/v1/connectors/'+chosen.id+'/syncs/'+encodeURIComponent(syncId));if(!owns())return;setSync(verifySourceSync(raw,chosen,syncId));setUnknown(false);await refresh();}catch(e){if(owns()&&e instanceof ApiError&&e.code==='SYNC_TASK_NOT_FOUND'){setUnknown(false);setSync(null);}throw e;}});}
 async function confirm(){if(!chosen||!sync)return;const owns=captureSync(),body=sourceConfirmation(sync,reviewer,groups);await run(async()=>{try{
  const raw=await request('/v1/connectors/'+chosen.id+'/commit',{method:'POST',body});if(!owns())return;setSync(verifySourceSync(raw,chosen,sync.id));setUnknown(false);await refresh();
 }catch(e){if(owns())setUnknown(sourceWriteNeedsRecovery(e));throw e;}});}
 async function readFacts(){if(!sync?.import_id)return;const owns=captureSync();await run(async()=>{const result=await request<ImportTask>('/v1/imports/'+sync.import_id);if(!owns())return;if(result.id!==sync.import_id||!result.canonical)throw Error('核对资料身份不一致。');setFacts(result);});}
 let canConfirm=false;if(sync&&!busy&&!unknown)try{sourceConfirmation(sync,reviewer,groups);canConfirm=true;}catch{}
 const changes=(sync?.object_changes??[]) as Change[],models=[...new Set(changes.map(c=>c.model))],filtered=changes.filter(c=>!model||c.model===model),shown=filtered.slice(page*20,(page+1)*20);
 return <section ref={workspace} className="source-workspace"><div className="page-title"><h1>企业数据来源</h1><Link to="/imports">返回资料库</Link></div><ErrorNote error={error}/>
 {!sources?<Loading/>:!sources.length?<p>尚未配置真实ERP来源。连接凭据由服务端管理。</p>:<>
 <OperatorSession onAuthorized={()=>void refresh()}/>
 <nav className="source-options" aria-label="已配置ERP来源">{sources.map(s=><Button key={s.id} disabled={busy} aria-pressed={chosen?.id===s.id} onClick={()=>navigate(sourceWorkspacePath(s.id))}>{s.name}</Button>)}</nav>
 {!chosen?<p>请选择已配置来源。</p>:<>
 <header className="connector-heading"><h2>{chosen.name}</h2><Badge>{labels[chosen.status]??chosen.status}</Badge></header>
 <div className="connector-actions"><Button disabled={busy} onClick={()=>run(async()=>{await request('/v1/connectors/'+chosen.id+'/test',{method:'POST'});await refresh();})}>检测连接</Button><Button className="primary" disabled={busy||unknown} onClick={synchronize}>同步并核对变化</Button><Button disabled={busy} onClick={()=>run(async()=>{await refresh();})}>刷新来源状态</Button>{chosen.preview&&!syncId&&<Button disabled={busy} onClick={()=>navigate(sourceWorkspacePath(chosen.id,chosen.preview!.id))}>继续上次核对</Button>}</div>
 <Definition rows={[["企业",chosen.enterprise_id+' · Odoo 公司 '+chosen.company_id],["来源类型","ERP"],["已配置范围",'销售 '+(chosen.configured_scope?.sale_ids?.join('、')??'待读取')+' · 产品 '+(chosen.configured_scope?.product_ids?.join('、')??'待读取')+' · BOM '+(chosen.configured_scope?.bom_id??'待读取')],["连接状态",(labels[chosen.connection.status]??chosen.connection.status)+' · '+time(chosen.connection.checked_at)],["最近同步读取",time(chosen.last_read_at)],["最近确认",time(chosen.last_sync?.committed_at)],["当前数据版本",chosen.current_version_id?<Link to={'/data/'+chosen.current_version_id}>{chosen.current_version_id}</Link>:'尚未确认'],["已确认版本规划状态",chosen.readiness.ready?'READY':chosen.readiness.status??'BLOCKED']]}/>
 <details><summary>查看业务范围与专业政策</summary><Definition rows={[["来源身份",chosen.id],["最近读取范围",text(chosen.source_scope)],["独立专业政策",chosen.professional_policy.id]]}/></details>
 <p>人员资格与换线采用单独确认的专业政策；订单、BOM、设备、库存和制造记录来自Odoo。仅同步已配置的公司及制造业务范围。</p>
 {busy&&<p role="status">正在处理原请求，请勿重复提交。{syncId&&<> 请求 {syncId}</>}</p>}
 {unknown&&<div className="connector-recovery"><strong>原操作状态待核实</strong><p>读取原同步记录，核对实际状态后继续。页面不会自动重新提交。</p><Button disabled={busy||!syncId} onClick={recover}>查询原同步请求</Button></div>}
 {sync&&<section className="connector-preview" aria-label="Odoo来源核对"><div className="connector-section-title"><h2>{labels[sync.status]??sync.status}</h2><span>{sync.id}</span></div>
 <Definition rows={[["来源获知时间",time(sync.as_of)],["本次来源快照",sync.source_snapshot?.sha256??'未取得'],["上一确认版本",sync.parent_version_id??'无'],["本次导入",sync.import_id??'尚未形成可确认资料'],["规划就绪",sync.readiness?.ready?'READY':'BLOCKED']]}/>
 {sync.summary&&<dl className="connector-delta">{([['added','新增'],['updated','变化'],['removed','移出本次范围'],['unchanged','未变化']] as const).map(([key,label])=><div key={key}><dt>{label}</dt><dd>{sync.summary[key]??'未知'}</dd></div>)}</dl>}
 {!!sync.anomalies?.length&&<ul>{sync.anomalies.map((a:any,i:number)=><li key={i}>{a.message??a.code}</li>)}</ul>}
 {!!changes.length&&<><label>业务对象 <select aria-label="来源对象类型" value={model} onChange={e=>{setModel(e.target.value);setPage(0);}}><option value="">全部对象</option>{models.map(m=><option key={m}>{m}</option>)}</select></label>
 <div className="connector-table-scroll" tabIndex={0} aria-label="来源字段变化"><table className="connector-table"><thead><tr><th>来源对象</th><th>变化</th><th>字段、原值与当前值</th></tr></thead><tbody>{shown.map(c=><tr key={c.model+':'+c.id}><th>{c.model}:{c.id}</th><td>{labels[c.classification]??c.classification}</td><td><details><summary>查看字段与修订</summary><p>前修订 {c.previous_revision??'无'} · 当前 {c.current_revision??'无'}</p><table><thead><tr><th>字段</th><th>上一确认值</th><th>本次原值</th></tr></thead><tbody>{Object.entries(c.fields).map(([k,v])=><tr key={k}><th>{k}</th><td>{text(v.previous)}</td><td>{text(v.current)}</td></tr>)}</tbody></table></details></td></tr>)}</tbody></table></div>
 <div className="connector-actions"><Button disabled={!page} onClick={()=>setPage(page-1)}>上一页</Button><span>{page*20+1}—{Math.min((page+1)*20,filtered.length)} / {filtered.length}</span><Button disabled={(page+1)*20>=filtered.length} onClick={()=>setPage(page+1)}>下一页</Button></div></>}
 {sync.import_id&&<Button disabled={busy} onClick={readFacts}>核对转换后的专业字段</Button>}{facts?.canonical&&<CanonicalFacts canonical={facts.canonical}/>}
 {sync.status==='READY'&&<form className="connector-confirm" onSubmit={e=>{e.preventDefault();void confirm();}}><p>同步仅接收来源。请核对原值、专业字段及全部业务领域后确认；确认前不会形成有效DATA，也不会自动计算。</p><label><input type="checkbox" checked={sync.groups.length>0&&groups.length===sync.groups.length} disabled={busy||unknown} onChange={e=>setGroups(e.target.checked?[...sync.groups]:[])}/>已核对全部业务领域</label><div className="connector-groups">{sync.groups.map(g=><label key={g}><input type="checkbox" disabled={busy||unknown} checked={groups.includes(g)} onChange={e=>setGroups(old=>e.target.checked?[...old,g]:old.filter(k=>k!==g))}/>{domainNames[g]??g}</label>)}</div><label className="connector-reviewer">核对人<input aria-label="ERP来源核对人" autoComplete="name" value={reviewer} maxLength={80} disabled={busy||unknown} onChange={e=>setReviewer(e.target.value)}/></label><Button type="submit" className="primary" disabled={!canConfirm}>确认业务资料并保存版本</Button></form>}
 {sync.version_id&&<Link className="button-link primary" to={'/data/'+sync.version_id}>打开数据版本与计划准备</Link>}
 </section>}
 <div className="connector-actions"><label>对照版本 <select aria-label="来源历史对照版本" value={comparisonPrevious} onChange={e=>setComparisonPrevious(e.target.value)}><option value="">上次确认版本</option>{chosen.history.filter(h=>h.status==='COMMITTED'&&h.version_id&&h.version_id!==chosen.current_version_id).map(h=><option key={h.id} value={h.version_id!}>{time(h.committed_at)} · {h.version_id}</option>)}</select></label><CheckpointComparison key={chosen.id+chosen.current_version_id+comparisonPrevious} connectorId={chosen.id} previous={comparisonPrevious||chosen.last_sync?.parent_version_id||null} current={chosen.current_version_id} includeIntermediate={!!comparisonPrevious}/></div>
 <details><summary>同步与确认历史（{chosen.history.length}）</summary><div className="connector-history">{chosen.history.map(h=><article key={h.id}><Button disabled={busy} onClick={()=>navigate(sourceWorkspacePath(chosen.id,h.id))}>{labels[h.status]??h.status} · {time(h.created_at)}</Button><p>{h.id} · {h.version_id??'尚未确认版本'}</p></article>)}</div></details>
 </>}</>}
 </section>;
}
