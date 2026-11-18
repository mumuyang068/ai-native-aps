"""Read-only 9.19 native locations, distribution lanes, orders and flow order."""
def inspect(frepple):
 def name(obj):return obj.name if obj else None
 orders=[]
 for p in frepple.operationplans():
  orders.append({'reference':p.reference,'order_type':p.ordertype,'operation_class':type(p.operation).__name__,
   'item':name(p.item),'location':name(p.location),'origin':name(p.origin),'status':p.status,'quantity':str(p.quantity),
   'start':p.start.isoformat(),'end':p.end.isoformat(),'demand':name(p.demand)})
 routes=[]
 for item in frepple.items():
  for r in item.itemdistributions:
   routes.append({'item':name(r.item),'origin':name(r.origin),'destination':name(r.destination),'leadtime':str(r.leadtime),
    'size_minimum':str(r.size_minimum),'size_multiple':str(r.size_multiple),'effective_start':r.effective_start.isoformat(),
    'effective_end':r.effective_end.isoformat(),'source':getattr(r,'source',None)})
 return {'locations':[r.name for r in frepple.locations()],'routes':routes,'orders':orders,
  'buffers':[{'item':b.item.name,'location':b.location.name,'opening':str(b.onhand),'events':[
   {'reference':f.operationplan.reference,'order_type':f.operationplan.ordertype,'operation_class':type(f.operationplan.operation).__name__,
    'at':f.date.isoformat(),'quantity':str(f.quantity),'onhand':str(f.onhand)} for f in b.flowplans]} for b in frepple.buffers()],
  'demands':[{'name':d.name,'item':d.item.name,'location':d.location.name,'quantity':str(d.quantity),'due':d.due.isoformat(),
   'minshipment':str(d.minshipment),'maxlateness':str(d.maxlateness),'priority':getattr(d,'priority',None),'status':getattr(d,'status',None)} for d in frepple.demands()]}
