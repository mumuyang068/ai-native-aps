"""Observe the existing native alternate; never choose a supplier in Python."""
from decimal import Decimal

def text(v):return format(Decimal(str(v)).normalize(),'f')
def seconds(v):return int(v.total_seconds()) if hasattr(v,'total_seconds') else int(v)
def inspect(frepple,spec,configure=False):
    terms=spec['priority_supply'];site=terms['site'];material=terms['sources'][0]['item_id']
    b=frepple.buffer(name=material+' @ '+site)
    alternate=b.producing
    if type(alternate).__name__!='operation_alternate':raise ValueError('PRIORITY_ALTERNATE_MISSING')
    if configure:
        if terms['strategy']!='PRIORITY':raise ValueError('UNSUPPORTED_NATIVE_STRATEGY')
        alternate.search='PRIORITY'
    children={s.operation.name:s for s in alternate.suboperations}
    paths=[]
    for source in terms['sources']:
        name='Purchase '+source['item_id']+' @ '+site+' from '+source['supplier_id']+' valid from '+source['effective_from_native']
        if name not in children:raise ValueError('NATIVE_SUPPLY_PATH_MISSING:'+source['id'])
        child=children[name];o=child.operation
        flows=list(o.flows);loads=list(o.loads)
        if len(flows)!=1 or len(loads)!=1:raise ValueError('NATIVE_SUPPLY_PATH_EVIDENCE_INCOMPLETE')
        paths.append({'source_id':source['id'],'operation_name':name,'priority':child.priority,
            'cost':text(o.cost),'lead_time_seconds':seconds(o.duration),
            'minimum':text(o.size_minimum),'multiple':text(o.size_multiple),
            'qc_seconds':seconds(flows[0].offset),'capacity_resource':loads[0].resource.name,
            'capacity_per_unit':text(loads[0].quantity)})
    return {'alternate':{'name':alternate.name,'type':type(alternate).__name__,'search':alternate.search},'paths':paths}
