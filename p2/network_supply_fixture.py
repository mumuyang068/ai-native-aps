"""Emit the declared synthetic textile supply source workbook; no import or solve."""
from io import BytesIO
import copy
from openpyxl import load_workbook
def workbook_bytes(data):
    from p2.finished_delivery_fixture import workbook_bytes as existing_writer
    from p2.network_supply_import import SHEET,HEADERS
    d=copy.deepcopy(data);d.setdefault('skills',[]);d.setdefault('resource_skills',[])
    for row in d['demand_lines']:row['customer_id']='P417-DESTINATION-REQUESTOR'
    book=load_workbook(BytesIO(existing_writer(d)))
    for sheet in book:
        for row in sheet:
            for c in row:
                if isinstance(c.value,str):c.value=c.value.replace('P403 independent synthetic finished-garment source slice','P417 synthetic textile replenishment source slice').replace('P403 independent synthetic source slice; see 使用说明 for retained facts and changed assumptions; no enterprise execution evidence','P417 SYNTHETIC_RESEARCH_ASSUMPTION textile material, no execution evidence').replace('P403 synthetic slice reviewer','P417 synthetic source reviewer')
    def write(name,heads,rows):
        sh=book[name] if name in book.sheetnames else book.create_sheet(name)
        for ci,h in enumerate(heads,1):sh.cell(5,ci,h)
        for ri,values in enumerate(rows,6):
            for ci,value in enumerate(values,1):sh.cell(ri,ci,value)
    def source(r):return [r['provenance']['source_record_id'],r['provenance']['source_version'],'SYNTHETIC_RESEARCH_ASSUMPTION '+r['provenance']['source_locator']]
    write('地点主档',['地点编码','名称','时区','来源记录','来源版本','来源说明'],[[r['id'],r['name'],r['timezone'],*source(r)] for r in data['locations']])
    fields={'路径编码':'id','物料编码':'item_id','起运地点':'origin_location_id','目的地点':'destination_location_id','运输秒':'lead_time_seconds','最小配送量':'minimum_transfer_quantity','配送倍数':'transfer_multiple','单位':'uom','生效时间':'effective_from','失效时间':'effective_to','路径状态':'status'}
    write('配送路径',list(fields)+['来源记录','来源版本','来源说明'],[[r[k] for k in fields.values()]+source(r) for r in data['distribution_lanes']])
    option=data['distribution_lead_time_option'];write(SHEET,HEADERS,[[option['lane_id'],option['maximum_lead_time_seconds'],*source(option)]])
    write('使用说明',['项目','内容'],[['来源性质','P417 新声明的纺织面料补货研发合成资料，不是企业事实或SIM-GARMENT事实'],['业务终点','目的地材料需求满足；不是成衣交付、实际调拨或客户签收'],['能力边界','仅三地点、两路线、单笔完整未收confirmed PO及QC；新proposed PO/confirmed DO/MO/部分收货/运输日历/MINCOST不认证']])
    out=BytesIO();book.save(out);return out.getvalue()
