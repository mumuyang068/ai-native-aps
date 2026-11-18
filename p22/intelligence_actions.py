"""Explicit research actions; all plan computation stays with original Owners."""
import copy,json,os
from pathlib import Path
from p22.foundation_projection import digest,require
from p22.intelligence_service import shape,research_input
from p22.intelligence_versions import revise_version
from p22.intelligence_process import inventory_library

def execute(owner,identity,action,payload,request_id):
    service,full=owner.scope(identity,allow_discarded_release=action=='quote_release')
    data=research_input(service)
    registry=owner.registry
    if action=='forecast_start':return owner.forecast_start(identity,payload,request_id)
    if action=='history_register':
        owner.scope(identity,creating=True)
        shape(payload,{'classification','rows','source','training_cutoff','issue_time'}, {'backtest','observation_kind','freq'})
        require(payload['classification'] in {'SYNTHETIC_RESEARCH','PUBLIC_BENCHMARK','SOURCE_OBSERVED'} and bool(payload['source']),'HISTORY_CLASSIFICATION_REQUIRED')
        require(isinstance(payload['rows'],list) and 1<=len(payload['rows'])<=10000,'HISTORY_ROWS_BUDGET')
        from demand_intelligence import select_training_history
        require(payload.get('freq','W-MON') in {'D','W-MON','MS'},'FORECAST_FREQUENCY_UNSUPPORTED')
        selected=select_training_history(payload['rows'],training_cutoff=payload['training_cutoff'],observation_kind=payload.get('observation_kind','OBSERVED_DEMAND'),censored_policy='exclude_series')
        require(bool(selected['rows']),'HISTORY_NO_OBSERVABLE_SERIES')
        return registry.create('dataset',request_id,payload,lambda:dict(payload,sha256=digest(payload)))
    if action in {'forecast_confirm','forecast_override','forecast_restore','forecast_withdraw'}:
        owner.scope(identity,creating=True)
        required={'version_id','actor','reason'}|({'row_id','quantity'} if action=='forecast_override' else {'restore_version_id'} if action=='forecast_restore' else set())
        shape(payload,required)
        require(all(isinstance(payload[k],str) and bool(payload[k].strip()) for k in ('actor','reason')),'FORECAST_OVERRIDE_REASON_REQUIRED')
        version=registry.get('version',payload['version_id'])
        if action=='forecast_withdraw':
            def revoke():
                require(not any(r['version_id']==version['id'] for r in registry.list('revocation')),'FORECAST_VERSION_WITHDRAWN')
                return dict(payload)
            return registry.create('revocation',request_id,payload,revoke)
        require(not any(r['version_id']==version['id'] for r in registry.list('revocation')),'FORECAST_VERSION_WITHDRAWN')
        if action!='forecast_confirm':owner.check_version(version)
        restore=registry.get('version',payload['restore_version_id']) if action=='forecast_restore' else None
        if restore:require(restore['data_version_id']==version['data_version_id'],'FORECAST_VERSION_IDENTITY')
        return registry.create('version',request_id,payload,lambda:revise_version(version,actor=payload['actor'],reason=payload['reason'],
            row_id=payload.get('row_id'),quantity=payload.get('quantity'),restore=restore))
    if action=='inventory_recommend':
        owner.scope(identity,creating=True);shape(payload,{'source_id','service_level','reason'},{'method','forecast_version_id'})
        # The foundation native mapper does not implement canonical hard
        # reservations. Never silently turn a nonempty set into reserved=0.
        require(not data.get('reservations'), 'INVENTORY_CANONICAL_RESERVATIONS_UNSUPPORTED')
        require(bool(payload['reason']),'INVENTORY_POLICY_REASON_REQUIRED')
        require(type(payload['service_level']) in {float,int} and 0.5<float(payload['service_level'])<0.9999,'INVENTORY_SERVICE_LEVEL')
        path=os.environ.get('PLANNING_INVENTORY_RESEARCH_INPUT')
        require(bool(path),'INVENTORY_RESEARCH_HISTORY_REQUIRED')
        seed=json.loads(Path(path).read_text('utf8'))['request']
        from inventory_optimization.enterprise import build_request
        from inventory_optimization import recommend
        policy=copy.deepcopy(seed['service_policy']);policy['target_cycle_service_level']=str(payload['service_level'])
        # History applies to this material only; do not reuse it for another SKU.
        source=next((s for s in data['supply_sources'] if s['id']==payload['source_id']),None)
        require(source is not None and all(source[k]==seed['dimension'][k] for k in ('item_id','location_id')),'INVENTORY_HISTORY_DIMENSION')
        from order_promising import HoldStore
        holds=HoldStore(owner.folder/'promising').list()
        require(not any(q['state'] in {'HELD','COMMITTED_SIMULATED'} and all(q['request'].get(k)==source.get(k) for k in ('item_id','location_id')) for q in holds),'INVENTORY_ACTIVE_HOLD_REQUIRES_RECALCULATION')
        request=build_request(data,history=seed['history'],demand_lineage=seed['demand_lineage'],
            source_id=source['id'],policy_version='INV06-'+request_id[:12],service_policy=policy,
            reserved_quantity='0',reserved_source_ref='CANONICAL_RESERVATIONS_VERIFIED_EMPTY;NO_ACTIVE_RESEARCH_HOLD')
        if payload.get('forecast_version_id'):
            v=registry.get('version',payload['forecast_version_id']);owner.check_version(v)
            # The finished-goods forecast is not a raw-material demand history.
            # It remains a linked input; native BOM planning performs conversion.
            request['linked_forecast_version']=dict(id=v['id'],sha256=v['sha256'],conversion='BOM_IN_ORIGINAL_WORKER')
        result=recommend(request)
        result['algorithm_receipt']['library_execution']=inventory_library(request,owner.folder)
        native=copy.deepcopy(result['native_policy'])
        if payload.get('method'):
            candidate=next((c for c in result['candidates'] if c['method']==payload['method']),None)
            require(candidate is not None,'INVENTORY_METHOD_UNSUPPORTED')
            require(candidate.get('status')!='NOT_APPLICABLE','INVENTORY_METHOD_NOT_APPLICABLE')
            for r in native['rows']:r.update(minimum=candidate['minimum'],maximum=candidate['maximum'])
            result['selected_method']=candidate['method']
            native['source']='inventory-optimization-v1 request='+result['request_sha256']+' method='+candidate['method']
            result['native_policy']=copy.deepcopy(native)
            result['algorithm_receipt']['native_policy_sha256']=digest(native)
            result['selection']=dict(method=candidate['method'],basis='EXPLICIT_RESEARCH_SELECTION')
        return registry.create('inventory',request_id,payload,lambda:dict(source_id=source['id'],reason=payload['reason'],
            request=request,result=result,native_policy=native,engine_executed=False,simulation_only=True,execution_authorized=False))
    if action.startswith('quote_'):
        from order_promising import HoldStore
        store=HoldStore(owner.folder/'promising')
        wanted={k:full[k] for k in ('plan_id','data_version_id','baseline_run_id','run_id','target')}
        if action=='quote_create':
            shape(payload,{'request'});q=payload['request']
            data=service.drafts.baseline
            require(q.get('item_id') in {i['id'] for i in data['items']} and q.get('customer_id') in {r['id'] for r in data['foundation_projection']['source_tables']['customers']},'PROMISING_UNKNOWN_BUSINESS_OBJECT')
            require(any((p['item_id'],p['location_id'],p['uom'])==(q.get('item_id'),q.get('location_id'),q.get('uom')) for p in data['inventory_positions']),'PROMISING_DIMENSION_UNKNOWN')
            return store.create_inquiry(q,wanted,request_id)
        if action=='quote_expire':shape(payload,set());return dict(expired=store.expire())
        shape(payload,{'quote_id','expires_at'} if action=='quote_hold' else {'quote_id'})
        q=store.get(payload['quote_id']);require(q['identity']==wanted,'PROMISING_IDENTITY_MISMATCH')
        if action=='quote_calculate':return store.calculate(q['id'],owner.promising_snapshot(identity),wanted)
        if action=='quote_offer':return store.offer(q['id'],wanted)
        if action=='quote_hold':return store.hold(q['id'],owner.promising_snapshot(identity),wanted,request_id,payload['expires_at'])
        if action=='quote_release':return store.release(q['id'],wanted,request_id)
        if action=='quote_commit':return store.commit_simulated(q['id'],wanted,request_id)
    raise ValueError('INTELLIGENCE_ACTION_UNSUPPORTED')
