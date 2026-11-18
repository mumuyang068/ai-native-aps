"""Bounded fixed upstream / continuous downstream over the existing WIP model."""
from collections import Counter,defaultdict
from datetime import datetime,timedelta
from decimal import Decimal

PROFILE='mixed-fixed-batch-wip-v1'
def enabled(data):
    from mixed_batch_confirmed_supply import enabled as joint_enabled
    return data.get('rules',{}).get('manufacturing_profile')==PROFILE or joint_enabled(data)

def upstream(data):
    from finished_delivery import enabled as finished_enabled,upstream as finished_upstream
    if finished_enabled(data):return finished_upstream(data)
    wip=next(i['id'] for i in data['items'] if i['kind']=='SEMI')
    p=next(p for p in data['production_recipes'] if p['output_item_id']==wip)
    o=next(r for r in data['routing_revisions'] if r['id']==p['routing_revision_id'])['operations'][0]
    return p,o,next(r for r in data['resources'] if r['id']==o['resource_id'])

def validate(data):
    from two_level_wip import validate as validate_wip
    validate_wip(data,mixed=True)

def map_fixed_operation(data,native):
    """Keep both routing containers; only the upstream child has fixed time."""
    p,o,r=upstream(data)
    for row in native['operation']:
        if row['name']==p['id'] or row['name']==p['id']+':'+o['id']:
            row.update(sizeminimum=p['manufacturing_minimum_quantity'],sizemaximum=p['manufacturing_maximum_quantity'],sizemultiple=p['manufacturing_multiple'])
        if row['name']==p['id']+':'+o['id']:
            row.update(type='fixed_time',available=r['availability_calendar_id'])
            row.pop('duration_per')

def schedule_issues(data,operations,raw):
    """Check full upstream batches against true business windows, never 1s work."""
    from planning_adapter import decimal_text
    p,o,r=upstream(data)
    windows=next(c for c in data['calendars'] if c['id']==r['availability_calendar_id'])['available_intervals']
    used=defaultdict(Decimal);issues=[]
    def check(ok,code,ref):
        if not ok:issues.append({'code':code,'severity':'ERROR','entity_id':ref,'field_ref':None,'message':code,'evidence_ids':[]})
    for row in operations:
        if row['operation_id']!=o['id']:continue
        start,end=map(datetime.fromisoformat,[row['start_at'],row['end_at']]);qty=Decimal(row['quantity'])
        check(qty==Decimal(p['manufacturing_minimum_quantity']),'MIXED_CUT_FULL_BATCH',row['id'])
        matches=[i for i,w in enumerate(windows) if datetime.fromisoformat(w['start_at'])==start and datetime.fromisoformat(w['end_at'])==end]
        check(len(matches)==1 and (end-start).total_seconds()==Decimal(o['fixed_seconds']),'MIXED_CUT_FULL_WINDOW',row['id'])
        load=qty*Decimal(o['resource_units'])
        check(Decimal(row['resource_units'])==load,'MIXED_CUT_QUANTITY_LOAD',row['id'])
        if len(matches)==1:used[matches[0]]+=load
    for i,qty in used.items():check(qty<=Decimal(r['window_capacity_quantity']),'MIXED_CUT_WINDOW_CAPACITY',str(i))
    return issues

def audit_native(data,snap,phase,check,missing):
    """Finite in-memory batch sizes, actual bucket calendars and load events."""
    from planning_adapter import engine_time
    p,o,r=upstream(data);zone=data['site']['timezone'];key=p['id']+':'+o['id']
    ops={x['name']:x for x in snap['operations']}
    for name in [p['id'],key]:
        row=ops.get(name)
        if row is None:missing.append('native_'+phase+':'+name);continue
        for field,value in [('size_minimum',p['manufacturing_minimum_quantity']),('size_maximum',p['manufacturing_maximum_quantity']),('size_multiple',p['manufacturing_multiple'])]:
            if row.get(field) is None:missing.append('native_'+phase+':'+name+':'+field)
            else:check(Decimal(row[field])==Decimal(value),'MIXED_NATIVE_BATCH_SIZE',name+':'+field)
    native_r=next((x for x in snap['resources'] if x['name']==r['id']),None)
    if native_r is None:missing.append('native_'+phase+':bucket_resource');return
    cap='P3-CAPACITY:'+r['id']
    check(native_r['type']=='resource_buckets' and native_r['maximum_calendar']==cap,'MIXED_NATIVE_BUCKET_MODEL',phase)
    cal=next((x for x in snap['calendars'] if x['name']==cap),None)
    if cal is None:missing.append('native_'+phase+':quantity_calendar');return
    windows=next(c for c in data['calendars'] if c['id']==r['availability_calendar_id'])['available_intervals']
    expected=Counter((engine_time(w['start_at'],zone),engine_time((datetime.fromisoformat(w['start_at'])+timedelta(seconds=1)).isoformat(),zone),Decimal(r['window_capacity_quantity'])) for w in windows)
    actual=Counter((w['start'],w['end'],Decimal(w['value'])) for w in cal['buckets'])
    check(actual==expected and Decimal(cal['default'])==0,'MIXED_NATIVE_LEGAL_START_WINDOWS',phase)
    for mo in [x for x in snap['operationplans'] if x['operation']==key]:
        loads=[x for x in native_r['loadplans'] if x['reference']==mo['reference']]
        check(len(loads)==1 and loads[0]['at']==mo['start'] and abs(Decimal(loads[0]['quantity']))==Decimal(mo['quantity'])*Decimal(o['resource_units']),
              'MIXED_NATIVE_QUANTITY_LOAD_EVENT',mo['reference'])

def endpoint(data):
    return {'item_id':next(i['id'] for i in data['items'] if i['kind']=='FINISHED'),
        'scope':'SYNTHETIC_PROCESS_TEST_ENDPOINT','label':'合成缝后工序终点；阶段需求满足不代表成衣客户交付',
        'excluded':['garment_QC_and_packaging','enterprise_operators_skills_setup','existing_orders','purchasing','execution_checkpoint']}
