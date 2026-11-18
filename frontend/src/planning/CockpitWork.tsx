import {useState} from 'react';
import type {Dict} from '../api/contracts';
import {Button,Select} from '../components/Controls';
import {fulfillmentAmount,fulfillmentGroupId} from './demandFulfillmentModel';
import {formatSiteTime} from './timeRange';
import type {CockpitScope} from './cockpitModel';
import {cockpitWorkRows,selectCockpitWork,cockpitQueueStatus,cockpitPerformanceDrill,cockpitWorkDrill} from './cockpitWorkModel';

type Open=(id:string,params:Record<string,string>)=>void;
const statusLabel:Record<string,string>={confirmed:'确认安排',approved:'核准安排',proposed:'计划建议',PENDING_QC:'已收待QC',FROZEN:'质量冻结'};
export const performanceLabels:Record<string,string>={FULL_ON_TIME:'全部按期',FULL_LATE:'迟后全部排定',PARTIALLY_PLANNED:'部分已排',NOT_PLANNED:'尚未排定',UNKNOWN:'按期待核验'};
const list=(x:unknown):Dict[]=>Array.isArray(x)?x:[];

export function CockpitWorkQueue({data,context,result,scope,search,change,open}:{data:Dict;context:Dict;result:Dict;scope:CockpitScope;search:URLSearchParams;change:(values:Record<string,string>)=>void;open:Open}){
 const all=cockpitWorkRows(data,context,result,scope),domains=[['purchase','采购与QC'],['manufacturing','制造工序'],['distribution','配送调拨']];
 const chosen=search.get('cockpit_domain'),domain=domains.some(([id])=>id===chosen)?chosen!:domains.find(([id])=>all.some(r=>r.domain===id))?.[0]??'purchase',phase=search.get('cockpit_phase')==='end'?'end':'start',status=search.get('cockpit_status')??'';
 const [expanded,setExpanded]=useState(''),selected=selectCockpitWork(all,domain,phase,status,data.range.start_at,data.range.end_at),showAll=expanded===JSON.stringify([scope,domain,phase,status,data.range])||selected.findIndex(r=>r.id===search.get('cockpit_focus'))>=5,shown=showAll?selected:selected.slice(0,5);
 const time=(v:string|null)=>v?formatSiteTime(v,data.timezone).slice(5):'时间待核实',first=domain==='purchase'?'物理到货':domain==='distribution'?'计划发运':'计划开工',last=domain==='purchase'?'QC后可用':domain==='distribution'?'计划到达':'计划完成';
 const queueStatus=cockpitQueueStatus(data,context,scope,domain),ready=['PRESENT','EMPTY_CHECKED'].includes(queueStatus);
 const unverifiedPurchases=list(result.purchase_suggestions).filter(d=>!all.some(r=>r.id==='queue:proposed-po:'+d.id));
  return <section className="cockpit-business-panel cockpit-work-queue" aria-label="近期工作队列"><div className="cockpit-section-title"><h2>全计划 · 近期工作</h2><span>{ready?selected.length+'个对象':queueStatus==='UNSUPPORTED'?'当前入口未认证':'队列证据待核验'} · 计划与确认资料</span></div>
  <div className="cockpit-queue-tabs" role="group" aria-label="工作领域">{domains.map(([id,label])=>{const status=cockpitQueueStatus(data,context,scope,id);return <button key={id} aria-pressed={domain===id} onClick={()=>change({cockpit_domain:id,cockpit_status:'',cockpit_focus:''})}>{label}<span>{['PRESENT','EMPTY_CHECKED'].includes(status)?all.filter(r=>r.domain===id).length:status==='UNSUPPORTED'?'未认证':'待核验'}</span></button>})}</div>
  <div className="cockpit-queue-filters">{domain!=='manufacturing'&&<Select label="队列日期依据" value={phase} options={[{value:'start',label:first},{value:'end',label:last}]} onChange={v=>change({cockpit_phase:v,cockpit_focus:''})}/>}<Select label="队列业务状态" value={status||'all'} options={[{value:'all',label:'全部状态'},...[...new Set(all.filter(r=>r.domain===domain).map(r=>r.status))].map(value=>({value,label:statusLabel[value]??value}))]} onChange={v=>change({cockpit_status:v==='all'?'':v,cockpit_focus:''})}/></div>
  {shown.length?<ul className="cockpit-action-queue">{shown.map(row=><li key={row.id}><button data-cockpit-id={row.id} onClick={()=>open(row.id,cockpitWorkDrill(row,data,context,phase))}><div><strong>{row.title}</strong><span>{row.detail}</span><small>{first} {time(row.start)}<br/>{row.status==='FROZEN'?'解冻时间未知；不计可用':last+' '+time(row.end)}</small></div><div><b>{fulfillmentAmount(row.quantity,row.uom)}</b><span className={['FROZEN','PENDING_QC'].includes(row.status)?'risk-text':''}>{statusLabel[row.status]??row.status}</span><small>{row.phase==='received'?'已收部分，独立于未收量':row.domain==='manufacturing'?'工序量，非父单累计':'未执行承诺'}</small><span className="cockpit-next">调查 →</span></div></button></li>)}</ul>:<p className="cockpit-empty">{all.some(r=>r.domain===domain)?'当前期间与状态下没有对应对象。':'当前入口没有此领域的认证队列；不代表没有业务或工作。'}</p>}
  {selected.length>5&&<Button className="quiet" onClick={()=>setExpanded(showAll?'':JSON.stringify([scope,domain,phase,status,data.range]))}>{showAll?'收起到前5项':'查看全部 '+selected.length+' 个对象'}</Button>}
  {domain==='purchase'&&unverifiedPurchases.length>0&&<div className="cockpit-uncertified-purchases"><p role="status">本运行另有 {unverifiedPurchases.length} 笔采购建议未纳入认证队列，需核对来源、数量及物理到货/QC时点；不能作为可执行供应。</p><details><summary>查看待核验建议的原记录</summary><pre className="raw-json">{JSON.stringify(unverifiedPurchases,null,2)}</pre></details></div>}
 </section>;
}

