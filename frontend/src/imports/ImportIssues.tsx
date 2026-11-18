import type {Issue,Location} from '../api/contracts';
import {Badge,Button} from '../components/Controls';
import {presentIssue} from './issuePresentation';
export function ImportIssues({issues,onLocate}:{issues:Issue[];onLocate:(location:Location)=>void}){
 return <div className="issue-list">{!issues.length?<p className="page-note">当前没有字段或业务关系错误。请由相关负责人核对事实后确认。</p>:issues.map((issue,i)=>{const p=presentIssue(issue);return <section className="issue-item" key={issue.code+i}><div className="issue-heading"><Badge kind={p.blocked?'risk':''}>{p.label}</Badge><h3>{p.title}</h3></div>{p.message&&<p>{p.message}</p>}<div className="issue-resolution"><strong>处理方式</strong><p>{p.resolution}</p></div><div className="issue-locations">{issue.source_locations.map((loc,j)=><div className="issue-source" key={j}><Button onClick={()=>onLocate(loc)}>定位原表</Button><span>{loc.sheet}{loc.cell?' · '+loc.cell:''}</span></div>)}</div><details className="issue-original"><summary>查看校验依据</summary><p>{issue.message}</p><p>{issue.resolution}</p><small>校验项 {issue.code}</small></details></section>})}</div>;
}
