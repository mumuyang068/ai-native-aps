"""Bounded two-file source reconciliation inside the existing P2 import task.

Original workbooks, explicit identities and review are authoritative. No stock
events are synthesized and unsupported partial receipts never become PlanInput.
"""
import copy,hashlib,re
from decimal import Decimal,InvalidOperation
from datetime import datetime
from p2.preflight import load_workbook,digest,parsed,column

SPECIAL={'来源信息','物料关联','收货记录'}
def issue(code,message,cell='来源包',severity='BLOCKER'):
    return {'code':code,'severity':severity,'cell':cell,'message':message,'source_locations':[],
            'resolution':'核对两个原文件的业务时点、记录身份及关联依据；确认不能绕过业务冲突。'}
def indexed_rows(tab):
    return [(ri,dict(zip(tab['headers'],r))) for ri,r in enumerate(tab['rows']) if any(v is not None and v!='' for v in r)]
def table_rows(tab):return [r for _,r in indexed_rows(tab)]
def mapped_rows(task,target):
    for sheet,mapping in task['mappings'].items():
        if mapping['target_sheet']!=target:continue
        tab=task['source']['sheets'][sheet]
        for ri,row in enumerate(tab['rows']):
            if not any(v is not None and v!='' for v in row):continue
            yield sheet,ri,{mapping['columns'][h]:row[ci] if ci<len(row) else None for ci,h in enumerate(tab['headers']) if mapping['columns'].get(h)}
def source_for(task,sheet):
    link=task['source_package']['sheet_origins'][sheet]
    return next(f for f in task['source_package']['files'] if f['sha256']==link['file_sha256'])
