import {it,expect} from 'vitest';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import fixture from './orderSupplyChain.fixture.json';
import {OrderSupplyChain} from './OrderSupplyChain';

function render(delayed=false,change?:(props:any)=>void){
 const data=structuredClone(fixture[delayed?'delayed':'baseline']);
 const props={...data,orderId:'D16-ORDER-2-L1',target:delayed?'DRAFT-90cf626fd3d8f6ef':'baseline',orders:[{id:'D16-ORDER-2-L1',label:'阶段需求2'}],scenarios:[{id:'baseline',label:'原始基线'}],onOrder:()=>{},onTarget:()=>{},onLocate:()=>{}};
 change?.(props);return renderToStaticMarkup(createElement(OrderSupplyChain,props as any));
}
it('keeps stock and native association evidence while hiding links by default',()=>{
 const html=render();expect(html).toContain('12 套');expect(html).not.toContain('data-chain-link=');
 expect(html).toContain('显示关联');expect(html).toContain('查看上游关联依据');
 expect(html).toContain('不含后续成衣质检与包装');expect(html).toContain('不等于本需求的分配量');
});
it('keeps eight unmet units undated and shows the delayed QC availability',()=>{
 const html=render(true);expect(html).toContain('尚未满足的 8 件 没有已核验的计划完成日期');
 expect(html).toContain('2026-11-06 09:00');expect(html).toContain('已排定部分预计');expect(html).not.toContain('晚交48小时');
});
it('does not render a chain for an unsupported profile',()=>{
 const html=render(false,p=>{p.input.rules.manufacturing_profile='unsupported'});
 expect(html).toContain('本视图暂不支持该模型');expect(html).not.toContain('data-chain-link=');
});
it('keeps the declared customer FG endpoint while refusing unverified order pegging',()=>{
 const html=render(false,p=>{p.input.rules.manufacturing_profile='finished-garment-confirmed-v1';p.view.context.wip={stage_endpoint:{kind:'CUSTOMER_FG',label:'客户FG需求：完成QC与PACK后供客户'}}});
 expect(html).toContain('客户FG需求：完成QC与PACK后供客户');expect(html).not.toContain('不含后续成衣质检与包装');
 expect(html).toContain('本视图暂不支持该模型');expect(html).not.toContain('data-chain-link=');
});
it('names the QC interval and both receipt milestones without changing the native path',()=>{
 const html=render();expect(html).toContain('QC等待 24小时');
 expect(html).toContain('物理到货 11-03 09:00');expect(html).toContain('QC后可用 11-04 09:00');
 expect(html).toContain('时间图图例');expect(html).toContain('完整时间链');expect(html).toContain('生产时段');
 expect(html).not.toContain('data-chain-link=');
});
