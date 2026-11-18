"""Read frozen FOUNDATION forecast facts and propose non-executable Gross changes.

DraftStore owns persistence and lifecycle. No policy, source row or solver input
is manufactured here; a Gross hypothesis remains uncomputed until a separately
reviewed policy exists.
"""
import copy
import re
from decimal import Decimal

from p22.foundation_projection import REVISION, digest, validate

ACTION='set_foundation_forecast_gross'
QUANTITY_PATTERN=r'^(0|[1-9][0-9]{0,11})(\.[0-9]{1,8})?$'
BUCKET_FIELDS=('forecast_id','forecast_version_id','item_id','location_id','customer_id','uom',
    'bucket_start','bucket_end','dataset_id','entity_id','source_id','source_sha256','source_section')
IDENTITY_FIELDS=('plan_id','data_version_id','baseline_run_id','run_id','target','snapshot_sha256')
STRING={'type':'string','minLength':1}
BUCKET_SCHEMA={'type':'object','properties':{k:STRING for k in BUCKET_FIELDS},'required':list(BUCKET_FIELDS),'additionalProperties':False}
IDENTITY_SCHEMA={'type':'object','properties':{k:STRING for k in (*IDENTITY_FIELDS,'profile')},'required':list(IDENTITY_FIELDS),'additionalProperties':False}
CHANGE_SCHEMA={'type':'object','properties':{'identity':IDENTITY_SCHEMA,'bucket':BUCKET_SCHEMA,
    'gross_quantity':{'type':'string','pattern':QUANTITY_PATTERN},'reason':{'type':'string','minLength':1,'maxLength':1000},
    'confirmation':{'type':'object','properties':{'sha256':{'type':'string','pattern':'^[a-f0-9]{64}$'}},'required':['sha256'],'additionalProperties':False}},
    'required':['identity','bucket','gross_quantity','reason','confirmation'],'additionalProperties':False}


def fail(code):
    from drafts import DraftError
    raise DraftError(code)


def text(value):return format(Decimal(str(value)).normalize(),'f')


def available(data):return data.get('foundation_projection',{}).get('schema_revision')==REVISION


def bucket_identity(row,version_id):
    return {k:(row['id'] if k=='forecast_id' else version_id if k=='forecast_version_id' else copy.deepcopy(row[k])) for k in BUCKET_FIELDS}


def validate_identity(data,identity,*,creating=False):
    from jsonschema import Draft202012Validator
    if list(Draft202012Validator(IDENTITY_SCHEMA).iter_errors(identity)):fail('FOUNDATION_FORECAST_IDENTITY')
    if not available(data):fail('UNSUPPORTED_PROFILE_CAPABILITY: foundation_forecast')
    validate(data)
    if identity['data_version_id']!=data['foundation_projection']['source_version_id']:fail('DATA_VERSION_MISMATCH')
    if identity['run_id']!=identity['baseline_run_id']:fail('FORECAST_RUN_MISMATCH')
    if creating and identity['target']!='baseline':fail('FORECAST_TARGET_MISMATCH')


def _rows(data,identity):
    meta=data['foundation_projection']
    # This path supports unresolved facts only; reviewed executable netting stays
    # in forecast_override and its existing certified profiles.
    if meta['policy'].get('forecast_policy')!='PRESERVE_PENDING_NOT_NETTED_OR_ADDED':fail('FOUNDATION_FORECAST_POLICY_CHANGED')
    rows=[]
    for row in meta['source_tables']['forecasts']:
        if row.get('netting_policy')!='NOT_SPECIFIED' or row.get('net_quantity') is not None:fail('FOUNDATION_FORECAST_POLICY_CHANGED')
        if not isinstance(row.get('gross_quantity'),(str,int,float)) or not re.fullmatch(QUANTITY_PATTERN,str(row['gross_quantity'])):fail('FOUNDATION_FORECAST_GROSS')
        items=[i for i in data['items'] if i['id']==row['item_id']]
        if len(items)!=1 or row['uom']!=items[0]['base_uom']:fail('FOUNDATION_FORECAST_UNIT_MISMATCH')
        rows.append(dict(id=row['id'],item_id=row['item_id'],location_id=row['location_id'],customer_id=row['customer_id'],
            uom=row['uom'],bucket_start=row['bucket_start'],bucket_end=row['bucket_end'],
            bucket=bucket_identity(row,identity['data_version_id']),source=copy.deepcopy(row),
            source_gross_quantity=text(row['gross_quantity']),gross_quantity=text(row['gross_quantity']),
            net_quantity=None,consumed_quantity=None,policy_status='NET_UNKNOWN',
            outside_horizon_portion_preserved=row.get('outside_horizon_portion_preserved',False)))
    if len({r['id'] for r in rows})!=len(rows):fail('FOUNDATION_FORECAST_DUPLICATE_BUCKET')
    return rows


