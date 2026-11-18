import type {ResourceCalendarRow,CalendarSegment} from './resourceCalendarModel';
import {layoutTaskBars} from './presentationModel';
import {formatSiteTime} from './timeRange';
import {qty} from './planModel';
import './resource-calendar.css';
export type ResourceCalendarProps={rows:ResourceCalendarRow[];start:string;end:string;timezone:string;selectedEvent?:string;selectedWindow?:string;onResource:(id:string)=>void;onEvent:(id:string)=>void;onWindow:(segment:CalendarSegment)=>void};
const calendarMessages:Record<string,string>={NOT_COLLECTED:'日历未采集；未推定工作或非工作时段',UNREADABLE:'日历来源不可读取',INVALID_EVIDENCE:'日历来源或区间无效，停止绘制',UNSUPPORTED:'当前日历未认证此分析'};
const loadMessages:Record<string,string>={EMPTY_CHECKED:'已查询，无对应原生占用',NOT_COLLECTED:'负荷未采集，不能当作空闲',INVALID_EVIDENCE:'负荷证据不一致',UNREADABLE:'负荷来源不可读取'};

export function ResourceCalendar({rows,start,end,timezone,selectedEvent,selectedWindow,onResource,onEvent,onWindow}:ResourceCalendarProps){
 const a=Date.parse(start),b=Date.parse(end),time=(s:string)=>formatSiteTime(s,timezone),width=800;
 if(!Number.isFinite(a)||!Number.isFinite(b)||b<=a)return null;
 const guides=()=>Array.from({length:5},(_,i)=><span key={i} className="calendar-guide" style={{left:i*25+'%'}}/>);
 const selected=rows.flatMap(row=>row.windows.segments).find(s=>s.id===selectedWindow);
 return <section className="resource-calendar" aria-label="资源与日历检查">
  <header><h3>资源与日历检查</h3><span>输入声明日历 · 原生实际占用</span></header>
  <details className="calendar-reading-note"><summary>日历与占用含义</summary><p className="analysis-note">按当前时间范围查看工作与非工作时段。日历来自本运行的输入声明；占用来自原生负荷记录，两种来源分别保留。</p></details>
  <div className="calendar-legend"><span><i className="calendar-work-key"/>声明工作时段</span><span><i className="calendar-closed-key"/>声明非工作时段</span><span><i className="calendar-load-key"/>原生实际占用</span><span>同刻先释放再占用</span></div>
  <div className="resource-calendar-scroll" tabIndex={0} aria-label="资源日历和原生占用，可横向滚动"><div className="resource-calendar-canvas">
   <div className="calendar-axis"><strong>设备 / 具名人员</strong><div>{Array.from({length:5},(_,i)=><span key={i} style={{left:i*25+'%'}}>{time(new Date(a+(b-a)*i/4).toISOString()).slice(5,16)}</span>)}</div></div>
   {rows.map(({resource:r,windows,loads,validationStatus})=>{
    const packed=layoutTaskBars(loads as {id:string;start_at:string;end_at:string}[],a,b,width),height=Math.max(45,packed.lanes*32+10);
    return <div className="calendar-resource" key={r.id} data-calendar-resource={r.id}>
     <button type="button" className="calendar-resource-name" onClick={()=>onResource(r.id)}><strong>{r.id}</strong><span>{r.role==='OPERATOR'?'具名操作员':'设备'}{r.pool_id?' · 所属 '+r.pool_id:''}</span><span>并行容量 {qty(r.capacity)}</span><small>{r.calendar_id??'日历身份未提供'}</small></button>
     <div className="calendar-tracks">
      <div className="calendar-track declared-calendar">{guides()}{windows.segments.map(s=><button type="button" key={s.id} data-calendar-kind={s.kind} className={'calendar-window '+(s.kind==='WORKING'?'calendar-working':'calendar-nonworking')+(selectedWindow===s.id?' is-selected':'')} style={{left:(Date.parse(s.start_at)-a)/(b-a)*100+'%',width:(Date.parse(s.end_at)-Date.parse(s.start_at))/(b-a)*100+'%'}} onClick={()=>onWindow(s)} aria-pressed={selectedWindow===s.id} aria-label={`${r.id} ${s.kind==='WORKING'?'声明工作':'声明非工作'} ${time(s.start_at)} 至 ${time(s.end_at)}`} title={(s.kind==='WORKING'?'声明工作':'声明非工作')+' '+time(s.start_at)+' → '+time(s.end_at)}><span>{s.kind==='WORKING'?'工作':'非工作'}</span></button>)}{!windows.segments.length&&<span className="calendar-lane-note">{calendarMessages[windows.status]??'日历证据状态未知'}</span>}</div>
      <div className="calendar-track native-calendar-loads" style={{height}}>{guides()}{packed.bars.map(({task,lane,left,hitWidth,barLeft,barWidth,showLabel})=>{
       const load=task as Record<string,any>;
       return <button type="button" key={load.id} className={'calendar-load-hit '+(selectedEvent===load.id?'is-selected':'')} style={{left:left/width*100+'%',width:hitWidth/width*100+'%',top:5+lane*32}} aria-pressed={selectedEvent===load.id} aria-label={`${r.id} 原生占用 ${load.operation_id} ${time(load.start_at)} 至 ${time(load.end_at)} 并行负荷 ${qty(load.load_quantity)}`} onClick={()=>onEvent(load.id)} title={load.operation_id+' · '+time(load.start_at)+' → '+time(load.end_at)+' · 负荷 '+qty(load.load_quantity)}><span style={{left:barLeft/hitWidth*100+'%',width:Math.max(1,barWidth)/hitWidth*100+'%'}}>{showLabel?load.operation_id+' · '+qty(load.load_quantity):''}</span></button>
      })}{!loads.length&&<span className="calendar-lane-note">{loadMessages[r.event_status]??(r.event_status==='PRESENT'?'当前筛选无匹配原生占用':'负荷状态未知')}</span>}{validationStatus==='FAIL'&&<strong className="calendar-fail">已知业务违规 FAIL</strong>}</div>
     </div>
     <details className="calendar-source"><summary>{r.id} 日历与容量来源</summary><p>日历为企业输入声明，未据此声称原生日历已完整读回。负荷明细保留独立原生来源。</p><pre className="raw-json">{JSON.stringify({calendar_id:r.calendar_id,calendar_status:windows.status,calendar_basis:r.calendar_basis,calendar_sources:r.calendar_sources,capacity:r.capacity,capacity_uom:r.capacity_uom,resource_sources:r.sources},null,2)}</pre></details>
    </div>;
   })}
   {!rows.length&&<p className="analysis-note">当前筛选没有已认证的连续设备或具名人员。数量窗口与人员池不显示为可用工时。</p>}
  </div></div>
  {selected&&<div className="calendar-selection" aria-live="polite"><strong>{selected.resource_id} · {selected.kind==='WORKING'?'声明工作时段':'声明非工作时段'}</strong><span>{time(selected.start_at)} → {time(selected.end_at)}</span><span>已定位此资源；模拟动作仍按原认证范围与工作时段检查。</span></div>}
 </section>;
}
