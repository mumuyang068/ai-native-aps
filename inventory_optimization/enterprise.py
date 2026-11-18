"""Read-only adapter from the existing canonical projection to inventory inputs."""
import copy
from decimal import Decimal

from .core import at, number, text, require


def _ref(row):
    p=row.get('provenance',row)
    return p.get('entity_id',row.get('id',''))+'#'+p.get('source_sha256','')


def opening_stock(positions):
    """Aggregate disjoint Canonical positions exactly as the native buffer does.

    Only identical dimension, unit and observation time may be combined. Every
    component remains in the receipt; missing or unknown balances are refused.
    """
    require(bool(positions),'ENTERPRISE_KNOWN_STOCK_NOT_UNIQUE')
    first=positions[0]
    require(len({p['id'] for p in positions})==len(positions),'ENTERPRISE_STOCK_DUPLICATE')
    fields=('item_id','location_id','uom','as_of')
    require(all(all(p.get(k)==first.get(k) for k in fields) for p in positions),'ENTERPRISE_STOCK_SCOPE_MISMATCH')
    for p in positions:
        require(all(p.get(k) is not None for k in ('on_hand','quality_blocked','other_nonnettable')),'ENTERPRISE_STOCK_UNKNOWN')
        require(number(p['on_hand'])>=number(p['quality_blocked'])+number(p['other_nonnettable'])>=0,'ENTERPRISE_STOCK_BALANCE')
    if len(positions)==1:return first
    return dict(id='AGGREGATED_CANONICAL_POSITIONS',**{k:first.get(k) for k in fields},
        **{k:text(sum((number(p[k]) for p in positions),Decimal(0))) for k in ('on_hand','quality_blocked','other_nonnettable')})


def build_request(data, *, history, demand_lineage, source_id, policy_version,
                  service_policy, reserved_quantity, reserved_source_ref,
                  demand_forecast=None):
    """Translate sourced fields without changing canonical data or supplier terms.

    Reservation scope must be explicit because this adapter does not own the
    promising hold store. The caller supplies holds from the same selected state.
    All source capacities retain WHOLE_HORIZON_RECEIPTS semantics.
    """
    from p22.foundation_projection import validate
    validate(data)
    sources=[s for s in data['supply_sources'] if s['id']==source_id]
    require(len(sources)==1,'ENTERPRISE_SOURCE_NOT_UNIQUE');source=sources[0]
    dim={k:source[k] for k in ('item_id','location_id')}
    positions=[p for p in data['inventory_positions'] if all(p.get(k)==v for k,v in dim.items())]
    stock=opening_stock(positions)
    dim['uom']=stock['uom'];cap=source['capacity']
    require(cap['scope']=='WHOLE_HORIZON_RECEIPTS' and cap['uom']==dim['uom']
        and at(cap['start_at'])<=at(data['planning_at'])
        and at(cap['end_at'])>=at(data['horizon']['end_at']), 'ENTERPRISE_CAPACITY_SCOPE')
    require(source['lead_time_basis']=='CALENDAR_TIME'
        and source['receiving_qc']['basis']=='CALENDAR_TIME','ENTERPRISE_LEAD_BASIS')
    require(reserved_source_ref and number(reserved_quantity)>=0,'RESERVATION_SCOPE_REQUIRED')
    inbound=[];used=Decimal(0);capacity_refs=[]
    source_tables=data['foundation_projection']['source_tables']
    for po in source_tables['purchase_orders']:
        if any(po.get(k)!=v for k,v in dim.items()):continue
        require(po['status'] in {'confirmed','cancelled'},'ENTERPRISE_PO_STATUS_UNSUPPORTED')
        if po['status']=='cancelled':continue
        q=number(str(po['unreceived_quantity']))
        if q==0:continue
        require(po['included_in_opening'] is False,'ENTERPRISE_PO_OPENING_DOUBLE_COUNT')
        inbound.append(dict(id=po['id'],quantity=text(q),physical_receipt_at=po['physical_receipt_at'],
            qc_available_at=po['available_at'],status='CONFIRMED',source_ref=_ref(po)))
        if po['source_id']==source_id and at(cap['start_at'])<=at(po['available_at'])<at(cap['end_at']):
            used+=q;capacity_refs.append(_ref(po))
    # A verified empty source PO list is an observation, not a capacity reset.
    if not capacity_refs:capacity_refs=['CONFIRMED_CANONICAL_PURCHASE_ORDERS_EMPTY_FOR_SOURCE:'+source_id]
    current=[]
    for p in source_tables.get('inventory_targets',[]):
        if all(p.get(k)==v for k,v in dim.items()):
            require(p.get('hard_limit') is False,'ENTERPRISE_HARD_INVENTORY_LIMIT_UNSUPPORTED')
            current.append(dict(minimum=str(p['minimum']),maximum=str(p['maximum']),
                effective_from=p['effective_from'],effective_to=p['effective_to'],source_ref=_ref(p)))
    result=dict(schema_revision='inventory-optimization-request-v1',dimension=dim,
        identity=dict(data_version_id=data['foundation_projection']['source_version_id'],
            projection_sha256=data['foundation_projection']['projection_sha256']),
        policy_version=policy_version,planning_at=data['planning_at'],horizon_end=data['horizon']['end_at'],
        history=copy.deepcopy(history),demand_lineage=copy.deepcopy(demand_lineage),
        source_terms=dict(id=source_id,**dim,lead_time_seconds=source['lead_time_seconds'],
            qc_wait_seconds=source['receiving_qc']['wait_seconds'],
            minimum_order_quantity=source['minimum_order_quantity'],order_multiple=source['order_multiple'],
            maximum_order_quantity=source.get('maximum_order_quantity'),
            total_capacity_quantity=cap['quantity'],capacity_consumed_quantity=text(used),
            capacity_source_ref=';'.join(capacity_refs),source_ref=_ref(source),lead_time_sd_days='0'),
        stock=dict(status='KNOWN',on_hand=stock['on_hand'],quality_blocked=stock['quality_blocked'],
            other_nonnettable=stock['other_nonnettable'],reserved=reserved_quantity,backorders='0',
            inbound=inbound,source_refs=[*[_ref(p) for p in positions],reserved_source_ref],
            backorder_scope='NONE_DECLARED_IN_CANONICAL_OPENING;FIRM_DEMAND_STAYS_IN_NATIVE_WORKER'),
        current_policy=current,service_policy=copy.deepcopy(service_policy),costs=None,
        enterprise_source_receipt=dict(stock=copy.deepcopy(stock),stock_positions=copy.deepcopy(positions),source=copy.deepcopy(source),
            capacity_reserved_by_existing_po=capacity_refs,current_policy=copy.deepcopy(current),
            original_fact_tables_unchanged=True,unknown_external_stock_used=False))
    if demand_forecast is not None:result['demand_forecast']=copy.deepcopy(demand_forecast)
    return result
