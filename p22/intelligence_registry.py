"""Plan-scoped research artifacts in the existing PlanStore database.

Canonical remains the enterprise fact owner. These are immutable research
datasets, forecast versions, policy candidates and revocations, plus bounded
job status records. No parallel forecast or inventory database is created.
"""
import hashlib
import json
import math
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone


PREFIXES = dict(dataset='DS-', version='FCV-', inventory='INVP-', job='FCJOB-', revocation='FCREV-')
JOB_FIELDS = {'state', 'error', 'elapsed_seconds', 'version_id', 'completed_at'}
JOB_TRANSITIONS = dict(QUEUED={'RUNNING'}, RUNNING={'COMPLETED', 'FAILED', 'INTERRUPTED'},
                       COMPLETED=set(), FAILED=set(), INTERRUPTED=set())


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


class ResearchRegistry:
    def __init__(self, plan_store, plan_id):
        plan_store.get(plan_id)
        self.plan_store = plan_store
        self.plan_id = plan_id
        with self.connection(write=True) as db:
            db.execute('CREATE TABLE IF NOT EXISTS professional_artifacts ('
                       'plan_id TEXT NOT NULL, kind TEXT NOT NULL, artifact_id TEXT NOT NULL, '
                       'request_id TEXT NOT NULL, payload_sha256 TEXT NOT NULL, record_json TEXT NOT NULL, '
                       'created_at TEXT NOT NULL, PRIMARY KEY(plan_id,kind,artifact_id), '
                       'UNIQUE(plan_id,kind,request_id))')

    @contextmanager
    def connection(self, write=False):
        db = self.plan_store.db()
        try:
            with db:
                if write:
                    db.execute('BEGIN IMMEDIATE')
                yield db
        finally:
            db.close()

    @staticmethod
    def kind(kind):
        if kind not in PREFIXES:
            raise ValueError('INVALID_ARTIFACT_KIND')
        return kind

    def _get(self, db, kind, artifact_id):
        row = db.execute('SELECT record_json FROM professional_artifacts '
                         'WHERE plan_id=? AND kind=? AND artifact_id=?',
                         (self.plan_id, kind, artifact_id)).fetchone()
        if row is None:
            raise ValueError(kind.upper() + '_NOT_FOUND')
        return json.loads(row[0])

    def create(self, kind, request_id, payload, build):
        kind = self.kind(kind)
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
            raise ValueError('INVALID_REQUEST_ID')
        try:
            payload_hash = hashlib.sha256(encoded(payload).encode()).hexdigest()
        except (ValueError, TypeError):
            raise ValueError('INVALID_ARTIFACT_PAYLOAD') from None
        with self.connection(write=True) as db:
            existing = db.execute('SELECT payload_sha256,record_json FROM professional_artifacts '
                                  'WHERE plan_id=? AND kind=? AND request_id=?',
                                  (self.plan_id, kind, request_id)).fetchone()
            if existing:
                if existing[0] != payload_hash:
                    raise ValueError('IDEMPOTENCY_CONFLICT')
                return json.loads(existing[1])
            record = build()
            if not isinstance(record, dict):
                raise ValueError('INVALID_ARTIFACT_RECORD')
            record = dict(record)
            if 'id' not in record:
                record['id'] = PREFIXES[kind] + uuid.uuid4().hex
            if not isinstance(record['id'], str) or not record['id'].strip():
                raise ValueError('INVALID_ARTIFACT_ID')
            if kind == 'job' and record.get('state') != 'QUEUED':
                raise ValueError('INVALID_INITIAL_JOB_STATE')
            text = encoded(record)
            try:
                db.execute('INSERT INTO professional_artifacts VALUES(?,?,?,?,?,?,?)',
                           (self.plan_id, kind, record['id'], request_id, payload_hash, text,
                            datetime.now(timezone.utc).isoformat()))
            except sqlite3.IntegrityError:
                raise ValueError('ARTIFACT_IMMUTABLE') from None
        return json.loads(text)

    def get(self, kind, artifact_id):
        kind = self.kind(kind)
        with self.connection() as db:
            return self._get(db, kind, artifact_id)

    def list(self, kind):
        kind = self.kind(kind)
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute(
                'SELECT record_json FROM professional_artifacts WHERE plan_id=? AND kind=? ORDER BY rowid DESC',
                (self.plan_id, kind))]

    def update_job(self, artifact_id, changes):
        if not isinstance(changes, dict) or set(changes) - JOB_FIELDS:
            raise ValueError('IMMUTABLE_JOB_FIELD')
        if 'elapsed_seconds' in changes:
            value = changes['elapsed_seconds']
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError('INVALID_JOB_ELAPSED_SECONDS')
        with self.connection(write=True) as db:
            record = self._get(db, 'job', artifact_id)
            current = record['state']; next_state = changes.get('state', current)
            if next_state != current and next_state not in JOB_TRANSITIONS.get(current, set()):
                raise ValueError('INVALID_JOB_TRANSITION')
            updated = dict(record, **changes)
            text = encoded(updated)
            db.execute('UPDATE professional_artifacts SET record_json=? WHERE plan_id=? AND kind=? AND artifact_id=?',
                       (text, self.plan_id, 'job', artifact_id))
        return json.loads(text)
