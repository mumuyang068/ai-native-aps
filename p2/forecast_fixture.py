"""Author a source-backed synthetic forecast slice for the existing P2 flow.

The frozen release is read only. This module creates no import, version, plan,
run, customer promise or solver output. New manufacturing conditions and firm
orders are explicitly declared synthetic, independent of the full SIM model.
"""
import copy
import hashlib
import json
from io import BytesIO
from pathlib import Path

from demand_forecast import PROFILE, POLICY, COVERAGE


def source_backed_slice(release_root):
    root = Path(release_root)
    folder = root / 'synthetic/SIM-GARMENT-01/V1/normalized'
    evidence = {}
    def table(name):
        path = folder / (name + '.json')
        raw = path.read_bytes()
        evidence[name] = dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest())
        return json.loads(raw)
    frozen = next(f for f in table('forecasts') if f['id'] == 'FG-TB-M-CUST-01-2026-11')
    original_items = [i for i in table('items') if i['id'] in ('FG-TB-M', 'FAB-BLK')]
    factory = next(x for x in table('locations') if x['id'] == frozen['location_id'])
    customer = next(x for x in table('customers') if x['id'] == frozen['customer_id'])
    history = table('sales_history')
    version = 'P404-MANUAL-FORECAST-V1'
    observed, start, end = '2026-10-20T17:00:00+08:00', frozen['bucket_start'], frozen['bucket_end']
    def declared(record):
        return dict(source_system='P404_DECLARED_SYNTHETIC', source_record_id=record, source_version=version,
                    observed_at=observed, import_batch_id='P404-FORECAST-SLICE-V1', source_locator='p2.forecast_fixture#' + record)
    def original(row, name):
        return dict(source_system='APS_PRO_FROZEN_SYNTHETIC', source_record_id=row['entity_id'], source_version=row['dataset_id'],
                    observed_at=observed, import_batch_id='P404-FORECAST-SLICE-V1', source_locator=evidence[name]['path'] + '#' + row['id'])
    def assumption(record, field, value, reason):
        return dict(id=record, field_ref=field, value=value, reason=reason, acknowledged_by='P404_SYNTHETIC_SLICE_AUTHOR', acknowledged_at=observed)
    forecast = {key: str(frozen[key]) if key == 'gross_quantity' else frozen[key] for key in
                ('id', *('item_id', 'location_id', 'customer_id', 'uom'), 'bucket_start', 'bucket_end', 'gross_quantity')}
    forecast.update(version_id=version, policy_id=POLICY, coverage=COVERAGE, evidence_status='SOURCE_CONFIRMED_SYNTHETIC',
                    net_demand=dict(due_at='2026-11-30T18:00:00+08:00', priority=10, minimum_shipment='1', maximum_lateness_seconds=0),
                    provenance=original(frozen, 'forecasts'))
    orders = []
    for i, due in enumerate(('2026-11-06T18:00:00+08:00', '2026-11-09T18:00:00+08:00'), 1):
        ident = 'P404-FORECAST-FIRM-' + str(i) + '-L1'
        orders.append(dict(id=ident, order_ref='P404-FORECAST-FIRM-' + str(i), line_ref='L1',
                           item_id=frozen['item_id'], location_id=frozen['location_id'], customer_id=customer['id'],
                           quantity='6', uom=frozen['uom'], due_at=due, priority=i, minimum_shipment='1',
                           maximum_lateness_seconds=0, status='OPEN', kind='FIRM_ORDER', provenance=declared(ident)))
    fg, raw = frozen['item_id'], 'FAB-BLK'
    conditions = dict(firm_orders='Two new synthetic OPEN orders of 6 件 each; not rows from frozen demand_lines or closed sales_history',
        manufacturing='Independent simplified one-step continuous demonstration: 100 seconds/件, zero setup, 1 米 FAB-BLK per 件 FG-TB-M, no loss; not SIM CUT/SEW/QC/PACK certification',
        inventory='New synthetic opening FAB-BLK=100 米, FG-TB-M=0 件, quality_blocked=0, other_nonnettable=0; no PO/MO/supplier/reservations',
        calendar='New synthetic available interval 2026-11-02 08:00 to 2026-11-30 18:00 +08, no business calendar inferred',
        policy=dict(policy_id=POLICY, coverage=COVERAGE, net_due_at=forecast['net_demand']['due_at'], priority=10, minimum_shipment='1', maximum_lateness_seconds=0),
        history='sales_history retains monthly synthetic observations, is_true_zero and stockout_evidence; none of its ' + str(len(history)) + ' records becomes a future demand')
    d = dict(schema_version='0.2.0', snapshot_id='SNAP-P404-FORECAST-V1', scenario_id='P404-FORECAST-SOURCE-SLICE', scenario_revision=0,
        planning_at=start, horizon=dict(start_at=start, end_at=end),
        site=dict(id=factory['id'], name=factory['name'], timezone=factory['timezone'], provenance=original(factory, 'locations')),
        engine=dict(profile='frepple-community-9.19-supply-v1', engine='frepple', target_version='9.19.0', problem_kind='SUPPLY_PLANNING'),
        rules=dict(profile_version='v1', finite_material=True, finite_capacity=True, allow_overtime=False, freeze_existing=True,
                   manufacturing_profile=PROFILE, move_approved_early=0, erase_previous_proposed=True, fix_broken_supply_path=False),
        items=[dict(id=x['id'], kind=x['kind'], base_uom=x['base_uom'], provenance=original(x, 'items')) for x in original_items],
        calendars=[dict(id='P404-FORECAST-WORK', timezone=factory['timezone'], available_intervals=[dict(start_at='2026-11-02T08:00:00+08:00', end_at='2026-11-30T18:00:00+08:00')], provenance=declared('CALENDAR'))],
        resources=[dict(id='P404-FORECAST-LINE', location_id=factory['id'], availability_calendar_id='P404-FORECAST-WORK', parallel_units='1', provenance=declared('RESOURCE'))],
        bom_revisions=[dict(id='P404-FORECAST-BOM', output_item_id=fg, output_quantity='1', output_uom=frozen['uom'], effective_from=start, effective_to=end,
            lines=[dict(item_id=raw, quantity_per_output='1', uom=next(i['base_uom'] for i in original_items if i['id'] == raw), scrap_fraction='0', consume_operation_id='FORECAST-MAKE', consume_at='START')], provenance=declared('BOM'))],
        routing_revisions=[dict(id='P404-FORECAST-ROUTE', output_item_id=fg, effective_from=start, effective_to=end,
            operations=[dict(id='FORECAST-MAKE', sequence=1, predecessors=[], duration_model='FIXED_PLUS_PER_UNIT', fixed_seconds='0', seconds_per_unit='100', resource_id='P404-FORECAST-LINE', resource_units='1')], provenance=declared('ROUTE'))],
        production_recipes=[dict(id='P404-FORECAST-RECIPE', output_item_id=fg, location_id=factory['id'], bom_revision_id='P404-FORECAST-BOM', routing_revision_id='P404-FORECAST-ROUTE',
            effective_from=start, effective_to=end, manufacturing_minimum_quantity='1', manufacturing_multiple='1', transfer_policy='WHOLE_MANUFACTURING_BATCH', provenance=declared('RECIPE'))],
        inventory_positions=[dict(id='P404-FORECAST-INV-' + x['id'], item_id=x['id'], location_id=factory['id'], physical_lot_ref=None,
            on_hand='100' if x['id'] == raw else '0', quality_blocked='0', other_nonnettable='0', uom=x['base_uom'], as_of=start, provenance=declared('INVENTORY/' + x['id'])) for x in original_items],
        reservations=[], suppliers=[], supply_sources=[], existing_orders=[], demand_lines=orders, forecasts=[forecast],
        assumptions=[assumption('P404-FROZEN-FORECAST', 'forecasts/0', json.dumps(dict(original_record=frozen, source_files=evidence), ensure_ascii=False, sort_keys=True), 'Frozen fields are retained; net_quantity=null and netting_policy=NOT_SPECIFIED are not reclassified as an approved policy.'),
                     assumption('P404-NEW-CONDITIONS', 'synthetic_conditions', json.dumps(conditions, ensure_ascii=False, sort_keys=True), 'All conditions in this record are new stage-specific synthetic inputs, not original enterprise facts or engine results.')])
    return d


