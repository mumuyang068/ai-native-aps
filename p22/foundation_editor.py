"""Complete-enterprise proposed-task editor; no changes to frozen source facts.

Selectors identify a baseline observation, not an ERP order or cross-run native
number. The source view and original native bindings must reproduce that tuple.
"""
import copy
from datetime import timedelta
from decimal import Decimal
from plan_editor import digest,fail,at,AUTHORITY,reject_quantity_edits

SCOPE='foundation-proposed-editor-v1'
ATOMIC_SCOPE='foundation-proposed-editor-v2'
FROZEN_419_TABLES_SHA256='8fd64b7fee267baeab8f85f2ff7c8f1ad550c2f1581ae48b3a7ac777829e8aeb'

def research_base_tables(data):
 """Authenticate the exact frozen 419 plus separately declared additions.

 This pinned source digest belongs to SIM-GARMENT-01/V1's source JSON tables,
 not any historical runtime database. No external file is read by this gate.
 """
 meta=data['foundation_projection'];extension=meta.get('editor_scope',{}).get('research_extension')
 if extension is None:return None
 declaration=extension.get('declaration',{});tables=meta['source_tables'];sid=extension.get('added_source_id')
 if declaration.get('schema')!='completeness-editor-research/1' or declaration.get('scope')!='NEW_ISOLATED_DECLARATIONS_NOT_FROZEN_ENTERPRISE_FACTS' or declaration.get('authority')!='SYNTHETIC_RESEARCH_ONLY_NO_EXECUTION' or declaration.get('original_419_sha256')!=FROZEN_419_TABLES_SHA256 or sid!='COMPLETENESS-02-DECLARATION':fail('EDITOR_RESEARCH_DECLARATION')
 base={k:[r for r in rows if r.get('source_id')!=sid] for k,rows in tables.items()}
 added={k:[r for r in rows if r.get('source_id')==sid] for k,rows in tables.items()}
 if digest(base)!=FROZEN_419_TABLES_SHA256 or sum(map(len,base.values()))!=419 or len(base)!=28:fail('EDITOR_FROZEN_SOURCE_CHANGED')
 if not any(added.values()) or any(r.get('source_sha256')!=digest(declaration) for rows in added.values() for r in rows) or extension.get('added_rows_sha256')!=digest(added):fail('EDITOR_RESEARCH_ADDITIONS_CHANGED')
 if len(base['demand_lines'])!=24 or sum(Decimal(str(d['quantity'])) for d in base['demand_lines'])!=480:fail('EDITOR_FROZEN_DEMAND_SCOPE')
 return base

def valid_source(t):
 required={'id','baseline_run_id','native_operation','quantity','start_at','end_at','resources','source_native_reference','status'}
 extra={'root_native_reference','root_quantity','recipe_id','operation_id','operation_task_id','manufacture_id','affected_operation_ids','demand_ids'}
 return isinstance(t,dict) and required<=set(t) and not set(t)-required-extra and t['status']=='proposed' and t['id']==task_id(t)

def validate_alternates(data):
 groups=data['foundation_projection'].get('editor_scope',{}).get('alternate_groups',[])
 recipes={r['id']:r for r in data['production_recipes']};seen=set()
 for g in groups:
  if set(g)!={'id','recipe_ids','source'} or not g['source'] or len(g['recipe_ids'])<2 or len(set(g['recipe_ids']))!=len(g['recipe_ids']) or not set(g['recipe_ids'])<=set(recipes):fail('EDITOR_ALTERNATE_DEFINITION')
  if seen.intersection(g['recipe_ids']):fail('EDITOR_ALTERNATE_DEFINITION')
  seen.update(g['recipe_ids']);rows=[recipes[i] for i in g['recipe_ids']]
  if len({(r['output_item_id'],r['location_id']) for r in rows})!=1:fail('EDITOR_ALTERNATE_PRODUCT')
 return groups

def normalize_edits(edits):
 """Keep legacy single-field receipts stable; coalesce compound leaf requests."""
 if not isinstance(edits,list):fail('EDITOR_INVALID_CHANGE')
 counts={e.get('reference'):sum(v.get('reference')==e.get('reference') and v.get('kind') in {'proposed_window','machine','task'} for v in edits) for e in edits}
 atomic=any(e.get('kind') in {'task','demand'} for e in edits) or any(n>1 for n in counts.values())
 if not atomic:return copy.deepcopy(edits)
 result=[];tasks={}
 for e in edits:
  if e.get('kind') not in {'proposed_window','machine','task'}:result.append(copy.deepcopy(e));continue
  if e['kind']=='task':
   if set(e)!={'kind','reference','changes'} or not isinstance(e['changes'],dict):fail('EDITOR_INVALID_CHANGE')
   patch=copy.deepcopy(e['changes'])
  elif e['kind']=='machine':
   if set(e)!={'kind','reference','resource_id'}:fail('EDITOR_INVALID_CHANGE')
   patch=dict(resource_id=e['resource_id'])
  else:
   if set(e)!={'kind','reference','start_at'}:fail('EDITOR_INVALID_CHANGE')
   patch=dict(time=dict(mode='START_ANCHOR',start_at=e['start_at']))
  if e['reference'] not in tasks:
   tasks[e['reference']]=dict(kind='task',reference=e['reference'],changes={});result.append(tasks[e['reference']])
  if set(tasks[e['reference']]['changes']).intersection(patch):fail('EDITOR_DUPLICATE_FIELD')
  tasks[e['reference']]['changes'].update(patch)
 return result

def task_demands(data,recipe_id,view=None,manufacture_id=None):
 if view and view.get('order_evidence'):
  ids=[o['demand_id'] for o in view['order_evidence'].get('orders',[]) if any(x.get('id')==manufacture_id for x in o.get('manufacturing',[]))]
  if ids:return sorted(set(ids))
 # Conservative source-BOM closure when the baseline lacks pegging evidence.
 recipes={r['id']:r for r in data['production_recipes']}
 if recipe_id not in recipes:return []
 items={recipes[recipe_id]['output_item_id']};boms={b['id']:b for b in data['bom_revisions']}
 prior=None
 while prior!=items:
  prior=set(items)
  items.update(r['output_item_id'] for r in recipes.values() if any(l['item_id'] in items for l in boms[r['bom_revision_id']]['lines']))
 return sorted(d['id'] for d in data['demand_lines'] if d['item_id'] in items)

def recipe_for(data,t):
 rid=data['foundation_projection']['native_operation_map'][t['native_operation']]['recipe_id']
 return next(r for r in data['production_recipes'] if r['id']==rid)

def target_operation(data,t,recipe):
 old=spec(data,t);route=next(r for r in data['routing_revisions'] if r['id']==recipe['routing_revision_id'])
 candidates=[o for o in route['operations'] if o['id']==old['id'] or o['sequence']==old['sequence']]
 if len(candidates)!=1:fail('EDITOR_ALTERNATE_OPERATION_ANCHOR')
 return candidates[0]

