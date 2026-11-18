import {it,expect} from 'vitest';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {MemoryRouter} from 'react-router';
import {PlanningChart} from '../components/PlanningChart';
import {PlanCockpit} from './PlanCockpit';
import {BusinessKindTabs} from './BusinessFacts';
import type {PlanView,PlanState} from '../api/contracts';
import {ProfessionalPolicy} from './ProfessionalPolicy';
it('independent research entry survives successful baseline without a native intelligence scope',()=>{
 const p={scope:{planId:'PLAN-a',versionId:'DATA-a',baselineRunId:'',runId:'',target:'baseline'},mode:'forecast-netting' as const,onTarget:()=>{},onRefresh:()=>{},onCompare:()=>{},onReview:()=>{},onInventory:()=>{},onDemand:()=>{}};
 for(const runState of ['NOT_RUN','SUCCEEDED']){
  const html=renderToStaticMarkup(createElement(MemoryRouter,{},createElement(ProfessionalPolicy,{...p,runState})));
  expect(html).toContain('独立研究');expect(html).toContain('联合规划');
  expect(html).not.toContain('professional-policy-editor');
 }
});
it('Home still opens the business reader without a successful native run',()=>{
 const scope={planId:'PLAN-0123456789abcdef',versionId:'DATA-a',runId:'',target:'baseline',profile:'foundation-planning-projection-v1'};
 const html=renderToStaticMarkup(createElement(MemoryRouter,{},createElement(PlanCockpit,{scope,view:{run:{state:'NOT_RUN'}} as PlanView,state:{baseline:{},drafts:[],timezone:'Asia/Shanghai'} as unknown as PlanState,onRefresh:()=>{}})));
 expect(html).toContain('查看计划结果');expect(html).not.toContain('驾驶舱信息范围');expect(html).not.toContain('企业业务概况');expect(html).not.toContain('这个方案尚未计算');
});
it('micro charts have no meaningless reset toolbar but keep an accessible equivalent',()=>{
 const html=renderToStaticMarkup(createElement(PlanningChart,{controls:false,compact:true,identityKey:'fact',label:'事实图',option:{series:[]}}));
 expect(html).not.toContain('复位图表视窗');expect(html).toContain('事实图');
});
it('a single Net kind remains selectable when a product filter leaves Firm empty',()=>{
 const html=renderToStaticMarkup(createElement(BusinessKindTabs,{compact:true,kinds:['NET_FORECAST'],value:'FIRM_ORDER',onChange:()=>{}}));
 expect(html).toContain('净预测');expect(html).toContain('需求类别');
});
it('Home omits a single already selected kind while retaining real category choices',()=>{
 const props={compact:true,value:'FIRM_ORDER',onChange:()=>{}};
 expect(renderToStaticMarkup(createElement(BusinessKindTabs,{...props,kinds:['FIRM_ORDER']}))).toBe('');
 const html=renderToStaticMarkup(createElement(BusinessKindTabs,{...props,kinds:['FIRM_ORDER','NET_FORECAST']}));
 expect(html).toContain('确认需求');expect(html).toContain('净预测');
});
