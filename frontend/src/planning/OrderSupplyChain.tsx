import {useId,useMemo,useState} from 'react';
import type {Dict,PlanView} from '../api/contracts';
import {Badge,Button,Definition} from '../components/Controls';
import {NativeSource} from './OrderDetails';
import {buildOrderSupplyChain,type SupplyNode,validDate} from './supplyChainModel';
import {formatSiteTime} from './timeRange';
import {qty,unit} from './planModel';
import {deliveryStatusLabel} from './orderModel';
import './order-supply-chain.css';

const labels={PURCHASE:'采购与质检等待',STOCK:'期初净可用库存',OPERATION:'生产工序',DEMAND:'需求结果'};
function amount(value:string|null|undefined,uom:string){return value===null||value===undefined?'未核验':qty(String(value).replace(/(\.\d*?)0+$/,'$1').replace(/\.$/,''))+' '+(uom==='set'?'套':unit(uom));}
export function OrderSupplyChain({input,view,planId,versionId,orderId,target,orders,scenarios,onOrder,onTarget,onLocate}:{input:Dict;view:PlanView;planId:string;versionId:string;orderId:string;target:string;orders:{id:string;label:string}[];scenarios:{id:string;label:string}[];onOrder:(id:string)=>void;onTarget:(id:string)=>void;onLocate:(id:string)=>void}){
 const m=useMemo(()=>buildOrderSupplyChain({input,view,planId,versionId,orderId}),[input,view,planId,versionId,orderId]);
 const [selected,setSelected]=useState('demand:'+orderId),node=m.nodes.find(n=>n.id===selected)??m.nodes.find(n=>n.kind==='DEMAND');
 const [scale,setScale]=useState<'all'|'production'>('all'),[linkScope,setLinkScope]=useState<string|null>(null);
 const currentScope=JSON.stringify([planId,versionId,m.runId,orderId,target]),showLinks=linkScope===currentScope;
 const timezone=view.context?.timezone??input.site?.timezone??'Asia/Shanghai';
 const id=useId().replace(/[^a-zA-Z0-9]/g,''),time=(v:string|null|undefined)=>validDate(v)?formatSiteTime(v,timezone):'尚无已核验日期';
 const dates=[m.planningAt,m.dueAt,...m.nodes.flatMap(n=>[n.startAt,n.endAt])].filter((s):s is string=>!!s).map(Date.parse);
 const productionDates=m.nodes.filter(n=>n.kind==='OPERATION'||n.kind==='DEMAND').flatMap(n=>[n.startAt,n.endAt]).filter((s):s is string=>!!s).map(Date.parse);
 const production=scale==='production'&&productionDates.length>0;
 const rangeDates=production?productionDates:dates,pad=production?1800000:7200000;
 const lo=rangeDates.length?Math.min(...rangeDates)-pad:0,hi=rangeDates.length?Math.max(...rangeDates)+pad:1;
 const tickStep=hi-lo>172800000?86400000:hi-lo>43200000?21600000:hi-lo>10800000?3600000:1800000;
 const ticks=Array.from({length:Math.max(0,Math.floor(hi/tickStep)-Math.ceil(lo/tickStep)+1)},(_,i)=>(Math.ceil(lo/tickStep)+i)*tickStep);
 const left=260,right=1080,chartHeight=m.nodes.length*82+118;
 const rawX=(s:string)=>left+(Date.parse(s)-lo)/Math.max(hi-lo,1)*(right-left),x=(s:string)=>Math.max(left,Math.min(right,rawX(s))),y=(i:number)=>92+i*82;
 const active=node?.id,related=new Set(m.links.filter(l=>l.from===active||l.to===active).flatMap(l=>[l.from,l.to]));
 const duration=(n:SupplyNode)=>{if(!n.startAt||!n.endAt)return '';const minutes=Math.round((Date.parse(n.endAt)-Date.parse(n.startAt))/60000);return minutes>=60?(minutes%60?`${Math.floor(minutes/60)}小时${minutes%60}分`:`${minutes/60}小时`):`${minutes}分钟`;};
 const kindLabel=(n:SupplyNode)=>n.kind==='STOCK'?'库存':n.kind==='PURCHASE'?'采购':n.kind==='DEMAND'?'需求':'工序';
 const pos=new Map(m.nodes.map((n,i)=>[n.id,{n,y:y(i)}])),linkedPurchases=new Set(m.nodes.filter(n=>n.kind==='PURCHASE').map(n=>n.reference)),unlinked=m.purchaseFacts.filter(p=>!linkedPurchases.has(p.reference));
 const parts=(n:SupplyNode)=>`${n.label} · ${amount(n.quantity,n.uom)}`;
 const selectedLinks=m.links.filter(l=>l.to===node?.id);
 const url='#/plans/'+encodeURIComponent(planId)+'?'+new URLSearchParams({target,order_line_id:orderId,view:'supply',run_id:m.runId,data_version_id:versionId}).toString();
 return <section className="order-supply-chain" aria-label="订单供需与生产时间链">
  <div className="chain-toolbar"><label>需求行<select aria-label="供需链需求行" value={orderId} onChange={e=>onOrder(e.target.value)}>{orders.map(o=><option key={o.id} value={o.id}>{o.label} · {o.id}</option>)}</select></label><label>计划方案<select aria-label="供需链计划方案" value={target} onChange={e=>onTarget(e.target.value)}>{scenarios.map(s=><option key={s.id} value={s.id}>{s.label}</option>)}</select></label><a href={url}>此需求的专属链接</a></div>
  <p className="chain-context">{m.stageEndpoint?'缝后阶段需求 · 不含后续成衣质检与包装':view.context?.wip?.stage_endpoint?.label??'规划需求'}<span>运行 {m.runId}</span></p>
  {m.delivery&&<div className="chain-metrics"><div><span>需求数量</span><strong>{amount(m.delivery.requested_quantity,m.delivery.uom)}</strong></div><div><span>计划满足</span><strong>{amount(m.delivery.planned_quantity,m.delivery.uom)}</strong></div><div className={Number(m.delivery.unfulfilled_quantity)>0?'chain-short':''}><span>尚未满足</span><strong>{amount(m.delivery.unfulfilled_quantity,m.delivery.uom)}</strong></div><div><span>要求时间</span><strong>{time(m.dueAt)}</strong></div></div>}
  {m.delivery&&<p className="chain-outcome">已排定部分：{Number(m.delivery.planned_quantity)>0?time(m.delivery.planned_delivery_at):'本次未排定'}。{Number(m.delivery.unfulfilled_quantity)>0&&<>尚未满足的 {amount(m.delivery.unfulfilled_quantity,m.delivery.uom)} 没有已核验的计划完成日期。</>}</p>}
  <div className="chain-legend"><Badge kind={m.status==='PRESENT_WITH_LIMITS'?'good':'risk'}>{m.status==='PRESENT_WITH_LIMITS'?'存在原生供需关联':m.status==='UNSUPPORTED'?'本视图暂不支持该模型':m.status==='NOT_CHECKED'?'当前证据未核验':'供需关联证据不足'}</Badge><span>任务全量、缓冲区总量不等于本需求的分配量。</span></div>
  {m.notices.map(n=><p key={n} className="pending-note">{n}</p>)}
  {m.nodes.length>0&&<>
   <div className="chain-chart-tools"><div><strong>供需时间链</strong><span>按真实时间比例 · {timezone}</span></div><div className="chain-chart-actions"><Button className="chain-links-toggle" aria-pressed={showLinks} disabled={!m.links.length} title="查看所选节点的直接上下游关联" onClick={()=>setLinkScope(showLinks?null:currentScope)}>{showLinks?'隐藏关联':'显示关联'}</Button><div className="chain-scale" role="group" aria-label="时间显示范围"><Button aria-pressed={scale==='all'} onClick={()=>setScale('all')}>完整时间链</Button><Button aria-pressed={scale==='production'} disabled={!productionDates.length} onClick={()=>setScale('production')}>生产时段</Button></div></div></div>
   <div className="chain-key" aria-label="时间图图例"><span><i className="key-stock"/>净可用库存</span><span><i className="key-qc"/>到货后QC等待</span><span><i className="key-operation"/>制造任务</span><span><i className="key-demand"/>计划满足</span><span><i className="key-due"/>需求截止</span></div>
   <div className="chain-chart" tabIndex={0} aria-label="供需时间图，可横向滚动"><svg viewBox={`0 0 1120 ${chartHeight}`} width="1120" height={chartHeight} role="group" aria-label="订单供需时间图">
    <defs><marker id={'arrow'+id} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L10,5 L0,10" fill="currentColor"/></marker><pattern id={'qc'+id} width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="7" height="7" fill="#f8efda"/><line x1="0" y1="0" x2="0" y2="7" stroke="#d2b777" strokeWidth="2"/></pattern></defs>
    <rect className="chain-header-bg" width="1120" height="47"/>
    <text className="chain-column-title" x="16" y="28">业务对象 / 任务全量</text>
    {m.nodes.map((n,i)=><g key={'row'+n.id}><rect className={'chain-row-bg '+(n.id===node?.id?'is-selected':'')} x="0" y={y(i)-37} width="1120" height="80"/><line className="chain-row-line" x1="0" x2="1120" y1={y(i)+43} y2={y(i)+43}/></g>)}
    {ticks.map(t=>{const at=x(new Date(t).toISOString());return <g key={t}><line className="chain-gridline" x1={at} x2={at} y1="47" y2={chartHeight-39}/><text className="chain-tick" textAnchor={at>right-50?'end':at<left+50?'start':'middle'} x={at} y="28">{formatSiteTime(new Date(t).toISOString(),timezone).slice(5)}</text></g>;})}
    <line className="chain-label-divider" x1={left-18} x2={left-18} y1="0" y2={chartHeight-39}/>
    {m.planningAt&&rawX(m.planningAt)>=left&&rawX(m.planningAt)<=right&&<g><line className="chain-now" x1={x(m.planningAt)} x2={x(m.planningAt)} y1="47" y2={chartHeight-39}/><text className="chain-tick" x={x(m.planningAt)+4} y={chartHeight-17}>规划时点</text></g>}
    {m.dueAt&&rawX(m.dueAt)>=left&&rawX(m.dueAt)<=right&&<g><line className="chain-due" x1={x(m.dueAt)} x2={x(m.dueAt)} y1="47" y2={chartHeight-39}/><text className="chain-due-label" textAnchor="end" x={x(m.dueAt)-5} y={chartHeight-17}>需求截止 {time(m.dueAt).slice(5)}</text></g>}
    {showLinks&&m.links.filter(l=>l.from===active||l.to===active).map(l=>{const f=pos.get(l.from),t=pos.get(l.to);if(!f?.n.endAt||!t?.n.startAt)return null;const a=x(f.n.endAt),b=x(t.n.startAt);return <path key={l.from+'|'+l.to} data-chain-link="native" data-link-from={l.from} data-link-to={l.to} className="chain-link is-related" d={`M${a},${f.y} C${a+18},${f.y} ${Math.max(left,b-22)},${t.y} ${b},${t.y}`} markerEnd={`url(#arrow${id})`}/>})}
    {m.nodes.map((n,i)=>{const before=!!n.endAt&&rawX(n.endAt)<left,after=!!n.startAt&&rawX(n.startAt)>right;
     const a=n.startAt?x(n.startAt):left,b=n.endAt?x(n.endAt):left,wide=b-a>150,nearEnd=b>right-150;
     const caption=n.kind==='PURCHASE'?`QC等待 ${duration(n)}`:n.kind==='OPERATION'?`${amount(n.quantity,n.uom)} · ${duration(n)}`:n.kind==='STOCK'?`净可用 ${amount(n.quantity,n.uom)}`:`已排定 ${amount(n.quantity,n.uom)}`;
     return <g key={n.id} className={'chain-node kind-'+n.kind+' '+(n.id===node?.id?'is-selected':'')+(showLinks&&related.has(n.id)?' is-related':'')} role="button" tabIndex={0} aria-label={parts(n)} aria-pressed={n.id===node?.id} data-node-id={n.id} data-node-kind={n.kind} onClick={()=>setSelected(n.id)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();setSelected(n.id)}}}>
      <title>{parts(n)}；{time(n.startAt)} → {time(n.endAt)}</title><rect className="chain-row-hit" x="0" y={y(i)-36} width="1120" height="79"/>
      <rect className="chain-type-bg" x="15" y={y(i)-23} width="34" height="20" rx="3"/><text className="chain-type" textAnchor="middle" x="32" y={y(i)-9}>{kindLabel(n)}</text>
      <text className="chain-node-title" x="59" y={y(i)-8}>{n.label.length>20?n.label.slice(0,19)+'…':n.label}</text><text className="chain-node-subtitle" x="16" y={y(i)+15}>{n.itemId} · {amount(n.quantity,n.uom)}</text>
      {n.startAt&&n.endAt?<>
       {before||after?<><path className="chain-offscreen" d={before?`M${left+7},${y(i)-6} l-6,6 6,6`:`M${right-7},${y(i)-6} l6,6 -6,6`}/><text className="chain-event-label" x={before?left+16:right-16} textAnchor={before?'start':'end'} y={y(i)-12}>{before?'此前已可用':'晚于当前时段'} · {time(n.endAt).slice(5)}</text></>:<>
        {n.kind==='STOCK'||n.kind==='DEMAND'?<path className={'chain-milestone kind-'+n.kind} d={`M${a},${y(i)-7} l7,7 -7,7 -7,-7 Z`}/>:<rect className={'chain-bar kind-'+n.kind} x={a} y={y(i)-8} width={Math.max(1,b-a)} height="16" rx="2" style={n.kind==='PURCHASE'?{fill:`url(#qc${id})`}:undefined}/>}
        {n.kind==='PURCHASE'&&<><circle className="chain-physical" cx={a} cy={y(i)} r="5"/><circle className="chain-available" cx={b} cy={y(i)} r="4"/><text className="chain-event-label" x={a} y={y(i)+25}>物理到货 {time(n.startAt).slice(5)}</text><text className="chain-event-label" x={b} textAnchor="end" y={y(i)-17}>QC后可用 {time(n.endAt).slice(5)}</text></>}
        <text className={'chain-bar-label '+(n.kind==='PURCHASE'&&wide?'inside-qc':'')} x={n.kind==='PURCHASE'&&wide?(a+b)/2:nearEnd?b-10:b+12} textAnchor={n.kind==='PURCHASE'&&wide?'middle':nearEnd?'end':'start'} y={n.kind==='PURCHASE'&&wide?y(i)+4:y(i)-14}>{caption}</text>
       </>}
      </>:<text className="chain-node-subtitle" x={left+10} y={y(i)+4}>日期未核验，不补造时间</text>}
     </g>;
    })}
   </svg></div>
   <p className="chain-chart-help">{production?'生产时段已放大，边缘箭头表示时段外事件。':'短工序按真实时长绘制，可切换“生产时段”放大。'} {showLinks?'仅显示所选节点的直接供需关联，关联不代表数量分配或唯一原因。':'选择节点查看数量、时间及来源；需要追踪上下游时，可开启“显示关联”。'}</p>
   <ol className="chain-mobile" aria-label="供需节点列表">{m.nodes.map(n=><li key={n.id}><Button aria-pressed={n.id===node?.id} className={n.id===node?.id?'selected':''} onClick={()=>setSelected(n.id)}><strong>{n.label}</strong><span>{n.itemId} · {amount(n.quantity,n.uom)}</span><small>{time(n.startAt)}{n.endAt!==n.startAt?' → '+time(n.endAt):''}</small></Button></li>)}</ol>
    {node&&<section className="chain-inspect" aria-label="供需节点详情"><header><div><small>{labels[node.kind]}</small><h3>{node.label}</h3></div><Badge>{node.kind==='DEMAND'?deliveryStatusLabel(node.status):node.status==='confirmed'?'已确认':node.status==='proposed'?'建议 · 未下达':node.status}</Badge></header>
    <Definition rows={[["物料",node.itemId],[node.kind==='STOCK'?'缓冲区净可用总量':node.kind==='DEMAND'?'计划满足数量':'完整任务数量',amount(node.quantity,node.uom)],...(node.kind==='PURCHASE'?[["预计物理到货",time(node.physicalAt)],["QC后预计可用",time(node.endAt)]] as [string,string][]:node.kind==='STOCK'?[["库存余额时点",time(node.startAt)]] as [string,string][]:node.kind==='DEMAND'?[["已排定部分预计",time(node.endAt)]] as [string,string][]:[["计划开始",time(node.startAt)],["计划结束",time(node.endAt)]] as [string,string][]) ]}/>
    {node.inventory&&<><Definition rows={[["物理库存",amount(node.inventory.physical,node.uom)],["质量冻结",amount(node.inventory.frozen,node.uom)],["其他不可用",amount(node.inventory.other,node.uom)]]}/><p>冻结及其他不可用量不参与生产。这里是规划期初余额，原生初始化日期不代表实际入库日期；未确定该需求的库存批次分配。</p></>}
    {node.kind==='PURCHASE'&&<p>确认采购尚未实际收货，物理到货不等于立即可用。日期为本方案的预计安排。</p>}
    {node.kind==='DEMAND'&&<p>计划满足不代表已经发货；未满足数量没有确定完成日期。</p>}
    <p className="chain-upstream">明确的原生上游：{selectedLinks.length?selectedLinks.map(l=>m.nodes.find(n=>n.id===l.from)?.label??'未解析引用').join('、'):'当前没有可绘制的直接上游引用'}。</p>
    {node.taskId&&<Button icon="fit" onClick={()=>onLocate(node.taskId!)}>在原排程中定位此工序</Button>}
    {!!node.materials?.length&&<details><summary>本任务物料事件 · {node.materials.length} 条</summary>{node.materials.map((e,i)=><div key={i}><p>{e.record.item} · {e.record.quantity} {unit(e.item_uom??'')} · {time(e.times?.flowdate)} · 余额 {e.record.onhand}</p><NativeSource entry={e}/></div>)}</details>}
    {!!node.resources?.length&&<details><summary>本任务资源占用 · {node.resources.length} 条</summary>{node.resources.map((e,i)=><div key={i}><p>{e.record.resource} · 资源单位 {e.record.quantity}，不等于订单分配量或瓶颈结论。</p><NativeSource entry={e}/></div>)}</details>}
    <details><summary>查看节点来源 · {node.evidence.length} 项</summary>{node.evidence.map((e,i)=><NativeSource key={i} entry={e}/>)}</details>
    {!!selectedLinks.length&&<details><summary>查看上游关联依据</summary>{selectedLinks.map(l=><NativeSource key={l.from} entry={l.source}/>)}</details>}
   </section>}
  </>}
  {!!unlinked.length&&<section className="chain-unlinked"><h3>本计划的其他确认采购</h3><p>以下供应尚未证明分配到所选需求，未画入该需求的供需连线。</p>{unlinked.map(p=><div key={p.reference}><strong>{p.reference} · {amount(p.quantity,p.uom)}</strong><p>{p.itemId} · 物理到货 {time(p.physicalAt)} · QC后可用 {time(p.availableAt)}</p></div>)}</section>}
  <p className="detail-note">原生供需引用不等于唯一根因，也不授予采购或生产执行许可。</p>
  <details><summary>计划与运行身份</summary><Definition rows={[["计划",planId],["企业数据版本",versionId],["运行",m.runId],["需求行",orderId],["结果摘要",view.result?.result_hash??'尚无'] ]}/></details>
 </section>;
}
