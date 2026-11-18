"""Conservative event ATP over a caller-owned, exact-version supply snapshot.

This module never invents supply, mutates inventory, or runs manufacturing.
Existing claims are allocated first over the complete certified horizon. Only
remaining eligible source slices available by the requested date can be quoted.
"""
import copy
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

IDENTITY_FIELDS = ('plan_id', 'data_version_id', 'baseline_run_id', 'run_id', 'target')
DIMENSIONS = ('item_id', 'location_id', 'uom')
SUPPLY = {'OPENING_AVAILABLE', 'CONFIRMED_SUPPLY', 'QC_RELEASE', 'DO_ARRIVAL', 'SIMULATED_CTP_SUPPLY'}
CLAIMS = {'FIRM_DEMAND', 'PLAN_CONSUMPTION', 'DO_DISPATCH', 'FORECAST_NET'}
INFORMATION = {'PO_PHYSICAL_RECEIPT', 'DO_IN_TRANSIT'}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def stamp(value=None, time_zone=None):
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        except (ValueError, TypeError):
            raise ValueError('PROMISING_INVALID_TIME') from None
    if result.tzinfo is None:
        if not time_zone:
            raise ValueError('PROMISING_TIME_ZONE_REQUIRED')
        result = result.replace(tzinfo=ZoneInfo(time_zone))
    return result.astimezone(timezone.utc)


def quantity(value, positive=False):
    if value is None or isinstance(value, bool):
        raise ValueError('PROMISING_INVALID_QUANTITY')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise ValueError('PROMISING_INVALID_QUANTITY') from None
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise ValueError('PROMISING_INVALID_QUANTITY')
    return result


def number(value):
    return format(value.normalize(), 'f') if value else '0'


def validate_identity(identity):
    if not isinstance(identity, dict) or any(identity.get(k) in (None, '', {}) for k in IDENTITY_FIELDS):
        raise ValueError('PROMISING_IDENTITY_REQUIRED')
    return copy.deepcopy({k: identity[k] for k in IDENTITY_FIELDS})


def validate_request(request, time_zone=None):
    if not isinstance(request, dict) or any(not request.get(k) for k in (*DIMENSIONS, 'customer_id', 'due_at')):
        raise ValueError('PROMISING_REQUEST_FIELDS_REQUIRED')
    quantity(request.get('quantity'), positive=True)
    stamp(request['due_at'], time_zone)
    if not isinstance(request.get('allow_partial'), bool):
        raise ValueError('PROMISING_PARTIAL_POLICY_REQUIRED')
    if request.get('supply_policy', 'CONFIRMED_ONLY') not in {'CONFIRMED_ONLY', 'RESEARCH_CTP'}:
        raise ValueError('PROMISING_INVALID_SUPPLY_POLICY')
    return copy.deepcopy(request)


def dimensions(row):
    return tuple(row.get(k) for k in DIMENSIONS)


def effective(hold, now):
    if hold.get('state') == 'HELD' and not hold.get('expires_at'):
        raise ValueError('PROMISING_HOLD_EXPIRY_REQUIRED')
    return hold.get('state') == 'COMMITTED_SIMULATED' or (
        hold.get('state') == 'HELD' and stamp(hold.get('expires_at')) > now)


