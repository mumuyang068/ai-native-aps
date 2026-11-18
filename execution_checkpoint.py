"""One completed, posted and released upstream batch -> existing WIP PlanInput.

This validates an explicitly confirmed fact prefix, not a live MES or inventory
ledger. Facts stay in the immutable data version; no historical MO enters solve.
"""
import copy,json,re
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
from jsonschema import Draft202012Validator,FormatChecker
from planning_adapter import InputError,SCHEMA,validate_input,decimal_text

PROFILE='whole-upstream-completion-v1'

def require(ok,reason):
    if not ok:raise InputError('INVALID_EXECUTION_CHECKPOINT: '+reason)

def exact(row,keys):require(isinstance(row,dict) and set(row)==set(keys),'missing or unsupported fields: '+','.join(keys))
def qty(v):
    require(isinstance(v,str) and re.fullmatch(r'(0|[1-9][0-9]{0,11})(\.[0-9]{1,8})?',v),'explicit nonnegative quantity required')
    return Decimal(v)
def instant(v):
    try:t=datetime.fromisoformat(v.replace('Z','+00:00'))
    except (ValueError,TypeError,AttributeError):raise InputError('INVALID_EXECUTION_CHECKPOINT: invalid date') from None
    require(t.tzinfo is not None and t.utcoffset() is not None,'timezone offset required')
    return t

