"""Optional personnel facts in the existing P2 source/confirmation pipeline."""
def apply(candidate,tables,settings,issue,lineage):
    from p2.preflight import parsed,column
    from qualified_operator import PROFILE
    joint=settings.get('rules.manufacturing_profile') in {'mixed-fixed-batch-confirmed-qualified-v1','mixed-fixed-batch-confirmed-cut-qualified-v1','finished-garment-confirmed-v1','enterprise-forecast-fg-v1','enterprise-accessory-supply-v1','multi-receipt-protected-manufacturing-v1','routing-alternate-capacity-v1'}
    active=joint or settings.get('rules.manufacturing_profile')==PROFILE;tabs=tables['sheets']
    def records(name):
        if name not in tabs:return []
        return [(i,dict(zip(tabs[name]['headers'],r))) for i,r in enumerate(tabs[name]['rows']) if any(v not in (None,'') for v in r)]
    specs=[('生产资源',{'资源角色':('role','string'),'所属人员池':('pool_id','string')}),
           ('工艺路线',{'人员池编码':('pool_id','string'),'必需技能':('skill_id','string'),'所需人数':('quantity','decimal_string')})]
    extras=['技能主档','人员技能资格','停工日历']
    if not active:
        for name,fields in specs:
            for i,row in records(name):
                for h in fields:
                    if row.get(h) not in (None,''):
                        issue('UNSUPPORTED_QUALIFIED_OPERATOR','非空人员约束需显式人员profile，不能静默丢弃：'+h,name+'!'+column(tabs[name]['headers'].index(h)+1)+str(i+6),'CAPABILITY')
        for name in extras:
            if records(name):issue('UNSUPPORTED_QUALIFIED_OPERATOR','当前profile不支持人员/技能或全期停工资料',name,'CAPABILITY')
        return
    def prov(name,i,row):
        for h in ['来源记录','来源版本','来源说明']:
            if not row.get(h):raise ValueError('缺少来源字段 '+h)
        return {'source_system':'ENTERPRISE_XLSX','source_record_id':str(row['来源记录']),'source_version':str(row['来源版本']),
            'observed_at':settings['planning_at'],'import_batch_id':'P2-'+tables['sha256'][:12],'source_locator':name+'!A'+str(i+6)}
    def field(name,i,row,h,typ,target):
        value=parsed(row.get(h),typ);lineage(name,i,tabs[name]['headers'].index(h),row.get(h),value,target,'EXPLICIT_QUALIFIED_RESOURCE_FACT');return value
    for name,fields in specs:
        for i,row in records(name):
            try:
                if name=='生产资源':
                    obj=next(r for r in candidate['resources'] if r['id']==row['记录编码'])
                    for h,(key,typ) in fields.items():
                        if h=='所属人员池' and not (row.get('资源角色')=='OPERATOR' or (settings.get('rules.manufacturing_profile')=='routing-alternate-capacity-v1' and row.get('资源角色')=='MACHINE' and row.get(h) not in (None,''))):
                            if row.get(h) not in (None,''):raise ValueError('仅具名人员可填写所属人员池')
                            continue
                        obj[key]=field(name,i,row,h,typ,'/resources/'+obj['id']+'/'+key)
                else:
                    if joint and all(row.get(h) in (None,'') for h in fields):continue
                    route=next(r for r in candidate['routing_revisions'] if r['id']==row['记录编码']);obj=next(o for o in route['operations'] if o['id']==row['工序编码'])
                    obj['operator_requirement']={key:field(name,i,row,h,typ,'/routing_revisions/'+route['id']+'/operations/'+obj['id']+'/operator_requirement/'+key) for h,(key,typ) in fields.items()}
                    obj['operator_requirement']['provenance']=prov(name,i,row)
            except (KeyError,ValueError,StopIteration,TypeError) as exc:issue('QUALIFIED_OPERATOR_MAPPING',str(exc),name+'!A'+str(i+6))
    from p2.preflight import PROFILE as MAPPING
    for name in extras:
        if name not in tabs:issue('MISSING_SHEET','人员profile需要显式资料表，空表表示无记录',name);continue
        if set(tabs[name]['headers'])!=set(MAPPING['auxiliary'][name]['headers']):issue('MAPPING_CONFIRMATION_REQUIRED','人员/技能资料表头不完整或有未知列',name);continue
        key='skills' if name=='技能主档' else 'resource_skills'
        if name!='停工日历':candidate[key]=[]
        for i,row in records(name):
            try:
                provenance=prov(name,i,row)
                if name=='技能主档':obj={'id':field(name,i,row,'技能编码','string','/skills/id'),'provenance':provenance}
                elif name=='人员技能资格':
                    fields={'人员编码':('resource_id','string'),'技能编码':('skill_id','string'),'资格优先级':('priority','integer'),
                        '资格生效':('effective_from','datetime_offset'),'资格失效':('effective_to','datetime_offset')}
                    obj={k:field(name,i,row,h,t,'/resource_skills/'+str(i)+'/'+k) for h,(k,t) in fields.items()};obj['provenance']=provenance
                else:
                    obj={'id':field(name,i,row,'日历编码','string','/calendars/id'),'timezone':field(name,i,row,'时区','string','/calendars/timezone'),
                         'available_intervals':[],'provenance':provenance}
                    if any(c['id']==obj['id'] for c in candidate['calendars']):raise ValueError('停工日历不能与工作时段日历重复')
                    candidate['calendars'].append(obj);continue
                candidate[key].append(obj)
            except (KeyError,ValueError,TypeError) as exc:issue('QUALIFIED_OPERATOR_MAPPING',str(exc),name+'!A'+str(i+6))
