"""Project the user's explicit mappings/edits onto the P2.0 validator."""
import copy
import re
from datetime import datetime,timezone
from p2.preflight import PROFILE,PROFILE_SHA,inspect_tables,digest,column

GROUPS=['mappings','orders','production','inventory','purchasing','customers']
GROUP_NAMES={'mappings':'资料与字段对应','orders':'订单与交付要求','production':'材料、工序与生产能力','inventory':'库存、单位与预留','purchasing':'采购与供应关系','customers':'客户标识'}
ALIASES={'款号':'产品物料编码','交货时间':'承诺交期','订单编号':'订单号','客户编号':'客户编码'}
SHEET_ALIASES={'订单表':'订单','库存表':'库存','资源表':'生产资源','工艺表':'工艺路线','供应商表':'供应商','物料表':'产品物料'}
ALIASES.update({'需求行ID':'记录编码','库存记录ID':'记录编码','客户订单号':'订单号','订单行号':'行号',
 'SKU':'产品物料编码','物料号':'产品物料编码','需求数量':'数量','要求交付时间':'承诺交期','需求优先级':'优先级',
 '账面实物量':'实存数量','质检冻结数量':'质检冻结量','其他冻结数量':'其他不可用量','库存单位':'数量单位','快照时间':'库存时点',
 'PO号':'采购单号','采购物料号':'物料编码','供应商编号':'供应商编码','订购数量':'订购量','累计收货量':'已收量',
 '未收数量':'未收量','预计物理到货时间':'预计到货','质检后可用时刻':'质检后可用时间'})
SHEET_ALIASES.update({'销售订单明细':'订单','WMS库存余额':'库存','采购未收明细':'采购台账','物料清单':'BOM','工序明细':'工艺路线'})
RESOLUTIONS={
 'MISSING_REQUIRED':'请找对应业务负责人补充原值；空白不能当成零。',
 'INVALID_TYPE':'核对数值、具体时刻、时区或编码格式，按原始证据修改。',
 'MAPPING_CONFIRMATION_REQUIRED':'核对原列含义并选择对应字段；无法确定时保留待核对。',
 'EMPTY_ENTITY':'补齐此类资料，并确认每条记录与订单、物料和资源的关系。',
 'MISSING_SHEET':'上传或补充缺少的业务资料，不根据其他表猜测。',
 'UNIT_CONVERSION':'请仓库确认物料的原单位、基本单位及换算倍数。',
 'UNSUPPORTED_RESERVATION':'保留预留记录；当前缺少需求绑定与释放能力，补齐适配器前不能规划。',
 'UNSUPPORTED_OPEN_PURCHASE':'保留未结采购；现有适配器不能承接在途供应，不能删记录或重复增加库存。',
 'UNSUPPORTED_SUPPLY_EFFECTIVITY':'核对供应有效日期；未覆盖全范围时，需要供应有效期适配能力。',
 'CUSTOMER_MAPPING_GAP':'在“客户标识”中确认企业客户编码，不能使用 CUSTOMER-DEMO 占位值。',
 'DUPLICATE_ID':'保留两条原始记录，核实是否同一业务记录；更正编码或提供独立快照，不能累加。',
 'RECIPE_REFERENCE':'请生产负责人核对材料清单和工艺版本的配套关系。',
 'INVENTORY_SNAPSHOT':'请仓库提供与规划起点一致的库存快照。',
 'CALENDAR_OVERLAP':'请生产负责人核实重复时段；重叠工作时间不能重复增加产能。',
 'FORMULA_INPUT':'请提供已核对的值；公式原件保留，不使用可能过期的缓存结果。',
 'CAPACITY_BASIS_UNCONFIRMED':'日产量不能直接作为每件工时。请补充经核实的每件秒数和实际有效产能口径。',
}

def headers_for(target):
    if target in PROFILE['sheets']:
        c=PROFILE['sheets'][target]
        return [x['header'] for x in c['columns']]+c['extras']+c['source_columns']+c.get('optional_headers',[])
    if target in PROFILE['auxiliary']:
        c=PROFILE['auxiliary'][target];return c['headers']+c.get('optional_headers',[])
    return ['字段路径','确认值','来源记录','来源版本','来源说明'] if target=='工厂参数' else ['项目','内容']

def initial_mappings(source):
    out={}
    for name,tab in source['sheets'].items():
        target=name if name in PROFILE['sheets'] or name in PROFILE['auxiliary'] or name in ('使用说明','工厂参数') else SHEET_ALIASES.get(name)
        allowed=headers_for(target) if target else []
        out[name]={'target_sheet':target,'header_row':tab['header_row'],'columns':{
            h:(h if h in allowed else ALIASES.get(h) if ALIASES.get(h) in allowed else None) for h in tab['headers'] if h is not None},
            'method':'RULE','confirmed':False}
        out[name]['candidates']=[{'source_sheet':name,'source_column':h,'target_sheet':target,
          'target_field':field,'reason':'原字段名称一致' if h==field else '限定字段别名规则' if field else '没有明确对应，需人工核对含义',
          'requires_confirmation':True,'file_sha256':source.get('sha256'),
          'source_header_cell':name+'!'+column(tab['headers'].index(h)+1)+str(tab['header_row'])}
          for h,field in out[name]['columns'].items()]
    return out

