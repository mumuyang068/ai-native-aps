"""Real Stockpyl research execution, only in the pinned task environment."""
from importlib.metadata import version
import math
from statistics import NormalDist

from .core import InventoryError


def classical_models(*, fixed_cost, holding_cost, demand_rate, overage_cost,
                     underage_cost, demand_mean, demand_sd):
    from stockpyl.eoq import economic_order_quantity
    from stockpyl.newsvendor import newsvendor_normal
    values=(fixed_cost,holding_cost,demand_rate,overage_cost,underage_cost,demand_mean,demand_sd)
    if not all(isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) and x>0 for x in values):
        raise InventoryError('EXPLICIT_POSITIVE_COST_AND_DEMAND_REQUIRED')
    q,c=economic_order_quantity(fixed_cost,holding_cost,demand_rate)
    s,nc=newsvendor_normal(overage_cost,underage_cost,demand_mean,demand_sd)
    qe=math.sqrt(2*fixed_cost*demand_rate/holding_cost)
    se=demand_mean+NormalDist().inv_cdf(underage_cost/(overage_cost+underage_cost))*demand_sd
    if not(math.isclose(q,qe,rel_tol=1e-12) and math.isclose(s,se,rel_tol=1e-12)):
        raise InventoryError('STOCKPYL_CLOSED_FORM_MISMATCH')
    return dict(stockpyl_version=version('stockpyl'),eoq=dict(quantity=q,cost_per_time=c),
        newsvendor=dict(quantity=s,expected_cost=nc),closed_form_check='PASS',
        cost_scope='EXPLICIT_RESEARCH_COEFFICIENTS_ONLY',enterprise_financial_optimality=False,
        limits=['EOQ assumes constant unconstrained demand and replenishment; no MOQ/cap/QC feasibility claim.',
            'Newsvendor is a single-period normal-demand cost experiment, not a recurring procurement optimum.'])


def two_node_simulation(*, seed=61025, periods=90):
    """Actual Stockpyl two-stage serial run; no enterprise topology inference."""
    from stockpyl.supply_chain_network import serial_system
    from stockpyl.sim import simulation
    if type(seed) is not int or type(periods) is not int or not 1<=periods<=366:
        raise InventoryError('BOUNDED_SIMULATION_REQUIRED')
    network=serial_system(num_nodes=2,node_order_in_system=[0,1],
        demand_type='P',mean=4,policy_type=['BS','BS'],base_stock_level=[22,14],
        shipment_lead_time=[2,1],local_holding_cost=[.1,.2],stockout_cost=[0,2])
    cost=simulation(network=network,num_periods=periods,rand_seed=seed,progress_bar=False)
    nodes=[]
    for node in sorted(network.nodes,key=lambda n:n.index):
        series=[]
        for day in range(periods):
            state=node.state_vars[day]
            series.append(dict(day=day,inventory_level=float(state.get_inventory_level()),
                inbound_order=float(state.get_inbound_order()),
                order_quantity=float(state.get_order_quantity()),
                inbound_shipment=float(state.get_inbound_shipment()),
                outbound_shipment=float(state.get_outbound_shipment()),
                cumulative_fill_rate={str(k):float(v) for k,v in state.fill_rate.items()},
                cost=float(state.total_cost_incurred)))
        nodes.append(dict(node=node.index,periods=series))
    return dict(stockpyl_version=version('stockpyl'),network_type='TWO_NODE_SERIAL_SYNTHETIC',
        seed=seed,period_count=periods,nodes=nodes,total_research_cost=float(cost),
        external_supply_is_unconstrained=True,enterprise_network_certification=False,
        scope='INDEPENDENT_POISSON_DEMAND_FIXED_LEAD_TIME_SERIAL_RESEARCH',
        event_order='STOCKPYL_DEMAND_ORDER_RECEIPT_SHIPMENT',
        limits=['This network is not the enterprise single-factory distribution model.',
            'Research cost coefficients and unlimited external supply are explicit assumptions.',
            'This run establishes library execution, not multi-factory or constrained network optimality.'])


def service_quantile_check(request):
    """Run Stockpyl's quantile solver using weights as CSL probability only."""
    from stockpyl.newsvendor import newsvendor_normal
    from .core import recommend
    out=recommend(request);c=out['candidates'][2];m=c['metrics']
    alpha=m['target_cycle_service_level'];mu=m['mean_daily_demand'];sd=m['sample_daily_sd']
    lead=m['protection_lead_days'];cover=m['review_days'];lead_sd=m['lead_time_sd_days']
    sigma=math.sqrt((lead+cover)*sd**2+mu**2*lead_sd**2)
    if mu<=0 or sigma<=0:raise InventoryError('NORMAL_CHECK_POSITIVE_DEMAND_VARIANCE_REQUIRED')
    s,_=newsvendor_normal(1-alpha,alpha,mu*(lead+cover),sigma)
    expected=mu*(lead+cover)+NormalDist().inv_cdf(alpha)*sigma
    if not math.isclose(s,expected,rel_tol=1e-12):raise InventoryError('SERVICE_QUANTILE_MISMATCH')
    return dict(check='PASS',stockpyl_version=version('stockpyl'),stockpyl_unrounded_order_up_to=float(s),
        native_minimum=c['minimum'],native_maximum=c['maximum'],reorder_point=c['reorder_point'],
        rounded_order_up_to=c['order_up_to'],weights_are_financial_costs=False,
        weights=dict(overage=1-alpha,underage=alpha),native_minimum_excludes_lead_demand=True,
        scope='NORMAL_MOMENT_MATCHED_PROTECTION_QUANTILE_NOT_CONSTRAINED_OPTIMUM')
