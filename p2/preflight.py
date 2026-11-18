"""Offline P2.0 sample contract validator, not a general Excel importer.

Reads the actual OOXML source. Never supplies missing business facts from fixtures.
No HTTP, model API, database writes, solver calls or formal plan mutations.
"""
import argparse
import copy
import hashlib
import json
import posixpath
import re
import zipfile
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree as ET
from planning_adapter import validate_input, to_native, InputError

HERE=Path(__file__).resolve().parent
PROFILE_BYTES=(HERE/'contracts/mapping.json').read_bytes()
PROFILE=json.loads(PROFILE_BYTES)
PROFILE_SHA=hashlib.sha256(PROFILE_BYTES).hexdigest()
NS={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}

def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def column(n):
    out=''
    while n:n,r=divmod(n-1,26);out=chr(65+r)+out
    return out

def load_workbook(path,header_rows=None):
    path=Path(path)
    sheets={}
    with zipfile.ZipFile(path) as z:
        if sum(i.file_size for i in z.infolist())>15_000_000:raise ValueError('Sample workbook exceeds offline validation size limit')
        shared=[]
        if 'xl/sharedStrings.xml' in z.namelist():
            shared=[''.join(s.itertext()) for s in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si',NS)]
        rels={r.attrib['Id']:r.attrib['Target'] for r in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
        for s in ET.fromstring(z.read('xl/workbook.xml')).findall('m:sheets/m:sheet',NS):
            name=s.attrib['name'];rid=s.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']
            target=rels[rid]
            target=target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/'+target)
            tree=ET.fromstring(z.read(target)); cells={};formulas=[]
            for c in tree.findall('.//m:sheetData/m:row/m:c',NS):
                ref=c.attrib['r'];t=c.attrib.get('t');v=c.find('m:v',NS)
                if c.find('m:f',NS) is not None:formulas.append(ref)
                if t=='inlineStr':
                    inline=c.find('m:is',NS)
                    val=''.join(inline.itertext()) if inline is not None else None
                elif v is None or v.text is None:val=None
                elif t=='s':val=shared[int(v.text)]
                elif t=='b':val=v.text=='1'
                elif t in ('str','e','d'):val=v.text
                else:
                    number=Decimal(v.text);val=int(number) if number==int(number) else float(number)
                cells[ref]=val
            row_choice=(header_rows or {}).get(name,5) if isinstance(header_rows,dict) else 5
            if header_rows=='detect':
                known={c['header'] for conf in PROFILE['sheets'].values() for c in conf['columns']}
                known.update(['字段路径','确认值','来源记录','来源版本','来源说明','项目','内容','采购单号','已收量','未收量','物料编码','换算倍数','款号','交货时间','日产量','编码'])
                scores={}
                for ref,v in cells.items():
                    r=int(re.search(r'\d+$',ref)[0])
                    if r<=30 and v is not None:scores[r]=scores.get(r,0)+(8 if str(v) in known else 1)
                row_choice=max(scores,key=lambda r:(scores[r],-r)) if scores else 1
            if not isinstance(row_choice,int) or not 1<=row_choice<=100:raise ValueError('表头行必须在 1 到 100 之间')
            headercols=[]
            for ref in cells:
                match=re.fullmatch(r'([A-Z]+)'+str(row_choice),ref)
                if match and cells[ref] is not None:
                    n=0
                    for char in match[1]:n=n*26+ord(char)-64
                    headercols.append(n)
            if not headercols:
                if header_rows is None:raise ValueError(f'{name}: missing explicit row 5 header')
                sheets[name]={'headers':[],'rows':[],'formulas':formulas,'header_row':row_choice,'cells':cells};continue
            width=max(headercols)
            maxrow=max((int(re.search(r'\d+$',r)[0]) for r,v in cells.items() if v is not None),default=row_choice)
            maxcol=max((sum((ord(c)-64)*26**i for i,c in enumerate(re.match(r'[A-Z]+',ref)[0][::-1])) for ref,v in cells.items() if v is not None),default=width)
            if maxrow>20000 or maxcol>150:raise ValueError('单表最多 20000 行、150 列；请拆分资料后导入')
            width=max(width,maxcol);headers=[cells.get(f'{column(j)}{row_choice}') for j in range(1,width+1)]
            rows=[[cells.get(f'{column(j)}{i}') for j in range(1,width+1)] for i in range(row_choice+1,maxrow+1)]
            sheets[name]={'headers':headers,'rows':rows,'formulas':formulas}
            if header_rows is not None:sheets[name].update(header_row=row_choice,cells=cells)
    return {'file':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'sheets':sheets}

def sample_review(tables):
    """Explicit SIMULATION ONLY acceptance record, never business user approval."""
    return {'reviewer':'SIMULATED_FACTORY_REVIEWER','reviewed_at':'2026-10-14T09:00:00+08:00',
            'scope':'SYNTHETIC_ONLY','file_sha256':tables['sha256'],'mapping_sha256':PROFILE_SHA,
            'content_sha256':digest(tables['sheets']),'all_mapped_and_policy_fields_reviewed':True,
            'roles':['SIMULATED_PRODUCTION_MANAGER','SIMULATED_WAREHOUSE','SIMULATED_BUYER']}

def parsed(value, typ):
    if value is None or value=='':
        if typ=='nullable_string':return None
        raise ValueError('必填值空白，不能视为零')
    if typ=='boolean':
        if type(value) is not bool:raise ValueError('必须明确为 TRUE/FALSE')
        return value
    if typ in ('integer','decimal_string'):
        if type(value) is bool:raise ValueError('布尔值不是业务数量')
        d=Decimal(str(value))
        if not d.is_finite():raise ValueError('数量必须为有限数值')
        if typ=='integer':
            if d!=d.to_integral_value():raise ValueError('必须是整数')
            return int(d)
        return format(d.normalize(),'f')
    if typ=='datetime_offset':
        # Explicit year/month/day and offset only. Never infer locale or timezone.
        if isinstance(value,str) and re.fullmatch(r'\d{4}/\d\d/\d\d \d\d:\d\d:\d\d [+-]\d\d:\d\d',value):
            value=value[:10].replace('/','-')+'T'+value[11:19]+value[20:]
        if isinstance(value,str) and re.fullmatch(r'\d{4}-\d\d-\d\d \d\d:\d\d:\d\d〔[+-]\d\d:\d\d〕',value):
            value=value[:19].replace(' ','T')+value[20:-1]
        if not isinstance(value,str) or not re.search(r'(Z|[+-]\d\d:\d\d)$',value):raise ValueError('时间必须包含时区与具体时刻，不能猜测日期的截止时间')
        dt=datetime.fromisoformat(value.replace('Z','+00:00'))
        if dt.utcoffset() is None:raise ValueError('缺少时区')
        return value
    if typ=='list':
        v=json.loads(value)
        if not isinstance(v,list) or any(not isinstance(x,str) for x in v):raise ValueError('前置工序必须为编码数组')
        return v
    if not isinstance(value,str):raise ValueError('编码或文本须为文本，禁止丢失前导零')
    return value

def assign(obj,path,value):
    bits=path.split('.')
    for k in bits[:-1]:obj=obj.setdefault(k,{})
    obj[bits[-1]]=value

def inspect_tables(tables,approval=None,*,customer_links=None):
    result={'file':tables['file'],'file_sha256':tables['sha256'],'mapping_version':PROFILE['version'],
            'mapping_sha256':PROFILE_SHA,'evidence_scope':'OFFLINE_SYNTHETIC_CONTRACT_ONLY',
            'issues':[],'lineage':[],'retained':{},'archived_purchases':[],
            'engine_executed':False,'ai_model_called':False}
    issues=result['issues'];tabs=tables['sheets'];candidate={};parsed_rows={};settings={}
    def issue(code,message,cell,level='BLOCKER'):
        issues.append({'code':code,'severity':level,'cell':cell,'message':message})
    def lineage(sheet,row,col,raw,value,target,rule='EXPLICIT_VALUE'):
        result['lineage'].append({'file_sha256':tables['sha256'],'cell':f'{sheet}!{column(col+1)}{row+6}',
            'raw_value':raw,'normalized_value':copy.deepcopy(value),'target':target,'rule':rule,'mapping_version':PROFILE['version']})
    def rows(sheet,expected):
        if sheet not in tabs:
            issue('MISSING_SHEET','缺少业务表',sheet);return []
        tab=tabs[sheet];heads=tab['headers']
        optional=set(PROFILE['auxiliary'].get(sheet,{}).get('optional_headers',[]))|set(PROFILE['sheets'].get(sheet,{}).get('optional_headers',[]))
        if len(heads)!=len(set(heads)) or set(expected)-set(heads) or set(heads)-set(expected)-optional:
            issue('MAPPING_CONFIRMATION_REQUIRED',f'缺失/未知/重名表头；应人工确认映射。缺少 {sorted(set(expected)-set(heads),key=str)}，新增 {sorted(set(heads)-set(expected),key=str)}',sheet+'!5')
            return []
        if tab.get('formulas'):
            issue('FORMULA_INPUT','本轮不计算企业公式；请提供核对后的值并保留公式原件',sheet+'!'+','.join(tab['formulas']))
        out=[]
        for ri,row in enumerate(tab['rows']):
            if not any(v is not None and v!='' for v in row):continue
            item=dict(zip(heads,row));out.append((ri,item))
            for key in ('来源记录','来源版本','来源说明'):
                if not item.get(key):issue('MISSING_SOURCE','必须保留来源记录、版本与说明',f'{sheet}!{ri+6}')
        result['retained'][sheet]=[copy.deepcopy(item) for _,item in out]
        return out
    unknown=set(tabs)-set(PROFILE['sheets'])-set(PROFILE['auxiliary'])-{'使用说明','工厂参数'}
    if unknown:issue('MAPPING_CONFIRMATION_REQUIRED','未映射的工作表需要人工判断：'+','.join(sorted(unknown)),'workbook')
    for ri,row in rows('工厂参数',['字段路径','确认值']+['来源记录','来源版本','来源说明']):
        key=row['字段路径']
        if key not in PROFILE['settings']:issue('UNKNOWN_POLICY','未知参数不能默默忽略',f'工厂参数!A{ri+6}');continue
        if key in settings:issue('DUPLICATE_ID','重复参数',f'工厂参数!A{ri+6}')
        try:
            val=parsed(row['确认值'],PROFILE['settings'][key]['type']);settings[key]=val
            lineage('工厂参数',ri,1,row['确认值'],val,'/'+key.replace('.','/'))
            if not key.startswith('p2.'):assign(candidate,key,val)
        except (ValueError,InvalidOperation) as e:issue('MISSING_REQUIRED' if row['确认值'] is None else 'INVALID_TYPE',str(e),f'工厂参数!B{ri+6}')
    for key in PROFILE['settings']:
        if key not in settings and not PROFILE['settings'][key].get('optional'):issue('MISSING_REQUIRED','缺少明确参数 '+key,'工厂参数')
    for sheet,conf in PROFILE['sheets'].items():
        cols=conf['columns'];headers=[c['header'] for c in cols]+conf['extras']+conf['source_columns']
        rr=rows(sheet,headers);groups={};data=[]
        distribution_empty=(settings.get('rules.manufacturing_profile')=='single-lane-distribution-v1' and conf['entity'] in {'calendars','resources','bom_revisions','routing_revisions','production_recipes','suppliers','supply_sources'}) or (settings.get('rules.manufacturing_profile')=='network-distribution-confirmed-supply-v1' and conf['entity'] in {'calendars','resources','bom_revisions','routing_revisions','production_recipes'})
        if not rr and not distribution_empty and not(settings.get('rules.manufacturing_profile') in {'fixed-batch-window-v1','two-level-wip-v1','mixed-fixed-batch-wip-v1','machine-qualified-operator-v1','forecast-net-demand-v1'} and conf['entity'] in {'suppliers','supply_sources'}):issue('EMPTY_ENTITY','缺少可读取的业务记录',sheet)
        for ri,row in rr:
            flat={}
            for c in cols:
                raw=row[c['header']];ci=tabs[sheet]['headers'].index(c['header'])
                if c['field']=='parallel_units' and settings.get('rules.manufacturing_profile') in {'mixed-fixed-batch-wip-v1','mixed-fixed-batch-confirmed-supply-v1','mixed-fixed-batch-confirmed-qualified-v1','mixed-fixed-batch-confirmed-cut-qualified-v1','finished-garment-confirmed-v1','enterprise-forecast-fg-v1','enterprise-accessory-supply-v1'} and row.get('产能模型')=='WINDOW_QUANTITY' and raw in (None,''):continue
                if c['field']=='parallel_units' and settings.get('rules.manufacturing_profile') in {'fixed-batch-window-v1','fixed-batch-confirmed-supply-v1','fixed-batch-priority-supply-v1'} and raw in (None,''):continue
                try:
                    val=parsed(raw,c['type']);flat[c['field']]=val
                    lineage(sheet,ri,ci,raw,val,c['target'])
                except (ValueError,InvalidOperation,TypeError) as e:
                    issue('MISSING_REQUIRED' if raw is None else 'INVALID_TYPE',str(e),f'{sheet}!{column(ci+1)}{ri+6}')
            if 'id' not in flat:continue
            nested=conf['nested'];parent={k:v for k,v in flat.items() if '.' not in k};ident=flat['id']
            if ident in groups:
                if not nested:issue('DUPLICATE_ID','重复业务编码，禁止最后一行覆盖',f'{sheet}!A{ri+6}');continue
                previous={k:v for k,v in groups[ident].items() if k not in (nested,'provenance')}
                if previous!=parent:issue('REVISION_CONFLICT','同版本的主信息不一致',f'{sheet}!{ri+6}')
                obj=groups[ident]
            else:
                obj=parent;groups[ident]=obj;data.append(obj)
                obj['provenance']={'source_system':'SYNTHETIC_FACTORY','source_record_id':str(row['来源记录']),
                    'source_version':str(row['来源版本']),'observed_at':settings.get('planning_at',''),
                    'import_batch_id':'P2-'+tables['sha256'][:12],
                    'source_locator':f'{sheet}!A{ri+6}:{column(len(headers))}{ri+6}'}
                if nested:obj[nested]=[]
            if nested:obj[nested].append({k.split('.',1)[1]:v for k,v in flat.items() if k.startswith(nested+'.')})
            for extra in conf['extras']:
                lineage(sheet,ri,tabs[sheet]['headers'].index(extra),row[extra],row[extra],'/retained/'+sheet+'/'+extra,'RETAIN_WITHOUT_NATIVE_MAPPING')
            if sheet=='订单' and not row['联系人']:issue('OPTIONAL_MISSING','联系人可稍后补充，不阻止数量/交期计算',f'{sheet}!{ri+6}','WARNING')
            parsed_rows.setdefault(sheet,[]).append((ri,row,obj))
        candidate[conf['entity']]=data
    # Stable line IDs and explicit source record keys are authoritative. Shared
    # display order/line references alone do not prove that two demands are one.
    seen_lines=set()
    for ri,row,obj in parsed_rows.get('订单',[]):
        key=(row.get('来源版本'),row.get('来源记录'),row.get('客户编码'),obj.get('order_ref'),obj.get('line_ref'))
        if key in seen_lines:issue('DUPLICATE_BUSINESS_RECORD','同一来源业务记录重复出现，不能因记录编码不同而重复计入','订单!'+str(ri+6))
        seen_lines.add(key)
    # Build and validate explicit item-scoped conversion factors.
    units={};itemmap={x.get('id'):x for x in candidate.get('items',[])}
    for ri,row in rows('单位换算',PROFILE['auxiliary']['单位换算']['headers']):
        try:
            key=(row['物料编码'],row['原单位']);factor=Decimal(str(row['换算倍数']))
            if key in units or not factor.is_finite() or factor<=0 or row['物料编码'] not in itemmap or row['基本单位']!=itemmap[row['物料编码']].get('base_uom'):
                raise ValueError('换算键唯一、倍数为正，且目标须匹配物料基本单位')
            units[key]=factor
            for ci,h in enumerate(tabs['单位换算']['headers']):
                lineage('单位换算',ri,ci,row[h],row[h],'/unit_conversion/'+h,'ITEM_SCOPED_CONFIRMED_FACTOR')
        except (ValueError,InvalidOperation) as e:issue('UNIT_CONVERSION',str(e),f'单位换算!{ri+6}')
    def convert(obj,fields,item,unit,sheet,ri):
        base=itemmap.get(item,{}).get('base_uom')
        factor=Decimal(1) if base==unit else units.get((item,unit))
        if factor is None or base is None:
            issue('UNIT_CONVERSION',f'缺少 {item} {unit} → {base} 的已确认换算',f'{sheet}!{ri+6}');return
        for f in fields:
            if f in obj:
                obj[f]=format((Decimal(obj[f])*factor).normalize(),'f')
        # Keep the original source cell and the normalized business quantity together.
        for entry in result['lineage']:
            if entry['cell'].startswith(sheet+'!') and re.search(r'\d+$',entry['cell'])[0]==str(ri+6):
                target=entry['target'].rsplit('/',1)[-1]
                if target in fields:
                    entry.update(normalized_value=obj.get(target),rule=f'{unit} × {factor} → {base}')
                elif target=='uom':
                    entry.update(normalized_value=base,rule=f'已确认 {unit} → {base}')
        return base
    for bom in candidate.get('bom_revisions',[]):
        # Rows correspond to explicit BOM line order; no recipe or material is invented.
        for line in bom.get('lines',[]):
            source=next((r for r in parsed_rows.get('BOM',[]) if r[1]['记录编码']==bom['id'] and r[1]['材料编码']==line.get('item_id')),None)
            if source:line['uom']=convert(line,['quantity_per_output'],line.get('item_id'),line.get('uom'),'BOM',source[0])
    for ri,row,obj in parsed_rows.get('库存',[]):
        oldunit=obj.get('uom');obj['uom']=convert(obj,['on_hand','quality_blocked','other_nonnettable'],obj.get('item_id'),oldunit,'库存',ri)
        try:
            reserved=Decimal(str(row['预留数量']))
            if not reserved.is_finite() or reserved<0:raise ValueError('预留必须是非负确定值')
            if reserved>0:issue('UNSUPPORTED_RESERVATION','保留硬预留原记录；现有适配器缺少需求绑定与释放规则，不能忽略或直接相减',f'库存!{ri+6}','CAPABILITY')
        except (ValueError,InvalidOperation) as e:issue('MISSING_REQUIRED',str(e),f'库存!{ri+6}')
        if obj.get('as_of')!=settings.get('planning_at'):issue('INVENTORY_SNAPSHOT','库存快照必须与规划时点一致',f'库存!{ri+6}')
    po_ids=set();confirmed_purchases=[]
    for ri,row in rows('采购台账',PROFILE['auxiliary']['采购台账']['headers']):
        try:
            if not row['采购单号'] or row['采购单号'] in po_ids:raise ValueError('采购单号必须非空且唯一')
            po_ids.add(row['采购单号'])
            qty,received,remaining=[Decimal(str(row[k])) for k in ('订购量','已收量','未收量')]
            if any(not x.is_finite() or x<0 for x in (qty,received,remaining)) or qty!=received+remaining:raise ValueError('订购量必须等于已收加未收，数量非负')
            if row['物料编码'] not in itemmap or row['供应商编码'] not in {x['id'] for x in candidate.get('suppliers',[])}:raise ValueError('采购物料或供应商无主档')
            if row['单位']!=itemmap[row['物料编码']].get('base_uom') and (row['物料编码'],row['单位']) not in units:raise ValueError('采购单位缺少已确认换算')
            parsed(row['预计到货'],'datetime_offset')
            if row['状态']=='confirmed':
                # Bounded extension; no quantities, dates, QC or confirmation
                # status are inferred from the algorithm's proposed purchases.
                multi=settings.get('rules.manufacturing_profile') in {'multi-receipt-protected-manufacturing-v1','routing-alternate-capacity-v1'}
                if not multi and len(confirmed_purchases):raise ValueError('本轮仅支持单笔完整未收 confirmed 采购')
                if qty<=0 or remaining<=0 or row['库存已含收货'] is not False or (not multi and (received!=0 or remaining!=qty)):raise ValueError('confirmed 数量必须对账且明确未重复包含收货；旧profile仍仅支持完整未收')
                if row['单位']!=itemmap[row['物料编码']]['base_uom']:raise ValueError('confirmed 采购仅支持明确的物料基本单位')
                source=next((s for s in candidate['supply_sources'] if s['id']==row.get('供应来源编码')),None)
                if source is None:raise ValueError('必须明确采购供应来源编码')
                qc=parsed(row.get('质检等待秒'),'integer')
                if qc<0:raise ValueError('质检等待秒不能为负')
                if multi and 'receiving_qc' in source and source['receiving_qc']['wait_seconds']!=qc:
                    issue('RECEIPT_QC_CONFLICT','同一供应来源的质检等待秒与前笔采购不一致；显式0也不能被后行覆盖',
                          '采购台账!'+column(tabs['采购台账']['headers'].index('质检等待秒')+1)+str(ri+6))
                    continue
                prov={'source_system':'ENTERPRISE_XLSX','source_record_id':str(row['来源记录']),
                    'source_version':str(row['来源版本']),'observed_at':settings['planning_at'],
                    'import_batch_id':'P2-'+tables['sha256'][:12],
                    'source_locator':f'采购台账!A{ri+6}:{column(len(tabs["采购台账"]["headers"]))}{ri+6}'}
                source['receiving_qc']={'wait_seconds':qc,'provenance':copy.deepcopy(prov)}
                confirmed_purchases.append({'type':'PO','id':str(row['采购单号']),'external_ref':str(row['采购单号']),
                    'item_id':row['物料编码'],'supplier_id':row['供应商编码'],'location_id':candidate['site']['id'],
                    'supply_source_id':source['id'],'ordered_quantity':format(qty,'f'),'received_quantity':format(received,'f'),'cancelled_quantity':'0',
                    'remaining_quantity':format(remaining,'f'),'uom':row['单位'],'source_status':'confirmed','freeze_policy':'LOCKED',
                    'ordered_at':parsed(row['下单时间'],'datetime_offset') if row.get('下单时间') not in (None,'') else None,
                    'planned_start_at':parsed(row.get('计划采购开始') or row.get('下单时间'),'datetime_offset'),
                    'expected_receipt_at':parsed(row['预计到货'],'datetime_offset'),
                    'available_at':parsed(row.get('质检后可用时间'),'datetime_offset'),'inventory_includes_receipt':False,'provenance':prov})
            elif remaining>0 or row['状态']!='CLOSED':issue('UNSUPPORTED_OPEN_PURCHASE','保留原记录；仅支持来源、未收数量、到货及质检可用时间完整的单笔 confirmed 采购',f'采购台账!{ri+6}','CAPABILITY')
            elif row['库存已含收货'] is not True:issue('RECEIPT_RECONCILIATION','已收采购与库存包含关系未确认，禁止双计或丢失',f'采购台账!{ri+6}')
            elif datetime.fromisoformat(parsed(row['预计到货'],'datetime_offset'))>datetime.fromisoformat(settings['planning_at']):issue('RECEIPT_RECONCILIATION','已关闭收货不能晚于库存快照',f'采购台账!{ri+6}')
            else:result['archived_purchases'].append(row)
            for ci,h in enumerate(tabs['采购台账']['headers']):lineage('采购台账',ri,ci,row[h],row[h],'/existing_orders/'+str(len(confirmed_purchases)-1)+'/'+h if row['状态']=='confirmed' else '/retained/purchase_ledger/'+h,'CONFIRMED_RECEIPT_EXPLICIT' if row['状态']=='confirmed' else 'ARCHIVE_OR_BLOCK_NO_SUPPLY_INJECTION')
        except (ValueError,InvalidOperation,KeyError) as e:issue('PURCHASE_RECONCILIATION',str(e),f'采购台账!{ri+6}')
    # Collect independent conflicts before the existing adapter's first-error check.
    for recipe in candidate.get('production_recipes',[]):
        for field,key in [('bom_revision_id','bom_revisions'),('routing_revision_id','routing_revisions')]:
            if recipe.get(field) not in {x.get('id') for x in candidate.get(key,[])}:issue('RECIPE_REFERENCE','配方绑定的版本不存在','生产配方')
    for cal in candidate.get('calendars',[]):
        prev=None
        for interval in sorted(cal.get('available_intervals',[]),key=lambda x:x.get('start_at','')):
            if 'start_at' not in interval or 'end_at' not in interval:continue
            start,end=datetime.fromisoformat(interval['start_at']),datetime.fromisoformat(interval['end_at'])
            if prev and start<prev:issue('CALENDAR_OVERLAP','工作区间重叠，不可重复增加产能','工作日历')
            if end<=start:issue('CALENDAR_ORDER','工作结束不得早于开始','工作日历')
            prev=end
    for source in candidate.get('supply_sources',[]):
        try:
            if not(datetime.fromisoformat(source['effective_from'])<=datetime.fromisoformat(settings['planning_at']) and datetime.fromisoformat(source['effective_to'])>=datetime.fromisoformat(settings['horizon.end_at'])):
                issue('UNSUPPORTED_SUPPLY_EFFECTIVITY','适配器未映射供应有效期；本轮只允许覆盖整个规划范围的显式版本','供应来源','CAPABILITY')
        except KeyError:pass
    if customer_links is None:
        if settings.get('p2.allow_demo_customer') is not True:issue('CUSTOMER_MAPPING_GAP','现有适配器仅有演示客户；真实客户映射须扩展，禁止替换身份','订单','CAPABILITY')
        else:issue('CUSTOMER_DEMO_ONLY','客户编码已保留旁表；CUSTOMER-DEMO 仅用于明确许可的模拟静态导出','订单','WARNING')
    else:
        for demand in candidate.get('demand_lines',[]):
            customer=customer_links.get(demand['id'])
            if not isinstance(customer,str) or not customer.strip() or customer=='CUSTOMER-DEMO':issue('CUSTOMER_MAPPING_GAP','请核对本订单的客户标识；不能使用演示占位值','订单','CAPABILITY')
            elif settings.get('rules.manufacturing_profile') in {'finished-garment-confirmed-v1','enterprise-forecast-fg-v1','enterprise-accessory-supply-v1','forecast-net-demand-v1','routing-alternate-capacity-v1'}:demand['customer_id']=customer
    if settings.get('p2.no_unlisted_supply_or_reservations') is not True:issue('UNCONFIRMED_SUPPLY_COMPLETENESS','未确认无额外在途/在制/硬预留，不可假定空列表','工厂参数')
    if 'site' in candidate:
        candidate['site']['provenance']={'source_system':'SYNTHETIC_FACTORY','source_record_id':'P2-SITE',
            'source_version':'p2.0-sim-v1','observed_at':settings.get('planning_at',''),
            'import_batch_id':'P2-'+tables['sha256'][:12],'source_locator':'工厂参数!A6:E'+str(5+len(tabs.get('工厂参数',{}).get('rows',[])))}
    candidate['reservations']=[];candidate['existing_orders']=confirmed_purchases;candidate['assumptions']=[]
    from p2.distribution_import import apply as apply_distribution
    apply_distribution(candidate,tables,settings,issue,lineage)
    from p2.network_supply_import import apply as apply_network
    apply_network(candidate,tables,settings,issue,lineage)
    from p2.qualified_operator_import import apply as apply_qualified
    apply_qualified(candidate,tables,settings,issue,lineage)
    from p2.fixed_batch_import import apply
    apply(candidate,tables,settings,issue,lineage)
    from p2.priority_supply_import import apply as apply_supply
    apply_supply(candidate,tables,settings,issue,lineage)
    from p2.inventory_quality_import import apply as apply_quality
    apply_quality(candidate,tables,settings,issue,lineage)
    from p2.inventory_policy_import import apply as apply_policy
    apply_policy(candidate,tables,settings,issue,lineage)
    from p2.accessory_supply_import import apply as apply_accessory
    apply_accessory(candidate,tables,settings,issue,lineage)
    from p2.inventory_decision_import import apply as apply_inventory_decision
    apply_inventory_decision(candidate,tables,settings,issue,lineage)
    from p2.multi_receipt_import import apply as apply_multi_receipt
    apply_multi_receipt(candidate,tables,settings,issue,lineage)
    from p2.routing_capacity_import import apply as apply_routing_capacity
    apply_routing_capacity(candidate,tables,settings,issue,lineage)
    from p2.forecast_import import apply as apply_forecast
    apply_forecast(candidate,tables,settings,issue,lineage)
    if customer_links is None and settings.get('p2.allow_demo_customer') is True:
        candidate['assumptions'].append({'id':'ASM-P2-DEMO-CUSTOMER','field_ref':'native_mapping/customer',
            'value':'CUSTOMER-DEMO','reason':'仅模拟静态转换；客户身份保留于旁表，真实企业必须先扩展映射。',
            'acknowledged_by':'SIMULATED_FACTORY_REVIEWER','acknowledged_at':'2026-10-14T09:00:00+08:00'})
    # All safety gates precede candidate publication. No approximation for unsupported data.
    blocking=lambda:any(i['severity'] in ('BLOCKER','CAPABILITY') for i in issues)
    if not blocking():
        try:validate_input(candidate)
        except (InputError,KeyError,ValueError) as e:issue('CANONICAL_VALIDATION',str(e),'PlanInput')
    review_scope='ENTERPRISE_IMPORT' if customer_links is not None else 'SYNTHETIC_ONLY'
    valid_review=bool(approval and approval.get('scope')==review_scope and approval.get('reviewer') and approval.get('all_mapped_and_policy_fields_reviewed') is True
        and approval.get('file_sha256')==tables['sha256'] and approval.get('mapping_sha256')==PROFILE_SHA and approval.get('content_sha256')==digest(tables['sheets']))
    if approval and not valid_review:issue('STALE_REVIEW','文件、映射或数据内容发生变化，必须重新确认','review')
    if blocking():result['status']='BLOCKED'
    elif not valid_review:result['status']='NEEDS_CONFIRMATION'
    else:
        result['status']='READY_FOR_STATIC_EXPORT';result['review']=approval
        if customer_links is not None:
            for collection in candidate.values():
                for obj in (collection if isinstance(collection,list) else [collection]):
                    if isinstance(obj,dict) and 'provenance' in obj:obj['provenance']['source_system']='ENTERPRISE_XLSX'
            result['evidence_scope']='HUMAN_CONFIRMED_IMPORT_STATIC_VALIDATION'
        result['canonical']=candidate;result['native']=to_native(candidate)
        if customer_links is not None:
            links={**customer_links,**{d['id']:d['customer_id'] for d in candidate['demand_lines'] if d.get('kind')=='NET_FORECAST'}}
            result['native']['customer']=[{'name':x} for x in sorted(set(links.values()))]
            for demand in result['native']['demand']:demand['customer']=links[demand['name']]
    result['counts']={'source_sheets':len(tabs),'mapped_cells':len(result['lineage']),
                      'blocking_issues':sum(i['severity'] in ('BLOCKER','CAPABILITY') for i in issues)}
    return result

def main():
    parser=argparse.ArgumentParser(description='P2.0 offline synthetic sample preflight. No engine execution.')
    parser.add_argument('workbook',type=Path);parser.add_argument('--review',type=Path)
    parser.add_argument('--write-simulated-review',type=Path,help='SYNTHETIC ONLY: write explicit example ledger; never enterprise approval')
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();tables=load_workbook(args.workbook)
    if args.write_simulated_review:
        args.write_simulated_review.parent.mkdir(parents=True,exist_ok=True)
        args.write_simulated_review.write_text(json.dumps(sample_review(tables),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    approval=json.loads(args.review.read_text(encoding='utf-8')) if args.review else None
    report=inspect_tables(tables,approval)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':report['status'],'counts':report['counts'],'engine_executed':False},ensure_ascii=False))

if __name__=='__main__':main()
