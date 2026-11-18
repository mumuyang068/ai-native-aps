import {useState} from 'react';
import {Link} from 'react-router';
import {request} from '../api/client';
import {Button,Definition,Dialog,ErrorNote,Loading} from '../components/Controls';

type Side={odoo?:{company_id:number;stock_audit:string;future_operations:number;proposed_manufacture_count:number;native_business_audit:string;physical:{item_id:string;quantity:string;uom:string}[];manufacturing:{id:number;name:string;quantity:number;produced:number;stored:string;state:string}[]};version_id:string;plan_id:string|null;run_id:string|null;as_of:string;totals:{uom:string;open:string;planned:string;unplanned:string}[]};
type Comparison={lineage_versions?:string[];status:string;previous:Side;current:Side;demand_changes:{demand_id:string;classification:string;previous:unknown;current:unknown}[];limits:string[]};
export function CheckpointComparison({previous,current,connectorId='demo-garment',includeIntermediate=false}:{previous:string|null;current:string|null;connectorId?:string;includeIntermediate?:boolean}){
 const[open,setOpen]=useState(false),[busy,setBusy]=useState(false),[value,setValue]=useState<Comparison|null>(null),[error,setError]=useState('');
 async function read(){if(!previous||!current||busy)return;setOpen(true);setBusy(true);setValue(null);setError('');try{
  const result=await request<Comparison>('/v1/connectors/'+encodeURIComponent(connectorId)+'/comparison?'+new URLSearchParams({previous_version_id:previous,current_version_id:current,include_intermediate:String(includeIntermediate)}));
  if(result.previous.version_id!==previous||result.current.version_id!==current)throw new Error('比较版本身份不一致，请重新读取。');setValue(result);
 }catch(e){setError(e instanceof Error?e.message:'暂时无法读取检查点对照。')}finally{setBusy(false)}}
 return <><Button disabled={!previous||!current||busy} onClick={read}>对照上次检查点</Button>{open&&<Dialog title="经营检查点对照" size="840" onClose={()=>setOpen(false)}><ErrorNote error={error}/>{busy&&<Loading/>}{value&&<>
 <p>查看连续企业版本的经营变化。来源、开放需求和规划时钟均随检查点变化。</p>{value.lineage_versions&&value.lineage_versions.length>2&&<p>确认版本链：{value.lineage_versions.join(' → ')}。中间版本保留原有成功或失败记录；两侧分别使用自己的原生结果。</p>}
 {value.status==='NOT_RUN'&&<p role="status">部分检查点尚无成功运行；这里只显示已知结果，不将缺失结果填为零。</p>}
 <div className="connector-comparison-sides">{([['上次',value.previous],['本次',value.current]] as const).map(([label,side])=><section key={label}><h3>{label} · {side.as_of}</h3><Definition rows={[["企业版本",<Link to={'/data/'+side.version_id}>{side.version_id}</Link>],["计划",side.plan_id?<Link to={'/plans/'+side.plan_id+'?target=baseline'}>{side.plan_id}</Link>:'尚未创建'],["运行",side.run_id??'尚无成功运行']]}/>{side.totals.length?<table className="connector-table"><thead><tr><th>单位</th><th>开放需求</th><th>已计划</th><th>未排</th></tr></thead><tbody>{side.totals.map(t=><tr key={t.uom}><th>{t.uom}</th><td>{t.open}</td><td>{t.planned}</td><td>{t.unplanned}</td></tr>)}</tbody></table>:<p>结果未取得</p>}{side.odoo&&<><h4>Odoo 公司 {side.odoo.company_id} · 已确认执行与库存</h4><p>未来工序 {side.odoo.future_operations} 条 · 新制造建议 {side.odoo.proposed_manufacture_count} 笔</p><table className="connector-table"><thead><tr><th>MO</th><th>工单数量</th><th>已产出</th><th>已存放</th><th>状态</th></tr></thead><tbody>{side.odoo.manufacturing.map(m=><tr key={m.id}><td>{m.id} · {m.name}</td><td>{m.quantity}</td><td>{m.produced}</td><td>{m.stored}</td><td>{m.state}</td></tr>)}</tbody></table><ul>{side.odoo.physical.map(r=><li key={r.item_id}>{r.item_id}：{r.quantity} {r.uom}</li>)}</ul></>}</section>)}</div>
 <h3>需求变化 · {value.demand_changes.length} 项</h3><div className="connector-table-scroll"><table className="connector-table"><thead><tr><th>需求行</th><th>变化</th></tr></thead><tbody>{value.demand_changes.map(d=><tr key={d.demand_id}><td>{d.demand_id}</td><td>{({ADDED:'新增开放需求',CHANGED:'数量或计划变化',NO_LONGER_OPEN:'本版不再开放'} as Record<string,string>)[d.classification]??d.classification}</td></tr>)}</tbody></table></div>
 <p>“本版不再开放”须回到销售及发货来源核对，不表示订单取消；跨时点变化不作为算法优化收益。</p>
 </>}</Dialog>}</>;
}
