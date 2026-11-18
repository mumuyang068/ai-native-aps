"""Read-only adjacent source checkpoints, each through its own baseline Owner.

This is a business-state timeline comparison, not a cross-plan optimization or
another source ledger. No SourceSync publication or planning submission occurs.
"""
import hashlib
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation


LIMITS = [
    '两个时点的订单、库存、在制、规划起点和可用条件可能同时变化；这是跨时点状态对照，不是算法 A/B 优劣实验。',
    'NO_LONGER_OPEN 只表示需求不在下一时点的开放需求快照中；不能据此认定已取消、已交付或已结清。',
    'planned/unplanned 是各自已保存 baseline 的规划数量，不代表实际生产、发货或执行承诺。',
]


def _fail(code):
    raise ValueError(code)


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _instant(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.utcoffset() is None:
            raise ValueError()
        return result
    except (ValueError, TypeError, AttributeError):
        _fail('CONNECTOR_COMPARISON_SOURCE_MISMATCH')


def _quantity(value):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0:
            raise ValueError()
        return number
    except (InvalidOperation, ValueError, TypeError):
        _fail('CONNECTOR_COMPARISON_DEMAND_EVIDENCE_MISMATCH')


def _text(value):
    return format(value.normalize(), 'f')


def _demand_projection(data, result):
    """Reconcile quantities to this input; never manufacture missing results."""
    deliveries = {}
    for delivery in result.get('deliveries', []):
        identifier = delivery.get('demand_id')
        if not identifier or identifier in deliveries:
            _fail('CONNECTOR_COMPARISON_DEMAND_EVIDENCE_MISMATCH')
        deliveries[identifier] = delivery
    rows = {}
    totals = {}
    for demand in data['demand_lines']:
        identifier = demand['id']
        delivery = deliveries.get(identifier)
        if identifier in rows or delivery is None:
            _fail('CONNECTOR_COMPARISON_DEMAND_EVIDENCE_MISMATCH')
        opened = _quantity(demand['quantity'])
        planned = _quantity(delivery.get('planned_quantity'))
        unplanned = _quantity(delivery.get('unfulfilled_quantity'))
        if (delivery.get('uom') != demand['uom'] or planned + unplanned != opened
            or _quantity(delivery.get('requested_quantity')) != opened
            or delivery.get('due_at') != demand['due_at']):
            _fail('CONNECTOR_COMPARISON_DEMAND_EVIDENCE_MISMATCH')
        row = {k: demand[k] for k in ('item_id', 'location_id', 'uom', 'due_at')}
        row.update(open=_text(opened), planned=_text(planned), unplanned=_text(unplanned),
            priority=demand.get('priority'), status=delivery.get('status'),
            planned_delivery_at=delivery.get('planned_delivery_at'))
        rows[identifier] = row
        total = totals.setdefault(demand['uom'], [Decimal(0), Decimal(0), Decimal(0)])
        for index, number in enumerate((opened, planned, unplanned)):
            total[index] += number
    if set(deliveries) != set(rows):
        _fail('CONNECTOR_COMPARISON_DEMAND_EVIDENCE_MISMATCH')
    return rows, [dict(uom=unit, open=_text(value[0]), planned=_text(value[1]), unplanned=_text(value[2]))
        for unit, value in sorted(totals.items())]


def _side(plans, version, receipt):
    vid = version['id']
    side = dict(version_id=vid, plan_id=None, run_id=None, as_of=receipt['as_of'],
        status='NOT_RUN', totals=[])
    records = [r for r in plans.store.list() if r['data_version_id'] == vid]
    if not records:
        return side, None
    # PlanStore is unique per DATA version. Sort defensively for compatible
    # Owners; baseline_id selects its current explicit retry, never a draft.
    record = sorted(records, key=lambda r: r.get('created_at', ''), reverse=True)[0]
    pid = record['id']
    side['plan_id'] = pid
    snapshot = plans.store.snapshot(pid)
    if (snapshot.get('data_version') != version
        or record.get('data_version_sha256') != _digest(version)):
        _fail('CONNECTOR_COMPARISON_SOURCE_MISMATCH')
    service = plans.service(pid)
    expected = dict(plan_id=pid, data_version_id=vid, snapshot_sha256=record['snapshot_sha256'])
    if any(service.enterprise.get(k) != v for k, v in expected.items()):
        _fail('RUN_SOURCE_MISMATCH')
    service.drafts.verify_baseline()
    data = service.drafts.baseline
    if data != snapshot['plan_input'] or data['scenario_id'] != pid:
        _fail('RUN_SOURCE_MISMATCH')
    try:
        rid = service.baseline_id()
    except ValueError as error:
        if str(error).split(':', 1)[0] != 'BASELINE_NOT_READY':
            raise
        return side, None
    side['run_id'] = rid
    try:
        worker = service.compute().run(rid)
    except KeyError:
        return side, None
    side['run_state'] = worker.get('state')
    identities = dict(solve_run_id=rid, scenario_id=pid,
        scenario_revision=data['scenario_revision'], snapshot_id=data['snapshot_id'],
        input_hash=_digest(data))
    if any(worker.get(k) != v for k, v in identities.items()):
        _fail('RUN_SOURCE_MISMATCH')
    if worker.get('state') != 'SUCCEEDED':
        return side, None
    # selected_view authenticates the original manifests/artifacts and reads the
    # PlanResult through the original compute/RunStore result endpoint.
    try:
        view = service.selected_view('baseline')
    except FileNotFoundError:
        return side, None
    except ValueError as error:
        if str(error).split(':', 1)[0] != 'RESULT_NOT_READY':
            raise
        return side, None
    result = view.get('result')
    if not result:
        return side, None
    if (view.get('run', {}).get('state') != 'SUCCEEDED'
        or any(view['run'].get(k) != v or result.get(k) != v for k, v in identities.items())
        or any(view.get('context', {}).get('enterprise_source', {}).get(k) != v
               for k, v in expected.items())
        or result.get('engine', {}).get('engine_task_ref') != worker.get('engine_task_ref')
        or result.get('result_hash') != _digest({k: v for k, v in result.items() if k != 'result_hash'})):
        _fail('RUN_SOURCE_MISMATCH')
    for key, value in expected.items():
        if key in result and result[key] != value:
            _fail('RUN_SOURCE_MISMATCH')
    rows, totals = _demand_projection(data, result)
    bundle=next((r.get('odoo_bundle') for r in data.get('foundation_projection',{}).get('source_tables',{}).get('locations',[]) if r.get('odoo_bundle')),None)
    if bundle:
        from interop.odoo_business_audit import stored_quantity,audit_stock
        docs=bundle['capture']['documents']
        side['odoo']=dict(company_id=bundle['capture']['company_id'],stock_audit=audit_stock(bundle['capture'])['status'],
            physical=[dict(item_id=r['item_id'],quantity=r['physical'],uom=r['uom']) for r in data['foundation_projection']['source_tables']['inventory_positions']],
            manufacturing=[dict(id=m['id'],name=m['name'],quantity=m['product_qty'],produced=m['qty_produced'],stored=str(stored_quantity(bundle['capture'],m)),state=m['state'],backorder_sequence=m['backorder_sequence']) for m in docs['mrp.production']],
            future_operations=len(result['operations']),proposed_manufacture_count=len(result['manufacture_suggestions']),
            native_business_audit=view['interpretation'].get('foundation_validation',{}).get('status','NOT_CHECKED'))
    side.update(status='SUCCEEDED', totals=totals)
    return side, rows


def compare_checkpoints(connector_service, enterprise_plans, previous_version_id, current_version_id,*,include_intermediate=False):
    """Compare only committed, same-enterprise, direct parent DATA versions."""
    if previous_version_id == current_version_id:
        _fail('CONNECTOR_COMPARISON_PARENT_REQUIRED')
    history = connector_service._records()  # No get_sync_status/recovery side effect.
    identifiers=[previous_version_id,current_version_id]
    if include_intermediate:
        committed={r['version_id']:r for r in history if r.get('status')=='COMMITTED' and r.get('version_id')}
        reverse=[current_version_id]
        while reverse[-1]!=previous_version_id:
            parent=committed.get(reverse[-1],{}).get('parent_version_id')
            if not parent or parent in reverse or parent not in committed:_fail('CONNECTOR_COMPARISON_PARENT_REQUIRED')
            reverse.append(parent)
        identifiers=list(reversed(reverse))
    selected = []
    enterprise = connector_service.adapter.enterprise_id
    for identifier in identifiers:
        records = [r for r in history if r.get('version_id') == identifier and r.get('status') == 'COMMITTED']
        if not records:
            _fail('CONNECTOR_COMPARISON_COMMITTED_REQUIRED')
        record = records[-1]
        if record.get('enterprise_id') != enterprise:
            _fail('CONNECTOR_COMPARISON_ENTERPRISE_MISMATCH')
        version = connector_service.imports.version(identifier)
        bundle=next((r.get('odoo_bundle') for r in version.get('canonical',{}).get('tables',{}).get('locations',[]) if r.get('odoo_bundle')),None)
        if bundle and record.get('ingestion_mode')=='SCOPED_ERP_DOMAIN_REVALIDATION':
            # ERP domain snapshots retain parentage in the original Connector
            # journal. Never invent three-system continuity receipts or alter
            # already confirmed immutable DATA versions.
            from p21.odoo_connector import source_revision
            connector_service.imports.verify_version_sources(version)
            if (version['canonical']['enterprise_id']!=enterprise or bundle['capture']['enterprise_id']!=enterprise
                or source_revision(bundle['capture'])!=record['source_revision']
                or version['canonical']['semantic_sha256']!=record['canonical_semantic_sha256']
                or enterprise_plans.imports.version(identifier)!=version):_fail('CONNECTOR_COMPARISON_SOURCE_MISMATCH')
            receipt=dict(enterprise_id=enterprise,as_of=bundle['capture']['captured_at'],lineage_basis='ORIGINAL_CONNECTOR_COMMIT_JOURNAL')
        else:
            receipt = version.get('source_sync_receipt', {})
            if (version.get('id') != identifier or version.get('canonical', {}).get('enterprise_id') != enterprise
                or receipt.get('enterprise_id') != enterprise):
                _fail('CONNECTOR_COMPARISON_ENTERPRISE_MISMATCH')
            if (record.get('sync_receipt') != receipt or receipt.get('as_of') != record.get('as_of')
                or enterprise_plans.imports.version(identifier) != version):
                _fail('CONNECTOR_COMPARISON_SOURCE_MISMATCH')
        selected.append((record, version, receipt))
    before, after = selected[0],selected[-1]
    if before[2].get('lineage_basis')=='ORIGINAL_CONNECTOR_COMMIT_JOURNAL' or after[2].get('lineage_basis')=='ORIGINAL_CONNECTOR_COMMIT_JOURNAL':
        captures=[side[1]['canonical']['tables']['locations'][0]['odoo_bundle']['capture'] for side in selected]
        if any(c[k]!=captures[0][k] for c in captures[1:] for k in ('database','company_id','enterprise_id')):_fail('CONNECTOR_COMPARISON_SOURCE_MISMATCH')
    for left,right in zip(selected,selected[1:]):
        if (right[0].get('parent_version_id') != left[1]['id']
            or (right[2].get('lineage_basis')!='ORIGINAL_CONNECTOR_COMMIT_JOURNAL' and right[1].get('parent_version_id') != left[1]['id'])):
            _fail('CONNECTOR_COMPARISON_PARENT_REQUIRED')
        if _instant(right[2]['as_of']) < _instant(left[2]['as_of']):_fail('CONNECTOR_COMPARISON_SOURCE_MISMATCH')
    previous, old = _side(enterprise_plans, before[1], before[2])
    current, new = _side(enterprise_plans, after[1], after[2])
    changes = []
    if old is not None and new is not None:
        for identifier in sorted(set(old) | set(new)):
            left, right = old.get(identifier), new.get(identifier)
            if left == right:
                continue
            classification = 'ADDED' if left is None else 'NO_LONGER_OPEN' if right is None else 'CHANGED'
            changes.append(dict(demand_id=identifier, classification=classification,
                previous=left, current=right))
    return dict(status='COMPARED' if old is not None and new is not None else 'NOT_RUN',
        previous=previous, current=current, demand_changes=changes,lineage_versions=identifiers,limits=list(LIMITS))
