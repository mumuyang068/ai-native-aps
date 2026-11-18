import {describe,expect,it} from 'vitest';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {AdoptionOperations,YearSourcePanel,LiveEventSection,readLiveEvents,adoptionSelection,bindLiveAdoptionPreview,liveAdoptionBody,liveMutationBody,verifyLiveStatus,traceEvents} from './LiveEnterprisePanel';

const status={branch_id:'A',enterprise_id:'SIM-GARMENT-CONT-01',revision:3,as_of:'2026-11-03T08:00:00+08:00',event_count:16,actions:[],connector_id:'live-A'};
const key='a'.repeat(32);
const selection={plan_id:'PLAN-'+'1'.repeat(16),target:'DRAFT-'+'2'.repeat(16),order_id:'SO-LIVE-20',action_type:'MANUFACTURE' as const,manufacture_id:'API-3333333333333333:MO:50'};
const preview={fingerprint:'b'.repeat(64),selection,branch_id:'A',expected_revision:3,payload:{branch_id:'A',world_revision:3,action_type:'MANUFACTURE',plan_id:selection.plan_id,data_version_id:'DATA-current',run_id:'API-'+'3'.repeat(16),input_hash:'c'.repeat(64),order_id:'SO-LIVE-20',quantity:'20',operations:[]},summary:{order_id:'SO-LIVE-20',quantity:'20'},limits:[]};
const yearStatus={...status,year_mode:'BASE',revision:9,business_revision:9,as_of:'2027-02-01T00:00:00+08:00',adoption_available:false,
 selected_source:{checkpoint_id:'2026-11',revision:1796054400,as_of:'2026-12-01T00:00:00+08:00'},
 checkpoints:[{id:'CURRENT',as_of:'2027-02-01T00:00:00+08:00',available:true},{id:'2026-11',as_of:'2026-12-01T00:00:00+08:00',available:true},{id:'2027-10',as_of:'2027-11-01T00:00:00+08:00',available:false}]};

describe('annual actual event pagination',()=>{
 const eventsStatus={...yearStatus,event_count:701};
 const events=Array.from({length:701},(_,index)=>({event_id:'ACTUAL-'+index,occurred_at:'2026-11-03T09:00:00+08:00',
  kind:index===700?'OperationCompleted':'SalesOrderCreated',payload:index===700?{order_id:'SO-RECOVERED',adoption_id:'ADOPTION-REAL',operation_id:'CUT-TB-M'}:{order_id:'SO-'+index}}));
 function fixture(change?:string){
  const paths:string[]=[];
  const read=async(path:string)=>{
   paths.push(path);
   if(!path.includes('/events?'))return change==='final-status'?{...eventsStatus,revision:10,business_revision:10}:eventsStatus;
   const cursor=Number(new URL(path,'http://local.invalid').searchParams.get('after_sequence'));
   const values=events.slice(cursor,cursor+500);
   return {events:values,next_sequence:change==='stalled'?cursor:cursor+values.length,has_more:cursor+values.length<events.length,
    branch_id:cursor&&change==='branch'?'OTHER':yearStatus.branch_id,business_revision:yearStatus.business_revision,
    selected_source:cursor&&change==='source'?{...yearStatus.selected_source,as_of:'2027-01-01T00:00:00+08:00'}:yearStatus.selected_source};
  };
  return {read,paths};
 }
 it('reads both real pages and exposes the operation/adoption event after the first500',async()=>{
  const {read,paths}=fixture();const result=await readLiveEvents(eventsStatus,read);
  expect(result.events).toEqual(events);expect(result.has_more).toBe(false);expect(result.next_sequence).toBe(701);
  expect(paths.filter(path=>path.includes('/events?'))).toEqual(['/v1/simulation/A/events?after_sequence=0&limit=500','/v1/simulation/A/events?after_sequence=500&limit=500']);
  expect(result.events.find(event=>event.event_id==='ACTUAL-700')?.payload).toEqual({order_id:'SO-RECOVERED',adoption_id:'ADOPTION-REAL',operation_id:'CUT-TB-M'});
  const html=renderToStaticMarkup(createElement(LiveEventSection,{result}));
  for(const value of ['701','当前来源已全部读完','SO-RECOVERED','工序完成'])expect(html).toContain(value);
 });
 for(const changed of ['branch','source','final-status'])it('rejects source identity changes without returning stitched events: '+changed,async()=>{
  await expect(readLiveEvents(eventsStatus,fixture(changed).read)).rejects.toThrow(/来源|分支|修订/);
 });
 it('rejects nonprogressing cursors instead of looping or treating a partial page as complete',async()=>{
  const {read,paths}=fixture('stalled');await expect(readLiveEvents(eventsStatus,read)).rejects.toThrow(/游标|分页/);
  expect(paths.filter(path=>path.includes('/events?'))).toHaveLength(1);
 });
 it('does not accept annual pages without authenticated view identity',async()=>{
  await expect(readLiveEvents(eventsStatus,async path=>path.includes('/events?')?{events:events.slice(0,500),next_sequence:500,has_more:true}:eventsStatus)).rejects.toThrow(/身份|来源/);
 });
 it('keeps the original nonannual unpaginated response compatible',async()=>{
  const result=await readLiveEvents(status,async path=>path.includes('/events?')?{events:events.slice(0,2)}:status);
  expect(result.events).toEqual(events.slice(0,2));expect(result.has_more).toBe(false);
 });
});

