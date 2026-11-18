import {useLayoutEffect,useMemo,useRef,useState} from 'react';
import {useSearchParams} from 'react-router';
import type {GridScope,ProfessionalColumn} from '../components/ProfessionalGrid';
import {ProfessionalGrid} from '../components/ProfessionalGrid';
import {Button,Tabs} from '../components/Controls';
import {PlanningChart} from '../components/PlanningChart';
import {buildTimeBucketRows,readReportCell,reportCellId,reportValue,resolveReportSelection,reportSelectionSearch,timeBucketColumnWidths,timeBucketCellLabel,type ReportSelection,type TimeBucketReportData} from './timeBucketReportModel';
import {timeBucketChartOption} from './timeBucketCharts';
import type {ExportInput} from './reportExport';
import {ExportCsvButton} from './ExportCsvButton';
import {bucketExportInput} from './timeBucketExport';
import './time-bucket-report.css';

type Props={report:TimeBucketReportData;scope:GridScope;label:string;onInvestigate:(selection:ReportSelection,origin:URLSearchParams)=>void;exportInput?:ExportInput;objectQuery?:string;onObjectQuery?:(value:string)=>void;showObjectFilter?:boolean};
export function TimeBucketReport({report,scope,label,onInvestigate,exportInput,objectQuery,onObjectQuery,showObjectFilter=true}:Props){
 const container=useRef<HTMLElement>(null),[containerWidth,setContainerWidth]=useState<number>();
 useLayoutEffect(()=>{
  const element=container.current;if(!element)return;
  const measure=()=>{const width=element.getBoundingClientRect().width;if(width>0)setContainerWidth(width)};
  measure();
  if(typeof ResizeObserver==='undefined'){window.addEventListener('resize',measure);return()=>window.removeEventListener('resize',measure)}
  const observer=new ResizeObserver(entries=>{const width=entries[0]?.contentRect.width;if(width>0)setContainerWidth(width)});
  observer.observe(element);return()=>observer.disconnect();
 },[]);
 const [search,setSearch]=useSearchParams(),mode=search.get('bucket_view')??'graph',query=showObjectFilter?(objectQuery??''):'',page=Number(search.get('bucket_page')??0),allMetrics=search.get('bucket_metrics')==='all';
 const currentSearch=useRef(search);currentSearch.current=search;
 let selection:ReportSelection|null=null;try{selection=JSON.parse(search.get('bucket_selection')??'null')}catch{/* An invalid URL is not a business selection. */}
 // iX emits tabChange when a controlled tab is changed programmatically too.
 // Preserve the same pending URL instead of overwriting it from a stale tab callback.
 function updateSearch(update:(next:URLSearchParams)=>void){const next=new URLSearchParams(currentSearch.current);update(next);currentSearch.current=next;setSearch(next)}
 function param(key:string,value:string){if((currentSearch.current.get(key)??'')===value)return;updateSearch(next=>{value?next.set(key,value):next.delete(key)})}
 const setMode=(value:string)=>param('bucket_view',value),setPage=(value:number)=>param('bucket_page',String(value));
 const setSelection=(value:ReportSelection|null)=>param('bucket_selection',value?JSON.stringify(value):'');
 const toggleMetrics=()=>updateSearch(next=>{next.set('bucket_view','numbers');mode==='numbers'&&allMetrics?next.delete('bucket_metrics'):next.set('bucket_metrics','all')});
 const setQuery=(value:string)=>onObjectQuery?.(value);
 const selected=resolveReportSelection(selection,report),filtered=report.objects.filter(o=>(o.label+' '+o.unit).toLowerCase().includes(query.toLowerCase()));
 const pageCount=Math.max(1,Math.ceil(filtered.length/6)),currentPage=Number.isInteger(page)&&page>=0?Math.min(page,pageCount-1):0,visible=filtered.slice(currentPage*6,currentPage*6+6);
 const metrics=allMetrics?report.metrics:report.metrics.filter(m=>m.default),view={...report,objects:visible,metrics};
 const reduced=typeof matchMedia!=='undefined'&&matchMedia('(prefers-reduced-motion: reduce)').matches;
 const chartKey=JSON.stringify([report.scopeKey,metrics,visible.map(o=>[o.key,o.cells,o.states])]);
 const option=useMemo(()=>timeBucketChartOption({...report,metrics:report.metrics.filter(m=>m.default||allMetrics)},visible.map(o=>o.key),reduced),[chartKey,reduced]);
 function pick(id:string){try{const [objectKey,bucketId,metricId]=JSON.parse(id);const next=resolveReportSelection({scopeKey:report.scopeKey,objectKey,bucketId,metricId},report);if(next)onInvestigate(next,reportSelectionSearch(search,next))}catch{/* Non-report chart events cannot become business selection. */}}
 const rows=buildTimeBucketRows(view,metrics),object=selected?report.objects.find(o=>o.key===selected.objectKey):null,metric=report.metrics.find(m=>m.id===selected?.metricId),bucket=report.buckets.find(b=>b.id===selected?.bucketId);
 const widths=timeBucketColumnWidths(containerWidth);
 const columns:ProfessionalColumn<typeof rows[number]>[]=[{id:'object',title:'业务对象 / 单位',pinned:true,width:widths.object,minWidth:widths.object,value:r=><span title={r.object.label+' · '+r.object.unit} aria-label={r.object.label+' · '+r.object.unit} className={r.metric.id===metrics[0]?.id?undefined:'sr-only'}><strong>{r.object.label}</strong><small> {r.object.unit}</small></span>},{id:'metric',title:'指标',pinned:true,width:widths.metric,minWidth:widths.metric,value:r=><span title={r.metric.label+(r.metric.unit?'（'+r.metric.unit+'）':'')+' · '+(r.metric.kind==='flow'?'期间流量':r.metric.kind==='balance'?'时点余额，不跨期相加':'当前运行快照，不跨期相加')}>{r.metric.label}{r.metric.unit?'（'+r.metric.unit+'）':''}</span>},...report.buckets.map(b=>({id:b.id,title:b.label+(b.partial?' *':''),numeric:true,width:widths.bucket,minWidth:90,value:(r:typeof rows[number])=><button className="bucket-value" aria-label={timeBucketCellLabel(r.object,b,r.metric,r.values[b.id])} title={timeBucketCellLabel(r.object,b,r.metric,r.values[b.id])} aria-pressed={selected?.objectKey===r.object.key&&selected.bucketId===b.id&&selected.metricId===r.metric.id} onClick={()=>pick(reportCellId(r.object.key,b.id,r.metric.id))}>{r.object.states?.[b.id]==='NOT_YET_KNOWN'?'当时未知':reportValue(r.values[b.id])}</button>}))];
 return <section ref={container} className="time-bucket-report" aria-label={label}>
  <div className="bucket-toolbar"><Tabs label={label+'展示方式'} value={mode} tabs={[["graph","图形"],["numbers","数值"]]} onChange={setMode}/>{showObjectFilter&&<label>对象筛选<input aria-label={label+'对象筛选'} value={query} placeholder="产品、物料、地点或客户" onChange={e=>setQuery(e.target.value)}/></label>}<Button className="quiet" aria-pressed={mode==='numbers'&&allMetrics} onClick={toggleMetrics}>{mode==='numbers'&&allMetrics?'主要指标':'全部指标'}</Button>{exportInput&&<ExportCsvButton input={bucketExportInput(exportInput,{...report,objects:filtered,metrics})}/>}<span>{filtered.length} 个对象 · {report.buckets.length} 个期间</span></div>
  {!visible.length?<p role="status">当前筛选没有匹配的业务对象。</p>:mode==='numbers'?<ProfessionalGrid scope={scope} label={label+'数值'} rows={rows} columns={columns} rowId={r=>r.id} tools={false} height={Math.min(620,90+rows.length*42)}/>:<><div className="bucket-chart-scroll"><div className="bucket-multi-chart" style={{height:116*visible.length+120}}><PlanningChart key={report.scopeKey} identityKey={report.scopeKey} label={label+'多对象期间图'} option={option} controls={false} compact onPick={pick} selectedEventId={selected?reportCellId(selected.objectKey,selected.bucketId,selected.metricId):undefined}/></div></div><small className="bucket-chart-note">各行量程独立；流出柱向下。颜色对应同一业务指标，缺失值留空。切换数值可用键盘逐格调查。</small></>}
  {pageCount>1&&<div className="bucket-pagination"><Button disabled={currentPage===0} onClick={()=>setPage(currentPage-1)}>上一组对象</Button><span>{currentPage+1} / {pageCount}</span><Button disabled={currentPage+1>=pageCount} onClick={()=>setPage(currentPage+1)}>下一组对象</Button></div>}
  {selected&&object&&bucket&&<div className="bucket-selection" aria-live="polite"><div><strong>{object.label}</strong><p>{bucket.label} · {metric?.label} <strong>{reportValue(readReportCell(object,bucket.id,selected.metricId))} {metric?.unit??object.unit}</strong></p><small>{bucket.start} — {bucket.end}（不含结束）{metric?.kind!=='flow'?' · 时点/快照，不跨期间相加':''}</small></div><Button onClick={()=>onInvestigate(selected,reportSelectionSearch(search,selected))}>调查组成记录</Button><Button className="quiet" onClick={()=>setSelection(null)}>取消选择</Button></div>}
 </section>;
}
