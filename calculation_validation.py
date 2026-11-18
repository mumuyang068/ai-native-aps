"""Bounded checks for the existing sequential, single-site P3 profile.

No solver or rule engine. Native records stay immutable. Procurement profiles
continue to use their existing specialized validators.
"""
from collections import Counter, defaultdict
from datetime import datetime
from inventory_availability import net_available
from decimal import Decimal
import re
from zoneinfo import ZoneInfo

REPORTED_STOCK='reported_material_onhand_nonnegative'
MATERIAL_SCOPE='bounded_material_event_reconciliation'

def same_events(expected,actual):
    """JOINT's existing pure multiset matcher, without its procurement imports.

    Source: f-integrate-02/joint/results.py:same_events. Keep the same
    identity, multiplicity and 1e-6 tolerance; no new planning semantics.
    """
    def groups(records):
        grouped=defaultdict(list)
        for (item,ref,qty,when),count in records.items():
            grouped[(item,ref,when)].extend([Decimal(str(qty))]*count)
        return grouped
    a,b=groups(expected),groups(actual)
    return a.keys()==b.keys() and all(len(a[k])==len(b[k]) and all(abs(x-y)<Decimal('0.000001') for x,y in zip(sorted(a[k]),sorted(b[k]))) for k in a)

def rows(value):
    return value.get('rows',value.get('results',[])) if isinstance(value,dict) else value

def evidence_rows(value):
    """Unknown/error envelopes are missing evidence, never empty result tables."""
    if isinstance(value,dict):value=value.get('rows',value.get('results'))
    return value if isinstance(value,list) and all(isinstance(r,dict) for r in value) else None

def engine_provenance(metadata):
    # Reference release source is documentation, not binary build attestation.
    return {'reference_source_commit':metadata.get('reference_source_commit'),
            'actual_build_source_commit':None,'binary_commit_attested':False,
            'engine_reported_version':metadata.get('engine_reported_version'),
            'engine_image_digest':metadata.get('engine_image_digest') or metadata.get('image_digest'),
            'binary_identity':metadata.get('binary_identity'),
            'attestation_status':'NOT_ATTESTED',
            'limits':['A release version or reference commit does not prove the binary build commit.']}

def purchase_events(data,rows,check,event):
    """Reuse the same confirmed identity and QC-event checks in both profiles."""
    from datetime import timedelta
    zone=ZoneInfo(data['site']['timezone'])
    def q(value):
        result=Decimal(str(value))
        if not result.is_finite():raise ValueError('NONFINITE_QUANTITY')
        return result
    def at(value):
        result=datetime.fromisoformat(value.replace('Z','+00:00'))
        return result.replace(tzinfo=zone) if result.tzinfo is None else result.astimezone(zone)
    expected=Counter()
    confirmed={p['id']:p for p in data['existing_orders'] if p['type']=='PO'};seen_confirmed=set()
    for po in rows:
        ref=str(po['reference'])
        source=next((s for s in data['supply_sources'] if (s['item_id'],s['supplier_id'],s['location_id'])==
                    (po['item'],po['supplier'],po['location'])),None)
        check(source is not None,'MATERIAL_UNKNOWN_SUPPLY_PATH',ref)
        original=confirmed.get(ref)
        if original:
            check(ref not in seen_confirmed,'CONFIRMED_PO_DUPLICATE',ref);seen_confirmed.add(ref)
            check(po['status']=='confirmed' and po['item']==original['item_id'] and po['location']==original['location_id']
                and po['supplier']==original['supplier_id'] and q(po['quantity'])==q(original['remaining_quantity'])
                and at(po['startdate'])==at(original.get('planned_start_at') or original['ordered_at']) and at(po['enddate'])==at(original['expected_receipt_at']),
                'CONFIRMED_PO_CHANGED',ref)
            available=at(original['available_at'])
        else:
            check(po['status']=='proposed','LEGACY_UNSUPPORTED_PURCHASE_STATUS',ref)
            from datetime import timedelta
            available=at(po['enddate'])+timedelta(seconds=(source or {}).get('receiving_qc',{}).get('wait_seconds',0))
        expected[event(po['item'],ref,q(po['quantity']),available.isoformat())]+=1
    check(seen_confirmed==set(confirmed),'CONFIRMED_PO_LOST')
    return expected


