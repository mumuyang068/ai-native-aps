"""Pure visible-source adapter for the declared continuous research enterprise.

No world state, databases, wall-clock calls, file reads or solving occur here.
Original source documents are preserved in the location receipt. Every derived
row can be reconstructed from those documents and the pinned declarations.
"""
import copy
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

from p22.foundation_projection import digest, require, text

ENTERPRISE = 'SIM-GARMENT-CONT-01'
PROFILE = 'continuity-current-state-v1'
STAGE_PROGRESS = 'stage-flow-v2'


def stage_policy(operating):
    policy=operating.get('wip_progress_policy')
    if policy is None:return None
    require(isinstance(policy,dict) and policy.get('schema')=='stage-wip-policy/2' and
            policy.get('mode')=='STAGE_FLOW','CONTINUITY_STAGE_POLICY_INVALID')
    receipt=policy.get('receiving',{})
    require(receipt==dict(basis='DECLARED_RESEARCH_ZERO_TIME_RECEIPT',duration_seconds=0,
                         calendar_id='CAL-FACTORY'),'STAGE_RECEIVING_POLICY_REQUIRED')
    return policy


def actual_quantity(row):
    return Decimal(str(row['actual_quantity'] if row.get('status')=='INTERRUPTED' else row['quantity']))


def stage_progress(job,mes,definition,wms):
    """Derive disjoint physical partitions from visible MES and WMS documents."""
    route=definition['products'][job['product_id']]['routing'];total=Decimal(job['quantity'])
    actual=[o for o in mes.get('operations',[]) if o['job_id']==job['id']]
    require(total==20 and len(route)==4 and all(o.get('status') in {'COMPLETED','INTERRUPTED'} for o in actual),
            'CONTINUITY_WIP_NONPREFIX_OR_RUNNING_UNSUPPORTED:'+job['id'])
    require(len({o['id'] for o in actual})==len(actual),'CONTINUITY_ACTUAL_DUPLICATE:'+job['id'])
    quantities={op:Decimal(0) for op in route}
    for o in actual:
        op=o['operation_id'];spec=definition['operations'].get(op);q=actual_quantity(o)
        require(op in quantities and spec is not None and q.is_finite() and 0<=q<=total,
                'CONTINUITY_ACTUAL_OPERATION_CONFLICT:'+o['id'])
        require(q==q.to_integral_value(),'CONTINUITY_INTEGER_STAGE_ACTUAL_REQUIRED:'+o['id'])
        if spec.get('batch_quantity') is not None:
            require(o['status']=='COMPLETED' and q==total,
                    'CONTINUITY_FIXED_OPERATION_PARTIAL_UNSUPPORTED:'+o['id'])
        else:
            require(Decimal(spec['fixed_minutes'])==0 and Decimal(spec['minutes_per_piece'])>0,
                    'CONTINUITY_CONTINUOUS_SEGMENT_CONTRACT_REQUIRED:'+o['id'])
            if q!=total:require(bool(o.get('segment_id')) or o['status']=='INTERRUPTED',
                               'CONTINUITY_CONTINUOUS_SEGMENT_CONTRACT_REQUIRED:'+o['id'])
        finish=at(o.get('actual_end',o['end']));begin=at(o['start'])
        require(begin<=finish and Decimal(str((finish-begin).total_seconds()))>=
                (Decimal(spec['fixed_minutes'])+Decimal(spec['minutes_per_piece'])*q)*60,
                'CONTINUITY_SEGMENT_DURATION_SHORT:'+o['id'])
        if o['status']=='INTERRUPTED':
            require(bool(o.get('interruption_reason')) and q<Decimal(o['quantity']) and finish<=at(o['end']),
                    'CONTINUITY_INTERRUPTION_FACT_REQUIRED:'+o['id'])
        quantities[op]+=q
        require(quantities[op]<=total,'CONTINUITY_ACTUAL_OPERATION_CONFLICT:'+o['id'])
    values=[quantities[op] for op in route]
    require(all(a>=b for a,b in zip(values,values[1:])), 'CONTINUITY_STAGE_BALANCE_NEGATIVE:'+job['id'])
    for o in actual:
        begin=at(o['start']);op=o['operation_id'];q=actual_quantity(o)
        already=sum((actual_quantity(p) for p in actual if p['operation_id']==op and
                     at(p.get('actual_end',p['end']))<=begin),Decimal(0))
        for prior in route[:route.index(op)]:
            available=sum((actual_quantity(p) for p in actual if p['operation_id']==prior and
                           at(p.get('actual_end',p['end']))<=begin),Decimal(0))
            require(available>=already+q,'CONTINUITY_ACTUAL_PREDECESSOR_NOT_COMPLETED:'+o['id'])
    movements=[m for m in wms.get('movements',[]) if m.get('job_id')==job['id']]
    received=sum((Decimal(m['quantity']) for m in movements if m['kind']=='FinishedGoodsReceived'),Decimal(0))
    shipped=sum((Decimal(m['quantity']) for m in movements if m['kind']=='ShipmentPosted'),Decimal(0))
    require(0<=shipped<=received<=values[-1] and Decimal(job['completed_quantity'])==received,
            'CONTINUITY_FG_BALANCE_CONFLICT:'+job['id'])
    if received<total:require(values[0]==total and job['status']=='WIP','CONTINUITY_WIP_STATE_UNSUPPORTED:'+job['id'])
    labels=['SEW','QC','PACK','RECEIPT'];openings=[values[0]-values[1],values[1]-values[2],values[2]-values[3],values[3]-received]
    remaining=[op for op in route if quantities[op]<total]
    return dict(progress_contract=STAGE_PROGRESS,original_job_quantity=number(total),
        completed_quantities={op:number(q) for op,q in quantities.items()},
        finished_received_quantity=number(received),shipped_quantity=number(shipped),
        packed_quantity=number(values[-1]),packed_unreceived_quantity=number(values[-1]-received),
        remaining_quantity=number(total-received),remaining_from_operation_id=remaining[0] if remaining else None,
        remaining_operation_ids=remaining,stage_partitions=[dict(stage_id=label,quantity=number(q),
            item_id='CONT-WIP:'+job['id']+':'+label) for label,q in zip(labels,openings)],
        completed_prefix=[copy.deepcopy(o) for o in actual if quantities[o['operation_id']]==total],
        actual_operation_records=copy.deepcopy(actual),actual_operation_ids=[o['id'] for o in actual],
        source_refs=[dict(table='operations',id=o['id'],sha256=digest(o)) for o in actual])