def quantity_policy(data,t,recipe):
 source=next((r for r in data['foundation_projection']['source_tables']['recipes'] if r['id']==recipe['id']),{})
 wip=bool(source.get('continuity_job_id')) or recipe['id'].startswith('CONT-CONTINUE:')
 return dict(editable=not wip,scope='PROPOSED_SUPPLY_ROOT',uom=next(i['base_uom'] for i in data['items'] if i['id']==recipe['output_item_id']),minimum=recipe['manufacturing_minimum_quantity'],maximum=recipe.get('manufacturing_maximum_quantity'),multiple=recipe['manufacturing_multiple'],reason='EDITOR_WIP_QUANTITY_PROTECTED' if wip else None)

def checked_quantity(data,t,recipe,value):
 p=quantity_policy(data,t,recipe)
 if not p['editable']:fail('EDITOR_QUANTITY_WIP_PROTECTED')
 try:
  if not isinstance(value,str):raise ValueError()
  q=Decimal(value);minimum=Decimal(str(p['minimum']));multiple=Decimal(str(p['multiple']));maximum=Decimal(str(p['maximum'])) if p['maximum'] is not None else None
  if not q.is_finite() or q<=0 or q<minimum or (maximum is not None and q>maximum) or (multiple>0 and q%multiple):raise ValueError()
 except (ValueError,ArithmeticError):fail('EDITOR_QUANTITY_BATCH')
 return format(q.normalize(),'f')

def final_machine(data,operation,rid,start,end):
 resources={r['id']:r for r in data['resources']};r=resources.get(rid)
 if not r or r.get('role')!='MACHINE':fail('UNQUALIFIED_RESOURCE')
 if operation.get('machine_skill_id'):
  if r.get('pool_id')!=operation['resource_id'] or not any(q['resource_id']==rid and q['skill_id']==operation['machine_skill_id'] and q.get('priority',1)>0 and at(q['effective_from'])<=start and end<=at(q['effective_to']) for q in data.get('resource_skills',[])):fail('UNQUALIFIED_RESOURCE')
 elif rid!=operation['resource_id']:fail('UNQUALIFIED_RESOURCE')

def atomic_task(data,t,patch):
 if not valid_source(t):fail('EDITOR_SOURCE_TASK_IDENTITY')
 if at(t['start_at'])<at(data['planning_at']):fail('EDITOR_WINDOW_NOT_FUTURE')
 if not patch or set(patch)-{'time','quantity','resource_id','recipe_id'}:fail('EDITOR_INVALID_CHANGE')
 identity=data['foundation_projection']['native_operation_map'][t['native_operation']]
 if not remaining_task(data,identity['recipe_id'],identity['operation_id'],t['quantity']):fail('EDITOR_WIP_REMAINING_TASK_REQUIRED')
 old=recipe_for(data,t);recipe=old
 if 'recipe_id' in patch:
  if not any({old['id'],patch['recipe_id']}<=set(g['recipe_ids']) for g in validate_alternates(data)):fail('EDITOR_ROUTE_NOT_DECLARED')
  if not quantity_policy(data,t,old)['editable']:fail('EDITOR_WIP_ROUTE_PROTECTED')
  recipe=next(r for r in data['production_recipes'] if r['id']==patch['recipe_id'])
 operation=target_operation(data,t,recipe);q=checked_quantity(data,t,recipe,patch['quantity']) if 'quantity' in patch else t.get('root_quantity',t['quantity'])
 if recipe!=old:q=checked_quantity(data,t,recipe,str(q))
 changed=copy.deepcopy(patch)
 for k,oldvalue in [('quantity',str(t.get('root_quantity',t['quantity']))),('recipe_id',old['id'])]:
  if k in changed and (Decimal(changed[k])==Decimal(oldvalue) if k=='quantity' else changed[k]==oldvalue):changed.pop(k)
 if 'quantity' in changed:changed['quantity']=q
 if 'resource_id' in changed and changed['resource_id'] in t['resources']:changed.pop('resource_id')
 a,b=at(t['start_at']),at(t['end_at']);clock=changed.get('time')
 if clock:
  if not isinstance(clock,dict) or clock.get('mode') not in {'START_ANCHOR','END_ANCHOR','WINDOW'}:fail('EDITOR_TIME_MODE')
  mode=clock['mode'];required={'mode','start_at'} if mode=='START_ANCHOR' else {'mode','end_at'} if mode=='END_ANCHOR' else {'mode','start_at','end_at'}
  if set(clock)!=required:fail('EDITOR_INVALID_CHANGE')
  seconds=Decimal(str(operation.get('fixed_seconds',0)))+Decimal(str(operation.get('seconds_per_unit',0)))*Decimal(q)
  if mode=='START_ANCHOR':a=at(clock['start_at']);b=a+timedelta(seconds=float(seconds))
  elif mode=='END_ANCHOR':b=at(clock['end_at']);a=b-timedelta(seconds=float(seconds))
  else:a,b=at(clock['start_at']),at(clock['end_at'])
  if a<at(data['planning_at']):fail('EDITOR_WINDOW_NOT_FUTURE')
  if not at(data['horizon']['start_at'])<=a<b<=at(data['horizon']['end_at']):fail('EDITOR_WINDOW_OUTSIDE_HORIZON')
  if mode=='WINDOW' and Decimal(str((b-a).total_seconds()))<seconds:fail('EDITOR_WINDOW_DURATION')
  if mode=='START_ANCHOR' and a==at(t['start_at']) and q==t.get('root_quantity',t['quantity']) and recipe==old:changed.pop('time')
 if not at(recipe['effective_from'])<=a<b<=at(recipe['effective_to']):fail('EDITOR_ROUTE_NOT_EFFECTIVE')
 if 'resource_id' in patch:final_machine(data,operation,patch['resource_id'],a,b)
 # A hard anchored interval must have source availability; native may extend
 # processing across shifts and audits certify the actual resulting endpoint.
 if clock:
  resources={r['id']:r for r in data['resources']};calendars={c['id']:c for c in data['calendars']}
  assigned=([patch['resource_id']] if patch.get('resource_id') else [rid for rid in t['resources'] if resources[rid].get('role')=='MACHINE'])
  if recipe==old:assigned+=[rid for rid in t['resources'] if resources[rid].get('role')=='OPERATOR']
  anchor=a if clock['mode']!='END_ANCHOR' else b-timedelta(microseconds=1)
  for rid in assigned:
   if not any(at(w['start_at'])<=anchor<at(w['end_at']) for w in calendars[resources[rid]['availability_calendar_id']]['available_intervals']):fail('EDITOR_WINDOW_OUTSIDE_SOURCE_CALENDAR')
  if operation['duration_model']=='FIXED_BATCH':
   calendar=calendars[resources[operation['resource_id']]['availability_calendar_id']]
   if not any(at(w['start_at'])==a for w in calendar['available_intervals']):fail('EDITOR_WINDOW_OUTSIDE_SOURCE_CALENDAR')
 if not changed:fail('EDITOR_UNCHANGED')
 native=next(name for name,v in data['foundation_projection']['native_operation_map'].items() if v==dict(recipe_id=recipe['id'],operation_id=operation['id']))
 return dict(kind='task',reference=t['id'],source_task=copy.deepcopy(t),changes=changed,target=dict(recipe_id=recipe['id'],operation_id=operation['id'],native_operation=native,quantity=str(q)),expected_window=dict(start_at=a.isoformat(),end_at=b.isoformat()))

