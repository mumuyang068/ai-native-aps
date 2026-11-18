"""One explicitly sourced auxiliary purchase, in an isolated simulation only.

The native engine creates any proposed PO. This module maps the declared
option, then independently authenticates its paths, loads and QC material
events. It never purchases, releases quality, chooses quantities or adds stock.
"""
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import json
from zoneinfo import ZoneInfo

PROFILE = 'enterprise-accessory-supply-v1'
OPTION_FIELDS = {'source_id', 'enabled', 'earliest_order_at', 'latest_order_at',
                 'total_capacity_quantity', 'uom', 'fact_kind', 'permission', 'provenance'}
TOLERANCE = Decimal('0.000001')


def enabled(data):
    return data.get('rules', {}).get('manufacturing_profile') == PROFILE


def quantity(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('nonfinite quantity')
    return result


def at(value, zone):
    t = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return t.replace(tzinfo=ZoneInfo(zone)) if t.tzinfo is None else t.astimezone(ZoneInfo(zone))


def source(data):
    return next(s for s in data['supply_sources'] if s['id'] == data['accessory_supply_option']['source_id'])


def capacity_id(value):
    return 'P406-SUPPLY-CAP:' + value['id']


def validate(data):
    """Validate only the new option; the existing FG validator owns the core."""
    from planning_adapter import InputError
    def require(ok, why):
        if not ok:
            raise InputError('UNSUPPORTED_ACCESSORY_SUPPLY: ' + why)
    try:
        option = data.get('accessory_supply_option')
        if not enabled(data):
            require(option is None, 'option requires its explicit joint profile')
            return
        require(isinstance(option, dict) and set(option) == OPTION_FIELDS, 'complete, bounded option fields required')
        require(type(option['enabled']) is bool, 'enabled must be explicit boolean')
        require(option['fact_kind'] == 'SYNTHETIC_RESEARCH_ASSUMPTION' and option['permission'] == 'SIMULATION_ONLY',
                'declared independent research source and simulation permission required')
        require(len(data['supply_sources']) == len(data['suppliers']) == 2, 'one fabric and one independent LABEL supplier/source only')
        matches = [s for s in data['supply_sources'] if s['id'] == option['source_id']]
        require(len(matches) == 1, 'unknown or ambiguous source identity')
        s = matches[0]
        require(s['item_id'] == 'LABEL' and s['location_id'] == data['site']['id'] == 'FACTORY', 'only the declared LABEL at FACTORY')
        others = [v for v in data['supply_sources'] if v is not s]
        require(len(others) == 1 and others[0]['supplier_id'] != s['supplier_id'] and others[0]['item_id'] != s['item_id'],
                'the original fabric source is distinct')
        require(len([v for v in data['suppliers'] if v['id'] == s['supplier_id']]) == 1, 'unique declared supplier')
        require(not {'priority','unit_price','currency','price_basis','capacity'} & set(s),
                'strategy/price/other capacity fields are not certified for this source option')
        require(len(data['existing_orders']) == 1 and data['existing_orders'][0]['type'] == 'PO'
                and data['existing_orders'][0]['supply_source_id'] == others[0]['id'], 'only the original confirmed fabric PO may exist')
        item = next(i for i in data['items'] if i['id'] == s['item_id'])
        require(item['kind'] == 'RAW' and option['uom'] == item['base_uom'], 'canonical accessory material and units')
        require(any(l['item_id'] == s['item_id'] for b in data['bom_revisions'] for l in b['lines']), 'accessory must be a real BOM input')
        require(s['lead_time_basis'] == 'CALENDAR_TIME' and s['lead_time_seconds'] > 0 and s['batch_window_seconds'] == 0,
                'explicit positive calendar lead time; no aggregation')
        require(isinstance(s.get('receiving_qc'), dict) and s['receiving_qc']['wait_seconds'] > 0, 'nonzero evidenced QC wait required')
        minimum, multiple, capacity = [quantity(v) for v in [s['minimum_order_quantity'], s['order_multiple'], option['total_capacity_quantity']]]
        require(minimum > 0 and multiple > 0 and minimum % multiple == 0 and capacity >= minimum,
                'positive finite capacity, MOQ and integral multiple')
        zone = data['site']['timezone']
        now, end = at(data['planning_at'], zone), at(data['horizon']['end_at'], zone)
        earliest, latest = at(option['earliest_order_at'], zone), at(option['latest_order_at'], zone)
        require(now <= earliest <= latest < end, 'order window must be chronological and inside horizon')
        require(latest + timedelta(seconds=s['lead_time_seconds'] + s['receiving_qc']['wait_seconds']) <= end,
                'last permitted order must have physical receipt and QC inside horizon')
        require(at(s['effective_from'], zone) <= now and at(s['effective_to'], zone) >= end,
                'source validity must cover the complete planning period')
        for p in [option['provenance'], s['provenance'], s['receiving_qc']['provenance'],
                  next(v['provenance'] for v in data['suppliers'] if v['id'] == s['supplier_id'])]:
            require(isinstance(p, dict) and p and all(isinstance(v, str) and v.strip() for v in p.values()), 'complete provenance')
        require(capacity_id(s) not in {r['id'] for r in data['resources']}, 'capacity identity collides with production resource')
    except InputError:
        raise
    except (KeyError, StopIteration, TypeError, ValueError, ArithmeticError) as exc:
        raise InputError('UNSUPPORTED_ACCESSORY_SUPPLY: incomplete or invalid source terms') from exc


def map_native(data, native):
    """Use native ItemSupplier/QC and one total budget, never post-hoc stock."""
    if not enabled(data):
        return native
    validate(data)
    from planning_adapter import engine_time, duration
    option, s = data['accessory_supply_option'], source(data)
    key = (s['item_id'], s['supplier_id'], s['location_id'])
    if not option['enabled']:
        native['itemsupplier'] = [r for r in native['itemsupplier'] if (r['item'], r['supplier'], r['location']) != key]
        return native
    paths = [r for r in native['itemsupplier'] if (r['item'], r['supplier'], r['location']) == key]
    if len(paths) != 1:
        from planning_adapter import InputError
        raise InputError('ACCESSORY_NATIVE_SOURCE_IDENTITY')
    zone = data['site']['timezone']
    paths[0].update(hard_safety_leadtime=duration(s['receiving_qc']['wait_seconds']),
        extra_safety_leadtime=duration(0), fence=duration(0),
        effective_start=engine_time(s['effective_from'], zone), effective_end=engine_time(s['effective_to'], zone),
        resource=capacity_id(s), resource_qty='1')
    calendar = capacity_id(s) + ':TOTAL'
    native['calendar'].append({'name': calendar, 'defaultvalue': '0'})
    # Native purchase capacity is loaded at PO start. A single budget bucket
    # covers the declared inclusive order window; it does not refill each day.
    # maxearly=0 prevents loading another time bucket to bypass that window.
    end = at(option['latest_order_at'], zone) + timedelta(seconds=1)
    native['calendarbucket'].append(dict(id=len(native['calendarbucket'])+1, calendar=calendar,
        startdate=engine_time(option['earliest_order_at'], zone), enddate=engine_time(end.isoformat(), zone),
        value=option['total_capacity_quantity'], priority=1,
        **{day:True for day in ['monday','tuesday','wednesday','thursday','friday','saturday','sunday']},
        starttime='00:00:00', endtime='23:59:59'))
    native['resource'].append(dict(name=capacity_id(s), type='buckets', maximum_calendar=calendar,
        maxearly=duration(0), constrained=True))
    return native


def summary(data, raw=None):
    """Read source terms; no uncomputed improvement or execution claims."""
    if not enabled(data):
        return None
    option, s = data['accessory_supply_option'], source(data)
    result = {**option, **{k:s[k] for k in ['id','supplier_id','item_id','location_id',
        'minimum_order_quantity','order_multiple','lead_time_seconds','effective_from','effective_to']}}
    result.update(source_id=s['id'], qc_wait_seconds=s['receiving_qc']['wait_seconds'],
        simulation_only=True, business_permission='NOT_CONFIRMED', execution_authorized=False,
        capacity_scope='ONE_TOTAL_ORDER_WINDOW_BUDGET_NOT_DAILY',
        price_status='NOT_PROVIDED', unit_price=None, currency=None)
    if 'maximum_order_quantity' in s:
        result['maximum_order_quantity']=s['maximum_order_quantity']
    return result


def audit_supply_model(data, snap, phase, check, missing):
    """Authenticate both original fabric and explicitly enabled LABEL paths."""
    from planning_adapter import engine_time
    option, s = data['accessory_supply_option'], source(data)
    expected_sources = [v for v in data['supply_sources'] if v['id'] != s['id'] or option['enabled']]
    model, wip = snap.get('confirmed_supply_model'), snap.get('wip_model')
    if not isinstance(model, dict) or not isinstance(wip, dict):
        missing.append(phase+':accessory_supply_model'); return
    natural, paths = model.get('itemsuppliers'), model.get('paths')
    if not isinstance(natural, list) or not isinstance(paths, list) or not isinstance(wip.get('purchase_paths'), list):
        missing.append(phase+':accessory_supply_paths'); return
    keys = lambda rows: Counter((r.get('item'), r.get('supplier'), r.get('location')) for r in rows)
    check(keys(natural) == Counter((v['item_id'], v['supplier_id'], v['location_id']) for v in expected_sources), 'ACCESSORY_NATIVE_SOURCE_KEYS', phase)
    expected_names = []
    for v in expected_sources:
        prefix = 'Purchase '+v['item_id']+' @ '+v['location_id']+' from '+v['supplier_id']
        found = [p for p in paths if p.get('name') == prefix or str(p.get('name')).startswith(prefix+' valid from ')]
        if len(found) != 1:
            check(False, 'ACCESSORY_NATIVE_SOURCE_PATH', v['id']+':'+phase); continue
        p = found[0]; expected_names.append(p['name'])
        for field, value in [('lead_time_seconds', v['lead_time_seconds']), ('minimum', v['minimum_order_quantity']), ('multiple', v['order_multiple'])]:
            if p.get(field) is None:
                missing.append(phase+':accessory_native:'+field)
            else:
                check(quantity(p[field]) == quantity(value), 'ACCESSORY_NATIVE_SOURCE_TERMS', v['id']+':'+field)
        if 'maximum_order_quantity' in v:
            if p.get('maximum') is None:
                missing.append(phase+':accessory_native:maximum')
            else:
                check(quantity(p['maximum']) == quantity(v['maximum_order_quantity']),
                      'ACCESSORY_NATIVE_SOURCE_TERMS', v['id']+':maximum')
        flows = p.get('flows')
        if not isinstance(flows, list):
            missing.append(phase+':accessory_native_qc_flows')
        else:
            check(len(flows)==1 and flows[0].get('item')==v['item_id'] and quantity(flows[0]['quantity'])==1
                  and quantity(flows[0]['offset_seconds'])==v['receiving_qc']['wait_seconds'], 'ACCESSORY_NATIVE_QC', v['id']+':'+phase)
        if v['id'] == s['id']:
            loads = p.get('loads')
            if not isinstance(loads, list):
                missing.append(phase+':accessory_native_capacity_link')
            else:
                check(len(loads)==1 and loads[0].get('resource')==capacity_id(s) and quantity(loads[0]['quantity'])==1,
                      'ACCESSORY_NATIVE_CAPACITY_LINK', phase)
    check(len(paths)==len(expected_sources) and Counter(wip['purchase_paths'])==Counter(expected_names), 'ACCESSORY_UNDECLARED_SUPPLY_PATH', phase)
    calendars = snap.get('calendars')
    if not isinstance(calendars, list):
        missing.append(phase+':calendars'); return
    for c in data['calendars']:
        found = [a for a in calendars if a.get('name')==c['id']]
        if len(found)!=1 or not isinstance(found[0].get('buckets'), list):
            missing.append(phase+':calendar:'+c['id']); continue
        expected = Counter((engine_time(i['start_at'], data['site']['timezone']), engine_time(i['end_at'], data['site']['timezone']), Decimal(1)) for i in c['available_intervals'])
        actual = Counter((b['start'], b['end'], quantity(b['value'])) for b in found[0]['buckets'])
        check(quantity(found[0]['default'])==0 and actual==expected, 'ACCESSORY_PRODUCTION_CALENDAR_DRIFT', c['id']+':'+phase)


def audit(data, raw, *, run_id=None):
    """Missing evidence cannot certify supply; established violations win."""
    from calculation_validation import evidence_rows
    from planning_adapter import engine_time, decimal_text
    issues, missing, purchases = [], [], []
    def check(ok, code, reference=None):
        if not ok: issues.append(dict(code=code, reference=reference))
    result = dict(status='NOT_CHECKED', scope='single_sourced_accessory_native_purchase', issues=issues,
        missing_evidence=missing, unvalidated_purchases=[], purchase_references=[], certified_purchase_references=[])
    if not enabled(data):
        return {**result, 'status':'UNSUPPORTED'}
    option, s = data['accessory_supply_option'], source(data)
    zone = data['site']['timezone']
    declared = {p['id']:p for p in data['existing_orders']}
    rows, flows = evidence_rows(raw.get('purchaseorder')), evidence_rows(raw.get('operationplanmaterial'))
    if rows is None: missing.append('purchaseorder')
    if flows is None: missing.append('operationplanmaterial')
    used = Decimal(0); expected_loads = {}
    digest = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    try:
        if rows is not None:
            result['purchase_references'] = [str(p['reference']) for p in rows]
            check(len(set(result['purchase_references']))==len(rows), 'ACCESSORY_DUPLICATE_PURCHASE')
            for p in rows:
                ref = str(p['reference'])
                if ref in declared:
                    original=declared[ref]
                    check(p['status']=='confirmed' and (p['item'],p['supplier'],p['location'])==
                          (original['item_id'], original['supplier_id'], original['location_id']) and quantity(p['quantity'])==quantity(original['remaining_quantity'])
                          and at(p['enddate'],zone)==at(original['expected_receipt_at'],zone)
                          and at(p['startdate'],zone)==at(original.get('planned_start_at') or original['ordered_at'],zone),
                          'ACCESSORY_ORIGINAL_CONFIRMED_DRIFT', ref)
                    continue
                check((p['item'],p['supplier'],p['location'])==(s['item_id'],s['supplier_id'],s['location_id']), 'ACCESSORY_UNKNOWN_PURCHASE_SOURCE', ref)
                check(option['enabled'], 'ACCESSORY_DISABLED_OPTION_USED', ref)
                check(p['status']=='proposed', 'ACCESSORY_UNAUTHORIZED_CONFIRMED', ref)
                amount=quantity(p['quantity']); start, end=at(p['startdate'],zone), at(p['enddate'],zone)
                available=end+timedelta(seconds=s['receiving_qc']['wait_seconds'])
                check(amount>0 and amount>=quantity(s['minimum_order_quantity']) and amount%quantity(s['order_multiple'])==0, 'ACCESSORY_MOQ_MULTIPLE', ref)
                if 'maximum_order_quantity' in s:
                    check(amount<=quantity(s['maximum_order_quantity']), 'ACCESSORY_ORDER_MAXIMUM', ref)
                check(at(option['earliest_order_at'],zone)<=start<=at(option['latest_order_at'],zone), 'ACCESSORY_ORDER_WINDOW', ref)
                check((end-start).total_seconds()>=s['lead_time_seconds'], 'ACCESSORY_LEAD_TIME', ref)
                check(at(s['effective_from'],zone)<=start<=end<=at(s['effective_to'],zone) and available<=at(data['horizon']['end_at'],zone), 'ACCESSORY_SOURCE_VALIDITY', ref)
                if flows is not None:
                    fs=[f for f in flows if str(f.get('operationplan'))==ref]
                    check(len(fs)==1 and fs[0].get('item')==s['item_id'] and fs[0].get('location')==s['location_id']
                          and quantity(fs[0]['quantity'])==amount and at(fs[0]['flowdate'],zone)==available, 'ACCESSORY_QC_EVENT', ref)
                used+=amount; expected_loads[ref]=(amount,start)
                purchases.append(dict(reference=ref,source_id=s['id'],supplier_id=s['supplier_id'],item_id=s['item_id'],
                    location_id=s['location_id'],status=p['status'],quantity=decimal_text(amount),uom=option['uom'],
                    physical_receipt_at=end.isoformat(),available_at=available.isoformat(),
                    certification='NOT_CHECKED', simulation_only=True))
            check(set(declared)<=set(result['purchase_references']), 'ACCESSORY_ORIGINAL_CONFIRMED_MISSING')
            check(used<=quantity(option['total_capacity_quantity']), 'ACCESSORY_TOTAL_CAPACITY', s['id'])
        for phase in ['pre','post']:
            snap=raw.get('fixed_batch_'+phase)
            if not isinstance(snap,dict): missing.append('fixed_batch_'+phase); continue
            check(run_id is not None and snap.get('run_id')==run_id and snap.get('input_hash')==digest and snap.get('phase')==phase,
                  'ACCESSORY_NATIVE_IDENTITY',phase)
            check(snap.get('move_approved_early')==0 and snap.get('wip_model',{}).get('fix_broken_supply_path')=='false', 'ACCESSORY_GLOBAL_PROTECTION',phase)
            audit_supply_model(data,snap,phase,check,missing)
            native_pos=snap.get('purchaseorders')
            native_purchases=[]
            if not isinstance(native_pos,list): missing.append(phase+':purchaseorders')
            else:
                got=[p for p in native_pos if str(p.get('reference')) not in declared]
                check(len({str(p.get('reference')) for p in got})==len(got), 'ACCESSORY_DUPLICATE_PURCHASE',phase)
                expected=[] if phase=='pre' else (purchases if rows is not None else None)
                if expected is not None:
                    check(Counter(str(p['reference']) for p in got)==Counter(p['reference'] for p in expected), 'ACCESSORY_NATIVE_PO_IDENTITIES',phase)
                for p in got:
                    ref=str(p['reference']);amount=quantity(p['quantity'])
                    start,end=at(p['start'],zone),at(p['end'],zone)
                    available=end+timedelta(seconds=s['receiving_qc']['wait_seconds'])
                    check((p['item'],p['supplier'],p['location'])==(s['item_id'],s['supplier_id'],s['location_id']), 'ACCESSORY_UNKNOWN_PURCHASE_SOURCE',ref)
                    check(option['enabled'], 'ACCESSORY_DISABLED_OPTION_USED',ref)
                    check(p['status']=='proposed', 'ACCESSORY_UNAUTHORIZED_CONFIRMED',ref)
                    check(amount>0 and amount>=quantity(s['minimum_order_quantity']) and amount%quantity(s['order_multiple'])==0, 'ACCESSORY_MOQ_MULTIPLE',ref)
                    if 'maximum_order_quantity' in s:
                        check(amount<=quantity(s['maximum_order_quantity']), 'ACCESSORY_ORDER_MAXIMUM',ref)
                    check(at(option['earliest_order_at'],zone)<=start<=at(option['latest_order_at'],zone), 'ACCESSORY_ORDER_WINDOW',ref)
                    check((end-start).total_seconds()>=s['lead_time_seconds'], 'ACCESSORY_LEAD_TIME',ref)
                    check(at(s['effective_from'],zone)<=start<=end<=at(s['effective_to'],zone) and available<=at(data['horizon']['end_at'],zone), 'ACCESSORY_SOURCE_VALIDITY',ref)
                    native_purchases.append(dict(reference=ref,quantity=amount,available_at=available.isoformat()))
                    match=next((v for v in (rows or []) if str(v.get('reference'))==str(p.get('reference'))),None)
                    if match:
                        check(all(p.get(k)==match.get(k) for k in ['item','supplier','location','status']) and quantity(p['quantity'])==quantity(match['quantity'])
                              and at(p['start'],zone)==at(match['startdate'],zone) and at(p['end'],zone)==at(match['enddate'],zone), 'ACCESSORY_NATIVE_PO_READBACK',p.get('reference'))
                check(sum((p['quantity'] for p in native_purchases),Decimal(0))<=quantity(option['total_capacity_quantity']), 'ACCESSORY_NATIVE_TOTAL_CAPACITY',phase)
            if not option['enabled']: continue
            rs=snap.get('resources'); cs=snap.get('calendars')
            if not isinstance(rs,list) or not isinstance(cs,list): missing.append(phase+':accessory_capacity'); continue
            resources=[r for r in rs if r.get('name')==capacity_id(s)]
            calendars=[c for c in cs if c.get('name')==capacity_id(s)+':TOTAL']
            if len(resources)!=1 or len(calendars)!=1 or not isinstance(resources[0].get('loadplans'),list):
                missing.append(phase+':accessory_capacity'); continue
            r,c=resources[0],calendars[0]
            if r.get('maxearly_seconds') is None:
                missing.append(phase+':accessory_maxearly')
            else:
                check(quantity(r['maxearly_seconds'])==0, 'ACCESSORY_CAPACITY_TYPE',phase)
            check(r.get('type')=='resource_buckets' and r.get('maximum_calendar')==capacity_id(s)+':TOTAL', 'ACCESSORY_CAPACITY_TYPE',phase)
            loads=r['loadplans']; expected={} if phase=='pre' else expected_loads
            actual={str(l['reference']):(abs(quantity(l['quantity'])),at(l['at'],zone)) for l in loads}
            check(all(quantity(l['quantity'])<0 for l in loads) and len(actual)==len(loads), 'ACCESSORY_CAPACITY_LOADS',phase)
            check(sum((abs(quantity(l['quantity'])) for l in loads),Decimal(0))<=quantity(option['total_capacity_quantity']), 'ACCESSORY_NATIVE_TOTAL_CAPACITY',phase)
            if phase=='pre' or rows is not None:
                check(actual==expected, 'ACCESSORY_CAPACITY_LOADS',phase)
            buckets=c.get('buckets')
            if not isinstance(buckets,list): missing.append(phase+':accessory_budget_bucket')
            else:
                if len(buckets)==1 and not {'days','starttime','endtime'} <= set(buckets[0]):
                    missing.append(phase+':accessory_budget_days')
                elif len(buckets)==1:
                    check(buckets[0]['days']==127 and buckets[0]['starttime']==0 and buckets[0]['endtime']==86400,
                          'ACCESSORY_SINGLE_TOTAL_BUDGET',phase)
                check(quantity(c['default'])==0 and len(buckets)==1 and quantity(buckets[0]['value'])==quantity(option['total_capacity_quantity'])
                    and at(buckets[0]['start'],zone)==at(option['earliest_order_at'],zone)
                    and at(buckets[0]['end'],zone)==at(option['latest_order_at'],zone)+timedelta(seconds=1), 'ACCESSORY_SINGLE_TOTAL_BUDGET',phase)
            if phase=='post':
                bs=snap.get('buffers')
                if not isinstance(bs,list): missing.append('post:accessory_buffer'); continue
                found=[b for b in bs if b.get('item')==s['item_id'] and b.get('location')==s['location_id']]
                if len(found)!=1 or not isinstance(found[0].get('events'),list): missing.append('post:accessory_buffer'); continue
                # A missing REST export does not erase violations already present
                # in the independently observed native purchase and buffer rows.
                for p in native_purchases:
                    events=[e for e in found[0]['events'] if str(e.get('reference'))==p['reference']]
                    check(len(events)==1 and quantity(events[0]['quantity'])==quantity(p['quantity'])
                          and at(events[0]['at'],zone)==at(p['available_at'],zone), 'ACCESSORY_NATIVE_QC_EVENT',p['reference'])
    except (KeyError,TypeError,ValueError,ArithmeticError) as exc:
        missing.append('unreadable_accessory_evidence:'+type(exc).__name__)
    from inventory_decision import audit_policy
    policy_validation=audit_policy(data, raw, run_id=run_id)
    if policy_validation['status'] != 'UNSUPPORTED':
        issues.extend(policy_validation['issues'])
        missing.extend(policy_validation['missing_evidence'])
        result['inventory_policy_validation']=policy_validation
    status='FAIL' if issues else 'NOT_CHECKED' if missing else 'PASS'
    result['status']=status
    for p in purchases: p['certification']='NATIVE_SOURCE_QC_CAPACITY_VALIDATED' if status=='PASS' else status
    result['certified_purchase_references']=[p['reference'] for p in purchases] if status=='PASS' else []
    result['unvalidated_purchases']=[] if status=='PASS' else [dict(reference=p['reference'],reason='ACCESSORY_EVIDENCE_'+status) for p in purchases]
    result['summary']=dict(source_option=summary(data),purchase_orders=purchases if rows is not None else None,
        capacity=dict(quantity=option['total_capacity_quantity'],used=decimal_text(used) if rows is not None else None,uom=option['uom'],
                      scope='ONE_TOTAL_ORDER_WINDOW_BUDGET_NOT_DAILY'),
        limits=['simulation source terms are not supplier confirmation or purchasing permission',
                'no actual receipt, quality release, payment or optimal-cost claim'])
    result['missing_evidence']=sorted(set(missing))
    return result
