"""Map one external routing MO to one native root with owned work orders."""
from decimal import Decimal


def child_reference(m,operation):return m['id']+'::WO::'+operation['operation_id']


def protected_commitments(m,zone,names):
    from planning_adapter import engine_time
    multi=len(m['operations'])>1
    result=[]
    for op in m['operations']:
        row=dict(reference=child_reference(m,op) if multi else m['id'],operation=names[op['operation_id']],
            quantity=m['quantity'],status=m['source_status'],resource=op['resource_id'],
            start=engine_time(op['start_at'],zone),end=engine_time(op['end_at'],zone))
        if multi:row.update(owner=m['id'],operator=op['operator_id'])
        result.append(row)
    return result


def native_manufacturing_rows(m,bindings,zone):
    from planning_adapter import engine_time
    multi=len(m['operations'])>1;result=[]
    if multi:
        result.append(dict(reference=m['id'],operation=m['recipe_id'],quantity=m['quantity'],status=m['source_status'],
            startdate=engine_time(m['start_at'],zone),enddate=engine_time(m['end_at'],zone),source=m['external_ref']))
    for op in m['operations']:
        if Decimal(str(op['quantity']))!=Decimal(str(m['quantity'])):raise ValueError('EXTERNAL_MO_COMMITMENT_QUANTITY')
        if multi and not op.get('operator_id'):raise ValueError('EXTERNAL_MO_NAMED_PERSON_REQUIRED')
        name,units=bindings[op['operation_id']]
        resources=[[op['resource_id'],units]]
        if op.get('operator_id'):resources.append([op['operator_id'],'1'])
        row=dict(reference=child_reference(m,op) if multi else m['id'],operation=name,quantity=m['quantity'],
            status=m['source_status'],startdate=engine_time(op['start_at'],zone),enddate=engine_time(op['end_at'],zone),
            source=m['external_ref'],resources=resources)
        if multi:row['owner']=m['id']
        result.append(row)
    return result
