import {useEffect,useState} from 'react';
import {Link} from 'react-router';
import {request} from '../api/client';
import type {Dict} from '../api/contracts';
import {Button,ErrorNote,Loading} from '../components/Controls';
import {DataTable} from '../components/DataTable';
import {catalogTitle,catalogSummary,type CatalogPlan} from './planCatalogModel';
export function usePlanCatalog(){
 const[offset,setOffset]=useState(0),[page,setPage]=useState<{offset:number;data:Dict}|null>(null),[error,setError]=useState('');
 useEffect(()=>{const c=new AbortController();setError('');request<Dict>('/v1/enterprise-plans/catalog?'+new URLSearchParams({offset:String(offset),limit:'20'}),{signal:c.signal}).then(data=>{if(data.schema_version!=='plan-catalog/1')throw Error('计划目录响应不完整。');if(!c.signal.aborted)setPage({offset,data})}).catch(e=>{if(!c.signal.aborted)setError(e.message)});return()=>c.abort()},[offset]);
 const current=page?.offset===offset?page.data:null;
 return {entries:current?.items as CatalogPlan[]|undefined,page:current?.page,error,offset,setOffset};
}
export function PlanDirectory(){const{entries,page,error,offset,setOffset}=usePlanCatalog();
 return <section className="list-page"><div className="page-title"><h1>生产计划</h1><Link className="button-link" to="/imports">建立工厂数据</Link></div><p className="page-note">按企业、来源截至和计算状态识别计划。保存的提交状态不等于原生结果已重新核验。</p><ErrorNote error={error}/>{entries?<><div className="list-table"><DataTable rows={entries} rowId={r=>r.id} columns={[{id:'business',title:'企业与业务范围',value:r=><><Link to={'/plans/'+r.id+'?target=baseline&analysis=cockpit'}>{catalogTitle(r)}</Link><small>{catalogSummary(r)}</small><small>{r.id}</small></>},{id:'demand',title:'来源需求摘要',value:r=><>{r.business.demand?.line_count==null?'未收集需求摘要':r.business.demand.line_count+' 条确认需求'}<small>{r.business.demand?.requested?.map((t:Dict)=>t.quantity+' '+t.uom).join(' / ')||'数量与单位待核对'}</small></>},{id:'candidate',title:'候选与待复核',value:r=><>{r.business.candidates?.count??'未知'} 个候选<small>{r.business.candidates?.needs_review?'存在待复核事项':'未取得待复核标记，不表示已批准'}</small></>},{id:'version',title:'数据版本 / 入口',value:r=><><Link to={'/data/'+r.data_version_id}>{r.data_version_id}</Link><small><Link to={'/plans/'+r.id+'?target=baseline&assistant=1'}>研究任务</Link> · <Link to={'/plans/'+r.id+'?target=baseline&analysis=review'}>方案评审</Link></small></>}]} empty="还没有生产计划。先核对资料，再主动生成计划。"/></div><div className="fact-pagination">{offset>0&&<Button onClick={()=>setOffset(Math.max(0,offset-20))}>上一页计划</Button>}<span>第 {Math.floor(offset/20)+1} 页 · 本页 {entries.length} 份</span>{page?.next_offset!=null&&<Button onClick={()=>setOffset(page.next_offset)}>下一页计划</Button>}</div></>:!error&&<Loading/>}</section>;
}
