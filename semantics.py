"""Evidence-derived interpretation alongside the frozen V0.2 PlanResult."""
from collections import Counter
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
from planning_adapter import check_result_constraints, decimal_text
from calculation_validation import material_validation,audit_loaded_input,engine_provenance,REPORTED_STOCK,MATERIAL_SCOPE

def business_status(state):
    status = 'DATA_INSUFFICIENT' if state == 'INVALID_INPUT' else 'SOLVE_FAILED' if state == 'FAILED' else 'PENDING'
    return {'calculation_status':state, 'business_status':status, 'infeasibility_proven':False,
            'conclusion_scope':'未证明无解；技术失败或数据不足不能推出业务无解。'}


def native_reference(identifier, run_id, kind):
    """Decode the known owning prefix; external keys may themselves contain ':'."""
    prefix=run_id+':'+kind+':'
    if not isinstance(identifier,str) or not identifier.startswith(prefix) or len(identifier)==len(prefix):
        raise ValueError('RESULT_IDENTITY_PREFIX')
    return identifier[len(prefix):]


def operation_assignments(data, operation_rows, loads, *, fixed_ops=None, machine_only=True):
    """Map every native operation, including a declared zero-load receipt.

    Missing or duplicate processing assignments and any receipt load are
    mapping failures. They must not become a KeyError or an omitted row.
    """
    foundation=data.get('foundation_projection');fixed_ops=fixed_ops or {}
    equipment={r['id'] for r in data['resources'] if r.get('role')=='MACHINE'}
    specs={o['id']:o for route in data.get('routing_revisions',[]) for o in route['operations']}
    result=[];valid=True
    for row in operation_rows:
        ref=str(row['reference']);identity=foundation['native_operation_map'][row['operation']] if foundation else None
        operation=identity['operation_id'] if identity else fixed_ops.get(row['operation'],row['operation'].split(':')[-1])
        actual=[load for load in loads if str(load['operationplan'])==ref]
        transfer=bool(identity and specs[operation].get('duration_model')=='AVAILABILITY_TRANSFER')
        assigned=[load for load in actual if not machine_only or load['resource'] in equipment]
        if transfer:
            valid=valid and not actual;resource=None
        else:
            valid=valid and len(assigned)==1;resource=assigned[0]['resource'] if len(assigned)==1 else None
        result.append((ref,operation,resource,'AVAILABILITY_TRANSFER' if transfer else None))
    return result,valid

