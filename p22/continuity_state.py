"""Bounded continuity postimage checks over an authenticated parent index.

No database, cursor publication, planning clock or synthetic fact is owned here.
SourceSync supplies actual archived postimages; ImportStore supplies a lookup
bound to one confirmed parent. The original SourceSync row rules are reused.
"""
import copy
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from p21.demo_connector import TABLES
from p21.source_sync import _business, _hash, _time, merge_records


RELATION_KEYS = {'order_id', 'po_id', 'receipt_id', 'lot_id', 'job_id', 'resource_id', 'operator_id'}


class _LocalState:
    def __init__(self, lookup):
        self.lookup = lookup
        self.cache = {}
        self.overlay = defaultdict(dict)
        self.calls = 0
        self.loaded = 0
        self.context = {}

    def read(self, system, table, *, ids=None, where=None):
        if ids is None and not where:
            raise ValueError('CONTINUITY_UNBOUNDED_LOOKUP')
        if where and not set(where) <= RELATION_KEYS:
            raise ValueError('CONTINUITY_RELATION_INDEX_UNSUPPORTED')
        key = (system, table, tuple(ids) if ids is not None else None, tuple(sorted((where or {}).items())))
        if key not in self.cache:
            values = self.lookup(system, table, ids=ids, where=where)
            if not isinstance(values, list):
                raise ValueError('CONTINUITY_PARENT_LOOKUP_INVALID')
            self.cache[key] = copy.deepcopy(values)
            self.calls += 1
            self.loaded += len(values)
        rows = {entry['row']['id']: entry for entry in self.cache[key]}
        for identifier, entry in self.overlay[system, table].items():
            if (ids is not None and identifier in ids) or (where and all(entry['row'].get(k) == v for k, v in where.items())):
                rows[identifier] = entry
        return list(rows.values())

    def one(self, system, table, identifier):
        entries = self.read(system, table, ids=[identifier])
        if len(entries) > 1:
            raise ValueError('DUPLICATE_BUSINESS_IDENTITY:' + table + ':' + identifier)
        return entries[0] if entries else None


