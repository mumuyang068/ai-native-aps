"""Explicit two-source PRIORITY extension of the bounded D11 model.

F04/F-Integrate supply-path, bucket and QC mappings, through the original
PlanningService/Worker. No source selection or alternate solver in Python.
"""
from collections import Counter,defaultdict
from datetime import datetime,timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

PROFILE='fixed-batch-priority-supply-v1'
TERM_FIELDS={'priority','unit_price','currency','price_basis','capacity'}
INTENT='供应来源条件模拟'
PRICE_BASIS='MODEL_UNIT_PRICE_EXCLUDES_TAX_FREIGHT_HOLDING'
def enabled(data):return data.get('rules',{}).get('manufacturing_profile')==PROFILE
def capacity_id(s):return 'P3-SUPPLY-CAP:'+s['id']
def quantity(v):return Decimal(str(v))
def at(v,zone):
    t=datetime.fromisoformat(v.replace('Z','+00:00'))
    return t.replace(tzinfo=ZoneInfo(zone)) if t.tzinfo is None else t.astimezone(ZoneInfo(zone))
def validate(data):
    from planning_adapter import InputError
    def require(ok,why):
        if not ok:raise InputError('UNSUPPORTED_PRIORITY_SUPPLY: '+why)
    strategy=data['rules'].get('procurement_strategy')
    if strategy=='MINCOST' and any(s.get('receiving_qc',{}).get('wait_seconds',0)>0 for s in data['supply_sources']):
        raise InputError('MINCOST_HARD_QC_UNSUPPORTED_919: hard QC date semantics are not certified for MINCOST')
    require(strategy=='PRIORITY','only explicit PRIORITY supported')
    sources=data['supply_sources'];pos=[p for p in data['existing_orders'] if p['type']=='PO']
    require(len(sources)==len(data['suppliers'])==2 and len(pos)==1,'exactly two independent sources and one confirmed PO')
    require(len({s['supplier_id'] for s in sources})==2 and len({s['item_id'] for s in sources})==1,'one material and two distinct suppliers')
    require(len({s['priority'] for s in sources})==2,'explicit distinct priorities required')
    material=next(i for i in data['items'] if i['id']==sources[0]['item_id'])
    require(len(data['production_recipes'])==1,'this profile has one fixed-batch production recipe')
    recipe=data['production_recipes'][0]
    route=next(r for r in data['routing_revisions'] if r['id']==recipe['routing_revision_id'])
    duration=quantity(route['operations'][0]['fixed_seconds'])
    for resource in data['resources']:
        calendar=next(c for c in data['calendars'] if c['id']==resource['availability_calendar_id'])
        require(all((at(w['end_at'],data['site']['timezone'])-at(w['start_at'],data['site']['timezone'])).total_seconds()==duration for w in calendar['available_intervals']),
                'each complete batch equals one declared production window; wider/mixed windows are not certified')
    for s in sources:
        require(TERM_FIELDS<=s.keys() and 'receiving_qc' in s,'complete evidenced source terms required')
        c=s['capacity']
        require(c['scope']=='WHOLE_HORIZON_RECEIPTS' and at(c['start_at'],data['site']['timezone'])==at(data['planning_at'],data['site']['timezone'])
            and at(c['end_at'],data['site']['timezone'])==at(data['horizon']['end_at'],data['site']['timezone']),'capacity is total receipts over the complete planning period')
        require(c['uom']==material['base_uom'] and quantity(c['quantity'])>0,'explicit capacity in material base units')
        require(s['currency']=='CNY' and s['price_basis']==PRICE_BASIS,'explicit comparable CNY model prices only')
        require(s['batch_window_seconds']==0,'no supply batching window aggregation')
        require(capacity_id(s) not in {r['id'] for r in data['resources']},'capacity resource identity collision')
        require(all(isinstance(x,str) and x.strip() for p in [s['provenance'],c['provenance'],s['receiving_qc']['provenance']] for x in p.values()),'source, capacity and QC provenance required')
        require(at(s['effective_from'],data['site']['timezone'])<=at(data['planning_at'],data['site']['timezone']) and
            at(s['effective_to'],data['site']['timezone'])>=at(data['horizon']['end_at'],data['site']['timezone']),'no partial supply validity')
    from confirmed_supply import validate_receipts
    validate_receipts({**data,'existing_orders':pos},allow_extra_qc=True)