def build_tables(task):
    tabs={};origins={};extra=[];links={};customers={}
    for name,tab in task['source']['sheets'].items():
        mapping=task['mappings'][name];target=mapping['target_sheet'] or '待核对:'+name
        if target in tabs:
            extra.append({'code':'MAPPING_CONFIRMATION_REQUIRED','severity':'BLOCKER','cell':name,'message':'多张表映射到同一业务对象，需要先核实合并规则，不能直接拼接。'});continue
        heads=[]
        for i,h in enumerate(tab['headers']):
            heads.append(mapping['columns'].get(h) or '未映射:'+str(h or column(i+1)))
            origins[(target,column(i+1))]=(name,column(i+1))
        rr=copy.deepcopy(tab['rows']);expected=headers_for(target)
        for h in ['来源记录','来源版本','来源说明','联系人']:
            if h in expected and h not in heads:
                heads.append(h)
                for ri,row in enumerate(rr):
                    if len(row)<len(heads)-1:row.extend([None]*(len(heads)-1-len(row)))
                    val=(f'{task["file_sha256"]}#{name}!{tab["header_row"]+ri+1}' if h=='来源记录' else f'upload:{task["file_sha256"][:12]}' if h=='来源版本' else '原始文件与人工修订来源由导入台账保留' if h=='来源说明' else None)
                    row.append(val)
        tabs[target]={'headers':heads,'rows':rr,'formulas':tab.get('formulas',[])}
        if target=='订单' and '客户编码' in heads and '记录编码' in heads:
            for row in rr:
                source_id=row[heads.index('客户编码')];demand=row[heads.index('记录编码')]
                match=task['customers'].get(str(source_id),{})
                if match.get('customer_id'):
                    links[str(demand)]=match['customer_id'];customers[match['customer_id']]={'id':match['customer_id'],'name':match.get('name') or match['customer_id']}
        for h in ('每件工时秒','有效并行数'):
            for source_col,target_col in mapping['columns'].items():
                if target_col==h and re.search('日产|每日|班产',str(source_col)):
                    extra.append({'code':'CAPACITY_BASIS_UNCONFIRMED','severity':'BLOCKER','cell':target,'message':'“'+source_col+'”不能直接映射为“'+h+'”，需要单独补充可信工时和产能口径。'})
    return {'file':task['filename'],'sha256':task['file_sha256'],'sheets':tabs},links,customers,extra

