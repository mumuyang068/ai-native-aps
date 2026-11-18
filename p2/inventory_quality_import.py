"""Optional quality source sheet in the existing P2 confirmation/version flow."""
HEADERS=['库存记录编码','待检数量','放行净可用量','单位','放行凭证','证据类型','质量时点','来源记录','来源版本','来源说明']
SHEET='库存质量状态'

def apply(candidate,tables,settings,issue,lineage):
    from p2.preflight import parsed
    sheet=tables['sheets'].get(SHEET)
    if not sheet:return
    if sheet.get('formulas'):
        for cell in sheet['formulas']:
            issue('FORMULA_INPUT','质量表公式必须提供核对后的显式值，不接受缓存放行量',SHEET+'!'+cell)
        return
    if sheet['headers']!=HEADERS:
        issue('QUALITY_MAPPING','质量表必须保留完整字段与来源',SHEET);return
    seen=set()
    for ri,values in enumerate(sheet['rows']):
        if not any(v not in (None,'') for v in values):continue
        row=dict(zip(HEADERS,values))
        try:
            if settings.get('rules.manufacturing_profile')not in ('finished-garment-confirmed-v1','enterprise-forecast-fg-v1','enterprise-accessory-supply-v1'):raise ValueError('此模型不支持非空质量可用性字段')
            ref=parsed(row['库存记录编码'],'string')
            if ref in seen:raise ValueError('重复库存质量记录')
            seen.add(ref)
            target=next((i for i in candidate['inventory_positions'] if i['id']==ref),None)
            if target is None:raise ValueError('质量记录没有唯一当前库存身份')
            if row['单位']!=target['uom']:raise ValueError('质量数量必须明确使用已确认库存基本单位，不自动换算')
            p={'source_system':'SYNTHETIC_QUALITY_INPUT','source_record_id':parsed(row['来源记录'],'string'),
               'source_version':parsed(row['来源版本'],'string'),'observed_at':settings['planning_at'],
               'import_batch_id':'P2-'+tables['sha256'][:12],'source_locator':SHEET+'!'+str(ri+6)}
            target['quality_availability']={'inspection_pending_quantity':parsed(row['待检数量'],'decimal_string'),
                'released_quantity':parsed(row['放行净可用量'],'decimal_string'),'release_reference':parsed(row['放行凭证'],'nullable_string'),
                'evidence_kind':parsed(row['证据类型'],'string'),'effective_at':parsed(row['质量时点'],'datetime_offset'),'provenance':p}
            for ci,h in enumerate(HEADERS):lineage(SHEET,ri,ci,row[h],row[h],'/inventory_positions/'+ref+'/quality_availability/'+h,'EXPLICIT_SYNTHETIC_QUALITY_FACT')
        except (ValueError,KeyError,TypeError) as e:issue('QUALITY_MAPPING',str(e),SHEET+'!'+str(ri+6))
