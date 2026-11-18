"""Emit the declared synthetic receipt slice as source Excel, without importing or solving."""
from io import BytesIO
import copy
from openpyxl import load_workbook


def workbook_bytes(data):
    from p2.finished_delivery_fixture import workbook_bytes as existing_writer
    from p2.multi_receipt_import import SHEET,FIELDS,HEADERS
    d=copy.deepcopy(data)
    # The existing source-cell writer accepts PO rows and customer columns.
    d['existing_orders']=[r for r in d['existing_orders'] if r['type']=='PO']
    for row in d['demand_lines']:row['customer_id']='P415-SYNTHETIC-CUSTOMER'
    book=load_workbook(BytesIO(existing_writer(d)))
    for sheet in book:
        for row in sheet:
            for cell in row:
                if isinstance(cell.value,str) and 'P403' in cell.value:cell.value=cell.value.replace('P403','P415')
    sheet=book[SHEET]
    for ci,h in enumerate(HEADERS,1):sheet.cell(5,ci,h)
    for ri,r in enumerate(data['purchase_receipts'],6):
        p=r['provenance'];values={h:r[f] for h,(f,_) in FIELDS.items()}
        values.update(来源记录=r['receipt_reference'],来源版本=p['source_version'],来源说明='SYNTHETIC_ASSUMPTION '+p['source_locator'])
        for ci,h in enumerate(HEADERS,1):sheet.cell(ri,ci,values[h])
    sheet=book['制造工单'];headers=[c.value for c in sheet[5]]
    for ri,m in enumerate([r for r in data['existing_orders'] if r['type']=='MO'],6):
        c=m['operations'][0]
        values=dict(工单号=m['id'],外部工单号=m['external_ref'],产品物料编码=m['item_id'],工厂编码=m['location_id'],配方编码=m['recipe_id'],
            制造数量=m['quantity'],已完工数量=m['completed_quantity'],单位=m['uom'],状态=m['source_status'],冻结规则=m['freeze_policy'],
            工序编码=c['operation_id'],资源编码=c['resource_id'],操作员编码=c['operator_id'],单件窗口用量=c['resource_units'],原开始=c['start_at'],原结束=c['end_at'],
            来源记录=m['id'],来源版本=m['provenance']['source_version'],来源说明='SYNTHETIC_ASSUMPTION '+m['provenance']['source_locator'])
        for ci,h in enumerate(headers,1):sheet.cell(ri,ci,values[h])
    out=BytesIO();book.save(out);return out.getvalue()
