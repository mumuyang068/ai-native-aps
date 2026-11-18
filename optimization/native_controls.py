"""Only evidenced Community switches; reset persistent solver on every upload."""
def configure(data,native):
 from .contract import event
 controls=[e for e in data['foundation_projection'].get('plan_editor_overlay',{}).get('events',[]) if e['kind']=='native_strategy']
 if len(controls)>1:raise ValueError('OPTIMIZER_NATIVE_STRATEGY_INVALID')
 solver='heuristic'
 if controls:
  c=event(data,controls[0],[]);solver=c['solver']
  machines={r['id'] for r in data['resources'] if r.get('role')=='MACHINE'}
  pools={r['id'] for r in data['resources'] if r.get('role')=='MACHINE_POOL'}
  for load in native['operationresource']:
   if load['resource'] in machines|pools:load['search']=c['machine_search']
 native['parameter']=[p for p in native['parameter'] if p['name']!='plan.solver']+[dict(name='plan.solver',value=solver)]
