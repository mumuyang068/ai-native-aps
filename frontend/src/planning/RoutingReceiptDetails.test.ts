import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {expect,it} from 'vitest';
import {RoutingReceiptDetails} from './RoutingReceiptDetails';
import input from '../../../fixtures/p416-routing-capacity.json';
it('shows released, waiting and frozen receipts as business facts, not opaque audit JSON',()=>{const html=renderToStaticMarkup(createElement(RoutingReceiptDetails,{receipts:input.purchase_receipts,purchases:input.existing_orders,timezone:'Asia/Shanghai'}));for(const text of ['已放行','待质检','质量冻结','预计质检后可用','未放行 · 不可用','P416-RCV-1','P416-PO-1','P416-FAB','2026-11-03 07:00'])expect(html).toContain(text);expect(html).not.toContain('release_at');expect(html).not.toContain('PENDING_QC')});
it('does not invent receipt ownership or a release time when evidence is absent',()=>{const html=renderToStaticMarkup(createElement(RoutingReceiptDetails,{receipts:[{po_id:'foreign',receipt_reference:'r',quantity:'4',uom:'m',quality_status:'UNKNOWN'}],purchases:[],timezone:'Asia/Shanghai'}));expect(html).toContain('关联物料未核验');expect(html).toContain('质量未核验');expect(html).not.toContain('已放行可用')});
