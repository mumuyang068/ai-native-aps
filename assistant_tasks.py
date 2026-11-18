"""Durable Agent metadata in the existing plan-local SessionStore.

Business facts, drafts, runs and reviews stay with their original Owners. An
EXECUTING write without a receipt is deliberately *not* replayable after restart.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
import copy
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import uuid

IDENTITY_FIELDS=('plan_id','data_version_id','baseline_run_id','run_id','target')
STOPPED={'CANCELLED','CANCEL_REQUESTED'}
RESULT_STATES={'PRESENT','EMPTY_CHECKED','NOT_COLLECTED','UNSUPPORTED','UNREADABLE','INVALID_EVIDENCE'}

class AssistantError(ValueError):
    pass

def stamp():return datetime.now(timezone.utc).isoformat()
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def key(value):
    if not isinstance(value,str) or not re.fullmatch('[a-f0-9]{32}',value):raise AssistantError('REQUEST_ID_INVALID')
    return value

def candidate_projection(task):
    """Fold later, exact Owner read receipts over historical submission status.

    This is an evidence projection, not a new RunStore or a claim of a fresh
    native audit. Original receipts remain unchanged and retain their timestamps.
    """
    entries=copy.deepcopy(task.get('candidates',[]))
    for call in task.get('tool_calls',{}).values():
        if call.get('state')!='COMPLETED' or call.get('tool')!='get_professional_evidence':continue
        if call.get('arguments',{}).get('view')!='scenario_review':continue
        result=call.get('result') or {};identity=result.get('identity') or {}
        if result.get('result_status')!='PRESENT' or any(identity.get(k)!=task['identity'].get(k) for k in IDENTITY_FIELDS[:3]):continue
        body=result.get('result') or {}
        for row in body.get('rows',[]):
            if row.get('kind')!='scenario_candidate':continue
            record=row.get('record') or {}
            entry=next((e for e in entries if e.get('draft_id')==record.get('target') and e.get('run_id')==record.get('run_id')),None)
            state=record.get('calculation_state')
            if entry is None or state not in {'NOT_RUN','QUEUED','RUNNING','SUCCEEDED','FAILED'}:continue
            validation=record.get('validation') or {};material=validation.get('material') or {}
            states=[v.get('status') for v in validation.values() if isinstance(v,dict) and v.get('status')]
            audit='FAIL' if 'FAIL' in states else 'PASS' if state=='SUCCEEDED' and record.get('comparable') is True and material.get('status')=='PASS' and all(s=='PASS' for s in states) else 'NOT_CHECKED'
            entry.update(state=state,business_audit=audit,status_call_id=call['call_id'],status_checked_at=call.get('finished_at'))
    return entries

class AssistantTasks:
    def __init__(self,session_store,plan_id):
        self.path=session_store.path
        self.plan_id=plan_id
        with self.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS assistant_tasks (id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, session_id TEXT NOT NULL, record TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS assistant_tasks_session ON assistant_tasks(plan_id,session_id)')
            db.execute("CREATE INDEX IF NOT EXISTS assistant_tasks_identity ON assistant_tasks(plan_id,json_extract(record,'$.identity.data_version_id'),json_extract(record,'$.identity.baseline_run_id'))")
            db.execute('CREATE TABLE IF NOT EXISTS assistant_requests (plan_id TEXT NOT NULL, session_id TEXT NOT NULL, request_id TEXT NOT NULL, fingerprint TEXT NOT NULL, task_id TEXT NOT NULL, PRIMARY KEY(plan_id,session_id,request_id))')

    @contextmanager
    def connection(self):
        with sqlite3.connect(self.path,timeout=20) as db:
            yield db

    def _get(self,db,tid):
        row=db.execute('SELECT record FROM assistant_tasks WHERE id=? AND plan_id=?',(tid,self.plan_id)).fetchone()
        if row is None:raise AssistantError('TASK_NOT_FOUND')
        return json.loads(row[0])

    def _save(self,db,task):
        task['revision']+=1;task['updated_at']=stamp()
        db.execute('UPDATE assistant_tasks SET record=? WHERE id=? AND plan_id=?',
                   (json.dumps(task,ensure_ascii=False,allow_nan=False),task['id'],self.plan_id))

    def _event(self,task,kind,message='',**fields):
        event=dict(id='EVENT-'+uuid.uuid4().hex[:16],at=stamp(),kind=kind,message=message,**fields)
        task['events'].append(event)
        return event

    def _request(self,db,task,rid,payload):
        key(rid);fp=digest(payload)
        old=db.execute('SELECT fingerprint,task_id FROM assistant_requests WHERE plan_id=? AND session_id=? AND request_id=?',
                       (self.plan_id,task['session_id'],rid)).fetchone()
        if old:
            if old!=(fp,task['id']):raise AssistantError('IDEMPOTENCY_CONFLICT')
            return False
        db.execute('INSERT INTO assistant_requests VALUES(?,?,?,?,?)',(self.plan_id,task['session_id'],rid,fp,task['id']))
        return True

    def get(self,tid):
        with self.connection() as db:return self._get(db,tid)

    def list(self,session_id):
        key(session_id)
        with self.connection() as db:
            rows=db.execute('SELECT record FROM assistant_tasks WHERE plan_id=? AND session_id=? ORDER BY rowid DESC LIMIT 30',
                            (self.plan_id,session_id)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def history(self,session_id,data_version_id,baseline_run_id,*,offset=0,limit=16):
        """Read discoverable metadata inside the existing exact Plan read grant.

        Session-local lists retain their original semantics. This explicit
        history projection never transfers a session, returns consent material,
        or loads tool receipts and business inputs into a list response.
        """
        key(session_id)
        if type(offset) is not int or not 0<=offset<=10000 or type(limit) is not int or not 1<=limit<=20:
            raise AssistantError('TASK_PAGE_INVALID')
        columns=("id,session_id,json_extract(record,'$.identity'),json_extract(record,'$.revision'),"
            "json_extract(record,'$.state'),json_extract(record,'$.mode'),json_extract(record,'$.created_at'),"
            "json_extract(record,'$.updated_at'),substr(json_extract(record,'$.original_request'),1,8000),"
            "json_array_length(record,'$.candidates')")
        with self.connection() as db:
            rows=db.execute('SELECT '+columns+" FROM assistant_tasks WHERE plan_id=? "
                "AND json_extract(record,'$.identity.data_version_id')=? "
                "AND json_extract(record,'$.identity.baseline_run_id')=? ORDER BY rowid DESC LIMIT ? OFFSET ?",
                (self.plan_id,data_version_id,baseline_run_id,limit+1,offset)).fetchall()
        more=len(rows)>limit;items=[]
        for row in rows[:limit]:
            from agent import redact
            identity=json.loads(row[2])
            items.append(dict(id=row[0],identity=identity,revision=row[3],state=row[4],mode=row[5],
                created_at=row[6],updated_at=row[7],request_summary=redact(row[8])[:200],candidate_count=row[9] or 0,
                session_relation='CURRENT_SESSION' if row[1]==session_id else 'HISTORICAL_SESSION',
                href='/workspace/#/plans/'+self.plan_id+'?'+urlencode(dict(target=identity['target'],assistant=1,assistant_task=row[0]))))
        return dict(schema_version='agent-task-history/1',
            identity=dict(plan_id=self.plan_id,data_version_id=data_version_id,baseline_run_id=baseline_run_id),
            items=items,page=dict(offset=offset,limit=limit,returned=len(items),has_more=more,
                next_offset=offset+len(items) if more else None),
            checked_scope='EXISTING_PLAN_READ_GRANT; SESSION_LIST_UNCHANGED',execution_authorized=False)

    def create(self,*,session_id,request_id,message,identity,mode='external',budgets=None,access_principal=None):
        key(session_id);key(request_id)
        if identity.get('plan_id')!=self.plan_id or any(not identity.get(k) for k in IDENTITY_FIELDS):raise AssistantError('TASK_IDENTITY_MISMATCH')
        if mode not in {'local_codex','external'}:raise AssistantError('TASK_MODE_INVALID')
        if not isinstance(message,str) or not message.strip() or len(message)>8000:raise AssistantError('TASK_MESSAGE_INVALID')
        limits={'tool_calls':32,'native_runs':2,'optimization_jobs':2}
        caps={'tool_calls':80,'native_runs':4,'optimization_jobs':4}
        if budgets is not None:
            if set(budgets)-set(limits):raise AssistantError('TASK_BUDGET_INVALID')
            limits.update(budgets)
        if any(type(v) is not int or not 0<=v<=caps[k] for k,v in limits.items()):raise AssistantError('TASK_BUDGET_INVALID')
        payload=dict(kind='start',message=message,identity=identity,mode=mode,budgets=limits)
        if access_principal is not None:payload['access_principal']=access_principal
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT fingerprint,task_id FROM assistant_requests WHERE plan_id=? AND session_id=? AND request_id=?',
                           (self.plan_id,session_id,request_id)).fetchone()
            if old:
                if old[0]!=digest(payload):raise AssistantError('IDEMPOTENCY_CONFLICT')
                return self._get(db,old[1])
            task=dict(id='TASK-'+uuid.uuid4().hex[:16],plan_id=self.plan_id,session_id=session_id,request_id=request_id,
                      revision=1,state='QUEUED' if mode=='local_codex' else 'RUNNING',mode=mode,
                      identity=copy.deepcopy(identity),original_request=message,intent=None,
                      messages=[dict(role='user',content=message,at=stamp())],events=[],tool_calls={},
                      pending_confirmation=None,candidates=[],evidence=[],answer=None,error=None,
                      model_thread_id=None,action_generation=1,budgets=limits,usage=dict(tool_calls=0,native_runs=0,optimization_jobs=0,model_calls=0,model_attempts=0,tokens=None,cost=None),
                      created_at=stamp(),updated_at=stamp())
            if access_principal is not None:task['access_principal']=copy.deepcopy(access_principal)
            self._event(task,'task_created','任务已绑定当前企业计划。')
            db.execute('INSERT INTO assistant_tasks VALUES(?,?,?,?)',(task['id'],self.plan_id,session_id,json.dumps(task,ensure_ascii=False)))
            self._request(db,task,request_id,payload)
            return task

    def begin_tool(self,tid,request_id,tool,arguments,*,read_only,action_kind=None,business_label=None):
        key(request_id)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE');task=self._get(db,tid)
            fp=digest(dict(tool=tool,arguments=arguments,read_only=read_only,action_kind=action_kind,generation=task.get('action_generation',1)))
            old=task['tool_calls'].get(request_id)
            if old:
                if old['tool']!=tool or old['arguments']!=arguments or old['read_only']!=read_only or old['action_kind']!=action_kind:
                    raise AssistantError('IDEMPOTENCY_CONFLICT')
                return {**old,'execute':False}
            if not read_only:
                identical=next((c for c in task['tool_calls'].values() if c['fingerprint']==fp),None)
                if identical:return {**identical,'execute':False}
            if task['state'] in STOPPED:raise AssistantError('TASK_CANCELLED')
            if task['state']=='PAUSED_RECOVERY_REQUIRED' and not read_only:raise AssistantError('TASK_RECONCILIATION_REQUIRED')
            if not read_only and any(c['state']=='EXECUTING' and not c['read_only'] for c in task['tool_calls'].values()):
                raise AssistantError('TASK_ACTION_IN_PROGRESS')
            if any(arguments.get(k)!=task['identity'][k] for k in IDENTITY_FIELDS[:3]):raise AssistantError('TASK_IDENTITY_MISMATCH')
            # A baseline run is immutable. A draft may gain a run after a
            # confirmed solve; its current identity is authenticated upstream
            # by the original DraftStore before this metadata operation.
            if arguments.get('target')=='baseline' and arguments.get('run_id')!=task['identity']['baseline_run_id']:
                raise AssistantError('TASK_IDENTITY_MISMATCH')
            if not arguments.get('target') or not arguments.get('run_id'):raise AssistantError('TASK_IDENTITY_MISMATCH')
            if task['usage']['tool_calls']>=task['budgets']['tool_calls']:raise AssistantError('TOOL_BUDGET_EXHAUSTED')
            if not read_only and task['pending_confirmation']:raise AssistantError('CONFIRMATION_ALREADY_PENDING')
            counter='native_runs' if action_kind=='RUN_PLANNING' else 'optimization_jobs' if action_kind=='START_SCHEDULE_OPTIMIZATION' else None
            if counter and task['usage'][counter]>=task['budgets'][counter]:raise AssistantError('COMPUTE_BUDGET_EXHAUSTED')
            call=dict(call_id='CALL-'+uuid.uuid4().hex[:16],request_id=request_id,tool=tool,arguments=copy.deepcopy(arguments),
                      fingerprint=fp,read_only=read_only,action_kind=action_kind,state='EXECUTING' if read_only else 'WAITING_CONFIRMATION',
                      started_at=stamp(),result=None,error=None,counter=counter)
            task['usage']['tool_calls']+=1
            if not read_only:
                pending=dict(id='CONFIRM-'+uuid.uuid4().hex[:16],nonce=secrets.token_urlsafe(32),call_id=call['call_id'],
                    title=business_label or tool,description='仅创建或计算研究方案，不改变企业执行事实。请核对目标、条件与计算范围。',
                    action=action_kind or tool,scope={k:arguments[k] for k in IDENTITY_FIELDS},arguments=copy.deepcopy(arguments),arguments_sha256=digest(arguments),
                    expires_at=(datetime.now(timezone.utc)+timedelta(minutes=20)).isoformat())
                call['pending_confirmation']=pending;task['pending_confirmation']=pending;task['state']='WAITING_CONFIRMATION'
            task['tool_calls'][request_id]=call
            self._event(task,'tool_requested',business_label or tool,tool=tool,business_label=business_label or tool,status=call['state'])
            self._save(db,task)
            return {**call,'execute':read_only}

    def _find_call(self,task,call_id):
        for call in task['tool_calls'].values():
            if call['call_id']==call_id:return call
        raise AssistantError('TOOL_CALL_NOT_FOUND')

    def confirm(self,tid,*,request_id,confirmation_id,nonce,expected_revision,decision,principal=None):
        if decision not in {'approve','reject'}:raise AssistantError('CONFIRMATION_DECISION_INVALID')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE');task=self._get(db,tid)
            fresh=self._request(db,task,request_id,dict(kind='confirm',id=confirmation_id,nonce=nonce,decision=decision,expected_revision=expected_revision))
            if not fresh:return dict(execute=False,task=task)
            if task['state'] in STOPPED:raise AssistantError('TASK_CANCELLED')
            if task['revision']!=expected_revision:raise AssistantError('TASK_REVISION_CHANGED')
            p=task.get('pending_confirmation')
            if not p or p['id']!=confirmation_id or not hmac.compare_digest(p['nonce'],nonce):raise AssistantError('CONFIRMATION_INVALID')
            if decision=='approve' and datetime.fromisoformat(p['expires_at'])<=datetime.now(timezone.utc):raise AssistantError('CONFIRMATION_EXPIRED')
            call=self._find_call(task,p['call_id'])
            if call['state']!='WAITING_CONFIRMATION' or digest(call['arguments'])!=p['arguments_sha256']:raise AssistantError('CONFIRMATION_CHANGED')
            if decision=='approve':
                if call['counter']:
                    if task['usage'][call['counter']]>=task['budgets'][call['counter']]:raise AssistantError('COMPUTE_BUDGET_EXHAUSTED')
                    task['usage'][call['counter']]+=1
                call['state']='EXECUTING';call['confirmed_at']=stamp();task['state']='RUNNING'
            else:call['state']='REJECTED';task['state']='WAITING_INFORMATION'
            if principal is not None:call['confirmed_by']=copy.deepcopy(principal)
            task['pending_confirmation']=None
            self._event(task,'confirmation', '用户批准研究动作。' if decision=='approve' else '用户拒绝研究动作。',tool=call['tool'],status=call['state'],confirmed_by=copy.deepcopy(principal))
            self._save(db,task)
            return dict(execute=decision=='approve',call=copy.deepcopy(call),task=task)

    def finish_tool(self,tid,call_id,*,result=None,error=None,elapsed_ms=None):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE');task=self._get(db,tid);call=self._find_call(task,call_id)
            if call['state'] not in {'EXECUTING','OUTCOME_UNKNOWN'}:raise AssistantError('TOOL_CALL_NOT_EXECUTING')
            call.update(state='FAILED' if error else 'COMPLETED',result=copy.deepcopy(result),error=copy.deepcopy(error),finished_at=stamp(),elapsed_ms=elapsed_ms)
            if error and not call['read_only'] and error.get('outcome_unknown'):
                call['state']='OUTCOME_UNKNOWN'
                if task['state'] not in STOPPED:task['state']='PAUSED_RECOVERY_REQUIRED'
            if result:
                task['evidence'].append(dict(call_id=call_id,result_status=result.get('result_status','NOT_COLLECTED'),
                    identity=result.get('identity'),source=result.get('source'),evidence=result.get('evidence'),checked_scope=result.get('checked_scope')))
                self._references(task,result,call)
            self._event(task,'tool_finished',error.get('message',error.get('code','工具失败')) if error else '专业工具已返回可核对结果。',
                tool=call['tool'],status=call['state'],elapsed_ms=elapsed_ms,result_status=(result or {}).get('result_status','UNREADABLE' if error else 'NOT_COLLECTED'))
            self._save(db,task);return task

    def _references(self,task,result,call):
        # Only identifiers returned by the domain service, never model-generated hrefs.
        body=result.get('result',result)
        if not isinstance(body,dict):return
        draft=body.get('draft')
        if isinstance(draft,dict) and isinstance(draft.get('id'),str) and re.fullmatch('DRAFT-[a-f0-9]{16}',draft['id']):
            entry=dict(draft_id=draft['id'],run_id=draft.get('run_id'),target=draft['id'],plan_id=self.plan_id)
            if not any(c['draft_id']==draft['id'] for c in task['candidates']):task['candidates'].append(entry)
        if call.get('action_kind')=='RUN_PLANNING' and re.fullmatch('API-[a-f0-9]{16}',str(body.get('solve_run_id',''))):
            did=call['arguments']['draft_id'];entry=next((c for c in task['candidates'] if c['draft_id']==did),None)
            if entry is None:
                entry=dict(draft_id=did,target=did,plan_id=self.plan_id);task['candidates'].append(entry)
            entry.update(run_id=body['solve_run_id'],state=body.get('state'),business_audit='NOT_CHECKED')

    def event(self,tid,kind,message='',**fields):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE');task=self._get(db,tid)
            self._event(task,kind,message,**fields);self._save(db,task);return task

    def record_intent(self,tid,intent):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE');task=self._get(db,tid)
            if task['state'] in STOPPED:raise AssistantError('TASK_CANCELLED')
            task['intent']=dict(intent,original_request=task['original_request'],identity=task['identity'],
                allowed_actions='READ_AND_PREVIEW; PERSISTENT_ACTIONS_REQUIRE_SERVER_HUMAN_CONFIRMATION',execution_authorized=False)
            self._event(task,'intent_recorded','已记录目标、约束、偏好与缺失信息；未授予新的执行权限。')
            self._save(db,task);return task

    def finish_answer(self,tid,answer,limitations,evidence_call_ids):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE');task=self._get(db,tid)
            if task['state'] in STOPPED:raise AssistantError('TASK_CANCELLED')
            if task['pending_confirmation']:raise AssistantError('CONFIRMATION_REQUIRED')
            if any(c['state']=='EXECUTING' and not c['read_only'] for c in task['tool_calls'].values()):raise AssistantError('TASK_ACTION_IN_PROGRESS')
            unresolved=any(c['state']=='OUTCOME_UNKNOWN' and not c['read_only'] for c in task['tool_calls'].values())
            if unresolved and not limitations:raise AssistantError('ANSWER_UNRESOLVED_ACTION')
            completed={c['call_id'] for c in task['tool_calls'].values() if c['state']=='COMPLETED'}
            if not set(evidence_call_ids)<=completed:raise AssistantError('ANSWER_EVIDENCE_NOT_IN_TASK')
            task['answer']=answer;task['limitations']=limitations
            task['messages'].append(dict(role='assistant',content=answer,at=stamp(),evidence_call_ids=evidence_call_ids))
            task['state']='PAUSED_RECOVERY_REQUIRED' if unresolved else 'COMPLETED_WITH_LIMITS' if limitations else 'COMPLETED'
            self._event(task,'answer_recorded','分析已保存。业务认证仍以各运行的审计结论为准。')
            self._save(db,task);return task

    def model_result(self,tid,result):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE');task=self._get(db,tid)
            task['usage']['model_attempts']=task['usage'].get('model_attempts',0)+1
            if result.get('turn_id'):task['usage']['model_calls']+=1
            task['usage']['tokens']=result.get('usage') or None
            if result.get('thread_id'):task['model_thread_id']=result['thread_id']
            task['error']=result.get('error')
            finalized=task['state'] in {'COMPLETED','COMPLETED_WITH_LIMITS'}
            if result.get('text') and not finalized:
                task['answer']=result['text'];task['messages'].append(dict(role='assistant',content=result['text'],at=stamp()))
            if task['state'] not in STOPPED|{'WAITING_CONFIRMATION','PAUSED_RECOVERY_REQUIRED','COMPLETED','COMPLETED_WITH_LIMITS'}:
                task['state']='PAUSED_RECOVERY_REQUIRED' if result.get('error') else 'WAITING_INFORMATION'
            self._event(task,'model_finished', '模型连接未完成，已保留任务与工具记录。' if result.get('error') else '模型本轮答复已保存。',status=result.get('status','UNKNOWN'))
            self._save(db,task);return task

    def continue_task(self,tid,*,request_id,message,expected_revision):
        if not isinstance(message,str) or not message.strip() or len(message)>8000:raise AssistantError('TASK_MESSAGE_INVALID')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE');task=self._get(db,tid)
            fresh=self._request(db,task,request_id,dict(kind='continue',message=message,expected_revision=expected_revision))
            if not fresh:return dict(start=False,task=task)
            if task['revision']!=expected_revision:raise AssistantError('TASK_REVISION_CHANGED')
            if task['state'] in STOPPED:raise AssistantError('TASK_CANCELLED')
            if task['pending_confirmation']:raise AssistantError('CONFIRMATION_REQUIRED')
            if any(c['state']=='OUTCOME_UNKNOWN' for c in task['tool_calls'].values()):raise AssistantError('TASK_RECONCILIATION_REQUIRED')
            if any(c['state']=='EXECUTING' and not c['read_only'] for c in task['tool_calls'].values()):raise AssistantError('TASK_ACTION_IN_PROGRESS')
            if task['state'] in {'QUEUED','MODEL_RUNNING'}:raise AssistantError('TASK_ALREADY_RUNNING')
            task['messages'].append(dict(role='user',content=message,at=stamp()));task['error']=None
            task['action_generation']=task.get('action_generation',1)+1
            task['state']='QUEUED' if task['mode']=='local_codex' else 'RUNNING'
            self._save(db,task);return dict(start=task['mode']=='local_codex',task=task)

    def cancel(self,tid,request_id,expected_revision):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE');task=self._get(db,tid)
            if not self._request(db,task,request_id,dict(kind='cancel',expected_revision=expected_revision)):return task
            if task['revision']!=expected_revision:raise AssistantError('TASK_REVISION_CHANGED')
            task['state']='CANCELLED';task['pending_confirmation']=None
            for call in task['tool_calls'].values():
                if call['state']=='WAITING_CONFIRMATION':call['state']='REJECTED'
            self._event(task,'cancelled','已停止后续助手动作；已提交的原生计算及档案保留，请从原方案查看状态。')
            self._save(db,task);return task

    def mark_model_running(self,tid):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE');task=self._get(db,tid)
            if task['state']!='QUEUED':return False
            task['state']='MODEL_RUNNING';self._save(db,task);return True

    def recover_interrupted(self):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            rows=db.execute('SELECT id FROM assistant_tasks WHERE plan_id=?',(self.plan_id,)).fetchall()
            for row in rows:
                task=self._get(db,row[0]);changed=False
                for call in task['tool_calls'].values():
                    if call['state']=='EXECUTING':
                        call['state']='FAILED' if call['read_only'] else 'OUTCOME_UNKNOWN'
                        call['error']=dict(code='INTERRUPTED_READ_RETRYABLE' if call['read_only'] else 'WRITE_OUTCOME_UNKNOWN',outcome_unknown=not call['read_only'])
                        changed=True
                if changed or task['state'] in {'QUEUED','MODEL_RUNNING'}:
                    task['state']='PAUSED_RECOVERY_REQUIRED'
                    self._event(task,'recovery_required','服务中断；已保留调用身份，未重发任何写操作。')
                    self._save(db,task)
