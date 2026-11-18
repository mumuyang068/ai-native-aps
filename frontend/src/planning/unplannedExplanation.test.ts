import {expect,it} from 'vitest';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {OrderDetails} from './OrderDetails';
import {UnplannedExplanation} from '../scenarios/UnplannedExplanation';
import type {PlanView} from '../api/contracts';
import type {OrderEvidence,OrderTrace} from '../api/contracts';

function fixture(status='UNPLANNED',wrongRun=false){
 const order={demand_id:'D1',order_ref:'O1',delivery:{demand_id:'D1',requested_quantity:'30',planned_quantity:status==='ON_TIME'?'30':'0',unfulfilled_quantity:status==='ON_TIME'?'0':'30',uom:'piece',due_at:'2026-11-03T18:00:00+08:00',planned_delivery_at:status==='ON_TIME'?'2026-11-03T18:00:00+08:00':null,status,delay_seconds:null},manufacturing:[],operations:[],purchases:[],materials:[],resources:[],constraints:[],demand_records:[{record:{name:'D1',maxlateness:'00:00:00'},source:{solve_run_id:wrongRun?'OTHER':'RUN1',scenario_id:'S1',artifact:'demand.json'},times:{}}],cause_status:'INSUFFICIENT_EVIDENCE'} as unknown as OrderTrace;
 const projection={schema_version:'order-evidence/1',solve_run_id:'RUN1',scenario_id:'S1',source_result_hash:'hash',projection_hash:'projection',missing_tables:[],orders:[order]} as OrderEvidence;
 return {order,projection};
}
function render(status='UNPLANNED',wrongRun=false){return renderToStaticMarkup(createElement(OrderDetails,{...fixture(status,wrongRun),timezone:'Asia/Shanghai',onRelated:()=>{}}));}
it('keeps business evidence actions while removing the Web model launcher',()=>{
 const html=render();expect(html).toContain('来源与证据');expect(html).toContain('关联业务对象');expect(html).not.toContain('向 AI 查询');
});
it('shows unplanned status and shortage in the existing order inspector',()=>{
 const html=render();expect(html).toContain('未排定');expect(html).not.toContain('UNPLANNED');
 expect(html).toContain('计划交付量');expect(html).toContain('欠交数量');expect(html).toContain('30 件');expect(html).toContain('订单行 D1');
 expect(html).toContain('当前无法确认具体瓶颈');expect(html).toContain('不代表不存在约束');
});
it('only shows a zero-lateness rule from the current order and run',()=>{
 expect(render()).toContain('本次不允许晚交');expect(render('UNPLANNED',true)).not.toContain('本次不允许晚交');
});
it('retains the normal on-time view',()=>{
 const html=render('ON_TIME');expect(html).toContain('预计按期');expect(html).not.toContain('当前无法确认具体瓶颈');
});
function comparison(){const{order,projection}=fixture();return {
 base:{run:{solve_run_id:'BASE'},context:{manufacturing_status:{totals:[{quantity:'60',uom:'piece'}]}}} as unknown as PlanView,
 view:{run:{solve_run_id:'RUN1'},result:{result_hash:'hash',deliveries:[order.delivery]},order_evidence:projection,context:{manufacturing_status:{totals:[]}},diff:{base_run_id:'BASE',candidate_run_id:'RUN1',confirmed_supplies:[{reference:'PO'}]}} as unknown as PlanView
};}
it('separates scenario facts, observed shortages and unproven causes',()=>{
 const html=renderToStaticMarkup(createElement(UnplannedExplanation,comparison()));
 for(const text of ['已确认事实','计算观察结果','尚未确认的原因','欠交 30 件','制造建议由 60 件 变为 无','不代表不存在约束','计划级变化','未证明所有其他方案无解'])expect(html).toContain(text);
});
it('does not issue comparison observations from another run',()=>{
 const props=comparison();props.view.diff!.candidate_run_id='OTHER';const html=renderToStaticMarkup(createElement(UnplannedExplanation,props));
 expect(html).toContain('对照暂不可用');expect(html).not.toContain('制造建议由');
});
it('does not treat missing order evidence as an empty constraint table',()=>{
 const props=comparison();delete props.view.order_evidence;const html=renderToStaticMarkup(createElement(UnplannedExplanation,props));
 expect(html).toContain('约束证据尚未完整核对');expect(html).not.toContain('未返回该订单的明确约束记录');
});
