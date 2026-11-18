import {describe,it,expect} from 'vitest';
import {ganttAppearance,ganttLegend,ganttStatusText} from './ganttSemantics';
import {chartDefaults} from '../design/chartSemantics';
import {taskInterval} from '../planning/ganttProjection';
import {buildWorkspaceView} from '../planning/planModel';
import type {GanttInterval} from './ganttModel';
const interval:GanttInterval={id:'R:OP',taskId:'R:OP',rowId:'M1',start:10,end:20,label:'CUT',details:[],nature:'planned'};
describe('Gantt independent visual dimensions',()=>{
 it('keeps formal fill through protection, native conflict and order delay without changing time or assignment',()=>{
  const x={...interval,protected:true,feasibility:'CONFLICT' as const,orderDelaySeconds:120},before=JSON.stringify(x),a=ganttAppearance(x);
  expect(a.fill).toBe(chartDefaults.planned);expect(a.protected).toBe(true);expect(a.risk).toBe('原生约束冲突');expect(a.riskColor).toBe(chartDefaults.danger);
  expect(ganttStatusText(x)).toContain('受保护');expect(JSON.stringify(x)).toBe(before);
 });
 it('never promotes an existing completed source status into an actual executed segment',()=>{
  const x=taskInterval({...interval,origin:'EXISTING',sourceStatus:'completed',start_at:'2026-11-01T08:00:00Z',end_at:'2026-11-01T09:00:00Z',quantity:'20',uom:'piece'} as any,{},'M1');
  expect(x.nature).toBe('planned');expect(x.protected).toBe(true);expect(ganttLegend([x],[],0,100).map(e=>e.key)).not.toContain('actual');
 });
 it('shows only supported legend states and keeps calendar absence distinct from unavailable time',()=>{
  expect(ganttLegend([],[],0,100)).toEqual([]);
  const x={...interval,nature:'proposed' as const,comparison:'candidate' as const};
  const legend=ganttLegend([x],[{id:'M1',label:'M1',calendar:null}],0,100);
  expect(legend.map(e=>e.key)).toEqual(['proposed','candidate']);expect(legend.some(e=>e.key==='calendar')).toBe(false);
  expect(ganttLegend([x],[{id:'M1',label:'M1',calendar:[]}],0,100).map(e=>e.key)).toContain('calendar');
  expect(ganttAppearance({...interval,nature:undefined}).fill).toBe(chartDefaults.unavailable);
 });
 it('uses explicit result collections when origin is absent, without mutating original protection or input contracts',()=>{
  const source={plan_id:'P',data_version_id:'D',snapshot_sha256:'S'},o={id:'OP',manufacture_id:'M',start_at:'2026-11-01T08:00:00Z',end_at:'2026-11-01T09:00:00Z'},v:any={context:{enterprise_source:source},result:{operations:[o],manufacture_suggestions:[{id:'M',uom:'piece'}]}};
  const snapshot:any={data_version:{id:'D'},plan_input:{resources:[],calendars:[]}},before=JSON.stringify(v),t=buildWorkspaceView(v,v,snapshot).tasks[0],x=taskInterval(t,{},'M1');
  expect(t.origin).toBeUndefined();expect(t.presentationNature).toBe('proposed');expect(x.nature).toBe('proposed');expect(x.protected).toBe(false);expect(JSON.stringify(v)).toBe(before);
 });
});
