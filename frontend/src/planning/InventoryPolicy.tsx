import {useEffect,useState} from 'react';
import {useSearchParams} from 'react-router';
import {request} from '../api/client';
import {Badge,Button,Definition,ErrorNote,Loading} from '../components/Controls';
import {DataTable,type TableColumn} from '../components/DataTable';
import {formatSiteTime} from './timeRange';
import {eventLabels} from './analysisModel';
import {decisionQuantity,type DemandDecisionScope} from './demandDecisionModel';
import {inventoryPolicyEventSearch,inventoryPolicyRows,inventoryPolicySelection,policyScopeKey,verifyInventoryPolicyIdentity,type InventoryPolicyEvent,type InventoryPolicyFilters,type InventoryPolicyReport,type InventoryPolicyRow} from './inventoryPolicyModel';
import './demand-decisions.css';
import './inventory-policy.css';

export type InventoryPolicyProps=DemandDecisionScope&{onAnalysis?:(context:URLSearchParams)=>void;onSupply?:()=>void};
const filterNames={query:'policy_query',item:'policy_item',location:'policy_location',uom:'policy_uom',bucket:'policy_bucket'} as const;

export function InventoryPolicy(p:InventoryPolicyProps){
 const[search,setSearch]=useSearchParams(),key=policyScopeKey(p);
 const[response,setResponse]=useState<{key:string;data:InventoryPolicyReport}|null>(null),[failure,setFailure]=useState<{key:string;message:string}|null>(null),[retry,setRetry]=useState(0);
 useEffect(()=>{
  const controller=new AbortController(),query=new URLSearchParams({data_version_id:p.versionId,run_id:p.runId,target:p.target});
  setFailure(null);
  request<InventoryPolicyReport>('/v1/enterprise-plans/'+encodeURIComponent(p.planId)+'/inventory-policy?'+query,{signal:controller.signal})
   .then(value=>{if(!controller.signal.aborted)setResponse({key,data:verifyInventoryPolicyIdentity(value,p)})})
   .catch((error:unknown)=>{if(!controller.signal.aborted)setFailure({key,message:error instanceof Error?error.message:'无法读取当前运行的库存政策。'})});
  return()=>controller.abort();
 },[key,retry]);
 const error=failure?.key===key?failure.message:'',data=response?.key===key?response.data:null;
 if(error)return <section className="demand-decisions inventory-policy"><h2>库存目标与期间缺口</h2><ErrorNote error={error}/><Button onClick={()=>setRetry(value=>value+1)}>重新读取当前运行</Button></section>;
 if(!data)return <Loading/>;
 const filters=Object.fromEntries(Object.entries(filterNames).map(([name,param])=>[name,search.get(param)??''])) as InventoryPolicyFilters;
 const rows=inventoryPolicyRows(data.rows,filters),choice={scope:search.get('policy_selection_scope')??'',rowId:search.get('policy_row')??'',eventId:search.get('policy_event')??undefined};
 const{row:current,event:currentEvent}=inventoryPolicySelection(data,p,filters,choice);
 const time=(value?:string|null)=>value?formatSiteTime(value,data.timezone):'未观察到 / 未核验';
 function change(field:keyof InventoryPolicyFilters,value:string){setSearch(previous=>{const next=new URLSearchParams(previous);if(value)next.set(filterNames[field],value);else next.delete(filterNames[field]);for(const name of ['policy_row','policy_event','policy_selection_scope'])next.delete(name);return next})}
 function choose(row:InventoryPolicyRow,eventId?:string){setSearch(previous=>{const next=new URLSearchParams(previous);next.set('policy_row',row.id);next.set('policy_selection_scope',key);if(eventId)next.set('policy_event',eventId);else next.delete('policy_event');return next})}
 function openAnalysis(row:InventoryPolicyRow,eventId?:string){const context=inventoryPolicyEventSearch(search,data!,p,row.id,eventId);if(p.onAnalysis)p.onAnalysis(context);else setSearch(context)}
 const values=(field:'item_id'|'location_id'|'uom')=>[...new Set(data.rows.map(row=>row[field]))];
 const columns:TableColumn<InventoryPolicyRow>[]=[
  {id:'item',title:'物料 / 地点 / 单位',value:row=><>{row.item_id}<small>{row.location_id} · {row.uom}</small></>,sortValue:row=>row.item_id},
  {id:'period',title:'期间 [开始, 结束)',value:row=><>{time(row.start_at)}<small>至 {time(row.end_at)}</small><small>{row.policy_version} · {row.id}</small></>},
  ...([['target_quantity','最低净可用目标'],['opening_quantity','期初净可用'],['inflow_quantity','计划流入'],['consumption_quantity','计划消耗'],['ending_quantity','预计期末'],['minimum_quantity','期间最低'],['ending_gap_quantity','期末目标差额'],['maximum_gap_quantity','期间最大差额']] as const).map(([id,title])=>({id,title,numeric:true,value:(row:InventoryPolicyRow)=>decisionQuantity(row[id],row.uom)})),
  {id:'first',title:'首次低于目标',value:row=>row.status==='PRESENT'?(row.first_gap_at?time(row.first_gap_at):'未观察到'): '未知'},
  {id:'state',title:'证据 / 负库存',value:row=><>{row.status==='PRESENT'?'已核验余额':'待核验'}<small>{row.negative_inventory_status==='FAIL'?'负库存：FAIL':row.negative_inventory_status==='PASS'?'未观察到负库存':'负库存未核验'}</small></>},
 ];
 const eventColumns:TableColumn<InventoryPolicyEvent>[]=[
  {id:'at',title:'时点 / 原生顺序',value:event=><>{time(event.at)}<small>序号 {event.native_sequence}</small></>},
  {id:'kind',title:'事件 / 原生引用',value:event=><>{eventLabels[event.kind]??event.kind}<small>{event.reference}</small></>},
  {id:'quantity',title:'增减',numeric:true,value:event=>decisionQuantity(event.quantity,current?.uom)},
  {id:'balance',title:'事件后净可用',numeric:true,value:event=>decisionQuantity(event.balance,current?.uom)},
 ];
 return <section className="demand-decisions inventory-policy" aria-label="库存目标与期间缺口工作区">
  <header className="demand-decision-heading"><h2>库存目标与期间缺口</h2><Button icon="reload" onClick={()=>setRetry(value=>value+1)}>刷新当前运行</Button></header>
  <details className="demand-decision-scope"><summary>{p.target==='baseline'?'原始基线':'隔离模拟方案'} · {p.runId} · {data.timezone}</summary><Definition rows={[["计划",p.planId],["数据版本",p.versionId],["运行",p.runId],["认证模型",data.identity.profile??'未提供']]}/></details>
  <div className="demand-decision-state"><Badge kind={data.known_business_fail?'bad':data.status==='PRESENT'?'':'risk'}>{data.status==='UNSUPPORTED'?'动态库存未认证':data.status==='NOT_READY'?'待补库存证据':data.status==='PARTIAL'?'部分桶待核验':data.status==='EMPTY_CHECKED'?'无政策桶':'政策缺口调查'}</Badge>{data.reason&&<span>{data.reason}</span>}</div>
  {data.known_business_fail&&<p className="risk-message">本运行存在已知业务核验失败；政策差额不能抵消硬约束失败。</p>}
  <p className="demand-decision-note">每桶目标为显式版本化政策假设，余额来自本运行净期初与真实原生事件。目标差额用于调查，负库存与客户欠量另行核对。</p>
  {!!data.rows.length&&<>
   <div className="demand-decision-toolbar">
    <label>查找物料 / 政策来源<input aria-label="库存政策文本筛选" value={filters.query??''} onChange={event=>change('query',event.target.value)} placeholder="物料、策略版本或来源"/></label>
    <label>物料<select aria-label="库存政策物料筛选" value={filters.item??''} onChange={event=>change('item',event.target.value)}><option value="">全部物料</option>{values('item_id').map(value=><option key={value}>{value}</option>)}</select></label>
    <label>地点<select aria-label="库存政策地点筛选" value={filters.location??''} onChange={event=>change('location',event.target.value)}><option value="">全部地点</option>{values('location_id').map(value=><option key={value}>{value}</option>)}</select></label>
    <label>单位<select aria-label="库存政策单位筛选" value={filters.uom??''} onChange={event=>change('uom',event.target.value)}><option value="">全部单位</option>{values('uom').map(value=><option key={value}>{value}</option>)}</select></label>
    <label>政策期间<select aria-label="库存政策期间筛选" value={filters.bucket??''} onChange={event=>change('bucket',event.target.value)}><option value="">全部期间</option>{data.rows.map(row=><option value={row.id} key={row.id}>{time(row.start_at)} — {time(row.end_at)}</option>)}</select></label>
    <Button onClick={()=>setSearch(previous=>{const next=new URLSearchParams(previous);for(const name of [...Object.values(filterNames),'policy_row','policy_event','policy_selection_scope'])next.delete(name);return next})}>清除筛选</Button>
   </div>
   <p className="demand-decision-note">显示 {rows.length} / {data.rows.length} 个政策桶。期间结束为开边界；同刻事务按原生顺序核对，桶最低值包含期初及各事件后的余额。</p>
   <div className="demand-decision-workspace inventory-policy-workspace"><div className="demand-decision-table inventory-policy-table" tabIndex={0} aria-label="库存目标期间表，可横向滚动"><DataTable rows={rows} columns={columns} rowId={row=>row.id} selected={current?.id} onSelect={row=>choose(row)} empty="当前筛选没有政策桶，可清除物料、地点、单位或期间筛选。"/></div>
    <aside className="demand-decision-inspector" aria-label="库存政策桶详情"><h3>{current?current.item_id+' · '+current.location_id:'政策桶与事件来源'}</h3>{current?<>
     <Definition rows={[["策略版本",current.policy_version],["期间开始",time(current.start_at)],["期间结束（不含）",time(current.end_at)],["目标净可用",decisionQuantity(current.target_quantity,current.uom)],["桶起点净可用",decisionQuantity(current.opening_quantity,current.uom)],["预计期末净可用",decisionQuantity(current.ending_quantity,current.uom)],["期间最低净可用",decisionQuantity(current.minimum_quantity,current.uom)],["期末目标差额",decisionQuantity(current.ending_gap_quantity,current.uom)],["期间最大差额",decisionQuantity(current.maximum_gap_quantity,current.uom)],["首次低于目标",current.status==='PRESENT'?(current.first_gap_at?time(current.first_gap_at):'未观察到'):'未知'],["负库存",current.negative_inventory_status==='FAIL'?'FAIL：已观察到负余额':current.negative_inventory_status==='PASS'?'未观察到负余额':'未核验']]}/>
     {current.reason&&<p className="pending-note">{current.reason}。缺失量保持未知。</p>}
     <details className="demand-decision-source"><summary>规划期初实物、冻结与待检</summary><Definition rows={[["实物在库",decisionQuantity(current.physical_opening,current.uom)],["质量冻结",decisionQuantity(current.quality_blocked,current.uom)],["待检数量",decisionQuantity(current.inspection_pending_quantity,current.uom)],["其他不可用",decisionQuantity(current.other_nonnettable,current.uom)]]}/><p>这些是规划期初来源字段。桶起点净量承接此前原生事务，不将冻结、待检或在途叠加为可用量。</p></details>
     {p.onSupply&&<Button onClick={p.onSupply}>调查辅料来源与补给</Button>}{current.status==='PRESENT'&&<Button onClick={()=>openAnalysis(current,currentEvent?.id)}>查看原生库存时间账</Button>}
     <details className="demand-decision-source"><summary>政策假设与原始来源</summary><pre className="raw-json">{JSON.stringify(current.policy,null,2)}</pre></details>
     <details className="demand-decision-source"><summary>余额承接与来源证据</summary><pre className="raw-json">{JSON.stringify({opening_source:current.opening_source,predecessor_event_ids:current.predecessor_event_ids,sources:current.sources},null,2)}</pre></details>
    </>:<p>选择一条物料与期间，核对目标、期末与最低余额，再查看该桶原生物料事件。</p>}</aside>
   </div>
   {current&&<section className="inventory-policy-events" aria-label="政策桶原生物料事件"><h3>{current.item_id} · {current.id} · 桶内原生事件</h3><div className="demand-decision-table inventory-policy-event-table" tabIndex={0} aria-label="政策桶事件表，可横向滚动"><DataTable rows={current.events} columns={eventColumns} rowId={event=>event.id} selected={currentEvent?.id} onSelect={event=>choose(current,event.id)} empty={current.status==='PRESENT'?'已核对：本桶没有原生库存增减事务，净量承接期初及前序事件。':'动态事件证据未完整核验，不能按0显示。'}/></div>
    {currentEvent&&<div className="inventory-policy-event-detail"><h4>{eventLabels[currentEvent.kind]??currentEvent.kind} · {currentEvent.reference}</h4><Definition rows={[["原生事件",currentEvent.id],["事件时点",time(currentEvent.at)],["原生同刻顺序",currentEvent.native_sequence],["增减",decisionQuantity(currentEvent.quantity,current.uom)],["事件后净可用",decisionQuantity(currentEvent.balance,current.uom)]]}/><Button onClick={()=>openAnalysis(current,currentEvent.id)}>在原生时间账定位此事件</Button><details className="demand-decision-source"><summary>原生事务记录与来源 SHA</summary><pre className="raw-json">{JSON.stringify(currentEvent,null,2)}</pre></details></div>}
    <p className="demand-decision-note">物料事件保留原生事务类别；Firm 与净预测身份以需求来源为准。本视图不推断某一库存由哪条客户订单独占或消耗。</p>
   </section>}
  </>}
  {!data.rows.length&&<p className="pending-note">{data.status==='UNSUPPORTED'?'当前模型尚未认证动态库存，不能生成目标差额。':'此确认版本在当前范围没有显式库存政策桶。'}</p>}
  {!!data.limits?.length&&<details className="demand-decision-limits"><summary>政策口径与适用范围</summary><ul>{data.limits.map((value,index)=><li key={index}>{value}</li>)}</ul></details>}
 </section>;
}
