import {chartColors} from '../design/chartSemantics';
import {calendarGaps,type GanttInterval,type GanttRow} from './ganttModel';
export const ganttNatureLabels={planned:'正式工单计划',proposed:'制造建议 · 未执行',actual:'实际执行',unknown:'任务性质未核实'};
/** Protection and risk never replace the task's main fill. No status infers actual execution. */
export function ganttAppearance(x:GanttInterval){
 const c=chartColors(),nature=x.nature??'unknown';
 return {fill:nature==='unknown'?c.unavailable:c[nature],label:ganttNatureLabels[nature],outline:x.protected?'#243746':c.baseline,risk:x.feasibility==='CONFLICT'?'原生约束冲突':(x.orderDelaySeconds??0)>0?'关联订单延期':null,riskColor:c.danger,candidate:x.comparison==='candidate',baseline:x.comparison==='baseline',protected:x.protected===true};
}
export type GanttLegendEntry={key:string;label:string;fill?:string;shape?:'outline'|'double'|'lock'|'risk'|'calendar'|'short'};
export function ganttLegend(intervals:GanttInterval[],rows:GanttRow[],start:number,end:number,blocked=false):GanttLegendEntry[]{
 const colors=chartColors(),entries:GanttLegendEntry[]=[];
 for(const nature of ['planned','proposed','actual','unknown'] as const)if(intervals.some(x=>(x.nature??'unknown')===nature))entries.push({key:nature,label:ganttNatureLabels[nature],fill:nature==='unknown'?colors.unavailable:colors[nature]});
 if(intervals.some(x=>x.comparison==='baseline'))entries.push({key:'baseline',label:'原始基线 · 实边',shape:'outline'});
 if(intervals.some(x=>x.comparison==='candidate'))entries.push({key:'candidate',label:'候选方案 · 双线',shape:'double'});
 if(intervals.some(x=>x.protected))entries.push({key:'protected',label:'锁 · 受保护',shape:'lock'});
 if(intervals.some(x=>x.feasibility==='CONFLICT'||(x.orderDelaySeconds??0)>0))entries.push({key:'risk',label:'! · 冲突 / 订单延期',shape:'risk',fill:colors.danger});
 if(blocked||rows.some(r=>(calendarGaps(r.calendar,start,end)?.length??0)>0))entries.push({key:'calendar',label:'非工作 / 不可用',shape:'calendar'});
 return entries;
}
export function ganttStatusText(x:GanttInterval){const a=ganttAppearance(x);return [a.label,x.comparison==='candidate'?'候选方案':x.comparison==='baseline'?'原始基线':null,x.protected?'受保护 · 不可直接调整':null,a.risk].filter(Boolean).join(' · ')}