def demand_event(data,e):
 if set(e)!={'kind','reference','action','demand_ids'} or e['reference']!='customer-demands' or e['action'] not in {'UNPLAN','FORWARD','BACKWARD'} or not isinstance(e['demand_ids'],list) or not e['demand_ids'] or len(set(e['demand_ids']))!=len(e['demand_ids']):fail('EDITOR_DEMAND_ACTION')
 rows={d['id']:d for d in data['demand_lines']};out=[]
 for id in e['demand_ids']:
  if id not in rows:fail('EDITOR_DEMAND_UNKNOWN')
  d=rows[id];source=next((r for r in data['foundation_projection']['source_tables']['demand_lines'] if r['id']==id),d)
  # Optional source facts remain in the authenticated Canonical tables even
  # when the bounded native projection has no corresponding input property.
  due=at(d['due_at']);latest=due+timedelta(seconds=d['maximum_lateness_seconds']);earliest=max(at(data['planning_at']),at(source.get('earliest_delivery_at',data['planning_at'])))
  anchor=earliest if e['action']=='FORWARD' else due
  if e['action']=='FORWARD' and anchor>latest:fail('EDITOR_DEMAND_NO_LEGAL_WINDOW')
  out.append(dict(demand_id=id,source_due_at=d['due_at'],source_maximum_lateness_seconds=d['maximum_lateness_seconds'],quantity=d['quantity'],minimum_shipment=d['minimum_shipment'],anchor_at=anchor.isoformat(),latest_at=latest.isoformat(),earliest_at=earliest.isoformat(),maximum_lateness_seconds=int((latest-anchor).total_seconds())))
 return dict(**copy.deepcopy(e),demands=out)

def apply_atomic(data,edits,reason,confirmation,source):
 if not contract(data):fail('EDITOR_ENTERPRISE_SCOPE_REQUIRED')
 if data['foundation_projection'].get('plan_editor_overlay'):fail('EDITOR_OVERLAY_ALREADY_PRESENT')
 source=source or []
 if any(not valid_source(t) for t in source) or len({t['id'] for t in source})!=len(source):fail('EDITOR_SOURCE_TASK_IDENTITY')
 if any(e['kind'] not in {'task','demand'} for e in edits):fail('EDITOR_COMPOUND_KIND_CONFLICT')
 events=[];used=set();demand_ids=set();roots={}
 for e in edits:
  if e['kind']=='demand':
   event=demand_event(data,e)
   if demand_ids.intersection(e['demand_ids']):fail('EDITOR_DEMAND_ACTION_CONFLICT')
   demand_ids.update(e['demand_ids']);events.append(event)
  else:
   t=next((t for t in source if t['id']==e['reference']),None)
   if not t:fail('EDITOR_SOURCE_TASK_REQUIRED')
   if set(e)!={'kind','reference','changes'} or e['reference'] in used:fail('EDITOR_DUPLICATE_OBJECT')
   root=t.get('root_native_reference',t['source_native_reference'])
   roots.setdefault(root,[]).append(e['changes'])
   used.add(t['id']);events.append(atomic_task(data,t,e['changes']))
 # A root rebuild invalidates all original sibling native pointers. Reject
 # the entire draft in either order until a single root-level intent owns it.
 if any(len(patches)>1 and any({'quantity','recipe_id'}.intersection(p) for p in patches) for patches in roots.values()):fail('EDITOR_ROOT_CHANGE_CONFLICT')
 if used!={t['id'] for t in source}:fail('EDITOR_UNUSED_SOURCE_TASK')
 if any(demand_ids.intersection(t.get('demand_ids') or task_demands(data,recipe_for(data,t)['id'])) for t in source):fail('EDITOR_TASK_DEMAND_CONFLICT')
 before={d['id']:d['priority'] for d in data['demand_lines']};meta=data['foundation_projection']
 meta['plan_editor_overlay']=dict(revision=SCOPE,baseline_projection_sha256=meta['projection_sha256'],before_priorities=before,after_priorities=before,events=copy.deepcopy(events),simulation_only=True,execution_authorized=False)
 from p22.foundation_projection import seal
 seal(data);validate_overlay(data)
 return dict(action='simulate_plan_editor',intent='专业排程联合调整',edits=copy.deepcopy(edits),events=events,source_tasks=copy.deepcopy(source),reason=reason.strip(),confirmation=copy.deepcopy(confirmation),**AUTHORITY)

def edit_options(data,view):
 tasks=source_tasks(data,view);result=[];recipes={r['id']:r for r in data['production_recipes']};groups=validate_alternates(data)
 for t in tasks:
  recipe=recipe_for(data,t);operation=spec(data,t);resources=[]
  for r in data['resources']:
   if r.get('role')!='MACHINE' or (r['id']!=operation['resource_id'] and r.get('pool_id')!=operation['resource_id']):continue
   try:final_machine(data,operation,r['id'],at(t['start_at']),at(t['end_at']));qualified=True
   except ValueError:qualified=False
   resources.append(dict(resource_id=r['id'],qualified=qualified,reason=None if qualified else 'UNQUALIFIED_RESOURCE'))
  alternatives=next((g['recipe_ids'] for g in groups if recipe['id'] in g['recipe_ids']),[recipe['id']])
  route_options=[dict(recipe_id=rid,route_id=recipes[rid]['routing_revision_id'],bom_id=recipes[rid]['bom_revision_id'],effective_from=recipes[rid]['effective_from'],effective_to=recipes[rid]['effective_to'],allowed=quantity_policy(data,t,recipe)['editable'],reason=None) for rid in alternatives]
  result.append(dict(reference=t['id'],operation_task_id=t['operation_task_id'],root_reference=t.get('root_native_reference',t['source_native_reference']),current_recipe_id=recipe['id'],affected_operation_ids=t.get('affected_operation_ids',[]),source_status='proposed',editable=True,blocked_reasons=[],time=dict(modes=['START_ANCHOR','END_ANCHOR','WINDOW'],planning_at=data['planning_at'],horizon=data['horizon']),quantity=quantity_policy(data,t,recipe),resource_options=resources,route_options=route_options))
 return dict(contract_revision=ATOMIC_SCOPE,task_options=result,demand_options=[dict(demand_id=d['id'],order_ref=d.get('order_ref',d['id']),source_due_at=d['due_at'],quantity=d['quantity'],minimum_shipment=d['minimum_shipment'],maximum_lateness_seconds=d['maximum_lateness_seconds'],actions=['UNPLAN','FORWARD','BACKWARD'],blocked_reasons=[]) for d in data['demand_lines']])

