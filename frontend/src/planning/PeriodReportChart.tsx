import {useMemo} from 'react';
import type {Dict} from '../api/contracts';
import {PlanningChart} from '../components/PlanningChart';
import {Select} from '../components/Controls';
import {periodChartOption} from './periodChartModel';
import {unit} from './planModel';
import type {ReportScope} from './reportModel';

export function PeriodReportChart({mode,rows,scope,timezone,group,onGroup,selected,onPick}:{mode:'resource'|'inventory';rows:Dict[];scope:ReportScope;timezone:string;group:string;onGroup:(value:string)=>void;selected?:string;onPick:(value:string)=>void}){
 const key=(r:Dict)=>mode==='resource'?r.resource_id:JSON.stringify([r.item_id,r.location_id,r.uom]);
 const groups=[...new Map(rows.map(r=>[key(r),r])).entries()];
 const chosen=groups.some(([id])=>id===group)?group:groups[0]?.[0];
 const visible=rows.filter(r=>key(r)===chosen).sort((a,b)=>a.start_at.localeCompare(b.start_at));
 const option=useMemo(()=>periodChartOption(visible,mode,timezone),[visible,mode,timezone]);
 if(!visible.length)return null;
 return <section className="period-report-chart" aria-label={mode==='resource'?'期间资源负荷图':'期间库存收支图'}>
  <Select label={mode==='resource'?'图表资源':'图表物料与地点'} value={chosen} onChange={onGroup} options={groups.map(([value,r])=>({value,label:mode==='resource'?r.resource_id+' · '+(r.role==='OPERATOR'?'具名人员':'设备'):r.item_id+' · '+r.location_id+' · '+unit(r.uom)}))}/>
  <PlanningChart compact identityKey={JSON.stringify([scope,mode,chosen,visible.map(r=>r.start_at+'|'+r.end_at)])} label={mode==='resource'?'原生负荷与日历可用容量':'原生库存流入、流出与期末余额'} option={option} selectedEventId={selected} onPick={onPick}/>
 </section>;
}
