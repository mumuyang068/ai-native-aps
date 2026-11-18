"""Explicit optional columns; reject unsupported values before they disappear."""
FIELDS={'供应优先级':('priority','integer'),'采购模型单价':('unit_price','decimal_string'),
    '采购币种':('currency','string'),'价格口径':('price_basis','string'),
    '规划期供应总量':('capacity.quantity','decimal_string'),'供应能力单位':('capacity.uom','string'),
    '供应能力起点':('capacity.start_at','datetime_offset'),'供应能力终点':('capacity.end_at','datetime_offset'),
    '供应能力口径':('capacity.scope','string'),'供应质检等待秒':('receiving_qc.wait_seconds','integer')}

def apply(data,tables,settings,issue,lineage):
    import copy
    from p2.preflight import parsed,column,assign
    from priority_supply import PROFILE
    tab=tables['sheets'].get('供应来源')
    if not tab:return
    active=settings.get('rules.manufacturing_profile')==PROFILE
    for i,values in enumerate(tab['rows']):
        if not any(v not in (None,'') for v in values):continue
        row=dict(zip(tab['headers'],values))
        obj=next((s for s in data.get('supply_sources',[]) if s['id']==row.get('记录编码')),None)
        for header,(field,typ) in FIELDS.items():
            raw=row.get(header)
            cell='供应来源!'+(column(tab['headers'].index(header)+1) if header in tab['headers'] else '')+str(i+6)
            if not active:
                if raw not in (None,''):issue('UNSUPPORTED_PRIORITY_SUPPLY_FIELD',f'字段“{header}”需要显式双来源 PRIORITY profile，不能丢弃已填约束',cell,'CAPABILITY')
                continue
            try:
                if obj is None:raise ValueError('供应来源身份不存在')
                value=parsed(raw,typ);assign(obj,field,value)
                index=data['supply_sources'].index(obj)
                lineage('供应来源',i,tab['headers'].index(header),raw,value,'/supply_sources/'+str(index)+'/'+field.replace('.','/'),'EXPLICIT_PRIORITY_SUPPLY_TERM')
            except (ValueError,TypeError,KeyError) as exc:issue('PRIORITY_SUPPLY_MAPPING',str(exc),cell)
        if active and obj:
            for key in ['capacity','receiving_qc']:
                if key in obj:obj[key]['provenance']=copy.deepcopy(obj['provenance'])
