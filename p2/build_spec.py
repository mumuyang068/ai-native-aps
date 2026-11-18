"""Create explicit synthetic source tables and mappings from the frozen P0 fixture.

This prepares authoring data only. XLSX files are authored by artifact-tool.
The preflight never reads this authoring data or falls back to the P0 fixture.
"""
import copy
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
LABELS = {
    'id':'记录编码','name':'名称','kind':'类别','base_uom':'基本单位',
    'order_ref':'订单号','line_ref':'行号','item_id':'产品物料编码','location_id':'工厂编码',
    'quantity':'数量','uom':'数量单位','due_at':'承诺交期','priority':'优先级',
    'minimum_shipment':'最小交付量','maximum_lateness_seconds':'延期搜索窗口秒','status':'状态',
    'output_item_id':'产出产品编码','output_quantity':'产出基数','output_uom':'产出单位',
    'effective_from':'版本生效时间','effective_to':'版本失效时间',
    'lines.item_id':'材料编码','lines.quantity_per_output':'材料用量','lines.uom':'材料单位',
    'lines.scrap_fraction':'损耗比例','lines.consume_operation_id':'领料工序',
    'lines.consume_at':'领料时点','operations.id':'工序编码','operations.sequence':'工序顺序',
    'operations.predecessors':'前置工序','operations.duration_model':'工时模型',
    'operations.fixed_seconds':'固定准备秒','operations.seconds_per_unit':'每件工时秒',
    'operations.resource_id':'生产线编码','operations.resource_units':'占用资源数',
    'availability_calendar_id':'日历编码','parallel_units':'有效并行数',
    'timezone':'时区','available_intervals.start_at':'工作开始','available_intervals.end_at':'工作结束',
    'physical_lot_ref':'指定实物批次','on_hand':'实存数量','quality_blocked':'质检冻结量',
    'other_nonnettable':'其他不可用量','as_of':'库存时点','supplier_id':'供应商编码',
    'lead_time_seconds':'采购提前期秒','lead_time_basis':'提前期口径',
    'minimum_order_quantity':'最小采购量','order_multiple':'采购倍量','batch_window_seconds':'合批窗口秒',
    'bom_revision_id':'BOM版本','routing_revision_id':'工艺版本',
    'manufacturing_minimum_quantity':'最小制造批量','manufacturing_multiple':'制造倍量',
    'transfer_policy':'转运批量规则',
}
ENTITIES = [
    ('订单','demand_lines',None),('产品物料','items',None),('BOM','bom_revisions','lines'),
    ('工艺路线','routing_revisions','operations'),('生产配方','production_recipes',None),
    ('生产资源','resources',None),('工作日历','calendars','available_intervals'),
    ('库存','inventory_positions',None),('供应商','suppliers',None),('供应来源','supply_sources',None),
]
SOURCE = ['来源记录','来源版本','来源说明']

def kind(value):
    if isinstance(value, bool): return 'boolean'
    if isinstance(value, int): return 'integer'
    if isinstance(value, list): return 'list'
    if value is None: return 'nullable_string'
    if isinstance(value, str):
        try: float(value); return 'decimal_string'
        except ValueError: pass
        if 'T' in value and value.endswith('+08:00'): return 'datetime_offset'
    return 'string'

def flatten(d, prefix=''):
    out={}
    for k,v in d.items():
        if k=='provenance': continue
        key=prefix+k
        if isinstance(v,dict): out.update(flatten(v,key+'.'))
        else: out[key]=v
    return out

def encoded(v):
    if isinstance(v,list): return json.dumps(v,ensure_ascii=False)
    if kind(v)=='decimal_string': return float(v) if '.' in v else int(v)
    return v

