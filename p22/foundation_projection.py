"""Reviewed single-factory projection of shared facts, never a fact correction.

One combined contract replaces feature-count gates for this path. Preparation
does not certify native behavior; its receipt remains pending until real audit.
"""
import copy
import hashlib
import json
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

REVISION='foundation-planning-projection-v1'
ADAPTER='canonical-enterprise-factory-v1'
PERSON_POOL='FOUNDATION:OPERATORS'
MACHINE_POOL='FOUNDATION:SEW-MACHINES'


def digest(v):return hashlib.sha256(json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def require(ok,code):
    if not ok:
        from planning_adapter import InputError
        raise InputError(code)
def text(v):return format(Decimal(str(v)).normalize(),'f')
def provenance(r):return {'source_id':r.get('source_id'),'source_sha256':r.get('source_sha256'),'source_section':r.get('source_section'),'entity_id':r.get('entity_id')}


def validate_facts(version):
    from p21.canonical import assemble
    c=version.get('canonical',{})
    require(c and version.get('business_data',{}).get('canonical')==c,'CANONICAL_CHANGED')
    require(not c.get('source_conflicts') and not c.get('source_retractions'),'SOURCE_AUTHORITY_UNRESOLVED')
    rebuilt=assemble(c['enterprise_id'],c['tables'],planning_at=c['planning_at'],horizon=c['horizon'],timezone=c['timezone'],source_refs={})
    require(rebuilt['semantic_sha256']==c['semantic_sha256'] and not rebuilt['issues'],'CANONICAL_CHANGED')
    fields=('canonical_id','enterprise_id','object_type','business_id','payload','field_statuses','source_row')
    # Saved JSON sorts table keys, while object arrays retain source table
    # insertion order. Compare stable identities, retaining row order/fields.
    normalized=lambda objects:sorted(({k:o[k] for k in fields} for o in objects),key=lambda o:o['canonical_id'])
    require(normalized(c['objects'])==normalized(rebuilt['objects']),'CANONICAL_OBJECT_MISMATCH')
    require(all(o.get('factual_status')=='CONFIRMED_FACT' for o in c['objects']),'FACT_CONFIRMATION_REQUIRED')
    require(set(c['tables'])<=set(version.get('confirmations',{})),'FACT_CONFIRMATION_REQUIRED')
    require(all(version['confirmations'][k].get('reviewer') and version['confirmations'][k].get('content_sha256') for k in c['tables']),'FACT_CONFIRMATION_REQUIRED')
    require(len({version['confirmations'][k]['content_sha256'] for k in c['tables']})==1,'FACT_CONFIRMATION_MISMATCH')
    return c


def validate_quantity_contracts(t,site):
    """This adapter has no unit conversions or cross-recipe consumption model."""
    items={r['id']:r for r in t['items']};recipes={r['id']:r for r in t['recipes']}
    ops={r['id']:r for r in t['operations']}
    def unit(row,field,item):
        require(row[field]==items[item]['base_uom'],'UNIT_CONVERSION_REQUIRED: '+row['id']+':'+field)
    for table in ['demand_lines','inventory_positions','purchase_orders','supply_sources','inventory_targets']:
        for r in t[table]:
            if table=='inventory_positions' and r['location_id']!=site:continue
            unit(r,'uom',r['item_id'])
    for r in t['bom_lines']:
        unit(r,'input_uom',r['input_item_id']);unit(r,'output_uom',r['output_item_id'])
        require(r['output_item_id']==recipes[r['recipe_id']]['output_item_id'],'RECIPE_OUTPUT_MISMATCH')
        require(ops[r['consume_operation_id']]['recipe_id']==r['recipe_id'],'RECIPE_OPERATION_MISMATCH')
    for o in t['operations']:
        require(o['routing_id']==recipes[o['recipe_id']]['routing_id'],'RECIPE_OPERATION_MISMATCH')
        if o['predecessor_id']:
            require(ops[o['predecessor_id']]['recipe_id']==o['recipe_id'],'RECIPE_OPERATION_MISMATCH')
        for w in t['capacity_windows']:
            if w['resource_id'] in o['resource_ids']:
                unit(w,'uom',recipes[o['recipe_id']]['output_item_id'])
    for m in t['existing_manufacturing_orders']:
        o=ops[m['operation_id']]
        require(o['recipe_id']==m['recipe_id'] and m['resource_id'] in o['resource_ids'],'RECIPE_OPERATION_MISMATCH')
        if m.get('plan_operations'):
            route=sorted([o for o in t['operations'] if o['recipe_id']==m['recipe_id']],key=lambda o:o['sequence'])
            require([r['operation_id'] for r in m['plan_operations']]==[o['id'] for o in route],'EXTERNAL_MO_COMPLETE_ROUTING_REQUIRED')
            for r,o in zip(m['plan_operations'],route):
                require(r['resource_id'] in o['resource_ids'] and r.get('operator_id') in {p['id'] for p in t['operators']},'EXTERNAL_MO_NAMED_ASSIGNMENT_REQUIRED')
    sources={r['id']:r for r in t['supply_sources']}
    for p in t['purchase_orders']:
        s=sources[p['source_id']]
        require((p['item_id'],p['location_id'])==(s['item_id'],s['location_id']),'PURCHASE_SOURCE_SCOPE_MISMATCH')


def build_projection(version,policy,derivations):
    if policy.get('profile')=='odoo-verified-manufacturing-v1':
        from p22.odoo_projection import build_projection as build_odoo
        return build_odoo(version,policy)
    if policy.get('profile')=='continuity-current-state-v1':
        from p22.continuity_projection import build_projection as build_continuity
        return build_continuity(version,policy)
    c=validate_facts(version);t=c['tables']
    require(policy.get('approval',{}).get('status')=='USER_APPROVED_RESEARCH_ONLY' and policy.get('scope')=='RESEARCH_SIMULATION_ONLY' and policy.get('execution_authorized') is False and policy.get('change_frozen_facts') is False,'POLICY_APPROVAL_REQUIRED')
    require(policy.get('enterprise_id')==c['enterprise_id'] and {x['dataset_id'] for rows in t.values() for x in rows}=={policy.get('dataset_id')},'POLICY_IDENTITY_MISMATCH')
    require(policy.get('qualification_priority_policy')=='EQUAL_ELIGIBLE_CANDIDATES' and policy.get('demand_scope')=='ALL_24_FIRM_DEMANDS' and policy.get('forecast_policy')=='PRESERVE_PENDING_NOT_NETTED_OR_ADDED' and policy.get('procurement_strategy')=='PRIORITY' and policy.get('purchase_aggregation_seconds')==0 and policy.get('protected_mo_person_policy')=='PROPOSE_ONLY_WITHIN_ORIGINAL_MACHINE_AND_WINDOW','UNSUPPORTED_RESEARCH_POLICY')
    require(derivations.get('enterprise_id')==c['enterprise_id'] and derivations.get('dataset_id')==policy['dataset_id'] and derivations.get('review_status')=='SOURCE_CHECKED','DERIVATION_SOURCE_REQUIRED')
    require(derivations.get('source_sha256') in {r.get('source_sha256') for r in t['operations']},'DERIVATION_SOURCE_MISMATCH')
    return _build_projection_common(version,policy,derivations,c)


def _build_projection_common(version,policy,derivations,c):
    """Shared mapping after each profile's own complete applicability checks."""
    t=c['tables']
    sites={r['location_id'] for r in t['demand_lines']}
    require(len(sites)==1 and len(t['demand_lines'])>0,'UNSUPPORTED_MULTI_FACTORY_PROJECTION')
    site=next(iter(sites));loc=next(x for x in t['locations'] if x['id']==site)
    validate_quantity_contracts(t,site)
    require(datetime.fromisoformat(c['horizon']['start_at'])==datetime.fromisoformat(c['planning_at'])<datetime.fromisoformat(c['horizon']['end_at']),'INVALID_PLANNING_RANGE')
    items={x['id']:x for x in t['items']};recipes={x['id']:x for x in t['recipes']}
    resources=[]
    for r in t['resources']:
        require(r['location_id']==site and r.get('efficiency_percent')==100,'RESOURCE_CONTRACT_UNSUPPORTED')
        resources.append(dict(id=r['id'],role='MACHINE',location_id=site,availability_calendar_id=r['calendar_id'],parallel_units=text(r['parallel_units']) if r['parallel_units'] is not None else '1',capacity_model=r['capacity_model'],provenance=provenance(r)))
    for r in resources:
        if r['capacity_model']=='WINDOW_QUANTITY':
            windows=[w for w in t['capacity_windows'] if w['resource_id']==r['id']]
            require(windows and len({w['capacity_quantity'] for w in windows})==1 and len({w['uom'] for w in windows})==1,'INCONSISTENT_CAPACITY_WINDOWS')
            r.update(window_capacity_quantity=text(windows[0]['capacity_quantity']),capacity_uom=windows[0]['uom'],maxearly_seconds=2592000)
    calendars=[dict(id=x['id'],timezone=x['timezone'],available_intervals=copy.deepcopy(x['available_intervals']),provenance=provenance(x)) for x in t['calendars']]
    common=next((x for x in calendars if x['id']=='CAL-FACTORY'),None)
    require(common is not None,'COMMON_WORK_CALENDAR_REQUIRED')
    for r in t['operators']:
        require(r['location_id']==site and r['capacity']==1 and r['efficiency_percent']==100,'PERSON_CONTRACT_UNSUPPORTED')
        resources.append(dict(id=r['id'],role='OPERATOR',pool_id=PERSON_POOL,location_id=site,availability_calendar_id=r['calendar_id'],parallel_units=text(r['capacity']),provenance=provenance(r)))
    resources.extend(dict(id=i,role=role,location_id=site,availability_calendar_id=common['id'],parallel_units='1',provenance={'derived':'TECHNICAL_POOL_NOT_NAMED_RESOURCE'}) for i,role in [(PERSON_POOL,'OPERATOR_POOL'),(MACHINE_POOL,'MACHINE_POOL')])
    alternatives={r['resource_id'] for r in t['machine_qualifications']}
    for r in resources:
        if r['id'] in alternatives:r['pool_id']=MACHINE_POOL
    routes=[];boms=[];projected_recipes=[];operation_map={};setups=derivations['operation_setups']
    for recipe in t['recipes']:
        require(recipe['location_id']==site,'RECIPE_LOCATION_UNSUPPORTED')
        ops=sorted([o for o in t['operations'] if o['recipe_id']==recipe['id']],key=lambda o:o['sequence'])
        require(ops,'EMPTY_RECIPE_OPERATIONS')
        projected=[]
        for o in ops:
            if o['duration_model']=='AVAILABILITY_TRANSFER':
                receiving=policy.get('wip_progress_policy',{}).get('receiving',{})
                require(recipe.get('progress_contract')=='stage-flow-v2' and recipe.get('continuation_kind')=='AVAILABILITY_TRANSFER' and o.get('continuation_kind')=='AVAILABILITY_TRANSFER' and not o['resource_ids'] and o.get('machine_and_person_simultaneously_required') is False and o.get('required_operator_skill') is None and receiving.get('basis')=='DECLARED_RESEARCH_ZERO_TIME_RECEIPT' and receiving.get('duration_seconds')==o.get('fixed_seconds')==0 and receiving.get('calendar_id') in {c['id'] for c in calendars},'AVAILABILITY_TRANSFER_SOURCE_POLICY')
                op=dict(id=o['id'],sequence=o['sequence'],predecessors=[],duration_model='AVAILABILITY_TRANSFER',fixed_seconds='0',seconds_per_unit='0',availability_calendar_id=receiving['calendar_id'],provenance=provenance(o))
                projected.append(op);operation_map[recipe['id']]={'recipe_id':recipe['id'],'operation_id':o['id']}
                continue
            require(o.get('machine_and_person_simultaneously_required') is True and bool(o['required_operator_skill']),'JOINT_PERSON_REQUIREMENT_MISSING')
            rids=o['resource_ids'];alt=o['resources_are_alternatives']
            require(bool(rids) and (alt or len(rids)==1),'UNSUPPORTED_RESOURCE_REQUIREMENT')
            op=dict(id=o['id'],sequence=o['sequence'],predecessors=[o['predecessor_id']] if o['predecessor_id'] else [],duration_model=o['duration_model'],fixed_seconds=text(o['fixed_seconds']),seconds_per_unit=text(o['seconds_per_unit'] or 0),resource_id=MACHINE_POOL if alt else rids[0],resource_units='1',operator_requirement={'pool_id':PERSON_POOL,'skill_id':o['required_operator_skill'],'quantity':'1','search':'PRIORITY'},provenance=provenance(o))
            if alt:
                op['machine_skill_id']=o['required_operator_skill'];require(o['id'] in setups,'SETUP_SOURCE_MAPPING_MISSING: '+o['id'])
            if o['duration_model']=='FIXED_BATCH':
                op['batch_quantity']=text(o['batch_quantity'])
            elif o['duration_model']=='PER_UNIT':op['duration_model']='FIXED_PLUS_PER_UNIT'
            else:require(False,'UNSUPPORTED_DURATION_MODEL')
            projected.append(op)
            name=recipe['id'] if len(ops)==1 else recipe['id']+':'+o['id']
            operation_map[name]={'recipe_id':recipe['id'],'operation_id':o['id']}
        routes.append(dict(id=recipe['routing_id'],output_item_id=recipe['output_item_id'],effective_from=recipe['effective_from'],effective_to=recipe['effective_to'],operations=projected,provenance=provenance(recipe)))
        lines=[dict(item_id=l['input_item_id'],quantity_per_output=text(l['quantity_per_output']),uom=l['input_uom'],scrap_fraction=text(l['scrap_fraction']),consume_operation_id=l['consume_operation_id'],consume_at=l['consume_at'],provenance=provenance(l)) for l in t['bom_lines'] if l['recipe_id']==recipe['id']]
        boms.append(dict(id=recipe['bom_id'],output_item_id=recipe['output_item_id'],output_quantity='1',output_uom=items[recipe['output_item_id']]['base_uom'],effective_from=recipe['effective_from'],effective_to=recipe['effective_to'],lines=lines,provenance=provenance(recipe)))
        pr=dict(id=recipe['id'],output_item_id=recipe['output_item_id'],location_id=site,bom_revision_id=recipe['bom_id'],routing_revision_id=recipe['routing_id'],effective_from=recipe['effective_from'],effective_to=recipe['effective_to'],manufacturing_minimum_quantity=text(recipe['minimum_quantity']),manufacturing_multiple=text(recipe['multiple']),transfer_policy='WHOLE_MANUFACTURING_BATCH',provenance=provenance(recipe))
        if recipe['maximum_quantity'] is not None:pr['manufacturing_maximum_quantity']=text(recipe['maximum_quantity'])
        projected_recipes.append(pr)
    inventory=[]
    reachable={site}|{r[k] for r in t.get('distribution_lanes',[]) for k in ('origin_location_id','destination_location_id')}
    for r in t['inventory_positions']:
        if r['location_id'] not in reachable:continue
        require(r.get('physical') is not None and r.get('net_available') is not None,'UNKNOWN_REACHABLE_INVENTORY: '+r['id'])
        require(Decimal(str(r['physical']))-Decimal(str(r['quality_blocked']))-Decimal(str(r['other_unavailable']))==Decimal(str(r['net_available'])),'INVENTORY_BALANCE_MISMATCH')
        inventory.append(dict(id=r['id'],item_id=r['item_id'],location_id=r['location_id'],uom=r['uom'],on_hand=text(r['physical']),quality_blocked=text(r['quality_blocked']),other_nonnettable=text(r['other_unavailable']),physical_lot_ref=None,as_of=r['as_of'],provenance=provenance(r)))
    # Unknown off-site stock stays in source facts. With no transfer lanes it is
    # outside this factory's dependency closure, never converted to zero stock.
    sources=[]
    for s in t['supply_sources']:
        require(s['capacity_includes_confirmed'] is True,'SUPPLY_CAPACITY_ACCOUNTING_REQUIRED')
        sources.append(dict(id=s['id'],supplier_id=s['supplier_id'],item_id=s['item_id'],location_id=s['location_id'],lead_time_seconds=s['lead_time_seconds'],lead_time_basis='CALENDAR_TIME',minimum_order_quantity=text(s['minimum_quantity']),order_multiple=text(s['order_multiple']),batch_window_seconds=0,effective_from=s['effective_from'],effective_to=s['effective_to'],priority=s['priority'],unit_price=text(s['unit_price']) if s['unit_price'] is not None else None,currency=s['currency'],price_basis='MODEL_UNIT_PRICE_EXCLUDES_TAX_FREIGHT_HOLDING',capacity={'scope':'WHOLE_HORIZON_RECEIPTS','quantity':text(s['horizon_capacity']),'uom':s['uom'],'start_at':s['capacity_period']['start'],'end_at':s['capacity_period']['end']},receiving_qc={'wait_seconds':s['qc_wait_seconds'],'basis':'CALENDAR_TIME','provenance':provenance(s)},provenance=provenance(s)))
    source_by_id={s['id']:s for s in sources};orders=[]
    for p in t['purchase_orders']:
        s=source_by_id[p['source_id']]
        require(not p['included_in_opening'] and p['quantity']-p['received_quantity']-p['cancelled_quantity']==p['unreceived_quantity'],'CONFIRMED_PO_BALANCE_MISMATCH')
        orders.append(dict(id=p['id'],type='PO',external_ref=p['reference'],source_status=p['status'],status=p['status'],supply_source_id=s['id'],item_id=p['item_id'],supplier_id=s['supplier_id'],location_id=p['location_id'],uom=p['uom'],ordered_at=p['ordered_at'],expected_receipt_at=p['physical_receipt_at'],available_at=p['available_at'],inventory_includes_receipt=False,ordered_quantity=text(p['quantity']),remaining_quantity=text(p['unreceived_quantity']),quantity=text(p['quantity']),received_quantity=text(p['received_quantity']),cancelled_quantity=text(p['cancelled_quantity']),provenance=provenance(p)))
    for m in t['existing_manufacturing_orders']:
        require(m['protected'] is True and m['completed_quantity']==0 and m['recipe_id'] in recipes,'EXISTING_MO_UNSUPPORTED')
        op=dict(operation_id=m['operation_id'],resource_id=m['resource_id'],start_at=m['start_at'],end_at=m['end_at'],quantity=text(m['quantity']))
        commitments=copy.deepcopy(m.get('plan_operations') or [op])
        orders.append(dict(id=m['id'],type='MO',external_ref=m['reference'],source_status=m['status'],status=m['status'],recipe_id=m['recipe_id'],item_id=recipes[m['recipe_id']]['output_item_id'],location_id=site,quantity=text(m['quantity']),start_at=m['start_at'],end_at=m['end_at'],operations=commitments,freeze_policy='LOCKED',protected=True,provenance=provenance(m)))
    transitions=copy.deepcopy(t['setup_transitions'])
    pairs={(s['from_state'],s['to_state']) for s in transitions}
    for r in policy['setup_overlay']:
        require((r['from_state'],r['to_state']) not in pairs,'SETUP_OVERLAY_OVERWRITES_FACT')
        transitions.append(dict(r,id='ASSUMPTION:'+r['from_state']+'::'+r['to_state'],provenance={'policy_id':policy['policy_id'],'scope':'RESEARCH_ASSUMPTION'}))
    receipts=[]
    native_tables={'items','calendars','capacity_windows','customers','demand_lines','existing_manufacturing_orders','inventory_positions','machine_qualifications','operations','operators','purchase_orders','recipes','resource_skills','resources','routings','setup_transitions','skills','suppliers','supply_sources','bom_lines','inventory_targets'}
    for k,rows in t.items():
        for r in rows:
            role='NATIVE_INPUT' if k in native_tables else 'REFERENCE_ONLY'
            if k=='inventory_positions' and r['location_id']!=site:role='OUTSIDE_FACTORY_NO_TRANSFER_PATH'
            if k=='forecasts':role='PRESERVED_PENDING_NETTING_POLICY'
            if k in {'history_events','historical_manufacturing_orders','checkpoints'}:role='OPENING_ALREADY_INCLUDES_HISTORY_NO_REPLAY'
            receipts.append({'table':k,'id':r['id'],'role':role,'source_sha256':r.get('source_sha256')})
    active_setups={k:v for k,v in setups.items() if not any(o['id']==k and o['duration_model']=='AVAILABILITY_TRANSFER' for o in t['operations'])}
    meta=dict(schema_revision=REVISION,scope=policy['scope'],policy_id=policy['policy_id'],policy_sha256=digest(policy),policy=copy.deepcopy(policy),source_version_id=version['id'],canonical_semantic_sha256=c['semantic_sha256'],source_tables=copy.deepcopy(t),capacity_windows=copy.deepcopy(t['capacity_windows']),operation_setups=copy.deepcopy(active_setups),resource_initial_setups={r['id']:r['initial_setup'] for r in t['resources'] if r['initial_setup'] is not None},setup_transitions=transitions,qualification_priority_policy=policy['qualification_priority_policy'],native_operation_map=operation_map,source_derivations=copy.deepcopy(derivations),row_receipts=receipts)
    data=dict(schema_version='0.2.0',snapshot_id='SNAP-'+version['id'],scenario_id=version['id'],scenario_revision=1,planning_at=c['planning_at'],horizon=copy.deepcopy(c['horizon']),site={'id':site,'name':loc['name'],'timezone':c['timezone'],'provenance':provenance(loc)},engine={'profile':'frepple-community-9.19-supply-v1','engine':'frepple','target_version':'9.19.0','problem_kind':'SUPPLY_PLANNING'},rules={'profile_version':'v1','finite_material':True,'finite_capacity':True,'allow_overtime':False,'freeze_existing':True,'procurement_strategy':'PRIORITY','move_approved_early':0,'fix_broken_supply_path':False,'erase_previous_proposed':True},items=[{'id':x['id'],'kind':x['kind'],'base_uom':x['base_uom'],'provenance':provenance(x)} for x in t['items']],calendars=calendars,resources=resources,bom_revisions=boms,routing_revisions=routes,production_recipes=projected_recipes,inventory_positions=inventory,reservations=[],suppliers=[{'id':s['id'],'provenance':provenance(s)} for s in t['suppliers']],supply_sources=sources,demand_lines=[dict(id=r['id'],customer_id=r['customer_id'],item_id=r['item_id'],location_id=r['location_id'],quantity=text(r['quantity']),uom=r['uom'],due_at=r['due_at'],priority=r['priority'],minimum_shipment=text(r['minimum_shipment']),maximum_lateness_seconds=r['maximum_lateness_seconds'],provenance=provenance(r)) for r in t['demand_lines']],existing_orders=orders,skills=copy.deepcopy(t['skills']),resource_skills=[dict(id=s['id'],resource_id=s['resource_id'],skill_id=s['skill_id'],priority=1,effective_from=s['effective_from'],effective_to=s['effective_to'],provenance=provenance(s)) for s in t['resource_skills']+t['machine_qualifications']],assumptions=copy.deepcopy(policy['limits']),foundation_projection=meta)
    if t.get('purchase_receipts') or t.get('distribution_lanes'):
        data['locations']=copy.deepcopy(t['locations'])
        data['purchase_receipts']=[dict(copy.deepcopy(r),quantity=text(r['quantity']),provenance=provenance(r)) for r in t.get('purchase_receipts',[])]
        data['distribution_lanes']=[dict(copy.deepcopy(r),minimum_transfer_quantity=text(r['minimum_transfer_quantity']),transfer_multiple=text(r['transfer_multiple']),provenance=provenance(r)) for r in t.get('distribution_lanes',[])]
        meta['joint_supply_scope']=dict(source_table_count=len(t),source_row_count=sum(map(len,t.values())),reachable_locations=sorted(reachable),
            unknown_stock_policy='PRESERVE_DISCONNECTED_UNKNOWN_NEVER_AS_SUPPLY',execution_authorized=False)
        for row in receipts:
            if row['table'] in {'purchase_receipts','distribution_lanes'}:row['role']='NATIVE_INPUT'
            if row['table']=='inventory_positions' and next(r['location_id'] for r in t['inventory_positions'] if r['id']==row['id']) in reachable:row['role']='NATIVE_INPUT'
    seal(data)
    return {'plan_input':data,'context':{'adapter_profile':ADAPTER,'customers':copy.deepcopy(t['customers']),'demand_customer_links':{r['id']:r['customer_id'] for r in t['demand_lines']},'foundation_projection_sha256':meta['projection_sha256'],'source_sha256':c['semantic_sha256'],'simulation_only':True,'execution_authorized':False},'readiness':{'ready':True,'status':'PROJECTION_READY_PENDING_NATIVE_AUDIT','native_validated':False,'policy_id':policy['policy_id'],'scope':policy['scope'],'execution_authorized':False}}


def projection_hash(data):
    # JSON encoding is read-only: avoid recursively copying the entire frozen
    # enterprise each time a caller authenticates its seal. Same bytes/hash.
    value={k:v for k,v in data.items() if k not in {'snapshot_id','scenario_id','scenario_revision'}}
    value['foundation_projection']={k:v for k,v in data['foundation_projection'].items() if k!='projection_sha256'}
    return digest(value)


def seal(data):data['foundation_projection']['projection_sha256']=projection_hash(data)


PRIORITY_OVERLAY_REVISION='foundation-firm-priority-overlay-v1'


def _firm_rows(data,demand_id):
    meta=data['foundation_projection']
    require(meta['policy'].get('demand_scope')=='ALL_24_FIRM_DEMANDS' or
            (meta['policy'].get('profile')=='continuity-current-state-v1' and meta['policy'].get('demand_scope')=='VISIBLE_GROSS_MINUS_SHIPPED') or
            (meta['policy'].get('profile')=='odoo-verified-manufacturing-v1' and meta['policy'].get('demand_scope')=='SCOPED_ODOO_OPEN_SALES'),'FOUNDATION_PRIORITY_SCOPE')
    rows=[r for r in data['demand_lines'] if r['id']==demand_id]
    originals=[r for r in meta['source_tables']['demand_lines'] if r['id']==demand_id]
    require(len(rows)==len(originals)==1,'FOUNDATION_PRIORITY_UNKNOWN_FIRM')
    row,original=rows[0],originals[0]
    require(row.get('kind','FIRM_ORDER')=='FIRM_ORDER','FOUNDATION_PRIORITY_NOT_FIRM')
    require(all(row.get(k)==original.get(k) for k in ('item_id','location_id','uom','customer_id','due_at'))
            and Decimal(str(row['quantity']))==Decimal(str(original['quantity'])),'FOUNDATION_PRIORITY_FIRM_IDENTITY')
    return row,original


def firm_order_ref(data,demand_id):
    """Exact sealed source semantics; an absent order reference stays absent."""
    validate(data)
    _,original=_firm_rows(data,demand_id)
    value=original.get('order_ref')
    return value if isinstance(value,str) and value.strip() else None


def firm_order_refs(data):
    """Authenticate this projection once, then resolve its exact source rows.

    This is scoped to one call, with no persistent or mutable-object cache.
    Candidate validation still restores and authenticates its baseline seal.
    """
    validate(data)
    references={}
    for row in data['demand_lines']:
        if row['id'] not in {r['id'] for r in data['foundation_projection']['source_tables']['demand_lines']}:
            references[row['id']]=None
            continue
        _,original=_firm_rows(data,row['id'])
        value=original.get('order_ref')
        references[row['id']]=value if isinstance(value,str) and value.strip() else None
    return references


def validate_priority_overlay(data):
    """Restore one priority and the original sealed projection, without edits.

    DraftStore additionally replays against its immutable actual baseline; a
    self-resealed receipt is never a replacement for that owning boundary.
    """
    meta=data['foundation_projection'];receipt=meta.get('priority_overlay')
    keys={'schema_revision','source_version_id','canonical_semantic_sha256',
          'baseline_projection_sha256','demand_id','before_priority','after_priority',
          'simulation_only','execution_authorized'}
    require(isinstance(receipt,dict) and set(receipt)==keys,'FOUNDATION_PRIORITY_OVERLAY_SHAPE')
    require(receipt['schema_revision']==PRIORITY_OVERLAY_REVISION and receipt['simulation_only'] is True
            and receipt['execution_authorized'] is False,'FOUNDATION_PRIORITY_OVERLAY_SCOPE')
    require(receipt['source_version_id']==meta['source_version_id'] and
            receipt['canonical_semantic_sha256']==meta['canonical_semantic_sha256'],'FOUNDATION_PRIORITY_SOURCE_MISMATCH')
    before,after=receipt['before_priority'],receipt['after_priority']
    require(all(type(p) is int and 0<=p<=2147483647 for p in (before,after)) and before!=after,
            'FOUNDATION_PRIORITY_VALUE')
    row,original=_firm_rows(data,receipt['demand_id'])
    require(type(original['priority']) is int and original['priority']==before and row['priority']==after,
            'FOUNDATION_PRIORITY_BEFORE_AFTER_MISMATCH')
    restored=copy.deepcopy(data);restored['foundation_projection'].pop('priority_overlay')
    next(r for r in restored['demand_lines'] if r['id']==receipt['demand_id'])['priority']=before
    restored['foundation_projection']['projection_sha256']=receipt['baseline_projection_sha256']
    require(projection_hash(restored)==receipt['baseline_projection_sha256'],'FOUNDATION_PRIORITY_BASELINE_MISMATCH')
    validate(restored)
    return receipt


def apply_priority_overlay(data,demand_id,priority):
    """Construct one research receipt; frozen source rows remain byte-for-byte equal."""
    validate(data);meta=data['foundation_projection']
    require('priority_overlay' not in meta,'FOUNDATION_PRIORITY_OVERLAY_ALREADY_PRESENT')
    row,original=_firm_rows(data,demand_id)
    before=row['priority']
    require(type(priority) is int and 0<=priority<=2147483647 and priority!=before,'FOUNDATION_PRIORITY_VALUE')
    require(type(before) is int and before==original['priority'],'FOUNDATION_PRIORITY_BEFORE_MISMATCH')
    receipt={'schema_revision':PRIORITY_OVERLAY_REVISION,'source_version_id':meta['source_version_id'],
             'canonical_semantic_sha256':meta['canonical_semantic_sha256'],
             'baseline_projection_sha256':meta['projection_sha256'],'demand_id':demand_id,
             'before_priority':before,'after_priority':priority,'simulation_only':True,'execution_authorized':False}
    row['priority']=priority;meta['priority_overlay']=receipt
    seal(data);validate(data)
    return copy.deepcopy(receipt)


def validate(data):
    meta=data.get('foundation_projection',{})
    if meta.get('policy',{}).get('profile')=='odoo-verified-manufacturing-v1':
        from p22.odoo_projection import validate as validate_odoo
        return validate_odoo(data)
    if meta.get('policy',{}).get('profile')=='continuity-current-state-v1':
        from p22.continuity_projection import validate as validate_continuity
        return validate_continuity(data)
    require(meta.get('schema_revision')==REVISION and meta.get('scope')=='RESEARCH_SIMULATION_ONLY','UNSUPPORTED_FOUNDATION_PROJECTION')
    require(meta.get('projection_sha256')==projection_hash(data),'PROJECTION_CHANGED')
    require(meta.get('policy_sha256')==digest(meta.get('policy')),'POLICY_CHANGED')
    require(meta['policy'].get('approval',{}).get('status')=='USER_APPROVED_RESEARCH_ONLY' and meta['policy'].get('execution_authorized') is False,'POLICY_APPROVAL_REQUIRED')
    require(data['rules']['finite_material'] is True and data['rules']['finite_capacity'] is True and data['rules']['allow_overtime'] is False and data['rules']['freeze_existing'] is True,'HARD_CONSTRAINT_RELAXED')
    if 'professional_overlay' in meta:
        from p22.professional_policy import validate_overlay
        validate_overlay(data)
    else:
        require({r['id'] for r in data['demand_lines']}=={r['id'] for r in meta['source_tables']['demand_lines']},'DEMAND_SCOPE_CHANGED')
    if 'priority_overlay' in meta:validate_priority_overlay(data)
    if 'editor_scope' in meta:
        from p22.foundation_editor import contract,validate_overlay
        contract(data)
        if 'plan_editor_overlay' in meta:validate_overlay(data)
    return data
