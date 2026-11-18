"""Validated canonical input, frePPLe 9.19 REST mapping and business comparison."""
import json
from order_evidence import project_order_evidence
import hashlib
import time
import csv
import shutil
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from jsonschema import Draft202012Validator, FormatChecker
import requests
from urllib.parse import quote

ROOT=Path(__file__).resolve().parent
ENGINE_EVIDENCE=Path(os.environ.get('PLANNING_ENGINE_EVIDENCE_DIR',str(ROOT/'evidence')))

def engine_config():
    return dict(line.split('=',1) for line in Path(os.environ.get('PLANNING_ENGINE_CONFIG_FILE',str(ROOT/'.env'))).read_text().splitlines() if '=' in line and not line.startswith('#'))
SCHEMA=json.loads((ROOT/'planning.schema.json').read_text(encoding='utf-8'))
INPUT_VALIDATOR=Draft202012Validator({'$defs':SCHEMA['$defs'],'$ref':'#/$defs/PlanInput'},format_checker=FormatChecker())

class InputError(ValueError):
    pass

def decimal_text(value):
    return format(Decimal(str(value)).normalize(),'f')

def engine_time(value, zone):
    # frePPLe is configured with local naive datetimes. Preserve the local clock.
    return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(ZoneInfo(zone)).replace(tzinfo=None).isoformat()

def duration(seconds):
    micros=int((Decimal(str(seconds))*1000000).to_integral_value(rounding='ROUND_CEILING'))
    days,micros=divmod(micros,86400*1000000)
    hours,micros=divmod(micros,3600*1000000)
    minutes,micros=divmod(micros,60*1000000)
    secs,frac=divmod(micros,1000000)
    text=f'{hours:02}:{minutes:02}:{secs:02}' + (f'.{frac:06}' if frac else '')
    return (f'{days} ' if days else '')+text

def validate_input(data):
    if 'foundation_projection' in data:
        if 'plan_editor_contract' in data or 'plan_editor_hypothesis' in data:raise InputError('UNSUPPORTED_PROFILE_CAPABILITY: plan_editor')
        from p22.foundation_projection import validate
        from p22.foundation_native import validate as validate_native
        validate(data);validate_native(data);return data
    from inventory_availability import validate as validate_quality,net_available
    errors=list(INPUT_VALIDATOR.iter_errors(data))
    if errors:raise InputError('INVALID_INPUT: '+str(errors[0].message))
    from plan_editor import validate_input as validate_editor
    try:validate_editor(data)
    except ValueError as exc:raise InputError(str(exc)) from exc
    from multi_receipt_supply import enabled as multi_receipt_enabled
    if not multi_receipt_enabled(data) and ('purchase_receipts' in data or any('operator_id' in c for m in data['existing_orders'] if m['type']=='MO' for c in m['operations'])):
        raise InputError('MULTI_RECEIPT_UNSUPPORTED_PROFILE: receipt facts and protected named-person commitments require the explicit profile')
    # Reject unsupported optional semantics before any profile-specific early return.
    from accessory_supply import enabled as accessory_enabled
    if "accessory_supply_option" in data and not accessory_enabled(data):
        raise InputError("UNSUPPORTED_ACCESSORY_SUPPLY_PROFILE")
    from routing_capacity import enabled as routing_enabled
    if not routing_enabled(data) and (any(r.get('role')=='MACHINE_POOL' or (r.get('role')=='MACHINE' and 'pool_id' in r) for r in data['resources']) or any('machine_skill_id' in o for rt in data['routing_revisions'] for o in rt['operations'])):
        raise InputError('UNSUPPORTED_ROUTING_CAPACITY_FIELDS: explicit routing capacity profile required')
    validate_quality(data)
    from inventory_policy import validate as validate_policy
    validate_policy(data)
    from inventory_decision import validate as validate_inventory_decision
    validate_inventory_decision(data)
    from demand_forecast import enabled as forecast_enabled,validate as validate_forecast
    validate_forecast(data)
    from network_distribution import enabled as network_enabled,validate as validate_network
    if not network_enabled(data) and 'distribution_lead_time_option' in data:raise InputError('UNSUPPORTED_NETWORK_DISTRIBUTION_PROFILE')
    if network_enabled(data):
        validate_network(data);return data
    from single_lane_distribution import enabled as distribution_enabled,validate as validate_distribution
    validate_distribution(data)
    if distribution_enabled(data):return data
    from qualified_operator import enabled as qualified_enabled,validate as validate_qualified
    validate_qualified(data)
    from finished_delivery import enabled as finished_enabled
    if not (finished_enabled(data) or forecast_enabled(data) or multi_receipt_enabled(data)) and any('customer_id' in d for d in data['demand_lines']):
        raise InputError('UNSUPPORTED_CUSTOMER_ID: canonical customer identity requires the explicit finished-delivery profile')
    from priority_supply import enabled as priority_enabled,TERM_FIELDS
    if not priority_enabled(data) and ('procurement_strategy' in data['rules'] or any(TERM_FIELDS & s.keys() for s in data['supply_sources'])):
        raise InputError('UNSUPPORTED_PRIORITY_SUPPLY: new source terms require the explicit priority profile')
    if data['reservations']:
        raise InputError('UNSUPPORTED_CONSTRAINT: hard reservations')
    from confirmed_supply import validate_receipts
    from fixed_batch import enabled
    if not enabled(data) and not multi_receipt_enabled(data):validate_receipts(data,allow_extra_qc=accessory_enabled(data))
    site=data['site']['id']
    try:ZoneInfo(data['site']['timezone'])
    except (ZoneInfoNotFoundError,ValueError) as exc:raise InputError('INVALID_INPUT: unknown site timezone') from exc
    groups={}
    for name in ['items','resources','calendars','bom_revisions','routing_revisions','production_recipes','suppliers','supply_sources','demand_lines']:
        groups[name]={row['id']:row for row in data[name]}
        if len(groups[name])!=len(data[name]):raise InputError('INVALID_INPUT: duplicate '+name)
    for resource in data['resources']:
        if resource['location_id']!=site or resource['availability_calendar_id'] not in groups['calendars']:
            raise InputError('INVALID_INPUT: resource location/calendar')
    # PlanResult 0.2 exposes bare operation IDs. Reject identities that cannot
    # round-trip that contract instead of silently merging recipe-scoped steps.
    operation_routes=defaultdict(set)
    for route in data['routing_revisions']:
        for op in route['operations']:
            if ':' in op['id']:
                raise InputError('OPERATION_ID_NOT_ROUNDTRIPPABLE: 工序 '+op['id']+'，工艺 '+route['id']+'；当前结果契约不支持工序编码含冒号，原编码未修改')
            operation_routes[op['id']].add(route['id'])
    for op_id,routes in operation_routes.items():
        if len(routes)>1:
            recipes=sorted(r['id'] for r in data['production_recipes'] if r['routing_revision_id'] in routes)
            raise InputError('DUPLICATE_OPERATION_ID: 工序 '+op_id+'；冲突工艺 '+', '.join(sorted(routes))+'；配方 '+', '.join(recipes)+'；原编码未修改')
    for route in data['routing_revisions']:
        done=set()
        for op in sorted(route['operations'],key=lambda row:row['sequence']):
            if op['id'] in done or set(op['predecessors'])!=({previous} if done else set()):
                raise InputError('INVALID_INPUT: P0 requires a sequential acyclic routing')
            if op['resource_id'] not in groups['resources']:raise InputError('INVALID_INPUT: unknown resource')
            done.add(op['id']);previous=op['id']
    planning=datetime.fromisoformat(data['planning_at'])
    for recipe in data['production_recipes']:
        if recipe['bom_revision_id'] not in groups['bom_revisions'] or recipe['routing_revision_id'] not in groups['routing_revisions']:
            raise InputError('INVALID_INPUT: missing recipe version')
        bom=groups['bom_revisions'][recipe['bom_revision_id']];route=groups['routing_revisions'][recipe['routing_revision_id']]
        if len({bom['output_item_id'],route['output_item_id'],recipe['output_item_id']})!=1 or recipe['location_id']!=site:
            raise InputError('INVALID_INPUT: recipe version/item mismatch')
        for revision in [recipe,bom,route]:
            if not(datetime.fromisoformat(revision['effective_from'])<=planning<datetime.fromisoformat(revision['effective_to'])):
                raise InputError('INVALID_INPUT: inactive revision')
        operations={op['id'] for op in route['operations']}
        for line in bom['lines']:
            if line['item_id'] not in groups['items'] or line['consume_operation_id'] not in operations:
                raise InputError('INVALID_INPUT: unknown BOM item/operation')
            if line['uom']!=groups['items'][line['item_id']]['base_uom'] or Decimal(line['scrap_fraction'])>=1:
                raise InputError('INVALID_INPUT: BOM units/scrap')
    for demand in data['demand_lines']:
        choices=[r for r in data['production_recipes'] if r['output_item_id']==demand['item_id'] and r['location_id']==demand['location_id']]
        if len(choices)!=1 or demand['item_id'] not in groups['items']:raise InputError('INVALID_INPUT: demand recipe')
        if demand['uom']!=groups['items'][demand['item_id']]['base_uom']:raise InputError('INVALID_INPUT: demand units')
        if Decimal(demand['minimum_shipment'])>Decimal(demand['quantity']):raise InputError('INVALID_INPUT: shipment quantity')
    for source in data['supply_sources']:
        if source['item_id'] not in groups['items'] or source['supplier_id'] not in groups['suppliers'] or source['location_id']!=site:
            raise InputError('INVALID_INPUT: supply source')
    for inv in data['inventory_positions']:
        if inv['item_id'] not in groups['items'] or inv['location_id']!=site:raise InputError('INVALID_INPUT: inventory reference')
        if inv['uom']!=groups['items'][inv['item_id']]['base_uom']:raise InputError('INVALID_INPUT: inventory units')
        if net_available(inv)<0:raise InputError('INVALID_INPUT: negative nettable inventory')
        if inv['physical_lot_ref'] is not None:raise InputError('UNSUPPORTED_CONSTRAINT: physical lot allocation')
        if inv['as_of']!=data['planning_at']:raise InputError('INVALID_INPUT: inventory as_of differs from planning_at')
    horizon_start=datetime.fromisoformat(data['horizon']['start_at']);horizon_end=datetime.fromisoformat(data['horizon']['end_at'])
    if horizon_start!=planning or horizon_start>=horizon_end:raise InputError('INVALID_INPUT: planning horizon')
    for calendar in data['calendars']:
        previous_end=None
        if calendar['timezone']!=data['site']['timezone']:raise InputError('INVALID_INPUT: mixed calendar timezones')
        for interval in sorted(calendar['available_intervals'],key=lambda x:x['start_at']):
            start=datetime.fromisoformat(interval['start_at']);end=datetime.fromisoformat(interval['end_at'])
            if not(horizon_start<=start<end<=horizon_end) or (previous_end and previous_end>start):raise InputError('INVALID_INPUT: calendar intervals')
            previous_end=end
    from two_level_wip import enabled as wip_enabled,validate as validate_wip
    from mixed_batch_wip import enabled as mixed_enabled,validate as validate_mixed
    from two_level_confirmed_supply import enabled as confirmed_wip_enabled,validate as validate_confirmed_wip
    from mixed_batch_confirmed_supply import enabled as joint_mixed_enabled,validate as validate_joint_mixed
    if multi_receipt_enabled(data):pass # finite receipt/MO shape validated by qualified profile dispatch
    elif forecast_enabled(data):pass # explicit forecast scope validated above
    elif finished_enabled(data):pass # independently validated by qualified_operator's explicit profile dispatch
    elif joint_mixed_enabled(data):validate_joint_mixed(data)
    elif confirmed_wip_enabled(data):validate_confirmed_wip(data)
    elif mixed_enabled(data):validate_mixed(data)
    elif wip_enabled(data):validate_wip(data)
    elif enabled(data):
        from fixed_batch import validate
        validate(data)
    elif qualified_enabled(data):pass
    elif any(o.get('duration_model')=='FIXED_BATCH' for r in data['routing_revisions'] for o in r['operations']) or any(r.get('capacity_model')=='WINDOW_QUANTITY' or any(k in r for k in ['window_capacity_quantity','capacity_uom','maxearly_seconds']) for r in data['resources']) or any('manufacturing_maximum_quantity' in r for r in data['production_recipes']) or any(k in data['rules'] for k in ['move_approved_early','erase_previous_proposed','fix_broken_supply_path']):
        raise InputError('UNSUPPORTED_CONSTRAINT: fixed batch/window resources require the explicit manufacturing profile')
    return data

