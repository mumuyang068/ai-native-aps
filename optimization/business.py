"""Native business qualification is separate from a finite solver status."""
from decimal import Decimal

def compare(base,candidate):
 issues=[]
 def check(ok,code):
  if not ok:issues.append(code)
 for label,v in [('BASE',base),('CANDIDATE',candidate)]:
  i=v.get('interpretation',{})
  check(v.get('run',{}).get('state')=='SUCCEEDED',label+'_RUN_NOT_SUCCESSFUL')
  check(i.get('schedule_check')=='PASS',label+'_SCHEDULE_NOT_CERTIFIED')
  for owner in ('material_validation','foundation_validation','plan_editor_validation'):
   check(i.get(owner,{}).get('status')=='PASS',label+'_'+owner.upper())
 a={r['demand_id']:r for r in (base.get('result') or {}).get('deliveries',[])};b={r['demand_id']:r for r in (candidate.get('result') or {}).get('deliveries',[])}
 check(bool(a) and set(a)==set(b),'DEMAND_IDENTITY_CHANGED');rows=[]
 for key in sorted(a.keys()&b.keys()):
  x,y=a[key],b[key]
  quantity=Decimal(y['planned_quantity'])>=Decimal(x['planned_quantity'])
  delay=x['delay_seconds'] is None or y['delay_seconds'] is not None and y['delay_seconds']<=x['delay_seconds']
  check(x['requested_quantity']==y['requested_quantity'],'DEMAND_QUANTITY_CHANGED:'+key)
  check(quantity and delay,'PROTECTED_DELIVERY_REGRESSION:'+key)
  rows.append(dict(demand_id=key,base_planned=x['planned_quantity'],candidate_planned=y['planned_quantity'],base_delay=x['delay_seconds'],candidate_delay=y['delay_seconds'],protected=quantity and delay))
 left=base.get('interpretation',{}).get('plan_editor_validation',{}).get('setup_seconds');right=candidate.get('interpretation',{}).get('plan_editor_validation',{}).get('setup_seconds')
 check(left is not None and right is not None,'SETUP_EVIDENCE_MISSING')
 saving=str(Decimal(left)-Decimal(right)) if left is not None and right is not None else None
 return dict(status='PASS' if not issues else 'FAIL',eligible=not issues,improved=not issues and Decimal(saving)>0,issues=issues,deliveries=rows,setup_saved_seconds=saving,scope='NATIVE_COMPLETE_ENTERPRISE_FROZEN_RESEARCH',execution_authorized=False,global_optimality_certified=False)
