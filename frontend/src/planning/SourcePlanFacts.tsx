import {useEffect,useState} from 'react';
import type {Dict} from '../api/contracts';
import {request} from '../api/client';
import {Button,Definition,Dialog,ErrorNote,Loading} from '../components/Controls';
import {DataTable,type TableColumn} from '../components/DataTable';
import {PlanningChart} from '../components/PlanningChart';
import {PlanningGantt} from '../components/PlanningGantt';
import {BusinessFacts} from './BusinessFacts';
import {sourceGantt,sourceManufacturing,sourceResources,verifySourceWorkspace} from './sourcePlanModel';
import {formatSiteTime} from './timeRange';
import './business-facts.css';

export const sourcePageModes=['schedule','manufacturing','composite','resources','inventory','supply-work','accessory-supply','inventory-policy','inventory-decision','availability','demand-planning','network-supply','order-inquiry'];
export function SourcePlanFacts({planId,versionId,mode,onMode}:{planId:string;versionId:string;mode:string;onMode:(mode:string)=>void}){
 const key=JSON.stringify([planId,versionId]),[loaded,setLoaded]=useState<{key:string;value:Dict}|null>(null),[error,setError]=useState(''),[retry,setRetry]=useState(0),[selected,setSelected]=useState<Dict|null>(null);
 const data=loaded?.key===key?loaded.value:null;
 useEffect(()=>{const c=new AbortController();setError('');setSelected(null);request<Dict>('/v1/enterprise-plans/'+encodeURIComponent(planId)+'/source-workspace?'+new URLSearchParams({data_version_id:versionId}),{signal:c.signal}).then(v=>{verifySourceWorkspace(v,planId,versionId);if(!c.signal.aborted)setLoaded({key,value:v})}).catch(e=>{if(!c.signal.aborted)setError(e.message)});return()=>c.abort()},[key,retry]);
 const scope={planId,versionId};
 if(error)return <section className="business-facts"><ErrorNote error={error}/><Button onClick={()=>setRetry(x=>x+1)}>重新读取来源</Button></section>;
 if(!data)return <Loading/>;
 const input=data.input,tz=input.site.timezone,time=(v:string)=>v?formatSiteTime(v,tz):'未声明',resources=sourceResources(input),mos=sourceManufacturing(input),gantt=sourceGantt(input);
 const table=(rows:Dict[],columns:TableColumn<Dict>[],id:(r:Dict)=>string=r=>r.id)=><div className="fact-table" tabIndex={0}><DataTable rows={rows} columns={columns} rowId={id} onSelect={r=>setSelected(r)}/></div>;
 const sourceColumns:TableColumn<Dict>[]=[{id:'id',title:'来源对象',value:r=>r.id},{id:'item',title:'物料 / 地点',value:r=><>{r.item_id}<small>{r.location_id}</small></>},{id:'quantity',title:'数量',value:r=>String(r.quantity??r.gross_quantity??'未知')+' '+(r.uom??'')},{id:'time',title:'来源时间',value:r=>time(r.start_at??r.bucket_start??r.receipt_at)}];
 return <section className="business-facts" aria-label="当前确认版本的专业来源资料"><div className="business-facts-heading"><div><h2>来源资料</h2><span>截至 {time(input.planning_at)} · {tz} · 容量范围 {time(input.horizon.start_at)} 至 {time(input.horizon.end_at)}</span></div><Button className="quiet" onClick={()=>onMode('cockpit')}>返回计划驾驶舱</Button></div>
  {['inventory','availability','supply-work','accessory-supply','inventory-decision'].includes(mode)?<BusinessFacts scope={scope} focus={['supply-work','accessory-supply'].includes(mode)?'purchases':'inventory'}/>:
   mode==='demand-planning'?<><BusinessFacts scope={scope} focus="orders"/><section className="fact-panel"><header><h3>来源 Gross 预测</h3><Button onClick={()=>onMode('forecast-netting')}>历史与统计预测</Button></header>{table(data.forecasts,sourceColumns)}<p className="fact-caption">Gross 保留原值；净需求仍须明确消费政策。</p></section></>:
   mode==='network-supply'?<><section className="fact-panel"><header><h3>来源路线与既有调拨</h3></header>{table(input.distribution_lanes??[],[{id:'id',title:'路线',value:r=>r.id},{id:'item',title:'物料',value:r=>r.item_id},{id:'route',title:'起点 → 终点',value:r=>r.origin_location_id+' → '+r.destination_location_id},{id:'lead',title:'提前期（小时）',value:r=>r.lead_time_seconds==null?'未知':Number(r.lead_time_seconds)/3600}])}{table(input.distribution_orders??(input.existing_orders??[]).filter((r:Dict)=>r.type==='DO'),sourceColumns)}</section><BusinessFacts scope={scope} focus="inventory"/></>:
   mode==='inventory-policy'||mode==='order-inquiry'?<><section className="fact-panel"><header><h3>来源库存目标与供应条件</h3><Button onClick={()=>onMode('stock-replenishment')}>库存策略研究</Button></header>{table(data.inventory_targets??[],[{id:'item',title:'物料 / 地点',value:r=>r.item_id+' · '+r.location_id},{id:'min',title:'最低目标',value:r=>r.minimum+' '+r.uom},{id:'max',title:'补至目标',value:r=>r.maximum+' '+r.uom},{id:'range',title:'来源生效范围',value:r=><>{time(r.effective_from)}<small>至 {time(r.effective_to)}</small></>}],r=>r.id??JSON.stringify([r.item_id,r.location_id,r.uom,r.effective_from,r.effective_to]))}{table(input.supply_sources??[],[{id:'id',title:'供应来源',value:r=>r.id},{id:'item',title:'物料 / 地点',value:r=>r.item_id+' · '+r.location_id},{id:'moq',title:'MOQ / 倍量',value:r=>r.minimum_order_quantity+' / '+r.order_multiple},{id:'lead',title:'提前期 / QC（小时）',value:r=>Number(r.lead_time_seconds)/3600+' / '+(r.receiving_qc?.wait_seconds==null?'未知':Number(r.receiving_qc.wait_seconds)/3600)}])}</section>{mode==='order-inquiry'&&<p>供应承诺与接单结论须先有通过业务审计的原生运行。</p>}</>:
   <>{mode==='manufacturing'&&<BusinessFacts scope={scope} focus="manufacturing"/>}<section className="fact-panel"><header><h3>设备、人员与来源日历</h3><span>可用容量按输入声明；计划负荷未据此推定</span></header>
    <div style={{height:Math.max(180,Math.min(420,resources.length*30+60))}}><PlanningChart compact controls={false} label="来源连续资源可用工时" identityKey={key} option={{tooltip:{trigger:'axis',axisPointer:{type:'shadow'},valueFormatter:(v:unknown)=>v==null?'未声明工时':Number(v).toFixed(2)+' 小时'},grid:{left:100,right:35,top:20,bottom:30},xAxis:{type:'value',name:'小时'},yAxis:{type:'category',inverse:true,data:resources.filter((r:Dict)=>r.available_hours!==null).map((r:Dict)=>r.id)},series:[{type:'bar',name:'来源可用容量',barMaxWidth:18,data:resources.filter((r:Dict)=>r.available_hours!==null).map((r:Dict)=>({value:r.available_hours,eventId:r.id})),emphasis:{focus:'self'}}]}} onPick={id=>setSelected(resources.find((r:Dict)=>r.id===id)??null)}/></div>
    {table(resources,[{id:'id',title:'资源',value:r=>r.id},{id:'role',title:'角色 / 模型',value:r=>(r.role??'未声明角色')+' · '+(r.capacity_model??'按来源日历')},{id:'hours',title:'来源可用容量',value:r=>r.available_hours===null?(r.capacity_model==='WINDOW_QUANTITY'?r.window_capacity_quantity+' '+r.capacity_uom+' / 窗口':'工时不适用或未声明'):r.available_hours.toFixed(2)+' 小时'},{id:'load',title:'计划负荷',value:()=>'待原生计算'}])}
   </section>
   <section className="fact-panel" style={{marginTop:16}}><header><h3>来源工单与保护窗口</h3><span>{mos.length} 笔 · 不重建原生工序占用</span></header>
    {mos.length>0&&<PlanningGantt identityKey={key} label="来源受保护工单窗口" {...gantt} start={Date.parse(input.horizon.start_at)} end={Date.parse(input.horizon.end_at)} timezone={tz} selectedTask={selected?.id} onSelect={v=>setSelected(mos.find((r:Dict)=>r.id===v.taskId))}/>}
    {table(mos,[...sourceColumns,{id:'resource',title:'源设备 / 状态',value:r=>(r.resource_id??r.operations?.map((o:Dict)=>o.resource_id).join(' / ')??'未声明')+' · '+r.status},{id:'end',title:'来源结束',value:r=>time(r.end_at)}])}
   </section>
   <section className="fact-panel" style={{marginTop:16}}><header><h3>资源资格</h3><span>具名人员、设备与池分别保留</span></header>{table(input.resource_skills??[],[{id:'r',title:'资源',value:r=>r.resource_id},{id:'s',title:'资格',value:r=>r.skill_id},{id:'from',title:'生效',value:r=>time(r.effective_from)},{id:'to',title:'到期',value:r=>time(r.effective_to)}],r=>JSON.stringify([r.resource_id,r.skill_id,r.effective_from]))}</section></>}
  {selected&&<Dialog title="来源对象详情" size="720" onClose={()=>setSelected(null)}><Definition rows={[["确认数据版本",versionId],["规划运行",'未使用原生结果']]}/><pre className="fact-source">{JSON.stringify(selected,null,2)}</pre></Dialog>}
 </section>;
}
