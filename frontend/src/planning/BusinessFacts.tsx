import {useEffect,useMemo,useRef,useState} from 'react';
import {Link,useParams,useSearchParams} from 'react-router';
import * as echarts from 'echarts/core';
import {HeatmapChart} from 'echarts/charts';
import {CalendarComponent,VisualMapComponent} from 'echarts/components';
import type {Dict} from '../api/contracts';
import {request} from '../api/client';
import {Button,Definition,Dialog,ErrorNote,Loading,Tabs} from '../components/Controls';
import {DataTable,type TableColumn} from '../components/DataTable';
import {PlanningChart} from '../components/PlanningChart';
import {formatSiteTime} from './timeRange';
import {PlannerHomeOverview,type HomePlanContext} from './PlannerHomeOverview';
import {chartColors} from '../design/chartSemantics';
import {businessViewUrl,verifyBusinessView,businessCalendarWindow,businessDetailQuery,type BusinessScope} from './businessViewModel';
import './business-facts.css';
echarts.use([HeatmapChart,CalendarComponent,VisualMapComponent]);
const amount=(v:unknown,u:unknown='')=>v==null?'未知':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:3})+(u?' '+u:'');
const escape=(v:unknown)=>echarts.format.encodeHTML(String(v));
const tip=(lines:unknown[])=>lines.map(escape).join('<br/>');
const orderColumns:TableColumn<Dict>[]=[{id:'reference',title:'订单 / 产品地点',value:r=><>{r.order_ref}<small>{r.item_id} · {r.location_id}</small></>},{id:'ordered',title:'原数量',value:r=>amount(r.ordered,r.uom)},{id:'shipped',title:'来源已发货',value:r=>amount(r.shipped,r.uom)},{id:'outstanding',title:'尚未发货',value:r=>amount(r.outstanding,r.uom)},{id:'due',title:'要求日期',value:r=>r.date??'未知'}];
export function BusinessKindTabs({kinds,value,onChange,compact=false}:{kinds:string[];value:string;onChange:(kind:string)=>void;compact?:boolean}){
 const labels:Record<string,string>={FIRM_ORDER:'确认需求',NET_FORECAST:'净预测',UNKNOWN:'未分类'};
 return kinds.length>0&&(!compact||kinds.length>1||kinds[0]!==value)?<Tabs label="需求类别" value={value} tabs={kinds.map(k=>[k,labels[k]??k])} onChange={onChange}/>:null;
}
export function BusinessFacts({scope,focus,home}:{scope:BusinessScope;focus?:'orders'|'purchases'|'inventory'|'manufacturing';home?:HomePlanContext}){
 const [search,setSearch]=useSearchParams(),[retry,setRetry]=useState(0),[loaded,setLoaded]=useState<{key:string;data:Dict}|null>(null),[failure,setFailure]=useState<{key:string;error:string}|null>(null);
 const [filters,setFilters]=useState(false),[from,setFrom]=useState(''),[to,setTo]=useState(''),[item,setItem]=useState('');
 const query=useMemo(()=>Object.fromEntries(['start','end','item_id','location_id','kind','status','offset','section','order_id'].flatMap(k=>search.get('facts_'+k)?[[k,search.get('facts_'+k)!]]:[])),[search.toString()]);
 const key=JSON.stringify([scope,query]),data=loaded?.key===key?loaded.data:null,error=failure?.key===key?failure.error:'';
 useEffect(()=>{const c=new AbortController();setFailure(null);request<Dict>(businessViewUrl(scope,query),{signal:c.signal}).then(v=>{verifyBusinessView(v,scope);if(!c.signal.aborted)setLoaded({key,data:v})}).catch(e=>{if(!c.signal.aborted)setFailure({key,error:e.message})});return()=>c.abort()},[key,retry]);
 function update(values:Record<string,string>){setSearch(prev=>{const p=new URLSearchParams(prev);for(const[k,v]of Object.entries(values)){v?p.set('facts_'+k,v):p.delete('facts_'+k)}p.delete('facts_offset');return p})}
 function openFilters(){setFrom(query.start??'');setTo(query.end??'');setItem(query.item_id??'');setFilters(true)}
 function applyFilters(){update({start:from,end:to,item_id:item});setFilters(false)}
 return <section className={'business-facts'+(home?' business-home':'')} aria-label="已确认业务事实"><div className="business-facts-heading"><div>{!home&&<h2>{focus==='inventory'?'来源库存余额':focus==='purchases'?'来源采购与收货':focus==='manufacturing'?'来源制造记录':'企业业务概况'}</h2>}{data&&<span>{home?'数据截至 ':data.identity.enterprise_id??'已确认企业资料'}{!home&&data.identity.source_system==='ODOO'?' · Odoo 公司'+data.identity.company_id:''}{home?'':' · 截至 '}{formatSiteTime(data.identity.source_as_of,data.timezone)}</span>}</div><div>{home&&<Button onClick={home.onPlanResults}>查看计划结果</Button>}<Button className="quiet" onClick={openFilters}>筛选{Object.keys(query).some(k=>!['section','offset'].includes(k))?' · 已应用':''}</Button>{Object.keys(query).length>0&&<Button className="quiet" onClick={()=>update({start:'',end:'',item_id:'',location_id:'',kind:'',status:'',section:''})}>清除筛选</Button>}{home&&<details className="home-identity"><summary>计划与数据身份</summary><Definition rows={[["企业",data?.identity.enterprise_id??'读取中'],["PLAN",scope.planId],["DATA",scope.versionId],["当前方案",home.target],["保存运行",home.view.run?.solve_run_id??'未提交']]}/></details>}<Link to={'/data/'+scope.versionId}>确认资料</Link></div></div>
 {data&&(!focus||focus==='orders')&&<BusinessKindTabs compact={!!home} kinds={data.orders.available_kinds??[]} value={data.orders.kind??'FIRM_ORDER'} onChange={kind=>update({kind})}/>}
 {error?<><ErrorNote error={error}/><Button onClick={()=>setRetry(x=>x+1)}>重新读取业务资料</Button></>:data?<BusinessFactsContent data={data} scope={scope} focus={focus} home={home} onPage={(section,offset)=>setSearch(prev=>{const p=new URLSearchParams(prev);p.set('facts_section',section);p.set('facts_offset',String(offset));return p})}/>:<Loading/>}
 {filters&&<Dialog title="业务观察范围" size="480" centered onClose={()=>setFilters(false)}><form onSubmit={e=>{e.preventDefault();applyFilters()}}><label>要求日期起<input type="date" value={from} onChange={e=>setFrom(e.target.value)}/></label><label>要求日期止（不含）<input type="date" value={to} onChange={e=>setTo(e.target.value)}/></label><label>产品<input value={item} onChange={e=>setItem(e.target.value)} placeholder="产品编码；留空为全部"/></label><p>日期筛选订单要求日期；库存余额保持为本版本截至时点，不倒推历史库存。</p><Button onClick={applyFilters} className="primary">应用范围</Button></form></Dialog>}</section>;
}

