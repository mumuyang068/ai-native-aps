"""Confirmed-only RAW supply for the bounded continuous two-level WIP profile.

No supplier search, new purchase authorization or separate planning service.
"""
from datetime import datetime
from decimal import Decimal

PROFILE='two-level-confirmed-supply-v1'

def enabled(data):
    from mixed_batch_confirmed_supply import enabled as joint_enabled
    return data.get('rules',{}).get('manufacturing_profile')==PROFILE or joint_enabled(data)

def validate(data,*,mixed=False):
    from planning_adapter import InputError
    from two_level_wip import validate as validate_wip
    def require(ok,why):
        if not ok:raise InputError('UNSUPPORTED_TWO_LEVEL_CONFIRMED_SUPPLY: '+why)
    require(len(data['suppliers'])==len(data['supply_sources'])==len(data['existing_orders'])==1,
            'exactly one RAW source, supplier and full unreceived confirmed PO')
    validate_wip(data,confirmed=True,mixed=mixed)
    source=data['supply_sources'][0]
    raw=next(i for i in data['items'] if i['kind']=='RAW')
    require(source['item_id']==raw['id'],'only RAW may have a purchase path')
    require(datetime.fromisoformat(source['effective_from'])<=datetime.fromisoformat(data['planning_at'])
            and datetime.fromisoformat(source['effective_to'])>=datetime.fromisoformat(data['horizon']['end_at']),
            'source must cover the complete planning horizon')

def endpoint(data):
    item=next(i for i in data['items'] if i['kind']=='FINISHED')
    return {'item_id':item['id'],'label':'合成缝后阶段产出，不代表完整成衣交付'}

def supply_scope(data,raw):
    from calculation_validation import evidence_rows
    rows=evidence_rows(raw.get('purchaseorder'))
    if rows is None:return {'status':'NOT_CHECKED','issues':[],'missing_evidence':['purchaseorder'],'unvalidated_purchases':[]}
    original={p['id'] for p in data['existing_orders']};unvalidated=[];issues=[]
    for p in rows:
        if p.get('supplier')=='Unknown supplier':issues.append({'code':'WIP_UNDECLARED_REPLENISHMENT','reference':p.get('reference')})
        if str(p.get('reference')) not in original:
            if p.get('status')=='proposed':unvalidated.append({'reference':p.get('reference'),'reason':'JOINT_NEW_PURCHASE_NOT_VALIDATED'})
            else:issues.append({'code':'JOINT_UNDECLARED_CONFIRMED_SUPPLY','reference':p.get('reference')})
    return {'status':'FAIL' if issues else 'UNSUPPORTED' if unvalidated else 'PASS',
            'issues':issues,'missing_evidence':[],'unvalidated_purchases':unvalidated}

def audit_supply_model(data,snap,phase,check,missing):
    """Finite actual path properties plus native XML natural keys, never import markers."""
    from planning_adapter import engine_time
    source=data['supply_sources'][0];site=data['site']['id']
    expected_name='Purchase '+source['item_id']+' @ '+site+' from '+source['supplier_id']
    check(snap['wip_model']['purchase_paths']==[expected_name],'WIP_UNDECLARED_REPLENISHMENT',phase)
    native=snap.get('confirmed_supply_model')
    if not isinstance(native,dict):missing.append('native_'+phase+':confirmed_supply_model');return
    sources=native.get('itemsuppliers');paths=native.get('paths')
    if not isinstance(sources,list) or not isinstance(paths,list):missing.append('native_'+phase+':supply_paths');return
    check(len(sources)==1 and sources[0]=={k:source[k+'_id'] for k in ['item','supplier','location']},'JOINT_NATIVE_SUPPLY_NATURAL_KEY',phase)
    check(len(paths)==1 and [p.get('name') for p in paths]==[expected_name]
          and snap['wip_model']['purchase_paths']==[expected_name],'WIP_UNDECLARED_REPLENISHMENT',phase)
    if len(paths)==1:
        p=paths[0]
        for key,value in [('lead_time_seconds',source['lead_time_seconds']),('minimum',source['minimum_order_quantity']),('multiple',source['order_multiple'])]:
            if p.get(key) is None:missing.append('native_'+phase+':path:'+key)
            else:check(Decimal(str(p[key]))==Decimal(str(value)),'JOINT_NATIVE_SUPPLY_PARAMETER',phase+':'+key)
        flows=p.get('flows')
        if not isinstance(flows,list):missing.append('native_'+phase+':path:flows')
        else:check(len(flows)==1 and flows[0].get('item')==source['item_id']
                   and Decimal(str(flows[0]['quantity']))==1
                   and Decimal(str(flows[0]['offset_seconds']))==source['receiving_qc']['wait_seconds'],
                   'JOINT_NATIVE_QC_PATH',phase)
    calendars=snap.get('calendars')
    if not isinstance(calendars,list):missing.append('native_'+phase+':calendars');return
    from collections import Counter
    for calendar in data['calendars']:
        found=[c for c in calendars if c.get('name')==calendar['id']]
        if len(found)!=1:missing.append('native_'+phase+':calendar:'+calendar['id']);continue
        actual=found[0]
        expected=Counter((engine_time(i['start_at'],data['site']['timezone']),engine_time(i['end_at'],data['site']['timezone']),Decimal(1)) for i in calendar['available_intervals'])
        check(Decimal(actual['default'])==0 and Counter((b['start'],b['end'],Decimal(b['value'])) for b in actual['buckets'])==expected,
              'JOINT_NATIVE_AVAILABILITY_CALENDAR',calendar['id']+':'+phase)
