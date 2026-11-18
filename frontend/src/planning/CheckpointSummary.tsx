import type {Dict} from '../api/contracts';
import {formatSiteTime} from './timeRange';

export function CheckpointSummary({summary:s,validation:v,timezone}:{summary:Dict;validation:Dict;timezone:string}){
 const task=s.completed_task??{},labels:Record<string,string>={material_issued:'实际领料',operation_completed:'整批完工',receipt:'物理入账',wip_released:'明确放行',customer_shipped:'实际交付'};
 return <details className="condition-group procurement-summary"><summary>执行检查点与下一轮计划</summary>
  <p>{v.status==='PASS'?'已核对执行前缀与剩余计划的两段物料账':v.status==='FAIL'?'执行结转或剩余计划核验未通过':'两段物料账尚未完成核验'}</p>
  <p>{s.confirmation?.declaration==='SYNTHETIC_ACTUAL_EXECUTION'?'合成实际执行记录':'已确认的执行记录'} · {s.checkpoint_id}</p>
  <p>截止 {formatSiteTime(s.cutoff_at,timezone)}，事件序号 {s.cutoff_sequence}。同刻后续事件不自动计入。</p>
  <p><a href={'#/plans/'+s.parent_plan_id+'?target=baseline'}>查看原计划</a><br/>{s.parent_data_version_id}</p>
  <p>已完成工单 {task.reference}：{task.operation_id}，{task.quantity} {task.uom}。{formatSiteTime(task.actual_start_at,timezone)} 至 {formatSiteTime(task.actual_completed_at,timezone)}</p>
  <div className="condition-supply-table" tabIndex={0} aria-label="检查点前后库存，可横向滚动"><table><thead><tr><th>物料 / 单位</th><th>原净可用</th><th>结转物理量</th><th>质量冻结</th><th>其他不可用</th><th>结转净可用</th></tr></thead><tbody>{(s.after??[]).map((r:Dict)=><tr key={r.item_id}><td>{r.item_id}<small>{r.uom}</small></td><td>{s.before?.find((b:Dict)=>b.item_id===r.item_id)?.net_available}</td><td>{r.on_hand}</td><td>{r.quality_blocked}</td><td>{r.other_nonnettable}</td><td>{r.net_available}</td></tr>)}</tbody></table></div>
  <details><summary>查看已发生事件与来源</summary><div className="condition-supply-table" tabIndex={0} aria-label="已发生执行事件，可横向滚动"><table><thead><tr><th>序号 / 来源事件</th><th>已发生事实</th><th>时间</th><th>数量</th></tr></thead><tbody>{(s.historical_ledger??[]).map((e:Dict)=><tr key={e.event_id}><td>{e.sequence}<small>{e.event_id}<br/>{e.source_system} / {e.source_version}</small></td><td>{labels[e.kind]??e.kind}</td><td>{formatSiteTime(e.at,timezone)}</td><td>{e.payload.quantity??e.payload.quantity_delta} {e.payload.uom}</td></tr>)}</tbody></table></div></details>
  <p>原需求减去有凭据的实际交付：{(s.remaining_demands??[]).map((d:Dict)=><span key={d.order_line_id}>{d.order_line_id}：{d.original_quantity} − {d.actually_delivered} = {d.remaining_quantity} {d.uom}；</span>)}</p>
  <p className="detail-note">历史领料和入账已包含在新期初中，不再次作为未来事件加载。下一轮制造与交付建议见原明细；原生库存初始化日期不代表真实入库日期。</p>
 </details>;
}
