"""Projection of an authenticated existing APS run into a sandbox action.

No solving, enterprise facts, approval store, or actual event generation here.
The simulator rechecks execution; a discussion note never authorizes adoption.
"""
import copy
import hashlib
import json
from datetime import datetime
from decimal import Decimal
from enterprise_sim.live import check_manufacture_release,check_continuation_release
from p22.professional_outcome import baseline_eligible


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def require(test,code):
    if not test:raise ValueError(code)


def instant(value):
    d=datetime.fromisoformat(value.replace('Z','+00:00'))
    require(d.utcoffset() is not None,'EXPLICIT_TIMEZONE_REQUIRED')
    return d


def manufacturing_action(data,result,selection,definition):
    roots=[r for r in result['manufacture_suggestions'] if r['id']==selection.get('manufacture_id')]
    require(len(roots)==1,'NATIVE_MANUFACTURE_NOT_FOUND')
    root=roots[0]
    require(root.get('origin')=='PROPOSED' and root.get('source_status')=='proposed','PROTECTED_ORDER_ADOPTION_FORBIDDEN')
    recipe=next((row for row in data['foundation_projection']['source_tables'].get('recipes',[]) if row['id']==root.get('recipe_id')),None)
    if recipe and recipe.get('progress_contract')=='stage-flow-v2':
        return stage_manufacturing_action(data,result,selection,definition,recipe)
    product=root['item_id'];require(product in definition['products'],'UNSUPPORTED_LIVE_PRODUCT')
    continuing=bool(recipe and recipe.get('continuity_job_id'))
    fields=['continuity_job_id','actual_order_id','original_job_quantity','completed_quantities','finished_received_quantity',
        'shipped_quantity','remaining_quantity','remaining_from_operation_id','remaining_operation_ids','issued_materials','source_refs']
    if continuing:
        require(all(key in recipe for key in fields),'CONTINUATION_SOURCE_EVIDENCE_REQUIRED')
    quantity=Decimal(str(recipe['remaining_quantity'])) if continuing else Decimal(20)
    require(Decimal(str(root['quantity']))==quantity and quantity>0 and root['uom']=='件','CONTINUATION_QUANTITY_CONFLICT' if continuing else 'FIXED_20_PIECE_TASK_REQUIRED')
    if continuing:
        require(recipe['id']=='CONT-CONTINUE:'+recipe['continuity_job_id'] and recipe['actual_order_id']==selection['order_id'], 'CONTINUATION_JOB_IDENTITY_MISMATCH')
    leaves=[r for r in result['operations'] if r['manufacture_id']==root['id']]
    source={r['id']:r for r in data['foundation_projection']['source_tables']['operations']}
    route=recipe['remaining_operation_ids'] if continuing else definition['products'][product]['routing']
    by_source={}
    roles={r['id']:r.get('role') for r in data['resources']}
    ops=[];mapping=[]
    for row in leaves:
        require(row['operation_id'] in source,'NATIVE_OPERATION_SOURCE_MISSING')
        actual=source[row['operation_id']].get('actual_operation_id',row['operation_id'])
        require(actual not in by_source,'AMBIGUOUS_NATIVE_OPERATION')
        people=[load for load in result['resource_loads'] if load['operation_id']==row['id'] and roles.get(load['resource_id'])=='OPERATOR']
        require(len(people)==1,'EXACT_NATIVE_OPERATOR_REQUIRED')
        person=people[0]
        require(instant(person['start_at'])==instant(row['start_at']) and instant(person['end_at'])==instant(row['end_at']),
            'NATIVE_OPERATOR_INTERVAL_MISMATCH')
        require(Decimal(str(row['quantity']))==quantity,'NATIVE_BATCH_QUANTITY_MISMATCH')
        require(instant(row['end_at'])>instant(row['start_at']),'INVALID_PLANNED_INTERVAL')
        by_source[actual]=dict(operation_id=actual,resource_id=row['resource_id'],operator_id=person['resource_id'],
            start_at=row['start_at'],end_at=row['end_at'],quantity=str(quantity),uom='件')
        mapping.append(dict(native_operation_id=row['id'],source_operation_id=row['operation_id'],actual_operation_id=actual,
            operation_evidence_ids=row['evidence_ids'],operator_evidence_ids=person['evidence_ids']))
    require(set(by_source)==set(route) and len(leaves)==len(route),'COMPLETE_ORDERED_ROUTING_REQUIRED')
    ops=[by_source[k] for k in route]
    require(all(instant(a['end_at'])<=instant(b['start_at']) for a,b in zip(ops,ops[1:])),'NATIVE_PRECEDENCE_VIOLATION')
    result=dict(product_id=product,quantity=str(quantity),manufacture_id=root['id'],operations=ops,
        mapping=dict(kind='EXACT_NATIVE_WIP_CONTINUATION' if continuing else 'EXPLICIT_SANDBOX_ALLOCATION',native_manufacture_id=root['id'],engine_reference=root['engine_ref'],
            order_id=selection['order_id'],basis='Exact original job remainder from the authenticated continuation recipe.' if continuing else 'Operator explicitly allocates this exact same-product whole batch; native demand pegging is not inferred.',operations=mapping))
    if continuing:
        result.update(existing_job_id=recipe['continuity_job_id'],continuation={key:copy.deepcopy(recipe[key]) for key in fields})
        result['continuation']['recipe_id']=recipe['id']
    return result


