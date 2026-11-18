"""Source-cell preserving receipt and protected-MO facts in the original P2 pipeline."""
SHEET='采购收货记录'
PROFILE='multi-receipt-protected-manufacturing-v1'
FIELDS={'采购单号':('po_id','string'),'收货记录号':('receipt_reference','string'),
        '收货数量':('quantity','decimal_string'),'数量单位':('uom','string'),
        '实际物理收货':('physical_receipt_at','datetime_offset'),
        '质量状态':('quality_status','string'),'计划放行时间':('release_at','datetime_offset')}
HEADERS=[*FIELDS,'来源记录','来源版本','来源说明']

def apply(candidate,tables,settings,issue,lineage):
    from p2.preflight import parsed,column,PROFILE as MAPPING
    tabs=tables['sheets'];active=settings.get('rules.manufacturing_profile') in {PROFILE,'routing-alternate-capacity-v1'}
    def records(name):
        t=tabs.get(name,{})
        return [(i,dict(zip(t['headers'],r))) for i,r in enumerate(t.get('rows',[])) if any(v not in (None,'') for v in r)]
    if not active:
        for i,row in records(SHEET):
            for h,v in row.items():
                if v not in (None,''):issue('UNSUPPORTED_RECEIPT_PROFILE','非空收货事实需要显式收货联合profile，不能静默丢弃：'+h,SHEET+'!'+column(tabs[SHEET]['headers'].index(h)+1)+str(i+6),'CAPABILITY')
        for i,row in records('制造工单'):
            if row.get('操作员编码') not in (None,''):issue('UNSUPPORTED_RECEIPT_PROFILE','该profile未认证已有MO具名人员承诺','制造工单!'+str(i+6),'CAPABILITY')
        return
    def provenance(name,i,row):
        for h in ['来源记录','来源版本','来源说明']:parsed(row.get(h),'string')
        return dict(source_system='ENTERPRISE_XLSX',source_record_id=row['来源记录'],source_version=row['来源版本'],
                    observed_at=settings['planning_at'],import_batch_id='P2-'+tables['sha256'][:12],source_locator=name+'!A'+str(i+6))
    tab=tabs.get(SHEET)
    if not tab or len(tab['headers'])!=len(set(tab['headers'])) or set(tab['headers'])!=set(HEADERS):
        issue('RECEIPT_MAPPING','必须完整、唯一地提供收货记录表头',SHEET);return
    if tab.get('formulas'):
        for c in tab['formulas']:issue('FORMULA_INPUT','收货事实不可由缓存公式推断',SHEET+'!'+c)
        return
    candidate['purchase_receipts']=[]
    for i,row in records(SHEET):
        try:
            obj={f:(None if f=='release_at' and row.get(h) in (None,'') else parsed(row.get(h),typ)) for h,(f,typ) in FIELDS.items()}
            obj['provenance']=provenance(SHEET,i,row)
            target='/purchase_receipts/'+str(len(candidate['purchase_receipts']))
            candidate['purchase_receipts'].append(obj)
            for h in HEADERS:lineage(SHEET,i,tab['headers'].index(h),row.get(h),obj.get(FIELDS[h][0]) if h in FIELDS else row.get(h),target+'/'+(FIELDS[h][0] if h in FIELDS else 'provenance/'+h),'EXPLICIT_RECEIPT_FACT_NO_SUPPLY_DUPLICATION')
        except (ValueError,KeyError,TypeError) as exc:issue('RECEIPT_MAPPING',str(exc),SHEET+'!A'+str(i+6))
    name='制造工单';tab=tabs.get(name);expected=set(MAPPING['auxiliary'][name]['headers'])|{'操作员编码'}
    if not tab or len(tab['headers'])!=len(set(tab['headers'])) or set(tab['headers'])!=expected:
        issue('RECEIPT_MO_MAPPING','必须提供完整制造承诺及操作员编码',name);return
    if tab.get('formulas'):
        for c in tab['formulas']:issue('FORMULA_INPUT','已确认制造承诺必须为有来源的显式值，不接受缓存公式',name+'!'+c)
        return
    for i,row in records(name):
        try:
            get=lambda h,t:parsed(row.get(h),t)
            mo=dict(type='MO',id=get('工单号','string'),external_ref=get('外部工单号','string'),item_id=get('产品物料编码','string'),
                    location_id=get('工厂编码','string'),recipe_id=get('配方编码','string'),quantity=get('制造数量','decimal_string'),
                    completed_quantity=get('已完工数量','decimal_string'),uom=get('单位','string'),source_status=get('状态','string'),freeze_policy=get('冻结规则','string'),
                    operations=[dict(operation_id=get('工序编码','string'),resource_id=get('资源编码','string'),operator_id=get('操作员编码','string'),
                    resource_units=get('单件窗口用量','decimal_string'),start_at=get('原开始','datetime_offset'),end_at=get('原结束','datetime_offset'))],provenance=provenance(name,i,row))
            target='/existing_orders/'+str(len(candidate['existing_orders']));candidate['existing_orders'].append(mo)
            for ci,h in enumerate(tab['headers']):lineage(name,i,ci,row[h],row[h],target+'/'+h,'EXPLICIT_LOCKED_MANUFACTURING_COMMITMENT')
        except (ValueError,KeyError,TypeError) as exc:issue('RECEIPT_MO_MAPPING',str(exc),name+'!A'+str(i+6))
