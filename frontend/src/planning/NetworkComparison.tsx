import {useMemo,useState} from 'react';
import type {Dict,PlanView} from '../api/contracts';
import {PlanningChart} from '../components/PlanningChart';
import {analysisUnit} from './analysisModel';
import {demandComparisonOption,networkDemandChartRows} from './ixChartModel';
import {qty,businessLabel} from './planModel';
import {formatSiteTime} from './timeRange';
import {networkEvidence} from './networkSupplyModel';
export function NetworkComparison({base,view}:{base:PlanView;view:PlanView}){const b=base.interpretation?.distribution_validation,c=view.interpretation?.distribution_validation,tz=view.context?.timezone??'Asia/Shanghai',time=(v:string)=>v?formatSiteTime(v,tz):'未知';
 const model=useMemo(()=>networkDemandChartRows(base,view),[base,view]),[selection,setSelection]=useState<{identity:string;id:string}|null>(null);
 const groups=useMemo(()=>[...new Set(model.rows.map(r=>r.uom))].map(uom=>({uom,rows:model.rows.filter(r=>r.uom===uom),option:demandComparisonOption(model.rows.filter(r=>r.uom===uom),uom)})),[model]);
 const identity=JSON.stringify([base.context?.enterprise_source,base.run?.solve_run_id,view.run?.solve_run_id,base.result?.result_hash,view.result?.result_hash]);
 const selected=selection?.identity===identity?selection.id:'',setSelected=(id:string)=>setSelection({identity,id});
 const routes:Dict[]=b?.summary?.routes??[],locations:Dict[]=b?.summary?.locations??[];
 function routeValues(v:PlanView,id:string){if(!v.result)return '未计算';const rows=(v.result.distribution_suggestions as Dict[]??[]).filter(d=>d.lane_id===id);return rows.length?rows.map(d=><div key={d.id}>{qty(d.quantity)} 米<small>{time(d.ship_at)} 发运 → {time(d.receipt_at)} 到达</small></div>):'无调拨建议'}
 return <section className="net-comparison" aria-label="跨地点方案业务并读"><h3>同计划基线与候选 · 地点和路线并读</h3><p>基线：{businessLabel(base.interpretation?.business_status)}；候选：{view.result?businessLabel(view.interpretation?.business_status):'尚未计算'}。这里只汇总同路线结果，不按运行内编号匹配两笔调拨，不推断需求专属分配。</p>
 <div className="net-table-scroll" tabIndex={0}><table><thead><tr><th>路线 / 地点</th><th>原始基线</th><th>当前候选</th></tr></thead><tbody>{routes.map(r=><tr key={r.id}><th>{r.origin_location_id} → {r.destination_location_id}<small>运输提前期</small></th><td>{r.lead_time_seconds/3600} 小时{routeValues(base,r.id)}</td><td>{c?.summary?.routes?.find((x:Dict)=>x.id===r.id)?.lead_time_seconds/3600||'未计算'} 小时{routeValues(view,r.id)}</td></tr>)}{locations.map(l=><tr key={l.location_id}><th>{l.location_id}<small>期末净可用 / 过程最低</small></th><td>{networkEvidence(b)?qty(l.ending)+' / '+qty(l.minimum)+' 米':'未核验'}</td><td>{networkEvidence(c)?(()=>{const q=c.summary.locations.find((x:Dict)=>x.location_id===l.location_id);return q?qty(q.ending)+' / '+qty(q.minimum)+' 米':'缺少该地点证据'})():'未核验'}</td></tr>)}</tbody></table></div>
 {model.status==='PRESENT'?groups.map(group=><div key={identity+'|'+group.uom}><h4>需求行计划满足与缺量 · {analysisUnit(group.uom)}</h4><PlanningChart identityKey={identity+'|'+group.uom} label={'同计划需求行方案比较，单位'+analysisUnit(group.uom)} option={group.option} selectedEventId={selected} onPick={id=>{setSelected(id);document.getElementById('ix-demand-comparison-'+encodeURIComponent(id))?.scrollIntoView({block:'nearest'})}}/><div className="net-table-scroll" tabIndex={0}><table className="ix-comparison-table"><thead><tr><th>需求行</th><th>基线计划满足</th><th>候选计划满足</th><th>基线尚未满足</th><th>候选尚未满足</th></tr></thead><tbody>{group.rows.map(row=><tr id={'ix-demand-comparison-'+encodeURIComponent(row.id)} key={row.id} aria-selected={selected===row.id} tabIndex={0} onClick={()=>setSelected(row.id)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();setSelected(row.id)}}}><th>{row.id}</th>{[row.before,row.after,row.shortBefore,row.shortAfter].map((q,i)=><td key={i}>{q===null?'未知':q+' '+analysisUnit(row.uom)}</td>)}</tr>)}</tbody></table></div></div>):<p className="net-note">{model.reason}</p>}
 <p className="net-note">已确认采购、冻结库存和原需求保持原值。欠量没有已验证的完成日期；未核对实际执行、运输能力、成本或订单逐笔分配。</p></section>;
}
