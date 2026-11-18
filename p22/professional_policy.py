"""Typed research policies over an immutable enterprise projection.

This owns only the declared projection delta. Existing facts, material/people/
setup constraints, Worker, stores and business audits remain authoritative.
"""
import copy
import re
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from demand_forecast import DIMENSIONS, POLICY, COVERAGE, projection_netting, _time, _quantity, _text
from p22.foundation_projection import digest, seal, projection_hash, require, validate

ACTION='simulate_professional_policy'
INTENT='专业政策与接单研究试算'
REVISION='professional-policy-overlay-v1'
TEXT={'type':'string','minLength':1,'maxLength':1000}
# Full field validation stays in this pure domain Owner; the public envelope
# rejects arbitrary extensions rather than letting JSON fields become actions.
CHANGE_SCHEMA={'type':'object','additionalProperties':False,'properties':{
    'netting':{'type':['object','null']},'inquiry':{'type':['object','null']},
    'replenishment':{'type':['object','null']},'supply':{'type':['object','null']},'reason':TEXT},
    'required':['netting','inquiry','replenishment','reason']}
CHANGE_SCHEMA['properties'].update(identity={'type':'object'},fingerprint={'type':'string','pattern':'^[a-f0-9]{64}$'},acknowledge_limits={'const':True})
CHANGE_SCHEMA['required']+=['identity','fingerprint','acknowledge_limits']


def _shape(value,keys,code):
    require(isinstance(value,dict) and set(value)==set(keys),code)


def _label(value,code):
    require(isinstance(value,str) and 0<len(value.strip())<=1000,code)


def _research(value,code):
    _label(value.get('policy_version'),code+'_VERSION')
    _label(value.get('source'),code+'_SOURCE')
    require(value.get('approval_status')=='RESEARCH_POLICY',code+'_APPROVAL')


def _source_provenance(row,data):
    return dict(source_system=row['source_id'],source_record_id=row['entity_id'],
        source_version=row['dataset_id'],observed_at=data['planning_at'],
        import_batch_id=data['foundation_projection']['source_version_id'],
        source_locator=row['source_sha256']+'#'+row['source_section'])