def to_native(data):
    from inventory_availability import net_available
    validate_input(data)
    if 'foundation_projection' in data:
        from p22.foundation_native import map_native
        return map_native(data)
    from network_distribution import enabled as network_enabled,native as network_native
    if network_enabled(data):return network_native(data)
    from single_lane_distribution import enabled as distribution_enabled,native as distribution_native
    if distribution_enabled(data):return distribution_native(data)
    from fixed_batch import enabled,native
    if enabled(data):return native(data)
    return _to_native_standard(data)

def _to_native_standard(data):
    """Shared mapping mechanics; callers must validate their explicit contract."""
    from inventory_availability import net_available
    zone=data['site']['timezone'];site=data['site']['id']
    native={'parameter':[{'name':'currentdate','value':engine_time(data['planning_at'],zone).replace('T',' ')}],
        'location':[{'name':site}], 'customer':[{'name':'CUSTOMER-DEMO'}], 'item':[{'name':x['id']} for x in data['items']],
        'calendar':[{'name':x['id'],'defaultvalue':0} for x in data['calendars']],
        'calendarbucket':[], 'resource':[], 'operation':[], 'operationresource':[],
        'operationmaterial':[], 'buffer':[], 'supplier':[{'name':x['id']} for x in data['suppliers']],
        'itemsupplier':[], 'demand':[]}
    for calendar in data['calendars']:
        for interval in calendar['available_intervals']:
            native['calendarbucket'].append({'id':len(native['calendarbucket'])+1,'calendar':calendar['id'],
                'startdate':engine_time(interval['start_at'],zone),'enddate':engine_time(interval['end_at'],zone),
                'value':'1','priority':0,'monday':True,'tuesday':True,'wednesday':True,'thursday':True,'friday':True,
                'saturday':True,'sunday':True,'starttime':'00:00:00','endtime':'23:59:59'})
    from mixed_batch_wip import enabled as mixed_enabled,map_fixed_operation
    from finished_delivery import enabled as finished_enabled
    for resource in data['resources']:
        if (mixed_enabled(data) or finished_enabled(data)) and resource.get('capacity_model')=='WINDOW_QUANTITY':
            from fixed_batch import add_window_resource
            add_window_resource(data,native,resource,legal_start_only=True)
            continue
        native['resource'].append({'name':resource['id'],'location':site,'type':'default','maximum':resource['parallel_units'],
            'available':resource['availability_calendar_id'],'constrained':True})
    boms={x['id']:x for x in data['bom_revisions']};routes={x['id']:x for x in data['routing_revisions']}
    for recipe in data['production_recipes']:
        native['operation'].append({'name':recipe['id'],'type':'routing','item':recipe['output_item_id'],'location':site,
            'sizeminimum':recipe['manufacturing_minimum_quantity'],'sizemultiple':recipe['manufacturing_multiple'],
            **({'sizemaximum':recipe['manufacturing_maximum_quantity']} if recipe.get('manufacturing_maximum_quantity') is not None else {}),
            'effective_start':engine_time(recipe['effective_from'],zone),'effective_end':engine_time(recipe['effective_to'],zone)})
        route=routes[recipe['routing_revision_id']];bom=boms[recipe['bom_revision_id']]
        for op in sorted(route['operations'],key=lambda x:x['sequence']):
            name=recipe['id']+':'+op['id']
            native['operation'].append({'name':name,'type':'time_per','item':None,'owner':recipe['id'],'location':site,
                'priority':op['sequence'],'duration':duration(op['fixed_seconds']),'duration_per':duration(op['seconds_per_unit'])})
            if op.get('duration_model')!='AVAILABILITY_TRANSFER':
                native['operationresource'].append({'id':len(native['operationresource'])+1,'operation':name,
                    'resource':op['resource_id'],'quantity':op['resource_units']})
        for line in bom['lines']:
            quantity=Decimal(line['quantity_per_output'])/Decimal(bom['output_quantity'])/(1-Decimal(line['scrap_fraction']))
            native['operationmaterial'].append({'id':len(native['operationmaterial'])+1,'operation':recipe['id']+':'+line['consume_operation_id'],
                'item':line['item_id'],'quantity':decimal_text(-quantity),'type':line['consume_at'].lower()})
        last=max(route['operations'],key=lambda x:x['sequence'])
        native['operationmaterial'].append({'id':len(native['operationmaterial'])+1,'operation':recipe['id']+':'+last['id'],
            'item':recipe['output_item_id'],'quantity':'1','type':'end'})
    inventory=defaultdict(Decimal)
    for inv in data['inventory_positions']:
        inventory[(inv['item_id'],inv['location_id'])]+=net_available(inv)
    for index,((item,location),quantity) in enumerate(inventory.items(),1):
        native['buffer'].append({'id':index,'item':item,'location':location,'batch':'','onhand':decimal_text(quantity)})
    for demand in data['demand_lines']:
        native['demand'].append({'name':demand['id'],'item':demand['item_id'],'location':site,'quantity':demand['quantity'],
            'customer':'CUSTOMER-DEMO','due':engine_time(demand['due_at'],zone),'status':'open','priority':demand['priority'],
            'minshipment':demand['minimum_shipment'],'maxlateness':duration(demand['maximum_lateness_seconds'])})
    from confirmed_supply import add_native_supply
    add_native_supply(data,native)
    if mixed_enabled(data) or finished_enabled(data):map_fixed_operation(data,native)
    from two_level_wip import material_enabled as wip_enabled
    from demand_forecast import enabled as forecast_enabled
    if wip_enabled(data) or forecast_enabled(data):native['parameter'].extend([{'name':'plan.move_approved_early','value':'0'},
        {'name':'plan.fixBrokenSupplyPath','value':'false'}])
    from qualified_operator import enabled as qualified_enabled,map_native
    if qualified_enabled(data):native=map_native(data,native)
    from demand_forecast import enabled as forecast_enabled
    if finished_enabled(data) or forecast_enabled(data):
        native['customer']=[{'name':c} for c in sorted({d['customer_id'] for d in data['demand_lines']})]
        customers={d['id']:d['customer_id'] for d in data['demand_lines']}
        for demand in native['demand']:demand['customer']=customers[demand['name']]
    from accessory_supply import enabled as accessory_enabled, map_native as map_accessory
    if accessory_enabled(data):map_accessory(data,native)
    from inventory_decision import map_native as map_inventory_decision
    map_inventory_decision(data,native)
    from multi_receipt_supply import enabled as multi_receipt_enabled, map_native as map_multi_receipt
    if multi_receipt_enabled(data):map_multi_receipt(data,native)
    from plan_editor import map_native as editor_native
    return editor_native(data,native)

