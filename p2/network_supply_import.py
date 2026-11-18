"""Source-cell-preserving frozen transport assumption; no runtime changes."""
from network_distribution import PROFILE
SHEET='运输条件假设'
FIELDS={'路径编码':('lane_id','string'),'最大运输秒':('maximum_lead_time_seconds','integer')}
HEADERS=list(FIELDS)+['来源记录','来源版本','来源说明']
def apply(candidate,tables,settings,issue,lineage):
    from p2.preflight import parsed
    tab=tables['sheets'].get(SHEET)
    rows=[] if not tab else [(i,dict(zip(tab['headers'],row))) for i,row in enumerate(tab['rows']) if any(v not in (None,'') for v in row)]
    if settings.get('rules.manufacturing_profile')!=PROFILE:
        if rows:issue('UNSUPPORTED_DISTRIBUTION_CONDITION','当前profile未认证运输假设，不能丢弃原记录',SHEET,'CAPABILITY')
        return
    if not tab or set(tab['headers'])!=set(HEADERS) or len(rows)!=1:
        issue('DISTRIBUTION_OPTION_REQUIRED','必须显式确认一条有来源的运输提前期上限假设',SHEET);return
    i,row=rows[0]
    try:
        if not all(row.get(k) for k in HEADERS[2:]):raise ValueError('来源记录/版本/说明不可缺少')
        option={}
        for h,(field,typ) in FIELDS.items():
            option[field]=parsed(row[h],typ);lineage(SHEET,i,tab['headers'].index(h),row[h],option[field],'/distribution_lead_time_option/'+field)
        option['provenance']=dict(source_system='ENTERPRISE_XLSX',source_record_id=str(row['来源记录']),source_version=str(row['来源版本']),observed_at=settings['planning_at'],import_batch_id='P2-'+tables['sha256'][:12],source_locator=SHEET+'!A'+str(i+6))
        candidate['distribution_lead_time_option']=option
    except (ValueError,TypeError,KeyError) as exc:issue('DISTRIBUTION_OPTION_MAPPING',str(exc),SHEET+'!A'+str(i+6))
