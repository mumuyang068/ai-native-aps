"""Authenticated bounded Odoo projection through existing foundation Owners."""
import copy
from interop.odoo_planning import PROFILE, build_snapshot, readiness
from p22.foundation_projection import (validate_facts, _build_projection_common,
    require, digest, seal, projection_hash)


def build_policy(professional):
    return dict(profile=PROFILE, policy_id=professional['policy_id'],
        enterprise_id=professional['enterprise_id'], dataset_id='ODOO03',
        scope='RESEARCH_SIMULATION_ONLY', execution_authorized=False,
        change_frozen_facts=False, approval=copy.deepcopy(professional['approval']) | {'status':professional['status']},
        qualification_priority_policy='EQUAL_ELIGIBLE_CANDIDATES', setup_overlay=[],
        demand_scope='SCOPED_ODOO_OPEN_SALES', professional_policy=copy.deepcopy(professional),
        limits=['Isolated Odoo company only; not customer ERP certification',
                'Personnel and setup are approved independent research policy, not native ERP facts',
                'INSPECT work order is not a quality release certificate'])


def source_contract(c, policy):
    require(policy.get('profile')==PROFILE,'ODOO_POLICY_PROFILE_REQUIRED')
    locations=c['tables']['locations'];require(len(locations)==1,'ODOO_WAREHOUSE_SCOPE_REQUIRED')
    bundle=locations[0].get('odoo_bundle',{})
    require(bundle and policy==build_policy(bundle['professional_policy']),'ODOO_POLICY_CHANGED')
    snapshot=build_snapshot(bundle['capture'],bundle['professional_policy'])
    require(all(c[k]==snapshot[k] for k in ['enterprise_id','planning_at','horizon','timezone','tables']), 'ODOO_CANONICAL_REBUILD_MISMATCH')
    return snapshot,bundle


def _project(version,policy,c):
    snapshot,bundle=source_contract(c,policy)
    c=copy.deepcopy(c);c['tables']=snapshot['tables']
    setups={o['id']:bundle['professional_policy']['setup']['operation_state'] for o in c['tables']['operations']}
    derivations=dict(enterprise_id=c['enterprise_id'],dataset_id='ODOO03',review_status='SOURCE_CHECKED',
        operation_setups=setups,source_sha256=bundle['capture']['source_sha256'],profile=PROFILE)
    result=_build_projection_common(version,policy,derivations,c);data=result['plan_input']
    data['foundation_projection']['odoo_scope']=dict(company_id=bundle['capture']['company_id'],
        database=bundle['capture']['database'], source_sha256=bundle['capture']['source_sha256'],
        source_origin='REAL_ODOO_NOT_SIMULATOR',readiness=readiness(bundle['capture'],bundle['professional_policy']),
        stock_basis='CURRENT_QUANT_BALANCE; DONE_MOVES_ARE_EVIDENCE_ONLY_NOT_REPLAYED',
        reservation_basis='OWN_SCOPE_RESERVATIONS_NOT_CONSUMPTION',
        professional_policy_sha256=digest(bundle['professional_policy']))
    seal(data)
    result['context'].update(adapter_profile=PROFILE,source_origin='REAL_ODOO_NOT_SIMULATOR',
        foundation_projection_sha256=data['foundation_projection']['projection_sha256'])
    return result


def build_projection(version,policy,derivations=None):
    c=validate_facts(version);result=_project(version,policy,c)
    from p22.foundation_native import validate as validate_native
    validate_native(result['plan_input'])
    from p22.foundation_editor import enable_research_scope
    enable_research_scope(result['plan_input'],'REAL_ODOO_SCOPE_FROM_CONFIRMED_CANONICAL')
    result['context']['foundation_projection_sha256']=result['plan_input']['foundation_projection']['projection_sha256']
    capture=c['tables']['locations'][0]['odoo_bundle']['capture']
    result['readiness'].update(source_scope=dict(company_id=capture['company_id'],enterprise_id=capture['enterprise_id'],**capture['scope']),
        source_known_at=capture['captured_at'],
        professional_policy=dict(id=policy['policy_id'],source='APPROVED_INDEPENDENT_POLICY_NOT_ERP',status=policy['professional_policy']['status']))
    return result


def validate(data):
    from p21.canonical import assemble
    meta=data['foundation_projection']
    require(meta.get('projection_sha256')==projection_hash(data),'PROJECTION_CHANGED')
    require(meta.get('policy_sha256')==digest(meta['policy']),'POLICY_CHANGED')
    if 'editor_scope' in meta:
        from p22.foundation_editor import contract,validate_overlay
        contract(data)
        if 'plan_editor_overlay' in meta:validate_overlay(data)
        restored=copy.deepcopy(data);m=restored['foundation_projection'];overlay=m.pop('plan_editor_overlay',None)
        if overlay:
            for d in restored['demand_lines']:d['priority']=overlay['before_priorities'][d['id']]
        scope=m.pop('editor_scope');m['projection_sha256']=scope['original_projection_sha256']
        return validate(restored)
    policy=meta['policy'];tables=meta['source_tables']
    c=assemble(policy['enterprise_id'],tables,planning_at=data['planning_at'],horizon=data['horizon'],timezone=data['site']['timezone'],source_refs={})
    require(not c['issues'] and c['semantic_sha256']==meta['canonical_semantic_sha256'],'ODOO_CANONICAL_REBUILD_MISMATCH')
    expected=_project({'id':meta['source_version_id']},policy,c)['plan_input']
    for f in ['snapshot_id','scenario_id','scenario_revision']:expected[f]=data[f]
    require(expected==data,'ODOO_PROJECTION_REBUILD_MISMATCH')
    return data
