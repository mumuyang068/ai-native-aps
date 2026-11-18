import {useState} from 'react';
import type {Dict} from '../api/contracts';
import {Button,ErrorNote} from '../components/Controls';
import {forecastPeriodMatrix,forecastEditRequest} from './forecastPeriodModel';
import {reportObjectKey} from './timeBucketReportModel';
import './time-bucket-report.css';
type Props={versionId:string;rows:Dict[];selectedId:string;onSelect:(id:string)=>void;writable:boolean;canSave:boolean;busy:boolean;onSave:(body:{row_id:string;quantity:string})=>void};
export function ForecastPeriodEditor(p:Props){
 const [edit,setEdit]=useState<{key:string;quantity:string}|null>(null),[error,setError]=useState('');
 const key=p.versionId+'|'+p.selectedId,quantity=edit?.key===key?edit.quantity:'',matrix=forecastPeriodMatrix(p.rows,p.selectedId);
 const series=[...new Map(p.rows.map(r=>[reportObjectKey({...r,uom:r.uom??r.unit}),r])).values()];
 const selected=p.rows.find(r=>r.id===p.selectedId),metricLabels={baseline:'统计基线 · 只读',override:'人工覆盖',gross:'最终 Gross',consumed:'已抵扣 Consumed',net:'净预测 Net'};
 function save(){try{const body=forecastEditRequest(p.versionId,edit?.key.split('|')[0]??'',p.rows,p.selectedId,quantity);setError('');p.onSave(body)}catch(e){setError((e as Error).message)}}
 return <section className="forecast-period-editor" aria-label="预测原始期间编辑"><div className="bucket-toolbar"><label>产品 / 地点 / 客户<select aria-label="预测序列" value={matrix?reportObjectKey({...selected,uom:selected?.uom??selected?.unit}):''} onChange={e=>{const row=series.find(r=>reportObjectKey({...r,uom:r.uom??r.unit})===e.target.value);if(row){p.onSelect(row.id);setEdit(null);setError('')}}}>{series.map(r=><option key={reportObjectKey({...r,uom:r.uom??r.unit})} value={reportObjectKey({...r,uom:r.uom??r.unit})}>{r.item_id} · {r.location_id} · {r.customer_id} · {r.uom??r.unit}</option>)}</select></label><span>原始预测桶 · {matrix?.unit}</span></div>
 {matrix&&<div className="forecast-period-scroll" tabIndex={0} aria-label="预测指标与原始期间，可横向滚动"><table><thead><tr><th>指标 / {matrix.unit}</th>{matrix.buckets.map(b=><th key={b.id} scope="col">{b.label}<small>至 {b.end?.slice(0,10)}</small></th>)}</tr></thead><tbody>{Object.entries(metricLabels).map(([metric,label])=><tr key={metric}><th scope="row">{label}</th>{matrix.buckets.map(b=><td key={b.id}><button className="bucket-value" aria-pressed={p.selectedId===b.id} aria-label={label+' '+b.label} onClick={()=>{p.onSelect(b.id);setError('')}}>{matrix.values[metric][b.id]==null?(metric==='override'?'未覆盖':metric==='net'||metric==='consumed'?'待抵扣':'未知'):String(matrix.values[metric][b.id])}</button></td>)}</tr>)}</tbody></table></div>}
 {selected&&<fieldset disabled={!p.writable||p.busy} className="forecast-single-edit"><legend>{selected.bucket_start?.slice(0,10)} — {selected.bucket_end?.slice(0,10)} · 人工覆盖</legend><label>覆盖后的 Gross（{selected.uom??selected.unit}）<input aria-label="所选原始期间覆盖数量" inputMode="decimal" value={quantity} onChange={e=>setEdit({key,quantity:e.target.value})}/></label><Button className="primary" disabled={!p.canSave||quantity===''} onClick={save}>保存该期间为新版本</Button><Button disabled={quantity===''} onClick={()=>{setEdit(null);setError('')}}>撤销未保存修改</Button><small>填写下方修改人和理由后保存。保存创建新版本；原统计基线保留，抵扣与原生规划需单独执行。</small></fieldset>}
 <ErrorNote error={error}/></section>;
}
