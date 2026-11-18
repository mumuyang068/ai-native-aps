import {expect,it,vi} from 'vitest';
import {activeTask,assistantDraftKey,assistantRequest,AssistantRequestError,assistantViewKey,blocksContinuation,businessLink,candidateLink,candidateStatusSource,confirmationSummary,confirmationUsable,eventLabel,eventStatus,readAssistantDraft,startTaskBody,taskBelongs,taskHandoffText,taskStatusLabel,usesExternalAgent,type AssistantIdentity,type AssistantTask} from './assistantModel';
const identity:AssistantIdentity={plan_id:'PLAN-1111111111111111',data_version_id:'DATA-1',baseline_run_id:'API-1111111111111111',run_id:'API-1111111111111111',target:'baseline'};
it('distinguishes a persisted submission receipt from a later exact status check',()=>{
 const submitted={plan_id:identity.plan_id,draft_id:'DRAFT-a',state:'QUEUED',business_audit:'NOT_CHECKED'};
 expect(candidateStatusSource(submitted)).toBe('提交回执');
 expect(candidateStatusSource({...submitted,state:'SUCCEEDED',status_call_id:'CALL-a',status_checked_at:'2026-10-30T05:00:00Z'})).toBe('最近核对');
 expect(submitted.state).toBe('QUEUED');
});
const task={id:'TASK-1',identity,mode:'local_codex',session_id:'a'.repeat(32),state:'WAITING_CONFIRMATION',revision:2,pending_confirmation:{id:'CONFIRM-1',nonce:'nonce-owned-by-server',scope:{...identity,target:'DRAFT-2222222222222222',run_id:'API-2222222222222222'},expires_at:'2026-10-26T11:00:00Z'}} as AssistantTask;
it('gives URL task switches distinct mounted views and write-response ownership',()=>{
 const a=assistantViewKey(identity,task.session_id,'TASK-a2a607a6928a4b61'),b=assistantViewKey(identity,task.session_id,'TASK-6af32ce3ce084ca4');
 expect(a).not.toBe(b);expect(assistantViewKey(identity,task.session_id,null)).not.toBe(a);expect(assistantViewKey(identity,task.session_id,null)).not.toBe(b);
 expect(assistantViewKey(identity,task.session_id,'TASK-a2a607a6928a4b61')).toBe(a);
});
it('keeps each task and the new-question slot drafts separate across closing and reopening',()=>{
 const data=new Map<string,string>(),storage={getItem:(key:string)=>data.get(key)??null},session=task.session_id;
 data.set('planning-assistant-task:'+identity.plan_id+'|'+session,JSON.stringify('TASK-A'));
 data.set(assistantDraftKey(identity.plan_id,session,'TASK-A'),JSON.stringify('A的未提交追问'));
 expect(readAssistantDraft(storage,identity.plan_id,session,'TASK-B')).toBe('');expect(readAssistantDraft(storage,identity.plan_id,session,null)).toBe('');
 data.set(assistantDraftKey(identity.plan_id,session,'TASK-B'),JSON.stringify('B的未提交追问'));data.set(assistantDraftKey(identity.plan_id,session,null),JSON.stringify('新的业务问题'));
 expect(readAssistantDraft(storage,identity.plan_id,session,'TASK-A')).toBe('A的未提交追问');expect(readAssistantDraft(storage,identity.plan_id,session,'TASK-B')).toBe('B的未提交追问');expect(readAssistantDraft(storage,identity.plan_id,session,null)).toBe('新的业务问题');
 data.set(assistantDraftKey(identity.plan_id,session,'TASK-B'),JSON.stringify(''));
 expect(readAssistantDraft(storage,identity.plan_id,session,'TASK-A')).toBe('A的未提交追问');expect(readAssistantDraft(storage,identity.plan_id,session,null)).toBe('新的业务问题');
});
it('preserves the former unowned shared draft only in the new-question slot',()=>{
 const legacy='planning-assistant-input:'+identity.plan_id+'|'+task.session_id,data=new Map([[legacy,JSON.stringify('原来尚未提交的输入')]]),storage={getItem:(key:string)=>data.get(key)??null};
 expect(readAssistantDraft(storage,identity.plan_id,task.session_id,'TASK-A')).toBe('');expect(readAssistantDraft(storage,identity.plan_id,task.session_id,'TASK-B')).toBe('');expect(readAssistantDraft(storage,identity.plan_id,task.session_id,null)).toBe('原来尚未提交的输入');
 expect(data.get(legacy)).toBe(JSON.stringify('原来尚未提交的输入'));
 data.set(assistantDraftKey(identity.plan_id,task.session_id,null),JSON.stringify(''));expect(readAssistantDraft(storage,identity.plan_id,task.session_id,null)).toBe('');
});
it('restores only the exact plan/version/baseline/session and retains historical task target',()=>{
 expect(taskBelongs(task,{...identity,target:'DRAFT-2222222222222222',run_id:'API-2222222222222222'},'a'.repeat(32))).toBe(true);
 for(const change of [{plan_id:'PLAN-2222222222222222'},{data_version_id:'DATA-2'},{baseline_run_id:'API-2222222222222222'}])expect(taskBelongs(task,{...identity,...change},'a'.repeat(32))).toBe(false);
 expect(taskBelongs(task,identity,'b'.repeat(32))).toBe(false);
});
it('opens an explicitly linked external task without admitting another local model session',()=>{
 const foreign={...task,session_id:'b'.repeat(32)},external={...foreign,mode:'external' as const};
 expect(taskBelongs(external,identity,'a'.repeat(32))).toBe(false);
 expect(taskBelongs(external,identity,'a'.repeat(32),external.id)).toBe(true);
 expect(taskBelongs(external,identity,'a'.repeat(32),'TASK-other')).toBe(false);
 expect(taskBelongs(foreign,identity,'a'.repeat(32),foreign.id)).toBe(false);
 expect(taskBelongs(external,{...identity,data_version_id:'other'},'a'.repeat(32),external.id)).toBe(false);
});
it('allows a confirmation bound to its actual candidate but rejects expired or foreign scope',()=>{
 const now=Date.parse('2026-10-26T10:00:00Z');expect(confirmationUsable(task,now)).toBe(true);
 expect(confirmationUsable(task,Date.parse('2026-10-26T12:00:00Z'))).toBe(false);
 expect(confirmationUsable(task,Date.parse('2026-10-26T12:00:00Z'),true)).toBe(true);
 expect(confirmationUsable({...task,pending_confirmation:{...task.pending_confirmation!,nonce:''}},Date.parse('2026-10-26T12:00:00Z'),true)).toBe(false);
 expect(confirmationUsable({...task,pending_confirmation:{...task.pending_confirmation!,scope:{...identity,plan_id:'PLAN-2222222222222222'}}},now)).toBe(false);
 expect(confirmationUsable({...task,state:'CANCELLED'},now)).toBe(false);
});
it('shows actual action parameters without exposing identifiers in its business summary',()=>{
 const rows=confirmationSummary({...identity,quantity:'20',item_id:'COAT',hard_constraints:['原Firm逐行交付不得退化'],edits:[{resource_id:'M2',start_at:'2026-10-27T08:00:00+08:00'}]});
 expect(rows).toEqual([['数量','20'],['产品 / 物料','COAT'],['硬约束','原Firm逐行交付不得退化'],['调整内容','资源：M2；开始：2026-10-27T08:00:00+08:00']]);
 expect(confirmationSummary({algorithm:'cp_sat',scope:'setup_and_downstream',budget_seconds:30,model:'vns'})).toEqual([['优化方法','CP-SAT'],['业务范围','换线及关联下游工序'],['计算预算（秒）','30'],['预测方法','关联VNS']]);
 const nested=confirmationSummary({netting:{revision:'advanced-netting-v1',forecast_version_ref:{id:'FCV-private',sha256:'a'.repeat(64)},consumption_policy:{backward_days:0,forward_days:7,match_customer:true},net_terms:{priority:1000}},inquiry:{quantity:'20',delivery_rule:'ALL_OR_NOTHING',protect_existing:true}});
 expect(nested[0]).toEqual(['预测抵扣与净需求','订单抵扣政策：向前抵扣天数：0；向后抵扣天数：7；同客户抵扣：是；净需求条件：优先级：1000']);
 expect(nested[1]).toEqual(['新增研究需求','数量：20；交付规则：整批满足；保护原客户交付：是']);
 expect(JSON.stringify(nested)).not.toContain('FCV-private');expect(JSON.stringify(nested)).not.toContain('sha256');
});
it('builds only same-plan valid candidate links and preserves task return identity',()=>{
 const c={plan_id:identity.plan_id,draft_id:'DRAFT-2222222222222222',run_id:'API-2222222222222222'};
 const link=candidateLink(c,identity,'TASK-1',true)!;expect(link).toContain('/compare/'+c.draft_id);expect(link).toContain('assistant_task=TASK-1');expect(link).toContain('run_id='+c.run_id);
 expect(candidateLink({...c,plan_id:'PLAN-2222222222222222'},identity,'TASK-1')).toBeNull();expect(candidateLink({...c,draft_id:'https://foreign.invalid'},identity,'TASK-1')).toBeNull();
 expect(businessLink('resource',identity,'TASK-1')).toContain('analysis=resources');expect(businessLink('unsafe',identity,'TASK-1')).toBeNull();
});
it('polls real active states but does not schedule polling of completed or paused tasks',()=>{for(const s of ['QUEUED','MODEL_RUNNING','RUNNING','WAITING_COMPUTE'])expect(activeTask(s)).toBe(true);for(const s of ['COMPLETED','CANCELLED','PAUSED_RECOVERY_REQUIRED'])expect(activeTask(s)).toBe(false)});
it('uses the external Agent entry without certifying an unavailable built-in model',()=>{
 const caps={available:false,certification:'EXTERNAL_AGENT_ENTRY',provider:'external_agent_mcp'};
 expect(usesExternalAgent(caps,null)).toBe(true);expect(usesExternalAgent(caps,{...task,mode:'external'})).toBe(true);expect(usesExternalAgent(caps,task)).toBe(false);
 expect(startTaskBody(identity,'a'.repeat(32),'  检查交付风险  ','b'.repeat(32))).toMatchObject({mode:'external',message:'检查交付风险',expected_run_id:identity.run_id});
});
it('copies only the existing task origin and original question for Agent handoff',()=>{
 const text=taskHandoffText({...task,original_request:'检查交付风险'});expect(text).toContain('task_id: TASK-1');expect(text).toContain('用户原问题：检查交付风险');for(const [key,value] of Object.entries(identity))expect(text).toContain(key+': '+value);
 expect(text).not.toContain(task.pending_confirmation!.nonce);expect(text).not.toContain('nonce');
});
it('allows external RUNNING task follow-up when professional calls are idle',()=>{
 const idle={...task,mode:'external' as const,state:'RUNNING',pending_confirmation:null,tool_calls:{}};
 expect(blocksContinuation(idle)).toBe(false);
 expect(blocksContinuation({...idle,mode:'local_codex'})).toBe(true);
 for(const state of ['EXECUTING','WAITING_CONFIRMATION','OUTCOME_UNKNOWN'])expect(blocksContinuation({...idle,tool_calls:{call:{call_id:'CALL-1',tool:'get_professional_evidence',state}}})).toBe(true);
 expect(blocksContinuation({...idle,tool_calls:{call:{call_id:'CALL-1',tool:'get_professional_evidence',state:'COMPLETED'}}})).toBe(false);
});
it('does not imply a saved external question is already being investigated',()=>{
 const idle={...task,mode:'external' as const,state:'RUNNING',pending_confirmation:null,tool_calls:{}};
 expect(taskStatusLabel(idle)).toBe('等待 Agent 继续');
 expect(taskStatusLabel({...idle,tool_calls:{call:{call_id:'CALL-1',tool:'get_professional_evidence',state:'COMPLETED'}}})).toBe('等待 Agent 继续');
 expect(taskStatusLabel({...idle,tool_calls:{call:{call_id:'CALL-1',tool:'get_professional_evidence',state:'EXECUTING'}}})).toBe('正在执行专业调查');
 expect(taskStatusLabel({...idle,mode:'local_codex'})).toBe('正在执行专业调查');
 expect(taskStatusLabel({...idle,state:'COMPLETED'})).toBe('分析已完成');
});
it('renders legacy tool codes as business labels and distinguishes one finished tool from a finished task',()=>{
 expect(eventLabel({id:1,at:'',kind:'tool_finished',business_label:'request_run_planning',tool:'request_run_planning'})).toBe('交给原规划引擎计算');
 expect(eventLabel({id:2,at:'',kind:'tool_requested',tool:'get_professional_evidence'})).toBe('读取专业业务证据');
 expect(eventStatus('COMPLETED')).toBe('已完成');expect(eventStatus('COMPLETED')).not.toBe('分析已完成');
});
it('sends scoped typed requests with original client authorization and never redirects to model HTTP',async()=>{
 const fetcher=vi.fn().mockResolvedValue(new Response(JSON.stringify({available:false,certification:'NOT_CERTIFIED'}),{status:200}));
 const result=await assistantRequest('/v1/agent-capabilities',identity.plan_id,undefined,undefined,fetcher);
 expect(result).toEqual({available:false,certification:'NOT_CERTIFIED'});expect(fetcher).toHaveBeenCalledWith('/v1/agent-capabilities?plan_id='+identity.plan_id,expect.objectContaining({method:'GET',headers:{'X-Planning-Client':'p1'}}));
 await expect(assistantRequest('/v1/chat',identity.plan_id,undefined,undefined,fetcher)).rejects.toThrow('任务请求地址无效');
});
it('distinguishes unknown writes from explicit rejection so retries can preserve the original request',async()=>{
 const fetcher=vi.fn().mockRejectedValue(new TypeError('connection lost'));
 await expect(assistantRequest('/v1/agent-tasks',identity.plan_id,{request_id:'a'.repeat(32)},undefined,fetcher)).rejects.toMatchObject({code:'WRITE_STATUS_UNKNOWN',unknown:true});
 const reject=vi.fn().mockResolvedValue(new Response(JSON.stringify({detail:{code:'CONFIRMATION_EXPIRED',message:'确认已过期'}}),{status:409}));
 await expect(assistantRequest('/v1/agent-tasks/TASK-1/confirm',identity.plan_id,{nonce:'original'},undefined,reject)).rejects.toMatchObject({code:'CONFIRMATION_EXPIRED',unknown:false});
 expect(AssistantRequestError).toBeDefined();
});
it('treats a server 500 after persistence as unknown and permits only the original idempotent retry',async()=>{
 const body={request_id:'a'.repeat(32),message:'调查风险'},saved=new Map<string,string>();
 const fetcher=vi.fn(async(_url:RequestInfo|URL,init?:RequestInit)=>{const text=String(init?.body),request=JSON.parse(text);if(saved.has(request.request_id)){expect(saved.get(request.request_id)).toBe(text);return new Response(JSON.stringify({id:'TASK-saved'}),{status:200})}saved.set(request.request_id,text);return new Response(JSON.stringify({detail:{code:'INTERNAL_ERROR',message:'返回途中失败'}}),{status:500})});
 await expect(assistantRequest('/v1/agent-tasks',identity.plan_id,body,undefined,fetcher)).rejects.toMatchObject({code:'INTERNAL_ERROR',unknown:true});
 expect(saved.size).toBe(1);await expect(assistantRequest('/v1/agent-tasks',identity.plan_id,body,undefined,fetcher)).resolves.toEqual({id:'TASK-saved'});expect(saved.size).toBe(1);
});
it('preserves cancellation while a poll response body is being read',async()=>{
 const fetcher=vi.fn().mockResolvedValue({ok:true,json:()=>Promise.reject(new DOMException('aborted after headers','AbortError'))});
 await expect(assistantRequest('/v1/agent-tasks/TASK-1',identity.plan_id,undefined,undefined,fetcher)).rejects.toMatchObject({name:'AbortError'});
});
it('allows only the explicit local reviewer session route alongside task routes',async()=>{
 const fetcher=vi.fn().mockResolvedValue(new Response(JSON.stringify({authenticated:true}),{status:200}));
 await expect(assistantRequest('/v1/agent-operator-session',identity.plan_id,{credential:'local-fixture'},undefined,fetcher)).resolves.toEqual({authenticated:true});
 await expect(assistantRequest('/v1/arbitrary-admin',identity.plan_id,{approved:true},undefined,fetcher)).rejects.toThrow('任务请求地址无效');
 expect(fetcher).toHaveBeenCalledTimes(1);
});
it('allows authentication before a plan is loaded without opening other unscoped requests',async()=>{
 const fetcher=vi.fn().mockResolvedValue(new Response(JSON.stringify({authenticated:false}),{status:200}));
 await assistantRequest('/v1/agent-operator-session','',undefined,undefined,fetcher);
 expect(fetcher).toHaveBeenCalledWith('/v1/agent-operator-session',expect.objectContaining({method:'GET'}));
 await expect(assistantRequest('/v1/agent-tasks','',undefined,undefined,fetcher)).rejects.toThrow('任务请求地址无效');
});
it('summarizes large optimized assignments while retaining the exact action unchanged',()=>{
 const assignments=Array.from({length:83},(_,i)=>({reference:'TASK-'+i,start_at:'2026-11-03T09:00:00+08:00',end_at:'2026-11-03T10:00:00+08:00',resource_id:i%2?'SEW-01':'SEW-02'}));
 const action={edits:[{kind:'optimized_schedule',reference:'optimization',assignments}]};
 const before=JSON.stringify(action),summary=confirmationSummary(action);
 expect(summary[0][1]).toContain('83 项工序分配');expect(summary[0][1]).toContain('2 项设备');
 expect(summary[0][1].length).toBeLessThan(250);expect(JSON.stringify(action)).toBe(before);
});
