"""Persistent research branches around the original enterprise event engine.

Only the certified T0 prefix is regenerated at creation. Later day-local demand
and supply use the original generator; production is driven by explicit sandbox
adoptions. APS dates are earliest-start requests, never actual completion facts.
The visible-source reader cannot access events, pending actions or credentials.
"""
import copy
import hashlib
import hmac
import json
import os
import re
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
from pathlib import Path

from .model import SimulationError, amount, dec, dt, replay, text, operation_key
from .scenario import Simulation
from .sources import SYSTEMS, project_sources
from .qualification import LiveQualificationPolicy

T0 = '2026-11-02T23:59:59+08:00'
DAY_GENERATOR_SHA256 = 'ef81da8bc77c9f82c696c30c056bfde7a97ce2c6b1668c2656f619913d32d615'
DAY_GENERATOR_KINDS = {'SalesOrderCreated', 'SalesOrderChanged', 'PurchaseOrderCreated',
    'PurchaseETAChanged', 'GoodsReceived', 'QCReleased', 'QCHeld', 'ResourceUnavailable'}
TABLES = {'ERP': ['purchase_orders', 'purchase_receipts', 'sales_orders', 'shipments'],
          'WMS': ['finished_goods', 'lots', 'movements', 'quality_events', 'receipts'],
          'MES': ['manufacturing_tasks', 'material_issues', 'operations', 'resource_unavailability']}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def instant(value):
    result = dt(value)
    if result.utcoffset() is None:
        raise SimulationError('TIMEZONE_REQUIRED')
    # This Owner supports the declared CONT Shanghai site. Preserve instants,
    # but always evaluate the site's shifts in that site's civil time.
    return result.astimezone(ZoneInfo('Asia/Shanghai'))


def check_manufacture_release(state, actions, payload, *, exclude_action_id=None):
    """Gate a new whole batch against actual jobs and unissued reservations.

    This is an execution safety check, not a WIP continuation algorithm. Both
    the APS adoption projection and the authoritative Live mutations use it.
    A released job and its adoption reserve the same pieces only once.
    """
    order = state['orders'].get(payload['order_id'])
    if not order or order['product_id'] != payload['product_id']:
        raise SimulationError('CROSS_SYSTEM_IDENTITY')
    quantity = amount(payload['quantity'])
    if quantity != 20:
        raise SimulationError('FIXED_20_PIECE_TASK_REQUIRED')
    others = [a for a in actions if a['action_id'] != exclude_action_id
        and a['payload']['action_type'] == 'MANUFACTURE']
    native = payload.get('manufacture_id')
    if native and any(a['status'] != 'CANCELLED' and all(a['payload'].get(k) == payload.get(k)
        for k in ['data_version_id', 'run_id', 'manufacture_id']) for a in others):
        raise SimulationError('NATIVE_MANUFACTURE_ALREADY_ADOPTED')
    jobs = [j for j in state['jobs'].values() if j['order_id'] == payload['order_id']]
    released = sum((dec(j['quantity']) - dec(j['shipped']) for j in jobs), Decimal(0))
    reserved = sum((dec(a['payload']['quantity']) for a in others
        if a['payload']['order_id'] == payload['order_id']
        and a['status'] not in ['CANCELLED', 'COMPLETED']
        and a.get('job_id') not in state['jobs']), Decimal(0))
    open_quantity = dec(order['quantity']) - dec(order['shipped'])
    if released + reserved + quantity > open_quantity:
        if released + quantity > open_quantity and any(
            dec(j['quantity']) > dec(j['shipped']) and j['status'] != 'COMPLETED'
            and (j['issued'] or j['completed']) for j in jobs):
            raise SimulationError('WIP_REQUIRES_CERTIFIED_CONTINUATION')
        raise SimulationError('ADOPTION_EXCEEDS_OPEN_DEMAND')


def check_continuation_release(state, actions, payload, definition, *, exclude_action_id=None):
    """Authorize only one conserved remainder on an existing, fully issued job."""
    if payload.get('continuation',{}).get('progress_contract')=='stage-flow-v2':
        return check_stage_release(state,actions,payload,definition,exclude_action_id=exclude_action_id)
    meta = payload.get('continuation', {})
    job = state['jobs'].get(payload.get('existing_job_id'))
    if (not job or meta.get('continuity_job_id') != payload.get('existing_job_id')
            or meta.get('actual_order_id') != payload.get('order_id')
            or job['order_id'] != payload.get('order_id') or job['product_id'] != payload.get('product_id')):
        raise SimulationError('CONTINUATION_JOB_IDENTITY_MISMATCH')
    route = definition['products'][job['product_id']]['routing']
    progress = {op: dec(job['completed'].get(op, 0)) for op in route}
    supplied = meta.get('completed_quantities', {})
    if set(supplied) != set(route) or any(dec(supplied[op]) != progress[op] for op in route):
        raise SimulationError('CONTINUATION_PROGRESS_CHANGED')
    total, done = dec(job['quantity']), progress[route[-1]]
    if (total != 20 or dec(meta.get('original_job_quantity', 0)) != total or not 0 <= done < total
            or dec(meta.get('finished_received_quantity', -1)) != dec(job['fg_received'])
            or dec(meta.get('shipped_quantity', -1)) != dec(job['shipped']) or dec(job['fg_received']) != done):
        raise SimulationError('CONTINUATION_QUANTITY_CONFLICT')
    boundary = next((i for i, op in enumerate(route) if progress[op] != total), None)
    if (boundary is None or boundary == 0 or any(progress[op] != done for op in route[boundary:])
            or any(definition['operations'][op].get('batch_quantity') or dec(definition['operations'][op].get('fixed_minutes', 0)) != 0
                or dec(definition['operations'][op].get('minutes_per_piece', 0)) <= 0 for op in route[boundary:])):
        raise SimulationError('CONTINUATION_MULTISTAGE_OR_FIXED_REMAINDER_UNSUPPORTED')
    remaining = total - done
    if (amount(payload['quantity']) != remaining or dec(meta.get('remaining_quantity', 0)) != remaining
            or meta.get('remaining_from_operation_id') != route[boundary]
            or meta.get('remaining_operation_ids') != route[boundary:]
            or [row['operation_id'] for row in payload['operations']] != route[boundary:]
            or meta.get('recipe_id') != 'CONT-CONTINUE:' + payload['existing_job_id']):
        raise SimulationError('CONTINUATION_REMAINING_ROUTE_MISMATCH')
    bom = definition['products'][job['product_id']]['bom']
    if set(job['issued']) != set(bom) or any(dec(job['issued'][item]) != dec(qty) * total for item, qty in bom.items()):
        raise SimulationError('CONTINUATION_MATERIAL_ISSUE_CONFLICT')
    if any(row['job_id'] == payload['existing_job_id'] and row['status'] == 'STARTED' for row in state['operations'].values()):
        raise SimulationError('CONTINUATION_INFLIGHT_UNSUPPORTED')
    for action in actions:
        if action['action_id'] == exclude_action_id or action['status'] == 'CANCELLED':
            continue
        old = action['payload']
        if all(old.get(k) == payload.get(k) for k in ['data_version_id', 'run_id', 'manufacture_id']):
            raise SimulationError('NATIVE_MANUFACTURE_ALREADY_ADOPTED')
        if action.get('job_id') == payload['existing_job_id'] and action['status'] != 'COMPLETED':
            raise SimulationError('CONTINUATION_JOB_ALREADY_RESERVED')


