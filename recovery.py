"""Evidence-bound reconciliation; never adopts results or resubmits a solve."""
import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone

from agent import redact
from planning_adapter import FreppleClient, InputError

TERMINAL = {'Done', 'Failed', 'Canceled', 'Cancelled'}


def token(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def stamp(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


class RecoveryService:
    def __init__(self, store, native=None):
        self.store = store
        self.native = native
        with store.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS recovery_inspections(id TEXT PRIMARY KEY, created REAL NOT NULL, data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS recovery_audit(id TEXT PRIMARY KEY, inspection_id TEXT UNIQUE NOT NULL, request_hash TEXT NOT NULL, at TEXT NOT NULL, data TEXT NOT NULL)')

    def _snapshot(self, db):
        row = db.execute("SELECT data FROM worker_metadata WHERE key='recovery'").fetchone()
        worker = json.loads(row[0]) if row else {'state': 'READY'}
        queue = [dict(x) for x in db.execute("SELECT id,state FROM jobs WHERE state IN ('QUEUED','RUNNING') ORDER BY id")]
        cases = []
        for rid in worker.get('run_ids', []):
            job = db.execute('SELECT id,state,input_hash,failure FROM jobs WHERE id=?', (rid,)).fetchone()
            cases.append(dict(job) if job else {'id': rid, 'state': 'MISSING'})
        data = {'worker': worker, 'queue': queue, 'cases': cases}
        return {**data, 'state_token': token(data)}

    def overview(self):
        with self.store.connection() as db:
            snapshot = self._snapshot(db)
            history = [json.loads(x[0]) for x in db.execute('SELECT data FROM recovery_audit ORDER BY at DESC LIMIT 50')]
        cases = []
        for row in snapshot['cases']:
            context = self.store.context(row['id']) or {}
            cases.append({**row, 'plan_id': context.get('plan_id'), 'data_version_id': context.get('data_version_id')})
        return {**snapshot, 'cases': cases, 'history': history}

    def _probe(self, snapshot):
        tasks, blocking = [], []

        def issue(code, message, run_id=None):
            blocking.append({'code': code, 'message': message, 'run_id': run_id})

        def records(value):
            if not isinstance(value, dict) or any(not isinstance(v, dict) for v in value.values()):
                raise ValueError('Unexpected native response')
            return {str(k): v for k, v in value.items()}

        native = self.native or FreppleClient(base_url=os.environ.get('PLANNING_ENGINE_URL', 'http://127.0.0.1:9294'))
        active = {}
        try:
            active = {key: {k: value.get(k) for k in ['name', 'status', 'submitted', 'started', 'finished']}
                      for key, value in records(native.request('GET', '/execute/api/status/').json()).items()}
            if any(row.get('status') not in TERMINAL for row in active.values()):
                issue('NATIVE_QUEUE_ACTIVE', '引擎仍有运行中或等待中的任务，请待其结束后重新核对。')
        except Exception:
            issue('NATIVE_STATUS_UNAVAILABLE', '无法读取引擎当前队列，不能恢复。')
        if snapshot['worker']['state'] != 'PAUSED_RECOVERY_REQUIRED' or not snapshot['cases']:
            issue('NO_PAUSED_TASK', '当前没有需要人工恢复的暂停任务。')
        if any(row['state'] == 'RUNNING' for row in snapshot['queue']):
            issue('LOCAL_WORKER_ACTIVE', '计算服务仍有运行中任务，不能恢复。')
        for case in snapshot['cases']:
            rid = case['id']
            raw = self.store.root / 'runs' / rid / 'raw'
            uncertain = sorted(raw.glob('*-uncertain.json'))
            sources = uncertain or ([raw / 'solve-submit.json'] if (raw / 'solve-submit.json').is_file() else [])
            if case['state'] != 'FAILED' or not sources:
                issue('TASK_REFERENCE_MISSING', '缺少可核实的最后一次原生任务记录；继续暂停，请由运维核对原始日志。', rid)
            for path in sources:
                try:
                    data = json.loads(path.read_text(encoding='utf-8'))
                    phase = path.name.split('-')[0]
                    command = {'solve': 'runplan', 'import': 'importfromfolder'}[phase]
                    ref = data.get('task_ref') if uncertain else data.get('taskid') or data.get('id') or data.get('task_id')
                    if not ref or not re.fullmatch(r'[1-9][0-9]*', str(ref)):
                        issue('TASK_REFERENCE_MISSING', '未记录明确的原生任务编号，不能根据日期或产品猜测关联。', rid)
                        continue
                    value = records(native.request('GET', '/execute/api/status/', params={'id': str(ref)}).json()).get(str(ref))
                    if not value or value.get('name') != command:
                        issue('NATIVE_TASK_MISMATCH', '引擎没有返回编号与操作一致的任务记录。', rid)
                        continue
                    if value.get('status') not in TERMINAL:
                        issue('NATIVE_TASK_NOT_TERMINAL', '原生任务尚未处于明确终态，继续暂停。', rid)
                    tasks.append({'run_id': rid, 'phase': phase, 'task_ref': str(ref),
                                  'source_ref': str(path.relative_to(self.store.root)).replace('\\', '/'),
                                  'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                  'native': {k: value.get(k) for k in ['name', 'status', 'submitted', 'started', 'finished']}})
                except Exception:
                    issue('NATIVE_STATUS_UNAVAILABLE', '原生任务记录暂无法读取或校验，继续暂停。', rid)
        evidence = {'native_tasks': tasks, 'active_tasks': active, 'blocking_reasons': blocking}
        return {**evidence, 'evidence_token': token(evidence), 'can_resume': not blocking and bool(tasks)}

    def inspect(self):
        with self.store.connection() as db:
            snapshot = self._snapshot(db)
        checked = time.time()
        result = {**snapshot, **self._probe(snapshot), 'id': 'CHECK-' + uuid.uuid4().hex,
                  'checked_at': stamp(checked), 'expires_at': stamp(checked + 300)}
        with self.store.connection() as db:
            db.execute('INSERT INTO recovery_inspections VALUES(?,?,?)',
                       (result['id'], checked, json.dumps(result, ensure_ascii=False)))
        return result


    def resolve(self, body):
        fields = {'inspection_id', 'operator', 'reason', 'acknowledge_no_result'}
        if (set(body) != fields or body.get('acknowledge_no_result') is not True
                or not all(isinstance(body.get(k), str) and 0 < len(body[k].strip()) <= limit
                           for k, limit in [('operator', 80), ('reason', 500), ('inspection_id', 80)])):
            raise InputError('RECOVERY_INVALID_CONFIRMATION: 请填写核对人和依据，并明确确认本次不会恢复计划结果。')
        request_hash = token(body)
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT request_hash,data FROM recovery_audit WHERE inspection_id=?', (body['inspection_id'],)).fetchone()
            if previous:
                if previous[0] != request_hash:
                    raise InputError('RECOVERY_CONFIRMATION_CONFLICT: 该次核对已经由其他确认内容处理。')
                return json.loads(previous[1])
            row = db.execute('SELECT created,data FROM recovery_inspections WHERE id=?', (body['inspection_id'],)).fetchone()
            if not row:
                raise InputError('RECOVERY_INSPECTION_NOT_FOUND: 请先读取引擎状态。')
            if time.time() > row[0] + 300:
                raise InputError('RECOVERY_INSPECTION_EXPIRED: 核对已过期，请重新读取引擎状态。')
            checked = json.loads(row[1])
            snapshot = self._snapshot(db)
            if snapshot['state_token'] != checked['state_token']:
                raise InputError('RECOVERY_STATE_CHANGED: 暂停任务或等待队列已变化，请重新核对。')
            if not checked['can_resume']:
                raise InputError('RECOVERY_BLOCKED: 仍有未解决的任务状态，不能解除暂停。')
            fresh = self._probe(snapshot)
            if fresh['evidence_token'] != checked['evidence_token'] or not fresh['can_resume']:
                raise InputError('RECOVERY_EVIDENCE_CHANGED: 原生状态或来源已变化，请重新读取并核对。')
            result = {'id': 'REC-' + uuid.uuid4().hex, 'inspection_id': checked['id'],
                      'at': stamp(time.time()), 'operator': redact(body['operator'].strip()), 'reason': redact(body['reason'].strip()),
                      'state': 'RECOVERED_WITHOUT_RESULT', 'runs': snapshot['cases'],
                      'native_tasks': fresh['native_tasks'], 'queued_count': len(snapshot['queue']),
                      'message': '异常记录已结束，队列恢复。本次没有可用计划结果，异常任务未自动重算。'}
            for case in snapshot['cases']:
                db.execute("UPDATE jobs SET failure='RECOVERY_RECONCILED_NO_RESULT' WHERE id=?", (case['id'],))
            db.execute('DELETE FROM worker_metadata WHERE key=\'recovery\'')
            db.execute('INSERT INTO recovery_audit VALUES(?,?,?,?,?)',
                       (result['id'], checked['id'], request_hash, result['at'], json.dumps(result, ensure_ascii=False)))
        return result


def register_recovery(app, store, authorize, native=None):
    from fastapi import Body, Depends, HTTPException

    def call(action):
        try:
            return action()
        except InputError as error:
            code = str(error).split(':')[0]
            raise HTTPException(422 if code == 'RECOVERY_INVALID_CONFIRMATION' else 409,
                                detail={'code': code, 'message': redact(error)}) from None

    @app.get('/v1/operations/recovery', dependencies=[Depends(authorize)])
    def overview():
        return RecoveryService(store, native).overview()

    @app.post('/v1/operations/recovery/inspect', dependencies=[Depends(authorize)])
    def inspect():
        return call(lambda: RecoveryService(store, native).inspect())

    @app.post('/v1/operations/recovery/resolve', dependencies=[Depends(authorize)])
    def resolve(body: dict = Body(...)):
        return call(lambda: RecoveryService(store, native).resolve(body))
