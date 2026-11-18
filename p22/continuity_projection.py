"""Legitimate current-state policy, source rebuild and composite native mapping."""
import copy

from p21.canonical import assemble
from p22.continuity_sources import build_snapshot,build_policy,native_year_qualifications,PROFILE
from p22.foundation_projection import digest,require,validate_facts,_build_projection_common,projection_hash,seal


def source_contract(c,policy):
    require(c['enterprise_id']=='SIM-GARMENT-CONT-01','POLICY_IDENTITY_MISMATCH')
    locations=c['tables'].get('locations',[])
    require(len(locations)==1 and 'continuity_bundle' in locations[0],'CONTINUITY_SOURCE_BUNDLE_REQUIRED')
    bundle=locations[0]['continuity_bundle']
    expected_policy=build_policy(bundle['definition'],bundle['operating'],bundle['reference'])
    require(policy==expected_policy,'CONTINUITY_POLICY_CHANGED')
    snapshot=build_snapshot(**bundle)
    require(all(snapshot[k]==c[k] for k in ['enterprise_id','planning_at','horizon','timezone']), 'CONTINUITY_SOURCE_CLOCK_CHANGED')
    require(snapshot['tables']==c['tables'],'CONTINUITY_CANONICAL_REBUILD_MISMATCH')
    rebuilt=assemble(snapshot['enterprise_id'],snapshot['tables'],planning_at=snapshot['planning_at'],horizon=snapshot['horizon'],timezone=snapshot['timezone'],source_refs={})
    require(not rebuilt['issues'] and rebuilt['semantic_sha256']==c['semantic_sha256'],'CONTINUITY_CANONICAL_REBUILD_MISMATCH')
    return snapshot


def _project(version,policy,c,*,snapshot=None):
    # Rebuild the insertion order from the source adapter itself. JSON storage
    # sorts object keys; that must not reorder the row-receipt array or change
    # an already saved, authenticated input when it is resumed.
    c=copy.deepcopy(c)
    bundle=c['tables']['locations'][0]['continuity_bundle']
    c['tables']=(snapshot if snapshot is not None else build_snapshot(**bundle))['tables']
    setups={o['id']:'CONT:NEUTRAL' for o in c['tables']['operations']}
    derivations=dict(enterprise_id=c['enterprise_id'],dataset_id=policy['dataset_id'],review_status='SOURCE_CHECKED',
                     operation_setups=setups,source_sha256=policy['definition_sha256'],profile=PROFILE)
    result=_build_projection_common(version,policy,derivations,c)
    data=result['plan_input'];meta=data['foundation_projection']
    if bundle['operating'].get('year_policy'):
        data['resource_skills']=native_year_qualifications(data['resource_skills'])
    meta['continuity_scope']=dict(revision=PROFILE,enterprise_id=c['enterprise_id'],planning_time=c['planning_at'],
        source_documents_sha256={s:digest(d) for s,d in c['tables']['locations'][0]['continuity_bundle']['documents'].items()},
        wip_jobs=[dict(job_id=r['continuity_job_id'],order_id=r['actual_order_id'],recipe_id=r['id'],
                      wip_item_id='CONT-WIP:'+r['continuity_job_id'],quantity=r.get('remaining_quantity',20),issued_materials=copy.deepcopy(r['issued_materials']),
                      completed_prefix=copy.deepcopy(r['completed_prefix']),
                      **({k:copy.deepcopy(r[k]) for k in ('original_job_quantity','completed_quantities','finished_received_quantity',
                          'shipped_quantity','remaining_quantity','remaining_from_operation_id','remaining_operation_ids',
                          'actual_operation_records','actual_operation_ids','source_refs')} if 'remaining_quantity' in r else {}))
                      for r in c['tables']['recipes'] if r.get('continuity_job_id')],
        history_policy='ALL_SOURCE_HISTORY_RETAINED_IN_BUNDLE_NOT_REPLAYED',remaining_wip_policy='REPORT_NATIVE_UNUSED_OPENING')
    if policy.get('wip_progress_policy'):
        jobs={};operations={o['recipe_id']:o for o in c['tables']['operations']}
        for recipe in c['tables']['recipes']:
            if recipe.get('progress_contract')!='stage-flow-v2':continue
            jid=recipe['continuity_job_id'];op=operations[recipe['id']]
            if jid not in jobs:
                jobs[jid]=dict(job_id=jid,order_id=recipe['actual_order_id'],progress_contract='stage-flow-v2',
                    original_product_id=recipe['original_product_id'],quantity=recipe['original_job_quantity']-recipe['finished_received_quantity'],
                    issued_materials=copy.deepcopy(recipe['issued_materials']),completed_prefix=copy.deepcopy(recipe['completed_prefix']),
                    stage_operations=[],**{k:copy.deepcopy(recipe[k]) for k in ('original_job_quantity','completed_quantities',
                        'finished_received_quantity','shipped_quantity','packed_quantity','packed_unreceived_quantity',
                        'remaining_from_operation_id','remaining_operation_ids','stage_partitions','actual_operation_records','actual_operation_ids','source_refs')})
                jobs[jid]['remaining_quantity']=jobs[jid]['quantity']
            jobs[jid]['stage_operations'].append(dict(recipe_id=recipe['id'],operation_id=op['id'],
                actual_operation_id=op['actual_operation_id'],stage_id=recipe['stage_id'],quantity=recipe['remaining_quantity'],
                continuation_kind=recipe['continuation_kind'],input_item_id=recipe['stage_input_item_id'],output_item_id=recipe['stage_output_item_id']))
        meta['continuity_scope']['wip_jobs']=list(jobs.values())
        meta['continuity_scope']['stage_graph']=dict(schema='native-stage-flow/2',receiving=copy.deepcopy(policy['wip_progress_policy']['receiving']),
            jobs=list(jobs),physical_partition_policy='JOB_PRIVATE_OPENING_ONCE',actual_fg_authority='WMS_RECEIPT_MOVEMENTS_ONLY')
    # A prior actual receipt is part of WMS opening. Only the reconciled
    # unreceived tranche is native PO supply; original PO quantities stay intact.
    for p in data['existing_orders']:
        if p['type']=='PO':
            original=next(r for r in c['tables']['purchase_orders'] if r['id']==p['id'])
            p['planned_start_at']=original['planned_start_at']
    seal(data)
    result['context']['adapter_profile']='canonical-continuity-current-state-v1'
    result['context']['foundation_projection_sha256']=meta['projection_sha256']
    return result


