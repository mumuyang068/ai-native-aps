import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {describe,expect,it} from 'vitest';
import {AccessoryPurchaseObservations,AccessorySupply,AccessoryTerms} from './AccessorySupply';
import {accessoryReturnSearch,accessorySearch,type AccessoryOption} from './accessorySupplyModel';
import {scenarioPreview} from '../scenarios/scenarioPreview';

const option:AccessoryOption={source_id:'SYN-LABEL-SOURCE',supplier_id:'SYN-LABEL-SUPPLIER',item_id:'LABEL',location_id:'FACTORY',uom:'个',minimum_order_quantity:'20',order_multiple:'10',lead_time_seconds:86400,qc_wait_seconds:86400,total_capacity_quantity:'40',earliest_order_at:'2026-11-01T08:00:00+08:00',latest_order_at:'2026-11-08T08:00:00+08:00',effective_from:'2026-11-01T08:00:00+08:00',effective_to:'2026-12-01T08:00:00+08:00',enabled:false,price_status:'NOT_PROVIDED',unit_price:null,currency:null};
describe('accessory terms and existing workspace continuity',()=>{
 it('keeps missing REST purchase evidence null as unknown rather than zero or a crash',()=>{
  const html=renderToStaticMarkup(createElement(AccessoryPurchaseObservations,{orders:null,timezone:'Asia/Shanghai'}));
  expect(html).toContain('尚未取得本运行的原生建议采购认证');expect(html).not.toContain('没有辅料采购建议');
 });
 it('distinguishes undefined evidence from an actually checked empty purchase collection',()=>{
  const missing=renderToStaticMarkup(createElement(AccessoryPurchaseObservations,{orders:undefined,timezone:'Asia/Shanghai'}));
  const empty=renderToStaticMarkup(createElement(AccessoryPurchaseObservations,{orders:[],timezone:'Asia/Shanghai'}));
  expect(missing).toContain('不能当作零采购');expect(empty).toContain('已读取本运行摘要，没有辅料采购建议');expect(empty).not.toContain('尚未取得');
 });
 it('shows frozen source terms and unknown price without editable time quantity or capacity',()=>{
  const html=renderToStaticMarkup(createElement(AccessoryTerms,{option,timezone:'Asia/Shanghai'}));
  for(const text of ['SYN-LABEL-SOURCE','SYN-LABEL-SUPPLIER','20 个','40 个','到货后 QC 等待','24 小时','未提供','不按天重复'])expect(html).toContain(text);
  expect(html).not.toContain('<input');expect(html).not.toContain('0 CNY');expect(html).not.toContain('可增产');
 });
 it('keeps the existing NOT_RUN scenario preview clear about simulation and original facts',()=>{
  const rows=scenarioPreview({action:'enable_accessory_supply',source_id:option.source_id,reason:'核验真实补给变化'});
  expect(rows).toContainEqual(['辅料来源',option.source_id]);expect(rows).toContainEqual(['采购与生产执行','未授权']);
  expect(rows).toContainEqual(['原确认采购与企业版本','保持不变']);expect(rows.some(([key])=>key==='急单编号')).toBe(false);
 });
 it('keeps a known whole-run business failure visible apart from source certification',()=>{
  const nothing=()=>{};
  const html=renderToStaticMarkup(createElement(AccessorySupply,{scope:{planId:'P',versionId:'V',runId:'R',baselineRunId:'BASE',target:'D'},profile:'enterprise-accessory-supply-v1',timezone:'Asia/Shanghai',businessStatus:'INVALID_SCHEDULE',onDraft:nothing,onReturn:nothing,onInventory:nothing,onDemand:nothing,onReview:nothing}));
  expect(html).toContain('本运行存在业务核验失败');expect(html).toContain('来源认证不能替代整体计划合法性');
 });
 it('does not reuse another plan or malformed return selection',()=>{
  const scope={planId:'P',versionId:'V',runId:'R',baselineRunId:'R',target:'baseline'};
  const origin=accessorySearch(new URLSearchParams('analysis=inventory&analysis_item=LABEL&analysis_event=E'),scope);
  const foreign=accessoryReturnSearch(origin,{...scope,planId:'OTHER'});
  expect(foreign.get('analysis')).toBe('schedule');expect(foreign.has('analysis_event')).toBe(false);expect(foreign.has('analysis_item')).toBe(false);
  expect(()=>accessoryReturnSearch(new URLSearchParams('accessory_return_scope=not-json'),scope)).not.toThrow();
 });
});
