import {expect,it} from 'vitest';
import {scenarioPreview,scenarioTitle} from './scenarioPreview';
it('source terms are supply assumptions and do not become undefined MO windows',()=>{
 const rows=scenarioPreview({intent:'供应来源条件模拟',simulation_only:true,source_id:'SRC',before:{capacity_quantity:'100',minimum_order_quantity:'20'},after:{capacity_quantity:'40',minimum_order_quantity:'80'}});
 expect(rows).toContainEqual(['供应来源','SRC']);expect(rows).toContainEqual(['全期容量假设','100 → 40']);expect(rows.join(' ')).not.toContain('undefined');
});
it('MO simulation keeps before and after windows distinct',()=>{
 const rows=scenarioPreview({intent:'approved工单窗口模拟',simulation_only:true,reference:'MO',before:{start_at:'A',end_at:'B'},after:{start_at:'C',end_at:'D'}});
 expect(rows).toContainEqual(['假设窗口','C → D']);
});
it('resource absence remains a scoped simulation with no execution permission',()=>{
 const rows=scenarioPreview({intent:'资源不可用模拟',resource_id:'PERSON-1',start_at:'2026-11-05T16:00:00+08:00',end_at:'2026-11-05T18:00:00+08:00',before_calendar_id:'CAL-1',after_calendar_id:'CAL-SIM-1',removed_seconds:7200,simulation_only:true});
 expect(rows).toContainEqual(['模拟资源','PERSON-1']);expect(rows).toContainEqual(['减少原可用时间','120 分钟']);expect(rows).toContainEqual(['实际执行','未授权']);expect(rows.join(' ')).not.toContain('undefined');
});

it('route leadtime is a separate transport simulation, never a rush order or execution',()=>{const rows=scenarioPreview({action:'simulate_distribution_leadtime',conditions:{lane_id:'LANE-A'},before_lead_time_seconds:86400,after_lead_time_seconds:172800,reason:'研究假设'});expect(rows).toContainEqual(['精确调拨路线','LANE-A']);expect(rows).toContainEqual(['运输提前期假设','24 → 48 小时（连续日历）']);expect(rows).toContainEqual(['业务许可 / 实际执行','尚未确认 / 未授权']);expect(rows.join(' ')).not.toContain('急单');});

it('compares an unresolved Foundation Gross proposal without inventing Net or urgent-order fields',()=>{
 const change={action:'set_foundation_forecast_gross',bucket:{item_id:'FG-TB-M',location_id:'FACTORY',customer_id:'CUST-01',uom:'件',bucket_start:'2026-11-01T00:00:00+08:00',bucket_end:'2026-12-01T00:00:00+08:00'},before:{gross_quantity:'100',net_quantity:null},after:{gross_quantity:'125',net_quantity:null},reason:'审阅毛预测',source_run_id:'API-f19e1f97097a427c',worker_eligible:false};
 const rows=scenarioPreview(change),text=rows.flat().join(' ');
 expect(rows).toContainEqual(['来源 Gross','100 件']);expect(rows).toContainEqual(['候选 Gross','125 件']);
 expect(rows).toContainEqual(['净预测 / 已抵扣','未知 / 未计算']);
 expect(rows).toContainEqual(['原Firm需求及企业事实','保持不变']);
 expect(text).toContain('抵扣政策待确认');expect(text).not.toContain('急单');expect(text).not.toContain('undefined');
 expect(scenarioTitle(change)).toBe('毛预测调整');expect(scenarioTitle({intent:'修改交期'})).toBe('修改交期');
});
