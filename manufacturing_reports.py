"""Authenticated manufacturing activity, separate from execution and allocation.

Only existing resource-audited profiles are included. Native task references are
run-local. Parent manufacturing orders never enter the operation totals.
"""
import copy
from collections import defaultdict
from zoneinfo import ZoneInfo
from planning_capabilities import supports,profile_id
from planning_reports import at,q,text


def manufacturing_basis(data,result,raw,hashes,validation=None):
    hard=result.get('assessment',{}).get('constraint_check')=='FAIL'
    def empty(status,code):
        return {'status':status,'validation':{'status':'FAIL' if hard or status=='INVALID_EVIDENCE' else 'NOT_CHECKED','issues':[{'code':code}]},'tasks':[],'orders':[],
                'limits':['制造任务和父制造单不重复累计；不是订单分配、实际生产或执行授权。']}
    foundation=profile_id(data)=='foundation-planning-projection-v1'
    if not supports(profile_id(data),'analyses','resources'):
        return empty('UNSUPPORTED','MANUFACTURING_SUMMARY_PROFILE_NOT_CERTIFIED')
    if foundation:
        if validation is None:
            from p22.foundation_audit import audit
            validation=audit(data,raw,run_id=result['solve_run_id'])
        if validation['status']!='PASS':
            output=empty('INVALID_EVIDENCE' if validation['status']=='FAIL' else 'NOT_COLLECTED','COMBINED_NATIVE_AUDIT_NOT_PASS')
            output['validation']=copy.deepcopy(validation)
            return output
    missing=[]
    def rows(container,key):
        value=container.get(key) if isinstance(container,dict) else None
        if not isinstance(value,list):missing.append(key);return []
        return value
    def required(row,key):
        value=row.get(key)
        if value is None:missing.append(key)
        return value
    post=rows(raw.get('fixed_batch_post'),'operationplans')
    def index(rows):
        values={}
        for i,r in enumerate(rows):
            reference=required(r,'reference')
            if reference is None:continue
            reference=str(reference)
            if reference in values:raise ValueError('DUPLICATE_NATIVE_REFERENCE')
            values[reference]=(i,r)
        return values
    try:
        tables={k:index(rows(raw,k)) for k in ['workorder','manufacturingorder']};observed=index(post)
        zone=ZoneInfo(data['site']['timezone']);items={i['id']:i for i in data['items']}
        parent_rows=rows(result,'manufacture_suggestions')+result.get('existing_manufacturing_orders',[])
        parents={r['id']:r for r in parent_rows}
        if len(parents)!=len(parent_rows):raise ValueError('DUPLICATE_RESULT_PARENT')
        existing={r['id']:r for r in data.get('existing_orders',[]) if r.get('type')=='MO'}
        tasks=[];orders=[];seen=set()
        for op in rows(result,'operations'):
            if op['id'] in seen:raise ValueError('DUPLICATE_RESULT_TASK')
            seen.add(op['id']);parent=parents.get(op['manufacture_id'])
            if parent is None:
                if not isinstance(result.get('manufacture_suggestions'),list):missing.append('MANUFACTURING_PARENT_NOT_COLLECTED');continue
                raise ValueError('MANUFACTURING_PARENT_MISMATCH')
            refs=[x.split(':',2) for x in op.get('evidence_ids',[]) if x.startswith(('ENGINE:workorder:','ENGINE:manufacturingorder:'))]
            if len(refs)!=1:missing.append('EXACT_TASK_REFERENCE_MISSING');continue
            _,table,reference=refs[0]
            ni,native=tables[table].get(reference,(None,{}));pi,persisted=observed.get(reference,(None,{}))
            if ni is None or pi is None:missing.append('REFERENCED_NATIVE_TASK_MISSING')
            native_status=required(native,'status');post_status=required(persisted,'status')
            parent_reference=str(parent.get('engine_ref',parent.get('source_reference',parent.get('external_ref',''))))
            if table=='workorder':
                owner=required(native,'owner')
                if owner is not None and str(owner)!=parent_reference:raise ValueError('NATIVE_PARENT_MISMATCH')
            for record in [native,persisted]:
                value=required(record,'quantity')
                if value is not None and q(value)!=q(op['quantity']):raise ValueError('NATIVE_QUANTITY_MISMATCH')
            for k,nk,pk in [('start_at','startdate','start'),('end_at','enddate','end')]:
                for record,field in [(native,nk),(persisted,pk)]:
                    value=required(record,field)
                    if value is not None and at(op[k],zone)!=at(value,zone):raise ValueError('NATIVE_TIME_MISMATCH')
            if native_status is not None and post_status is not None and native_status!=post_status:raise ValueError('NATIVE_STATUS_MISMATCH')
            if at(op['end_at'],zone)<at(op['start_at'],zone):raise ValueError('NATIVE_NEGATIVE_DURATION')
            if missing:continue
            item=items[parent['item_id']]
            enterprise_reference=parent.get('source_reference',parent.get('external_ref'))
            if enterprise_reference not in existing:enterprise_reference=None
            sources=[{'artifact':'PlanResult.json','pointer':'/operations/'+str(result['operations'].index(op)),'sha256':hashes.get('PlanResult.json')},
                     {'artifact':'raw/'+table+'.json','pointer':'/'+str(ni),'sha256':hashes.get('raw/'+table+'.json')},
                     {'artifact':'raw/fixed_batch_post.json','pointer':'/operationplans/'+str(pi),'sha256':hashes.get('raw/fixed_batch_post.json')}]
            tasks.append({'id':op['id'],'manufacture_id':op['manufacture_id'],'operation_id':op['operation_id'],
                'item_id':parent['item_id'],'location_id':parent['location_id'],'uom':parent.get('uom',item['base_uom']),
                'quantity':text(q(op['quantity'])),'start_at':at(op['start_at'],zone).isoformat(),'end_at':at(op['end_at'],zone).isoformat(),
                'status':native['status'],'native_reference':reference,'enterprise_reference':enterprise_reference,
                'identity_scope':'ENTERPRISE' if enterprise_reference else 'RUN_LOCAL',
                'quantity_completed':None if native.get('quantity_completed') is None else text(q(native['quantity_completed'])),
                'resource_ids':list(dict.fromkeys(r['resource'] for r in native.get('resources',[]) if r.get('resource'))),'sources':sources})
        for parent in parents.values():
            reference=str(parent.get('engine_ref',parent.get('source_reference',parent.get('external_ref',''))))
            if reference not in tables['manufacturingorder']:missing.append('NATIVE_PARENT_ORDER_MISSING');continue
            i,n=tables['manufacturingorder'][reference]
            required(n,'status');required(n,'operation');value=required(n,'quantity')
            if value is not None and q(value)!=q(parent['quantity']):raise ValueError('PARENT_QUANTITY_MISMATCH')
            for k,nk in [('start_at','startdate'),('end_at','enddate')]:
                value=required(n,nk)
                if value is not None and at(parent[k],zone)!=at(value,zone):raise ValueError('PARENT_TIME_MISMATCH')
            if missing:continue
            er=parent.get('source_reference',parent.get('external_ref'));er=er if er in existing else None
            linked=[t for t in tasks if t['manufacture_id']==parent['id']]
            orders.append({'id':parent['id'],'manufacture_id':parent['id'],'operation_id':n['operation'],'item_id':parent['item_id'],
                'location_id':parent['location_id'],'uom':parent.get('uom',items[parent['item_id']]['base_uom']),'quantity':text(q(parent['quantity'])),
                'start_at':at(parent['start_at'],zone).isoformat(),'end_at':at(parent['end_at'],zone).isoformat(),'status':n.get('status'),
                'native_reference':reference,'enterprise_reference':er,'identity_scope':'ENTERPRISE' if er else 'RUN_LOCAL',
                'operation_task_ids':[t['id'] for t in linked],
                'resource_ids':list(dict.fromkeys(r for task in linked for r in task['resource_ids'])),
                'protected':er is not None,'protection_reason':('已有工单，保持来源事实；调整仅可走原认证动作。' if er else None),
                'sources':[{'artifact':'raw/manufacturingorder.json','pointer':'/'+str(i),'sha256':hashes.get('raw/manufacturingorder.json')}]})
    except KeyError as e:
        return empty('NOT_COLLECTED','REQUIRED_MANUFACTURING_FIELD_MISSING:'+str(e))
    except (TypeError,ValueError,ArithmeticError) as e:
        return empty('INVALID_EVIDENCE',str(e))
    if hard:return empty('INVALID_EVIDENCE','SOURCE_RESULT_HARD_CONSTRAINT_FAIL')
    if missing:return empty('NOT_COLLECTED','REQUIRED_MANUFACTURING_EVIDENCE_MISSING:'+','.join(dict.fromkeys(missing)))
    return {'status':'PRESENT' if tasks else 'EMPTY_CHECKED','validation':{'status':'PASS','issues':[]},'tasks':tasks,'orders':orders,
        'limits':['开工/完成按原生计划起止归桶，不是实际生产或已交付。','分时产量未采集，不均匀摊产。',
                  '工序任务与父制造单分别列示，不累加；工序数量按工艺目标物料/单位，非各阶段实际入库总和。','临时原生ID仅在本次运行有效；没有认证pegging不关联客户分配。']}


