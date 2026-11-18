"""One source-labelled Firm hypothesis; original facts are protected by full replay."""
import copy
from datetime import datetime
from decimal import Decimal
from jsonschema import Draft202012Validator
from demand_forecast import DIMENSIONS, apply_net_forecasts, net_forecasts, validate, _time
from planning_capabilities import profile_id, supports

ACTION = 'add_firm_demand'
INTENT = '新增需求影响试算'
PROFILES = {'enterprise-accessory-supply-v1'}
TEXT = {'type': 'string', 'minLength': 1, 'maxLength': 200}
QUANTITY = {'type':'string','pattern':r'^(0|[1-9][0-9]{0,11})(\.[0-9]{1,8})?$'}
HYPOTHESIS_SCHEMA = {'type':'object','additionalProperties':False,'properties':{
    **{k:TEXT for k in ('id','order_ref','line_ref',*DIMENSIONS,'due_at','research_identity')},
    'quantity':QUANTITY,'minimum_shipment':QUANTITY,
    'priority':{'type':'integer','minimum':0,'maximum':2147483647},
    'maximum_lateness_seconds':{'type':'integer','minimum':0,'maximum':2147483647},
    'evidence_status':{'const':'SYNTHETIC_RESEARCH_ASSUMPTION'}}}
HYPOTHESIS_SCHEMA['required'] = list(HYPOTHESIS_SCHEMA['properties'])
CHANGE_SCHEMA = {'type':'object','additionalProperties':False,'properties':{
    'hypothesis':HYPOTHESIS_SCHEMA,'reason':{'type':'string','minLength':1,'maxLength':1000},
    'simulation_permission':{'const':True}},'required':['hypothesis','reason','simulation_permission']}


def apply(data, hypothesis, reason, simulation_permission):
    from drafts import DraftError
    def require(ok, code):
        if not ok: raise DraftError(code)
    errors=list(Draft202012Validator(CHANGE_SCHEMA).iter_errors(dict(hypothesis=hypothesis,reason=reason,simulation_permission=simulation_permission)))
    require(not errors,'FIRM_INVALID_HYPOTHESIS')
    require(type(simulation_permission) is bool and simulation_permission,'FIRM_SIMULATION_PERMISSION_REQUIRED')
    require(all(isinstance(v,str) and v.strip() for k,v in hypothesis.items() if HYPOTHESIS_SCHEMA['properties'][k].get('type')=='string') and reason.strip(),'FIRM_IDENTITY_REQUIRED')
    require(profile_id(data) in PROFILES and supports(profile_id(data),'actions','firm_demand'),'UNSUPPORTED_PROFILE_CAPABILITY: firm_demand')
    validate(data)
    h=copy.deepcopy(hypothesis)
    require(Decimal(h['quantity'])>0 and Decimal(h['minimum_shipment'])>0 and Decimal(h['minimum_shipment'])<=Decimal(h['quantity']),'FIRM_INVALID_QUANTITY')
    require(type(h['priority']) is int and type(h['maximum_lateness_seconds']) is int,'FIRM_INVALID_POLICY')
    due=_time(h['due_at'],'hypothesis.due_at')
    require(_time(data['planning_at'],'planning_at')<=due<_time(data['horizon']['end_at'],'horizon.end_at'),'FIRM_DUE_OUTSIDE_HORIZON')
    require(not any(d['id']==h['id'] or (d['order_ref'],d['line_ref'])==(h['order_ref'],h['line_ref']) for d in data['demand_lines']),'FIRM_DUPLICATE_ORDER_LINE')
    buckets=[f for f in data['forecasts'] if all(f[k]==h[k] for k in DIMENSIONS) and _time(f['bucket_start'],'bucket_start')<=due<_time(f['bucket_end'],'bucket_end')]
    require(len(buckets)==1,'FIRM_GROSS_COVERAGE_MISMATCH')
    before=net_forecasts(data)
    provenance=dict(source_system='SYNTHETIC_RESEARCH_ASSUMPTION',source_record_id=h['research_identity']+':'+h['id'],
        source_version=h['research_identity'],observed_at=data['planning_at'],import_batch_id=data['snapshot_id'],source_locator='firm-hypothesis:'+h['research_identity'])
    require(not any(d.get('provenance',{}).get('source_system')==provenance['source_system'] and d.get('provenance',{}).get('source_version')==provenance['source_version'] and d.get('provenance',{}).get('source_record_id')==provenance['source_record_id'] for d in data['demand_lines']),'FIRM_DUPLICATE_SOURCE')
    row={k:copy.deepcopy(h[k]) for k in ('id','order_ref','line_ref',*DIMENSIONS,'quantity','due_at','priority','minimum_shipment','maximum_lateness_seconds')}
    row.update(kind='FIRM_ORDER',status='OPEN',provenance=provenance)
    source=copy.deepcopy(data)
    source['demand_lines']=[d for d in source['demand_lines'] if d.get('kind')!='NET_FORECAST']+[row]
    data['demand_lines']=apply_net_forecasts(source)['demand_lines']
    validate(data)
    return dict(action=ACTION,intent=INTENT,hypothesis=h,reason=reason.strip(),simulation_permission=True,
        added_demand=copy.deepcopy(row),before=before,after=net_forecasts(data),simulation_only=True,
        business_permission='NOT_CONFIRMED',execution_authorized=False,engine_executed=False)


def verify(baseline,draft):
    from drafts import DraftError
    changes=draft.get('changes',{})
    original={d['id'] for d in baseline.get('demand_lines',[])}
    added=any(d.get('kind')=='FIRM_ORDER' and d.get('id') not in original for d in draft.get('input',{}).get('demand_lines',[]))
    if changes.get('action')!=ACTION and changes.get('intent')!=INTENT and not (profile_id(baseline) in PROFILES and added):return
    try:
        if changes.get('action')!=ACTION:raise ValueError('missing action')
        expected=copy.deepcopy(baseline)
        recreated=apply(expected,changes['hypothesis'],changes['reason'],changes['simulation_permission'])
        expected.update(scenario_id=draft['id'],scenario_revision=1)
        if recreated!=changes or expected!=draft['input']:raise ValueError('undeclared mutation')
    except (KeyError,TypeError,ValueError) as error:
        raise DraftError('FIRM_DEMAND_SCOPE_MISMATCH: only the declared Firm hypothesis and derived Net may change') from error
