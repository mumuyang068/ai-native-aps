import type {Dict} from '../api/contracts';
import {formatSiteTime} from './timeRange';
import './procurement-summary.css';

export function WipSummary({summary,validation,timezone}:{summary:Dict;validation:Dict;timezone:string}){
 const status=validation.status==='PASS'?'已核对本范围内的物料数量、可用时间与两级制造事件':validation.status==='FAIL'?'物料约束核验未通过，当前结果不能作为可执行计划':validation.status==='UNSUPPORTED'?'新增采购尚未认证，当前结果不能作为可执行供给承诺':'物料约束尚未完成核验';
 const purchases=(summary.inventory??[]).some((s:Dict)=>s.future_purchase_receipts!==undefined);
 return <details className="condition-group procurement-summary"><summary>独立半成品与物料平衡</summary><p>{status}</p>
 {summary.stage_endpoint&&<p>{summary.stage_endpoint.label}。本次结果用于核对阶段需求，不含后续成衣质检与包装。</p>}
 {(summary.unvalidated_purchases??[]).length>0&&<p>未认证采购建议：{summary.unvalidated_purchases.map((p:Dict)=>p.reference).join('、')}。采购建议不等于已确认供应。</p>}
 <p className="detail-note">质量冻结和其他不可用量不参与生产。历史制造不再次计入；以下期初是已入账余额，原生初始化日期不代表真实入库日期。</p>
 <div className="condition-supply-table" tabIndex={0} aria-label="半成品与物料数量，可横向滚动"><table><thead><tr><th>物料 / 单位</th><th>物理期初</th><th>质量冻结</th><th>其他不可用</th><th>净可用期初</th>{purchases&&<th>采购供应入流</th>}<th>未来制造入库</th><th>未来消耗</th><th>期末净可用</th></tr></thead><tbody>{(summary.inventory??[]).map((s:Dict)=><tr key={s.item_id}><td>{s.item_id}<small>{s.item_id===summary.stage_endpoint?.item_id?'合成缝后工序终点':s.kind==='SEMI'?'独立半成品':s.kind==='RAW'?'原材料':'成品'} · {s.uom}</small></td><td>{s.physical_opening}</td><td>{s.quality_blocked}</td><td>{s.other_nonnettable}</td><td>{s.net_available_opening}</td>{purchases&&<td>{s.future_purchase_receipts??'0'}</td>}<td>{s.future_production}</td><td>{s.future_consumption}</td><td>{s.ending_net_available}</td></tr>)}</tbody></table></div>
 <details><summary>查看物料时间明细</summary><div className="condition-supply-table" tabIndex={0} aria-label="物料时间事件，可横向滚动"><table><thead><tr><th>物料 / 原生任务</th><th>时间</th><th>收发数量</th><th>净可用余额</th></tr></thead><tbody>{(summary.material_events??[]).map((e:Dict,i:number)=><tr key={i}><td>{e.item_id}<small>{e.reference}</small></td><td>{formatSiteTime(e.at,timezone)}</td><td>{e.quantity}</td><td>{e.net_available_balance}</td></tr>)}</tbody></table></div></details>
 </details>;
}
