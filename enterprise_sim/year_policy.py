"""Versioned synthetic research rules, separate from frozen reference facts.

No event or stock is created here. The original 56 days keep their original
Replay policy; the continuation declares its own calendar and role evidence.
"""
import copy
from .live import digest

ORIGIN='2026-11-02T00:00:00+08:00'
PREFIX_END='2026-12-28T00:00:00+08:00'
YEAR_END='2027-11-02T00:00:00+08:00'
ROLE_END='2027-12-28T00:00:00+08:00'
POLICY_ID='APS-SIM-YEAR-01-POLICY-V1'


def build_year_declarations(original):
    """Copy original public declarations and add explicitly labelled new rules."""
    result=copy.deepcopy(original)
    definition,operating,reference=(result[k] for k in ['definition','operating','reference'])
    if definition['origin']!=ORIGIN or definition['enterprise_id']!='SIM-GARMENT-CONT-01':
        raise ValueError('YEAR_FROZEN_ENTERPRISE_IDENTITY_REQUIRED')
    source_definition_sha=digest(original['definition'])
    source_operating_sha=digest(original['operating'])
    policy=dict(schema='year-operating-policy/1',id=POLICY_ID,history_origin=ORIGIN,
        continuation_from=PREFIX_END,history_end=YEAR_END,frozen_prefix_days=56,year_days=365,
        planning_lookahead_days=56,known_from=ORIGIN,execution_authorized=False,
        scope='SYNTHETIC_RESEARCH_FACTORY_NOT_REAL_ENTERPRISE_FACTS',
        source_definition_sha256=source_definition_sha,source_operating_sha256=source_operating_sha,
        calendar=dict(id='YEAR-RESEARCH-DAY-V1',effective_from=PREFIX_END,effective_to=ROLE_END,
            weekdays=[0,1,2,3,4],segments=[[8,12],[13,17]],holidays='NONE_DECLARED_SYNTHETIC',
            basis='Task A4: new annual research shifts; original workday shape retained.'),
        demand=dict(monthly_weekday_probability=[0.75,0.75,0.85,0.95,0.95,0.85,0.75,0.85,0.9,0.95,0.9,0.8],
            batch_quantity=20,due_calendar_days=7,urgent_every_days=35,urgent_due_days=3,
            revision_every_days=43,calibration='SYNTHETIC_NOT_INDUSTRY_STATISTICS',
            basis='After frozen56, lower declared arrivals to approximately one batch/workday; do not erase inherited backlog or increase capacity.'),
        execution=dict(dispatch='FIFO_FIXED_PRIMARY',batch_quantity=20,no_scrap=True,no_rework=True,
            materials='ISSUE_ONCE_AT_RELEASE',advance='COMPLETE_PREDECESSOR_REQUIRED',
            distinction='Baseline policy is autonomous synthetic routine, not unadopted APS advice.'),
        supply=dict(minimum_order_quantity=100,order_multiple=100,reorder_workdays=5,target_workdays=20,
            lead_calendar_days=2,qc_calendar_days=1,partial_every_orders=4,partial_percent=60,
            remaining_delay_days=2,quality_hold_every_orders=7,quality_hold_percent=10,
            late_erp_every_orders=9,late_erp_minutes=1560,receipt_ledger_required=True,
            capacity_policy='SYNTHETIC_ROLLING_56_DAY_NEW_SUPPLY_ALLOWANCE',capacity=5000,
            basis='Versioned threshold/MOQ replenishment; never direct stock edits or a second MRP solver.'),
        disturbances=dict(machine_every_days=61,person_every_days=47,downtime_calendar_days=2,
            basis='Deterministic configured stress within base line; no claimed industry calibration.'),
        prefix_qualification_basis='Original A-CONT-QUALIFICATION/A-CONT-CALENDAR eight-week Replay research roles, not original enterprise qualification validity.')
    definition['end']=YEAR_END
    definition['annual_policy_id']=POLICY_ID
    definition.setdefault('assumptions',[]).append(dict(id='A-YEAR-POLICY',value=copy.deepcopy(policy)))
    operating['year_policy']=policy
    operating['supply']['capacity_period']={'start':ORIGIN,'end':ROLE_END}
    operating['supply'].update(minimum_order_quantity=100,order_multiple=100,
        horizon_capacity=5000,lead_time_seconds=2*86400,qc_wait_seconds=86400,
        capacity_policy=policy['supply']['capacity_policy'])
    source_ops={r['id']:r for r in reference['tables']['operations']}
    roles={};machines=[]
    for phase,begin,end,basis in [
        ('FROZEN_REPLAY',ORIGIN,PREFIX_END,policy['prefix_qualification_basis']),
        ('ANNUAL_SYNTHETIC',PREFIX_END,ROLE_END,'APS-SIM-YEAR-01 A4: newly declared synthetic annual primary roles; not extension of original qualifications')]:
        for op,spec in definition['operations'].items():
            skill=source_ops[op]['required_operator_skill'];person=spec['operator']
            key=(phase,person,skill)
            roles[key]=dict(id='YEAR-'+phase+'-'+person+'-'+skill,resource_id=person,skill_id=skill,
                effective_from=begin,effective_to=end,phase=phase,basis=basis,policy_id=POLICY_ID,
                known_from=ORIGIN,factual_status='DECLARED_RESEARCH_POLICY')
            machines.append(dict(id='YEAR-'+phase+'-'+op,resource_id=spec['resource'],operation_id=op,
                effective_from=begin,effective_to=end,phase=phase,basis=basis,policy_id=POLICY_ID,
                known_from=ORIGIN,factual_status='DECLARED_RESEARCH_POLICY'))
    operating['research_qualifications']=[roles[k] for k in sorted(roles)]
    operating['research_machine_qualifications']=machines
    return result
