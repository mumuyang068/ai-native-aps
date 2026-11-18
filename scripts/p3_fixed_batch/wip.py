"""Finite WIP observation only; routing containers have no processing duration."""
def seconds(value):return value.total_seconds() if hasattr(value,'total_seconds') else float(value)

def describe_operation(operation):
    kind=type(operation).__name__
    return {'name':operation.name,'type':kind,
        'fixed_seconds':seconds(operation.duration) if kind in {'operation_time_per','operation_fixed_time'} else None,
        'seconds_per_unit':seconds(operation.duration_per) if kind=='operation_time_per' else None,
        'flows':[{'item':f.item.name,'quantity':str(f.quantity),'type':type(f).__name__} for f in operation.flows],
        **{k:str(getattr(operation,k)) for k in ['size_minimum','size_maximum','size_multiple'] if hasattr(operation,k)}}

def describe_confirmed_supply(frepple,xml):
    """Observe real purchase operations and XML ItemSupplier keys; never configure them."""
    def name(node):return node.attrib.get('name',node.findtext('name')) if node is not None else None
    sources=[{'item':name(item),'supplier':name(s.find('supplier')),'location':name(s.find('location'))}
             for item in xml.findall('items/item') for s in item.findall('itemsuppliers/itemsupplier')]
    paths=[{'name':o.name,'lead_time_seconds':seconds(o.duration),'minimum':str(o.size_minimum),
            'multiple':str(o.size_multiple),'loads':[{'resource':l.resource.name,'quantity':str(l.quantity)} for l in o.loads],'flows':[{'item':f.item.name,'quantity':str(f.quantity),'offset_seconds':seconds(f.offset)} for f in o.flows]}
           for o in frepple.operations() if type(o).__name__=='operation_itemsupplier']
    return {'itemsuppliers':sources,'paths':paths}
