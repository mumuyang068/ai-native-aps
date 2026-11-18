"""Single-operation fixed batches and quantity-capacity windows.

This is a bounded P3 profile, not a general bucket resource or execution model.
Existing confirmed/approved MOs are immutable for this profile; previous
proposed suggestions carry no such authority and are erased by replanning.
"""
from collections import Counter,defaultdict
from datetime import datetime
from decimal import Decimal

PROFILE='fixed-batch-window-v1'
JOINT_PROFILE='fixed-batch-confirmed-supply-v1'

def joint_enabled(data):return data.get('rules',{}).get('manufacturing_profile') in {JOINT_PROFILE,'fixed-batch-priority-supply-v1'}

def enabled(data):return data.get('rules',{}).get('manufacturing_profile') in {PROFILE,JOINT_PROFILE,'fixed-batch-priority-supply-v1'}
def validate(data):
    from planning_adapter import InputError
    def require(ok,reason):
        if not ok:raise InputError('UNSUPPORTED_FIXED_BATCH: '+reason)
    routes={r['id']:r for r in data['routing_revisions']}
    recipes={r['id']:r for r in data['production_recipes']}
    resources={r['id']:r for r in data['resources']}
    calendars={c['id']:c for c in data['calendars']}
    items={i['id']:i for i in data['items']}
    mos=[m for m in data['existing_orders'] if m['type']=='MO']
    if joint_enabled(data):
        from confirmed_supply import validate_receipts
        pos=[p for p in data['existing_orders'] if p['type']=='PO']
        require(len(pos)==1 and bool(mos) and len(pos)+len(mos)==len(data['existing_orders']),
                'joint profile needs exactly one confirmed PO and protected MOs, no transfers')
        from priority_supply import enabled as priority_enabled,validate as validate_priority
        if priority_enabled(data):validate_priority(data)
        else:require(len(data['supply_sources'])==len(data['suppliers'])==1,'joint profile supports one evidenced supply path only')
        require(pos[0]['item_id'] not in {p['output_item_id'] for p in recipes.values()},'purchased material cannot also have a manufacturing route')
        validate_receipts({**data,'existing_orders':pos},allow_extra_qc=priority_enabled(data))
        source=data['supply_sources'][0]
        require(datetime.fromisoformat(source['effective_from'])<=datetime.fromisoformat(data['planning_at'])
                and datetime.fromisoformat(source['effective_to'])>=datetime.fromisoformat(data['horizon']['end_at']),
                'joint supply validity must cover the entire planning horizon')
    else:
        require(not data['supply_sources'] and not data['suppliers'],'this profile uses explicit opening stock only; PO combination is not supported')
    require(len({m['id'] for m in data['existing_orders']})==len(data['existing_orders']),'ambiguous existing order reference')
    require(data['rules'].get('move_approved_early')==0 and data['rules'].get('erase_previous_proposed') is True
            and data['rules'].get('fix_broken_supply_path') is False,'explicit non-movement, rolling erase and no repaired supply paths are required')
    for r in resources.values():
        require(r.get('capacity_model')=='WINDOW_QUANTITY' and 'parallel_units' not in r,'quantity-window capacity is not parallel machine capacity')
    for p in recipes.values():
        ops=routes[p['routing_revision_id']]['operations']
        require(len(ops)==1,'exactly one operation per recipe')
        o=ops[0];res=resources[o['resource_id']]
        require(o['duration_model']=='FIXED_BATCH' and Decimal(o['seconds_per_unit'])==0 and Decimal(o['fixed_seconds'])>0,'fixed batch duration, not per-unit conversion')
        minimum=Decimal(p['manufacturing_minimum_quantity']);maximum=Decimal(p.get('manufacturing_maximum_quantity','0'))
        require(minimum==maximum==Decimal(p['manufacturing_multiple'])>0,'fixed indivisible batch needs equal minimum, maximum and multiple')
        require(items[p['output_item_id']]['base_uom']==res['capacity_uom'],'capacity quantity unit must match produced item unit')
        require(minimum*Decimal(o['resource_units'])<=Decimal(res['window_capacity_quantity']),'one batch cannot exceed window quantity capacity')
        for w in calendars[res['availability_calendar_id']]['available_intervals']:
            require((datetime.fromisoformat(w['end_at'])-datetime.fromisoformat(w['start_at'])).total_seconds()>=Decimal(o['fixed_seconds']),'a complete fixed batch must fit a production window')
    ids=[p['id'] for p in data['existing_orders'] if p['type']=='PO']
    for mo in mos:
        require(mo['type']=='MO','no purchase/transfer mixing in this profile')
        ids.append(mo['id'])
        require(':' not in mo['id'],'MO reference contains an unsupported result-ID delimiter; original code is not rewritten')
        require(mo['source_status'] in {'confirmed','approved'} and mo['freeze_policy']=='LOCKED','only protected confirmed/approved MOs')
        require(Decimal(mo['completed_quantity'])==0,'started/partial/completed manufacturing is not supported')
        p=recipes.get(mo['recipe_id']);require(p is not None,'unknown MO recipe')
        o=routes[p['routing_revision_id']]['operations'][0]
        require(mo['item_id']==p['output_item_id'] and mo['location_id']==p['location_id'] and mo['uom']==items[p['output_item_id']]['base_uom'],'MO item, site or unit mismatch')
        require(Decimal(mo['quantity'])==Decimal(p['manufacturing_minimum_quantity']),'existing MO must be one complete batch')
        require(len(mo['operations'])==1,'one protected operation per MO')
        c=mo['operations'][0];res=resources[o['resource_id']]
        require(c['operation_id']==o['id'] and c['resource_id']==o['resource_id'] and Decimal(c['resource_units'])==Decimal(o['resource_units']),'protected operation/resource commitment mismatch')
        start,end=map(datetime.fromisoformat,[c['start_at'],c['end_at']])
        require((end-start).total_seconds()==Decimal(o['fixed_seconds']),'protected duration differs from fixed processing time')
        require(any(datetime.fromisoformat(w['start_at'])<=start<end<=datetime.fromisoformat(w['end_at']) for w in calendars[res['availability_calendar_id']]['available_intervals']),'protected MO lies outside a complete resource window')
        require(all(isinstance(v,str) and v.strip() for v in mo['provenance'].values()),'explicit MO source identity is required')
    require(len(ids)==len(set(ids)),'duplicate protected MO reference')
    require(len({m['external_ref'] for m in data['existing_orders']})==len(data['existing_orders']),'ambiguous external MO identity')
    for p in data.get('previous_manufacturing_suggestions',[]):
        require(p['reference'] not in ids,'previous suggestion cannot share a protected MO identity')
        ids.append(p['reference'])
        require(p['recipe_id'] in recipes and p['status']=='proposed','previous proposed recipe/status mismatch')
        require(Decimal(p['quantity'])==Decimal(recipes[p['recipe_id']]['manufacturing_minimum_quantity']),'previous suggestion must be one fixed batch')
        require(datetime.fromisoformat(p['start_at'])<datetime.fromisoformat(p['end_at']),'invalid previous suggestion window')
    return data