def continuation_native_evidence(payload,result,audit,run_id):
    """Bind a continuation adoption to the original same-run audit's references."""
    if payload.get('continuation',{}).get('progress_contract')=='stage-flow-v2':
        return stage_native_evidence(payload,result,audit,run_id)
    continuity=audit.get('checks',{}).get('continuity',{})
    rows=[row for row in continuity.get('wip',[]) if row.get('job_id')==payload['existing_job_id']]
    require(audit.get('status')=='PASS' and audit.get('run_id')==run_id and
        continuity.get('status')=='PASS' and continuity.get('run_id')==run_id and len(rows)==1 and
        rows[0].get('continuation_certification')=='NATIVE_REMAINING_ROUTE_VERIFIED',
        'CERTIFIED_NATIVE_CONTINUATION_REQUIRED')
    row=rows[0]
    roots=[root for root in result['manufacture_suggestions'] if root['id']==payload['manufacture_id']]
    leaves=[op for op in result['operations'] if op['manufacture_id']==payload['manufacture_id']]
    references=sorted(op['id'].rsplit(':',1)[-1] for op in leaves)
    native=sorted(str(op['reference']) for op in row.get('native_operations',[]))
    require(len(roots)==1 and [str(roots[0]['engine_ref'])]==row.get('native_root_references') and references==native and
        Decimal(str(row.get('remaining_quantity',0)))==Decimal(payload['quantity']) and
        all(Decimal(str(op['quantity']))==Decimal(payload['quantity']) for op in row.get('native_operations',[])),
        'CONTINUATION_NATIVE_REFERENCE_MISMATCH')
    return dict(owner='p22.continuity_audit.audit',run_id=run_id,job_id=payload['existing_job_id'],
        native_root_reference=str(roots[0]['engine_ref']),native_operation_references=references,source_sha256=digest(row))


