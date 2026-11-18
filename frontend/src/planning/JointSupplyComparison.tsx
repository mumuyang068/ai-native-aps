import type {Dict} from '../api/contracts';
import {DataTable} from '../components/DataTable';
import {formatSiteTime} from './timeRange';

export function JointSupplyComparison({value,timezone}:{value:Dict;timezone:string}){
 const time=(v:string)=>formatSiteTime(v,timezone),base=value.base,candidate=value.candidate;
 const unit=(row:Dict)=>candidate.stock.find((r:Dict)=>r.item_id===(row.item??row.item_id)&&r.location_id===(row.location??row.location_id??row.destination))?.uom??'单位未提供';
 const stocks=candidate.stock.map((r:Dict)=>({...r,base:base.stock.find((b:Dict)=>b.item_id===r.item_id&&b.location_id===r.location_id)}));
 const documents=(kind:'purchases'|'distribution')=>[...base[kind].map((r:Dict)=>({...r,side:'基线'})),...candidate[kind].map((r:Dict)=>({...r,side:'候选'}))];
 return <section className="professional-policy-panel" aria-label="联合供应链方案比较"><h2 className="section-title">联合供应链变化</h2><p>同一企业版本；采购和调拨均为规划结果。运输成本无可信依据，不作经济性排名。</p>
 <div className="professional-policy-table" tabIndex={0} aria-label="地点库存比较，可横向滚动"><DataTable<Dict> rows={stocks} rowId={r=>r.item_id+'@'+r.location_id} columns={[{id:'object',title:'物料 / 地点',value:r=>r.item_id+' · '+r.location_id},{id:'min',title:'最低可用：基线 → 候选',value:r=>(r.base?.minimum_available??'未知')+' → '+(r.minimum_available??'未知')+' '+r.uom},{id:'end',title:'期末可用：基线 → 候选',value:r=>(r.base?.end_available??'未知')+' → '+(r.end_available??'未知')+' '+r.uom}]}/></div>
 <details open><summary>原生采购来源、数量及到货时点</summary><div className="professional-policy-table" tabIndex={0}><DataTable<Dict> rows={documents('purchases')} rowId={r=>r.side+r.reference} columns={[{id:'side',title:'方案',value:r=>r.side},{id:'object',title:'物料 / 地点 / 来源',value:r=>r.item+' · '+r.location+' · '+r.supplier},{id:'quantity',title:'数量 / 状态',value:r=>r.quantity+' '+unit(r)+' · '+r.status},{id:'time',title:'物理到货',value:r=>time(r.enddate)}]}/></div></details>
 <details open><summary>调拨发运与到达</summary><div className="professional-policy-table" tabIndex={0}><DataTable<Dict> rows={documents('distribution')} rowId={r=>r.side+r.reference} columns={[{id:'side',title:'方案',value:r=>r.side},{id:'object',title:'物料 / 路线',value:r=>r.item+' · '+r.origin+' → '+r.destination},{id:'quantity',title:'计划调拨量',value:r=>r.quantity+' '+unit(r)},{id:'start',title:'源地扣减',value:r=>time(r.startdate)},{id:'end',title:'目的地增加',value:r=>time(r.enddate)}]}/></div></details>
 <details><summary>来源单价与成本边界</summary><div className="professional-policy-table" tabIndex={0}><DataTable<Dict> rows={value.source_prices} rowId={r=>r.id} columns={[{id:'source',title:'来源 / 对象',value:r=>r.id+' · '+r.item_id+' · '+r.location_id},{id:'price',title:'模型单价',value:r=>r.unit_price+' '+r.currency+' / '+unit(r)},{id:'basis',title:'估值范围',value:()=> '不含税、运输及持有成本'}]}/></div></details>
 </section>;
}
