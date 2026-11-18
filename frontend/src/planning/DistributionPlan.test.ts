import {it,expect} from 'vitest';
import * as model from './presentationModel';
const input={rules:{manufacturing_profile:'single-lane-distribution-v1'},inventory_positions:[{id:'A',location_id:'FACTORY',item_id:'FG',on_hand:'40',uom:'piece'},{id:'B',location_id:'SHOP-1',item_id:'FG',on_hand:'0',uom:'piece'}]};
it('keeps opening and ending balances separate by location without adding in-transit stock',()=>{
 const build=(model as Record<string,any>).distributionView;expect(typeof build).toBe('function');
 const view=build(input,{distribution_suggestions:[{engine_ref:'3',origin_location_id:'FACTORY',destination_location_id:'SHOP-1',quantity:'20',source_status:'proposed'}]},
 {status:'PASS',summary:{locations:[{location_id:'FACTORY',ending:'20'},{location_id:'SHOP-1',ending:'0'}]}});
 expect(view.locations.map((r:any)=>[r.location_id,r.on_hand,r.ending])).toEqual([['FACTORY','40','20'],['SHOP-1','0','0']]);
 expect(view.suggestions[0].source_status).toBe('proposed');
});
it('keeps unplanned demand quantities and unknown dates and does not fabricate a transfer',()=>{
 const build=(model as Record<string,any>).distributionView;expect(typeof build).toBe('function');
 const result={distribution_suggestions:[],deliveries:[{demand_id:'D',planned_quantity:'0',unfulfilled_quantity:'20',planned_delivery_at:null}]};
 const view=build(input,result,{status:'PASS',summary:{locations:[]}});
 expect(view.suggestions).toEqual([]);expect(view.deliveries[0].planned_delivery_at).toBeNull();expect(view.deliveries[0].unfulfilled_quantity).toBe('20');
});
it('does not present unaudited ending inventory as verified stock',()=>{
 const build=(model as Record<string,any>).distributionView;expect(typeof build).toBe('function');
 const view=build(input,{}, {status:'NOT_CHECKED',summary:{locations:[{location_id:'FACTORY',ending:'40'}]}});
 expect(view.locations.every((r:any)=>r.ending===null)).toBe(true);
});

it('renders dispatch and receipt separately and calls proposed transport a simulation',async()=>{
 const {createElement}=await import('react'),{renderToStaticMarkup}=await import('react-dom/server'),{DistributionPlan}=await import('./DistributionPlan');
 const result={distribution_suggestions:[{id:'DO3',engine_ref:'3',origin_location_id:'FACTORY',destination_location_id:'SHOP-1',quantity:'20',uom:'piece',ship_at:'2026-11-03T18:00:00+08:00',receipt_at:'2026-11-05T18:00:00+08:00',source_status:'proposed'}],deliveries:[]};
 const html=renderToStaticMarkup(createElement(DistributionPlan,{input,result,report:{status:'PASS',summary:{locations:[{location_id:'FACTORY',ending:'20'},{location_id:'SHOP-1',ending:'0'}]}},timezone:'Asia/Shanghai'}));
 for(const value of ['FACTORY','SHOP-1','2026-11-03 18:00','2026-11-05 18:00','配送建议 · 未发运','未实际发运','20 件'])expect(html).toContain(value);
});
it('renders shortage with no completion date and labels absent audit as unknown',async()=>{
 const {createElement}=await import('react'),{renderToStaticMarkup}=await import('react-dom/server'),{DistributionPlan}=await import('./DistributionPlan');
 const result={distribution_suggestions:[],deliveries:[{demand_id:'D',requested_quantity:'20',planned_quantity:'0',unfulfilled_quantity:'20',uom:'piece',due_at:'2026-11-03T18:00:00+08:00',planned_delivery_at:null,status:'UNPLANNED'}]};
 const html=renderToStaticMarkup(createElement(DistributionPlan,{input,result,timezone:'Asia/Shanghai'}));
 for(const value of ['证据不足','尚未核实','没有配送建议','未排定，无完成日期','20 件'])expect(html).toContain(value);
 expect(html).not.toContain('本次地点库存与运输核验通过');
});