def add_window_resource(data,native,resource,*,legal_start_only=False):
    """Shared quantity-window encoding; 1s capacity is a legal start marker."""
    from planning_adapter import engine_time,duration
    from datetime import timedelta
    zone=data['site']['timezone'];name='P3-CAPACITY:'+resource['id']
    native['calendar'].append({'name':name,'defaultvalue':0})
    windows=next(c for c in data['calendars'] if c['id']==resource['availability_calendar_id'])['available_intervals']
    for w in windows:
        end=(datetime.fromisoformat(w['start_at'])+timedelta(seconds=1)).isoformat() if legal_start_only else w['end_at']
        native['calendarbucket'].append({'id':len(native['calendarbucket'])+1,'calendar':name,
            'startdate':engine_time(w['start_at'],zone),'enddate':engine_time(end,zone),'value':resource['window_capacity_quantity'],'priority':0,
            **{d:True for d in ['monday','tuesday','wednesday','thursday','friday','saturday','sunday']},'starttime':'00:00:00','endtime':'23:59:59'})
    native['resource'].append({'name':resource['id'],'type':'buckets','maximum_calendar':name,
        'maxearly':duration(resource['maxearly_seconds']),'constrained':True})

def native(data):
    from planning_adapter import engine_time,duration,decimal_text
    zone=data['site']['timezone'];site=data['site']['id']
    at=lambda v:engine_time(v,zone)
    n={'parameter':[{'name':'currentdate','value':at(data['planning_at']).replace('T',' ')},
         {'name':'plan.move_approved_early','value':'0'},{'name':'plan.fixBrokenSupplyPath','value':'false'},
         {'name':'plan.administrativeLeadtime','value':'0'},{'name':'plan.autoFenceOperations','value':'0'},
         {'name':'plan.loglevel','value':'2'}],
       'location':[{'name':site}],'customer':[{'name':'CUSTOMER-DEMO'}],
       'item':[{'name':i['id']} for i in data['items']], 'calendar':[],'calendarbucket':[],
       'resource':[],'operation':[],'operationresource':[],'operationmaterial':[],
       'buffer':[],'supplier':[],'itemsupplier':[],'demand':[],'manufacturingorder':[]}
    def calendar(name,windows,value):
        n['calendar'].append({'name':name,'defaultvalue':0})
        for w in windows:
            n['calendarbucket'].append({'id':len(n['calendarbucket'])+1,'calendar':name,
             'startdate':at(w['start_at']),'enddate':at(w['end_at']),'value':value,'priority':0,
             **{d:True for d in ['monday','tuesday','wednesday','thursday','friday','saturday','sunday']},
             'starttime':'00:00:00','endtime':'23:59:59'})
    cals={c['id']:c for c in data['calendars']}
    for c in cals.values():calendar(c['id'],c['available_intervals'],'1')
    for r in data['resources']:
        add_window_resource(data,n,r)
    routes={r['id']:r for r in data['routing_revisions']};boms={b['id']:b for b in data['bom_revisions']}
    resources={r['id']:r for r in data['resources']}
    for p in data['production_recipes']:
        o=routes[p['routing_revision_id']]['operations'][0];b=boms[p['bom_revision_id']]
        n['operation'].append({'name':p['id'],'type':'fixed_time','item':p['output_item_id'],'location':site,
          'duration':duration(o['fixed_seconds']),'available':resources[o['resource_id']]['availability_calendar_id'],
          'fence':duration(0),'sizeminimum':p['manufacturing_minimum_quantity'],
          'sizemaximum':p['manufacturing_maximum_quantity'],'sizemultiple':p['manufacturing_multiple']})
        n['operationresource'].append({'id':len(n['operationresource'])+1,'operation':p['id'],
          'resource':o['resource_id'],'quantity':o['resource_units']})
        for l in b['lines']:
            amount=-Decimal(l['quantity_per_output'])/Decimal(b['output_quantity'])/(1-Decimal(l['scrap_fraction']))
            n['operationmaterial'].append({'id':len(n['operationmaterial'])+1,'operation':p['id'],
              'item':l['item_id'],'quantity':decimal_text(amount),'type':l['consume_at'].lower()})
        n['operationmaterial'].append({'id':len(n['operationmaterial'])+1,'operation':p['id'],
          'item':p['output_item_id'],'quantity':'1','type':'end'})
    inv=defaultdict(Decimal)
    for i in data['inventory_positions']:inv[(i['item_id'],i['location_id'])]+=Decimal(i['on_hand'])-Decimal(i['quality_blocked'])-Decimal(i['other_nonnettable'])
    n['buffer']=[{'id':idx,'item':k[0],'location':k[1],'batch':'','onhand':decimal_text(q)} for idx,(k,q) in enumerate(inv.items(),1)]
    n['demand']=[{'name':d['id'],'item':d['item_id'],'location':site,'customer':'CUSTOMER-DEMO',
      'quantity':d['quantity'],'due':at(d['due_at']),'priority':d['priority'],'status':'open',
      'minshipment':d['minimum_shipment'],'maxlateness':duration(d['maximum_lateness_seconds'])} for d in data['demand_lines']]
    for m in data['existing_orders']:
        if m['type']!='MO':continue
        o=m['operations'][0]
        n['manufacturingorder'].append({'reference':m['id'],'operation':m['recipe_id'],'quantity':m['quantity'],
          'status':m['source_status'],'startdate':at(o['start_at']),'enddate':at(o['end_at']),'source':m['external_ref']})
    for p in data.get('previous_manufacturing_suggestions',[]):
        n['manufacturingorder'].append({'reference':p['reference'],'operation':p['recipe_id'],'quantity':p['quantity'],
          'status':'proposed','startdate':at(p['start_at']),'enddate':at(p['end_at']),'source':'PREVIOUS_PROPOSED'})
    if joint_enabled(data):
        from confirmed_supply import add_native_supply
        add_native_supply(data,n)
        from priority_supply import enabled as priority_enabled,add_native as add_priority
        if priority_enabled(data):add_priority(data,n)
    return n

