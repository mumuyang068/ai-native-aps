"""Versioned algorithm outputs enter the original protected professional overlay.

No original Forecast or enterprise source table is mutated. The API Owner also
checks the submitted version against its immutable model/override registry.
"""
import copy
from decimal import Decimal
from p22.foundation_projection import validate,seal,digest,require
from demand_forecast import _time,_quantity,_text,DIMENSIONS

REVISION='advanced-planning-research-v1'

def enable_scope(data,source):
    validate(data)
    require(isinstance(source,str) and bool(source.strip()),'INTELLIGENCE_SCOPE_SOURCE')
    require('professional_overlay' not in data['foundation_projection'],'INTELLIGENCE_BASELINE_ONLY')
    data['foundation_projection']['intelligence_scope']=dict(revision=REVISION,source=source,
        original_source_sha256=digest(data['foundation_projection']['source_tables']),
        simulation_only=True,execution_authorized=False)
    seal(data)

def require_scope(data):
    validate(data)
    scope=data['foundation_projection'].get('intelligence_scope',{})
    require(scope.get('revision')==REVISION and scope.get('simulation_only') is True
        and scope.get('execution_authorized') is False
        and scope.get('original_source_sha256')==digest(data['foundation_projection']['source_tables']),'INTELLIGENCE_SCOPE')

def native_material_coordination(data):
    """Opt-in, bounded native reconciliation for this declared research Owner."""
    meta=data.get('foundation_projection',{})
    if not meta.get('intelligence_scope') or not meta.get('professional_overlay'):
        return None
    require_scope(data)
    parameters=meta['professional_overlay']['changes']['parameters']
    policy=parameters.get('replenishment')
    if policy is None:return None
    return dict(enabled=True,revision='bounded-proposed-po-topup-v1',
        scope='ADVANCED_PLANNING_RESEARCH',simulation_only=True,execution_authorized=False,
        time_zone=data['site']['timezone'],max_adjustments=len(policy['rows']),
        allowed_dimensions=[{k:r[k] for k in ('item_id','location_id','uom')} for r in policy['rows']])

def validate_version(data,version):
    require(isinstance(version,dict),'FORECAST_VERSION_SHAPE')
    require(version.get('sha256')==digest({k:v for k,v in version.items() if k!='sha256'}),'FORECAST_VERSION_CHANGED')
    require(version.get('data_version_id')==data['foundation_projection']['source_version_id'],'FORECAST_VERSION_IDENTITY')
    require(version.get('status')=='CONFIRMED_RESEARCH','FORECAST_VERSION_NOT_CONFIRMED')
    require(version.get('classification') in {'SYNTHETIC_RESEARCH','PUBLIC_BENCHMARK','SOURCE_OBSERVED'},'FORECAST_SOURCE_CLASSIFICATION')
    require(isinstance(version.get('dataset_sha256'),str) and len(version['dataset_sha256'])==64,'FORECAST_DATASET_SOURCE')
    cutoff=_time(version.get('training_cutoff'),'training_cutoff');issue=_time(version.get('issue_time'),'issue_time')
    require(cutoff<=issue<=_time(data['planning_at'],'planning_at'),'FORECAST_TIME_LEAKAGE')
    rows=version.get('rows');require(isinstance(rows,list) and 1<=len(rows)<=1000,'FORECAST_VERSION_ROWS')
    original=data['foundation_projection']['source_tables']['demand_lines']
    dimensions={tuple(d[k] for k in DIMENSIONS) for d in original}
    start,end=(_time(data['horizon'][k],k) for k in ('start_at','end_at'))
    for row in rows:
        require(isinstance(row,dict) and tuple(row.get(k) for k in DIMENSIONS) in dimensions,'FORECAST_DIMENSION')
        require(row.get('version_id')==version['id'],'FORECAST_ROW_VERSION')
        a,b=(_time(row.get(k),k) for k in ('bucket_start','bucket_end'))
        require(start<=a<b<=end,'FORECAST_BUCKET_NOT_COVERED')
        require(a>=cutoff,'FORECAST_TIME_LEAKAGE')
        if 'training_cutoff' in row:require(_time(row['training_cutoff'],'training_cutoff')==cutoff,'FORECAST_ROW_CUTOFF')
        if 'issue_time' in row:require(_time(row['issue_time'],'issue_time')==issue,'FORECAST_ROW_ISSUE')
    return version

