"""A requested qualified assignment on an unambiguous native proposed leaf.

The original solver creates the batch, routing, dates, staff and material flows.
This Owner uses the existing native loadplan binding, then native feasibility.
Any ambiguous batch or changed time, quantity, person is a failed calculation.
"""
from decimal import Decimal
from p3_fixed_batch.foundation import native_state,refresh_native_feasibility

def apply(frepple,spec):
    if spec.get('mode')=='foundation':return apply_enterprise(frepple,spec)
    changes=[]
    for h in spec.get('hypotheses',[]):
        rows=[p for p in frepple.operationplans() if p.ordertype=='MO' and p.operation.name==h['native_operation'] and Decimal(str(p.quantity))==Decimal(h['quantity'])]
        if len(rows)!=1 or rows[0].status!='proposed':raise ValueError('EDITOR_AMBIGUOUS_OR_PROTECTED_NATIVE_TASK')
        p=rows[0];before=native_state(frepple,p)
        machines=set(spec['machine_ids'])
        # Qualification itself is validated independently against unchanged
        # resource-skill facts; native setter must accept the existing load.
        frepple.loadplan(operationplan=p,resource=frepple.resource(name=h['resource_id']),action='C')
        after=native_state(frepple,p)
        if any(before[k]!=after[k] for k in ['reference','operation','quantity','status','start','end']):raise ValueError('EDITOR_NATIVE_WINDOW_DRIFT')
        if set(before['resources'])-machines!=set(after['resources'])-machines:raise ValueError('EDITOR_NATIVE_PERSON_DRIFT')
        if h['resource_id'] not in after['resources']:raise ValueError('EDITOR_NATIVE_ASSIGNMENT_FAILED')
        changes.append(dict(reference=h['reference'],before=before,after=after))
    refresh=refresh_native_feasibility(frepple)
    if refresh['false_after']:raise ValueError('EDITOR_NATIVE_INFEASIBLE')
    return dict(changes=changes,feasibility=refresh)


def apply_enterprise(frepple,spec):
    from datetime import datetime
    if any(e['kind'] in {'task','demand'} for e in spec.get('hypotheses',[])):
        return apply_atomic(frepple,spec)
    if any(e['kind']=='optimized_schedule' for e in spec.get('hypotheses',[])):
        from p3_fixed_batch.optimization import apply as optimized
        return optimized(frepple,spec)
    changes=[]
    for e in spec.get('hypotheses',[]):
        if e['kind'] in {'sequence','native_strategy'}:continue
        t=e['source_task']
        def local(v):return native_time(v,spec['timezone']).isoformat()
        rows=[]
        for p in frepple.operationplans():
            if p.ordertype!='MO' or p.status!='proposed' or p.operation.name!=t['native_operation'] or Decimal(str(p.quantity))!=Decimal(t['quantity']):continue
            state=native_state(frepple,p)
            if state['start']==local(t['start_at']) and state['end']==local(t['end_at']) and sorted(state['resources'])==t['resources']:rows.append(p)
        if len(rows)!=1:raise ValueError('EDITOR_SOURCE_TASK_NOT_REPRODUCED')
        p=rows[0];before=native_state(frepple,p)
        if e['kind']=='machine':frepple.loadplan(operationplan=p,resource=frepple.resource(name=e['resource_id']),action='C')
        elif e['kind']=='proposed_window':p.start=native_time(e['start_at'],spec['timezone'])
        else:raise ValueError('EDITOR_NATIVE_CHANGE_UNSUPPORTED')
        after=native_state(frepple,p)
        if any(before[k]!=after[k] for k in ['reference','operation','quantity','status']):raise ValueError('EDITOR_NATIVE_FACT_DRIFT')
        machines=set(spec['machine_ids'])
        if set(before['resources'])-machines!=set(after['resources'])-machines:raise ValueError('EDITOR_NATIVE_PERSON_DRIFT')
        if e['kind']=='machine' and (after['start']!=before['start'] or after['end']!=before['end'] or e['resource_id'] not in after['resources']):raise ValueError('EDITOR_NATIVE_ASSIGNMENT_FAILED')
        if e['kind']=='proposed_window' and (after['start']!=local(e['start_at']) or after['resources']!=before['resources'] or ('end_at' in e and after['end']!=local(e['end_at']))):raise ValueError('EDITOR_NATIVE_WINDOW_DRIFT')
        changes.append(dict(reference=e['reference'],before=before,after=after))
    refresh=refresh_native_feasibility(frepple)
    if refresh['false_after']:raise ValueError('EDITOR_NATIVE_INFEASIBLE')
    return dict(changes=changes,feasibility=refresh)


