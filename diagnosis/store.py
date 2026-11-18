import copy
import hashlib
import json
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from agent import redact

TERMINAL = {'COMPLETED', 'COMPLETED_WITH_LIMITS', 'CANCELLED'}


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class TaskStore:
    def __init__(self, root):
        from pathlib import Path
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'tasks.sqlite3'
        self.lock = threading.RLock()
        with self.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, request_hash TEXT NOT NULL, data TEXT NOT NULL)')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=20)
        try:
            with db: yield db
        finally:
            db.close()

    def create(self, bound, question, request_id):
        if not re.fullmatch('[a-f0-9]{32}', request_id):
            raise ValueError('INVALID_REQUEST_ID')
        signature = digest({'scope':bound['scope'],'selection':bound['selection'],'order_id':bound['order_id'],'question': question})
        key = bound['scope']['plan_id'] + ':' + request_id
        with self.lock, self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT data,request_hash FROM tasks WHERE request_key=?', (key,)).fetchone()
            if old:
                if old[1] != signature: raise ValueError('DIAGNOSIS_IDEMPOTENCY_CONFLICT')
                return json.loads(old[0])
            task = {'id': 'DIAG-' + uuid.uuid4().hex, 'request_id':request_id, 'question': redact(question), 'bound': copy.deepcopy(bound),
                    'frozen_hash': digest(bound), 'state': 'QUEUED' if bound['order_id'] else 'WAITING_SELECTION',
                    'created_at': now(), 'updated_at': now(), 'coverage': {}, 'ledger': [], 'events': [],
                    'models': [], 'model_calls': 0, 'tool_calls': 0, 'active_ms': 0, 'answer': None,
                    'error': None, 'pending_tool': None, 'model_pending': False}
            db.execute('INSERT INTO tasks VALUES(?,?,?,?)', (task['id'], key, signature, json.dumps(task, ensure_ascii=False)))
        return task

    def get(self, tid):
        with self.connection() as db:
            row = db.execute('SELECT data FROM tasks WHERE id=?', (tid,)).fetchone()
        if not row: raise KeyError('DIAGNOSIS_NOT_FOUND')
        task = json.loads(row[0])
        if digest(task['bound']) != task['frozen_hash']: raise ValueError('DIAGNOSIS_SNAPSHOT_CHANGED')
        return task

    def mutate(self, tid, action):
        with self.lock, self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT data FROM tasks WHERE id=?', (tid,)).fetchone()
            if not row: raise KeyError('DIAGNOSIS_NOT_FOUND')
            task = json.loads(row[0])
            if digest(task['bound']) != task['frozen_hash']: raise ValueError('DIAGNOSIS_SNAPSHOT_CHANGED')
            action(task)
            task['updated_at'] = now()
            db.execute('UPDATE tasks SET data=? WHERE id=?', (json.dumps(task, ensure_ascii=False), tid))
        return task

    def event(self, task, kind, **data):
        task['events'].append({'id': len(task['events']) + 1, 'at': now(), 'type': kind, **data})

    def list(self, plan_id):
        with self.connection() as db: rows = db.execute('SELECT data FROM tasks ORDER BY rowid DESC').fetchall()
        return [self.public(json.loads(r[0])) for r in rows if json.loads(r[0])['bound']['scope']['plan_id'] == plan_id]

    def recover_interrupted(self):
        with self.connection() as db: ids = [r[0] for r in db.execute('SELECT id FROM tasks')]
        for tid in ids:
            def pause(t):
                if t['state'] in {'RUNNING', 'PAUSE_REQUESTED', 'QUEUED'}:
                    t['state'] = 'PAUSED'; t['error'] = 'SERVICE_INTERRUPTED: 调查中断，已保留证据；请明确恢复。'
                    if t['model_pending']:
                        if t['models']:
                            t['models'][-1].update(status='OUTCOME_UNKNOWN',cost={'status':'UNKNOWN','reason':'进程中断，提供方结果、用量和费用未知；不会自动重发。'})
                        self.event(t, 'MODEL_OUTCOME_UNKNOWN', message='上次模型请求结果未知；明确恢复后才允许新的模型尝试，预算不重置。')
                        t['model_pending'] = False
                    self.event(t, 'SERVICE_INTERRUPTED')
            self.mutate(tid, pause)

    @staticmethod
    def public(task):
        value = {k: copy.deepcopy(v) for k,v in task.items() if k not in {'bound', 'frozen_hash', 'models','decisions','pending_decision'}}
        value.update(scope=task['bound']['scope'], selection=task['bound']['selection'],
                     order_id=task['bound']['order_id'], candidates=task['bound']['candidates'])
        value['models'] = [{k:m.get(k) for k in ['index','status','requested_model','returned_model','response_id','usage','started_at','elapsed_ms','cost','error']} for m in task['models']]
        return value
