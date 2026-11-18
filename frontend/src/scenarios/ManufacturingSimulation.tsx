import type {Dict} from '../api/contracts';
import {formatSiteTime} from '../planning/timeRange';
import {qty,unit} from '../planning/planModel';

export function ManufacturingSimulation({diff,timezone,units}:{diff:Dict;timezone:string;units:Map<string,string>}){
 if(!diff.manufacturing_simulation)return null;
 const time=(v:string|undefined)=>v?formatSiteTime(v,timezone):'未核验';
 return <section aria-label="已有工单调整模拟"><h2 className="section-title">已有工单调整模拟 · 待业务确认</h2><p>允许在隔离情景中考虑此窗口，不代表企业规则允许调整。未获得实际执行授权，不可据此下发或发布。</p><p>本次仅指定一个新窗口；全局禁止自动提前其他 approved 工单。实际开工状态仍需核实。</p><table><thead><tr><th>已有工单 / 状态</th><th>原始基线窗口</th><th>模拟窗口</th><th>变化范围</th></tr></thead><tbody>{diff.existing_manufacturing.map((r:Dict)=><tr key={r.reference}><th>{r.reference}<small>{r.base?.source_status} → {r.candidate?.source_status}</small></th><td>{time(r.base?.start_at)}<small>{time(r.base?.end_at)}</small></td><td>{time(r.candidate?.start_at)}<small>{time(r.candidate?.end_at)}</small></td><td>{r.selected?'指定工单的窗口假设':r.changed?'出现未预期变化，请核对':'承诺保持不变'}</td></tr>)}</tbody></table>
 <h2 className="section-title">新增制造建议变化</h2><p>按产品和配方汇总；不同运行复用的原生编号不作为同一工单移动的证据。</p><table><thead><tr><th>产品 / 配方</th><th>基线建议量</th><th>候选建议量</th></tr></thead><tbody>{diff.manufacture_groups.map((r:Dict)=><tr key={[r.item_id,r.location_id,r.recipe_id].join('|')}><th>{r.item_id}<small>{r.recipe_id}</small></th><td>{qty(r.base_quantity)} {unit(r.uom??units.get(r.item_id)??'单位未提供')}</td><td>{qty(r.candidate_quantity)} {unit(r.uom??units.get(r.item_id)??'单位未提供')}</td></tr>)}</tbody></table>
 <h2 className="section-title">期末计划库存变化</h2>{diff.inventory_comparison_status==='NOT_CHECKED'?<p>缺少已核验的物料事件，未比较期末库存。</p>:<table><thead><tr><th>物料</th><th>基线期末量</th><th>候选期末量</th><th>变化</th></tr></thead><tbody>{diff.inventory_balances.map((r:Dict)=><tr key={[r.item_id,r.location_id].join('|')}><th>{r.item_id}</th><td>{qty(r.base_quantity)} {unit(r.uom??units.get(r.item_id)??'单位未提供')}</td><td>{qty(r.candidate_quantity)} {unit(r.uom??units.get(r.item_id)??'单位未提供')}</td><td>{qty(r.quantity_delta)}</td></tr>)}</tbody></table>}</section>;
}
