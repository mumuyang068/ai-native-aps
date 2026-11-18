"""Bind required input loads and observe the 9.19 combined native model.

Fields follow pinned include/frepple/model.h registerFields and the existing
qualified/confirmed-supply observers. Missing properties stay missing.
"""
from p3_fixed_batch.wip import seconds
from datetime import datetime
from decimal import Decimal


def setup_only_pending(expected, observed):
    """Recognize only a continuous-window native setup expansion, never a PASS.

    The immutable imported source window remains the final hard commitment.
    This deliberately refuses calendar-break envelopes and unexplained drift.
    The final native setup sequence and source window must both be audited.
    """
    try:
        if any(observed[k] != expected[k] for k in ('reference','operation','status')):
            return False
        if Decimal(observed['quantity']) != Decimal(expected['quantity']):
            return False
        if expected['resource'] not in observed['resources']:
            return False
        start,end,setup_end=(datetime.fromisoformat(observed[k]) for k in ('start','end','setup_end'))
        source_start,source_end=(datetime.fromisoformat(expected[k]) for k in ('start','end'))
        setup=Decimal(str(observed['setup_duration_seconds']))
        return bool(setup.is_finite() and setup > 0 and start == source_start and
            Decimal(str((setup_end-start).total_seconds())) == setup and
            Decimal(str((end-source_end).total_seconds())) == setup and
            end-setup_end == source_end-source_start)
    except (KeyError,TypeError,ValueError,ArithmeticError):
        return False


def native_state(frepple,p):
    assigned=sorted({r.name for r in frepple.resources() for l in r.loadplans if l.operationplan==p})
    return dict(reference=p.reference,operation=p.operation.name,quantity=str(p.quantity),status=p.status,
        start=p.start.isoformat(),end=p.end.isoformat(),resources=assigned,
        setup_end=p.setupend.isoformat(),setup_duration_seconds=seconds(p.setup))


def refresh_native_feasibility(frepple):
    """Repeat official stage 199's validation after stage 201 date/load changes.

    updateFeasible refreshes the native cached diagnostic; it does not solve or
    set that flag to a requested value. The official task also propagates status,
    so only its documented no-argument validation method is called here.
    """
    plans={p.reference:p for o in frepple.operations() for p in o.operationplans}
    def facts():
        loads=sorted((r.name,l.operationplan.reference,str(l.quantity),
            l.startdate.isoformat(),l.enddate.isoformat())
            for r in frepple.resources() for l in r.loadplans)
        orders=sorted((p.reference,str(p.quantity),p.status,p.start.isoformat(),p.end.isoformat())
            for p in plans.values())
        return orders,loads
    before_facts=facts()
    before={ref:p.feasible for ref,p in plans.items()}
    for p in plans.values():p.updateFeasible()
    if facts()!=before_facts:
        raise ValueError('FEASIBILITY_REFRESH_BUSINESS_DRIFT')
    rows=[dict(reference=ref,before=before[ref],after=p.feasible) for ref,p in sorted(plans.items())]
    return dict(method='OFFICIAL_OPERATIONPLAN_UPDATE_FEASIBLE',business_fields_unchanged=True,
        count=len(rows),rows=rows,false_before=[r['reference'] for r in rows if r['before'] is False],
        false_after=[r['reference'] for r in rows if r['after'] is False])