def enable_research_scope(data,source,optimizer=False,alternate_groups=None,research_extension=None):
 from p22.foundation_projection import seal,validate
 validate(data);meta=data['foundation_projection']
 if meta.get('editor_scope') or meta.get('plan_editor_overlay'):fail('EDITOR_SCOPE_ALREADY_PRESENT')
 meta['editor_scope']=dict(revision=SCOPE,source=source,source_facts_sha256=digest(meta['source_tables']),original_projection_sha256=meta['projection_sha256'])
 if alternate_groups is not None:meta['editor_scope']['alternate_groups']=copy.deepcopy(alternate_groups)
 if research_extension is not None:
  added={k:[r for r in rows if r.get('source_id')=='COMPLETENESS-02-DECLARATION'] for k,rows in meta['source_tables'].items()}
  meta['editor_scope']['research_extension']=dict(declaration=copy.deepcopy(research_extension),added_source_id='COMPLETENESS-02-DECLARATION',added_rows_sha256=digest(added))
  research_base_tables(data)
 if optimizer:meta['editor_scope']['optimizer_revision']='aps-optimizer-v1'
 seal(data)

def contract(data):
 meta=data['foundation_projection'];s=meta.get('editor_scope')
 if not s:return None
 if not isinstance(s,dict) or set(s)-{'revision','source','source_facts_sha256','original_projection_sha256','optimizer_revision','alternate_groups','research_extension'} or ('optimizer_revision' in s and s['optimizer_revision']!='aps-optimizer-v1') or not {'revision','source','source_facts_sha256','original_projection_sha256'}<=set(s) or s['revision']!=SCOPE or not s['source']:fail('EDITOR_ENTERPRISE_SCOPE')
 validate_alternates(data)
 research_base_tables(data)
 if meta.get('scope')!='RESEARCH_SIMULATION_ONLY' or meta['policy'].get('execution_authorized') is not False or s['source_facts_sha256']!=digest(meta['source_tables']):fail('EDITOR_ENTERPRISE_SOURCE')
 restored=copy.deepcopy(data);m=restored['foundation_projection'];overlay=m.pop('plan_editor_overlay',None)
 if overlay:
  for d in restored['demand_lines']:d['priority']=overlay['before_priorities'][d['id']]
 m.pop('editor_scope');m['projection_sha256']=s['original_projection_sha256']
 from p22.foundation_projection import projection_hash
 if projection_hash(restored)!=s['original_projection_sha256']:fail('EDITOR_ENTERPRISE_BASELINE_CHANGED')
 return dict(tasks=[],sequence=dict(demand_ids=[d['id'] for d in data['demand_lines']],goal='setup_then_delay',max_candidates=3,delay_cap_seconds=0),setup=dict(source='original foundation setup owner'),
             atomic_revision=ATOMIC_SCOPE,quantity_editing=dict(editable=True,requires_task_policy=True,reason=None,basis='PROPOSED_SUPPLY_ROOT_WITHIN_SOURCE_BATCH;WIP_ACTUAL_QUANTITY_PROTECTED'))

def task_id(row):
 return 'TASK-'+digest({k:row[k] for k in ['baseline_run_id','native_operation','quantity','start_at','end_at','resources']})[:24]

def remaining_task(data,recipe_id,operation_id,quantity):
 """WIP selectors expose the authenticated remaining tranche, never actual work."""
 meta=data['foundation_projection']
 recipes=[r for r in meta.get('source_tables',{}).get('recipes',[]) if r['id']==recipe_id]
 jobs=[r for r in meta.get('continuity_scope',{}).get('wip_jobs',[]) if r.get('recipe_id')==recipe_id or any(o.get('recipe_id')==recipe_id for o in r.get('stage_operations',[]))]
 continuity=recipe_id.startswith('CONT-CONTINUE:') or any(r.get('continuity_job_id') for r in recipes)
 if not continuity:return True
 if len(recipes)!=1 or len(jobs)!=1:return False
 r,j=recipes[0],jobs[0]
 if r.get('progress_contract')=='stage-flow-v2':
  try:
   bound=Decimal(str(r.get('remaining_quantity',0)));leaf=Decimal(str(quantity))
   return r.get('continuation_kind')=='STAGE_PROCESSING' and bound.is_finite() and leaf.is_finite() and 0<leaf<=bound and any(v.get('recipe_id')==recipe_id and v.get('operation_id')==operation_id and Decimal(str(v.get('quantity',0)))==bound for v in j.get('stage_operations',[]))
  except (TypeError,ValueError,ArithmeticError):return False
 # The native leaf uses a job-scoped remaining-operation identity. Its source
 # row explicitly binds that leaf to the original business operation; suffix
 # similarity cannot establish permission to edit a completed or foreign leaf.
 operations=[o for o in meta.get('source_tables',{}).get('operations',[]) if o['id']==operation_id and o.get('recipe_id')==recipe_id]
 if len(operations)!=1:return False
 actual_operation_id=operations[0].get('actual_operation_id')
 try:
  remaining=Decimal(str(j['remaining_quantity']));original=Decimal(str(j['original_job_quantity']));received=Decimal(str(j['finished_received_quantity']))
  return (remaining.is_finite() and original.is_finite() and received.is_finite() and 0<remaining<=original
          and received>=0 and received+remaining==original and Decimal(str(quantity))==remaining
          and Decimal(str(r['remaining_quantity']))==remaining
          and actual_operation_id in j['remaining_operation_ids'] and actual_operation_id in r['remaining_operation_ids'])
 except (KeyError,TypeError,ValueError,ArithmeticError):return False

def source_tasks(data,view):
 if not contract(data):return []
 result=view['result'];parents={m['id']:m['recipe_id'] for m in result['manufacture_suggestions']};mapping=data['foundation_projection']['native_operation_map'];machines={r['id'] for r in data['resources'] if r.get('role')=='MACHINE'};rows=[]
 stages={r['id']:Decimal(str(r['remaining_quantity'])) for r in data['foundation_projection']['source_tables']['recipes'] if r.get('progress_contract')=='stage-flow-v2'}
 totals={rid:sum((Decimal(str(o['quantity'])) for o in result['operations'] if parents.get(o['manufacture_id'])==rid),Decimal(0)) for rid in stages}
 for op in result['operations']:
  recipe=parents.get(op['manufacture_id'])
  if not recipe:continue
  # A certified stage can legitimately split across several native lots.
  # Authorize each original leaf only within the unchanged aggregate tranche;
  # the continuity Owner separately requires complete stage/flow closure.
  if recipe in stages and (not totals[recipe].is_finite() or totals[recipe]>stages[recipe]):continue
  if op.get('status','proposed')!='proposed' or at(op['start_at'])<at(data['planning_at']) or not remaining_task(data,recipe,op['operation_id'],op['quantity']):continue
  native=next((name for name,v in mapping.items() if v['recipe_id']==recipe and v['operation_id']==op['operation_id']),None)
  resources=sorted({l['resource_id'] for l in result['resource_loads'] if l['operation_id']==op['id']})
  if not native or not machines.intersection(resources):continue
  row=dict(baseline_run_id=view['run']['solve_run_id'],native_operation=native,quantity=format(Decimal(op['quantity']).normalize(),'f'),start_at=op['start_at'],end_at=op['end_at'],resources=resources,source_native_reference=op['id'].rsplit(':',1)[-1],status='proposed')
  parent=next(m for m in result['manufacture_suggestions'] if m['id']==op['manufacture_id'])
  row.update(root_native_reference=str(parent.get('engine_ref',op['manufacture_id'].rsplit(':',1)[-1])),root_quantity=parent.get('quantity',row['quantity']),recipe_id=recipe,operation_id=op['operation_id'],operation_task_id=op['id'],manufacture_id=op['manufacture_id'],affected_operation_ids=[v['id'] for v in result['operations'] if v['manufacture_id']==op['manufacture_id']],demand_ids=task_demands(data,recipe,view,op['manufacture_id']))
  row['id']=task_id(row);rows.append(row)
 counts={r['id']:sum(v['id']==r['id'] for v in rows) for r in rows}
 return [r for r in rows if counts[r['id']]==1]

