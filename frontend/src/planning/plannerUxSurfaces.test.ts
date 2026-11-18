import {it,expect} from 'vitest';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {MemoryRouter} from 'react-router';
import {WorkspaceReadError} from './WorkspaceReadError';
import {catalogSummary} from './planCatalogModel';
import {PlannerHomeOverview} from './PlannerHomeOverview';
import type {PlanRecord,PlanState} from '../api/contracts';
it('candidate read failure preserves exact metadata and safe historical entry without showing baseline quantities',()=>{
 const plan={id:'PLAN-p',data_version_id:'DATA-v'} as PlanRecord,state={baseline:{run:{solve_run_id:'API-base'},result:{deliveries:[{quantity:'999'}]}},drafts:[{id:'DRAFT-c',run_id:'API-candidate'}]} as unknown as PlanState;
 const html=renderToStaticMarkup(createElement(MemoryRouter,{},createElement(WorkspaceReadError,{planId:'PLAN-p',target:'DRAFT-c',error:'RUN_EVIDENCE_UNAVAILABLE',context:{plan,state},onRetry:()=>{}})));
 for(const value of ['PLAN-p','DATA-v','DRAFT-c','API-base','API-candidate','返回本计划首页','只读'])expect(html).toContain(value);
 expect(html).not.toContain('999');expect(html).not.toContain('保存评审意见');expect(html).toContain('不启动计算');
});
it('catalog distinguishes unreadable run metadata from an unsubmitted plan',()=>{
 const base={id:'P',data_version_id:'D',created_at:'',snapshot_sha256:'',business:{run:{status:'UNAVAILABLE',solve_run_id:'API-x',saved_submission_state:'QUEUED'}}};
 expect(catalogSummary(base)).toContain('运行状态暂不可读取');expect(catalogSummary(base)).not.toContain('尚未计算');
 expect(catalogSummary({...base,business:{run:{status:'NOT_SUBMITTED'}}})).toContain('尚未提交');
 expect(catalogSummary({...base,business:{source_as_of:'2026-10-30T04:10:00+00:00'}})).toContain('04:10:00+00:00');
});
it('a candidate failure risk and run activity stay on that exact candidate',()=>{
 const html=renderToStaticMarkup(createElement(MemoryRouter,{},createElement(PlannerHomeOverview,{scope:{planId:'PLAN-p',versionId:'DATA-v'},data:{identity:{enterprise_id:'E',source_as_of:'2026-11-01T00:00:00Z'},timezone:'Asia/Shanghai',range:{start:'2026-11-01',end:'2026-12-01'},filters:{},orders:{totals:[],total:0,overdue_count:0}},attention:null,children:null,home:{target:'DRAFT-c',view:{run:{state:'FAILED',solve_run_id:'API-c'}} as any,state:{baseline:{},drafts:[]} as any}})));
 expect(html.match(/href="[^"]*target=DRAFT-c[^"]*analysis=schedule/g)?.length).toBe(2);
 expect(html).toContain('target=DRAFT-c&amp;analysis=resources');
});