describe('annual source month selection',()=>{
 it('binds selection to the real business revision and rejects future or unknown checkpoints',()=>{
  expect(liveMutationBody(yearStatus,'source-select',{checkpoint_id:'2026-11'},key)).toEqual({payload:{checkpoint_id:'2026-11'},expected_revision:9,idempotency_key:key});
  for(const checkpoint_id of ['2027-10','OTHER'])expect(()=>liveMutationBody(yearStatus,'source-select',{checkpoint_id},key)).toThrow();
  expect(()=>liveMutationBody(status,'source-select',{checkpoint_id:'2026-11'},key)).toThrow();
 });
 it('keeps current business date, selected source date and source business records readable',()=>{
  const history={as_of:'2026-12-01T00:00:00+08:00',systems:[{system:'ERP',as_of:'2026-12-01T00:00:00+08:00',records:10}],
   source_state:{orders:[{id:'SO-NOV',product_id:'FG-TB-M',quantity:'40',shipped:'20',due:'2026-12-07T17:00:00+08:00'}],
    purchase_orders:[{id:'PO-NOV',item_id:'FAB-BLK',quantity:'100',received:'60',outstanding:'40',unit:'米'}],
    lots:[{id:'LOT-NOV',item_id:'FAB-BLK',physical:'60',available:'50',held:'10',pending:'0',unit:'米'}],
    manufacturing_tasks:[{id:'JOB-NOV',order_id:'SO-NOV',product_id:'FG-TB-M',quantity:'20',status:'WIP'}],
    shipments:[{id:'SHIP-NOV',order_id:'SO-NOV',job_id:'JOB-NOV',quantity:'20'}]}};
  const html=renderToStaticMarkup(createElement(YearSourcePanel,{status:yearStatus,history,busy:false,onSelect:()=>{}}));
  for(const text of ['来源月份与检查点','次月 1 日零点','当前经营日期','正在查看来源','2027','2026','SO-NOV','PO-NOV','LOT-NOV','JOB-NOV','SHIP-NOV','实际发货'])expect(html).toContain(text);
  expect(html).toContain('disabled');
 });
 it('refuses annual history as a current adoption basis',()=>{
  expect(()=>bindLiveAdoptionPreview(preview,selection,{...yearStatus,revision:3,year_mode:'APS_INTERVENTION'})).toThrow();
 });
});

describe('purchase adoption confirmation contents',()=>{
 it('renders the actual native purchase reference, material, supplier, receipt and unit',()=>{
  const payload={action_type:'PURCHASE',purchase_reference:'API-native:PO:950',item_id:'FAB-BLK',supplier_id:'SUP-CONT-1',expected_receipt_at:'2026-11-05T09:00:00+08:00',quantity:'100',uom:'米'};
  const html=renderToStaticMarkup(createElement(AdoptionOperations,{payload}));
  for(const value of ['采购建议引用','物料编号','供应商编号','预计到货（上海）','来源单位','API-native:PO:950','FAB-BLK','SUP-CONT-1','11/05 09:00:00','米'])expect(html).toContain(value);
 });
 it('does not invent missing purchase facts or use the manufacture unit',()=>{
  const html=renderToStaticMarkup(createElement(AdoptionOperations,{payload:{action_type:'PURCHASE',quantity:'100'}}));
  expect(html).toContain('来源单位');expect(html).toContain('未提供');expect(html).not.toMatch(/100\s*件/);
 });
 it('keeps manufacture operation, named person, interval and piece quantity visible',()=>{
  const html=renderToStaticMarkup(createElement(AdoptionOperations,{payload:{action_type:'MANUFACTURE',quantity:'20',operations:[{operation_id:'CUT-TB-M',resource_id:'CUT-01',operator_id:'OP-06',start_at:'2026-11-05T08:00:00+08:00',end_at:'2026-11-05T09:00:00+08:00'}]}}));
  for(const value of ['CUT-TB-M','CUT-01','OP-06','20','件','11/05 08:00:00','11/05 09:00:00'])expect(html).toContain(value);
 });
});

it('shows shared lot and order events once, in actual time order, without summing quantities',()=>{
 const receipt={event_id:'R',occurred_at:'2026-11-03T08:00:00+08:00',payload:{quantity:'100'}},issue={event_id:'I',occurred_at:'2026-11-03T09:00:00+08:00',payload:{quantity:'20'}};
 const rows=traceEvents({supply_events:[issue,receipt],events:[issue]});
 expect(rows).toEqual([receipt,issue]);
});

