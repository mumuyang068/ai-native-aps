"""One machine and one qualified pool member, over the original P3 pipeline."""
from datetime import datetime
from decimal import Decimal

PROFILE='machine-qualified-operator-v1'
def is_priority(value):
    # Fixed 9.19.0 model.h SearchMode: PRIORITY=0; the Python binding emits int.
    return value=='PRIORITY' or (type(value) is int and value==0)
def enabled(data):
    from multi_receipt_supply import enabled as multi_receipt_enabled
    from joint_qualified import enabled as joint_enabled
    from finished_delivery import enabled as finished_enabled
    return data.get('rules',{}).get('manufacturing_profile')==PROFILE or joint_enabled(data) or finished_enabled(data) or multi_receipt_enabled(data)
def validate(data):
    from multi_receipt_supply import enabled as multi_receipt_enabled,validate as validate_multi_receipt
    if multi_receipt_enabled(data):return validate_multi_receipt(data)
    from finished_delivery import enabled as finished_enabled,validate as validate_finished
    if finished_enabled(data):return validate_finished(data)
    from joint_qualified import enabled as joint_enabled,validate as validate_joint
    if joint_enabled(data):return validate_joint(data)
    from planning_adapter import InputError
    def require(ok,text):
        if not ok:raise InputError('QUALIFIED_OPERATOR_UNSUPPORTED: '+text)
    present=any(k in data for k in ['skills','resource_skills']) or any('role' in r or 'pool_id' in r for r in data.get('resources',[])) or any('operator_requirement' in o for r in data.get('routing_revisions',[]) for o in r['operations'])
    if not enabled(data):
        require(not present,'qualification fields require the explicit profile');return
    require(not any(data.get(k) for k in ['suppliers','supply_sources','existing_orders','reservations','previous_manufacturing_suggestions']),'no procurement, existing MO or previous suggestions combination')
    require(data['rules'].get('move_approved_early')==0 and data['rules'].get('fix_broken_supply_path') is False,'explicit protected native parameters required')
    resources={r['id']:r for r in data['resources']};calendars={c['id']:c for c in data['calendars']}
    pools=[r for r in resources.values() if r.get('role')=='OPERATOR_POOL'];machines=[r for r in resources.values() if r.get('role')=='MACHINE'];people=[r for r in resources.values() if r.get('role')=='OPERATOR']
    require(len(pools)==1 and 1<=len(machines)<=2 and 1<=len(people)<=8 and len(resources)==len(pools)+len(machines)+len(people),'one pool, one/two machines, one/eight named people')
    pool=pools[0];work=calendars.get(pool['availability_calendar_id']);require(work is not None and bool(work['available_intervals']),'pool working calendar must be explicit')
    for r in resources.values():
        require(r.get('capacity_model','CONTINUOUS')=='CONTINUOUS' and r.get('parallel_units')=='1','unit continuous resources only')
        require(r['location_id']==data['site']['id'] and r['availability_calendar_id'] in calendars,'resource identity/location/calendar')
        require((r.get('pool_id')==pool['id']) if r['role']=='OPERATOR' else 'pool_id' not in r,'only named people are pool members')
        for i in calendars[r['availability_calendar_id']]['available_intervals']:
            require(any(datetime.fromisoformat(w['start_at'])<=datetime.fromisoformat(i['start_at'])<datetime.fromisoformat(i['end_at'])<=datetime.fromisoformat(w['end_at']) for w in work['available_intervals']),'individual calendar must lie within pool working envelope')
    skills={s['id'] for s in data.get('skills',[])};require(bool(skills) and len(skills)==len(data.get('skills',[])),'explicit unique skills')
    seen=set()
    for s in data.get('resource_skills',[]):
        key=(s['resource_id'],s['skill_id']);require(key not in seen,'duplicate qualification');seen.add(key)
        require(s['resource_id'] in {p['id'] for p in people} and s['skill_id'] in skills,'unknown person or skill')
        require(datetime.fromisoformat(s['effective_from'])<=datetime.fromisoformat(data['planning_at']) and datetime.fromisoformat(s['effective_to'])>=datetime.fromisoformat(data['horizon']['end_at']),'qualification must cover entire fixed horizon; dynamic/expired skills unsupported')
    routes=data['routing_revisions'];require(1<=len(routes)<=2 and len(routes)==len(data['production_recipes'])==len(data['bom_revisions']),'one/two independent single-step recipes')
    for route in routes:
        require(len(route['operations'])==1,'no sequential or complex routing combination')
        o=route['operations'][0];rq=o.get('operator_requirement',{})
        require(o['resource_id'] in {m['id'] for m in machines} and o['resource_units']=='1','specified machine capacity one')
        require(rq.get('pool_id')==pool['id'] and rq.get('quantity')=='1' and rq.get('skill_id') in skills,'one explicit skilled pool member per operation')
        require(any(s['skill_id']==rq['skill_id'] for s in data.get('resource_skills',[])),'no declared qualified candidate')
        require(o['duration_model']=='FIXED_PLUS_PER_UNIT' and Decimal(o['fixed_seconds'])==0 and Decimal(o['seconds_per_unit'])>0,'positive continuous per-unit processing only')
    raw={i['id'] for i in data['items'] if i['kind']=='RAW'};finished={i['id'] for i in data['items'] if i['kind']=='FINISHED'}
    require(len(raw)==1 and len(finished)==len(routes) and len(data['items'])==len(raw)+len(finished),'one raw and one/two independent finished products; no WIP')
    for b in data['bom_revisions']:
        require(b['output_item_id'] in finished and len(b['lines'])==1 and b['lines'][0]['item_id'] in raw and Decimal(b['lines'][0]['scrap_fraction'])==0 and b['lines'][0]['consume_at']=='START','single raw zero-loss BOM')
    require(all(p['transfer_policy']=='WHOLE_MANUFACTURING_BATCH' and Decimal(p['manufacturing_minimum_quantity'])==Decimal(p['manufacturing_multiple'])==1 and 'manufacturing_maximum_quantity' not in p for p in data['production_recipes']),'continuous unit batches only')
    require(len(data['inventory_positions'])==len(data['items']) and len({i['item_id'] for i in data['inventory_positions']})==len(data['items']),'one explicit opening stock per item')