def protected_expectations(data):
    from planning_adapter import engine_time
    return [{'reference':m['id'],'operation':m['recipe_id'],'quantity':m['quantity'],
             'status':m['source_status'],'resource':m['operations'][0]['resource_id'],
             'start':engine_time(m['operations'][0]['start_at'],data['site']['timezone']),
             'end':engine_time(m['operations'][0]['end_at'],data['site']['timezone'])} for m in data['existing_orders'] if m['type']=='MO']

def validate_native_evidence(data,raw):
    expected=protected_expectations(data);issues=[];missing=[]
    for phase in ['fixed_batch_pre','fixed_batch_post']:
        snap=raw.get(phase)
        if not isinstance(snap,dict) or not isinstance(snap.get('operationplans'),list):missing.append(phase);continue
        if snap.get('move_approved_early')!=0:issues.append({'code':'APPROVED_POLICY_DRIFT','phase':phase})
        for e in expected:
            matches=[p for p in snap['operationplans'] if p.get('reference')==e['reference']]
            if len(matches)!=1:issues.append({'code':'PROTECTED_MO_IDENTITY','phase':phase,'reference':e['reference']});continue
            for k,v in e.items():
                x=matches[0].get(k)
                same=Decimal(str(x))==Decimal(v) if k=='quantity' and x is not None else x==v
                if not same:issues.append({'code':'PROTECTED_MO_DRIFT','phase':phase,'reference':e['reference'],'field':k,'expected':v,'actual':x})
    return {'status':'FAIL' if issues else 'NOT_CHECKED' if missing else 'PASS',
      'scope':'fixed_batch_protected_mo_native_pre_post','issues':issues,'missing_evidence':missing,
      'limits':['Only this profile and listed MO fields; no physical execution or authorization inference.']}

