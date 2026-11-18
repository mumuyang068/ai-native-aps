"""Annual history in the existing immutable Live branch/action Owner."""
import copy
import json
import re
import threading
from datetime import timedelta
from pathlib import Path

from .live import LiveEnterprise, DAY_GENERATOR_KINDS, digest, instant
from .model import SimulationError, replay
from .qualification import execution_contract
from .year_policy import ORIGIN, PREFIX_END, YEAR_END
from .year_simulation import AnnualQualifications, AnnualSimulation


class _AnnualLiveQualifications(AnnualQualifications):
    def __init__(self, declarations):
        super().__init__(declarations)
        self.contract = execution_contract(self.definition, declarations['reference'])
        roles = {key: declarations['operating'][key] for key in
                 ['research_qualifications', 'research_machine_qualifications']}
        self.contract.update(policy_id=declarations['operating']['year_policy']['id'],
            research_roles_sha256=digest(roles),
            operating_policy_sha256=digest(declarations['operating']['year_policy']),
            operator_policy='EXPLICIT_NAMED_SYNTHETIC_RESEARCH_ROLES',
            qualification_time_policy='EXPLICIT_SYNTHETIC_RESEARCH_INTERVALS',
            factual_status='DECLARED_RESEARCH_POLICY_NOT_ORIGINAL_QUALIFICATION_EXTENSION')


