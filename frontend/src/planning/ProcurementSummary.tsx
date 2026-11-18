import type {Dict} from '../api/contracts';
import {Definition} from '../components/Controls';
import {formatSiteTime} from './timeRange';
import './procurement-summary.css';

export function ProcurementSummary({summary,validation,timezone}:{summary:Dict;validation:Dict;timezone:string}){
 const status=validation.status==='PASS'?'已核验列明的来源、批量、容量与可用时间':validation.status==='FAIL'?'采购约束未通过核验':'采购约束尚未完成核验';
 return <details className="condition-group procurement-summary"><summary>采购来源与模型支出 · PRIORITY</summary>
 <p>{status}</p>{summary.eligible_for_cost_comparison===false&&<p className="detail-note">当前计划未通过可行性检查，金额仅供分析，不能据此推荐执行。</p>}<Definition rows={[
 ['新增建议采购模型支出',`${summary.incremental_purchase_spend} ${summary.currency}`],
 ['已有采购承诺模型估值',`${summary.confirmed_purchase_commitment_model_value} ${summary.currency}`]]}/>
 <p className="detail-note">金额按资料中的来源单价估算，未计税费、运费及库存持有成本，不代表付款金额。先核对计划合法性，再比较支出。</p>
 {summary.capacity.map((s:Dict)=><section className="condition-resource" key={s.source_id}><h3>{s.source_id} · 优先级 {s.priority}</h3><p>本规划期合计占用 {s.used} / {s.quantity} {s.uom}，包含已有确认采购及新增建议。</p><p>最小采购量 {s.minimum_order_quantity}，采购倍数 {s.order_multiple}；质检等待 {s.qc_wait_seconds/3600} 小时。</p></section>)}
 <div className="condition-supply-table" tabIndex={0} aria-label="采购来源与到货明细，可横向滚动"><table><thead><tr><th>采购 / 来源</th><th>业务状态</th><th>数量 / 单价</th><th>物理到货</th><th>质检后可用</th></tr></thead><tbody>{summary.purchase_orders.map((p:Dict)=><tr key={p.reference}><td>{p.reference}<small>{p.source_id} · {p.supplier_id}</small></td><td>{p.status==='confirmed'?'已有确认采购':'新增采购建议'}</td><td>{p.quantity} {p.uom}<small>{p.unit_price} {p.currency}/{p.uom}</small></td><td>{formatSiteTime(p.physical_receipt_at,timezone)}</td><td>{formatSiteTime(p.available_at,timezone)}</td></tr>)}</tbody></table></div>
 </details>;
}
