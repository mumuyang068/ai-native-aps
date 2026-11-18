import {describe,it,expect} from 'vitest';
import {cockpitDemandMap} from './cockpitDemandMap';
import type {Dict} from '../api/contracts';
const pass={status:'PASS',issues:[],missing_evidence:[]};
function input():Dict{
 const line=(id:string,item:string,due:string|null,kind='FIRM_ORDER',planned='6',short='0')=>({demand_id:id,order_ref:id,item_id:item,location_id:'F',endpoint:item,uom:'件',demand_kind:kind,due_at:due,requested_quantity:'6',planned_quantity:planned,unfulfilled_quantity:short,delivery_validation_status:'PASS',delivery_evidence_status:'PRESENT'});
 return {identity:{profile:'enterprise-accessory-supply-v1'},range:{start_at:'2026-11-01T00:00:00+08:00',end_at:'2026-12-01T00:00:00+08:00'},demand:{status:'PRESENT',validation:pass,lines:[line('A1','A','2026-11-06T18:00:00+08:00','FIRM_ORDER','2','4'),line('A2','A','2026-11-06T18:00:00+08:00'),line('B1','B',null),line('N1','A','2026-11-30T18:00:00+08:00','NET_FORECAST','0','6')],delivery_performance:{status:'PRESENT',validation:pass,groups:[{item_id:'A',location_id:'F',endpoint:'A',uom:'件',lines:[{demand_id:'A1',classification:'PARTIALLY_PLANNED'},{demand_id:'A2',classification:'FULL_ON_TIME'}]}]}}};
}
describe('product / due-date map evidence',()=>{
 it('clusters simultaneous demands without hiding a partial line or summing different units',()=>{const m=cockpitDemandMap(input(),'FIRM_ORDER');expect(m.lineCount).toBe(3);expect(m.points).toHaveLength(2);expect(m.points[0].lines.map(l=>l.id)).toEqual(['A1','A2']);expect(m.points[0].state).toBe('PARTIALLY_PLANNED');expect(m.points[0]).not.toHaveProperty('totalQuantity')});
 it('keeps an unknown date outside positioned events without manufacturing a completion date',()=>{const p=cockpitDemandMap(input(),'FIRM_ORDER').points.find(p=>p.group.includes('B'));expect(p?.at).toBeNull();expect(p?.state).toBe('UNKNOWN');expect(p?.lines[0]).not.toHaveProperty('completion_at')});
 it('does not infer on-time status from a complete quantity or legacy delivery date',()=>{const r=input();delete r.demand.delivery_performance;for(const l of r.demand.lines)l.planned_delivery_at=l.due_at;expect(cockpitDemandMap(r,'FIRM_ORDER').points.every(p=>p.state==='UNKNOWN')).toBe(true)});
 it('separates forecast coverage from Firm delivery performance',()=>{const m=cockpitDemandMap(input(),'NET_FORECAST');expect(m.lineCount).toBe(1);expect(m.points[0].state).toBe('NOT_PLANNED');expect(m.points[0].lines[0].id).toBe('N1')});
 it('keeps same-name products in another location on distinct lanes',()=>{const r=input();r.demand.lines.push({...r.demand.lines[0],demand_id:'SHOP',location_id:'SHOP'});expect(cockpitDemandMap(r,'FIRM_ORDER').products).toHaveLength(3)});
 it('does not reuse a proof for the same demand ID in a different product group',()=>{const r=input();r.demand.delivery_performance.groups[0].item_id='WRONG';expect(cockpitDemandMap(r,'FIRM_ORDER').points[0].state).toBe('UNKNOWN')});
 it('preserves the observation half-open boundary and retains undated entries separately',()=>{const r=input();r.range.end_at='2026-11-06T18:00:00+08:00';const m=cockpitDemandMap(r,'FIRM_ORDER');expect(m.points).toHaveLength(1);expect(m.points[0].at).toBeNull()});
 it('retains an original hard FAIL instead of plotting successful-looking markers',()=>{const r=input();r.known_business_fail=true;expect(cockpitDemandMap(r,'FIRM_ORDER').points).toEqual([])});
});
