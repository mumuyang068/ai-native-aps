import {describe,it,expect} from 'vitest';
import fixture from './orderSupplyChain.fixture.json';
import {buildOrderSupplyChain,type SupplyArgs} from './supplyChainModel';

function args(delayed=false,orderId='D16-ORDER-2-L1'):SupplyArgs{return structuredClone({...fixture[delayed?'delayed':'baseline'],orderId}) as unknown as SupplyArgs;}
describe('native order supply references and timeline boundaries',()=>{
 it('does not label a declared customer FG endpoint as stage output just because it is stored under wip',()=>{
  const a=args();a.input.rules.manufacturing_profile='finished-garment-confirmed-v1';
  a.view.context!.wip={stage_endpoint:{kind:'CUSTOMER_FG',scope:'BOUNDED_SYNTHETIC_FINISHED_CUSTOMER_DEMAND',label:'客户FG需求'}};
  const m=buildOrderSupplyChain(a);expect(m.stageEndpoint).toBe(false);expect(m.status).toBe('UNSUPPORTED');expect(m.links).toEqual([]);
 });
 it('uses the real D16 purchase, CUT, shared WIP and SEW references',()=>{
  const m=buildOrderSupplyChain(args());expect(m.status).toBe('PRESENT_WITH_LIMITS');
  expect(m.nodes.filter(n=>n.kind==='PURCHASE')).toHaveLength(1);
  expect(m.nodes.filter(n=>n.kind==='OPERATION')).toHaveLength(2);
  const stock=m.nodes.find(n=>n.kind==='STOCK')!;expect(stock.inventory?.net).toBe('12');expect(stock.inventory?.frozen).toBe('8');
  expect(m.links.length).toBeGreaterThanOrEqual(4);
  expect(m.links.every(l=>l.source.source.sha256&&l.source.source.solve_run_id===m.runId)).toBe(true);
  expect(m.delivery?.planned_quantity).toBe('10');
 });
 it('does not allocate the same-plan confirmed PO to the first demand without native pegging',()=>{
  const m=buildOrderSupplyChain(args(false,'D16-ORDER-1-L1'));
  expect(m.nodes.some(n=>n.kind==='PURCHASE')).toBe(false);expect(m.purchaseFacts).toHaveLength(1);
  expect(m.links.every(l=>!l.from.includes('CONFIRMED-40'))).toBe(true);
 });
 it('uses current scenario PO windows, never the baseline physical/QC dates',()=>{
  const m=buildOrderSupplyChain(args(true));expect(m.purchaseFacts[0].physicalAt).toBe('2026-11-05T09:00:00+08:00');
  expect(m.purchaseFacts[0].availableAt).toBe('2026-11-06T09:00:00+08:00');
  expect(m.nodes.some(n=>n.kind==='PURCHASE')).toBe(false);
  expect(m.delivery?.planned_quantity).toBe('2');expect(m.delivery?.unfulfilled_quantity).toBe('8');expect(m.unfulfilledAt).toBeNull();
 });
 it('never reconstructs a missing path from the BOM, item names or matching dates',()=>{
  const a=args();for(const o of a.view.order_evidence!.orders)for(const group of ['manufacturing','operations','purchases','deliveries'] as const)
   for(const e of (o as any)[group]??[]){e.record.plan={};}
  const m=buildOrderSupplyChain(a);expect(m.status).toBe('INSUFFICIENT_EVIDENCE');expect(m.links).toEqual([]);
  expect(m.nodes.filter(n=>n.kind!=='DEMAND')).toEqual([]);
 });
 it('does not connect tasks just because they share this demand',()=>{
  const a=args();for(const o of a.view.order_evidence!.orders)for(const group of ['manufacturing','operations','purchases','deliveries'] as const)
   for(const e of (o as any)[group]??[])e.record.plan.upstream_opplans=[];
  expect(buildOrderSupplyChain(a).links).toEqual([]);
 });
 it('needs the actual native buffer observation, not a reconstructed buffer name',()=>{
  const a=args();a.view.order_evidence!.buffer_records=[];
  const m=buildOrderSupplyChain(a);expect(m.nodes.some(n=>n.kind==='STOCK')).toBe(false);
  expect(m.notices.some(n=>n.includes('上游引用'))).toBe(true);
 });
 it.each(['plan','version','run','hash'])('refuses a mixed %s identity',kind=>{
  const a=args();if(kind==='plan')a.planId='OTHER';if(kind==='version')a.versionId='OTHER';
  if(kind==='run')a.view.order_evidence!.solve_run_id='OTHER';if(kind==='hash')a.view.order_evidence!.source_result_hash='OTHER';
  const m=buildOrderSupplyChain(a);expect(m.status).toBe('NOT_CHECKED');expect(m.nodes).toEqual([]);expect(m.links).toEqual([]);
 });
 it('does not accept cross-run source rows or a missing SHA',()=>{
  const a=args();const o=a.view.order_evidence!.orders.find(o=>o.demand_id===a.orderId)!;
  for(const e of o.operations)e.source.solve_run_id='OTHER';for(const e of o.manufacturing)e.source.sha256=null;
  const m=buildOrderSupplyChain(a);expect(m.nodes.some(n=>n.kind==='OPERATION')).toBe(false);
 });
 it('keeps unknown dates and unfulfilled quantities unknown',()=>{
  const a=args(true);const d=a.view.order_evidence!.orders.find(o=>o.demand_id===a.orderId)!.delivery;
  d.planned_delivery_at=null;d.planned_quantity='0';d.unfulfilled_quantity='10';d.status='UNPLANNED';
  Object.assign(a.view.result!.deliveries.find(r=>r.demand_id===a.orderId)!,d);
  const m=buildOrderSupplyChain(a);expect(m.nodes.find(n=>n.kind==='DEMAND')?.endAt).toBeNull();expect(m.unfulfilledAt).toBeNull();
 });
 it('refuses order-result quantities that disagree with the current PlanResult',()=>{
  const a=args();a.view.order_evidence!.orders.find(o=>o.demand_id===a.orderId)!.delivery.planned_quantity='999';
  expect(buildOrderSupplyChain(a).status).toBe('NOT_CHECKED');
 });
 it('marks an unvalidated profile as unsupported without hiding existing records',()=>{
  const a=args();a.input.rules.manufacturing_profile='unvalidated-profile';const m=buildOrderSupplyChain(a);
  expect(m.status).toBe('UNSUPPORTED');expect(m.links).toEqual([]);
 });
 it('does not turn missing inventory quantities into zero',()=>{
  const a=args();delete a.input.inventory_positions.find((i:any)=>i.item_id==='D16-CUT-WIP').quality_blocked;
  const m=buildOrderSupplyChain(a);expect(m.nodes.find(n=>n.kind==='STOCK')?.inventory?.net).toBeNull();
 });
});