def workbook_bytes(data):
    """Encode actual source cells, retaining source facts and policy separately."""
    from openpyxl import Workbook
    from p2.preflight import PROFILE as MAPPING
    from p2.forecast_import import SHEET, FIELDS, HEADERS, ORDER_FIELDS
    book = Workbook(); book.remove(book.active)
    def tab(name, heads, rows):
        sheet = book.create_sheet(name)
        sheet.append(['P404 source-backed synthetic forecast planning slice'])
        for _ in range(3):sheet.append([None])
        sheet.append(heads)
        for row in rows:sheet.append(row)
    def encoded(value):
        return json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value
    def source(obj):
        p = obj.get('provenance', {})
        return [p.get('source_record_id', obj.get('id', 'P404-SETTING')), p.get('source_version', data['snapshot_id']),
                json.dumps(dict(provenance=p, slice='P404 independent synthetic conditions; see 使用说明'), ensure_ascii=False, sort_keys=True)]
    settings = []
    for key in MAPPING['settings']:
        try:
            value = data
            for part in key.split('.'):value = value[part]
        except KeyError:
            if key == 'p2.allow_demo_customer':value = False
            elif key == 'p2.no_unlisted_supply_or_reservations':value = True
            else:continue
        settings.append([key, encoded(value), key, data['snapshot_id'], 'P404 explicitly declared synthetic planning configuration'])
    tab('工厂参数', ['字段路径', '确认值', '来源记录', '来源版本', '来源说明'], settings)
    for name, conf in MAPPING['sheets'].items():
        heads = list(dict.fromkeys([c['header'] for c in conf['columns']] + conf['extras'] + conf['source_columns'] + conf.get('optional_headers', []) + (list(ORDER_FIELDS) if name == '订单' else [])))
        rows = []
        for obj in data.get(conf['entity'], []):
            children = obj[conf['nested']] if conf['nested'] else [None]
            for child in children:
                values = {c['header']: encoded(obj.get(c['field']) if '.' not in c['field'] else child.get(c['field'].split('.', 1)[1])) for c in conf['columns']}
                if name == '订单':values.update(客户编码=obj['customer_id'], 联系人='P404 synthetic source reviewer', **{h:obj.get(f) for h,(f,_) in ORDER_FIELDS.items()})
                if name == '库存':values.update({h:0 for h in conf['extras']})
                values.update(dict(zip(conf['source_columns'], source(obj))))
                rows.append([values.get(h) for h in heads])
        tab(name, heads, rows)
    for name, conf in MAPPING['auxiliary'].items():
        if name != SHEET:tab(name, conf['headers'] + conf.get('optional_headers', []), [])
    rows = []
    for forecast in data['forecasts']:
        values = {}
        for header, (field, _) in FIELDS.items():
            value = forecast
            for part in field.split('.'):value = value[part]
            values[header] = encoded(value)
        values.update(dict(zip(['来源记录', '来源版本', '来源说明'], source(forecast))))
        rows.append([values.get(h) for h in HEADERS])
    tab(SHEET, HEADERS, rows)
    tab('使用说明', ['项目', '内容'], [[x['id'], x['value']] for x in data.get('assumptions', [])])
    stream = BytesIO();book.save(stream)
    return stream.getvalue()