def build_projection(version,policy,derivations=None):
    c=validate_facts(version)
    snapshot=source_contract(c,policy)
    result=_project(version,policy,c,snapshot=snapshot)
    # Preparation precedes PlanStore's immutable write. Reject unsupported
    # native semantics here rather than leaving a saved, unusable plan.
    from p22.foundation_native import validate as validate_native
    validate_native(result['plan_input'])
    return result


def validate(data):
    from p22.validation_scope import validate_annual_once
    return validate_annual_once(data, _validate)


def _validate(data):
    meta=data.get('foundation_projection',{})
    require(meta.get('schema_revision')=='foundation-planning-projection-v1' and meta.get('scope')=='RESEARCH_SIMULATION_ONLY','UNSUPPORTED_FOUNDATION_PROJECTION')
    require(meta.get('projection_sha256')==projection_hash(data),'PROJECTION_CHANGED')
    require(meta.get('policy_sha256')==digest(meta.get('policy')),'POLICY_CHANGED')
    if 'editor_scope' in meta:
        from p22.foundation_editor import contract,validate_overlay
        contract(data)
        if 'plan_editor_overlay' in meta:
            validate_overlay(data)
        restored=copy.deepcopy(data);m=restored['foundation_projection']
        overlay=m.pop('plan_editor_overlay',None)
        if overlay:
            for d in restored['demand_lines']:d['priority']=overlay['before_priorities'][d['id']]
        scope=m.pop('editor_scope');m['projection_sha256']=scope['original_projection_sha256']
        _validate(restored)
        return data
    if 'priority_overlay' in meta:
        # The original Owner restores one exact priority and authenticates the
        # complete sealed source projection. Other overlays and fact edits still
        # fail that rebuild; a resealed receipt grants no wider change scope.
        from p22.foundation_projection import validate_priority_overlay
        validate_priority_overlay(data)
        return data
    tables=meta['source_tables']
    c=assemble(meta['policy']['enterprise_id'],tables,planning_at=data['planning_at'],horizon=data['horizon'],timezone=data['site']['timezone'],source_refs={})
    require(c['semantic_sha256']==meta['canonical_semantic_sha256'],'CONTINUITY_CANONICAL_REBUILD_MISMATCH')
    snapshot=source_contract(c,meta['policy'])
    expected=_project({'id':meta['source_version_id']},meta['policy'],c,snapshot=snapshot)['plan_input']
    for field in ['snapshot_id','scenario_id','scenario_revision']:
        expected[field]=data[field]
    require(data==expected,'CONTINUITY_PROJECTION_REBUILD_MISMATCH')
    return data