def compare_results(base, candidate):
    for field in ['snapshot_id','engine_profile','planning_at']:
        if base.get(field)!=candidate.get(field):raise InputError('BASELINE_MISMATCH: '+field)
    groups=[]
    def purchase_groups(result):
        found=defaultdict(lambda:{'quantity':Decimal(0),'dates':[]})
        for row in result.get('purchase_suggestions',[]):
            key=(row['item_id'],row['location_id'],row['supplier_id'])
            found[key]['quantity']+=Decimal(row['quantity']);found[key]['dates'].append(row['receipt_at'])
        return found
    a=purchase_groups(base);b=purchase_groups(candidate)
    for key in sorted(set(a)|set(b)):
        left=a[key];right=b[key]
        groups.append({'item_id':key[0],'location_id':key[1],'supplier_id':key[2],
            'base_quantity':decimal_text(left['quantity']),'candidate_quantity':decimal_text(right['quantity']),
            'quantity_delta':decimal_text(right['quantity']-left['quantity']),
            'base_receipts':sorted(left['dates']),'candidate_receipts':sorted(right['dates']),
            'changed':left['quantity']!=right['quantity'] or sorted(left['dates'])!=sorted(right['dates'])})
    def schedule_groups(result,field,keys):
        found=defaultdict(lambda:{'quantity':Decimal(0),'windows':[]})
        units={m['id']:m['uom'] for m in result.get('manufacture_suggestions',[])+result.get('existing_manufacturing_orders',[])}
        for row in result.get(field,[]):
            if field=='operations':row={**row,'uom':units[row['manufacture_id']]}
            key=tuple(row[k] for k in keys)
            found[key]['quantity']+=Decimal(row['quantity'])
            found[key]['windows'].append((row['start_at'],row['end_at'],row['quantity']))
        return found
    def group_diff(field,keys):
        left=schedule_groups(base,field,keys);right=schedule_groups(candidate,field,keys);out=[]
        for key in sorted(set(left)|set(right)):
            a=left[key];b=right[key]
            out.append({**dict(zip(keys,key)),'base_quantity':decimal_text(a['quantity']),
                'candidate_quantity':decimal_text(b['quantity']),'quantity_delta':decimal_text(b['quantity']-a['quantity']),
                'base_windows':sorted(a['windows']),'candidate_windows':sorted(b['windows']),
                'changed':a['quantity']!=b['quantity'] or sorted(a['windows'])!=sorted(b['windows'])})
        return out
    left={row['demand_id']:row for row in base.get('deliveries',[])};right={row['demand_id']:row for row in candidate.get('deliveries',[])}
    deliveries=[]
    for demand in sorted(set(left)|set(right)):
        a=left.get(demand);b=right.get(demand)
        when_a=(a or {}).get('planned_delivery_at');when_b=(b or {}).get('planned_delivery_at')
        delta=int((datetime.fromisoformat(when_b)-datetime.fromisoformat(when_a)).total_seconds()) if when_a and when_b else None
        fields=['due_at','planned_delivery_at','planned_quantity','unfulfilled_quantity','status']
        deliveries.append({'demand_id':demand,'kind':'ADDED' if a is None else 'REMOVED' if b is None else 'EXISTING',
            'base':{k:a[k] for k in fields} if a else None,'candidate':{k:b[k] for k in fields} if b else None,
            'planned_delivery_shift_seconds':delta,'changed':a is None or b is None or any(a[k]!=b[k] for k in fields)})
    return {'purchase_groups':groups,'manufacture_groups':group_diff('manufacture_suggestions',['item_id','location_id','recipe_id']),
        'operation_groups':group_diff('operations',['operation_id','resource_id','uom']),'deliveries':deliveries,
        'base_run_id':base.get('solve_run_id'),'candidate_run_id':candidate.get('solve_run_id')}

