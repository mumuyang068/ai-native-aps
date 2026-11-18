"""Project an audited native root through configured ERP IDs, never name matching."""
import copy


def professional_request(native, mapping):
    if mapping.get('uom') != '件' and not (mapping.get('source_profile')=='odoo-verified-manufacturing-v1'
            and isinstance(mapping.get('uom'),str) and mapping['uom'].strip()):
        raise ValueError('ODOO_PRODUCT_UOM_MAPPING_REQUIRED')
    operations, constraints, seen = [], [], set()
    previous = []
    for op in native['operations']:
        mapped = mapping.get('operations', {}).get(op['operation_id'])
        if (not mapped or op['operation_id'] in seen or op.get('uom') != mapping['uom']
                or type(mapped.get('operation_id')) is not int
                or type(mapped.get('resources', {}).get(op['resource_id'])) is not int):
            raise ValueError('ODOO_OPERATION_RESOURCE_MAPPING_REQUIRED')
        seen.add(op['operation_id'])
        operations.append(dict(aps_operation_id=op['operation_id'], operation_id=mapped['operation_id'],
            workcenter_id=mapped['resources'][op['resource_id']], quantity=op['quantity'],
            start_at=op['start_at'], end_at=op['end_at'], predecessor_operation_ids=previous))
        previous = [mapped['operation_id']]
        if op.get('operator_id'):
            constraints.append(dict(kind='NAMED_PERSON_TIME', aps_operation_id=op['operation_id'],
                operator_id=op['operator_id'], start_at=op['start_at'], end_at=op['end_at']))
    if not operations or len({o['operation_id'] for o in operations}) != len(operations):
        raise ValueError('ODOO_COMPLETE_OPERATION_MAPPING_REQUIRED')
    # This handoff inherits the audited SIM profile's setup rules. Community
    # workcenter time_start/stop is a different (constant) duration semantics.
    constraints.append(dict(kind='SEQUENCE_DEPENDENT_SETUP', status='NOT_REPRESENTABLE',
        basis='Original APS native run retains setup constraints; target has no equivalent sequence matrix'))
    return dict(product_id=mapping['product_id'], bom_id=mapping['bom_id'], uom_id=mapping['uom_id'],
        quantity=native['quantity'], start_at=operations[0]['start_at'], end_at=operations[-1]['end_at'],
        operations=copy.deepcopy(operations), constraints=constraints)