def spec(data,row):
 identity=data['foundation_projection']['native_operation_map'].get(row['native_operation'])
 if not identity:fail('EDITOR_SOURCE_OPERATION_UNKNOWN')
 return next(o for rt in data['routing_revisions'] for o in rt['operations'] if o['id']==identity['operation_id'])

def validate_machine(data,t,rid):
 resources={r['id']:r for r in data['resources']};r=resources.get(rid);o=spec(data,t)
 skill=o.get('machine_skill_id')
 if not r or r.get('role')!='MACHINE' or r.get('pool_id')!=o['resource_id'] or not skill or not any(q['resource_id']==rid and q['skill_id']==skill and at(q['effective_from'])<=at(t['start_at']) and at(q['effective_to'])>=at(t['end_at']) for q in data['resource_skills']):fail('UNQUALIFIED_RESOURCE')

def window_end(data,t,start):
 """Screen an unchanged observed duration; native feasibility remains mandatory."""
 o=spec(data,t);a=at(start);old_a,old_b=at(t['start_at']),at(t['end_at']);span=old_b-old_a;b=a+span
 if old_a<at(data['planning_at']) or a<at(data['planning_at']):fail('EDITOR_WINDOW_NOT_FUTURE')
 if not at(data['horizon']['start_at'])<=a<b<=at(data['horizon']['end_at']):fail('EDITOR_WINDOW_OUTSIDE_HORIZON')
 resources={r['id']:r for r in data['resources']};calendars={c['id']:c for c in data['calendars']}
 if o['duration_model']=='FIXED_BATCH':
  if span!=timedelta(seconds=float(o['fixed_seconds'])):fail('EDITOR_WINDOW_MODEL_NOT_CERTIFIED')
  calendar=calendars[resources[o['resource_id']]['availability_calendar_id']]
  if not any(at(w['start_at'])==a for w in calendar['available_intervals']):fail('EDITOR_WINDOW_OUTSIDE_SOURCE_CALENDAR')
 elif o['duration_model']=='FIXED_PLUS_PER_UNIT':
  required=Decimal(str(o['fixed_seconds']))+Decimal(str(o['seconds_per_unit']))*Decimal(t['quantity'])
  if required<=0 or Decimal(str(span.total_seconds()))<required:fail('EDITOR_WINDOW_MODEL_NOT_CERTIFIED')
  assigned=[resources.get(rid) for rid in t['resources']]
  if any(r is None for r in assigned) or not any(r.get('role')=='MACHINE' for r in assigned) or not any(r.get('role')=='OPERATOR' for r in assigned):fail('EDITOR_WINDOW_RESOURCE_EVIDENCE_REQUIRED')
  for r in assigned:
   intervals=sorted(calendars[r['availability_calendar_id']]['available_intervals'],key=lambda w:at(w['start_at']))
   covered=a
   for w in intervals:
    x,y=at(w['start_at']),at(w['end_at'])
    if x<=covered<y:covered=max(covered,y)
    if covered>=b:break
   if covered<b:fail('EDITOR_WINDOW_OUTSIDE_SOURCE_CALENDAR')
   skill=o.get('machine_skill_id') if r.get('role')=='MACHINE' else o.get('operator_requirement',{}).get('skill_id') if r.get('role')=='OPERATOR' else None
   if skill and not any(q['resource_id']==r['id'] and q['skill_id']==skill and at(q['effective_from'])<=a and b<=at(q['effective_to']) for q in data.get('resource_skills',[])):fail('UNQUALIFIED_RESOURCE')
 else:fail('EDITOR_WINDOW_MODEL_NOT_CERTIFIED')
 return b.isoformat()

def apply(data,edits,reason,confirmation,source):
 edits=normalize_edits(edits)
 if any(e['kind'] in {'task','demand'} for e in edits):return apply_atomic(data,edits,reason,confirmation,source)
 if not contract(data):fail('EDITOR_ENTERPRISE_SCOPE_REQUIRED')
 if data['foundation_projection'].get('plan_editor_overlay'):fail('EDITOR_OVERLAY_ALREADY_PRESENT')
 if source is None:source=[]
 if not isinstance(source,list) or len(source)>(128 if any(e['kind']=='optimized_schedule' for e in edits) else 24):fail('EDITOR_SOURCE_TASK_IDENTITY')
 for t in source:
  if not valid_source(t):fail('EDITOR_SOURCE_TASK_IDENTITY')
  spec(data,t)
  identity=data['foundation_projection']['native_operation_map'][t['native_operation']]
  if at(t['start_at'])<at(data['planning_at']) or not remaining_task(data,identity['recipe_id'],identity['operation_id'],t['quantity']):fail('EDITOR_WIP_REMAINING_TASK_REQUIRED')
 if len({t['id'] for t in source})!=len(source):fail('EDITOR_SOURCE_TASK_IDENTITY')
 before={d['id']:d['priority'] for d in data['demand_lines']};events=[]
 for e in edits:
  kind=e['kind']
  if kind in {'optimized_schedule','native_strategy'}:
   if len(edits)!=1:fail('OPTIMIZER_COMPOUND_ONLY')
   from optimization.contract import event
   events.append(event(data,e,source))
  elif kind=='sequence':
   ids=[d['id'] for d in sorted(data['demand_lines'],key=lambda d:d['priority'])]
   if set(e)!={'kind','reference','order'} or e['reference']!='manufacturing-sequence' or len(e['order'])!=len(set(e['order'])) or set(e['order'])!=set(ids):fail('EDITOR_SEQUENCE_POLICY_INVALID')
   if ids==e['order']:fail('EDITOR_UNCHANGED')
   for d in data['demand_lines']:d['priority']=e['order'].index(d['id'])+1
   events.append(dict(kind='sequence',reference=e['reference'],before=ids,after=e['order'],goal='setup_then_delay'))
  else:
   if kind not in {'proposed_window','machine'}:fail('EDITOR_PROTECTED_OR_UNSUPPORTED_OBJECT')
   t=next((t for t in source if t['id']==e['reference']),None)
   if not t:fail('EDITOR_SOURCE_TASK_REQUIRED')
   if kind=='machine':
    if set(e)!={'kind','reference','resource_id'}:fail('EDITOR_INVALID_CHANGE')
    validate_machine(data,t,e['resource_id'])
    if e['resource_id'] in t['resources']:fail('EDITOR_UNCHANGED')
    events.append(dict(kind=kind,reference=t['id'],source_task=copy.deepcopy(t),resource_id=e['resource_id']))
   else:
    if set(e)!={'kind','reference','start_at'}:fail('EDITOR_INVALID_CHANGE')
    o=spec(data,t);end=window_end(data,t,e['start_at'])
    if at(e['start_at'])==at(t['start_at']):fail('EDITOR_UNCHANGED')
    event=dict(kind=kind,reference=t['id'],source_task=copy.deepcopy(t),start_at=e['start_at'])
    if o['duration_model']=='FIXED_PLUS_PER_UNIT':event['end_at']=end
    events.append(event)
 used={e['reference'] for e in events if e['kind'] not in {'sequence','native_strategy','optimized_schedule'}}
 for e in events:
  if e['kind']=='optimized_schedule':used.update(a['reference'] for a in e['assignments'])
 if used!={t['id'] for t in source}:fail('EDITOR_UNUSED_SOURCE_TASK')
 meta=data['foundation_projection'];meta['plan_editor_overlay']=dict(revision=SCOPE,baseline_projection_sha256=meta['projection_sha256'],before_priorities=before,after_priorities={d['id']:d['priority'] for d in data['demand_lines']},events=copy.deepcopy(events),simulation_only=True,execution_authorized=False)
 from p22.foundation_projection import seal
 seal(data);validate_overlay(data)
 return dict(action='simulate_plan_editor',intent='专业排程联合调整',edits=copy.deepcopy(edits),events=events,source_tasks=copy.deepcopy(source),reason=reason.strip(),confirmation=copy.deepcopy(confirmation),**AUTHORITY)