class AnnualEnterprise(LiveEnterprise):
    @staticmethod
    def _check_destination(root, branch_id, operator_token, read_token):
        root = Path(root).resolve()
        if root.exists() and any(root.iterdir()):
            raise SimulationError('LIVE_BRANCH_ALREADY_EXISTS')
        if not isinstance(branch_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,60}', branch_id):
            raise SimulationError('INVALID_BRANCH_ID')
        if not operator_token or not read_token or operator_token == read_token:
            raise SimulationError('SEPARATE_OPERATOR_READ_TOKENS_REQUIRED')
        return root

    @classmethod
    def from_prefix(cls, root, branch_id, *, declarations, events, as_of,
                    operator_token, read_token, seed=73,
                    expected_events_sha256, expected_state_sha256):
        root = cls._check_destination(root, branch_id, operator_token, read_token)
        declarations, events = copy.deepcopy(declarations), copy.deepcopy(events)
        if set(declarations) != {'definition', 'operating', 'reference'}:
            raise SimulationError('DECLARATIONS_REQUIRED')
        definition = declarations['definition']
        if (definition.get('enterprise_id') != 'SIM-GARMENT-CONT-01'
                or instant(definition['origin']) != instant(ORIGIN)
                or instant(definition['end']) != instant(YEAR_END)):
            raise SimulationError('YEAR_ENTERPRISE_DECLARATION_MISMATCH')
        if instant(as_of) != instant(PREFIX_END) - timedelta(seconds=1):
            raise SimulationError('YEAR_FROZEN_PREFIX_TIME_REQUIRED')
        if not events or any(not instant(ORIGIN) <= instant(e['occurred_at']) < instant(PREFIX_END) for e in events):
            raise SimulationError('YEAR_PREFIX_EVENT_OUTSIDE_FROZEN_INTERVAL')
        if digest(events) != expected_events_sha256:
            raise SimulationError('YEAR_PREFIX_EVENTS_HASH_MISMATCH')
        qualification = _AnnualLiveQualifications(declarations)
        world = replay(definition, events, assignment_validator=qualification)
        if digest(world.snapshot()) != expected_state_sha256:
            raise SimulationError('YEAR_PREFIX_STATE_HASH_MISMATCH')
        proof = dict(events_sha256=expected_events_sha256, state_sha256=expected_state_sha256,
            event_count=len(events), occurrence_interval=dict(start=ORIGIN, end=PREFIX_END),
            validation='SEMANTIC_DIGEST_AND_FULL_QUALIFIED_REPLAY')
        bootstrap = dict(schema='live-enterprise/1', branch_id=branch_id, year_mode='BASE',
            definition=definition, declarations=declarations, seed=int(seed),
            parent=dict(kind='FROZEN_56_DAY_REPLAY', enterprise_id=definition['enterprise_id'], as_of=as_of, **proof),
            proof=proof, parent_checkpoint=dict(id='FROZEN-56', as_of=as_of,
                events_sha256=expected_events_sha256, event_count=len(events),
                restoration='IMMUTABLE_FROZEN_56_EVENT_PREFIX'),
            operator_hash=digest(operator_token), read_hash=digest(read_token),
            execution_policy='VERSIONED_SYNTHETIC_ROUTINE_NO_APS_ADOPTION')
        body = dict(revision=0, as_of=as_of, clock=as_of, next_day=56, current_day=55,
            pending=[], scheduled=[], events=events, actions={}, requests={}, execution_blocks=[],
            controls=[], parent_sha256=None, bootstrap_sha256=digest(bootstrap))
        cls._write_initial(root, bootstrap, body)
        return cls.open(root)

    @classmethod
    def _write_initial(cls, root, bootstrap, body):
        root.mkdir(parents=True, exist_ok=True)
        (root / 'commits').mkdir()
        (root / 'checkpoints').mkdir()
        cls._atomic_new(root / 'bootstrap.json', bootstrap)
        body['sha256'] = digest(body)
        cls._atomic_new(root / 'commits' / '00000000.json', body)

    @classmethod
    def open(cls, root):
        obj = cls()
        obj.root = Path(root).resolve()
        obj.bootstrap = json.loads((obj.root / 'bootstrap.json').read_text('utf-8'))
        if obj.bootstrap.get('schema') != 'live-enterprise/1' or obj.bootstrap.get('year_mode') not in ['BASE', 'APS_INTERVENTION']:
            raise SimulationError('YEAR_BOOTSTRAP_REQUIRED')
        obj.definition = copy.deepcopy(obj.bootstrap['definition'])
        obj.branch_id = obj.bootstrap['branch_id']
        # Must be installed before inherited _reload replays the frozen history.
        obj.qualification = _AnnualLiveQualifications(obj.bootstrap['declarations'])
        obj._thread_lock = threading.RLock()
        obj._reload()
        return obj

    def _validate_assignment(self, definition, event, state):
        if event.get('adoption_id') or event.get('source_execution_record'):
            return super()._validate_assignment(definition, event, state)
        if event.get('execution_contract_sha256'):
            raise SimulationError('EXECUTION_CONTRACT_MISMATCH')
        return self.qualification(definition, event, state)

    def _world_read_fingerprint(self):
        return digest(dict(definition=self.world.definition,state=self.world.state,
                           events=self.world.events,ids=self.world.ids))

    def _reload(self):
        """Validate all immutable records; reuse only the same trusted World.

        Disk bytes and the entire chain are checked on every call. The cache
        removes repeated event execution, never hash, prefix or ancestry checks.
        A changed head, callback/qualification, World contents or failed read
        invalidates it; a new process always performs its first full replay.
        """
        try:
            disk_bootstrap=json.loads((self.root/'bootstrap.json').read_text('utf8'))
            if digest(disk_bootstrap)!=digest(self.bootstrap):raise SimulationError('LIVE_BOOTSTRAP_HASH_MISMATCH')
            paths=sorted((self.root/'commits').glob('[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].json'))
            if not paths:raise SimulationError('LIVE_COMMIT_MISSING')
            parent=None
            for index,path in enumerate(paths):
                latest=self._read_commit(int(path.stem))
                if latest['revision']!=index or int(path.stem)!=index:raise SimulationError('LIVE_REVISION_CHAIN_GAP')
                if index==0:
                    if latest.get('bootstrap_sha256')!=digest(self.bootstrap):raise SimulationError('LIVE_BOOTSTRAP_HASH_MISMATCH')
                    checkpoint=self.bootstrap['parent_checkpoint']
                    if len(latest['events'])!=checkpoint['event_count'] or digest(latest['events'])!=checkpoint['events_sha256']:
                        raise SimulationError('LIVE_T0_PREFIX_MISMATCH')
                elif (latest['parent_sha256']!=parent['sha256'] or latest['events'][:len(parent['events'])]!=parent['events'] or
                      instant(latest['as_of'])<=instant(parent['as_of'])):
                    raise SimulationError('LIVE_EVENT_CHAIN_BROKEN')
                parent=latest
            if latest['revision']<getattr(self,'_verified_head_revision',-1):raise SimulationError('LIVE_REVISION_CHAIN_GAP')
            self.current=latest
            basis=digest(dict(head_sha256=latest['sha256'],definition=self.definition,qualification=vars(self.qualification)))
            trusted=(getattr(self,'world',None) is getattr(self,'_trusted_read_world',None) and
                     getattr(self,'world',None) is not None and self.world.assignment_validator==self._validate_assignment and
                     getattr(self,'_trusted_read_basis',None)==basis and
                     self._world_read_fingerprint()==getattr(self,'_trusted_read_fingerprint',None))
            if not trusted:
                self.world=self._replay(latest['events'])
                self._trusted_read_world=self.world
                self._trusted_read_basis=basis
                self._trusted_read_fingerprint=self._world_read_fingerprint()
            self._verified_head_revision=latest['revision']
        except Exception:
            self._trusted_read_world=None
            self._trusted_read_basis=None
            self._trusted_read_fingerprint=None
            raise

    def _mutate(self, kind, payload, **kwargs):
        if self.bootstrap['year_mode'] == 'BASE' and kind != 'ADVANCE':
            self._authorize(kwargs['operator_token'], 'operator')
            raise SimulationError('YEAR_BASE_RESEARCH_MUTATION_FORBIDDEN')
        return super()._mutate(kind, payload, **kwargs)

    def _simulation(self, *, routine_production):
        simulation = AnnualSimulation(self.bootstrap['declarations'], self.bootstrap['seed'],
            branch_id=self.branch_id, routine_production=routine_production)
        simulation.world = self.world
        simulation.clock = self.current['as_of']
        for key in ['next_day', 'current_day', 'pending', 'scheduled']:
            setattr(simulation, key, copy.deepcopy(self.current[key]))
        return simulation

    def _advance(self, target):
        target = instant(target)
        now = instant(self.current['as_of'])
        if target < now:
            raise SimulationError('CLOCK_CANNOT_REVERSE')
        if target > instant(self.definition['end']):
            raise SimulationError('BEYOND_SCENARIO_END')
        if self.bootstrap['year_mode'] == 'BASE':
            simulation = self._simulation(routine_production=True)
            simulation.advance(target.isoformat())
            self.world = simulation.world
            for key in ['clock', 'next_day', 'current_day', 'pending', 'scheduled']:
                self.current[key] = copy.deepcopy(getattr(simulation, key))
        else:
            self._advance_intervention(now, target)
        self.current['as_of'] = target.isoformat()
        self.current['clock'] = target.isoformat()
        for action in self.current['actions'].values():
            action['actual_event_ids'] = [e['event_id'] for e in self.world.events if e.get('adoption_id') == action['action_id']]
        return dict(new_event_count=len(self.world.events) - len(self.current['events']))

    def _advance_intervention(self, now, target):
        origin = instant(self.definition['origin'])
        while now <= target:
            pending = self.current['pending']
            if pending:
                for event in pending:
                    if instant(event['occurred_at']) <= now:
                        at = instant(event['occurred_at'])
                        latencies = {system: (instant(known) - at).total_seconds() / 60
                                     for system, known in event['known_at'].items()}
                        self._emit(event['kind'], event['payload'], event['occurred_at'],
                            latencies=latencies, generator_rule_event_id=event['event_id'])
                self.current['pending'] = [e for e in pending if instant(e['occurred_at']) > now]
                if not self.current['pending']:
                    self.current['next_day'] += 1
            day_at = (origin + timedelta(days=self.current['next_day'])).replace(hour=7)
            if not self.current['pending'] and self.current['next_day'] < 365 and day_at <= now:
                simulation = self._simulation(routine_production=False)
                simulation.emit = lambda kind, payload, at, tag, latencies=None: self._emit(
                    kind, payload, at.isoformat(), latencies=latencies,
                    generator_rule_event_id=self.branch_id + '-' + tag)
                simulation._day(self.current['next_day'])
                self.current['scheduled'] = simulation.scheduled
                self.current['current_day'] = simulation.current_day
                self.current['next_day'] += 1
            while True:
                count = len(self.world.events)
                for action in self.current['actions'].values():
                    if action['status'] not in ['COMPLETED', 'CANCELLED']:
                        self._execute_action(action, now)
                if count == len(self.world.events):
                    break
            candidates = [instant(e['occurred_at']) for e in self.current['pending']]
            if not self.current['pending'] and self.current['next_day'] < 365:
                candidates.append((origin + timedelta(days=self.current['next_day'])).replace(hour=7))
            for action in self.current['actions'].values():
                if action['status'] not in ['COMPLETED', 'CANCELLED']:
                    candidates.append(instant(action.get('receipt_at', action.get('retry_at', target.isoformat()))))
            candidates = [at for at in candidates if now < at <= target]
            if not candidates:
                break
            now = min(candidates)

    def fork_from_revision(self, root, newbranch, revision, *, operator_token, read_token):
        self._authorize(operator_token, 'operator')
        root = self._check_destination(root, newbranch, operator_token, read_token)
        if newbranch == self.branch_id:
            raise SimulationError('YEAR_FORK_BRANCH_MUST_DIFFER')
        with self._locked():
            record = self._read_commit(revision)
            # A fork does not take over or cancel future routine execution.
            if any(e['kind'] not in DAY_GENERATOR_KINDS for e in record['pending']):
                raise SimulationError('INTERVENTION_FORK_REQUIRES_QUIESCENT_CHECKPOINT')
            source_current = self.current
            try:
                self.current = record
                world = self._replay(record['events'])
            finally:
                self.current = source_current
            if any(op['status'] == 'STARTED' for op in world.state['operations'].values()):
                raise SimulationError('INTERVENTION_FORK_REQUIRES_QUIESCENT_CHECKPOINT')
            bootstrap = copy.deepcopy(self.bootstrap)
            bootstrap.update(branch_id=newbranch, year_mode='APS_INTERVENTION',
                operator_hash=digest(operator_token), read_hash=digest(read_token),
                execution_policy='EXPLICIT_ADOPTION_FIXED_20_NO_SCRAP_REWORK_NO_INFLIGHT_INTERRUPTION',
                parent=dict(kind='ANNUAL_IMMUTABLE_REVISION', branch_id=self.branch_id,
                    revision=record['revision'], commit_sha256=record['sha256'], as_of=record['as_of']),
                parent_checkpoint=dict(id='YEAR-REVISION-' + str(record['revision']), as_of=record['as_of'],
                    event_count=len(record['events']), events_sha256=digest(record['events']),
                    restoration='IMMUTABLE_PARENT_REVISION_NO_HEAD_ROLLBACK'))
            body = copy.deepcopy(record)
            body.pop('sha256', None)
            body.update(revision=0, parent_sha256=None, bootstrap_sha256=digest(bootstrap),
                requests={}, controls=[])
            self._write_initial(root, bootstrap, body)
        return type(self).open(root)

    def export_audit(self, *, operator_token):
        result = super().export_audit(operator_token=operator_token)
        operating = self.bootstrap['declarations']['operating']
        result.update(year_mode=self.bootstrap['year_mode'], parent=copy.deepcopy(self.bootstrap['parent']),
            frozen_prefix_proof=copy.deepcopy(self.bootstrap['proof']),
            research_roles={key: copy.deepcopy(operating[key]) for key in
                            ['research_qualifications', 'research_machine_qualifications']})
        result['branch_provenance']['generator'] = dict(
            origin='ANNUAL_DECLARED_ROUTINE_POLICY' if self.bootstrap['year_mode'] == 'BASE'
                   else 'ANNUAL_DECLARED_ROUTINE_BUSINESS_ONLY',
            policy_id=operating['year_policy']['id'], policy_sha256=digest(operating['year_policy']))
        return result