export function CockpitDeliveryPerformance({data,scope,selectedGroup,search,change,open}:{data:Dict;scope:CockpitScope;selectedGroup?:string;search:URLSearchParams;change:(values:Record<string,string>)=>void;open:Open}){
 const perf=data.demand?.delivery_performance,groups=list(perf?.groups),group=selectedGroup?groups.find(g=>fulfillmentGroupId(g)===selectedGroup):groups.find(g=>fulfillmentGroupId(g)===search.get('cockpit_performance_group'))??groups[0],category=search.get('cockpit_performance')??'',lines=list(group?.lines).filter(l=>!category||l.classification===category),counts=group?.counts??{};
 const selectedLines=list(data.demand?.lines).filter(l=>!selectedGroup||fulfillmentGroupId(l)===selectedGroup),unclassified=selectedLines.filter(l=>!['FIRM_ORDER','NET_FORECAST'].includes(l.demand_kind)).length,netCount=selectedLines.filter(l=>l.demand_kind==='NET_FORECAST').length;
 function drill(l:Dict){if(group)open('performance:'+l.demand_id,cockpitPerformanceDrill(data,scope,group,l))}
 return <section className="cockpit-business-panel cockpit-performance" aria-label="确认需求计划表现"><div className="cockpit-section-title"><h2>所选产品 · 确认需求表现</h2><span>按需求行</span></div>
  {!group?<div className="cockpit-empty"><strong>{perf?.status==='EMPTY_CHECKED'?'本期间没有确认需求':'所选对象的确认需求待核验'}</strong><p>{perf?.status==='EMPTY_CHECKED'?'已核验的确认需求交期均不在所选期间。':unclassified>0?unclassified+'行未明确需求类型，不能推定为确认订单。':'所选产品缺少本期间可认证的确认需求表现。'}</p><Button className="quiet" onClick={()=>open('performance-source',{analysis:'reports',report:'demand',...(selectedGroup?{report_group:selectedGroup}:{})})}>查看原需求</Button></div>:<>
  {groups.length>1&&!selectedGroup?<Select label="表现物料地点阶段" value={fulfillmentGroupId(group)} options={groups.map(g=>({value:fulfillmentGroupId(g),label:g.item_id+' · '+g.location_id+' · '+g.endpoint}))} onChange={v=>change({cockpit_group:v,cockpit_performance_group:v,cockpit_performance:'',cockpit_focus:''})}/>:<p className="cockpit-muted">{group.item_id} · {group.location_id} · {group.firm_line_count}行</p>}
  <div className="cockpit-performance-value"><strong>{group.on_time_percent==null?'按期比例待核验':Number(group.on_time_percent).toLocaleString('zh-CN',{maximumFractionDigits:1})+'%'}</strong><span>全部按期排定 / 确认需求行<br/>计划表现，非客户实际收货</span></div>
  <div className="cockpit-performance-track" role="img" aria-label={Object.entries(performanceLabels).map(([k,v])=>v+' '+(counts[k]??0)+'行').join('；')}>{Object.keys(performanceLabels).filter(k=>counts[k]>0).map(k=><span key={k} className={'performance-'+k} style={{flex:counts[k]}}/>)}</div>
  <div className="cockpit-performance-counts">{Object.entries(performanceLabels).map(([k,label])=><button key={k} className={'performance-'+k} aria-pressed={category===k} onClick={()=>change({cockpit_performance:category===k?'':k,cockpit_focus:''})}><span>{label}</span><b>{counts[k]??0}</b></button>)}</div>
  <ul className="cockpit-performance-lines">{lines.map(l=><li key={l.demand_id}><button data-cockpit-id={'performance:'+l.demand_id} onClick={()=>drill(l)}><strong>{l.order_ref??l.demand_id}</strong><span>{performanceLabels[l.classification]??'待核验'}</span><small>计划 {fulfillmentAmount(l.planned_quantity,group.uom)} / {fulfillmentAmount(l.requested_quantity,group.uom)} · 未满足 {fulfillmentAmount(l.unfulfilled_quantity,group.uom)}</small>{l.classification==='PARTIALLY_PLANNED'&&<small>未排部分没有确定完成日期</small>}</button></li>)}</ul>
  </>}
  <p className="cockpit-muted">所选对象的净预测 {netCount}行不进入确认需求分母{unclassified>0?'；另有'+unclassified+'行类型未知':''}。</p>
 </section>;
}

