"""Bounded quality availability of opening stock, never an execution ledger.

An explicitly sourced synthetic release is input to a new confirmed simulation
version. Future PACK supply never creates an actual release, shipment or receipt.
"""
from decimal import Decimal
from datetime import datetime

PROFILE='finished-garment-confirmed-v1'

def net_available(position):
    pending=position.get('quality_availability',{}).get('inspection_pending_quantity','0')
    return Decimal(position['on_hand'])-Decimal(position['quality_blocked'])-Decimal(position['other_nonnettable'])-Decimal(pending)


def opening_components(data,position):
    """Read projection of explicitly separate receipt cohorts, not new stock."""
    from planning_adapter import InputError,decimal_text
    receipts=[r for r in data.get('purchase_receipts',[]) if (r['item_id'],r['location_id'])==(position['item_id'],position['location_id'])]
    if any(r['uom']!=position['uom'] for r in receipts):raise InputError('RECEIPT_SCOPE_OR_UNIT')
    physical=Decimal(position['on_hand']);frozen=Decimal(position['quality_blocked']);other=Decimal(position['other_nonnettable'])
    pending=Decimal(position.get('quality_availability',{}).get('inspection_pending_quantity','0'))
    available=net_available(position)
    for r in receipts:
        q=Decimal(str(r['quantity']));physical+=q
        if r['quality_status']=='RELEASED':available+=q
        elif r['quality_status']=='PENDING_QC':pending+=q
        elif r['quality_status']=='FROZEN':frozen+=q
        else:raise InputError('RECEIPT_RELEASE_EVIDENCE')
    return dict(physical=decimal_text(physical),frozen=decimal_text(frozen),pending=decimal_text(pending),other=decimal_text(other),available=decimal_text(available),basis='BASE_EXCLUDES_LISTED_RECEIPTS')

def validate(data):
    from planning_adapter import InputError
    for row in data['inventory_positions']:
        record=row.get('quality_availability')
        if record is None:
            fg_ids={i['id'] for i in data['items'] if i['kind']=='FINISHED'}
            if data.get('rules',{}).get('manufacturing_profile')in (PROFILE, 'enterprise-forecast-fg-v1','enterprise-accessory-supply-v1') and row['item_id'] in fg_ids and net_available(row)>0:
                raise InputError('QUALITY_RELEASE_SOURCE_REQUIRED: '+row['id'])
            continue
        def require(ok,code):
            if not ok:raise InputError(code+': '+row['id'])
        require(data.get('rules',{}).get('manufacturing_profile')in (PROFILE, 'enterprise-forecast-fg-v1','enterprise-accessory-supply-v1'),'UNSUPPORTED_INVENTORY_QUALITY_PROFILE')
        pending=Decimal(record['inspection_pending_quantity']);released=Decimal(record['released_quantity'])
        require(pending>=0 and released>=0 and net_available(row)>=0,'INVALID_QUALITY_BALANCE')
        require(released==net_available(row),'QUALITY_RELEASE_BALANCE_MISMATCH')
        at=datetime.fromisoformat(record['effective_at']);planning=datetime.fromisoformat(data['planning_at'])
        require(at<=planning and at<=datetime.fromisoformat(row['as_of']),'FUTURE_QUALITY_NOT_OPENING_STOCK')
        if released>0:
            require(record['evidence_kind']=='SYNTHETIC_CONFIRMED_RELEASE' and bool(record['release_reference']),'QUALITY_RELEASE_SOURCE_REQUIRED')
        elif record['evidence_kind']=='NO_RELEASE':
            require(record['release_reference'] is None,'QUALITY_RELEASE_STATE_MISMATCH')

def project(data,result,identity):
    if data.get('rules',{}).get('manufacturing_profile')not in (PROFILE, 'enterprise-forecast-fg-v1','enterprise-accessory-supply-v1'):
        return {'identity':identity,'status':'UNSUPPORTED','reason':'当前模型未认证FG质量可用性分解。','rows':[],'timezone':data['site']['timezone']}
    rows=[]
    for i in data['inventory_positions']:
        record=i.get('quality_availability');future=None;planned=[]
        if 'manufacture_suggestions' in result:
            planned=[m for m in result['manufacture_suggestions'] if m['item_id']==i['item_id'] and m['location_id']==i['location_id']]
            future=str(sum((Decimal(m['quantity']) for m in planned),Decimal(0)))
        rows.append({'id':i['id'],'item_id':i['item_id'],'location_id':i['location_id'],'uom':i['uom'],
            'physical_onhand':i['on_hand'],'quality_blocked':i['quality_blocked'],'other_nonnettable':i['other_nonnettable'],
            'qc_waiting':record['inspection_pending_quantity'] if record else None,
            'planning_net_available':str(net_available(i)),
            'released_net_available':record['released_quantity'] if record else None,
            'release_evidence_status':'PRESENT' if record else 'NOT_COLLECTED',
            'declaration_scope':record['evidence_kind'] if record else 'DECLARED_NET_STOCK_NOT_RELEASE_CERTIFIED',
            'release_reference':record['release_reference'] if record else None,
            'effective_at':record['effective_at'] if record else i['as_of'],
            'actual_quality_release_verified':False,'future_planned_supply':future,
            'planned_supply_records':planned,'sources':[i['provenance']]+([record['provenance']] if record else [])})
    return {'identity':identity,'status':'PRESENT','timezone':data['site']['timezone'],'rows':rows,
        'known_business_fail':result.get('assessment',{}).get('constraint_check')=='FAIL',
        'limits':['质量可用性是当前模拟版本的有来源输入，不确认实际质量放行或出库。',
                  '未来PACK制造建议仅为计划供应，不倒灌成现货、放行净可用或客户签收。',
                  '未采集放行来源时保持未知；本轮仅支持期初有界合成质量状态，不支持未来释放事件或MES生命周期。']}