def check_schedule(data,operations,raw):
    """Fixed-duration windows, batch quantity and the protected MO chain."""
    issues=[]
    def issue(code,message):issues.append({'code':code,'severity':'ERROR','entity_id':None,
      'field_ref':None,'message':message,'evidence_ids':[]})
    frozen=validate_native_evidence(data,raw)
    if frozen['status']!='PASS':issue('PROTECTED_MO_NOT_VERIFIED',str(frozen))
    loaded=validate_loaded_model(data,raw)
    if loaded['status']!='PASS':issue('FIXED_BATCH_NATIVE_MODEL_NOT_VERIFIED',str(loaded))
    resources={r['id']:r for r in data['resources']};calendars={c['id']:c for c in data['calendars']}
    specs={o['id']:o for r in data['routing_revisions'] for o in r['operations']}
    recipes={r['id']:r for r in data['production_recipes']}
    roots={str(r['reference']):r for r in raw.get('manufacturingorder',[]) if r.get('owner') is None}
    if raw.get('workorder'):issue('UNSUPPORTED_FIXED_BATCH_CHILDREN','This profile has no nested manufacturing operations')
    seen=Counter();window_usage=defaultdict(Decimal)
    for op in operations:
        ref=op['manufacture_id'].rsplit(':',1)[-1];row=roots.get(ref)
        if row is None or row.get('operation') not in recipes:
            issue('UNKNOWN_MANUFACTURING_IDENTITY',ref);continue
        seen[ref]+=1;p=recipes[row['operation']];spec=specs.get(op['operation_id'])
        if spec is None or op['resource_id']!=spec['resource_id']:
            issue('FIXED_BATCH_RESOURCE_IDENTITY',ref);continue
        if Decimal(op['quantity'])!=Decimal(p['manufacturing_minimum_quantity']):issue('FIXED_BATCH_QUANTITY',ref)
        start,end=map(datetime.fromisoformat,[op['start_at'],op['end_at']])
        if (end-start).total_seconds()!=Decimal(spec['fixed_seconds']):issue('FIXED_BATCH_DURATION',ref)
        if start<datetime.fromisoformat(data['planning_at']) or end>datetime.fromisoformat(data['horizon']['end_at']):issue('HORIZON',ref)
        r=resources[op['resource_id']]
        matches=[i for i,w in enumerate(calendars[r['availability_calendar_id']]['available_intervals'])
                 if datetime.fromisoformat(w['start_at'])<=start<end<=datetime.fromisoformat(w['end_at'])]
        if len(matches)!=1:issue('FIXED_BATCH_WINDOW',ref);continue
        quantity=Decimal(op['quantity'])*Decimal(spec['resource_units'])
        window_usage[(r['id'],matches[0])]+=quantity
        if abs(Decimal(op['resource_units']))!=quantity:issue('WINDOW_LOAD_QUANTITY',ref)
    if set(seen)!=set(roots) or any(c!=1 for c in seen.values()):issue('MANUFACTURING_OPERATION_COVERAGE','Missing or duplicate root operation')
    for (resource,_),amount in window_usage.items():
        if amount>Decimal(resources[resource]['window_capacity_quantity']):issue('WINDOW_CAPACITY_EXCEEDED',resource)
    for expected in protected_expectations(data):
        row=roots.get(expected['reference'])
        if not row:issue('PROTECTED_MO_RESULT_MISSING',expected['reference']);continue
        for key,field in [('operation','operation'),('status','status'),('quantity','quantity'),('start','startdate'),('end','enddate')]:
            x=row.get(field);want=expected[key]
            if key in {'start','end'}:
                x=datetime.fromisoformat(x).replace(tzinfo=None).isoformat() if x else x
            same=Decimal(str(x))==Decimal(want) if key=='quantity' and x is not None else x==want
            if not same:issue('PROTECTED_MO_RESULT_DRIFT',expected['reference']+':'+key)
    for f in raw.get('operationplanmaterial',[]):
        if Decimal(str(f['onhand']))<Decimal('-0.000001'):issue('NEGATIVE_STOCK',str(f.get('id')))
    return issues