def map_native(data,native):
    from planning_adapter import engine_time
    zone=data['site']['timezone'];pool=next(r for r in data['resources'] if r['role']=='OPERATOR_POOL');work=pool['availability_calendar_id']
    rs={r['id']:r for r in data['resources']}
    for r in native['resource']:
        fact=rs[r['name']]
        if fact.get('capacity_model')=='WINDOW_QUANTITY':continue
        r.update(available=work,maximum_calendar=fact['availability_calendar_id'])
        if fact['role']=='OPERATOR':r['owner']=fact['pool_id']
    native['skill']=[{'name':s['id']} for s in data['skills']]
    native['resourceskill']=[{'id':i+1,'resource':s['resource_id'],'skill':s['skill_id'],'priority':s['priority'],
        'effective_start':engine_time(s['effective_from'],zone),'effective_end':engine_time(s['effective_to'],zone)} for i,s in enumerate(data['resource_skills'])]
    for recipe in data['production_recipes']:
        route=next(r for r in data['routing_revisions'] if r['id']==recipe['routing_revision_id'])
        for o in route['operations']:
            if 'operator_requirement' not in o:continue
            rq=o['operator_requirement']
            native['operationresource'].append({'id':len(native['operationresource'])+1,'operation':recipe['id']+':'+o['id'],
                'resource':rq['pool_id'],'quantity':rq['quantity'],'skill':rq['skill_id'],'search':'PRIORITY'})
    terms={'plan.move_approved_early':'0','plan.fixBrokenSupplyPath':'false','plan.individualPoolResources':'false'}
    native['parameter']=[p for p in native['parameter'] if p['name'] not in terms]+[{'name':k,'value':v} for k,v in terms.items()]
    from routing_capacity import enabled as routing_enabled, map_native as map_routing
    if routing_enabled(data):map_routing(data,native)
    order=['parameter','location','customer','item','calendar','calendarbucket','skill','resource','resourceskill','operation','operationresource','operationmaterial','buffer','supplier','itemsupplier','demand']
    return {k:native[k] for k in order+list(native) if k in native}

