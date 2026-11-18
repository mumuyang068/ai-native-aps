import type {Dict} from '../api/contracts';
import {siteInputToIso} from './analysisModel';
import {formatSiteTime} from './timeRange';
export type EditorTime={mode:'START_ANCHOR'|'END_ANCHOR'|'WINDOW';start_at?:string;end_at?:string};
export type EditorRequest={taskId:string;time?:EditorTime;nonce:number};
export type TaskFields={mode:EditorTime['mode'];start:string;end:string;quantity:string;resource:string;recipe:string};
const equalTime=(a:string,b:string)=>Date.parse(a)===Date.parse(b);
const decimal=(v:string)=>{if(!/^\d+(?:\.\d+)?$/.test(v))throw Error('数量必须为非负十进制数。');return v.replace(/^0+(?=\d)/,'').replace(/(\.\d*?)0+$/,'$1').replace(/\.$/,'')};
/** Form values describe an intent only. The server owns batch, qualification and feasibility checks. */
export function taskEditFromFields(task:Dict,f:TaskFields,timezone:string):Dict{
 const changes:Dict={},time:EditorTime={mode:f.mode};
 if(f.mode!=='END_ANCHOR'){const start=siteInputToIso(f.start,timezone);if(!start)throw Error('开始时间无效或存在时区歧义。');time.start_at=start}
 if(f.mode!=='START_ANCHOR'){const end=siteInputToIso(f.end,timezone);if(!end)throw Error('结束时间无效或存在时区歧义。');time.end_at=end}
 if(time.start_at&&time.end_at&&Date.parse(time.start_at)>=Date.parse(time.end_at))throw Error('结束时间必须晚于开始时间。');
 if((time.start_at&&!equalTime(time.start_at,task.start_at))||(time.end_at&&!equalTime(time.end_at,task.end_at)))changes.time=time;
 if(f.quantity!==''&&decimal(f.quantity)!==decimal(String(task.root_quantity??task.quantity)))changes.quantity=f.quantity;
 if(f.resource&&!(task.resources??[]).includes(f.resource))changes.resource_id=f.resource;
 if(f.recipe&&f.recipe!==task.recipe_id)changes.recipe_id=f.recipe;
 return {kind:'task',reference:task.id,changes};
}
export function mergeTaskTime(edits:Dict[],reference:string,time:EditorTime):Dict[]{
 const previous=edits.find(e=>e.kind==='task'&&e.reference===reference),value={kind:'task',reference,changes:{...previous?.changes,time}};
 return previous?edits.map(e=>e===previous?value:e):[...edits,value];
}
export const demandActionLabel:Record<string,string>={UNPLAN:'撤销建议排程',FORWARD:'正排 · 尽早供应',BACKWARD:'倒排 · 围绕交期'};
export function editorTaskLabel(task:Dict,timezone:string):string{return [task.operation_id??task.native_operation,task.quantity,formatSiteTime(task.start_at,timezone),(task.resources??[]).join('、'),'供应 '+(task.root_native_reference??String(task.id).slice(-12))].join(' · ')}
/** UI operation identity and server edit selector are distinct, with an explicit one-to-one binding. */
export function editorTaskReference(options:Dict,id:string):string|null{const matches=(options.proposal_tasks??[]).filter((t:Dict)=>t.id===id||t.operation_task_id===id);return matches.length===1?matches[0].id:null}
export function editorIntentText(e:Dict,timezone:string):string{
 const stamp=(v:string)=>formatSiteTime(v,timezone);
 if(e.kind==='task'){const c=e.changes??{},t=c.time;return [t?(t.mode==='WINDOW'?'窗口 '+stamp(t.start_at)+' → '+stamp(t.end_at):t.mode==='END_ANCHOR'?'完成锚点 '+stamp(t.end_at):'开始锚点 '+stamp(t.start_at)):'',c.quantity!==undefined?'供应数量 '+c.quantity:'',c.resource_id?'设备 '+c.resource_id:'',c.recipe_id?'工艺 '+c.recipe_id:''].filter(Boolean).join(' · ')||'无变化'}
 if(e.kind==='demand')return (demandActionLabel[e.action]??e.action)+' · '+(e.demand_ids??[]).join('、');
 if(['window','proposed_window'].includes(e.kind))return stamp(e.start_at);
 if(e.kind==='machine')return '设备 '+e.resource_id;
 if(e.kind==='optimized_schedule')return '联合调整 '+(e.assignments??[]).length+' 道工序';
 if(e.kind==='native_strategy')return e.solver+' · '+e.machine_search;
 return (e.order??[]).join(' → ');
}
