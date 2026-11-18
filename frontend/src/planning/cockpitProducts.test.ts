import {describe,it,expect} from 'vitest';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {cockpitProducts} from './cockpitModel';
import {CockpitDemandSnapshot} from './CockpitPanels';
import {CockpitDeliveryPerformance} from './CockpitWork';
import {fulfillmentGroupId} from './demandFulfillmentModel';
import type {Dict} from '../api/contracts';
const pass={status:'PASS',issues:[],missing_evidence:[]};
function line(id:string,item:string,kind:string|null,short='4',uom='件',location='FACTORY',endpoint=item):Dict{return {demand_id:id,item_id:item,location_id:location,endpoint,uom,demand_kind:kind,requested_quantity:'12',planned_quantity:String(12-Number(short)),unfulfilled_quantity:short,due_at:'2026-11-06T18:00:00+08:00',delivery_validation_status:'PASS',delivery_evidence_status:'PRESENT'}}
function report(lines:Dict[]):Dict{return {timezone:'Asia/Shanghai',demand:{status:'PRESENT',validation:pass,lines},identity:{profile:'enterprise-accessory-supply-v1'}}}
describe('product-led cockpit demand overview',()=>{
 it('combines Firm and Net beneath the same product without adding their quantities',()=>{const r=report([line('F','A','FIRM_ORDER'),line('N','A','NET_FORECAST','12')]);const p=cockpitProducts(r);expect(p).toHaveLength(1);expect(p[0].count).toBe(2);expect(p[0].firm?.short).toBe(4);expect(p[0].forecast?.short).toBe(12);expect(p[0]).not.toHaveProperty('totalQuantity')});
 it('separates products, sites, units and stage endpoints, prioritizing confirmed shortages',()=>{const r=report([line('NET','B','NET_FORECAST','12'),line('OK','C','FIRM_ORDER','0'),line('GAP','A','FIRM_ORDER'),line('SITE','A','FIRM_ORDER','0','件','SHOP'),line('UNIT','A','FIRM_ORDER','0','箱'),line('END','A','FIRM_ORDER','0','件','FACTORY','另一个阶段')]);const p=cockpitProducts(r);expect(p).toHaveLength(6);expect(p[0].item).toBe('A');expect(new Set(p.map(k=>k.id)).size).toBe(6)});
 it('does not guess missing demand type or missing quantity, and only uses real due dates',()=>{const a=line('U','A',null);a.unfulfilled_quantity=null;a.due_at=null;const p=cockpitProducts(report([a]))[0];expect(p.firm).toBeUndefined();expect(p.needsCheck).toBe(true);expect(p.unclassified[0].short).toBeNull();expect(p.attentionDue).toBeNull()});
 it('keeps fail and invalid source presentation empty',()=>{const r=report([line('F','A','FIRM_ORDER')]);r.known_business_fail=true;expect(cockpitProducts(r)).toEqual([])});
 it('keeps product selection available in the filter while Home starts with the aggregate calendar',()=>{const r=report([line('F','A','FIRM_ORDER'),line('N','A','NET_FORECAST','12'),line('B','B','FIRM_ORDER','0')]);const html=renderToStaticMarkup(createElement(CockpitDemandSnapshot,{data:r,selectedGroup:fulfillmentGroupId(r.demand.lines[0]),onKind:()=>{}}));expect(html).toContain('到期需求多月热力图');expect(html).toContain('筛选产品与地点');expect(html).toContain('需求清单');expect(html).not.toContain('本计划的产品与阶段品');expect(cockpitProducts(r)).toHaveLength(2)});
 it('does not silently show another product performance when the selected one has no Firm evidence',()=>{const r=report([line('F','A','FIRM_ORDER'),line('U','B',null)]);r.demand.delivery_performance={status:'PRESENT',groups:[{item_id:'A',location_id:'FACTORY',endpoint:'A',uom:'件',firm_line_count:1,on_time_percent:'100',counts:{FULL_ON_TIME:1},lines:[]} ]};const html=renderToStaticMarkup(createElement(CockpitDeliveryPerformance,{data:r,scope:{planId:'PLAN-1',versionId:'DATA-1',runId:'API-1',target:'baseline',profile:'enterprise-accessory-supply-v1'},selectedGroup:fulfillmentGroupId(r.demand.lines[1]),search:new URLSearchParams(),change:()=>{},open:()=>{}}));expect(html).toContain('1行未明确需求类型');expect(html).not.toContain('100%');expect(html).not.toContain('A · FACTORY')});
});
