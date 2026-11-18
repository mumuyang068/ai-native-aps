import type {Dict} from '../api/contracts';
import type {ReportScope} from './reportModel';
export type CapacityScope={planId:string;versionId:string;runId:string;baselineRunId:string;target:string;sourceSha256:string;snapshotSha256:string};
/** URL view state is scoped to the same four identities as the authoritative report. */
export function routingScopeKey(scope:ReportScope){return JSON.stringify({planId:scope.planId,versionId:scope.versionId,runId:scope.runId,target:scope.target})}
export function routingScopeMatches(value:string|null,scope:ReportScope){
 try{const saved=JSON.parse(value??'null');return !!saved&&['planId','versionId','runId','target'].every(k=>saved[k]===scope[k as keyof ReportScope])}catch{return false}
}
export function routingTaskSelection<T extends Dict>(input:Dict,tasks:T[],assignments:Dict[],id:string,resourceId?:string){
 const task=tasks.find(t=>t.id===id);if(!task)return null;
 const actual=assignments.find(a=>String(a.native_reference)===id.split(':').at(-1));
 const alternatives=operationAlternatives(input,task.operation_id);
 return {group:String(task.manufacture_id),task:id,resource:resourceId??actual?.machine_id??task.resource_id??'',machine:alternatives.some(a=>a.resource.id===actual?.machine_id)?String(actual?.machine_id):''};
}
/** Reject a requested foreign/missing object; never replace it with the first task's proof. */
export function routingViewContext<T extends Dict>(scope:ReportScope,input:Dict,allTasks:T[],assignments:Dict[],search:URLSearchParams){
 const scoped=routingScopeMatches(search.get('rc_scope'),scope),hasObject=!!(search.get('rc_task')||search.get('rc_group')),foreign=(search.has('rc_scope')||hasObject)&&!scoped;
 let issue=foreign?'保存的工序上下文不属于当前计划、版本、运行或方案，未恢复旧对象与来源。':'';
 const tasks=allTasks.filter(t=>t.id.startsWith(scope.runId+':')),groups=routingGroups(tasks,assignments),query=scoped?search.get('rc_query')??'':'';
 const filtered=groups.filter(g=>!query||JSON.stringify(g.tasks.map(t=>[t.operation_id,t.itemId,t.externalRef])).toLowerCase().includes(query.toLowerCase()));
 const requestedTask=scoped?search.get('rc_task'):null,requestedGroup=scoped?search.get('rc_group'):null;
 const explicit=tasks.find(t=>t.id===requestedTask),parent=groups.find(g=>g.id===requestedGroup);
 const invalid=!!((requestedTask&&!explicit)||(requestedGroup&&!parent)||(explicit&&parent&&explicit.manufacture_id!==parent.id));
 if(invalid)issue='指定工序或制造父任务不属于当前运行，未显示其它工序作为替代来源。';
 const group=explicit?groups.find(g=>g.id===explicit.manufacture_id):parent??filtered.find(g=>g.tasks.length>1)??filtered[0]??groups[0];
 const task=!foreign&&!invalid?(explicit??group?.tasks[0]):undefined,actual=task?assignments.find(a=>String(a.native_reference)===task.id.split(':').at(-1)):undefined;
 const requestedResource=scoped?search.get('rc_resource')??search.get('rc_machine'):null;
 const knownResource=!requestedResource||(input.resources??[]).some((r:Dict)=>r.id===requestedResource);
 if(!knownResource)issue='指定资源不属于当前业务版本，未借用其它资源的来源。';
 const rid=task&&knownResource?(requestedResource??actual?.machine_id??task.resource_id):undefined;
 const proposed=tasks.filter(t=>t.origin!=='EXISTING'),extent=proposed.length?proposed:tasks;
 const defaultStart=foreign?input.planning_at:extent.reduce((a,t)=>Date.parse(t.start_at)<Date.parse(a)?t.start_at:a,extent[0]?.start_at??input.planning_at);
 const defaultEnd=foreign?input.horizon.end_at:extent.reduce((a,t)=>Date.parse(t.end_at)>Date.parse(a)?t.end_at:a,extent[0]?.end_at??input.horizon.end_at);
 const from=scoped?search.get('rc_from'):null,to=scoped?search.get('rc_to'):null,start=from??defaultStart,end=to??defaultEnd;
 const validRange=Number.isFinite(Date.parse(start))&&Number.isFinite(Date.parse(end))&&Date.parse(start)<Date.parse(end)&&Date.parse(start)>=Date.parse(input.planning_at)&&Date.parse(end)<=Date.parse(input.horizon.end_at);
 if(!validRange)issue='保存的时间范围无效或超出当前规划期，已恢复当前运行的可核对范围。';
 return {groups,filtered,group,tasks:group?.tasks??[],task,actual,rid,query,onlyGroup:!scoped||search.get('rc_only')!=='0',start:validRange?start:defaultStart,end:validRange?end:defaultEnd,issue,scoped};
}
export function routingObjectSource<T extends Dict>(scope:ReportScope,task:T|undefined,assignments:Dict[],analysis:Dict|undefined,resourceId?:string,operationValidation?:Dict):Dict{
 const identity={plan_id:scope.planId,data_version_id:scope.versionId,run_id:scope.runId,target:scope.target};
 const same=!!analysis?.identity&&Object.entries(identity).every(([k,v])=>analysis.identity[k]===v);
 const actual=task&&task.id.startsWith(scope.runId+':')?assignments.find(a=>String(a.native_reference)===task.id.split(':').at(-1)):undefined;
 const resource=same?analysis!.resource_analysis:undefined,validation=resource?.validation??{status:'NOT_CHECKED'},evidenceStatus=resource?.status??'NOT_CHECKED';
 const failed=same&&(validation.status==='FAIL'||operationValidation?.status==='FAIL');
 if(!same||!actual)return {status:failed?'FAIL':'NOT_CHECKED',object_status:'NOT_CHECKED',identity,resource_evidence_status:evidenceStatus,resource_validation:validation,loads:[],loads_verified:false,reason:failed?'当前运行已有资源硬约束违规，且所选工序来源不完整；未以缺证覆盖违规。':'没有当前运行与所选工序的完整来源，未使用其它工序证据兜底。'};
 const loads:Dict[]=Array.isArray(resource?.intervals)?resource.intervals.filter((l:Dict)=>l.operation_task_id===task!.id):[],resources:Dict[]=Array.isArray(resource?.resources)?resource.resources:[];
 const required=[actual.machine_id,actual.operator_id],complete=required.every(id=>typeof id==='string'&&resources.some(r=>r.id===id&&r.event_status==='PRESENT')&&loads.some(l=>l.resource_id===id));
 const certified=!failed&&evidenceStatus==='PRESENT'&&validation.status==='PASS'&&(!operationValidation||operationValidation.status==='PASS')&&complete;
 const unavailable=['UNSUPPORTED','NOT_COLLECTED','UNREADABLE','INVALID_EVIDENCE'].includes(evidenceStatus);
 return {status:failed?'FAIL':unavailable?evidenceStatus:certified?'PRESENT':'NOT_CHECKED',object_status:'PRESENT',identity,task,native_assignment:actual,investigated_resource_id:resourceId??null,
  resource_evidence_status:evidenceStatus,resource_validation:validation,operation_validation:operationValidation??null,evidence_errors:resource?.evidence_errors??{},
  loads:certified?loads:[],loads_verified:certified,evidence_files:analysis!.evidence_files??[],
  ...(certified?{}:{reason:failed?'资源或工序已记录硬约束违规；对象记录存在不代表业务合法。':'工序身份记录存在，但该工序的设备与人员负荷证据未完整核验；未展示可信负荷，不能解释为零占用。'})};
}
export function routingGroups<T extends Dict>(tasks:T[],assignments:Dict[]):{id:string;owner:string|null;tasks:T[]}[]{
 const owners=new Map(assignments.map(a=>[String(a.native_reference),a.owner]));const groups=new Map<string,{id:string;owner:string|null;tasks:T[]}>();
 for(const t of tasks){const owner=owners.get(t.id.split(':').at(-1)!)??null;const id=t.manufacture_id;
  if(!groups.has(id))groups.set(id,{id,owner,tasks:[]});groups.get(id)!.tasks.push(t)}
 return [...groups.values()].map(g=>({...g,tasks:g.tasks.slice().sort((a:Dict,b:Dict)=>Date.parse(a.start_at)-Date.parse(b.start_at)||a.id.localeCompare(b.id))}));
}
/** Qualification belongs to the selected operation, never the first global machine pool. */
export function operationAlternatives(input:Dict,operationId:string):{resource:Dict;qualification:Dict|undefined;qualified:boolean;required:string}[]{
 const op=(input.routing_revisions??[]).flatMap((r:Dict)=>r.operations??[]).find((o:Dict)=>o.id===operationId);
 if(!op)return [];
 const pool=(input.resources??[]).find((r:Dict)=>r.id===op.resource_id&&r.role==='MACHINE_POOL');
 if(!pool)return [];
 return input.resources.filter((r:Dict)=>r.role==='MACHINE'&&r.pool_id===pool.id).map((resource:Dict)=>{const qualification=(input.resource_skills??[]).find((s:Dict)=>s.resource_id===resource.id&&s.skill_id===op.machine_skill_id&&s.priority>0&&Date.parse(s.effective_from)<=Date.parse(input.planning_at)&&Date.parse(s.effective_to)>=Date.parse(input.horizon.end_at));return {resource,qualification,qualified:!!qualification,required:op.machine_skill_id}});
}
export function qualifiedAlternatives(input:Dict){const pool=(input.resources??[]).find((r:Dict)=>r.role==='MACHINE_POOL');const op=(input.routing_revisions??[]).flatMap((r:Dict)=>r.operations??[]).find((o:Dict)=>o.resource_id===pool?.id);return op?operationAlternatives(input,op.id):[]}
export function capacityReturn(previous:URLSearchParams,target:string){const p=new URLSearchParams(previous);p.set('target',target);p.set('analysis','manufacturing');for(const k of ['analysis_task','analysis_event','mo_task'])p.delete(k);if(previous.get('target')!==target)for(const k of ['rc_scope','rc_task','rc_group','rc_demand'])p.delete(k);return p}
export function verifyCapacityPreview(value:Dict,scope:CapacityScope,body:Dict){const i=value?.identity,c=value?.changes;
 if(!i||i.plan_id!==scope.planId||i.data_version_id!==scope.versionId||i.run_id!==scope.runId||i.baseline_run_id!==scope.baselineRunId||i.target!=='baseline'||i.profile!=='routing-alternate-capacity-v1'||i.source_sha256!==scope.sourceSha256||i.snapshot_sha256!==scope.snapshotSha256||!/^[a-f0-9]{64}$/.test(value.fingerprint??'')||value.engine_executed!==false||value.calculation_submitted!==false||value.simulation_only!==true||c?.action!=='simulate_resource_capacity'||c?.execution_authorized!==false||c?.simulation_only!==true||c?.reason!==body.reason||c.conditions?.resource_id!==body.resource_id||['start_at','end_at'].some(k=>!Number.isFinite(Date.parse(c.conditions?.[k]))||Date.parse(c.conditions[k])!==Date.parse(body[k]))||c.resource_change?.resource_id!==body.resource_id||!(Number(c.resource_change?.removed_seconds)>0))throw Error('当前设备预览的身份、条件或模拟边界不一致，请重新核对。');return value;
}
export function verifyCapacityDraft(value:Dict,scope:CapacityScope,body:Dict,preview:Dict){
 const replay={...value,calculation_submitted:false,changes:value.draft?.changes};verifyCapacityPreview(replay,scope,body);
 if(value.fingerprint!==preview.fingerprint||value.run?.state!=='NOT_RUN'||value.calculation_submitted!==false||!value.draft?.id||value.draft.run_id!==null||value.draft.state!=='DRAFT'||JSON.stringify(value.draft.changes.conditions)!==JSON.stringify(preview.changes.conditions)||value.draft.changes.confirmation?.fingerprint!==preview.fingerprint)throw Error('保存结果不是本次已核对条件的NOT_RUN草案，请刷新核对。');return String(value.draft.id);
}
