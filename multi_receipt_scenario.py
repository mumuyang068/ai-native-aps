"""One outstanding-balance receipt delay in the existing isolated draft store.

Already received quantities, receipt facts and protected MOs are immutable.
This action changes a planning assumption, never an enterprise commitment.
"""
import copy
import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator
from controlled_conditions import CONFIRMATION_SCHEMA
from planning_capabilities import profile_id, supports

PROFILE = 'multi-receipt-protected-manufacturing-v1'
ACTION = 'simulate_outstanding_receipt'
INTENT = '未收采购余额到货条件试算'
CHANGE_SCHEMA = {'type':'object', 'additionalProperties':False, 'properties':{
    'reference':{'type':'string', 'minLength':1, 'maxLength':200},
    'new_receipt_at':{'type':'string', 'minLength':1, 'maxLength':100},
    'reason':{'type':'string', 'minLength':1, 'maxLength':1000},
    'confirmation':CONFIRMATION_SCHEMA}, 'required':['reference','new_receipt_at','reason']}


def available(data):
    return profile_id(data) == PROFILE and supports(PROFILE, 'actions', 'outstanding_receipt')


def joint_purchase_condition(data,condition):
    """Same outstanding-only delay semantics, within the sealed joint projection."""
    from p22.foundation_projection import require
    require(isinstance(condition,dict) and set(condition)=={'reference','new_receipt_at'},'JOINT_PURCHASE_SHAPE')
    rows=[p for p in data['existing_orders'] if p['type']=='PO' and p['id']==condition['reference']]
    require(len(rows)==1,'JOINT_PURCHASE_UNKNOWN')
    row=rows[0];source=next(s for s in data['supply_sources'] if s['id']==row['supply_source_id'])
    require(row['source_status']=='confirmed' and Decimal(row['remaining_quantity'])>0,'JOINT_PURCHASE_BALANCE')
    try:
        date=datetime.fromisoformat(condition['new_receipt_at'].replace('Z','+00:00'))
        require(date.utcoffset() is not None,'JOINT_PURCHASE_TIMEZONE')
        date=date.astimezone(ZoneInfo(data['site']['timezone']));available=date+timedelta(seconds=source['receiving_qc']['wait_seconds'])
    except (TypeError,ValueError,OverflowError):
        require(False,'JOINT_PURCHASE_DATE')
    require(datetime.fromisoformat(row['expected_receipt_at'])<date<=available<=datetime.fromisoformat(data['horizon']['end_at']),'JOINT_PURCHASE_DELAY_ONLY')
    return dict(reference=row['id'],before={k:row[k] for k in ('expected_receipt_at','available_at')},
        after=dict(expected_receipt_at=date.isoformat(),available_at=available.isoformat()),scope='OUTSTANDING_BALANCE_ONLY')


def _digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
        separators=(',', ':')).encode()).hexdigest()


