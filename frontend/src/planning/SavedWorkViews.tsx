import {useState} from 'react';
import {Button,ErrorNote} from '../components/Controls';
import type {ReportScope} from './reportModel';
import {captureWorkView,restoreWorkView,readWorkViews,writeWorkViews,type WorkView} from './workViewModel';
export function SavedWorkViews({scope,search,onRestore}:{scope:ReportScope;search:URLSearchParams;onRestore:(s:URLSearchParams)=>void}){
 const[initial]=useState(()=>readWorkViews(localStorage)),[views,setViews]=useState<WorkView[]>(initial.views),[name,setName]=useState(''),[chosen,setChosen]=useState(''),[error,setError]=useState(initial.error),[message,setMessage]=useState('');
 function save(){try{const v=captureWorkView(name,search,scope),next=[v,...views.filter(x=>x.name!==v.name)].slice(0,30);writeWorkViews(localStorage,next);setViews(next);setChosen(v.id);setMessage('已保存到本机浏览器。');setError('')}catch(e:any){setError(e.message)}}
 return <div className="saved-workviews" aria-label="个人专业作业视图"><div className="workview-controls"><label>个人工作视图<select aria-label="已保存工作视图" value={chosen} onChange={e=>setChosen(e.target.value)}><option value="">选择已保存视图</option>{views.map(v=><option key={v.id} value={v.id}>{v.name}</option>)}</select></label><Button disabled={!chosen} onClick={()=>{const v=views.find(v=>v.id===chosen);if(v){const r=restoreWorkView(v,scope);onRestore(r.search);setMessage(r.stale?'已恢复筛选与列；原对象属于其他计划、版本或运行，未恢复旧对象。':'已恢复当前运行的筛选、列与对象。')}}}>恢复视图</Button><label>另存名称<input aria-label="工作视图名称" maxLength={80} value={name} onChange={e=>setName(e.target.value)} placeholder="例如：采购待核对"/></label><Button onClick={save} disabled={!name.trim()}>保存当前视图</Button></div><ErrorNote error={error}/>{message&&<p role="status">{message}</p>}</div>
}
