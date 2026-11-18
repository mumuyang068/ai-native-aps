"""Task-local quote/hold overlay, never a second enterprise inventory ledger.

Like RunStore, each operation opens its own connection. BEGIN IMMEDIATE makes
the complete read/recompute/reserve operation atomic across threads/processes.
No enterprise SQL file or historical run file is read by this owner.
"""
import copy
import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from .atp import calculate_atp, digest, quantity, stamp, validate_identity, validate_request


def compact_calculation(result):
    keys = ('schema', 'identity', 'snapshot_sha256', 'request', 'status', 'available_quantity',
            'requested_quantity', 'promised_quantity', 'shortfall_quantity', 'effective_held_quantity',
            'source_slices', 'candidate_source_slices', 'earliest_source_ready_at', 'forecast_policy',
            'horizon_end', 'supply_policy', 'existing_consumption_policy', 'existing_consumption_note', 'execution')
    return {key: copy.deepcopy(result[key]) for key in keys}


class HoldStore:
    def __init__(self, root):
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'research-promising.sqlite3'
        with self.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS promising_quotes(id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS promising_actions(request_key TEXT PRIMARY KEY, request_hash TEXT NOT NULL, quote_id TEXT NOT NULL)')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                yield db
        finally:
            db.close()

    @staticmethod
    def _read(db, quote_id):
        row = db.execute('SELECT data FROM promising_quotes WHERE id=?', (quote_id,)).fetchone()
        if row is None:
            raise KeyError('PROMISING_QUOTE_NOT_FOUND')
        return json.loads(row[0])

    @staticmethod
    def _save(db, quote):
        db.execute('UPDATE promising_quotes SET data=? WHERE id=?',
                   (json.dumps(quote, ensure_ascii=False, allow_nan=False), quote['id']))

    @staticmethod
    def _transition(quote, state, now, **detail):
        quote['state'] = state; quote['updated_at'] = now.isoformat(); quote['revision'] += 1
        quote['events'].append(dict(sequence=len(quote['events'])+1, at=now.isoformat(), state=state, **detail))

    @staticmethod
    def _identity(quote, identity):
        if quote['identity'] != validate_identity(identity):
            raise ValueError('PROMISING_IDENTITY_MISMATCH')

    @staticmethod
    def _request_key(identity, action, request_key):
        if not isinstance(request_key, str) or not request_key.strip() or len(request_key) > 128:
            raise ValueError('PROMISING_INVALID_IDEMPOTENCY_KEY')
        return identity['plan_id'] + ':' + action + ':' + request_key

    @staticmethod
    def _replay(db, key, signature):
        previous = db.execute('SELECT request_hash,quote_id FROM promising_actions WHERE request_key=?', (key,)).fetchone()
        if previous:
            if previous[0] != signature:
                raise ValueError('PROMISING_IDEMPOTENCY_CONFLICT')
            return HoldStore._read(db, previous[1])
        return None

    @staticmethod
    def _remember(db, key, signature, quote_id):
        db.execute('INSERT INTO promising_actions VALUES(?,?,?)', (key, signature, quote_id))

    def _expire(self, db, now):
        expired = []
        for (text,) in db.execute('SELECT data FROM promising_quotes').fetchall():
            quote = json.loads(text)
            if quote['state'] == 'HELD' and stamp(quote['expires_at']) <= now:
                self._transition(quote, 'EXPIRED', now, reason='HOLD_EXPIRY_REACHED')
                self._save(db, quote); expired.append(quote['id'])
        return expired

    @staticmethod
    def _active(db):
        return [json.loads(row[0]) for row in db.execute('SELECT data FROM promising_quotes')
                if json.loads(row[0])['state'] in {'HELD', 'COMMITTED_SIMULATED'}]

    def create_inquiry(self, request, identity, request_key, now=None):
        identity = validate_identity(identity); request = validate_request(request)
        now = stamp(now); key = self._request_key(identity, 'INQUIRY', request_key)
        signature = digest(dict(request=request, identity=identity))
        with self.connection() as db:
            self._expire(db, now)
            replay = self._replay(db, key, signature)
            if replay:
                return replay
            quote = dict(id='PROM-'+uuid.uuid4().hex, request=request, identity=identity, state='INQUIRY',
                         created_at=now.isoformat(), updated_at=now.isoformat(), revision=1,
                         events=[dict(sequence=1, at=now.isoformat(), state='INQUIRY')],
                         calculation=None, snapshot_sha256=None, source_slices=[], expires_at=None,
                         execution='RESEARCH_ONLY_NO_ERP_WRITE')
            db.execute('INSERT INTO promising_quotes VALUES(?,?,?)',
                       (quote['id'], identity['plan_id'], json.dumps(quote, ensure_ascii=False)))
            self._remember(db, key, signature, quote['id'])
        return quote

    def calculate(self, quote_id, snapshot, identity, now=None):
        now = stamp(now)
        with self.connection() as db:
            self._expire(db, now); quote = self._read(db, quote_id); self._identity(quote, identity)
            if validate_identity(snapshot.get('identity')) != quote['identity']:
                raise ValueError('PROMISING_IDENTITY_MISMATCH')
            if quote['state'] not in {'INQUIRY', 'CALCULATED'}:
                raise ValueError('PROMISING_INVALID_TRANSITION')
            if quote['snapshot_sha256'] and quote['snapshot_sha256'] != digest(snapshot):
                raise ValueError('PROMISING_SNAPSHOT_CHANGED')
            result = calculate_atp(snapshot, quote['request'], self._active(db), now=now)
            quote['calculation'] = compact_calculation(result); quote['snapshot_sha256'] = result['snapshot_sha256']
            self._transition(quote, 'CALCULATED', now, snapshot_sha256=result['snapshot_sha256'])
            self._save(db, quote)
        return quote

    def offer(self, quote_id, identity, now=None):
        now = stamp(now)
        with self.connection() as db:
            self._expire(db, now); quote = self._read(db, quote_id); self._identity(quote, identity)
            if quote['state'] == 'OFFERED':
                return quote
            if quote['state'] != 'CALCULATED':
                raise ValueError('PROMISING_INVALID_TRANSITION')
            if quantity(quote['calculation']['promised_quantity']) <= 0:
                raise ValueError('PROMISING_NOT_FEASIBLE')
            self._transition(quote, 'OFFERED', now); self._save(db, quote)
        return quote

    def hold(self, quote_id, snapshot, identity, request_key, expires_at, now=None):
        identity = validate_identity(identity); now = stamp(now); expiry = stamp(expires_at)
        key = self._request_key(identity, 'HOLD', request_key)
        signature = digest(dict(quote_id=quote_id, identity=identity, snapshot_sha256=digest(snapshot), expires_at=expiry.isoformat()))
        with self.connection() as db:
            self._expire(db, now); quote = self._read(db, quote_id); self._identity(quote, identity)
            replay = self._replay(db, key, signature)
            if replay:
                return replay
            if quote['state'] != 'OFFERED':
                raise ValueError('PROMISING_INVALID_TRANSITION')
            if expiry <= now:
                raise ValueError('PROMISING_EXPIRY_NOT_FUTURE')
            if validate_identity(snapshot.get('identity')) != identity:
                raise ValueError('PROMISING_IDENTITY_MISMATCH')
            if digest(snapshot) != quote['snapshot_sha256']:
                raise ValueError('PROMISING_SNAPSHOT_CHANGED')
            # An accepted partial offer keeps its offered quantity. Competition
            # must never silently shrink the quantity the user accepted.
            accepted = dict(quote['request'], quantity=quote['calculation']['promised_quantity'], allow_partial=False)
            result = calculate_atp(snapshot, accepted, self._active(db), now=now)
            if result['status'] != 'FEASIBLE':
                raise ValueError('PROMISING_SUPPLY_COMPETED')
            quote['source_slices'] = result['source_slices']; quote['held_quantity'] = result['promised_quantity']
            quote['held_calculation'] = compact_calculation(result); quote['expires_at'] = expiry.isoformat()
            self._transition(quote, 'HELD', now, quantity=result['promised_quantity'], expires_at=expiry.isoformat(),
                             source_slices=result['source_slices'])
            self._save(db, quote); self._remember(db, key, signature, quote_id)
        return quote

    def _finish(self, quote_id, identity, request_key, state, now=None):
        identity = validate_identity(identity); now = stamp(now)
        key = self._request_key(identity, state, request_key)
        signature = digest(dict(quote_id=quote_id, identity=identity, state=state))
        with self.connection() as db:
            self._expire(db, now); quote = self._read(db, quote_id); self._identity(quote, identity)
            replay = self._replay(db, key, signature)
            if replay:
                return replay
            allowed = {'HELD', 'COMMITTED_SIMULATED'} if state == 'RELEASED' else {'HELD'}
            if quote['state'] not in allowed:
                raise ValueError('PROMISING_INVALID_TRANSITION')
            self._transition(quote, state, now); self._save(db, quote)
            self._remember(db, key, signature, quote_id)
        return quote

    def release(self, quote_id, identity, request_key, now=None):
        return self._finish(quote_id, identity, request_key, 'RELEASED', now)

    def commit_simulated(self, quote_id, identity, request_key, now=None):
        return self._finish(quote_id, identity, request_key, 'COMMITTED_SIMULATED', now)

    def expire(self, now=None):
        with self.connection() as db:
            return self._expire(db, stamp(now))

    def get(self, quote_id, now=None):
        with self.connection() as db:
            self._expire(db, stamp(now))
            return self._read(db, quote_id)

    def list(self, identity=None, now=None):
        if identity is not None:
            identity = validate_identity(identity)
        with self.connection() as db:
            self._expire(db, stamp(now))
            quotes = [json.loads(row[0]) for row in db.execute('SELECT data FROM promising_quotes ORDER BY rowid')]
        return [quote for quote in quotes if identity is None or quote['identity'] == identity]