def netting_preview(data, policy):
    validate(data)
    if isinstance(policy,dict) and policy.get('revision')=='advanced-netting-v1':
        from p22.intelligence_bridge import project_forecast
        return project_forecast(data,policy)
    _shape(policy,('policy_version','approval_status','source','timezone','dimensions','eligible_statuses',
        'bucket_grain','early_buckets','late_buckets','excess_orders','unmatched','duplicates','negative',
        'unknown_units','coverage','forecast_ids','net_terms'),'POLICY_SHAPE')
    _research(policy,'POLICY')
    require(policy['timezone']==data['site']['timezone'] and policy['dimensions']==list(DIMENSIONS),'POLICY_DIMENSIONS')
    require(policy['eligible_statuses']==['OPEN'] and policy['bucket_grain'] in {'DAY','WEEK','MONTH','EXPLICIT'},'POLICY_STATUS_OR_BUCKET')
    require(type(policy['early_buckets']) is int and type(policy['late_buckets']) is int
        and policy['early_buckets']==policy['late_buckets']==0,'POLICY_CROSS_BUCKET_UNSUPPORTED')
    require(policy['excess_orders']==policy['unmatched']=='KEEP_FIRM'
        and policy['duplicates']==policy['negative']==policy['unknown_units']=='REJECT'
        and policy['coverage']==COVERAGE,'POLICY_HANDLING_UNSUPPORTED')
    ids=policy['forecast_ids'];source=data['foundation_projection']['source_tables']
    require(isinstance(ids,list) and bool(ids) and all(isinstance(x,str) for x in ids)
        and len(ids)==len(set(ids)) and set(ids)<={f['id'] for f in source['forecasts']},'POLICY_FORECAST_SCOPE')
    terms=policy['net_terms']
    _shape(terms,('priority','minimum_shipment','maximum_lateness_seconds','due_by_forecast'),'POLICY_NET_TERMS')
    require(type(terms['priority']) is int and 0<=terms['priority']<=2147483647
        and type(terms['maximum_lateness_seconds']) is int and 0<=terms['maximum_lateness_seconds']<=2147483647,'POLICY_NET_TERMS')
    _quantity(terms['minimum_shipment'],'policy.minimum_shipment',positive=True)
    require(isinstance(terms['due_by_forecast'],dict) and set(terms['due_by_forecast'])==set(ids),'POLICY_NET_DATES')
    start,end=(_time(data['horizon'][k],k) for k in ('start_at','end_at'))
    rows,eligible=[],[]
    for f in source['forecasts']:
        identity={k:copy.deepcopy(f[k]) for k in ('id',*DIMENSIONS,'bucket_start','bucket_end')}
        row=dict(identity,forecast_id=f['id'],gross_quantity=_text(_quantity(str(f['gross_quantity']),'gross')),
            firm_quantity=None,consumed_quantity=None,net_quantity=None,consumptions=[],
            provenance=copy.deepcopy(f),approval_status='RESEARCH_POLICY',policy_version=policy['policy_version'])
        if f['id'] not in ids:
            row['status']='NOT_SELECTED';rows.append(row);continue
        a,b=(_time(f[k],k) for k in ('bucket_start','bucket_end'))
        require(a<b,'POLICY_BUCKET')
        if not start<=a<b<=end:
            row['status']='BUCKET_NOT_FULLY_COVERED';rows.append(row);continue
        zone=ZoneInfo(policy['timezone']);az,bz=a.astimezone(zone),b.astimezone(zone)
        if policy['bucket_grain']!='EXPLICIT':
            require(az.time()==bz.time()==datetime.min.time(),'POLICY_BUCKET_ALIGNMENT')
            if policy['bucket_grain']=='MONTH':require(az.day==bz.day==1 and (bz.year-az.year)*12+bz.month-az.month==1,'POLICY_BUCKET_ALIGNMENT')
            elif policy['bucket_grain']=='WEEK':require(az.weekday()==0 and (bz-az).days==7,'POLICY_BUCKET_ALIGNMENT')
            else:require((bz-az).days==1,'POLICY_BUCKET_ALIGNMENT')
        due=terms['due_by_forecast'][f['id']]
        require(a<=_time(due,'policy.net_due')<b,'POLICY_NET_DATE_OUTSIDE_BUCKET')
        eligible.append(dict(id=f['id'],version_id=policy['policy_version'],**{k:f[k] for k in DIMENSIONS},
            bucket_start=f['bucket_start'],bucket_end=f['bucket_end'],gross_quantity=row['gross_quantity'],
            policy_id=POLICY,coverage=COVERAGE,evidence_status='SOURCE_CONFIRMED_SYNTHETIC',
            net_demand={k:terms[k] for k in ('priority','minimum_shipment','maximum_lateness_seconds')}|{'due_at':due},
            provenance=_source_provenance(f,data)))
        row['status']='ELIGIBLE';rows.append(row)
    # Validate exact OPEN source identities, not the generated/inquiry rows.
    firm=[]
    for row in source['demand_lines']:
        require(row.get('status')=='OPEN','POLICY_UNSUPPORTED_SOURCE_STATUS')
        firm.append({k:copy.deepcopy(row[k]) for k in ('id',*DIMENSIONS,'quantity','due_at','priority','minimum_shipment','maximum_lateness_seconds')}
            |dict(quantity=str(row['quantity']),minimum_shipment=str(row['minimum_shipment']),
                order_ref=row['order_ref'],line_ref=row['id'],kind='FIRM_ORDER',status='OPEN',provenance=_source_provenance(row,data)))
    report,generated=(projection_netting(dict(data,forecasts=eligible,demand_lines=firm),{i['id']:i for i in data['items']})
        if eligible else (dict(rows=[],unmatched_firm_orders=[dict(demand_id=d['id'],quantity=d['quantity'],reason='NO_COVERED_BUCKET') for d in firm],totals_by_uom=[]),[]))
    by_id={r['forecast_id']:r for r in report['rows']}
    for row in rows:
        if row['forecast_id'] in by_id:row.update(by_id[row['forecast_id']],status='NETTED',approval_status='RESEARCH_POLICY',policy_version=policy['policy_version'])
    return dict(policy=copy.deepcopy(policy),policy_sha256=digest(policy),rows=rows,
        unmatched_firm_orders=report['unmatched_firm_orders'],totals_by_uom=report['totals_by_uom'],
        generated_demands=generated,worker_eligible=all(r['status'] in {'NETTED','NOT_SELECTED'} for r in rows),
        engine_executed=False,approval_status='RESEARCH_POLICY')


