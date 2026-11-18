import copy
import json
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command, interrupt
from agent import redact
from diagnosis.store import TERMINAL, digest
from diagnosis.tools import CORE, READ_TOOLS, read_tool
from diagnosis.claims import render_answer
from diagnosis.model import call_model


class State(TypedDict, total=False):
    task_id: str
    next: str
    tool: str


class DiagnosisRuntime:
    def __init__(self, store, model=None, reader=None):
        self.store = store; self.model = model or call_model; self.reader = reader or read_tool
        self.connection = sqlite3.connect(store.root/'graph.sqlite3', check_same_thread=False)
        self.saver = SqliteSaver(self.connection)
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='readonly-diagnosis')
        self.running = set(); self.lock = threading.RLock()
        g = StateGraph(State)
        g.add_node('investigate', self._step); g.add_node('read', self._tool); g.add_node('pause', self._pause)
        g.add_node('repair',lambda state:{})
        g.add_edge(START,'investigate')
        g.add_conditional_edges('investigate',lambda s:s['next'],{'read':'read','pause':'pause','repair':'repair','end':END})
        g.add_edge('read','investigate');g.add_edge('pause','investigate');g.add_edge('repair','investigate')
        self.graph = g.compile(checkpointer=self.saver)

    def _finish(self, tid, answer=None, limitation=None):
        def apply(t):
            if t['state'] in TERMINAL:return
            args = answer or {'claims': [], 'gaps': []}
            args = copy.deepcopy(args)
            if re.search(r'(提高|调整|修改|提升|设置).{0,12}优先级|优先级.{0,8}(提高|调整|修改)',t['question']):
                args['gaps'].append('UNSUPPORTED_PRIORITY')
            t['answer'] = render_answer(args,t['ledger'],t['bound'],t['coverage'])
            t['state'] = 'COMPLETED_WITH_LIMITS' if limitation or any(v in {'ERROR','UNAVAILABLE','PARTIAL'} for v in t['coverage'].values()) else 'COMPLETED'
            if limitation:t['answer']['text']+='\n调查限制：'+limitation
            t['error'] = limitation
            self.store.event(t,'FINISHED',state=t['state'],evidence_count=len(t['ledger']))
        self.store.mutate(tid,apply)

    def _step(self, state, config):
        tid=state['task_id'];t=self.store.get(tid)
        if t['state'] in TERMINAL:return {'next':'end'}
        if t['state'] in {'PAUSED','PAUSE_REQUESTED'}:return {'next':'pause'}
        step=str(config['metadata']['langgraph_step'])
        saved=t.get('pending_decision') or t.get('decisions',{}).get(step)
        if saved is None and (t['active_ms']>=90000 or t['tool_calls']>=8 or t['model_calls']>=4):
            self._finish(tid,limitation='已到本次调查预算，保留已查事实；没有继续推断或运行计划。');return {'next':'end'}
        pending=t.get('pending_tool')
        if pending and t['coverage'].get(pending)=='ERROR':return {'next':'read','tool':pending}
        if t.get('pending_tools'):return {'next':'read','tool':t['pending_tools'][0]}
        missing=next((n for n in CORE if n not in t['coverage']),None)
        if missing:return {'next':'read','tool':missing}
        if saved is not None:
            decision=saved
        else:
            def begin(t):
                t['model_calls']+=1;t['model_pending']=True
                t['models'].append({'index':t['model_calls'],'status':'PREPARING','started_at':time.time(),
                                    'cost':{'status':'UNKNOWN','reason':'请求尚未完成，用量和费用未知。'}})
                self.store.event(t,'MODEL_STARTED',index=t['model_calls'])
            self.store.mutate(tid,begin);started=time.monotonic()
            def persist_request(record):
                self.store.mutate(tid,lambda t:t['models'][-1].update(record,status='REQUESTED'))
            try:
                task=self.store.get(tid)
                decision,record=self.model(task,on_request=persist_request) if self.model is call_model else self.model(task)
            except Exception as error:
                record=getattr(error,'model_record',{'error':redact(str(error)), 'cost':{'status':'UNKNOWN'}})
                def fail(t):
                    t['model_pending']=False;t['active_ms']+=round((time.monotonic()-started)*1000)
                    t['models'][-1].update(record,status='FAILED')
                    if t['state'] not in TERMINAL:
                        t['state']='PAUSED';t['error']='MODEL_UNAVAILABLE: 模型调用未完成，已保留查询证据，请明确恢复。'
                    self.store.event(t,'MODEL_FAILED',index=t['model_calls'],message=redact(str(error)))
                self.store.mutate(tid,fail);return {'next':'pause'}
            def completed(t):
                t['model_pending']=False;t['active_ms']+=round((time.monotonic()-started)*1000)
                t['models'][-1].update(record,status='COMPLETED')
                t.setdefault('decisions',{})[step]=decision
                t['pending_decision']=decision
                self.store.event(t,'MODEL_COMPLETED',index=t['model_calls'],tool=decision.get('tool'))
            t=self.store.mutate(tid,completed)
        if t['state'] in TERMINAL:return {'next':'end'}
        if t['state']=='PAUSE_REQUESTED':return {'next':'pause'}
        name,args=decision.get('tool'),decision.get('arguments')
        if name=='finish_diagnosis':
            try:self._finish(tid,args)
            except (ValueError,KeyError,TypeError) as error:
                def reject(t):
                    t['pending_decision']=None
                    t['validation_feedback']=redact(str(error))
                    self.store.event(t,'ANSWER_REJECTED',index=t['model_calls'],message=redact(str(error)))
                self.store.mutate(tid,reject)
                return {'next':'repair'}
            return {'next':'end'}
        if name=='read_batch':
            names=args.get('tools',[]) if isinstance(args,dict) else []
            if (not names or len(names)>8-t['tool_calls'] or len(set(names))!=len(names)
                    or any(n not in READ_TOOLS or t['coverage'].get(n) not in {None,'ERROR'} for n in names)):
                self._finish(tid,limitation='模型批量查询超出预算、重复或不在只读合同内，已停止。');return {'next':'end'}
            self.store.mutate(tid,lambda t:t.update(pending_tools=names,pending_decision=None))
            return {'next':'read','tool':names[0]}
        if name not in READ_TOOLS or args!={}:
            self._finish(tid,limitation='模型请求超出只读工具合同，已停止。');return {'next':'end'}
        if t['coverage'].get(name) not in {None,'ERROR'}:
            self._finish(tid,limitation='模型重复查询且无新增证据，已停止并保留已查记录。');return {'next':'end'}
        self.store.mutate(tid,lambda t:t.update(pending_tools=[name],pending_decision=None))
        return {'next':'read','tool':name}

    def _tool(self,state):
        tid,name=state['task_id'],state['tool'];t=self.store.get(tid)
        if t['state'] in TERMINAL:return {}
        # The task journal and graph checkpoint commit independently. A completed
        # read is reusable even if a crash prevented the graph from advancing.
        if t['coverage'].get(name) not in {None,'ERROR'}:return {}
        if t['tool_calls']>=8:return {}
        def begin(t):
            t['tool_calls']+=1;t['pending_tool']=name
            self.store.event(t,'TOOL_STARTED',tool=name,index=t['tool_calls'])
        self.store.mutate(tid,begin);started=time.monotonic()
        try:result=self.reader(t['bound'],name)
        except Exception as error:
            def fail(t):
                t['coverage'][name]='ERROR';t['active_ms']+=round((time.monotonic()-started)*1000)
                if t['state'] not in TERMINAL:t['state']='PAUSED';t['error']='QUERY_UNAVAILABLE: 查询中断，已保留此前证据，请核对后恢复。'
                self.store.event(t,'TOOL_FAILED',tool=name,message=redact(str(error)))
            self.store.mutate(tid,fail);return {}
        def apply(t):
            t['coverage'][name]=result['coverage'];t['pending_tool']=None
            if t.get('pending_tools') and t['pending_tools'][0]==name:t['pending_tools'].pop(0)
            t['active_ms']+=round((time.monotonic()-started)*1000)
            known={e['id'] for e in t['ledger']}
            t['ledger'].extend(e for e in result['evidence'] if e['id'] not in known)
            self.store.event(t,'TOOL_COMPLETED',tool=name,coverage=result['coverage'],
                             evidence_ids=[e['id'] for e in result['evidence']],scope=result['scope'],elapsed_ms=round((time.monotonic()-started)*1000))
        self.store.mutate(tid,apply);return {}

    def _pause(self,state):
        tid=state['task_id']
        if self.store.get(tid)['state'] in TERMINAL:return {}
        self.store.mutate(tid,lambda t:t.update(state='PAUSED') if t['state'] not in TERMINAL else None)
        if self.store.get(tid)['state'] in TERMINAL:return {}
        interrupt({'task_id':tid,'message':'调查已暂停，已查证据与预算保留。'})
        self.store.mutate(tid,lambda t:t.update(state='RUNNING',error=None) if t['state'] not in TERMINAL else None)
        return {}

    def run(self,tid):
        with self.lock:
            if tid in self.running:return
            if self.store.get(tid)['state'] not in {'QUEUED','RUNNING'}:return
            self.running.add(tid)
        config={'configurable':{'thread_id':tid},'recursion_limit':40}
        try:
            claimed=self.store.mutate(tid,lambda t:t.update(state='RUNNING') if t['state']=='QUEUED' else None)
            if claimed['state']!='RUNNING':return
            snapshot=self.graph.get_state(config)
            paused=any(x.interrupts for x in snapshot.tasks)
            initial=Command(resume=True) if paused else None if snapshot.next else {'task_id':tid}
            self.graph.invoke(initial,config)
        except Exception as error:
            def fail(t):
                if t['state'] not in TERMINAL:t['state']='PAUSED';t['error']='DIAGNOSIS_INTERRUPTED: 调查中断，已保留证据。'
                self.store.event(t,'RUNTIME_INTERRUPTED',message=redact(type(error).__name__+': '+str(error)))
            self.store.mutate(tid,fail)
        finally:
            with self.lock:self.running.discard(tid)

    def start(self,tid):
        self.pool.submit(self.run,tid)

    def prepare_resume(self,tid,order_id=None):
        with self.lock:
            if tid in self.running:raise ValueError('DIAGNOSIS_STILL_ACTIVE: 当前调用仍在结束，请稍后恢复。')
        def resume(t):
            if t['state'] not in {'PAUSED','WAITING_SELECTION'}:raise ValueError('DIAGNOSIS_NOT_PAUSED')
            if t['state']=='WAITING_SELECTION':
                if order_id not in {o['demand_id'] for o in t['bound']['candidates']}:raise ValueError('SELECTION_ORDER_MISMATCH')
                t['bound']['order_id']=order_id;t['bound']['selection']['order_id']=order_id
                t['frozen_hash']=digest(t['bound'])
            elif order_id is not None and order_id!=t['bound']['order_id']:
                raise ValueError('SELECTION_ORDER_MISMATCH')
            if t['model_pending']:
                self.store.event(t,'MODEL_OUTCOME_UNKNOWN');t['model_pending']=False
            t['state']='QUEUED';t['error']=None;self.store.event(t,'RESUME_CONFIRMED')
        return self.store.mutate(tid,resume)

    def pause(self,tid):
        def apply(t):
            if t['state'] not in TERMINAL and t['state']!='WAITING_SELECTION':
                t['state']='PAUSE_REQUESTED';self.store.event(t,'PAUSE_REQUESTED')
        return self.store.mutate(tid,apply)

    def cancel(self,tid):
        def apply(t):
            if t['state'] not in TERMINAL:t['state']='CANCELLED';self.store.event(t,'CANCELLED')
        return self.store.mutate(tid,apply)
