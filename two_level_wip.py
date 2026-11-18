"""Bounded independent-stock RAW -> WIP -> FG profile over existing P3 models.

No execution checkpoint, stock ledger, purchasing strategy or replacement solver.
"""
from collections import Counter,defaultdict
from datetime import datetime
from decimal import Decimal
import hashlib,json
from zoneinfo import ZoneInfo

PROFILE='two-level-wip-v1'
def enabled(data):return data.get('rules',{}).get('manufacturing_profile')==PROFILE

def material_enabled(data):
    from mixed_batch_wip import enabled as mixed_enabled
    from two_level_confirmed_supply import enabled as confirmed_enabled
    from finished_delivery import enabled as finished_enabled
    return enabled(data) or mixed_enabled(data) or confirmed_enabled(data) or finished_enabled(data)

def validate(data,*,mixed=False,confirmed=False):
    from planning_adapter import InputError
    def require(ok,why):
        if not ok:raise InputError('UNSUPPORTED_TWO_LEVEL_WIP: '+why)
    require((confirmed or not data['existing_orders']) and not data.get('previous_manufacturing_suggestions') and not data['reservations'],
            'opening balances only; no historical/existing MO, receipts, reservations or execution replay')
    require((confirmed or (not data['supply_sources'] and not data['suppliers'])) and 'procurement_strategy' not in data['rules'],
            'no purchasing/profile combination')
    kinds={kind:[i for i in data['items'] if i['kind']==kind] for kind in ['RAW','SEMI','FINISHED']}
    require(len(data['items'])==3 and all(len(v)==1 for v in kinds.values()),'exactly one RAW, one independent SEMI and one FINISHED item')
    raw,wip,fg=[kinds[k][0] for k in ['RAW','SEMI','FINISHED']]
    require(len(data['production_recipes'])==len(data['bom_revisions'])==len(data['routing_revisions'])==2,'exactly two independent recipes, BOMs and single-step routes')
    require({p['output_item_id'] for p in data['production_recipes']}=={wip['id'],fg['id']},'one upstream and one downstream recipe, no alternate')
    require(len({p['bom_revision_id'] for p in data['production_recipes']})==len({p['routing_revision_id'] for p in data['production_recipes']})==2,'independent BOM/route identities')
    require(len(data['inventory_positions'])==3 and Counter(i['item_id'] for i in data['inventory_positions'])==Counter(i['id'] for i in data['items']),
            'exactly one opening balance per item/location; no duplicate historical receipt')
    require(all(d['item_id']==fg['id'] for d in data['demand_lines']),'only final-product customer demand')
    horizon_end=datetime.fromisoformat(data['horizon']['end_at'])
    boms={b['id']:b for b in data['bom_revisions']};routes={r['id']:r for r in data['routing_revisions']}
    for recipe in data['production_recipes']:
        upstream=recipe['output_item_id']==wip['id']
        if mixed and upstream:
            require(Decimal(recipe['manufacturing_minimum_quantity'])==Decimal(recipe.get('manufacturing_maximum_quantity','0'))==Decimal(recipe['manufacturing_multiple'])>0,'one indivisible upstream batch')
        else:
            require('manufacturing_maximum_quantity' not in recipe,'no unimplemented maximum-batch mapping')
            require(Decimal(recipe['manufacturing_minimum_quantity'])==Decimal(recipe['manufacturing_multiple'])==1,'whole integer units with min/multiple1 only')
        bom=boms[recipe['bom_revision_id']];route=routes[recipe['routing_revision_id']]
        require(len(bom['lines'])==len(route['operations'])==1,'one material input and one operation per level')
        upstream=recipe['output_item_id']==wip['id'];line=bom['lines'][0];op=route['operations'][0]
        require(line['item_id']==(raw['id'] if upstream else wip['id']),'linear RAW -> WIP -> FG required')
        require(bom['output_uom']==(wip if upstream else fg)['base_uom'],'explicit output unit must match item')
        require(Decimal(bom['output_quantity'])==Decimal(line['quantity_per_output'])==1 and Decimal(line['scrap_fraction'])==0,'1:1 and zero loss only')
        require(line['consume_at']=='START' and line['consume_operation_id']==op['id'],'consume at the declared operation start')
        if mixed and upstream:
            require(op['duration_model']=='FIXED_BATCH' and Decimal(op['fixed_seconds'])>0 and Decimal(op['seconds_per_unit'])==0,'upstream fixed-time processing only')
        else:
            require(op['duration_model']=='FIXED_PLUS_PER_UNIT' and Decimal(op['fixed_seconds'])==0 and Decimal(op['seconds_per_unit'])>0,
                    'positive per-unit processing, no fixed-batch/window profile')
        require(all(datetime.fromisoformat(r['effective_to'])>=horizon_end for r in [recipe,bom,route]),'no partial validity')
    if mixed:
        from mixed_batch_wip import upstream
        p,op,res=upstream(data)
        require(len(data['resources'])==2 and len({o['resource_id'] for r in data['routing_revisions'] for o in r['operations']})==2,'two distinct role resources')
        require(res.get('capacity_model')=='WINDOW_QUANTITY' and 'parallel_units' not in res,'upstream quantity capacity, not parallel machines')
        require(res['capacity_uom']==wip['base_uom'] and Decimal(p['manufacturing_minimum_quantity'])*Decimal(op['resource_units'])==Decimal(res['window_capacity_quantity']),'one complete batch must equal the explicit WIP-unit window capacity')
        windows=next(c for c in data['calendars'] if c['id']==res['availability_calendar_id'])['available_intervals']
        require(all((datetime.fromisoformat(w['end_at'])-datetime.fromisoformat(w['start_at'])).total_seconds()==Decimal(op['fixed_seconds']) for w in windows),'one whole fixed batch equals a business window')
    else:require(all(r.get('capacity_model','CONTINUOUS')=='CONTINUOUS' for r in data['resources']), 'continuous capacity only')
    for r in data['resources']:
        if mixed and r['id']==res['id']:continue
        require(r.get('capacity_model','CONTINUOUS')=='CONTINUOUS','downstream continuous resource required')
        require(not {'window_capacity_quantity','capacity_uom','maxearly_seconds'} & r.keys(),'no ignored quantity-window fields')
    require(data['rules'].get('move_approved_early')==0 and data['rules'].get('erase_previous_proposed') is True
            and data['rules'].get('fix_broken_supply_path') is False,'explicit existing-plan protection parameters required')