def _inquiry(data,q):
    require(isinstance(q,dict),'INQUIRY_SHAPE')
    _shape({k:v for k,v in q.items() if k not in {'request_source','search'}},('id','customer_id','item_id','location_id','uom','quantity','due_at','delivery_rule','coverage','protect_existing'),'INQUIRY_SHAPE')
    if 'request_source' in q:_label(q['request_source'],'INQUIRY_REQUEST_SOURCE')
    if 'search' in q:
        s=q['search']
        _shape(s,('origin_draft_id','previous_draft_id','attempt','budget','requested_due_at','requested_quantity'),'INQUIRY_SEARCH_SHAPE')
        require(type(s['attempt']) is int and 2<=s['attempt']<=3 and type(s['budget']) is int and s['budget']==3,'INQUIRY_SEARCH_BUDGET')
        require(s['requested_quantity']==q['quantity'] and _time(s['requested_due_at'],'requested_due_at')<_time(q['due_at'],'due_at'),'INQUIRY_SEARCH_SCOPE')
    require(isinstance(q['id'],str) and re.fullmatch(r'INQUIRY-[A-Za-z0-9_-]{1,60}',q['id']), 'INQUIRY_ID')
    source=data['foundation_projection']['source_tables']
    require(q['customer_id'] in {c['id'] for c in source['customers']},'INQUIRY_UNKNOWN_CUSTOMER')
    require(any(all(r[k]==q[k] for k in DIMENSIONS) for r in source['demand_lines']),'INQUIRY_DIMENSION_OR_CUSTOMER_RELATION')
    try:quantity=_quantity(q['quantity'],'inquiry.quantity',positive=True)
    except ValueError:require(False,'INQUIRY_QUANTITY')
    require(q['protect_existing'] is True and q['coverage']=='INCREMENTAL_OUTSIDE_GROSS'
        and q['delivery_rule'] in {'ALL_OR_NOTHING','SPLIT_BY_NATIVE_EVENTS'},'INQUIRY_POLICY_UNSUPPORTED')
    due=_time(q['due_at'],'inquiry.due_at')
    require(_time(data['planning_at'],'planning_at')<=due<_time(data['horizon']['end_at'],'end_at'),'INQUIRY_DATE')
    require(q['id'] not in {d['id'] for d in data['demand_lines']},'INQUIRY_DUPLICATE')
    # Lower preference than every original commitment; protection is separately
    # checked against actual delivery events after the solve, never inferred.
    priority=max(r['priority'] for r in source['demand_lines'])+1
    row={k:q[k] for k in ('id',*DIMENSIONS,'quantity','due_at')}
    row.update(priority=priority,minimum_shipment=q['quantity'] if q['delivery_rule']=='ALL_OR_NOTHING' else '1',
        maximum_lateness_seconds=0,kind='INQUIRY',status='OPEN',
        provenance=dict(research_inquiry=q['id'],scope='NON_BINDING_SIMULATION'))
    if 'request_source' in q:row['provenance']['request_source']=q['request_source']
    return dict(request=copy.deepcopy(q),added_demand=row,atp_status='CONDITIONAL_SUPPLY_ONLY',
        atp_quantity=None,reservation_created=False,formal_order_created=False,protect_existing=True)


