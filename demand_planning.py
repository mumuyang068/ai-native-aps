"""Read projection of a confirmed forecast conversion and one actual run."""
from decimal import Decimal
from demand_forecast import enabled,net_forecasts

def _gross_context(data,forecast_id,identity,baseline_data,changes):
    from forecast_override import ACTION,bucket_identity
    from planning_adapter import InputError
    scenario=identity.get('target','baseline')!='baseline'
    try:
        if scenario and baseline_data is None:raise ValueError('immutable baseline required')
        current=[f for f in data['forecasts'] if f['id']==forecast_id]
        if len(current)!=1:raise ValueError('ambiguous current forecast')
        bucket=bucket_identity(current[0])
        originals=[f for f in (baseline_data or data)['forecasts'] if bucket_identity(f)==bucket]
        if len(originals)!=1:raise ValueError('confirmed source bucket does not match')
        original=originals[0]['gross_quantity'];quantity=current[0]['gross_quantity']
        overridden=Decimal(original)!=Decimal(quantity)
        declared=isinstance(changes,dict) and changes.get('action')==ACTION and changes.get('bucket')==bucket and Decimal(changes.get('gross_quantity','-1'))==Decimal(quantity)
        if overridden and (not scenario or not declared):raise ValueError('unverified Gross override')
        return dict(source_confirmed_gross_quantity=original,scenario_gross_quantity=quantity if scenario else None,
            simulation_only=scenario,gross_overridden=overridden,
            forecast_assumption_reason=changes.get('reason') if declared else None)
    except (KeyError,TypeError,ValueError,ArithmeticError) as error:
        raise InputError('FORECAST_SOURCE_SCOPE_MISMATCH: 情景 Gross 必须与同计划原确认版本的唯一精确来源桶分开展示') from error


def project(data,result,identity,*,baseline_data=None,changes=None):
    if not enabled(data):return {'identity':identity,'status':'UNSUPPORTED','reason':'当前模型未认证预测订单抵扣。','rows':[],'timezone':data['site']['timezone']}
    report=net_forecasts(data);deliveries={d['demand_id']:d for d in result.get('deliveries',[])};rows=[]
    for f in report['rows']:
        firm_ids=[c['demand_id'] for c in f['consumptions']]
        net_ids=[f['net_demand_id']] if f['net_demand_id'] else []
        ids=firm_ids+net_ids
        def planned(keys):
            return str(sum((Decimal(deliveries[k]['planned_quantity']) for k in keys),Decimal(0))) if all(k in deliveries and deliveries[k].get('planned_quantity') is not None for k in keys) else None
        rows.append({**f,**_gross_context(data,f['forecast_id'],identity,baseline_data,changes),'id':f['forecast_id'],'source_version':f['version_id'],'start_at':f['bucket_start'],'end_at':f['bucket_end'],
            'gross_forecast':f['gross_quantity'],'firm_open_orders':f['firm_quantity'],
            'forecast_consumed':f['consumed_quantity'],'net_forecast':f['net_quantity'],
            'effective_planning_demand':f['effective_quantity'],'planned_quantity':planned(ids),
            'firm_planned_quantity':planned(firm_ids),'forecast_planned_quantity':planned(net_ids),
            'firm_demand_ids':firm_ids,'forecast_demand_ids':net_ids,
            'sources':[f['provenance']]+[c['provenance'] for c in f['consumptions']],
            'deliveries':[{**deliveries[k],'kind':'NET_FORECAST' if k in net_ids else 'FIRM_ORDER'} for k in ids if k in deliveries]})
    return {'identity':identity,'timezone':data['site']['timezone'],'status':'PRESENT','rows':rows,
        'policy_id':report['policy_id'],'unmatched_firm_orders':report['unmatched_firm_orders'],
        'known_business_fail':result.get('assessment',{}).get('constraint_check')=='FAIL',
        'limits':['确定性同维度同半开桶订单抵扣；不是统计预测或原生Forecast引擎。',
                  '净预测是独立计划需求，不是客户已确认订单或实际发货；总量按相同维度核对。',
                  '不支持跨桶挪用、层级分摊、自动预测、库存政策或ATP/CTP承诺。']}
