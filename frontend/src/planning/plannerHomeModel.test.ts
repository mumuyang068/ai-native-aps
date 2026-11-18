import {it,expect} from 'vitest';
import {homeHistoryGroups,historyChartRows,homePlanLink,homeHistoryLink,homeTaskLink} from './plannerHomeModel';
it('task navigation uses the SPA route with the original candidate, not a nested workspace hash',()=>{
 const link=homeTaskLink('P',{id:'TASK-t',identity:{target:'DRAFT-c'},href:'/workspace/#/plans/P'});
 expect(link).toBe('/plans/P?target=DRAFT-c&analysis=cockpit&assistant=1&assistant_task=TASK-t');expect(link).not.toContain('#');
});
it('history drill keeps the selected product, location and customer cohort',()=>{
 const link=homeHistoryLink('P',{item_id:'FG',location_id:'L',customer_id:'C2',uom:'件'});
 const q=new URLSearchParams(link.split('?')[1]);expect(q.get('history_customer')).toBe('C2');expect(q.get('history_item')).toBe('FG');expect(q.get('history_location')).toBe('L');expect(q.get('target')).toBe('baseline');
});
it('rejects cross enterprise, data, plan and result history instead of splicing datasets',()=>{
 const v={identity:{plan_id:'P',data_version_id:'D',enterprise_id:'E',run_id:null},groups:[]};
 expect(homeHistoryGroups(v,{planId:'P',versionId:'D'},'E')).toEqual([]);
 for(const identity of [{...v.identity,enterprise_id:'F'},{...v.identity,plan_id:'Q'},{...v.identity,data_version_id:'V'},{...v.identity,run_id:'API-x'}])expect(()=>homeHistoryGroups({...v,identity},{planId:'P',versionId:'D'},'E')).toThrow();
});
it('keeps month end stock separate from event flow and never fills unknown with zero',()=>{
 const rows=historyChartRows({cells:[{status:'KNOWN',new_quantity:'20',shipped:'12',ending_open:'8'},{status:'NOT_YET_KNOWN',new_quantity:'0',shipped:null,ending_open:null}]},[{id:'m1',start:'2026-11-01'},{id:'m2',start:'2026-12-01'}]);
 expect(rows[0]).toMatchObject({created:20,shipped:12,balance:8});expect(rows[1]).toMatchObject({created:null,shipped:null,balance:null});
 expect(homePlanLink('P',{analysis:'review',review_drafts:'DRAFT-x'})).toContain('review_drafts=DRAFT-x');
});
it('marks partial source coverage rather than presenting a partially observed month as complete',()=>{
 const rows=historyChartRows({cells:[{status:'KNOWN',shipped:'4'}]},[{id:'m',label:'2027-11',start:'2027-11-01',end:'2027-12-01',partial:false}],'2027-11-02');expect(rows[0].label).toBe('2027-11 *');
});
