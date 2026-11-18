"""Additional same-run current-state/WIP audit; never substitutes base audits."""
from collections import Counter
from decimal import Decimal
from datetime import datetime
from zoneinfo import ZoneInfo

from p22.foundation_audit import table,status,quantity,numeric


def audit(data,raw,run_id):
    if data.get('foundation_projection',{}).get('continuity_scope',{}).get('stage_graph'):
        return audit_stages(data,raw,run_id)
    issues=[];missing=[];wip_results=[]
    def check(ok,code,reference=None,**detail):
        if not ok:issues.append(dict(code=code,reference=reference,**detail))
    try:
        from p22.continuity_projection import validate
        validate(data)
    except Exception as error:
        issues.append(dict(code='CONTINUITY_INPUT_REBUILD_FAILED',reason=str(error)))
    meta=data['foundation_projection'];source=meta['source_tables'];scope=meta['continuity_scope']
    docs=source['locations'][0]['continuity_bundle']['documents']
    check(scope['planning_time']==data['planning_at'] and all(d['as_of']==data['planning_at'] for d in docs.values()),'CONTINUITY_CLOCK_MISMATCH')
    check(data.get('purchase_receipts',[])==[],'CONTINUITY_HISTORICAL_RECEIPT_REPLAY')
    receipts={r['id'] for r in docs['WMS']['tables'].get('receipts',[])}
    pos=table(raw.get('purchaseorder'));flows=table(raw.get('operationplanmaterial'))
    if pos is None:missing.append('raw.purchaseorder')
    if flows is None:missing.append('raw.operationplanmaterial')
    if pos is not None:
        check(not receipts.intersection(str(p['reference']) for p in pos),'CONTINUITY_HISTORICAL_RECEIPT_REPLAY')
    expected=Counter((k,r['id']) for k,rows in source.items() for r in rows)
    check(expected==Counter((r['table'],r['id']) for r in meta['row_receipts']),'CONTINUITY_SOURCE_ROW_RECEIPT_SCOPE')
    for phase in ['pre','post']:
        snap=raw.get('fixed_batch_'+phase)
        if not isinstance(snap,dict):missing.append('raw.fixed_batch_'+phase);continue
        check(snap.get('run_id')==run_id,'CONTINUITY_RUN_MISMATCH',phase)
        buffers=table(snap.get('buffers'))
        if buffers is None:missing.append('raw.fixed_batch_'+phase+'.buffers');continue
        for w in scope['wip_jobs']:
            iid=w['wip_item_id'];bs=[b for b in buffers if (b['item'],b['location'])==(iid,data['site']['id'])]
            check(len(bs)==1 and quantity(bs[0]['onhand'])==quantity(w['quantity']),'CONTINUITY_WIP_OPENING_ONCE',w['job_id'],phase=phase)
            if phase!='post' or len(bs)!=1:continue
            events=table(bs[0].get('events'))
            if events is None:missing.append('native_post.wip.'+w['job_id']+'.events');continue
            future=[e for e in events if str(e['at'])[:4]!='1971']
            check(all(quantity(e['quantity'])<=0 for e in future),'CONTINUITY_WIP_REGENERATED',w['job_id'])
            used=-sum((quantity(e['quantity']) for e in future),Decimal(0));remaining=quantity(w['quantity'])-used
            check(0<=remaining<=quantity(w['quantity']),'CONTINUITY_WIP_CONSERVATION',w['job_id'])
            if events:check(quantity(events[-1]['onhand'])==remaining,'CONTINUITY_WIP_REMAINING_BALANCE',w['job_id'])
            wip_results.append(dict(job_id=w['job_id'],order_id=w['order_id'],opening=numeric(w['quantity']),used=numeric(used),remaining=numeric(remaining),
                                    issued_materials=w['issued_materials'],completed_prefix=w['completed_prefix'],
                                    **{k:w.get(k) for k in ('recipe_id','original_job_quantity','completed_quantities',
                                       'finished_received_quantity','shipped_quantity','remaining_quantity',
                                       'remaining_from_operation_id','remaining_operation_ids','actual_operation_records',
                                       'actual_operation_ids','source_refs')},
                                    continuation_certification='NOT_CERTIFIED',native_operations=[]))
    if flows is not None:
        by_ref={str(r['reference']):r for key in ['manufacturingorder','workorder'] for r in table(raw.get(key)) or []}
        for w in scope['wip_jobs']:
            continuations={ref for ref,r in by_ref.items() if r.get('operation','').startswith(w['recipe_id']+':')}
            inputs=[f for f in flows if str(f['operationplan']) in continuations and quantity(f['quantity'])<0]
            check(all(f['item']==w['wip_item_id'] for f in inputs),'CONTINUITY_WIP_RAW_CONSUMED_AGAIN',w['job_id'])
            recipe=next(r for r in source['recipes'] if r['id']==w['recipe_id'])
            completed={o['operation_id'] for o in recipe['completed_prefix']}
            check(not any(any(by_ref[r].get('operation','').endswith(':'+op) for op in completed) for r in continuations),'CONTINUITY_COMPLETED_CUT_REPEATED',w['job_id'])
            observed=next((r for r in wip_results if r['job_id']==w['job_id']),None)
            if observed is None or quantity(observed['used'])==0 or not w.get('remaining_operation_ids'):continue
            before=len(issues);q=quantity(w['quantity'])
            roots=[r for r in by_ref.values() if r.get('operation')==w['recipe_id'] and r.get('owner') is None]
            leaves=[r for ref,r in by_ref.items() if ref in continuations]
            expected={w['recipe_id']+':'+w['job_id']+'::REMAIN:'+op for op in w['remaining_operation_ids']}
            check(len(roots)==1 and quantity(roots[0]['quantity'])==q,'CONTINUITY_REMAINING_ROOT_QUANTITY',w['job_id'])
            check({r.get('operation') for r in leaves}==expected and len(leaves)==len(expected) and
                  all(quantity(r['quantity'])==q for r in leaves),'CONTINUITY_REMAINING_ROUTE_QUANTITY',w['job_id'])
            check(quantity(observed['used'])==q and quantity(observed['remaining'])==0,
                  'CONTINUITY_REMAINING_NOT_FULLY_PLANNED',w['job_id'])
            outputs=[f for f in flows if str(f['operationplan']) in continuations and quantity(f['quantity'])>0]
            check(len(outputs)==1 and outputs[0]['item']==recipe['output_item_id'] and quantity(outputs[0]['quantity'])==q,
                  'CONTINUITY_REMAINING_FG_OUTPUT',w['job_id'])
            check(sum((-quantity(f['quantity']) for f in inputs if f['item']==w['wip_item_id']),Decimal(0))==q,
                  'CONTINUITY_REMAINING_WIP_INPUT',w['job_id'])
            observed['native_operations']=[dict(reference=str(r['reference']),operation=r['operation'],quantity=r['quantity'],
                start_at=r.get('startdate'),end_at=r.get('enddate'),resource_id=r.get('resource')) for r in leaves]
            observed['native_root_references']=[str(r['reference']) for r in roots]
            if len(issues)==before:observed['continuation_certification']='NATIVE_REMAINING_ROUTE_VERIFIED'
    # This field is owned by the simulator's task dispatch gate. It was never
    # mapped to a native order-specific earliest-production constraint. Report
    # the actual advice and its execution limit without claiming that support.
    release_gates=[];zone=ZoneInfo(data['site']['timezone'])
    def aware(value):
        value=datetime.fromisoformat(value.replace('Z','+00:00'))
        return value.replace(tzinfo=zone) if value.tzinfo is None else value.astimezone(zone)
    recipes={r['id']:r for r in source['recipes']}
    mos=table(raw.get('manufacturingorder'))
    for demand in source['demand_lines']:
        original=demand.get('source_record',{});release=original.get('release_not_before')
        if not release:continue
        same_item=[d for d in source['demand_lines'] if d['item_id']==demand['item_id']]
        candidates=[m for m in mos or [] if m.get('owner') is None and m.get('status')=='proposed' and
                    recipes.get(m.get('operation'),{}).get('output_item_id')==demand['item_id']]
        exact=[m for m in candidates if m.get('demand')==demand['id'] or
               (isinstance(m.get('plan'),dict) and quantity(m['plan'].get('pegging',{}).get(demand['id'],0))>0)]
        selected=exact if exact else candidates
        basis='CAPTURED_NATIVE_DEMAND_PEGGING' if exact else 'ONLY_OPEN_DEMAND_FOR_ITEM' if len(same_item)==1 else 'SAME_ITEM_CANDIDATES_ALLOCATION_NOT_CAPTURED'
        first=min((aware(m['startdate']) for m in selected if m.get('startdate')),default=None)
        review=('NON_EXECUTABLE_BEFORE_SIMULATOR_RELEASE' if first<aware(release) else 'OBSERVED_ADVICE_AFTER_RELEASE_NOT_NATIVE_ENFORCEMENT') if first else 'NO_PRODUCTION_PROPOSAL_CAPTURED'
        release_gates.append(dict(order_id=original['id'],demand_id=demand['id'],source_release_not_before=release,
            business_owner='SIMULATOR_TASK_DISPATCH',native_hard_constraint_supported=False,
            execution_review_status=review,matching_basis=basis,earliest_candidate_start=first.isoformat() if first else None,
            candidate_native_refs=[str(m['reference']) for m in selected],execution_authorized=False))
    return dict(status=status(issues,missing),scope='current_balance_history_and_remaining_wip_same_run',issues=issues,
                missing_evidence=sorted(set(missing)),run_id=run_id,wip=wip_results,
                source_as_of=data['planning_at'],release_gates=release_gates,execution_authorized=False,
                limits=['release_not_before is enforced by simulator task dispatch, not by a native order-specific earliest-production model.',
                        'Same-item candidates without native demand pegging are not asserted as an exact order allocation.'])