def calculate_atp(snapshot, request, holds=(), now=None):
    """Return source-level ATP; quantities are exact decimal strings.

    A confirmed source requires eligible/confirmed/reliable to each be true.
    Explicit RESEARCH_CTP requests can also use independently authenticated,
    same-run simulated CTP outputs, without declaring that supply confirmed.
    Raw physical PO receipts and DO transit messages are information only.
    No BOM explosion, finite scheduling, or automatic CTP supply is performed.
    Same-time sequence is caller supplied and must be unambiguous per dimension.
    """
    identity = validate_identity(snapshot.get('identity'))
    snapshot_hash = digest(snapshot)
    zone = snapshot.get('time_zone')
    validate_request(request, zone)
    due = stamp(request['due_at'], zone)
    horizon = stamp(snapshot.get('horizon_end'), zone) if snapshot.get('horizon_end') else None
    if horizon is None or due > horizon:
        raise ValueError('PROMISING_OUTSIDE_HORIZON')
    now = stamp(now)
    supply_policy = request.get('supply_policy', 'CONFIRMED_ONLY')
    policy = snapshot.get('forecast_policy')
    if policy not in {'EXCLUDE', 'NET_ONLY'}:
        raise ValueError('PROMISING_FORECAST_POLICY_REQUIRED')
    rows = copy.deepcopy(snapshot.get('events', []))
    ids = {}; order_keys = set(); deduplicated = []
    for row in rows:
        eid = row.get('event_id')
        if not eid or eid in ids:
            raise ValueError('PROMISING_DUPLICATE_EVENT_ID')
        ids[eid] = row
    for claim in snapshot.get('commitments', []):
        represented = claim.get('represented_by_event_id')
        if represented:
            base = ids.get(represented)
            if (not base or base.get('kind') not in {'FIRM_DEMAND', 'PLAN_CONSUMPTION'} or dimensions(base) != dimensions(claim)
                    or quantity(base.get('quantity')) != quantity(claim.get('quantity'))):
                raise ValueError('PROMISING_COMMITMENT_REPRESENTATION_MISMATCH')
            deduplicated.append(claim['event_id'])
        else:
            if claim.get('event_id') in ids:
                raise ValueError('PROMISING_DUPLICATE_COMMITMENT')
            row = copy.deepcopy(claim); row['kind'] = 'FIRM_DEMAND'
            rows.append(row); ids[row.get('event_id')] = row
    for row in rows:
        if row.get('kind') not in SUPPLY | CLAIMS | INFORMATION:
            raise ValueError('PROMISING_UNSUPPORTED_EVENT_KIND')
        if any(not row.get(k) for k in DIMENSIONS):
            raise ValueError('PROMISING_EVENT_DIMENSIONS_REQUIRED')
        if (not row.get('event_id') or not row.get('source_refs') or not isinstance(row['source_refs'], list)
                or not all(row['source_refs'])):
            raise ValueError('PROMISING_SOURCE_REFS_REQUIRED')
        if not isinstance(row.get('sequence'), int) or isinstance(row.get('sequence'), bool):
            raise ValueError('PROMISING_EVENT_SEQUENCE_REQUIRED')
        if not row.get('at'):
            raise ValueError('PROMISING_EVENT_TIME_REQUIRED')
        if row.get('quantity_mode', 'INCREMENTAL') != 'INCREMENTAL':
            raise ValueError('PROMISING_INCREMENTAL_EVENT_REQUIRED')
        row['_time'] = stamp(row['at'], zone)
        key = (dimensions(row), row['_time'], row['sequence'])
        if key in order_keys:
            raise ValueError('PROMISING_AMBIGUOUS_EVENT_ORDER')
        order_keys.add(key)
        if row.get('quantity') is None:
            if row['kind'] in CLAIMS:
                raise ValueError('PROMISING_UNKNOWN_COMMITMENT_QUANTITY')
            row['_quantity'] = None
        else:
            row['_quantity'] = quantity(row['quantity'])
    rows.sort(key=lambda row: (row['_time'], row['sequence'], row['event_id']))
    wanted = dimensions(request)
    sources = []; excluded = []; timeline = []; allocations = []; backlog = []

    def slice_for(source, amount, **extra):
        result = dict(event_id=source['event_id'], kind=source['kind'], at=source['at'], sequence=source['sequence'],
                    item_id=source['item_id'], location_id=source['location_id'], uom=source['uom'],
                    quantity=number(amount), source_refs=copy.deepcopy(source['source_refs']), **extra)
        if source['kind'] == 'SIMULATED_CTP_SUPPLY':
            result.update({key: copy.deepcopy(source[key]) for key in ('native_ref', 'native_run_id',
                'native_run_audited', 'research_policy', 'execution_authorized')})
        return result

    def consume(claim, amount):
        # Latest available receipt serves the claim first. This is a bounded
        # look-ahead allocation: a receipt before a future Firm can protect that
        # Firm while leaving earlier stock available to a new earlier request.
        for source in reversed(sources):
            take = min(source['_free'], amount)
            if take:
                source['_free'] -= take; amount -= take
                allocations.append(slice_for(source, take, claim_event_id=claim['event_id'], claim_source_refs=claim['source_refs']))
            if amount == 0:
                break
        return amount

    for row in rows:
        if dimensions(row) != wanted:
            continue
        kind = row['kind']; qty = row['_quantity']; reason = None
        if row['_time'] > horizon:
            reason = 'OUTSIDE_CERTIFIED_HORIZON'
        elif kind in INFORMATION:
            reason = 'PHYSICAL_OR_TRANSIT_NOT_USABLE'
        elif kind == 'FORECAST_NET' and policy == 'EXCLUDE':
            reason = 'EXPLICIT_FORECAST_EXCLUSION'
        elif kind in SUPPLY:
            if qty is None:
                reason = 'UNKNOWN_QUANTITY'
            elif kind == 'SIMULATED_CTP_SUPPLY':
                if supply_policy != 'RESEARCH_CTP':
                    reason = 'RESEARCH_CTP_OPT_IN_REQUIRED'
                elif not (row.get('native_run_audited') is True and row.get('research_policy') is True
                          and row.get('execution_authorized') is False and row.get('native_ref')
                          and row.get('native_run_id') == identity['run_id']):
                    reason = 'UNAUTHENTICATED_SIMULATED_CTP_SUPPLY'
            elif row.get('supply_status') in {'CTP_SIMULATED', 'PLANNED', 'PROPOSED', 'NOT_RUN', 'NEW_MANUFACTURING'}:
                reason = 'SIMULATED_SUPPLY_REQUIRES_CTP'
            elif not all(row.get(k) is True for k in ('eligible', 'confirmed', 'reliable')):
                reason = 'INELIGIBLE_OR_UNCONFIRMED_SOURCE'
            if reason is None:
                row['_free'] = qty; sources.append(row)
                pending = []
                for claim, outstanding in backlog:
                    outstanding = consume(claim, outstanding)
                    if outstanding:
                        pending.append((claim, outstanding))
                backlog = pending
        else:
            outstanding = consume(row, qty)
            if outstanding:
                backlog.append((row, outstanding))
        public = {k: copy.deepcopy(v) for k, v in row.items() if not k.startswith('_')}
        public.update(effect='EXCLUDED' if reason else ('SUPPLY' if kind in SUPPLY else 'CLAIM'),
                      reason=reason, unallocated_supply=number(sum((s['_free'] for s in sources), Decimal(0))))
        timeline.append(public)
        if reason:
            excluded.append(dict(event_id=row['event_id'], kind=kind, reason=reason,
                                 quantity=row['quantity'], source_refs=row['source_refs']))

    by_id = {s['event_id']: s for s in sources}
    held = Decimal(0); held_slices = []; hold_ids = set()
    for hold in holds:
        if not effective(hold, now):
            continue
        other_identity = validate_identity(hold.get('identity'))
        if other_identity != identity:
            if other_identity['plan_id'] == identity['plan_id']:
                raise ValueError('PROMISING_ACTIVE_HOLDS_REQUIRE_RELEASE')
            continue
        if not hold.get('id') or hold['id'] in hold_ids:
            raise ValueError('PROMISING_DUPLICATE_HOLD')
        hold_ids.add(hold['id'])
        if hold.get('snapshot_sha256') and hold['snapshot_sha256'] != snapshot_hash:
            raise ValueError('PROMISING_SNAPSHOT_CHANGED')
        for part in hold.get('source_slices', []):
            if dimensions(part) != wanted:
                continue
            raw_source = ids.get(part.get('event_id'))
            if (supply_policy == 'CONFIRMED_ONLY' and raw_source
                    and raw_source['kind'] == 'SIMULATED_CTP_SUPPLY' and part.get('kind') == 'SIMULATED_CTP_SUPPLY'):
                # This source is outside the strict policy's supply set. Other
                # confirmed slices of the same research hold still consume ATP.
                continue
            source = by_id.get(part.get('event_id')); amount = quantity(part.get('quantity'), positive=True)
            if source is None or source['_free'] < amount:
                raise ValueError('PROMISING_HOLD_SOURCE_UNAVAILABLE')
            source['_free'] -= amount; held += amount
            held_slices.append(slice_for(source, amount, hold_id=hold.get('id')))
    available_sources = [s for s in sources if s['_time'] <= due and s['_free'] > 0]
    available = sum((s['_free'] for s in available_sources), Decimal(0))
    requested = quantity(request['quantity'], positive=True)
    feasible = available >= requested
    promised = min(available, requested) if feasible or request['allow_partial'] else Decimal(0)
    source_slices = []; candidate_slices = []; left = min(available, requested)
    for source in available_sources:
        take = min(left, source['_free'])
        if take:
            candidate_slices.append(slice_for(source, take)); left -= take
    if promised:
        source_slices = copy.deepcopy(candidate_slices)
    ready = max((stamp(s['at'], zone) for s in source_slices), default=None)
    native_consumption = any(row['kind'] == 'PLAN_CONSUMPTION' for row in rows)
    consumption_policy = snapshot.get('existing_consumption_policy',
        'NATIVE_OUTFLOWS_PROTECTED_ONCE' if native_consumption else 'EXPLICIT_DEMAND_EVENTS')
    consumption_note = ('Existing native material outflows are protected once; do not add Firm deliveries or Net consumption '
                        'again for the same flows. No order pegging is inferred.' if native_consumption else
                        'Existing obligations are supplied as explicit demand events.')
    return dict(schema='order-promising-atp-v1', identity=identity, snapshot_sha256=snapshot_hash,
                request=copy.deepcopy(request), status='FEASIBLE' if feasible else ('PARTIAL' if promised else 'INSUFFICIENT'),
                available_quantity=number(available), requested_quantity=number(requested),
                promised_quantity=number(promised), shortfall_quantity=number(max(Decimal(0), requested-available)),
                effective_held_quantity=number(held), source_slices=source_slices,
                candidate_source_slices=candidate_slices, existing_claim_slices=allocations,
                existing_backlog=[dict(event_id=c['event_id'], quantity=number(q)) for c, q in backlog],
                held_source_slices=held_slices, deduplicated_commitments=deduplicated,
                earliest_source_ready_at=ready.isoformat() if ready else None,
                forecast_policy=policy, supply_policy=supply_policy, existing_consumption_policy=consumption_policy,
                existing_consumption_note=consumption_note, horizon_end=snapshot['horizon_end'], timeline=timeline,
                excluded_sources=excluded, execution='RESEARCH_ONLY_NO_ERP_WRITE')