def material_validation(data,raw,*,run_id=None):
    if 'foundation_projection' in data:
        from p22.foundation_audit import audit
        return audit(data,raw,run_id=run_id)
    from multi_receipt_supply import enabled as multi_receipt_enabled,audit as multi_receipt_audit
    if multi_receipt_enabled(data):return multi_receipt_audit(data,raw,run_id=run_id)
    from finished_delivery import enabled as finished_enabled,audit as finished_audit
    if finished_enabled(data):return finished_audit(data,raw,run_id=run_id)
    from network_distribution import enabled as network_enabled,audit as network_audit
    if network_enabled(data):return network_audit(data,raw,run_id=run_id)
    from single_lane_distribution import enabled as distribution_enabled,audit as distribution_audit
    if distribution_enabled(data):return distribution_audit(data,raw,run_id=run_id)
    from two_level_wip import material_enabled as enabled,audit
    if enabled(data):return audit(data,raw)
    return routing_material_validation(data,raw,run_id=run_id)


def routing_material_validation(data,raw,*,run_id=None):
    """Reconcile linear BOM/PO/delivery events, initial nettable stock and balances.

    Reuses JOINT's multiset/tolerance, confirmed-PO identity and QC event checks.
    Only the single complete receipt admitted by validate_input is supported.
    """
    from fixed_batch import enabled,material_check
    if enabled(data):return material_check(data,raw)
    from planning_adapter import validate_input,InputError
    limits=['full_general_material_balance','physical_execution_feasibility']
    unsupported=['MTO_and_physical_lots','hard_reservations','segmented_supply_paths','nonlinear_or_coproduct_flows','legacy_confirmed_supply_and_receiving_QC']
    result={'status':'NOT_CHECKED','scope':MATERIAL_SCOPE,'issues':[],
            'not_checked':limits,'unsupported':unsupported,'missing_evidence':[]}
    if data.get('procurement') or data.get('engine',{}).get('profile')!='frepple-community-9.19-supply-v1':
        result['reason']='Use the existing procurement/joint validator for this profile.';return result
    try:validate_input(data)
    except InputError as e:
        result.update(status='UNSUPPORTED',reason=str(e));return result
    if data['existing_orders']:
        result['unsupported'].remove('legacy_confirmed_supply_and_receiving_QC')
        result['unsupported'].extend(['partial_receipts','multiple_confirmed_purchases','non_confirmed_existing_supply'])
    required=['operationplanmaterial','purchaseorder','manufacturingorder','workorder','deliveryorder','demand']
    tables={n:evidence_rows(raw.get(n)) for n in required}
    missing=[n for n in required if tables[n] is None]
    if missing:result['missing_evidence']=missing;return result
    if Counter(r.get('name') for r in tables['demand'])!=Counter(r['id'] for r in data['demand_lines']):
        result['missing_evidence']=['demand'];result['reason']='Demand evidence does not cover the input order identities exactly.';return result
    issues=result['issues']
    def check(ok,code,ref=None):
        if not ok:issues.append({'code':code,'reference':ref})
    def q(v):
        x=Decimal(str(v))
        if not x.is_finite():raise ValueError('NONFINITE_QUANTITY')
        return x
    zone=ZoneInfo(data['site']['timezone'])
    def at(v):
        t=datetime.fromisoformat(v.replace('Z','+00:00'))
        return t.replace(tzinfo=zone) if t.tzinfo is None else t.astimezone(zone)
    def event(item,ref,quantity,when):return ((item,data['site']['id']),str(ref),str(quantity),at(when).isoformat())
    expected=Counter();actual=Counter();opening=defaultdict(Decimal);initial=defaultdict(Decimal)
    recipes={r['id']:r for r in data['production_recipes']};boms={b['id']:b for b in data['bom_revisions']};routes={r['id']:r for r in data['routing_revisions']}
    plan_start=at(data['planning_at'])
    processing_starts={}
    if data.get('plan_editor_contract',{}).get('setup'):
        from plan_editor import audit_native
        proof=audit_native(data,raw,run_id=run_id)
        if proof['status']!='PASS':
            result.update(status=proof['status'],issues=proof['issues'],missing_evidence=proof['missing_evidence']);return result
        processing_starts={str(p['reference']):p['setup_end'] for p in raw['fixed_batch_post']['operationplans'] if 'setup_end' in p}
    try:
        roots={str(r['reference']):r for r in tables['manufacturingorder'] if r.get('owner') is None}
        children=tables['workorder'];seen=Counter()
        for p in children:
            parent=roots.get(str(p.get('owner')));check(parent is not None,'MATERIAL_UNKNOWN_PARENT',p['reference'])
            if parent is None:continue
            recipe=recipes.get(parent['operation']);check(recipe is not None,'MATERIAL_UNKNOWN_RECIPE',parent['operation'])
            if recipe is None:continue
            route=routes[recipe['routing_revision_id']]
            op=next((o for o in route['operations'] if recipe['id']+':'+o['id']==p['operation']),None)
            check(op is not None,'MATERIAL_UNKNOWN_OPERATION',p['reference'])
            if op is None:continue
            seen[(str(parent['reference']),op['id'])]+=1
            qty=q(p['quantity']);check(abs(qty-q(parent['quantity']))<=Decimal('0.000001'),'MATERIAL_CHILD_QUANTITY',p['reference'])
            bom=boms[recipe['bom_revision_id']]
            for line in bom['lines']:
                if line['consume_operation_id']==op['id']:
                    amount=-qty*q(line['quantity_per_output'])/q(bom['output_quantity'])/(1-q(line['scrap_fraction']))
                    when=processing_starts.get(str(p['reference']),p['startdate']) if line['consume_at']=='START' else p['enddate']
                    expected[event(line['item_id'],p['reference'],amount,when)]+=1
            if op['sequence']==max(x['sequence'] for x in route['operations']):expected[event(recipe['output_item_id'],p['reference'],qty,p['enddate'])]+=1
        for ref,p in roots.items():
            recipe=recipes.get(p['operation']);check(recipe is not None,'MATERIAL_UNKNOWN_RECIPE',ref)
            if recipe:
                for op in routes[recipe['routing_revision_id']]['operations']:check(seen[(ref,op['id'])]==1,'MATERIAL_MISSING_OR_DUPLICATE_STEP',ref+':'+op['id'])
        expected.update(purchase_events(data,tables['purchaseorder'],check,event))
        demands={r['id']:r for r in data['demand_lines']};delivered=defaultdict(Decimal)
        for dl in tables['deliveryorder']:
            d=demands.get(dl.get('demand'));check(d is not None,'MATERIAL_UNKNOWN_DELIVERY',dl['reference'])
            if d is None:continue
            check(dl['item']==d['item_id'] and dl['location']==d['location_id'],'MATERIAL_DELIVERY_IDENTITY',dl['reference'])
            delivered[d['id']]+=q(dl['quantity']);expected[event(dl['item'],dl['reference'],-q(dl['quantity']),dl['startdate'])]+=1
        for d in tables['demand']:check(abs(delivered[d['name']]-q(d.get('plannedquantity') or 0))<=Decimal('0.000001'),'MATERIAL_DELIVERY_QUANTITY',d['name'])
        for inv in data['inventory_positions']:initial[(inv['item_id'],inv['location_id'])]+=net_available(inv)
        grouped=defaultdict(list)
        for index,f in enumerate(tables['operationplanmaterial']):
            key=(f['item'],f['location']);when=at(f['flowdate']);qty=q(f['quantity'])
            check(key[0] in {i['id'] for i in data['items']} and key[1]==data['site']['id'],'MATERIAL_UNKNOWN_BUFFER',key)
            grouped[key].append((when,index,f));check(q(f['onhand'])>=-Decimal('0.000001'),'NEGATIVE_STOCK',f.get('id'))
            if when<plan_start:opening[key]+=qty
            elif qty:actual[(key,str(f['operationplan']),str(qty),when.isoformat())]+=1
        check(all(abs(opening[k]-initial[k])<=Decimal('0.000001') for k in set(opening)|set(initial)),'MATERIAL_OPENING_STOCK')
        for key,events in grouped.items():
            balance=Decimal(0)
            for _,_,f in sorted(events,key=lambda x:(x[0],x[1])):
                balance+=q(f['quantity']);check(abs(balance-q(f['onhand']))<=Decimal('0.000001'),'MATERIAL_RUNNING_BALANCE',f.get('id'))
                check(balance>=-Decimal('0.000001'),'MATERIAL_NEGATIVE_RECONSTRUCTED',f.get('id'))
        check(same_events(expected,actual),'MATERIAL_BOM_EVENT_QUANTITY_OR_TIME')
    except (KeyError,TypeError,ValueError,ArithmeticError) as e:
        result.update(status='NOT_CHECKED',reason='Required evidence field unavailable or invalid: '+str(e));return result
    result.update(status='FAIL' if issues else 'PASS',expected_event_count=sum(expected.values()),actual_event_count=sum(actual.values()),
                  event_order='chronological; equal timestamps retain archived native row order')
    return result

