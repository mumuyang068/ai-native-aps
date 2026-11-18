import {useMemo} from 'react';
import {useSearchParams} from 'react-router';
import type {Dict} from '../api/contracts';
import {Button,Select,ErrorNote} from '../components/Controls';
import {PlanningChart} from '../components/PlanningChart';
import type {ReportScope} from './reportModel';
import {reportRowId} from './reportModel';
import {fulfillmentGroups,fulfillmentGroupId,fulfillmentChart,fulfillmentAmount} from './demandFulfillmentModel';
import './demand-fulfillment.css';
type Props={demandKind?:string;onDemandKind?:(kind:string)=>void;onHelp:()=>void;scope:ReportScope;data:Dict;rows:Dict[];group:string;baselineRows:Dict[];comparisonError:string;comparePending:boolean;selected?:string;onGroup:(id:string)=>void;onSelect:(id:string)=>void;compareOverride?:boolean;onCompare?:(value:boolean)=>void;onDetails?:()=>void};
export function DemandFulfillment({demandKind,onDemandKind,onHelp,scope,data,rows,group,baselineRows,comparisonError,comparePending,selected,onGroup,onSelect,compareOverride,onCompare,onDetails}:Props){
 const [search,setSearch]=useSearchParams(),compare=scope.target!=='baseline'&&(compareOverride??search.get('report_compare')==='1'),key=JSON.stringify([scope,data.range,data.grain,compare]);
 const groups=fulfillmentGroups(data.demand.rows??[]),current=groups.find(g=>g.id===group),old=compare?baselineRows:[];
 const chart=useMemo(()=>fulfillmentChart(rows,old,data.timezone),[rows,old,data.timezone]),unknown=rows.some(r=>r.planned_quantity==null||r.overdue_unfulfilled==null);
 return <section className="fulfillment-focus" aria-label="需求履约时间分布">
  <div className="fulfillment-controls">{onDemandKind&&data.demand.lines?.some((l:Dict)=>['FIRM_ORDER','NET_FORECAST'].includes(l.demand_kind))&&<Select label="需求类别" value={demandKind??'all'} onChange={onDemandKind} options={[{value:'FIRM_ORDER',label:'确认需求'},{value:'NET_FORECAST',label:'净预测'},{value:'INQUIRY',label:'询价试算'},{value:'UNCLASSIFIED',label:'未分类需求'},{value:'all',label:'全部需求'}]}/>}<label>物料 / 地点<Select label="履约分析分组" value={group} onChange={onGroup} options={groups.map(g=>({value:g.id,label:g.label}))}/></label>{scope.target!=='baseline'&&<label><input aria-label="叠加原始基线履约" type="checkbox" checked={compare} onChange={e=>onCompare?onCompare(e.target.checked):setSearch(p=>{p.set('report_compare',e.target.checked?'1':'0');return p})}/>叠加同计划原始基线</label>}<Button className="quiet" icon="info" onClick={onHelp}>指标口径</Button></div>
  {comparisonError&&<ErrorNote error={comparisonError}/>} {comparePending&&<p role="status">正在核对原始基线事件…</p>}
  {unknown&&<p className="pending-note">交付事件待核实，部分计划满足量与逾期缺量暂不可判断。</p>}
  {current&&rows.length?<><PlanningChart compact key={key+'|'+group} identityKey={key+'|'+group} label="需求到期、真实计划满足事件与期末逾期未满足" option={chart} selectedEventId={selected} onPick={onSelect}/>{selected&&rows.some(r=>reportRowId(r)===selected)&&<div className="fulfillment-selected" role="status"><strong>选中期间</strong><span>到期 {fulfillmentAmount(rows.find(r=>reportRowId(r)===selected)?.due_quantity,current.row.uom)}</span><span>计划满足 {fulfillmentAmount(rows.find(r=>reportRowId(r)===selected)?.planned_quantity,current.row.uom)}</span><span>逾期未满足 {fulfillmentAmount(rows.find(r=>reportRowId(r)===selected)?.overdue_unfulfilled,current.row.uom)}</span></div>}</>:<p>当前筛选没有可核验的履约期间。请清除筛选或选择其他分组。</p>}
  <Button icon="table" className="quiet fulfillment-details-link" onClick={onDetails??(()=>document.querySelector('.report-table')?.scrollIntoView({block:'nearest'}))}>查看需求明细</Button>
 </section>;
}
