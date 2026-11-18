import {useState} from 'react';
import {useSearchParams} from 'react-router';
import type {Dict} from '../api/contracts';
import {Button,Dialog} from '../components/Controls';
import {SavedWorkViews} from './SavedWorkViews';
import {ExportCsvButton} from './ExportCsvButton';
import {dailyWorkSummary,dailyWorkRange} from './dailyWorkModel';
import {qty,unit} from './planModel';
import {demandKindLabel,type ExportField} from './reportExport';
import type {ExceptionScope,ExceptionFilters} from './exceptionsModel';
const numeric=[['quantityMin','需求至少'],['plannedMin','计划满足至少'],['plannedMax','计划满足至多'],['shortMin','未排至少']] as const;
const labels=[['demand_id','需求行'],['order_ref','订单引用'],['item_id','产品'],['location_id','地点'],['uom','单位'],['kind','需求类型'],['requested_quantity','需求数量'],['planned_quantity','计划满足'],['unfulfilled_quantity','未排数量'],['due_at','要求日期'],['planned_delivery_at','已排部分完成日期'],['status','规划状态'],['constraint_status','约束证据状态']] as const;
export function ExceptionWorkControls({scope,data,rows,filters,change,columns,onColumns}:{scope:ExceptionScope;data:Dict;rows:Dict[];filters:ExceptionFilters;change:(field:string,value:string)=>void;columns:{id:string;title:string}[];onColumns:(value:string)=>void}){
 const[search,setSearch]=useSearchParams(),[panel,setPanel]=useState<'filter'|'view'|null>(null),[anchor,setAnchor]=useState(()=>String(data.planning_at??'').slice(0,10));
 const bound={planId:scope.planId,versionId:scope.versionId,runId:scope.runId,target:scope.target};
 const fields:ExportField[]=labels.map(([key,label])=>({key,label,meaning:key==='planned_delivery_at'?'仅已排部分；未排数量没有认证完成日期':key==='planned_quantity'?'当前原生运行的计划满足，非实际交付':'同一运行的原始字段，未知保留空值'}));
 const exportInput={scope:bound,data,status:rows.length?'PRESENT':'EMPTY_CHECKED',kind:'exceptions',rows,fields,filters:{...filters,columns:search.get('exception_columns')??''},sourceUrl:'/v1/enterprise-plans/'+scope.planId+'/exceptions?'+new URLSearchParams({data_version_id:scope.versionId,run_id:scope.runId,target:scope.target})};
 function range(grain:'day'|'week'){if(!anchor)return;const p=dailyWorkRange(anchor,grain,data.timezone);setSearch(previous=>{const n=new URLSearchParams(previous);n.set('exception_from',p.from);n.set('exception_to',p.to);n.delete('exception_row');return n})}
 const active=[filters.item,filters.location,filters.kind,filters.uom,...numeric.map(([k])=>filters[k])].filter(Boolean).length;
 return <div className="daily-work-controls">
  <div className="daily-work-summary" aria-label="当前筛选需求合计">{dailyWorkSummary(rows).map(g=><span key={JSON.stringify([g.kind,g.uom])}><strong>{demandKindLabel(g.kind)}</strong> · {g.lines} 行 · 需求 <b>{qty(g.requested)}</b> · 计划满足 <b>{qty(g.planned)}</b> · 未排 <b className={g.unfulfilled?'risk-text':''}>{qty(g.unfulfilled)}</b> {unit(g.uom)}{g.unknown>0&&<small>已知数量小计；另有 {g.unknown} 行证据不完整</small>}</span>)}</div>
  <div className="daily-work-toolbar"><label>作业日期<input aria-label="作业观察日期" type="date" value={anchor} onChange={e=>setAnchor(e.target.value)}/></label><Button onClick={()=>range('day')}>查看当日</Button><Button onClick={()=>range('week')}>查看当周</Button><Button onClick={()=>setSearch(previous=>{const n=new URLSearchParams(previous);n.delete('exception_from');n.delete('exception_to');n.delete('exception_row');return n})}>全部期间</Button><Button icon="filter" onClick={()=>setPanel('filter')}>产品与数量{active?' · '+active:''}</Button><Button icon="table" onClick={()=>setPanel('view')}>列与个人视图</Button><Button onClick={()=>setSearch(previous=>{const n=new URLSearchParams(previous);n.set('analysis','reports');n.set('report','demand');n.set('grain','week');return n})}>日周需求与供应报表</Button></div>
  {(filters.item||filters.location||filters.kind||filters.uom)&&<div className="daily-work-applied" aria-label="已应用筛选">{(['item','location','kind','uom'] as const).filter(k=>filters[k]).map(k=><Button key={k} onClick={()=>change(k,'')}>{filters[k]} ×</Button>)}</div>}
  <ExportCsvButton input={exportInput}/>
  {panel==='filter'&&<Dialog title="产品、地点与数量" onClose={()=>setPanel(null)}><div className="daily-work-filter-fields">{([['item','产品','item_id'],['location','地点','location_id'],['kind','需求类别','kind'],['uom','单位','uom']] as const).map(([key,label,field])=><label key={key}>{label}<select aria-label={'日常清单'+label} value={filters[key]??''} onChange={e=>change(key,e.target.value)}><option value="">全部{label}</option>{[...new Set<string>(data.demands.map((r:Dict)=>r[field]).filter(Boolean))].map(v=><option key={v}>{v}</option>)}</select></label>)}{numeric.map(([key,label])=><label key={key}>{label}<input aria-label={label} type="number" min="0" step="any" value={filters[key]??''} onChange={e=>change(key,e.target.value)}/></label>)}</div><p>数量按每行原单位筛选；合计按单位和需求类别分别呈现。未排不代表已经确认延期。</p><Button onClick={()=>setPanel(null)}>完成</Button></Dialog>}
  {panel==='view'&&<Dialog title="列与个人工作视图" onClose={()=>setPanel(null)}><fieldset><legend>显示列（需求身份始终保留）</legend>{columns.map(c=><label key={c.id}><input type="checkbox" disabled={c.id==='demand'} checked={c.id==='demand'||!search.get('exception_columns')||search.get('exception_columns')!.split(',').includes(c.id)} onChange={e=>{const ids=search.get('exception_columns')?.split(',')??columns.map(x=>x.id);onColumns([...new Set(e.target.checked?[...ids,c.id]:ids.filter(id=>id!==c.id))].join(','))}}/>{c.title}</label>)}</fieldset><SavedWorkViews scope={bound} search={search} onRestore={setSearch}/><Button onClick={()=>setPanel(null)}>完成</Button></Dialog>}
 </div>;
}