def stage_manufacturing_action(data,result,selection,definition,selected):
    """One existing job graph is adopted atomically, with per-native-leaf amounts."""
    source=data['foundation_projection']['source_tables'];jid=selected['continuity_job_id'];product=selected['original_product_id']
    require(product in definition['products'] and selected['actual_order_id']==selection['order_id'],'CONTINUATION_JOB_IDENTITY_MISMATCH')
    recipes={r['id']:r for r in source['recipes'] if r.get('progress_contract')=='stage-flow-v2' and r.get('continuity_job_id')==jid}
    source_ops={o['id']:o for o in source['operations']};roles={r['id']:r.get('role') for r in data['resources']}
    roots=[r for r in result['manufacture_suggestions'] if r['recipe_id'] in recipes]
    require(bool(roots) and all(r.get('origin')=='PROPOSED' and r.get('source_status')=='proposed' for r in roots),'PROTECTED_ORDER_ADOPTION_FORBIDDEN')
    root_ids={r['id'] for r in roots};operations=[];receipts=[];mapping=[]
    for r in recipes.values():
        require(sum((Decimal(str(x['quantity'])) for x in roots if x['recipe_id']==r['id']),Decimal(0))==Decimal(str(r['remaining_quantity'])),
                'CONTINUATION_STAGE_NATIVE_QUANTITY')
    for op in result['operations']:
        if op['manufacture_id'] not in root_ids:continue
        src=source_ops.get(op['operation_id']);require(src is not None and src['recipe_id'] in recipes,'NATIVE_OPERATION_SOURCE_MISSING')
        common=dict(quantity=str(op['quantity']),start_at=op['start_at'],end_at=op['end_at'],native_reference=op['id'].rsplit(':',1)[-1])
        if src['continuation_kind']=='AVAILABILITY_TRANSFER':
            require(instant(op['start_at'])==instant(op['end_at']),'CONTINUATION_RECEIPT_WINDOW_INVALID')
            receipts.append(common)
        else:
            people=[load for load in result['resource_loads'] if load['operation_id']==op['id'] and roles.get(load['resource_id'])=='OPERATOR']
            require(len(people)==1,'EXACT_NATIVE_OPERATOR_REQUIRED');person=people[0]
            require(instant(person['start_at'])==instant(op['start_at']) and instant(person['end_at'])==instant(op['end_at']),'NATIVE_OPERATOR_INTERVAL_MISMATCH')
            require(instant(op['start_at'])<instant(op['end_at']),'INVALID_PLANNED_INTERVAL')
            operations.append(dict(**common,operation_id=src['actual_operation_id'],resource_id=op['resource_id'],operator_id=person['resource_id'],uom='件'))
        mapping.append(dict(native_operation_id=op['id'],source_operation_id=op['operation_id'],actual_operation_id=src['actual_operation_id'],operation_evidence_ids=op['evidence_ids']))
    operations.sort(key=lambda o:(instant(o['start_at']),instant(o['end_at']),o['native_reference']))
    fields=['progress_contract','continuity_job_id','actual_order_id','original_job_quantity','completed_quantities',
        'finished_received_quantity','shipped_quantity','stage_partitions','actual_operation_records','actual_operation_ids','source_refs','issued_materials']
    meta={key:copy.deepcopy(selected[key]) for key in fields};meta['remaining_quantity']=selected['original_job_quantity']-selected['finished_received_quantity']
    meta['native_root_references']=sorted(str(r['engine_ref']) for r in roots)
    meta['stage_operations']=[dict(recipe_id=r['id'],actual_operation_id=next(o for o in source['operations'] if o['recipe_id']==r['id'])['actual_operation_id'],quantity=r['remaining_quantity']) for r in recipes.values()]
    return dict(product_id=product,quantity=str(meta['remaining_quantity']),manufacture_id=selection['manufacture_id'],
        existing_job_id=jid,continuation=meta,operations=operations,planned_receipts=receipts,
        mapping=dict(kind='EXACT_NATIVE_STAGE_GRAPH',order_id=selection['order_id'],operations=mapping))