def audit_resources(data,raw,*,run_id=None):
    """Reconcile raw leaf loads against qualifications, calendars and pre/post memory.

    This does not select staff or change a plan. Known violations take precedence
    over absent evidence. End events precede starts at a shared timestamp.
    """
    from routing_capacity import enabled as routing_enabled, audit_resources as audit_routing
    if routing_enabled(data):return audit_routing(data,raw,run_id=run_id)
    from collections import Counter,defaultdict
    from joint_qualified import enabled as joint_enabled
    from finished_delivery import enabled as finished_enabled
    from multi_receipt_supply import enabled as multi_receipt_enabled,result_operations
    multi_receipt=multi_receipt_enabled(data)
    joint=joint_enabled(data) or finished_enabled(data) or multi_receipt
    from zoneinfo import ZoneInfo
    import json,hashlib
    from calculation_validation import evidence_rows
    issues=[];missing=[];assignments=[];zone=ZoneInfo(data['site']['timezone'])
    def check(ok,code,ref=None):
        if not ok:issues.append({'code':code,'reference':ref})
    def q(v):
        value=Decimal(str(v))
        if not value.is_finite():raise ValueError('nonfinite resource value')
        return value
    def at(v):
        d=datetime.fromisoformat(str(v).replace('Z','+00:00'));return d.replace(tzinfo=zone) if d.tzinfo is None else d.astimezone(zone)
    def event(ref,res,start,end,qty):return (str(ref),res,at(start).isoformat(),at(end).isoformat(),str(q(qty).normalize()))
    resources={r['id']:r for r in data['resources']};calendars={c['id']:c for c in data['calendars']}
    pool=next(r for r in resources.values() if r['role']=='OPERATOR_POOL')
    specs={p['id']+':'+o['id']:o for p in data['production_recipes'] for route in data['routing_revisions'] if route['id']==p['routing_revision_id'] for o in route['operations']}
    all_specs=specs
    if joint:specs={k:v for k,v in specs.items() if 'operator_requirement' in v}
    expected_hash=hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    workorders=evidence_rows(raw.get('workorder'));loads=evidence_rows(raw.get('operationplanresource'))
    if multi_receipt and workorders is not None:workorders=result_operations(data,raw)
    if workorders is None:missing.append('workorder')
    if loads is None:missing.append('operationplanresource')
    pre=raw.get('fixed_batch_pre');post=raw.get('fixed_batch_post')
    if run_id is None:missing.append('expected_run_id')
    purchase_budget=None;purchase_refs=set();pending_purchase_refs=set();purchase_validation=None
    try:
        from accessory_supply import enabled as accessory_enabled,source as accessory_source,capacity_id,audit as audit_purchase
        if accessory_enabled(data) and data.get('accessory_supply_option',{}).get('enabled') is True:
            term=accessory_source(data);purchase_budget=capacity_id(term)
            # The distinct purchase auditor owns source/QC/total-capacity
            # semantics. Its known failures and missing proof cannot be hidden
            # by the manufacturing scope separation below.
            purchase_validation=audit_purchase(data,raw,run_id=run_id)
            issues.extend(purchase_validation.get('issues',[]))
            if purchase_validation['status']!='PASS':
                missing.extend('purchase_budget:'+p for p in purchase_validation.get('missing_evidence',[]))
                if not purchase_validation.get('issues') and not purchase_validation.get('missing_evidence'):
                    missing.append('purchase_budget:certification')
            purchase_rows=evidence_rows(raw.get('purchaseorder'))
            if purchase_rows is None and isinstance(post,dict) and isinstance(post.get('purchaseorders'),list):
                # This establishes only native PO type/identity for classifying
                # a load. It does not replace missing REST proof: that remains
                # NOT_CHECKED in the independent purchase audit above.
                purchase_rows=[dict(p,startdate=p.get('start'),enddate=p.get('end')) for p in post['purchaseorders']]
            matched={str(p['reference']):p for p in purchase_rows or [] if p.get('status')=='proposed'
                     and (p.get('item'),p.get('supplier'),p.get('location'))==(term['item_id'],term['supplier_id'],term['location_id'])}
            purchase_refs=set(matched)
            if loads is not None:
                budget_rows=[l for l in loads if l.get('resource')==purchase_budget]
                expected=Counter((ref,str(q(p['quantity']).normalize()),at(p['startdate']).isoformat(),at(p['enddate']).isoformat()) for ref,p in matched.items())
                actual=Counter()
                work_ids={str(w['reference']) for w in workorders or []}
                budget_total=Decimal(0);option=data['accessory_supply_option']
                for l in budget_rows:
                    ref=str(l['operationplan'])
                    value=q(l['quantity']);start,end=at(l['startdate']),at(l['enddate'])
                    budget_total+=value
                    check(value>0 and start<end and l.get('status')=='proposed'
                          and at(option['earliest_order_at'])<=start<=at(option['latest_order_at']),
                          'QUALIFIED_PURCHASE_BUDGET_EXPORT',ref)
                    if purchase_rows is None and ref not in work_ids:
                        pending_purchase_refs.add(ref)
                        missing.append('purchase_budget:PO_identity:'+ref)
                    else:
                        actual[(ref,str(value.normalize()),start.isoformat(),end.isoformat())]+=1
                check(budget_total<=q(option['total_capacity_quantity']),'QUALIFIED_PURCHASE_BUDGET_EXPORT')
                if purchase_rows is not None:
                    check(actual==expected,'QUALIFIED_PURCHASE_BUDGET_EXPORT')
                # Only authenticated source-PO loads leave manufacturing
                # occupancy. Unresolved records are not certified or called
                # manufacturing violations; their missing identity blocks PASS.
                loads=[l for l in loads if not (l.get('resource')==purchase_budget and
                    str(l.get('operationplan')) in purchase_refs|pending_purchase_refs)]
        for phase,snap in [('pre',pre),('post',post)]:
            if not isinstance(snap,dict):missing.append('native_'+phase);continue
            if multi_receipt and not all(k in snap for k in ['input_hash','run_id','move_approved_early']):
                missing.append('native_'+phase+':identity_and_protection');continue
            check(snap.get('input_hash')==expected_hash,'QUALIFIED_INPUT_IDENTITY',phase)
            check(bool(snap.get('run_id')) and (not isinstance(pre,dict) or snap.get('run_id')==pre.get('run_id')),'QUALIFIED_RUN_IDENTITY',phase)
            if run_id is not None:check(snap.get('run_id')==run_id,'QUALIFIED_RUN_IDENTITY',phase)
            check(snap.get('move_approved_early')==0,'QUALIFIED_GLOBAL_PROTECTION',phase)
            model=snap.get('qualified_model')
            if not isinstance(model,dict):missing.append('native_'+phase+':qualified_model');continue
            if multi_receipt and 'fix_broken_supply_path' not in model:missing.append('native_'+phase+':fix_broken_supply_path')
            else:check(model.get('fix_broken_supply_path')=='false','QUALIFIED_NO_UNKNOWN_SUPPLY',phase)
            if not all(k in model for k in ['resources','resource_skills','operation_loads']):missing.append('native_'+phase+':model_fields');continue
            nr={r['name']:r for r in model['resources']}
            check(set(nr)==set(resources)|({purchase_budget} if purchase_budget else set()),'QUALIFIED_NATIVE_RESOURCE_IDENTITIES',phase)
            for ref,r in resources.items():
                actual=nr.get(ref)
                if not actual:continue
                if joint and r.get('capacity_model')=='WINDOW_QUANTITY':continue # independently checked by mixed_batch_wip.audit_native
                if multi_receipt:
                    for field in ['owner','maximum','available','maximum_calendar']:
                        if field not in actual:missing.append('native_'+phase+':resource:'+ref+':'+field)
                    if 'owner' in actual:check(actual['owner']==(r.get('pool_id') if r['role']=='OPERATOR' else None),'QUALIFIED_NATIVE_POOL_MEMBER',ref)
                    for field,wanted in [('maximum',Decimal(1)),('available',pool['availability_calendar_id']),('maximum_calendar',r['availability_calendar_id'])]:
                        if field in actual:check(q(actual[field])==wanted if field=='maximum' else actual[field]==wanted,'QUALIFIED_NATIVE_RESOURCE_CALENDAR',ref)
                else:
                    check(actual.get('owner')==(r.get('pool_id') if r['role']=='OPERATOR' else None),'QUALIFIED_NATIVE_POOL_MEMBER',ref)
                    check(q(actual['maximum'])==1 and actual.get('available')==pool['availability_calendar_id'] and actual.get('maximum_calendar')==r['availability_calendar_id'],'QUALIFIED_NATIVE_RESOURCE_CALENDAR',ref)
            expected=Counter((s['resource_id'],s['skill_id'],s['priority'],at(s['effective_from']).isoformat(),at(s['effective_to']).isoformat()) for s in data['resource_skills'])
            actual=Counter((s['resource'],s['skill'],int(s['priority']),at(s['effective_start']).isoformat(),at(s['effective_end']).isoformat()) for s in model['resource_skills'])
            check(expected==actual,'QUALIFIED_NATIVE_SKILL_RELATION',phase)
            expected_loads=Counter()
            for name,o in specs.items():
                expected_loads[(name,o['resource_id'],None,'1')]+=1
                rq=o['operator_requirement'];person=next(r['id'] for r in resources.values() if r.get('role')=='OPERATOR') if multi_receipt else rq['pool_id']
                expected_loads[(name,person,rq['skill_id'],'1')]+=1
            actual_loads=Counter((r['operation'],r['resource'],r.get('skill'),str(q(r['quantity']).normalize())) for r in model['operation_loads'] if not joint or r['operation'] in specs)
            check(expected_loads==actual_loads,'QUALIFIED_NATIVE_TWO_REQUIRED_LOADS',phase)
            check(all(is_priority(r.get('search')) for r in model['operation_loads'] if r.get('skill')),'QUALIFIED_NATIVE_SKILL_SEARCH',phase)
            if 'calendars' not in snap:missing.append('native_'+phase+':calendars')
            else:
                native_cal={c['name']:c for c in snap['calendars']}
                for ref,c in calendars.items():
                    actual=native_cal.get(ref)
                    if not actual:missing.append('native_'+phase+':calendar:'+ref);continue
                    expected=Counter((at(i['start_at']).isoformat(),at(i['end_at']).isoformat(),'1') for i in c['available_intervals'])
                    got=Counter((at(b['start']).isoformat(),at(b['end']).isoformat(),str(q(b['value']).normalize())) for b in actual['buckets'])
                    check(q(actual['default'])==0 and expected==got,'QUALIFIED_NATIVE_CALENDAR_BUCKETS',ref)
        if workorders is not None and loads is not None:
            events=defaultdict(list);native_expected=Counter()
            ids={str(w['reference']) for w in workorders}
            check(all(str(l['operationplan']) in ids for l in loads),'QUALIFIED_ORPHAN_LOAD')
            if joint:
                check(all(w['operation'] in all_specs for w in workorders),'JOINT_UNKNOWN_OPERATION')
                workorders=[w for w in workorders if w['operation'] in specs]
            for w in workorders:
                ref=str(w['reference']);o=specs.get(w['operation']);check(o is not None,'QUALIFIED_UNKNOWN_OPERATION',ref)
                if not o:continue
                start,end=at(w['startdate']),at(w['enddate']);quantity=q(w['quantity'])
                check(at(data['planning_at'])<=start<end<=at(data['horizon']['end_at']),'QUALIFIED_HORIZON',ref)
                duration=q(o['fixed_seconds']) if o.get('duration_model')=='FIXED_BATCH' else quantity*q(o['seconds_per_unit'])
                check(abs(q((end-start).total_seconds())-duration)<Decimal('0.000001'),'QUALIFIED_PROCESS_DURATION',ref)
                linked=[l for l in loads if str(l['operationplan'])==ref];machines=[l for l in linked if l['resource']==o['resource_id']]
                people=[l for l in linked if resources.get(l['resource'],{}).get('role')=='OPERATOR']
                check(len(linked)==2 and len(machines)==len(people)==1,'QUALIFIED_MISSING_MACHINE_OR_PERSON',ref)
                person=people[0]['resource'] if len(people)==1 else None
                if person:
                    rq=o['operator_requirement'];fact=resources[person]
                    check(fact.get('pool_id')==rq['pool_id'],'QUALIFIED_PERSON_POOL',ref)
                    check(any(s['resource_id']==person and s['skill_id']==rq['skill_id'] and at(s['effective_from'])<=start and end<=at(s['effective_to']) for s in data['resource_skills']),'QUALIFIED_PERSON_SKILL',ref)
                for l in linked:
                    r=resources.get(l['resource']);check(r is not None and r.get('role') in {'MACHINE','OPERATOR'},'QUALIFIED_UNKNOWN_OR_POOL_ASSIGNMENT',ref)
                    ls,le=at(l['startdate']),at(l['enddate']);units=q(l['quantity'])
                    bucket=r and r.get('capacity_model')=='WINDOW_QUANTITY'
                    expected_units=quantity*q(o['resource_units']) if bucket else Decimal(1)
                    check((ls,le)==(start,end) and units==expected_units,'QUALIFIED_LOAD_SYNCHRONIZATION',ref)
                    if r:
                        check(any(at(i['start_at'])<=ls<le<=at(i['end_at']) for i in calendars[r['availability_calendar_id']]['available_intervals']),'QUALIFIED_RESOURCE_OFF_CALENDAR',l['resource'])
                        if not bucket:events[l['resource']].extend([(ls,1,units),(le,0,-units)])
                    if bucket:continue # quantity-window event is authenticated by mixed_batch_wip.audit_native
                    native_expected[(event(ref,l['resource'],ls.isoformat(),le.isoformat(),units),ls.isoformat(),str(units.normalize()))]+=1
                    native_expected[(event(ref,l['resource'],ls.isoformat(),le.isoformat(),units),le.isoformat(),str((-units).normalize()))]+=1
                assignments.append({'native_reference':ref,'operation_id':o['id'],'machine_id':o['resource_id'],'operator_id':person,
                    'required_skill_id':o['operator_requirement']['skill_id'],'operator_pool_id':o['operator_requirement']['pool_id'],
                    'quantity':str(quantity.normalize()),'start_at':start.isoformat(),'end_at':end.isoformat()})
            for ref,ev in events.items():
                total=Decimal(0)
                for _,_,delta in sorted(ev):
                    total+=delta;check(0<=total<=q(resources[ref]['parallel_units']),'QUALIFIED_RESOURCE_CAPACITY',ref)
            if not isinstance(post,dict) or 'resources' not in post:missing.append('native_post:loadplans')
            else:
                actual=Counter()
                for r in post['resources']:
                    if joint and resources.get(r['name'],{}).get('capacity_model')=='WINDOW_QUANTITY':continue
                    if 'loadplans' not in r:missing.append('native_post:loadplans:'+r['name']);continue
                    for l in r['loadplans']:
                        if r['name']==purchase_budget and str(l.get('reference')) in purchase_refs|pending_purchase_refs:
                            continue # independently audited purchase budget, never machine/person occupancy
                        units=q(l['quantity'])
                        if not units:continue
                        actual[(event(l['reference'],r['name'],l['start'],l['end'],abs(units)),at(l['at']).isoformat(),str(units.normalize()))]+=1
                check(native_expected==actual,'QUALIFIED_NATIVE_LOADPLAN_RECONCILIATION')
        if raw.get('purchaseorder') and not joint:check(False,'QUALIFIED_UNDECLARED_PURCHASE')
    except (KeyError,TypeError,ValueError,ArithmeticError) as exc:
        missing.append('unreadable_native_resource_evidence:'+type(exc).__name__)
    from joint_qualified import cut_enabled
    return {'status':'FAIL' if issues else 'NOT_CHECKED' if missing else 'PASS','scope':'bounded_machine_qualified_operator_native_pre_post',
        **({'excluded_purchase_budget':{'resource_id':purchase_budget,'native_references':sorted(purchase_refs),
            'unclassified_references':sorted(pending_purchase_refs),'validation_status':purchase_validation['status'],
            'scope':'purchase capacity only; never manufacturing equipment/person occupancy'}} if purchase_budget else {}),
        'issues':issues,'missing_evidence':sorted(set(missing)),'assignments':assignments,
        'limits':(['CUT/SEW/QC/PACK each require one qualified person and distinct processing resource; inspection completion is not physical quality release.'] if finished_enabled(data) else ['Fixed complete CUT and continuous SEW each require one named skilled person; bucket quantity and continuous occupancy remain separate.'] if cut_enabled(data) else ['Joint profile: only continuous SEW requires one named skilled person; fixed CUT is independently audited, no CUT staffing claim.'] if joint else ['One person and one specified machine; no efficiency/setup/multi-profile combinations.'])+['No enterprise execution authorization or physical execution evidence.']}
