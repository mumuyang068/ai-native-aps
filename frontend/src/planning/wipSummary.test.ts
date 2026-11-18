import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {describe,it,expect} from 'vitest';
import {WipSummary} from './WipSummary';

const summary={inventory:[{item_id:'WIP',kind:'SEMI',physical_opening:'20',quality_blocked:'8',other_nonnettable:'0',net_available_opening:'12',future_production:'8',future_consumption:'20',ending_net_available:'0',uom:'set'}],material_events:[{item_id:'WIP',reference:'CUT-NEW',at:'2026-11-05T14:00:00+08:00',quantity:'8',net_available_balance:'12'}]};
describe('independent WIP business view',()=>{
 it('keeps physical, held, usable and future events separate',()=>{
  const html=renderToStaticMarkup(createElement(WipSummary,{summary,validation:{status:'PASS'},timezone:'Asia/Shanghai'}));
  for(const s of ['物理期初','质量冻结','净可用期初','20','8','12','未来制造入库','未来消耗','CUT-NEW','已核对本范围'])expect(html).toContain(s);
  expect(html).toContain('历史制造不再次计入');
 });
 it('does not certify missing or failed material evidence',()=>{
  const html=renderToStaticMarkup(createElement(WipSummary,{summary,validation:{status:'NOT_CHECKED'},timezone:'Asia/Shanghai'}));
  expect(html).toContain('尚未完成核验');expect(html).not.toContain('已核对本范围');
 });
 it('labels a synthetic sewn endpoint without claiming garment delivery',()=>{
  const stage={...summary,stage_endpoint:{item_id:'TEST-SEWN',label:'合成缝后工序终点；阶段需求满足不代表成衣客户交付'},inventory:[{...summary.inventory[0],item_id:'TEST-SEWN',kind:'FINISHED'}]};
  const html=renderToStaticMarkup(createElement(WipSummary,{summary:stage,validation:{status:'PASS'},timezone:'Asia/Shanghai'}));
  expect(html).toContain('阶段需求满足不代表成衣客户交付');expect(html).not.toContain('成品 ·');
 });
 it('separates purchase receipts from manufacture and marks uncertified purchases',()=>{
  const joint={...summary,inventory:[{...summary.inventory[0],item_id:'RAW',kind:'RAW',future_purchase_receipts:'40',future_production:'0'}],unvalidated_purchases:[{reference:'PO-NEW'}]};
  const html=renderToStaticMarkup(createElement(WipSummary,{summary:joint,validation:{status:'UNSUPPORTED'},timezone:'Asia/Shanghai'}));
  expect(html).toContain('采购供应入流');expect(html).toContain('未来制造入库');
  expect(html).toContain('新增采购尚未认证');expect(html).toContain('PO-NEW');expect(html).not.toContain('已核对本范围');
 });

});