def main():
    baseline=ROOT/'fixtures/baseline.json'
    b=json.loads(baseline.read_text(encoding='utf-8'))
    sha=hashlib.sha256(baseline.read_bytes()).hexdigest()
    profile={'version':'p2.0-mapping-v1','header_row':5,'source_sha256':sha,'sheets':{},
             'scope':'SYNTHETIC_ONLY','approval_policy':'ALL_MAPPED_AND_POLICY_FIELDS',
             'empty_arrays_require_review':['reservations','existing_orders'],
             'forbidden_inference':['capacity','routing','lead_time','uom_factor','recipe_binding','calendar','overtime_policy']}
    sheets=[]
    settings=flatten({k:b[k] for k in ['schema_version','snapshot_id','scenario_id','scenario_revision','planning_at','horizon','site','engine','rules']})
    settings['p2.allow_demo_customer']=True
    settings['p2.no_unlisted_supply_or_reservations']=True
    profile['settings']={k:{'type':kind(v),'critical':True,'target':'/'+k.replace('.','/'),
                            'source':'工厂参数','required':True} for k,v in settings.items()}
    sheets.append({'name':'工厂参数','headers':['字段路径','确认值','来源记录','来源版本','来源说明'],
                   'rows':[[k,encoded(v),'baseline#/'+k.replace('.','/'),'p2.0-sim-v1',
                            '仅模拟演示许可' if k.startswith('p2.') else '冻结 P0 显式值；规则需业务核对'] for k,v in settings.items()],
                   'note':'逐项核对规划时点、时区、引擎配置与规则；禁止加班是当前计划的显式规则。'})
    for sheet,key,nested in ENTITIES:
        first=b[key][0]
        fields=[k for k in first if k not in ('provenance',nested)]
        if nested: fields += [nested+'.'+k for k in first[nested][0]]
        cols=[]
        for f in fields:
            v=first[f] if '.' not in f else first[nested][0][f.split('.',1)[1]]
            cols.append({'field':f,'header':LABELS[f],'type':kind(v),'required':v is not None,
                         'critical':True,'target':'/'+key+'/*/'+f.replace('.','/'),
                         'unit_rule': 'CONVERT_TO_ITEM_BASE' if f in ('lines.quantity_per_output','on_hand','quality_blocked','other_nonnettable') else 'PRESERVE_EXPLICIT',
                         'nullable':v is None})
        extras=[]
        if key=='demand_lines': extras=['客户编码','联系人']
        if key=='inventory_positions': extras=['预留数量']
        rows=[]
        for i,obj in enumerate(b[key]):
            children=obj[nested] if nested else [None]
            for j,child in enumerate(children):
                vals=[obj[f] if '.' not in f else child[f.split('.',1)[1]] for f in fields]
                vals=[encoded(v) for v in vals]
                if sheet=='BOM':
                    vals[fields.index('lines.quantity_per_output')]=200
                    vals[fields.index('lines.uom')]='cm'
                if sheet=='库存' and obj['item_id']=='FAB-BLK':
                    vals[fields.index('on_hand')]=8; vals[fields.index('uom')]='卷'
                vals += (['C-DEMO-01','模拟跟单员'] if sheet=='订单' else [0] if sheet=='库存' else [])
                locator=f'baseline#/{key}/{i}'+(f'/{nested}/{j}' if nested else '')
                note='P0 冻结参数；仅模拟工厂'
                if sheet=='BOM': note='2 米改写为 200 cm；换算见单位换算表'
                if sheet=='库存' and obj['item_id']=='FAB-BLK': note='800 米改写为 8 卷；100 米/卷为显式模拟包装参数；包含已收采购'
                vals += [locator,'p2.0-sim-v1',note]; rows.append(vals)
        profile['sheets'][sheet]={'entity':key,'nested':nested,'columns':cols,'extras':extras,'key':'id',
                                  'source_columns':SOURCE,'required':True}
        sheets.append({'name':sheet,'headers':[c['header'] for c in cols]+extras+SOURCE,'rows':rows,
                       'note':'时间保留具体时刻与〔+08:00〕偏移；空白不等于零。' if sheet!='工艺路线' else '工时为每件占用生产线的秒数；不从“日产量”推测有效工时。'})
    aux=[
        {'name':'单位换算','headers':['物料编码','原单位','基本单位','换算倍数']+SOURCE,
         'rows':[['FAB-BLK','cm','m',0.01,'p2:unit-cm','p2.0-sim-v1','长度单位显式换算，待模拟业务确认'],
                 ['FAB-BLK','卷','m',100,'p2:unit-roll','p2.0-sim-v1','仅此模拟包装：100 米/卷；真实工厂不可沿用']],
         'note':'数量 × 换算倍数 = 基本数量。包装换算必须由企业核实，不能凭单位名称猜测。'},
        {'name':'采购台账','headers':['采购单号','物料编码','供应商编码','订购量','已收量','未收量','单位','状态','预计到货','库存已含收货']+SOURCE,
         'rows':[['PO-HIST-01','FAB-BLK','SUP-A',800,800,0,'m','CLOSED','2026-10-13T17:00:00+08:00',True,'p2:closed-po','p2.0-sim-v1','补充的模拟历史凭证；已收 800 米已包含于库存，不重复入供应']],
         'note':'仅零未收、已关闭且明确库存已含的历史记录可归档；在途采购必须保留并阻断当前适配器。'},
    ]
    sheets += aux
    profile['auxiliary']={'单位换算':{'headers':aux[0]['headers'],'required':True},
                          '采购台账':{'headers':aux[1]['headers'],'required':True}}
    cases=[]
    def cell(case,sheet,header,row,value,reason):
        tab=next(s for s in case['sheets'] if s['name']==sheet)
        tab['rows'][row][tab['headers'].index(header)]=value
        tab['rows'][row][-1]+='；专项扰动：'+reason
        case['changes'].append({'sheet':sheet,'header':header,'row':row+6,'reason':reason})
    descriptions=[('01_规范样本','基础字段完整；模拟人工确认后可静态转换。'),
                  ('02_缺失字段','缺少工时、并行数、采购提前期；联系人空白仅提示。'),
                  ('03_字段歧义','不明编码、用量单位和日产量口径；须人工确认。'),
                  ('04_数据冲突','重复主键、配方版本、库存时点和重叠日历冲突。'),
                  ('05_在途与预留边界','有效在途采购和硬预留是真实业务形态，但当前适配器不支持。')]
    for name,description in descriptions:
        cases.append({'name':name,'description':description,'sheets':copy.deepcopy(sheets),'changes':[]})
    for s,h,r in [('工艺路线','每件工时秒',1),('生产资源','有效并行数',1),('供应来源','采购提前期秒',0)]:
        cell(cases[1],s,h,r,None,'关键参数缺失，禁止填默认值')
    cell(cases[1],'订单','联系人',0,None,'可选联系字段缺失，不影响计算')
    orders=next(s for s in cases[2]['sheets'] if s['name']=='订单')
    orders['headers'][orders['headers'].index('产品物料编码')]='编码'
    routes=next(s for s in cases[2]['sheets'] if s['name']=='工艺路线')
    routes['headers'][routes['headers'].index('每件工时秒')]='日产量'
    routes['rows'][0][[c['header'] for c in profile['sheets']['工艺路线']['columns']].index('每件工时秒')]=1200
    cell(cases[2],'BOM','材料单位',0,None,'“用量”未给出单位，不能猜 cm 或 m')
    cell(cases[3],'生产配方','工艺版本',0,'ROUTE-TEE-R9','配方引用不存在的版本')
    cell(cases[3],'库存','库存时点',0,'2026-10-13T09:00:00+08:00','不是规划起点库存快照')
    cell(cases[3],'工作日历','工作开始',1,'2026-10-14T11:00:00+08:00','工作区间重叠')
    tab=next(s for s in cases[3]['sheets'] if s['name']=='产品物料')
    dup=copy.deepcopy(tab['rows'][0]);dup[tab['headers'].index('基本单位')]='kg';dup[-1]='专项扰动：同编码不同单位';tab['rows'].append(dup)
    cell(cases[4],'库存','预留数量',0,1,'1 卷硬预留，不能简单从实存中扣除后丢弃需求关系')
    po=next(s for s in cases[4]['sheets'] if s['name']=='采购台账')
    po['rows'].append(['PO-OPEN-02','FAB-BLK','SUP-A',200,0,200,'m','OPEN','2026-10-18T09:00:00+08:00',False,'p2:open-po','p2.0-sim-v1','专项扰动：未结采购；现有适配器不支持'])
    HERE.joinpath('contracts').mkdir(exist_ok=True)
    HERE.joinpath('artifacts').mkdir(exist_ok=True)
    HERE.joinpath('contracts/mapping.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    HERE.joinpath('artifacts/authoring-spec.json').write_text(json.dumps({'source_sha256':sha,'cases':cases},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    rows=['# 字段映射规范','',f'版本 `{profile["version"]}`，来源为冻结 P0 模拟夹具 SHA-256 `{sha}`。',
          '', '表头固定在第 5 行，第 6 行起为数据。字段别名只是候选；当前离线检查器只接受明确映射，表头变化阻断。时间源为显式文本 `2026-10-14 09:00:00〔+08:00〕`，确定性转换成 ISO `2026-10-14T09:00:00+08:00`；也接受带偏移的完整 ISO 文本。禁止只凭日期默认补成 18:00。',
          '', '数字列为数值；十进制业务量写回统一模型时使用十进制字符串。空白不等于 0；允许为空的指定批次除外。前置工序为 JSON 数组文本。',
          '', '以下业务字段均需人工确认；“直接转换”仅指确认后的确定性转换，不代表可跳过确认。来源记录、来源版本、来源说明和原始文件哈希随导入版本保留。',
          '', '|工作表|企业字段|统一模型路径|类型|必填|转换/校验|','|---|---|---|---|---|---|']
    for s,c in profile['sheets'].items():
        for col in c['columns']:
            rows.append(f'|{s}|{col["header"]}|`{col["target"]}`|{col["type"]}|{"是" if col["required"] else "允许显式空值"}|{col["unit_rule"]}；主外键/版本/日历见预检|')
    rows += ['', '工厂参数采用字段路径/确认值结构，以下每项是输入，不是隐藏默认值：','', '|路径|类型|必填|','|---|---|---|']
    rows += [f'|`{k}`|{v["type"]}|是|' for k,v in profile['settings'].items()]
    rows += ['', '单位换算表：物料编码+原单位为唯一键，基本单位必须匹配物料主档，倍数为大于零的已确认值。包装口径变化必须生成新版本。',
             '', '采购台账：订单号为唯一键，订购量=已收量+未收量；OPEN/部分收货或非零未收阻断当前适配器。CLOSED、未收为零、已收已含库存且到货不晚于库存时点，才归档且不重复增加供应。',
             '', '客户编码、联系人留在来源旁表：客户是业务必核身份，联系人可选。现有统一模型无客户字段、适配器固定 CUSTOMER-DEMO，仅明确模拟许可下可导出；真实企业接入前须扩展客户映射。',
             '', '库存预留数量为非负；大于零时保留原始记录并报能力缺口，不将其伪装为一般不可用量。零预留、无未列示在途/在制的声明也需业务确认。',
             '', '工序、BOM、配方、资源、日历、库存、供应商及采购提前期、单位换算、损耗、准备工时、规则均禁止 AI 自行补全。优先级、整批交付、延期搜索窗口、合批/倍量和转运规则可由业务接受明确的策略版本；未确认前不能参与计算。',
             '', '供应来源有效期必须覆盖规划起点与规划范围；当前适配器未映射供应有效期至原生模型，离线预检只允许覆盖全范围的样本，超出需扩展，禁止忽略时间限制。']
    HERE.joinpath('字段映射规范.md').write_text('\n'.join(rows)+'\n',encoding='utf-8')
    print(f'Prepared {len(cases)} synthetic cases, {len(sheets)} business sheets each')

if __name__=='__main__': main()