def audit_stages(data,raw,run_id):
    """Reconcile captured native stage timelines against sealed visible sources."""
    issues=[];missing=[];results=[]
    def check(ok,code,reference=None,**detail):
        if not ok:issues.append(dict(code=code,reference=reference,**detail))
    try:
        from p22.continuity_projection import validate
        validate(data)
    except (ValueError,KeyError,TypeError) as error:
        issues.append(dict(code='CONTINUITY_INPUT_REBUILD_FAILED',reason=str(error)))
    meta=data['foundation_projection'];scope=meta['continuity_scope'];source=meta['source_tables']
    docs=source['locations'][0]['continuity_bundle']['documents']
    check(scope['planning_time']==data['planning_at'] and all(d['as_of']==data['planning_at'] for d in docs.values()),'CONTINUITY_CLOCK_MISMATCH')
    check(data.get('purchase_receipts',[])==[],'CONTINUITY_HISTORICAL_RECEIPT_REPLAY')
    check(Counter((k,r['id']) for k,rows in source.items() for r in rows)==
          Counter((r['table'],r['id']) for r in meta['row_receipts']),'CONTINUITY_SOURCE_ROW_RECEIPT_SCOPE')
    flows=table(raw.get('operationplanmaterial'));orders={}
    if flows is None:missing.append('raw.operationplanmaterial')
    for kind in ['manufacturingorder','workorder']:
        rows=table(raw.get(kind))
        if rows is None:missing.append('raw.'+kind)
        else:orders.update({str(r['reference']):r for r in rows})
    for job in scope['wip_jobs']:
        before=len(issues);parts=[];native=[];expected_ops=job['stage_operations'];job_flows=[]
        for phase in ['pre','post']:
            snap=raw.get('fixed_batch_'+phase)
            if not isinstance(snap,dict):missing.append('raw.fixed_batch_'+phase);continue
            check(snap.get('run_id')==run_id,'CONTINUITY_RUN_MISMATCH',phase)
            buffers=table(snap.get('buffers'))
            if buffers is None:missing.append('raw.fixed_batch_'+phase+'.buffers');continue
            for part in job['stage_partitions']:
                selected=[b for b in buffers if (b['item'],b['location'])==(part['item_id'],data['site']['id'])]
                check(len(selected)==1 and quantity(selected[0]['onhand'])==quantity(part['quantity']),
                      'CONTINUITY_WIP_OPENING_ONCE',part['item_id'],phase=phase)
                if phase=='post' and len(selected)==1:
                    events=table(selected[0].get('events'))
                    if events is None:missing.append('native_post.stage.'+part['item_id']+'.events');continue
                    future=[e for e in events if str(e['at'])[:4]!='1971']
                    inbound=sum((quantity(e['quantity']) for e in future if quantity(e['quantity'])>0),Decimal(0))
                    used=-sum((quantity(e['quantity']) for e in future if quantity(e['quantity'])<0),Decimal(0))
                    remain=quantity(part['quantity'])+inbound-used
                    check(remain>=0,'CONTINUITY_STAGE_CONSERVATION',part['item_id'])
                    if events:check(quantity(events[-1]['onhand'])==remain,'CONTINUITY_STAGE_REMAINING_BALANCE',part['item_id'])
                    parts.append(dict(**part,inbound=numeric(inbound),used=numeric(used),remaining=numeric(remain)))
        receipt_total=Decimal(0)
        for expected in expected_ops:
            refs={ref for ref,row in orders.items() if row.get('operation')==expected['recipe_id']}
            observed=[orders[ref] for ref in refs]
            total=sum((quantity(r['quantity']) for r in observed),Decimal(0))
            check(total==quantity(expected['quantity']),'CONTINUITY_STAGE_OPERATION_QUANTITY',expected['operation_id'],expected=expected['quantity'],observed=numeric(total))
            these=[f for f in flows or [] if str(f['operationplan']) in refs];job_flows.extend(these)
            consumes=[f for f in these if quantity(f['quantity'])<0];produces=[f for f in these if quantity(f['quantity'])>0]
            check(all(f['item']==expected['input_item_id'] for f in consumes) and
                  -sum((quantity(f['quantity']) for f in consumes),Decimal(0))==total,
                  'CONTINUITY_STAGE_NATIVE_INPUT',expected['recipe_id'])
            check(all(f['item']==expected['output_item_id'] for f in produces) and
                  sum((quantity(f['quantity']) for f in produces),Decimal(0))==total,
                  'CONTINUITY_STAGE_NATIVE_OUTPUT',expected['recipe_id'])
            if expected['continuation_kind']=='AVAILABILITY_TRANSFER':receipt_total+=total
            for r in observed:
                native.append(dict(reference=str(r['reference']),operation=r['operation'],operation_id=expected['operation_id'],
                    actual_operation_id=expected['actual_operation_id'],stage_id=expected['stage_id'],quantity=r['quantity'],
                    continuation_kind=expected['continuation_kind'],start_at=r.get('startdate'),end_at=r.get('enddate'),resource_id=r.get('resource')))
        check(all(f['item'].startswith('CONT-WIP:'+job['job_id']+':') or quantity(f['quantity'])>0 for f in job_flows),
              'CONTINUITY_WIP_RAW_CONSUMED_AGAIN',job['job_id'])
        remaining=quantity(job['quantity'])-receipt_total
        check(remaining>=0,'CONTINUITY_RECEIPT_EXCEEDS_JOB',job['job_id'])
        results.append(dict(**job,opening=job['quantity'],used=numeric(receipt_total),remaining=numeric(remaining),
            stage_balances=parts,native_operations=native,native_root_references=[n['reference'] for n in native],
            planned_receipt_quantity=numeric(receipt_total),actual_receipt_quantity=job['finished_received_quantity'],
            continuation_certification='NATIVE_STAGE_FLOW_VERIFIED' if len(issues)==before and not missing else 'NOT_CERTIFIED'))
    return dict(status=status(issues,missing),scope='job_private_native_stage_flow_same_run',issues=issues,
        missing_evidence=sorted(set(missing)),run_id=run_id,wip=results,source_as_of=data['planning_at'],release_gates=[],execution_authorized=False,
        limits=['Planned availability transfers are not actual finished-goods receipts.'])