def check_stage_release(state,actions,payload,definition,*,exclude_action_id=None):
    meta=payload['continuation'];jid=payload.get('existing_job_id');job=state['jobs'].get(jid)
    if not job or meta.get('continuity_job_id')!=jid or job['order_id']!=payload.get('order_id') or job['product_id']!=payload.get('product_id') or meta.get('actual_order_id')!=job['order_id']:
        raise SimulationError('CONTINUATION_JOB_IDENTITY_MISMATCH')
    route=definition['products'][job['product_id']]['routing'];total=dec(job['quantity'])
    progress={op:dec(job['completed'].get(op,0)) for op in route};supplied=meta.get('completed_quantities',{})
    if set(supplied)!=set(progress) or any(dec(supplied[op])!=q for op,q in progress.items()):raise SimulationError('CONTINUATION_PROGRESS_CHANGED')
    received=dec(job['fg_received']);remaining=total-received
    if total!=20 or dec(meta.get('original_job_quantity',0))!=total or dec(meta.get('finished_received_quantity',-1))!=received or dec(meta.get('shipped_quantity',-1))!=dec(job['shipped']) or dec(meta.get('remaining_quantity',-1))!=remaining or dec(payload['quantity'])!=remaining or remaining<=0:
        raise SimulationError('CONTINUATION_QUANTITY_CONFLICT')
    if any(r['job_id']==jid and r['status']=='STARTED' for r in state['operations'].values()):raise SimulationError('CONTINUATION_INFLIGHT_UNSUPPORTED')
    bom=definition['products'][job['product_id']]['bom']
    if set(job['issued'])!=set(bom) or any(dec(job['issued'][i])!=dec(q)*total for i,q in bom.items()):raise SimulationError('CONTINUATION_MATERIAL_ISSUE_CONFLICT')
    requested={op:Decimal(0) for op in route}
    for row in payload['operations']:
        op=row['operation_id'];spec=definition['operations'].get(op,{})
        if op not in requested or spec.get('batch_quantity') or dec(spec.get('fixed_minutes',0))!=0 or dec(spec.get('minutes_per_piece',0))<=0:raise SimulationError('CONTINUATION_STAGE_OPERATION_INVALID')
        q=amount(row['quantity'])
        if q!=q.to_integral_value():raise SimulationError('INTEGER_OPERATION_SEGMENT_REQUIRED')
        requested[op]+=q
    if any(requested[op]!=total-progress[op] for op in route):raise SimulationError('CONTINUATION_REMAINING_ROUTE_MISMATCH')
    if sum((amount(r['quantity']) for r in payload.get('planned_receipts',[])),Decimal(0))!=remaining:
        raise SimulationError('CONTINUATION_RECEIPT_QUANTITY_CONFLICT')
    for action in actions:
        if action['action_id']==exclude_action_id or action['status']=='CANCELLED':continue
        old=action['payload']
        if all(old.get(k)==payload.get(k) for k in ['data_version_id','run_id','manufacture_id']):raise SimulationError('NATIVE_MANUFACTURE_ALREADY_ADOPTED')
        if action.get('job_id')==jid and action['status']!='COMPLETED':raise SimulationError('CONTINUATION_JOB_ALREADY_RESERVED')


class _BusinessGenerator(Simulation):
    def __init__(self, definition, seed=73, emit_event=None):
        super().__init__(definition, seed)
        self._emit_event = emit_event

    def emit(self, kind, payload, at, tag, latencies=None):
        if self._emit_event is None:
            return super().emit(kind, payload, at, tag, latencies)
        # Give the event its branch identity before the authoritative apply.
        # Rule identity remains available without altering the original day rule.
        return self._emit_event(kind, payload, at.isoformat(), latencies=latencies,
            generator_rule_event_id='CONT-' + tag)

    def _execute_day(self, base):
        # The original business generator remains unchanged. This live policy
        # gates manufacturing on an audited, explicitly adopted action.
        pass