def audit(data,raw):
    """Reuse P3's BOM matcher, then certify opening stock and native time events.

    Inventory initialization is the same opening balance, never an extra receipt.
    All event quantities/times are checked against business BOM/schedules first.
    """
    from calculation_validation import routing_material_validation,evidence_rows
    from planning_adapter import decimal_text
    from mixed_batch_wip import enabled as mixed_enabled
    from two_level_confirmed_supply import enabled as confirmed_enabled,supply_scope,endpoint as confirmed_endpoint,audit_supply_model
    mixed=mixed_enabled(data)
    confirmed=confirmed_enabled(data)
    base=routing_material_validation(data,raw)
    result={**base,'scope':'two_level_wip_material_time_ledger',
        'not_checked':['actual_execution','historical_receipt_date','automatic_checkpoint_carryforward','full_general_material_balance'],
        'unsupported':['existing_MO_PO','lots_MTO','alternate_recipes','multi_site','procurement_strategy_combination'],
        'event_order':'native buffer chronological order, reconciled to independently derived BOM events; same-time production precedes dependent consumption'}
    issues=list(base.get('issues',[]));missing=list(base.get('missing_evidence',[]))
    scope=None;receipts=None
    if confirmed:
        from confirmed_supply import validate_native_receipts
        receipts=validate_native_receipts(data,raw);scope=supply_scope(data,raw)
        issues.extend(receipts['issues']+scope['issues']);missing.extend(receipts['missing_evidence']+scope['missing_evidence'])
        result.update(scope='two_level_confirmed_supply_material_time_ledger',
            unsupported=['existing_MO','multiple_or_partial_receipts','new_proposed_purchase_certification','lots_MTO','alternate_recipes','multi_site','procurement_strategy_combination'],
            confirmed_supply_validation=receipts,unvalidated_purchases=scope['unvalidated_purchases'])
    def check(ok,code,ref=None):
        if not ok:issues.append({'code':code,'reference':ref})
    def q(v):
        x=Decimal(str(v))
        if not x.is_finite():raise ValueError('nonfinite quantity')
        return x
    zone=ZoneInfo(data['site']['timezone'])
    def at(v):
        t=datetime.fromisoformat(v.replace('Z','+00:00'))
        return t.replace(tzinfo=zone) if t.tzinfo is None else t.astimezone(zone)
    start=at(data['planning_at']);site=data['site']['id'];stock={i['item_id']:i for i in data['inventory_positions']}
    initial={k:q(v['on_hand'])-q(v['quality_blocked'])-q(v['other_nonnettable']) for k,v in stock.items()}
    rows=evidence_rows(raw.get('operationplanmaterial'))
    if rows is not None:
        for f in rows:
            try:check(q(f['onhand'])>=-Decimal('0.000001'),'WIP_REPORTED_NEGATIVE',f.get('id'))
            except (KeyError,ValueError,ArithmeticError):missing.append('operationplanmaterial:balance')
    summaries=[];ledger=[]
    try:
        expected_hash=hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
        for phase in ['pre','post']:
            snap=raw.get('fixed_batch_'+phase)
            if not isinstance(snap,dict):missing.append('native_'+phase);continue
            check(snap['input_hash']==expected_hash and snap['phase']==phase,'WIP_NATIVE_INPUT_IDENTITY',phase)
            check(snap['move_approved_early']==0,'WIP_GLOBAL_PROTECTION',phase)
            if phase=='post' and isinstance(raw.get('fixed_batch_pre'),dict):
                check(snap['run_id']==raw['fixed_batch_pre']['run_id'],'WIP_NATIVE_RUN_IDENTITY')
            model=snap.get('wip_model')
            if not isinstance(model,dict):missing.append('native_'+phase+':wip_model');continue
            check(model['fix_broken_supply_path']=='false' and (confirmed or model['purchase_paths']==[]), 'WIP_UNDECLARED_REPLENISHMENT',phase)
            if confirmed:audit_supply_model(data,snap,phase,check,missing)
            ops={o['name']:o for o in model['operations']}
            for p in data['production_recipes']:
                route=next(r for r in data['routing_revisions'] if r['id']==p['routing_revision_id']);op=route['operations'][0]
                bom=next(b for b in data['bom_revisions'] if b['id']==p['bom_revision_id']);line=bom['lines'][0]
                parent=ops.get(p['id']);child=ops.get(p['id']+':'+op['id'])
                if parent is None or child is None:missing.append('native_'+phase+':operation:'+p['id']);continue
                check(parent['type']=='operation_routing' and not parent['flows'],'WIP_PARENT_DUPLICATE_OUTPUT',p['id'])
                fixed=mixed and op['duration_model']=='FIXED_BATCH'
                check((child['type']=='operation_fixed_time' and q(child['fixed_seconds'])==q(op['fixed_seconds']) if fixed else
                       child['type']=='operation_time_per' and q(child['fixed_seconds'])==0 and q(child['seconds_per_unit'])==q(op['seconds_per_unit'])),
                      'WIP_NATIVE_DURATION',p['id'])
                check(Counter((f['item'],q(f['quantity'])) for f in child['flows'])==Counter([(line['item_id'],Decimal(-1)),(p['output_item_id'],Decimal(1))]),
                      'WIP_NATIVE_BOM_FLOW',p['id'])
            for resource in data['resources']:
                if mixed and resource.get('capacity_model')=='WINDOW_QUANTITY':continue
                actual=next((r for r in model['resources'] if r['name']==resource['id']),None)
                if actual is None:missing.append('native_'+phase+':resource:'+resource['id']);continue
                from joint_qualified import enabled as qualified_joint
                expected_calendar=(next(r for r in data['resources'] if r.get('role')=='OPERATOR_POOL')['availability_calendar_id'] if qualified_joint(data) else resource['availability_calendar_id'])
                check(actual['type']=='resource_default' and q(actual['maximum'])==q(resource['parallel_units'])
                      and actual['available']==expected_calendar,'WIP_NATIVE_RESOURCE',resource['id'])
            if mixed:
                from mixed_batch_wip import audit_native
                audit_native(data,snap,phase,check,missing)
            for item,inv in stock.items():
                buffers=[b for b in snap['buffers'] if b['item']==item and b['location']==site]
                if len(buffers)!=1:missing.append('native_'+phase+':buffer:'+item);continue
                b=buffers[0]
                check(q(b['onhand'])==initial[item],'WIP_NET_OPENING_LOAD',item+':'+phase)
                if phase!='post':continue
                if rows is None:continue
                native=Counter((str(f['reference']),at(f['at']),q(f['quantity']),q(f['onhand'])) for f in b['events'])
                exported=Counter((str(f['operationplan']),at(f['flowdate']),q(f['quantity']),q(f['onhand'])) for f in rows if f['item']==item and f['location']==site)
                check(native==exported,'WIP_NATIVE_EXPORT_EVENTS',item)
                opening=sum((q(f['quantity']) for f in b['events'] if at(f['at'])<start),Decimal(0))
                check(opening==initial[item],'WIP_OPENING_NOT_A_SECOND_RECEIPT',item)
                balance=initial[item];produced=Decimal(0);consumed=Decimal(0);purchased=Decimal(0);last=start
                po_refs={str(p['reference']) for p in (evidence_rows(raw.get('purchaseorder')) or [])} if confirmed else set()
                for f in b['events']:
                    when=at(f['at']);qty=q(f['quantity'])
                    if when<start:continue
                    check(when>=last,'WIP_NATIVE_EVENT_ORDER',item);last=when
                    balance+=qty
                    check(balance>=-Decimal('0.000001'),'WIP_NEGATIVE_RECONSTRUCTED',item)
                    check(abs(balance-q(f['onhand']))<Decimal('0.000001'),'WIP_RUNNING_BALANCE',item)
                    purchase=qty>0 and str(f['reference']) in po_refs
                    if purchase:purchased+=qty
                    elif qty>0:produced+=qty
                    else:consumed-=qty
                    ledger.append({'item_id':item,'location_id':site,'reference':str(f['reference']),'at':when.isoformat(),
                        'quantity':decimal_text(qty),'net_available_balance':decimal_text(balance),'source':'raw/fixed_batch_post.json:/buffers',
                        **({'event_kind':'PURCHASE_RECEIPT' if purchase else 'MANUFACTURING_OUTPUT' if qty>0 else 'CONSUMPTION_OR_DELIVERY'} if confirmed else {})})
                summaries.append({'item_id':item,'kind':next(i['kind'] for i in data['items'] if i['id']==item),'location_id':site,'uom':inv['uom'],
                    'physical_opening':inv['on_hand'],'quality_blocked':inv['quality_blocked'],'other_nonnettable':inv['other_nonnettable'],
                    'net_available_opening':decimal_text(initial[item]),'future_production':decimal_text(produced),'future_consumption':decimal_text(consumed),
                     **({'future_purchase_receipts':decimal_text(purchased)} if confirmed else {}),
                    'ending_net_available':decimal_text(balance),'source':inv['provenance'],
                    'opening_as_of':inv['as_of'],'historical_receipt_date':'NOT_PROVIDED','native_initialization_is_receipt_date':False})
    except (KeyError,ValueError,TypeError,ArithmeticError) as exc:missing.append('native_evidence:'+str(exc))
    result.update(status='FAIL' if issues or base['status']=='FAIL' else 'NOT_CHECKED' if missing or base['status']!='PASS' else 'UNSUPPORTED' if scope and scope['status']=='UNSUPPORTED' else 'PASS',
        issues=issues,missing_evidence=sorted(set(missing)),summary={'profile':data['rules']['manufacturing_profile'],'inventory':summaries,'material_events':ledger,
        'limits':['opening balances already include history; no historical execution replay','quality holds excluded for the entire plan','initialization date is not business receipt date','no order-level allocation inferred']})
    if mixed:
        from mixed_batch_wip import endpoint
        result['summary']['stage_endpoint']=endpoint(data)
    if confirmed:
        result['summary']['stage_endpoint']=confirmed_endpoint(data)
        result['summary']['confirmed_purchases']=[{**p,'certification':'EXISTING_CONFIRMED_ONLY'} for p in data['existing_orders']]
        result['summary']['unvalidated_purchases']=scope['unvalidated_purchases']
    return result
