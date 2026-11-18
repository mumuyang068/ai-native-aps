import {expect,it,vi} from 'vitest';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import type {Dict} from '../api/contracts';
const captured=vi.hoisted(()=>[] as Dict[]);
vi.mock('../components/PlanningChart',()=>({PlanningChart:({option}:{option:Dict})=>{captured.push(option);return null}}));
import {CockpitComparison} from './CockpitComparison';

function render(changes=true){
 captured.length=0;
 const profile='enterprise-accessory-supply-v1',scope={planId:'P',versionId:'V',runId:'C',target:'D',profile};
 const identity=(baseline:boolean)=>({plan_id:'P',data_version_id:'V',run_id:baseline?'B':'C',target:baseline?'baseline':'D',profile});
 const report=(baseline:boolean)=>({identity:identity(baseline),timezone:'Asia/Shanghai',grain:'week',range:{start_at:'2026-11-01T00:00:00+08:00',end_at:'2026-12-01T00:00:00+08:00'},demand:{status:'PRESENT',validation:{status:'PASS'},lines:Array.from({length:8},(_,i)=>({demand_id:'L'+i,order_ref:i<6?'UNCHANGED-'+i:i===6?'MORE-SHORT':'LESS-SHORT',item_id:'FG',location_id:'FACTORY',endpoint:'FG',uom:'件',demand_kind:'FIRM_ORDER',unfulfilled_quantity:changes&&i>=6?(baseline?(i===6?0:20):(i===6?20:0)):0,delivery_validation_status:'PASS',delivery_evidence_status:'PRESENT'}))}});
 const html=renderToStaticMarkup(createElement(CockpitComparison,{data:report(false),base:report(true),native:null,baseNative:null,context:{},baseContext:{},scope,demandKind:'FIRM_ORDER',error:'',pending:false,enabled:true,onEnable:()=>{},open:()=>{},compareUrl:'#/complete-comparison'}));
 return {html,options:[...captured]};
}

it('puts actual signed changes ahead of unchanged rows when many objects share Home',()=>{
 const{html,options}=render();
 expect(html).toContain('MORE-SHORT');expect(html).toContain('LESS-SHORT');
 expect(html).not.toContain('UNCHANGED-0');expect(html).toContain('#/complete-comparison');
 expect(options[0].series[0].data.map((r:Dict)=>r.value)).toEqual([20,-20]);
 expect(html).toContain('其余 6 项数值未变');
});

it('states an observed no-change result without drawing five empty bars',()=>{
 const{html,options}=render(false);
 expect(html).toContain('8 项可比指标均无数值变化');expect(options).toHaveLength(0);
 expect(html).toContain('#/complete-comparison');
});