def stage_native_evidence(payload,result,audit,run_id):
    continuity=audit.get('checks',{}).get('continuity',{})
    rows=[r for r in continuity.get('wip',[]) if r.get('job_id')==payload['existing_job_id']]
    require(audit.get('status')=='PASS' and audit.get('run_id')==run_id and len(rows)==1 and
        continuity.get('status')=='PASS' and continuity.get('run_id')==run_id and
        rows[0].get('continuation_certification')=='NATIVE_STAGE_FLOW_VERIFIED','CERTIFIED_NATIVE_CONTINUATION_REQUIRED')
    row=rows[0];expected=payload['continuation']['native_root_references']
    require(sorted(row.get('native_root_references',[]))==expected,'CONTINUATION_NATIVE_REFERENCE_MISMATCH')
    requested=payload['operations']+payload['planned_receipts']
    require(sorted((str(o['native_reference']),Decimal(str(o['quantity']))) for o in requested)==
        sorted((str(o['reference']),Decimal(str(o['quantity']))) for o in row['native_operations']),
        'CONTINUATION_NATIVE_REFERENCE_MISMATCH')
    return dict(owner='p22.continuity_audit.audit',run_id=run_id,job_id=payload['existing_job_id'],
        native_root_references=expected,native_operation_references=[o['native_reference'] for o in requested],source_sha256=digest(row))


def validate_draft_reschedule(job,payload,expected_version):
    """A new audited routing may replace only an unexecuted external draft.

    Confirmed source commitments remain protected by the existing Plan Editor.
    A target-side permission does not grant APS authority to move that source.
    """
    require(isinstance(job,dict) and job.get('status')=='DRAFT' and not job.get('protected') and not job.get('frozen') and
        not job.get('issued') and not job.get('completed') and Decimal(str(job.get('fg_received',0)))==0,
        'EXTERNAL_DRAFT_RESCHEDULE_REQUIRED')
    require(job.get('external_version')==expected_version,'EXTERNAL_DOCUMENT_VERSION_CONFLICT')
    require(job.get('order_id')==payload.get('order_id') and job.get('product_id')==payload.get('product_id') and
        Decimal(str(job.get('quantity')))==Decimal(str(payload.get('quantity'))) and not payload.get('continuation'),
        'EXTERNAL_DOCUMENT_SCOPE_MISMATCH')
    return job['job_id']