def resolve_protected_setups(frepple,spec):
    """Rebind an existing native proposal to meet an immutable commitment.

    Official SupplyPlanning owns creation, sizing, dates and named-person
    selection. This bounded adapter may change only a proposed predecessor's
    eligible machine, keeping its native-selected person and exact time/qty.
    A single obstructing proposed job may exchange machines with that existing
    predecessor under the same guards. No dates or people are searched here.
    No new order, approval, setup override, initial state or Python schedule.
    Failure is recorded and rejected by the unchanged final protection gate.
    """
    bindings=spec['foundation'].get('operation_bindings',{})
    receipts=[]
    for expected in spec['protected']:
        p=frepple.operationplan(reference=expected['reference'])
        before=native_state(frepple,p)
        if before['start']==expected['start'] and before['end']==expected['end']:
            continue
        receipt=dict(reference=expected['reference'],before=before,status='UNRESOLVED')
        receipts.append(receipt)
        # Any business-identity drift is not a setup restoration problem.
        if any(before[k]!=expected[k] for k in ('reference','operation','status')) or (
            Decimal(before['quantity'])!=Decimal(expected['quantity']) or
            expected['resource'] not in before['resources']):
            receipt['reason']='SOURCE_BINDING_CHANGED';continue
        target=frepple.resource(name=expected['resource'])
        start=datetime.fromisoformat(expected['start'])
        if seconds(p.setup)>0:
            required=bindings.get(p.operation.name,{}).get('setup')
            candidates=[];exchanges=[]
            machines={r for b in bindings.values() for r in b.get('resources',[])}
            for candidate in frepple.operationplans():
                if candidate.ordertype!='MO' or candidate.status!='proposed' or candidate.end>start:
                    continue
                binding=bindings.get(candidate.operation.name,{})
                if not required or binding.get('setup')!=required or target.name not in binding.get('resources',[]):
                    continue
                current=native_state(frepple,candidate)
                if target.name in current['resources']:
                    continue
                # Keep a vacant interval through the protected start so no
                # intervening job can invalidate the predecessor setup state.
                obstructing={str(l.operationplan.reference):l.operationplan for l in target.loadplans
                    if l.quantity>0 and l.operationplan!=p and l.startdate<start and l.enddate>candidate.start}
                if not obstructing:
                    candidates.append((candidate.end,str(candidate.reference),candidate,current,None,None,None))
                    continue
                current_machines=set(current['resources'])&machines
                if len(obstructing)!=1 or len(current_machines)!=1:continue
                blocker=next(iter(obstructing.values()));other_name=next(iter(current_machines))
                if (blocker.ordertype!='MO' or blocker.status!='proposed' or
                    other_name not in bindings.get(blocker.operation.name,{}).get('resources',[])):continue
                other=frepple.resource(name=other_name)
                if any(l.quantity>0 and l.operationplan!=candidate and l.operationplan!=blocker
                    and l.startdate<blocker.end and l.enddate>blocker.start for l in other.loadplans):continue
                exchanges.append((candidate.end,str(candidate.reference),candidate,current,blocker,native_state(frepple,blocker),other))
            # Prefer the old one-job path. At most one two-job exchange is
            # attempted; a failed native recalculation remains a failed run.
            if not candidates:candidates=exchanges
            if not candidates:
                receipt['reason']='NO_EXISTING_ELIGIBLE_PREDECESSOR';continue
            _,_,candidate,current,blocker,blocker_before,other=sorted(candidates,key=lambda x:(x[0],x[1]),reverse=True)[0]
            if blocker is not None:
                receipt['exchange_before']=blocker_before
                frepple.loadplan(operationplan=blocker,resource=other,action='C')
            frepple.loadplan(operationplan=candidate,resource=target,action='C')
            if blocker is not None:
                # Resource reassignment can transiently propagate setup dates
                # in 9.19. Reapply ONLY the starts already selected by native
                # SupplyPlanning, after both assignments have settled. Native
                # recomputes each end/flow/load; no Python end-time arithmetic.
                pairs=[(candidate,current),(blocker,blocker_before)]
                intact=all(all(native_state(frepple,job)[k]==state[k]
                    for k in ('reference','operation','quantity','status')) for job,state in pairs)
                if not intact:
                    receipt['reason']='EXCHANGE_BUSINESS_IDENTITY_CHANGED';continue
                for job,state in pairs:job.start=datetime.fromisoformat(state['start'])
                receipt['native_start_reapplied']={state['reference']:state['start'] for _,state in pairs}
            changed=native_state(frepple,candidate)
            receipt['predecessor_before']=current
            receipt['predecessor_after']=changed
            if any(current[k]!=changed[k] for k in ('reference','operation','quantity','status','start','end')):
                receipt['reason']='PREDECESSOR_NATIVE_WINDOW_CHANGED';continue
            if set(current['resources'])-machines != set(changed['resources'])-machines:
                receipt['reason']='NATIVE_SELECTED_PERSON_CHANGED';continue
            if blocker is not None:
                blocker_after=native_state(frepple,blocker);receipt['exchange_after']=blocker_after
                if any(blocker_before[k]!=blocker_after[k] for k in ('reference','operation','quantity','status','start','end')):
                    receipt['reason']='EXCHANGE_NATIVE_WINDOW_CHANGED';continue
                if set(blocker_before['resources'])-machines != set(blocker_after['resources'])-machines:
                    receipt['reason']='EXCHANGE_NATIVE_SELECTED_PERSON_CHANGED';continue
        if seconds(p.setup)!=0:
            receipt['reason']='SETUP_STILL_REQUIRED';continue
        # A native zero transition is necessary, not an override. Updating the
        # original start recalculates end/flows/loads through the native API.
        p.start=start
        after=native_state(frepple,p)
        receipt['after']=after
        same=all(after[k]==expected[k] for k in ('reference','operation','status','start','end'))
        same=same and Decimal(after['quantity'])==Decimal(expected['quantity']) and target.name in after['resources']
        receipt['status']='RESOLVED_NATIVE_SEQUENCE' if same else 'UNRESOLVED'
        receipt['method']=('EXISTING_TWO_PROPOSALS_MACHINE_EXCHANGE_THEN_SOURCE_START_RECALCULATION'
            if receipt.get('exchange_before') else 'EXISTING_NATIVE_PROPOSAL_REBIND_THEN_SOURCE_START_RECALCULATION')
    return receipts


