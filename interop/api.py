"""Explicitly enabled local test-company publication using original Plan Owners."""
import copy
import json
import os
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse
from fastapi import Depends,HTTPException,Request
from pydantic import BaseModel,ConfigDict,Field
from assistant_authorization import COOKIE
from interop.publication import PublicationService


def source_documents(version):
    """Observe formal documents from this confirmed version, never the outbox.

    Receipt acceptance and current document execution are separate facts. In
    particular execution after rescheduling does not certify the first window.
    """
    locations=version.get('canonical',{}).get('tables',{}).get('locations',[])
    odoo=next((r['odoo_bundle']['capture'] for r in locations if r.get('odoo_bundle')),None)
    if odoo:
        from interop.odoo_planning import ident,key
        d=odoo['documents'];warehouse=d['stock.warehouse'][0]
        units={u['id']:u['name'] for u in d['uom.uom']}
        result=[]
        for m in d['mrp.production']:
            # Storage is a separate real transfer in the declared three-step
            # flow. WO completion and production output are not storage proof.
            from interop.odoo_business_audit import stored_quantity
            stored=float(stored_quantity(odoo,m))
            qty=float(m['product_qty']);produced=float(m['qty_produced'])
            result.append(dict(id=f"odoo:{odoo['company_id']}:mrp.production:{m['id']}",
                product_id=key('product.product',ident(m['product_id'])),quantity=qty,uom=units[ident(m['product_uom_id'])],
                status=m['state'],external_version=odoo['revisions']['mrp.production'][str(m['id'])],
                produced_quantity=produced,received_quantity=stored,
                document_execution_state='EXECUTED' if stored==qty else 'PARTIALLY_EXECUTED' if stored>0 else 'NOT_RECEIVED',
                production_state=m['state'],backorder_sequence=m['backorder_sequence'],
                data_version_id=version['id'],source_as_of=odoo['captured_at'],
                receipt_basis='DONE_TRANSFER_TO_WAREHOUSE_STOCK_NOT_WO_DONE'))
        return result
    bundle=next((r['continuity_bundle'] for r in locations if r.get('continuity_bundle')),None)
    tasks=bundle['documents']['MES']['tables'].get('manufacturing_tasks',[]) if bundle else []
    result=[]
    for row in tasks:
        if not row.get('external_version'):continue
        received=row.get('completed_quantity');quantity=row['quantity']
        execution='UNKNOWN' if received is None else 'EXECUTED' if Decimal(str(received))==Decimal(str(quantity)) else 'PARTIALLY_EXECUTED' if Decimal(str(received))>0 else 'NOT_RECEIVED'
        result.append(dict(id=row['id'],order_id=row['order_id'],product_id=row['product_id'],
            external_version=row['external_version'],status=row['status'],quantity=quantity,uom=row.get('unit'),
            received_quantity=received,document_execution_state=execution,data_version_id=version['id'],
            source_as_of=bundle['documents']['MES']['as_of'],source_record_revision=row.get('source_record_revision')))
    return result


class Selection(BaseModel):
    model_config=ConfigDict(extra='forbid')
    target:str=Field(pattern=r'^(baseline|DRAFT-[a-f0-9]{16})$')
    order_id:str=Field(min_length=1,max_length=200)
    manufacture_id:str=Field(min_length=1,max_length=200)
    action_type:str=Field(default='CREATE_MO',pattern=r'^(CREATE_MO|CONTINUE_MO|RESCHEDULE_MO|SCHEDULE_MO)$')
    external_document_id:str|None=Field(default=None,max_length=200)
    expected_document_version:int|None=Field(default=None,ge=1)


class Submit(BaseModel):
    model_config=ConfigDict(extra='forbid')
    selection:Selection
    fingerprint:str=Field(pattern=r'^[a-f0-9]{64}$')
    request_id:str=Field(min_length=1,max_length=200)
    confirmed:bool=Field(strict=True)