def validate_loaded_model(data,raw):
    """Finite field list, from native in-memory snapshots, never DB inference."""
    from planning_adapter import engine_time
    issues=[];missing=[]
    def require(ok,phase,field):
        if not ok:issues.append({'phase':phase,'field':field,'code':'NATIVE_FIXED_BATCH_FIELD_MISMATCH'})
    routes={r['id']:r for r in data['routing_revisions']}
    for phase in ['fixed_batch_pre','fixed_batch_post']:
        snap=raw.get(phase,{})
        if not all(isinstance(snap.get(k),list) for k in ['operations','resources','calendars']):missing.append(phase);continue
        ops={o['name']:o for o in snap['operations']};resources={r['name']:r for r in snap['resources']};cals={c['name']:c for c in snap['calendars']}
        for recipe in data['production_recipes']:
            n=ops.get(recipe['id']);spec=routes[recipe['routing_revision_id']]['operations'][0]
            if n is None:require(False,phase,recipe['id']);continue
            require(n.get('type')=='operation_fixed_time',phase,recipe['id']+'.type')
            for field,expected in [('duration',spec['fixed_seconds']),('size_minimum',recipe['manufacturing_minimum_quantity']),
                ('size_maximum',recipe['manufacturing_maximum_quantity']),('size_multiple',recipe['manufacturing_multiple'])]:
                try:equal=Decimal(n.get(field))==Decimal(expected)
                except (TypeError,ValueError,ArithmeticError):equal=False
                require(equal,phase,recipe['id']+'.'+field)
            loads=n.get('loads',[])
            require(len(loads)==1 and loads[0].get('resource')==spec['resource_id'] and Decimal(loads[0].get('quantity','-1'))==Decimal(spec['resource_units']),phase,recipe['id']+'.resource_quantity')
            # This binary serializes the default load without a type tag.
            # Verify its actual quantity/time events instead of naming a type
            # the native Python/XML interfaces do not expose.
            for mo in [m for m in snap['operationplans'] if m['operation']==recipe['id']]:
                resource=resources.get(spec['resource_id'],{})
                events=[l for l in resource.get('loadplans',[]) if l['reference']==mo['reference']]
                require(len(events)==1 and events[0].get('at')==mo['start']
                        and abs(Decimal(events[0]['quantity']))==Decimal(mo['quantity'])*Decimal(spec['resource_units']),
                        phase,mo['reference']+'.native_window_load_time_quantity')
        for r in data['resources']:
            n=resources.get(r['id'],{});capacity='P3-CAPACITY:'+r['id']
            require(n.get('type')=='resource_buckets' and n.get('maximum_calendar')==capacity,phase,r['id']+'.capacity_model')
            cal=cals.get(capacity)
            if cal is None:require(False,phase,capacity);continue
            source=next(c for c in data['calendars'] if c['id']==r['availability_calendar_id'])
            from priority_supply import enabled as priority_enabled
            from datetime import timedelta
            end=lambda w:(datetime.fromisoformat(w['start_at'])+timedelta(seconds=1)).isoformat() if priority_enabled(data) else w['end_at']
            expected=Counter((engine_time(w['start_at'],data['site']['timezone']),engine_time(end(w),data['site']['timezone']),Decimal(r['window_capacity_quantity'])) for w in source['available_intervals'])
            actual=Counter((w['start'],w['end'],Decimal(w['value'])) for w in cal['buckets'])
            require(actual==expected and Decimal(cal['default'])==0,phase,capacity+'.windows')
    if joint_enabled(data):
        from confirmed_supply import validate_native_receipts
        purchase=validate_native_receipts(data,raw)
        issues.extend(purchase['issues']);missing.extend(purchase['missing_evidence'])
    return {'status':'FAIL' if issues else 'NOT_CHECKED' if missing else 'PASS','scope':'fixed_batch_native_model_fields',
      'issues':issues,'missing_evidence':missing,'not_checked':['unlisted_native_model_fields','native_load_class_tag_not_exposed']}

