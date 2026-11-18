import type {Dict,PlanRecord} from '../api/contracts';
import {runLabel} from './planModel';
export type CatalogPlan=PlanRecord&{business:Dict};
export function catalogTitle(p:CatalogPlan){const b=p.business;return b.enterprise_name??b.enterprise_id??'企业资料待核对';}
export function catalogSummary(p:CatalogPlan){const b=p.business,r=b.run;const status=r?.state?runLabel(r.state)+'（归档另行核验）':r?.status==='NOT_SUBMITTED'?'尚未提交':r?.solve_run_id?'运行状态暂不可读取'+(r.saved_submission_state?' · 保存状态 '+runLabel(r.saved_submission_state):''):'运行记录未收集';return [b.source_name??'来源未声明',b.source_as_of?b.source_as_of.replace('T',' '):'来源截至未知',status,b.candidates?.count?b.candidates.count+' 个候选':''].filter(Boolean).join(' · ');}
export function catalogLabel(p:CatalogPlan){return catalogTitle(p)+' · '+catalogSummary(p);}