# Only fields explicitly generated by the existing P3 adapter are compared.
READBACK={
 'itemdistribution':(('item','origin','location'),('leadtime','sizeminimum','sizemultiple','effective_start','effective_end','priority','source')),
 'parameter':(('name',),('value',)),
 'calendar':(('name',),('defaultvalue',)),
 'calendarbucket':(('calendar','startdate','enddate'),('value','priority','monday','tuesday','wednesday','thursday','friday','saturday','sunday','starttime','endtime')),
 'resource':(('name',),('type','location','maximum','available','constrained','maximum_calendar','maxearly','owner','setup','setupmatrix')),
 'setupmatrix':(('name',),()),
 'setuprule':(('setupmatrix','priority'),('fromsetup','tosetup','duration','cost','resource')),
 'skill':(('name',),()),
 'resourceskill':(('resource','skill'),('priority','effective_start','effective_end')),
 'operation':(('name',),('type','item','location','owner','priority','duration','duration_per','sizeminimum','sizemaximum','sizemultiple','available','fence','effective_start','effective_end')),
 'operationmaterial':(('operation','item','type'),('quantity',)),
 'operationresource':(('operation','resource'),('quantity','skill','search','setup')),
 'buffer':(('item','location','batch'),('onhand','minimum','maximum')),
 'itemsupplier':(('item','location','supplier'),('leadtime','sizeminimum','sizemultiple','sizemaximum','batchwindow','hard_safety_leadtime','extra_safety_leadtime','resource','resource_qty','effective_start','effective_end','fence')),
 'purchaseorder':(('reference',),('item','location','supplier','quantity','status','startdate','enddate','source')),
 'manufacturingorder':(('reference',),('operation','quantity','status','startdate','enddate','source','owner','resources')),
 'workorder':(('reference',),('operation','quantity','status','startdate','enddate','source','owner','resources')),
 'demand':(('name',),('item','location','customer','quantity','due','status','priority','minshipment','maxlateness')),
}
NUMBERS={'cost','resource_qty','defaultvalue','priority','minimum','maximum','sizeminimum','sizemaximum','sizemultiple','quantity','onhand','minshipment'}
DURATIONS={'duration','duration_per','leadtime','batchwindow','maxlateness','starttime','endtime','hard_safety_leadtime','extra_safety_leadtime','maxearly','fence'}
DATES={'startdate','enddate','due','effective_start','effective_end'}
def value(field,x):
    if x is None:return None
    if field=='resources':
        # CSV uses [resource, quantity] pairs; the real REST serializer returns
        # nested resource records. Compare both machine and named-person loads
        # by natural identity, preserving duplicates and exact quantities.
        if not isinstance(x,list):raise ValueError('Invalid resource bindings')
        bindings=[]
        for row in x:
            if isinstance(row,dict):
                if not {'resource','quantity'}<=row.keys():raise ValueError('Incomplete resource binding')
                resource,quantity=row['resource'],row['quantity']
            elif isinstance(row,(list,tuple)) and len(row)==2:resource,quantity=row
            else:raise ValueError('Invalid resource binding')
            quantity=Decimal(str(quantity))
            if not isinstance(resource,str) or not resource or not quantity.is_finite():
                raise ValueError('Invalid resource binding')
            bindings.append((resource,quantity))
        return sorted(bindings)
    if field=='value':
        try:return Decimal(str(x))
        except Exception:return str(x)
    if field in NUMBERS:return Decimal(str(x))
    if field in DATES:return datetime.fromisoformat(str(x).replace('Z','+00:00'))
    if field in DURATIONS:
        s=str(x);m=re.fullmatch(r'(?:(\d+) )?(\d+):(\d+):(\d+(?:\.\d+)?)',s)
        if m:return Decimal(m[1] or 0)*86400+Decimal(m[2])*3600+Decimal(m[3])*60+Decimal(m[4])
        m=re.fullmatch(r'PT(\d+(?:\.\d+)?)S',s)
        if m:return Decimal(m[1])
        raise ValueError('Unsupported duration representation')
    return x