def evaluate(task):
    original_task=task;package_issues=[]
    if task.get('source_package'):
        from p21.source_package import prepare_validation
        task,package_issues,reconciliation=prepare_validation(task)
    tables,links,customers,extra=build_tables(task)
    confirmed=all(g in task['confirmations'] for g in GROUPS)
    approval=None
    if confirmed:
        approval={'scope':'ENTERPRISE_IMPORT','reviewer':task['confirmations']['mappings']['reviewer'],
            'reviewed_at':task['confirmations']['mappings']['at'],'file_sha256':tables['sha256'],
            'mapping_sha256':PROFILE_SHA,'content_sha256':digest(tables['sheets']),
            'all_mapped_and_policy_fields_reviewed':True}
    try:report=inspect_tables(tables,approval,customer_links=links)
    except (ValueError,KeyError,TypeError,IndexError) as exc:
        report={'status':'BLOCKED','issues':[{'code':'CANONICAL_VALIDATION','severity':'BLOCKER','cell':'workbook','message':'资料未能转换：'+str(exc)[:300]}],'lineage':[],'retained':{}}
    report['issues'].extend(extra);report['issues'].extend(package_issues)
    inverse={m['target_sheet']:name for name,m in task['mappings'].items() if m['target_sheet']}
    for item in report['issues']:
        cell=item['cell'];sheet=cell.split('!')[0];original=inverse.get(sheet,sheet)
        tab=task['source']['sheets'].get(original)
        locations=[]
        suffix=cell.split('!',1)[1] if '!' in cell else ''
        if tab:
            match=re.fullmatch(r'([A-Z]*)(\d+)',suffix)
            if match:
                row=int(match[2]);row=tab['header_row'] if row==5 else row-5+tab['header_row']
                locations=[{'sheet':original,'cell':(match[1] or 'A')+str(row)}]
            else:locations=[{'sheet':original,'cell':'A'+str(tab['header_row']),'scope':'sheet'}]
            def field_cell(field,ri):
                source_field=next((h for h,v in task['mappings'][original]['columns'].items() if v==field),None)
                if source_field in tab['headers']:return {'sheet':original,'cell':column(tab['headers'].index(source_field)+1)+str(tab['header_row']+ri+1)}
            code=item['code']
            if code in ('INVENTORY_SNAPSHOT','UNSUPPORTED_RESERVATION'):
                ri=max(0,int(re.search(r'\d+$',suffix)[0])-6) if re.search(r'\d+$',suffix) else 0
                precise=field_cell('库存时点' if code=='INVENTORY_SNAPSHOT' else '预留数量',ri)
                if precise:locations=[precise]
            if code=='MAPPING_CONFIRMATION_REQUIRED':
                unmapped=[column(ci+1)+str(tab['header_row']) for ci,h in enumerate(tab['headers']) if not task['mappings'][original]['columns'].get(h)]
                if unmapped:locations=[{'sheet':original,'cell':c} for c in unmapped]
            if code=='RECIPE_REFERENCE':
                locations=[]
                for field,entity in [('工艺版本','工艺路线'),('BOM版本','BOM')]:
                    source_field=next((h for h,v in task['mappings'][original]['columns'].items() if v==field),None)
                    if source_field not in tab['headers']:continue
                    other=inverse.get(entity);valid=set()
                    if other:
                        other_tab=task['source']['sheets'][other];idheader=next((h for h,v in task['mappings'][other]['columns'].items() if v=='记录编码'),None)
                        if idheader in other_tab['headers']:valid={r[other_tab['headers'].index(idheader)] for r in other_tab['rows']}
                    for ri,row in enumerate(tab['rows']):
                        if row[tab['headers'].index(source_field)] not in valid:locations.append(field_cell(field,ri))
                if not locations:locations=[{'sheet':original,'cell':'A'+str(tab['header_row']),'scope':'sheet'}]
            if code=='CALENDAR_OVERLAP':
                start_field=next((h for h,v in task['mappings'][original]['columns'].items() if v=='工作开始'),None)
                end_field=next((h for h,v in task['mappings'][original]['columns'].items() if v=='工作结束'),None)
                if start_field in tab['headers'] and end_field in tab['headers']:
                    si,ei=tab['headers'].index(start_field),tab['headers'].index(end_field);previous=None;located=[]
                    for ri,row in sorted(enumerate(tab['rows']),key=lambda pair:str(pair[1][si])):
                        if previous is not None and row[si] is not None and str(row[si])<str(previous):located.append(field_cell('工作开始',ri))
                        previous=row[ei]
                    if located:locations=located
        else:locations=[{'sheet':original,'cell':None,'scope':'missing_or_model'}]
        item['source_locations']=locations;item['resolution']=RESOLUTIONS.get(item['code'],'核对原表中的业务关系与版本；修正后重新校验。')
    for entry in report.get('lineage',[]):
        sheet,ref=entry['cell'].split('!',1);original=inverse.get(sheet,sheet);tab=task['source']['sheets'].get(original)
        if tab:
            match=re.fullmatch(r'([A-Z]+)(\d+)',ref);r=int(match[2])-5+tab['header_row']
            entry['source_cell']=f'{original}!{match[1]}{r}'
    blocked=any(i['severity'] in ('BLOCKER','CAPABILITY') for i in report['issues'])
    ready=confirmed and not blocked and 'canonical' in report
    if task.get('source_package'):
        hard=[i for i in package_issues if i['code'] not in {'MATERIAL_ASSOCIATION_REQUIRED'}]
        reconciliation['status']='READY' if ready else 'NOT_READY_FOR_PLANNING' if hard or (blocked and not any(i['code']=='MATERIAL_ASSOCIATION_REQUIRED' for i in package_issues)) else 'NEEDS_CONFIRMATION'
        original_task['reconciliation']=reconciliation
    business=None
    if ready:
        def source_location(prov):
            locator=prov['source_locator'];target=locator.split('!')[0];original=inverse.get(target,target)
            if original in task['source']['sheets']:
                delta=task['source']['sheets'][original]['header_row']-5
                parts=locator.split('!',1)
                if len(parts)==2:prov['source_locator']=original+'!'+re.sub(r'\d+',lambda m:str(int(m[0])+delta),parts[1])
        for collection in report['canonical'].values():
            for obj in collection if isinstance(collection,list) else [collection]:
                if not isinstance(obj,dict) or 'provenance' not in obj:continue
                source_location(obj['provenance'])
                if 'receiving_qc' in obj:source_location(obj['receiving_qc']['provenance'])
        business={'schema_version':'p21-enterprise-import-1','plan_input':report.pop('canonical'),
                  'native_preview':report.pop('native'),'customers':list(customers.values()),
                  'demand_customer_links':links,'lineage':report.get('lineage',[]),
                  'adapter_profile':'p21-customer-aware-static-v1','source_sha256':task['file_sha256']}
        for d in business['plan_input']['demand_lines']:
            if d.get('kind')=='NET_FORECAST':business['demand_customer_links'][d['id']]=d['customer_id']
        if task.get('source_package'):
            from p21.source_package import decorate_business
            decorate_business(original_task,business)
    report.pop('canonical',None);report.pop('native',None)
    return report,{'ready':ready,'confirmed':confirmed,'blocking_count':sum(i['severity'] in ('BLOCKER','CAPABILITY') for i in report['issues']),
        'scope':'STATIC_INPUT_VALIDATED','engine_executed':False},business

def now():return datetime.now(timezone.utc).isoformat()