def at(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def number(value):
    q = Decimal(str(value))
    require(q.is_finite(), 'CONTINUITY_NONFINITE_QUANTITY')
    return int(q) if q == q.to_integral_value() else float(q)


def declaration(reference):
    """Only declared reference roles/units; no inherited stock/MO/history."""
    return {k: copy.deepcopy(reference['tables'][k]) for k in
            ['items', 'operations', 'skills', 'resource_skills', 'machine_qualifications']}


def _live_source_scope(documents):
    """Live qualification semantics require one explicit, coherent source view."""
    if not any('branch_id' in doc or 'source_revision' in doc for doc in documents.values()):
        return False
    identities=[]
    for doc in documents.values():
        branch,revision=doc.get('branch_id'),doc.get('source_revision')
        require(isinstance(branch,str) and bool(branch.strip()) and branch==branch.strip() and
                isinstance(revision,int) and not isinstance(revision,bool) and revision>=0,
                'CONTINUITY_LIVE_SOURCE_SCOPE_MISMATCH')
        identities.append((branch,revision))
    require(len(set(identities))==1,'CONTINUITY_LIVE_SOURCE_SCOPE_MISMATCH')
    return True


def _live_actual_operator_matches(operation, definition, role_ref, reference_ops, cutoff):
    """Read only public skill/validity facts, never simulator state or policies."""
    person=operation.get('operator_id');source=reference_ops.get(operation.get('operation_id'))
    if person not in definition['operators'] or not source:return False
    skill=source.get('required_operator_skill')
    if skill not in {row['id'] for row in role_ref['skills']}:return False
    try:
        begin=at(operation['start']);end=at(operation.get('actual_end',operation['end']))
        if not (begin<end<=cutoff and (operation.get('status')=='INTERRUPTED' or at(operation['end'])<=end)):return False
        return any(row.get('resource_id')==person and row.get('skill_id')==skill and
                   at(row['effective_from'])<=begin and end<=at(row['effective_to'])
                   for row in role_ref['resource_skills'])
    except (KeyError,TypeError,ValueError):
        return False


def _year_contract(definition, operating, reference):
    if 'year_policy' not in operating:
        return None
    policy=operating['year_policy']
    require(isinstance(policy,dict) and policy.get('schema')=='year-operating-policy/1' and
            policy.get('id')=='APS-SIM-YEAR-01-POLICY-V1' and
            type(policy.get('planning_lookahead_days')) is int and policy['planning_lookahead_days']==56,
            'YEAR_OPERATING_POLICY_INVALID')
    try:
        origin,continuation,end=(at(policy[k]) for k in ['history_origin','continuation_from','history_end'])
        require(all(value.utcoffset() is not None for value in [origin,continuation,end]) and
                origin==at(definition['origin']) and continuation==origin+timedelta(days=56) and
                end==at(definition['end']) and continuation<end,'YEAR_OPERATING_POLICY_INVALID')
    except (KeyError,TypeError,ValueError):
        require(False,'YEAR_OPERATING_POLICY_INVALID')
    people=operating.get('research_qualifications');machines=operating.get('research_machine_qualifications')
    require(isinstance(people,list) and bool(people) and isinstance(machines,list) and bool(machines),
            'YEAR_RESEARCH_QUALIFICATIONS_REQUIRED')
    ops={row['id']:row for row in reference['tables']['operations']}
    skills={row['id'] for row in reference['tables']['skills']}
    primary={(spec['operator'],ops[op]['required_operator_skill']) for op,spec in definition['operations'].items()}
    for rows,machine in [(people,False),(machines,True)]:
        require(len({r.get('id') for r in rows})==len(rows),'YEAR_RESEARCH_QUALIFICATION_INVALID')
        for r in rows:
            require(all(isinstance(r.get(k),str) and bool(r[k].strip()) for k in
                        ['id','resource_id','effective_from','effective_to','phase','basis']),
                    'YEAR_RESEARCH_QUALIFICATION_INVALID')
            try:
                begin,finish=at(r['effective_from']),at(r['effective_to'])
                require(begin.utcoffset() is not None and finish.utcoffset() is not None and begin<finish,
                        'YEAR_RESEARCH_QUALIFICATION_INVALID')
                require((r['phase']=='FROZEN_REPLAY' and origin<=begin<finish<=continuation) or
                        (r['phase']=='ANNUAL_SYNTHETIC' and continuation<=begin<finish),
                        'YEAR_RESEARCH_QUALIFICATION_INVALID')
            except (TypeError,ValueError):
                require(False,'YEAR_RESEARCH_QUALIFICATION_INVALID')
            if machine:
                spec=definition['operations'].get(r.get('operation_id'))
                require(spec is not None and r['resource_id'] in spec.get('resource_ids',[spec['resource']]) and
                        r['resource_id'] in definition['resources'],
                        'YEAR_RESEARCH_MACHINE_IDENTITY_INVALID')
            else:
                require(r.get('skill_id') in skills and (r['resource_id'],r['skill_id']) in primary,
                        'YEAR_RESEARCH_PERSON_IDENTITY_INVALID')
    require(operating['supply'].get('capacity_policy')=='SYNTHETIC_ROLLING_56_DAY_NEW_SUPPLY_ALLOWANCE' and
            number(operating['supply']['horizon_capacity'])>0,'YEAR_ROLLING_CAPACITY_POLICY_REQUIRED')
    return policy


def _year_actual_assignment_matches(operation, definition, operating, reference_ops, cutoff):
    try:
        op=operation['operation_id'];source=reference_ops[op]
        begin=at(operation['start']);end=at(operation.get('actual_end',operation['end']))
        if not (begin<end<=cutoff and (operation.get('status')=='INTERRUPTED' or at(operation['end'])<=end)):return False
        person=any(row['resource_id']==operation['operator_id'] and row['skill_id']==source['required_operator_skill'] and
                   at(row['effective_from'])<=begin and end<=at(row['effective_to']) for row in operating['research_qualifications'])
        machine=any(row['resource_id']==operation['resource_id'] and row['operation_id']==op and
                    at(row['effective_from'])<=begin and end<=at(row['effective_to']) for row in operating['research_machine_qualifications'])
        spec=definition['operations'][op]
        return person and machine and operation['resource_id'] in spec.get('resource_ids',[spec['resource']])
    except (KeyError,TypeError,ValueError):
        return False


def native_year_qualifications(rows):
    """Map explicit contiguous phase grants to native's one-row-per-pair form.

    Canonical keeps every original phase grant. Native cannot encode a gap in
    one qualification interval, so a disjoint union is rejected, never filled.
    A single existing grant is returned unchanged for saved-plan compatibility.
    """
    grouped={}
    for qualification in rows:
        grouped.setdefault((qualification['resource_id'],qualification['skill_id']),[]).append(qualification)
    result=[]
    for pair,grants in grouped.items():
        if len(grants)==1:
            result.append(copy.deepcopy(grants[0]));continue
        grants=sorted(grants,key=lambda q:(at(q['effective_from']),at(q['effective_to']),q['id']))
        first=at(grants[0]['effective_from']);finish=at(grants[0]['effective_to'])
        for grant in grants[1:]:
            require(at(grant['effective_from'])<=finish,'YEAR_DISCONTINUOUS_NATIVE_QUALIFICATION_UNSUPPORTED:'+pair[0]+':'+pair[1])
            finish=max(finish,at(grant['effective_to']))
        merged=copy.deepcopy(grants[0])
        merged.update(id='YEAR-CONTINUOUS:'+pair[0]+':'+pair[1],effective_from=first.isoformat(),effective_to=finish.isoformat(),
                      source_qualification_ids=[grant['id'] for grant in grants],
                      qualification_derivation='UNION_OF_EXPLICIT_CONTIGUOUS_RESEARCH_PHASES_NO_GAP_FILLED')
        result.append(merged)
    return result


def build_policy(definition, operating, reference):
    result=dict(policy_id='APS-SIM-CONTINUITY-02-POLICY-V1', profile=PROFILE,
        enterprise_id=ENTERPRISE, dataset_id=ENTERPRISE+'-V1', scope='RESEARCH_SIMULATION_ONLY',
        approval={'status':'USER_APPROVED_RESEARCH_ONLY','basis':'APS-SIM-CONTINUITY-02 task D2'},
        execution_authorized=False, change_frozen_facts=False,
        qualification_priority_policy='EQUAL_ELIGIBLE_CANDIDATES',
        demand_scope='VISIBLE_GROSS_MINUS_SHIPPED', procurement_strategy='PRIORITY',
        purchase_aggregation_seconds=0, setup_overlay=[],
        definition_sha256=digest(definition), operating_sha256=digest(operating),
        reference_roles_sha256=digest(declaration(reference)),
        inventory_policy='CURRENT_WMS_BALANCE_HISTORY_INCLUDED_EXACTLY_ONCE',
        receipt_authority='WMS_ACTUAL_RECEIPT_WITH_MATCHED_ERP_PO_ITEM_UOM_AND_LOT',
        wip_policy='COMPLETED_PREFIX_FULLY_ISSUED_UNIQUE_OPENING_REMAINING_OPERATIONS_ONLY',
        finished_goods_allocation='FUNGIBLE_WITHIN_IDENTICAL_ITEM; ACTUAL_JOB_ORDER_LINK_PRESERVED',
        cut_policy='CONSERVATIVE_FULL_HOUR_STARTS_WITHIN_DECLARED_WORK_SHIFTS',
        setup_policy='NO_ADDITIONAL_SETUP_IN_DECLARED_SIMULATION_NEUTRAL_STATE',
        cost_policy='UNKNOWN_SOURCE_PRICES_OMITTED_NATIVE_PRIORITY_ONLY_NO_ECONOMIC_CLAIM',
        supplier_capacity_policy='DECLARED_5000_EIGHT_WEEK_TOTAL_LESS_WMS_ACTUAL_RECEIPTS_FUTURE_CONFIRMED_INCLUDED',
        planning_clock='VISIBLE_SOURCE_AS_OF_SITE_TIME_NOT_WALL_CLOCK',
        limits=['Research assumptions are not supplier quotations or execution authorization.',
                'Whole-batch transfer; conservative complete-hour CUT slots.',
                'Only fully issued WIP with a completed operation prefix is supported.',
                'FG may satisfy another demand for the identical item; actual job identity is retained.',
                'Pending QC remains unavailable without an actual known release.',
                'Supplier capacity is a study allowance for this planning horizon.'])
    stage=stage_policy(operating)
    if stage:
        result.update(wip_progress_policy=copy.deepcopy(stage),wip_policy='JOB_PRIVATE_STAGE_FLOW_WITH_SEPARATE_ACTUAL_RECEIPTS')
        result['limits']=[line for line in result['limits'] if line!='Only fully issued WIP with a completed operation prefix is supported.']
    year=_year_contract(definition,operating,reference)
    if year:
        result.update(policy_id=year['id'],year_policy=copy.deepcopy(year),
            approval={'status':'USER_APPROVED_RESEARCH_ONLY','basis':'APS-SIM-YEAR-01 task A4/D2'},
            qualification_priority_policy='DECLARED_YEAR_RESEARCH_PRIMARY_ROLES_WITH_INTERVAL_VALIDITY',
            supplier_capacity_policy='SYNTHETIC_ROLLING_56_DAY_NEW_SUPPLY_ALLOWANCE')
        result['limits'].append('Annual roles and supplier allowances are explicit synthetic research declarations; original reference qualifications remain unchanged.')
    return result



def inventory_facts(wms,mes,definition,operating,planning):
    """Original source inventory semantics shared by full and indexed reads."""
    positions=[];site=operating['site_id'];tasks={r['id']:r for r in mes.get('manufacturing_tasks',[])}
    for r in wms.get('lots',[]):
        issued=sum((Decimal(m['quantity']) for m in wms.get('movements',[]) if m.get('kind')=='MaterialIssued' and m.get('lot_id')==r['id']),Decimal(0))
        require(Decimal(r['quantity'])-Decimal(r['consumed'])==Decimal(r['physical']) and issued==Decimal(r['consumed']) and
                Decimal(r['physical'])==Decimal(r['available'])+Decimal(r['pending'])+Decimal(r['held']),
                'CONTINUITY_CURRENT_LOT_BALANCE_CONFLICT:'+r['id'])
        positions.append(dict(id=r['id'],item_id=r['item_id'],location_id=site,physical=number(r['physical']),
            quality_blocked=number(Decimal(r['pending'])+Decimal(r['held'])),other_unavailable=operating['inventory']['other_unavailable'],
            net_available=number(r['available']),uom=r['unit'],as_of=planning,knowledge='CURRENT_WMS_BALANCE_HISTORY_INCLUDED',source_record=copy.deepcopy(r)))
    for r in wms.get('finished_goods',[]):
        require('quantity' in r or 'physical' in r,'CONTINUITY_FG_BALANCE_UNSUPPORTED:'+r['id'])
        q=r.get('quantity',r.get('physical'));job=tasks.get(r['id'])
        require(job is not None and job['status'] in {'WIP','COMPLETED'},'CONTINUITY_FG_JOB_UNRESOLVED:'+r['id'])
        progress=job_progress(job,mes,definition,policy=stage_policy(operating),wms=wms)
        movements=[m for m in wms.get('movements',[]) if m.get('job_id')==job['id']]
        received=sum((Decimal(m['quantity']) for m in movements if m['kind']=='FinishedGoodsReceived'),Decimal(0))
        shipped=sum((Decimal(m['quantity']) for m in movements if m['kind']=='ShipmentPosted'),Decimal(0))
        require(received==Decimal(str(progress['finished_received_quantity'])) and
                0<=shipped<=received and Decimal(str(q))==received-shipped,
                'CONTINUITY_FG_BALANCE_CONFLICT:'+r['id'])
        iid=job['product_id']
        positions.append(dict(id='FG:'+r['id'],item_id=iid,location_id=site,physical=number(q),quality_blocked=0,other_unavailable=0,
            net_available=number(q),uom=definition['items'][iid]['unit'],as_of=planning,knowledge='CURRENT_WMS_FG_BALANCE_AFTER_SHIPMENTS',source_record=copy.deepcopy(r)))
    fg_ids={r['id'] for r in wms.get('finished_goods',[])}
    for job in tasks.values():
        if Decimal(job.get('completed_quantity','0'))>0:
            require(job['id'] in fg_ids,'CONTINUITY_FG_POSITION_MISSING:'+job['id'])
    return positions


def job_progress(job,mes,definition,*,policy=None,wms=None):
    """Reconcile closed MES execution segments into one remaining-stage stock.

    No partial fixed batch, running interval, scrap or multiple stage balance is
    silently approximated. Source records and original job quantity remain intact.
    """
    if policy is not None:return stage_progress(job,mes,definition,wms or {})
    route=definition['products'][job['product_id']]['routing'];total=Decimal(job['quantity'])
    actual=[o for o in mes.get('operations',[]) if o['job_id']==job['id']]
    require(total==20 and all(o.get('status')=='COMPLETED' for o in actual),
            'CONTINUITY_WIP_NONPREFIX_OR_RUNNING_UNSUPPORTED:'+job['id'])
    quantities={op:Decimal(0) for op in route}
    for o in actual:
        op=o['operation_id'];q=Decimal(o['quantity']);spec=definition['operations'].get(op)
        require(op in quantities and spec is not None and q.is_finite() and 0<q<=total,
                'CONTINUITY_ACTUAL_OPERATION_CONFLICT:'+o['id'])
        if spec['batch_quantity'] is not None:
            require(q==total,'CONTINUITY_ACTUAL_OPERATION_CONFLICT:CONTINUITY_FIXED_OPERATION_PARTIAL_UNSUPPORTED:'+o['id'])
        elif q!=total:
            require(Decimal(spec['fixed_minutes'])==0 and Decimal(spec['minutes_per_piece'])>0 and
                    bool(o.get('segment_id')),'CONTINUITY_ACTUAL_OPERATION_CONFLICT:CONTINUITY_CONTINUOUS_SEGMENT_CONTRACT_REQUIRED:'+o['id'])
            required_seconds=Decimal(spec['minutes_per_piece'])*q*60
            require(Decimal(str((at(o.get('actual_end',o['end']))-at(o['start'])).total_seconds()))>=required_seconds,
                    'CONTINUITY_ACTUAL_OPERATION_CONFLICT:CONTINUITY_SEGMENT_DURATION_SHORT:'+o['id'])
        quantities[op]+=q
        require(quantities[op]<=total,'CONTINUITY_ACTUAL_OPERATION_CONFLICT:'+o['id'])
    values=[quantities[op] for op in route]
    for o in actual:
        begin=at(o['start']);op=o['operation_id'];q=Decimal(o['quantity'])
        already=sum((Decimal(p['quantity']) for p in actual if p['operation_id']==op and
                     at(p.get('actual_end',p['end']))<=begin),Decimal(0))
        for prior in route[:route.index(op)]:
            available=sum((Decimal(p['quantity']) for p in actual if p['operation_id']==prior and
                           at(p.get('actual_end',p['end']))<=begin),Decimal(0))
            require(available>=already+q,'CONTINUITY_ACTUAL_OPERATION_CONFLICT:CONTINUITY_ACTUAL_PREDECESSOR_NOT_COMPLETED:'+o['id'])
    require(all(a>=b for a,b in zip(values,values[1:])),
            'CONTINUITY_WIP_NONPREFIX_OR_RUNNING_UNSUPPORTED:'+job['id'])
    finished=values[-1]
    require(Decimal(job['completed_quantity'])==finished,'CONTINUITY_COMPLETED_TASK_CONFLICT:'+job['id'])
    if finished==total:
        require(job['status']=='COMPLETED','CONTINUITY_COMPLETED_TASK_CONFLICT:'+job['id'])
        remaining=[]
    else:
        require(job['status']=='WIP' and values[0]==total,'CONTINUITY_WIP_STATE_UNSUPPORTED:'+job['id'])
        first=next(i for i,q in enumerate(values) if q<total)
        require(all(q==finished for q in values[first:]),
                'CONTINUITY_WIP_MULTIPLE_STAGE_BALANCES_UNSUPPORTED:'+job['id'])
        remaining=route[first:]
        if finished:
            require(all(definition['operations'][op]['batch_quantity'] is None for op in remaining),
                    'CONTINUITY_FIXED_OPERATION_PARTIAL_UNSUPPORTED:'+job['id'])
    return dict(original_job_quantity=number(total),completed_quantities={op:number(q) for op,q in quantities.items()},
        finished_received_quantity=number(finished),remaining_quantity=number(total-finished),
        remaining_from_operation_id=remaining[0] if remaining else None,remaining_operation_ids=remaining,
        completed_prefix=[copy.deepcopy(o) for o in actual if quantities[o['operation_id']]==total],
        actual_operation_records=copy.deepcopy(actual),
        actual_operation_ids=[o['id'] for o in actual],source_refs=[dict(table='operations',id=o['id'],sha256=digest(o)) for o in actual])


def wip_inventory_fact(job,mes,definition,lots,site,planning):
    progress=job_progress(job,mes,definition)
    require(progress['remaining_operation_ids'],'CONTINUITY_WIP_STATE_UNSUPPORTED:'+job['id'])
    totals=defaultdict(Decimal)
    for i in mes.get('material_issues',[]):
        if i['job_id']!=job['id']:continue
        lot=lots.get(i['lot_id']);require(lot is not None and i['unit']==lot['unit'],'WIP_ISSUE_LOT_UNRESOLVED:'+i['id']);totals[lot['item_id']]+=Decimal(i['quantity'])
    expected={i:Decimal(q)*Decimal(job['quantity']) for i,q in definition['products'][job['product_id']]['bom'].items()}
    require(dict(totals)==expected,'WIP_ISSUED_MATERIAL_MISMATCH:'+job['id'])
    wid='CONT-WIP:'+job['id']
    remaining=progress['remaining_quantity']
    return dict(id='OPEN:'+wid,item_id=wid,location_id=site,physical=remaining,quality_blocked=0,other_unavailable=0,net_available=remaining,
        uom='件',as_of=planning,knowledge='DERIVED_FULLY_ISSUED_COMPLETED_PREFIX_WIP_ONCE',source_record=copy.deepcopy(job))


def wip_inventory_facts(job,mes,definition,lots,site,planning,*,operating,wms):
    policy=stage_policy(operating)
    if policy is None:return [wip_inventory_fact(job,mes,definition,lots,site,planning)]
    progress=job_progress(job,mes,definition,policy=policy,wms=wms)
    totals=defaultdict(Decimal)
    for issue in mes.get('material_issues',[]):
        if issue['job_id']!=job['id']:continue
        lot=lots.get(issue['lot_id'])
        require(lot is not None and issue['unit']==lot['unit'],'WIP_ISSUE_LOT_UNRESOLVED:'+issue['id'])
        totals[lot['item_id']]+=Decimal(issue['quantity'])
    expected={i:Decimal(q)*Decimal(job['quantity']) for i,q in definition['products'][job['product_id']]['bom'].items()}
    require(dict(totals)==expected,'WIP_ISSUED_MATERIAL_MISMATCH:'+job['id'])
    return [dict(id='OPEN:'+part['item_id'],item_id=part['item_id'],location_id=site,
        physical=part['quantity'],quality_blocked=0,other_unavailable=0,net_available=part['quantity'],uom='件',
        as_of=planning,knowledge='VISIBLE_STAGE_BALANCE_ONCE',progress_contract=STAGE_PROGRESS,
        stage_id=part['stage_id'],actual_job_id=job['id'],source_record=copy.deepcopy(job)) for part in progress['stage_partitions']]


def order_demand(r, definition, operating):
    """One source order's existing Canonical demand contract, also used by deltas."""
    from p21.canonical import DOMAIN
    from pydantic import ValidationError
    try:
        total,shipped=Decimal(str(r['quantity'])),Decimal(str(r['shipped']))
        require(total.is_finite() and shipped.is_finite() and total>=shipped>=0,'SOURCE_ORDER_BALANCE_NEGATIVE:'+r['id'])
        require(r['product_id'] in definition['products'],'UNSUPPORTED_CONTINUITY_PRODUCT:'+r['id'])
        require(type(r['priority']) is int,'INVALID_TYPED_CONTINUITY_ORDER:'+r['id']+':priority')
        result=dict(id=r['id']+'-DEMAND',order_ref=r['id'],customer_id=r['customer_id'],item_id=r['product_id'],
            location_id=operating['site_id'],quantity=number(total-shipped),uom=definition['items'][r['product_id']]['unit'],due_at=r['due'],priority=r['priority'],
            minimum_shipment=operating['demand']['minimum_shipment'],maximum_lateness_seconds=operating['demand']['maximum_lateness_seconds'],
            status=r['status'],source_record=copy.deepcopy(r),quantity_basis='SOURCE_GROSS_MINUS_SHIPPED')
        DOMAIN['demand_lines'].model_validate(result)
        return result
    except (KeyError,TypeError,ArithmeticError,ValidationError) as error:
        raise ValueError('INVALID_TYPED_CONTINUITY_ORDER:'+str(r.get('id',''))) from error


def build_snapshot(*, definition, operating, reference, documents):
    require(definition['enterprise_id']==operating['enterprise_id']==ENTERPRISE, 'CONTINUITY_IDENTITY_MISMATCH')
    require(set(documents)=={'ERP','WMS','MES'}, 'CONTINUITY_SOURCES_REQUIRED')
    live_scope=_live_source_scope(documents)
    progress_policy=stage_policy(operating)
    year=_year_contract(definition,operating,reference)
    scope={d['as_of'] for d in documents.values()}
    require(len(scope)==1, 'SOURCE_SCOPE_MISMATCH'); planning=next(iter(scope)); start=at(planning)
    require(at(operating['known_from'])<=start<=at(definition['end']), 'CONTINUITY_DECLARATION_NOT_EFFECTIVE')
    for system, doc in documents.items():
        require(doc['system']==system and doc['enterprise_id']==ENTERPRISE and doc['mode']=='FULL_SNAPSHOT', 'SOURCE_IDENTITY_MISMATCH')
        require(not doc.get('unresolved_events'), 'SOURCE_DEPENDENCY_UNRESOLVED:'+system)
        for rows in doc['tables'].values():
            require(len({r['id'] for r in rows})==len(rows),'SOURCE_DUPLICATE_BUSINESS_ID:'+system)
            for row in rows:
                require(at(row['source_known_at'])<=start and at(row['business_occurred_at'])<=at(row['source_known_at']), 'SOURCE_FUTURE_KNOWLEDGE:'+system+':'+row['id'])
    require(operating['inventory']['historical_receipts_included_in_current_snapshot'] is True and
            operating['inventory']['physical_balances_are_current_after_receipts_and_consumption'] is True,
            'CONTINUITY_CURRENT_INVENTORY_CONTRACT_REQUIRED')
    end=operating['supply']['capacity_period']['end'];site=operating['site_id']; dataset=ENTERPRISE+'-V1'
    if year:
        end=min(start+timedelta(days=year['planning_lookahead_days']),at(end)).isoformat()
        require(start<at(end),'YEAR_PLANNING_RANGE_INVALID')
        for q in operating['research_qualifications']+operating['research_machine_qualifications']:
            require(at(q.get('known_from',operating['known_from']))<=start,'YEAR_RESEARCH_QUALIFICATION_FUTURE_KNOWLEDGE')
    from p21.canonical import DOMAIN
    tables={k:[] for k in DOMAIN}
    role_ref=declaration(reference)
    bundle=dict(definition=copy.deepcopy(definition), operating=copy.deepcopy(operating),
                reference={'tables':role_ref}, documents=copy.deepcopy(documents))
    source_hashes={}
    def row(value, system='DECLARATION'):
        source=system if system in documents else 'DECLARATION'
        if source not in source_hashes:
            source_hashes[source]=digest(documents[system] if system in documents else definition)
        return dict(dataset_id=dataset,source_id=system,source_sha256=source_hashes[source],
                    source_section=system,entity_id=ENTERPRISE+'/'+value['id']) | value
    tables['locations']=[row(dict(id=site,name='连续经营研究工厂',timezone=definition['timezone'],continuity_bundle=bundle))]
    ids={r['id']:r for r in role_ref['items']}
    tables['items']=[row(dict(id=i,kind=ids[i]['kind'],base_uom=v['unit'])) for i,v in definition['items'].items()]
    erp,wms,mes=(documents[s]['tables'] for s in ['ERP','WMS','MES'])
    lots={r['id']:r for r in wms.get('lots',[])}
    required_materials={iid for p in definition['products'].values() for iid in p['bom']}
    require(required_materials<={r['item_id'] for r in lots.values()},'CONTINUITY_REQUIRED_STOCK_UNKNOWN')
    orders={r['id']:r for r in erp.get('sales_orders',[])}
    tasks={r['id']:r for r in mes.get('manufacturing_tasks',[])}
    for k in ['operations','material_issues']:
        for r in mes.get(k,[]):
            require(r.get('job_id') in tasks,'CONTINUITY_MES_ORPHAN:'+r['id'])
    for c in sorted({r['customer_id'] for r in orders.values()}):tables['customers'].append(row(dict(id=c),'ERP'))
    for r in orders.values():
        tables['sales_orders'].append(row(copy.deepcopy(r),'ERP'))
        demand=order_demand(r,definition,operating)
        if not demand['quantity']:continue
        tables['demand_lines'].append(row(demand,'ERP'))
    for position in inventory_facts(wms,mes,definition,operating,planning):
        tables['inventory_positions'].append(row(position,'WMS'))
    # Field authority is conditional on stable PO/item/unit/lot linkage and exact receipt identities.
    po_index={r['id']:r for r in erp.get('purchase_orders',[])}
    w_receipts={r['id']:r for r in wms.get('receipts',[])}
    for r in w_receipts.values():
        p=po_index.get(r['po_id']);lot=lots.get(r['lot_id'])
        require(p is not None and lot is not None and (r['item_id'],r['unit'])==(p['item_id'],p['unit']) and
                (lot['po_id'],lot['item_id'],lot['unit'])==(r['po_id'],r['item_id'],r['unit']), 'CONTINUITY_RECEIPT_LINK_CONFLICT:'+r['id'])
    for r in erp.get('purchase_receipts',[]):
        w=w_receipts.get(r['id'])
        require(w is not None and all(r[k]==w[k] for k in ['po_id','lot_id','item_id','quantity','unit']), 'CONTINUITY_ERP_WMS_RECEIPT_CONFLICT:'+r['id'])
    sp=operating['supply'];sources={}
    for p in po_index.values():
        received=sum((Decimal(r['quantity']) for r in w_receipts.values() if r['po_id']==p['id']),Decimal(0))
        erp_received=sum((Decimal(r['quantity']) for r in erp.get('purchase_receipts',[]) if r['po_id']==p['id']),Decimal(0))
        require(erp_received==Decimal(p['received']) and Decimal(p['quantity'])-erp_received==Decimal(p['outstanding']), 'CONTINUITY_ERP_PO_BALANCE_CONFLICT:'+p['id'])
        remaining=Decimal(p['quantity'])-received;require(remaining>=0 and received>=erp_received,'CONTINUITY_PO_RECEIPT_CONFLICT:'+p['id'])
        sid='CONT-SRC-'+p['item_id']+'-'+p['supplier_id']
        sources[sid]=row(dict(id=sid,item_id=p['item_id'],location_id=site,supplier_id=p['supplier_id'],uom=p['unit'],
            minimum_quantity=sp['minimum_order_quantity'],order_multiple=sp['order_multiple'],lead_time_seconds=sp['lead_time_seconds'],qc_wait_seconds=sp['qc_wait_seconds'],
            horizon_capacity=sp['horizon_capacity'],capacity_includes_confirmed=True,capacity_period={'start':planning,'end':end},effective_from=planning,effective_to=end,
            priority=1,unit_price=None,currency=None,economic_terms_status='UNKNOWN_NOT_FILLED'))
        if not remaining:continue
        require(at(p['eta'])>=start,'CONTINUITY_PAST_DUE_UNRECEIVED_ETA_REQUIRES_REVIEW:'+p['id'])
        tables['purchase_orders'].append(row(dict(id=p['id'],reference=p['po_id'],item_id=p['item_id'],location_id=site,source_id=sid,
            quantity=number(p['quantity']),received_quantity=number(received),cancelled_quantity=0,unreceived_quantity=number(remaining),uom=p['unit'],
            physical_receipt_at=p['eta'],available_at=(at(p['eta'])+timedelta(seconds=sp['qc_wait_seconds'])).isoformat(),status='confirmed',
            included_in_opening=False,ordered_at=None,planned_start_at=planning,source_record=copy.deepcopy(p),
            receipt_treatment='HISTORICAL_RECEIVED_ALREADY_IN_CURRENT_WMS; ONLY_UNRECEIVED_IS_FUTURE',wms_receipt_ids=[r['id'] for r in w_receipts.values() if r['po_id']==p['id']]),'ERP'))
    tables['supply_sources']=list(sources.values())
    for s in tables['supply_sources']:
        prior=sum((Decimal(r['quantity']) for r in w_receipts.values() if r['item_id']==s['item_id'] and po_index[r['po_id']]['supplier_id']==s['supplier_id']),Decimal(0))
        remaining=Decimal(str(sp['horizon_capacity']))-(Decimal(0) if year else prior)
        require(remaining>0,'CONTINUITY_SUPPLIER_CAPACITY_EXHAUSTED:'+s['id'])
        s.update(horizon_capacity=number(remaining),whole_period_capacity=sp['horizon_capacity'],historical_received_quantity=number(prior),
                 capacity_accounting='DECLARED_ROLLING_FUTURE_RECEIPTS_CONFIRMED_INCLUDED_NO_HISTORICAL_DEBIT' if year else 'DECLARED_EIGHT_WEEK_TOTAL_LESS_ACTUAL_WMS_RECEIPTS')
    tables['suppliers']=[row(dict(id=s)) for s in sorted({p['supplier_id'] for p in po_index.values()})]
    # Research calendars are newly declared; inherited calendar dates are not extended.
    intervals=[];day=start.replace(hour=0,minute=0,second=0,microsecond=0)
    while day<at(end):
        if day.weekday() in definition['workday']['weekdays']:
            for a,b in definition['workday']['segments']:
                x,y=day.replace(hour=a),day.replace(hour=b)
                if max(x,start)<min(y,at(end)):intervals.append({'start_at':max(x,start).isoformat(),'end_at':min(y,at(end)).isoformat()})
        day+=timedelta(days=1)
    tables['calendars']=[row(dict(id='CAL-FACTORY',timezone=definition['timezone'],available_intervals=intervals))]
    for rid in list(definition['resources'])+list(definition['operators']):
        selected=copy.deepcopy(intervals)
        if year and rid in definition['resources']:
            # Fixed machines have no native machine-skill pool. A shared machine
            # calendar conservatively requires a grant for every declared use.
            for op,spec in definition['operations'].items():
                if rid not in spec.get('resource_ids',[spec['resource']]):continue
                grants=[q for q in operating['research_machine_qualifications'] if q['operation_id']==op and q['resource_id']==rid]
                pieces=[]
                for i in selected:
                    for q in grants:
                        a=max(at(i['start_at']),at(q['effective_from']));b=min(at(i['end_at']),at(q['effective_to']))
                        if a<b:pieces.append({'start_at':a.isoformat(),'end_at':b.isoformat()})
                selected=pieces
        for down in mes.get('resource_unavailability',[]):
            if down['resource_id']!=rid:continue
            pieces=[]
            for i in selected:
                a,b=at(i['start_at']),at(i['end_at']);x,y=at(down['start']),at(down['end'])
                if y<=a or b<=x:pieces.append(i);continue
                if a<x:pieces.append({'start_at':a.isoformat(),'end_at':x.isoformat()})
                if y<b:pieces.append({'start_at':y.isoformat(),'end_at':b.isoformat()})
            selected=pieces
        cal='CONT-CAL:'+rid;tables['calendars'].append(row(dict(id=cal,timezone=definition['timezone'],available_intervals=selected)))
        if rid in definition['resources']:
            tables['resources'].append(row(dict(id=rid,location_id=site,calendar_id=cal,role='MACHINE',capacity_model='WINDOW_QUANTITY' if rid=='CUT-01' else 'CONTINUOUS',
                parallel_units=1,initial_setup='CONT:NEUTRAL',efficiency_percent=100)))
        else:tables['operators'].append(row(dict(id=rid,location_id=site,calendar_id=cal,capacity=1,efficiency_percent=100)))
        if rid=='CUT-01':
            for i in selected:
                a,b=at(i['start_at']),at(i['end_at'])
                while a+timedelta(hours=1)<=b:
                    tables['capacity_windows'].append(row(dict(id='CONT-CUT:'+a.isoformat(),resource_id=rid,start_at=a.isoformat(),end_at=(a+timedelta(hours=1)).isoformat(),capacity_quantity=20,uom='件')))
                    a+=timedelta(hours=1)
    tables['setup_transitions']=[row(dict(id='CONT:NEUTRAL->CONT:NEUTRAL',from_state='CONT:NEUTRAL',to_state='CONT:NEUTRAL',duration_seconds=0,occupies_machine=True))]
    reference_ops={o['id']:o for o in role_ref['operations']}
    used_skills=set()
    def recipe(product, operation_ids, *,job=None, wip=None, issued=None, progress=None):
        spec=definition['products'][product];rid='CONT-RECIPE:'+product if job is None else 'CONT-CONTINUE:'+job['id'];route='ROUTE:'+rid;bom='BOM:'+rid
        rec=dict(id=rid,output_item_id=product,bom_id=bom,routing_id=route,location_id=site,effective_from=planning,effective_to=end,
                 minimum_quantity=20,maximum_quantity=20,multiple=20)
        if job:
            q=progress['remaining_quantity']
            rec.update(continuity_job_id=job['id'],actual_order_id=job['order_id'],issued_materials=issued,
                       completed_prefix=[copy.deepcopy(o) for o in mes['operations'] if o['job_id']==job['id']])
            if progress['finished_received_quantity']:
                rec.update(minimum_quantity=q,maximum_quantity=q,multiple=q,**copy.deepcopy(progress),
                           progress_contract='closed-continuous-single-boundary-v1')
                rec['shipped_quantity']=number(sum((Decimal(m['quantity']) for m in wms.get('movements',[]) if
                    m.get('job_id')==job['id'] and m.get('kind')=='ShipmentPosted'),Decimal(0)))
        tables['recipes'].append(row(rec));tables['routings'].append(row(dict(id=route,recipe_id=rid,output_item_id=product,kind='SEQUENTIAL')))
        previous=None
        for seq,original in enumerate(operation_ids,1):
            op=definition['operations'][original];oid=original if job is None else job['id']+'::REMAIN:'+original
            skill=reference_ops[original]['required_operator_skill'];used_skills.add(skill)
            candidates=op.get('resource_ids',[op['resource']]) if progress_policy else [op['resource']]
            tables['operations'].append(row(dict(id=oid,actual_operation_id=original,recipe_id=rid,routing_id=route,sequence=seq,predecessor_id=previous,
                duration_model='FIXED_BATCH' if op['batch_quantity'] else 'PER_UNIT',fixed_seconds=number(Decimal(op['fixed_minutes'])*60),seconds_per_unit=number(Decimal(op['minutes_per_piece'])*60),
                batch_quantity=op['batch_quantity'],resource_ids=candidates,resources_are_alternatives=len(candidates)>1,machine_and_person_simultaneously_required=True,
                required_operator_skill=skill,produces_output=seq==len(operation_ids))))
            previous=oid
        first=operation_ids[0] if job is None else job['id']+'::REMAIN:'+operation_ids[0]
        for iid,q in ({wip:1} if wip else spec['bom']).items():
            tables['bom_lines'].append(row(dict(id=bom+':'+iid,recipe_id=rid,input_item_id=iid,output_item_id=product,input_uom='件' if wip else definition['items'][iid]['unit'],
                output_uom=definition['items'][product]['unit'],quantity_per_output=number(q),scrap_fraction=0,consume_operation_id=first,consume_at='START')))
    for product,spec in definition['products'].items():recipe(product,spec['routing'])
    tasks={r['id']:r for r in mes.get('manufacturing_tasks',[])}
    def stage_recipes(job,progress):
        issued=[copy.deepcopy(i) for i in mes.get('material_issues',[]) if i['job_id']==job['id']]
        totals=defaultdict(Decimal)
        for issue in issued:
            lot=lots.get(issue['lot_id']);require(lot is not None and issue['unit']==lot['unit'],'WIP_ISSUE_LOT_UNRESOLVED:'+issue['id'])
            totals[lot['item_id']]+=Decimal(issue['quantity'])
        expected={iid:Decimal(q)*Decimal(job['quantity']) for iid,q in definition['products'][job['product_id']]['bom'].items()}
        require(dict(totals)==expected,'WIP_ISSUED_MATERIAL_MISMATCH:'+job['id'])
        parts=progress['stage_partitions'];route=definition['products'][job['product_id']]['routing']
        for part in parts:
            wid=part['item_id'];tables['items'].append(row(dict(id=wid,kind='SEMI',base_uom='件',actual_job_id=job['id'])))
        for position in wip_inventory_facts(job,mes,definition,lots,site,planning,operating=operating,wms=wms):
            tables['inventory_positions'].append(row(position,'MES'))
        for index,part in enumerate(parts):
            original=route[index+1] if index<3 else None
            q=Decimal(job['quantity'])-Decimal(str(progress['completed_quantities'][original] if original else progress['finished_received_quantity']))
            if q==0:continue
            sid=part['stage_id'];rid='CONT-STAGE:'+job['id']+':'+sid;oid=job['id']+'::STAGE:'+sid
            rout='ROUTE:'+rid;bom='BOM:'+rid;out=parts[index+1]['item_id'] if index<3 else job['product_id']
            kind='STAGE_PROCESSING' if original else 'AVAILABILITY_TRANSFER'
            rec=dict(id=rid,output_item_id=out,bom_id=bom,routing_id=rout,location_id=site,effective_from=planning,effective_to=end,
                minimum_quantity=1,maximum_quantity=number(q),multiple=1,continuity_job_id=job['id'],actual_order_id=job['order_id'],
                original_product_id=job['product_id'],stage_id=sid,stage_input_item_id=part['item_id'],stage_output_item_id=out,
                continuation_kind=kind,issued_materials=copy.deepcopy(issued),**copy.deepcopy(progress))
            rec['remaining_quantity']=number(q)
            if not original:rec['receiving_policy']=copy.deepcopy(progress_policy['receiving'])
            tables['recipes'].append(row(rec));tables['routings'].append(row(dict(id=rout,recipe_id=rid,output_item_id=out,kind='SEQUENTIAL')))
            spec=definition['operations'][original] if original else None
            skill=reference_ops[original]['required_operator_skill'] if original else None
            if skill:used_skills.add(skill)
            candidates=spec.get('resource_ids',[spec['resource']]) if spec else []
            tables['operations'].append(row(dict(id=oid,actual_operation_id=original,recipe_id=rid,routing_id=rout,sequence=1,predecessor_id=None,
                duration_model='PER_UNIT' if original else 'AVAILABILITY_TRANSFER',fixed_seconds=0,
                seconds_per_unit=number(Decimal(spec['minutes_per_piece'])*60) if spec else 0,batch_quantity=None,
                resource_ids=candidates,resources_are_alternatives=len(candidates)>1,
                machine_and_person_simultaneously_required=bool(original),required_operator_skill=skill,produces_output=True,
                continuity_job_id=job['id'],stage_id=sid,continuation_kind=kind,progress_contract=STAGE_PROGRESS)))
            tables['bom_lines'].append(row(dict(id=bom+':'+part['item_id'],recipe_id=rid,input_item_id=part['item_id'],output_item_id=out,
                input_uom='件',output_uom='件',quantity_per_output=1,scrap_fraction=0,consume_operation_id=oid,consume_at='START')))
    for job in tasks.values():
        require(job['order_id'] in orders and orders[job['order_id']]['product_id']==job['product_id'], 'CONTINUITY_JOB_ORDER_CONFLICT:'+job['id'])
        actual=[o for o in mes.get('operations',[]) if o['job_id']==job['id']]
        if job['status'] in {'DRAFT','CONFIRMED'}:
            require(not actual and not any(i['job_id']==job['id'] for i in mes.get('material_issues',[])) and
                    Decimal(job['completed_quantity'])==0 and job.get('plan_operations') and
                    type(job.get('external_version')) is int,'CONTINUITY_FORMAL_MO_SOURCE_CONFLICT:'+job['id'])
            if job['status']=='DRAFT':continue
            ops=copy.deepcopy(job['plan_operations'])
            require([o['operation_id'] for o in ops]==definition['products'][job['product_id']]['routing'],
                    'CONTINUITY_FORMAL_MO_ROUTE_CONFLICT:'+job['id'])
            first=ops[0]
            tables['existing_manufacturing_orders'].append(row(dict(id=job['id'],reference=job['id'],
                recipe_id='CONT-RECIPE:'+job['product_id'],operation_id=first['operation_id'],resource_id=first['resource_id'],
                operator_id=first['operator_id'],quantity=job['quantity'],completed_quantity=0,start_at=first['start_at'],
                end_at=ops[-1]['end_at'],status='confirmed',protected=True,plan_operations=ops,
                external_document_id=job['id'],external_version=job['external_version'],source_record=copy.deepcopy(job)),'MES'))
            continue
        route=definition['products'][job['product_id']]['routing']
        for o in actual:
            declared=definition['operations'].get(o['operation_id'])
            operator_matches=(_year_actual_assignment_matches(o,definition,operating,reference_ops,start) if year else
                              _live_actual_operator_matches(o,definition,role_ref,reference_ops,start) if live_scope else
                              declared is not None and o['operator_id']==declared['operator'])
            require(declared is not None and o['operation_id'] in route and 0<Decimal(o['quantity'])<=Decimal(job['quantity']) and
                    o['resource_id'] in declared.get('resource_ids',[declared['resource']]) and operator_matches and at(o['start'])<at(o['end']) and
                    at(o.get('actual_end',o['end']) if progress_policy else o['end'])<=start,'CONTINUITY_ACTUAL_OPERATION_CONFLICT:'+o['id'])
        progress=job_progress(job,mes,definition,policy=progress_policy,wms=wms)
        if job['status']=='COMPLETED':
            continue
        if progress_policy:
            stage_recipes(job,progress)
            continue
        position=wip_inventory_fact(job,mes,definition,lots,site,planning)
        issued=[copy.deepcopy(i) for i in mes.get('material_issues',[]) if i['job_id']==job['id']]
        wid=position['item_id'];tables['items'].append(row(dict(id=wid,kind='SEMI',base_uom='件',actual_job_id=job['id'])))
        tables['inventory_positions'].append(row(position,'MES'))
        recipe(job['product_id'],progress['remaining_operation_ids'],job=job,wip=wid,issued=issued,progress=progress)
    tables['skills']=[row(copy.deepcopy(s)) for s in role_ref['skills'] if s['id'] in used_skills]
    for q in operating['research_qualifications'] if year else role_ref['resource_skills']:
        if q['resource_id'] in definition['operators'] and q['skill_id'] in used_skills:
            begin=max(start,at(q['effective_from'])) if year else start
            finish=min(at(end),at(q['effective_to'])) if year else at(end)
            if begin<finish:
                tables['resource_skills'].append(row(dict(id=q['id'],resource_id=q['resource_id'],skill_id=q['skill_id'],
                    effective_from=begin.isoformat() if year else planning,effective_to=finish.isoformat() if year else end)))
    if year and progress_policy:
        for grant in operating['research_machine_qualifications']:
            original=grant['operation_id'];spec=definition['operations'][original]
            if len(spec.get('resource_ids',[]))<2:continue
            begin=max(start,at(grant['effective_from']));finish=min(at(end),at(grant['effective_to']))
            if begin>=finish:continue
            tables['machine_qualifications'].append(row(dict(id=grant['id']+'::NATIVE',resource_id=grant['resource_id'],
                skill_id=reference_ops[original]['required_operator_skill'],effective_from=begin.isoformat(),effective_to=finish.isoformat(),
                actual_operation_id=original,source_grant_id=grant['id'],factual_status='DECLARED_RESEARCH_POLICY')))
    return dict(enterprise_id=ENTERPRISE,planning_at=planning,horizon={'start_at':planning,'end_at':end},timezone=definition['timezone'],tables=tables)
