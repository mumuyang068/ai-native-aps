"""Bounded independent CUT WIP followed by three real routing leaf operations.

Native resource pools select actual qualified machines. No scheduling heuristic,
execution ledger or permission to move a confirmed routing order is introduced.
"""
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
import hashlib
import json

PROFILE='routing-alternate-capacity-v1'

def enabled(data):
    return data.get('rules',{}).get('manufacturing_profile')==PROFILE

def validate(data):
    from planning_adapter import InputError
    from multi_receipt_supply import validate_supply_facts, mos, q
    def require(ok,why):
        if not ok:raise InputError('ROUTING_CAPACITY_UNSUPPORTED: '+why)
    require(enabled(data),'explicit profile required')
    require(not any(k in data for k in ['forecasts','inventory_policies','inventory_decision_catalog','execution_checkpoint','accessory_supply_option']), 'no policy/forecast/checkpoint composition')
    require(not data['reservations'] and not data.get('previous_manufacturing_suggestions'),'no reservations or historical proposal replay')
    require(data['rules'].get('move_approved_early')==0 and data['rules'].get('erase_previous_proposed') is True and data['rules'].get('fix_broken_supply_path') is False,'protected native parameters')
    if data['rules'].get('procurement_strategy')=='MINCOST':raise InputError('MINCOST_HARD_QC_UNSUPPORTED_919')
    require('procurement_strategy' not in data['rules'],'no procurement strategy combination')
    kinds={k:[i for i in data['items'] if i['kind']==k] for k in ['RAW','SEMI','FINISHED']}
    require(len(data['items'])==3 and all(len(v)==1 for v in kinds.values()),'one RAW, independent WIP and stage output')
    raw,wip,fg=[kinds[k][0] for k in ['RAW','SEMI','FINISHED']]
    require(len(data['inventory_positions'])==3 and Counter(i['item_id'] for i in data['inventory_positions'])==Counter(i['id'] for i in data['items']),'one source opening per material')
    require(len(data['production_recipes'])==len(data['bom_revisions'])==len(data['routing_revisions'])==2,'exactly two independent recipes')
    require({p['output_item_id'] for p in data['production_recipes']}=={wip['id'],fg['id']},'linear WIP supply graph')
    require(all(d['item_id']==fg['id'] for d in data['demand_lines']),'stage demand only')
    recipes={p['id']:p for p in data['production_recipes']};routes={r['id']:r for r in data['routing_revisions']};boms={b['id']:b for b in data['bom_revisions']}
    now=datetime.fromisoformat(data['planning_at']);end=datetime.fromisoformat(data['horizon']['end_at'])
    resources={r['id']:r for r in data['resources']};calendars={c['id']:c for c in data['calendars']}
    require(len(resources)==len(data['resources']),'unique resource identities')
    pools={r['role']:r for r in resources.values() if r.get('role') in {'MACHINE_POOL','OPERATOR_POOL'}}
    people=[r for r in resources.values() if r.get('role')=='OPERATOR']
    require(len(pools)==2 and sum(r.get('role')=='MACHINE_POOL' for r in resources.values())==sum(r.get('role')=='OPERATOR_POOL' for r in resources.values())==1 and len(people)==1,'one machine pool and one named operator')
    machine_pool=pools['MACHINE_POOL'];person_pool=pools['OPERATOR_POOL'];person=people[0]
    require(person.get('pool_id')==person_pool['id'],'named person pool identity')
    members=[r for r in resources.values() if r.get('pool_id')==machine_pool['id']]
    require(2<=len(members)<=3 and all(r.get('role')=='MACHINE' for r in members),'two/three alternative machines only')
    require(all(r.get('role') in {'MACHINE','MACHINE_POOL','OPERATOR','OPERATOR_POOL'} for r in resources.values()),'unknown resource role')
    for r in resources.values():
        require(r.get('parallel_units')=='1' and r.get('capacity_model','CONTINUOUS')=='CONTINUOUS' and not {'window_capacity_quantity','capacity_uom','maxearly_seconds'}&r.keys(),'unit continuous capacity, no quantity buckets')
        require(r['location_id']==data['site']['id'] and r['availability_calendar_id'] in calendars,'resource site/calendar')
        if r.get('role')=='MACHINE' and 'pool_id' in r:require(r['pool_id']==machine_pool['id'],'machine belongs to the declared machine pool')
        elif r.get('role')!='OPERATOR':require('pool_id' not in r,'pools and dedicated machines cannot have a parent')
        envelope=calendars[person_pool['availability_calendar_id']]['available_intervals']
        require(all(any(datetime.fromisoformat(e['start_at'])<=datetime.fromisoformat(i['start_at'])<datetime.fromisoformat(i['end_at'])<=datetime.fromisoformat(e['end_at']) for e in envelope) for i in calendars[r['availability_calendar_id']]['available_intervals']),'resource calendar outside working envelope')
    skills={s['id'] for s in data['skills']};require(len(skills)==len(data['skills']),'unique skills')
    qualifications={}
    for s in data['resource_skills']:
        key=(s['resource_id'],s['skill_id']);require(key not in qualifications,'duplicate qualification')
        require(s['resource_id'] in resources and resources[s['resource_id']].get('role') in {'MACHINE','OPERATOR'} and s['skill_id'] in skills,'qualifications require real named resources')
        require(s['priority']>0 and datetime.fromisoformat(s['effective_from'])<=now and end<=datetime.fromisoformat(s['effective_to']),'positive priority and full-horizon qualification')
        qualifications[key]=s
    for p in recipes.values():
        route=routes[p['routing_revision_id']];bom=boms[p['bom_revision_id']];ops=sorted(route['operations'],key=lambda o:o['sequence']);up=p['output_item_id']==wip['id']
        require(len(ops)==(1 if up else 3),'one upstream leaf and exactly three downstream routing steps')
        require(p['transfer_policy']=='WHOLE_MANUFACTURING_BATCH' and q(p['manufacturing_minimum_quantity'])==q(p['manufacturing_multiple'])==1 and 'manufacturing_maximum_quantity' not in p,'integer complete transfer units; no fixed-batch combination')
        require(all(datetime.fromisoformat(v['effective_from'])<=now and end<=datetime.fromisoformat(v['effective_to']) for v in [p,route,bom]),'full-horizon recipe validity')
        require(len(bom['lines'])==1 and bom['lines'][0]['item_id']==(raw if up else wip)['id'],'linear raw/WIP consumption only')
        line=bom['lines'][0]
        require(q(bom['output_quantity'])>0 and q(line['quantity_per_output'])>0 and q(line['scrap_fraction'])==0 and line['consume_operation_id']==ops[0]['id'] and line['consume_at']=='START','positive sourced BOM ratio at first step, zero loss')
        require(bom['output_uom']==(wip if up else fg)['base_uom'],'BOM output unit')
        if not up:require(ops[0]['resource_id']==machine_pool['id'] and ops[0].get('machine_skill_id') in skills,'first downstream step must use the qualified machine pool')
        previous=None
        for o in ops:
            require(o['predecessors']==([] if previous is None else [previous]),'linear predecessors')
            previous=o['id'];r=resources.get(o['resource_id']);rq=o.get('operator_requirement',{})
            require(r is not None and r['role'] in {'MACHINE','MACHINE_POOL'} and o['resource_units']=='1','one machine requirement per step')
            require(o['duration_model']=='FIXED_PLUS_PER_UNIT' and q(o['fixed_seconds'])==0 and q(o['seconds_per_unit'])>0,'positive continuous processing only')
            require(rq.get('pool_id')==person_pool['id'] and rq.get('quantity')=='1' and (person['id'],rq.get('skill_id')) in qualifications,'actual named qualified operator per step')
            if r['role']=='MACHINE_POOL':
                require(not up and o is ops[0] and o.get('machine_skill_id') in skills,'only first downstream step uses certified machine pool')
                require(sum((m['id'],o['machine_skill_id']) in qualifications for m in members)>=2,'at least two qualified alternate machines')
            else:require('machine_skill_id' not in o,'dedicated machine cannot hide an ignored skill field')
    validate_supply_facts(data,require)
    for m in mos(data):
        p=recipes.get(m['recipe_id']);require(p is not None and p['output_item_id']==wip['id'],'confirmed only for independent upstream leaf; routing parent freeze is unverified')
        o=routes[p['routing_revision_id']]['operations'][0];require(len(m['operations'])==1,'one confirmed upstream commitment')
        c=m['operations'][0];a=datetime.fromisoformat(c['start_at']);b=datetime.fromisoformat(c['end_at'])
        require(m['source_status']=='confirmed' and m['freeze_policy']=='LOCKED' and q(m['completed_quantity'])==0,'unexecuted confirmed leaf only')
        require((m['item_id'],m['location_id'],m['uom'])==(p['output_item_id'],data['site']['id'],wip['base_uom']),'confirmed item/location/unit')
        require(c['operation_id']==o['id'] and c['resource_id']==o['resource_id'] and c.get('operator_id')==person['id'] and c['resource_units']=='1','confirmed leaf resources must remain exact')
        require(now<=a<b<=end and q((b-a).total_seconds())==q(m['quantity'])*q(o['seconds_per_unit']),'confirmed duration/window')
        require(all(any(datetime.fromisoformat(i['start_at'])<=a<b<=datetime.fromisoformat(i['end_at']) for i in calendars[resources[r]['availability_calendar_id']]['available_intervals']) for r in [c['resource_id'],person['id']]),'confirmed commitment conflicts with availability')

