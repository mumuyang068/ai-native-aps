import hashlib
import json
from datetime import datetime
from planning_adapter import InputError, to_native, validate_input

PROFILE='p21-customer-aware-static-v1'
GROUPS={'mappings','orders','production','inventory','purchasing','customers'}

def encoded(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def digest(value):return hashlib.sha256(encoded(value).encode()).hexdigest()

def enterprise_native(data,context):
    if 'foundation_projection' in data:
        from p22.foundation_projection import ADAPTER,validate
        validate(data)
        adapters={ADAPTER}
        if data['foundation_projection'].get('policy',{}).get('profile')=='continuity-current-state-v1':
            adapters.add('canonical-continuity-current-state-v1')
        if data['foundation_projection'].get('policy',{}).get('profile')=='odoo-verified-manufacturing-v1':
            adapters={'odoo-verified-manufacturing-v1'}
        if context.get('adapter_profile') not in adapters or context.get('foundation_projection_sha256')!=data['foundation_projection']['projection_sha256']:
            raise InputError('PROJECTION_CONTEXT_MISMATCH')
        native=to_native(data)
        native['customer']=[{'name':r['id']} for r in context['customers']]
        links=context.get('demand_customer_links',{})
        if links!={r['id']:r['customer_id'] for r in data['demand_lines']}:raise InputError('CUSTOMER_MAPPING_GAP')
        for row in native['demand']:row['customer']=links[row['name']]
        return native
    if context.get('adapter_profile')!=PROFILE:raise InputError('INCOMPATIBLE_ADAPTER: 不支持的数据适配版本')
    validate_input(data)
    if context.get('execution_checkpoint'):
        from execution_checkpoint import check_projection
        check_projection(data,context['execution_checkpoint'])
    for source in data['supply_sources']:
        if not (datetime.fromisoformat(source['effective_from'])<=datetime.fromisoformat(data['planning_at']) and datetime.fromisoformat(source['effective_to'])>=datetime.fromisoformat(data['horizon']['end_at'])):
            raise InputError('UNSUPPORTED_SUPPLY_EFFECTIVITY: 供应关系必须覆盖整个规划范围')
    customers=context.get('customers',[]);links=context.get('demand_customer_links',{})
    if not isinstance(customers,list) or any(not isinstance(r,dict) or not isinstance(r.get('id'),str) for r in customers) or not isinstance(links,dict):
        raise InputError('CUSTOMER_MAPPING_GAP: 客户数据格式不完整')
    ids={r.get('id') for r in customers}
    if not ids or len(ids)!=len(customers) or any(not isinstance(x,str) or not x.strip() or x=='CUSTOMER-DEMO' for x in ids):
        raise InputError('CUSTOMER_MAPPING_GAP: 需要已确认的企业客户标识')
    if set(links)!={r['id'] for r in data['demand_lines']} or any(c not in ids for c in links.values()):
        raise InputError('CUSTOMER_MAPPING_GAP: 每个订单必须关联已确认客户')
    native=to_native(data)
    native['customer']=[{'name':r['id']} for r in customers]
    for row in native['demand']:row['customer']=links[row['name']]
    return native

def validate_version(version):
    if version.get('business_data',{}).get('schema_version')=='canonical-business-v1':
        from p22.foundation_projection import validate_facts
        return validate_facts(version)
    if not version.get('planning_ready'):raise InputError('PLANNING_NOT_READY: 请选择已经通过规划就绪检查的数据版本')
    confirmations=version.get('confirmations',{})
    if not GROUPS.issubset(confirmations) or any(not confirmations[g].get('reviewer') or not confirmations[g].get('content_sha256') for g in GROUPS):
        raise InputError('CONFIRMATION_REQUIRED: 需要完整的人工确认记录')
    if len({confirmations[g]['content_sha256'] for g in GROUPS})!=1:raise InputError('CONFIRMATION_REQUIRED: 人工确认不属于同一份资料')
    business=version.get('business_data',{})
    if version.get('version_mode')=='EXECUTION_CHECKPOINT' and not business.get('execution_checkpoint'):
        raise InputError('CHECKPOINT_EVIDENCE_MISSING: 派生规划版本缺少执行来源，不能按普通导入版本使用')
    if business.get('schema_version')!='p21-enterprise-import-1':raise InputError('INCOMPATIBLE_SCHEMA: 数据版本不兼容')
    if business.get('source_sha256')!=version.get('file_sha256') or not business.get('lineage'):
        raise InputError('SOURCE_EVIDENCE_MISSING: 缺少原文件与转换记录')
    native=enterprise_native(business['plan_input'],business)
    if native!=business.get('native_preview'):raise InputError('NATIVE_PREVIEW_MISMATCH: 数据与已确认的转换结果不一致')
    return native
