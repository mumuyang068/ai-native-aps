"""Read-only domain projection of one immutable confirmed version.

No solver, staging database, suggested orders, or separate fact persistence.
Current stored objects still originate in PlanInput; this is not a model migration.
"""
import copy,hashlib,json,re
from inventory_availability import net_available
from decimal import Decimal

def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def project(version,original=None,*,imported_at=None):
    business=version['business_data'];data=business['plan_input']
    mappings=version.get('mappings',{});lineage=business.get('lineage',[])
    po_fields={'采购单号':'id','物料编码':'item_id','供应商编码':'supplier_id','订购量':'ordered_quantity',
       '已收量':'received_quantity','未收量':'remaining_quantity','单位':'uom','状态':'source_status',
       '预计到货':'expected_receipt_at','质检后可用时间':'available_at','下单时间':'ordered_at',
       '计划采购开始':'planned_start_at','供应来源编码':'supply_source_id','库存已含收货':'inventory_includes_receipt'}
    def source_fields(obj):
        locator=obj.get('provenance',{}).get('source_locator','')
        match=re.fullmatch(r'(.+)!([A-Z]*)(\d+)(?::[A-Z]+\d+)?',locator)
        if not match:return []
        sheet,row=match[1],int(match[3]);entries=[]
        cells=(original or {}).get('sheets',{}).get(sheet,{}).get('cells',{})
        for entry in lineage:
            source_cell=entry.get('source_cell','');m=re.fullmatch(r'(.+)!([A-Z]+)(\d+)',source_cell)
            if not m or m[1]!=sheet or int(m[3])!=row:continue
            ref=m[2]+m[3];corrections=[copy.deepcopy(c) for c in version.get('corrections',[]) if c['sheet']==sheet and c['cell']==ref]
            key=entry['target'].rsplit('/',1)[-1]
            field=po_fields.get(key,key) if obj.get('type')=='PO' else key
            canonical=field in obj and not entry['target'].startswith('/retained/')
            entries.append({'source_cell':(entry['source_sheet']+'!'+ref) if entry.get('source_sheet') else source_cell,'original_value':cells.get(ref),
                'reviewed_value':entry['raw_value'],'normalized_value':copy.deepcopy(obj[field]) if canonical else entry['normalized_value'],
                'stored_lineage_normalized_value':copy.deepcopy(entry['normalized_value']),
                'value_basis':'CONFIRMED_VERSION_CANONICAL' if canonical else 'VERSION_LINEAGE',
                'target':entry['target'],'rule':entry['rule'],'file_sha256':entry.get('file_sha256',version['file_sha256']),
                **({k:entry[k] for k in ['source_system','source_sheet','source_version','business_as_of']} if entry.get('source_system') else {}),
                'source_status':'PRESENT' if ref in cells else 'HUMAN_SUPPLEMENT' if corrections else 'NOT_CHECKED',
                'corrections':corrections})
        return entries
    def fact(obj):
        fields=source_fields(obj)
        return {'source_record':copy.deepcopy(obj.get('provenance',{})),
                'source_fields':fields,'confirmation_status':'CONFIRMED_VERSION',
                'source_status':'PRESENT' if original is not None and obj.get('provenance') and any(f['source_status'] in {'PRESENT','HUMAN_SUPPLEMENT'} for f in fields) else 'NOT_CHECKED'}
    demands=[]
    for row in data['demand_lines']:
        if row.get('kind')=='NET_FORECAST':continue
        demands.append({**{k:copy.deepcopy(row[k]) for k in ['id','order_ref','line_ref','item_id','location_id','quantity','uom','due_at','priority','minimum_shipment','maximum_lateness_seconds','status'] if k in row},
                        'customer_id':business.get('demand_customer_links',{}).get(row['id']),**fact(row)})
    inventory=[]
    for row in data['inventory_positions']:
        net=net_available(row)
        inventory.append({**{k:copy.deepcopy(row[k]) for k in ['id','item_id','location_id','physical_lot_ref','on_hand','quality_blocked','other_nonnettable','uom','as_of'] if k in row},
                          'net_available':format(net.normalize(),'f'),
                          'business_status':{'value':None,'status':'NOT_PROVIDED','reason':'库存对象表达余额与冻结量，未单独声明执行状态'},**fact(row)})
    purchases=[];sources={s['id']:s for s in data['supply_sources']}
    for row in data['existing_orders']:
        if row.get('type')!='PO' or row.get('source_status')!='confirmed':continue
        qc=sources.get(row.get('supply_source_id'),{}).get('receiving_qc')
        purchases.append({**{k:copy.deepcopy(row[k]) for k in ['external_ref','item_id','location_id','supplier_id','supply_source_id','ordered_quantity','received_quantity','cancelled_quantity','remaining_quantity','uom','source_status','ordered_at','planned_start_at','inventory_includes_receipt'] if k in row},
            'reference':row['id'],'quantity':row['remaining_quantity'],'physical_receipt_at':row['expected_receipt_at'],
            'available_at':row['available_at'],'qc_wait_seconds':qc.get('wait_seconds') if qc else None,
            'qc_source':copy.deepcopy(qc.get('provenance')) if qc else None,**fact(row)})
    distribution=bool(data.get('distribution_lanes'))
    return {**({'locations':[{**copy.deepcopy(r),**fact(r)} for r in data['locations']],
        'distribution_lanes':[{**copy.deepcopy(r),**fact(r)} for r in data['distribution_lanes']]} if distribution else {}),
        'schema_version':'business-fact-view-v1','status':'CONFIRMED_VERSION_PROJECTION','timezone':data['site']['timezone'],
        'data_version_id':version['id'],'import_id':version['import_id'],
        'business_snapshot':{'kind':'DERIVED_FROM_CONFIRMED_VERSION','sha256':digest(data),'profile':data['rules'].get('manufacturing_profile')},
        'source_file':{'filename':version['filename'],'sha256':version['file_sha256'],'status':'HASH_VERIFIED' if original is not None else 'NOT_CHECKED'},
        'times':{'business_as_of':data['planning_at'],'imported_at':imported_at,'version_saved_at':version['created_at'],
                 'confirmation_records':copy.deepcopy(version.get('confirmations',{}))},
        'forecasts':copy.deepcopy(data.get('forecasts',[])),'net_forecast_demands':[copy.deepcopy(d) for d in data['demand_lines'] if d.get('kind')=='NET_FORECAST'],
        'mappings':copy.deepcopy(mappings),'demand_lines':demands,'inventory_positions':inventory,'confirmed_purchases':purchases,
        'limits':['Read-only projection; stored source of truth remains the immutable P2 version.',
                  'Stored facts still use the supported planning profile; no full domain-model or multi-solver migration.',
                  'No proposed orders, scenario assumptions, physical receipt or actual QC completion inferred.',
                  'Missing original cells or provenance remain NOT_CHECKED; no invented timestamps or source keys.']}
