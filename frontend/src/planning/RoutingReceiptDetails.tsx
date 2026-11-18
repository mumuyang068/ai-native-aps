import type {Dict} from '../api/contracts';
import {qty,unit} from './planModel';
import {receiptQualityLabel} from './receiptProtectionModel';
import {formatSiteTime} from './timeRange';
/** Business view of the existing PO-bound receipt facts, without making new supply. */
export function RoutingReceiptDetails({receipts,purchases,timezone}:{receipts:Dict[];purchases:Dict[];timezone:string}){
 if(!receipts.length)return <p className="rc-note">本运行未列出实收明细，不能据此推断实收数量为零。</p>;
 return <div className="rc-table-scroll"><h4>已收原料与质量可用性</h4><table><thead><tr><th>实收单 / 原采购</th><th>物料</th><th>已收数量</th><th>物理实收</th><th>质量状态</th><th>可用时间</th></tr></thead><tbody>{receipts.map(r=>{const po=purchases.find(p=>p.id===r.po_id),quality=r.quality_status==='PENDING_QC'?'待质检':receiptQualityLabel[r.quality_status];return <tr key={r.po_id+':'+r.receipt_reference}><th>{r.receipt_reference}<small>{r.po_id}</small></th><td>{po?.item_id??'关联物料未核验'}</td><td>{qty(r.quantity)} {unit(r.uom)}</td><td>{r.physical_receipt_at?formatSiteTime(r.physical_receipt_at,timezone):'未知'}</td><td>{quality??'质量未核验'}</td><td>{r.quality_status==='FROZEN'?'未放行 · 不可用':!quality?'未核验':r.release_at?<>{r.quality_status==='PENDING_QC'?'预计质检后可用':'已放行可用'}<small>{formatSiteTime(r.release_at,timezone)}</small></>:'可用时间未知'}</td></tr>})}</tbody></table><p className="rc-note">已放行实收参与净开账；待检仅按声明的未来可用时点进入模型，冻结量不进入可用供应。采购原单状态与实收质量分别保留。</p></div>;
}
