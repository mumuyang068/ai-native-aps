"""Explicit, bounded composition of D15 manufacturing and D16 PO/QC.

No new supply authorization, solver, stock store or execution lifecycle.
"""
PROFILE='mixed-fixed-batch-confirmed-supply-v1'

def enabled(data):
    from joint_qualified import enabled as qualified_joint
    return data.get('rules',{}).get('manufacturing_profile')==PROFILE or qualified_joint(data)

def validate(data):
    from joint_qualified import enabled,manufacturing_view
    if enabled(data):data=manufacturing_view(data)
    from two_level_confirmed_supply import validate as validate_confirmed
    validate_confirmed(data,mixed=True)