def accounting_start(data,source):
    # One bucket only. Include a pre-T0 load for the confirmed PO whose receipt
    # belongs to this business period. No allowance is copied or replenished.
    return min([data['planning_at']]+[p.get('planned_start_at') or p['ordered_at'] for p in data['existing_orders']
        if p['type']=='PO' and p['supply_source_id']==source['id']],key=lambda x:at(x,data['site']['timezone']))

def add_native(data,n):
    from planning_adapter import engine_time,duration
    zone=data['site']['timezone']
    # F04's verified legal-start quantity bucket: one full-hour batch must
    # start at the beginning of the full-hour working window. Keep the actual
    # operation availability calendar unchanged. The 1s bucket is an encoding
    # of the legal start, not a one-second business processing window.
    for row in n['calendarbucket']:
        if row['calendar'].startswith('P3-CAPACITY:'):
            row['enddate']=(datetime.fromisoformat(row['startdate'])+timedelta(seconds=1)).isoformat()
    for s in data['supply_sources']:
        row=next(r for r in n['itemsupplier'] if (r['item'],r['supplier'],r['location'])==(s['item_id'],s['supplier_id'],s['location_id']))
        row.update(priority=s['priority'],cost=s['unit_price'],resource=capacity_id(s),resource_qty='1',
            hard_safety_leadtime=duration(s['receiving_qc']['wait_seconds']),extra_safety_leadtime=duration(0),fence=duration(0),
            effective_start=engine_time(s['effective_from'],zone),effective_end=engine_time(s['effective_to'],zone))
        cal=capacity_id(s)+':TOTAL'
        n['calendar'].append({'name':cal,'defaultvalue':'0'})
        n['calendarbucket'].append({'id':len(n['calendarbucket'])+1,'calendar':cal,
            'startdate':engine_time(accounting_start(data,s),zone),'enddate':engine_time(s['capacity']['end_at'],zone),
            'value':s['capacity']['quantity'],'priority':1,
            **{d:True for d in ['monday','tuesday','wednesday','thursday','friday','saturday','sunday']},'starttime':'00:00:00','endtime':'23:59:59'})
        n['resource'].append({'name':capacity_id(s),'type':'buckets','maximum_calendar':cal,'maxearly':duration(2592000),'constrained':True})

def simulate(data,source_id,capacity_quantity=None,minimum_order_quantity=None):
    from drafts import DraftError
    if not enabled(data):raise DraftError('UNSUPPORTED_PRIORITY_SUPPLY')
    found=[s for s in data['supply_sources'] if s['id']==source_id]
    if len(found)!=1:raise DraftError('UNKNOWN_SUPPLY_SOURCE')
    if capacity_quantity is None and minimum_order_quantity is None:raise DraftError('EMPTY_SOURCE_CHANGE')
    s=found[0];before={'capacity_quantity':s['capacity']['quantity'],'minimum_order_quantity':s['minimum_order_quantity']}
    if capacity_quantity is not None:s['capacity']['quantity']=capacity_quantity
    if minimum_order_quantity is not None:s['minimum_order_quantity']=minimum_order_quantity
    return {'intent':INTENT,'source_id':source_id,'before':before,
        'after':{'capacity_quantity':s['capacity']['quantity'],'minimum_order_quantity':s['minimum_order_quantity']},
        'simulation_only':True,'business_permission':'NOT_CONFIRMED','execution_authorized':False}

def verify_draft(baseline,draft):
    import copy
    from drafts import DraftError
    if draft.get('changes',{}).get('intent')!=INTENT:return
    try:
        expected=copy.deepcopy(baseline);c=draft['changes'];after=c['after']
        actual=simulate(expected,c['source_id'],after['capacity_quantity'],after['minimum_order_quantity'])
        expected.update(scenario_id=draft['id'],scenario_revision=1)
        if c!=actual or draft['input']!=expected:raise ValueError('unrelated fact changed')
        from planning_adapter import validate_input
        validate_input(expected)
    except (KeyError,ValueError,TypeError) as exc:raise DraftError('PRIORITY_SIMULATION_SCOPE_MISMATCH') from exc