def native_csv_batches(table,rows):
    """Keep optional resource-free MOs out of the required binding CSV schema."""
    import io
    groups=[('',rows)]
    if table=='manufacturingorder' and any(r.get('owner') for r in rows):
        groups=[('.roots',[r for r in rows if not r.get('owner') and not r.get('resources')]),
                ('.bound',[r for r in rows if not r.get('owner') and r.get('resources')]),
                ('.children',[r for r in rows if r.get('owner')])]
        groups=[(suffix,values) for suffix,values in groups if values]
    elif table=='manufacturingorder' and any(r.get('resources') for r in rows) and any(not r.get('resources') for r in rows):
        groups=[('.bound',[r for r in rows if r.get('resources')]),('.availability',[r for r in rows if not r.get('resources')])]
    batches=[]
    for suffix,values in groups:
        fields=list(dict.fromkeys(k for r in values for k in r))
        if table in {'buffer','calendarbucket','operationmaterial','operationresource','resourceskill','itemdistribution','itemsupplier','setuprule'}:fields=[k for k in fields if k!='id']
        out=io.StringIO(newline='');writer=csv.DictWriter(out,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(values)
        batches.append((suffix,out.getvalue()))
    return batches


class FreppleClient:
    """One task-owned, serial engine workspace accessed through native REST APIs."""
    def __init__(self, base_url='http://127.0.0.1:9194', timeout=120):
        values=engine_config()
        self.session=requests.Session()
        self.session.auth=(values['P0_ENGINE_USER'],values['P0_ENGINE_PASSWORD'])
        self.session.headers['Accept']='application/json'
        self.base_url=base_url.rstrip('/')
        self.timeout=timeout

    def request(self, method, path, **kwargs):
        response=self.session.request(method,self.base_url+path,timeout=20,**kwargs)
        if not response.ok:
            raise RuntimeError(f'ENGINE_HTTP_{response.status_code}: {method} {path}: {response.text[:1200]}')
        return response

    @staticmethod
    def rows(value):
        if isinstance(value,list):return value
        if isinstance(value,dict):
            for name in ['results','rows']:
                if name in value:return value[name]
        raise RuntimeError('Unexpected engine collection response')

    def delete_snapshot_rows(self,table,rows,key,audit):
        """Delete observed IDs only, retaining the upstream all-table safeguard."""
        path='/api/input/'+table+'/'
        pending=list(rows)
        if len(pending)>30 and all(',' not in str(r[key]) for r in pending):
            # Leave the final row for the detail endpoint: frePPLe deliberately
            # rejects a filtered bulk delete that matches its whole table.
            while len(pending)>1:
                batch=pending[:min(200,len(pending)-1)]
                selected={str(r[key]) for r in batch}
                params={key+'__in':','.join(sorted(selected,key=int if key=='id' else str))}
                preview=self.rows(self.request('GET',path,params=params).json())
                if {str(r[key]) for r in preview}!=selected or len(preview)!=len(selected):
                    raise InputError('SNAPSHOT_DELETE_FILTER_MISMATCH: '+table)
                self.request('DELETE',path,params=params)
                audit.append({'table':table,'method':'DELETE_SUBSET','key':key,'ids':sorted(selected,key=int if key=='id' else str),'full_table_guard_bypassed':False})
                pending=pending[len(batch):]
        for row in pending:
            self.request('DELETE',path+quote(str(row[key]),safe='')+'/')
            audit.append({'table':table,'action':'DELETE','key':row[key],'reason':'FULL_SNAPSHOT_EXPLICIT_ID'})

    def load_input(self, native, run_dir):
        """Use the upstream import task; avoid broken 9.19 AutoField bulk writes."""
        audit=[];filenames=[];csv_sha256={};buffer_expected=None;buffer_readback=None
        csv_dir=run_dir/'native-csv';csv_dir.mkdir(exist_ok=True)
        upload_dir=ENGINE_EVIDENCE/'engine-logs/data/default';upload_dir.mkdir(parents=True,exist_ok=True)
        # Full snapshots use a dedicated staging DB. Confirmed POs survive native
        # replanning, so remove the previous snapshot's POs and source QC rules
        # before loading either an old empty-supply plan or a new confirmed one.
        for table,key in ([('manufacturingorder','reference'),('purchaseorder','reference'),('distributionorder','reference'),('itemdistribution','id'),('itemsupplier','id')] if 'itemsupplier' in native else []):
            path='/api/input/'+table+'/'
            self.delete_snapshot_rows(table,self.rows(self.request('GET',path).json()),key,audit)
        # Like calendar buckets, link IDs are generated per snapshot. A reused
        # ID can otherwise update A->B while another old row already owns B.
        for table in ['operationmaterial','operationresource','resourceskill','setuprule']:
            if table not in native:continue
            path='/api/input/'+table+'/'
            self.delete_snapshot_rows(table,self.rows(self.request('GET',path).json()),'id',audit)
        # Bucket IDs are generated per snapshot, not persistent identities.
        # Replacing a complete staging model must remove all old buckets before
        # importing new natural keys; otherwise retained IDs can collide.
        if 'calendarbucket' in native:
            path='/api/input/calendarbucket/'
            self.delete_snapshot_rows('calendarbucket',self.rows(self.request('GET',path).json()),'id',audit)
            (run_dir/'calendar-replacement.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
        for table, rows in native.items():
            if table=='buffer':
                # Importing a complete snapshot updates matching natural keys.
                # Missing CSV columns would retain the previous run's policy.
                # Materialize frePPLe defaults only at the import boundary;
                # canonical input/native_preview and caller rows stay unchanged.
                materialized=[]
                for row in rows:
                    defaults={k:v for k,v in {'minimum':'0','maximum':'0',
                        'minimum_calendar':None,'maximum_calendar':None}.items() if k not in row}
                    materialized.append({**row,**defaults})
                    if defaults:
                        audit.append({'table':'buffer','action':'MATERIALIZE_IMPORT_DEFAULTS',
                            'natural_key':{'item':row['item'],'location':row['location'],'batch':row.get('batch') or ''},
                            'fields':defaults,'reason':'COMPLETE_SNAPSHOT_REPLACES_OMITTED_BUFFER_POLICY_WITH_FREPPLE_DEFAULTS',
                            'canonical_native_preview_modified':False})
                rows=materialized
                buffer_expected=materialized
            path='/api/'+('common' if table=='parameter' else 'input')+'/'+table+'/'
            key='reference' if table in {'purchaseorder','manufacturingorder'} else 'name' if table in {'parameter','location','customer','item','calendar','resource','operation','supplier','demand','skill','setupmatrix'} else 'id'
            existing=self.rows(self.request('GET',path).json())
            # Buffer IDs belong to the staging DB; the canonical input numbers
            # rows independently. Preserve/replace stock by its natural key.
            identity=(lambda row:(row['item'],row['location'],row.get('batch') or '')) if table=='buffer' else (lambda row:(row['setupmatrix'],row['priority'])) if table=='setuprule' else (lambda row:str(row[key]))
            wanted={identity(row) for row in rows}
            # Only this task-owned workspace is changed. Native detail DELETE
            # resolves normally; parameter defaults remain untouched.
            if table!='parameter':
                for old in existing:
                    if identity(old) not in wanted:
                        self.request('DELETE',path+quote(str(old[key]),safe='')+'/')
                        audit.append({'table':table,'action':'DELETE','key':old[key]})
            if not rows:continue
            for suffix,content in native_csv_batches(table,rows):
                filename=table+'.'+run_dir.name.lower()+suffix+'.csv'
                (csv_dir/filename).write_text(content,encoding='utf-8-sig',newline='')
                shutil.copyfile(csv_dir/filename,upload_dir/filename)
                filenames.append(filename)
                csv_sha256[filename]=hashlib.sha256((csv_dir/filename).read_bytes()).hexdigest()
        resource_bound_mos=[r for r in native.get('manufacturingorder',[]) if r.get('resources')]
        if resource_bound_mos:
            # Upstream MO CSV resolves a selected resource through its nested-set
            # hierarchy. Newly imported resources have NULL lft/rght/lvl until
            # the official resource-detail query rebuilds this derived index.
            mo_files=[f for f in filenames if f.startswith('manufacturingorder.')]
            task=self.run_task('importfromfolder',{'files':','.join(f for f in filenames if f not in mo_files)},run_dir/'raw','import-model')
            # Reimport can retain an indexed CUT root while a replaced setup
            # matrix recreates only its SEW leaves. The upstream view rebuilds
            # only when the specifically requested resource lacks lft/rght.
            # Resolve each selected machine, not just the first protected MO.
            hierarchy_reads=[]
            for selected in dict.fromkeys(pair[0] for row in resource_bound_mos for pair in row['resources']):
                response=self.request('GET','/data/input/operationplanresource/resource/'+quote(selected,safe='')+'/',params={'format':'json','pagesize':1})
                hierarchy=response.json();self.rows(hierarchy)
                hierarchy_reads.append({'resource':selected,'response':hierarchy})
                audit.append({'action':'UPSTREAM_RESOURCE_HIERARCHY_INITIALIZATION','resource':selected,'mechanism':'ResourceDetail.basequeryset -> Resource.rebuildHierarchy','business_resource_binding_removed':False})
            (run_dir/'raw/resource-hierarchy-read.json').write_text(json.dumps(hierarchy_reads,ensure_ascii=False,indent=2),encoding='utf-8')
            roots=[f for f in mo_files if '.roots.csv' in f]
            if roots:
                # The upstream FK form requires the actual routing root before
                # its owned work orders. Neither creates extra root supply.
                self.run_task('importfromfolder',{'files':','.join(roots)},run_dir/'raw','import-manufacturing-roots')
            task=self.run_task('importfromfolder',{'files':','.join(f for f in mo_files if f not in roots)},run_dir/'raw','import-manufacturing')
        else:task=self.run_task('importfromfolder',{'files':','.join(filenames)},run_dir/'raw','import')
        readback_tables=list(native)
        if 'manufacturingorder' in native and 'workorder' not in readback_tables:
            # Upstream imports owned manufacturing rows as type WO. Its MO
            # and WO REST Owners expose separate real collections; retain both
            # responses instead of treating omitted children as absent imports.
            readback_tables.append('workorder')
        for table in readback_tables:
            path='/api/'+('common' if table=='parameter' else 'input')+'/'+table+'/'
            value=self.request('GET',path).json()
            (run_dir/'raw'/('imported-'+table+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
            if table=='buffer':
                buffer_readback=self._buffer_policy_import_readback(buffer_expected,value)
        audit.append({'action':'OFFICIAL_CSV_IMPORT','engine_task_ref':task,'files':filenames,
            'csv_sha256':csv_sha256,'buffer_default_scope':'ONLY_OMITTED_MINIMUM_MAXIMUM_AND_POLICY_CALENDARS',
            'canonical_native_preview_modified':False})
        if buffer_readback is not None:audit.append(buffer_readback)
        (run_dir/'import-receipts.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
        if buffer_readback is not None and buffer_readback['status']!='PASS':
            raise InputError('BUFFER_POLICY_IMPORT_'+buffer_readback['status']+': '+json.dumps(buffer_readback,ensure_ascii=False))

    @staticmethod
    def _buffer_policy_import_readback(expected, observed):
        """Only the four transmitted buffer policy fields, before runplan."""
        issues=[];missing=[];checked=[]
        fields=('minimum','maximum','minimum_calendar','maximum_calendar')
        try:actual=FreppleClient.rows(observed)
        except RuntimeError:actual=None
        if actual is None:missing.append('buffer_collection')
        else:
            key=lambda r:(r['item'],r['location'],r.get('batch') or '')
            if len(actual)!=len(expected):issues.append({'code':'BUFFER_IMPORT_ROW_COUNT'})
            for row in expected:
                identity=key(row)
                try:matches=[r for r in actual if key(r)==identity]
                except (KeyError,TypeError):missing.append('buffer_natural_key');continue
                if len(matches)!=1:
                    issues.append({'code':'BUFFER_IMPORT_NATURAL_KEY','key':identity});continue
                observed_row=matches[0]
                for field in fields:
                    if field not in observed_row:
                        missing.append(str(identity)+':'+field);continue
                    wanted,got=row[field],observed_row[field]
                    try:equal=(got==wanted) if field.endswith('_calendar') or wanted is None else Decimal(str(got))==Decimal(str(wanted))
                    except (ValueError,TypeError,ArithmeticError):equal=False
                    if not equal:issues.append({'code':'BUFFER_IMPORT_POLICY_FIELD','key':identity,'field':field,'expected':wanted,'actual':got})
                    else:checked.append({'key':identity,'field':field,'expected':wanted,'actual':got})
        return {'action':'BUFFER_POLICY_IMPORT_READBACK','status':'FAIL' if issues else 'NOT_CHECKED' if missing else 'PASS',
            'scope':'ACTUAL_CSV_MINIMUM_MAXIMUM_AND_POLICY_CALENDARS_ONLY','issues':issues,'missing_evidence':missing,
            'checked':checked,'canonical_native_preview_modified':False}

    def run_task(self, command, arguments, raw_dir, label):
        raw_dir=Path(raw_dir)
        def uncertain(reason,task_ref=None):
            record={'command':command,'task_ref':task_ref,'reason':str(reason),'recorded_at':datetime.now(timezone.utc).isoformat()}
            (raw_dir/(label+'-uncertain.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
            raise RuntimeError('RECOVERY_REQUIRED: '+command+' task outcome unknown; inspect '+label+'-uncertain.json') from None
        try:submitted=self.request('POST','/execute/api/'+command+'/',data=arguments).json()
        except Exception as exc:
            if str(exc).startswith('ENGINE_HTTP_4'):raise
            uncertain(type(exc).__name__+': '+str(exc))
        (raw_dir/(label+'-submit.json')).write_text(json.dumps(submitted,indent=2),encoding='utf-8')
        task_ref=submitted.get('taskid') or submitted.get('id') or submitted.get('task_id')
        if not task_ref:uncertain('ENGINE_TASK_ID_MISSING')
        deadline=time.monotonic()+self.timeout;polls=[];status=''
        while time.monotonic()<deadline:
            try:value=self.request('GET','/execute/api/status/',params={'id':task_ref}).json()
            except Exception as exc:uncertain(type(exc).__name__+': '+str(exc),task_ref)
            polls.append(value)
            (raw_dir/(label+'-polls.json')).write_text(json.dumps(polls,indent=2),encoding='utf-8')
            records=value if isinstance(value,list) else list(value.values()) if isinstance(value,dict) and 'status' not in value else [value]
            record=next((x for x in records if isinstance(x,dict) and str(x.get('id',task_ref))==str(task_ref)),None)
            status=(record or {}).get('status','')
            if status in {'Done','Failed','Canceled','Cancelled'}:break
            time.sleep(.25)
        else:
            try:self.request('GET','/execute/api/cancel/',params={'id':task_ref})
            except Exception as exc:uncertain('Cancellation not confirmed: '+type(exc).__name__,task_ref)
            uncertain('ENGINE_TIMED_OUT; cancel requested, terminal status not confirmed',task_ref)
        log=self.request('GET','/execute/api/log/',params={'id':task_ref}).text
        (raw_dir/(label+'.log')).write_text(log,encoding='utf-8')
        if status!='Done':raise RuntimeError('ENGINE_TASK_'+status+': '+log[-1500:])
        return str(task_ref)

    def solve(self, data, run_id, run_dir, context=None):
        if context is None:native=to_native(data)
        else:
            from p22.contracts import enterprise_native
            native=enterprise_native(data,context)
        run_dir=Path(run_dir);run_dir.mkdir(parents=True,exist_ok=True)
        raw_dir=run_dir/'raw';raw_dir.mkdir(exist_ok=True)
        from fixed_batch import enabled,protected_expectations
        fixed=enabled(data)
        from multi_receipt_supply import enabled as multi_receipt_enabled, protected_expectations as multi_receipt_expectations
        from two_level_wip import material_enabled as wip_enabled
        from qualified_operator import enabled as qualified_enabled
        from single_lane_distribution import enabled as distribution_enabled
        from network_distribution import enabled as network_enabled
        network=network_enabled(data)
        foundation='foundation_projection' in data
        observed=bool(data.get('plan_editor_contract')) or foundation or fixed or wip_enabled(data) or qualified_enabled(data) or distribution_enabled(data) or network
        if observed:
            request={'run_id':run_id,'input_hash':hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest(),
                     'protected':protected_expectations(data) if fixed else multi_receipt_expectations(data) if multi_receipt_enabled(data) else []}
            if data.get('plan_editor_contract'):request['editor']={'contract':data['plan_editor_contract'],'hypotheses':data.get('plan_editor_hypothesis',[]),'machine_ids':[r['id'] for r in data['resources'] if r.get('role')=='MACHINE']}
            if foundation and data['foundation_projection'].get('editor_scope'):
                request['editor']={'mode':'foundation','timezone':data['site']['timezone'],'horizon_end_at':data['horizon']['end_at'],'hypotheses':data['foundation_projection'].get('plan_editor_overlay',{}).get('events',[]),'machine_ids':[r['id'] for r in data['resources'] if r.get('role')=='MACHINE']}
            if foundation:
                request['qualified_operator']=True;request['two_level_wip']=True;request['two_level_confirmed_supply']=True
                request['foundation']={'sources':[{**s,'effective_from_native':engine_time(s['effective_from'],data['site']['timezone']),'effective_to_native':engine_time(s['effective_to'],data['site']['timezone'])} for s in data['supply_sources']]}
                from p22.intelligence_bridge import native_material_coordination
                material_coordination=native_material_coordination(data)
                if material_coordination is not None:request['foundation']['material_repair']=material_coordination
                source_operations={o['id']:o for o in data['foundation_projection']['source_tables']['operations']}
                request['foundation']['operation_bindings']={name:{
                    'resources':source_operations[identity['operation_id']]['resource_ids'],
                    'setup':data['foundation_projection']['operation_setups'].get(identity['operation_id'])}
                    for name,identity in data['foundation_projection']['native_operation_map'].items()}
                from interop.manufacturing import protected_commitments
                names={v['operation_id']:k for k,v in data['foundation_projection']['native_operation_map'].items()}
                request['protected']=[row for m in data['existing_orders'] if m['type']=='MO'
                    for row in protected_commitments(m,data['site']['timezone'],names)]
                request['foundation']['protected_roots']=[dict(reference=m['id'],operation=m['recipe_id'],quantity=m['quantity'],
                    status=m['status'],start=engine_time(m['start_at'],data['site']['timezone']),end=engine_time(m['end_at'],data['site']['timezone']))
                    for m in data['existing_orders'] if m['type']=='MO' and len(m['operations'])>1]
            if multi_receipt_enabled(data):request['multi_receipt_supply']=True
            if qualified_enabled(data):request['qualified_operator']=True
            if distribution_enabled(data) or network or (foundation and data.get('distribution_lanes')):request['single_lane_distribution']=True
            if wip_enabled(data) or multi_receipt_enabled(data):request['two_level_wip']=True
            from two_level_confirmed_supply import enabled as confirmed_wip_enabled
            from finished_delivery import enabled as finished_enabled
            if confirmed_wip_enabled(data) or finished_enabled(data) or multi_receipt_enabled(data) or network:request['two_level_confirmed_supply']=True
            from priority_supply import enabled as priority_enabled
            if priority_enabled(data):
                request['priority_supply']={'strategy':data['rules']['procurement_strategy'],'site':data['site']['id'],
                    'sources':[{**s,'effective_from_native':engine_time(s['effective_from'],data['site']['timezone'])} for s in data['supply_sources']]}
            probe_dir=ENGINE_EVIDENCE/'engine-logs/p3-fixed-batch';probe_dir.mkdir(parents=True,exist_ok=True)
            request_path=probe_dir/(run_id+'.request.json')
            request_path.write_text(json.dumps(request,ensure_ascii=False,indent=2),encoding='utf-8')
            native['parameter'].append({'name':'p3.fixed_batch.request','value':'/var/log/frepple/p3-fixed-batch/'+request_path.name})
            (run_dir/'ProtectedManufacturingInput-v1.json').write_text(json.dumps(request,ensure_ascii=False,indent=2),encoding='utf-8')
        (run_dir/'canonical_input.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        (run_dir/'native_input.json').write_text(json.dumps(native,ensure_ascii=False,indent=2),encoding='utf-8')
        self.load_input(native,run_dir)
        from calculation_validation import audit_loaded_input
        imported={p.stem.removeprefix('imported-'):json.loads(p.read_text(encoding='utf-8')) for p in raw_dir.glob('imported-*.json')}
        loaded=audit_loaded_input(native,imported)
        (run_dir/'InputReadback-v1.json').write_text(json.dumps(loaded,ensure_ascii=False,indent=2),encoding='utf-8')
        if loaded['status']=='FAIL':raise InputError('INPUT_READBACK_MISMATCH: '+json.dumps(loaded['issues'],ensure_ascii=False))
        try:task_ref=self.run_task('runplan',{'constraint':'capa,mfg_lt,po_lt','plantype':'1','env':'supply,p3_fixed_batch' if observed else 'supply'},raw_dir,'solve')
        finally:
            if observed:
                for phase in ['pre','post']:
                    for suffix in ['json','xml']:
                        source=probe_dir/(run_id+'.'+phase+'.'+suffix)
                        if source.is_file():shutil.copyfile(source,raw_dir/('fixed_batch_'+phase+'.'+suffix))
                source=probe_dir/(run_id+'.material-repair-failed.json')
                if source.is_file():shutil.copyfile(source,raw_dir/'material-repair-failed.json')
                source=probe_dir/(run_id+'.editor-failed.json')
                if source.is_file():shutil.copyfile(source,raw_dir/'editor-failed-native.json')
        raw={}
        for table in ['purchaseorder','manufacturingorder','workorder','demand','operationplanmaterial','operationplanresource','deliveryorder']+(['distributionorder'] if distribution_enabled(data) or network or (foundation and data.get('distribution_lanes')) else []):
            value=self.request('GET','/api/input/'+table+'/').json()
            (raw_dir/(table+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
            raw[table]=self.rows(value)
        for name in ['problem','constraint']:
            value=self.request('GET','/'+name+'/',params={'format':'json','pagesize':1000}).json()
            (raw_dir/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
            raw[name]=self.rows(value)
        if observed:
            for phase in ['pre','post']:
                p=raw_dir/('fixed_batch_'+phase+'.json')
                if p.is_file():
                    snap=json.loads(p.read_text(encoding='utf-8'))
                    if snap.get('run_id')!=run_id or snap.get('input_hash')!=request['input_hash']:
                        raise InputError('FIXED_BATCH_EVIDENCE_MISMATCH: native snapshot belongs to another input/run')
                    raw['fixed_batch_'+phase]=snap
                if foundation:
                    xml_path=raw_dir/('fixed_batch_'+phase+'.xml')
                    if xml_path.is_file():raw['fixed_batch_'+phase+'_xml']=xml_path.read_text(encoding='utf-8')
        result=normalize_result(data,run_id,task_ref,raw,run_dir)
        (run_dir/'PlanResult.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        projection=read_order_evidence(data,result,run_dir,{k:v for k,v in raw.items() if not k.endswith('_xml')})
        (run_dir/'OrderEvidence-v1.json').write_text(json.dumps(projection,ensure_ascii=False,indent=2),encoding='utf-8')
        return result

def read_order_evidence(data,result,run_dir,raw=None):
    """Companion output; do not rewrite historical PlanResult or its hash."""
    folder=Path(run_dir)
    if folder.name!=result['solve_run_id']:raise InputError('ORDER_EVIDENCE_RUN_MISMATCH')
    from order_evidence import TABLES
    if raw is None:
        raw={t:json.loads((folder/'raw'/(t+'.json')).read_text(encoding='utf-8')) for t in TABLES if (folder/'raw'/(t+'.json')).is_file()}
        capture=folder/'raw/fixed_batch_post.json'
        if capture.is_file():raw['fixed_batch_post']=json.loads(capture.read_text(encoding='utf-8'))
    hashes={name:hashlib.sha256((folder/'raw'/(name+'.json')).read_bytes()).hexdigest() for name in raw}
    return project_order_evidence(data,result,raw,hashes)

def normalize_result(data, run_id, task_ref, raw, run_dir):
    foundation='foundation_projection' in data
    from fixed_batch import enabled
    fixed=enabled(data)
    from multi_receipt_supply import enabled as multi_receipt_enabled
    multi_receipt=multi_receipt_enabled(data)
    zone=data['site']['timezone']
    def at(value):
        if not value:return None
        dt=datetime.fromisoformat(value.replace('Z','+00:00'))
        return (dt.replace(tzinfo=ZoneInfo(zone)) if dt.tzinfo is None else dt.astimezone(ZoneInfo(zone))).isoformat()
    def sid(kind,ref):return run_id+':'+kind+':'+str(ref)
    def evidence(table,ref):return ['ENGINE:'+table+':'+str(ref)]
    metadata=json.loads((ENGINE_EVIDENCE/'runtime.json').read_text(encoding='utf-8'))
    purchases=[{'id':sid('PO',row['reference']),'engine_ref':str(row['reference']),
        'item_id':row['item'],'location_id':row['location'],'supplier_id':row['supplier'],
        'quantity':decimal_text(row['quantity']),'uom':next(x['base_uom'] for x in data['items'] if x['id']==row['item']),
        'order_at':at(row['startdate']),'receipt_at':at(row['enddate']),
        'evidence_ids':evidence('purchaseorder',row['reference'])} for row in raw['purchaseorder'] if row['status']=='proposed']
    orders={str(x['reference']):x for x in raw['manufacturingorder']+raw['workorder']}
    order_tables={str(x['reference']):table for table in ('manufacturingorder','workorder') for x in raw[table]}
    roots=[x for x in orders.values() if x.get('owner') is None and x['status']=='proposed']
    recipes={x['id']:x for x in data['production_recipes']}
    manufacture=[{'id':sid('MO',row['reference']),'engine_ref':str(row['reference']),
        'item_id':recipes[row['operation']]['output_item_id'],'location_id':recipes[row['operation']]['location_id'],
        'recipe_id':row['operation'],'quantity':decimal_text(row['quantity']),'uom':next(x['base_uom'] for x in data['items'] if x['id']==recipes[row['operation']]['output_item_id']),
        'start_at':at(row['startdate']),'end_at':at(row['enddate']),
        'evidence_ids':evidence('manufacturingorder',row['reference'])} for row in roots]
    existing=[]
    if foundation or fixed or multi_receipt:
        for row in orders.values():
            if row.get('owner') is not None or row['status'] not in {'confirmed','approved'}:continue
            original=next((m for m in data['existing_orders'] if m['type']=='MO' and m['id']==str(row['reference'])),{})
            if (multi_receipt or foundation) and not original:continue # QC release is receipt availability, never manufacturing
            p=recipes[original['recipe_id'] if multi_receipt or foundation else row['operation']]
            existing.append({'id':sid('MO',row['reference']),'engine_ref':str(row['reference']),
              'item_id':p['output_item_id'],'location_id':p['location_id'],'recipe_id':p['id'],
              'quantity':decimal_text(row['quantity']),'uom':next(x['base_uom'] for x in data['items'] if x['id']==p['output_item_id']),
              'start_at':at(row['startdate']),'end_at':at(row['enddate']),'source_status':row['status'],
              'origin':'EXISTING','external_ref':original.get('external_ref',str(row['reference'])),
              'source_reference':str(row['reference']),'evidence_ids':evidence('manufacturingorder',row['reference'])})
        for m in manufacture:m.update(source_status='proposed',origin='PROPOSED')
    operations=[]
    for row in orders.values():
        if foundation:
            if row['operation'] not in data['foundation_projection']['native_operation_map']:continue
        elif row.get('owner') is None and not fixed and not (multi_receipt and any(m['type']=='MO' and m['id']==str(row['reference']) for m in data['existing_orders'])):continue
        resource_records=[r for r in raw['operationplanresource'] if str(r['operationplan'])==str(row['reference'])]
        from qualified_operator import enabled as qualified_enabled
        from routing_capacity import enabled as routing_enabled
        if foundation:
            resource_records=[r for r in resource_records if next((x for x in data['resources'] if x['id']==r['resource']),{}).get('role')=='MACHINE']
        elif qualified_enabled(data):
            primary=next(o for rt in data['routing_revisions'] for o in rt['operations'] if row['operation'].endswith(':'+o['id']))
            resource_records=[r for r in resource_records if (next((x for x in data['resources'] if x['id']==r['resource']),{}).get('role')=='MACHINE' if routing_enabled(data) else r['resource']==primary['resource_id'])]
        op_id=data['foundation_projection']['native_operation_map'][row['operation']]['operation_id'] if foundation else next(r['operations'][0]['id'] for r in data['routing_revisions'] if r['id']==recipes[row['operation']]['routing_revision_id']) if fixed else row['operation'].split(':')[-1]
        availability=foundation and any(o['id']==op_id and o.get('duration_model')=='AVAILABILITY_TRANSFER' for route in data['routing_revisions'] for o in route['operations'])
        if len(resource_records)!=(0 if availability else 1):raise RuntimeError('Native operation resource contract mismatch')
        load=dict(resource=None,quantity='0') if availability else resource_records[0]
        operations.append({'id':sid('OP',row['reference']),'manufacture_id':sid('MO',row['reference'] if fixed or row.get('owner') is None else row['owner']),
            'operation_id':op_id,'resource_id':load['resource'],
            **({'operation_kind':'AVAILABILITY_TRANSFER'} if availability else {}),
            'resource_units':decimal_text(load['quantity']),'quantity':decimal_text(row['quantity']),
            'start_at':at(row['startdate']),'end_at':at(row['enddate']),
            'evidence_ids':evidence(order_tables[str(row['reference'])] if foundation else
                'manufacturingorder' if multi_receipt and row.get('owner') is None else 'workorder',row['reference'])})
    demand_input={x['id']:x for x in data['demand_lines']}
    deliveries=[]
    for row in raw['demand']:
        request=demand_input[row['name']]
        from network_distribution import enabled as network_enabled
        if network_enabled(data) and row.get('plannedquantity') is None:raise InputError('NETWORK_DEMAND_PLANNED_QUANTITY_UNKNOWN: '+row['name'])
        planned=Decimal(str(row.get('plannedquantity') or 0));qty=Decimal(request['quantity'])
        when=at(row.get('deliverydate'));due=request['due_at']
        delay=max(0,int((datetime.fromisoformat(when)-datetime.fromisoformat(due)).total_seconds())) if when else None
        status='UNPLANNED' if planned==0 else 'SHORT' if planned<qty else 'LATE' if delay else 'ON_TIME'
        deliveries.append({'demand_id':row['name'],'requested_quantity':request['quantity'],
            'planned_quantity':decimal_text(planned),'unfulfilled_quantity':decimal_text(max(Decimal(0),qty-planned)),
            'uom':request['uom'],'due_at':due,'planned_delivery_at':when,'delay_seconds':delay,
            'status':status,'evidence_ids':evidence('demand',row['name'])})
    materials=[]
    for row in raw['operationplanmaterial']:
        qty=Decimal(str(row['quantity']))
        if not qty:continue
        materials.append({'id':sid('FLOW',row['id']),'order_id':str(row['operationplan']),'item_id':row['item'],
            'location_id':row['location'],'direction':'CONSUME' if qty<0 else 'PRODUCE',
            'quantity':decimal_text(abs(qty)),'uom':next(x['base_uom'] for x in data['items'] if x['id']==row['item']),
            'at':at(row['flowdate']),'evidence_ids':evidence('operationplanmaterial',row['id'])})
    operation_ids={o['id'] for o in operations}
    loads=[{'operation_id':sid('OP',row['operationplan']),'resource_id':row['resource'],
        'resource_units':decimal_text(row['quantity']),'start_at':at(row['startdate']),'end_at':at(row['enddate']),
        'evidence_ids':evidence('operationplanresource',row['id'])} for row in raw['operationplanresource']
        if not foundation or sid('OP',row['operationplan']) in operation_ids]
    ev=[]
    for table,records in raw.items():
        if not isinstance(records,list):continue
        for index,row in enumerate(records):
            key=row.get('reference',row.get('id',row.get('name',index)))
            ev.append({'id':'ENGINE:'+table+':'+str(key),'kind':'ENGINE_OUTPUT',
                'artifact_ref':str((Path(run_dir)/'raw'/(table+'.json')).relative_to(ENGINE_EVIDENCE.parent)), 'locator':'row:'+str(index)})
    issues=[]
    for row in raw['problem']:
        issues.append({'code':str(row.get('name','ENGINE_PROBLEM')),'severity':'WARNING','entity_id':str(row.get('owner','')) or None,
            'field_ref':None,'message':str(row.get('description',row)), 'evidence_ids':[]})
    for row in raw['constraint']:
        issues.append({'code':str(row.get('name','ENGINE_CONSTRAINT')).upper().replace(' ','_'),
            'severity':'WARNING','entity_id':row.get('demand'),'field_ref':None,
            'message':str(row.get('description',row)), 'evidence_ids':evidence('constraint',row['id'])})
    # Detailed independent constraint checks are added before acceptance is complete.
    constraint_issues=check_result_constraints(data,operations,raw,run_id=run_id)
    issues+=constraint_issues
    check='FAIL' if constraint_issues else 'PASS'
    assessment='BLOCKED' if check=='FAIL' else 'NEEDS_REVIEW' if issues or any(x['status']!='ON_TIME' for x in deliveries) else 'ACCEPTABLE'
    result={'schema_version':'0.2.0','solve_run_id':run_id,'scenario_id':data['scenario_id'],
        'scenario_revision':data['scenario_revision'],'snapshot_id':data['snapshot_id'],
        'input_hash':hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest(),
        'engine':{'name':'frepple','runtime_version':metadata['engine_reported_version'],
            'source_commit':'UNKNOWN','adapter_version':'p0.1', 'engine_task_ref':str(task_ref)},
        'computed_at':at(next(iter(json.loads((Path(run_dir)/'raw'/'solve-polls.json').read_text(encoding='utf-8'))[-1].values()))['finished']),
        'assessment':{'status':assessment,'constraint_check':check,
            'coverage':['reported_material_onhand_nonnegative','resource_capacity','calendar_duration','routing_order','procurement_lead_time'],'issues':issues},
        'purchase_suggestions':purchases,'manufacture_suggestions':manufacture,'operations':operations,
        'deliveries':deliveries,'material_flows':materials,'resource_loads':loads,
        'pegging':[],'pegging_available':False,'evidence':ev,
        'raw_artifact_refs':[str(p.relative_to(ENGINE_EVIDENCE.parent)) for p in sorted((Path(run_dir)/'raw').iterdir())]}
    if foundation:
        from p22.foundation_audit import audit
        report=audit(data,raw,run_id=run_id)
        result['existing_manufacturing_orders']=existing
        # Preserve the published PlanResult contract. The full, independent
        # report is a companion artifact; its real status still gates results.
        result['assessment']['constraint_check']=report['status']
        result['assessment']['status']='BLOCKED' if report['status']=='FAIL' else 'NEEDS_REVIEW' if report['status']!='PASS' or issues or any(x['status']!='ON_TIME' for x in deliveries) else 'ACCEPTABLE'
        result['assessment']['coverage']=['combined_factory_native_audit']
        if data.get('distribution_lanes'):
            from network_distribution import suggestions as joint_distribution_suggestions
            result['distribution_suggestions']=joint_distribution_suggestions(data,raw.get('distributionorder',[]),run_id)
        (Path(run_dir)/'FoundationNativeAudit-v1.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    if fixed or multi_receipt:
        result['existing_manufacturing_orders']=existing
        result['assessment']['coverage']=(['reported_material_onhand_nonnegative','bounded_multi_receipt_protected_mo_material_ledger','bounded_machine_qualified_operator_native_pre_post'] if multi_receipt else
            ['reported_material_onhand_nonnegative','fixed_batch_window_capacity','fixed_batch_duration','fixed_batch_protected_mo_native_pre_post'])
        if multi_receipt and check!='FAIL':
            from multi_receipt_supply import audit as multi_audit
            from qualified_operator import audit_resources
            if any(r['status']!='PASS' for r in [multi_audit(data,raw,run_id=run_id),audit_resources(data,raw,run_id=run_id)]):
                result['assessment'].update(constraint_check='NOT_CHECKED',status='NEEDS_REVIEW')
    from single_lane_distribution import enabled as distribution_enabled,suggestions,audit as distribution_audit
    from network_distribution import enabled as network_enabled
    if distribution_enabled(data) or network_enabled(data):
        if network_enabled(data):
            from network_distribution import suggestions,audit as distribution_audit
        report=distribution_audit(data,raw,run_id=run_id)
        result['distribution_suggestions']=suggestions(data,raw.get('distributionorder',[]),run_id)
        result['assessment']['coverage']=[report['scope']] if report['status']=='PASS' else []
        if report['status']=='NOT_CHECKED' and check!='FAIL':
            result['assessment'].update(constraint_check='NOT_CHECKED',status='NEEDS_REVIEW')
    if data.get('plan_editor_contract') or data.get('foundation_projection',{}).get('editor_scope'):
        from plan_editor import audit_native as editor_audit
        editor=editor_audit(data,raw,run_id=run_id)
        if editor['status']!='PASS':result['assessment'].update(constraint_check=editor['status'],status='BLOCKED' if editor['status']=='FAIL' else 'NEEDS_REVIEW')
        (Path(run_dir)/'PlanEditorNativeAudit-v1.json').write_text(json.dumps(editor,ensure_ascii=False,indent=2),encoding='utf8')
    result['result_hash']=hashlib.sha256(json.dumps(result,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    Draft202012Validator({'$defs':SCHEMA['$defs'],'$ref':'#/$defs/PlanResult'},format_checker=FormatChecker()).validate(result)
    from calculation_validation import engine_provenance
    (Path(run_dir)/'EngineProvenance-v1.json').write_text(json.dumps(engine_provenance(metadata),ensure_ascii=False,indent=2),encoding='utf-8')
    return result

def check_result_constraints(data, operations, raw,*,run_id=None):
    if 'foundation_projection' in data:
        from p22.foundation_audit import audit
        return [{'code':r['code'],'severity':'ERROR','entity_id':str(r.get('reference')) if r.get('reference') is not None else None,'field_ref':None,'message':r['code'],'evidence_ids':[]} for r in audit(data,raw,run_id=run_id)['issues']]
    from multi_receipt_supply import enabled as multi_receipt_enabled, audit as multi_receipt_audit
    if multi_receipt_enabled(data):
        from qualified_operator import audit_resources
        reports=[multi_receipt_audit(data,raw,run_id=run_id),audit_resources(data,raw,run_id=run_id)]
        return [{'code':r['code'],'severity':'ERROR','entity_id':str(r['reference']) if r.get('reference') is not None else None,
            'field_ref':None,'message':r['code'],'evidence_ids':[]} for report in reports for r in report['issues']]
    from single_lane_distribution import enabled as distribution_enabled,audit as distribution_audit
    from network_distribution import enabled as network_enabled
    if distribution_enabled(data) or network_enabled(data):
        if network_enabled(data):
            from network_distribution import audit as distribution_audit
        return [{'code':r['code'],'severity':'ERROR','entity_id':str(r['reference']) if r['reference'] is not None else None,
            'field_ref':None,'message':r['code'],'evidence_ids':[]} for r in distribution_audit(data,raw,run_id=run_id)['issues']]
    from fixed_batch import enabled,check_schedule
    if enabled(data):return check_schedule(data,operations,raw)
    from qualified_operator import enabled as qualified_enabled,audit_resources
    if qualified_enabled(data):
        report=audit_resources(data,raw,run_id=run_id)
        from plan_editor import audit_native as editor_audit
        report['issues']+=editor_audit(data,raw,run_id=run_id)['issues']
        issues=[{'code':r['code'],'severity':'ERROR','entity_id':r['reference'],'field_ref':None,'message':r['code'],'evidence_ids':[]} for r in report['issues']]
        from mixed_batch_wip import enabled as mixed_enabled,schedule_issues
        if mixed_enabled(data):issues.extend(schedule_issues(data,operations,raw))
        from finished_delivery import enabled as finished_enabled,schedule_issues as finished_schedule_issues
        if finished_enabled(data):issues.extend(finished_schedule_issues(data,operations,raw))
        issues.extend({'code':'NEGATIVE_STOCK','severity':'ERROR','entity_id':None,'field_ref':None,'message':'Negative material stock','evidence_ids':[]} for r in raw['operationplanmaterial'] if Decimal(str(r['onhand']))<Decimal('-0.00000001'))
        return issues
    issues=[]
    def issue(code,message):issues.append({'code':code,'severity':'ERROR','entity_id':None,'field_ref':None,'message':message,'evidence_ids':[]})
    calendars={x['id']:x for x in data['calendars']};resources={x['id']:x for x in data['resources']}
    specs={op['id']:op for r in data['routing_revisions'] for op in r['operations']}
    for operation in operations:
        start=datetime.fromisoformat(operation['start_at']);end=datetime.fromisoformat(operation['end_at'])
        if start<datetime.fromisoformat(data['planning_at']) or end>datetime.fromisoformat(data['horizon']['end_at']):issue('HORIZON','Operation outside planning horizon')
        available=calendars[resources[operation['resource_id']]['availability_calendar_id']]['available_intervals']
        active=sum(max(0,(min(end,datetime.fromisoformat(i['end_at']))-max(start,datetime.fromisoformat(i['start_at']))).total_seconds()) for i in available)
        spec=specs[operation['operation_id']]
        expected=Decimal(spec['fixed_seconds'])+Decimal(spec['seconds_per_unit'])*Decimal(operation['quantity'])
        if data.get('plan_editor_contract',{}).get('setup'):
            rows=[p for p in raw.get('fixed_batch_post',{}).get('operationplans',[]) if str(p['reference'])==operation['id'].rsplit(':',1)[-1]]
            if len(rows)==1 and 'setup_duration_seconds' in rows[0]:expected+=Decimal(rows[0]['setup_duration_seconds'])
        if abs(Decimal(str(active))-expected)>1:issue('CALENDAR_DURATION',f"{operation['id']}: active={active} expected={expected}")
    grouped=defaultdict(list)
    for operation in operations:grouped[operation['manufacture_id']].append(operation)
    for rows in grouped.values():
        by={x['operation_id']:x for x in rows}
        for row in rows:
            for predecessor in specs[row['operation_id']]['predecessors']:
                if predecessor not in by or datetime.fromisoformat(by[predecessor]['end_at'])>datetime.fromisoformat(row['start_at']):issue('ROUTING_ORDER','Predecessor not finished before next operation')
    from mixed_batch_wip import enabled as mixed_enabled,schedule_issues
    if mixed_enabled(data):issues.extend(schedule_issues(data,operations,raw))
    for resource in resources.values():
        if mixed_enabled(data) and resource.get('capacity_model')=='WINDOW_QUANTITY':continue
        events=[]
        for row in operations:
            if row['resource_id']==resource['id']:
                events.extend([(datetime.fromisoformat(row['start_at']),1,Decimal(row['resource_units'])),(datetime.fromisoformat(row['end_at']),0,-Decimal(row['resource_units']))])
        load=Decimal(0)
        for _,_,delta in sorted(events):
            load+=delta
            if load>Decimal(resource['parallel_units']):issue('RESOURCE_CAPACITY','Concurrent resource load exceeds maximum')
    for row in raw['operationplanmaterial']:
        if Decimal(str(row['onhand']))<Decimal('-0.00000001'):issue('NEGATIVE_STOCK','Engine material onhand is negative')
    for row in raw['purchaseorder']:
        source=next((x for x in data['supply_sources'] if x['item_id']==row['item'] and x['supplier_id']==row['supplier']),None)
        if source is None:
            issue('UNKNOWN_SUPPLY_PATH','Purchase evidence has no matching input supply path');continue
        start=datetime.fromisoformat(row['startdate']);end=datetime.fromisoformat(row['enddate'])
        # Existing confirmed promises are fixed input facts, not new suggestions
        # that must be regenerated from the current supplier planning leadtime.
        if row['status']=='proposed' and (end-start).total_seconds()<source['lead_time_seconds']:issue('PROCUREMENT_LEAD_TIME','PO lead time violated')
    if data.get('plan_editor_contract') or data.get('foundation_projection',{}).get('editor_scope'):
        from plan_editor import audit_native as editor_audit
        issues.extend(dict(code=r['code'],severity='ERROR',entity_id=r.get('reference'),field_ref=None,message=r['code'],evidence_ids=[]) for r in editor_audit(data,raw,run_id=run_id)['issues'])
    return issues
