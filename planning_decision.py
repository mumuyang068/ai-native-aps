"""Read-only worklists from the existing immutable run and draft stores.

Only native Constraint.demand establishes demand ownership. Problem stays run-level.
No new evidence store, solver, allocation inference, or execution authority.
"""
import copy
import hashlib
import json
from zoneinfo import ZoneInfo
from planning_analysis import clock
from planning_adapter import InputError


def endpoint(data):
    from network_distribution import enabled as network_enabled
    if network_enabled(data):return dict(kind='MATERIAL_REPLENISHMENT',label='目的地材料需求满足（规划）；不代表成衣交付、实际调拨或客户签收')
    from finished_delivery import enabled as finished_enabled
    finished=finished_enabled(data)
    stage=data['rules'].get('manufacturing_profile') in {'routing-alternate-capacity-v1','two-level-wip-v1','two-level-confirmed-supply-v1','mixed-fixed-batch-wip-v1','mixed-fixed-batch-confirmed-supply-v1','mixed-fixed-batch-confirmed-qualified-v1','mixed-fixed-batch-confirmed-cut-qualified-v1'}
    return {'kind':'CUSTOMER_FG' if finished else 'STAGE_OUTPUT' if stage else 'ORDER_DELIVERY',
            'label':'成品客户需求（规划，未执行）' if finished else '阶段需求；不代表后续成衣质检、包装及客户收货' if stage else '当前计划需求履约（规划，未执行）'}


def read_table(folder,name,run_id):
    """Keep absent, empty, unreadable, and tampered records distinct."""
    manifest=json.loads((folder/'run-manifest.json').read_bytes())
    key='raw/'+name+'.json';digest=manifest.get('files',{}).get(key)
    result={'status':'NOT_COLLECTED','records':[]}
    if not digest:return result
    result['source']={'artifact':key,'sha256':digest,'run_id':run_id}
    try:content=(folder/key).read_bytes()
    except OSError:return {**result,'status':'UNREADABLE'}
    if hashlib.sha256(content).hexdigest()!=digest:return {**result,'status':'INVALID_EVIDENCE'}
    try:
        value=json.loads(content);rows=value if isinstance(value,list) else value['rows']
        if not isinstance(rows,list) or not all(isinstance(r,dict) for r in rows):raise ValueError()
        if isinstance(value,dict) and 'records' in value and value['records']!=len(rows):
            return {**result,'status':'NOT_COLLECTED','records':[],'collection_reason':'INCOMPLETE_NATIVE_PAGE','declared_records':value['records'],'collected_records':len(rows)}
    except (ValueError,TypeError,KeyError):return {**result,'status':'UNREADABLE'}
    return {**result,'status':'PRESENT' if rows else 'EMPTY_CHECKED','records':rows,'records_pointer':'' if isinstance(value,list) else '/rows'}


def exception_projection(data,result,identity,constraints,problems):
    zone=ZoneInfo(data['site']['timezone']);native=[]
    for i,row in enumerate(constraints['records']):
        try:
            native.append({'id':str(row.get('id',i)),'record':row,
                'start_at':clock(row['startdate'],zone).isoformat() if row.get('startdate') else None,
                'end_at':clock(row['enddate'],zone).isoformat() if row.get('enddate') else None,
                'source':{**constraints.get('source',{}),'pointer':constraints.get('records_pointer','/rows')+'/'+str(i)}})
        except (ValueError,TypeError):raise InputError('EVIDENCE_UNREADABLE: 原生Constraint日期不能安全解析') from None
    deliveries={r['demand_id']:r for r in result.get('deliveries',[])};demands=[]
    ids={r['id'] for r in data['demand_lines']}
    for d in data['demand_lines']:
        v=deliveries.get(d['id'],{});linked=[r for r in native if r['record'].get('demand')==d['id']]
        demands.append({'demand_id':d['id'],'order_ref':d.get('order_ref'),'item_id':d['item_id'],
            'location_id':d['location_id'],'uom':d['uom'],'priority':d.get('priority'),'kind':d.get('kind','FIRM_ORDER'),'requested_quantity':d['quantity'],
            'planned_quantity':v.get('planned_quantity'),'unfulfilled_quantity':v.get('unfulfilled_quantity'),
            'due_at':d['due_at'],'planned_delivery_at':v.get('planned_delivery_at'),'status':v.get('status','NOT_CHECKED'),
            'constraints':linked,'constraint_status':('PRESENT' if linked else 'EMPTY_CHECKED') if constraints['status'] in {'PRESENT','EMPTY_CHECKED'} else constraints['status']})
    summary=lambda e:{k:v for k,v in e.items() if k!='records'}
    return {'identity':identity,'timezone':data['site']['timezone'],'planning_at':data['planning_at'],
        'horizon_end':data['horizon']['end_at'],'endpoint':endpoint(data),
        'validation':{'known_fail':result.get('assessment',{}).get('constraint_check')=='FAIL'},
        'evidence':{'constraints':summary(constraints),'problems':{**summary(problems),'relation_scope':'RUN_LEVEL_ONLY'}},
        'demands':demands,'unlinked_constraints':[r for r in native if r['record'].get('demand') not in ids],
        'problems':[{'id':str(r.get('id',i)),'record':r,'source':{**problems.get('source',{}),'pointer':problems.get('records_pointer','/rows')+'/'+str(i)}} for i,r in enumerate(problems['records'])],
        'limits':['Constraint是原生规划观察，不代表唯一根因、独立欠量贡献或执行授权。',
                  'Problem只保留运行级归属；缺少原生demand引用时不根据同名、BOM或时间推断客户关联。']}


