import {expect,it} from 'vitest';
import {layoutTaskBars,supplyRows,activeOrder,relatedQuantity,chronologicalEntries,taskDaySpan,demandEndpointKind,demandTabLabel} from './presentationModel';
import * as presentation from './presentationModel';
it('keeps actual FG manufacturing suggestions separate by item, location and unit in the selected result',()=>{
 const summarize=(presentation as Record<string,any>).stageManufactureText;expect(typeof summarize).toBe('function');
 const result={manufacture_suggestions:[
  {item_id:'CUT-TB-M',location_id:'FACTORY',quantity:'20',uom:'套',start_at:'2026-11-06T09:00:00+08:00'},
  {item_id:'FG-TB-M',location_id:'FACTORY',quantity:'20',uom:'件',start_at:'2026-11-06T16:20:00+08:00'},
  {item_id:'SEWN-TB-M',location_id:'FACTORY',quantity:'20',uom:'件',start_at:'2026-11-06T13:40:00+08:00'}]};
 expect(summarize(result)).toBe('CUT-TB-M @ FACTORY 20 套；SEWN-TB-M @ FACTORY 20 件；FG-TB-M @ FACTORY 20 件');
 expect(summarize({manufacture_suggestions:[{...result.manufacture_suggestions[0],quantity:'10',location_id:'OTHER'}]})).toBe('CUT-TB-M @ OTHER 10 套');
 expect(result.manufacture_suggestions[1].item_id).toBe('FG-TB-M');
});
it('labels customer FG, stage output and ordinary delivery from declared endpoint semantics',()=>{
 expect(demandEndpointKind({wip:{stage_endpoint:{kind:'CUSTOMER_FG'}}})).toBe('CUSTOMER_FG');
 expect(demandTabLabel({wip:{stage_endpoint:{kind:'CUSTOMER_FG'}}})).toBe('客户FG需求 ');
 expect(demandTabLabel({wip:{stage_endpoint:{scope:'BOUNDED_SYNTHETIC_FINISHED_CUSTOMER_DEMAND'}}})).toBe('客户FG需求 ');
 expect(demandTabLabel({wip:{stage_endpoint:{scope:'STAGE_OUTPUT',label:'缝后阶段'}}})).toBe('阶段需求 ');
 expect(demandTabLabel(undefined)).toBe('订单交付 ');
});
it('keeps true short-task width but separates at least 24px selection targets',()=>{
 const tasks=[{id:'A',start_at:'2026-10-15T09:00Z',end_at:'2026-10-15T09:35Z'},{id:'B',start_at:'2026-10-15T09:35Z',end_at:'2026-10-15T10:10Z'}];
 const start=Date.parse('2026-10-15T00:00Z'),end=start+15*86400000,r=layoutTaskBars(tasks,start,end,900);
 expect(r.bars).toHaveLength(2);expect(r.bars[0].hitWidth).toBeGreaterThanOrEqual(24);expect(r.bars[0].barWidth).toBeCloseTo(35/60/24/15*900);expect(r.bars[0].showLabel).toBe(false);expect(r.bars[0].lane).not.toBe(r.bars[1].lane);
 expect(tasks[0].end_at).toBe('2026-10-15T09:35Z');
});
it('clips visible windows and excludes out-of-window tasks without changing the source',()=>{
 const r=layoutTaskBars([{id:'A',start_at:'2026-10-14T00:00Z',end_at:'2026-10-16T00:00Z'},{id:'B',start_at:'2026-10-17T00:00Z',end_at:'2026-10-18T00:00Z'}],Date.parse('2026-10-15T00:00Z'),Date.parse('2026-10-16T00:00Z'),600);
 expect(r.bars).toHaveLength(1);expect(r.bars[0].barWidth).toBe(600);expect(r.bars[0].left).toBe(0);
});
it('formats procurement time basis and units from confirmed data without guessing',()=>{
 const c={effective_constraints:[{id:'supply:S',value:{item_id:'FAB',supplier_id:'SUP',lead_time_seconds:172800,minimum_order_quantity:'100',order_multiple:'100'},source:{pointer:'/supply_sources/0'}}]} as any;
 const input={items:[{id:'FAB',base_uom:'m'}],supply_sources:[{id:'S',item_id:'FAB',supplier_id:'SUP',lead_time_basis:'CALENDAR_TIME'}]};
 const row=supplyRows(c,input)[0];expect(row.leadTime).toBe('2 个自然日');expect(row.minimum).toBe('100 米');expect(row.multiple).toBe('100 米');expect(row.source.pointer).toBe('/supply_sources/0');
 const unknown=supplyRows(c,{items:[],supply_sources:[]})[0];expect(unknown.leadTime).toContain('计时方式未注明');expect(unknown.minimum).toContain('单位未提供');
});
it('never substitutes the first order for a selected order or an ambiguous selection',()=>{
 const rows=[{id:'D1',order_ref:'O1'},{id:'D2',order_ref:'O2'}];
 expect(activeOrder(rows,'D2')?.id).toBe('D2');expect(activeOrder(rows,null)).toBeUndefined();expect(activeOrder(rows,'OTHER')).toBeUndefined();expect(activeOrder(rows.slice(0,1),null)?.id).toBe('D1');
});
it('gets operation units through the explicit owner manufacture and preserves decimal precision',()=>{
 const order={manufacturing:[{engine_ref:'44',task:{uom:'piece'}}]} as any;
 expect(relatedQuantity(order,'operations',{owner_ref:'44',record:{quantity:'70.00000000'},task:{quantity:'70'}} as any)).toBe('70 件');
 expect(relatedQuantity(order,'materials',{record:{quantity:'-140.01000000'},item_uom:'m'} as any)).toBe('-140.01 米');
 expect(relatedQuantity(order,'operations',{owner_ref:'OTHER',record:{quantity:'1'}} as any)).toContain('单位未提供');
});
it('orders associated operations by actual start without changing native rows',()=>{
 const rows=[{id:'PACK',times:{startdate:'2026-10-15T17:00Z'}},{id:'CUT',times:{startdate:'2026-10-15T09:00Z'}}] as any;
 expect(chronologicalEntries(rows).map(r=>r.id)).toEqual(['CUT','PACK']);expect(rows[0].id).toBe('PACK');
});
it('fits the complete task across midnight even when elapsed time is less than one day',()=>{expect(taskDaySpan({id:'A',start_at:'2026-10-15T16:30:00+08:00',end_at:'2026-10-16T12:00:00+08:00'},'Asia/Shanghai')).toEqual({start:'2026-10-15',days:2})});