def validate_overlay(data):
 contract(data);meta=data['foundation_projection'];v=meta['plan_editor_overlay']
 if set(v)!={'revision','baseline_projection_sha256','before_priorities','after_priorities','events','simulation_only','execution_authorized'} or v['revision']!=SCOPE or v['simulation_only'] is not True or v['execution_authorized'] is not False:fail('EDITOR_OVERLAY_INVALID')
 if v['after_priorities']!={d['id']:d['priority'] for d in data['demand_lines']}:fail('EDITOR_PRIORITY_CHANGED')
 restored=copy.deepcopy(data);m=restored['foundation_projection'];m.pop('plan_editor_overlay')
 for d in restored['demand_lines']:d['priority']=v['before_priorities'][d['id']]
 m['projection_sha256']=v['baseline_projection_sha256']
 from p22.foundation_projection import projection_hash,validate
 if projection_hash(restored)!=v['baseline_projection_sha256']:fail('EDITOR_OVERLAY_BASELINE_MISMATCH')
 validate(restored)
 return v

def audit_native(data,raw,run_id):
 from p22.foundation_audit import audit
 checked=audit(data,raw,run_id=run_id)
 issues=list(checked['issues']);missing=list(checked['missing_evidence']);post=raw.get('fixed_batch_post',{});assignments=[]
 def check(ok,code):
  if not ok:issues.append(dict(code=code))
 check(post.get('run_id')==run_id and post.get('input_hash')==digest(data),'EDITOR_NATIVE_IDENTITY')
 overlay=data['foundation_projection'].get('plan_editor_overlay');receipt=post.get('editor_receipt')
 if any(e['kind'] in {'task','demand'} for e in (overlay or {}).get('events',[])):
  return audit_atomic(data,raw,run_id,checked)
 events=[]
 for e in (overlay or {}).get('events',[]):
  if e['kind']=='optimized_schedule':events.extend(dict(a,kind='optimized_schedule') for a in e['assignments'])
  elif e['kind'] not in {'sequence','native_strategy'}:events.append(e)
 if events:
  if not isinstance(receipt,dict):missing.append('editor_receipt')
  else:
   changes=receipt.get('changes',[]);check(len(changes)==len(events),'EDITOR_NATIVE_CHANGES_INCOMPLETE')
   for e in events:
    r=next((r for r in changes if r['reference']==e['reference']),None)
    if not r:check(False,'EDITOR_NATIVE_CHANGE_MISSING');continue
    t=e['source_task'];before=r['before'];after=r['after'];assignments.append(copy.deepcopy(r))
    from zoneinfo import ZoneInfo
    zone=ZoneInfo(data['site']['timezone'])
    def local(v):return at(v).astimezone(zone).replace(tzinfo=None).isoformat()
    check(before['operation']==t['native_operation'] and Decimal(before['quantity'])==Decimal(t['quantity']) and before['start']==local(t['start_at']) and before['end']==local(t['end_at']) and sorted(before['resources'])==t['resources'] and before['status']=='proposed','EDITOR_NATIVE_SOURCE_MISMATCH')
    check(all(before[k]==after[k] for k in ['reference','operation','quantity','status']),'EDITOR_NATIVE_FACT_DRIFT')
    if e['kind']=='machine':check(e['resource_id'] in after['resources'] and before['start']==after['start'] and before['end']==after['end'],'EDITOR_ACTUAL_MACHINE_MISMATCH')
    elif e['kind']=='optimized_schedule':
     check(after['start']==local(e['start_at']) and after.get('setup_end')==local(e['setup_end_at']) and after['end']==local(e['end_at']) and e['resource_id'] in after['resources'] and set(before['resources'])-set(r['id'] for r in data['resources'] if r.get('role')=='MACHINE')==set(after['resources'])-set(r['id'] for r in data['resources'] if r.get('role')=='MACHINE'),'OPTIMIZER_NATIVE_INTENT_NOT_ADOPTED')
    else:check(after['start']==local(e['start_at']) and before['resources']==after['resources'] and ('end_at' not in e or after['end']==local(e['end_at'])),'EDITOR_ACTUAL_WINDOW_MISMATCH')
 try:measured=setup_metric(data,post)
 except (ValueError,KeyError,TypeError,ArithmeticError) as exc:
  issues.append(dict(code='EDITOR_SETUP_EVIDENCE',detail=str(exc)));measured=dict(setup_seconds=None,setup_sequence=[])
 return dict(status='FAIL' if issues else 'NOT_CHECKED' if missing else 'PASS',issues=issues,missing_evidence=missing,setup_seconds=measured['setup_seconds'] if not issues and not missing else None,setup_sequence=measured['setup_sequence'],setup_metric_revision=SETUP_METRIC_REVISION,scope='COMPLETE_ENTERPRISE_PROPOSED_RESEARCH',assignments=assignments,execution_authorized=False)

def delivery_receipt_matches(data,raw,demand_id,after):
 """Compare an incremental receipt with the same run's final native export."""
 from collections import Counter
 from datetime import datetime
 from zoneinfo import ZoneInfo
 from p22.foundation_audit import table,quantity
 rows=table(raw.get('deliveryorder'))
 if rows is None:return None
 zone=ZoneInfo(data['site']['timezone'])
 def local(value):
  value=datetime.fromisoformat(str(value).replace('Z','+00:00'))
  return (value.replace(tzinfo=zone) if value.tzinfo is None else value.astimezone(zone)).replace(tzinfo=None)
 try:
  actual=Counter((str(r['reference']),quantity(r['quantity']),r['status'],local(r['startdate']),local(r['enddate'])) for r in rows if r.get('demand')==demand_id)
  expected=Counter((str(r['reference']),quantity(r['quantity']),r['status'],local(r['start']),local(r['end'])) for r in after)
  return actual==expected
 except (ValueError,KeyError,TypeError,ArithmeticError):return False


