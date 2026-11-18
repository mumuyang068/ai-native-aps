import {useMemo} from 'react';
import type {Dict} from '../api/contracts';
import type {TaskRecord} from './planModel';
import {PlanningGantt} from '../components/PlanningGantt';
import {compositeProjection} from './ganttProjection';
import './composite-schedule.css';
type Props={tasks:TaskRecord[];data:Dict;start:string;end:string;query:string;resourceId:string;selectedTask?:string|null;selectedEvent?:string;onTask:(id:string)=>void;onEvent:(id:string)=>void;onResource:(id:string)=>void};
export function CompositeSchedule(p:Props){
 const a=Date.parse(p.start),b=Date.parse(p.end);
 const projection=useMemo(()=>compositeProjection(p.tasks,p.data,a,b,p.query,p.resourceId),[p.tasks,p.data,a,b,p.query,p.resourceId]);
 return <section className="composite-schedule" aria-label="工序与实际资源复合甘特"><div className="composite-toolbar"><strong>工序与实际资源 · 共享时间标尺</strong></div>
  <PlanningGantt {...projection} identityKey={JSON.stringify(p.data.identity)} label="工序和设备具名人员共享时间网格" start={a} end={b} timezone={p.data.timezone} selectedTask={p.selectedTask} selectedEvent={p.selectedEvent} selectedResource={projection.intervals.find(x=>x.id===p.selectedEvent)?.rowId} onSelect={x=>x.eventId?p.onEvent(x.eventId):p.onTask(x.taskId)} onResource={id=>id.startsWith('operation:')?p.onTask(id.slice(10)):p.onResource(id)}/>
  <p className="analysis-note">工序行显示计划窗口，设备与人员行显示同运行原生计划占用，不代表实际生产执行。同一任务的设备与具名人员同步选中。</p>
 </section>;
}
