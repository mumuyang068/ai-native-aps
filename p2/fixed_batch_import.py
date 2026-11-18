"""Optional fields for the same P2 version pipeline; no separate import store."""
def apply(candidate,tables,settings,issue,lineage):
    from p2.preflight import parsed,PROFILE,column
    from fixed_batch import PROFILE as FIXED,JOINT_PROFILE
    mixed=settings.get('rules.manufacturing_profile') in {'mixed-fixed-batch-wip-v1','mixed-fixed-batch-confirmed-supply-v1','mixed-fixed-batch-confirmed-qualified-v1','mixed-fixed-batch-confirmed-cut-qualified-v1','finished-garment-confirmed-v1','enterprise-forecast-fg-v1','enterprise-accessory-supply-v1'}
    fixed=mixed or settings.get('rules.manufacturing_profile') in {FIXED,JOINT_PROFILE,'fixed-batch-priority-supply-v1'}
    tabs=tables['sheets']
    def records(name):
        if name not in tabs:return []
        return [(i,dict(zip(tabs[name]['headers'],r))) for i,r in enumerate(tabs[name]['rows']) if any(x not in (None,'') for x in r)]
    field_specs=[
        ('生产资源','resources',{'产能模型':('capacity_model','string'),'窗口产能':('window_capacity_quantity','decimal_string'),
          '产能单位':('capacity_uom','string'),'最大提前秒':('maxearly_seconds','integer')}),
        ('生产配方','production_recipes',{'最大制造批量':('manufacturing_maximum_quantity','decimal_string')})]
    if not fixed:
        multi=settings.get('rules.manufacturing_profile') in {'multi-receipt-protected-manufacturing-v1','routing-alternate-capacity-v1'}
        for name,_,fields in field_specs:
            for i,row in records(name):
                for header in fields:
                    if multi and header=='产能模型' and row.get(header)=='CONTINUOUS':
                        obj=next((r for r in candidate['resources'] if r['id']==row.get('记录编码')),None)
                        if obj is not None:obj['capacity_model']='CONTINUOUS'
                        continue
                    if row.get(header) not in (None,''):
                        cell=column(tabs[name]['headers'].index(header)+1)+str(i+6)
                        issue('UNSUPPORTED_MANUFACTURING_PROFILE',
                          f'字段“{header}”（记录“{row.get("记录编码", "未提供编码")}”）需要显式固定批量 profile；当前不支持，不能忽略已填写的约束',
                          name+'!'+cell,'CAPABILITY')
        for name in ['制造工单','历史制造建议']:
            if records(name) and not (multi and name=='制造工单'):issue('UNSUPPORTED_MANUFACTURING_PROFILE','已有制造工单和历史建议需要显式已认证 profile',name,'CAPABILITY')
        return
    def provenance(name,i,row):
        return {'source_system':'SYNTHETIC_FACTORY','source_record_id':str(row['来源记录']),
          'source_version':str(row['来源版本']),'observed_at':settings['planning_at'],
          'import_batch_id':'P2-'+tables['sha256'][:12],'source_locator':name+'!'+str(i+6)}
    for name,collection,fields in field_specs:
        for i,row in records(name):
            obj=next((r for r in candidate[collection] if r['id']==row.get('记录编码')),None)
            if obj is None:continue
            try:
                for header,(field,typ) in fields.items():
                    if mixed:
                        if collection=='resources' and row.get('产能模型')=='CONTINUOUS' and header!='产能模型':
                            if row.get(header) not in (None,''):
                                cell=column(tabs[name]['headers'].index(header)+1)+str(i+6)
                                issue('UNSUPPORTED_MANUFACTURING_PROFILE',f'连续资源不能填写数量窗口字段“{header}”',name+'!'+cell,'CAPABILITY')
                            continue
                        if collection=='production_recipes' and row.get(header) in (None,''):continue
                    obj[field]=parsed(row.get(header),typ)
                    lineage(name,i,tabs[name]['headers'].index(header),row[header],obj[field],'/'+collection+'/'+obj['id']+'/'+field)
            except (KeyError,ValueError,TypeError) as exc:issue('FIXED_BATCH_MAPPING',str(exc),name+'!'+str(i+6))
    for name in ['制造工单','历史制造建议']:
        expected=PROFILE['auxiliary'][name]['headers']
        if name not in tabs:
            issue('MISSING_SHEET','需显式提供已有工单与旧建议清单，空表表示无记录',name);continue
        optional=set(PROFILE['auxiliary'][name].get('optional_headers',[]))
        if not set(expected).issubset(tabs[name]['headers']) or set(tabs[name]['headers'])-set(expected)-optional:issue('MAPPING_CONFIRMATION_REQUIRED','制造输入表头不完整或包含未知列',name);continue
        if name=='历史制造建议':candidate['previous_manufacturing_suggestions']=[]
        for i,row in records(name):
            try:
                for h in expected:
                    if row.get(h) in (None,''):raise ValueError('缺少显式值：'+h)
                if name=='制造工单':
                    mo={'type':'MO','id':parsed(row['工单号'],'string'),'external_ref':parsed(row['外部工单号'],'string'),
                      'item_id':parsed(row['产品物料编码'],'string'),'location_id':parsed(row['工厂编码'],'string'),
                      'recipe_id':parsed(row['配方编码'],'string'),'quantity':parsed(row['制造数量'],'decimal_string'),
                      'completed_quantity':parsed(row['已完工数量'],'decimal_string'),'uom':parsed(row['单位'],'string'),
                      'source_status':parsed(row['状态'],'string'),'freeze_policy':parsed(row['冻结规则'],'string'),
                      'operations':[{'operation_id':parsed(row['工序编码'],'string'),'resource_id':parsed(row['资源编码'],'string'),
                        'resource_units':parsed(row['单件窗口用量'],'decimal_string'),'start_at':parsed(row['原开始'],'datetime_offset'),
                        'end_at':parsed(row['原结束'],'datetime_offset')}],'provenance':provenance(name,i,row)}
                    candidate['existing_orders'].append(mo)
                    target_index=len(candidate['existing_orders'])-1
                else:
                    candidate['previous_manufacturing_suggestions'].append({'reference':parsed(row['建议编号'],'string'),
                      'recipe_id':parsed(row['配方编码'],'string'),'quantity':parsed(row['制造数量'],'decimal_string'),
                      'status':parsed(row['状态'],'string'),'start_at':parsed(row['原开始'],'datetime_offset'),
                      'end_at':parsed(row['原结束'],'datetime_offset'),'provenance':provenance(name,i,row)})
                    target_index=len(candidate['previous_manufacturing_suggestions'])-1
                for ci,h in enumerate(tabs[name]['headers']):lineage(name,i,ci,row[h],row[h],'/'+('existing_orders' if name=='制造工单' else 'previous_manufacturing_suggestions')+'/'+str(target_index)+'/'+h,'EXPLICIT_MANUFACTURING_FACT')
            except (ValueError,KeyError,TypeError) as exc:issue('FIXED_BATCH_MAPPING',str(exc),name+'!'+str(i+6))
