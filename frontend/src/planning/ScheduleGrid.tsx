import {useMemo,useEffect,useState} from 'react';
import {Input} from 'antd';
import {Button,Dialog,Icon} from '../components/Controls';
import {PlanningGantt} from '../components/PlanningGantt';
import {type TaskRecord,type WorkspaceModel,qty,unit} from './planModel';
import {addDays,formatSiteTime,visibleDays,siteDate} from './timeRange';
import {taskDaySpan} from './presentationModel';
import {scheduleProjection} from './ganttProjection';
import {orderScheduleProjection} from './ganttProjection';
import {businessLabel,runLabel} from './planModel';
import type {GanttEditRequest} from '../components/ganttModel';
export function ScheduleGrid({model,tasks,selected,onSelect,query,setQuery,startDate,setStartDate,days,setDays,fit,onResource,onComposite,scopeKey,onEdit}:{model:WorkspaceModel;tasks:TaskRecord[];selected:string|null;onSelect:(id:string)=>void;query:string;setQuery:(v:string)=>void;startDate:string;setStartDate:(v:string)=>void;days:number;setDays:(n:number)=>void;fit:()=>void;onResource?:(id:string)=>void;onComposite?:()=>void;scopeKey?:string;onEdit?:(r:GanttEditRequest)=>void}){
 const [resourceList,setResourceList]=useState<string|null>(null),tz=model.view.context!.timezone;
 const windows=useMemo(()=>visibleDays(startDate,days,tz),[startDate,days,tz]);
 const[dimension,setDimension]=useState('resources');
 const projection=useMemo(()=>dimension==='orders'?orderScheduleProjection(model,tasks):scheduleProjection(model,tasks,query),[model,tasks,query,dimension]);
 const deadlines=useMemo(()=>(model.view.result?.deliveries??[]).map(d=>Date.parse(d.due_at)),[model.view.result]);
 useEffect(()=>setResourceList(null),[model.view.run?.solve_run_id]);
 function zoom(next:number){const task=tasks.find(t=>t.id===selected);if(task)setStartDate(siteDate(task.start_at,tz));setDays(next)}
 function focusTask(task:TaskRecord){const span=taskDaySpan(task,tz);onSelect(task.id);setStartDate(span.start);setDays(span.days);setResourceList(null)}
 return <section className="chart-panel" aria-label="资源排程"><div className="toolbar"><h2>{dimension==="orders"?"订单与时间":"资源与时间"}</h2><select aria-label="甘特展示维度" value={dimension} onChange={e=>setDimension(e.target.value)}><option value="resources">资源维度</option><option value="orders">订单维度</option></select>{onComposite&&<Button onClick={onComposite}>工序与资源复合甘特</Button>}<Button icon="left" className="icon-button" aria-label="上一时段" onClick={()=>setStartDate(addDays(startDate,-days))}>上一时段</Button><Button icon="right" className="icon-button" aria-label="下一时段" onClick={()=>setStartDate(addDays(startDate,days))}>下一时段</Button><Button icon="fit" onClick={fit}>适合全部任务</Button><select aria-label="时间窗口长度" value={days} onChange={e=>zoom(Number(e.target.value))}>{[...new Set([days,1,3,7,14])].sort((a,b)=>a-b).map(n=><option value={n} key={n}>{n===1?'1 天 · 小时刻度':n+' 天'}</option>)}</select><Button disabled={days<=1} onClick={()=>zoom(days>7?7:days>3?3:1)} aria-label="放大时间轴">放大</Button><span className="view-range" data-start={startDate} data-end={windows.at(-1)!.date}>查看 <strong>{startDate.replaceAll('-','/')} — {windows.at(-1)!.date.slice(5).replace('-','/')}</strong></span><label className="search-field"><Icon name="search"/><Input variant="borderless" id="filter" aria-label="筛选排程与制造明细" value={query} onChange={e=>setQuery(e.target.value)} placeholder="资源 / 工序 / 产品" type="search"/></label><Button className="icon-button" icon="close" aria-label="清除筛选" disabled={!query} onClick={()=>setQuery('')}>清除筛选</Button></div>
 <PlanningGantt identityKey={JSON.stringify([scopeKey,model.view.context?.enterprise_source,model.view.run?.solve_run_id])} label={dimension==="orders"?"订单排程时间网格":"资源排程时间网格"} {...projection} start={windows[0].start} end={windows.at(-1)!.end} timezone={tz} selectedTask={selected} deadlines={deadlines} onEdit={onEdit} onSelect={x=>onSelect(x.taskId)} onFocus={x=>{const t=tasks.find(t=>t.id===x.taskId);if(t)focusTask(t)}} onResource={dimension==="resources"?id=>{if(id!=="availability-transfer")setResourceList(id)}:undefined}/>
 <div className="chart-caption"><span>{runLabel(model.view.run?.state)} · {businessLabel(model.view.interpretation?.business_status)}。条形来自当前运行；灰色为声明的非工作时段。已有工单保持保护。</span></div>
 {resourceList&&<Dialog title={'资源任务 · '+resourceList} onClose={()=>setResourceList(null)}><p>选择任务，在时间轴和对象详情中定位。</p>{onResource&&<Button icon="search" onClick={()=>{onResource(resourceList);setResourceList(null)}}>调查此资源</Button>}<div className="resource-task-list">{tasks.filter(t=>t.resource_id===resourceList).map(t=><Button key={t.id} onClick={()=>focusTask(t)}><strong>{t.name} · {qty(t.quantity)} {unit(t.uom)}</strong><small>{formatSiteTime(t.start_at,tz)} → {formatSiteTime(t.end_at,tz)}</small></Button>)}</div></Dialog>}
 </section>;
}