def carry_forward(source,request):
    """Return a new canonical input and a bounded, reproducible historical proof."""
    validate_input(source)
    from two_level_wip import enabled
    require(enabled(source),'only two-level-wip-v1 can carry this execution prefix')
    exact(request,['checkpoint_id','timezone','cutoff_at','cutoff_sequence','confirmation','events'])
    require(isinstance(request['checkpoint_id'],str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}',request['checkpoint_id']),'stable checkpoint id required')
    require(request['timezone']==source['site']['timezone'],'checkpoint timezone must match business site')
    cutoff=instant(request['cutoff_at']);start=instant(source['planning_at'])
    require(start<cutoff<instant(source['horizon']['end_at']),'checkpoint must advance within the source horizon')
    confirm=request['confirmation'];exact(confirm,['reviewer','basis','declaration'])
    require(all(isinstance(v,str) and v.strip() and len(v)<=1000 for v in confirm.values()),'execution confirmation source required')
    require(confirm['declaration'] in {'SYNTHETIC_ACTUAL_EXECUTION','CONFIRMED_EXECUTION_RECORD'},'planned output is not execution evidence')
    events=request['events'];limit=request['cutoff_sequence']
    require(type(limit) is int and 1<=limit<=100 and isinstance(events,list) and 4<=len(events)<=100,'bounded event prefix required')
    ids=set();last_seq=0;included=[];excluded=[]
    for wrapper in events:
        exact(wrapper,['sequence','event']);seq=wrapper['sequence'];event=wrapper['event']
        require(type(seq) is int and seq>last_seq,'strict event sequence required');last_seq=seq
        exact(event,['event_type','source_system','external_event_id','source_version','occurred_at','entity_ref','payload'])
        for k in ['event_type','source_system','external_event_id','source_version','entity_ref']:
            require(isinstance(event[k],str) and event[k].strip() and len(event[k])<=200,'event identity/source missing')
        identity=event['external_event_id'];require(identity not in ids,'duplicate event id: '+identity);ids.add(identity)
        when=instant(event['occurred_at'])
        if seq<=limit:
            require(start<=when<=cutoff,'accepted event outside declared time boundary')
            included.append(wrapper)
        else:
            require(when>=cutoff,'known past event outside accepted sequence; closed prefix is not established')
            excluded.append(identity)
    require([e['sequence'] for e in included]==list(range(1,limit+1)),'accepted event range must be closed and contiguous from 1')
    require([e['event']['event_type'] for e in included[:4]]==['material_issued','operation_completed','receipt','wip_released'],
            'one complete issue -> completion -> posting -> explicit release required')
    require(all(e['event']['event_type']=='customer_shipped' for e in included[4:]),'unsupported execution lifecycle')
    accepted_ids={e['event']['external_event_id'] for e in included}
    require(not any(i['provenance']['source_record_id'] in accepted_ids for i in source['inventory_positions']),
            'accepted receipt is already represented in source opening')
    raw=next(i for i in source['items'] if i['kind']=='RAW');wip=next(i for i in source['items'] if i['kind']=='SEMI')
    fg=next(i for i in source['items'] if i['kind']=='FINISHED');site=source['site']['id']
    recipe=next(p for p in source['production_recipes'] if p['output_item_id']==wip['id'])
    op=next(r for r in source['routing_revisions'] if r['id']==recipe['routing_revision_id'])['operations'][0]
    stock={i['item_id']:copy.deepcopy(i) for i in source['inventory_positions']}
    balances={k:{f:qty(i[f]) for f in ['on_hand','quality_blocked','other_nonnettable']} for k,i in stock.items()}
    def summary():return [{ 'item_id':k,'location_id':site,'uom':stock[k]['uom'],
        **{f:decimal_text(v) for f,v in b.items()},'net_available':decimal_text(b['on_hand']-b['quality_blocked']-b['other_nonnettable'])} for k,b in balances.items()]
    opening=summary();ledger=[];shipments={d['id']:Decimal(0) for d in source['demand_lines']}
    task=included[0]['event']['entity_ref'];batch=None;issue_at=None;completed_at=None;receipt_id=None;previous=start
    for wrapped in included:
        event=wrapped['event'];kind=event['event_type'];p=event['payload'];when=instant(event['occurred_at'])
        require(when>=previous,'event timestamps conflict with sequence');previous=when
        if kind in {'operation_completed','receipt'}:
            validator=Draft202012Validator({'$defs':SCHEMA['$defs'],'$ref':'#/$defs/'+('CompletionEvent' if kind=='operation_completed' else 'ReceiptEvent')},format_checker=FormatChecker())
            require(not list(validator.iter_errors(event)),'existing completion/receipt contract rejected event')
        if kind=='material_issued':
            exact(p,['item_id','location_id','quantity','uom','recipe_id','operation_id','resource_id'])
            require((p['recipe_id'],p['operation_id'],p['resource_id'])==(recipe['id'],op['id'],op['resource_id']),'actual task recipe/operation/resource mismatch')
            require((p['item_id'],p['location_id'],p['uom'])==(raw['id'],site,raw['base_uom']),'actual RAW identity/unit mismatch')
            batch=qty(p['quantity']);require(batch>0 and batch==batch.to_integral_value(),'complete integer batch required')
            issue_at=when;balances[raw['id']]['on_hand']-=batch
        elif kind=='operation_completed':
            require(event['entity_ref']==task and p['operation_id']==op['id'] and p['uom']==wip['base_uom'] and qty(p['quantity_delta'])==batch,'completion does not match issued complete batch')
            completed_at=when
            require(Decimal(str((when-issue_at).total_seconds()))>=batch*Decimal(op['seconds_per_unit']),'actual processing duration too short')
            resource=next(r for r in source['resources'] if r['id']==op['resource_id'])
            cal=next(c for c in source['calendars'] if c['id']==resource['availability_calendar_id'])
            require(any(instant(i['start_at'])<=issue_at<when<=instant(i['end_at']) for i in cal['available_intervals']),'actual full batch outside one continuous work interval')
        elif kind in {'receipt','wip_released'}:
            if kind=='wip_released':exact(p,['item_id','location_id','quantity','uom','receipt_event_id'])
            require(event['entity_ref']==task and (p['item_id'],p['location_id'],p['uom'])==(wip['id'],site,wip['base_uom']) and qty(p['quantity'])==batch,'WIP posting/release differs from completed batch')
            if kind=='receipt':
                require(completed_at is not None and when>=completed_at,'posting before completion')
                receipt_id=event['external_event_id'];balances[wip['id']]['on_hand']+=batch;balances[wip['id']]['other_nonnettable']+=batch
            else:
                require(p['receipt_event_id']==receipt_id,'release does not bind actual receipt')
                balances[wip['id']]['other_nonnettable']-=batch
        else:
            exact(p,['order_line_id','item_id','location_id','quantity','uom'])
            demand=next((d for d in source['demand_lines'] if d['id']==p['order_line_id']),None)
            require(demand is not None and (p['item_id'],p['location_id'],p['uom'])==(fg['id'],site,fg['base_uom']),'shipment needs original demand-line and FG identity')
            quantity=qty(p['quantity']);require(quantity>0,'positive actual shipment required')
            shipments[demand['id']]+=quantity
            require(shipments[demand['id']]<=qty(demand['quantity']),'actual shipment exceeds original demand')
            balances[fg['id']]['on_hand']-=quantity
        require(all(b['on_hand']>=b['quality_blocked']+b['other_nonnettable'] and min(b.values())>=0 for b in balances.values()),'actual material ledger is negative or disagrees with opening')
        ledger.append({'sequence':wrapped['sequence'],'event_id':event['external_event_id'],'kind':kind,'at':event['occurred_at'],
                       'entity_ref':event['entity_ref'],'source_system':event['source_system'],'source_version':event['source_version'],'payload':copy.deepcopy(p),'balances':summary()})
    closing=summary();output=copy.deepcopy(source)
    output.update(planning_at=request['cutoff_at']);output['horizon']['start_at']=request['cutoff_at']
    # Existing PlanInput requires calendar intervals inside the new horizon.
    # Intersect the old intervals; preserve all remaining working availability.
    for cal in output['calendars']:
        cal['available_intervals']=[{**i,'start_at':max(instant(i['start_at']),cutoff).isoformat()}
            for i in cal['available_intervals'] if instant(i['end_at'])>cutoff]
    def provenance(identity):return {'source_system':'EXECUTION_CHECKPOINT','source_record_id':identity,'source_version':request['checkpoint_id'],
          'observed_at':request['cutoff_at'],'import_batch_id':request['checkpoint_id'],'source_locator':'checkpoint://'+request['checkpoint_id']+'/'+identity}
    for inv in output['inventory_positions']:
        inv.update({f:decimal_text(v) for f,v in balances[inv['item_id']].items()});inv.update(as_of=request['cutoff_at'],provenance=provenance(inv['id']))
    remaining=[];demands=[]
    for d in output['demand_lines']:
        total=qty(d['quantity']);left=total-shipments[d['id']]
        remaining.append({'order_line_id':d['id'],'order_ref':d['order_ref'],'original_quantity':decimal_text(total),'actually_delivered':decimal_text(shipments[d['id']]),'remaining_quantity':decimal_text(left),'uom':d['uom']})
        if left>0:d.update(quantity=decimal_text(left),provenance=provenance(d['id']));demands.append(d)
    require(demands,'no remaining demand; this bounded replanning endpoint does not create empty plans')
    output['demand_lines']=demands
    validate_input(output)
    proof={'profile':PROFILE,'checkpoint_id':request['checkpoint_id'],'cutoff_at':request['cutoff_at'],'timezone':request['timezone'],
       'cutoff_sequence':limit,'accepted_event_ids':[e['event']['external_event_id'] for e in included],
       'excluded_event_ids':excluded,'historical_status':'PASS','before':opening,'after':closing,'historical_ledger':ledger,'remaining_demands':remaining,
       'completed_task':{'reference':task,'recipe_id':recipe['id'],'operation_id':op['id'],'resource_id':op['resource_id'],
                         'quantity':decimal_text(batch),'uom':wip['base_uom'],'actual_start_at':issue_at.isoformat(),'actual_completed_at':completed_at.isoformat()},
       'confirmation':copy.deepcopy(confirm),'limits':['closed declared execution prefix, no live MES synchronization','initialization timestamp is not receipt date','no partial manufacturing or automatic release','historical events are not future engine flows']}
    return output,proof

def check_projection(data,record):
    try:
        expected,proof=carry_forward(record['source_input'],record['request'])
        require(proof==record['proof'],'checkpoint evidence/proof differs')
        actual=copy.deepcopy(data)
        for k in ['snapshot_id','scenario_id','scenario_revision']:actual[k]=expected[k]
        require(actual==expected,'next planning input differs from checkpoint balances/remaining demand')
    except (KeyError,TypeError,ValueError) as error:
        if isinstance(error,InputError):raise
        raise InputError('INVALID_EXECUTION_CHECKPOINT: checkpoint source evidence missing') from None
    return proof

def project_validation(data,record,future):
    if not isinstance(record,dict) or not all(k in record for k in ['source_input','request','proof']):
        return {'status':'FAIL' if future.get('status')=='FAIL' else 'NOT_CHECKED','historical_status':'NOT_CHECKED',
                'future_material_status':future.get('status','NOT_CHECKED'),'issues':[],
                'missing_evidence':['checkpoint_source_or_execution_prefix'],'summary':{},
                'not_checked':['historical_material_carryforward']}
    issues=[]
    try:proof=check_projection(data,record);historical='PASS'
    except InputError as error:historical='FAIL';issues.append(str(error));proof={}
    status='FAIL' if issues or future.get('status')=='FAIL' else 'PASS' if future.get('status')=='PASS' else 'NOT_CHECKED'
    return {'status':status,'historical_status':historical,'future_material_status':future.get('status','NOT_CHECKED'),
            'issues':issues,'summary':{**proof,'parent_plan_id':record.get('parent_plan_id'),'parent_data_version_id':record.get('parent_data_version_id')},
            'not_checked':['physical execution/source authenticity outside confirmed records','full MES lifecycle','general material balance beyond supported WIP model']}