def _replenishment(data,policy):
    _shape(policy,('policy_version','approval_status','source','rows'),'REPLENISHMENT_SHAPE');_research(policy,'REPLENISHMENT')
    require(isinstance(policy['rows'],list) and 1<=len(policy['rows'])<=30,'REPLENISHMENT_ROWS')
    seen=set()
    for row in policy['rows']:
        _shape(row,('item_id','location_id','uom','minimum','maximum','effective_from','effective_to'),'REPLENISHMENT_ROW')
        key=(row['item_id'],row['location_id']);require(key not in seen,'REPLENISHMENT_DUPLICATE');seen.add(key)
        require(any((r['item_id'],r['location_id'],r['uom'])==(row['item_id'],row['location_id'],row['uom']) for r in data['inventory_positions']),'REPLENISHMENT_UNKNOWN_STOCK_OR_UOM')
        require(any((s['item_id'],s['location_id'])==key for s in data['supply_sources']),'REPLENISHMENT_NO_CONFIRMED_SOURCE_TERMS')
        lo,hi=(_quantity(row[k],k) for k in ('minimum','maximum'))
        require(hi>=lo,'REPLENISHMENT_RANGE')
        require(row['effective_from']==data['planning_at'] and row['effective_to']==data['horizon']['end_at'],'REPLENISHMENT_EFFECTIVITY_UNSUPPORTED')
    return copy.deepcopy(policy)|dict(hard_limit=False,calculation='EXPLICIT_MANUAL_SOFT_TARGET',
        source_terms=copy.deepcopy([s for s in data['supply_sources'] if (s['item_id'],s['location_id']) in seen]))


def apply(data,*,netting,inquiry,replenishment,reason,supply=None):
    validate(data);meta=data['foundation_projection']
    require('professional_overlay' not in meta and 'priority_overlay' not in meta,'PROFESSIONAL_BASELINE_ONLY')
    _label(reason,'PROFESSIONAL_REASON');require(any(x is not None for x in (netting,inquiry,replenishment,supply)),'PROFESSIONAL_EMPTY')
    # Compute every change before mutating, so a rejected combination is atomic.
    n=netting_preview(data,netting) if netting is not None else None
    require(n is None or n['worker_eligible'],'POLICY_BUCKET_NOT_FULLY_COVERED')
    q=_inquiry(data,inquiry) if inquiry is not None else None
    p=_replenishment(data,replenishment) if replenishment is not None else None
    supply_changes={}
    if supply is not None:
        require(bool(data.get('distribution_lanes')) and bool(data.get('purchase_receipts')),'JOINT_SUPPLY_SCOPE_NOT_CERTIFIED')
        _shape(supply,('purchase','distribution'),'JOINT_SUPPLY_SHAPE')
        require(any(v is not None for v in supply.values()),'JOINT_SUPPLY_EMPTY')
        if supply['purchase'] is not None:
            from multi_receipt_scenario import joint_purchase_condition
            supply_changes['purchase']=joint_purchase_condition(data,supply['purchase'])
        if supply['distribution'] is not None:
            from distribution_scenario import joint_distribution_condition
            supply_changes['distribution']=joint_distribution_condition(data,supply['distribution'])
    changes=dict(action=ACTION,intent=INTENT,parameters=dict(netting=copy.deepcopy(netting),inquiry=copy.deepcopy(inquiry),
        replenishment=copy.deepcopy(replenishment),reason=reason.strip()),netting=n,inquiry=q,replenishment=p,
        simulation_only=True,execution_authorized=False,engine_executed=False)
    if supply is not None:
        changes['parameters']['supply']=copy.deepcopy(supply)
        changes['supply']=copy.deepcopy(supply_changes)
    added=(n['generated_demands'] if n else [])+([q['added_demand']] if q else [])
    ids=[r['id'] for r in added]
    require(len(ids)==len(set(ids)) and not set(ids)&{r['id'] for r in data['demand_lines']},'PROFESSIONAL_DUPLICATE_DEMAND')
    receipt=dict(schema_revision=REVISION,baseline_projection_sha256=meta['projection_sha256'],
        source_version_id=meta['source_version_id'],changes=copy.deepcopy(changes),added_demand_ids=ids)
    for kind,change in supply_changes.items():
        table='existing_orders' if kind=='purchase' else 'distribution_lanes'
        next(r for r in data[table] if r['id']==change['reference']).update(change['after'])
    data['demand_lines'].extend(copy.deepcopy(added));meta['professional_overlay']=receipt;seal(data)
    return changes


