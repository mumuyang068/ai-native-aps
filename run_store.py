"""Task-local durable solve queue. It does not own enterprise business facts."""
import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from planning_adapter import InputError, validate_input

def now():return datetime.now(timezone.utc).isoformat()


def failure_summary(message):
    """Keep a bounded public error without cutting off the native refusal.

    The complete redacted message remains in failure.jsonl. Native tracebacks
    end in a domain code; keeping only their head used to hide that code and
    its exact object references behind long source paths.
    """
    limit=1200
    if len(message)<=limit:return message
    causes=[line.strip() for line in message.splitlines()
            if re.match(r'^(?:[A-Za-z_][A-Za-z0-9_]*Error|Exception): [A-Z][A-Z0-9_]+(?::|$)',line.strip())]
    if not causes or causes[-1] in message[:limit]:return message[:limit]
    cause=causes[-1][:600]
    marker='\n[... full redacted traceback retained in run failure log ...]\n'
    return message[:limit-len(marker)-len(cause)]+marker+cause


class RunStore:
    def __init__(self, root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/'jobs.sqlite3'
        with self.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, idem_key TEXT UNIQUE NOT NULL, input_hash TEXT NOT NULL, input_json TEXT NOT NULL, state TEXT NOT NULL, created TEXT NOT NULL, started TEXT, finished TEXT, failure TEXT, engine_task TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS job_context(id TEXT PRIMARY KEY, data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS worker_metadata(key TEXT PRIMARY KEY, data TEXT NOT NULL)')

    @contextmanager
    def connection(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()

    def submit(self, data, key, context=None):
        validate_input(data)
        if context is not None:
            from p22.contracts import enterprise_native
            enterprise_native(data,context)
        if not key or len(key)>128:raise InputError('INVALID_IDEMPOTENCY_KEY')
        text=json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        digest=hashlib.sha256(text.encode()).hexdigest()
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT * FROM jobs WHERE idem_key=?',(key,)).fetchone()
            if old:
                if old['input_hash']!=digest:raise InputError('IDEMPOTENCY_CONFLICT')
                saved=db.execute('SELECT data FROM job_context WHERE id=?',(old['id'],)).fetchone()
                if (json.loads(saved[0]) if saved else None)!=context:raise InputError('IDEMPOTENCY_CONFLICT')
                return self.record(old)
            run_id='API-'+uuid.uuid4().hex[:16]
            folder=self.root/'runs'/run_id;folder.mkdir(parents=True)
            (folder/'canonical_input.json').write_text(text,encoding='utf-8')
            db.execute('INSERT INTO jobs(id,idem_key,input_hash,input_json,state,created) VALUES(?,?,?,?,?,?)',
                (run_id,key,digest,text,'QUEUED',now()))
            if context is not None:
                context_text=json.dumps(context,ensure_ascii=False,sort_keys=True)
                db.execute('INSERT INTO job_context VALUES(?,?)',(run_id,context_text))
                (folder/'enterprise-context.json').write_text(context_text,encoding='utf-8')
            row=db.execute('SELECT * FROM jobs WHERE id=?',(run_id,)).fetchone()
        return self.record(row)

    def context(self,run_id):
        with self.connection() as db:row=db.execute('SELECT data FROM job_context WHERE id=?',(run_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def get(self, run_id):
        with self.connection() as db:row=db.execute('SELECT * FROM jobs WHERE id=?',(run_id,)).fetchone()
        if row is None:raise KeyError(run_id)
        return self.record(row)

    def metadata_many(self,run_ids):
        """Bounded lifecycle metadata, independent of native result availability."""
        if (not isinstance(run_ids,list) or not 1<=len(run_ids)<=20
            or any(not isinstance(rid,str) or not re.fullmatch(r'API-[a-f0-9]{16}',rid) for rid in run_ids)
            or len(set(run_ids))!=len(run_ids)):
            raise InputError('RUN_METADATA_QUERY_INVALID')
        placeholders=','.join('?' for _ in run_ids)
        with self.connection() as db:
            rows=db.execute("SELECT id,json_extract(input_json,'$.scenario_id') AS scenario_id,state,created,started,finished "
                'FROM jobs WHERE id IN ('+placeholders+')',run_ids).fetchall()
        found={r['id']:r for r in rows}
        return dict(schema_version='solve-run-metadata/1',items=[dict(solve_run_id=rid,scenario_id=found[rid]['scenario_id'],
            state=found[rid]['state'],created_at=found[rid]['created'],started_at=found[rid]['started'],finished_at=found[rid]['finished'])
            for rid in run_ids if rid in found],missing_run_ids=[rid for rid in run_ids if rid not in found],
            native_result_verified=False)

    def record(self,row):
        data=json.loads(row['input_json'])
        return {'solve_run_id':row['id'],'scenario_id':data['scenario_id'],'scenario_revision':data['scenario_revision'],
            'snapshot_id':data['snapshot_id'],'input_hash':row['input_hash'],'engine_profile':data['engine']['profile'],
            'state':row['state'],'engine_task_ref':row['engine_task'],
            'result_ref':'/v1/solve-runs/'+row['id']+'/result' if row['state']=='SUCCEEDED' else None,
            'failure_code':row['failure'],'created_at':row['created'],'started_at':row['started'],'finished_at':row['finished'],
            'worker_state':self.worker_state()['state']}

    def worker_state(self):
        with self.connection() as db:row=db.execute("SELECT data FROM worker_metadata WHERE key='recovery'").fetchone()
        return json.loads(row[0]) if row else {'state':'READY'}

    def claim(self):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM worker_metadata WHERE key='recovery'").fetchone():return None
            row=db.execute("SELECT * FROM jobs WHERE state='QUEUED' ORDER BY created LIMIT 1").fetchone()
            if not row:return None
            db.execute("UPDATE jobs SET state='RUNNING',started=? WHERE id=?",(now(),row['id']))
        return row['id'],json.loads(row['input_json'])

    def finish(self, run_id, result):
        context=self.context(run_id)
        if context is not None:
            folder=self.root/'runs'/run_id
            manifest={'run_id':run_id,'data_version_id':context.get('data_version_id'),'plan_id':context.get('plan_id'),
                      'snapshot_sha256':context.get('snapshot_sha256'),'engine':result['engine'],
                      'context_sha256':hashlib.sha256(json.dumps(context,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),
                      'files':{str(p.relative_to(folder)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in folder.rglob('*') if p.is_file() and p.name!='run-manifest.json'}}
            with (folder/'run-manifest.json').open('x',encoding='utf-8') as f:json.dump(manifest,f,ensure_ascii=False,indent=2)
        with self.connection() as db:
            db.execute("UPDATE jobs SET state='SUCCEEDED',finished=?,engine_task=? WHERE id=?",(now(),result['engine']['engine_task_ref'],run_id))

    def fail(self, run_id, error):
        from agent import redact
        message=redact(error);uncertain=message.startswith('RECOVERY_REQUIRED')
        with (self.root/'runs'/run_id/'failure.jsonl').open('a',encoding='utf-8') as f:
            f.write(json.dumps({'at':now(),'type':type(error).__name__,'message':message},ensure_ascii=False)+'\n')
        with self.connection() as db:
            db.execute("UPDATE jobs SET state='FAILED',finished=?,failure=? WHERE id=?",(now(),'RECOVERY_REQUIRED' if uncertain else failure_summary(message),run_id))
            if uncertain:self._pause(db,[run_id],'NATIVE_TASK_OUTCOME_UNKNOWN')

    def _pause(self, db, run_ids, reason):
        previous=db.execute("SELECT data FROM worker_metadata WHERE key='recovery'").fetchone()
        old=json.loads(previous[0]) if previous else {}
        record={'state':'PAUSED_RECOVERY_REQUIRED','run_ids':sorted(set(old.get('run_ids',[])+run_ids)),
                'reason':reason,'at':now(),'generation':uuid.uuid4().hex}
        db.execute("INSERT OR REPLACE INTO worker_metadata VALUES('recovery',?)",(json.dumps(record),))

    def stop_interrupted(self):
        # An interrupted engine submission may already exist: never blind-resubmit.
        with self.connection() as db:
            rows=[r[0] for r in db.execute("SELECT id FROM jobs WHERE state='RUNNING'")]
            db.execute("UPDATE jobs SET state='FAILED',finished=?,failure='RECOVERY_REQUIRED' WHERE state='RUNNING'",(now(),))
            if rows:self._pause(db,rows,'SERVICE_INTERRUPTED')

    def result(self, run_id):
        if self.get(run_id)['state']!='SUCCEEDED':raise InputError('RESULT_NOT_READY')
        return json.loads((self.root/'runs'/run_id/'PlanResult.json').read_text(encoding='utf-8'))
