import type {ImportTask} from '../api/contracts';
import {Button,Icon} from '../components/Controls';
import {Checkbox} from 'antd';
import {Status} from '../components/WorkspaceUi';
import {reviewSections} from './importSummary';

export function ImportOverview({task,schema,groups,setGroups,onOpen,busy,onConfirm}:{
  task:ImportTask;schema:Record<string,string>;groups:string[];setGroups:(v:string[])=>void;
  onOpen:(group:string,sheet?:string)=>void;busy:boolean;onConfirm:()=>void;
}){
  const rows=reviewSections(task,schema),confirmed=rows.filter(r=>r.confirmed).length;
  const hints:Record<string,string>={mappings:'字段含义、单位与对应关系',orders:'产品、数量、客户与交期',production:'材料用量、工序与有效产能',inventory:'实存、可用数量、单位与预留',purchasing:'供应来源、提前期与在途采购',customers:'企业客户编码与真实身份'};
  return <section className="import-review" aria-labelledby="review-title">
    <div className="review-section-head"><div><h2 id="review-title">业务资料核对</h2><p>查看原始资料，核实关键事实后勾选确认。</p></div><span>{confirmed} / {rows.length} 类已确认</span></div>
    <form id="import-review-form" onSubmit={e=>{e.preventDefault();onConfirm()}}>
      <div className="review-table-scroll" tabIndex={0} aria-label="业务资料核对表，可横向滚动"><table className="review-table"><thead><tr><th className="review-select">勾选</th><th>核对事项</th><th>已识别原始资料</th><th>核对状态</th><th>操作</th></tr></thead><tbody>{rows.map(row=><tr key={row.id}>
        <td className="review-select"><Checkbox name="review-group" value={row.id} aria-label={'核对 '+row.label} disabled={busy} checked={groups.includes(row.id)} onChange={e=>setGroups(e.target.checked?[...groups,row.id]:groups.filter(x=>x!==row.id))}/></td>
        <th scope="row">{row.label}<small>{hints[row.id]??'核对业务关系与关键参数'}</small></th>
        <td>{row.sheets.length?<><span>{row.sheets.slice(0,2).map(s=>s.name).join('、')}{row.sheets.length>2?' 等 '+row.sheets.length+' 张原表':''}</span><small>{row.id==='mappings'?row.sheets.length+' 张原工作表':row.rawRows+' 行原始记录'}{row.id==='customers'?' · 客户映射另行核对':''}</small></>:<span className="review-missing">尚无已匹配的原表</span>}</td>
        <td><Status kind={row.confirmed?'success':'warning'}>{row.confirmed?'已确认':'待核对'}</Status></td>
        <td><Button className="review-open" onClick={()=>onOpen(row.id,row.sheets[0]?.name)}>{row.id==='customers'?'核对客户':'查看资料'}<Icon name="right"/></Button></td>
      </tr>)}</tbody></table></div>
    </form>
    <p className="review-data-note">表中数量为原工作表的记录行数，尚未合并为订单或汇总生产数量。</p>
  </section>;
}