def adoption_preview(plans,branch,selection,head,*,reschedule_job_id=None,expected_document_version=None):
    pid=selection['plan_id'];target=selection['target']
    require(head['branch_id']==branch,'SIM_BRANCH_MISMATCH')
    record=plans.store.get(pid);snapshot=plans.store.snapshot(pid)
    version=plans.imports.version(record['data_version_id'])
    plans.imports.verify_version_sources(version)
    require(version==snapshot['data_version'],'VERSION_CHANGED')
    receipt=version['business_data'].get('source_sync_receipt',{})
    scope=receipt.get('source_scope',{})
    require(scope.get('branch_id')==branch and scope.get('connector_id')=='live-'+branch,'ADOPTION_SOURCE_BRANCH_MISMATCH')
    require(scope.get('source_revision')==head['revision'] and instant(receipt['as_of'])==instant(head['as_of']),
        'STALE_ADOPTION_EVIDENCE')
    require(head.get('current_version_id')==version['id'],'ADOPTION_REQUIRES_CURRENT_SOURCE_VERSION')
    s=plans.service(pid);s.drafts.verify_baseline()
    data=s.drafts.baseline if target=='baseline' else s.drafts.for_run(target)['input']
    view=s.selected_view(target)
    require(baseline_eligible(view),'AUDITED_NATIVE_RUN_REQUIRED')
    run=view['run'];result=view['result']
    require(result['solve_run_id']==run['solve_run_id'] and result['input_hash']==run['input_hash'],'NATIVE_RESULT_RUN_MISMATCH')
    require(data['scenario_id']==run['scenario_id'] and data['snapshot_id']==run['snapshot_id'],'NATIVE_INPUT_IDENTITY_MISMATCH')
    # Reuse the same canonical digest as the existing RunStore's input identity.
    require(digest(data)==run['input_hash'],'NATIVE_INPUT_HASH_MISMATCH')
    bundle=version['business_data']['canonical']['tables']['locations'][0]['continuity_bundle']
    definition=bundle['definition']
    order=head['orders'].get(selection['order_id'])
    require(order is not None,'LIVE_ORDER_NOT_FOUND')
    require(any(d.get('order_ref')==selection['order_id'] or d['id']==selection['order_id']
        for d in data['foundation_projection']['source_tables']['demand_lines']),'SOURCE_OPEN_ORDER_REQUIRED')
    payload=dict(enterprise_id=definition['enterprise_id'],branch_id=branch,plan_id=pid,data_version_id=version['id'],
        run_id=run['solve_run_id'],input_hash=run['input_hash'],world_revision=head['revision'],target=target,
        action_type=selection['action_type'],order_id=selection['order_id'],
        native_audit=dict(status='PASS',run_id=run['solve_run_id'],input_hash=run['input_hash'],
            result_hash=result['result_hash'],checks=view['interpretation']['foundation_validation']),
        source_evidence=dict(revision=head['revision'],as_of=head['as_of'],source_scope=scope,
            snapshot_sha256=record['snapshot_sha256']))
    if selection['action_type']=='MANUFACTURE':
        payload.update(manufacturing_action(data,result,selection,definition))
        require(order['product_id']==payload['product_id'],'ADOPTION_PRODUCT_MISMATCH')
        if reschedule_job_id:
            require(not payload.get('existing_job_id'),'WIP_REQUIRES_CERTIFIED_CONTINUATION')
            job=head.get('jobs',{}).get(reschedule_job_id)
            payload.update(existing_job_id=validate_draft_reschedule(job,payload,expected_document_version),
                expected_document_version=expected_document_version)
        elif payload.get('existing_job_id'):
            payload['continuation_evidence']=continuation_native_evidence(payload,result,view['interpretation']['foundation_validation'],run['solve_run_id'])
            check_continuation_release(head,head['actions'],payload,definition)
        else:
            check_manufacture_release(head,head['actions'],payload)
    elif selection['action_type']=='PURCHASE':
        rows=[r for r in result['purchase_suggestions'] if r.get('id')==selection.get('purchase_reference')]
        require(len(rows)==1,'NATIVE_PURCHASE_NOT_FOUND')
        row=rows[0];q=Decimal(str(row['quantity']))
        require(q>=100 and q%100==0,'PURCHASE_POLICY_100_MULTIPLE_REQUIRED')
        require(isinstance(row.get('uom'),str) and bool(row['uom'].strip()),'NATIVE_PURCHASE_UOM_REQUIRED')
        payload.update(item_id=row['item_id'],quantity=str(q),uom=row['uom'],supplier_id=row['supplier_id'],
            expected_receipt_at=row['receipt_at'],purchase_reference=row['id'],
            mapping=dict(kind='EXACT_NATIVE_PURCHASE',native_purchase=copy.deepcopy(row)))
    else:raise ValueError('SIM_ACTION_UNSUPPORTED')
    contract=head.get('execution_contract')
    if contract:
        require(contract.get('reference_roles_sha256')==digest(bundle['reference']['tables']) and contract['definition_sha256']==digest(definition),
            'LIVE_EXECUTION_CONTRACT_SOURCE_MISMATCH')
        payload['execution_contract']=copy.deepcopy(contract)
    limits=['研究沙箱采纳，不是正式企业执行授权。','计划开始时间是最早请求；实际工时、资格、日历、物料、QC及前置工序由模拟企业再次核验。',
        '已有工单续排按精确剩余任务关联；新工单按同产品20件整批分配，不推断原生pegging。']
    return dict(branch_id=branch,expected_revision=head['revision'],selection=copy.deepcopy(selection),payload=payload,
        fingerprint=digest(dict(selection=selection,payload=payload)),limits=limits,
        summary=dict(order_id=selection['order_id'],run_id=run['solve_run_id'],quantity=payload['quantity'],
            action_type=selection['action_type'],execution_authorized=False))
