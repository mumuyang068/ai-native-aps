import {describe,it,expect} from 'vitest';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {CockpitDemandMap} from './ProductDemandMap';
import {fulfillmentGroupId} from './demandFulfillmentModel';
import type {Dict} from '../api/contracts';
function input():Dict{
 const line={demand_id:'L1',order_ref:'订单一',item_id:'上衣',location_id:'一号工厂',endpoint:'缝后',uom:'件',demand_kind:'FIRM_ORDER',due_at:'2026-11-06T18:00:00+08:00',requested_quantity:'10',planned_quantity:'2',unfulfilled_quantity:'8',delivery_validation_status:'PASS',delivery_evidence_status:'PRESENT'};
 return {timezone:'Asia/Shanghai',identity:{plan_id:'P',data_version_id:'D',run_id:'R',target:'baseline'},range:{start_at:'2026-11-01T00:00:00+08:00',end_at:'2026-12-01T00:00:00+08:00'},demand:{status:'PRESENT',validation:{status:'PASS'},lines:[line],delivery_performance:{status:'PRESENT',validation:{status:'PASS'},groups:[{...line,lines:[{demand_id:'L1',classification:'PARTIALLY_PLANNED'}]}]}}};
}
function markup(data=input()){return renderToStaticMarkup(createElement(CockpitDemandMap,{data,selectedGroup:fulfillmentGroupId(data.demand.lines[0]),onKind:()=>{}}))}
describe('home demand calendar disclosure',()=>{
 it('offers the native chart and an accessible demand list, with details initially closed',()=>{const html=markup();expect(html).toContain('到期需求多月热力图');expect(html).toContain('需求清单');expect(html).toContain('筛选产品与地点');expect(html).not.toContain('逐条需求数量覆盖');expect(html).not.toContain('coverage-matrix-scroll')});
 it('does not permanently print product rows or a second set of date inputs on Home',()=>{const html=markup();expect(html).not.toContain('<table');expect(html).not.toContain('datetime-local');expect(html).toContain('2026年')});
 it('keeps undated demand reachable without assigning it a calendar date',()=>{const data=input();data.demand.lines[0].due_at=null;expect(markup(data)).toContain('交期待核实 1条')});
 it('falls back to actual unclassified demand when a previous firm selection is unavailable',()=>{const data=input();delete data.demand.lines[0].demand_kind;const html=renderToStaticMarkup(createElement(CockpitDemandMap,{data,selectedGroup:'',selectedKind:'FIRM_ORDER',onKind:()=>{}}));expect(html).toContain('1条需求');expect(html).toContain('未分类');expect(html).not.toContain('0条需求')});
 it('does not draw missing or failed source evidence as a zero-demand heatmap',()=>{const data=input();data.demand.status='NOT_COLLECTED';expect(markup(data)).toContain('来源证据不足');expect(markup(data)).not.toContain('到期需求多月热力图');data.demand.status='PRESENT';data.demand.validation.status='FAIL';expect(markup(data)).toContain('存在业务违规')});
});
