"""Read-only native evidence after a failed Editor request.

The official Problem API owns every reason/window below. This collector does
not solve, repair, infer a source reason, or certify an exported schedule.
"""
from p3_fixed_batch.foundation import native_state


def write_failure(frepple,spec,phase,path,error,receipt=None):
    """Keep failed calculation evidence without replacing its original error."""
    import json
    code=str(error).split(':',1)[0]
    try:
        value=capture(frepple,spec['editor'],code,receipt)
        value['capture_status']='OBSERVED'
    except Exception as capture_error:
        value=dict(schema='native-editor-failure/1',scope='NATIVE_FAILURE_OBSERVATION',
            capture_status='UNAVAILABLE',capture_error=type(capture_error).__name__+': '+str(capture_error),
            missing_evidence=['native_failure_capture'],infeasibility_proven=False,
            schedule_certified=False,solver_called_for_diagnostics=False)
    value.update(run_id=spec['run_id'],input_hash=spec['input_hash'],phase=phase,code=code,error=str(error))
    try:path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    except (OSError,TypeError,ValueError):return False
    return True


def capture(frepple,editor,code,receipt=None):
    states={}
    for p in frepple.operationplans():
        if p.ordertype!='MO':continue
        row=native_state(frepple,p)
        row.update(owner=str(p.owner.reference) if p.owner is not None else None,feasible=bool(p.feasible))
        states[row['reference']]=row
    false={ref for ref,row in states.items() if row['feasible'] is False}
    requested=[dict(reference=e['reference'],source_native_reference=e['source_task']['source_native_reference'],source_operation_task_id=e['source_task'].get('operation_task_id'),target_native_operation=e.get('target',{}).get('native_operation'),changes=e.get('changes',{})) for e in editor.get('hypotheses',[]) if e['kind']=='task']
    target_names={row['target_native_operation'] for row in requested}
    selected={ref for ref,row in states.items() if ref in false or row['owner'] in false or row['operation'] in target_names}
    problems=[];missing=[]
    for problem in frepple.problems():
        owner=problem.owner
        problems.append(dict(code=problem.name,entity=problem.entity,description=problem.description,
            owner_reference=str(owner.reference) if getattr(owner,'reference',None) is not None else None,
            owner_name=getattr(owner,'name',None),owner_type=type(owner).__name__,
            start_at=problem.start.isoformat(),end_at=problem.end.isoformat(),feasible=bool(problem.feasible)))
    resource_ids={rid for ref in selected for rid in states[ref]['resources']}
    resource_ids.update(p['owner_name'] for p in problems if p['entity']=='capacity' and p['owner_name'])
    resources=[]
    for resource in frepple.resources():
        if resource.name not in resource_ids:continue
        windows=[p for p in problems if p['entity']=='capacity' and p['owner_name']==resource.name]
        events=[]
        for load in resource.loadplans:
            ref=str(load.operationplan.reference);a,b=load.startdate.isoformat(),load.enddate.isoformat()
            if ref not in selected and not any(a<w['end_at'] and w['start_at']<b for w in windows):continue
            events.append(dict(operation_reference=ref,quantity=str(load.quantity),at=load.date.isoformat(),start_at=a,end_at=b))
            if ref in states:selected.add(ref)
        resources.append(dict(resource_id=resource.name,loadplans=events))
    if false and not any(not p['feasible'] for p in problems):missing.append('native_problem_for_infeasible_operation')
    expected={str(row[key]['reference']) for row in (receipt or {}).get('changes',[]) for key in ['after','root_after']}
    return dict(schema='native-editor-failure/1',scope='NATIVE_FAILURE_OBSERVATION',code=code,
        native_false_refs=sorted(false),missing_references=sorted(expected-set(states)),
        operations=[states[ref] for ref in sorted(selected)],problems=problems,resources=resources,
        requested_tasks=requested,missing_evidence=missing,infeasibility_proven=False,
        schedule_certified=False,native_api='OFFICIAL_PROBLEM_AND_OPERATIONPLAN_RESOURCE_APIS',
        solver_called_for_diagnostics=False)