def audit(data,raw):
    """Bounded independent source, quantity, timing, whole-period load and value checks."""
    from calculation_validation import evidence_rows
    from planning_adapter import engine_time,decimal_text
    result={'status':'NOT_CHECKED','scope':'two_source_priority_receipts_capacity','issues':[],
        'not_checked':['actual_supplier_commitments','actual_QC_release','full_general_material_balance','global_optimality','tax_freight_holding']}
    required=['purchaseorder','operationplanmaterial','fixed_batch_pre','fixed_batch_post']
    missing=[n for n in required if n not in raw or (not n.startswith('fixed_batch_') and evidence_rows(raw[n]) is None)]
    if missing:return {**result,'missing_evidence':missing}
    issues=result['issues'];zone=data['site']['timezone'];pos=evidence_rows(raw['purchaseorder']);flows=evidence_rows(raw['operationplanmaterial'])
    def check(ok,code,reference=None):
        if not ok:issues.append({'code':code,'reference':reference})
    sources={(s['item_id'],s['supplier_id'],s['location_id']):s for s in data['supply_sources']}
    confirmed={p['id']:p for p in data['existing_orders'] if p['type']=='PO'}
    expected_loads=defaultdict(dict);used=defaultdict(Decimal);purchase_rows=[];incremental=Decimal(0);committed=Decimal(0)
    missing=[]
    try:
        check(len({str(p['reference']) for p in pos})==len(pos),'DUPLICATE_PO')
        for p in pos:
            ref=str(p['reference']);s=sources.get((p['item'],p['supplier'],p['location']))
            check(s is not None,'UNKNOWN_PURCHASE_SOURCE',ref)
            if s is None:continue
            qty=quantity(p['quantity']);start=at(p['startdate'],zone);end=at(p['enddate'],zone)
            check(qty>0,'NONPOSITIVE_PO',ref)
            check(at(data['planning_at'],zone)<=end<=at(data['horizon']['end_at'],zone),'PO_OUTSIDE_BUSINESS_CAPACITY_PERIOD',ref)
            available=end+timedelta(seconds=s['receiving_qc']['wait_seconds'])
            fs=[f for f in flows if str(f['operationplan'])==ref and f['item']==s['item_id'] and f['location']==s['location_id'] and quantity(f['quantity'])>0]
            check(len(fs)==1 and quantity(fs[0]['quantity'])==qty and at(fs[0]['flowdate'],zone)==available,'PO_QC_AVAILABLE_EVENT',ref)
            if ref in confirmed:
                c=confirmed[ref]
                check(p['status']=='confirmed' and c['supply_source_id']==s['id'] and qty==quantity(c['remaining_quantity']) and end==at(c['expected_receipt_at'],zone),'CONFIRMED_SOURCE_OR_QUANTITY_DRIFT',ref)
            else:
                check(p['status']=='proposed','UNEXPECTED_PURCHASE_STATE',ref)
                check(start>=at(data['planning_at'],zone) and (end-start).total_seconds()>=s['lead_time_seconds'],'PROPOSED_LEADTIME',ref)
                check(qty>=quantity(s['minimum_order_quantity']) and qty%quantity(s['order_multiple'])==0,'PROPOSED_MOQ_OR_MULTIPLE',ref)
            check(at(s['effective_from'],zone)<=start<=end<=at(s['effective_to'],zone),'PURCHASE_SOURCE_VALIDITY',ref)
            value=qty*quantity(s['unit_price'])
            if p['status']=='proposed':incremental+=value
            else:committed+=value
            used[s['id']]+=qty;expected_loads[s['id']][ref]=(qty,start)
            purchase_rows.append({'reference':ref,'source_id':s['id'],'supplier_id':s['supplier_id'],'status':p['status'],
                'quantity':decimal_text(qty),'uom':s['capacity']['uom'],'unit_price':s['unit_price'],'currency':s['currency'],'model_value':decimal_text(value),
                'physical_receipt_at':end.isoformat(),'available_at':available.isoformat(),
                'source_provenance':s['provenance'],'evidence':{'file':'raw/purchaseorder.json','reference':ref}})
        for s in sources.values():
            check(used[s['id']]<=quantity(s['capacity']['quantity']),'SUPPLIER_TOTAL_CAPACITY',s['id'])
        for phase in ['fixed_batch_pre','fixed_batch_post']:
            snap=raw[phase];probe=snap.get('procurement')
            if not isinstance(probe,dict) or not isinstance(probe.get('paths'),list) or not isinstance(probe.get('alternate'),dict):
                missing.append(phase+':procurement');continue
            check(probe['alternate'].get('type')=='operation_alternate' and probe['alternate'].get('search')==0,'PRIORITY_NOT_LOADED',phase)
            for s in sources.values():
                paths=[r for r in probe['paths'] if r['source_id']==s['id']]
                check(len(paths)==1,'NATIVE_SOURCE_IDENTITY',s['id'])
                if len(paths)==1:
                    p=paths[0]
                    check(p['cost']==s['unit_price'] and p['lead_time_seconds']==s['lead_time_seconds'] and p['minimum']==s['minimum_order_quantity'] and p['multiple']==s['order_multiple']
                          and p['qc_seconds']==s['receiving_qc']['wait_seconds'] and p['priority']==s['priority'],'NATIVE_SOURCE_TERMS',s['id'])
                    check(p['capacity_resource']==capacity_id(s) and p['capacity_per_unit']=='1','NATIVE_SUPPLY_RESOURCE_LINK',s['id'])
                resource=next((r for r in snap['resources'] if r['name']==capacity_id(s)),None)
                check(resource is not None and resource.get('type')=='resource_buckets','SUPPLY_BUCKET_TYPE',s['id'])
                if resource:
                    check(all(quantity(l['quantity'])<0 for l in resource['loadplans']),'SUPPLIER_CAPACITY_LOAD_SIGN',s['id']+':'+phase)
                    actual={str(l['reference']):(abs(quantity(l['quantity'])),at(l['at'],zone)) for l in resource['loadplans']}
                    expected=expected_loads[s['id']] if phase=='fixed_batch_post' else {ref:v for ref,v in expected_loads[s['id']].items() if ref in confirmed}
                    check(actual==expected and len(actual)==len(resource['loadplans']),'CONFIRMED_AND_PROPOSED_CAPACITY_LOADS',s['id']+':'+phase)
                    cal=next((c for c in snap['calendars'] if c['name']==resource['maximum_calendar']),None)
                    buckets=cal.get('buckets',[]) if cal else []
                    check(len(buckets)==1 and quantity(cal['default'])==0,'SUPPLY_WHOLE_PERIOD_BUCKET',s['id'])
                    if len(buckets)==1:
                        b=buckets[0]
                        check(quantity(b['value'])==quantity(s['capacity']['quantity']) and at(b['start'],zone)==at(accounting_start(data,s),zone) and at(b['end'],zone)==at(s['capacity']['end_at'],zone)
                              and b.get('days')==127 and b.get('starttime')==0 and b.get('endtime')==86400,'SUPPLY_CAPACITY_MAPPING',s['id'])
    except (KeyError,ValueError,TypeError,ArithmeticError) as exc:
        return {**result,'status':'FAIL' if issues else 'NOT_CHECKED','reason':str(exc),'missing_evidence':missing}
    summary={'strategy':'PRIORITY','currency':'CNY','price_basis':PRICE_BASIS,'purchase_orders':purchase_rows,
        'incremental_purchase_spend':decimal_text(incremental),'confirmed_purchase_commitment_model_value':decimal_text(committed),
        'capacity':[{'source_id':s['id'],'quantity':s['capacity']['quantity'],'used':decimal_text(used[s['id']]),'uom':s['capacity']['uom'],
            'business_start_at':s['capacity']['start_at'],'end_at':s['capacity']['end_at'],'native_accounting_start_at':accounting_start(data,s),
            'priority':s['priority'],'minimum_order_quantity':s['minimum_order_quantity'],'order_multiple':s['order_multiple'],'qc_wait_seconds':s['receiving_qc']['wait_seconds']} for s in sources.values()],
        'limits':['source-model valuation, not actual payment or contract price','excludes tax freight holding','not MINCOST or global optimum']}
    return {**result,'status':'FAIL' if issues else 'NOT_CHECKED' if missing else 'PASS','missing_evidence':missing,'summary':summary}
