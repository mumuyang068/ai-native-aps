import {useMemo} from 'react';
import {Button} from '../components/Controls';
import {PlanningGantt} from '../components/PlanningGantt';
import type {Dict} from '../api/contracts';
import type {TaskRecord} from './planModel';
import {workingViewport} from './routingTimelineModel';
import {routingProjection} from './ganttProjection';
type Props={input:Dict;data:Dict;tasks:TaskRecord[];selectedTask?:string;groupId?:string;resourceId?:string;start:string;end:string;onlyGroup:boolean;condition?:Dict;onTask:(id:string,resource?:string)=>void;onResource:(id:string)=>void;onRange?:(start:string,end:string)=>void};
export function RoutingResourceTimeline(p:Props){
 const projection=useMemo(()=>routingProjection(p.input,p.data,p.tasks,p.start,p.end,p.onlyGroup,p.groupId,p.resourceId),[p.input,p.data,p.tasks,p.start,p.end,p.onlyGroup,p.groupId,p.resourceId]);
 const range=(factor:number,anchor=.5,pan=0)=>{const v=workingViewport(projection.resources.flatMap(r=>r.availability??[]),p.input.planning_at,p.input.horizon.end_at,p.start,p.end,factor,anchor,pan);if(v)p.onRange?.(v.start,v.end)};
 const blocked=useMemo(()=>p.condition?[{rowId:p.condition.resource_id,start:Date.parse(p.condition.start_at),end:Date.parse(p.condition.end_at)}]:[],[p.condition]);
 return <><div className="rc-axis-note"><span>工作时段标尺 · 午休 / 夜间折叠 · {p.input.site.timezone}</span><div><Button aria-label="向前查看工作时段" disabled={!p.onRange} onClick={()=>range(1,.5,-.5)} icon="left"/><Button aria-label="缩小资源时间范围" disabled={!p.onRange} onClick={()=>range(1.5)}>缩小</Button><Button aria-label="放大资源时间范围" disabled={!p.onRange} onClick={()=>range(2/3)}>放大</Button><Button aria-label="向后查看工作时段" disabled={!p.onRange} onClick={()=>range(1,.5,.5)} icon="right"/></div></div>
  {!projection.work.total?<p className="rc-note">此期间没有可核验的声明工作时段，不能解释为零负荷。</p>:<PlanningGantt rows={projection.rows} intervals={projection.intervals} links={projection.links} folded={projection.work.segments} blocked={blocked} identityKey={JSON.stringify(p.data.identity??p.tasks[0]?.id.split(':')[0])} label="设备与具名人员共享时间轴" start={Date.parse(p.start)} end={Date.parse(p.end)} timezone={p.input.site.timezone} selectedTask={p.selectedTask} selectedResource={p.resourceId} onSelect={x=>p.onTask(x.taskId,x.rowId)} onResource={p.onResource} onRange={p.onRange}/>}
  <div className="rc-legend"><span>条形为原生计划占用，不代表实际执行</span><span>选中工序的设备与人员同步</span><span>虚线仅为同父任务工序顺序，不是订单分配</span><span>条长为可见工作时段，完整时刻见选中摘要</span></div>
 </>;
}