def validate_overlay(data):
    """Restore the original seal then recreate the declared delta, field by field."""
    meta=data['foundation_projection'];r=meta.get('professional_overlay')
    _shape(r,('schema_revision','baseline_projection_sha256','source_version_id','changes','added_demand_ids'),'PROFESSIONAL_RECEIPT_SHAPE')
    require(r['schema_revision']==REVISION and r['source_version_id']==meta['source_version_id'],'PROFESSIONAL_SOURCE')
    restored=copy.deepcopy(data);restored['foundation_projection'].pop('professional_overlay')
    restored['demand_lines']=[d for d in restored['demand_lines'] if d['id'] not in r['added_demand_ids']]
    restored['foundation_projection']['projection_sha256']=r['baseline_projection_sha256']
    for kind,change in r['changes'].get('supply',{}).items():
        require(kind in {'purchase','distribution'},'JOINT_SUPPLY_RECEIPT')
        table='existing_orders' if kind=='purchase' else 'distribution_lanes'
        next(row for row in restored[table] if row['id']==change['reference']).update(change['before'])
    require(projection_hash(restored)==r['baseline_projection_sha256'],'PROFESSIONAL_BASELINE_MISMATCH')
    validate(restored)
    expected=copy.deepcopy(restored)
    recreated=apply(expected,**r['changes']['parameters'])
    require(recreated==r['changes'] and expected==data,'PROFESSIONAL_REPLAY_MISMATCH')
    return copy.deepcopy(r)


def verify(baseline,draft):
    """Original DraftStore is the authority; a self-signed input isn't enough."""
    from drafts import DraftError
    c=draft.get('changes') or {};actual=draft.get('input') or {}
    marked=c.get('action')==ACTION or 'professional_overlay' in actual.get('foundation_projection',{})
    if not marked:return False
    try:
        require(c.get('action')==ACTION,'PROFESSIONAL_ACTION')
        expected=copy.deepcopy(baseline)
        recreated=confirmed_apply(expected,**c['parameters'],identity=c['confirmation']['identity'],
            fingerprint=c['confirmation']['fingerprint'],acknowledge_limits=True,baseline_sha256=draft['baseline_sha256'])
        expected.update(scenario_id=draft['id'],scenario_revision=1)
        require(recreated==c and expected==actual,'PROFESSIONAL_DRAFT_SCOPE')
        validate_overlay(actual)
    except (KeyError,TypeError,ValueError,ArithmeticError) as error:
        raise DraftError('PROFESSIONAL_DRAFT_SCOPE: 原基线与政策完整重放不一致') from error
    return True


def preview(data,identity,parameters,baseline_sha256):
    _shape(identity,('plan_id','data_version_id','baseline_run_id','run_id','target','snapshot_sha256'),'PROFESSIONAL_IDENTITY')
    require(identity['data_version_id']==data['foundation_projection']['source_version_id']
        and identity['run_id']==identity['baseline_run_id'] and identity['target']=='baseline','PROFESSIONAL_IDENTITY')
    require(re.fullmatch(r'PLAN-[a-f0-9]{16}',identity['plan_id']) is not None
        and re.fullmatch(r'API-[a-f0-9]{16}',identity['baseline_run_id']) is not None
        and re.fullmatch(r'[a-f0-9]{64}',identity['snapshot_sha256']) is not None,'PROFESSIONAL_IDENTITY')
    candidate=copy.deepcopy(data)
    # A blocked source forecast is still a useful review, never a candidate.
    n=netting_preview(data,parameters['netting']) if parameters['netting'] is not None else None
    if n and not n['worker_eligible']:
        # Other fields are still checked atomically; nothing is written.
        if parameters['inquiry'] is not None:_inquiry(data,parameters['inquiry'])
        if parameters['replenishment'] is not None:_replenishment(data,parameters['replenishment'])
        changes=dict(netting=n,inquiry=None,replenishment=None,parameters=copy.deepcopy(parameters),
            engine_executed=False,simulation_only=True,execution_authorized=False)
    else:changes=apply(candidate,**parameters)
    value=dict(identity=copy.deepcopy(identity),changes=changes,baseline_sha256=baseline_sha256,
        worker_eligible=n is None or n['worker_eligible'],simulation_only=True,execution_authorized=False)
    return value|dict(fingerprint=digest(value))


