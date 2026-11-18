import {useEffect,useState} from 'react';
import {Link,useNavigate} from 'react-router';
import {request} from '../api/client';
import type {PlanRecord,DataVersion} from '../api/contracts';
import {Button,ErrorNote,Loading,Select} from '../components/Controls';
import {previousCockpit} from './cockpitNavigation';
import './cockpit.css';

import {usePlanCatalog} from './PlanDirectory';
import {catalogLabel,catalogTitle,catalogSummary} from './planCatalogModel';
function usePlanEntries(){
 const catalog=usePlanCatalog(),[versions,setVersions]=useState<DataVersion[]>([]);
 useEffect(()=>{if(catalog.entries?.length!==0)return;const c=new AbortController();request<DataVersion[]>('/v1/data-versions',{signal:c.signal}).then(setVersions).catch(()=>{});return()=>c.abort()},[catalog.entries?.length]);
 return {...catalog,entries:catalog.entries?.map(p=>({...p,label:catalogLabel(p)})),versions};
}
export function CockpitPlanPicker({planId}:{planId:string}){
 const{entries,error,page,offset,setOffset}=usePlanEntries(),navigate=useNavigate();
 return <div className="cockpit-plan-picker">{entries?<Select label="当前企业计划" value={planId} options={[...(!entries.some(p=>p.id===planId)?[{value:planId,label:'当前计划 · '+planId.slice(-6)}]:[]),...entries.map(p=>({value:p.id,label:p.label}))]} onChange={id=>navigate('/plans/'+id+'?target=baseline&analysis=cockpit')}/>:error?<Link to="/">重新选择计划</Link>:<span>正在读取计划目录…</span>}{page?.next_offset!=null&&<Button className="quiet" onClick={()=>setOffset(page.next_offset)}>更多计划</Button>}{offset>0&&<Button className="quiet" onClick={()=>setOffset(0)}>首批计划</Button>}</div>;
}
export function CockpitHome(){
 const{entries,versions,error}=usePlanEntries(),navigate=useNavigate();
 useEffect(()=>{if(!entries)return;const last=previousCockpit(localStorage);if(last&&entries.some(p=>p.id===last.planId))navigate('/plans/'+last.planId+'?target='+last.target+'&analysis=cockpit',{replace:true})},[entries,navigate]);
 return <section className="cockpit-home"><header><h1>计划驾驶舱</h1><p>从一份企业计划开始，查看需求缺口与供应、制造安排，继续调查当前需要关注的工作。</p></header><ErrorNote error={error}/>{!entries&&!error?<Loading/>:entries?.length?<><h2>选择工作计划</h2><ul className="cockpit-plan-list">{entries.map(p=><li key={p.id}><div><strong>{catalogTitle(p)}</strong><span>{catalogSummary(p)}</span><span>{p.id}</span></div><Button onClick={()=>navigate('/plans/'+p.id+'?target=baseline&analysis=cockpit')}>打开驾驶舱</Button></li>)}</ul><Link to="/imports">导入另一份企业资料</Link></>:entries&&<div className="cockpit-start"><h2>{versions.some(v=>v.planning_ready)?'资料已确认，尚未建立计划':'还没有可查看的企业计划'}</h2>{versions.filter(v=>v.planning_ready).map(v=><p key={v.id}><Link to={'/data/'+v.id+'/business'}>查看 {v.filename} 的业务概况</Link> · <Link to={'/data/'+v.id}>计划准备</Link></p>)}<p>{versions.some(v=>v.planning_ready)?'选择已确认资料，进入原入口生成计划；打开首页不会自动计算。':'先上传并核对订单、库存、采购和生产能力，在资料确认后使用原计划入口生成计划。'}</p><Link className="button-link" to="/imports">导入并确认企业资料</Link><Link to="/plans">查看已有计划目录</Link></div>}</section>;
}
