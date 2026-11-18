"""Source-preserving optional two-location and lane facts in original P2."""
def apply(candidate,tables,settings,issue,lineage):
 from single_lane_distribution import PROFILE
 from p2.preflight import parsed,column
 tabs=tables['sheets'];active=settings.get('rules.manufacturing_profile') in {PROFILE,'network-distribution-confirmed-supply-v1'}
 specs={'地点主档':('locations',{'地点编码':('id','string'),'名称':('name','string'),'时区':('timezone','string')}),
  '配送路径':('distribution_lanes',{'路径编码':('id','string'),'物料编码':('item_id','string'),'起运地点':('origin_location_id','string'),
   '目的地点':('destination_location_id','string'),'运输秒':('lead_time_seconds','integer'),'最小配送量':('minimum_transfer_quantity','decimal_string'),
   '配送倍数':('transfer_multiple','decimal_string'),'单位':('uom','string'),'生效时间':('effective_from','datetime_offset'),
   '失效时间':('effective_to','datetime_offset'),'路径状态':('status','string')})}
 for sheet,(key,fields) in specs.items():
  tab=tabs.get(sheet);records=[] if not tab else [(i,dict(zip(tab['headers'],r))) for i,r in enumerate(tab['rows']) if any(v not in (None,'') for v in r)]
  if not active:
   if records:issue('UNSUPPORTED_DISTRIBUTION','当前profile不支持非空地点/配送字段，不能丢弃',sheet,'CAPABILITY')
   continue
  if not tab:issue('MISSING_SHEET','配送profile需要显式地点/路径表',sheet);continue
  if set(tab['headers'])!=set(fields)|{'来源记录','来源版本','来源说明'}:issue('MAPPING_CONFIRMATION_REQUIRED','配送表头需完整核对',sheet);continue
  candidate[key]=[]
  for i,row in records:
   try:
    if not all(row.get(k) for k in ['来源记录','来源版本','来源说明']):raise ValueError('缺少业务来源')
    obj={}
    for h,(field,typ) in fields.items():
     obj[field]=parsed(row.get(h),typ);lineage(sheet,i,tab['headers'].index(h),row.get(h),obj[field],'/'+key+'/'+str(i)+'/'+field)
    obj['provenance']={'source_system':'ENTERPRISE_XLSX','source_record_id':str(row['来源记录']),'source_version':str(row['来源版本']),
     'observed_at':settings['planning_at'],'import_batch_id':'P2-'+tables['sha256'][:12],'source_locator':sheet+'!A'+str(i+6)}
    candidate[key].append(obj)
   except (ValueError,TypeError,KeyError) as exc:issue('DISTRIBUTION_MAPPING',str(exc),sheet+'!A'+str(i+6))
 existing=tabs.get('已有配送单')
 if existing and any(any(v not in (None,'') for v in r) for r in existing['rows']):
  issue('UNSUPPORTED_DISTRIBUTION_ORDER_STATE','已有confirmed/approved/在途/收货及手工proposed配送单尚未认证；保留原记录，不转为新建议','已有配送单','CAPABILITY')