def manufacturing_periods(component,periods,timezone):
    if component['status'] not in {'PRESENT','EMPTY_CHECKED'}:return []
    zone=ZoneInfo(timezone);rows=[]
    for a,b in periods:
        a=at(a,zone);b=at(b,zone);groups=defaultdict(list)
        for task in component['tasks']:
            if a<=at(task['start_at'],zone)<b or a<=at(task['end_at'],zone)<b:
                groups[tuple(task[k] for k in ['operation_id','item_id','location_id','uom','status'])].append(task)
        for key,tasks in groups.items():
            starts=[t for t in tasks if a<=at(t['start_at'],zone)<b];ends=[t for t in tasks if a<=at(t['end_at'],zone)<b]
            rows.append({**dict(zip(['operation_id','item_id','location_id','uom','status'],key)),
                'start_at':a.isoformat(),'end_at':b.isoformat(),'started_quantity':text(sum((q(t['quantity']) for t in starts),q(0))),
                'finished_quantity':text(sum((q(t['quantity']) for t in ends),q(0))),'started_task_ids':[t['id'] for t in starts],
                'finished_task_ids':[t['id'] for t in ends],'task_ids':[t['id'] for t in tasks],
                'production_quantity':None,'production_status':'NOT_COLLECTED'})
    return rows