def restore_required_loads(frepple,protected):
    """Recreate required native loads, then restore only the evidenced machine.

    9.19's nonempty assigned-resources path restores only the listed loads.
    The source list evidences a machine, not the absence of a required person.
    Native default allocation selects the person; Python never picks a member.
    This changes native memory only, before the unchanged protection check.
    """
    receipts=[]
    for expected in protected:
        p=frepple.operationplan(reference=expected['reference'])
        before=native_state(frepple,p)
        if expected.get('owner'):
            # A source routing WO already carries both evidenced assignments.
            # Deleting/recreating it would detach the original manufacturing
            # root, so validate the upstream import instead of rebuilding it.
            if (not p.owner or p.owner.reference!=expected['owner'] or
                expected.get('operator') not in before['resources'] or expected['resource'] not in before['resources']):
                raise ValueError('FOUNDATION_EXTERNAL_MO_BINDING_MISMATCH:'+expected['reference'])
            receipts.append(dict(reference=expected['reference'],before=before,after=before,
                method='UPSTREAM_ROUTING_WO_NAMED_ASSIGNMENTS_PRESERVED',person_choice='EXTERNAL_SOURCE_COMMITMENT',
                original_window_check='REQUIRED_BY_PROTECTION_OWNER'))
            continue
        if (before['operation']!=expected['operation'] or before['status']!=expected['status']
            or Decimal(before['quantity'])!=Decimal(expected['quantity'])
            or expected['resource'] not in before['resources']):
            raise ValueError('FOUNDATION_SOURCE_BINDING_MISMATCH:'+expected['reference'])
        operation=p.operation
        # The native delete action invalidates Python wrappers. Release ours
        # before deletion so a reused C++ address cannot alias a stale wrapper.
        del p
        frepple.operationplan(reference=expected['reference'],action='R')
        p=frepple.operationplan(reference=expected['reference'],operation=operation,
            quantity=float(expected['quantity']),statusNoPropagation=expected['status'],source=expected['reference'],
            start=datetime.fromisoformat(expected['start']),end=datetime.fromisoformat(expected['end']))
        frepple.loadplan(operationplan=p,resource=frepple.resource(name=expected['resource']),action='C')
        after=native_state(frepple,p)
        if expected['resource'] not in after['resources']:
            raise ValueError('FOUNDATION_ORIGINAL_MACHINE_NOT_RESTORED:'+expected['reference'])
        receipts.append(dict(reference=expected['reference'],before=before,after=after,
            method='NATIVE_REQUIRED_LOADS_THEN_ORIGINAL_MACHINE_BINDING',person_choice='NATIVE_NOT_PYTHON',
            original_window_check='REQUIRED_BY_PROTECTION_OWNER'))
    return receipts


