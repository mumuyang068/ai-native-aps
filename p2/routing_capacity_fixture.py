"""Write declared synthetic inputs to the existing P2 Excel contract, no solve."""
from io import BytesIO
from openpyxl import load_workbook
def workbook_bytes(data):
    from p2.multi_receipt_fixture import workbook_bytes as receipt_workbook
    book=load_workbook(BytesIO(receipt_workbook(data)))
    specs={o['id']:o for r in data['routing_revisions'] for o in r['operations']}
    sh=book['工艺路线'];headers=[c.value for c in sh[5]]
    ci=headers.index('设备必需技能')+1 if '设备必需技能' in headers else sh.max_column+1
    sh.cell(5,ci,'设备必需技能')
    for ri in range(6,sh.max_row+1):
        op=specs.get(sh.cell(ri,headers.index('工序编码')+1).value,{})
        if op.get('machine_skill_id'):sh.cell(ri,ci,op['machine_skill_id'])
    customers={d['id']:d['customer_id'] for d in data['demand_lines']}
    sh=book['订单'];heads=[c.value for c in sh[5]]
    for ri in range(6,sh.max_row+1):
        did=sh.cell(ri,heads.index('记录编码')+1).value
        if did in customers:sh.cell(ri,heads.index('客户编码')+1,customers[did])
    for sheet in book:
        for row in sheet:
            for c in row:
                if isinstance(c.value,str):c.value=c.value.replace('P415','P416').replace('P403','P416')
    out=BytesIO();book.save(out);return out.getvalue()