def manufacturing_order_periods(component,periods,timezone):
    """Count distinct parent MOs; task counts and quantities never become MO counts."""
    if component['status'] not in {'PRESENT','EMPTY_CHECKED'}:return []
    zone=ZoneInfo(timezone);rows=[]
    for a,b in periods:
        groups=defaultdict(list)
        for order in component['orders']:
            if at(order['start_at'],zone)<b and at(order['end_at'],zone)>=a:
                groups[tuple(order[k] for k in ('item_id','location_id','uom','operation_id','status'))].append(order)
        for key,orders in groups.items():
            starts=[o for o in orders if a<=at(o['start_at'],zone)<b]
            ends=[o for o in orders if a<=at(o['end_at'],zone)<b]
            rows.append({**dict(zip(('item_id','location_id','uom','operation_id','status'),key)),
                'start_at':a.isoformat(),'end_at':b.isoformat(),'started_order_count':len(starts),'finished_order_count':len(ends),
                'started_quantity':text(sum((q(o['quantity']) for o in starts),q(0))),
                'finished_quantity':text(sum((q(o['quantity']) for o in ends),q(0))),
                'manufacture_ids':[o['id'] for o in orders],
                'resource_ids':list(dict.fromkeys(r for o in orders for r in o.get('resource_ids',[]))),
                'operation_task_ids':list(dict.fromkeys(t for o in orders for t in o['operation_task_ids']))})
    return rows