def package_sources(files,root):
    from p21.validation import initial_mappings
    if len(files)!=2:raise ValueError('SOURCE_PACKAGE_LIMIT: 仅接收ERP与WMS各一份XLSX')
    infos=[];sheets={};mappings={};origins={};aux={};errors=[]
    if len({hashlib.sha256(b).hexdigest() for _,b in files})!=2:raise ValueError('DUPLICATE_SOURCE_FILE: 不能把同一原件作为两个来源')
    for filename,blob in files:
        filename=filename.replace('\\','/').split('/')[-1]
        if not filename.lower().endswith('.xlsx') or len(filename)>160 or any(ord(c)<32 for c in filename) or not 0<len(blob)<=10*1024*1024:raise ValueError('INVALID_FILE: 来源须为不超过10MB的XLSX')
        h=hashlib.sha256(blob).hexdigest();p=root/(h+'.xlsx')
        if p.exists():
            if p.read_bytes()!=blob:raise ValueError('SOURCE_CHANGED: 已有原件哈希冲突')
        else:
            with p.open('xb') as stream:stream.write(blob)
        book=load_workbook(p,'detect');meta={}
        if len(book['sheets'])>40:raise ValueError('INVALID_FILE: 单来源最多40张表')
        for row in table_rows(book['sheets'].get('来源信息',{'headers':[],'rows':[]})):
            key,value=row.get('项目'),row.get('内容')
            if key in meta:errors.append(issue('SOURCE_METADATA_CONFLICT','来源信息中的同一字段重复，不能自动选值',filename))
            meta[key]=value
            if key not in {'来源系统','来源类型','业务快照时点','状态有效时点','导出时间','来源版本'} and value is not None and value!='':errors.append(issue('SOURCE_METADATA_UNSUPPORTED','来源信息有未解释字段，不能静默忽略：'+str(key),filename))
        info={'filename':filename,'sha256':h,'source_system':meta.get('来源系统'),'source_type':meta.get('来源类型'),
              'source_version':meta.get('来源版本'),'business_as_of':meta.get('业务快照时点'),
              'state_effective_at':meta.get('状态有效时点'),'exported_at':meta.get('导出时间'),'metadata':meta}
        infos.append(info);aux[h]={name:copy.deepcopy(tab) for name,tab in book['sheets'].items() if name in SPECIAL}
        permitted={'物料关联':{'来源物料编码','统一物料编码','来源记录','依据'},'收货记录':{'收货记录号','采购单号','来源物料编码','数量','单位','实际收货时刻','质量状态','已计入库存','库存记录编码','快照内数量','来源记录','来源版本'}}
        for special,tab in aux[h].items():
            if tab.get('formulas'):errors.append(issue('SOURCE_AUXILIARY_FORMULA_UNSUPPORTED','来源关联/状态表含未核验公式，不能采用缓存值',filename+' / '+special))
            if special not in permitted:continue
            for ci,header in enumerate(tab['headers']):
                if header not in permitted[special] and any(ci<len(r) and r[ci] is not None and r[ci]!='' for r in tab['rows']):errors.append(issue('SOURCE_AUXILIARY_FIELD_UNSUPPORTED','来源关联表有未解释的非空字段：'+str(header),filename+' / '+special))
        namespace=info['source_system'] if isinstance(info['source_system'],str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',info['source_system']) else '未声明来源-'+h[:10]
        for name,tab in book['sheets'].items():
            if name in SPECIAL:continue
            qualified=namespace+'::'+name
            if qualified in sheets:qualified=namespace+'-'+h[:8]+'::'+name
            sheets[qualified]=copy.deepcopy(tab);m=initial_mappings({'sha256':h,'sheets':{name:tab}})[name]
            mappings[qualified]=m;origins[qualified]={'file_sha256':h,'source_sheet':name}
    infos.sort(key=lambda f:(str(f['source_type']),f['sha256']))
    fingerprint=digest(sorted(f['sha256'] for f in infos))
    package={'schema_version':'two-source-package-v1','fingerprint':fingerprint,'files':infos,'sheet_origins':origins,'auxiliary_tables':aux,'initial_issues':errors}
    task={'filename':' + '.join(f['filename'] for f in infos),'file_sha256':fingerprint,'source_package':package,
          'source':{'file':'来源包','sha256':fingerprint,'sheets':sheets},'mappings':mappings}
    masters={str(r['记录编码']) for _,_,r in mapped_rows(task,'产品物料')}
    associations=[]
    for sheet,_,row in mapped_rows(task,'库存'):
        info=source_for(task,sheet);code=str(row.get('产品物料编码',''));key=(info['source_system'],code)
        if any((a['source_system'],a['source_code'])==key for a in associations):continue
        tab=aux[info['sha256']].get('物料关联',{'headers':[],'rows':[]});evidence=[]
        for ri,r in indexed_rows(tab):
            if str(r.get('来源物料编码'))==code:
                evidence.append({'target_id':str(r.get('统一物料编码','')),'record_key':r.get('来源记录'),'reason':r.get('依据'),
                                 'file_sha256':info['sha256'],'source_sheet':'物料关联','source_cell':'A'+str(tab['header_row']+ri+1)})
        candidates=sorted({r['target_id'] for r in evidence if r['target_id'] in masters})
        if not candidates and code in masters:candidates=[code]
        associations.append({'source_system':info['source_system'],'source_code':code,'candidate_targets':candidates,'evidence':evidence,
                             'target_id':candidates[0] if len(candidates)==1 else None,'confirmed':False,
                             'reason':'原件中的明确物料对照，仍需核对' if evidence else '同码也仅为候选；须核对物料身份' if candidates else '缺少明确物料对照'})
    task['material_associations']=associations
    return task

def prepare_validation(original):
    task=copy.deepcopy(original);package=task['source_package'];errors=copy.deepcopy(package['initial_issues']);infos=package['files']
    def bad(code,msg,cell='来源包'):errors.append(issue(code,msg,cell))
    def date(value,where):
        try:return datetime.fromisoformat(parsed(value,'datetime_offset').replace('Z','+00:00'))
        except (ValueError,TypeError):bad('SOURCE_TIME_UNCONFIRMED','来源时间必须有明确时区和具体业务时点',where);return None
    settings={r.get('字段路径'):r.get('确认值') for _,_,r in mapped_rows(task,'工厂参数')}
    planning=date(settings.get('planning_at'),'工厂参数')
    if sorted(str(f['source_type']) for f in infos)!=['ERP','WMS']:bad('SOURCE_ROLES_REQUIRED','限定来源包需要一份ERP和一份WMS，不按文件名猜测角色')
    systems=[f['source_system'] for f in infos]
    if len(set(systems))!=2:bad('SOURCE_SYSTEM_CONFLICT','来源系统身份必须明确且不同')
    for f in infos:
        if not f['source_system'] or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',str(f['source_system'])) or not f['source_version']:bad('SOURCE_IDENTITY_REQUIRED','缺少明确来源系统或来源版本',f['filename'])
        asof=date(f['business_as_of'],f['filename']);effective=date(f['state_effective_at'],f['filename']);exported=date(f['exported_at'],f['filename'])
        if asof and planning and asof!=planning:bad('SOURCE_SNAPSHOT_MISMATCH','业务快照时点与规划时点不一致；导出时间相同不能代替快照一致',f['filename'])
        if effective and asof and effective>asof:bad('SOURCE_STATE_TIME_CONFLICT','业务状态有效时点晚于快照，不能作为该时点已知状态',f['filename'])
        if exported and asof and exported<asof:bad('SOURCE_EXPORT_TIME_CONFLICT','导出时间早于所声明业务快照',f['filename'])
    masters={str(r['记录编码']) for _,_,r in mapped_rows(task,'产品物料')}
    associations={(a['source_system'],a['source_code']):a for a in task['material_associations']}
    for a in associations.values():
        if not a.get('confirmed') or not a.get('reviewer') or not a.get('at'):
            bad('MATERIAL_ASSOCIATION_REQUIRED','物料 '+a['source_code']+' 的跨系统关联尚未核对')
        if a.get('confirmed') and a.get('target_id') not in masters:bad('MATERIAL_ASSOCIATION_INVALID','关联目标不是ERP物料主档中的稳定身份')
    seen=set()
    for target in ['产品物料','订单','库存','采购台账']:
        for sheet,ri,row in mapped_rows(task,target):
            info=source_for(task,sheet);key=(target,info['source_system'],row.get('来源记录'),row.get('来源版本'),info['business_as_of'])
            if not row.get('来源记录') or row.get('来源版本')!=info['source_version']:bad('SOURCE_RECORD_IDENTITY_REQUIRED','记录必须有来源业务键及对应来源版本',sheet)
            if key in seen:bad('DUPLICATE_SOURCE_RECORD','同一来源业务记录重复，不能形成重复需求或供给',sheet)
            seen.add(key)
            if (target=='库存')!=(info['source_type']=='WMS'):bad('SOURCE_ROLE_CONFLICT','本轮库存只来自WMS；订单、物料及采购主数据来自ERP',sheet)
            if target=='库存':
                if date(row.get('库存时点'),sheet)!=planning:bad('SOURCE_SNAPSHOT_MISMATCH','库存余额时点不能证明属于当前规划快照',sheet)
                a=associations.get((info['source_system'],str(row.get('产品物料编码'))))
                # A unique explicit candidate may be shown in validation, but the
                # MATERIAL_ASSOCIATION_REQUIRED blocker prevents saving before review.
                target_id=a.get('target_id') if a and (a.get('confirmed') or len(a.get('candidate_targets',[]))==1) else None
                if target_id:
                    tab=task['source']['sheets'][sheet];header=next(h for h,v in task['mappings'][sheet]['columns'].items() if v=='产品物料编码')
                    tab['rows'][ri][tab['headers'].index(header)]=target_id
    stocks={str(row.get('记录编码')):row for _,_,row in mapped_rows(task,'库存')}
    receipts=[]
    for f in infos:
        table=package['auxiliary_tables'][f['sha256']].get('收货记录',{'headers':[],'rows':[]})
        for ri,r in indexed_rows(table):
            if f['source_type']!='WMS':bad('RECEIPT_SOURCE_ROLE_CONFLICT','本轮实际收货关联必须来自WMS原件')
            if not r.get('来源记录') or r.get('来源版本')!=f['source_version']:bad('SOURCE_RECORD_IDENTITY_REQUIRED','收货记录缺少对应来源业务键/版本')
            receipts.append({**r,'file_sha256':f['sha256'],'source_system':f['source_system'],'source_sheet':'收货记录','source_row':table['header_row']+ri+1})
    po_rows=[(sheet,row) for sheet,_,row in mapped_rows(task,'采购台账')];poids={str(row.get('采购单号')) for _,row in po_rows}
    for r in receipts:
        if str(r.get('采购单号')) not in poids:bad('RECEIPT_REFERENCE_CONFLICT','收货记录引用的采购单不属于本来源包')
        key=(r['source_system'],r.get('来源记录'),r.get('来源版本'))
        if key in seen:bad('DUPLICATE_SOURCE_RECORD','收货记录身份重复')
        seen.add(key)
        if not r.get('来源记录') or not r.get('收货记录号') or not r.get('库存记录编码'):bad('RECEIPT_EVIDENCE_REQUIRED','收货须有稳定身份及明确库存记录引用')
        when=date(r.get('实际收货时刻'),'收货记录')
        if when and planning and when>planning:bad('RECEIPT_AFTER_SNAPSHOT','未来收货不能算作期初库存')
    purchases=[]
    def amount(v):
        try:
            n=Decimal(str(v));assert n.is_finite() and n>=0;return n
        except (InvalidOperation,ValueError,AssertionError):bad('SOURCE_QUANTITY_UNCONFIRMED','来源数量缺失或非法，不能按零补齐');return None
    for sheet,p in po_rows:
        total,received,remaining=[amount(p.get(k)) for k in ['订购量','已收量','未收量']]
        if any(n is None for n in [total,received,remaining]):continue
        if total!=received+remaining:bad('SOURCE_PO_QUANTITY_CONFLICT','采购订购、已收、未收数量不能平衡',sheet)
        if p.get('状态')!='confirmed':bad('SOURCE_PO_STATUS_UNSUPPORTED','无法作为本轮confirmed采购解释的业务状态',sheet)
        linked=[r for r in receipts if str(r.get('采购单号'))==str(p.get('采购单号'))]
        receipt_quantities=[amount(r.get('数量')) for r in linked]
        receipt_total=None if any(v is None for v in receipt_quantities) else sum(receipt_quantities,Decimal(0))
        matched={};link_checks=[];counted_once=received==0 and not linked
        if received>0 or linked:
            if receipt_total!=received:bad('RECEIPT_QUANTITY_CONFLICT','ERP已收数量与明确关联的WMS收货数量不一致',sheet)
            for r in linked:
                stock=stocks.get(str(r.get('库存记录编码')));a=associations.get((r['source_system'],str(r.get('来源物料编码'))))
                retained=amount(r.get('快照内数量'));quantity=amount(r.get('数量'))
                stock_quantity=amount(stock.get('实存数量')) if stock else None
                ok=bool(stock and a and quantity is not None and retained is not None and stock_quantity is not None and stock_quantity>=retained and a.get('target_id')==p.get('物料编码')==stock.get('产品物料编码') and r.get('已计入库存') is True and retained==quantity and str(r.get('单位'))==str(p.get('单位'))==str(stock.get('数量单位')))
                if not ok:bad('RECEIPT_STOCK_LINK_UNCONFIRMED','不能证明收货数量已包含在指定库存快照中；禁止按时间或数量相近关联')
                link_checks.append(ok)
                if stock:matched[str(r['库存记录编码'])]=stock
                if r.get('质量状态') not in ['质检冻结','已放行']:bad('RECEIPT_QUALITY_UNKNOWN','收货质量状态缺少明确解释')
                if stock and quantity is not None and r.get('质量状态')=='质检冻结' and amount(stock.get('质检冻结量'))!=quantity:bad('RECEIPT_QUALITY_CONFLICT','收货冻结量与库存冻结事实不符')
            if p.get('库存已含收货') is not True:bad('RECEIPT_INVENTORY_CONFLICT','ERP已有收货但未确认其库存归属，不能再计为未来供应')
            counted_once=bool(linked and all(link_checks) and receipt_total==received and len({r.get('收货记录号') for r in linked})==len(linked))
            bad('PARTIAL_RECEIPT_PROFILE_UNSUPPORTED','已收/未收事实保留，但当前profile不支持此部分收货组合，不能改成新完整未收PO')
        elif p.get('库存已含收货') is not False:bad('RECEIPT_INVENTORY_CONFLICT','完整未收采购必须明确未计入期初库存')
        def stock_total(field):
            values=[amount(r.get(field)) for r in matched.values()]
            return None if any(v is None for v in values) else sum(values,Decimal(0))
        physical,blocked,unavailable=[stock_total(k) for k in ['实存数量','质检冻结量','其他不可用量']]
        net=None if any(v is None for v in [physical,blocked,unavailable]) else physical-blocked-unavailable
        text=lambda value:None if value is None else str(value)
        explanation=('已收与库存按明确收货/库存身份核对为同一事实，不能相加；未收量单列，当前规划不支持部分收货。' if counted_once else '已收、未收及库存原值保留；收货与库存关联尚未核实，不能相加或进入当前规划。') if received or linked else '已确认采购尚未收货，未加入期初库存；预计到货与QC可用时点仍分开。'
        purchases.append({'reference':p.get('采购单号'),'source_system':source_for(task,sheet)['source_system'],'ordered_quantity':str(total),'received_quantity':str(received),'remaining_quantity':str(remaining),'wms_receipt_quantity':text(receipt_total),'stock_counted_once':counted_once,'physical_stock_quantity':text(physical),'quality_blocked':text(blocked),'net_available':text(net),'receipts':linked,'explanation':explanation})
    inventory=[]
    for sheet,ri,row in mapped_rows(task,'库存'):
        rawtab=original['source']['sheets'][sheet];rawhead=next(h for h,v in original['mappings'][sheet]['columns'].items() if v=='产品物料编码')
        source_code=str(rawtab['rows'][ri][rawtab['headers'].index(rawhead)]);info=source_for(task,sheet)
        a=associations.get((info['source_system'],source_code),{})
        physical,blocked,other=[amount(row.get(k)) for k in ['实存数量','质检冻结量','其他不可用量']]
        net=None if any(v is None for v in [physical,blocked,other]) else physical-blocked-other
        inventory.append({'id':row.get('记录编码'),'source_item_id':source_code,'item_id':a.get('target_id') if a.get('confirmed') else None,'association_confirmed':bool(a.get('confirmed')),
                          'physical_quantity':None if physical is None else str(physical),'quality_blocked':None if blocked is None else str(blocked),'other_nonnettable':None if other is None else str(other),'net_available':None if net is None else str(net),
                          'uom':row.get('数量单位'),'as_of':row.get('库存时点'),'source_system':info['source_system'],'source_record_key':row.get('来源记录'),'file_sha256':info['sha256']})
    reconciliation={'status':'NEEDS_CONFIRMATION','planning_as_of':settings.get('planning_at'),'inventory':inventory,'purchases':purchases,'source_files':copy.deepcopy(infos),'material_targets':sorted(masters),'material_associations':copy.deepcopy(original['material_associations']),
                    'limits':['仅两份ERP/WMS合成导出，不是实时同步','部分收货只核对事实，不生成可规划版本','收货关联仅依据明确单据及库存记录键，不按时间或相同数量推断']}
    return task,errors,reconciliation

def decorate_business(original,business):
    package=copy.deepcopy(original['source_package'])
    package.pop('auxiliary_tables',None);package.pop('initial_issues',None)
    package['material_associations']=copy.deepcopy(original['material_associations'])
    package['reconciliation']=copy.deepcopy(original['reconciliation'])
    package['confirmed_fingerprint']=digest({'files':package['files'],'mappings':original['mappings'],'associations':package['material_associations'],'confirmations':original['confirmations']})
    business['source_package']=package
    for entry in business['lineage']:
        locator=entry.get('source_cell','');sheet,_,ref=locator.rpartition('!')
        if sheet not in original['source_package']['sheet_origins']:continue
        link=original['source_package']['sheet_origins'][sheet];info=source_for(original,sheet)
        entry.update(file_sha256=info['sha256'],source_system=info['source_system'],source_sheet=link['source_sheet'],source_version=info['source_version'],business_as_of=info['business_as_of'])
        if entry['target'].endswith('/item_id'):
            tab=original['source']['sheets'][sheet];m=re.fullmatch(r'([A-Z]+)(\d+)',ref)
            if m:
                ci=0
                for c in m[1]:ci=ci*26+ord(c)-64
                ri=int(m[2])-tab['header_row']-1
                if 0<=ri<len(tab['rows']):
                    raw=tab['rows'][ri][ci-1]
                    if raw!=entry['raw_value']:entry['raw_value']=raw;entry['rule']+=';CONFIRMED_CROSS_SOURCE_MATERIAL_ASSOCIATION'
    def annotate(prov):
        sheet=prov['source_locator'].split('!',1)[0]
        if sheet in original['source_package']['sheet_origins']:
            info=source_for(original,sheet);prov.update(source_system=info['source_system'],source_version=info['source_version'],observed_at=info['business_as_of'])
    for collection in business['plan_input'].values():
        for obj in collection if isinstance(collection,list) else [collection]:
            if not isinstance(obj,dict):continue
            if 'provenance' in obj:annotate(obj['provenance'])
            if obj.get('receiving_qc'):annotate(obj['receiving_qc']['provenance'])

def original_sources(version,root,*,require_all=False):
    package=version['business_data']['source_package'];sheets={};statuses=[]
    if version.get('source_package')!=package:raise ValueError('VERSION_CHANGED: 来源包与冻结业务版本不一致')
    if digest(sorted(f['sha256'] for f in package['files']))!=version['file_sha256']:raise ValueError('VERSION_CHANGED: 来源包身份不一致')
    if digest({'files':package['files'],'mappings':version['mappings'],'associations':package['material_associations'],'confirmations':version['confirmations']})!=package['confirmed_fingerprint']:raise ValueError('VERSION_CHANGED: 来源、关联与确认指纹不一致')
    for info in package['files']:
        p=root/(info['sha256']+'.xlsx');state={**info,'status':'NOT_CHECKED'}
        if p.is_file():
            if hashlib.sha256(p.read_bytes()).hexdigest()!=info['sha256']:raise ValueError('SOURCE_CHANGED: 原始来源文件哈希不一致')
            choices={link['source_sheet']:version['mappings'][qualified]['header_row'] for qualified,link in package['sheet_origins'].items() if link['file_sha256']==info['sha256']}
            book=load_workbook(p,choices)
            for qualified,link in package['sheet_origins'].items():
                if link['file_sha256']==info['sha256']:sheets[qualified]=book['sheets'][link['source_sheet']]
            state['status']='HASH_VERIFIED'
        elif require_all:raise ValueError('SOURCE_FILE_NOT_FOUND: 规划前缺少来源原件')
        statuses.append(state)
    return {'sheets':sheets},statuses