def confirmed_apply(data,*,identity,fingerprint,acknowledge_limits,baseline_sha256,**parameters):
    require(acknowledge_limits is True,'PROFESSIONAL_ACKNOWLEDGEMENT')
    p=preview(data,identity,parameters,baseline_sha256)
    require(p['fingerprint']==fingerprint,'PROFESSIONAL_PREVIEW_STALE')
    require(p['worker_eligible'],'POLICY_BUCKET_NOT_FULLY_COVERED')
    return apply(data,**parameters)|dict(confirmation=dict(identity=copy.deepcopy(identity),fingerprint=fingerprint))


def effective_demands(data):
    if 'professional_overlay' in data.get('foundation_projection',{}):validate_overlay(data)
    return copy.deepcopy(data['demand_lines'])


def next_inquiry_parameters(baseline,draft,drafts,due_at,reason):
    verify(baseline,draft)
    require(draft.get('run_id') and draft.get('state')!='DISCARDED','INQUIRY_SEARCH_PARENT')
    c=draft['changes'];q=(c.get('inquiry') or {}).get('request')
    require(q is not None,'INQUIRY_SEARCH_PARENT')
    previous=q.get('search');attempt=(previous['attempt'] if previous else 1)+1
    require(attempt<=3,'INQUIRY_SEARCH_BUDGET')
    due=_time(due_at,'due_at')
    require(_time(q['due_at'],'previous_due')<due<_time(baseline['horizon']['end_at'],'horizon.end_at'),'INQUIRY_SEARCH_DATE')
    origin=previous['origin_draft_id'] if previous else draft['id']
    params=copy.deepcopy(c['parameters']);params['reason']=reason
    params['inquiry']['due_at']=due_at
    params['inquiry']['search']=dict(origin_draft_id=origin,previous_draft_id=draft['id'],attempt=attempt,budget=3,
        requested_due_at=previous['requested_due_at'] if previous else q['due_at'],requested_quantity=q['quantity'])
    # Validate before a preview is offered, including unused sibling probes.
    synthetic=dict(id='PREVIEW',baseline_sha256=draft['baseline_sha256'],changes={'parameters':params})
    validate_search_lineage(baseline,synthetic,drafts)
    return params


def validate_search_lineage(baseline,draft,drafts):
    """Original DraftStore remains the only search history; no second ledger."""
    params=draft['changes']['parameters'];q=params.get('inquiry')
    if not q or 'search' not in q:return
    s=q['search'];_inquiry(baseline,q)
    lookup={d['id']:d for d in drafts};origin=lookup.get(s['origin_draft_id']);parent=lookup.get(s['previous_draft_id'])
    require(origin is not None and parent is not None and parent.get('run_id'),'INQUIRY_SEARCH_PARENT')
    require(origin['baseline_sha256']==parent['baseline_sha256']==draft['baseline_sha256'],'INQUIRY_SEARCH_SCOPE')
    verify(baseline,origin);verify(baseline,parent)
    original=origin['changes']['parameters'];oq=original.get('inquiry')
    require(oq is not None and 'search' not in oq,'INQUIRY_SEARCH_ORIGIN')
    fixed=lambda value:{k:v for k,v in value.items() if k not in ('due_at','search')}
    require(fixed(q)==fixed(oq) and all(params.get(k)==original.get(k) for k in ('netting','replenishment','supply'))
        and s['requested_due_at']==oq['due_at'] and s['requested_quantity']==oq['quantity'],'INQUIRY_SEARCH_SCOPE')
    pq=parent['changes']['parameters']['inquiry'];ps=pq.get('search')
    require(s['attempt']==(ps['attempt'] if ps else 1)+1 and s['origin_draft_id']==(ps['origin_draft_id'] if ps else parent['id'])
        and _time(pq['due_at'],'parent_due')<_time(q['due_at'],'probe_due'),'INQUIRY_SEARCH_SCOPE')
    family=[d for d in drafts if d['id']!=draft['id'] and
        (d.get('changes',{}).get('parameters',{}).get('inquiry') or {}).get('search',{}).get('origin_draft_id')==origin['id']]
    attempts=[d['changes']['parameters']['inquiry']['search']['attempt'] for d in family]+[s['attempt']]
    require(len(attempts)==len(set(attempts)) and len(attempts)<=2 and all(i in (2,3) for i in attempts),'INQUIRY_SEARCH_BUDGET')
