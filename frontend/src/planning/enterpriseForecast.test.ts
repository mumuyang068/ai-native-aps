import {expect,it} from 'vitest';
import {scenarioPreview} from '../scenarios/scenarioPreview';
import {supports} from './capabilityModel';

it('describes a forecast assumption without falling back to a new urgent customer order',()=>{
 const rows=scenarioPreview({action:'set_forecast_gross_override',bucket:{forecast_id:'FC'},before:{gross_quantity:'100',net_quantity:'88'},after:{gross_quantity:'16',net_quantity:'4'},reason:'测试假设'});
 const text=rows.flat().join(' ');
 expect(text).toContain('100 → 16');expect(text).toContain('88 → 4');
 expect(text).toContain('FC');expect(text).not.toContain('急单');
});
it('certifies the finite combined views and forecast action without inheritance of order allocation or other writes',()=>{
 for(const key of ['resources','inventory','demand_planning','inventory_policy'])expect(supports('enterprise-forecast-fg-v1','analyses',key)).toBe(true);
 expect(supports('enterprise-forecast-fg-v1','actions','forecast_override')).toBe(true);
 for(const key of ['approved_mo_move','resource_unavailability','priority_source_terms'])expect(supports('enterprise-forecast-fg-v1','actions',key)).toBe(false);
 expect(supports('enterprise-forecast-fg-v1','analyses','order_pegging')).toBe(false);
});
