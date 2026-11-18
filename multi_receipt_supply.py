"""Bounded receipt snapshots and protected manufacturing over the original Worker.

Receipt rows are sourced input facts, not an execution ledger. The mapping never
changes an enterprise PO or counts its received balance as a second future PO.
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
import copy
import hashlib
import json
from zoneinfo import ZoneInfo

PROFILE = 'multi-receipt-protected-manufacturing-v1'
RELEASE_PREFIX = 'QC:'


def enabled(data):
    return data.get('rules', {}).get('manufacturing_profile') in {PROFILE, 'routing-alternate-capacity-v1'}


def q(value):
    value = Decimal(str(value))
    if not value.is_finite():
        raise ValueError('non-finite quantity')
    return value


def pos(data):
    return [p for p in data['existing_orders'] if p['type'] == 'PO']


def mos(data):
    return [p for p in data['existing_orders'] if p['type'] == 'MO']


def released_opening(data):
    now = datetime.fromisoformat(data['planning_at'])
    return sum((q(r['quantity']) for r in data['purchase_receipts']
                if r['quality_status'] == 'RELEASED' and datetime.fromisoformat(r['release_at']) <= now), Decimal(0))


def future_releases(data):
    return [r for r in data['purchase_receipts']
            if r['quality_status'] == 'PENDING_QC' and r['release_at'] is not None]


def validate_receipt_snapshot(data, require):
    """Shared receipt contract, independent of the old bounded recipe/profile."""
    purchases={p['id']:p for p in pos(data)};seen=set();received=defaultdict(Decimal)
    now=datetime.fromisoformat(data['planning_at']);end=datetime.fromisoformat(data['horizon']['end_at'])
    for r in data.get('purchase_receipts',[]):
        ref=r['receipt_reference'];p=purchases.get(r['po_id'])
        require(ref not in seen and ':' not in ref, 'DUPLICATE_RECEIPT_IDENTITY');seen.add(ref)
        require(p is not None and (r['item_id'],r['location_id'],r['uom'])==(p['item_id'],p['location_id'],p['uom']), 'RECEIPT_SCOPE_OR_UNIT')
        require(r.get('inventory_includes_receipt') is False and p.get('inventory_includes_receipt') is False,'RECEIPT_OPENING_DOUBLE_COUNT')
        physical=datetime.fromisoformat(r['physical_receipt_at']);release=datetime.fromisoformat(r['release_at']) if r['release_at'] else None
        require(q(r['quantity'])>0 and physical<=now and (p.get('ordered_at') is None or datetime.fromisoformat(p['ordered_at'])<=physical),'RECEIPT_CHRONOLOGY')
        require(release is None or physical<=release<=end,'RECEIPT_RELEASE_CHRONOLOGY')
        state=r['quality_status']
        require((state=='RELEASED' and release is not None and release<=now) or
                (state=='PENDING_QC' and (release is None or release>now)) or
                (state=='FROZEN' and release is None),'RECEIPT_RELEASE_EVIDENCE')
        received[p['id']]+=q(r['quantity'])
    for p in purchases.values():
        require(q(p['ordered_quantity'])==q(p['received_quantity'])+q(p['cancelled_quantity'])+q(p['remaining_quantity'])
                and min(q(p[k]) for k in ['received_quantity','cancelled_quantity','remaining_quantity'])>=0,'PO_RECEIPT_BALANCE')
        require(received[p['id']]==q(p['received_quantity']),'PO_RECEIVED_SNAPSHOT_MISMATCH')


def receipt_openings(data):
    result=defaultdict(Decimal)
    for r in data.get('purchase_receipts',[]):
        if r['quality_status']=='RELEASED':result[r['item_id'],r['location_id']]+=q(r['quantity'])
    return result


def map_receipt_availability(data,native):
    """P4-15 semantics generalized to explicit item/location receipt identities."""
    from planning_adapter import engine_time,duration,decimal_text
    openings=receipt_openings(data);zone=data['site']['timezone']
    for row in native['buffer']:
        row['onhand']=decimal_text(q(row['onhand'])+openings[row['item'],row['location']])
    for r in future_releases(data):
        op='RECEIPT-RELEASE:'+r['receipt_reference'];ref=RELEASE_PREFIX+r['receipt_reference']
        native['operation'].append(dict(name=op,type='fixed_time',item=r['item_id'],location=r['location_id'],duration=duration(0),effective_end=engine_time(data['planning_at'],zone)))
        native['operationmaterial'].append(dict(id=len(native['operationmaterial'])+1,operation=op,item=r['item_id'],quantity='1',type='end'))
        native['manufacturingorder'].append(dict(reference=ref,operation=op,quantity=r['quantity'],status='confirmed',startdate=engine_time(r['release_at'],zone),enddate=engine_time(r['release_at'],zone),source=r['po_id']+'/'+r['receipt_reference']))
    return native


def validate_supply_facts(data, require):
    require(len(data['suppliers']) == len(data['supply_sources']) == 1, 'one evidenced RAW supply path only')
    require(2 <= len(pos(data)) <= 8 and 1 <= len(mos(data)) <= 4, 'two/eight PO identities and one/four protected MOs')
    require(len(pos(data)) + len(mos(data)) == len(data['existing_orders']), 'PO/MO only')
    ids = [p['id'] for p in data['existing_orders']]
    require(len(ids) == len(set(ids)) and all(':' not in value for value in ids), 'unique round-trippable PO/MO identities')
    require(len({p['external_ref'] for p in data['existing_orders']}) == len(ids), 'unique external business references')
    source = data['supply_sources'][0]
    raw = next(i for i in data['items'] if i['kind'] == 'RAW')
    require(source['item_id'] == raw['id'] and source['location_id'] == data['site']['id']
            and source['supplier_id'] == data['suppliers'][0]['id'], 'RAW source natural key')
    require('receiving_qc' in source and source['receiving_qc']['wait_seconds'] >= 0, 'explicit evidenced receiving QC')
    now = datetime.fromisoformat(data['planning_at']); end = datetime.fromisoformat(data['horizon']['end_at'])
    require(datetime.fromisoformat(source['effective_from']) <= now and datetime.fromisoformat(source['effective_to']) >= end,
            'source validity must cover horizon')
    for row in [source['receiving_qc'], *pos(data), *mos(data), *data.get('purchase_receipts', [])]:
        require(bool(row.get('provenance')) and all(isinstance(v, str) and v.strip() for v in row['provenance'].values()), 'complete source identity required')
    received = defaultdict(Decimal); receipt_ids = set()
    purchases = {p['id']: p for p in pos(data)}
    require(isinstance(data.get('purchase_receipts'), list) and bool(data['purchase_receipts']), 'explicit receipt snapshot required')
    for r in data['purchase_receipts']:
        key = (r['po_id'], r['receipt_reference'])
        require(key not in receipt_ids and r['receipt_reference'] not in {a[1] for a in receipt_ids}, 'duplicate receipt identity; never sum repeated imports')
        receipt_ids.add(key)
        p = purchases.get(r['po_id']); require(p is not None, 'receipt references unknown PO')
        require(':' not in r['receipt_reference'] and r['uom'] == raw['base_uom'], 'receipt reference/unit')
        require(q(r['quantity']) > 0, 'positive physical receipt quantity')
        physical = datetime.fromisoformat(r['physical_receipt_at'])
        require(physical <= now, 'receipt facts must already be physically received at snapshot')
        require(p['ordered_at'] is None or datetime.fromisoformat(p['ordered_at']) <= physical, 'physical receipt cannot precede evidenced enterprise order')
        release = datetime.fromisoformat(r['release_at']) if r['release_at'] else None
        require(release is None or physical <= release <= end, 'receipt release chronology')
        if r['quality_status'] == 'RELEASED': require(release is not None and release <= now, 'released receipt needs past release evidence')
        elif r['quality_status'] == 'PENDING_QC': require(release is None or release > now, 'pending receipt release must be future or unknown')
        else: require(r['quality_status'] == 'FROZEN' and release is None, 'frozen/unknown receipt cannot promise a release')
        received[p['id']] += q(r['quantity'])
    require(any(q(p['received_quantity']) > 0 and q(p['remaining_quantity']) > 0 for p in purchases.values()), 'at least one genuinely partially received PO')
    for p in purchases.values():
        require((p['item_id'], p['location_id'], p['supplier_id'], p['supply_source_id'], p['uom']) ==
                (raw['id'], data['site']['id'], source['supplier_id'], source['id'], raw['base_uom']), 'PO source/item/site/unit mismatch')
        require(p['source_status'] == 'confirmed' and p['freeze_policy'] == 'LOCKED', 'only confirmed unclosed PO balance')
        require(q(p['ordered_quantity']) == q(p['received_quantity']) + q(p['remaining_quantity'])
                and q(p['cancelled_quantity']) == 0 and q(p['remaining_quantity']) > 0, 'ordered = physically received + positive outstanding; no cancellation')
        require(received[p['id']] == q(p['received_quantity']), 'receipt snapshot must reconcile exactly to PO received quantity')
        require(p.get('inventory_includes_receipt') is False, 'opening stock must explicitly exclude all listed receipts')
        start = datetime.fromisoformat(p.get('planned_start_at') or p['ordered_at'])
        physical = datetime.fromisoformat(p['expected_receipt_at']); available = datetime.fromisoformat(p['available_at'])
        require(start <= physical and now <= physical <= available <= end, 'future outstanding PO dates')
        require(available == physical + timedelta(seconds=source['receiving_qc']['wait_seconds']), 'PO physical arrival and QC availability must remain distinct')
        require(p['ordered_at'] is None or datetime.fromisoformat(p['ordered_at']) <= now, 'actual PO placement cannot be future')
        require(datetime.fromisoformat(source['effective_from']) <= start <= physical <= datetime.fromisoformat(source['effective_to']), 'PO dates within source validity')


def validate(data):
    from routing_capacity import enabled as routing_enabled, validate as validate_routing
    if routing_enabled(data):return validate_routing(data)
    from planning_adapter import InputError
    from two_level_wip import validate as validate_wip

    def require(ok, why):
        if not ok:
            raise InputError('MULTI_RECEIPT_UNSUPPORTED: ' + why)

    require(enabled(data), 'explicit bounded profile required')
    if data['rules'].get('procurement_strategy') == 'MINCOST' and any(s.get('receiving_qc',{}).get('wait_seconds',0)>0 for s in data['supply_sources']):
        raise InputError('MINCOST_HARD_QC_UNSUPPORTED_919: hard QC date semantics are not certified for MINCOST')
    require(not any(k in data for k in ['forecasts', 'accessory_supply_option', 'inventory_policies',
                                       'inventory_decision_catalog', 'execution_checkpoint']), 'no forecast, policy, alternate or execution composition')
    require(not data.get('previous_manufacturing_suggestions') and not data['reservations'], 'no prior proposals or reservations')
    validate_wip(data, confirmed=True)
    validate_supply_facts(data, require)
    now = datetime.fromisoformat(data['planning_at']); end = datetime.fromisoformat(data['horizon']['end_at'])
    resources = {r['id']: r for r in data['resources']}; calendars = {c['id']: c for c in data['calendars']}
    pool = [r for r in resources.values() if r.get('role') == 'OPERATOR_POOL']
    people = [r for r in resources.values() if r.get('role') == 'OPERATOR']
    machines = [r for r in resources.values() if r.get('role') == 'MACHINE']
    require(len(pool) == len(people) == 1 and len(machines) == 2 and len(resources) == 4, 'two machines, one pool and exactly one named operator')
    pool, person = pool[0], people[0]
    require(person.get('pool_id') == pool['id'], 'named operator pool membership')
    for r in resources.values():
        require(r.get('parallel_units') == '1' and r.get('capacity_model', 'CONTINUOUS') == 'CONTINUOUS', 'unit continuous resources only')
        require(r['location_id'] == data['site']['id'] and r['availability_calendar_id'] in calendars, 'resource location/calendar')
        require((r.get('pool_id') == pool['id']) if r['role'] == 'OPERATOR' else 'pool_id' not in r, 'only named operator belongs to pool')
        require(all(any(datetime.fromisoformat(a['start_at']) <= datetime.fromisoformat(w['start_at']) < datetime.fromisoformat(w['end_at']) <= datetime.fromisoformat(a['end_at'])
                        for a in calendars[pool['availability_calendar_id']]['available_intervals']) for w in calendars[r['availability_calendar_id']]['available_intervals']), 'individual work calendar within pool envelope')
    skills = {s['id'] for s in data.get('skills', [])}
    require(len(skills) == len(data.get('skills', [])) == 2, 'two explicit operation skills')
    seen = set()
    for s in data.get('resource_skills', []):
        key = s['resource_id'], s['skill_id']; require(key not in seen, 'duplicate qualification'); seen.add(key)
        require(s['resource_id'] == person['id'] and s['skill_id'] in skills and datetime.fromisoformat(s['effective_from']) <= now
                and datetime.fromisoformat(s['effective_to']) >= end, 'named operator full-horizon skill evidence')
    recipes = {p['id']: p for p in data['production_recipes']}; routes = {r['id']: r for r in data['routing_revisions']}
    require(len({o['resource_id'] for r in routes.values() for o in r['operations']}) == 2, 'distinct CUT and SEW machines')
    for route in routes.values():
        o = route['operations'][0]; rq = o.get('operator_requirement', {})
        require(o['resource_id'] in {m['id'] for m in machines} and o['resource_units'] == '1', 'declared machine per operation')
        require(rq.get('pool_id') == pool['id'] and rq.get('quantity') == '1' and rq.get('skill_id') in skills
                and (person['id'], rq['skill_id']) in seen, 'one evidenced named qualified operator for every operation')
    for m in mos(data):
        require(m['source_status'] == 'confirmed' and m['freeze_policy'] == 'LOCKED' and q(m['completed_quantity']) == 0, 'only unexecuted confirmed MOs; approved/partial/closed unsupported')
        p = recipes.get(m['recipe_id']); require(p is not None, 'known MO recipe')
        o = routes[p['routing_revision_id']]['operations'][0]
        require(m['item_id'] == p['output_item_id'] and m['location_id'] == data['site']['id'] and m['uom'] == next(i['base_uom'] for i in data['items'] if i['id'] == m['item_id']), 'MO item/site/unit')
        require(len(m['operations']) == 1 and q(m['quantity']) == q(m['quantity']).to_integral_value(), 'one unexecuted whole-unit operation per MO')
        c = m['operations'][0]; start = datetime.fromisoformat(c['start_at']); finish = datetime.fromisoformat(c['end_at'])
        require(c['operation_id'] == o['id'] and c['resource_id'] == o['resource_id'] and c.get('operator_id') == person['id']
                and c['resource_units'] == '1', 'protected machine and named operator commitment')
        require(now <= start < finish <= end and (finish-start).total_seconds() == q(m['quantity'])*q(o['seconds_per_unit']), 'MO time and processing duration')
        require(all(any(datetime.fromisoformat(w['start_at']) <= start < finish <= datetime.fromisoformat(w['end_at'])
                        for w in calendars[resources[r]['availability_calendar_id']]['available_intervals']) for r in [o['resource_id'], person['id']]), 'protected MO must fit both committed calendars')


def map_native(data, native):
    """Pure receipt-to-availability mapping; no native received-quantity fiction."""
    from planning_adapter import engine_time, duration, decimal_text
    zone = data['site']['timezone']; source = data['supply_sources'][0]
    native['purchaseorder'] = [{'reference':p['id'], 'status':'confirmed', 'item':p['item_id'],
        'supplier':p['supplier_id'], 'location':p['location_id'], 'quantity':p['remaining_quantity'],
        'startdate':engine_time(p.get('planned_start_at') or p['ordered_at'], zone),
        'enddate':engine_time(p['expected_receipt_at'], zone), 'source':p['external_ref']} for p in pos(data)]
    raw = source['item_id']
    for row in native['buffer']:
        if row['item'] == raw: row['onhand'] = decimal_text(q(row['onhand']) + released_opening(data))
    person = next(r['id'] for r in data['resources'] if r.get('role') == 'OPERATOR')
    pool = next(r['id'] for r in data['resources'] if r.get('role') == 'OPERATOR_POOL')
    for load in native['operationresource']:
        if load['resource'] == pool and load.get('skill'): load['resource'] = person
    native['manufacturingorder'] = []
    for m in mos(data):
        c = m['operations'][0]
        native['manufacturingorder'].append({'reference':m['id'], 'operation':m['recipe_id']+':'+c['operation_id'],
            'quantity':m['quantity'], 'status':'confirmed', 'startdate':engine_time(c['start_at'], zone),
            'enddate':engine_time(c['end_at'], zone), 'source':m['external_ref']})
    for r in future_releases(data):
        ref = RELEASE_PREFIX+r['receipt_reference']; op = 'P415-RECEIPT-RELEASE-'+r['receipt_reference']
        native['operation'].append({'name':op, 'type':'fixed_time', 'item':raw, 'location':data['site']['id'],
            'duration':duration(0), 'sizeminimum':'1', 'sizemultiple':'1',
            'effective_end':engine_time(data['planning_at'],zone)})
        native['operationmaterial'].append({'id':len(native['operationmaterial'])+1, 'operation':op,
            'item':raw, 'quantity':'1', 'type':'end'})
        native['manufacturingorder'].append({'reference':ref, 'operation':op, 'quantity':r['quantity'],
            'status':'confirmed', 'startdate':engine_time(r['release_at'], zone), 'enddate':engine_time(r['release_at'], zone),
            'source':r['po_id']+'/'+r['receipt_reference']})
    return native


def protected_expectations(data):
    from planning_adapter import engine_time
    result = []
    for m in mos(data):
        c = m['operations'][0]
        result.append({'reference':m['id'], 'operation':m['recipe_id']+':'+c['operation_id'],
            'quantity':m['quantity'], 'status':'confirmed', 'owner':None, 'resources':sorted([c['resource_id'], c['operator_id']]),
            'start':engine_time(c['start_at'], data['site']['timezone']), 'end':engine_time(c['end_at'], data['site']['timezone'])})
    return result


def result_operations(data, raw):
    """Actual routing children plus input-identified protected leaf orders only."""
    from calculation_validation import evidence_rows
    existing = {m['id'] for m in mos(data)}
    orders = {str(r['reference']):r for table in ['manufacturingorder', 'workorder'] for r in evidence_rows(raw.get(table)) or []}
    return [r for r in orders.values() if r.get('owner') is not None or str(r['reference']) in existing]


def summary(data):
    source = data['supply_sources'][0]; now = datetime.fromisoformat(data['planning_at'])
    rows = []
    for p in pos(data):
        receipts = [copy.deepcopy(r) for r in data['purchase_receipts'] if r['po_id'] == p['id']]
        rows.append({**copy.deepcopy(p), 'receipts':receipts,
            'released_received_quantity':str(sum((q(r['quantity']) for r in receipts if r['quality_status']=='RELEASED'), Decimal(0))),
            'qc_waiting_quantity':str(sum((q(r['quantity']) for r in receipts if r['quality_status']=='PENDING_QC'), Decimal(0))),
            'frozen_received_quantity':str(sum((q(r['quantity']) for r in receipts if r['quality_status']=='FROZEN'), Decimal(0)))})
    return {'profile':data['rules']['manufacturing_profile'], 'purchase_orders':rows, 'purchase_receipts':copy.deepcopy(data['purchase_receipts']),
        'released_receipts_added_to_native_opening':str(released_opening(data)),
        'opening_stock_excludes_listed_receipts':True, 'protected_manufacturing_orders':copy.deepcopy(mos(data)),
        'operator_mode':'SINGLE_NAMED_OPERATOR_WITH_DECLARED_SKILLS',
        'limits':['来源明确的模拟快照；不是实际收货、质检放行、执行结转或企业采购授权。',
                  '未来QC释放是已声明可用时点假设；冻结及未知放行时点不产生可用供应。']}


def audit(data, raw, *, run_id=None):
    """Independent PO, receipt, BOM, frozen MO and chronological event checks."""
    from calculation_validation import evidence_rows, same_events
    from inventory_availability import net_available
    from planning_adapter import engine_time
    issues = []; missing = []; zone = ZoneInfo(data['site']['timezone'])
    try:validate(data)
    except (ValueError,KeyError,TypeError,ArithmeticError) as exc:
        issues.append({'code':'MULTI_RECEIPT_INPUT_CONTRACT_INVALID','reference':str(exc)})
    def check(ok, code, reference=None):
        if not ok: issues.append({'code':code, 'reference':reference})
    def at(value):
        value = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return value.replace(tzinfo=zone) if value.tzinfo is None else value.astimezone(zone)
    def event(item, ref, quantity, when): return ((item, data['site']['id']), str(ref), str(quantity), at(when).isoformat())
    def native_record(record, expected, code, reference, phase):
        for field,value in expected.items():
            if field not in record:
                missing.append(phase+':'+str(reference)+':'+field);continue
            try:equal=q(record[field])==q(value) if field=='quantity' else record[field]==value
            except (TypeError,ValueError,ArithmeticError):
                missing.append(phase+':'+str(reference)+':'+field);continue
            check(equal,code+':'+field,reference)
    tables = {name:evidence_rows(raw.get(name)) for name in ['purchaseorder','manufacturingorder','workorder','operationplanmaterial','deliveryorder','demand']}
    for name, rows in tables.items():
        if rows is None: missing.append(name)
    canonical_hash = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',',':'), ensure_ascii=False).encode()).hexdigest()
    expected_mos = protected_expectations(data)
    release_refs = {RELEASE_PREFIX+r['receipt_reference']:r for r in future_releases(data)}
    purchases = {p['id']:p for p in pos(data)}
    for phase in ['fixed_batch_pre','fixed_batch_post']:
        snap = raw.get(phase)
        if not isinstance(snap, dict): missing.append(phase); continue
        if run_id is None or not all(k in snap for k in ['run_id','input_hash']):missing.append(phase+':identity')
        else:check(snap.get('run_id') == run_id and snap.get('input_hash') == canonical_hash, 'MULTI_RECEIPT_NATIVE_IDENTITY', phase)
        if 'move_approved_early' not in snap:missing.append(phase+':move_approved_early')
        else:check(snap['move_approved_early'] == 0, 'MULTI_RECEIPT_GLOBAL_PROTECTION', phase)
        if not isinstance(snap.get('operationplans'),list) or not isinstance(snap.get('purchaseorders'),list):
            missing.append(phase+':order_records');continue
        for m in expected_mos:
            rows = [r for r in snap.get('operationplans', []) if str(r.get('reference')) == m['reference']]
            if len(rows) != 1: check(False, 'PROTECTED_MO_IDENTITY', m['reference']); continue
            native_record(rows[0],m,'PROTECTED_MO_DRIFT',m['reference'],phase)
        for p in purchases.values():
            rows = [r for r in snap.get('purchaseorders', []) if str(r.get('reference')) == p['id']]
            check(len(rows) == 1, 'CONFIRMED_PO_IDENTITY', p['id'])
            if len(rows) == 1:
                expected = {'item':p['item_id'], 'supplier':p['supplier_id'], 'location':p['location_id'], 'status':'confirmed',
                    'start':engine_time(p.get('planned_start_at') or p['ordered_at'],data['site']['timezone']),
                    'end':engine_time(p['expected_receipt_at'],data['site']['timezone'])}
                native_record(rows[0],{**expected,'quantity':p['remaining_quantity']},'CONFIRMED_PO_DRIFT',p['id'],phase)
        for ref, receipt in release_refs.items():
            rows = [r for r in snap.get('operationplans', []) if str(r.get('reference')) == ref]
            check(len(rows)==1,'RECEIPT_RELEASE_IDENTITY',ref)
            if len(rows)==1:
                when=engine_time(receipt['release_at'],data['site']['timezone'])
                native_record(rows[0],{'status':'confirmed','quantity':receipt['quantity'],'start':when,'end':when,
                    'operation':'P415-RECEIPT-RELEASE-'+receipt['receipt_reference'],'owner':None,'resources':[]},'RECEIPT_RELEASE_DRIFT',ref,phase)
        if not isinstance(snap.get('buffers'), list): missing.append(phase+':buffers')
        else:
            for inv in data['inventory_positions']:
                buffers = [b for b in snap['buffers'] if b.get('item') == inv['item_id'] and b.get('location') == inv['location_id']]
                wanted = net_available(inv)+(released_opening(data) if inv['item_id'] == data['supply_sources'][0]['item_id'] else 0)
                check(len(buffers)==1,'RECEIPT_OPENING_BUFFER_IDENTITY',inv['id'])
                if len(buffers)==1:
                    if 'onhand' not in buffers[0]:missing.append(phase+':buffer:'+inv['id']+':onhand')
                    else:check(q(buffers[0]['onhand'])==wanted,'RECEIPT_OPENING_NET_STOCK',inv['id'])
        if not isinstance(snap.get('confirmed_supply_model'),dict) or not isinstance(snap.get('wip_model'),dict):
            missing.append(phase+':supply_model')
        else:
            from two_level_confirmed_supply import audit_supply_model
            try:audit_supply_model(data,snap,phase,check,missing)
            except (KeyError,TypeError,ValueError,ArithmeticError):missing.append(phase+':supply_model_fields')
    ledger = []; inventory = []
    for row in tables['operationplanmaterial'] or []:
        try:check(q(row['onhand'])>=-Decimal('0.000001'),'MATERIAL_NEGATIVE_STOCK',row.get('id'))
        except (KeyError,TypeError,ValueError,ArithmeticError):missing.append('material_onhand')
    if all(rows is not None for rows in tables.values()):
        try:
            expected = Counter(); actual = Counter(); initial = defaultdict(Decimal); opening = defaultdict(Decimal)
            for p in tables['purchaseorder'] or []:
                original = purchases.get(str(p['reference']))
                check(original is not None and p.get('status') == 'confirmed', 'UNAUTHENTICATED_PROPOSED_PURCHASE', p['reference'])
                if original:
                    check(q(p['quantity']) == q(original['remaining_quantity']) and (p['item'],p['supplier'],p['location']) ==
                          (original['item_id'],original['supplier_id'],original['location_id']), 'CONFIRMED_PO_REST_DRIFT', p['reference'])
                    expected[event(original['item_id'],p['reference'],q(original['remaining_quantity']),original['available_at'])] += 1
            check(Counter(str(p['reference']) for p in tables['purchaseorder'] or []) == Counter(purchases.keys()), 'CONFIRMED_PO_LOST_OR_DUPLICATE')
            for ref,r in release_refs.items(): expected[event(data['supply_sources'][0]['item_id'],ref,q(r['quantity']),r['release_at'])] += 1
            recipes = {p['id']:p for p in data['production_recipes']}; routes = {r['id']:r for r in data['routing_revisions']}; boms = {b['id']:b for b in data['bom_revisions']}
            specifications = {p['id']+':'+o['id']:(p,o) for p in recipes.values() for o in routes[p['routing_revision_id']]['operations']}
            roots = {str(r['reference']):r for r in tables['manufacturingorder'] or [] if r.get('owner') is None}
            work = result_operations(data,raw)
            for w in work:
                spec = specifications.get(w['operation']); check(spec is not None, 'MULTI_RECEIPT_UNKNOWN_OPERATION', w['reference'])
                if not spec: continue
                p,o = spec; qty = q(w['quantity']); bom = boms[p['bom_revision_id']]
                for line in bom['lines']:
                    if line['consume_operation_id'] != o['id']:continue
                    expected[event(line['item_id'],w['reference'],-qty*q(line['quantity_per_output'])/q(bom['output_quantity']),w['startdate'])] += 1
                if o['id']==max(routes[p['routing_revision_id']]['operations'],key=lambda a:a['sequence'])['id']:
                    expected[event(p['output_item_id'],w['reference'],qty,w['enddate'])] += 1
            for ref,r in roots.items():
                if ref in release_refs or ref in {m['id'] for m in mos(data)}: continue
                check(r['operation'] in recipes and r.get('status') == 'proposed', 'UNDECLARED_MANUFACTURING_ROOT', ref)
                children = [w for w in work if str(w.get('owner')) == ref]
                route=routes[recipes[r['operation']]['routing_revision_id']] if r['operation'] in recipes else {'operations':[]}
                expected_names={r['operation']+':'+o['id'] for o in route['operations']}
                check(len(children)==len(expected_names) and {w['operation'] for w in children}==expected_names and all(q(w['quantity'])==q(r['quantity']) for w in children), 'MULTI_RECEIPT_CHILD_QUANTITY', ref)
            demands = {d['id']:d for d in data['demand_lines']}; delivered = defaultdict(Decimal)
            for dl in tables['deliveryorder'] or []:
                d = demands.get(dl.get('demand')); check(d is not None, 'MATERIAL_UNKNOWN_DELIVERY', dl.get('reference'))
                if d:
                    check(dl['item'] == d['item_id'] and dl['location'] == d['location_id'], 'MATERIAL_DELIVERY_IDENTITY', dl['reference'])
                    delivered[d['id']] += q(dl['quantity']); expected[event(dl['item'],dl['reference'],-q(dl['quantity']),dl['startdate'])] += 1
            check(Counter(d['name'] for d in tables['demand'] or []) == Counter(demands.keys()), 'MATERIAL_DEMAND_IDENTITY')
            for d in tables['demand'] or []: check(delivered[d['name']] == q(d.get('plannedquantity') or 0), 'MATERIAL_DELIVERY_QUANTITY', d['name'])
            for inv in data['inventory_positions']: initial[(inv['item_id'],inv['location_id'])] += net_available(inv)
            initial[(data['supply_sources'][0]['item_id'],data['site']['id'])] += released_opening(data)
            groups = defaultdict(list); now = at(data['planning_at'])
            for index,f in enumerate(tables['operationplanmaterial'] or []):
                key = (f['item'],f['location']); when = at(f['flowdate']); qty = q(f['quantity'])
                check(key in initial, 'MATERIAL_UNKNOWN_BUFFER', str(key))
                check(q(f['onhand']) >= -Decimal('0.000001'), 'MATERIAL_NEGATIVE_STOCK', f.get('id'))
                if when < now: opening[key] += qty
                elif qty: actual[event(f['item'],f['operationplan'],qty,f['flowdate'])] += 1
                groups[key].append((when,index,f))
            check(all(abs(initial[k]-opening[k]) < Decimal('0.000001') for k in set(initial)|set(opening)), 'MATERIAL_OPENING_RECEIPT_DOUBLE_COUNT')
            for key,events in groups.items():
                balance = Decimal(0)
                for when,index,f in sorted(events, key=lambda x:(x[0],x[1])):
                    qty = q(f['quantity']); balance += qty
                    check(abs(balance-q(f['onhand'])) < Decimal('0.000001'), 'MATERIAL_RUNNING_BALANCE', f.get('id'))
                    check(balance >= -Decimal('0.000001'), 'MATERIAL_NEGATIVE_RECONSTRUCTED', f.get('id'))
                    kind = 'OPENING_INITIALIZATION' if when < now else 'PURCHASE_RECEIPT' if str(f['operationplan']) in purchases else 'QC_RELEASE_OF_RECEIVED_STOCK' if str(f['operationplan']) in release_refs else 'MANUFACTURING_OUTPUT' if qty>0 else 'CONSUMPTION_OR_DELIVERY'
                    ledger.append({'item_id':key[0],'location_id':key[1],'native_reference':str(f['operationplan']),
                        'quantity':str(qty),'at':when.isoformat(),'onhand':str(balance),'event_kind':kind,'native_row_index':index})
                inv = next(i for i in data['inventory_positions'] if (i['item_id'],i['location_id']) == key)
                inventory.append({'item_id':key[0],'location_id':key[1],'uom':inv['uom'],'physical_opening_excluding_receipts':inv['on_hand'],
                    'quality_blocked':inv['quality_blocked'],'other_nonnettable':inv['other_nonnettable'],
                    'opening_net_available':str(initial[key]),'ending_net_available':str(balance)})
            check(same_events(expected,actual), 'MATERIAL_BOM_RECEIPT_EVENT_QUANTITY_OR_TIME')
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            missing.append('material_event_fields:'+type(exc).__name__)
    result_summary = summary(data); result_summary.update(inventory=inventory,material_events=ledger)
    return {'status':'FAIL' if issues else 'NOT_CHECKED' if missing else 'PASS',
        'scope':'bounded_multi_receipt_protected_mo_material_ledger', 'issues':issues,'missing_evidence':sorted(set(missing)),
        'summary':result_summary,'not_checked':['physical_execution','actual_receipt_or_quality_release','order_level_supply_allocation','binary_build_attestation'],
        'unsupported':['approved_MO_adjustment','partial_manufacturing','closed_PO_supply','multi_supplier_strategy','automatic_execution_carryforward']}