class LiveEnterprise:
    @classmethod
    def create(cls, root, branch_id, *, definition, declarations, checkpoint_at=T0,
               seed=73, operator_token, read_token):
        root = Path(root).resolve()
        if root.exists() and any(root.iterdir()):
            raise SimulationError('LIVE_BRANCH_ALREADY_EXISTS')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,60}', branch_id):
            raise SimulationError('INVALID_BRANCH_ID')
        if checkpoint_at != T0:
            raise SimulationError('ONLY_CERTIFIED_T0_SUPPORTED')
        if not operator_token or not read_token or operator_token == read_token:
            raise SimulationError('SEPARATE_OPERATOR_READ_TOKENS_REQUIRED')
        if set(declarations) != {'definition', 'operating', 'reference'}:
            raise SimulationError('DECLARATIONS_REQUIRED')
        if definition['enterprise_id'] != 'SIM-GARMENT-CONT-01':
            raise SimulationError('ENTERPRISE_IDENTITY_MISMATCH')
        initial = Simulation(definition, seed)
        initial.advance(checkpoint_at)
        if initial.pending:
            raise SimulationError('CHECKPOINT_HAS_UNELAPSED_GENERATOR_EVENTS')
        root.mkdir(parents=True, exist_ok=True)
        (root / 'commits').mkdir()
        (root / 'checkpoints').mkdir()
        bootstrap = dict(schema='live-enterprise/1', branch_id=branch_id,
            definition=copy.deepcopy(definition), declarations=copy.deepcopy(declarations), seed=int(seed),
            parent_checkpoint=dict(id='T0', as_of=checkpoint_at, events_sha256=digest(initial.world.events),
                event_count=len(initial.world.events), restoration='ORIGINAL_CODE_ELAPSED_PREFIX_ONLY'),
            operator_hash=digest(operator_token), read_hash=digest(read_token),
            execution_policy='EXPLICIT_ADOPTION_FIXED_20_NO_SCRAP_REWORK_NO_INFLIGHT_INTERRUPTION')
        cls._atomic_new(root / 'bootstrap.json', bootstrap)
        body = dict(revision=0, as_of=checkpoint_at, next_day=initial.next_day,
            events=initial.world.events, actions={}, requests={}, execution_blocks=[], controls=[], parent_sha256=None,
            bootstrap_sha256=digest(bootstrap))
        body['sha256'] = digest(body)
        cls._atomic_new(root / 'commits' / '00000000.json', body)
        return cls.open(root)

    @staticmethod
    def _atomic_new(path, value):
        if path.exists():
            raise SimulationError('IMMUTABLE_RECORD_EXISTS')
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        with temporary.open('x', encoding='utf-8') as handle:
            handle.write(canonical(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.rename(temporary, path)

    @classmethod
    def open(cls, root):
        obj = cls()
        obj.root = Path(root).resolve()
        obj.bootstrap = json.loads((obj.root / 'bootstrap.json').read_text('utf-8'))
        obj.definition = copy.deepcopy(obj.bootstrap['definition'])
        obj.branch_id = obj.bootstrap['branch_id']
        obj.qualification = LiveQualificationPolicy(obj.definition, obj.bootstrap['declarations']['reference'])
        obj._thread_lock = threading.RLock()
        obj._reload()
        return obj

    def _read_commit(self, revision):
        try:
            value = json.loads((self.root / 'commits' / f'{int(revision):08d}.json').read_text('utf-8'))
        except (OSError, ValueError, TypeError):
            raise SimulationError('UNKNOWN_VIEW_REVISION') from None
        supplied = value.pop('sha256', None)
        if digest(value) != supplied:
            raise SimulationError('LIVE_COMMIT_HASH_MISMATCH')
        value['sha256'] = supplied
        return value

    def _reload(self):
        paths = sorted((self.root / 'commits').glob('[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].json'))
        if not paths:
            raise SimulationError('LIVE_COMMIT_MISSING')
        parent = None
        for index, path in enumerate(paths):
            latest = self._read_commit(int(path.stem))
            if latest['revision'] != index or int(path.stem) != index:
                raise SimulationError('LIVE_REVISION_CHAIN_GAP')
            if index == 0:
                if latest.get('bootstrap_sha256') != digest(self.bootstrap):
                    raise SimulationError('LIVE_BOOTSTRAP_HASH_MISMATCH')
                checkpoint = self.bootstrap['parent_checkpoint']
                if len(latest['events']) != checkpoint['event_count'] or digest(latest['events']) != checkpoint['events_sha256']:
                    raise SimulationError('LIVE_T0_PREFIX_MISMATCH')
            elif (latest['parent_sha256'] != parent['sha256']
                  or latest['events'][:len(parent['events'])] != parent['events']
                  or instant(latest['as_of']) <= instant(parent['as_of'])):
                raise SimulationError('LIVE_EVENT_CHAIN_BROKEN')
            parent = latest
        self.current = latest
        self.world = self._replay(latest['events'])

    def _replay(self, events):
        return replay(self.definition, events, assignment_validator=self._validate_assignment)

    def _validate_assignment(self, definition, event, state):
        p = event['payload']
        spec = definition['operations'][p['operation_id']]
        if event.get('source_execution_record'):
            if event['kind'] == 'OperationStarted':
                values=(p['operation_id'],p['resource_id'],p['operator_id'],event['occurred_at'],p['end'])
            else:
                running=state['operations'][operation_key(p)]
                values=(p['operation_id'],running['resource_id'],running['operator_id'],running['start'],event['occurred_at'])
            return self.qualification.validate_assignment(*values)
        action_id = event.get('adoption_id')
        action = self.current['actions'].get(action_id) if action_id else None
        if action_id and action is None:
            raise SimulationError('ADOPTED_ACTION_REQUIRED')
        contract = action.get('execution_contract') if action else None
        if contract is None:
            # Historical prefix and old explicitly fixed-primary actions retain
            # their exact original contract. No old record is rewritten.
            if event.get('execution_contract_sha256'):
                raise SimulationError('EXECUTION_CONTRACT_MISMATCH')
            if event['kind'] == 'OperationStarted':
                return (spec['resource'], spec['operator']) == (p['resource_id'], p['operator_id'])
            return True
        if (contract != self.qualification.contract or action['payload'].get('execution_contract') != contract
                or event.get('execution_contract_sha256') != digest(contract)):
            raise SimulationError('EXECUTION_CONTRACT_MISMATCH')
        staged=action['payload'].get('continuation',{}).get('progress_contract')=='stage-flow-v2'
        requested = [row for i,row in enumerate(action['payload']['operations']) if row['operation_id'] == p['operation_id'] and
            (not staged or p.get('segment_id')==action_id+'_'+str(i))]
        if len(requested) != 1 or p['job_id'] != action['job_id']:
            raise SimulationError('ADOPTED_ASSIGNMENT_MISMATCH')
        wanted = requested[0]
        if event['kind'] == 'OperationStarted':
            resource, operator, start, end = p['resource_id'], p['operator_id'], event['occurred_at'], p['end']
        else:
            running = state['operations'][operation_key(p)]
            resource, operator, start, end = running['resource_id'], running['operator_id'], running['start'], event['occurred_at']
        if (resource, operator) != (wanted['resource_id'], wanted['operator_id']):
            raise SimulationError('ADOPTED_ASSIGNMENT_MISMATCH')
        return self.qualification.validate_assignment(p['operation_id'], resource, operator, start, end)

    def _authorize(self, token, kind):
        if not isinstance(token, str) or not hmac.compare_digest(digest(token), self.bootstrap[kind + '_hash']):
            raise SimulationError('OPERATOR_AUTHORIZATION_REQUIRED' if kind == 'operator' else 'SOURCE_READ_AUTHORIZATION_REQUIRED')

    @contextmanager
    def _locked(self):
        with self._thread_lock:
            # OS locks disappear on process exit. A crashed service must not
            # leave an application lock directory that permanently blocks it.
            with (self.root / 'writer.lock').open('a+b') as handle:
                handle.seek(0, os.SEEK_END)
                if not handle.tell():
                    handle.write(b'0')
                    handle.flush()
                handle.seek(0)
                try:
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    raise SimulationError('LIVE_BRANCH_WRITE_BUSY') from None
                try:
                    self._reload()
                    yield
                finally:
                    handle.seek(0)
                    if os.name == 'nt':
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _mutate(self, kind, payload, *, operator_token, expected_revision, idempotency_key, apply):
        self._authorize(operator_token, 'operator')
        if not isinstance(idempotency_key, str) or not idempotency_key or len(idempotency_key) > 200:
            raise SimulationError('IDEMPOTENCY_KEY_REQUIRED')
        signature = digest(dict(kind=kind, payload=payload, expected_revision=expected_revision))
        with self._locked():
            previous = self.current['requests'].get(idempotency_key)
            if previous:
                if previous['signature'] != signature:
                    self._rejection(kind, idempotency_key, 'IDEMPOTENCY_CONFLICT')
                    raise SimulationError('IDEMPOTENCY_CONFLICT')
                return copy.deepcopy(previous['response'])
            if expected_revision is not None and expected_revision != self.current['revision']:
                self._rejection(kind, idempotency_key, 'STALE_WORLD_REVISION')
                raise SimulationError('STALE_WORLD_REVISION')
            before = copy.deepcopy(self.current)
            try:
                next_at = instant(self.current['as_of']) + timedelta(seconds=1)
                if next_at > instant(self.definition['end']):
                    raise SimulationError('BEYOND_SCENARIO_END')
                # Even a one-second source revision can cross a day-local event
                # or actual execution boundary. Process elapsed work first.
                self._advance(next_at.isoformat())
                result = apply(copy.deepcopy(payload)) or {}
                self.current['revision'] = before['revision'] + 1
                self.current['parent_sha256'] = before['sha256']
                self.current['events'] = copy.deepcopy(self.world.events)
                self.current['controls'].append(dict(sequence=len(self.current['controls']) + 1, kind=kind,
                    at=self.current['as_of'], request_id=idempotency_key, payload_sha256=digest(payload)))
                response = dict(branch_id=self.branch_id, enterprise_id=self.definition['enterprise_id'],
                    revision=self.current['revision'], as_of=self.current['as_of'], **result)
                self.current['requests'][idempotency_key] = dict(signature=signature, response=copy.deepcopy(response))
                self.current.pop('sha256', None)
                self.current['sha256'] = digest(self.current)
                self._atomic_new(self.root / 'commits' / f"{self.current['revision']:08d}.json", self.current)
                return response
            except (ValueError, KeyError, TypeError) as exc:
                self.current = before
                self.world = self._replay(before['events'])
                self._rejection(kind, idempotency_key, str(exc))
                raise

    def _rejection(self, kind, key, code):
        # Invalid business events never enter World. Their refusal is retained.
        with (self.root / 'rejections.jsonl').open('a', encoding='utf-8') as handle:
            handle.write(canonical(dict(kind=kind, request_id=key, code=code,
                revision=self.current['revision'], as_of=self.current['as_of'],
                recorded_at=datetime.now(timezone.utc).isoformat())) + '\n')
            handle.flush()
            os.fsync(handle.fileno())

    def _emit(self, kind, payload, at=None, action=None, latencies=None, *, generator_rule_event_id=None, source_record=None):
        at = instant(at or self.current['as_of'])
        event = dict(event_id=f'LIVE-{self.branch_id}-{len(self.world.events) + 1:06d}', kind=kind,
            occurred_at=at.isoformat(), known_at={system: (at + timedelta(minutes=minutes)).isoformat()
                for system, minutes in (latencies or {'ERP': 120, 'WMS': 5, 'MES': 10}).items()},
            payload=copy.deepcopy(payload), research_id='APS-SIM-CLOSED-LOOP-01', branch_id=self.branch_id)
        if generator_rule_event_id is not None:
            event['generator_rule_event_id'] = generator_rule_event_id
        if source_record is not None:
            event['source_execution_record'] = copy.deepcopy(source_record)
        if action:
            event.update(adoption_id=action['action_id'], plan_id=action['payload']['plan_id'],
                run_id=action['payload']['run_id'], data_version_id=action['payload']['data_version_id'])
            if action.get('execution_contract'):
                event['execution_contract_sha256'] = digest(action['execution_contract'])
        self.world.apply(event)
        if action is not None:
            action['actual_event_ids'].append(event['event_id'])
        if kind == 'QCReleased':
            for waiting in self.current['actions'].values():
                if waiting['status'] == 'BLOCKED' and waiting.get('last_block', {}).get('code') == 'INSUFFICIENT_RELEASED':
                    waiting['retry_at'] = at.isoformat()
        return event

    def status(self):
        with self._thread_lock:
            self._reload()
            return dict(branch_id=self.branch_id, enterprise_id=self.definition['enterprise_id'],
                revision=self.current['revision'], as_of=self.current['as_of'],
                parent_checkpoint=copy.deepcopy(self.bootstrap['parent_checkpoint']),
                event_count=len(self.world.events), **self.world.snapshot(),
                actions=copy.deepcopy(list(self.current['actions'].values())),
                execution_blocks=copy.deepcopy(self.current['execution_blocks']),
                execution_contract=copy.deepcopy(self.qualification.contract),
                execution_policy=self.bootstrap['execution_policy'])

    def read_view(self, *, read_token, revision=None):
        self._authorize(read_token, 'read')
        with self._thread_lock:
            self._reload()
            record = self.current if revision is None else self._read_commit(revision)
            documents = {}
            for system in SYSTEMS:
                doc = project_sources(self.definition, record['events'], record['as_of'], system)
                for table in TABLES[system]:
                    doc['tables'].setdefault(table, [])
                    doc['event_refs'].setdefault(table, {})
                for table, rows in doc['tables'].items():
                    for row in rows:
                        row['source_record_revision'] = digest({k: v for k, v in row.items() if k != 'aps_read_at'})
                # Original start payload did not carry quantity. Live events do,
                # and restored T0 contains no STARTED operation left over.
                doc.update(branch_id=self.branch_id, source_id=f"{self.definition['enterprise_id']}/{self.branch_id}/{system}",
                    source_revision=record['revision'], source_cursor=f"{self.branch_id}:{record['revision']}",
                    knowledge_cutoff=record['as_of'],
                    latest_source_known_at=max((row['source_known_at'] for rows in doc['tables'].values() for row in rows), default=None, key=lambda value: instant(value) if value else instant(record['as_of'])))
                documents[system] = doc
            return dict(branch_id=self.branch_id, enterprise_id=self.definition['enterprise_id'],
                revision=record['revision'], as_of=record['as_of'], documents=documents,
                declarations=copy.deepcopy(self.bootstrap['declarations']), source_cursor=f"{self.branch_id}:{record['revision']}")

    def verify_view(self, revision, *, read_token):
        view = self.read_view(read_token=read_token)
        if view['revision'] != revision:
            raise SimulationError('SOURCE_CHANGED_AFTER_PREVIEW')
        return dict(revision=revision, as_of=view['as_of'])

    def changes(self, system, *, read_token, revision, previous_as_of):
        if system not in SYSTEMS:
            raise SimulationError('UNKNOWN_SYSTEM')
        after = self.read_view(read_token=read_token, revision=revision)
        prior = []
        for path in sorted((self.root / 'commits').glob('*.json')):
            record = self._read_commit(int(path.stem))
            if record['as_of'] == previous_as_of and record['revision'] <= revision:
                prior.append(record['revision'])
        if len(prior) != 1:
            raise SimulationError('INCREMENTAL_CURSOR_GAP')
        before = self.read_view(read_token=read_token, revision=prior[0])['documents'][system]
        doc = copy.deepcopy(after['documents'][system])
        tables = {}
        for table, rows in doc['tables'].items():
            old = {r['id']: r for r in before['tables'][table]}
            business = lambda row: {k: v for k, v in row.items() if k != 'aps_read_at'}
            tables[table] = [r for r in rows if r['id'] not in old or business(r) != business(old[r['id']])]
        doc.update(mode='INCREMENTAL_UPSERT', absence_semantics='NO_CHANGE', previous_as_of=previous_as_of, tables=tables,
            event_refs={table: {r['id']: doc['event_refs'][table][r['id']] for r in rows} for table, rows in tables.items()})
        return doc

    def add_order(self, payload, *, operator_token, expected_revision, idempotency_key):
        def apply(p):
            p.setdefault('priority', 1)
            if instant(p['due']) < instant(self.current['as_of']):
                raise SimulationError('ORDER_DUE_BEFORE_CREATION')
            if amount(p['quantity']) % 20:
                raise SimulationError('FIXED_20_PIECE_MULTIPLE_REQUIRED')
            if p.get('release_not_before'):
                instant(p['release_not_before'])
            return dict(event=self._emit('SalesOrderCreated', p))
        return self._mutate('ADD_ORDER', payload, operator_token=operator_token, expected_revision=expected_revision,
            idempotency_key=idempotency_key, apply=apply)

    def perturb_supply(self, payload, *, operator_token, expected_revision, idempotency_key):
        def apply(p):
            kind = p.pop('kind')
            if kind not in ['PurchaseETAChanged', 'ResourceUnavailable', 'QCHeld', 'QCReleased']:
                raise SimulationError('UNSUPPORTED_RESEARCH_PERTURBATION')
            if kind == 'PurchaseETAChanged':
                if instant(p['eta']) < instant(self.current['as_of']):
                    raise SimulationError('ETA_BEFORE_CURRENT_CLOCK')
                for action in self.current['actions'].values():
                    if action.get('po_id') == p['po_id'] and action['status'] != 'COMPLETED':
                        action['receipt_at'] = p['eta']
            # _mutate advances a bookkeeping second before apply. Compare the
            # requested effective time with the accepted source head, as the
            # adoption gate does; that second must not make a current change old.
            accepted_head=instant(self.current['as_of'])-timedelta(seconds=1)
            if kind == 'ResourceUnavailable' and instant(p['start']) < accepted_head:
                raise SimulationError('RETROACTIVE_RESOURCE_CHANGE_FORBIDDEN')
            return dict(event=self._emit(kind, p))
        return self._mutate('PERTURB_SUPPLY', payload, operator_token=operator_token, expected_revision=expected_revision,
            idempotency_key=idempotency_key, apply=apply)

    def _source_execution_record(self, kind, payload, request_id):
        confirmation=payload.get('confirmation', {})
        if (confirmation.get('role')!='RESEARCH_SANDBOX_OPERATOR' or confirmation.get('confirmed') is not True
                or not all(isinstance(confirmation.get(key),str) and confirmation[key].strip() for key in ('actor_id','basis'))):
            raise SimulationError('RESEARCH_SOURCE_CONFIRMATION_REQUIRED')
        job=self.world.state['jobs'].get(payload.get('job_id'))
        if not job or dec(job['quantity'])!=20:
            raise SimulationError('SOURCE_EXISTING_20_PIECE_JOB_REQUIRED')
        if any(a.get('job_id')==payload['job_id'] and a['status'] not in ['COMPLETED','CANCELLED'] for a in self.current['actions'].values()):
            raise SimulationError('EXECUTION_JOB_HAS_ACTIVE_ADOPTION')
        return dict(schema='research-execution-source-record/1',record_id=request_id,kind=kind,
            payload_sha256=digest(payload),payload=copy.deepcopy(payload))

    def record_execution_segment(self, payload, *, operator_token, expected_revision, idempotency_key):
        """Record one new, bounded continuous actual; never a native adoption."""
        kind='RECORD_EXECUTION_SEGMENT'
        def apply(p):
            if type(expected_revision) is not int:
                raise SimulationError('SOURCE_EXPECTED_REVISION_REQUIRED')
            if set(p)!={'job_id','operation_id','segment_id','quantity','resource_id','operator_id','start_at','end_at','confirmation'}:
                raise SimulationError('SOURCE_SEGMENT_FIELDS_REQUIRED')
            record=self._source_execution_record(kind,p,idempotency_key)
            spec=self.definition['operations'].get(p['operation_id'])
            if not spec or spec.get('batch_quantity') or dec(spec.get('fixed_minutes',0))!=0 or dec(spec.get('minutes_per_piece',0))<=0:
                raise SimulationError('CONTINUOUS_SEGMENT_MODEL_REQUIRED')
            start,end=instant(p['start_at']),instant(p['end_at'])
            if not instant(self.current['as_of'])<=start<end<=instant(self.definition['end']):
                raise SimulationError('SOURCE_ACTUAL_INTERVAL_OUTSIDE_CURRENT_BRANCH')
            self._advance(start.isoformat())
            started=self._emit('OperationStarted',{key:p[key] for key in ['job_id','operation_id','segment_id','quantity','resource_id','operator_id']} |
                {'end':end.isoformat()},start.isoformat(),latencies={system:0 for system in SYSTEMS},source_record=record)
            self._advance(end.isoformat())
            completed=self._emit('OperationCompleted',{key:p[key] for key in ['job_id','operation_id','segment_id','quantity']},
                end.isoformat(),latencies={system:0 for system in SYSTEMS},source_record=record)
            return dict(source_record=record,event_ids=[started['event_id'],completed['event_id']])
        return self._mutate(kind,payload,operator_token=operator_token,expected_revision=expected_revision,
            idempotency_key=idempotency_key,apply=apply)

    def record_execution_start(self, payload, *, operator_token, expected_revision, idempotency_key):
        """Record an actual start without inventing its future completion."""
        kind='RECORD_EXECUTION_START'
        def apply(p):
            if type(expected_revision) is not int:raise SimulationError('SOURCE_EXPECTED_REVISION_REQUIRED')
            required={'job_id','operation_id','segment_id','quantity','resource_id','operator_id','start_at','end_at','confirmation'}
            if set(p)!=required:raise SimulationError('SOURCE_SEGMENT_FIELDS_REQUIRED')
            record=self._source_execution_record(kind,p,idempotency_key)
            start,end=instant(p['start_at']),instant(p['end_at'])
            if not instant(self.current['as_of'])<=start<end<=instant(self.definition['end']):raise SimulationError('SOURCE_ACTUAL_INTERVAL_OUTSIDE_CURRENT_BRANCH')
            self._advance(start.isoformat())
            actual={key:p[key] for key in ['job_id','operation_id','quantity','resource_id','operator_id']}
            if p['segment_id'] is not None:actual['segment_id']=p['segment_id']
            event=self._emit('OperationStarted',actual|{'end':end.isoformat()},start.isoformat(),
                latencies={system:0 for system in SYSTEMS},source_record=record)
            return dict(source_record=record,event_ids=[event['event_id']])
        return self._mutate(kind,payload,operator_token=operator_token,expected_revision=expected_revision,
            idempotency_key=idempotency_key,apply=apply)

    def record_operation_interruption(self, payload, *, operator_token, expected_revision, idempotency_key):
        """Append a bounded partial actual; the original start remains immutable."""
        kind='RECORD_OPERATION_INTERRUPTION'
        def apply(p):
            if type(expected_revision) is not int:raise SimulationError('SOURCE_EXPECTED_REVISION_REQUIRED')
            if set(p)!={'job_id','operation_id','segment_id','actual_quantity','interrupted_at','reason','confirmation'}:
                raise SimulationError('SOURCE_INTERRUPTION_FIELDS_REQUIRED')
            record=self._source_execution_record(kind,p,idempotency_key)
            identity={key:p[key] for key in ['job_id','operation_id']}
            if p['segment_id'] is not None:identity['segment_id']=p['segment_id']
            if self.world.state['operations'].get(operation_key(identity),{}).get('status')!='STARTED':
                raise SimulationError('INVALID_INTERRUPTION')
            cutoff=instant(p['interrupted_at'])
            if not instant(self.current['as_of'])<=cutoff<=instant(self.definition['end']):raise SimulationError('SOURCE_ACTUAL_INTERVAL_OUTSIDE_CURRENT_BRANCH')
            self._advance(cutoff.isoformat())
            actual={key:p[key] for key in ['job_id','operation_id','actual_quantity','reason']}
            if p['segment_id'] is not None:actual['segment_id']=p['segment_id']
            event=self._emit('OperationInterrupted',actual,cutoff.isoformat(),
                latencies={system:0 for system in SYSTEMS},source_record=record)
            return dict(source_record=record,event_ids=[event['event_id']])
        return self._mutate(kind,payload,operator_token=operator_token,expected_revision=expected_revision,
            idempotency_key=idempotency_key,apply=apply)

    def record_finished_goods_receipt(self, payload, *, operator_token, expected_revision, idempotency_key):
        """Post only unreceived, already completed actual output on the same job."""
        kind='RECORD_FINISHED_GOODS_RECEIPT'
        def apply(p):
            if type(expected_revision) is not int:
                raise SimulationError('SOURCE_EXPECTED_REVISION_REQUIRED')
            if set(p)!={'job_id','quantity','received_at','confirmation'}:
                raise SimulationError('SOURCE_FINISHED_RECEIPT_FIELDS_REQUIRED')
            record=self._source_execution_record(kind,p,idempotency_key)
            at=instant(p['received_at'])
            if not instant(self.current['as_of'])<=at<=instant(self.definition['end']):
                raise SimulationError('SOURCE_ACTUAL_INTERVAL_OUTSIDE_CURRENT_BRANCH')
            self._advance(at.isoformat())
            event=self._emit('FinishedGoodsReceived',{key:p[key] for key in ['job_id','quantity']},at.isoformat(),
                latencies={system:0 for system in SYSTEMS},source_record=record)
            return dict(source_record=record,event_ids=[event['event_id']])
        return self._mutate(kind,payload,operator_token=operator_token,expected_revision=expected_revision,
            idempotency_key=idempotency_key,apply=apply)

    def record_shipment(self,payload,*,operator_token,expected_revision,idempotency_key):
        """Expose the original stock Owner shipment on already received goods."""
        kind='RECORD_SHIPMENT'
        def apply(p):
            if type(expected_revision) is not int:raise SimulationError('SOURCE_EXPECTED_REVISION_REQUIRED')
            if set(p)!={'job_id','quantity','shipped_at','confirmation'}:raise SimulationError('SOURCE_SHIPMENT_FIELDS_REQUIRED')
            record=self._source_execution_record(kind,p,idempotency_key)
            at=instant(p['shipped_at'])
            if not instant(self.current['as_of'])<=at<=instant(self.definition['end']):
                raise SimulationError('SOURCE_ACTUAL_INTERVAL_OUTSIDE_CURRENT_BRANCH')
            self._advance(at.isoformat())
            job=self.world.state['jobs'][p['job_id']]
            event=self._emit('ShipmentPosted',dict(job_id=p['job_id'],order_id=job['order_id'],quantity=p['quantity']),
                at.isoformat(),latencies={system:0 for system in SYSTEMS},source_record=record)
            return dict(source_record=record,event_ids=[event['event_id']])
        return self._mutate(kind,payload,operator_token=operator_token,expected_revision=expected_revision,
            idempotency_key=idempotency_key,apply=apply)

    def _check_adoption(self, p):
        required = ['plan_id', 'data_version_id', 'run_id', 'input_hash']
        if any(not p.get(k) for k in required):
            raise SimulationError('IMMUTABLE_PLAN_RUN_EVIDENCE_REQUIRED')
        if p.get('enterprise_id') != self.definition['enterprise_id'] or p.get('branch_id') != self.branch_id:
            raise SimulationError('CROSS_SYSTEM_IDENTITY')
        confirmation = p.get('operator_confirmation', {})
        if confirmation.get('role') != 'RESEARCH_SANDBOX_OPERATOR' or confirmation.get('confirmed') is not True or not all(confirmation.get(k) for k in ['record_id', 'actor_id']):
            raise SimulationError('RESEARCH_OPERATOR_CONFIRMATION_REQUIRED')
        audit = p.get('native_audit', {})
        if audit.get('status') != 'PASS' or audit.get('run_id') != p['run_id'] or audit.get('input_hash') != p['input_hash']:
            raise SimulationError('NATIVE_AUDIT_EVIDENCE_REQUIRED')
        # _mutate advanced clock by a second; evidence is for the accepted head.
        old_asof = (instant(self.current['as_of']) - timedelta(seconds=1)).isoformat()
        if p.get('world_revision') != self.current['revision'] or p.get('source_evidence', {}).get('revision') != self.current['revision'] or instant(p.get('source_evidence', {})['as_of']) != instant(old_asof):
            raise SimulationError('STALE_ADOPTION_EVIDENCE')

    def publish(self, command, *, operator_token, expected_revision, idempotency_key):
        from .interop import publish
        if command.get('command_id') != idempotency_key:
            raise SimulationError('PUBLICATION_COMMAND_ID_MISMATCH')
        return self._mutate('PUBLICATION',command,operator_token=operator_token,expected_revision=expected_revision,
            idempotency_key=idempotency_key,apply=lambda payload:publish(self,payload))

    def publication_receipt(self, command_id, *, read_token):
        from .interop import receipt
        return receipt(self,command_id,token=read_token)

    def withdraw_published_execution(self,payload,*,operator_token,expected_revision,idempotency_key):
        from .interop import withdraw_execution
        if type(expected_revision) is not int:raise SimulationError('SOURCE_EXPECTED_REVISION_REQUIRED')
        return self._mutate('WITHDRAW_PUBLISHED_EXECUTION',payload,operator_token=operator_token,
            expected_revision=expected_revision,idempotency_key=idempotency_key,apply=lambda request:withdraw_execution(self,request))

    def confirm_manufacturing_order(self, payload, *, operator_token, expected_revision, idempotency_key):
        from .interop import confirm
        return self._mutate('CONFIRM_MO',payload,operator_token=operator_token,expected_revision=expected_revision,
            idempotency_key=idempotency_key,apply=lambda request:confirm(self,request))

    def adopt(self, payload, *, operator_token, expected_revision, idempotency_key):
        def apply(p):
            self._check_adoption(p)
            contract = copy.deepcopy(self.qualification.contract)
            if p.get('execution_contract', contract) != contract:
                raise SimulationError('EXECUTION_CONTRACT_MISMATCH')
            p['execution_contract'] = contract
            action_id = 'ADOPT-' + self.branch_id + '-' + digest(dict(key=idempotency_key, payload=p))[:16]
            action = dict(action_id=action_id, payload=p, adopted_at=self.current['as_of'], status='ADOPTED',
                actual_event_ids=[], evidence_sha256=digest(p), execution_contract=copy.deepcopy(contract))
            if p['action_type'] == 'MANUFACTURE':
                continuing = bool(p.get('existing_job_id'))
                if continuing:
                    check_continuation_release(self.world.state, self.current['actions'].values(), p, self.definition)
                else:
                    if p.get('continuation'):
                        raise SimulationError('CONTINUATION_JOB_IDENTITY_MISMATCH')
                    check_manufacture_release(self.world.state, self.current['actions'].values(), p)
                staged=p.get('continuation',{}).get('progress_contract')=='stage-flow-v2'
                route = ([r['operation_id'] for r in p['operations']] if staged else p['continuation']['remaining_operation_ids']) if continuing else self.definition['products'][p['product_id']]['routing']
                if [r['operation_id'] for r in p['operations']] != route:
                    raise SimulationError('COMPLETE_ORDERED_ROUTING_REQUIRED')
                for row in p['operations']:
                    spec = self.definition['operations'][row['operation_id']]
                    if instant(row['end_at']) <= instant(row['start_at']):
                        raise SimulationError('INVALID_PLANNED_INTERVAL')
                    minimum = dec(spec.get('fixed_minutes', 0)) + dec(spec['minutes_per_piece']) * dec(row['quantity'] if staged else p['quantity'])
                    minimum_end = instant(row['start_at']) + timedelta(minutes=float(minimum))
                    self.qualification.validate_assignment(row['operation_id'], row['resource_id'], row['operator_id'],
                        row['start_at'], max(instant(row['end_at']), minimum_end).isoformat())
                first=p['operations'][0]['start_at'] if p['operations'] else p['planned_receipts'][0]['start_at']
                action.update(job_id=p['existing_job_id'] if continuing else 'JOB-' + action_id,
                    retry_at=max(first, self.current['as_of'], key=instant))
            elif p['action_type'] == 'PURCHASE':
                q = amount(p['quantity'])
                if q < 100 or q % 100:
                    raise SimulationError('PURCHASE_POLICY_100_MULTIPLE_REQUIRED')
                if p['item_id'] not in {item for product in self.definition['products'].values() for item in product['bom']}:
                    raise SimulationError('PURCHASE_RAW_MATERIAL_REQUIRED')
                if instant(p['expected_receipt_at']) <= instant(self.current['as_of']):
                    raise SimulationError('RECEIPT_MUST_BE_FUTURE')
                if not Decimal(0) <= dec(p.get('qc_release_quantity', p['quantity'])) <= q:
                    raise SimulationError('QC_EXCEEDS_BALANCE')
                action.update(po_id='PO-' + action_id, lot_id='LOT-' + action_id, receipt_id='RCV-' + action_id,
                    receipt_at=p['expected_receipt_at'], supplier_confirmation='RESEARCH_SUPPLIER_CONFIRMED')
                self._emit('PurchaseOrderCreated', dict(po_id=action['po_id'], item_id=p['item_id'], quantity=p['quantity'],
                    unit=self.definition['items'][p['item_id']]['unit'], supplier_id=p['supplier_id'], eta=p['expected_receipt_at'],
                    supplier_confirmation=action['supplier_confirmation']), action=action)
            else:
                raise SimulationError('UNSUPPORTED_ADOPTED_ACTION')
            self.current['actions'][action_id] = action
            return dict(action=copy.deepcopy(action))
        return self._mutate('ADOPT', payload, operator_token=operator_token, expected_revision=expected_revision,
            idempotency_key=idempotency_key, apply=apply)

    def _block(self, action, code, now):
        row = dict(action_id=action['action_id'], code=code, occurred_at=now.isoformat(), status='BLOCKED')
        if not self.current['execution_blocks'] or any(self.current['execution_blocks'][-1].get(k) != row[k] for k in ['action_id', 'code']):
            self.current['execution_blocks'].append(row)
        action['status'] = 'BLOCKED'
        action['last_block'] = row

    def _working_slot(self, after, minutes):
        cal = self.definition['workday']
        day = after.replace(hour=0, minute=0, second=0, microsecond=0)
        duration = timedelta(minutes=float(minutes))
        while day <= instant(self.definition['end']):
            if day.weekday() in cal['weekdays']:
                for start_hour, end_hour in cal.get('segments', [[cal['start'], cal['end']]]):
                    start = max(after, day + timedelta(hours=start_hour))
                    if start + duration <= day + timedelta(hours=end_hour):
                        return start
            day += timedelta(days=1)
        return None

    def _execute_action(self, action, now):
        p = action['payload']
        if p['action_type'] == 'PURCHASE':
            if instant(action['receipt_at']) > now:
                return
            unit = self.definition['items'][p['item_id']]['unit']
            self._emit('GoodsReceived', dict(po_id=action['po_id'], receipt_id=action['receipt_id'], lot_id=action['lot_id'],
                item_id=p['item_id'], quantity=p['quantity'], unit=unit), now.isoformat(), action,
                {'ERP': int(p.get('erp_receipt_delay_minutes', 120)), 'WMS': 5, 'MES': 10})
            released = dec(p.get('qc_release_quantity', p['quantity']))
            if released:
                self._emit('QCReleased', dict(lot_id=action['lot_id'], quantity=text(released), unit=unit), now.isoformat(), action)
            action.update(status='COMPLETED', actual_end=now.isoformat())
            return
        if instant(action.get('retry_at', now.isoformat())) > now:
            return
        jid = action['job_id']
        job = self.world.state['jobs'].get(jid)
        continuing = bool(p.get('existing_job_id'))
        if continuing and not action.get('continuation_started'):
            try:
                check_continuation_release(self.world.state, self.current['actions'].values(), p, self.definition,
                    exclude_action_id=action['action_id'])
            except SimulationError as exc:
                self._block(action, str(exc), now)
                action['retry_at'] = self.definition['end']
                return
        if job is None or (job.get('interop_command_id') and job['status']=='CONFIRMED' and not job['issued']):
            # Persisted actions accepted by older code must pass the same gate
            # before creating a task or issuing any additional material.
            try:
                if job is None:
                    check_manufacture_release(self.world.state, self.current['actions'].values(), p,
                        exclude_action_id=action['action_id'])
            except SimulationError as exc:
                self._block(action, str(exc), now)
                action['retry_at'] = (now + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0).isoformat()
                return
            order = self.world.state['orders'][p['order_id']]
            if order.get('release_not_before') and now < instant(order['release_not_before']):
                action['retry_at'] = order['release_not_before']
                self._block(action, 'ORDER_NOT_RELEASED', now)
                return
            required = {item: dec(qty) * dec(p['quantity']) for item, qty in self.definition['products'][p['product_id']]['bom'].items()}
            if any(sum((dec(lot['available']) for lot in self.world.state['lots'].values() if lot['item_id'] == item), Decimal(0)) < qty for item, qty in required.items()):
                self._block(action, 'INSUFFICIENT_RELEASED', now)
                action['retry_at'] = (now + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0).isoformat()
                return
            if job is None:
                self._emit('ManufacturingTaskCreated', dict(job_id=jid, order_id=p['order_id'], product_id=p['product_id'], quantity=p['quantity']), now.isoformat(), action)
            for item, qty in sorted(required.items()):
                for lid, lot in list(self.world.state['lots'].items()):
                    if lot['item_id'] != item or dec(lot['available']) <= 0:
                        continue
                    take = min(qty, dec(lot['available']))
                    self._emit('MaterialIssued', dict(job_id=jid, lot_id=lid, quantity=text(take), unit=self.definition['items'][item]['unit']), now.isoformat(), action)
                    qty -= take
                    if not qty:
                        break
            job = self.world.state['jobs'][jid]
        staged=p.get('continuation',{}).get('progress_contract')=='stage-flow-v2'
        for index,requested in enumerate(p['operations']):
            op = requested['operation_id']
            work_quantity=requested['quantity'] if staged else p['quantity']
            segment = dict(job_id=jid, operation_id=op, **({'segment_id': action['action_id']+('_'+str(index) if staged else '')} if continuing else {}))
            running = self.world.state['operations'].get(operation_key(segment))
            finished = bool(running and running['status'] == 'COMPLETED') if continuing else op in job['completed']
            if finished:
                continue
            if running:
                if instant(running['end']) > now:
                    return
                self._emit('OperationCompleted', dict(segment, quantity=work_quantity), now.isoformat(), action)
                job = self.world.state['jobs'][jid]
                continue
            spec = self.definition['operations'][op]
            minutes = dec(spec.get('fixed_minutes', 0)) + dec(spec['minutes_per_piece']) * dec(work_quantity)
            earliest = max(now, instant(requested['start_at']))
            slot = self._working_slot(earliest, minutes)
            if slot is None:
                self._block(action, 'NO_WORKING_SLOT_IN_HORIZON', now)
                action['retry_at'] = self.definition['end']
                return
            if slot > now:
                action['retry_at'] = slot.isoformat()
                self._block(action, 'NON_WORKING_TIME' if earliest == now else 'PLANNED_NOT_BEFORE', now)
                return
            end = now + timedelta(minutes=float(minutes))
            try:
                self._emit('OperationStarted', dict(segment, resource_id=requested['resource_id'],
                    operator_id=requested['operator_id'], quantity=work_quantity, end=end.isoformat()), now.isoformat(), action)
                if continuing:
                    action['continuation_started'] = True
            except SimulationError as exc:
                self._block(action, str(exc), now)
                retry = [instant(block['end']) for block in self.world.state['resource_blocks']
                    if block['resource_id'] in [requested['resource_id'], requested['operator_id']] and instant(block['end']) > now]
                retry += [instant(old.get('actual_end', old['end'])) for old in self.world.state['operations'].values()
                    if (old['resource_id'] == requested['resource_id'] or old['operator_id'] == requested['operator_id']) and instant(old.get('actual_end', old['end'])) > now]
                action['retry_at'] = (min(retry) if retry else now + timedelta(days=1)).isoformat()
                return
            action.update(status='EXECUTING', retry_at=end.isoformat())
            return
        if staged:
            receipt_at=max((instant(r['end_at']) for r in p['planned_receipts']),default=now)
            if now<receipt_at:
                action['retry_at']=receipt_at.isoformat()
                return
        posted = sum((dec(event['payload']['quantity']) for event in self.world.events
            if event.get('adoption_id') == action['action_id'] and event['kind'] == 'FinishedGoodsReceived'), Decimal(0)) if continuing else dec(job['fg_received'])
        if posted < dec(p['quantity']):
            self._emit('FinishedGoodsReceived', dict(job_id=jid, quantity=p['quantity']), now.isoformat(), action)
        self._emit('ShipmentPosted', dict(job_id=jid, order_id=p['order_id'], quantity=p['quantity']), now.isoformat(), action)
        action.update(status='COMPLETED', actual_end=now.isoformat())

    def _advance(self, target):
        target = instant(target)
        now = instant(self.current['as_of'])
        if target < now:
            raise SimulationError('CLOCK_CANNOT_REVERSE')
        if target > instant(self.definition['end']):
            raise SimulationError('BEYOND_SCENARIO_END')
        origin = instant(self.definition['origin'])
        while now <= target:
            day_at = (origin + timedelta(days=self.current['next_day'])).replace(hour=7)
            if self.current['next_day'] < 56 and day_at <= now:
                generator = _BusinessGenerator(self.definition, self.bootstrap['seed'], emit_event=self._emit)
                generator.world = self.world
                generator._day(self.current['next_day'])
                self.current['next_day'] += 1
            # A receipt/QC event can wake a blocked manufacturing action at the
            # same physical time; finish processing that instant before jumping.
            while True:
                count = len(self.world.events)
                for action in self.current['actions'].values():
                    if action['status'] not in ['COMPLETED', 'CANCELLED']:
                        self._execute_action(action, now)
                if count == len(self.world.events):
                    break
            candidates = []
            if self.current['next_day'] < 56:
                candidates.append((origin + timedelta(days=self.current['next_day'])).replace(hour=7))
            for action in self.current['actions'].values():
                if action['status'] not in ['COMPLETED', 'CANCELLED']:
                    candidates.append(instant(action.get('receipt_at', action.get('retry_at', target.isoformat()))))
            candidates = [at for at in candidates if now < at <= target]
            if not candidates:
                break
            now = min(candidates)
        self.current['as_of'] = target.isoformat()
        for action in self.current['actions'].values():
            action['actual_event_ids'] = [e['event_id'] for e in self.world.events if e.get('adoption_id') == action['action_id']]
        return dict(new_event_count=len(self.world.events) - len(self.current['events']))

    def advance(self, to, *, operator_token, expected_revision=None, idempotency_key):
        return self._mutate('ADVANCE', {'to': to}, operator_token=operator_token, expected_revision=expected_revision,
            idempotency_key=idempotency_key, apply=lambda p: self._advance(p['to']))

    def cancel(self, action_id, *, operator_token, expected_revision, idempotency_key):
        def apply(p):
            action = self.current['actions'].get(p['action_id'])
            if not action or action['status'] == 'COMPLETED':
                raise SimulationError('ACTION_NOT_CANCELLABLE')
            if any(op['job_id'] == action.get('job_id') and op['status'] == 'STARTED' for op in self.world.state['operations'].values()):
                raise SimulationError('INFLIGHT_CANCELLATION_UNSUPPORTED')
            if (action.get('job_id') in self.world.state['jobs'] or
                action.get('po_id') in self.world.state['purchase_orders']):
                raise SimulationError('RELEASED_ACTION_CANCELLATION_UNSUPPORTED')
            action.update(status='CANCELLED', cancelled_at=self.current['as_of'])
            return dict(action=copy.deepcopy(action))
        return self._mutate('CANCEL', {'action_id': action_id}, operator_token=operator_token, expected_revision=expected_revision,
            idempotency_key=idempotency_key, apply=apply)

    def events(self, *, operator_token, after_sequence=0):
        self._authorize(operator_token, 'operator')
        self._reload()
        return copy.deepcopy(self.world.events[int(after_sequence):])

    def checkpoint(self, label, *, operator_token, expected_revision=None, idempotency_key=None):
        self._authorize(operator_token, 'operator')
        with self._locked():
            signature=digest(dict(label=str(label),expected_revision=expected_revision))
            identifier='CP-'+(digest(dict(checkpoint_key=idempotency_key))[:32] if idempotency_key else uuid.uuid4().hex)
            path=self.root/'checkpoints'/(identifier+'.json')
            if path.exists():
                point=json.loads(path.read_text('utf8'))
                if point.get('request_signature')!=signature:raise SimulationError('IDEMPOTENCY_CONFLICT')
                return point
            if expected_revision is not None and expected_revision!=self.current['revision']:
                raise SimulationError('STALE_WORLD_REVISION')
            point=dict(checkpoint_id=identifier,label=str(label),branch_id=self.branch_id,
                revision=self.current['revision'],commit_sha256=self.current['sha256'],as_of=self.current['as_of'],request_signature=signature)
            self._atomic_new(path,point)
            return point

    def restore(self, checkpoint_id, *, operator_token, expected_revision, idempotency_key):
        if not re.fullmatch(r'CP-[a-f0-9]{32}', checkpoint_id):
            raise SimulationError('INVALID_CHECKPOINT_ID')
        point = json.loads((self.root / 'checkpoints' / (checkpoint_id + '.json')).read_text('utf-8'))
        def apply(p):
            if point['branch_id'] != self.branch_id or point['revision'] != self.current['revision']:
                raise SimulationError('CHECKPOINT_ROLLBACK_FORBIDDEN')
            if point['commit_sha256'] != self.current['sha256']:
                raise SimulationError('CHECKPOINT_COMMIT_MISMATCH')
            return dict(restored_checkpoint=point)
        return self._mutate('RESTORE', {'checkpoint_id': checkpoint_id}, operator_token=operator_token,
            expected_revision=expected_revision, idempotency_key=idempotency_key, apply=apply)

    def trace(self, order_id, *, operator_token):
        self._authorize(operator_token, 'operator')
        self._reload()
        order = self.world.state['orders'].get(order_id)
        if not order:
            raise SimulationError('UNKNOWN_IDENTITY')
        jobs = {key: job for key, job in self.world.state['jobs'].items() if job['order_id'] == order_id}
        events = [e for e in self.world.events if e['payload'].get('order_id') == order_id or e['payload'].get('job_id') in jobs]
        lots = {e['payload']['lot_id'] for e in events if e['kind'] == 'MaterialIssued'}
        supply = [e for e in self.world.events if e['payload'].get('lot_id') in lots]
        pos = {e['payload']['po_id'] for e in supply if e['kind'] == 'GoodsReceived'}
        return dict(order=copy.deepcopy(order), jobs=copy.deepcopy(jobs), events=copy.deepcopy(events),
            supply_events=copy.deepcopy([e for e in self.world.events if e in supply or e['payload'].get('po_id') in pos]),
            actions=copy.deepcopy([a for a in self.current['actions'].values() if a['payload'].get('order_id') == order_id]),
            remaining_quantity=text(dec(order['quantity']) - dec(order['shipped'])))

    def export_audit(self, *, operator_token):
        self._authorize(operator_token, 'operator')
        with self._thread_lock:
            self._reload()
            commits = [self._read_commit(revision) for revision in range(self.current['revision'] + 1)]
            prefix_count = self.bootstrap['parent_checkpoint']['event_count']
            legacy_ids = [event['event_id'] for event in self.world.events[prefix_count:]
                if 'branch_id' not in event and not event.get('adoption_id')
                and event.get('kind') in DAY_GENERATOR_KINDS
                and event.get('research_id') == 'APS-SIM-ENTERPRISE-01'
                and isinstance(event.get('event_id'), str) and event['event_id'].startswith('CONT-')]
            provenance = dict(schema='live-branch-commit-provenance/1', branch_id=self.branch_id,
                head_revision=self.current['revision'], head_commit_sha256=self.current['sha256'],
                generator=dict(origin='ORIGINAL_DAY_LOCAL_GENERATOR', source_sha256=DAY_GENERATOR_SHA256),
                legacy_event_ids=legacy_ids, commits=commits)
            return dict(branch_id=self.branch_id, definition=copy.deepcopy(self.definition),
                execution_contract=copy.deepcopy(self.qualification.contract),
                execution_reference=copy.deepcopy(self.bootstrap['declarations']['reference']),
                revision=self.current['revision'], as_of=self.current['as_of'],
                events=copy.deepcopy(self.world.events), state=self.world.snapshot(),
                actions=copy.deepcopy(list(self.current['actions'].values())),
                execution_blocks=copy.deepcopy(self.current['execution_blocks']),
                branch_provenance=provenance,
                parent_checkpoint=copy.deepcopy(self.bootstrap['parent_checkpoint']), commit_sha256=self.current['sha256'],
                **(dict(research_qualification_declarations={k:copy.deepcopy(self.bootstrap['declarations']['operating'][k])
                    for k in ['year_policy','research_qualifications','research_machine_qualifications']})
                    if self.bootstrap['declarations'].get('operating',{}).get('year_policy') else {}))
