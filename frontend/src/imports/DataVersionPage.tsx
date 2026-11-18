import {useEffect,useRef,useState} from 'react';
import {Link,useParams,useNavigate} from 'react-router';
import {request} from '../api/client';
import type {DataVersion,PlanRecord} from '../api/contracts';
import {Button,Badge,Definition,ErrorNote,Loading} from '../components/Controls';
import {resolveVersionAction} from './importModel';
import {useActionGuard} from '../app/useActionGuard';
import {SourceFiles} from './SourcePackagePanel';
import {CanonicalFacts} from './CanonicalFacts';
type PlanningPreparation={ready:boolean;status:string;policy_id:string;scope:string;source_known_at?:string;source_scope?:{company_id:number;sale_ids:number[];product_ids:number[]};professional_policy?:{id:string;source:string;status:string}};
export function planningPreparationCopy(preparation:Pick<PlanningPreparation,'policy_id'|'scope'>){
 const foundation=preparation.policy_id==='RP-FOUNDATION-01-20261024'&&preparation.scope==='RESEARCH_SIMULATION_ONLY';
 return{
  policyId:preparation.policy_id,scopeLabel:preparation.scope==='RESEARCH_SIMULATION_ONLY'?'隔离研究':preparation.scope,
  title:foundation?'完整企业规划 · 隔离研究':'本次企业版本 · 计划准备',
  intro:foundation?'使用已批准的研究条件，计算本版全部确认需求。':'使用本次企业版本的当前开放需求、可见来源与现有约束准备计算。',
  details:foundation?'固定批量、设备与人员资格、既有工单原窗、采购及库存约束共同参与。毛预测待净化政策；未明确的两条换线时间使用批准的45分钟研究假设。':'按本次企业版本的可见来源处理当前开放需求，保留现有约束，包括库存、确认采购、制造工单和资源条件。',
  createLabel:foundation?'计算完整企业研究计划':'生成本次企业版本的计划'
 };
}
export async function readVersionPlanRecovery(versionId:string,read:(path:string)=>Promise<unknown>=path=>request(path)){
 resolveVersionAction(versionId,[]);
 const[rawVersion,rawPlans]=await Promise.all([read('/v1/data-versions/'+versionId),read('/v1/enterprise-plans')]);
 const version=rawVersion as DataVersion;
 if(!version||version.id!==versionId||!Array.isArray(rawPlans)||rawPlans.some(p=>!p||!/^PLAN-[a-f0-9]{16}$/.test(p.id)||!/^DATA-[a-f0-9]{12}$/.test(p.data_version_id)))throw Error('读取的版本或计划身份不完整，请重新核对当前版本。');
 const plans=rawPlans as PlanRecord[];
 return{version,plans,existing:plans.find(p=>p.data_version_id===versionId)??null};
}
export function DataVersionPage(){const{versionId=''}=useParams(),navigate=useNavigate(),[version,setVersion]=useState<DataVersion|null>(null),[plans,setPlans]=useState<PlanRecord[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState(false),[preparation,setPreparation]=useState<PlanningPreparation|null>(null),[preparationError,setPreparationError]=useState(''),[writeUnknown,setWriteUnknown]=useState(false),[note,setNote]=useState(''),requestBusy=useRef(false);
 const capture=useActionGuard(versionId);
 useEffect(()=>{const c=new AbortController(),owns=capture();requestBusy.current=false;setBusy(false);setVersion(null);setPlans([]);setError('');setNote('');setWriteUnknown(false);Promise.all([request<DataVersion>('/v1/data-versions/'+versionId,{signal:c.signal}),request<PlanRecord[]>('/v1/enterprise-plans',{signal:c.signal})]).then(([v,p])=>{if(owns()){setVersion(v);setPlans(p)}}).catch(e=>{if(e.name!=='AbortError'&&owns())setError(e.message)});return()=>c.abort()},[versionId]);
 useEffect(()=>{if(!version?.canonical){setPreparation(null);return}const c=new AbortController(),owns=capture();setPreparation(null);setPreparationError('');request<any>('/v1/data-versions/'+versionId+'/planning-readiness',{signal:c.signal}).then(r=>{if(owns())setPreparation(r)}).catch(e=>{if(e.name!=='AbortError'&&owns())setPreparationError(e.message)});return()=>c.abort()},[version]);
 async function open(){
  if(!version||requestBusy.current||writeUnknown)return;
  const action=resolveVersionAction(versionId,plans);
  if(action.kind==='CREATE'&&!(version.canonical?preparation?.ready:version.planning_ready))return;
  const owns=capture();requestBusy.current=true;setBusy(true);setError('');setNote('');
  try{
   const plan=action.kind==='OPEN'?await request<PlanRecord>('/v1/enterprise-plans/'+action.planId):await request<PlanRecord>('/v1/enterprise-plans',{method:'POST',body:{data_version_id:versionId,...(version.canonical?{policy_id:preparation?.policy_id}:{})}});
   if(!owns())return;
   if(plan.data_version_id!==versionId||!/^PLAN-[a-f0-9]{16}$/.test(plan.id)){if(action.kind==='CREATE')setWriteUnknown(true);throw Error('计划响应身份不完整，请读取当前版本的计划状态核对。')}
   navigate('/plans/'+plan.id+'?target=baseline');
  }catch(e:any){if(owns()){setError(e.message);if(action.kind==='CREATE'&&e.code==='WRITE_STATUS_UNKNOWN')setWriteUnknown(true)}}finally{if(owns()){requestBusy.current=false;setBusy(false)}}
 }
 async function verifyUnknown(){
  if(requestBusy.current)return;const owns=capture();requestBusy.current=true;setBusy(true);setError('');setNote('');
  try{const latest=await readVersionPlanRecovery(versionId);if(!owns())return;setVersion(latest.version);setPlans(latest.plans);if(latest.existing){setWriteUnknown(false);setNote('已找到本次企业版本关联的计划，可打开查看当前状态。本次操作未重复提交。')}else setNote('已读取本次企业版本与计划列表，暂未找到关联计划。创建操作继续锁定，请稍后再次读取状态。')}
  catch(e:any){if(owns())setError(e.message)}finally{if(owns()){requestBusy.current=false;setBusy(false)}}
 }
 const existing=plans.find(p=>p.data_version_id===versionId);
 const copy=preparation?planningPreparationCopy(preparation):null;
 const recovery=writeUnknown?<div className="operation-note"><strong>创建计划的状态待核实</strong><p>连接中断，当前操作不会重新提交。请读取本次企业版本及其关联计划核对。</p><Button disabled={busy} onClick={verifyUnknown}>读取当前版本与计划状态</Button></div>:null;
 const notices=<><ErrorNote error={error}/>{note&&<p className="operation-note" role="status">{note}</p>}{recovery}</>;
 if(version?.canonical)return <section className="version-page"><div className="page-title"><h1>已保存的企业事实</h1><Link className="button-link" to={'/data/'+versionId+'/business'}>查看业务概况</Link><Link to="/imports">返回企业资料</Link></div>{notices}
 <Definition rows={[["企业",version.canonical.enterprise_id],["数据版本",version.id],["事实状态",<Badge kind="good">已接纳并保存 · {version.canonical.objects.length} 条记录</Badge>],["原始核对",<Link to={'/imports/'+version.import_id}>查看资料与确认记录</Link>]]}/>
 <div className="source-section"><h2>{copy?.title??'本次企业版本 · 计划准备'}</h2><ErrorNote error={preparationError}/>{preparation&&copy?<>
 <Definition rows={[["规划状态",preparation.ready?'READY':'BLOCKED'],["规划范围",copy.scopeLabel],["已选政策",copy.policyId]]}/>
 {preparation.source_scope&&<Definition rows={[["Odoo来源企业",'公司 '+preparation.source_scope.company_id],["知识截止时间",preparation.source_known_at?new Date(preparation.source_known_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'未提供'],["销售范围",preparation.source_scope.sale_ids.join('、')],["物料范围",preparation.source_scope.product_ids.join('、')],["专业规则来源",preparation.professional_policy?.id+' · 独立确认的人员资格与换线政策']]} />}
 <p>{copy.intro}</p><Button className="primary" icon="plan" disabled={busy||writeUnknown||(!existing&&!preparation.ready)} onClick={open}>{busy?'正在读取计划状态…':existing?'打开已有计划':copy.createLabel}</Button><details><summary>查看规划范围与条件</summary><p>{copy.details}</p></details></>:!preparationError&&<Loading/>}</div><CanonicalFacts key={version.id} canonical={version.canonical}/></section>;
 return <section className="version-page"><div className="page-title"><h1>已确认的企业资料</h1><Link className="button-link" to={'/data/'+versionId+'/business'}>查看业务概况</Link><Link to="/imports">返回企业资料</Link></div>{notices}{!version?!error&&<Loading/>:<><p className="page-note">{version.filename}</p><Definition rows={[
 ['数据版本',version.id],['规划条件',<Badge kind={version.planning_ready?'good':'risk'}>{version.planning_ready?'人工确认完整 · 校验通过':'仍不具备规划条件'}</Badge>],['原始导入',<Link to={'/imports/'+version.import_id}>{version.import_id}</Link>],['关联计划',existing?<Link to={'/plans/'+existing.id}>{existing.id}</Link>:'尚未生成'],['数据用途','作为不可变的计划依据，修改原资料后另存新版本']
 ]}/><div className="version-actions"><Button className="primary" icon="plan" disabled={busy||writeUnknown||(!existing&&!version.planning_ready)} onClick={open}>{busy?'正在读取计划状态…':existing?'打开生产计划':'生成第一份生产计划'}</Button></div><div className="source-section"><h2>本版资料</h2>{version.source_package&&<><p>规划快照：{version.source_package.reconciliation.planning_as_of} · 物料关联与确认记录随本版冻结</p><SourceFiles files={version.source_package.files} importId={version.import_id}/></>}<p className="detail-note">{existing?'打开已有计划只读取当前状态，不重新提交计算。':'点击生成后才提交真实计算。保存资料不会自动安排采购或生产。'}</p><details><summary>查看已确认对象与来源</summary><pre className="raw-json">{JSON.stringify({schema_version:version.business_data?.schema_version,customers:version.business_data?.customers,source_sha256:version.business_data?.source_sha256,confirmations:version.confirmations},null,2)}</pre></details></div></>}</section>
}