def apply(data, reference, new_receipt_at, reason, confirmation=None):
    from drafts import DraftError
    from planning_adapter import validate_input
    args = dict(reference=reference, new_receipt_at=new_receipt_at, reason=reason,
        **({'confirmation':confirmation} if confirmation is not None else {}))
    if list(Draft202012Validator(CHANGE_SCHEMA).iter_errors(args)) or not reason.strip():
        raise DraftError('OUTSTANDING_RECEIPT_INVALID_CONDITIONS')
    if not available(data):
        raise DraftError('UNSUPPORTED_PROFILE_CAPABILITY: outstanding_receipt')
    validate_input(data)
    candidate = copy.deepcopy(data)
    matches = [p for p in candidate['existing_orders'] if p['id'] == reference]
    if len(matches) != 1 or matches[0]['type'] != 'PO':
        raise DraftError('UNKNOWN_CONFIRMED_PURCHASE')
    row = matches[0]
    if row['source_status'] != 'confirmed' or Decimal(row['remaining_quantity']) <= 0:
        raise DraftError('OUTSTANDING_RECEIPT_REQUIRES_CONFIRMED_BALANCE')
    source = next(s for s in candidate['supply_sources'] if s['id'] == row['supply_source_id'])
    wait = source['receiving_qc']['wait_seconds']
    before = copy.deepcopy(row)
    before.update(qc_wait_seconds=wait, physical_receipt_at=row['expected_receipt_at'],
        planning_fact_kind='CONFIRMED_ENTERPRISE_SNAPSHOT')
    try:
        receipt = datetime.fromisoformat(new_receipt_at.replace('Z', '+00:00'))
        if receipt.tzinfo is None or receipt.utcoffset() is None:
            raise ValueError('explicit timezone required')
        receipt = receipt.astimezone(ZoneInfo(data['site']['timezone']))
        if receipt <= datetime.fromisoformat(row['expected_receipt_at']):
            raise DraftError('OUTSTANDING_RECEIPT_DELAY_ONLY: 提前到货尚无新增承诺证据')
        available_at = receipt + timedelta(seconds=wait)
        if not datetime.fromisoformat(data['planning_at']) <= receipt <= available_at <= datetime.fromisoformat(data['horizon']['end_at']):
            raise ValueError('outside declared horizon')
    except (TypeError, ValueError, OverflowError) as exc:
        if isinstance(exc, DraftError):
            raise
        raise DraftError('OUTSTANDING_RECEIPT_INVALID_DATE: 到货及质检可用时间需在规划范围内并含时区') from exc
    row.update(expected_receipt_at=receipt.isoformat(), available_at=available_at.isoformat())
    validate_input(candidate)
    after = copy.deepcopy(row)
    after.update(qc_wait_seconds=wait, physical_receipt_at=row['expected_receipt_at'],
        planning_fact_kind='PLANNING_ASSUMPTION')
    data.clear(); data.update(candidate)
    return dict(action=ACTION, intent=INTENT, reference=reference,
        conditions=dict(reference=reference, new_receipt_at=receipt.isoformat()), reason=reason.strip(),
        before=before, after=after, confirmation=copy.deepcopy(confirmation), simulation_only=True,
        business_permission='NOT_CONFIRMED', execution_authorized=False, engine_executed=False,
        scope='OUTSTANDING_BALANCE_ONLY', hypothesis_kind='PLANNING_ASSUMPTION')


def validate_confirmation(changes, baseline_sha256):
    from drafts import DraftError
    confirmation = changes.get('confirmation')
    if not isinstance(confirmation, dict):
        raise DraftError('OUTSTANDING_RECEIPT_CONFIRMATION_REQUIRED')
    if list(Draft202012Validator(CONFIRMATION_SCHEMA).iter_errors(confirmation)):
        raise DraftError('OUTSTANDING_RECEIPT_CONFIRMATION_MISMATCH')
    identity = confirmation['identity']
    if identity['profile'] != PROFILE or identity['target'] != 'baseline' or identity['run_id'] != identity['baseline_run_id']:
        raise DraftError('OUTSTANDING_RECEIPT_CONFIRMATION_MISMATCH')
    preview = copy.deepcopy(changes); preview['confirmation'] = None
    if _digest(dict(identity=identity, baseline_sha256=baseline_sha256, changes=preview)) != confirmation['fingerprint']:
        raise DraftError('OUTSTANDING_RECEIPT_CONFIRMATION_MISMATCH')


def verify(baseline, draft):
    """Replay the whole snapshot, including unchanged receipt and MO identities."""
    from drafts import DraftError
    if profile_id(baseline) != PROFILE:
        return False
    changes = draft.get('changes')
    try:
        if not isinstance(changes, dict) or changes.get('action') != ACTION:
            raise ValueError('new profile has only this certified scenario action')
        validate_confirmation(changes, draft['baseline_sha256'])
        expected = copy.deepcopy(baseline)
        replayed = apply(expected, reason=changes['reason'], confirmation=changes['confirmation'], **changes['conditions'])
        expected.update(scenario_id=draft['id'], scenario_revision=1)
        arguments = dict(**changes['conditions'], reason=changes['reason'], confirmation=changes['confirmation'])
        request_hash = hashlib.sha256(json.dumps({'name':ACTION, 'arguments':arguments}, sort_keys=True).encode()).hexdigest()
        if replayed != changes or expected != draft.get('input') or draft.get('external_request', {}).get('sha256') != request_hash:
            raise ValueError('undeclared enterprise mutation')
    except (KeyError, TypeError, ValueError, StopIteration) as exc:
        raise DraftError('OUTSTANDING_RECEIPT_SCOPE_MISMATCH: 仅允许指定未收余额的预计到货及QC可用时间变化，收货与工单事实保持不变') from exc
    return True
