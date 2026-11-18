import {describe,expect,it} from 'vitest';
import {comparisonDemandLabel} from './deliveryComparison';
import {demandTabLabel} from '../planning/presentationModel';
describe('comparison Firm and Net identity',()=>{
 it('uses exact current context identity rather than a demand ID naming pattern',()=>{
  const context={net_forecast_deliveries:[{demand_id:'renamed-forecast'}],order_summary:{orders:[{id:'NET-looking-firm'}]}};
  expect(comparisonDemandLabel('renamed-forecast',[context],'UNCHANGED')).toBe('净预测计划需求（非客户订单）');
  expect(comparisonDemandLabel('NET-looking-firm',[context],'UNCHANGED')).toBe('客户订单需求');
  expect(comparisonDemandLabel('NET_FORECAST:unproven',[context],'UNCHANGED')).toBe('需求来源类型未核实');
 });
 it('does not treat missing demand kinds as a customer promise',()=>{
  expect(comparisonDemandLabel('X',[],'ADDED')).toBe('新增需求行（来源类型未核实）');
  expect(comparisonDemandLabel('X',[],'REMOVED')).toBe('移除需求行（来源类型未核实）');
 });
 it('labels a shared FG list as Firm and Net instead of counting Net as a customer order',()=>{
  expect(demandTabLabel({wip:{stage_endpoint:{kind:'CUSTOMER_FG'}},net_forecast_deliveries:[{demand_id:'F'}]})).toBe('FG需求（Firm / Net） ');
 });
});
