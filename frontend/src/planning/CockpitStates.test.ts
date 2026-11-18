import {describe,it,expect} from 'vitest';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {MemoryRouter} from 'react-router';
import {PlanCockpit} from './PlanCockpit';
import type {PlanState,PlanView} from '../api/contracts';
import {analysisContextSearch} from './analysisModel';
import {reportReturnSearch} from './reportModel';
import {cockpitDrill,cockpitReturn} from './cockpitNavigation';
import {runLabel} from './planModel';
const scope={planId:'PLAN-0123456789abcdef',versionId:'DATA-example',runId:'',target:'DRAFT-0123456789abcdef',profile:'enterprise-accessory-supply-v1'};
function render(view:PlanView,query=''){
 return renderToStaticMarkup(createElement(MemoryRouter,{initialEntries:['/?cockpit_view=plan&'+query]},createElement(PlanCockpit,{scope,view,state:{timezone:'Asia/Shanghai',drafts:[],baseline:{}} as unknown as PlanState,onRefresh:()=>{}})));
}
describe('Cockpit unavailable run presentation',()=>{
 it('keeps unreadable recorded evidence distinct from a never-run plan',()=>{
  expect(runLabel('UNAVAILABLE')).toBe('结果暂不可核验');
  const html=render({run:{state:'UNAVAILABLE'},error:'RUN_SOURCE_MISMATCH: protected evidence unavailable'});
  expect(html).toContain('当前结果暂不可核验');
  expect(html).not.toContain('这个方案尚未计算');
  expect(html).toContain('RUN_SOURCE_MISMATCH');
 });
 it('shows NOT_RUN work state without baseline business quantities',()=>{
  const html=render({run:{state:'NOT_RUN'},business_summary:'原基线计划满足999件'});
  expect(html).toContain('这个方案尚未计算');expect(html).not.toContain('当前计划驾驶舱');expect(html).not.toContain('cockpit-work-list');
  expect(html).toContain('不会沿用另一方案');expect(html).toContain('查看状态原因与来源');
 });
 it('retains FAILED as a failed run and exposes original route, not inferred infeasibility',()=>{
  const html=render({run:{state:'FAILED'},error:'test engine failure'});
  expect(html).toContain('本次计算未完成');expect(html).toContain('未证明业务无解');expect(html).toContain('打开原计划与运行操作');expect(html).not.toContain('cockpit-stock-list');
 });
 it('rejects a wrong version before any chart can render',()=>{
  expect(render({run:{state:'NOT_RUN'}},'data_version_id=DATA-other')).toContain('企业数据版本与当前计划不一致');
 });
 it('preserves same-scope returns through existing analysis/report owners and clears them on scenario change',()=>{
  const s={...scope,runId:'API-0123456789abcdef'},p=cockpitDrill(new URLSearchParams({target:s.target}),s,'demand:1',{analysis:'reports',report:'demand'});
  expect(cockpitReturn(reportReturnSearch(p,s.target),s)).not.toBeNull();
  expect(cockpitReturn(analysisContextSearch(p,s.target),s)).not.toBeNull();
  expect(analysisContextSearch(p,'baseline').has('cockpit_return')).toBe(false);
 });
});
