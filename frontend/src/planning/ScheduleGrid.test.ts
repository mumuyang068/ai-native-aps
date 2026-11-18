import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {it,expect} from 'vitest';
import {ScheduleGrid} from './ScheduleGrid';
it('keys the chart by the existing view run, plan/version and target scope',()=>{
 const html=renderToStaticMarkup(createElement(ScheduleGrid,{model:{input:{},resources:[],calendars:[],tasks:[],view:{run:{solve_run_id:'API-current'},result:{deliveries:[]},context:{timezone:'Asia/Shanghai',enterprise_source:{plan_id:'PLAN-current',data_version_id:'DATA-current'}}}} as any,tasks:[],selected:null,onSelect:()=>{},query:'',setQuery:()=>{},startDate:'2026-11-01',setStartDate:()=>{},days:1,setDays:()=>{},fit:()=>{}}));
 expect(html).toContain('API-current');expect(html).toContain('PLAN-current');expect(html).toContain('DATA-current');expect(html).not.toContain('no-result');
});
