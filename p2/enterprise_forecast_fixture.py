"""New declared enterprise slice, never a rewrite of the frozen SIM or old runs."""
import copy
import hashlib
import json
from pathlib import Path

from demand_forecast import POLICY, COVERAGE
from enterprise_forecast import PROFILE


def source_backed_slice(release_root, *, pending=False):
    root = Path(release_root)
    source = root / 'synthetic/SIM-GARMENT-01/V1/normalized/forecasts.json'
    frozen = next(x for x in json.loads(source.read_bytes()) if x['id'] == 'FG-TB-M-CUST-01-2026-11')
    recipe_file = Path(__file__).parents[1] / 'fixtures/finished-garment-confirmed.json'
    d = json.loads(recipe_file.read_text('utf8').replace('P403', 'P405'))
    version = 'P405-ENTERPRISE-' + ('PENDING' if pending else 'RELEASED') + '-V1'
    start, end = frozen['bucket_start'], frozen['bucket_end']
    observed = '2026-10-20T17:00:00+08:00'
    def provenance(record):
        return dict(source_system='P405_DECLARED_SYNTHETIC', source_record_id=record, source_version=version,
            observed_at=observed, import_batch_id=version, source_locator='p2.enterprise_forecast_fixture#' + record)
    d.update(snapshot_id=version, scenario_id=version, scenario_revision=0, planning_at=start,
        horizon=dict(start_at=start, end_at=end))
    d['rules']['manufacturing_profile'] = PROFILE
    for group in ['production_recipes', 'routing_revisions', 'bom_revisions', 'resource_skills', 'supply_sources']:
        for row in d[group]:
            row['effective_to'] = end
            if group != 'supply_sources':
                row['effective_from'] = start
    for group in ['items', 'calendars', 'resources', 'production_recipes', 'routing_revisions', 'bom_revisions',
                  'inventory_positions', 'suppliers', 'supply_sources', 'existing_orders', 'skills', 'resource_skills']:
        for index, row in enumerate(d[group]):
            row['provenance'] = provenance(group + '/' + str(row.get('id', index)))
    for inv in d['inventory_positions']:
        inv['as_of'] = start
        if inv['item_id'] == 'LABEL':
            inv.update(on_hand='20', quality_blocked='8')
        if inv['item_id'] == 'FG-TB-M':
            inv.update(on_hand='20', quality_blocked='8', other_nonnettable='0')
            inv['quality_availability'] = dict(inspection_pending_quantity='12' if pending else '4',
                released_quantity='0' if pending else '8', release_reference=None if pending else 'P405-SYNTHETIC-RELEASE8',
                evidence_kind='NO_RELEASE' if pending else 'SYNTHETIC_CONFIRMED_RELEASE', effective_at=start,
                provenance=provenance('FG-OPENING-QUALITY'))
    template = d['demand_lines'][0]
    d['demand_lines'] = []
    for i, due in enumerate(['2026-11-06T18:00:00+08:00', '2026-11-09T18:00:00+08:00'], 1):
        order = copy.deepcopy(template)
        order.update(id=f'P405-FIRM-{i}-L1', order_ref=f'P405-FIRM-{i}', line_ref='L1', kind='FIRM_ORDER',
            customer_id=frozen['customer_id'], quantity='6', priority=i, minimum_shipment='1', due_at=due,
            provenance=provenance(f'FIRM-{i}-L1'))
        d['demand_lines'].append(order)
    f = {k: str(frozen[k]) if k == 'gross_quantity' else frozen[k] for k in
         ['id', 'item_id', 'location_id', 'customer_id', 'uom', 'bucket_start', 'bucket_end', 'gross_quantity']}
    f.update(version_id=version, policy_id=POLICY, coverage=COVERAGE, evidence_status='SOURCE_CONFIRMED_SYNTHETIC',
        net_demand=dict(due_at='2026-11-30T18:00:00+08:00', priority=10, minimum_shipment='1', maximum_lateness_seconds=0),
        provenance=dict(source_system='APS_PRO_FROZEN_SYNTHETIC', source_record_id=frozen['entity_id'],
            source_version=frozen['dataset_id'], observed_at=observed, import_batch_id=version,
            source_locator='APS-PRO-DATA-V1.0.0/synthetic/SIM-GARMENT-01/V1/normalized/forecasts.json#' + frozen['id']))
    d['forecasts'] = [f]
    boundaries = [start, '2026-11-06T00:00:00+08:00', end]
    d['inventory_policies'] = [dict(id='P405-FG-POLICY-' + str(i+1), policy_version='P405-FG-TARGET10-V1',
        fact_kind='SYNTHETIC_POLICY_ASSUMPTION', target_kind='MINIMUM_NET_AVAILABLE',
        item_id=frozen['item_id'], location_id=frozen['location_id'], uom=frozen['uom'],
        start_at=a, end_at=b, target_quantity='10', provenance=provenance('FG-POLICY-' + str(i+1)))
        for i, (a, b) in enumerate(zip(boundaries, boundaries[1:]))]
    d['assumptions'] = [dict(id='P405-SOURCE-AND-ASSUMPTIONS', field_ref='synthetic_enterprise_scope',
        value=json.dumps(dict(forecast_record=frozen, forecast_file_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            manufacturing_template='fixtures/finished-garment-confirmed.json', template_sha256=hashlib.sha256(recipe_file.read_bytes()).hexdigest(),
            identity_mapping={'P403-CUST-01': 'NO_ENTERPRISE_IDENTITY_EQUIVALENCE; new P405 orders explicitly addressed to frozen CUST-01',
                'P403 objects': 'new independent P405 synthetic identities; retained process quantities are declared assumptions'},
            new_facts=['Two new OPEN orders, 6 each, explicit final FG demand', 'Gross100 retained as input, not promised supply',
                'Same CUT20/3600, SEW480, QC180/PACK120, four machines and qualified named people',
                'One new synthetic confirmed fabric PO20; physical Nov3 09, QC Nov4 09; no new proposed supply certified',
                'Only inherited Nov2-6 work windows exist; no full-month or full-factory calendar claim',
                'Monthly horizon and validity are new P405 assumptions; no enterprise contract extension',
                'FG physical20/frozen8/pending4/net8 or controlled pending12/net0; LABEL physical20/frozen8/net12',
                'No existing MO, DO, reservation, future quality release or original SIM orders included'],
            limits='Not full SIM, an official demo, real enterprise quality release, execution or customer promise'), ensure_ascii=False, sort_keys=True),
        reason='New source-backed synthetic combination, frozen before calculation; source facts and new assumptions explicitly separated.',
        acknowledged_by='P405_SYNTHETIC_SLICE_AUTHOR', acknowledged_at=observed)]
    return d