describe('live enterprise bounded mutations',()=>{
 it('requires the selected branch and a known integer revision before writing',()=>{
  expect(verifyLiveStatus(status,'A')).toBe(status);
  expect(()=>verifyLiveStatus(status,'B')).toThrow();
  expect(()=>verifyLiveStatus({...status,revision:-1},'A')).toThrow();
  expect(()=>verifyLiveStatus({...status,as_of:'2026-11-03T08:00'},'A')).toThrow();
 });
 it('binds legal future advancement to the current revision without changing its timestamp',()=>{
  const payload={to:'2026-11-04T08:00:00+08:00'};
  expect(liveMutationBody(status,'advance',payload,key)).toEqual({payload,expected_revision:3,idempotency_key:key});
  for(const to of ['2026-11-03T07:59:59+08:00','2026-11-04T08:00','bad'])expect(()=>liveMutationBody(status,'advance',{to},key)).toThrow();
 });
 it('accepts only whole 20-piece orders with explicit product, customer and a future due date',()=>{
  const payload={order_id:'SO-LIVE-20',customer_id:'CUST-1',product_id:'FG-TB-M',quantity:'20',due:'2026-11-05T17:00:00+08:00',priority:1};
  expect(liveMutationBody(status,'order',payload,key).payload).toEqual(payload);
  for(const patch of [{quantity:'21'},{quantity:'0'},{quantity:'20x'},{customer_id:''},{product_id:''},{priority:-1},{due:'2026-11-01T08:00:00+08:00'},{release_not_before:'2026-11-03T08:00'}])expect(()=>liveMutationBody(status,'order',{...payload,...patch},key)).toThrow();
 });
 it('rejects unknown writes and invalid supply windows before a request is sent',()=>{
  expect(liveMutationBody(status,'supply',{kind:'ResourceUnavailable',resource_id:'SEW-01',start:'2026-11-03T09:00:00+08:00',end:'2026-11-03T10:00:00+08:00'},key).expected_revision).toBe(3);
  expect(()=>liveMutationBody(status,'supply',{kind:'ResourceUnavailable',resource_id:'SEW-01',start:'2026-11-03T10:00:00+08:00',end:'2026-11-03T09:00:00+08:00'},key)).toThrow();
  expect(()=>liveMutationBody(status,'supply',{kind:'InventorySet',quantity:'999'},key)).toThrow();
  expect(()=>liveMutationBody(status,'advance',{to:'2026-11-04T08:00:00+08:00'},key,true)).toThrow();
 });
});

describe('live adoption review binding',()=>{
 it('requires exact plan/target/order and the selected manufacture or purchase reference',()=>{
  expect(adoptionSelection(selection)).toEqual(selection);
  for(const patch of [{plan_id:'other'},{target:'other'},{order_id:''},{manufacture_id:''}])expect(()=>adoptionSelection({...selection,...patch})).toThrow();
  expect(adoptionSelection({...selection,action_type:'PURCHASE',purchase_reference:'PO-1'})).toEqual({plan_id:selection.plan_id,target:selection.target,order_id:selection.order_id,action_type:'PURCHASE',purchase_reference:'PO-1'});
 });
 it('requires an exact current preview and explicit sandbox acknowledgement to adopt',()=>{
  const bound=bindLiveAdoptionPreview(preview,selection,status);
  expect(liveAdoptionBody(bound,selection,status,key,true)).toEqual({selection,fingerprint:preview.fingerprint,confirmed:true,expected_revision:3,idempotency_key:key});
  expect(()=>liveAdoptionBody(bound,selection,status,key,false)).toThrow();
  expect(()=>liveAdoptionBody(bound,selection,status,key,true,true)).toThrow();
 });
 it('refuses stale revisions, other branches, changed selections and incomplete preview hashes',()=>{
  const bound=bindLiveAdoptionPreview(preview,selection,status);
  expect(()=>liveAdoptionBody(bound,selection,{...status,revision:4},key,true)).toThrow();
  expect(()=>liveAdoptionBody(bound,selection,{...status,branch_id:'B'},key,true)).toThrow();
  expect(()=>liveAdoptionBody(bound,{...selection,manufacture_id:'other'},status,key,true)).toThrow();
  expect(()=>bindLiveAdoptionPreview({...preview,fingerprint:'not-a-hash'},selection,status)).toThrow();
  expect(()=>bindLiveAdoptionPreview({...preview,payload:{...preview.payload,plan_id:'PLAN-'+'9'.repeat(16)}},selection,status)).toThrow();
  expect(()=>bindLiveAdoptionPreview({...preview,selection:{...selection,order_id:'OTHER'}},selection,status)).toThrow();
  expect(()=>bindLiveAdoptionPreview({...preview,selection:undefined},selection,status)).toThrow();
  expect(()=>bindLiveAdoptionPreview({...preview,payload:{...preview.payload,action_type:'PURCHASE'}},selection,status)).toThrow();
 });
});