def audit_loaded_input(native,readback):
    """Persisted REST input is evidence of DB import, not solver-memory loading."""
    issues=[];not_checked=[];checked=[]
    expected_tables=dict(native)
    owned=[r for r in native.get('manufacturingorder',[]) if r.get('owner')]
    if owned:
        # This projects original input expectations, never observed evidence.
        # The upstream MO CSV loader changes owned rows' persisted type to WO.
        expected_tables['manufacturingorder']=[dict(r,owner=None) for r in native['manufacturingorder'] if not r.get('owner')]
        expected_tables['workorder']=[*native.get('workorder',[]),*owned]
    elif 'manufacturingorder' in native and 'workorder' in readback:
        expected_tables.setdefault('workorder',[])
    required_owners={'manufacturingorder','workorder'} if owned else set()
    for table,(keys,fields) in READBACK.items():
        if table not in expected_tables:continue
        if table not in readback or readback[table] is None:
            not_checked.append(table)
            if table in required_owners:issues.append({'table':table,'code':'LOADED_TABLE_MISSING'})
            continue
        expected=expected_tables[table];actual=evidence_rows(readback[table])
        if actual is None:
            not_checked.append(table)
            if table in required_owners:issues.append({'table':table,'code':'LOADED_TABLE_MISSING'})
            continue
        if table!='parameter' and len(expected)!=len(actual):issues.append({'table':table,'code':'LOADED_ROW_COUNT_MISMATCH'})
        # Normalize natural identities once per table. Keep every duplicate so
        # an ambiguous readback still fails instead of silently picking a row.
        indexed={}
        try:
            for other in actual:
                if all(k in other for k in keys):
                    identity=tuple(value(k,other[k]) for k in keys)
                    indexed.setdefault(identity,[]).append(other)
        except (ValueError,ArithmeticError,TypeError):
            # Preserve the original short-circuit error contract for malformed
            # multi-field identities; these are never accepted as valid input.
            indexed=None
        for row in expected:
            try:
                matches=(indexed.get(tuple(value(k,row.get(k)) for k in keys),[]) if indexed is not None else
                         [r for r in actual if all(k in r and value(k,r[k])==value(k,row.get(k)) for k in keys)])
            except (ValueError,ArithmeticError,TypeError):
                issues.append({'table':table,'code':'LOADED_IDENTITY_INVALID','key':{k:row.get(k) for k in keys}});continue
            if len(matches)!=1:issues.append({'table':table,'code':'LOADED_IDENTITY_MISMATCH','key':{k:row.get(k) for k in keys}});continue
            other=matches[0]
            for field in [*keys,*[f for f in fields if f in row]]:
                if field not in other:
                    not_checked.append(table+'.'+field)
                    if table in required_owners:issues.append({'table':table,'field':field,'code':'LOADED_FIELD_MISSING'})
                    continue
                try:equal=value(field,row[field])==value(field,other[field])
                except (ValueError,ArithmeticError,TypeError):equal=False
                if equal:checked.append(table+'.'+field)
                else:issues.append({'table':table,'field':field,'code':'LOADED_FIELD_MISMATCH','expected':row[field],'actual':other[field]})
    return {'status':'FAIL' if issues else 'PARTIAL' if not_checked and checked else 'NOT_CHECKED' if not checked else 'PASS',
            'evidence_layer':'FREPPLE_PERSISTED_INPUT_READBACK','engine_memory_status':'NOT_CHECKED',
            'checked_fields':sorted(set(checked)),'not_checked':sorted(set(not_checked)),'issues':issues,
            'limits':['No solver-memory export is available in the legacy P3 loader. Database readback is not native-memory attestation.',
                      'Only listed explicit fields and natural identities are compared; generated database IDs are not business identities.']}
