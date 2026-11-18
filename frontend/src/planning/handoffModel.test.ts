import {expect,it} from 'vitest';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {handoffRows,handoffExport} from './handoffModel';
import {ScenarioHandoff} from './ScenarioHandoff';
const base={target:'baseline',run_id:'R',comparable:true,calculation_state:'SUCCEEDED',business_status:'FEASIBLE_ON_TIME',validation:{material:{status:'PASS'},schedule:{status:'PASS'},resources:null},deliveries:[{demand_id:'O',demand_kind:'FIRM_ORDER',uom:'ea',requested_quantity:'2',planned_quantity:'2'},{demand_id:'N',demand_kind:'NET_FORECAST',uom:'ea',requested_quantity:'4',planned_quantity:null}],inventory:[],professional:{identity:{plan_id:'P',data_version_id:'V',target:'baseline',run_id:'R',profile:'enterprise-accessory-supply-v1'},evidence_files:[{artifact:'x',sha256:'a'.repeat(64)}]}};
it('separates original Firm, explicit new hypothesis and Net without inventing attribution',()=>{const c={...base,target:'D',changes:{hypothesis:{id:'NEW'}},deliveries:[...base.deliveries,{demand_id:'NEW',demand_kind:'FIRM_ORDER',uom:'ea'}]};const rows=handoffRows(c,base);expect(rows.filter(r=>r.section==='sales').map(r=>r.category)).toEqual(['ORIGINAL_FIRM','NET_FORECAST','NEW_FIRM_ASSUMPTION']);expect(rows.find(r=>r.object_id==='N')?.planned_quantity).toBeNull();expect(rows.some(r=>r.section==='evidence')).toBe(true)});
it('NOT_RUN never exports stale native results as current',()=>{const c={...base,target:'D',run_id:null,calculation_state:'NOT_RUN'};expect(handoffRows(c,base).some(r=>r.section==='sales')).toBe(false);expect(handoffExport(c,base,{planId:'P',versionId:'V',baselineRunId:'R'},'Asia/Shanghai',{}).status).toBe('NOT_RUN')});

// Same fields and amounts as API-8c664e7088414051. scenario-review obtains
// demand_kind=INQUIRY from canonical kind, never from the demand id spelling.
const inquiry={demand_id:'INQUIRY-453f852bd0bb4c4d',demand_kind:'INQUIRY',item_id:'FG-TB-M',location_id:'FACTORY',uom:'件',requested_quantity:'20',planned_quantity:'20',unfulfilled_quantity:'0',due_at:'2026-12-10T10:00:00.000Z',planned_delivery_at:'2026-12-10T18:00:00+08:00',status:'ON_TIME'};
it('keeps the real research inquiry separate from Firm and Net with full quantities and dates',()=>{
 const c={...base,target:'D',deliveries:[inquiry,...base.deliveries]};
 const row=handoffRows(c,base).find(r=>r.object_id===inquiry.demand_id)!;
 expect(row.category).toBe('INQUIRY');
 expect([row.quantity,row.planned_quantity,row.unfulfilled_quantity,row.due_at,row.planned_delivery_at]).toEqual(['20','20','0',inquiry.due_at,inquiry.planned_delivery_at]);
 expect(handoffRows(c,base).filter(r=>r.section==='sales').map(r=>r.category)).toEqual(['INQUIRY','ORIGINAL_FIRM','NET_FORECAST']);
 const renamed={...c,deliveries:[{...inquiry,demand_id:'NO_TYPE_PREFIX'}]};
 expect(handoffRows(renamed,base).find(r=>r.object_id==='NO_TYPE_PREFIX')?.category).toBe('INQUIRY');
});
it('does not infer inquiry kind from an id or leak a missing inquiry result into the selected run',()=>{
 const c={...base,target:'D',deliveries:[{...inquiry,demand_kind:undefined}]};
 expect(handoffRows(c,base).find(r=>r.object_id===inquiry.demand_id)?.category).toBe('UNKNOWN_DEMAND_KIND');
 const missing=handoffRows({...base,target:'D'}, {...base,deliveries:[inquiry,...base.deliveries]}).find(r=>r.object_id===inquiry.demand_id)!;
 expect(missing.category).toBe('INQUIRY');expect(missing.quantity).toBeNull();expect(missing.planned_quantity).toBeNull();
 expect(missing.change).toBe('NOT_PRESENT_IN_SELECTED');
});
it('renders a dedicated research inquiry cell with its actual twenty units and original-object return',()=>{
 const candidate={...base,target:'D',deliveries:[inquiry,...base.deliveries]};
 const html=renderToStaticMarkup(createElement(ScenarioHandoff,{scope:{planId:'P',versionId:'V',baselineRunId:'R'},candidates:[base,candidate],timezone:'Asia/Shanghai',search:new URLSearchParams('handoff_target=D'),onSearch:()=>{},onOpen:()=>{}}));
 expect(html).toContain('<th>研究询价</th>');
 const start=html.indexOf(inquiry.demand_id),row=html.slice(start,html.indexOf('</tr>',start)),cells=row.match(/<td[\s\S]*?<\/td>/g)!;
 expect(cells.slice(0,3)).toEqual(['<td>—</td>','<td>—</td>','<td>—</td>']);
 expect(cells[3]).toContain('需求 20 件');expect(cells[3]).toContain('计划满足 20 件');expect(cells[3]).toContain('欠量 0 件');
 expect(cells[3]).toContain(inquiry.due_at);expect(cells[3]).toContain(inquiry.planned_delivery_at);
 expect(row).toContain('研究询价');expect(row).not.toContain('需求类型未知');expect(row).toContain('返回原对象');
});
it('labels an explicit inquiry absent from a valid baseline as added research rather than improvement',()=>{
 const c={...base,target:'D',deliveries:[inquiry,...base.deliveries]};
 expect(handoffRows(c,base).find(r=>r.object_id===inquiry.demand_id)?.change).toBe('ADDED_RESEARCH_INQUIRY');
 const html=renderToStaticMarkup(createElement(ScenarioHandoff,{scope:{planId:'P',versionId:'V',baselineRunId:'R'},candidates:[base,c],timezone:'Asia/Shanghai',search:new URLSearchParams('handoff_target=D'),onSearch:()=>{},onOpen:()=>{}}));
 const start=html.indexOf(inquiry.demand_id),row=html.slice(start,html.indexOf('</tr>',start));
 expect(row).toContain('新增研究询价');expect(row).not.toContain('改善');expect(row).not.toContain('ADDED_RESEARCH_INQUIRY');
});
it('retains actual comparison or unknown when the baseline has this inquiry or lacks valid evidence',()=>{
 const c={...base,target:'D',deliveries:[inquiry]};
 const same={...base,deliveries:[inquiry]};
 expect(handoffRows(c,same).find(r=>r.object_id===inquiry.demand_id)?.change).toBe('UNCHANGED');
 const changed={...same,deliveries:[{...inquiry,planned_quantity:'10'}]};
 expect(handoffRows(c,changed).find(r=>r.object_id===inquiry.demand_id)?.change).toBe('CHANGED');
 for(const b of [{...base,comparable:false},{...base,deliveries:undefined},{...base,deliveries:[{...inquiry,uom:'其他单位'}]}])
  expect(handoffRows(c,b).find(r=>r.object_id===inquiry.demand_id)?.change).toBe('UNKNOWN');
});