def native_supply_effectivity(xml,source,capacity_resource):
    """Read actual native XML, never echo requested supplier validity dates."""
    missing=dict(effective_start=None,effective_end=None,effectivity_evidence='NOT_ATTESTED_NATIVE_XML')
    if xml is None:
        return missing
    def name(node):
        return node.get('name',node.findtext('name')) if node is not None else None
    matches=[]
    for item in xml.findall('items/item'):
        if name(item)!=source['item_id']:
            continue
        for row in item.findall('itemsuppliers/itemsupplier'):
            if (name(row.find('supplier')),name(row.find('location')),name(row.find('resource')),
                row.findtext('effective_start')) == (source['supplier_id'],source['location_id'],
                    capacity_resource,source['effective_from_native']):
                matches.append(row)
    if len(matches)!=1 or not matches[0].findtext('effective_end'):
        return missing
    return dict(effective_start=matches[0].findtext('effective_start'),effective_end=matches[0].findtext('effective_end'),
        effectivity_evidence='NATIVE_XML_ITEMSUPPLIER')


def inspect(frepple,spec,xml=None):
    def ref(v):return v.name if v else None
    result={'resources':[],'setup_matrices':[],'operation_loads':[],
            'supply_paths':[],'supply_alternates':[],'missing_evidence':[]}
    for r in frepple.resources():
        result['resources'].append({'name':r.name,'setup':r.setup,'setupmatrix':ref(r.setupmatrix)})
    for m in frepple.setupmatrices():
        result['setup_matrices'].append({'name':m.name,'rules':[{'from_state':r.fromsetup,'to_state':r.tosetup,
            'duration_seconds':seconds(r.duration),'priority':r.priority} for r in m.rules]})
    for o in frepple.operations():
        if o.hidden:continue
        result['operation_loads'].extend({'operation':o.name,'resource':l.resource.name,'setup':l.setup,
            'skill':ref(l.skill),'quantity':str(l.quantity),'search':l.search} for l in o.loads)
    operations={o.name:o for o in frepple.operations()}
    alternates={}
    for b in frepple.buffers():
        if b.producing is not None and type(b.producing).__name__=='operation_alternate':
            alternates[b.item.name,b.location.name]=b.producing
            result['supply_alternates'].append({'item':b.item.name,'location':b.location.name,'search':b.producing.search})
    for s in spec['sources']:
        name='Purchase '+s['item_id']+' @ '+s['location_id']+' from '+s['supplier_id']+' valid from '+s['effective_from_native']
        o=operations.get(name)
        if o is None:
            result['missing_evidence'].append('supply_path:'+s['id']);continue
        loads=list(o.loads);flows=list(o.flows)
        parent=alternates.get((s['item_id'],s['location_id']))
        child=next((x for x in parent.suboperations if x.operation.name==o.name),None) if parent else None
        result['supply_paths'].append({'source_id':s['id'],'native_name':o.name,'item':ref(o.item),
            'location':ref(o.location),'supplier':s['supplier_id'],
            'priority':child.priority if child else None,'minimum':str(o.size_minimum),'multiple':str(o.size_multiple),
            'lead_time_seconds':seconds(o.duration),'qc_seconds':seconds(flows[0].offset) if len(flows)==1 else None,
            'capacity_resource':loads[0].resource.name if len(loads)==1 else None,
            'capacity_per_unit':str(loads[0].quantity) if len(loads)==1 else None,
            **native_supply_effectivity(xml,s,loads[0].resource.name if len(loads)==1 else None)})
    return result