def audit_atomic(data,raw,run_id,checked):
 post=raw.get('fixed_batch_post',{});receipt=post.get('editor_receipt');events=data['foundation_projection']['plan_editor_overlay']['events'];issues=list(checked['issues']);missing=list(checked['missing_evidence'])
 def check(ok,code,ref=None):
  if not ok:issues.append(dict(code=code,reference=ref))
 def delivery_readback(demand_id,after):
  match=delivery_receipt_matches(data,raw,demand_id,after)
  if match is None:missing.append('raw.deliveryorder.editor:'+demand_id)
  else:check(match,'EDITOR_NATIVE_DEMAND_READBACK',demand_id)
 check(post.get('run_id')==run_id and post.get('input_hash')==digest(data),'EDITOR_NATIVE_IDENTITY')
 if not isinstance(receipt,dict):return dict(status='NOT_CHECKED',issues=issues,missing_evidence=missing+['editor_receipt'],assignments=[],execution_authorized=False)
 tasks=[e for e in events if e['kind']=='task'];assignments=receipt.get('changes',[])
 check(receipt.get('revision')==ATOMIC_SCOPE and len(assignments)==len(tasks) and receipt.get('atomic_source_bindings')==len(tasks),'EDITOR_NATIVE_ATOMIC_BINDINGS')
 from zoneinfo import ZoneInfo
 zone=ZoneInfo(data['site']['timezone'])
 def local(v):return at(v).astimezone(zone).replace(tzinfo=None).isoformat()
 plans={str(p['reference']):p for p in post.get('operationplans',[])}
 for e in tasks:
  rows=[r for r in assignments if r['reference']==e['reference']]
  if len(rows)!=1:check(False,'EDITOR_NATIVE_CHANGE_MISSING',e['reference']);continue
  r=rows[0];t=e['source_task'];before=r['before'];after=r['after'];patch=e['changes'];target=e['target'];root=r.get('root_after',{})
  check(before['operation']==t['native_operation'] and Decimal(before['quantity'])==Decimal(t['quantity']) and before['start']==local(t['start_at']) and before['end']==local(t['end_at']) and sorted(before['resources'])==t['resources'] and before['status']=='proposed','EDITOR_NATIVE_SOURCE_MISMATCH',t['id'])
  actual=plans.get(str(after['reference']))
  check(actual is not None and all(actual.get(k)==after.get(k) for k in ['operation','quantity','status','start','end','resources']),'EDITOR_NATIVE_RECEIPT_READBACK',t['id'])
  actual_root=plans.get(str(root.get('reference')))
  check(actual_root is not None and all(actual_root.get(k)==root.get(k) for k in ['operation','quantity','status','start','end','resources']),'EDITOR_NATIVE_ROOT_READBACK',t['id'])
  check(after['status']=='proposed' and after['operation']==target['native_operation'] and Decimal(after['quantity'])==Decimal(target['quantity']) and Decimal(root.get('quantity',0))==Decimal(target['quantity']),'EDITOR_NATIVE_INTENT_NOT_ADOPTED',t['id'])
  if patch.get('resource_id'):check(patch['resource_id'] in after['resources'],'EDITOR_ACTUAL_MACHINE_MISMATCH',t['id'])
  clock=patch.get('time')
  if clock:
   if clock['mode']=='START_ANCHOR':check(after['start']==local(clock['start_at']),'EDITOR_ACTUAL_WINDOW_MISMATCH',t['id'])
   elif clock['mode']=='END_ANCHOR':check(after['end']==local(clock['end_at']),'EDITOR_ACTUAL_WINDOW_MISMATCH',t['id'])
   else:check(local(clock['start_at'])<=after['start']<after['end']<=local(clock['end_at']),'EDITOR_ACTUAL_WINDOW_MISMATCH',t['id'])
  if {'quantity','recipe_id'}.intersection(patch):
   private=[q for q in receipt.get('private_requests',[]) if q['reference']==e['reference']]
   check(len(private)==1 and private[0].get('removed_before_export') is True and private[0].get('extra_fg_consumption') is False and private[0].get('retained_in_source') is False and private[0].get('candidate_root')==str(root.get('reference')) and private[0].get('operation')==target['recipe_id'] and Decimal(private[0].get('quantity',0))==Decimal(target['quantity']),'EDITOR_PRIVATE_REQUEST_LIFECYCLE',t['id'])
   check(any(c['method']=='ORIGINAL_FINITE_SOLVER_MRP' for c in receipt.get('native_calls',[])),'EDITOR_NATIVE_REPLAN_REQUIRED',t['id'])
   for demand_id in t.get('demand_ids',[]):
    replan=[x for x in receipt.get('affected_customer_replans',[]) if x.get('demand_id')==demand_id]
    check(len(replan)==1 and replan[0].get('source_facts_preserved') is True and any(c['method']=='NATIVE_SOLVER_DELETE_DEMAND' and c['object']==demand_id for c in receipt.get('native_calls',[])) and any(c['method']=='ORIGINAL_FINITE_SOLVER_MRP' and c['object']=='affected_customer:'+demand_id for c in receipt.get('native_calls',[])),'EDITOR_AFFECTED_CUSTOMER_REPLAN_REQUIRED',demand_id)
    if len(replan)==1:delivery_readback(demand_id,replan[0]['after'])
 names=post.get('editor_native_demand_names')
 if names is None:missing.append('editor_native_demand_names')
 else:check(not any(n.startswith('EDITOR-REQUEST:') for n in names),'EDITOR_PRIVATE_DEMAND_LEAK')
 for e in events:
  if e['kind']!='demand':continue
  for d in e['demands']:
   rows=[r for r in receipt.get('demands',[]) if r['demand_id']==d['demand_id'] and r['action']==e['action']]
   if len(rows)!=1:check(False,'EDITOR_NATIVE_DEMAND_RECEIPT',d['demand_id']);continue
   r=rows[0];check(r.get('source_due_preserved') is True and r.get('source_maxlateness_preserved') is True,'EDITOR_SOURCE_DEMAND_CHANGED',d['demand_id'])
   delivery_readback(d['demand_id'],r['after'])
   if e['action']=='UNPLAN':check(not any(x['status']=='proposed' for x in r['after']),'EDITOR_UNPLAN_NOT_ADOPTED',d['demand_id'])
   else:check(all(local(d['earliest_at'])<=x['end']<=local(d['latest_at']) for x in r['after']),'EDITOR_DEMAND_HARD_WINDOW',d['demand_id'])
 try:measured=setup_metric(data,post)
 except (ValueError,KeyError,TypeError,ArithmeticError):measured=dict(setup_seconds=None,setup_sequence=[]);missing.append('editor_setup_metric')
 return dict(status='FAIL' if issues else 'NOT_CHECKED' if missing else 'PASS',issues=issues,missing_evidence=missing,assignments=assignments,demands=receipt.get('demands',[]),native_calls=receipt.get('native_calls',[]),private_requests=receipt.get('private_requests',[]),setup_seconds=measured['setup_seconds'],setup_sequence=measured['setup_sequence'],scope='ATOMIC_NATIVE_EDITOR',execution_authorized=False)


