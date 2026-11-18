import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {describe,it,expect} from 'vitest';
import {ProcurementSummary} from './ProcurementSummary';

const summary={incremental_purchase_spend:'640',confirmed_purchase_commitment_model_value:'480',currency:'CNY',
 eligible_for_cost_comparison:false,capacity:[{source_id:'SRC-A',priority:1,used:'80',quantity:'80',uom:'m',minimum_order_quantity:'80',order_multiple:'20',qc_wait_seconds:86400}],
 purchase_orders:[{reference:'PO-EXISTING',source_id:'SRC-B',supplier_id:'SUP-B',status:'confirmed',quantity:'40',uom:'m',unit_price:'12',currency:'CNY',physical_receipt_at:'2026-11-03T09:00:00+08:00',available_at:'2026-11-04T09:00:00+08:00'}]};
describe('procurement analysis scope',()=>{
 it('separates commitments, new spend, QC availability and non-executable cost figures',()=>{
  const html=renderToStaticMarkup(createElement(ProcurementSummary,{summary,validation:{status:'PASS'},timezone:'Asia/Shanghai'}));
  for(const text of ['新增建议采购模型支出','640 CNY','已有采购承诺模型估值','480 CNY','已有确认采购','2026-11-03','2026-11-04','不能据此推荐执行'])expect(html).toContain(text);
 });
 it('does not label missing evidence as checked',()=>{
  const html=renderToStaticMarkup(createElement(ProcurementSummary,{summary,validation:{status:'NOT_CHECKED'},timezone:'Asia/Shanghai'}));
  expect(html).toContain('尚未完成核验');expect(html).not.toContain('已核验列明');
 });
});
