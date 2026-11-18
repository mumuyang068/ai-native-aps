"""Pure, bounded item/location policy calculations, independent of Worker state.

Normal service targets are an explicitly declared IID approximation. A target
cycle-service probability is never reported as achieved service or fill rate.
Only native Worker results may establish enterprise planning feasibility.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
from statistics import NormalDist, mean, stdev
from typing import Any, TypedDict


class InventoryError(ValueError):
    """An explicit unsupported or invalid inventory contract."""


class Dimension(TypedDict):
    item_id: str
    location_id: str
    uom: str


class InventoryRequest(TypedDict):
    schema_revision: str
    dimension: Dimension
    identity: dict[str, str]
    policy_version: str
    planning_at: str
    horizon_end: str
    history: list[dict[str, Any]]
    demand_lineage: dict[str, Any]
    source_terms: dict[str, Any]
    stock: dict[str, Any]
    current_policy: list[dict[str, Any]]
    service_policy: dict[str, Any]
    costs: dict[str, Any] | None


def require(condition, code):
    if not condition:
        raise InventoryError(code)


def number(value, *, nonnegative=True):
    try:
        require(isinstance(value, str) and bool(value.strip()), 'EXPLICIT_DECIMAL_REQUIRED')
        n = Decimal(value)
        require(n.is_finite() and (not nonnegative or n >= 0), 'INVALID_QUANTITY')
        return n
    except InvalidOperation as exc:
        raise InventoryError('INVALID_QUANTITY') from exc


def text(value):
    return format(Decimal(str(value)).normalize(), 'f')


def at(value):
    try:
        t = datetime.fromisoformat(value.replace('Z', '+00:00'))
        require(t.utcoffset() is not None, 'TIMEZONE_REQUIRED')
        return t
    except (AttributeError, TypeError, ValueError) as exc:
        raise InventoryError('INVALID_TIME') from exc


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _ceil(value, step):
    return (Decimal(str(value))/step).to_integral_value(rounding=ROUND_CEILING)*step


def _floor(value, step):
    return (Decimal(str(value))/step).to_integral_value(rounding=ROUND_FLOOR)*step


def _dimension(value):
    require(isinstance(value, dict) and set(value) == {'item_id', 'location_id', 'uom'}
        and all(isinstance(v, str) and v.strip() for v in value.values()), 'DIMENSION_REQUIRED')


def _history(r):
    hist = r['history']; require(isinstance(hist, list) and len(hist) >= 2, 'HISTORY_TOO_SHORT')
    prev = None; quantities = []; censored = False
    for h in hist:
        require(all(h.get(k) == v for k, v in r['dimension'].items()), 'HISTORY_DIMENSION')
        a, b = at(h['bucket_start']), at(h['bucket_end'])
        require(b-a == timedelta(days=1) and b <= at(r['planning_at'])
            and (prev is None or a == prev), 'HISTORY_BUCKET')
        require(bool(h.get('source_ref')), 'HISTORY_SOURCE')
        censored |= h.get('observability') != 'OBSERVED_UNCENSORED'
        quantities.append(float(number(h['quantity']))); prev = b
    require(prev == at(r['planning_at']), 'HISTORY_CUTOFF_GAP')
    return quantities, censored


def _terms(r):
    s = r['source_terms']; dim = r['dimension']
    require(all(s.get(k) == v for k, v in dim.items()), 'SOURCE_DIMENSION')
    require(bool(s.get('id')) and bool(s.get('source_ref')) and bool(s.get('capacity_source_ref')), 'SOURCE_LINEAGE')
    for k in ('lead_time_seconds', 'qc_wait_seconds'):
        require(type(s.get(k)) is int and s[k] >= 0, 'SOURCE_LEAD_TIME')
    moq, multiple, total, used = [number(s[k]) for k in ('minimum_order_quantity',
        'order_multiple', 'total_capacity_quantity', 'capacity_consumed_quantity')]
    require(multiple > 0 and moq >= 0 and 0 <= used <= total, 'SOURCE_BATCH_OR_CAPACITY')
    maximum = number(s['maximum_order_quantity']) if s.get('maximum_order_quantity') is not None else total
    require(maximum >= 0, 'SOURCE_MAXIMUM')
    lead_sd = float(number(s['lead_time_sd_days']))
    require(lead_sd == 0 or bool(s.get('lead_time_variability_source')), 'LEAD_VARIABILITY_SOURCE')
    return moq, multiple, min(maximum, total-used), (s['lead_time_seconds']+s['qc_wait_seconds'])/86400, lead_sd


def _position(r, lead):
    stock=r['stock']; now=at(r['planning_at']); arrival=now+timedelta(days=lead)
    require(stock.get('source_refs') and stock.get('status') == 'KNOWN', 'STOCK_SOURCE')
    onhand, blocked, other, reserved, backorders = [number(stock[k]) for k in
        ('on_hand', 'quality_blocked', 'other_nonnettable', 'reserved', 'backorders')]
    require(blocked+other <= onhand, 'STOCK_COMPONENTS')
    net=onhand-blocked-other-reserved-backorders
    usable=Decimal(0); later=Decimal(0); ids=set()
    for row in stock['inbound']:
        require(row.get('id') and row['id'] not in ids and row.get('source_ref'), 'INBOUND_IDENTITY')
        ids.add(row['id'])
        require(row.get('status') == 'CONFIRMED', 'INBOUND_NOT_CONFIRMED')
        physical, available = at(row['physical_receipt_at']), at(row['qc_available_at'])
        require(now <= physical <= available, 'INBOUND_QC_TIME')
        q=number(row['quantity'])
        if available <= arrival: usable += q
        else: later += q
    return dict(net_available=text(net), usable_inbound_by_replenishment=text(usable),
        later_inbound_quantity=text(later), inventory_position=text(net+usable),
        expected_physical_receipt_at=(now+timedelta(seconds=r['source_terms']['lead_time_seconds'])).isoformat(),
        expected_qc_available_at=arrival.isoformat(), basis='NET_AVAILABLE_PLUS_CONFIRMED_QC_USABLE_BY_ARRIVAL')


def _active(policy, t):
    rows=[p for p in policy if at(p['effective_from']) <= t < at(p['effective_to'])]
    if not rows: return Decimal(0), Decimal(0)
    # Existing overlapping soft targets retain the stronger active target.
    return max(number(p['minimum']) for p in rows), max(number(p['maximum']) for p in rows)


def _order(lo, hi, position, moq, multiple, cap):
    desired=max(hi-position, Decimal(0)) if position < lo else Decimal(0)
    quantity=Decimal(0); status='NO_REPLENISHMENT_TRIGGER'
    if desired > 0:
        rounded=_ceil(max(desired, moq), multiple)
        legal_cap=_floor(cap, multiple)
        if legal_cap < moq or legal_cap == 0: status='MOQ_EXCEEDS_REMAINING_CAP'
        else:
            quantity=min(rounded,legal_cap)
            status='SOURCE_CAP_LIMITED' if quantity < desired else 'RESEARCH_SUGGESTION'
    return dict(quantity=text(quantity), desired_quantity=text(desired),
        unfilled_target_quantity=text(max(desired-quantity,Decimal(0))),
        remaining_source_cap=text(cap), status=status, execution_authorized=False,
        order_created=False, feasibility='REQUIRES_NATIVE_WORKER')


def recommend(request: InventoryRequest) -> dict[str, Any]:
    """Return three comparable research candidates and one native soft policy.

    Costs are deliberately not synthesized. EOQ/newsvendor cost experiments are
    separate, explicitly cost-scoped functions in ``stockpyl_checks``.
    """
    r=copy.deepcopy(request)
    require(r.get('schema_revision') == 'inventory-optimization-request-v1', 'REQUEST_REVISION')
    _dimension(r['dimension'])
    require(isinstance(r.get('identity'),dict) and bool(r['identity'])
        and all(isinstance(v,str) and v for v in r['identity'].values()), 'IDENTITY_REQUIRED')
    require(isinstance(r.get('policy_version'),str) and bool(r['policy_version']), 'POLICY_VERSION')
    now,end=at(r['planning_at']),at(r['horizon_end']);require(now<end,'HORIZON')
    require(r.get('demand_lineage',{}).get('source_refs'), 'DEMAND_LINEAGE')
    value=dict(schema_revision='inventory-optimization-result-v1',dimension=r['dimension'],identity=r['identity'],
        request_sha256=digest(r),policy_version=r['policy_version'],lineage=copy.deepcopy(r['demand_lineage']),
        status='PASS_WITH_LIMITS',approval_status='RESEARCH_POLICY',candidates=[],native_policy=None,
        financial_status='MISSING_COST_POLICY' if r.get('costs') is None else 'RESEARCH_COSTS_SEPARATE_FROM_NATIVE_FEASIBILITY',
        financial_optimality_claim=False, engine_executed=False,
        limits=['Inventory policy is a soft planning target, not stock availability or an order.',
            'Normal protection targets assume independent IID daily demand and independent lead time.',
            'Target cycle service is not an achieved fill rate; enterprise service requires native results.',
            'Source cap is shared with existing receipts; it is never renewed per suggested order.'])
    if r['stock'].get('status') != 'KNOWN':
        return value|dict(status='POLICY_BLOCKED',reason_code='UNKNOWN_STOCK')
    history,censored=_history(r)
    if censored:return value|dict(status='POLICY_BLOCKED',reason_code='DEMAND_NOT_FULLY_OBSERVED')
    moq,multiple,cap,lead,lead_sd=_terms(r)
    policy=r['service_policy']
    require(policy.get('approval_status')=='RESEARCH_POLICY' and policy.get('source'), 'SERVICE_POLICY_AUTHORITY')
    alpha=float(number(policy['target_cycle_service_level']));review=float(number(policy['review_days']))
    step=number(policy['quantity_step'])
    require(.5 <= alpha < 1 and review>0 and step>0, 'SERVICE_POLICY_RANGE')
    normal_assumptions=policy.get('independent_iid') is True and policy.get('normal_protection_approximation') is True
    mu,sd=mean(history),stdev(history)
    forecast=r.get('demand_forecast')
    if forecast is not None:
        require(forecast.get('source_refs') and forecast.get('forecast_version_id')
            and at(forecast['training_cutoff'])<=now, 'FORECAST_LINEAGE')
        require(forecast.get('coverage')=='EXPECTED_MATERIAL_DEMAND_NO_FIRM_DOUBLE_COUNT', 'FORECAST_COVERAGE')
        mu=float(number(forecast['mean_per_day']))
        value['lineage']['forecast_input']=copy.deepcopy(forecast)
    position=_position(r,lead);value['stock_position']=position
    require(isinstance(r['current_policy'],list),'CURRENT_POLICY')
    for row in r['current_policy']:
        require(row.get('source_ref') and at(row['effective_from'])<at(row['effective_to'])
            and number(row['minimum'])<=number(row['maximum']),'CURRENT_POLICY')
    baseline_lo,baseline_hi=_active(r['current_policy'],now)
    z=NormalDist().inv_cdf(alpha)
    lead_variance=lead*sd**2+mu**2*lead_sd**2
    protection_variance=(lead+review)*sd**2+mu**2*lead_sd**2
    lead_demand=_ceil(mu*lead,step)
    # Native min/max concern time-phased NET stock. Lead-time demand remains in
    # the native demand plan; adding it here would count that demand twice.
    deterministic_lo=Decimal(0); deterministic_hi=_ceil(mu*review,step)
    service_lo=_ceil(z*math.sqrt(lead_variance),step)
    service_hi=_ceil(mu*review+z*math.sqrt(protection_variance),step)
    cases=[('CURRENT_SOFT_MIN_MAX',baseline_lo,baseline_hi),
        ('DETERMINISTIC_ORDER_UP_TO',deterministic_lo,deterministic_hi),
        ('NORMAL_SERVICE_ORDER_UP_TO',service_lo,service_hi)]
    for method,lo,hi in cases:
        c=dict(method=method,minimum=text(lo),maximum=text(hi),hard_limit=False,
            reorder_point=text(lo+lead_demand),order_up_to=text(hi+lead_demand),
            recommendation=_order(lo+lead_demand,hi+lead_demand,
                number(position['inventory_position'],nonnegative=False),moq,multiple,cap),
            metrics=dict(mean_daily_demand=mu,sample_daily_sd=sd,history_buckets=len(history),
                zero_fraction=sum(q==0 for q in history)/len(history),protection_lead_days=lead,
                review_days=review,lead_time_sd_days=lead_sd,
                safety_stock_quantity=text(lo) if method=='NORMAL_SERVICE_ORDER_UP_TO' else '0',
                lead_time_expected_demand=text(lead_demand),
                target_cycle_service_level=alpha if method=='NORMAL_SERVICE_ORDER_UP_TO' else None,
                achieved_service_level=None,expected_financial_cost=None),
            assumptions=dict(demand_basis='DAILY_OBSERVABLE_MATERIAL_DEMAND',
                statistical_model='IID_NORMAL_PROTECTION_APPROXIMATION' if method=='NORMAL_SERVICE_ORDER_UP_TO' else None),
            native_feasibility='NOT_EXECUTED')
        if method=='CURRENT_SOFT_MIN_MAX':c['source_policy']=copy.deepcopy(r['current_policy'])
        value['candidates'].append(c)
    eligible=normal_assumptions and len(history)>=30 and sum(q==0 for q in history)/len(history)<.5
    if not eligible:
        value['candidates'][2]['status']='NOT_APPLICABLE'
        value['candidates'][2]['reason_code']='SERVICE_ASSUMPTIONS_REQUIRED' if not normal_assumptions else 'INSUFFICIENT_OR_INTERMITTENT_HISTORY_FOR_NORMAL_APPROXIMATION'
        if not normal_assumptions:
            normal=value['candidates'][2]
            for field in ('minimum','maximum','reorder_point','order_up_to'):normal[field]=None
            normal['recommendation']=dict(status='NOT_APPLICABLE',quantity=None,unfilled_target_quantity=None,execution_authorized=False,order_created=False)
            normal['metrics']['safety_stock_quantity']=None
        selected=value['candidates'][1]
    else:selected=value['candidates'][2]
    value['selected_method']=selected['method']
    value['native_policy']=dict(policy_version=r['policy_version'],approval_status='RESEARCH_POLICY',
        source=f"inventory-optimization-v1 request={value['request_sha256']} method={selected['method']}",
        rows=[dict(r['dimension'],minimum=selected['minimum'],maximum=selected['maximum'],
            effective_from=r['planning_at'],effective_to=r['horizon_end'])])
    value['algorithm_receipt']=dict(implementation='stdlib-decimal-normaldist-v1',
        normal_assumptions_confirmed=normal_assumptions,
        formulas=dict(native_minimum='z*sqrt(lead*sd^2+mean^2*lead_sd^2)',
            native_maximum='mean*review + z*sqrt((lead+review)*sd^2+mean^2*lead_sd^2)',
            reorder='lead_time_expected_demand + native_minimum',
            order_up_to='lead_time_expected_demand + native_maximum'),
        translation='NET_STOCK_BUFFER;LEAD_DEMAND_REMAINS_IN_NATIVE_DEMAND_PLAN',
        source_terms_sha256=digest(r['source_terms']),history_sha256=digest(r['history']),
        stock_sha256=digest(r['stock']),native_policy_sha256=digest(value['native_policy']))
    return value


def evaluate_events(dimension: Dimension, opening: str, events: list[dict]) -> dict:
    """Audit an already sourced sequence without reordering simultaneous events."""
    _dimension(dimension);balance=number(opening,nonnegative=False);minimum=balance
    previous=None;ids=set()
    for event in events:
        require(type(event.get('native_sequence')) is int, 'EVENT_NATIVE_SEQUENCE')
        order=(at(event['at']),event['native_sequence'])
        require(previous is None or order>previous,'EVENT_ORDER');previous=order
        require(event.get('id') and event['id'] not in ids and event.get('sources'),'EVENT_SOURCE')
        ids.add(event['id'])
    for event in events:
        balance+=number(event['quantity'],nonnegative=False)
        require(balance==number(event['balance'],nonnegative=False),'EVENT_CONSERVATION')
        minimum=min(minimum,balance)
    return dict(dimension=copy.deepcopy(dimension),opening_quantity=opening,minimum_quantity=text(minimum),
        ending_quantity=text(balance),negative_inventory_status='FAIL' if minimum<0 else 'PASS',
        event_ids=[e['id'] for e in events],same_time_rule='PRESERVE_NATIVE_SEQUENCE')


def compare_policies(request: InventoryRequest, frozen_test_demand: list[str], *,
                     lead_time_days: list[float] | None = None) -> dict:
    """Out-of-sample, capped lost-sales policy comparison over frozen daily demand.

    This is a research experiment, never a substitute for native finite-material
    planning. Receipts/QC precede daily demand; review follows observed demand.
    """
    r=copy.deepcopy(request);report=recommend(r)
    require(report['native_policy'] is not None,'COMPARISON_POLICY_BLOCKED')
    require(isinstance(frozen_test_demand,list) and bool(frozen_test_demand),'TEST_DEMAND_REQUIRED')
    demands=[number(x) for x in frozen_test_demand]
    terms=_terms(r);moq,multiple,cap,lead,_=terms
    if lead_time_days is None:lead_time_days=[lead]*len(demands)
    require(len(lead_time_days)==len(demands) and all(isinstance(x,(int,float))
        and not isinstance(x,bool) and math.isfinite(x) and x>=0 for x in lead_time_days),'TEST_LEAD_DAYS')
    # review_days is the order-up-to coverage extension. This (s,S) proxy
    # observes demand and reviews daily; it does not wait review_days to react.
    review=1
    rows=[];now=at(r['planning_at'])
    for candidate in report['candidates']:
        if candidate.get('status')=='NOT_APPLICABLE':
            rows.append(dict(method=candidate['method'],status='NOT_APPLICABLE',
                reason_code=candidate['reason_code'],fill_rate=None,financial_cost=None))
            continue
        balance=number(report['stock_position']['net_available'],nonnegative=False)
        require(balance>=0,'SIMULATION_OPENING_NEGATIVE')
        remaining=cap;pipeline=[]
        for inbound in r['stock']['inbound']:
            day=max(0,math.ceil((at(inbound['qc_available_at'])-now).total_seconds()/86400))
            pipeline.append((day,number(inbound['quantity'])))
        served=lost=purchased=stock_sum=Decimal(0);stockout_days=0;orders=0;trace=[]
        for day,demand in enumerate(demands):
            receipt=sum((q for due,q in pipeline if due<=day),Decimal(0))
            pipeline=[(due,q) for due,q in pipeline if due>day];balance+=receipt
            shipped=min(balance,demand);short=demand-shipped;balance-=shipped
            served+=shipped;lost+=short;stockout_days+=int(short>0);stock_sum+=balance
            order_quantity=Decimal(0)
            if day%review==0:
                if candidate['method']=='CURRENT_SOFT_MIN_MAX':
                    lo,hi=_active(r['current_policy'],now+timedelta(days=day))
                    lead_demand=number(candidate['metrics']['lead_time_expected_demand'])
                    lo+=lead_demand;hi+=lead_demand
                else:lo,hi=number(candidate['reorder_point']),number(candidate['order_up_to'])
                position=balance+sum((q for _,q in pipeline),Decimal(0))
                order=_order(lo,hi,position,moq,multiple,remaining);order_quantity=number(order['quantity'])
                if order_quantity:
                    # Even zero lead after today's demand becomes usable next day.
                    due=day+max(1,math.ceil(lead_time_days[day]));pipeline.append((due,order_quantity))
                    remaining-=order_quantity;purchased+=order_quantity;orders+=1
            trace.append(dict(day=day,opening_receipt=text(receipt),demand=text(demand),
                shipped=text(shipped),lost=text(short),ending=text(balance),order=text(order_quantity)))
        total=sum(demands,Decimal(0))
        rows.append(dict(method=candidate['method'],status='SIMULATED_RESEARCH',fill_rate=float(served/total) if total else None,
            no_stockout_day_fraction=1-stockout_days/len(demands),stockout_days=stockout_days,
            total_demand=text(total),immediate_served_quantity=text(served),lost_quantity=text(lost),
            average_ending_stock=float(stock_sum/len(demands)),ending_stock=text(balance),
            suggested_purchase_quantity=text(purchased),order_count=orders,remaining_capacity=text(remaining),
            financial_cost=None,trace=trace))
    return dict(schema_revision='inventory-policy-comparison-v1',request_sha256=digest(r),
        test_demand_sha256=digest(frozen_test_demand),test_lead_time_sha256=digest(lead_time_days),
        scope='FROZEN_SYNTHETIC_OUT_OF_SAMPLE_LOST_SALES_SIMULATION',rows=rows,
        actual_enterprise_service_claim=False,engine_executed=False,
        event_order='QC_USABLE_RECEIPTS -> OBSERVED_DEMAND -> REVIEW_ORDER; POST_REVIEW_ZERO_LEAD_NEXT_DAY',
        limits=['Daily discretization rounds receipt availability upward; this is not native event time.',
            'Soft native buffers are converted to a daily (s,S) proxy by adding lead demand, not certified as the native algorithm.',
            'No-stockout-day fraction is not replenishment-cycle service probability.',
            'Finite source horizon cap is shared by every newly suggested order.'])
