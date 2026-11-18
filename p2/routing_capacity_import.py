"""A sourced machine skill column; ordinary unknown columns stay unchanged."""
HEADER='设备必需技能'
def apply(candidate,tables,settings,issue,lineage):
    from p2.preflight import parsed,column
    from routing_capacity import PROFILE
    tab=tables['sheets'].get('工艺路线',{})
    if HEADER not in tab.get('headers',[]):return
    ci=tab['headers'].index(HEADER)
    for i,values in enumerate(tab.get('rows',[])):
        row=dict(zip(tab['headers'],values));raw=row.get(HEADER)
        if raw in (None,''):continue
        cell='工艺路线!'+column(ci+1)+str(i+6)
        if settings.get('rules.manufacturing_profile')!=PROFILE:
            issue('UNSUPPORTED_ROUTING_CAPACITY_FIELD','当前profile不支持非空设备资格字段，不能静默丢弃：'+HEADER,cell,'CAPABILITY');continue
        try:
            value=parsed(raw,'string')
            route=next(r for r in candidate['routing_revisions'] if r['id']==row['记录编码'])
            op=next(o for o in route['operations'] if o['id']==row['工序编码'])
            op['machine_skill_id']=value
            lineage('工艺路线',i,ci,raw,value,'/routing_revisions/'+route['id']+'/operations/'+op['id']+'/machine_skill_id','EXPLICIT_MACHINE_QUALIFICATION')
        except (KeyError,StopIteration,ValueError,TypeError) as exc:issue('ROUTING_CAPACITY_MAPPING',str(exc),cell)
