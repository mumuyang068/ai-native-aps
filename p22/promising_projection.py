"""Pure projection of one authenticated native run into a promising snapshot.

The service must authenticate the selected manifest before supplying JSON. This
adapter preserves the native buffer iterator's order, reconciles its material
export, and protects actual native outflows once. It never infers order pegging
or turns a proposed manufacturing/purchase order into confirmed availability.
"""
import copy
from collections import defaultdict
from decimal import Decimal,InvalidOperation
from datetime import datetime
from zoneinfo import ZoneInfo

from order_promising.atp import digest,validate_identity

EPS=Decimal('0.000001')


def _require(ok,code):
    if not ok:raise ValueError(code)


def _q(value):
    try:
        q=Decimal(str(value));_require(q.is_finite(),'PROMISING_NATIVE_QUANTITY')
        return q
    except (InvalidOperation,TypeError):raise ValueError('PROMISING_NATIVE_QUANTITY') from None


def _n(q):return format(q.normalize(),'f') if q else '0'


def _time(value,zone):
    try:
        t=datetime.fromisoformat(value.replace('Z','+00:00'))
        return t.replace(tzinfo=ZoneInfo(zone)) if t.tzinfo is None else t.astimezone(ZoneInfo(zone))
    except (ValueError,AttributeError,TypeError):raise ValueError('PROMISING_NATIVE_TIME') from None


def _table(raw,key,*,optional=False):
    value=raw.get(key,raw.get(key+'.json'))
    if value is None and optional:return []
    _require(isinstance(value,list),'PROMISING_NATIVE_TABLE_REQUIRED:'+key)
    return value


def _index(rows):
    found={}
    for row in rows:
        ref=str(row.get('reference',''))
        _require(ref and ref not in found,'PROMISING_NATIVE_DUPLICATE_REFERENCE')
        found[ref]=row
    return found


def _source(row):
    p=row.get('provenance',row)
    return p.get('entity_id',row.get('id',row.get('receipt_reference','CANONICAL')))+':'+digest(p)


def _flow_reconciliation(post,flows,zone):
    """API order is deliberately ignored; multiplicity and balances must match."""
    groups=defaultdict(list)
    for flow in flows:
        key=(flow.get('item'),flow.get('location'),str(flow.get('operationplan')),_time(flow.get('flowdate'),zone))
        groups[key].append(flow)
    matched={}
    for buffer in post['buffers']:
        for i,event in enumerate(buffer['events']):
            key=(buffer['item'],buffer['location'],str(event['reference']),_time(event['at'],zone))
            bucket=groups.get(key,[])
            valid=[(j,f) for j,f in enumerate(bucket) if abs(_q(f['quantity'])-_q(event['quantity']))<=EPS
                and abs(_q(f['onhand'])-_q(event['onhand']))<=EPS]
            _require(bool(valid),'PROMISING_NATIVE_FLOW_RECONCILIATION')
            j,flow=valid[0];bucket.pop(j);matched[(buffer['item'],buffer['location'],i)]=flow
    _require(not any(groups.values()),'PROMISING_NATIVE_FLOW_RECONCILIATION')
    return matched


def _ctp_gate(data,view,protection):
    interpretation=view.get('interpretation',{})
    original=data.get('foundation_projection',{}).get('source_tables',{}).get('demand_lines',[])
    expected={r['id'] for r in original}
    if not isinstance(protection,dict) or protection.get('status')!='PASS' or not expected:return False
    impacts=protection.get('impacts',[])
    if (not isinstance(impacts,list) or len(impacts)!=len(expected)
        or {r.get('demand_id') for r in impacts}!=expected):return False
    try:
        if any(_q(r['candidate_planned'])+EPS<_q(r['base_planned']) or _q(r['delta'])<-EPS for r in impacts):return False
    except (KeyError,TypeError,ValueError):return False
    return (view.get('run',{}).get('state')=='SUCCEEDED'
        and interpretation.get('foundation_validation',{}).get('status')=='PASS'
        and interpretation.get('schedule_check')=='PASS'
        and interpretation.get('mapping_checks',{}).get('material_flows')=='PASS'
        and view.get('result',{}).get('assessment',{}).get('constraint_check')=='PASS')