export function CockpitInventoryRisk({native,scope,open}:{native:Dict|null;scope:CockpitScope;open:Open}){
 const inv=native?.inventory_analysis,risk=inv?.risk,rows=list(risk?.rows),attention=rows.filter(r=>r.category!=='NO_NEGATIVE_OBSERVED'),ordered=[...attention,...rows.filter(r=>r.category==='NO_NEGATIVE_OBSERVED')];
 if(!risk||inv?.status==='UNSUPPORTED')return null;
 const names:Record<string,string>={NEGATIVE_NET_AVAILABLE:'净可用为负',BELOW_SOFT_MINIMUM:'低于软目标',NO_NEGATIVE_OBSERVED:'期间未见负数',NOT_CHECKED:'风险待核验'};
 const coverage=risk.coverage;
 function inspect(r:Dict){const predecessor=!r.first_event_id&&r.opening_basis?.predecessor_event_id;open('risk:'+r.id,{analysis:'inventory',analysis_item:r.item_id,analysis_location:r.location_id,...(r.first_event_id?{analysis_event:r.first_event_id}:predecessor?{analysis_event:predecessor}:{}),...(risk.window?.start_at?{analysis_start:predecessor?native!.planning_at:risk.window.start_at,analysis_end:risk.window.end_at}:{})})}
 return <section className="cockpit-business-panel cockpit-risk-panel" aria-label="库存过程风险"><div className="cockpit-section-title"><h2>库存过程风险</h2><span>{coverage?.verified_count==null||coverage?.item_location_count==null?'覆盖待核验':coverage.verified_count+' / '+coverage.item_location_count+'个物料地点已核验'}</span></div>
  {risk.validation?.status==='FAIL'&&<p role="alert" className="risk-text">原物料核验失败；风险细项未认证，不作为合法计划。</p>}
  <ul className="cockpit-risk-list">{ordered.map(r=><li key={r.id}><div><strong>{r.item_id} · {r.location_id}</strong><span className={['NEGATIVE_NET_AVAILABLE','BELOW_SOFT_MINIMUM'].includes(r.category)?'risk-text':''}>{names[r.category]??'待核验'}</span><small>{r.minimum_balance==null?'最低余额未核验':'最低净可用 '+fulfillmentAmount(r.minimum_balance,r.uom)}{r.first_risk_at?' · 首次 '+formatSiteTime(r.first_risk_at,native?.timezone??'Asia/Shanghai'):''}</small>{r.first_risk_at&&!r.first_event_id&&<small>观察期初已偏离目标，此时没有新事件；依据原期初及前序账。</small>}</div><Button className="quiet" data-cockpit-id={'risk:'+r.id} onClick={()=>inspect(r)}>{r.first_event_id?'调查首次事件':r.first_risk_at?'核对期初与全账':'库存时间账'}</Button></li>)}</ul>
  {!rows.length&&<p className="cockpit-empty">本次没有可认证的风险明细，不能当作零风险。</p>}
  <p className="cockpit-muted">软目标偏离不等同硬约束违规；未见负数不证明不会缺货。</p>
 </section>;
}