def map_native(data,native):
    """The original qualified mapper adds people; native pools select machines."""
    resources={r['id']:r for r in data['resources']}
    specs={p['id']+':'+o['id']:o for p in data['production_recipes'] for rt in data['routing_revisions'] if rt['id']==p['routing_revision_id'] for o in rt['operations']}
    for row in native['resource']:
        fact=resources[row['name']]
        if fact.get('role')=='MACHINE' and fact.get('pool_id'):row['owner']=fact['pool_id']
    # The ordinary CSV importer resolves owner FKs row by row. Pools must exist
    # before their children; changing only the array order changes no fact.
    native['resource'].sort(key=lambda r:bool(r.get('owner')))
    for load in native['operationresource']:
        o=specs[load['operation']]
        if load['resource']==o['resource_id'] and o.get('machine_skill_id'):
            load.update(skill=o['machine_skill_id'],search='PRIORITY')
    return native

def audit_resources(data,raw,*,run_id=None):
    """Audit independent evidence blocks; absence never hides known violations."""
    from contextlib import contextmanager
    from calculation_validation import evidence_rows
    from multi_receipt_supply import result_operations,q
    from qualified_operator import is_priority
    issues=[];missing=[];assignments=[];peaks={};zone=ZoneInfo(data['site']['timezone'])
    def check(ok,code,ref=None):
        if not ok:issues.append(dict(code=code,reference=ref))
    @contextmanager
    def guard(label):
        try:yield
        except (KeyError,TypeError,ValueError,ArithmeticError) as exc:missing.append(label+':'+type(exc).__name__)
    def at(v):
        t=datetime.fromisoformat(str(v).replace('Z','+00:00'));return t.replace(tzinfo=zone) if t.tzinfo is None else t.astimezone(zone)
    def collected(obj,key,label):
        rows=obj.get(key)
        if not isinstance(rows,list):missing.append(label+':'+key);return None
        return rows
    rs={r['id']:r for r in data['resources']};cs={c['id']:c for c in data['calendars']}
    specs={p['id']+':'+o['id']:(p,o) for p in data['production_recipes'] for rt in data['routing_revisions'] if rt['id']==p['routing_revision_id'] for o in rt['operations']}
    work_calendar=cs[next(r['availability_calendar_id'] for r in data['resources'] if r.get('role')=='OPERATOR_POOL')]
    def active_seconds(calendar,a,b):
        return sum((max(Decimal(0),Decimal(str((min(b,at(i['end_at']))-max(a,at(i['start_at']))).total_seconds()))) for i in calendar['available_intervals']),Decimal(0))
    expected_hash=hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    leaves=[];loads=None
    with guard('result_operations'):leaves=result_operations(data,raw)
    with guard('operationplanresource'):loads=evidence_rows(raw.get('operationplanresource'))
    if loads is None:missing.append('operationplanresource')
    for table in ['workorder','manufacturingorder']:
        if evidence_rows(raw.get(table)) is None:missing.append(table)
    known_leaf_refs={str(w['reference']) for w in leaves if 'reference' in w}
    catalog_complete=all(evidence_rows(raw.get(t)) is not None and all('reference' in r and 'owner' in r for r in evidence_rows(raw.get(t))) for t in ['workorder','manufacturingorder'])
    if loads is not None:
        ids=[l['id'] for l in loads if 'id' in l]
        check(len(ids)==len(set(ids)),'ROUTING_DUPLICATE_NATIVE_LOAD_ID','operationplanresource')
        for l in loads:
            with guard('raw_load_identity:'+str(l.get('id'))):
                if 'id' not in l:missing.append('operationplanresource:id')
                if catalog_complete:check(str(l['operationplan']) in known_leaf_refs,'ROUTING_UNEXPLAINED_RAW_LOAD_REFERENCE',l.get('id'))
                check(l['resource'] in rs and rs[l['resource']]['role'] in {'MACHINE','OPERATOR'},'ROUTING_UNDECLARED_RAW_LOAD_RESOURCE',l.get('id'))
    active=defaultdict(list);parents=defaultdict(list);expected_events=Counter();complete_refs=set()
    for table in ['workorder','manufacturingorder']:
        rows=evidence_rows(raw.get(table))
        if rows is not None:
            refs=[str(r['reference']) for r in rows if 'reference' in r]
            check(len(refs)==len(set(refs)),'ROUTING_DUPLICATE_NATIVE_REFERENCE',table)
    for w in leaves:
        if w.get('owner') is not None:parents[str(w['owner'])].append(w)
    for index,w in enumerate(leaves):
        with guard('leaf:'+str(w.get('reference',index))):
            ref=str(w['reference']);p,o=specs[w['operation']];a,b=at(w['startdate']),at(w['enddate']);qty=q(w['quantity'])
            check(a<b and qty>0,'ROUTING_LEAF_QUANTITY_OR_WINDOW',ref)
            check(active_seconds(work_calendar,a,b)==qty*q(o['seconds_per_unit']),'ROUTING_PROCESS_DURATION',ref)
            linked=[l for l in loads or [] if str(l['operationplan'])==ref]
            machines=[l for l in linked if rs.get(l['resource'],{}).get('role')=='MACHINE'];people=[l for l in linked if rs.get(l['resource'],{}).get('role')=='OPERATOR']
            if loads is not None:check(len(linked)==2 and len(machines)==len(people)==1,'ROUTING_MISSING_REAL_MACHINE_OR_PERSON',ref)
            leaf_complete=True
            for l in linked:
                previous_missing=len(missing)
                with guard('load:'+ref+':'+str(l.get('resource'))):
                    r=rs.get(l['resource']);check(r is not None and r.get('role') in {'MACHINE','OPERATOR'},'ROUTING_POOL_IS_NOT_ASSIGNMENT',ref)
                    if not r:continue
                    la,lb=at(l['startdate']),at(l['enddate']);units=q(l['quantity'])
                    check((la,lb)==(a,b) and units==1,'ROUTING_LOAD_SYNCHRONIZATION',ref)
                    check(active_seconds(cs[r['availability_calendar_id']],la,lb)==active_seconds(work_calendar,la,lb),'ROUTING_OFF_CALENDAR',r['id'])
                    if r['role']=='OPERATOR':
                        requirement=o['operator_requirement'];check(r.get('pool_id')==requirement['pool_id'],'ROUTING_PERSON_POOL',ref);skill=requirement['skill_id']
                    elif o.get('machine_skill_id'):
                        check(r.get('pool_id')==o['resource_id'],'ROUTING_MACHINE_POOL',ref);skill=o['machine_skill_id']
                    else:check(r['id']==o['resource_id'],'ROUTING_DEDICATED_MACHINE',ref);skill=None
                    if skill:check(any(s['resource_id']==r['id'] and s['skill_id']==skill and s['priority']>0 and at(s['effective_from'])<=a<b<=at(s['effective_to']) for s in data['resource_skills']),'ROUTING_UNQUALIFIED_ASSIGNMENT',ref)
                    active[r['id']].extend([(la,1,units),(lb,0,-units)])
                    for when,delta in [(la,units),(lb,-units)]:expected_events[(ref,r['id'],la.isoformat(),lb.isoformat(),when.isoformat(),str(delta.normalize()))]+=1
                leaf_complete=leaf_complete and len(missing)==previous_missing
            if leaf_complete and loads is not None:complete_refs.add(ref)
            assignments.append(dict(native_reference=ref,owner=str(w['owner']) if w.get('owner') is not None else None,operation_id=o['id'],
                machine_id=machines[0]['resource'] if len(machines)==1 else None,operator_id=people[0]['resource'] if len(people)==1 else None,
                quantity=str(qty),start_at=a.isoformat(),end_at=b.isoformat(),source_status=w['status'],
                required_skill_id=o['operator_requirement']['skill_id'],machine_skill_id=o.get('machine_skill_id')))
    for rid,events in active.items():
        with guard('resource_peak:'+rid):
            running=peak=Decimal(0)
            for _,_,delta in sorted(events):running+=delta;peak=max(peak,running);check(0<=running<=q(rs[rid]['parallel_units']),'ROUTING_RESOURCE_CAPACITY',rid)
            peaks[rid]=str(peak)
    parent_rows=evidence_rows(raw.get('manufacturingorder'));child_rows=evidence_rows(raw.get('workorder'))
    if parent_rows is not None and child_rows is not None:
        for root in parent_rows:
            with guard('root_children:'+str(root.get('reference'))):
                recipe=next((p for p in data['production_recipes'] if p['id']==root['operation']),None)
                if recipe is None:continue  # Input-identified confirmed leaf / QC release, audited separately.
                route=next(r for r in data['routing_revisions'] if r['id']==recipe['routing_revision_id'])
                expected=Counter(recipe['id']+':'+o['id'] for o in route['operations'])
                actual=Counter(w['operation'] for w in child_rows if str(w['owner'])==str(root['reference']))
                check(actual==expected,'ROUTING_PARENT_CHILD_SET',str(root['reference']))
    for parent,children in parents.items():
        matches=[r for r in parent_rows or [] if str(r.get('reference'))==parent]
        if parent_rows is not None:
            check(len(matches)==1,'ROUTING_PARENT_REFERENCE_MISSING',parent)
            if len(matches)==1:
                root=matches[0]
                with guard('parent_recipe:'+parent):
                    recipe=next((p for p in data['production_recipes'] if p['id']==root['operation']),None)
                    check(recipe is not None and root['owner'] is None,'ROUTING_PARENT_RECIPE',parent)
                    if recipe is not None:
                        route=next(r for r in data['routing_revisions'] if r['id']==recipe['routing_revision_id'])
                        check(Counter(w['operation'] for w in children)==Counter(recipe['id']+':'+o['id'] for o in route['operations']),'ROUTING_PARENT_CHILD_SET',parent)
                for child in children:
                    with guard('parent_quantity:'+parent+':'+str(child.get('reference'))):
                        check(q(child['quantity'])==q(root['quantity']) and child['status']==root['status'],'ROUTING_PARENT_QUANTITY_STATUS',parent)
                    with guard('parent_window:'+parent+':'+str(child.get('reference'))):
                        check(at(root['startdate'])<=at(child['startdate'])<at(child['enddate'])<=at(root['enddate']),'ROUTING_PARENT_WINDOW',parent)
        ordered=sorted(children,key=lambda w:specs.get(w.get('operation'),({},{}))[1].get('sequence',0))
        for left,right in zip(ordered,ordered[1:]):
            with guard('precedence:'+parent):check(at(left['enddate'])<=at(right['startdate']),'ROUTING_PRECEDENCE_VIOLATION',parent)
    for phase in ['fixed_batch_pre','fixed_batch_post']:
        snap=raw.get(phase)
        if not isinstance(snap,dict):missing.append(phase);continue
        # Independent protection fields are checked before optional model blocks.
        for key,expected,code in [('run_id',run_id,'ROUTING_NATIVE_IDENTITY'),('input_hash',expected_hash,'ROUTING_NATIVE_IDENTITY'),('move_approved_early',0,'ROUTING_GLOBAL_PROTECTION')]:
            if key not in snap:missing.append(phase+':'+key)
            else:check(snap[key]==expected,code,phase)
        calendars=collected(snap,'calendars',phase)
        for calendar in data['calendars']:
            with guard(phase+':calendar:'+calendar['id']):
                found=[c for c in calendars or [] if c.get('name')==calendar['id']]
                if len(found)!=1:missing.append(phase+':calendar:'+calendar['id']);continue
                observed=found[0];check(q(observed['default'])==0,'ROUTING_NATIVE_CALENDAR_DEFAULT',calendar['id'])
                expected=Counter((at(w['start_at']).isoformat(),at(w['end_at']).isoformat()) for w in calendar['available_intervals'])
                buckets=collected(observed,'buckets',phase+':'+calendar['id'])
                if buckets is None:continue
                actual=Counter();complete=True
                for bucket in buckets:
                    before=len(missing)
                    with guard(phase+':calendar_bucket:'+calendar['id']):
                        check(q(bucket['value'])==1 and bucket['days']==127 and bucket['starttime']==0 and bucket['endtime']==86400,'ROUTING_NATIVE_CALENDAR_CAPACITY',calendar['id'])
                        actual[(at(bucket['start']).isoformat(),at(bucket['end']).isoformat())]+=1
                    complete=complete and before==len(missing)
                if complete:check(actual==expected,'ROUTING_NATIVE_CALENDAR_INTERVALS',calendar['id'])
        operations=collected(snap,'operations',phase)
        native_operations={o.get('name'):o for o in operations or []}
        for name,expected_type in [(p['id'],'operation_routing') for p in data['production_recipes']]+[(name,'operation_time_per') for name in specs]:
            with guard(phase+':operation:'+name):
                observed=native_operations.get(name)
                if observed is None:missing.append(phase+':operation:'+name)
                else:check(observed['type']==expected_type,'ROUTING_NATIVE_OPERATION_TYPE',name)
        model=snap.get('qualified_model')
        if not isinstance(model,dict):missing.append(phase+':qualified_model')
        else:
            if 'fix_broken_supply_path' not in model:missing.append(phase+':fix_broken_supply_path')
            else:check(model['fix_broken_supply_path']=='false','ROUTING_GLOBAL_PROTECTION',phase)
            resources=collected(model,'resources',phase+':qualified_model')
            for r in data['resources']:
                with guard(phase+':resource:'+r['id']):
                    found=[a for a in resources or [] if a.get('name')==r['id']]
                    if len(found)!=1:missing.append(phase+':resource:'+r['id']);continue
                    observed=found[0];check(observed['owner']==r.get('pool_id') and q(observed['maximum'])==1,'ROUTING_NATIVE_RESOURCE_IDENTITY',r['id'])
                    check(observed['maximum_calendar']==r['availability_calendar_id'],'ROUTING_NATIVE_RESOURCE_CALENDAR',r['id'])
            skills=collected(model,'resource_skills',phase+':qualified_model')
            if skills is not None:
                with guard(phase+':resource_skills'):
                    expected_skills=Counter((s['resource_id'],s['skill_id'],s['priority'],at(s['effective_from']).isoformat(),at(s['effective_to']).isoformat()) for s in data['resource_skills'])
                    actual_skills=Counter((s['resource'],s['skill'],s['priority'],at(s['effective_start']).isoformat(),at(s['effective_end']).isoformat()) for s in skills)
                    check(actual_skills==expected_skills,'ROUTING_NATIVE_SKILLS',phase)
            operation_loads=collected(model,'operation_loads',phase+':qualified_model')
            if operation_loads is not None:
                for name,(p,o) in specs.items():
                    with guard(phase+':operation_loads:'+name):
                        person=next(r['id'] for r in data['resources'] if r.get('role')=='OPERATOR')
                        required=[(o['resource_id'],'1',o.get('machine_skill_id')),(person,'1',o['operator_requirement']['skill_id'])]
                        actual=[l for l in operation_loads if l['operation']==name]
                        check(Counter((l['resource'],str(q(l['quantity']).normalize()),l['skill']) for l in actual)==Counter(required),'ROUTING_NATIVE_OPERATION_LOADS',name)
                        check(all(is_priority(l['search']) for l in actual if l['skill']),'ROUTING_NATIVE_SEARCH',name)
        if phase=='fixed_batch_post':
            resources=collected(snap,'resources',phase)
            if resources is not None:
                names=[r.get('name') for r in resources]
                check(len(names)==len(set(names)),'ROUTING_DUPLICATE_NATIVE_RESOURCE',phase)
                for resource in resources:
                    if resource.get('name') not in rs:
                        with guard(phase+':undeclared_resource'):
                            plans=collected(resource,'loadplans',phase+':undeclared_resource')
                            if plans is not None:check(not any(q(l['quantity']) for l in plans),'ROUTING_UNDECLARED_NATIVE_RESOURCE_LOAD',resource.get('name'))
            known_refs={str(w['reference']) for w in leaves if 'reference' in w}
            catalog_complete=all(evidence_rows(raw.get(t)) is not None and all('reference' in r and 'owner' in r for r in evidence_rows(raw.get(t))) for t in ['workorder','manufacturingorder'])
            for rid in rs:
                with guard(phase+':loadplans:'+rid):
                    found=[r for r in resources or [] if r.get('name')==rid]
                    if len(found)!=1:missing.append(phase+':loadplans:'+rid);continue
                    plans=collected(found[0],'loadplans',phase+':'+rid)
                    if plans is None or loads is None:continue
                    actual_events=Counter();complete=True
                    for l in plans:
                        before=len(missing)
                        with guard(phase+':loadplan:'+rid):
                            units=q(l['quantity']);ref=str(l['reference'])
                            if units and catalog_complete:check(ref in known_refs,'ROUTING_UNEXPLAINED_LOADPLAN_REFERENCE',ref)
                            if units and ref in complete_refs:actual_events[(ref,rid,at(l['start']).isoformat(),at(l['end']).isoformat(),at(l['at']).isoformat(),str(units.normalize()))]+=1
                        complete=complete and before==len(missing)
                    if complete:check(actual_events==Counter({k:v for k,v in expected_events.items() if k[1]==rid and k[0] in complete_refs}),'ROUTING_NATIVE_LOADPLAN_RECONCILIATION',rid)
    return dict(status='FAIL' if issues else 'NOT_CHECKED' if missing else 'PASS',scope='bounded_routing_alternate_machine_named_operator',
        issues=issues,missing_evidence=sorted(set(missing)),assignments=assignments,resource_peaks=peaks,
        limits=['Routing intermediate steps have no independent WIP buffers.','Confirmed routing parent/leaf editing is unsupported.','Planned inspection is not physical quality release.','No execution authorization or optimum claim.'])