def native_time(value,zone):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.fromisoformat(value).astimezone(ZoneInfo(zone)).replace(tzinfo=None)


def _source_binding(frepple,t,zone):
    rows=[]
    for p in frepple.operationplans():
        if p.ordertype!='MO' or p.status!='proposed' or p.operation.name!=t['native_operation'] or Decimal(str(p.quantity))!=Decimal(t['quantity']):continue
        state=native_state(frepple,p)
        if state['start']==native_time(t['start_at'],zone).isoformat() and state['end']==native_time(t['end_at'],zone).isoformat() and sorted(state['resources'])==t['resources']:rows.append(p)
    if len(rows)!=1:raise ValueError('EDITOR_SOURCE_TASK_NOT_REPRODUCED')
    return rows[0]


def _root(p):
    while p.owner is not None and p.owner.ordertype=='MO':p=p.owner
    return p


def request_owner(p,demand):
    """Native may attach a request to a hidden alternate above its MO."""
    while p is not None:
        if p.demand==demand:return p
        p=p.owner
    return None


def manufacturing_request_window(root_before,spec):
    """A routing request asks for root completion, never a selected step end."""
    from datetime import datetime
    due=datetime.fromisoformat(root_before['end'])
    latest=native_time(spec['horizon_end_at'],spec['timezone'])
    if due>latest:raise ValueError('EDITOR_PRIVATE_REQUEST_OUTSIDE_HORIZON')
    return due,latest-due


def remove_private_requests(frepple,names):
    """Remove only existing candidate requests, also after a failed solve."""
    if not names:return
    for name in sorted(names):
        d=next((row for row in frepple.demands() if row.name==name),None)
        if d is None:continue
        # Hidden ALT owners need not occur in operationplans(); the demand's
        # own iterator is therefore part of the exact cleanup contract.
        for p in list(d.operationplans)+list(frepple.operationplans()):
            if p.demand==d:p.demand=None
        if list(d.operationplans):raise ValueError('EDITOR_PRIVATE_REQUEST_STILL_BOUND')
        frepple.demand(name=name,action='R')
        d=None
    if any(d.name in names for d in frepple.demands()):
        raise ValueError('EDITOR_PRIVATE_REQUEST_CLEANUP_FAILED')


def _solver():
    # Reuse the configured original finite SupplyPlanning solver, including its
    # algorithm, lead times, iteration controls and resource selection policy.
    from freppledb.execute.commands import SupplyPlanning
    solver=SupplyPlanning.solver
    if solver.plantype!=1 or (solver.constraints & 52)!=52:
        raise ValueError('EDITOR_FINITE_SOLVER_REQUIRED')
    return solver


def _solve(solver,obj,calls,label):
    try:
        solver.solve(obj)
        solver.commit()
    except Exception:
        solver.rollback()
        raise
    calls.append(dict(method='ORIGINAL_FINITE_SOLVER_MRP',object=label))


def _deliveries(frepple,d):
    return [dict(reference=str(p.reference),quantity=str(p.quantity),start=p.start.isoformat(),end=p.end.isoformat(),status=p.status) for p in frepple.operationplans() if p.demand==d and p.ordertype=='DLVR']


def _flow_buffers(root):
    result={}
    operations=[root.operation]+[p.operation for p in root.operationplans]
    for o in operations:
        for flow in o.flows:
            if flow.buffer is not None:result[flow.buffer.name]=flow.buffer
    return result