SETUP_METRIC_REVISION='foundation-setup-owned-v2'

def setup_metric(data,post):
 """Count only setup-owned operations; -1 is never a negative duration.

 The original Foundation auditor independently authenticates all transitions.
 A native no-event sentinel is normalized only for a zero rule and empty
 setup interval. Other negative, missing or nonfinite evidence is refused.
 """
 from datetime import datetime
 native_at=datetime.fromisoformat
 meta=data['foundation_projection'];mapping=meta['native_operation_map'];states=meta['operation_setups'];initial=meta['resource_initial_setups'];sequence=[];groups={}
 for p in post.get('operationplans',[]):
  identity=mapping.get(p.get('operation'),{});state=states.get(identity.get('operation_id'))
  if state is None:continue
  machines=[r for r in p.get('resources',[]) if r in initial]
  if len(machines)!=1:fail('EDITOR_SETUP_MACHINE_EVIDENCE')
  groups.setdefault(machines[0],[]).append(p)
 for rid,rows in sorted(groups.items()):
  previous=initial[rid]
  for p in sorted(rows,key=lambda p:(native_at(p['start']),native_at(p['end']),str(p['reference']))):
   state=states[mapping[p['operation']]['operation_id']]
   rules=[r for r in meta['setup_transitions'] if (r['from_state'],r['to_state'])==(previous,state)]
   if len(rules)!=1 or p.get('setup_duration_seconds') is None:fail('EDITOR_SETUP_RULE_EVIDENCE')
   duration=Decimal(str(p['setup_duration_seconds']));required=Decimal(str(rules[0]['duration_seconds']))
   if duration==-1 and required==0 and all(p.get(k) is not None and native_at(p[k])==native_at(p['start']) for k in ['setup_start','setup_end']):duration=Decimal(0)
   if not duration.is_finite() or duration<0 or duration!=required:fail('EDITOR_SETUP_DURATION_EVIDENCE')
   sequence.append(dict(native_reference=p['reference'],operation=p['operation'],quantity=p['quantity'],state=state,start_at=p['start'],end_at=p['end'],setup_seconds=str(duration),resource_id=rid))
   previous=state
 return dict(setup_seconds=str(sum((Decimal(p['setup_seconds']) for p in sequence),Decimal(0))),setup_sequence=sequence,setup_metric_revision=SETUP_METRIC_REVISION)



def preflight(data,view,edits):
 edits=normalize_edits(edits)
 if any(e['kind'] in {'task','demand'} for e in edits):
  rows=source_tasks(data,view);selected=[next((r for r in rows if r['id']==e['reference']),None) for e in edits if e['kind']=='task']
  if any(t is None for t in selected):fail('EDITOR_SOURCE_TASK_REQUIRED')
  selected=list({t['id']:t for t in selected}.values())
  if view.get('interpretation',{}).get('schedule_check')!='PASS' or view.get('interpretation',{}).get('material_validation',{}).get('status')!='PASS':fail('EDITOR_BASELINE_AUDIT_REQUIRED')
  candidate=copy.deepcopy(data);apply_atomic(candidate,edits,'preflight',None,selected)
  return selected
 reject_quantity_edits(edits)
 interpretation=view.get('interpretation',{})
 if interpretation.get('schedule_check')!='PASS' or interpretation.get('material_validation',{}).get('status')!='PASS':fail('EDITOR_BASELINE_AUDIT_REQUIRED')
 rows=source_tasks(data,view);selected=[]
 for e in edits:
  if e['kind'] in {'sequence','native_strategy'}:continue
  if e['kind']=='optimized_schedule':
   from optimization.contract import require
   require(data)
   for a in e.get('assignments',[]):
    t=next((r for r in rows if r['id']==a.get('reference')),None)
    if not t:fail('EDITOR_SOURCE_TASK_REQUIRED')
    selected.append(t)
   continue
  t=next((r for r in rows if r['id']==e['reference']),None)
  if not t:fail('EDITOR_SOURCE_TASK_REQUIRED')
  selected.append(t)
  if e['kind']=='machine':
   validate_machine(data,t,e['resource_id']);a,b=at(t['start_at']),at(t['end_at'])
   if any(l['resource_id']==e['resource_id'] and l['operation_id'].rsplit(':',1)[-1]!=t['source_native_reference'] and at(l['start_at'])<b and a<at(l['end_at']) for l in view['result']['resource_loads']):fail('EDITOR_MACHINE_CAPACITY_CONFLICT')
  elif e['kind']=='proposed_window':
   end=window_end(data,t,e['start_at'])
   if spec(data,t)['duration_model']=='FIXED_PLUS_PER_UNIT':
    a,b=at(e['start_at']),at(end)
    moving={x['reference'] for x in edits if x['kind']=='proposed_window'}
    ignored={r['source_native_reference'] for r in rows if r['id'] in moving}
    if any(l['resource_id'] in t['resources'] and l['operation_id'].rsplit(':',1)[-1] not in ignored and at(l['start_at'])<b and a<at(l['end_at']) for l in view['result']['resource_loads']):fail('EDITOR_WINDOW_CAPACITY_CONFLICT')
 _validate_window_sequence(data,view,edits,selected)
 return list({t['id']:t for t in selected}.values())

def _validate_window_sequence(data,view,edits,selected):
 """A leaf move cannot cross its evidenced routing neighbours or another move."""
 windows={e['reference']:e for e in edits if e['kind']=='proposed_window'}
 if not windows:return
 changed={t['source_native_reference']:(t,windows[t['id']]) for t in selected if t['id'] in windows}
 operations=view['result']['operations'];intervals={}
 for op in operations:
  match=changed.get(op['id'].rsplit(':',1)[-1])
  intervals[op['id']]=(at(match[1]['start_at']),at(window_end(data,match[0],match[1]['start_at']))) if match else (at(op['start_at']),at(op['end_at']))
 for t,e in changed.values():
  if spec(data,t)['duration_model']!='FIXED_PLUS_PER_UNIT':continue
  current=next((op for op in operations if op['id'].rsplit(':',1)[-1]==t['source_native_reference']),None)
  if current is None:fail('EDITOR_SOURCE_TASK_REQUIRED')
  for other in operations:
   if other['id']==current['id']:continue
   if other['manufacture_id']==current['manufacture_id']:
    current_spec=spec(data,t);other_spec=next((o for route in data['routing_revisions'] for o in route['operations'] if o['id']==other['operation_id']),None)
    if (other['operation_id'] in current_spec.get('predecessors',[]) and intervals[other['id']][1]>intervals[current['id']][0]) or (other_spec and current['operation_id'] in other_spec.get('predecessors',[]) and intervals[current['id']][1]>intervals[other['id']][0]):fail('EDITOR_WINDOW_ROUTING_CONFLICT')
  a,b=intervals[current['id']]
  for native,(other,e2) in changed.items():
   if native==t['source_native_reference'] or not set(t['resources']).intersection(other['resources']):continue
   x,y=at(e2['start_at']),at(window_end(data,other,e2['start_at']))
   if a<y and x<b:fail('EDITOR_WINDOW_CAPACITY_CONFLICT')