def project_forecast(data,policy):
    require_scope(data)
    require(isinstance(policy,dict) and set(policy)=={'revision','forecast_version','consumption_policy','net_terms'}
        and policy['revision']=='advanced-netting-v1','ADVANCED_NETTING_SHAPE')
    version=validate_version(data,policy['forecast_version'])
    terms=policy['net_terms']
    require(isinstance(terms,dict) and set(terms)=={'priority','minimum_shipment','maximum_lateness_seconds','due_at'},'FORECAST_NET_TERMS')
    require(type(terms['priority']) is int and max(d['priority'] for d in data['demand_lines'])<terms['priority']<=2147483647,'FORECAST_EXISTING_PRIORITY_PROTECTION')
    require(type(terms['maximum_lateness_seconds']) is int and 0<=terms['maximum_lateness_seconds']<=2147483647,'FORECAST_NET_TERMS')
    minimum=_quantity(terms['minimum_shipment'],'minimum_shipment',positive=True)
    due=_time(terms['due_at'],'due_at')
    require(_time(data['planning_at'],'planning_at')<=due<_time(data['horizon']['end_at'],'end_at'),'FORECAST_NET_DUE')
    source=data['foundation_projection']['source_tables']
    orders=[{k:copy.deepcopy(d[k]) for k in ('id',*DIMENSIONS,'quantity','due_at','status')}
        |dict(kind='FIRM_ORDER',source_ref=d['source_sha256']+'#'+d['entity_id']) for d in source['demand_lines']]
    from demand_intelligence import net_forecasts
    report=net_forecasts(version['rows'],orders,policy=policy['consumption_policy'])
    generated=[];rows=[]
    for r in report['rows']:
        row=copy.deepcopy(r);row['forecast_id']=r['id'];row['consumptions']=copy.deepcopy(r['allocations'])
        row['status']='NETTED';row['policy_version']=policy['consumption_policy']['version_id'];rows.append(row)
        quantity=Decimal(r['net_quantity'])
        if not quantity:continue
        # One explicit due policy is clamped only to an existing forecast bucket
        # endpoint, never a quantity truncation. Bucket's own start stays intact.
        bucket_end=_time(r['bucket_end'],'bucket_end')
        effective_due=min(due,bucket_end-__import__('datetime').timedelta(seconds=1))
        require(effective_due>=_time(r['bucket_start'],'bucket_start'),'FORECAST_NET_DATE_OUTSIDE_BUCKET')
        identifier='NET06-'+digest(dict(version=version['id'],row=r['id']))[:24]
        generated.append(dict(id=identifier,order_ref='FORECAST:'+r['id'],line_ref=version['id'],
            **{k:r[k] for k in DIMENSIONS},quantity=_text(quantity),priority=terms['priority'],
            minimum_shipment=_text(min(minimum,quantity)),maximum_lateness_seconds=terms['maximum_lateness_seconds'],
            due_at=effective_due.isoformat(),status='OPEN',kind='NET_FORECAST',forecast_id=r['id'],
            provenance=dict(forecast_version=version['id'],version_sha256=version['sha256'],
                dataset_sha256=version['dataset_sha256'],source_refs=copy.deepcopy(r['source_refs']),
                classification=version['classification'],policy_version=row['policy_version'])))
    totals=[dict(uom=u,**values) for u,values in report['totals_by_unit'].items()]
    return dict(policy=copy.deepcopy(policy),policy_sha256=digest(policy),rows=rows,
        unmatched_firm_orders=[r for r in report['participating_orders'] if Decimal(r['unconsumed_quantity'])>0],
        totals_by_uom=totals,generated_demands=generated,allocation_receipt=report['receipt'],
        worker_eligible=True,engine_executed=False,approval_status='RESEARCH_POLICY',
        original_gross_unchanged=True,forecast_version_id=version['id'])