def material_check(data,raw):
    """Independent linear BOM events, plus the explicit bounded joint PO."""
    from calculation_validation import same_events,evidence_rows,purchase_events
    from planning_adapter import decimal_text
    result={'status':'NOT_CHECKED','scope':'fixed_batch_linear_material_events','issues':[],
      'missing_evidence':[],'not_checked':['full_general_material_balance','physical_execution_feasibility'],
      'unsupported':['partial_MOs','nested_operations','PO_combination','MTO','physical_lots','nonlinear_flows']}
    if joint_enabled(data):
        result['unsupported'].remove('PO_combination')
        result['unsupported'].extend(['multiple_confirmed_purchases','partial_receipts'])
        if data['rules']['manufacturing_profile']!='fixed-batch-priority-supply-v1':result['unsupported'].append('new_proposed_purchases')
    required=['manufacturingorder','workorder','purchaseorder','deliveryorder','demand','operationplanmaterial']
    missing=[k for k in required if evidence_rows(raw.get(k)) is None]
    if missing:result['missing_evidence']=missing;return result
    from planning_adapter import validate_input,InputError
    try:validate_input(data)
    except InputError as exc:result.update(status='UNSUPPORTED',reason=str(exc));return result
    def issue(code,ref=None):result['issues'].append({'code':code,'reference':ref})
    def q(x):
        v=Decimal(str(x))
        if not v.is_finite():raise ValueError('nonfinite quantity')
        return v
    # All native datetimes without an offset use the evidenced site clock.
    from zoneinfo import ZoneInfo
    def when(x):
        t=datetime.fromisoformat(x.replace('Z','+00:00'))
        return t.replace(tzinfo=ZoneInfo(data['site']['timezone'])) if t.tzinfo is None else t.astimezone(ZoneInfo(data['site']['timezone']))
    def event(item,ref,qty,t):return ((item,data['site']['id']),str(ref),str(qty),when(t).isoformat())
    expected=Counter();actual=Counter();initial=defaultdict(Decimal);opening=defaultdict(Decimal);groups=defaultdict(list)
    recipes={p['id']:p for p in data['production_recipes']};boms={b['id']:b for b in data['bom_revisions']}
    try:
        if raw['workorder'] or (raw['purchaseorder'] and not joint_enabled(data)):issue('UNSUPPORTED_FIXED_BATCH_SUPPLY_OR_CHILDREN')
        if joint_enabled(data):
            if data['rules']['manufacturing_profile']!='fixed-batch-priority-supply-v1' and any(p.get('status')!='confirmed' for p in raw['purchaseorder']):
                issue('JOINT_NEW_PURCHASE_NOT_VALIDATED')
            expected.update(purchase_events(data,raw['purchaseorder'],lambda ok,code,ref=None:None if ok else issue(code,ref),event))
        roots=[p for p in raw['manufacturingorder'] if p.get('owner') is None]
        if len({str(p['reference']) for p in roots})!=len(roots):issue('DUPLICATE_MANUFACTURING_REFERENCE')
        for p in roots:
            recipe=recipes.get(p['operation'])
            if not recipe:issue('UNKNOWN_MANUFACTURING_RECIPE',p['operation']);continue
            qty=q(p['quantity']);bom=boms[recipe['bom_revision_id']]
            for line in bom['lines']:
                amount=-qty*q(line['quantity_per_output'])/q(bom['output_quantity'])/(1-q(line['scrap_fraction']))
                expected[event(line['item_id'],p['reference'],amount,p['startdate'] if line['consume_at']=='START' else p['enddate'])]+=1
            expected[event(recipe['output_item_id'],p['reference'],qty,p['enddate'])]+=1
        demands={d['id']:d for d in data['demand_lines']};delivered=defaultdict(Decimal)
        if Counter(d['name'] for d in raw['demand'])!=Counter(demands.keys()):issue('DEMAND_IDENTITY_COVERAGE')
        for d in raw['deliveryorder']:
            request=demands.get(d.get('demand'))
            if request is None or d['item']!=request['item_id'] or d['location']!=request['location_id']:
                issue('DELIVERY_IDENTITY',d['reference']);continue
            delivered[d['demand']]+=q(d['quantity'])
            expected[event(d['item'],d['reference'],-q(d['quantity']),d['startdate'])]+=1
        for d in raw['demand']:
            if abs(delivered[d['name']]-q(d.get('plannedquantity') or 0))>Decimal('0.000001'):issue('DELIVERY_QUANTITY',d['name'])
        for i in data['inventory_positions']:initial[(i['item_id'],i['location_id'])]+=q(i['on_hand'])-q(i['quality_blocked'])-q(i['other_nonnettable'])
        for i,f in enumerate(raw['operationplanmaterial']):
            key=(f['item'],f['location']);t=when(f['flowdate']);qty=q(f['quantity'])
            if key[0] not in {x['id'] for x in data['items']} or key[1]!=data['site']['id']:issue('UNKNOWN_MATERIAL_BUFFER',str(key))
            groups[key].append((t,i,f))
            if t<when(data['planning_at']):opening[key]+=qty
            if t>=when(data['planning_at']) and qty:actual[event(f['item'],f['operationplan'],qty,f['flowdate'])]+=1
        if joint_enabled(data) and any(abs(opening[k]-initial[k])>Decimal('0.000001') for k in set(opening)|set(initial)):
            issue('MATERIAL_OPENING_STOCK')
        ending=dict(initial)
        for key,rows in groups.items():
            balance=initial[key]
            for t,_,f in sorted(rows):
                if t<when(data['planning_at']):continue
                balance+=q(f['quantity'])
                if balance<Decimal('-0.000001'):issue('MATERIAL_NEGATIVE_RECONSTRUCTED',f.get('id'))
                if abs(balance-q(f['onhand']))>Decimal('0.000001'):issue('MATERIAL_REPORTED_BALANCE_MISMATCH',f.get('id'))
            ending[key]=balance
        if not same_events(expected,actual):issue('MATERIAL_BOM_EVENT_QUANTITY_OR_TIME')
    except (KeyError,ValueError,TypeError,ArithmeticError) as exc:
        result.update(status='NOT_CHECKED',reason=str(exc));return result
    scope_only={'JOINT_NEW_PURCHASE_NOT_VALIDATED'}
    status='FAIL' if any(i['code'] not in scope_only for i in result['issues']) else 'UNSUPPORTED' if result['issues'] else 'PASS'
    result.update(status=status,expected_event_count=sum(expected.values()),actual_event_count=sum(actual.values()),
      ending_balances=[{'item_id':k[0],'location_id':k[1],'quantity':decimal_text(v)} for k,v in sorted(ending.items())])
    return result
