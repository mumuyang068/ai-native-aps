"""Immutable algorithm versions and explicit manual overlays, never historical edits."""
import copy,uuid
from p22.foundation_projection import digest,require
from demand_forecast import DIMENSIONS,_quantity,_text,_time

def _seal(value):
    value.pop('sha256',None);value['sha256']=digest(value);return value

def statistical_version(data,result,dataset,parameters,*,planning_scope=True):
    identifier='FCV-'+uuid.uuid4().hex[:24]
    source=data['foundation_projection']['source_tables']
    dimensions={tuple(d[k] for k in DIMENSIONS) for d in source['demand_lines']+(source.get('forecasts',[]) if not planning_scope else [])}
    units={r['id']:r.get('base_uom') for r in data.get('items',[])}
    customers={r['id'] for r in source.get('customers',[])}
    locations={r['id'] for r in source.get('locations',[])}|{r['location_id'] for r in data.get('inventory_positions',[])}
    start,end=(_time(data['horizon'][k],k) for k in ('start_at','end_at'))
    rows=[];excluded=[]
    for source in result['rows']:
        r=copy.deepcopy(source)
        if 'uom' not in r:r['uom']=r.get('unit')
        known_source=(r.get('item_id') in units and r.get('uom')==units[r['item_id']] and r.get('customer_id') in customers and r.get('location_id') in locations)
        if tuple(r.get(k) for k in DIMENSIONS) not in dimensions and (planning_scope or not known_source):
            excluded.append(dict(id=r.get('id'),reason='DIMENSION_NOT_IN_NATIVE_SCOPE' if planning_scope else 'UNKNOWN_SOURCE_DIMENSION'));continue
        a,b=(_time(r[k],k) for k in ('bucket_start','bucket_end'))
        if planning_scope and not start<=a<b<=end:
            excluded.append(dict(id=r.get('id'),reason='BUCKET_NOT_FULLY_COVERED'));continue
        r.update(version_id=identifier,gross_quantity=_text(_quantity(str(r.get('gross_quantity',r.get('quantity'))),'forecast_quantity')),
            forecast_origin='STATISTICAL_BASELINE',training_cutoff=parameters['training_cutoff'],issue_time=parameters['issue_time'])
        rows.append(r)
    require(bool(rows),'NO_COMPLETE_NATIVE_FORECAST_BUCKETS')
    return _seal(dict(id=identifier,data_version_id=data['foundation_projection']['source_version_id'],
        classification=dataset['classification'],status='CANDIDATE',dataset_id=dataset['id'],dataset_sha256=dataset['sha256'],
        model=parameters['model'],training_cutoff=parameters['training_cutoff'],issue_time=parameters['issue_time'],
        rows=rows,excluded_rows=excluded,algorithm_receipt=copy.deepcopy(result.get('receipt',{})),
        backtest=copy.deepcopy(dataset.get('backtest',{})),original_gross_unchanged=True,
        planning_scope_applied=planning_scope,simulation_only=True,execution_authorized=False))

def revise_version(version,*,actor,reason,row_id=None,quantity=None,restore=None):
    require(isinstance(actor,str) and bool(actor.strip()) and isinstance(reason,str) and bool(reason.strip()),'FORECAST_OVERRIDE_REASON_REQUIRED')
    result=copy.deepcopy(restore or version);result.pop('sha256',None)
    result.update(id='FCV-'+uuid.uuid4().hex[:24],parent_version_id=version['id'],status='CONFIRMED_RESEARCH',
        author=actor.strip(),reason=reason.strip(),restored_from=restore['id'] if restore else None)
    if row_id is not None:
        found=[r for r in result['rows'] if r['id']==row_id];require(len(found)==1,'FORECAST_ROW_NOT_FOUND')
        row=found[0];value=_text(_quantity(quantity,'manual_override'))
        row.update(statistical_quantity=row.get('statistical_quantity',row['gross_quantity']),baseline_quantity=row.get('baseline_quantity',row.get('statistical_quantity',row['gross_quantity'])),gross_quantity=value,quantity=value,
            forecast_origin='MANUAL_OVERRIDE',lower=None,upper=None,prediction_interval_applicable=False,
            override=dict(actor=actor.strip(),reason=reason.strip(),parent_version_id=version['id']))
    for r in result['rows']:r['version_id']=result['id']
    return _seal(result)