def register(app,plans,local_client):
    config_path=os.environ.get('PLANNING_INTEROP_TARGETS_FILE')
    configured=json.loads(Path(config_path).read_text('utf8')) if config_path else dict(schema='aps-interop-targets/1',targets={})
    if configured.get('schema')!='aps-interop-targets/1':raise ValueError('PUBLICATION_CONFIGURATION_INVALID')
    targets=copy.deepcopy(configured['targets'])
    for key,cfg in targets.items():
        if cfg.get('kind')=='SIM':
            from interop.sim_adapter import SimAdapter
            cfg['adapter']=SimAdapter(cfg)
        elif cfg.get('kind')=='ODOO':
            from interop.odoo_adapter import OdooAdapter
            credentials=json.loads(Path(cfg['credentials_file']).read_text('utf8'))
            cfg['adapter']=OdooAdapter(credentials['base_url'],credentials['database'],credentials['username'],credentials['password'],cfg['company_id'],
                enabled=cfg.get('scope')=='ISOLATED_TEST_COMPANY_ONLY')
        else:raise ValueError('PUBLICATION_ADAPTER_UNSUPPORTED')

    def principal(request:Request):
        p=app.state.agent_operators.principal(request.cookies.get(COOKIE))
        if not p or getattr(request.state,'agent_principal',None):raise HTTPException(403,detail=dict(code='TEST_COMPANY_OPERATOR_REQUIRED'))
        return p

    def write(request:Request):
        local_client(request)
        supplied=urlparse(request.headers.get('origin',''));base=urlparse(str(request.base_url))
        if not supplied.netloc or (supplied.scheme,supplied.netloc)!=(base.scheme,base.netloc):
            raise HTTPException(403,detail=dict(code='SAME_ORIGIN_REQUIRED'))
        return principal(request)

    def read(request:Request,plan_id:str):
        agent=getattr(request.state,'agent_principal',None)
        if agent:
            if plan_id not in agent['plan_ids'] or 'READ' not in agent['permissions']:
                raise HTTPException(403,detail=dict(code='AGENT_PLAN_ACCESS_DENIED'))
            return agent
        return principal(request)

    def invoke(fn):
        try:return fn()
        except ValueError as exc:
            code=str(exc).split(':',1)[0]
            if not code or not all(c.isupper() or c.isdigit() or c=='_' for c in code):code='PUBLICATION_REJECTED'
            messages={'PUBLICATION_TARGET_SCOPE_MISMATCH':'目标企业或分支与所选数据版本不一致，请核对交接配置。',
                'STALE_ADOPTION_EVIDENCE':'Odoo来源已变化，请重新同步、确认并计算后再交接。',
                'ADOPTION_REQUIRES_CURRENT_SOURCE_VERSION':'所选计划不是当前已确认来源版本，请先核对最新企业资料。'}
            raise HTTPException(409,detail=dict(code=code,message=messages.get(code,'交接未完成：'+code))) from None

    def preview(target_id,selection):
        from closed_loop_actions import adoption_preview
        from p21.live_connector import LiveSourceClient
        cfg=targets[target_id];branch=cfg['branch_id']
        choice=dict(plan_id=selection['plan_id'],target=selection['target'],order_id=selection['order_id'],
            manufacture_id=selection['manufacture_id'],action_type='MANUFACTURE')
        action=selection.get('action_type','CREATE_MO')
        odoo_source=cfg.get('source_profile')=='odoo-verified-manufacturing-v1'
        if odoo_source:
            if cfg['kind']!='ODOO':raise ValueError('ODOO_SOURCE_TARGET_REQUIRED')
            connector=app.state.connector_registry.get(cfg.get('connector_id'))
            if connector is None:raise ValueError('ODOO_SOURCE_CONNECTOR_REQUIRED')
            from interop.odoo_adoption import adoption_preview as odoo_preview
            parent=None
            if action=='SCHEDULE_MO':
                parents=[r for r in service.list(selection['plan_id']) if r['target_id']==target_id
                    and r['command']['action_type']=='CREATE_MO' and r['state']=='ACCEPTED'
                    and r.get('receipt',{}).get('external_document_id')==selection.get('external_document_id')
                    and r['command']['suggestion_id']==selection['manufacture_id']]
                if len(parents)!=1:raise ValueError('ODOO_PUBLICATION_LINEAGE_MISMATCH')
                parent=parents[0]
            native=odoo_preview(plans,connector,choice,cfg['company_id'],branch,parent=parent)
        else:
            source=cfg.get('source_bridge',cfg)
            client=LiveSourceClient(dict(base_url=source['base_url'],branch_id=branch),source['operator_token'])
            head=client.request('/v1/sim/'+branch+'/status')
            connector=app.state.connector_registry.get('live-'+branch)
            head['current_version_id']=connector.get_sync_status().get('current_version_id') if connector else None
            native=adoption_preview(plans,branch,choice,head,
                reschedule_job_id=selection.get('external_document_id') if action=='RESCHEDULE_MO' and cfg['kind']=='SIM' else None,
                expected_document_version=selection.get('expected_document_version'))
        payload=native['payload']
        if action=='CREATE_MO' and payload.get('existing_job_id'):raise ValueError('WIP_REQUIRES_EXISTING_MO_CONTINUATION')
        if action=='CONTINUE_MO' and not payload.get('existing_job_id'):raise ValueError('CONTINUATION_JOB_IDENTITY_MISMATCH')
        if action=='RESCHEDULE_MO':
            if not selection.get('external_document_id') or not selection.get('expected_document_version'):
                raise ValueError('EXTERNAL_DOCUMENT_VERSION_REQUIRED')
            payload.update(existing_job_id=selection['external_document_id'],expected_document_version=selection['expected_document_version'])
        if cfg['kind']=='ODOO':
            if action not in ('CREATE_MO','SCHEDULE_MO'):raise ValueError('PUBLICATION_ACTION_UNSUPPORTED')
            mapping=cfg['product_mapping'].get(payload['product_id'])
            if not mapping or (mapping['uom']!='件' and not (odoo_source and mapping.get('source_profile')==cfg['source_profile'] and mapping['uom']==payload['uom'])):raise ValueError('ODOO_PRODUCT_UOM_MAPPING_REQUIRED')
            from interop.odoo_mapping import professional_request
            requested=professional_request(payload,mapping) if mapping.get('operations') else None
            payload=dict(product_id=mapping['product_id'],bom_id=mapping['bom_id'],uom_id=mapping['uom_id'],
                quantity=payload['quantity'],start_at=payload['operations'][0]['start_at'],end_at=payload['operations'][-1]['end_at'])
            if odoo_source and action=='CREATE_MO':
                payload.update(sale_order_id=native['payload']['source_sale_order_id'],expected_sale_revision=native['payload']['expected_sale_revision'])
            if action=='SCHEDULE_MO':
                prefix=f"odoo:{cfg['company_id']}:mrp.production:"
                external=selection.get('external_document_id','')
                if not requested or not external.startswith(prefix) or not external[len(prefix):].isdigit():
                    raise ValueError('ODOO_EXISTING_PROFESSIONAL_ORDER_REQUIRED')
                identifier=int(external[len(prefix):]);snapshot=cfg['adapter'].inspect_manufacturing(identifier)
                parents=[r for r in service.list(selection['plan_id']) if r['target_id']==target_id
                    and r['command']['action_type']=='CREATE_MO' and r['state']=='ACCEPTED'
                    and r.get('receipt',{}).get('external_document_id')==external
                    and all(r['command'].get(k)==native['payload'].get(k) for k in
                        ['enterprise_id','branch_id','data_version_id','plan_id','run_id'])
                    and r['command']['suggestion_id']==selection['manufacture_id']]
                if len(parents)!=1:raise ValueError('ODOO_PUBLICATION_LINEAGE_MISMATCH')
                payload=dict(requested,external_document_id=identifier,
                    expected_write_date=snapshot['version']['production'],expected_workorders=snapshot['version']['workorders'],
                    expected_master_sha256=snapshot['version']['masters_sha256'],expected_publication_command_id=parents[0]['command_id'])
            # Preserve the requested operations even for draft-only creation.
            native['requested_schedule']=requested
        command=dict(enterprise_id=native['payload']['enterprise_id'],branch_id=branch,data_version_id=native['payload']['data_version_id'],
            plan_id=selection['plan_id'],run_id=native['payload']['run_id'],suggestion_id=selection['manufacture_id'],
            action_type=action,payload=payload,test_company_id=cfg['company_id'],source_audit=copy.deepcopy(native['payload']['native_audit']))
        if native.get('requested_schedule'):command['requested_schedule']=native['requested_schedule']
        return command

    service=PublicationService(Path(os.environ.get('PLANNING_INTEROP_STATE_DIR',plans.store.root/'interop')),targets,preview);app.state.interop=service
    def documents(plan_id):
        record=plans.store.get(plan_id);version=plans.imports.version(record['data_version_id'])
        plans.imports.verify_version_sources(version)
        return source_documents(version)
    @app.get('/v1/enterprise-plans/{plan_id}/publications',dependencies=[Depends(read)])
    def records(plan_id:str):
        def perform():
            record=plans.store.get(plan_id)
            contexts=[s.version_context(record['data_version_id']) for s in getattr(app.state,'connector_registry',{}).values() if callable(getattr(s,'version_context',None))]
            contexts=[c for c in contexts if c is not None]
            if len(contexts)>1:raise ValueError('PUBLICATION_SOURCE_IDENTITY_AMBIGUOUS')
            return dict(commands=service.list(plan_id),documents=documents(plan_id),source_context=contexts[0] if contexts else None,
                targets=[dict(id=k,name=c['name'],scope=c['scope'],actions=c['actions']) for k,c in targets.items()])
        return invoke(perform)
    @app.post('/v1/enterprise-plans/{plan_id}/publications/{target_id}/preview',dependencies=[Depends(write)])
    def view(plan_id:str,target_id:str,body:Selection):return invoke(lambda:service.preview(target_id,dict(body.model_dump(exclude_none=True),plan_id=plan_id)))
    @app.post('/v1/enterprise-plans/{plan_id}/publications/{target_id}')
    def submit(plan_id:str,target_id:str,body:Submit,actor=Depends(write)):
        return invoke(lambda:service.submit(target_id,dict(body.selection.model_dump(exclude_none=True),plan_id=plan_id),body.fingerprint,
            request_id=body.request_id,confirmed=body.confirmed,principal=actor))
    @app.post('/v1/enterprise-plans/{plan_id}/publications/{command_id}/reconcile',dependencies=[Depends(write)])
    def reconcile(plan_id:str,command_id:str):
        def perform():
            if service.get(command_id)['command']['plan_id']!=plan_id:raise ValueError('PUBLICATION_PLAN_MISMATCH')
            return service.reconcile(command_id,retry=True)
        return invoke(perform)

    @app.get('/v1/enterprise-plans/{plan_id}/publications/{command_id}/observation',dependencies=[Depends(read)])
    def observation(plan_id:str,command_id:str):
        def perform():
            value=service.observe(command_id,plan_id=plan_id)
            for source in getattr(app.state,'connector_registry',{}).values():
                if not callable(getattr(source,'version_context',None)) or not source.version_context(value['data_version_id']):continue
                capture=source.adapter.capture()
                if capture['company_id']!=value['snapshot']['production']['company_id']:raise ValueError('PUBLICATION_SOURCE_IDENTITY_MISMATCH')
                observed=value['snapshot'];versions=observed['version']
                if capture['revisions']['mrp.production'].get(str(observed['production']['id']))!=versions['production'] or any(
                    capture['revisions']['mrp.workorder'].get(wid)!=revision for wid,revision in versions['workorders'].items()):
                    raise ValueError('ODOO_TARGET_CHANGED_DURING_READBACK')
                from interop.odoo_business_audit import manufacturing_actuals
                value['current_actuals']=manufacturing_actuals(capture,value['snapshot']['production']['id'])
                value['assessment']['finished_received_quantity']=value['current_actuals']['stored_quantity']
            return value
        return invoke(perform)