def preview(data,identity,bucket,gross_quantity,reason,baseline_sha256):
    validate_identity(data,identity,creating=True)
    if not isinstance(gross_quantity,str) or not re.fullmatch(QUANTITY_PATTERN,gross_quantity):fail('FOUNDATION_FORECAST_GROSS')
    if not isinstance(reason,str) or not reason.strip() or len(reason)>1000:fail('FOUNDATION_FORECAST_REASON')
    if not isinstance(baseline_sha256,str) or not re.fullmatch('[a-f0-9]{64}',baseline_sha256):fail('FOUNDATION_FORECAST_BASELINE')
    matches=[r for r in _rows(data,identity) if r['bucket']==bucket]
    if len(matches)!=1:fail('FOUNDATION_FORECAST_BUCKET_MISMATCH')
    row=matches[0];gross=text(gross_quantity)
    if gross==row['source_gross_quantity']:fail('NO_FORECAST_CHANGE')
    value=dict(action=ACTION,identity=copy.deepcopy(identity),bucket=copy.deepcopy(bucket),reason=reason.strip(),gross_quantity=gross,
        before={'gross_quantity':row['source_gross_quantity'],'net_quantity':None,'consumed_quantity':None},
        after={'gross_quantity':gross,'net_quantity':None,'consumed_quantity':None},
        baseline_sha256=baseline_sha256,source_projection_sha256=data['foundation_projection']['projection_sha256'],
        policy_status='NET_UNKNOWN',worker_eligible=False,worker_blocking_code='FORECAST_POLICY_PENDING',
        source_run_id=identity['baseline_run_id'],candidate_run_id=None,run_state='NOT_RUN',
        calculation_submitted=False,engine_executed=False,simulation_only=True,execution_authorized=False)
    value['confirmation']={'sha256':digest(value)}
    return value


def apply(data,identity,bucket,gross_quantity,reason,confirmation,*,baseline_sha256):
    value=preview(data,identity,bucket,gross_quantity,reason,baseline_sha256)
    if confirmation!=value['confirmation']:fail('FOUNDATION_FORECAST_PREVIEW_STALE')
    return value


def is_candidate(draft):
    return (draft.get('changes') or {}).get('action')==ACTION or draft.get('worker_blocking_code')=='FORECAST_POLICY_PENDING'


def verify_candidate(baseline,draft):
    if not is_candidate(draft):return False
    try:
        change=draft['changes']
        recreated=apply(copy.deepcopy(baseline),**{k:change[k] for k in ('identity','bucket','gross_quantity','reason','confirmation')},baseline_sha256=draft['baseline_sha256'])
        expected=copy.deepcopy(baseline);expected.update(scenario_id=draft['id'],scenario_revision=1)
        if recreated!=change or draft['input']!=expected or draft.get('run_id') is not None or draft.get('worker_eligible') is not False or draft.get('worker_blocking_code')!='FORECAST_POLICY_PENDING':raise ValueError()
    except (KeyError,TypeError,ValueError) as exc:
        from drafts import DraftError
        raise DraftError('FOUNDATION_FORECAST_SCOPE_MISMATCH') from exc
    return True


def project(data,identity,*,drafts=()):
    validate_identity(data,identity)
    rows=_rows(data,identity);history=[];selected=None
    for draft in drafts:
        if not is_candidate(draft):continue
        verify_candidate(data,draft);change=draft['changes']
        if any(change['identity'][k]!=identity[k] for k in ('plan_id','data_version_id','baseline_run_id','run_id','snapshot_sha256')):fail('FOUNDATION_FORECAST_HISTORY_SCOPE_MISMATCH')
        history.append(dict(draft_id=draft['id'],state=draft['state'],bucket=change['bucket'],before=change['before'],after=change['after'],reason=change['reason'],source_run_id=change['source_run_id'],candidate_run_id=None,run_state='NOT_RUN',worker_eligible=False))
        if identity['target']==draft['id']:selected=draft
    if identity['target']!='baseline':
        if selected is None:fail('DRAFT_NOT_FOUND')
        if selected['state']=='DISCARDED':fail('DRAFT_DISCARDED')
        change=selected['changes']
        next(r for r in rows if r['bucket']==change['bucket']).update(gross_quantity=change['gross_quantity'],adjustment_reason=change['reason'])
    units={r['uom'] for r in rows}|{r['uom'] for r in data['demand_lines']}
    if len(units)!=1:fail('FOUNDATION_FORECAST_MIXED_UNITS')
    return dict(identity=copy.deepcopy(identity),rows=rows,firm_rows=copy.deepcopy(data['demand_lines']),history=history,
        totals=dict(source_gross_quantity=text(sum(Decimal(r['source_gross_quantity']) for r in rows)),current_gross_quantity=text(sum(Decimal(r['gross_quantity']) for r in rows)),firm_quantity=text(sum(Decimal(r['quantity']) for r in data['demand_lines'])),net_quantity=None,uom=next(iter(units))),
        policy_status='NET_UNKNOWN',worker_eligible=False,worker_blocking_code='FORECAST_POLICY_PENDING',
        source_run_id=identity['baseline_run_id'],candidate_run_id=None,run_state='NOT_RUN' if selected else 'BASELINE_SOURCE',
        simulation_only=True,execution_authorized=False)
