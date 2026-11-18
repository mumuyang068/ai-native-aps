import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {describe,it,expect} from 'vitest';
import {SupplyOpeningLedger} from './SupplyWorkbench';
import type {SupplyWorkRow} from './supplyWorkModel';

const row:SupplyWorkRow={id:'WIP@FACTORY:DAY',itemId:'WIP',locationId:'FACTORY',name:'独立裁片',uom:'套',startAt:'2026-11-03T00:00:00+08:00',endAt:'2026-11-04T00:00:00+08:00',opening:12,inflow:0,outflow:0,ending:12,physicalOpening:20,frozenOpening:8,openingInspectionPending:null,otherNonnettable:null,qcWaiting:0,demandShort:null,tags:['frozen'],status:'PRESENT',validation:'PASS',series:{opening:'12'},raw:{}};
describe('物料开账的实际产品表达',()=>{
 it('实物、冻结与净可用分别列示，不称作指定订单的库存分配',()=>{const html=renderToStaticMarkup(createElement(SupplyOpeningLedger,{row}));for(const text of ['业务开账物理量','20 套','开账质量冻结','8 套','开账净可用','12 套','不是指定订单分配量','不代表每个期间重新入库'])expect(html).toContain(text)});
 it('已知0与缺失证据分别表达，不将未采集量补成0',()=>{const html=renderToStaticMarkup(createElement(SupplyOpeningLedger,{row:{...row,physicalOpening:null,frozenOpening:null,qcWaiting:null,status:'NOT_COLLECTED'}}));expect(html.match(/未知/g)).toHaveLength(6);expect(html).toContain('未采集');expect(html).not.toContain('0 套');expect(html).not.toContain('12 套')});
 it('开账待检与采购QC分别显示，物理4待检4净0不误写为无待检',()=>{const html=renderToStaticMarkup(createElement(SupplyOpeningLedger,{row:{...row,uom:'件',physicalOpening:4,frozenOpening:0,openingInspectionPending:4,otherNonnettable:0,qcWaiting:0,opening:0,ending:0,series:{opening:'0'}}}));expect(html).toContain('开账待检');expect(html).toContain('开账其他不可用');expect(html).toContain('本期间末采购待QC');expect(html.match(/4 件/g)).toHaveLength(2);expect(html).toContain('不代表未来释放');expect(html).not.toContain('本期间QC待可用');expect(html).not.toContain('2026-11-')});
 it('已查空的事件不会冒充未采集，也不抹去真实开账净可用',()=>{const html=renderToStaticMarkup(createElement(SupplyOpeningLedger,{row:{...row,status:'EMPTY_CHECKED'}}));expect(html).toContain('已查询并确认没有对应事件');expect(html).toContain('12 套');expect(html).not.toContain('必要证据未采集')});
});
