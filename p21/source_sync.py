"""One immutable batch per import and atomic independent ERP/MES/WMS heads.

Input is source-visibility/1, never private world state.  This store confirms
source transcription only; cross-system business confirmation is a later owner.

The original continuity_sync.store Owner is retained here, including its merge
contract. Transaction views share its immutable raw/batch archive; only their
index is staged until the P2 Owner has saved a version successfully.
"""
import copy
import hashlib
import json
import os
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path


SYSTEMS = {'ERP', 'MES', 'WMS'}
MODES = {'FULL_SNAPSHOT', 'INCREMENTAL_UPSERT'}
IDENTITY_FIELDS = {'unit', 'order_id', 'product_id', 'item_id', 'po_id', 'receipt_id',
                   'lot_id', 'job_id', 'operation_id', 'customer_id', 'supplier_id'}
QUANTITY_FIELDS = {'quantity', 'received', 'outstanding', 'shipped', 'physical',
                   'pending', 'available', 'held', 'consumed', 'completed_quantity', 'actual_quantity'}
STATUSES = {'OPEN', 'PARTIAL', 'SHIPPED', 'RECEIVED', 'RELEASED', 'WIP',
            'STARTED', 'COMPLETED', 'CANCELLED'}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _hash(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _time(value):
    if not isinstance(value, str):
        raise ValueError('TIMESTAMP_REQUIRED')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('TIMEZONE_REQUIRED')
    return result


def _business(row):
    return {key: value for key, value in row.items() if key != 'aps_read_at'}


def _formal_manufacturing_update(before,row):
    """New unexecuted MO states are scoped to the manufacturing source table.

    This accepts an external document postimage, never an execution event or a
    planning authorization. Source mapping still proves its complete routing.
    """
    formal=row.get('status') in {'DRAFT','CONFIRMED'}
    if not formal and not (before and before.get('plan_operations')):return
    ops=row.get('plan_operations');version=row.get('external_version')
    if type(version) is not int or version<1 or not isinstance(ops,list) or not ops:
        raise ValueError('FORMAL_MO_POSTIMAGE_REQUIRED:'+row['id'])
    if formal and Decimal(str(row.get('completed_quantity',-1)))!=0:
        raise ValueError('FORMAL_MO_CANNOT_HAVE_ACTUAL:'+row['id'])
    for operation in ops:
        if (not isinstance(operation,dict) or not all(operation.get(k) for k in ('operation_id','resource_id','operator_id')) or
            Decimal(str(operation.get('quantity',0)))!=Decimal(str(row['quantity'])) or
            _time(operation['start_at'])>=_time(operation['end_at'])):
            raise ValueError('FORMAL_MO_ROUTING_POSTIMAGE_REQUIRED:'+row['id'])
    if before and before.get('plan_operations'):
        old=before.get('external_version')
        if type(old) is not int or version<old:raise ValueError('FORMAL_MO_VERSION_REGRESSED:'+row['id'])
        if before['status'] in {'WIP','COMPLETED','RELEASED'}:
            if formal or before['plan_operations']!=ops:raise ValueError('FORMAL_MO_ACTUAL_REWRITE:'+row['id'])
        elif before['status']=='CONFIRMED' and row['status']=='DRAFT':raise ValueError('FORMAL_MO_STATE_REGRESSED:'+row['id'])
        if before['plan_operations']!=ops and version<=old:raise ValueError('FORMAL_MO_VERSION_REQUIRED:'+row['id'])


def _operation_execution_update(before, row, key):
    """Ordinary source revisions can advance execution, never rewrite actuals."""
    original_fields = {'quantity', 'start', 'end', 'resource_id', 'operator_id'}
    terminal_fields = original_fields | {'status', 'actual_quantity', 'actual_end', 'interruption_reason'}

    def same(field):
        if (field in before) != (field in row):
            return False
        if field not in before:
            return True
        if field in {'quantity', 'actual_quantity'}:
            return Decimal(str(before[field])) == Decimal(str(row[field]))
        if field in {'start', 'end', 'actual_end'}:
            return _time(before[field]) == _time(row[field])
        return before[field] == row[field]

    if before.get('status') in {'COMPLETED', 'INTERRUPTED'}:
        if not all(same(field) for field in terminal_fields):
            raise ValueError('OPERATION_TERMINAL_FACT_CHANGED:' + key)
    elif before.get('status') == 'STARTED':
        if not all(same(field) for field in original_fields):
            raise ValueError('OPERATION_EXECUTION_FACT_CHANGED:' + key)
        if row.get('status') not in {'STARTED', 'COMPLETED', 'INTERRUPTED'}:
            raise ValueError('OPERATION_EXECUTION_STATUS_REGRESSION:' + key)
    elif row.get('status') == 'INTERRUPTED':
        raise ValueError('OPERATION_INTERRUPTION_REQUIRES_STARTED:' + key)


def _normalized(state):
    return {table: sorted((_business(row) for row in rows), key=lambda row: row['id'])
            for table, rows in state.items() if rows}


def state_equivalent(left, right):
    """Compare source tables, ignoring read time and empty-table serialization."""
    return _normalized(left) == _normalized(right)


def _head_metadata(batch):
    return {key:copy.deepcopy(batch[key]) for key in ('schema','batch_id','enterprise_id','system','source_path','source_sha256',
        'cursor','imported_at','as_of','mode','previous_batch_id','projection_ready','status') if key in batch}


def _write(path, value):
    """Flush a sibling temporary file, then atomically replace the target."""
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('w', encoding='utf-8', newline='\n') as stream:
            stream.write(_json(value) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


class SyncStore:
    def __init__(self, root, enterprise_id='SIM-GARMENT-CONT-01', *, materialize=None):
        self.root = Path(root)
        self.enterprise_id = enterprise_id
        self._transaction_id = None
        self._stage_dir = None
        self._materialize = materialize
        self.root.mkdir(parents=True, exist_ok=True)
        for folder in ('raw', 'batches'):
            (self.root / folder).mkdir(exist_ok=True)
        # One explicit metadata migration for an inherited source index. New
        # batches write this small view at their original atomic commit point.
        if (self.root/'index.json').exists():
            with self._lock():
                index=self._index();heads=index.setdefault('head_metadata',{});changed=False
                for system,identifier in index['systems'].items():
                    if heads.get(system,{}).get('batch_id')!=identifier:
                        heads[system]=_head_metadata(self.batch(identifier));changed=True
                if changed:_write(self.root/'index.json',index)

    def _read_index(self, path):
        if not path.exists():
            return {'schema': 'continuity-sync-index/1', 'enterprise_id': self.enterprise_id,
                    'systems': {}, 'deliveries': {}}
        result = json.loads(path.read_text(encoding='utf-8'))
        if result['enterprise_id'] != self.enterprise_id:
            raise ValueError('STORE_ENTERPRISE_IDENTITY_MISMATCH')
        return result

    def _index_path(self):
        return (self._stage_dir or self.root) / 'index.json'

    def _index(self):
        return self._read_index(self._index_path())

    def _journal(self):
        if self._stage_dir is None:
            raise ValueError('SOURCE_TRANSACTION_REQUIRED')
        journal = json.loads((self._stage_dir / 'journal.json').read_text(encoding='utf-8'))
        if (journal.get('schema') != 'source-sync-transaction/1'
                or journal.get('enterprise_id') != self.enterprise_id
                or journal.get('transaction_id') != self._transaction_id):
            raise ValueError('SOURCE_TRANSACTION_IDENTITY_MISMATCH')
        return journal

    @property
    def expected_heads(self):
        """The complete formal head vector pinned when this preview began."""
        return copy.deepcopy(self._journal()['expected_heads'])

    def stage(self, transaction_id):
        """Open a recoverable index view of this Owner, without copying facts.

        Orphan raw/batch files are immutable audit evidence, not accepted heads.
        Reopening a transaction always restores its original base and preview.
        """
        if (not isinstance(transaction_id, str)
                or not re.fullmatch(r'SYNC-[a-f0-9]{32}', transaction_id)):
            raise ValueError('INVALID_SOURCE_TRANSACTION_ID')
        if self._stage_dir is not None:
            raise ValueError('SOURCE_FORMAL_OWNER_REQUIRED')
        view = copy.copy(self)
        view._transaction_id = transaction_id
        view._stage_dir = self.root / 'previews' / transaction_id
        with self._lock():
            if view._stage_dir.resolve() != self.root.resolve() / 'previews' / transaction_id:
                raise ValueError('SOURCE_TRANSACTION_PATH_ESCAPE')
            view._stage_dir.mkdir(parents=True, exist_ok=True)
            journal_path = view._stage_dir / 'journal.json'
            if not journal_path.exists():
                base = self._index()
                _write(journal_path, {'schema': 'source-sync-transaction/1',
                    'enterprise_id': self.enterprise_id, 'transaction_id': transaction_id,
                    'status': 'STAGED', 'expected_heads': copy.deepcopy(base['systems']),
                    'base_index': base})
            journal = view._journal()
            if not view._index_path().exists():
                _write(view._index_path(), journal['base_index'])
            view._index()
        return view

    def publish(self, expected_heads, *, data_version_id):
        """Publish every staged system head after the P2 version was saved.

        The caller supplies P2's completed version identifier. The formal index
        is the one atomic commit point and carries the idempotency receipt too,
        so a crash before the journal update cannot publish a second time.
        """
        with self._lock():
            return self._publish_unlocked(expected_heads, data_version_id)

    def _publication_state(self, expected_heads):
        """Validate the base while the caller holds this Owner's OS lock."""
        journal = self._journal()
        if not isinstance(expected_heads, dict) or expected_heads != journal['expected_heads']:
            raise ValueError('SOURCE_EXPECTED_HEADS_MISMATCH')
        formal = self._read_index(self.root / 'index.json')
        prior = formal.get('publications', {}).get(self._transaction_id)
        if prior is None:
            if formal['systems'] != expected_heads:
                raise ValueError('SOURCE_HEAD_CONFLICT')
            staged = self._index()
            if not staged['systems'] or any(not self.batch(bid)['projection_ready']
                                           for bid in staged['systems'].values()):
                raise ValueError('SOURCE_TRANSACTION_NOT_READY')
        return journal, formal, prior

    def _publish_unlocked(self, expected_heads, data_version_id):
        if (not isinstance(data_version_id, str)
                or not re.fullmatch(r'DATA-[a-f0-9]{12}', data_version_id)):
            raise ValueError('SOURCE_DATA_VERSION_REQUIRED')
        journal, formal, prior = self._publication_state(expected_heads)
        if prior is not None:
            if prior['data_version_id'] != data_version_id:
                raise ValueError('SOURCE_TRANSACTION_VERSION_CONFLICT')
            _write(self._stage_dir / 'journal.json',
                   {**journal, 'status': 'PUBLISHED', 'publication': prior})
            return copy.deepcopy(prior)
        staged = self._index()
        publication = {'transaction_id': self._transaction_id,
            'enterprise_id': self.enterprise_id, 'data_version_id': data_version_id,
            'expected_heads': copy.deepcopy(expected_heads),
            'systems': copy.deepcopy(staged['systems']),
            'published_at': datetime.now(timezone.utc).isoformat()}
        # Keep prior publication receipts while replacing only the heads and
        # delivery identities prepared against this exact base.
        staged['publications'] = copy.deepcopy(formal.get('publications', {}))
        staged['publications'][self._transaction_id] = publication
        _write(self.root / 'index.json', staged)
        _write(self._stage_dir / 'journal.json',
               {**journal, 'status': 'PUBLISHED', 'publication': publication})
        return copy.deepcopy(publication)

    def publish_after(self, expected_heads, save_version_callback):
        """Hold the source lock across P2 save and atomic cursor publication.

        The callback uses the original ImportStore Owner and returns its saved
        DATA id. Failure leaves the formal cursor untouched. A process death
        after P2 save is completed by the caller's durable intent recovery and
        ordinary publish(); an existing publication never invokes P2 again.
        """
        with self._lock():
            journal, formal, prior = self._publication_state(expected_heads)
            if prior is not None:
                return self._publish_unlocked(expected_heads, prior['data_version_id'])
            data_version_id = save_version_callback()
            return self._publish_unlocked(expected_heads, data_version_id)

    @contextmanager
    def _lock(self):
        path = self.root / 'writer.lock'
        # The file is a persistent lock target, not a claim of liveness. The OS
        # releases the byte/flock when a process dies, without stealing a live
        # writer's lock or depending on PID reuse/stale-file heuristics.
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR)
        acquired = False
        try:
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b'\0')
            os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                raise ValueError('SOURCE_WRITER_BUSY: another source writer is active') from None
            yield
        finally:
            if acquired:
                if os.name == 'nt':
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def batch(self, batch_id):
        if not isinstance(batch_id, str) or not re.fullmatch(r'B-[a-f0-9]{32}', batch_id):
            raise ValueError('INVALID_BATCH_ID')
        return json.loads((self.root / 'batches' / (batch_id + '.json')).read_text(encoding='utf-8'))

    def head(self, system):
        """Read source-watermark metadata; never materialize a business state."""
        if system not in SYSTEMS:
            raise ValueError('UNKNOWN_SYSTEM')
        index=self._index();batch_id=index['systems'].get(system)
        if not batch_id:return None
        header=index.get('head_metadata',{}).get(system)
        if header is not None:
            if header.get('batch_id')!=batch_id:raise ValueError('SOURCE_HEAD_METADATA_MISMATCH')
            return copy.deepcopy(header)
        return _head_metadata(self.batch(batch_id))

    def current(self, system):
        """Legacy explicit full-state read; normal indexed sync uses head()."""
        batch = self.head(system)
        if batch is not None:batch=self.batch(batch['batch_id'])
        if batch is None or 'state' in batch:
            return batch
        if self._materialize is None:
            raise ValueError('SOURCE_STATE_MATERIALIZER_REQUIRED')
        ref = batch.get('state_ref', {})
        publication = self._read_index(self.root / 'index.json').get('publications', {}).get(ref.get('transaction_id'))
        version = publication['data_version_id'] if publication else ref.get('parent_version_id')
        snapshot = self._materialize(version)
        document = snapshot['documents'][system]
        state = copy.deepcopy(document['tables'])
        event_refs = copy.deepcopy(document['event_refs'])
        if publication is None:
            # Preview full reads are explicit. Its base stays the confirmed P2
            # parent; only this transaction's postimages are overlaid.
            for change in batch['changes']:
                table, identifier = change['table'], change['record_id']
                rows = {row['id']: row for row in state.setdefault(table, [])}
                rows[identifier] = copy.deepcopy(change['row'])
                state[table] = list(rows.values())
                event_refs.setdefault(table, {})[identifier] = copy.deepcopy(change['event_refs'])
        revisions = {table: {row['id']: dict(revision=_hash({'business': _business(row), 'event_refs': sorted(set(event_refs[table][row['id']]))}),
            source_known_at=row['source_known_at'], business_occurred_at=row['business_occurred_at'], event_refs=event_refs[table][row['id']])
            for row in rows} for table, rows in state.items()}
        return dict(batch, state=state, revisions=revisions, event_refs=event_refs)

    def apply_indexed(self, package_path, *, parent_version_id, changes):
        """Stage already locally audited postimages in this same source Owner.

        This internal path is not an input receipt or an alternative publication
        API. P2 still saves/validates the task and publish_after owns the cursor.
        The source bytes and every supplied postimage are matched again here.
        """
        if self._stage_dir is None:
            raise ValueError('SOURCE_TRANSACTION_REQUIRED')
        if not isinstance(parent_version_id, str) or not re.fullmatch('DATA-[a-f0-9]{12}', parent_version_id):
            raise ValueError('SOURCE_DATA_VERSION_REQUIRED')
        raw = Path(package_path).read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        package = json.loads(raw.decode('utf-8-sig'))
        system = package.get('system')
        if (system not in SYSTEMS or package.get('enterprise_id') != self.enterprise_id or
                package.get('schema') != 'source-visibility/1' or package.get('mode') != 'INCREMENTAL_UPSERT'):
            raise ValueError('UNSUPPORTED_SOURCE_CONTRACT')
        if package.get('absent_from_snapshot') and any(package['absent_from_snapshot'].values()):
            raise ValueError('SOURCE_ABSENCE_REVIEW_REQUIRED')
        if package.get('unresolved_events'):
            raise ValueError('SOURCE_REVIEW_REQUIRED')
        expected = {(table, row['id']): row for table, rows in package['tables'].items() for row in rows}
        chosen = [change for change in changes if change['system'] == system]
        if (len(expected) != sum(map(len, package['tables'].values())) or len(chosen) != len(expected) or
                {(change['table'], change['id']): change['row'] for change in chosen} != expected or
                any(change['source_sha256'] != sha or change['event_refs'] != sorted(set(package['event_refs'][change['table']][change['id']])) for change in chosen)):
            raise ValueError('SOURCE_CHANGESET_ORIGINAL_MISMATCH')
        with self._lock():
            if self._transaction_id in self._read_index(self.root / 'index.json').get('publications', {}):
                raise ValueError('SOURCE_TRANSACTION_PUBLISHED')
            index = self._index()
            old_id = index['systems'].get(system)
            old = self.head(system) if old_id else None
            if not old or package.get('previous_as_of') != old['as_of']:
                raise ValueError('INCREMENTAL_CURSOR_GAP')
            if _time(package['as_of']) < _time(old['as_of']):
                raise ValueError('STALE_UNSEEN_DELIVERY')
            delivery_key = _hash({key: value for key, value in package.items() if key not in {'delivery_id', 'tables'}} | {'tables': _normalized(package['tables'])})
            duplicate = index['deliveries'].get(system, {}).get(delivery_key)
            if duplicate:
                return dict(old, status='DUPLICATE', duplicate_of=duplicate)
            target = self.root / 'raw' / (sha + '.json')
            if target.exists():
                if hashlib.sha256(target.read_bytes()).hexdigest() != sha:
                    raise ValueError('RAW_ARCHIVE_HASH_MISMATCH')
            else:
                with target.open('xb') as stream:
                    stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            batch = dict(schema='continuity-sync-batch/2', batch_id='B-' + uuid.uuid4().hex, enterprise_id=self.enterprise_id,
                system=system, source_path=str(target.resolve()), source_sha256=sha, raw_path='raw/' + target.name,
                imported_at=datetime.now(timezone.utc).isoformat(), source_as_of=package['as_of'], as_of=package['as_of'],
                mode=package['mode'], previous_batch_id=old_id, requested_previous_batch_id=old_id,
                status='COMMITTED', projection_ready=True, pending=[], correction_approvals=[],
                state_ref=dict(transaction_id=self._transaction_id, parent_version_id=parent_version_id),
                changes=[dict(change, record_id=change['id']) for change in chosen])
            batch['cursor'] = 'C-' + _hash(dict(enterprise_id=self.enterprise_id, system=system, as_of=batch['as_of'],
                previous_cursor=old['cursor'], revisions=[(c['table'], c['id'], c['revision']) for c in chosen]))
            _write(self.root / 'batches' / (batch['batch_id'] + '.json'), batch)
            index['systems'][system] = batch['batch_id']
            index.setdefault('head_metadata',{})[system]=_head_metadata(batch)
            index['deliveries'].setdefault(system, {})[delivery_key] = batch['batch_id']
            _write(self._index_path(), index)
            return batch

    def apply(self, package_path, previous_batch_id=None, *, confirmed_corrections=None):
        """Persist input and audit; commit all records or keep the prior valid head.

        Explicit correction approvals are out-of-band reviewed evidence bound to
        the exact raw SHA and old revision. Nothing in an input package approves
        its own correction. This method never triggers APS import or planning.
        """
        raw = Path(package_path).read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        with self._lock():
            if (self._stage_dir is not None
                    and self._transaction_id in self._read_index(self.root / 'index.json').get('publications', {})):
                raise ValueError('SOURCE_TRANSACTION_PUBLISHED')
            raw_path = Path('raw') / (sha + '.json')
            target = self.root / raw_path
            if not target.exists():
                with target.open('xb') as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
            elif hashlib.sha256(target.read_bytes()).hexdigest() != sha:
                raise ValueError('RAW_ARCHIVE_HASH_MISMATCH')
            index = self._index()
            batch = {'schema': 'continuity-sync-batch/1', 'batch_id': 'B-' + uuid.uuid4().hex,
                     'enterprise_id': self.enterprise_id, 'source_path': str(Path(package_path).resolve()),
                     'source_sha256': sha, 'raw_path': raw_path.as_posix(),
                     'imported_at': datetime.now(timezone.utc).isoformat(), 'changes': [], 'pending': [],
                     'correction_approvals': copy.deepcopy(confirmed_corrections or [])}
            package = {}
            old = None
            try:
                package = json.loads(raw.decode('utf-8-sig'))
                if not isinstance(package, dict):
                    raise ValueError('PACKAGE_OBJECT_REQUIRED')
                system = package.get('system')
                if system not in SYSTEMS:
                    raise ValueError('UNKNOWN_SYSTEM')
                old_id = index['systems'].get(system)
                old = self.batch(old_id) if old_id else None
                if old is not None and 'state' not in old:
                    # A declared full-domain fallback is an explicit full read;
                    # normal indexed delivery never takes this legacy branch.
                    old = self.current(system)
                batch.update(system=system, source_as_of=package.get('as_of'), mode=package.get('mode'),
                             previous_batch_id=old_id, requested_previous_batch_id=previous_batch_id)
                if package.get('enterprise_id') != self.enterprise_id:
                    raise ValueError('ENTERPRISE_IDENTITY_MISMATCH')
                if package.get('schema') != 'source-visibility/1' or package.get('mode') not in MODES:
                    raise ValueError('UNSUPPORTED_SOURCE_CONTRACT')
                as_of = _time(package['as_of'])
                tables = package.get('tables')
                if not isinstance(tables, dict):
                    raise ValueError('TABLES_REQUIRED')
                for table, rows in tables.items():
                    if not isinstance(table, str) or not isinstance(rows, list):
                        raise ValueError('MALFORMED_TABLE')
                    if any(not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id'] for row in rows):
                        raise ValueError('BUSINESS_RECORD_ID_REQUIRED')
                for field in ('event_refs', 'record_actions', 'absent_from_snapshot'):
                    mapping = package.get(field, {})
                    if not isinstance(mapping, dict):
                        raise ValueError('MALFORMED_' + field.upper())
                    expected_type = list if field == 'absent_from_snapshot' else dict
                    if any(not isinstance(value, expected_type) for value in mapping.values()):
                        raise ValueError('MALFORMED_' + field.upper())
                if any(not isinstance(action, dict) for mapping in package.get('record_actions', {}).values() for action in mapping.values()):
                    raise ValueError('MALFORMED_RECORD_ACTION')
                if not isinstance(package.get('unresolved_events', []), list):
                    raise ValueError('MALFORMED_UNRESOLVED_EVENTS')
                # Delivery identity excludes transport labels and per-read timestamps.
                delivery_key = _hash({key: value for key, value in package.items()
                                      if key not in {'delivery_id', 'tables'}} | {'tables': _normalized(tables)})
                duplicate_of = index['deliveries'].get(system, {}).get(delivery_key)
                if duplicate_of:
                    batch.update(status='DUPLICATE', duplicate_of=duplicate_of, as_of=old['as_of'],
                                 cursor=old['cursor'], state=copy.deepcopy(old['state']),
                                 revisions=copy.deepcopy(old['revisions']), pending=copy.deepcopy(old['pending']),
                                 projection_ready=old['projection_ready'], event_refs=copy.deepcopy(old['event_refs']),
                                 changes=[{'classification': 'DUPLICATE', 'scope': 'SOURCE_DELIVERY', 'flags': []}])
                    _write(self.root / 'batches' / (batch['batch_id'] + '.json'), batch)
                    return batch
                if previous_batch_id is not None and previous_batch_id != old_id:
                    raise ValueError('PREVIOUS_BATCH_MISMATCH')
                if old and as_of < _time(old['as_of']):
                    raise ValueError('STALE_UNSEEN_DELIVERY')
                if package['mode'] == 'INCREMENTAL_UPSERT':
                    previous_as_of = package.get('previous_as_of')
                    if old and (not previous_as_of or _time(previous_as_of) != _time(old['as_of'])):
                        raise ValueError('INCREMENTAL_CURSOR_GAP')
                    if not old and previous_as_of is not None:
                        raise ValueError('INCREMENTAL_REQUIRES_BASELINE')
                batch.update(self._merge(package, old, sha, confirmed_corrections or []))
                batch['status'] = 'COMMITTED'
                batch['as_of'] = package['as_of']
                batch['cursor'] = 'C-' + _hash({'enterprise_id': self.enterprise_id, 'system': system,
                    'as_of': package['as_of'], 'revisions': batch['revisions'], 'pending': batch['pending']})
            except (ValueError, TypeError, KeyError, InvalidOperation) as error:
                batch.update(status='BLOCKED', as_of=old['as_of'] if old else None,
                             cursor=old['cursor'] if old else None,
                             state=copy.deepcopy(old['state']) if old else {},
                             revisions=copy.deepcopy(old['revisions']) if old else {},
                             event_refs=copy.deepcopy(old['event_refs']) if old else {},
                             projection_ready=False,
                             changes=[{'classification': 'CONFLICT', 'reason': str(error), 'flags': []}],
                             pending=[{'classification': 'CONFLICT', 'reason': str(error),
                                       'source_sha256': sha, 'requires_review': True}])
            # Immutable batch is durable before the single atomic commit point.
            _write(self.root / 'batches' / (batch['batch_id'] + '.json'), batch)
            if batch['status'] == 'COMMITTED':
                index['systems'][batch['system']] = batch['batch_id']
                index.setdefault('head_metadata',{})[batch['system']]=_head_metadata(batch)
                index['deliveries'].setdefault(batch['system'], {})[delivery_key] = batch['batch_id']
                _write(self._index_path(), index)
            return batch

    def _merge(self, package, old, sha, approvals):
        return merge_records(package, old, sha, approvals)


def merge_records(package, old, sha, approvals):
    """Pure SourceSync postimage rules, shared by indexed and legacy state paths."""
    state = {table: {row['id']: copy.deepcopy(row) for row in rows}
             for table, rows in (old['state'] if old else {}).items()}
    revisions = copy.deepcopy(old['revisions']) if old else {}
    all_refs = copy.deepcopy(old['event_refs']) if old else {}
    pending = copy.deepcopy(old['pending']) if old else []
    changes = []
    incoming_keys = set()
    as_of = _time(package['as_of'])
    for table, rows in package['tables'].items():
        if not isinstance(table, str) or not isinstance(rows, list):
            raise ValueError('MALFORMED_TABLE')
        seen = set()
        state.setdefault(table, {})
        revisions.setdefault(table, {})
        all_refs.setdefault(table, {})
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id']:
                raise ValueError('BUSINESS_RECORD_ID_REQUIRED')
            key = row['id']
            if key in seen:
                raise ValueError('DUPLICATE_RECORD_ID:' + table + ':' + key)
            seen.add(key)
            incoming_keys.add((table, key))
            known = _time(row['source_known_at'])
            occurred = _time(row['business_occurred_at'])
            if occurred > as_of or known > as_of or known < occurred:
                raise ValueError('SOURCE_VISIBILITY_TIME_CONFLICT:' + table + ':' + key)
            if row.get('aps_read_at') and _time(row['aps_read_at']) > as_of:
                raise ValueError('READ_TIME_AFTER_AS_OF')
            refs = package.get('event_refs', {}).get(table, {}).get(key)
            if not isinstance(refs, list) or not refs or not all(isinstance(value, str) and value for value in refs):
                raise ValueError('SOURCE_REVISION_EVIDENCE_REQUIRED:' + table + ':' + key)
            refs = sorted(set(refs))
            interrupted=table=='operations' and package['system']=='MES' and row.get('status')=='INTERRUPTED'
            formal=table=='manufacturing_tasks' and package['system']=='MES' and row.get('status') in {'DRAFT','CONFIRMED'}
            if table=='manufacturing_tasks' and package['system']=='MES':_formal_manufacturing_update(state[table].get(key),row)
            if 'status' in row and row['status'] not in STATUSES and not interrupted and not formal:
                raise ValueError('UNKNOWN_STATUS:' + table + ':' + key)
            if interrupted:
                required={'quantity','actual_quantity','start','end','actual_end','interruption_reason','job_id','operation_id','resource_id','operator_id'}
                if not required<=row.keys() or not isinstance(row['interruption_reason'],str) or not row['interruption_reason'].strip():
                    raise ValueError('INTERRUPTION_POSTIMAGE_REQUIRED:'+key)
                actual=Decimal(str(row['actual_quantity']));planned=Decimal(str(row['quantity']))
                if not actual.is_finite() or not planned.is_finite() or not 0<=actual<planned or actual!=actual.to_integral_value() or not _time(row['start'])<=_time(row['actual_end'])<=_time(row['end']) or _time(row['actual_end'])>as_of:
                    raise ValueError('INVALID_INTERRUPTION_POSTIMAGE:'+key)
            for field in QUANTITY_FIELDS & row.keys():
                amount = Decimal(str(row[field]))
                if not amount.is_finite() or amount < 0:
                    raise ValueError('INVALID_QUANTITY:' + table + ':' + key + ':' + field)
            balance_fields = {'purchase_orders': {'quantity', 'received', 'outstanding'},
                'sales_orders': {'quantity', 'shipped'},
                'manufacturing_tasks': {'quantity', 'completed_quantity'},
                'lots': {'quantity', 'physical', 'pending', 'available', 'held', 'consumed'}}.get(table, set())
            if not balance_fields <= row.keys():
                raise ValueError('INCOMPLETE_BALANCE_POSTIMAGE:' + table + ':' + key)
            amounts = {field: Decimal(str(row[field])) for field in balance_fields}
            invalid_balance = (
                table == 'purchase_orders' and amounts['quantity'] != amounts['received'] + amounts['outstanding']
                or table == 'sales_orders' and amounts['shipped'] > amounts['quantity']
                or table == 'manufacturing_tasks' and amounts['completed_quantity'] > amounts['quantity']
                or table == 'lots' and (amounts['physical'] != amounts['pending'] + amounts['available'] + amounts['held']
                    or amounts['quantity'] != amounts['physical'] + amounts['consumed']))
            if invalid_balance:
                raise ValueError('SOURCE_BALANCE_CONFLICT:' + table + ':' + key)
            revision = _hash({'business': _business(row), 'event_refs': refs})
            before = state[table].get(key)
            old_revision = revisions[table].get(key)
            action = package.get('record_actions', {}).get(table, {}).get(key, {})
            kind = action.get('kind')
            if kind and kind not in {'CANCELLED', 'CORRECTED'}:
                raise ValueError('UNKNOWN_RECORD_ACTION')
            if kind and action.get('event_id') not in refs:
                raise ValueError('ACTION_SOURCE_EVENT_REQUIRED')
            if row.get('status') == 'CANCELLED' and kind != 'CANCELLED':
                raise ValueError('EXPLICIT_CANCELLATION_EVENT_REQUIRED')
            if before:
                missing_fields = (IDENTITY_FIELDS | QUANTITY_FIELDS) & before.keys() - row.keys()
                if missing_fields:
                    raise ValueError('INCOMPLETE_SOURCE_POSTIMAGE:' + table + ':' + key + ':' + ','.join(sorted(missing_fields)))
                for field in IDENTITY_FIELDS & before.keys() & row.keys():
                    if before[field] != row[field]:
                        raise ValueError('STABLE_RECORD_IDENTITY_CONFLICT:' + table + ':' + key + ':' + field)
                if table == 'operations' and package['system'] == 'MES' and kind != 'CORRECTED':
                    _operation_execution_update(before, row, key)
                if revision == old_revision['revision']:
                    classification = 'UNCHANGED' if package['mode'] == 'FULL_SNAPSHOT' else 'DUPLICATE'
                else:
                    if kind == 'CORRECTED':
                        approved = any(approval.get('table') == table and approval.get('record_id') == key
                            and approval.get('source_sha256') == sha
                            and approval.get('supersedes_revision') == old_revision['revision']
                            and action.get('supersedes_revision') == old_revision['revision']
                            and approval.get('reviewer') and approval.get('reason') and action.get('reason')
                            for approval in approvals)
                        if not approved:
                            raise ValueError('CORRECTION_REQUIRES_EXPLICIT_REVIEW:' + table + ':' + key)
                        classification = 'CORRECTED'
                    elif known < _time(before['source_known_at']) or not set(all_refs[table][key]) < set(refs):
                        raise ValueError('RECORD_REVISION_CONFLICT:' + table + ':' + key)
                    else:
                        classification = 'CANCELLED' if kind == 'CANCELLED' else 'UPDATED'
            else:
                if kind == 'CORRECTED':
                    raise ValueError('CORRECTION_TARGET_MISSING')
                classification = 'CANCELLED' if kind == 'CANCELLED' else 'ADDED'
            flags = []
            if old and occurred <= _time(old['as_of']) < known and classification not in {'UNCHANGED', 'DUPLICATE'}:
                flags.append('LATE_ARRIVAL')
            changes.append({'table': table, 'record_id': key, 'classification': classification, 'flags': flags,
                            'previous_revision': old_revision['revision'] if old_revision else None,
                            'revision': revision, 'business_occurred_at': row['business_occurred_at'],
                            'source_known_at': row['source_known_at'], 'event_refs': refs})
            state[table][key] = copy.deepcopy(row)
            revisions[table][key] = {'revision': revision, 'source_known_at': row['source_known_at'],
                'business_occurred_at': row['business_occurred_at'], 'event_refs': refs}
            all_refs[table][key] = refs
    # Presence resolves a prior missing-record review, without changing business status.
    pending = [item for item in pending if (item.get('table'), item.get('record_id')) not in incoming_keys]
    absent = []
    if package['mode'] == 'FULL_SNAPSHOT':
        absent = [(table, key) for table, rows in state.items() for key in rows
                  if (table, key) not in incoming_keys]
    else:
        absent = [(table, key) for table, keys in package.get('absent_from_snapshot', {}).items() for key in keys]
    for table, key in absent:
        item = {'table': table, 'record_id': key, 'classification': 'ABSENT_FROM_SOURCE',
                'requires_review': True, 'reason': 'MISSING_REVIEW_NOT_CANCELLATION'}
        changes.append({**item, 'flags': []})
        if item not in pending:
            pending.append(item)
    if package['mode'] == 'FULL_SNAPSHOT':
        pending = [item for item in pending if item.get('classification') != 'UNRESOLVED_SOURCE_EVENT']
    for event in package.get('unresolved_events', []):
        if (_time(event['business_occurred_at']) > as_of or _time(event['source_known_at']) > as_of
                or _time(event['source_known_at']) < _time(event['business_occurred_at'])):
            raise ValueError('UNRESOLVED_EVENT_VISIBILITY_CONFLICT')
        item = {'classification': 'UNRESOLVED_SOURCE_EVENT', 'requires_review': True, 'source_event': event}
        if item not in pending:
            pending.append(item)
    return {'state': {table: [rows[key] for key in sorted(rows)] for table, rows in sorted(state.items())},
            'revisions': revisions, 'event_refs': all_refs, 'changes': changes, 'pending': pending,
            'projection_ready': not pending}