def audit_result(data, result, raw, *, native_input=None, loaded_input=None, runtime_metadata=None):
    foundation=data.get('foundation_projection')
    material_evidence=raw
    joint=None
    if foundation:
        from p22.foundation_audit import audit
        joint=audit(data,material_evidence,run_id=result['solve_run_id'])
    from single_lane_distribution import enabled as distribution_enabled,audit as distribution_audit
    from network_distribution import enabled as network_enabled
    if network_enabled(data):
        from network_distribution import audit as distribution_audit
    distribution=distribution_audit(data,raw,run_id=result['solve_run_id']) if distribution_enabled(data) or network_enabled(data) else None
    raw={name:value.get('rows',value.get('results',[])) if isinstance(value,dict) and not name.startswith('fixed_batch_') else value for name,value in raw.items()}
    from fixed_batch import enabled,validate_native_evidence,validate_loaded_model
    fixed=enabled(data)
    from multi_receipt_supply import enabled as multi_enabled,result_operations
    multi=multi_enabled(data)
    zone=ZoneInfo(data['site']['timezone'])
    def at(value):
        if not value:return None
        dt=datetime.fromisoformat(value.replace('Z','+00:00'))
        return (dt.replace(tzinfo=zone) if dt.tzinfo is None else dt.astimezone(zone)).isoformat()
    def num(value):return decimal_text(value or 0)
    checks={}
    ref=lambda value,kind:native_reference(value,result['solve_run_id'],kind)
    def check(name,a,b):checks[name]='PASS' if Counter(a)==Counter(b) else 'FAIL'
    purchases=[r for r in raw['purchaseorder'] if r['status']=='proposed']
    orders={str(r['reference']):r for r in raw['manufacturingorder']+raw['workorder']}
    roots=[r for r in orders.values() if r.get('owner') is None and r['status']=='proposed']
    check('purchase',[(str(r['reference']),r['item'],r['location'],r['supplier'],num(r['quantity']),at(r['startdate']),at(r['enddate'])) for r in purchases],
          [(r['engine_ref'],r['item_id'],r['location_id'],r['supplier_id'],num(r['quantity']),r['order_at'],r['receipt_at']) for r in result['purchase_suggestions']])
    check('manufacture',[(str(r['reference']),r['operation'],num(r['quantity']),at(r['startdate']),at(r['enddate'])) for r in roots],
          [(r['engine_ref'],r['recipe_id'],num(r['quantity']),r['start_at'],r['end_at']) for r in result['manufacture_suggestions']])
    operation_rows=([r for r in orders.values() if r['operation'] in foundation['native_operation_map']] if foundation else result_operations(data,raw)) if foundation or multi else [r for r in orders.values() if fixed or r.get('owner') is not None]
    check('operations',[(str(r['reference']),str(r['reference'] if fixed or ((multi or foundation) and r.get('owner') is None) else r['owner']),num(r['quantity']),at(r['startdate']),at(r['enddate'])) for r in operation_rows],
          [(ref(r['id'],'OP'),ref(r['manufacture_id'],'MO'),num(r['quantity']),r['start_at'],r['end_at']) for r in result['operations']])
    from qualified_operator import enabled as qualified_enabled,audit_resources
    qualified=qualified_enabled(data)
    fixed_ops={p['id']:next(r for r in data['routing_revisions'] if r['id']==p['routing_revision_id'])['operations'][0]['id'] for p in data['production_recipes']} if fixed else {}
    assignments,assignment_valid=operation_assignments(data,operation_rows,raw['operationplanresource'],fixed_ops=fixed_ops,machine_only=bool(qualified or foundation))
    check('operation_assignment',assignments,
          [(ref(r['id'],'OP'),r['operation_id'],r['resource_id'],r.get('operation_kind')) for r in result['operations']])
    checks['operation_assignment_resources']='PASS' if assignment_valid else 'FAIL'
    # PlanResult.resource_loads references manufacturing operations. Supplier
    # capacity events remain in the same raw evidence and are independently
    # audited by foundation's purchase Owner, not fabricated as OP identities.
    manufacturing_refs={str(row['reference']) for row in operation_rows}
    mapped_loads=[r for r in raw['operationplanresource'] if not foundation or str(r['operationplan']) in manufacturing_refs]
    check('resource_loads',[(str(r['operationplan']),r['resource'],num(r['quantity']),at(r['startdate']),at(r['enddate'])) for r in mapped_loads],
          [(ref(r['operation_id'],'OP'),r['resource_id'],num(r['resource_units']),r['start_at'],r['end_at']) for r in result['resource_loads']])
    check('material_flows',[(str(r['operationplan']),r['item'],r['location'],'CONSUME' if Decimal(r['quantity'])<0 else 'PRODUCE',num(abs(Decimal(r['quantity']))),at(r['flowdate'])) for r in raw['operationplanmaterial'] if Decimal(r['quantity'])],
          [(r['order_id'],r['item_id'],r['location_id'],r['direction'],num(r['quantity']),r['at']) for r in result['material_flows']])
    demands={r['id']:r for r in data['demand_lines']}
    native_delivery=[]
    for row in raw['demand']:
        demand=demands[row['name']]; when=at(row.get('deliverydate')); planned=Decimal(str(row.get('plannedquantity') or 0))
        delay=max(0,int((datetime.fromisoformat(when)-datetime.fromisoformat(demand['due_at'])).total_seconds())) if when else None
        status='UNPLANNED' if planned==0 else 'SHORT' if planned<Decimal(demand['quantity']) else 'LATE' if delay else 'ON_TIME'
        native_delivery.append((row['name'],num(planned),num(max(Decimal(0),Decimal(demand['quantity'])-planned)),demand['due_at'],when,delay,status))
    check('delivery_and_risk',native_delivery,[(r['demand_id'],num(r['planned_quantity']),num(r['unfulfilled_quantity']),r['due_at'],r['planned_delivery_at'],r['delay_seconds'],r['status']) for r in result['deliveries']])
    expected_codes={str(r['name']).upper().replace(' ','_') for r in raw['constraint']}
    checks['constraint_risks']='PASS' if expected_codes.issubset({r['code'] for r in result['assessment']['issues']}) else 'FAIL'
    if fixed or foundation:
        qc_refs={'QC:'+r['receipt_reference'] for r in data.get('purchase_receipts',[])} if foundation else set()
        check('existing_manufacturing',[(str(r['reference']),r['operation'],r['status'],num(r['quantity']),at(r['startdate']),at(r['enddate'])) for r in orders.values() if r['status'] in {'confirmed','approved'} and str(r['reference']) not in qc_refs and (not foundation or r.get('owner') is None)],
              [(r['engine_ref'],r['recipe_id'],r['source_status'],num(r['quantity']),r['start_at'],r['end_at']) for r in result.get('existing_manufacturing_orders',[])])
    if multi:
        originals={m['id']:m for m in data['existing_orders'] if m['type']=='MO'}
        check('existing_manufacturing',[(str(r['reference']),originals[str(r['reference'])]['recipe_id'],r['status'],num(r['quantity']),at(r['startdate']),at(r['enddate'])) for r in operation_rows if str(r['reference']) in originals],
              [(r['engine_ref'],r['recipe_id'],r['source_status'],num(r['quantity']),r['start_at'],r['end_at']) for r in result.get('existing_manufacturing_orders',[])])
    hard_issues=joint['issues'] if joint is not None else check_result_constraints(data,result['operations'],raw,run_id=result['solve_run_id'])
    issues=[r['code'] for r in hard_issues]
    start=datetime.fromisoformat(data['planning_at']); end=datetime.fromisoformat(data['horizon']['end_at'])
    for row in purchases:
        if datetime.fromisoformat(at(row['startdate'])) < start:issues.append('PURCHASE_BEFORE_PLANNING_START')
        if datetime.fromisoformat(at(row['enddate'])) > end:issues.append('PURCHASE_AFTER_HORIZON')
    deliveries=result['deliveries']
    expected_assessment='BLOCKED' if issues else 'NEEDS_REVIEW' if raw['problem'] or raw['constraint'] or any(r['status']!='ON_TIME' for r in deliveries) else 'ACCEPTABLE'
    expected_check='FAIL' if any(r['code'] for r in hard_issues) else 'PASS'
    if distribution and distribution['status']=='NOT_CHECKED' and expected_check!='FAIL':expected_check='NOT_CHECKED'
    if multi and expected_check!='FAIL':
        from multi_receipt_supply import audit as audit_receipt_model
        if any(report['status']!='PASS' for report in [audit_receipt_model(data,material_evidence,run_id=result['solve_run_id']),
            audit_resources(data,material_evidence,run_id=result['solve_run_id'])]):expected_check='NOT_CHECKED'
    if foundation:
        expected_check=joint['status']
    from plan_editor import audit_native as editor_audit
    editor=editor_audit(data,material_evidence,run_id=result['solve_run_id']) if data.get('plan_editor_contract') or data.get('foundation_projection',{}).get('editor_scope') else None
    if editor and expected_check!='FAIL':expected_check=editor['status']
    if expected_check=='NOT_CHECKED':expected_assessment='NEEDS_REVIEW'
    checks['assessment']='PASS' if result['assessment']['status']==expected_assessment and result['assessment']['constraint_check']==expected_check else 'FAIL'
    mapping=all(v=='PASS' for v in checks.values())
    material=joint if joint is not None else material_validation(data,material_evidence,run_id=result['solve_run_id'])
    if foundation:material={**material,'not_checked':material['missing_evidence'],'unsupported':['physical_execution','gross_forecast_netting_policy_pending']}
    qualified_resources=audit_resources(data,material_evidence,run_id=result['solve_run_id']) if qualified else None
    if material['status']=='FAIL':issues.extend(i['code'] for i in material['issues'])
    from priority_supply import enabled as priority_enabled,audit as procurement_audit
    procurement=procurement_audit(data,raw) if priority_enabled(data) else None
    if procurement:
        if procurement['status']=='FAIL':issues.extend(i['code'] for i in procurement['issues'])
        elif procurement['status']!='PASS':issues.append('PRIORITY_SUPPLY_NOT_VALIDATED')
    loaded=audit_loaded_input(native_input,loaded_input or {}) if native_input is not None else {
        'status':'NOT_CHECKED','engine_memory_status':'NOT_CHECKED','checked_fields':[],
        'not_checked':['persisted_input_readback'],'issues':[]}
    if loaded['status']=='FAIL':issues.append('INPUT_READBACK_MISMATCH')
    coverage=[REPORTED_STOCK if x=='material_balance' else x for x in result['assessment']['coverage']]
    coverage=list(dict.fromkeys(coverage+['purchase_not_before_planning_start']))
    if material['status']=='PASS':coverage.append(material['scope'])
    provenance=engine_provenance(runtime_metadata or {})
    provenance['legacy_reported_source_commit']=result.get('engine',{}).get('source_commit')
    scope={'checked':coverage,'not_checked':material['not_checked']+['engine_memory_input_readback'],
           'unsupported':material['unsupported'],
           'material_status':material['status'],'input_readback_status':loaded['status']}
    if material['status']!='PASS':scope['not_checked'].append(MATERIAL_SCOPE)
    if loaded['status']!='PASS':scope['not_checked'].append('persisted_input_readback_complete')
    if qualified_resources:
        scope['qualified_resource_status']=qualified_resources['status']
        if qualified_resources['status']=='PASS':scope['checked'].append(qualified_resources['scope'])
        else:scope['not_checked'].append(qualified_resources['scope'])
    status='INVALID_SCHEDULE' if issues or not mapping else 'PARTIAL_OR_UNPLANNED' if any(r['status'] in {'SHORT','UNPLANNED'} for r in deliveries) else 'LATE_DELIVERY' if any(r['status']=='LATE' for r in deliveries) else 'FEASIBLE_ON_TIME'
    from two_level_wip import material_enabled as wip_enabled
    wip=wip_enabled(data)
    if foundation and material['status']!='PASS' and not issues and mapping:status='VALIDATION_INCOMPLETE'
    if wip and material['status']!='PASS' and not issues and mapping:status='VALIDATION_INCOMPLETE'
    if qualified and (qualified_resources['status']!='PASS' or material['status']!='PASS') and not issues and mapping:status='VALIDATION_INCOMPLETE'
    if distribution and distribution['status']=='NOT_CHECKED' and not issues and mapping:status='VALIDATION_INCOMPLETE'
    if editor and editor['status']!='PASS' and not issues and mapping:status='VALIDATION_INCOMPLETE'
    if procurement and procurement.get('summary'):
        procurement['summary']['business_status']=status
        procurement['summary']['eligible_for_cost_comparison']=(status=='FEASIBLE_ON_TIME' and procurement['status']=='PASS' and material['status']=='PASS')
    from confirmed_supply import receipt_windows
    return {'interpretation_version':'foundation-native-v3' if foundation else 'p1.3-fixed-batch-v1' if fixed else 'p1.3','calculation_status':'SUCCEEDED','business_status':status,
            'mapping_check':'PASS' if mapping else 'FAIL','mapping_checks':checks,
            'schedule_check':'FAIL' if issues else 'NOT_CHECKED' if (foundation and material['status']!='PASS') or (multi and material['status']!='PASS') or (qualified and qualified_resources['status']!='PASS') or (distribution and distribution['status']=='NOT_CHECKED') or (editor and editor['status']!='PASS') else 'PASS','schedule_issues':sorted(set(issues)),
            'infeasibility_proven':False,
            'conclusion_scope':'仅描述该次计算及已列明检查范围内的表现；未核验项不是PASS，未证明完整通用物料守恒、现实可执行、最优、最早交付或全局无解。',
            **({'plan_editor_validation':editor} if editor else {}),'coverage':coverage,'validation_scope':scope,'material_validation':material,
            'input_readback':loaded,'engine_provenance':provenance,**({'foundation_validation':material} if foundation else {}),
            'purchase_quantity':num(sum(Decimal(r['quantity']) for r in purchases)),
            'manufacture_quantity':num(sum(Decimal(r['quantity']) for r in roots)),
            'purchase_windows':result['purchase_suggestions'],'deliveries':deliveries,
            'confirmed_supply_windows':receipt_windows(data),
            **({'qualified_resource_validation':qualified_resources} if qualified else {}),
            **({'distribution':distribution.get('summary'),'distribution_validation':distribution} if distribution else {}),
            **({'wip':material.get('summary'),'wip_validation':{k:material[k] for k in ['status','scope','issues','missing_evidence','not_checked','unsupported']}} if wip else {}),
            **({'procurement_validation':procurement,'procurement':procurement.get('summary')} if procurement else {}),
            **({'native_manufacturing_validation':validate_native_evidence(data,raw),'native_model_validation':validate_loaded_model(data,raw)} if fixed else {}),
            'diagnostic_windows':[{'role':'DIAGNOSTIC_NOT_PURCHASE','code':str(r['name']).upper().replace(' ','_'),
                'demand_id':r.get('demand'),'start_at':at(r.get('startdate')),'end_at':at(r.get('enddate')),
                'evidence_id':'ENGINE:constraint:'+str(r['id']),
                'explanation':'引擎冲突诊断窗口，不是实际采购下单时间。实际时间见 purchase_windows。'} for r in raw['constraint']]}
