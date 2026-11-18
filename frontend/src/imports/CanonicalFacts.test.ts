import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {MemoryRouter} from 'react-router';
import {expect,it} from 'vitest';
import type {ImportTask} from '../api/contracts';
import {CanonicalReview,IncrementReview} from './CanonicalFacts';

function task(overrides:Partial<ImportTask>={}):ImportTask{
 return {id:'IMP-stages',filename:'企业来源.json',revision:1,state:'PENDING_REVIEW',file_sha256:'a'.repeat(64),version_ids:[],canonical:{schema_revision:'canonical-business-v1',enterprise_id:'FACTORY',planning_at:'2026-11-01T00:00:00+08:00',horizon:{start_at:'2026-11-01T00:00:00+08:00',end_at:'2026-12-01T00:00:00+08:00'},tables:{demand_lines:[]},objects:[],relations:[],data_status:'DATA_ACCEPTED',timezone:'Asia/Shanghai',unresolved_policies:[],issues:[],semantic_sha256:'b'.repeat(64),planning_readiness:{ready:false,status:'NOT_PROJECTED',blocking_issues:[]}},canonical_groups:['demand_lines'],source:{sheets:{}},mappings:{},customers:{},confirmations:{},validation:{issues:[]},readiness:{ready:false,confirmed:false,blocking_count:0,can_save_facts:false},ai_events:[],duplicate_records:[],corrections:[],...overrides};
}
function render(data:ImportTask){
 return renderToStaticMarkup(createElement(MemoryRouter,null,createElement(CanonicalReview,{task:data,busy:false,error:'',note:'',onSave:()=>{},onConfirm:()=>{}})));
}

it('distinguishes source reception from unconfirmed business facts and independent planning checks',()=>{
 const html=render(task({ingestion_stages:{reception:{status:'RECEIVED',originals_retained:true,transport:'STRUCTURED'},business:{status:'PENDING_CONFIRMATION',issues:[],confirmed_groups:[],requires_confirmation:true,partial_publication_supported:false},planning:{status:'NOT_PROJECTED',ready:false,issues:[],requires_policy_projection:true}}}));
 expect(html).toContain('来源记录已接收');
 expect(html).toContain('业务事实待确认');
 expect(html).toContain('规划待独立核验');
 expect(html).not.toContain('企业事实已接纳');
 expect(html).not.toContain('STRUCTURED');
});

it('shows explicit business confirmation without claiming that planning is ready',()=>{
 const html=render(task({confirmations:{demand_lines:{reviewer:'计划员'}},readiness:{ready:false,confirmed:true,blocking_count:0,can_save_facts:true},ingestion_stages:{reception:{status:'RECEIVED',originals_retained:true},business:{status:'CONFIRMED',issues:[],confirmed_groups:['demand_lines'],requires_confirmation:false,partial_publication_supported:false},planning:{status:'NOT_PROJECTED',ready:false,issues:[{code:'FORECAST_POLICY_PENDING'}],requires_policy_projection:true}}}));
 expect(html).toContain('业务事实已确认');
 expect(html).toContain('规划待独立核验');
 expect(html).not.toContain('规划条件已具备');
 expect(html).toContain('确认选定领域');
 expect(html).toContain('保存企业事实版本');
});

it('keeps blocked business records separate from received source records',()=>{
 const pending=task();
 pending.canonical!.data_status='DATA_BLOCKED';
 pending.canonical!.issues=[{code:'DANGLING_REFERENCE',table:'demand_lines',id:'D-1',field:'item_id'}];
 const html=render({...pending,ingestion_stages:{reception:{status:'RECEIVED',originals_retained:true},business:{status:'BLOCKED',issues:pending.canonical!.issues,confirmed_groups:[],requires_confirmation:true,partial_publication_supported:false},planning:{status:'NOT_PROJECTED',ready:false,issues:[],requires_policy_projection:true}}});
 expect(html).toContain('来源记录已接收');
 expect(html).toContain('业务事实需修正');
 expect(html).not.toContain('业务事实已确认');
 expect(html).toContain('DANGLING_REFERENCE');
});

it('preserves old task compatibility while avoiding acceptance language before confirmation',()=>{
 expect(render(task())).toContain('业务事实待确认');
 const confirmed=task({confirmations:{demand_lines:{reviewer:'仓库'}},readiness:{ready:false,confirmed:true,blocking_count:0,can_save_facts:true}});
 const html=render(confirmed);
 expect(html).toContain('业务事实已确认');
 expect(html).toContain('规划待独立核验');
 expect(html).not.toContain('企业事实已接纳');
});

it('keeps uncertain confirmation recovery reachable in the incremental review without replaying confirmation',()=>{
 let confirmations=0;
 const data=task({state_change:{enterprise_id:'FACTORY',parent_version_id:'DATA-parent',source_as_of:'2026-11-02T00:00:00+08:00',changes:[{system:'ERP',table:'sales_orders',id:'SO-1',classification:'UPDATED',row:{id:'SO-1',priority:2}}]},canonical_groups:['ERP.sales_orders']});
 const html=renderToStaticMarkup(createElement(MemoryRouter,null,createElement(IncrementReview,{
  task:data,busy:true,error:'确认响应未收到',note:'',onConfirm:()=>{confirmations++},
  recovery:createElement('div',{role:'status'},createElement('button',null,'读取最新资料核对'))
 })));
 expect(html).toContain('读取最新资料核对');
 expect(html).toMatch(/type="checkbox" disabled/);
 expect(html).toContain('SO-1');
 expect(confirmations).toBe(0);
});
