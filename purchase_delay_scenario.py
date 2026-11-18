"""The existing single-PO/QC delay calculation, plus exact enterprise replay."""
import copy
from datetime import datetime,timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
ACTION='delay_confirmed_purchase'
ENTERPRISE_PROFILE='enterprise-accessory-supply-v1'

def apply(data,reference,new_receipt_at,date):
    from drafts import DraftError
    found=[r for r in data['existing_orders'] if r['id']==reference]
    if len(found)!=1:raise DraftError('UNKNOWN_CONFIRMED_PURCHASE: 当前计划没有该确认采购')
    row=found[0]
    if row['type']!='PO' or row['source_status']!='confirmed' or Decimal(row['received_quantity'])!=0 or row['inventory_includes_receipt']:
        raise DraftError('UNSUPPORTED_CONFIRMED_SUPPLY: 仅支持完整未收的 confirmed 采购')
    source=next(s for s in data['supply_sources'] if s['id']==row['supply_source_id'])
    before_receipt,before_available=row['expected_receipt_at'],row['available_at']
    receipt=date(new_receipt_at)
    if datetime.fromisoformat(receipt)<=datetime.fromisoformat(before_receipt):raise DraftError('RECEIPT_MUST_BE_LATER: 模拟到货时间必须晚于原预计到货')
    wait=source['receiving_qc']['wait_seconds']
    available=date((datetime.fromisoformat(receipt)+timedelta(seconds=wait)).isoformat())
    row.update(expected_receipt_at=receipt,available_at=available)
    changes={'intent':'确认采购延期模拟','reference':row['id'],'item_id':row['item_id'],
        'quantity':row['remaining_quantity'],'uom':row['uom'],'qc_wait_seconds':wait,
        'before_receipt_at':before_receipt,'after_receipt_at':receipt,
        'before_available_at':before_available,'after_available_at':available}
    if data['rules'].get('manufacturing_profile')==ENTERPRISE_PROFILE:
        changes.update(action=ACTION,simulation_only=True,execution_authorized=False)
    return changes

def verify(baseline,draft):
    from drafts import DraftError
    if baseline['rules'].get('manufacturing_profile')!=ENTERPRISE_PROFILE:return
    c=draft.get('changes',{})
    changed=baseline.get('existing_orders')!=draft.get('input',{}).get('existing_orders')
    if c.get('action')!=ACTION and not changed:return
    def date(value):
        dt=datetime.fromisoformat(value.replace('Z','+00:00'))
        if dt.tzinfo is None:raise ValueError('timezone required')
        dt=dt.astimezone(ZoneInfo(baseline['site']['timezone']))
        if not datetime.fromisoformat(baseline['planning_at'])<=dt<=datetime.fromisoformat(baseline['horizon']['end_at']):raise ValueError('outside horizon')
        return dt.isoformat()
    try:
        if c.get('action')!=ACTION:raise ValueError('missing typed action')
        expected=copy.deepcopy(baseline)
        recreated=apply(expected,c['reference'],c['after_receipt_at'],date)
        expected.update(scenario_id=draft['id'],scenario_revision=1)
        if c!=recreated or draft['input']!=expected:raise ValueError('undeclared fact mutation')
    except (KeyError,ValueError,TypeError,StopIteration) as exc:
        raise DraftError('PURCHASE_DELAY_SCOPE_MISMATCH: 仅允许指定原确认采购的预计到货及规则推导QC日期变化') from exc
