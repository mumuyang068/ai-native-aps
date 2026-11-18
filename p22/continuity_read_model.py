"""Read-only presentation of the original same-run continuity audit."""
import copy
import hashlib
import json
from decimal import Decimal, InvalidOperation


POINTER = '/interpretation/foundation_validation/checks/continuity'


def _source(value, run_id, pointer):
    return dict(owner='p22.continuity_audit.audit', run_id=run_id, pointer=pointer,
        sha256=hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode()).hexdigest())


def _quantity(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result >= 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def continuity_wip_context(interpretation, run_id):
    """Label existing observations; do not calculate WIP or certify execution."""
    foundation = (interpretation or {}).get('foundation_validation') or {}
    audit = (foundation.get('checks') or {}).get('continuity')
    if not isinstance(audit, dict):
        return None
    result = dict(schema_version='continuity-wip-read/1', status='NOT_COLLECTED',
        audit_status=audit.get('status'), foundation_audit_status=foundation.get('status'),
        run_id=run_id, source_as_of=None, rows=[], continuation_certification='NOT_CERTIFIED',
        execution_authorized=False, execution_protection=dict(status='SERVER_ENFORCED',
            owner='enterprise_sim.live.check_manufacture_release',
            scope='NEW_FULL_BATCH_WITH_EXISTING_WORK_COVERAGE',
            block_codes=['WIP_REQUIRES_CERTIFIED_CONTINUATION', 'NATIVE_MANUFACTURE_ALREADY_ADOPTED',
                'ADOPTION_EXCEEDS_OPEN_DEMAND']))
    if not run_id or foundation.get('run_id') != run_id or audit.get('run_id') != run_id:
        return dict(result, status='INVALID_EVIDENCE', code='CONTINUITY_RUN_MISMATCH')
    result.update(source_as_of=audit.get('source_as_of'), source=_source(audit, run_id, POINTER))
    original_rows = audit.get('wip')
    if not isinstance(original_rows, list):
        return result
    for index, original in enumerate(original_rows):
        if not isinstance(original, dict):
            return dict(result, status='INVALID_EVIDENCE', code='CONTINUITY_WIP_ROW_INVALID', rows=[])
        row = {key: copy.deepcopy(original.get(key)) for key in
            ('job_id', 'order_id', 'opening', 'used', 'remaining', 'issued_materials', 'completed_prefix',
             'recipe_id','original_job_quantity','completed_quantities','finished_received_quantity','shipped_quantity',
             'remaining_quantity','remaining_from_operation_id','remaining_operation_ids','actual_operation_records',
             'actual_operation_ids','source_refs','native_operations','native_root_references')}
        if original.get('progress_contract')=='stage-flow-v2':
            row.update({key:copy.deepcopy(original.get(key)) for key in ('progress_contract','original_product_id','stage_partitions',
                'stage_operations','stage_balances','packed_quantity','packed_unreceived_quantity','planned_receipt_quantity','actual_receipt_quantity')})
        quantities = [_quantity(row[key]) for key in ('opening', 'used', 'remaining')]
        complete = (all(value is not None for value in quantities) and bool(row['job_id']) and bool(row['order_id'])
            and isinstance(row['issued_materials'], list) and isinstance(row['completed_prefix'], list))
        row.update(continuation_status='NOT_CHECKED', evidence_status='NOT_COLLECTED' if not complete
            else 'AUDITED' if audit.get('status') == 'PASS' else 'NOT_CERTIFIED',
            source=_source(original, run_id, POINTER + '/wip/' + str(index)))
        if complete and audit.get('status') == 'PASS':
            used, remaining = quantities[1:]
            row['continuation_status'] = ('NOT_USED' if used == 0 and remaining > 0 else
                'PARTIALLY_USED' if used > 0 and remaining > 0 else
                'USED_IN_NATIVE_PLAN' if used > 0 and remaining == 0 else 'NOT_CHECKED')
        row['continuation_certification']=(original.get('continuation_certification','NOT_CERTIFIED')
            if complete and audit.get('status')=='PASS' and foundation.get('status')=='PASS' else 'NOT_CERTIFIED')
        result['rows'].append(row)
    if result['rows'] and all(r['continuation_certification']=='NATIVE_STAGE_FLOW_VERIFIED' for r in result['rows']):
        result['continuation_certification']='NATIVE_STAGE_FLOW_VERIFIED'
    elif result['rows'] and all(r['continuation_certification']=='NATIVE_REMAINING_ROUTE_VERIFIED' for r in result['rows']):
        result['continuation_certification']='NATIVE_REMAINING_ROUTE_VERIFIED'
    result['status'] = ('PRESENT' if result['rows'] else 'EMPTY_CHECKED'
        if audit.get('status') == 'PASS' else 'NOT_COLLECTED')
    return result