def workbook_bytes(data):
    from io import BytesIO
    from openpyxl import load_workbook
    from p2.finished_delivery_fixture import workbook_bytes as manufacturing_workbook
    from p2.forecast_import import FIELDS, HEADERS
    book = load_workbook(BytesIO(manufacturing_workbook(data)))
    orders = book['订单']
    headers = [c.value for c in orders[5]]
    for ri, obj in enumerate(data['demand_lines'], 6):
        orders.cell(ri, headers.index('需求类型') + 1, obj['kind'])
    sheet = book['预测需求']
    for ci, head in enumerate(HEADERS, 1): sheet.cell(5, ci, head)
    for ri, f in enumerate(data['forecasts'], 6):
        values = {}
        for h, (field, _) in FIELDS.items():
            v = f
            for part in field.split('.'): v = v[part]
            values[h] = v
        values.update(来源记录=f['provenance']['source_record_id'], 来源版本=f['provenance']['source_version'],
            来源说明=json.dumps(f['provenance'], ensure_ascii=False))
        for ci, head in enumerate(HEADERS, 1): sheet.cell(ri, ci, values[head])
    if data.get('inventory_policies'):
        from p2.inventory_policy_import import write_sheet
        write_sheet(book, data['inventory_policies'])
    out = BytesIO(); book.save(out)
    return out.getvalue()