export function BusinessFactsContent({data,scope,focus,home,onPage=()=>{}}:{data:Dict;scope:BusinessScope;focus?:'orders'|'purchases'|'inventory'|'manufacturing';home?:HomePlanContext;onPage?:(section:string,offset:number)=>void}){
 const [windowStart,setWindowStart]=useState(''),[width,setWidth]=useState(900),[selected,setSelected]=useState<Dict|null>(null),[detail,setDetail]=useState<{key:string;value:Dict}|null>(null),[detailError,setDetailError]=useState('');
 const [reduced,setReduced]=useState(false),plot=useRef<HTMLDivElement>(null),[detailOffset,setDetailOffset]=useState(0);
 useEffect(()=>{const m=window.matchMedia('(prefers-reduced-motion: reduce)'),change=()=>setReduced(m.matches);change();m.addEventListener('change',change);return()=>m.removeEventListener('change',change)},[]);
 useEffect(()=>{if(!plot.current)return;const observer=new ResizeObserver(([entry])=>setWidth(entry.contentRect.width));observer.observe(plot.current);return()=>observer.disconnect()},[]);
 const calendar=businessCalendarWindow(data.range,width,windowStart),daily=new Map<string,number>(data.orders.daily.map((d:Dict)=>[d.date,d.count]));
 const maximum=Math.max(1,...data.orders.daily.map((d:Dict)=>d.count)),day=selected?.day;
 const detailKey=JSON.stringify([scope,data.identity,data.filters,data.range,day,selected?.overdue,selected?.section,detailOffset]);
 useEffect(()=>{setDetailError('');if(!day&&!selected?.overdue&&!selected?.section)return;const c=new AbortController();request<Dict>(businessViewUrl(scope,businessDetailQuery(data.filters,selected,detailOffset,data.range)),{signal:c.signal}).then(v=>{verifyBusinessView(v,scope);if(!c.signal.aborted)setDetail({key:detailKey,value:v})}).catch(e=>{if(!c.signal.aborted)setDetailError(e.message)});return()=>c.abort()},[detailKey]);
 const common={animation:!reduced,animationDuration:180,animationDurationUpdate:160,stateAnimation:{duration:reduced?0:120},tooltip:{trigger:'item',confine:true,showDelay:0,hideDelay:100,transitionDuration:reduced?0:.15}};
 const calendarOption:echarts.EChartsCoreOption={...common,aria:{enabled:true,description:'订单交期分布，颜色表示确认版本中的订单条数。包括已发货订单，不表示规划缺口。'},tooltip:{...common.tooltip,formatter:(p:any)=>tip([p.data.name,p.data.value[1]<0?'观察范围外':p.data.value[1]+' 条需求','按要求日期 · 非规划满足状态'])},
  visualMap:[{type:'continuous',min:0,max:maximum,dimension:1,left:28,bottom:0,orient:'horizontal',itemWidth:10,itemHeight:100,text:['多','少'],inRange:{color:['#e0edf2','#5cabc6','#087ea4','#00465e']},outOfRange:{color:'#ffffff'},textStyle:{fontSize:11}}],
  calendar:{top:8,left:28,range:[calendar.from,calendar.to],cellSize:[calendar.cellSize,calendar.cellSize],yearLabel:{show:false},monthLabel:{position:'end',formatter:(p:any)=>p.M+'月',fontSize:11,margin:10},dayLabel:{firstDay:1,nameMap:['','一','','三','','五',''],fontSize:11,margin:8},splitLine:{show:false},itemStyle:{borderWidth:0}},
  series:[{type:'heatmap',coordinateSystem:'calendar',data:calendar.dates.map(date=>({name:date,eventId:daily.get(date)?date:undefined,value:[date,date<data.range.start||date>=data.range.end?-1:daily.get(date)??0],itemStyle:date<data.range.start||date>=data.range.end?{color:'#fff',borderColor:'#d8dadd',borderWidth:1}:!daily.get(date)?{color:'#eef0f2'}:undefined})),itemStyle:{borderRadius:3,borderWidth:2,borderColor:'#fff'},emphasis:{itemStyle:{color:'#005a76',borderWidth:2,borderColor:'#fff'}}}]};
 const inventoryOption:echarts.EChartsCoreOption={...common,grid:{left:105,right:12,top:12,bottom:28},xAxis:{type:'value',max:100,axisLabel:{formatter:'{value}%'},splitLine:{show:false}},yAxis:{type:'category',inverse:true,data:data.inventory.rows.map((r:Dict)=>r.item_id+' · '+r.location_id),axisLabel:{width:95,overflow:'truncate',fontSize:11},axisLine:{show:false},axisTick:{show:false}},
  tooltip:{...common.tooltip,formatter:(p:any)=>{const r=data.inventory.rows[p.dataIndex];return tip([r.item_id+' · '+r.location_id,'物理 '+amount(r.physical,r.uom),'可用 '+amount(r.available,r.uom),'冻结/不可用不是可用供应'])}},series:[{name:'可用',type:'bar',stack:'stock',barMaxWidth:15,itemStyle:{color:chartColors().actual},data:data.inventory.rows.map((r:Dict)=>({eventId:r.id,value:r.physical!=null&&r.available!=null&&Number(r.physical)>0?Number(r.available)/Number(r.physical)*100:null}))},{name:'非可用',type:'bar',stack:'stock',barMaxWidth:15,itemStyle:{color:chartColors().unavailable},data:data.inventory.rows.map((r:Dict)=>r.physical!=null&&r.available!=null&&Number(r.physical)>0?(1-Number(r.available)/Number(r.physical))*100:null)}]};
 const stockColumns:TableColumn<Dict>[]=[{id:'item',title:'物料 / 地点',value:r=><>{r.item_id}<small>{r.location_id}</small></>},{id:'physical',title:'物理',value:r=>amount(r.physical,r.uom)},{id:'available',title:'可用',value:r=>amount(r.available,r.uom)},{id:'blocked',title:'冻结 / 不可用',value:r=>amount(r.blocked,r.uom)}];
 const poColumns:TableColumn<Dict>[]=[{id:'id',title:'采购 / 物料',value:r=><>{r.id}<small>{r.item_id}</small></>},{id:'quantity',title:'未收',value:r=>amount(r.outstanding,r.uom)},{id:'at',title:'来源预计到货',value:r=>r.expected_receipt_at?formatSiteTime(r.expected_receipt_at,data.timezone).slice(0,16):'未知'}];
 const pager=(section:string,value:Dict)=><div className="fact-pagination">{value.offset>0&&<Button className="quiet" onClick={()=>onPage(section,Math.max(0,value.offset-20))}>上一页</Button>}<span>{value.total} 条</span>{value.next_offset!=null&&<Button className="quiet" onClick={()=>onPage(section,value.next_offset)}>下一页</Button>}</div>;
 const chosen=detail?.key===detailKey?detail.value:null;
 const openSection=(section:string,title:string)=>{setSelected({section,title});setDetailOffset(0)};
 const detailSection=selected?.section??'orders',detailPage=chosen?.[detailSection];
 const sourceColumns:TableColumn<Dict>[]=[
  ...(data.identity.source_system==='ODOO'?[{id:'actual',title:'原工单 / 已产出 / 已存放',value:(r:Dict)=><>{amount(r.quantity,r.unit)}<small>产出 {amount(r.completed_quantity,r.unit)} · 存放 {amount(r.stored_quantity,r.unit)}</small></>}]:[]),
  {id:'id',title:'对象',value:r=>r.id??r.name??'未声明身份'},
  {id:'object',title:'产品 / 类型',value:r=>r.item_id??r.product_id??r.type??r.resource_type??'—'},
  {id:'status',title:'来源状态',value:r=>r.status??r.kind??'—'},
  {id:'source',title:'记录',value:r=><details><summary>查看来源字段</summary><pre className="fact-source">{JSON.stringify(r,null,2)}</pre></details>},
 ];
 const detailColumns=detailSection==='orders'?orderColumns:detailSection==='inventory'?stockColumns:sourceColumns;
 const attention=<section className="fact-panel home-attention"><header><h3>需要关注</h3></header><div className="fact-attention"><button onClick={()=>{setSelected({overdue:true});setDetailOffset(0)}}><strong>{data.orders.overdue_count} 笔逾期未完成</strong><span>按本版本截至时点及来源剩余量</span></button><button onClick={()=>openSection('inventory','库存与业务记录')}><strong>{data.inventory.unknown_count} 条库存余额未知</strong><span>未知不计为零，不计作可用供应</span></button>{data.orders.opening_backlog_count>0&&<p>筛选起点之前仍有 {data.orders.opening_backlog_count} 笔未完成订单，未计入期间新增需求。</p>}{data.orders.overdue_unknown_count>0&&<p>另有 {data.orders.overdue_unknown_count} 条已到期需求的来源余额未知，未判定为无欠量。</p>}<p>逾期按本版本数据截至时点判断。</p></div></section>;
 const content=<>{(!focus||focus==='orders')&&<>{!home&&<div className="fact-summary">{data.orders.totals.map((t:Dict)=><span key={t.uom??'unknown'}>原需求数量 <b>{amount(t.ordered,t.uom)}</b> · 来源已发货 {amount(t.shipped,t.uom)} · 尚未发货 {amount(t.outstanding,t.uom)}</span>)}</div>}<div className={home?"fact-primary home-calendar-row":"fact-primary"}><section className="fact-panel"><header><h3>订单与交期</h3><span>{data.orders.total} 条需求{data.orders.header_total!==data.orders.total?' · '+data.orders.header_total+' 笔订单':''} · 按要求日期</span></header><div className="fact-calendar-tools"><strong>{calendar.from.slice(0,7)} — {calendar.to.slice(0,7)}</strong><div>{calendar.previous&&<Button className="quiet" icon="left" aria-label="前一段月份" onClick={()=>setWindowStart(calendar.previous!)}/>} {calendar.next&&<Button className="quiet" icon="right" aria-label="后一段月份" onClick={()=>setWindowStart(calendar.next!)}/>}</div></div><div ref={plot} className="fact-calendar" style={{height:calendar.cellSize*7+85}}><PlanningChart controls={false} compact label="确认订单交期日历" identityKey={JSON.stringify([data.identity,data.range,calendar.from])} option={calendarOption} onPick={id=>{setSelected({day:id});setDetailOffset(0)}}/></div><footer>颜色：订单条数，包含已完成订单 {data.orders.undated_count>0&&<span>交期未知 {data.orders.undated_count} 笔</span>} <Button className="quiet" icon="table" onClick={()=>openSection('orders','订单清单')}>订单清单</Button></footer></section>
 {!home&&attention}</div>
 </>}<div className="fact-domains" style={focus?{gridTemplateColumns:'minmax(0,1fr)'}:undefined}>{(!focus||focus==='purchases')&&<section className="fact-panel"><header><h3>原采购与到货</h3><span>{data.purchases.total} 笔</span></header><div className="fact-table"><DataTable rows={data.purchases.rows} columns={poColumns} rowId={r=>r.id} onSelect={r=>setSelected({record:r,title:'原采购及来源'})} empty="本版本没有已声明采购记录。"/></div>{pager('purchases',data.purchases)}<p className="fact-caption">预计到货与预计可用不代表实际收货或 QC 放行。</p></section>}
 {(!focus||focus==='inventory')&&<section className="fact-panel"><header><h3>地点库存余额</h3><span>截至本版本时点</span></header><div className="fact-stock-chart" style={{height:Math.max(130,Math.min(280,data.inventory.rows.length*27+40))}}><PlanningChart controls={false} compact label="来源库存可用比例" identityKey={JSON.stringify([data.identity,data.inventory.rows.map((r:Dict)=>r.id)])} option={inventoryOption} onPick={id=>setSelected({record:data.inventory.rows.find((r:Dict)=>r.id===id),title:'库存余额及来源'})}/></div><div className="fact-table"><DataTable rows={data.inventory.rows} columns={stockColumns} rowId={r=>r.id} onSelect={r=>setSelected({record:r,title:'库存余额及来源'})} empty="本版本没有库存声明。"/></div>{pager('inventory',data.inventory)}</section>}</div>
 {focus==='manufacturing'&&data.identity.source_system==='ODOO'&&<p className="fact-caption">订单范围 {(data.identity.scope.sale_ids??[]).join('、')} · 人员与换线采用已批准的独立研究政策 {data.identity.professional_policy_id}。工序完成、生产产出、成品存放和客户发货分别记录。</p>}
 {focus==='manufacturing'&&<section className="fact-panel"><header><h3>制造来源记录</h3><span>{data.manufacturing.total} 条</span></header><div className="fact-table"><DataTable rows={data.manufacturing.rows} columns={sourceColumns} rowId={r=>r.id} onSelect={r=>setSelected({record:r,title:'制造来源记录'})}/></div>{pager('manufacturing',data.manufacturing)}</section>}
 {!focus&&<div className="fact-pagination">{[['resources','资源资料'],['manufacturing','来源制造记录'],['forecasts','来源 Gross 预测']].map(([section,title])=>data[section]?.total>0&&<Button key={section} className="quiet" onClick={()=>openSection(section,title)}>{title} {data[section].total} 条</Button>)}</div>}
 </>;
 return <>{home&&scope.planId?<PlannerHomeOverview data={data} scope={scope} home={home} attention={attention}>{content}</PlannerHomeOverview>:content}
 {selected&&<Dialog title={day?day+' · 到期订单':selected.overdue?'逾期未完成订单':selected.title??'业务记录'} size="840" centered animate={!reduced} onClose={()=>setSelected(null)}>
  {day||selected.overdue||selected.section?<>{detailError?<ErrorNote error={detailError}/>:detailPage?<>
   <div className="fact-table"><DataTable rows={detailPage.rows} columns={detailColumns} rowId={r=>r.id} onSelect={r=>setSelected({record:r,title:detailSection==='orders'?'原订单及来源':detailSection==='inventory'?'库存余额及来源':'原业务记录及来源'})}/></div>
   <div className="fact-pagination"><span>{detailPage.total} 条 · 第 {Math.floor(detailPage.offset/20)+1} 页</span>
    {detailPage.offset>0&&<Button onClick={()=>setDetailOffset(Math.max(0,detailPage.offset-20))}>上一页</Button>}
    {detailPage.next_offset!=null&&<Button onClick={()=>setDetailOffset(detailPage.next_offset)}>下一页</Button>}
   </div></>:<Loading/>}</>:selected.record?<><p>企业事实 · {data.identity.data_version_id}</p><pre className="fact-source">{JSON.stringify(selected.record,null,2)}</pre></>:null}
  <Link to={'/data/'+scope.versionId}>查看确认资料与来源</Link>
 </Dialog>}</>;

}
export function BusinessFactsPage(){const {versionId=''}=useParams(),[search]=useSearchParams(),back=search.get('history_return');return <>{back&&/^\/plans\/PLAN-[a-f0-9]{16}\?/.test(back)&&<Link className="button-link" to={back}>返回原历史查询</Link>}<BusinessFacts scope={{versionId}} focus={search.get('facts_order_id')?'orders':undefined}/></>}
