"""A declared single-MO window hypothesis, never execution permission."""
import copy,json
from datetime import datetime,timedelta,timezone
from decimal import Decimal

INTENT='approved工单窗口模拟'
AUTHORITY={'simulation_only':True,'business_permission':'NOT_CONFIRMED','execution_authorized':False}

def legal_windows(data,reference):
    """Read-only choices; original apply/validator remain authoritative on submit."""
    from drafts import DraftError
    from planning_adapter import validate_input,InputError
    if data['rules'].get('manufacturing_profile')!='fixed-batch-window-v1':raise DraftError('UNSUPPORTED_FIXED_BATCH')
    rows=[m for m in data['existing_orders'] if m['id']==reference]
    if len(rows)!=1:raise DraftError('UNKNOWN_APPROVED_MO')
    mo=rows[0]
    if mo['type']!='MO' or mo['source_status']!='approved' or Decimal(mo['completed_quantity'])!=0:raise DraftError('APPROVED_SIMULATION_ONLY')
    original=mo['operations'][0];duration=datetime.fromisoformat(original['end_at'])-datetime.fromisoformat(original['start_at'])
    resource=next(r for r in data['resources'] if r['id']==original['resource_id'])
    cal=next(c for c in data['calendars'] if c['id']==resource['availability_calendar_id']);options=[]
    for w in cal['available_intervals']:
        a=datetime.fromisoformat(w['start_at']);b=a+duration
        if a<datetime.fromisoformat(data['planning_at']) or b>datetime.fromisoformat(w['end_at']) or a==datetime.fromisoformat(original['start_at']):continue
        protected=[o for m in data['existing_orders'] if m['type']=='MO' and m['id']!=reference for o in m['operations'] if o['resource_id']==original['resource_id']]
        if any(a<datetime.fromisoformat(o['end_at']) and b>datetime.fromisoformat(o['start_at']) for o in protected):continue
        candidate=copy.deepcopy(data)
        try:apply(candidate,reference,a.isoformat());validate_input(candidate)
        except (DraftError,InputError):continue
        options.append({'start_at':a.isoformat(),'end_at':b.isoformat(),'resource_id':original['resource_id'],'quantity':mo['quantity'],'uom':mo['uom'],'calendar_id':cal['id'],**AUTHORITY})
    return options

def apply(data,reference,start,acknowledged_at=None):
    from drafts import DraftError
    from fixed_batch import enabled,joint_enabled
    if not enabled(data) or joint_enabled(data):raise DraftError('UNSUPPORTED_FIXED_BATCH: 仅支持已验收的固定批量窗口 profile')
    rows=[m for m in data['existing_orders'] if m['id']==reference]
    if len(rows)!=1:raise DraftError('UNKNOWN_APPROVED_MO: 当前计划没有该已有工单')
    mo=rows[0]
    if mo['type']!='MO' or mo['source_status']!='approved' or Decimal(mo['completed_quantity'])!=0:
        raise DraftError('APPROVED_SIMULATION_ONLY: 仅允许完整未执行的 approved 工单模拟')
    before=copy.deepcopy(mo['operations'][0])
    if datetime.fromisoformat(before['start_at'])<datetime.fromisoformat(data['planning_at']):
        raise DraftError('APPROVED_SIMULATION_ONLY: 已开始窗口不在支持范围')
    if datetime.fromisoformat(start)==datetime.fromisoformat(before['start_at']):
        raise DraftError('UNCHANGED_MO_WINDOW: 新开始时间与原安排相同')
    duration=datetime.fromisoformat(before['end_at'])-datetime.fromisoformat(before['start_at'])
    after={**before,'start_at':start,'end_at':(datetime.fromisoformat(start)+duration).isoformat()}
    mo['operations'][0]=after
    assumption={'id':'SIM-APPROVED-MO:'+reference,'field_ref':'/existing_orders/'+str(data['existing_orders'].index(mo))+'/operations/0',
      'value':json.dumps({'reference':reference,'before':before,'after':after,**AUTHORITY},ensure_ascii=False,sort_keys=True),
      'reason':'仅假设该工单使用指定窗口；原企业事实不变。业务允许性和实际进度仍需核实，未获执行授权。',
      'acknowledged_by':'P3_SIMULATION_REQUEST_NOT_EXECUTION_APPROVAL',
      'acknowledged_at':acknowledged_at or datetime.now(timezone.utc).isoformat()}
    data['assumptions'].append(assumption)
    return {'intent':INTENT,'reference':reference,'before':before,'after':after,
      'quantity':mo['quantity'],'uom':mo['uom'],'source_status':'approved','assumption':assumption,**AUTHORITY}

def verify_scope(baseline,draft):
    from drafts import DraftError
    changes=draft['changes']
    if changes.get('intent')!=INTENT:return
    try:
        expected=copy.deepcopy(baseline)
        recreated=apply(expected,changes['reference'],changes['after']['start_at'],changes['assumption']['acknowledged_at'])
        expected.update(scenario_id=draft['id'],scenario_revision=1)
        if changes!=recreated or expected!=draft['input']:raise ValueError()
        from planning_adapter import validate_input
        validate_input(expected)
    except (KeyError,ValueError,TypeError) as exc:
        raise DraftError('SIMULATION_SCOPE_MISMATCH: 草案超出指定工单窗口或改变了受保护承诺') from exc

def comparison(base,view,changes,items):
    """Existing identities are stable; proposed native numbers are not matched."""
    left={r['engine_ref']:r for r in base['result'].get('existing_manufacturing_orders',[])}
    right={r['engine_ref']:r for r in view['result'].get('existing_manufacturing_orders',[])}
    fields=['start_at','end_at','quantity','source_status','recipe_id','external_ref']
    orders=[{'reference':ref,'base':left.get(ref),'candidate':right.get(ref),
      'selected':ref==changes['reference'],'changed':any((left.get(ref) or {}).get(k)!=(right.get(ref) or {}).get(k) for k in fields)} for ref in sorted(set(left)|set(right))]
    a=base['interpretation'].get('material_validation',{});b=view['interpretation'].get('material_validation',{})
    stock=[]
    if a.get('status')==b.get('status')=='PASS':
        ai={(r['item_id'],r['location_id']):r['quantity'] for r in a['ending_balances']}
        bi={(r['item_id'],r['location_id']):r['quantity'] for r in b['ending_balances']}
        units={i['id']:i['base_uom'] for i in items}
        stock=[{'item_id':key[0],'location_id':key[1],'uom':units[key[0]],'base_quantity':ai.get(key),'candidate_quantity':bi.get(key),
          'quantity_delta':str(Decimal(bi[key])-Decimal(ai[key])) if key in ai and key in bi else None} for key in sorted(set(ai)|set(bi))]
    return {'manufacturing_simulation':{**AUTHORITY,'reference':changes['reference'],'before':changes['before'],'after':changes['after']},
      'existing_manufacturing':orders,'inventory_balances':stock,
      'inventory_comparison_status':'CHECKED_WITHIN_PROFILE' if stock else 'NOT_CHECKED'}