def _amount(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0:
            raise InvalidOperation()
        return result
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError('INVALID_QUANTITY') from None


def _require(condition, code):
    if not condition:
        raise ValueError(code)


def _validate_one(postimage, before, meta):
    system, table, row = (postimage[k] for k in ('system', 'table', 'row'))
    key = row['id']
    refs = postimage.get('event_refs')
    old = None
    if before:
        previous = before['row']
        previous_refs = before.get('event_refs')
        if (not isinstance(previous_refs, list) or not previous_refs or
                before.get('revision') != _hash({'business': _business(previous), 'event_refs': sorted(set(previous_refs))})):
            raise ValueError('SOURCE_PARENT_REVISION_MISMATCH:' + table + ':' + key)
        old = dict(state={table: [previous]}, revisions={table: {key: dict(revision=before['revision'],
            source_known_at=previous['source_known_at'], business_occurred_at=previous['business_occurred_at'], event_refs=previous_refs)}},
            event_refs={table: {key: previous_refs}}, pending=[], as_of=meta['previous_source_as_of'])
    elif meta.get('previous_source_as_of'):
        old = dict(state={}, revisions={}, event_refs={}, pending=[], as_of=meta['previous_source_as_of'])
    package = dict(system=system,as_of=meta['source_as_of'], mode='INCREMENTAL_UPSERT', tables={table: [row]}, event_refs={table: {key: refs}})
    if postimage.get('action'):
        package['record_actions'] = {table: {key: postimage['action']}}
    result = merge_records(package, old, postimage['source_sha256'], meta.get('confirmed_corrections', []))
    _require(result['projection_ready'], 'SOURCE_REVIEW_REQUIRED')
    change = result['changes'][0]
    return dict(system=system, table=table, id=key, row=copy.deepcopy(row),
        source_sha256=postimage['source_sha256'], **{k: copy.deepcopy(change[k]) for k in
            ('previous_revision', 'revision', 'classification', 'flags', 'event_refs')},
        **({'action': copy.deepcopy(postimage['action'])} if postimage.get('action') else {}))


def _domains(state, changes, meta, affected):
    definition = meta['declarations']['definition']
    manufacturing = False
    supplier_capacity = False
    structural_supply_addition = False
    for change in changes:
        if change['classification'] in {'DUPLICATE', 'UNCHANGED'}:
            continue
        system, table, row = change['system'], change['table'], change['row']
        # New supply/stock members introduce derived Canonical references and
        # supplier groups. The bounded existing-member closure cannot certify
        # those additions; the original complete Owner must do so before save.
        if change['classification'] == 'ADDED' and (system,table) in {('ERP','purchase_orders'),('WMS','lots')}:
            structural_supply_addition = True
        state.context=dict(system=system,table=table,id=row['id'])
        if system == 'ERP' and table == 'sales_orders':
            from p22.continuity_sources import order_demand
            order_demand(row,definition,meta['declarations']['operating'])
            _require(row.get('product_id') in definition['products'], 'UNSUPPORTED_CONTINUITY_PRODUCT:' + row['id'])
            affected['orders'].add(row['id'])
        if row.get('order_id'):
            affected['orders'].add(row['order_id'])
        if row.get('po_id'):
            affected['purchase_orders'].add(row['po_id'])
        if row.get('lot_id'):
            affected['lots'].add(row['lot_id'])
        if row.get('item_id'):
            affected['materials'].add(row['item_id'])
        if row.get('job_id'):
            affected['jobs'].add(row['job_id'])
        if row.get('resource_id'):
            affected['resources'].add(row['resource_id'])
        if system == 'ERP' and table == 'purchase_orders':
            affected['purchase_orders'].add(row['id'])
        if system == 'MES' or (system == 'WMS' and table in {'finished_goods', 'movements'}):
            manufacturing = True
        if system == 'WMS' and table == 'receipts' and not meta['declarations']['operating'].get('year_policy'):
            supplier_capacity = True

    # One PO closure, not all receipts in the enterprise. WMS receipt authority
    # and ERP late recognition retain the original builder's exact equations.
    for identifier in sorted(affected['purchase_orders']):
        state.context=dict(system='ERP',table='purchase_orders',id=identifier)
        entry = state.one('ERP', 'purchase_orders', identifier)
        _require(entry is not None, 'CONTINUITY_RECEIPT_PO_UNRESOLVED:' + identifier)
        po = entry['row']
        wms = [r['row'] for r in state.read('WMS', 'receipts', where={'po_id': identifier})]
        erp = [r['row'] for r in state.read('ERP', 'purchase_receipts', where={'po_id': identifier})]
        windex = {r['id']: r for r in wms}
        for receipt in wms:
            lot_entry = state.one('WMS', 'lots', receipt['lot_id'])
            _require(lot_entry is not None, 'CONTINUITY_RECEIPT_LINK_CONFLICT:' + receipt['id'])
            lot = lot_entry['row']
            _require((receipt['item_id'], receipt['unit']) == (po['item_id'], po['unit']) and
                (lot['po_id'], lot['item_id'], lot['unit']) == (receipt['po_id'], receipt['item_id'], receipt['unit']),
                'CONTINUITY_RECEIPT_LINK_CONFLICT:' + receipt['id'])
            affected['lots'].add(lot['id'])
        for receipt in erp:
            other = windex.get(receipt['id'])
            _require(other is not None and all(receipt[k] == other[k] for k in ('po_id', 'lot_id', 'item_id', 'quantity', 'unit')),
                'CONTINUITY_ERP_WMS_RECEIPT_CONFLICT:' + receipt['id'])
        received = sum((_amount(r['quantity']) for r in wms), Decimal(0))
        erp_received = sum((_amount(r['quantity']) for r in erp), Decimal(0))
        _require(erp_received == _amount(po['received']) and _amount(po['quantity']) - erp_received == _amount(po['outstanding']),
            'CONTINUITY_ERP_PO_BALANCE_CONFLICT:' + identifier)
        _require(_amount(po['quantity']) >= received >= erp_received, 'CONTINUITY_PO_RECEIPT_CONFLICT:' + identifier)
        if _amount(po['quantity'])>received:
            _require(_time(po['eta'])>=_time(meta['source_as_of']), 'CONTINUITY_PAST_DUE_UNRECEIVED_ETA_REQUIRES_REVIEW:' + identifier)

    for identifier in sorted(affected['lots']):
        state.context=dict(system='WMS',table='lots',id=identifier)
        entry = state.one('WMS', 'lots', identifier)
        _require(entry is not None, 'CONTINUITY_REQUIRED_STOCK_UNKNOWN:' + identifier)
        lot = entry['row']
        movements = [entry['row'] for entry in state.read('WMS', 'movements', where={'lot_id': identifier})]
        issued = sum((_amount(r['quantity']) for r in movements if r.get('kind') == 'MaterialIssued'), Decimal(0))
        _require(_amount(lot['quantity']) - _amount(lot['consumed']) == _amount(lot['physical']) and
            issued == _amount(lot['consumed']) and _amount(lot['physical']) == _amount(lot['available']) + _amount(lot['pending']) + _amount(lot['held']),
            'CONTINUITY_CURRENT_LOT_BALANCE_CONFLICT:' + identifier)
        # The quality source postimage remains authoritative. Never infer an
        # additional stock receipt or availability from an ERP/QC notification.
        state.read('WMS', 'quality_events', where={'lot_id': identifier})

    for identifier in sorted(affected['jobs']):
        state.context=dict(system='MES',table='manufacturing_tasks',id=identifier)
        entry = state.one('MES', 'manufacturing_tasks', identifier)
        _require(entry is not None, 'CONTINUITY_MES_ORPHAN:' + identifier)
        job = entry['row']
        affected['orders'].add(job['order_id'])
        order = state.one('ERP', 'sales_orders', job['order_id'])
        _require(order is not None and order['row']['product_id'] == job['product_id'], 'CONTINUITY_JOB_ORDER_CONFLICT:' + identifier)
        state.read('MES', 'operations', where={'job_id': identifier})
        issues = state.read('MES', 'material_issues', where={'job_id': identifier})
        for entry in issues:
            lot = state.one('WMS', 'lots', entry['row']['lot_id'])
            _require(lot is not None and lot['row']['unit'] == entry['row']['unit'], 'WIP_ISSUE_LOT_UNRESOLVED:' + entry['row']['id'])
            affected['lots'].add(lot['row']['id'])
    return ('MANUFACTURING_DOMAIN_REVALIDATION_REQUIRED' if manufacturing else
        'SUPPLIER_CAPACITY_DOMAIN_REVALIDATION_REQUIRED' if supplier_capacity else
        'STRUCTURAL_SUPPLY_ADDITION_REVALIDATION_REQUIRED' if structural_supply_addition else None)


def prepare_changes(postimages, lookup, meta):
    """Validate actual postimages and their bounded business relationships.

    ``lookup`` is bound by ImportStore to the authenticated confirmed parent.
    Entries are {row, revision, event_refs}; no complete-parent read is requested.
    A fallback is explicit and cannot be treated as a successful local audit.
    """
    state = _LocalState(lookup)
    affected = {key: set() for key in ('orders', 'purchase_orders', 'lots', 'jobs', 'resources', 'materials')}
    result = dict(changes=[], affected={}, issues=[], fallback_reason=None)
    context = {}
    try:
        _require(isinstance(postimages, list) and isinstance(meta, dict), 'CONTINUITY_CHANGESET_INVALID')
        _require(isinstance(meta.get('declarations'), dict) and meta.get('enterprise_id') == meta['declarations']['definition']['enterprise_id'], 'CONTINUITY_IDENTITY_MISMATCH')
        _require(_time(meta['source_as_of']) >= _time(meta['previous_source_as_of']), 'KNOWLEDGE_CLOCK_REGRESSION')
        seen = set()
        for postimage in postimages:
            context = {k: postimage.get(k) for k in ('system', 'table')}
            system, table, row = (postimage[k] for k in ('system', 'table', 'row'))
            _require(system in TABLES and table in TABLES[system], 'SOURCE_TABLE_SCHEMA_CHANGED')
            _require(isinstance(row, dict) and isinstance(row.get('id'), str) and row['id'], 'BUSINESS_RECORD_ID_REQUIRED')
            context['id'] = row['id']
            key = system, table, row['id']
            _require(key not in seen, 'DUPLICATE_RECORD_ID')
            seen.add(key)
            before = state.one(system, table, row['id'])
            change = _validate_one(postimage, before, meta)
            result['changes'].append(change)
            state.overlay[system, table][row['id']] = dict(row=change['row'], revision=change['revision'], event_refs=change['event_refs'])
        result['fallback_reason'] = _domains(state, result['changes'], meta, affected)
        if meta.get('previous_declarations_sha256') and meta['previous_declarations_sha256'] != meta.get('declarations_sha256'):
            result['fallback_reason'] = 'GLOBAL_DECLARATIONS_CHANGED'
    except (ValueError, KeyError, TypeError, InvalidOperation) as error:
        reason = str(error).split(':', 1)[0]
        lineage=state.context or context
        field=str(error).rsplit(':',1)[-1]
        cause=error.__cause__
        if hasattr(cause,'errors') and cause.errors():field=str(cause.errors()[0]['loc'][0])
        field={'due_at':'due'}.get(field,field)
        if field in {'priority','due','quantity','shipped','customer_id','product_id'}:lineage=dict(lineage,field=field)
        result['issues'].append(dict(lineage, code=reason, detail=str(error)))
        result['changes'] = []
    result['affected'] = {key: sorted(values) for key, values in affected.items()}
    result['metrics'] = dict(changed_records=sum(c['classification'] not in {'DUPLICATE', 'UNCHANGED'} for c in result['changes']),
        lookup_calls=state.calls, parent_records_loaded=state.loaded, full_table_reads=0, full_builder_calls=0)
    return result
