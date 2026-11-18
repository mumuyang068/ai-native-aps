import {describe,it,expect} from 'vitest';
import {cockpitSearch,cockpitDrill,cockpitReturn,cockpitLocatorError,previousCockpit,rememberCockpit,cockpitPeriodParams} from './cockpitNavigation';
import {fulfillmentVisibleRows} from './demandFulfillmentModel';
import {reportRowId} from './reportModel';
const s={planId:'PLAN-0123456789abcdef',versionId:'DATA-example',runId:'API-0123456789abcdef',target:'baseline'};
describe('Cockpit exact-scope navigation',()=>{
 it('preserves an explicitly selected zero-activity period through the original report filter owner',()=>{
  const row={item_id:'FG',location_id:'FACTORY',uom:'件',endpoint:'阶段产出',start_at:'2026-11-16T00:00:00+08:00',end_at:'2026-11-23T00:00:00+08:00',due_quantity:'0',planned_quantity:'0',overdue_unfulfilled:'0'};
  const p=cockpitPeriodParams(s,'week','group',reportRowId(row),false);
  expect(p.report_active).toBe('false');expect(p.report_row).toBe(reportRowId(row));
  expect(fulfillmentVisibleRows([row],[],'',p.report_active!=='false')).toHaveLength(1);
  expect(fulfillmentVisibleRows([row],[],'',true)).toHaveLength(0);
 });
 it('only restores a cross-target work origin after both destination and authoritative plan-state origin match',()=>{
  const destination={...s,target:'DRAFT-new',runId:''};
  const p=cockpitDrill(new URLSearchParams({target:s.target}),s,'work:DRAFT-new',{target:destination.target,analysis:'schedule'},destination);
  expect(cockpitReturn(p,destination)).toBeNull();
  expect(cockpitReturn(p,destination,[s])?.get('cockpit_focus')).toBe('work:DRAFT-new');
  expect(cockpitReturn(p,destination,[{...s,runId:'stale'}])).toBeNull();
  expect(cockpitReturn(p,{...destination,runId:'new-run'},[s])).toBeNull();
  expect(cockpitReturn(p,{...destination,versionId:'other'},[s])).toBeNull();
  expect(cockpitReturn(p,{...destination,planId:'other'},[s])).toBeNull();
  expect(()=>cockpitDrill(new URLSearchParams(),s,'work',{}, {...destination,planId:'other'})).toThrow();
 });
 it('restores only the original cockpit selection, not another run or target',()=>{
  const p=cockpitDrill(new URLSearchParams('target=baseline&analysis=cockpit&cockpit_grain=week&cockpit_group=FG&order_line_id=stale'),s,'demand-1',{analysis:'reports',report:'demand',report_row:'exact-bucket'});
  expect(p.get('report_row')).toBe('exact-bucket');expect(p.has('order_line_id')).toBe(false);
  expect(cockpitReturn(p,s)?.get('cockpit_focus')).toBe('demand-1');
  expect(cockpitReturn(p,{...s,runId:'API-other'})).toBeNull();expect(cockpitReturn(p,{...s,target:'DRAFT-other'})).toBeNull();
 });
 it('rejects injected navigation, malformed and oversized return state',()=>{
  const p=cockpitDrill(new URLSearchParams(),s,'x',{analysis:'inventory'}),v=JSON.parse(p.get('cockpit_return')!);v.search+='&redirect=https://other.invalid';p.set('cockpit_return',JSON.stringify(v));expect(cockpitReturn(p,s)).toBeNull();
  for(const text of ['{','a'.repeat(5001)])expect(cockpitReturn(new URLSearchParams({cockpit_return:text}),s)).toBeNull();
 });
 it('rejects explicit locator mismatch and preserves ordinary safe plan navigation',()=>{
  expect(cockpitLocatorError(new URLSearchParams({run_id:'API-other'}),s)).toBeTruthy();
  expect(cockpitLocatorError(new URLSearchParams({data_version_id:'DATA-other'}),s)).toBeTruthy();
  expect(cockpitLocatorError(new URLSearchParams({run_id:s.runId,data_version_id:s.versionId}),s)).toBe('');
  expect(cockpitSearch(new URLSearchParams('analysis=reports&run_id=old')).has('run_id')).toBe(false);
 });
 it('persists identity only, safely ignores denied storage or invalid values',()=>{
  let text:string|null=null;const storage={getItem:()=>text,setItem:(_k:string,v:string)=>{text=v}};
  rememberCockpit(storage,s.planId,s.target);expect(previousCockpit(storage)).toEqual({planId:s.planId,target:s.target});
  text='{"planId":"https://bad","target":"baseline"}';expect(previousCockpit(storage)).toBeNull();
  expect(()=>rememberCockpit({setItem:()=>{throw Error('denied')}},s.planId,s.target)).not.toThrow();
 });
});
