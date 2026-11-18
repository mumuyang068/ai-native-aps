"""Declare one new research source beside the unchanged frozen P405 input facts.

Only produces source JSON/Excel bytes. No import, confirmation, plan or solver.
The P405 input path is explicit and its original byte SHA must match.
"""
import copy
import hashlib
import json
from io import BytesIO
from pathlib import Path

from p2.accessory_supply_import import PROFILE

P405_SOURCE_SHA256 = 'a42edbec75a9dc081502ad0dc04f70b21c9a1698bb4778beacc90681099b7279'
VERSION = 'P406-ENTERPRISE-LABEL-SOURCE-V1'
SUPPLIER = 'P406-LABEL-SUP'
SOURCE = 'P406-LABEL-SOURCE'
OBSERVED_AT = '2026-10-20T22:20:00+08:00'


def source_backed_slice(source_input):
    """The only additive source assumptions are explicitly identified here."""
    raw = Path(source_input).read_bytes()
    if hashlib.sha256(raw).hexdigest() != P405_SOURCE_SHA256:
        raise ValueError('P405_ORIGINAL_FROZEN_SOURCE_SHA_MISMATCH')
    original = json.loads(raw)
    d = copy.deepcopy(original)
    if any(s.get('id') == SOURCE or s.get('item_id') == 'LABEL' for s in d['supply_sources']):
        raise ValueError('Original LABEL source unexpectedly exists')
    d.update(snapshot_id=VERSION, scenario_id=VERSION, scenario_revision=0)
    d['rules']['manufacturing_profile'] = PROFILE
    def provenance(record):
        return dict(source_system='SYNTHETIC_RESEARCH_ASSUMPTION', source_record_id=record,
            source_version=VERSION, observed_at=OBSERVED_AT, import_batch_id=VERSION,
            source_locator='simulation://'+VERSION+'/'+record)
    d['suppliers'].append(dict(id=SUPPLIER, name='P406 独立研发合成LABEL供给来源，非真实供应承诺',
        provenance=provenance('SUPPLIER')))
    d['supply_sources'].append(dict(id=SOURCE, supplier_id=SUPPLIER, item_id='LABEL', location_id='FACTORY',
        lead_time_seconds=86400, lead_time_basis='CALENDAR_TIME', minimum_order_quantity='10',
        order_multiple='10', batch_window_seconds=0, effective_from=d['horizon']['start_at'],
        effective_to=d['horizon']['end_at'], receiving_qc=dict(wait_seconds=86400, provenance=provenance('LABEL-QC-24H')),
        provenance=provenance('SOURCE')))
    d['accessory_supply_option'] = dict(source_id=SOURCE, enabled=False,
        earliest_order_at=d['planning_at'], latest_order_at='2026-11-28T00:00:00+08:00',
        total_capacity_quantity='20', uom='个', fact_kind='SYNTHETIC_RESEARCH_ASSUMPTION',
        permission='SIMULATION_ONLY', provenance=provenance('SOURCE-OPTION'))
    declared = dict(original_input_sha256=P405_SOURCE_SHA256,
        retained='Original P405 Firm/Gross/netting source, confirmed fabric PO, inventory quality, BOM, calendars, resources and skills unchanged.',
        added_source=dict(id=SOURCE, supplier_id=SUPPLIER, item='LABEL', location='FACTORY', uom='个',
            lead_time_seconds=86400, receiving_qc_wait_seconds=86400, minimum_order_quantity='10', order_multiple='10',
            batch_window_seconds=0, total_capacity_quantity='20', capacity_scope='WHOLE_PLANNING_HORIZON_SHARED_TOTAL',
            effective_from=d['horizon']['start_at'], effective_to=d['horizon']['end_at'],
            earliest_order_at=d['planning_at'], latest_order_at='2026-11-28T00:00:00+08:00',
            unit_price='NOT_PROVIDED', currency='NOT_PROVIDED', permission='SIMULATION_ONLY'),
        exclusions='Not a frozen SIM source, supplier quote, confirmed PO, purchase approval, actual order, receipt or quality release. No promise of an 80-unit improvement.',
        source_option_default='DISABLED_IN_BASELINE; enable only in an independently identified scenario draft')
    d['assumptions'].append(dict(id='P406-LABEL-RESEARCH-SOURCE', field_ref='accessory_supply_option',
        value=json.dumps(declared, ensure_ascii=False, sort_keys=True),
        reason='One independently declared research source, frozen before new computation; no original enterprise facts rewritten.',
        acknowledged_by='P406_SYNTHETIC_SOURCE_AUTHOR', acknowledged_at=OBSERVED_AT))
    return d


def workbook_bytes(data):
    from openpyxl import load_workbook
    from p2.enterprise_forecast_fixture import workbook_bytes as enterprise_workbook
    from p2.accessory_supply_import import write_sheet
    book = load_workbook(BytesIO(enterprise_workbook(data)))
    source = next(s for s in data['supply_sources'] if s['id'] == data['accessory_supply_option']['source_id'])
    write_sheet(book, data['accessory_supply_option'], source)
    out = BytesIO()
    book.save(out)
    return out.getvalue()