def issue_reports(data,post,identity,constraints,problems):
    """One run's native observations; resolve owners only by explicit native names."""
    zone=ZoneInfo(data['site']['timezone']);demands={d['id'] for d in data['demand_lines']}
    resources={r['id'] for r in data.get('resources',[])}
    buffers={b['name']:(b['item'],b['location']) for b in post.get('buffers',[])}
    output={}
    for name,source in [('constraints',constraints),('problems',problems)]:
        rows=[];status=source['status']
        try:
            seen=set()
            for index,record in enumerate(source['records']):
                start=clock(record['startdate'],zone) if record.get('startdate') else None
                end=clock(record['enddate'],zone) if record.get('enddate') else None
                if start is not None and end is not None and start>end:raise ValueError('Inverted issue interval')
                native_id=str(record.get('id',index))
                if native_id in seen:raise ValueError('Duplicate issue ID')
                seen.add(native_id);owner=record.get('owner');buffer=buffers.get(owner)
                rows.append({'id':identity['run_id']+':'+name.upper()+':'+native_id,'native_id':native_id,
                    'type':record.get('name'),'entity':record.get('entity'),'owner':owner,'description':record.get('description'),
                    'start_at':clock(record['startdate'],zone).isoformat() if record.get('startdate') else None,
                    'end_at':clock(record['enddate'],zone).isoformat() if record.get('enddate') else None,
                    'demand_id':record.get('demand') if name=='constraints' and record.get('demand') in demands else None,
                    'resource_id':owner if owner in resources else None,
                    'item_id':buffer[0] if buffer else None,'location_id':buffer[1] if buffer else None,
                    'sources':[{**source.get('source',{}),'pointer':source.get('records_pointer','/rows')+'/'+str(index)}],
                    'record':copy.deepcopy(record)})
        except (ValueError,TypeError,KeyError):status='INVALID_EVIDENCE';rows=[]
        output[name]={**{k:copy.deepcopy(v) for k,v in source.items() if k!='records'},'status':status,'rows':rows,
            'relation_scope':'RUN_LEVEL_ONLY' if name=='problems' else 'EXACT_NATIVE_DEMAND_OR_RUN_LEVEL',
            'validation':{'status':'PASS' if status in {'PRESENT','EMPTY_CHECKED'} else 'FAIL' if status=='INVALID_EVIDENCE' else 'NOT_CHECKED'}}
    return output


def candidate_projection(target,label,state,view,run_id,constraints,changes=None):
    interpretation=(view or {}).get('interpretation',{});result=(view or {}).get('result',{})
    validation={'material':interpretation.get('material_validation'),
        'resources':interpretation.get('qualified_resource_validation'),
        'schedule':{'status':interpretation.get('schedule_check','NOT_CHECKED')}}
    known_fail=(result.get('assessment',{}).get('constraint_check')=='FAIL'
        or interpretation.get('business_status',interpretation.get('status'))=='INVALID_SCHEDULE'
        or any(isinstance(v,dict) and v.get('status')=='FAIL' for v in validation.values()))
    business=interpretation.get('business_status',interpretation.get('status'))
    checked=(business in {'FEASIBLE_ON_TIME','LATE_DELIVERY','PARTIAL_OR_UNPLANNED'}
        and validation['schedule']['status']=='PASS'
        and (validation['material'] or {}).get('status')=='PASS'
        and (validation['resources'] is None or validation['resources'].get('status')=='PASS'))
    comparable=state=='SUCCEEDED' and bool(result) and not known_fail and checked and constraints['status'] not in {'INVALID_EVIDENCE','UNREADABLE'}
    return {'target':target,'label':label,'run_id':run_id,'calculation_state':state,
        'business_status':interpretation.get('business_status',interpretation.get('status')),'comparable':comparable,
        'reason':None if comparable else '存在已知硬约束失败或证据失效，不能比较合法指标。' if known_fail or constraints['status'] in {'INVALID_EVIDENCE','UNREADABLE'} else '必要业务核验尚未通过，不能展示为合法可比指标。' if state=='SUCCEEDED' else '尚无本草案实际成功运行，不能使用历史指标。',
        'changes':changes,'deliveries':copy.deepcopy(result.get('deliveries',[])) if comparable else [],
        'inventory':copy.deepcopy([r for r in interpretation.get('material_validation',{}).get('summary',{}).get('inventory',[]) if r.get('ending_net_available') is not None]) if comparable else [],
        'constraints':{'status':constraints['status'],'count':len(constraints['records'])},
        'validation':validation}