def apply_atomic(frepple,spec):
    """Bind the original solve once, then run genuine native incremental work.

    A private manufacturing request is a candidate-only native demand directly
    asking the selected production operation: it produces FG, never consumes a
    second FG delivery. It is detached and removed before export. Source ERP
    status, demand and actual facts are never used as an artificial lock.
    """
    from datetime import datetime,timedelta
    zone=spec['timezone'];events=spec.get('hypotheses',[]);calls=[];changes=[];requests=[];private_names=set();buffers={};bound={}
    for e in events:
        if e['kind']=='task':
            p=_source_binding(frepple,e['source_task'],zone)
            bound[e['reference']]=(str(p.reference),native_state(frepple,p),native_state(frepple,_root(p)))
    # Native delete frees C++ objects even while Python still references them.
    # Keeping an old leaf pointer across a rebuild can decrement the refcount
    # of a new object allocated at that address when this function returns.
    # Bind the original tuple once, retaining only immutable IDs and facts.
    p=None
    needs_solver=any(e['kind']=='demand' or {'quantity','recipe_id'}.intersection(e.get('changes',{})) for e in events)
    solver=_solver() if needs_solver else None
    previous_erase=solver.erasePreviousFirst if solver is not None else None
    if solver is not None:solver.erasePreviousFirst=False
    try:
        for e in events:
            if e['kind']!='task':continue
            reference,before,root_before=bound[e['reference']]
            p=next((row for row in frepple.operationplans() if str(row.reference)==reference),None)
            if p is None:raise ValueError('EDITOR_NATIVE_DEPENDENT_SOURCE_REMOVED')
            patch=e['changes'];root=_root(p);private=None
            if {'quantity','recipe_id'}.intersection(patch):
                buffers.update(_flow_buffers(root))
                # Remove only the bound proposed supply; the native delete
                # algorithm safely clears its upstream excess, including lots.
                p=None
                frepple.solver_delete().solve(root)
                root=None
                calls.append(dict(method='NATIVE_SOLVER_DELETE',object='source_supply:'+root_before['reference']))
                name='EDITOR-REQUEST:'+e['reference'];target=e['target'];q=Decimal(target['quantity'])
                operation=frepple.operation(name=target['recipe_id'])
                due,lateness=manufacturing_request_window(root_before,spec)
                if any(d.name==name for d in frepple.demands()):raise ValueError('EDITOR_PRIVATE_REQUEST_NAME_CONFLICT')
                private_names.add(name)
                private=frepple.demand(name=name,item=operation.item,location=operation.location,operation=operation,quantity=float(q),minshipment=float(q),due=due,maxlateness=lateness,priority=0,status='open',action='A')
                private.operation=operation
                if private.operation!=operation:raise ValueError('EDITOR_PRIVATE_OPERATION_BINDING')
                previous_log=solver.loglevel
                try:
                    solver.loglevel=2
                    _solve(solver,private,calls,name)
                finally:solver.loglevel=previous_log
                candidates=[r for r in frepple.operationplans() if request_owner(r,private) is not None and r.ordertype=='MO' and r.operation==operation and r.status=='proposed']
                if len(candidates)!=1 or Decimal(str(candidates[0].quantity))!=q:
                    import json
                    evidence=[dict(native_state(frepple,r),ordertype=r.ordertype,demand=r.demand.name if r.demand is not None else None,owner=str(r.owner.reference) if r.owner is not None else None) for r in frepple.operationplans() if r.demand==private or r.operation.name==operation.name]
                    deliveries=[dict(native_state(frepple,r),ordertype=r.ordertype,demand=r.demand.name if r.demand is not None else None) for r in private.operationplans]
                    raise ValueError('EDITOR_NATIVE_REQUEST_QUANTITY_NOT_ADOPTED:'+json.dumps(dict(request=name,request_operation=private.operation.name,request_status=private.status,request_item=private.item.name if private.item else None,request_location=private.location.name if private.location else None,request_maxlateness=str(private.maxlateness),solver_plantype=solver.plantype,solver_constraints=solver.constraints,quantity=str(q),due=due.isoformat(),observed=evidence,request_roots=deliveries),sort_keys=True))
                root=candidates[0]
                owner=request_owner(root,private)
                if owner.ordertype=='DLVR' or any(f.item==operation.item and Decimal(str(f.quantity))<0 for f in owner.operation.flows):raise ValueError('EDITOR_PRIVATE_REQUEST_CONSUMES_FG')
                original_owner=str(owner.reference)
                # Native excess cleanup inspects a flow's direct owner demand.
                # Move this candidate-only request association from a hidden
                # ALT shell to the actual manufacturing root, exactly once.
                # This changes no source status, flow, resource or quantity.
                if owner!=root:
                    old=native_state(frepple,root)
                    owner.demand=None;root.demand=private
                    if native_state(frepple,root)!=old:raise ValueError('EDITOR_PRIVATE_BINDING_BUSINESS_DRIFT')
                    owner=root
                leaves=[r for r in frepple.operationplans() if r.operation.name==target['native_operation'] and (r==root or _root(r)==root)]
                if len(leaves)!=1:raise ValueError('EDITOR_NATIVE_ROUTE_NOT_ADOPTED')
                p=leaves[0];requests.append(dict(name=name,reference=e['reference'],source_root=root_before['reference'],candidate_root=str(root.reference),native_original_request_owner=original_owner,request_owner=str(owner.reference),request_owner_type=owner.ordertype,quantity=str(q),operation=operation.name,kind='CANDIDATE_PRIVATE_MANUFACTURING_REQUEST',extra_fg_consumption=False,retained_in_source=False))
            if patch.get('resource_id'):
                frepple.loadplan(operationplan=p,resource=frepple.resource(name=patch['resource_id']),action='C')
            clock=patch.get('time')
            if clock:
                if clock['mode'] in {'START_ANCHOR','WINDOW'}:p.start=native_time(clock['start_at'],zone)
                else:p.end=native_time(clock['end_at'],zone)
            if private is not None:buffers.update(_flow_buffers(root))
            changes.append(dict(reference=e['reference'],before=before,after=native_state(frepple,p),root_before=root_before,root_after=native_state(frepple,root),native_method='PRIVATE_OPERATION_DEMAND_AND_ORIGINAL_MRP' if private is not None else 'NATIVE_ATOMIC_TIME_RESOURCE',changes=patch))
        # Replan the real customers after replacing a supply lot. Buffer
        # shortages alone cannot revise an already planned customer delivery.
        # Native delete and demand planning own the resulting fulfilled/short
        # quantity; original customer facts and hard dates stay unchanged.
        customer_replans=[]
        affected=sorted({d for e in events if e['kind']=='task' and {'quantity','recipe_id'}.intersection(e['changes']) for d in e['source_task'].get('demand_ids',[])})
        for name in affected:
            d=frepple.demand(name=name);before=_deliveries(frepple,d)
            facts=(d.quantity,d.due,d.maxlateness,d.status)
            frepple.solver_delete().solve(d)
            calls.append(dict(method='NATIVE_SOLVER_DELETE_DEMAND',object=name,reason='SUPPLY_LOT_CHANGED'))
            _solve(solver,d,calls,'affected_customer:'+name)
            if facts!=(d.quantity,d.due,d.maxlateness,d.status):raise ValueError('EDITOR_SOURCE_CUSTOMER_FACT_DRIFT')
            customer_replans.append(dict(demand_id=name,before=before,after=_deliveries(frepple,d),source_facts_preserved=True))
        # Repair the complete affected material timelines using the original
        # buffer shortage algorithm and clean only excess. Private requests keep
        # the exact manual lot separate from the customer's remaining demand.
        for name,b in sorted(buffers.items()):
            _solve(solver,b,calls,'buffer:'+name)
        for name,b in sorted(buffers.items()):
            frepple.solver_delete().solve(b)
            calls.append(dict(method='NATIVE_SOLVER_DELETE_EXCESS',object='buffer:'+name))
        demand_receipts=[]
        for e in events:
            if e['kind']!='demand':continue
            for request in e['demands']:
                d=frepple.demand(name=request['demand_id']);before=_deliveries(frepple,d)
                original_due,original_late=d.due,d.maxlateness
                frepple.solver_delete().solve(d)
                calls.append(dict(method='NATIVE_SOLVER_DELETE_DEMAND',object=d.name))
                try:
                    if e['action']!='UNPLAN':
                        d.due=native_time(request['anchor_at'],zone);d.maxlateness=timedelta(seconds=request['maximum_lateness_seconds'])
                        _solve(solver,d,calls,'demand:'+d.name)
                        planned=sum((Decimal(x['quantity']) for x in _deliveries(frepple,d)),Decimal(0))
                        latest=native_time(request['latest_at'],zone)
                        # CE9.19's retry loop is strict < latest. This second
                        # native request includes the exact legal final instant.
                        if planned<Decimal(request['quantity']) and latest>d.due:
                            d.due=latest;d.maxlateness=timedelta(0)
                            _solve(solver,d,calls,'demand_latest_boundary:'+d.name)
                finally:d.due=original_due;d.maxlateness=original_late
                after=_deliveries(frepple,d)
                if e['action']!='UNPLAN' and any(native_time(request['earliest_at'],zone)>datetime.fromisoformat(r['end']) or datetime.fromisoformat(r['end'])>native_time(request['latest_at'],zone) for r in after):raise ValueError('EDITOR_NATIVE_DEMAND_WINDOW')
                demand_receipts.append(dict(reference=e['reference'],action=e['action'],**request,before=before,after=after,source_due_preserved=d.due==original_due,source_maxlateness_preserved=d.maxlateness==original_late))
        # Detach private demand pointers after all native cleanup, then delete
        # the request itself. No fake customer demand or FK reaches the DB.
        private=None
        remove_private_requests(frepple,private_names)
        for request in requests:request['removed_before_export']=True
        # Re-read after every dependent operation and material solve, rather
        # than treating an intermediate setter return as the final receipt.
        plans={str(p.reference):p for p in frepple.operationplans()}
        for r in changes:
            p=plans.get(r['after']['reference']);root=plans.get(r['root_after']['reference'])
            if p is None or root is None:raise ValueError('EDITOR_NATIVE_INTENT_REMOVED')
            r['after']=native_state(frepple,p);r['root_after']=native_state(frepple,root)
            e=next(e for e in events if e.get('reference')==r['reference']);patch=e['changes'];after=r['after'];target=e['target']
            if after['status']!='proposed' or Decimal(r['root_after']['quantity'])!=Decimal(target['quantity']) or after['operation']!=target['native_operation']:raise ValueError('EDITOR_NATIVE_INTENT_NOT_ADOPTED')
            if patch.get('resource_id') and patch['resource_id'] not in after['resources']:raise ValueError('EDITOR_NATIVE_MACHINE_NOT_ADOPTED')
            clock=patch.get('time')
            if clock:
                a,b=datetime.fromisoformat(after['start']),datetime.fromisoformat(after['end'])
                if (clock['mode']=='START_ANCHOR' and a!=native_time(clock['start_at'],zone)) or (clock['mode']=='END_ANCHOR' and b!=native_time(clock['end_at'],zone)) or (clock['mode']=='WINDOW' and not native_time(clock['start_at'],zone)<=a<b<=native_time(clock['end_at'],zone)):raise ValueError('EDITOR_NATIVE_TIME_NOT_ADOPTED')
        refresh=refresh_native_feasibility(frepple)
        if refresh['false_after']:raise ValueError('EDITOR_NATIVE_INFEASIBLE')
        return dict(revision='foundation-proposed-editor-v2',changes=changes,demands=demand_receipts,affected_customer_replans=customer_replans,private_requests=requests,native_calls=calls,feasibility=refresh,atomic_source_bindings=len(bound))
    finally:
        private=None
        try:remove_private_requests(frepple,private_names)
        finally:
            if solver is not None:solver.erasePreviousFirst=previous_erase