def project_snapshot(data,view,raw,identity,protection=None):
    """Return event ATP input for exactly one manifest-authenticated native run.

    ``raw`` contains already read JSON values under fixed_batch_post (or post),
    operationplanmaterial, purchaseorder, manufacturingorder, distributionorder
    and deliveryorder keys. No paths are opened and no store is mutated here.
    """
    identity=validate_identity(identity);zone=data['site']['timezone']
    post=raw.get('fixed_batch_post',raw.get('fixed_batch_post.json',raw.get('post')))
    _require(isinstance(post,dict) and isinstance(post.get('buffers'),list),'PROMISING_NATIVE_POST_REQUIRED')
    run=view.get('run',{})
    _require(post.get('run_id')==run.get('solve_run_id')==identity['run_id']
        and run.get('state')=='SUCCEEDED' and post.get('phase')=='post','PROMISING_PROJECTION_RUN_IDENTITY')
    _require(post.get('input_hash')==run.get('input_hash')==digest(data)
        and data.get('foundation_projection',{}).get('source_version_id')==identity['data_version_id'],
        'PROMISING_PROJECTION_INPUT_IDENTITY')
    if isinstance(protection,dict):
        for key in ('baseline_run_id','run_id'):
            if key in protection:_require(protection[key]==identity[key],'PROMISING_PROTECTION_RUN_IDENTITY')
    flows=_table(raw,'operationplanmaterial')
    purchases=_index(_table(raw,'purchaseorder'))
    manufacture=_index(_table(raw,'manufacturingorder'))
    distribution=_index(_table(raw,'distributionorder',optional=True))
    _table(raw,'deliveryorder',optional=True)  # Never deducted a second time.
    for b in post['buffers']:_require(isinstance(b.get('events'),list),'PROMISING_NATIVE_EVENTS_REQUIRED')
    flow_matches=_flow_reconciliation(post,flows,zone)
    now=_time(data['planning_at'],zone);end=_time(data['horizon']['end_at'],zone)
    units={i['id']:i['base_uom'] for i in data['items']}
    positions={}
    for p in data['inventory_positions']:
        key=(p['item_id'],p['location_id']);_require(key not in positions,'PROMISING_OPENING_DUPLICATE')
        positions[key]=p
    releases=defaultdict(Decimal);release_refs=defaultdict(list)
    receipt_map={}
    for receipt in data.get('purchase_receipts',[]):
        key=(receipt['item_id'],receipt['location_id']);state=receipt['quality_status']
        if state=='RELEASED' and _time(receipt['release_at'],zone)<=now:
            releases[key]+=_q(receipt['quantity']);release_refs[key].append(_source(receipt))
        elif state=='PENDING_QC' and receipt.get('release_at'):
            receipt_map['QC:'+receipt['receipt_reference']]=receipt
    orders={str(p['id']):p for p in data.get('existing_orders',[])}
    sources={s['id']:s for s in data.get('supply_sources',[])}
    ctp_eligible=_ctp_gate(data,view,protection)
    events=[];excluded=[];opening_ids=[];used_po=defaultdict(Decimal);used_receipts=defaultdict(Decimal)
    seen_buffers=set()
    for buffer in post['buffers']:
        key=(buffer['item'],buffer['location'])
        _require(key not in seen_buffers and key in positions,'PROMISING_NATIVE_OPENING_SCOPE');seen_buffers.add(key)
        p=positions[key];uom=units.get(key[0])
        _require(uom and p['uom']==uom,'PROMISING_NATIVE_UOM')
        opening=_q(buffer['onhand'])
        expected=_q(p['on_hand'])-_q(p['quality_blocked'])-_q(p['other_nonnettable'])+releases[key]
        _require(abs(opening-expected)<=EPS and opening>=0,'PROMISING_NATIVE_OPENING_MISMATCH')
        dim=dict(item_id=key[0],location_id=key[1],uom=uom)
        prefix=identity['run_id']+':'+buffer['name']
        opening_id=prefix+':OPENING';opening_ids.append(opening_id)
        events.append(dict(dim,event_id=opening_id,kind='OPENING_AVAILABLE',at=now.isoformat(),sequence=-10,
            quantity=_n(opening),eligible=True,confirmed=True,reliable=True,supply_status='KNOWN_NET_OPENING',
            source_refs=[_source(p),'native:'+identity['run_id']+':buffer:'+buffer['name']]+release_refs[key],
            physical_quantity=p['on_hand'],quality_blocked=p['quality_blocked'],
            other_nonnettable=p['other_nonnettable'],released_receipts_in_opening=_n(releases[key])))
        balance=opening;previous=None
        for index,native in enumerate(buffer['events']):
            when=_time(native['at'],zone);q=_q(native['quantity']);ref=str(native['reference'])
            _require(previous is None or when>=previous,'PROMISING_NATIVE_EVENT_ORDER');previous=when
            if when<now:continue  # Native 1971 opening/history is already represented.
            balance+=q
            _require(abs(balance-_q(native['onhand']))<=EPS,'PROMISING_NATIVE_EVENT_CONSERVATION')
            if q==0:continue
            flow=flow_matches[(key[0],key[1],index)]
            event=dict(dim,event_id=prefix+':NATIVE:'+str(index),at=when.isoformat(),sequence=index*10+10,
                native_sequence=index,native_ref=ref,native_run_id=identity['run_id'],quantity=_n(abs(q)),
                source_refs=['native:'+identity['run_id']+':buffer:'+buffer['name']+':event:'+str(index),
                    'operationplanmaterial:'+str(flow.get('id',digest(flow)))],
                native_balance_after=_n(balance),quantity_mode='INCREMENTAL')
            if q<0:
                event.update(kind='PLAN_CONSUMPTION',demand_allocation='NOT_INFERRED',
                    consumption_scope='ACTUAL_NATIVE_MATERIAL_OUTFLOW_PROTECTED_ONCE')
                events.append(event);continue
            reason=None;po=orders.get(ref);api_po=purchases.get(ref)
            if po is not None and po.get('type')=='PO':
                source=sources.get(po.get('supply_source_id'),{})
                eligible=(api_po is not None and api_po.get('status')=='confirmed'
                    and po.get('source_status')=='confirmed' and po.get('inventory_includes_receipt') is False
                    and (po.get('item_id'),po.get('location_id'),po.get('uom'))==(key[0],key[1],uom)
                    and (api_po.get('item'),api_po.get('location'),api_po.get('supplier'))==(key[0],key[1],po.get('supplier_id'))
                    and (source.get('item_id'),source.get('location_id'),source.get('supplier_id'))==(key[0],key[1],po.get('supplier_id'))
                    and abs(_q(api_po.get('quantity'))-_q(po['remaining_quantity']))<=EPS
                    and _time(api_po['enddate'],zone)==_time(po['expected_receipt_at'],zone)
                    and when==_time(po['available_at'],zone))
                if eligible:
                    used_po[ref]+=q
                    _require(used_po[ref]<=_q(po['remaining_quantity'])+EPS,'PROMISING_PO_REMAINING_DOUBLE_COUNT')
                    physical=copy.deepcopy(event);physical.update(event_id=event['event_id']+':PHYSICAL',
                        kind='PO_PHYSICAL_RECEIPT',at=_time(po['expected_receipt_at'],zone).isoformat(),sequence=index*10+5,
                        source_refs=event['source_refs']+[_source(po)],supply_status='PHYSICAL_NOT_QC_USABLE')
                    events.append(physical)
                    event.update(kind='QC_RELEASE',eligible=True,confirmed=True,reliable=True,
                        supply_status='CONFIRMED_QC_USABLE',source_refs=event['source_refs']+[_source(po),_source(source)])
                else:reason='PO_NOT_CONFIRMED_OR_SOURCE_MISMATCH'
            elif ref in receipt_map:
                receipt=receipt_map[ref];used_receipts[ref]+=q
                native_receipt=manufacture.get(ref)
                receipt_po=orders.get(receipt['po_id'],{})
                eligible=(key==(receipt['item_id'],receipt['location_id']) and uom==receipt['uom']
                    and when==_time(receipt['release_at'],zone) and used_receipts[ref]<=_q(receipt['quantity'])+EPS
                    and native_receipt is not None and native_receipt.get('status')=='confirmed'
                    and abs(_q(native_receipt['quantity'])-_q(receipt['quantity']))<=EPS
                    and receipt_po.get('type')=='PO'
                    and (receipt_po.get('item_id'),receipt_po.get('location_id'),receipt_po.get('uom'))==(key[0],key[1],uom))
                _require(eligible,'PROMISING_RECEIPT_RELEASE_MISMATCH')
                event.update(kind='QC_RELEASE',eligible=True,confirmed=True,reliable=True,
                    supply_status='PHYSICALLY_RECEIVED_CONFIRMED_QC_RELEASE',
                    source_refs=event['source_refs']+[_source(receipt)])
            elif ref in purchases:reason='PROPOSED_OR_UNRECOGNIZED_PO_NOT_ATP'
            elif ref in distribution:
                # A route/proposed DO is not an executed/confirmed transfer.
                # No enterprise confirmed-DO owner is present in this scope.
                reason='DISTRIBUTION_WITHOUT_CONFIRMED_CANONICAL_ORDER'
            elif ref in manufacture:
                if ctp_eligible:
                    event.update(kind='SIMULATED_CTP_SUPPLY',native_run_audited=True,research_policy=True,
                        execution_authorized=False,eligible=False,confirmed=False,reliable=False,
                        supply_status='CTP_SIMULATED',source_refs=event['source_refs']+
                            ['manufacturingorder:'+ref,'foundation_audit:'+identity['run_id'],
                             'original_commitment_protection:'+digest(protection)])
                else:reason='MANUFACTURING_CTP_AUDIT_NOT_ELIGIBLE'
            else:reason='UNRECOGNIZED_POSITIVE_NATIVE_FLOW'
            if reason:
                event.update(kind='CONFIRMED_SUPPLY',eligible=False,confirmed=False,reliable=False,
                    supply_status='PROPOSED' if ref in purchases else 'UNRECOGNIZED_OR_UNAUDITED',
                    exclusion_reason=reason)
                excluded.append(dict(event_id=event['event_id'],native_ref=ref,**dim,quantity=event['quantity'],
                    at=event['at'],reason=reason,source_refs=event['source_refs']))
            events.append(event)
    _require(set(positions)<=seen_buffers,'PROMISING_NATIVE_OPENING_SCOPE')
    unknown=[]
    for p in data['foundation_projection'].get('source_tables',{}).get('inventory_positions',[]):
        if p.get('knowledge')=='UNKNOWN' or p.get('net_available','PRESENT') is None:
            unknown.append(dict(event_id='UNKNOWN:'+p['id'],item_id=p['item_id'],location_id=p['location_id'],
                uom=p['uom'],quantity=None,reason='UNKNOWN_EXTERNAL_STOCK_NOT_AVAILABLE',source_refs=[_source(p)]))
    # Sorting preserves native equal-time order through its monotonic sequence;
    # informational physical receipts have separate, non-supply identities.
    events.sort(key=lambda e:(_time(e['at'],zone),e['sequence'],e['event_id']))
    return dict(schema='native-promising-snapshot-v1',identity=identity,time_zone=zone,
        planning_at=data['planning_at'],horizon_end=data['horizon']['end_at'],forecast_policy='NET_ONLY',
        existing_consumption_policy='NATIVE_OUTFLOWS_PROTECTED_ONCE',events=events,commitments=[],
        excluded_sources=excluded,excluded_unknown_stock=unknown,
        provenance=dict(input_hash=post['input_hash'],native_run_id=identity['run_id'],
            source_version_id=identity['data_version_id'],native_post_sha256=digest(post),
            material_export_sha256=digest(flows),opening_event_ids=opening_ids,
            original_commitment_protection=copy.deepcopy(protection),ctp_audit_eligible=ctp_eligible,
            manifest_authentication_owner='CALLER_PLANNING_SERVICE_SELECTED_VIEW',
            event_order_owner='FIXED_BATCH_POST_BUFFER_NATIVE_ITERATOR',
            material_event_reconciliation='PASS',opening_conservation='PASS',
            forecast_representation='NATIVE_NET_DEMAND_OUTFLOWS_ONLY_NO_SECOND_FIRM_OR_FORECAST_DEDUCTION'),
        simulation_only=True,execution_authorized=False,
        limits=['Actual native plan outflows are protected; they are not inferred order-level allocations.',
            'Unplanned demand is not relabeled as an inventory reservation.',
            'Proposed purchases and unrecognized transfers are excluded from confirmed ATP.',
            'Manufacturing output is only same-run audited research CTP supply requiring explicit opt-in.',
            'The caller authenticates the full run manifest before passing these JSON values.'])
