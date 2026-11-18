"""Explicit live assignment policy from unchanged public skill/validity facts.

The original FIFO's primary operator remains the default Replay policy. Live
adoptions may select another original qualified person only with an immutable
contract and full-interval checks; this module never creates qualifications.
"""
import copy
import hashlib
import json

from .model import SimulationError, dt

POLICY_ID = 'LIVE_ORIGINAL_QUALIFICATIONS_V1'


def digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def execution_contract(definition, reference):
    roles = {name: reference['tables'][name] for name in
        ['items', 'operations', 'skills', 'resource_skills', 'machine_qualifications']}
    return dict(schema='live-execution-qualification/1', policy_id=POLICY_ID,
        reference_sha256=digest(reference), reference_roles_sha256=digest(roles), definition_sha256=digest(definition),
        machine_policy='EXPLICIT_DECLARED_ALTERNATE_MACHINES' if any(len(o.get('resource_ids',[]))>1 for o in definition['operations'].values()) else 'FIXED_DECLARED_MACHINE',
        operator_policy='EXACT_NATIVE_OPERATOR_WITH_ORIGINAL_SKILL_AND_FULL_INTERVAL_VALIDITY',
        qualification_time_policy='ORIGINAL_REFERENCE_EFFECTIVE_INTERVAL_NO_EXTENSION',
        calendar_policy='DECLARED_CONT_WORKDAY_AND_ACTUAL_BLOCKS')


def instant(value):
    try:
        result = dt(value)
        if result.utcoffset() is None:
            raise ValueError()
        return result
    except (ValueError, AttributeError, TypeError):
        raise SimulationError('QUALIFICATION_TIME_INVALID') from None


class LiveQualificationPolicy:
    def __init__(self, definition, reference):
        self.definition = copy.deepcopy(definition)
        self.reference = copy.deepcopy(reference)
        self.contract = execution_contract(definition, reference)
        tables = self.reference.get('tables', {})
        self.operations = {row['id']: row for row in tables.get('operations', [])}
        self.skills = {row['id'] for row in tables.get('skills', [])}
        self.qualifications = tables.get('resource_skills', [])

    def validate_assignment(self, operation_id, resource_id, operator_id, start_at, end_at):
        spec = self.definition['operations'].get(operation_id)
        source = self.operations.get(operation_id)
        if (not spec or not source or resource_id not in spec.get('resource_ids',[spec['resource']])
                or operator_id not in self.definition['operators']):
            raise SimulationError('UNQUALIFIED_ASSIGNMENT')
        skill = source.get('required_operator_skill')
        if not skill or skill not in self.skills:
            raise SimulationError('ORIGINAL_SKILL_EVIDENCE_REQUIRED')
        matching = [row for row in self.qualifications if row.get('resource_id') == operator_id and row.get('skill_id') == skill]
        if not matching:
            raise SimulationError('UNQUALIFIED_ASSIGNMENT')
        start, end = instant(start_at), instant(end_at)
        if start >= end:
            raise SimulationError('INVALID_PLANNED_INTERVAL')
        if not any(instant(row.get('effective_from')) <= start and end <= instant(row.get('effective_to')) for row in matching):
            raise SimulationError('OPERATOR_QUALIFICATION_NOT_EFFECTIVE')
        if len(spec.get('resource_ids',[]))>1 and not any(q.get('resource_id')==resource_id and q.get('skill_id')==skill and
                instant(q['effective_from'])<=start and end<=instant(q['effective_to']) for q in self.reference['tables'].get('machine_qualifications',[])):
            raise SimulationError('MACHINE_QUALIFICATION_NOT_EFFECTIVE')
        return True
