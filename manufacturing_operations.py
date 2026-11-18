"""Manufacturing work and a single sourced rolling prefix; no execution ledger.

Future operations remain run-local PlanResult records. Historical execution is
read only from the immutable D14 checkpoint record, never matched by task time.
"""
import copy
import hashlib
import json
from collections import defaultdict
from decimal import Decimal
from execution_checkpoint import carry_forward, check_projection
from planning_adapter import InputError


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def preview_checkpoint(data,request):
    # This product action retains D14's native-validated prefix, not its merely
    # defined nonzero customer shipment extension or a general execution editor.
    if len(request.get('events',[]))!=4 or request.get('cutoff_sequence')!=4:
        raise InputError('UNSUPPORTED_CHECKPOINT_PREFIX: only one complete upstream prefix; no customer shipment')
    _,proof=carry_forward(data,request)
    synthetic=proof['confirmation']['declaration']=='SYNTHETIC_ACTUAL_EXECUTION'
    return dict(proof=proof,request_sha256=digest(request),evidence_class='SYNTHETIC_RESEARCH_ASSUMPTION' if synthetic else 'CONFIRMED_EXECUTION_RECORD',
                calculation_submitted=False,source_authenticity='NOT_CHECKED',execution_permission_granted=False)


def project_work(data,result,record,identity):
    items={i['id']:i for i in data['items']}
    existing={r['id']:r for r in data.get('existing_orders',[]) if r.get('type')=='MO'}
    parents={r['id']:r for k in ['manufacture_suggestions','existing_manufacturing_orders'] for r in result.get(k,[])}
    tasks=[]
    for index,op in enumerate(result.get('operations',[])):
        parent=parents.get(op['manufacture_id'])
        if parent is None:raise InputError('INVALID_EVIDENCE: manufacturing parent is missing')
        er=parent.get('source_reference',parent.get('external_ref'))
        er=er if er in existing else None
        references=[e.split(':',2)[2] for e in op.get('evidence_ids',[]) if e.startswith(('ENGINE:workorder:','ENGINE:manufacturingorder:'))]
        leaf_reference=references[0] if len(references)==1 else None
        tasks.append({**copy.deepcopy(op),'item_id':parent['item_id'],'location_id':parent['location_id'],
            'uom':parent.get('uom',items[parent['item_id']]['base_uom']),
            'enterprise_reference':er,'native_reference':leaf_reference,'parent_native_reference':parent.get('engine_ref',er),
            'identity_scope':'ENTERPRISE' if er else 'RUN_LOCAL',
            'status':existing[er]['source_status'] if er else 'proposed',
            'resource_ids':list(dict.fromkeys([op['resource_id'],*op.get('additional_resource_ids',[])])),
            'actual_status':'NOT_COLLECTED','actual_quantity':None,'actual_start_at':None,
            'actual_completed_at':None,'remaining_execution_quantity':None,'completion_percent':None,
            'source':{'artifact':'PlanResult.json','pointer':'/operations/'+str(index)}})
    proof=None
    if record:
        proof=check_projection(data,record)
    return dict(identity=copy.deepcopy(identity),timezone=data['site']['timezone'],planning_at=data['planning_at'],horizon=data['horizon'],
        tasks=tasks,execution=dict(status='PRESENT' if proof else 'NOT_COLLECTED',proof=proof,
            evidence_class=('SYNTHETIC_RESEARCH_ASSUMPTION' if proof['confirmation']['declaration']=='SYNTHETIC_ACTUAL_EXECUTION' else 'CONFIRMED_EXECUTION_RECORD') if proof else None),
        checkpoint_supported=data['rules'].get('manufacturing_profile')=='two-level-wip-v1' and not record,
        limits=['计划工序不能证明实际执行或完成比例；已确认历史前缀与未来任务不按临时ID配对。',
                '无订单级pegging不归属订单；下一轮仅支持一次完整上游前缀，无实际非零客户交付认证。'])


def verify_parent(child,parent,parent_plan):
    from p22.contracts import digest as version_digest
    record=child.get('business_data',{}).get('execution_checkpoint')
    if not record or child.get('parent_version_id')!=parent['id'] or record.get('parent_data_version_id')!=parent['id']:
        raise InputError('ROLLING_PARENT_VERSION_MISMATCH')
    if (record.get('parent_plan_id')!=parent_plan['id'] or record.get('parent_snapshot_sha256')!=parent_plan['snapshot_sha256']
        or record.get('parent_data_version_sha256')!=version_digest(parent) or record['source_input']!=parent['business_data']['plan_input']):
        raise InputError('ROLLING_PARENT_SOURCE_MISMATCH')
    return check_projection(child['business_data']['plan_input'],record)


def operation_totals(tasks):
    amounts=defaultdict(Decimal)
    for t in tasks:amounts[(t['operation_id'],t['item_id'],t['location_id'],t['uom'])]+=Decimal(t['quantity'])
    return [{'operation_id':k[0],'item_id':k[1],'location_id':k[2],'uom':k[3],'quantity':format(v,'f')} for k,v in amounts.items()]
