import hashlib
import json
import re
import threading
import copy
from contextlib import ExitStack
from agent import AgentError
from drafts import DraftError
from service import PlanningService,planning_read_scope,read_run
from p22.store import PlanStore
from planning_adapter import InputError

class EnterprisePlans:
    def __init__(self,root,imports):
        self.store=PlanStore(root);self.imports=imports;self.services={};self.lock=threading.RLock()
        self._investigation_views={}

    def prepare(self,version_id,policy_id=None):
        """Pinned local research policy; no arbitrary policy payload from a client."""
        version=self.imports.version(version_id);self.imports.verify_version_sources(version)
        return self._prepare_version(version,policy_id)

    def _prepare_version(self,version,policy_id):
        """Project the exact version already authenticated by this Owner's caller."""
        import os
        from pathlib import Path
        from p22.foundation_projection import build_projection,digest
        if not version.get('canonical'):raise InputError('NOT_CANONICAL_VERSION')
        path=os.environ.get('PLANNING_FOUNDATION_POLICY_FILE');expected=os.environ.get('PLANNING_FOUNDATION_POLICY_SHA256')
        if not path or not expected:raise InputError('POLICY_APPROVAL_REQUIRED: 未配置已批准的隔离研究政策')
        policy=json.loads(Path(path).read_text('utf-8'))
        if digest(policy)!=expected:raise InputError('POLICY_CHANGED')
        if policy_id is not None and policy_id!=policy['policy_id']:raise InputError('POLICY_IDENTITY_MISMATCH')
        derivations=json.loads((Path(__file__).parents[1]/'fixtures/foundation/source-derivations.json').read_text('utf-8'))
        return build_projection(version,policy,derivations)

    def create(self,version_id,policy_id=None):
        with self.lock:
            version=self.imports.version(version_id)
            self.imports.verify_version_sources(version)
            projection=None
            if version.get('canonical'):
                if policy_id is None:raise InputError('POLICY_APPROVAL_REQUIRED: 选择已经批准的隔离研究条件')
                projection=self._prepare_version(version,policy_id)
            plan=self.store.create(version,projection=projection)
            run=self.service(plan['id']).bootstrap()
            return {**plan,'run':run,'workbench_url':'/?plan='+plan['id']}

    def service(self,pid):
        with self.lock:
            record=self.store.get(pid)
            if pid not in self.services:
                source=self.store.read_source(pid)
                context=source['context']
                self.services[pid]=PlanningService(self.store.folder(pid)/'workspace',self.store.folder(pid)/'canonical_input.json',context,
                    controlled_conditions_preflight=lambda draft:self._preflight_controlled_conditions_run(pid,draft),
                    inventory_decision_preflight=lambda draft:self._preflight_inventory_decision_run(pid,draft),
                    outstanding_receipt_preflight=lambda draft:self._preflight_outstanding_receipt_run(pid,draft),
                    resource_capacity_preflight=lambda draft:self._preflight_resource_capacity_run(pid,draft),
                    distribution_leadtime_preflight=lambda draft:self._preflight_distribution_leadtime_run(pid,draft),
                    professional_policy_preflight=lambda draft:self._preflight_professional_policy_run(pid,draft))
                self.services[pid].plan_editor_preflight=lambda draft:self._preflight_plan_editor_run(pid,draft)
                self.services[pid].query_version_metadata=source['version_metadata']
            if any(self.services[pid].enterprise.get(k)!=record[k] for k in ('data_version_id','snapshot_sha256')):
                raise InputError('PLAN_IDENTITY_CHANGED')
            return self.services[pid]

    def business_view(self,pid,data_version_id,**query):
        record=self.store.get(pid)
        if record['data_version_id']!=data_version_id:raise InputError('DATA_VERSION_MISMATCH')
        value=self.imports.business_view(data_version_id,**query)
        if value['identity']['data_version_id']!=data_version_id:raise InputError('DATA_VERSION_MISMATCH')
        value['identity']['plan_id']=pid
        return value

    def source_input(self,pid,data_version_id):
        """Original immutable Plan input, independently of any compute state."""
        record=self.store.get(pid)
        if record['data_version_id']!=data_version_id:raise InputError('DATA_VERSION_MISMATCH')
        if self.imports.version_metadata(data_version_id).get('confirmation_status')!='CONFIRMED':raise InputError('BUSINESS_FACTS_NOT_CONFIRMED')
        source=self.store.read_source(pid)
        if source['identity']['data_version_id']!=data_version_id:raise InputError('DATA_VERSION_MISMATCH')
        return source['plan_input']

    def source_workspace(self,pid,data_version_id):
        data=self.source_input(pid,data_version_id);record=self.store.get(pid)
        fields=('planning_at','horizon','site','items','resources','calendars','resource_skills','skills','existing_orders',
                'production_recipes','routing_revisions','inventory_positions','inventory_buffer_policy','supply_sources',
                'purchase_receipts','distribution_lanes','distribution_orders','rules','reservations')
        return dict(schema_version='source-workspace/1',basis='CONFIRMED_PLAN_INPUT',
            identity=dict(plan_id=pid,data_version_id=data_version_id,run_id=None,snapshot_sha256=record['snapshot_sha256']),
            input={k:copy.deepcopy(data[k]) for k in fields if k in data},
            forecasts=copy.deepcopy(data.get('foundation_projection',{}).get('source_tables',{}).get('forecasts',data.get('forecasts',[]))),
            inventory_targets=copy.deepcopy(data.get('foundation_projection',{}).get('source_tables',{}).get('inventory_targets',[])))

    def professional_overview(self,pid,*,draft_id=None):
        """One bounded original-Owner read; no complete DATA snapshot or professional reports."""
        with planning_read_scope(self.service(pid),reuse=True):
            return self._professional_overview(pid,draft_id=draft_id)

    def _professional_overview(self,pid,*,draft_id=None):
        import time
        from decimal import Decimal,InvalidOperation
        from plan_context import quantities
        from planning_capabilities import profile_id,input_capabilities,supports
        from professional_queries import _source_sync_context
        started=time.perf_counter();record=self.store.get(pid);service=self.service(pid);data=service.drafts.baseline
        profile=profile_id(data);checkpoint=bool(service.enterprise.get('execution_checkpoint'))
        try:rid=service.baseline_id();run=read_run(service,rid)
        except AgentError as exc:
            if str(exc)!='BASELINE_NOT_READY':raise
            rid=None;run=dict(state='NOT_SUBMITTED')
        if rid is not None and run.get('solve_run_id')!=rid:raise InputError('RUN_SOURCE_MISMATCH')
        identity=dict(plan_id=pid,data_version_id=record['data_version_id'],snapshot_sha256=record['snapshot_sha256'],
            baseline_run_id=rid,run_id=rid,target='baseline')
        firm=[r for r in data.get('demand_lines',[]) if r.get('kind')!='NET_FORECAST']
        summary=dict(status='NOT_COMPUTED',demand_line_count=len(firm),requested=quantities(firm),
            planned=None,unfulfilled=None,risk_counts=None)
        if run.get('state')=='SUCCEEDED':
            try:
                artifacts=service.certified_artifacts(rid);result=artifacts['result'];firm_ids={r['id'] for r in firm}
                deliveries=[r for r in result.get('deliveries',[]) if r['demand_id'] in firm_ids]
                if len(deliveries)!=len(firm_ids) or {r['demand_id'] for r in deliveries}!=firm_ids:
                    raise InputError('INCOMPLETE_EVIDENCE')
                requested={r['id']:r for r in firm}
                for delivery in deliveries:
                    row=requested[delivery['demand_id']]
                    values=[Decimal(str(delivery[k])) for k in ('requested_quantity','planned_quantity','unfulfilled_quantity')]
                    if (not all(v.is_finite() and v>=0 for v in values) or values[0]!=values[1]+values[2]
                        or values[0]!=Decimal(str(row['quantity'])) or delivery['uom']!=row['uom']
                        or delivery['status'] not in {'ON_TIME','LATE','SHORT','UNPLANNED'}):raise InputError('EVIDENCE_UNREADABLE')
                summary.update(status='PRESENT',planned=quantities(deliveries,'planned_quantity'),
                    unfulfilled=quantities(deliveries,'unfulfilled_quantity'),
                    risk_counts={state:sum(r.get('status')==state for r in deliveries) for state in ('ON_TIME','LATE','SHORT','UNPLANNED')},
                    delivered_line_count=len(deliveries),result_hash=result['result_hash'],
                    evidence_scope=artifacts['certification_scope'],business_audit='ON_DEMAND')
            except InputError as exc:
                code=str(exc).split(':',1)[0]
                summary.update(status='INCOMPLETE_EVIDENCE' if code=='INCOMPLETE_EVIDENCE' else 'INVALID_EVIDENCE',reason=code)
            except (KeyError,TypeError,ValueError,InvalidOperation):
                summary.update(status='INVALID_EVIDENCE',reason='EVIDENCE_UNREADABLE')
        metadata=service.drafts.metadata(draft_id)
        with service.drafts.db() as db:
            has_notes=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_notes'").fetchone()
            note_count=db.execute('SELECT count(*) FROM review_notes').fetchone()[0] if has_notes else 0
        capabilities=input_capabilities(data)
        if checkpoint:
            for value in capabilities.get('actions',{}).values():value.update(status='UNSUPPORTED',reason='CHECKPOINT_BASELINE_ONLY')
        can=lambda feature:not checkpoint and supports(profile,'actions',feature)
        base='/v1/enterprise-plans/'+pid
        value=dict(schema_version='professional-overview/1',status='PRESENT',**identity,
            enterprise_id=data.get('foundation_projection',{}).get('policy',{}).get('enterprise_id'),
            calculation_status=run.get('state'),run={k:run[k] for k in ('solve_run_id','state','failure_code','created_at','started_at','finished_at') if k in run},
            planning_at=data['planning_at'],timezone=data['site']['timezone'],horizon=data['horizon'],
            result_summary=summary,capabilities=capabilities,drafts=metadata['rows'],draft_count=metadata['total'],
            drafts_truncated=metadata['truncated'],review_note_count=note_count,
            permissions=(['READ_PLAN'] if checkpoint else ['READ_PLAN','RUN_ISOLATED_DRAFT','COMPARE'])+
                (['CREATE_DUE_DRAFT'] if can('due_change') else [])+(['CREATE_PURCHASE_DELAY_DRAFT'] if can('purchase_delay') else [])+
                (['FIND_MO_OPPORTUNITIES','CREATE_APPROVED_MO_SIMULATION_DRAFT'] if can('approved_mo_move') else [])+
                (['CREATE_ACCESSORY_SUPPLY_DRAFT'] if can('accessory_supply') else []),
            allowed_changes=(['ONE_EXISTING_ORDER_DUE_DATE'] if can('due_change') else [])+
                (['ONE_CONFIRMED_PURCHASE_RECEIPT_DELAY'] if can('purchase_delay') else [])+
                (['ONE_APPROVED_MO_WINDOW_SIMULATION'] if can('approved_mo_move') else [])+
                (['ONE_SOURCED_ACCESSORY_SIMULATION'] if can('accessory_supply') else []),
            queries=dict(professional=base+'/professional-query',snapshot=base+'/snapshot',reports=base+'/reports',
                review_notes=base+'/review-notes',overview=base+'/professional-overview',plan_editor=base+'/plan-editor'),
            source_sync=_source_sync_context(self,record,data),
            permission_scope='DECLARED_PROFILE_CAPABILITIES;CURRENT_CHANNEL_GRANTS_AND_CONFIRMATION_APPLY',
            evidence_scope='EXACT_PLAN_VERSION;PLANNED_RESULT_NOT_EXECUTION;FULL_AUDIT_EXPLICIT',
            simulation_only=True,execution_authorized=False)
        if supports(profile,'analyses','resources'):value['manufacturing_summary']=dict(status='ON_DEMAND',source_endpoint=base+'/reports')
        value['timings']=dict(total_seconds=time.perf_counter()-started,query_mode='BOUNDED_OWNER_OVERVIEW')
        return value

    def _plan_editor_scope(self,pid,data_version_id,baseline_run_id,run_id,target):
        from plan_editor import available
        from planning_capabilities import profile_id
        record=self.store.get(pid);snapshot=self.store.snapshot(pid)
        if record['data_version_id']!=data_version_id:raise InputError('DATA_VERSION_MISMATCH')
        version=self.imports.version(data_version_id)
        if version!=snapshot['data_version']:raise InputError('VERSION_CHANGED')
        self.imports.verify_version_sources(version)
        service=self.service(pid);service.drafts.verify_baseline()
        if not service.drafts.allow_changes:raise InputError('CHECKPOINT_BASELINE_ONLY')
        if service.baseline_id()!=baseline_run_id:raise InputError('BASELINE_RUN_MISMATCH')
        if target!='baseline' or run_id!=baseline_run_id:raise InputError('EDITOR_REQUIRES_CURRENT_BASELINE')
        if service.compute().run(run_id).get('state')!='SUCCEEDED':raise InputError('BASELINE_NOT_READY')
        if not available(service.drafts.baseline):raise InputError('UNSUPPORTED_PROFILE_CAPABILITY: plan_editor')
        return service,dict(plan_id=pid,data_version_id=data_version_id,baseline_run_id=baseline_run_id,run_id=run_id,target=target,
            profile=profile_id(service.drafts.baseline),snapshot_sha256=record['snapshot_sha256'],source_sha256=version['file_sha256'])

    def _preflight_plan_editor_run(self,pid,draft):
        i=draft['changes']['confirmation']['identity']
        _,current=self._plan_editor_scope(pid,i['data_version_id'],i['baseline_run_id'],i['run_id'],i['target'])
        if current!=i:raise InputError('EDITOR_RUN_IDENTITY_MISMATCH')
        for edit in draft['changes']['edits']:
            if edit['kind']=='optimized_schedule':
                from optimization.contract import read_problem,screen_edit
                screen_edit(read_problem(self.service(pid)),edit)
        if 'source_tasks' in draft['changes']:
            from p22.foundation_editor import preflight
            service=self.service(pid)
            if preflight(service.drafts.baseline,service.baseline_view(),draft['changes']['edits'])!=draft['changes']['source_tasks']:raise InputError('EDITOR_SOURCE_TASK_CHANGED')

    def plan_editor_options(self,pid,data_version_id,baseline_run_id,run_id,target):
        from plan_editor import contract
        with self.lock:
            s,i=self._plan_editor_scope(pid,data_version_id,baseline_run_id,run_id,target);data=s.drafts.baseline
            orders=[copy.deepcopy(m) for m in data['existing_orders'] if m['type']=='MO']
            for m in orders:m['editable']=i['profile']=='fixed-batch-window-v1' and m['source_status']=='approved' and str(m.get('completed_quantity'))=='0'
            c=contract(data)
            proposals=[]
            if data.get('foundation_projection',{}).get('editor_scope'):
                from p22.foundation_editor import source_tasks
                proposals=source_tasks(data,s.baseline_view())
            extra={}
            if data.get('foundation_projection',{}).get('editor_scope'):
                from p22.foundation_editor import edit_options
                extra=edit_options(data,s.baseline_view())
            return dict(identity=i,orders=orders,contract=c,proposal_tasks=proposals,resources=data['resources'],calendars=data['calendars'],resource_skills=data.get('resource_skills',[]),execution_authorized=False,**extra)

    def preview_plan_editor(self,pid,data_version_id,baseline_run_id,run_id,target,edits,reason):
        from plan_editor import apply,digest
        with self.lock:
            s,i=self._plan_editor_scope(pid,data_version_id,baseline_run_id,run_id,target)
            enterprise=s.drafts.baseline.get('foundation_projection',{}).get('editor_scope')
            for edit in edits:
                if edit['kind']=='optimized_schedule':
                    from optimization.contract import read_problem,screen_edit
                    screen_edit(read_problem(s),edit)
            source=None
            if enterprise:
                from p22.foundation_editor import preflight
                source=preflight(s.drafts.baseline,s.baseline_view(),edits)
            changes=apply(copy.deepcopy(s.drafts.baseline),edits,reason,source_tasks=source)
            if not enterprise and any(e['kind']=='machine' for e in edits):
                from plan_editor import validate_assignment_preview
                base=s.baseline_view()
                if base.get('interpretation',{}).get('qualified_resource_validation',{}).get('status')!='PASS' or base['interpretation'].get('material_validation',{}).get('status')!='PASS':raise InputError('EDITOR_BASELINE_AUDIT_REQUIRED')
                validate_assignment_preview(s.drafts.baseline,base['result'],edits)
            return dict(identity=i,changes=changes,normalized_edits=copy.deepcopy(changes['edits']),fingerprint=digest(dict(identity=i,baseline_sha256=s.drafts.digest,changes=changes)),
                engine_executed=False,calculation_submitted=False)

    def create_plan_editor_draft(self,pid,data_version_id,baseline_run_id,run_id,target,edits,reason,source_sha256,fingerprint,acknowledge_limits,request_id):
        from plan_editor import ACTION
        with self.lock:
            if acknowledge_limits is not True:raise InputError('EDITOR_ACK_REQUIRED')
            p=self.preview_plan_editor(pid,data_version_id,baseline_run_id,run_id,target,edits,reason)
            if source_sha256!=p['identity']['source_sha256']:raise InputError('SOURCE_SHA_MISMATCH')
            if fingerprint!=p['fingerprint']:raise InputError('EDITOR_PREVIEW_CHANGED')
            args=dict(edits=p['normalized_edits'],reason=reason,confirmation=dict(identity=p['identity'],fingerprint=fingerprint,acknowledge_limits=True))
            if 'source_tasks' in p['changes']:args['source_tasks']=p['changes']['source_tasks']
            return self._create_scoped_draft(pid,data_version_id,baseline_run_id,ACTION,args,request_id)

    def get(self,pid):
        record=self.store.get(pid);service=self.service(pid)
        try:run=service.compute().run(service.baseline_id())
        except AgentError as exc:
            if str(exc)=='BASELINE_NOT_READY':run={'state':'NOT_SUBMITTED'}
            else:raise
        return {**record,'run':run,'workbench_url':'/?plan='+pid,
            'capabilities':{'professional_query':'professional-query/1'}}

    def _professional_scope(self,pid,data_version_id,baseline_run_id,run_id,target,*,creating=False,allow_discarded_release=False):
        from planning_capabilities import profile_id
        record=self.store.get(pid);snapshot=self.store.snapshot(pid)
        if record['data_version_id']!=data_version_id:raise InputError('DATA_VERSION_MISMATCH')
        service=self.service(pid);service.drafts.verify_baseline()
        if profile_id(service.drafts.baseline)!='foundation-planning-projection-v1':raise InputError('UNSUPPORTED_PROFILE_CAPABILITY: professional_policy')
        if service.baseline_id()!=baseline_run_id:raise InputError('BASELINE_RUN_MISMATCH')
        if service.compute().run(baseline_run_id)['state']!='SUCCEEDED':raise InputError('BASELINE_NOT_READY')
        if creating:
            baseline=service.view(baseline_run_id)
            from p22.professional_outcome import baseline_eligible
            if not baseline_eligible(baseline):
                raise InputError('BASELINE_BUSINESS_AUDIT_NOT_PASSED')
        if creating and (target!='baseline' or run_id!=baseline_run_id):raise InputError('PROFESSIONAL_BASELINE_ONLY')
        if target=='baseline':
            if run_id!=baseline_run_id:raise InputError('RUN_NOT_IN_PLAN')
        else:
            draft=service.drafts.get(target)
            from p22.professional_policy import verify
            if not verify(service.drafts.baseline,draft):raise InputError('PROFESSIONAL_TARGET_MISMATCH')
            if draft['state']=='DISCARDED' and not allow_discarded_release:raise InputError('DRAFT_DISCARDED')
            if draft['changes']['confirmation']['identity']['baseline_run_id']!=baseline_run_id:raise InputError('BASELINE_RUN_MISMATCH')
            if run_id!=(draft['run_id'] or baseline_run_id):raise InputError('RUN_NOT_IN_PLAN')
        return service,dict(plan_id=pid,data_version_id=data_version_id,baseline_run_id=baseline_run_id,
            run_id=run_id,target=target,snapshot_sha256=record['snapshot_sha256'])

    def _preflight_professional_policy_run(self,pid,draft):
        from p22.professional_policy import verify
        with self.lock:
            wanted=draft['changes']['confirmation']['identity']
            service,current=self._professional_scope(pid,**{k:wanted[k] for k in ('data_version_id','baseline_run_id','run_id','target')},creating=True)
            if current!=wanted:raise InputError('PROFESSIONAL_IDENTITY_CHANGED')
            self.imports.verify_version_sources(self.store.snapshot(pid)['data_version'])
            verify(service.drafts.baseline,draft)
            self._validate_advanced_netting(pid,draft['changes']['parameters'].get('netting'))
            search=(draft['changes']['parameters'].get('inquiry') or {}).get('search')
            if search:
                parent=service.drafts.get(search['previous_draft_id'])
                if not parent.get('run_id') or service.view(parent['run_id'])['run']['state']!='SUCCEEDED':raise InputError('INQUIRY_SEARCH_PARENT_NOT_SUCCESSFUL')

    def preview_professional_policy(self,pid,data_version_id,baseline_run_id,run_id,target,netting,inquiry,replenishment,reason,supply=None):
        from p22.professional_policy import preview
        with self.lock:
            service,identity=self._professional_scope(pid,data_version_id,baseline_run_id,run_id,target,creating=True)
            netting=self._validate_advanced_netting(pid,netting)
            parameters=dict(netting=netting,inquiry=inquiry,replenishment=replenishment,reason=reason)
            if supply is not None:parameters['supply']=supply
            return preview(service.drafts.baseline,identity,parameters,service.drafts.digest)

    def create_professional_policy_draft(self,pid,data_version_id,baseline_run_id,run_id,target,netting,inquiry,replenishment,reason,fingerprint,acknowledge_limits,request_id,supply=None):
        from p22.professional_policy import ACTION
        with self.lock:
            _,identity=self._professional_scope(pid,data_version_id,baseline_run_id,run_id,target,creating=True)
            netting=self._validate_advanced_netting(pid,netting)
            return self._create_scoped_draft(pid,data_version_id,baseline_run_id,ACTION,
                dict(identity=identity,netting=netting,inquiry=inquiry,replenishment=replenishment,reason=reason,
                    fingerprint=fingerprint,acknowledge_limits=acknowledge_limits,**({'supply':supply} if supply is not None else {})),request_id)

    def _validate_advanced_netting(self,pid,netting):
        if isinstance(netting,dict) and netting.get('revision')=='advanced-netting-v1':
            from p22.intelligence_service import IntelligenceOwner
            try:
                owner=IntelligenceOwner(self,pid)
                if 'forecast_version_ref' in netting:
                    ref=netting['forecast_version_ref']
                    if not isinstance(ref,dict) or set(ref)!={'id','sha256'} or 'forecast_version' in netting:raise ValueError('FORECAST_VERSION_REFERENCE')
                    version=owner.registry.get('version',ref['id'])
                    if version['sha256']!=ref['sha256']:raise ValueError('FORECAST_VERSION_REFERENCE_CHANGED')
                    netting={k:v for k,v in netting.items() if k!='forecast_version_ref'}|dict(forecast_version=version)
                owner.check_version(netting['forecast_version'])
            except (ValueError,KeyError) as error:raise InputError(str(error)) from None
        return netting

    def _next_inquiry_preview(self,pid,data_version_id,baseline_run_id,run_id,target,due_at,reason,replay_request_id=None):
        from p22.professional_policy import next_inquiry_parameters,preview
        service,identity=self._professional_scope(pid,data_version_id,baseline_run_id,run_id,target)
        if target=='baseline':raise InputError('INQUIRY_SEARCH_PARENT_REQUIRED')
        view=service.selected_view(target)
        if view['run']['state']!='SUCCEEDED' or view.get('interpretation',{}).get('foundation_validation',{}).get('status')!='PASS':raise InputError('INQUIRY_SEARCH_PARENT_NOT_SUCCESSFUL')
        draft=service.drafts.get(target)
        history=service.drafts.list()
        # A lost response must reach the original DraftStore idempotency check.
        # Exclude only this request's own prior result from the preview budget;
        # the immutable request fingerprint still rejects changed arguments.
        if replay_request_id is not None:
            history=[d for d in history if (d.get('external_request') or {}).get('id')!=replay_request_id]
        params=next_inquiry_parameters(service.drafts.baseline,draft,history,due_at,reason)
        base_identity={**identity,'target':'baseline','run_id':baseline_run_id}
        value=preview(service.drafts.baseline,base_identity,params,service.drafts.digest)
        return dict(origin_identity=identity,preview=value,parameters=params,simulation_only=True,execution_authorized=False)

    def preview_next_inquiry(self,pid,**body):
        with self.lock:return self._next_inquiry_preview(pid,**body)

    def create_next_inquiry(self,pid,data_version_id,baseline_run_id,run_id,target,due_at,reason,fingerprint,acknowledge_limits,request_id):
        with self.lock:
            value=self._next_inquiry_preview(pid,data_version_id,baseline_run_id,run_id,target,due_at,reason,replay_request_id=request_id)
            if value['preview']['fingerprint']!=fingerprint:raise InputError('PROFESSIONAL_PREVIEW_STALE')
            return self.create_professional_policy_draft(pid,data_version_id,baseline_run_id,baseline_run_id,'baseline',
                **value['parameters'],fingerprint=fingerprint,acknowledge_limits=acknowledge_limits,request_id=request_id)

    def professional_policy_view(self,pid,data_version_id,baseline_run_id,run_id,target='baseline'):
        from p22.professional_policy import verify
        with self.lock:
            service,identity=self._professional_scope(pid,data_version_id,baseline_run_id,run_id,target)
            data=service.drafts.baseline;source=data['foundation_projection']['source_tables']
            draft=service.drafts.get(target) if target!='baseline' else None
            if draft:verify(data,draft)
            outcome=None
            if draft and draft['run_id']:
                view=service.selected_view(target)
                if view['run']['state']=='SUCCEEDED':
                    from p22.professional_outcome import outcome as project_outcome
                    base=service.baseline_view()
                    outcome=view.get('professional_assessment') or project_outcome(service,data,draft,base,view)
                else:outcome=dict(status=view['run']['state'],failure_code=view['run'].get('failure_code'))
            effective=draft['input'] if draft else data
            investigation=None
            if data.get('distribution_lanes'):
                from service import artifact_root
                from planning_analysis import joint_material_investigation
                view=service.selected_view(target) # authenticates all consumed run members
                if view['run']['state']=='SUCCEEDED':
                    folder=artifact_root()/view['run']['solve_run_id']/'raw'
                    raw={name:json.loads((folder/(name+'.json')).read_text('utf-8')) for name in ('fixed_batch_post','operationplanmaterial','purchaseorder','distributionorder')}
                    investigation=joint_material_investigation(effective,raw,view)
            return dict(identity=identity,forecasts=copy.deepcopy(source['forecasts']),
                firm_demands=copy.deepcopy(source['demand_lines']),customers=copy.deepcopy(source['customers']),
                items=copy.deepcopy(data['items']),stock_facts=copy.deepcopy(data['inventory_positions']),
                source_terms=copy.deepcopy(data['supply_sources']),purchases=copy.deepcopy([p for p in data['existing_orders'] if p['type']=='PO']),
                horizon=copy.deepcopy(data['horizon']),timezone=data['site']['timezone'],
                source_policy_status='FORECAST_POLICY_PENDING',intelligence_available=data['foundation_projection'].get('intelligence_scope',{}).get('revision')=='advanced-planning-research-v1',draft=service.public_draft(draft) if draft else None,
                changes=copy.deepcopy(draft['changes']) if draft else None,outcome=outcome,
                purchase_receipts=copy.deepcopy(effective.get('purchase_receipts',[])),distribution_lanes=copy.deepcopy(effective.get('distribution_lanes',[])),
                material_investigation=investigation,
                simulation_only=True,execution_authorized=False)

    def professional_query(self,pid,identity,view,filters=None,*,page_size=20,cursor=None):
        """Exact read-only projections; domain identities remain owned here."""
        from professional_queries import ProfessionalQueries
        with planning_read_scope(self.service(pid),reuse=True):
            if view=='scenario_review':
                return self._professional_review_query(pid,identity,filters,page_size=page_size,cursor=cursor)
            return ProfessionalQueries(self,pid).read(identity,view,filters,page_size=page_size,cursor=cursor)

    def _professional_review_query(self,pid,identity,filters,*,page_size,cursor):
        """Page the original comparison and review Owners in one request.

        Large source arrays remain readable as exact detail rows. Only their
        transport layout changes; legality and gate fingerprints stay original.
        """
        import base64,time
        from urllib.parse import urlencode
        from professional_queries import digest,encoded,IDENTITY
        started=time.perf_counter()
        if set(identity)!=set(IDENTITY) or identity.get('plan_id')!=pid:raise InputError('TOOL_SCOPE_MISMATCH')
        if not isinstance(filters,dict) or set(filters)!={'draft_ids'}:raise InputError('EXACT_REVIEW_SELECTION_REQUIRED')
        selected=filters['draft_ids']
        if (not isinstance(selected,list) or len(selected)>3 or any(not isinstance(t,str) or not re.fullmatch(r'DRAFT-[a-f0-9]{16}',t) for t in selected)
                or len(set(selected))!=len(selected)):raise InputError('INVALID_REVIEW_SELECTION')
        if type(page_size) is not int or not 1<=page_size<=100:raise InputError('QUERY_PAGE_BUDGET')
        if cursor is not None and (not isinstance(cursor,str) or not 1<=len(cursor)<=3000):raise InputError('CURSOR_INVALID')
        service,full=self.professional_query_scope(pid,identity,'scenario_review')
        with planning_read_scope(service):
            projection=self.scenario_review(pid,identity['data_version_id'],identity['baseline_run_id'],','.join(selected))
            notes=self.review_notes(pid,identity['data_version_id'],identity['baseline_run_id'],','.join(selected))
        built=time.perf_counter()
        query=urlencode(dict(data_version_id=identity['data_version_id'],baseline_run_id=identity['baseline_run_id'],draft_ids=','.join(selected)))
        prefix='/v1/enterprise-plans/'+pid+'/'
        comparison_endpoint=prefix+'scenario-review?'+query
        notes_endpoint=prefix+'review-notes?'+query
        details=[]
        def pointer(path,key):return path+'/'+str(key).replace('~','~0').replace('/','~1')
        def row(kind,value,path,endpoint):
            record=display(value,path,endpoint)
            return dict(kind=kind,fact_kind='NON_EXECUTING_REVIEW' if endpoint==notes_endpoint else 'NATIVE_PLANNED_RESULT',
                record=record,source=dict(owner='EnterprisePlans.review_notes' if endpoint==notes_endpoint else 'EnterprisePlans.scenario_review',
                    endpoint=endpoint,pointer=path,sha256=digest(value)),
                projection_status='EXACT_OWNER_RECORD' if record==value else 'OWNER_RECORD_WITH_PAGED_DETAILS')
        def reference(value,path,endpoint):
            return dict(record_ref=endpoint+'#'+path,sha256=digest(value),detail_rows_paginated=True,
                **({'count':len(value)} if isinstance(value,(list,dict)) else {}))
        def display(value,path,endpoint):
            if isinstance(value,dict):
                checkpoint=len(details)
                result={key:display(child,pointer(path,key),endpoint) for key,child in value.items()}
                if len(encoded(result).encode())<=10000:return result
                del details[checkpoint:]
            # Owner permits 2000 Unicode characters in a reason. JSON escaping
            # can require 12002 bytes; preserve every character within one row.
            elif len(encoded(value).encode())<=(6000 if isinstance(value,list) else 16000):return copy.deepcopy(value)
            if isinstance(value,(dict,list)):
                members=value.items() if isinstance(value,dict) else enumerate(value)
                for key,child in members:details.append(row('scenario_detail',child,pointer(path,key),endpoint))
                return reference(value,path,endpoint)
            raise InputError('EVIDENCE_RECORD_TOO_LARGE')
        candidates=[row('scenario_candidate',value,'/candidates/'+str(index),comparison_endpoint)
                    for index,value in enumerate(projection['candidates'])]
        history=[row('review_note',note,'/notes/'+str(index),notes_endpoint) for index,note in enumerate(notes['notes'])]
        summary={key:display(value,'/'+key,comparison_endpoint) for key,value in projection.items() if key not in {'identity','candidates'}}
        review_summary={key:display(value,'/'+key,notes_endpoint) for key,value in notes.items() if key!='notes'}
        review_summary.update(notes=[],history_total=len(history),history_row_kind='review_note',history_paginated=True)
        summary['review_notes']=review_summary
        rows=candidates+details+history
        fingerprint=digest(dict(comparison=projection,review_notes=notes))
        scope=digest(dict(identity=identity,view='scenario_review',filters=filters,page_size=page_size))
        offset=0
        if cursor:
            try:
                token=json.loads(base64.b64decode(cursor,altchars=b'-_',validate=True))
                if set(token)!={'scope','fingerprint','offset'} or type(token['offset']) is not int or token['offset']<0:raise ValueError()
            except (ValueError,TypeError,KeyError):raise InputError('CURSOR_INVALID') from None
            if token['scope']!=scope:raise InputError('CURSOR_SCOPE_MISMATCH')
            if token['fingerprint']!=fingerprint:raise InputError('CURSOR_EVIDENCE_CHANGED')
            offset=token['offset']
        if offset>len(rows):raise InputError('CURSOR_INVALID')
        page=[]
        for value in rows[offset:offset+page_size]:
            if len(encoded(page+[value]).encode())>38000:
                if not page:raise InputError('EVIDENCE_RECORD_TOO_LARGE')
                break
            page.append(value)
        end=offset+len(page)
        next_cursor=base64.urlsafe_b64encode(encoded(dict(scope=scope,fingerprint=fingerprint,offset=end)).encode()).decode() if end<len(rows) else None
        # History records use the same row cursor; summary never hides a count-only archive.
        summary['review_notes']['history_page']=dict(returned=sum(r['kind']=='review_note' for r in page),row_offset=offset)
        artifacts=[]
        for candidate in projection['candidates']:
            for artifact in candidate.get('professional',{}).get('evidence_files',[]):
                if artifact not in artifacts:artifacts.append(copy.deepcopy(artifact))
        finished=time.perf_counter()
        return dict(schema_version='professional-query/1',identity=full,result_status='PRESENT',rows=page,summary=summary,
            source_artifacts=artifacts,snapshot_fingerprint=fingerprint,
            checked_scope=dict(identity=identity,view='scenario_review',filters=filters,query_mode='ORIGINAL_OWNER_COMPARISON_AND_REVIEW',
                relation_scope='EXACT_OWNER_REFERENCES_ONLY;NO_INFERRED_ALLOCATION_OR_UNIQUE_CAUSE',history='ALL_STORED_NOTES_RECHECKED_AND_PAGED'),
            page=dict(returned=len(page),matched=len(rows),total=len(rows),offset=offset,next_cursor=next_cursor,truncated=next_cursor is not None),
            timings=dict(owner_query_seconds=built-started,projection_seconds=finished-built,total_seconds=finished-started,cache='REQUEST_ONLY_AUTHENTICATED_READS'),
            simulation_only=True,execution_authorized=False)

    def professional_query_scope(self,pid,identity,view):
        """Reuse the owning read identities without creating/auditing other views."""
        if view in {'forecast','inventory_research','promising'}:
            from p22.intelligence_service import IntelligenceOwner
            return IntelligenceOwner(self,pid).scope({k:identity[k] for k in ('data_version_id','baseline_run_id','run_id','target')})
        record=self.store.get(pid)
        if record['data_version_id']!=identity['data_version_id']:raise InputError('DATA_VERSION_MISMATCH')
        service=self.service(pid)
        if service.baseline_id()!=identity['baseline_run_id']:raise InputError('BASELINE_RUN_MISMATCH')
        target=identity['target']
        expected=service.baseline_id() if target=='baseline' else service.drafts.metadata(target)['rows'][0].get('run_id') or service.baseline_id()
        if identity['run_id']!=expected:raise InputError('RUN_NOT_IN_PLAN')
        return service,dict(identity,snapshot_sha256=record['snapshot_sha256'])

    def create_due_draft(self,pid,data_version_id,baseline_run_id,order_id,new_due_at,request_id):
        """A typed change in the existing P3 workspace; no model or new ledger."""
        return self._create_scoped_draft(pid,data_version_id,baseline_run_id,'propose_due_change',
            {'demand_id':order_id,'shift_days':None,'due_at':new_due_at},request_id)

    def create_demand_priority_draft(self,pid,data_version_id,baseline_run_id,demand_id,priority,request_id):
        return self._create_scoped_draft(pid,data_version_id,baseline_run_id,'set_demand_priority',
            {'demand_id':demand_id,'priority':priority},request_id)

    def preview_demand_priority(self,pid,data_version_id,baseline_run_id,run_id,target,demand_id,priority,reason):
        from demand_priority import apply
        with self.lock:
            service,identity=self._professional_scope(pid,data_version_id,baseline_run_id,run_id,target,creating=True)
            if not isinstance(reason,str) or not reason.strip() or len(reason)>1000:raise InputError('DEMAND_PRIORITY_REASON')
            candidate=copy.deepcopy(service.drafts.baseline)
            changes=apply(candidate,demand_id,priority)
            return dict(identity=identity,changes=changes,reason=reason,baseline_sha256=service.drafts.digest,
                engine_executed=False,calculation_submitted=False,simulation_only=True,execution_authorized=False)

    def _outstanding_receipt_scope(self,pid,data_version_id,baseline_run_id,run_id,target,*,_service=None):
        from multi_receipt_scenario import available
        record=self.store.get(pid);snapshot=self.store.snapshot(pid)
        if record['data_version_id']!=data_version_id:raise InputError('DATA_VERSION_MISMATCH')
        version=self.imports.version(data_version_id)
        if version!=snapshot['data_version']:raise InputError('VERSION_CHANGED')
        self.imports.verify_version_sources(version)
        service=_service if _service is not None else self.service(pid)
        service.drafts.verify_baseline()
        if not service.drafts.allow_changes or version['business_data'].get('execution_checkpoint'):
            raise InputError('CHECKPOINT_BASELINE_ONLY')
        if service.baseline_id()!=baseline_run_id:raise InputError('BASELINE_RUN_MISMATCH')
        if target!='baseline' or run_id!=baseline_run_id:raise InputError('OUTSTANDING_RECEIPT_REQUIRES_CURRENT_BASELINE')
        if service.compute().run(run_id).get('state')!='SUCCEEDED':raise InputError('BASELINE_NOT_READY')
        if not available(service.drafts.baseline):raise InputError('UNSUPPORTED_PROFILE_CAPABILITY: outstanding_receipt')
        identity=dict(plan_id=pid,data_version_id=data_version_id,baseline_run_id=baseline_run_id,run_id=run_id,
            target=target,profile=service.drafts.baseline['rules']['manufacturing_profile'],
            snapshot_sha256=record['snapshot_sha256'],source_sha256=version['file_sha256'])
        return service,identity

    def _preflight_outstanding_receipt_run(self,pid,draft):
        from p21.store import ImportError as SourceError
        identity=draft['changes']['confirmation']['identity']
        try:
            _,current=self._outstanding_receipt_scope(pid,identity['data_version_id'],identity['baseline_run_id'],
                identity['run_id'],identity['target'],_service=self.services[pid])
        except SourceError as error:raise InputError(str(error)) from None
        if current!=identity:raise InputError('OUTSTANDING_RECEIPT_RUN_IDENTITY_MISMATCH')

    def preview_outstanding_receipt(self,pid,data_version_id,baseline_run_id,run_id,target,reference,new_receipt_at,reason):
        from multi_receipt_scenario import apply
        with self.lock:
            service,identity=self._outstanding_receipt_scope(pid,data_version_id,baseline_run_id,run_id,target)
            changes=apply(copy.deepcopy(service.drafts.baseline),reference,new_receipt_at,reason)
            payload=dict(identity=identity,baseline_sha256=service.drafts.digest,changes=changes)
            fingerprint=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
            return dict(identity=identity,fingerprint=fingerprint,changes=changes,simulation_only=True,
                engine_executed=False,calculation_submitted=False)

    def create_outstanding_receipt_draft(self,pid,data_version_id,baseline_run_id,run_id,target,reference,new_receipt_at,reason,
            source_sha256,fingerprint,acknowledge_limits,request_id):
        from multi_receipt_scenario import ACTION
        with self.lock:
            if acknowledge_limits is not True:raise InputError('OUTSTANDING_RECEIPT_ACK_REQUIRED')
            preview=self.preview_outstanding_receipt(pid,data_version_id,baseline_run_id,run_id,target,reference,new_receipt_at,reason)
            if source_sha256!=preview['identity']['source_sha256']:raise InputError('SOURCE_SHA_MISMATCH')
            if fingerprint!=preview['fingerprint']:raise InputError('OUTSTANDING_RECEIPT_PREVIEW_CHANGED')
            arguments={**preview['changes']['conditions'],'reason':preview['changes']['reason'],
                'confirmation':dict(identity=preview['identity'],fingerprint=fingerprint,acknowledge_limits=True)}
            response=self._create_scoped_draft(pid,data_version_id,baseline_run_id,ACTION,arguments,request_id)
            return {**response,'identity':preview['identity'],'fingerprint':fingerprint,'simulation_only':True,
                'acknowledge_limits':True,'engine_executed':False,'calculation_submitted':False,
                'run':{'state':'NOT_RUN'} if not response['draft']['run_id'] else {'solve_run_id':response['draft']['run_id']}}

    def _distribution_leadtime_scope(self,pid,data_version_id,baseline_run_id,run_id,target,*,_service=None):
        from distribution_scenario import available
        record=self.store.get(pid);snapshot=self.store.snapshot(pid)
        if record['data_version_id']!=data_version_id:raise InputError('DATA_VERSION_MISMATCH')
        version=self.imports.version(data_version_id)
        if version!=snapshot['data_version']:raise InputError('VERSION_CHANGED')
        self.imports.verify_version_sources(version)
        service=_service if _service is not None else self.service(pid)
        service.drafts.verify_baseline()
        if not service.drafts.allow_changes or version['business_data'].get('execution_checkpoint'):
            raise InputError('CHECKPOINT_BASELINE_ONLY')
        if service.baseline_id()!=baseline_run_id:raise InputError('BASELINE_RUN_MISMATCH')
        if target!='baseline' or run_id!=baseline_run_id:raise InputError('DISTRIBUTION_REQUIRES_CURRENT_BASELINE')
        if service.compute().run(run_id).get('state')!='SUCCEEDED':raise InputError('BASELINE_NOT_READY')
        if not available(service.drafts.baseline):raise InputError('UNSUPPORTED_PROFILE_CAPABILITY: distribution_leadtime')
        identity=dict(plan_id=pid,data_version_id=data_version_id,baseline_run_id=baseline_run_id,run_id=run_id,
            target=target,profile=service.drafts.baseline['rules']['manufacturing_profile'],
            snapshot_sha256=record['snapshot_sha256'],source_sha256=version['file_sha256'])
        return service,identity

    def _preflight_distribution_leadtime_run(self,pid,draft):
        from p21.store import ImportError as SourceError
        identity=draft['changes']['confirmation']['identity']
        try:
            _,current=self._distribution_leadtime_scope(pid,identity['data_version_id'],identity['baseline_run_id'],
                identity['run_id'],identity['target'],_service=self.services[pid])
        except SourceError as error:raise InputError(str(error)) from None
        if current!=identity:raise InputError('DISTRIBUTION_RUN_IDENTITY_MISMATCH')

    def preview_distribution_leadtime(self,pid,data_version_id,baseline_run_id,run_id,target,lane_id,new_lead_time_seconds,reason):
        from distribution_scenario import apply
        with self.lock:
            service,identity=self._distribution_leadtime_scope(pid,data_version_id,baseline_run_id,run_id,target)
            changes=apply(copy.deepcopy(service.drafts.baseline),lane_id,new_lead_time_seconds,reason)
            payload=dict(identity=identity,baseline_sha256=service.drafts.digest,changes=changes)
            fingerprint=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
            return dict(identity=identity,fingerprint=fingerprint,changes=changes,simulation_only=True,
                engine_executed=False,calculation_submitted=False)

    def create_distribution_leadtime_draft(self,pid,data_version_id,baseline_run_id,run_id,target,lane_id,new_lead_time_seconds,reason,
            source_sha256,fingerprint,acknowledge_limits,request_id):
        from distribution_scenario import ACTION
        with self.lock:
            if acknowledge_limits is not True:raise InputError('DISTRIBUTION_ACK_REQUIRED')
            preview=self.preview_distribution_leadtime(pid,data_version_id,baseline_run_id,run_id,target,lane_id,new_lead_time_seconds,reason)
            if source_sha256!=preview['identity']['source_sha256']:raise InputError('SOURCE_SHA_MISMATCH')
            if fingerprint!=preview['fingerprint']:raise InputError('DISTRIBUTION_PREVIEW_CHANGED')
            arguments={**preview['changes']['conditions'],'reason':preview['changes']['reason'],
                'confirmation':dict(identity=preview['identity'],fingerprint=fingerprint,acknowledge_limits=True)}
            response=self._create_scoped_draft(pid,data_version_id,baseline_run_id,ACTION,arguments,request_id)
            return {**response,'identity':preview['identity'],'fingerprint':fingerprint,'simulation_only':True,
                'acknowledge_limits':True,'engine_executed':False,'calculation_submitted':False,
                'run':{'state':'NOT_RUN'} if not response['draft']['run_id'] else {'solve_run_id':response['draft']['run_id']}}

    def _resource_capacity_scope(self,pid,data_version_id,baseline_run_id,run_id,target,*,_service=None):
        from routing_capacity_scenario import available
        record=self.store.get(pid);snapshot=self.store.snapshot(pid)
        if record['data_version_id']!=data_version_id:raise InputError('DATA_VERSION_MISMATCH')
        version=self.imports.version(data_version_id)
        if version!=snapshot['data_version']:raise InputError('VERSION_CHANGED')
        self.imports.verify_version_sources(version)
        service=_service if _service is not None else self.service(pid)
        service.drafts.verify_baseline()
        if not service.drafts.allow_changes or version['business_data'].get('execution_checkpoint'):
            raise InputError('CHECKPOINT_BASELINE_ONLY')
        if service.baseline_id()!=baseline_run_id:raise InputError('BASELINE_RUN_MISMATCH')
        if target!='baseline' or run_id!=baseline_run_id:raise InputError('RESOURCE_CAPACITY_REQUIRES_CURRENT_BASELINE')
        if service.compute().run(run_id).get('state')!='SUCCEEDED':raise InputError('BASELINE_NOT_READY')
        if not available(service.drafts.baseline):raise InputError('UNSUPPORTED_PROFILE_CAPABILITY: resource_capacity')
        identity=dict(plan_id=pid,data_version_id=data_version_id,baseline_run_id=baseline_run_id,run_id=run_id,
            target=target,profile=service.drafts.baseline['rules']['manufacturing_profile'],
            snapshot_sha256=record['snapshot_sha256'],source_sha256=version['file_sha256'])
        return service,identity

    def _preflight_resource_capacity_run(self,pid,draft):
        from p21.store import ImportError as SourceError
        identity=draft['changes']['confirmation']['identity']
        try:
            _,current=self._resource_capacity_scope(pid,identity['data_version_id'],identity['baseline_run_id'],
                identity['run_id'],identity['target'],_service=self.services[pid])
        except SourceError as error:raise InputError(str(error)) from None
        if current!=identity:raise InputError('RESOURCE_CAPACITY_RUN_IDENTITY_MISMATCH')

    def preview_resource_capacity(self,pid,data_version_id,baseline_run_id,run_id,target,resource_id,start_at,end_at,reason):
        from routing_capacity_scenario import apply
        with self.lock:
            service,identity=self._resource_capacity_scope(pid,data_version_id,baseline_run_id,run_id,target)
            changes=apply(copy.deepcopy(service.drafts.baseline),resource_id,start_at,end_at,reason)
            payload=dict(identity=identity,baseline_sha256=service.drafts.digest,changes=changes)
            fingerprint=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
            return dict(identity=identity,fingerprint=fingerprint,changes=changes,simulation_only=True,
                engine_executed=False,calculation_submitted=False)

    def create_resource_capacity_draft(self,pid,data_version_id,baseline_run_id,run_id,target,resource_id,start_at,end_at,reason,
            source_sha256,fingerprint,acknowledge_limits,request_id):
        from routing_capacity_scenario import ACTION
        with self.lock:
            if acknowledge_limits is not True:raise InputError('RESOURCE_CAPACITY_ACK_REQUIRED')
            preview=self.preview_resource_capacity(pid,data_version_id,baseline_run_id,run_id,target,resource_id,start_at,end_at,reason)
            if source_sha256!=preview['identity']['source_sha256']:raise InputError('SOURCE_SHA_MISMATCH')
            if fingerprint!=preview['fingerprint']:raise InputError('RESOURCE_CAPACITY_PREVIEW_CHANGED')
            arguments={**preview['changes']['conditions'],'reason':preview['changes']['reason'],
                'confirmation':dict(identity=preview['identity'],fingerprint=fingerprint,acknowledge_limits=True)}
            response=self._create_scoped_draft(pid,data_version_id,baseline_run_id,ACTION,arguments,request_id)
            return {**response,'identity':preview['identity'],'fingerprint':fingerprint,'simulation_only':True,
                'acknowledge_limits':True,'engine_executed':False,'calculation_submitted':False,
                'run':{'state':'NOT_RUN'} if not response['draft']['run_id'] else {'solve_run_id':response['draft']['run_id']}}

    def receipt_protection_view(self,pid,data_version_id,run_id,target='baseline',item_id=None,location_id=None):
        """Read the selected saved result; receipt facts never become a second ledger."""
        from multi_receipt_supply import enabled as readable_receipts
        from multi_receipt_supply import summary
        from service import artifact_root
        with self.lock:
            record=self.store.get(pid);snapshot=self.store.snapshot(pid)
            if record['data_version_id']!=data_version_id:raise InputError('DATA_VERSION_MISMATCH')
            version=self.imports.version(data_version_id)
            if version!=snapshot['data_version']:raise InputError('VERSION_CHANGED')
            self.imports.verify_version_sources(version)
            service=self.service(pid);service.drafts.verify_baseline()
            if not readable_receipts(service.drafts.baseline):raise InputError('UNSUPPORTED_PROFILE_CAPABILITY: receipt_protection')
            data=service.drafts.baseline if target=='baseline' else service.drafts.for_run(target)['input']
            if item_id is not None and item_id not in {r['id'] for r in data['items']}:raise InputError('UNKNOWN_ITEM')
            if location_id is not None and location_id!=data['site']['id']:raise InputError('UNKNOWN_LOCATION')
            view=service.selected_view(target)
            if view.get('run',{}).get('solve_run_id')!=run_id:raise InputError('RECEIPT_PROTECTION_RUN_TARGET_MISMATCH')
            if view.get('run',{}).get('state')!='SUCCEEDED':raise InputError('RUN_NOT_READY')
            folder=artifact_root()/run_id
            try:
                actual=json.loads((folder/'canonical_input.json').read_text('utf8'))
                manifest=json.loads((folder/'run-manifest.json').read_text('utf8'))
                for field,wanted in [('plan_id',pid),('data_version_id',data_version_id),('snapshot_sha256',record['snapshot_sha256'])]:
                    if manifest.get(field)!=wanted:raise ValueError('run identity')
                digest=hashlib.sha256((folder/'canonical_input.json').read_bytes()).hexdigest()
                if digest!=manifest['files']['canonical_input.json'] or actual!=data:raise ValueError('input mismatch')
            except (OSError,ValueError,KeyError,TypeError):raise InputError('RUN_SOURCE_MISMATCH') from None
            current=summary(data)
            identity=dict(plan_id=pid,data_version_id=data_version_id,baseline_run_id=service.baseline_id(),run_id=run_id,
                target=target,profile=data['rules']['manufacturing_profile'],snapshot_sha256=record['snapshot_sha256'],
                source_sha256=version['file_sha256'],input_hash=hashlib.sha256(json.dumps(data,sort_keys=True,
                    ensure_ascii=False,separators=(',',':')).encode()).hexdigest(),
                manifest_sha256=hashlib.sha256((folder/'run-manifest.json').read_bytes()).hexdigest())
            material_validation=copy.deepcopy(view.get('interpretation',{}).get('material_validation'))
            resource_validation=copy.deepcopy(view.get('interpretation',{}).get('qualified_resource_validation'))
            constraint_check=view.get('result',{}).get('assessment',{}).get('constraint_check','NOT_CHECKED')
            components=dict(material=(material_validation or {}).get('status','NOT_CHECKED'),
                qualified_resources=(resource_validation or {}).get('status','NOT_CHECKED'),
                plan_result_constraints=constraint_check)
            joint_issues=[];joint_missing=[]
            for name,report in [('material',material_validation),('qualified_resources',resource_validation)]:
                joint_issues.extend({**copy.deepcopy(issue),'component':name} for issue in (report or {}).get('issues',[]))
                joint_missing.extend(name+':'+str(missing) for missing in (report or {}).get('missing_evidence',[]))
                if components[name] not in {'PASS','FAIL'} and not (report or {}).get('missing_evidence'):
                    joint_missing.append(name+':validation')
            if constraint_check=='FAIL':joint_issues.append(dict(code='PLAN_RESULT_CONSTRAINT_CHECK_FAILED',component='plan_result_constraints'))
            elif constraint_check!='PASS':joint_missing.append('plan_result_constraints:validation')
            business_invalid=view.get('interpretation',{}).get('business_status')=='INVALID_SCHEDULE'
            if business_invalid:joint_issues.append(dict(code='RUN_BUSINESS_INVALID_SCHEDULE',component='business_status'))
            validation=dict(status='FAIL' if business_invalid or 'FAIL' in components.values() else
                'PASS' if all(status=='PASS' for status in components.values()) else 'NOT_CHECKED',
                scope='bounded_receipt_material_qualified_resources_and_plan_constraints',
                components=components,issues=joint_issues,missing_evidence=sorted(set(joint_missing)))
            # This is a projection of already executed checks, not a new MO audit.
            # Unrelated material failures cannot be relabeled as MO window drift.
            mo_issues=[copy.deepcopy(issue) for issue in (material_validation or {}).get('issues',[])
                       if issue.get('code','').startswith(('PROTECTED_MO_', 'MULTI_RECEIPT_NATIVE_IDENTITY', 'MULTI_RECEIPT_GLOBAL_PROTECTION'))]
            protected_validation=dict(status='FAIL' if mo_issues else
                'PASS' if (material_validation or {}).get('status')=='PASS' else 'NOT_CHECKED',
                scope='protected_mo_identity_window_from_receipt_audit',
                source_scope=(material_validation or {}).get('scope'),issues=mo_issues,
                missing_evidence=copy.deepcopy((material_validation or {}).get('missing_evidence',[])))
            events=copy.deepcopy((material_validation or {}).get('summary',{}).get('material_events'))
            event_source='raw/operationplanmaterial.json'
            event_sha=manifest.get('files',{}).get(event_source)
            event_status='NOT_COLLECTED';event_file=folder/event_source
            if event_sha:
                try:
                    encoded=event_file.read_bytes()
                    if hashlib.sha256(encoded).hexdigest()!=event_sha:raise InputError('EVIDENCE_INTEGRITY_MISMATCH: '+event_source)
                    collected=json.loads(encoded)
                    if not isinstance(collected,list):raise ValueError('native event table')
                    event_status=('INVALID_EVIDENCE' if events is None or (collected and not events)
                                  else 'PRESENT' if events else 'EMPTY_CHECKED')
                except (OSError,ValueError):event_status='UNREADABLE'
            # Raw event quantities are run-wide observations, not order allocations.
            material_events=dict(status=event_status,events=events if events is not None else [],
                validation_status=(material_validation or {}).get('status','NOT_CHECKED'),relation_scope='RUN_LEVEL_ONLY',
                source=dict(run_id=run_id,artifact=event_source,sha256=event_sha))
            purchases=copy.deepcopy(current['purchase_orders'])
            sources={s['id']:s for s in data['supply_sources']}
            for po in purchases:
                po.update(physical_receipt_at=po['expected_receipt_at'],qc_wait_seconds=sources[po['supply_source_id']]['receiving_qc']['wait_seconds'])
            stocks=[copy.deepcopy(r) for r in data['inventory_positions']
                    if (item_id is None or r['item_id']==item_id) and (location_id is None or r['location_id']==location_id)]
            audited_stock={(r['item_id'],r['location_id']):r for r in (material_validation or {}).get('summary',{}).get('inventory',[])}
            for stock in stocks:
                audited=audited_stock.get((stock['item_id'],stock['location_id']))
                stock['net_available']=audited.get('opening_net_available') if audited and (material_validation or {}).get('status')=='PASS' else None
                stock['net_available_evidence_status']=(material_validation or {}).get('status','NOT_CHECKED')
            protected=copy.deepcopy(current['protected_manufacturing_orders'])
            protected_ids={m['id'] for m in protected}
            observed=[copy.deepcopy(m) for m in view.get('result',{}).get('existing_manufacturing_orders',[])
                      if m.get('engine_ref') in protected_ids]
            observed_ids={m['id'] for m in observed}
            observed_operations=[copy.deepcopy(o) for o in view.get('result',{}).get('operations',[])
                                 if o.get('manufacture_id') in observed_ids]
            operation_ids={o['id'] for o in observed_operations}
            observed_loads=[copy.deepcopy(r) for r in view.get('result',{}).get('resource_loads',[])
                            if r.get('operation_id') in operation_ids]
            return dict(identity=identity,purchase_orders=purchases,purchase_receipts=current['purchase_receipts'],
                inventory_positions=stocks,stock_facts=copy.deepcopy(stocks),existing_manufacturing_orders=protected,
                manufacturing_orders=copy.deepcopy(protected),protected_mo_validation=protected_validation,
                observed_manufacturing_orders=observed,observed_operations=observed_operations,
                observed_resource_loads=observed_loads,
                validation=validation,material_validation=material_validation,resource_validation=resource_validation,
                qualified_resource_validation=copy.deepcopy(resource_validation),material_events=material_events,
                native_events=copy.deepcopy(material_events),summary=current,
                business_status=view.get('interpretation',{}).get('business_status'),
                simulation_only=True,business_permission='NOT_CONFIRMED',execution_authorized=False,
                limits=current['limits'],workbench_url='/workspace/#/plans/'+pid+'?target='+target)

    def _controlled_conditions_scope(self,pid,data_version_id,baseline_run_id,run_id,target,*,_service=None):
        from controlled_conditions import PROFILE
        from planning_capabilities import profile_id,supports
        record=self.store.get(pid);snapshot=self.store.snapshot(pid)
        if record['data_version_id']!=data_version_id:raise InputError('DATA_VERSION_MISMATCH')
        version=self.imports.version(data_version_id)
        if version!=snapshot['data_version']:raise InputError('VERSION_CHANGED: 当前版本与计划冻结版本不一致')
        self.imports.verify_version_sources(version)
        service=_service if _service is not None else self.service(pid)
        service.drafts.verify_baseline()
        if not service.drafts.allow_changes or version['business_data'].get('execution_checkpoint'):
            raise InputError('CHECKPOINT_BASELINE_ONLY: 执行结转版本仅支持下一轮基线')
        if service.baseline_id()!=baseline_run_id:raise InputError('BASELINE_RUN_MISMATCH')
        if target!='baseline' or run_id!=baseline_run_id:raise InputError('CONTROLLED_REQUIRES_CURRENT_BASELINE')
        if service.compute().run(run_id).get('state')!='SUCCEEDED':raise InputError('BASELINE_NOT_READY')
        profile=profile_id(service.drafts.baseline)
        if profile!=PROFILE or not supports(profile,'actions','controlled_conditions'):
            raise InputError('UNSUPPORTED_PROFILE_CAPABILITY: controlled_conditions')
        identity=dict(plan_id=pid,data_version_id=data_version_id,baseline_run_id=baseline_run_id,
            run_id=run_id,target=target,profile=profile,snapshot_sha256=record['snapshot_sha256'],
            source_sha256=version['file_sha256'])
        return service,identity

    def _preflight_controlled_conditions_run(self,pid,draft):
        """Only the first P413 submission rechecks the current frozen parent/source."""
        from p21.store import ImportError as SourceError
        identity=draft['changes']['confirmation']['identity']
        try:
            _,current=self._controlled_conditions_scope(pid,identity['data_version_id'],identity['baseline_run_id'],
                identity['run_id'],identity['target'],_service=self.services[pid])
        except SourceError as error:
            # The existing public P3 run route handles InputError, not P2's source error.
            raise InputError(str(error)) from None
        if current!=identity:raise InputError('CONTROLLED_RUN_IDENTITY_MISMATCH: 当前计划或来源与已确认预览不一致')

    def preview_controlled_conditions(self,pid,data_version_id,baseline_run_id,run_id,target,reason,order=None,operator=None):
        from controlled_conditions import apply
        with self.lock:
            service,identity=self._controlled_conditions_scope(pid,data_version_id,baseline_run_id,run_id,target)
            changes=apply(copy.deepcopy(service.drafts.baseline),reason=reason,order=order,operator=operator)
            payload=dict(identity=identity,baseline_sha256=service.drafts.digest,changes=changes)
            fingerprint=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
            return dict(identity=identity,fingerprint=fingerprint,changes=changes,simulation_only=True,
                engine_executed=False,calculation_submitted=False)

    def create_controlled_conditions_draft(self,pid,data_version_id,baseline_run_id,run_id,target,reason,
            source_sha256,fingerprint,acknowledge_limits,request_id,order=None,operator=None):
        from controlled_conditions import ACTION
        with self.lock:
            if acknowledge_limits is not True:raise InputError('CONTROLLED_ACK_REQUIRED')
            preview=self.preview_controlled_conditions(pid,data_version_id,baseline_run_id,run_id,target,reason,order,operator)
            if source_sha256!=preview['identity']['source_sha256']:raise InputError('SOURCE_SHA_MISMATCH')
            if fingerprint!=preview['fingerprint']:raise InputError('CONTROLLED_PREVIEW_CHANGED: 请重新核对当前条件预览')
            arguments={**preview['changes']['conditions'],'reason':preview['changes']['reason'],
                'confirmation':dict(identity=preview['identity'],fingerprint=fingerprint,acknowledge_limits=True)}
            response=self._create_scoped_draft(pid,data_version_id,baseline_run_id,ACTION,arguments,request_id)
            return {**response,'identity':preview['identity'],'fingerprint':fingerprint,'simulation_only':True,
                'acknowledge_limits':True,'engine_executed':False,'calculation_submitted':False,
                'run':{'state':'NOT_RUN'} if not response['draft']['run_id'] else {'solve_run_id':response['draft']['run_id']}}

    def _inventory_decision_scope(self,pid,data_version_id,baseline_run_id,run_id,target,*,_service=None):
        from inventory_decision_scenario import available
        record=self.store.get(pid);snapshot=self.store.snapshot(pid)
        if record['data_version_id']!=data_version_id:raise InputError('DATA_VERSION_MISMATCH')
        version=self.imports.version(data_version_id)
        if version!=snapshot['data_version']:raise InputError('VERSION_CHANGED: 当前版本与计划冻结版本不一致')
        self.imports.verify_version_sources(version)
        service=_service if _service is not None else self.service(pid)
        service.drafts.verify_baseline()
        if not service.drafts.allow_changes or version['business_data'].get('execution_checkpoint'):
            raise InputError('CHECKPOINT_BASELINE_ONLY')
        if service.baseline_id()!=baseline_run_id:raise InputError('BASELINE_RUN_MISMATCH')
        if target!='baseline' or run_id!=baseline_run_id:raise InputError('INVENTORY_DECISION_REQUIRES_CURRENT_BASELINE')
        if service.compute().run(run_id).get('state')!='SUCCEEDED':raise InputError('BASELINE_NOT_READY')
        if not available(service.drafts.baseline):raise InputError('UNSUPPORTED_PROFILE_CAPABILITY: inventory_decision')
        identity=dict(plan_id=pid,data_version_id=data_version_id,baseline_run_id=baseline_run_id,run_id=run_id,
            target=target,profile=service.drafts.baseline['rules']['manufacturing_profile'],
            snapshot_sha256=record['snapshot_sha256'],source_sha256=version['file_sha256'])
        return service,identity

    def _preflight_inventory_decision_run(self,pid,draft):
        from p21.store import ImportError as SourceError
        identity=draft['changes']['confirmation']['identity']
        try:
            _,current=self._inventory_decision_scope(pid,identity['data_version_id'],identity['baseline_run_id'],
                identity['run_id'],identity['target'],_service=self.services[pid])
        except SourceError as error:
            raise InputError(str(error)) from None
        if current!=identity:raise InputError('INVENTORY_DECISION_RUN_IDENTITY_MISMATCH')

    def _inventory_decision_outcome(self,pid,data_version_id,run_id,target,data,identity,validation):
        """Read the current native minimum against the existing verified event projection.

        The diagnostic row exists only in this response. It never changes a
        version's inventory policy or creates a second inventory/event ledger.
        """
        from decimal import Decimal
        from inventory_policy import project,QUANTITY_FIELDS
        policy=data.get('inventory_buffer_policy')
        if policy is None:return None
        native_status=validation.get('status','NOT_CHECKED') if isinstance(validation,dict) else 'NOT_CHECKED'
        value=dict(identity=copy.deepcopy(identity),status='NOT_CHECKED',goal_status='NOT_CHECKED',
            target_quantity=policy['minimum'],replenishment_target_quantity=policy['maximum'],
            native_policy_status=native_status,first_gap_at=None,minimum_at=None,gap_duration_seconds=None,
            negative_inventory_status='NOT_CHECKED',known_business_fail=native_status=='FAIL',
            sources=[copy.deepcopy(policy['provenance'])],evidence_files=[],
            scope='NATIVE_SOFT_MINIMUM_DIAGNOSIS_NOT_HARD_FEASIBILITY')
        value.update({field:None for field in QUANTITY_FIELDS})
        if native_status!='PASS':
            value['reason']='原生政策载入尚未核验通过，不能判断库存软目标是否达到。'
            return value
        try:model=self.run_analysis(pid,data_version_id,run_id,target)
        except InputError as exc:
            if not str(exc).startswith('EVIDENCE_'):raise
            value.update(reason=str(exc),evidence_status=str(exc).split(':',1)[0])
            return value
        expected=hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
        if model.get('identity',{}).get('input_hash')!=expected:
            raise InputError('RUN_SOURCE_MISMATCH: 库存目标与运行实际输入不一致')
        value['identity'].update({key:model['identity'][key] for key in ('input_hash','manifest_sha256') if key in model['identity']})
        diagnostic=dict(id='P414-NATIVE-MINIMUM:'+policy['item_id']+'@'+policy['location_id'],
            policy_version=policy['provenance']['source_version']+':P414-NATIVE-MINIMUM',
            fact_kind='SYNTHETIC_POLICY_ASSUMPTION',target_kind='MINIMUM_NET_AVAILABLE',
            item_id=policy['item_id'],location_id=policy['location_id'],uom=policy['uom'],
            target_quantity=policy['minimum'],start_at=policy['start_at'],end_at=policy['end_at'],
            provenance=copy.deepcopy(policy['provenance']))
        projected=project(data,model,identity,policies=[diagnostic],item_id=policy['item_id'],
            location_id=policy['location_id'],uom=policy['uom'])
        value.update(known_business_fail=projected['known_business_fail'],
            evidence_files=copy.deepcopy(model.get('evidence_files',[])),
            evidence_status=projected['status'],window=copy.deepcopy(projected.get('window')))
        rows=projected['rows']
        if len(rows)!=1 or rows[0]['status']!='PRESENT':
            value['reason']=rows[0].get('reason','库存事件尚未完整核验。') if len(rows)==1 else projected.get('reason','未取得唯一库存目标诊断。')
            return value
        row=rows[0]
        value.update({field:row[field] for field in QUANTITY_FIELDS})
        minimum=Decimal(row['minimum_quantity'])
        # Read the first occurrence from the already verified balance sequence;
        # target-gap time can precede this and is a different business fact.
        minimum_at=row['start_at'] if Decimal(row['opening_quantity'])==minimum else next(
            (event['at'] for event in row['events'] if Decimal(str(event['balance']))==minimum),None)
        value.update(status='PRESENT',goal_status='BELOW_SOFT_TARGET' if Decimal(row['maximum_gap_quantity'])>0 else 'GOAL_MET',
            first_gap_at=row['first_gap_at'],minimum_at=minimum_at,negative_inventory_status=row['negative_inventory_status'],
            sources=copy.deepcopy(row['sources']),event_ids=copy.deepcopy(row['event_ids']),
            policy=copy.deepcopy(row['policy']),end_boundary=row['end_boundary'])
        return value

    def inventory_decision_view(self,pid,data_version_id,run_id,target='baseline',item_id=None,location_id=None):
        from inventory_decision_scenario import available
        from inventory_decision import summary
        with self.lock:
            record=self.store.get(pid);snapshot=self.store.snapshot(pid)
            if record['data_version_id']!=data_version_id:raise InputError('DATA_VERSION_MISMATCH')
            version=self.imports.version(data_version_id)
            if version!=snapshot['data_version']:raise InputError('VERSION_CHANGED')
            self.imports.verify_version_sources(version)
            service=self.service(pid);service.drafts.verify_baseline()
            if not available(service.drafts.baseline):raise InputError('UNSUPPORTED_PROFILE_CAPABILITY: inventory_decision')
            if item_id!='LABEL' or location_id!=service.drafts.baseline['site']['id']:
                raise InputError('INVENTORY_DECISION_DIMENSION')
            view=service.selected_view(target)
            if view.get('run',{}).get('solve_run_id')!=run_id:raise InputError('INVENTORY_DECISION_RUN_TARGET_MISMATCH')
            data=service.drafts.baseline if target=='baseline' else service.drafts.for_run(target)['input']
            identity=dict(plan_id=pid,data_version_id=data_version_id,baseline_run_id=service.baseline_id(),run_id=run_id,
                target=target,profile=data['rules']['manufacturing_profile'],snapshot_sha256=record['snapshot_sha256'],
                source_sha256=version['file_sha256'])
            current=summary(data);original=summary(service.drafts.baseline)
            source=next(s for s in data['supply_sources'] if s['id']==data['accessory_supply_option']['source_id'])
            policy_validation=view.get('interpretation',{}).get('inventory_decision')
            outcome=self._inventory_decision_outcome(pid,data_version_id,run_id,target,data,identity,policy_validation)
            supply_validation=view.get('interpretation',{}).get('accessory_supply')
            return dict(identity=identity,catalog=current['catalog'],original_policy=original['policy'],
                current_policy=current['policy'],selected_supply=current['supply'],source=copy.deepcopy(source),
                supply_option=copy.deepcopy(data['accessory_supply_option']),
                stock_facts=[copy.deepcopy(r) for r in data['inventory_positions'] if r['item_id']==item_id and r['location_id']==location_id],
                validation=policy_validation,policy_outcome=outcome,
                supply_validation=supply_validation,
                purchase_orders=supply_validation.get('summary',{}).get('purchase_orders') if isinstance(supply_validation,dict) else None,
                material_validation=view.get('interpretation',{}).get('material_validation'),
                simulation_only=True,business_permission='NOT_CONFIRMED',execution_authorized=False,
                limits=current['limits'],unit_price=None,currency=None,price_status='NOT_PROVIDED')

    def preview_inventory_decision(self,pid,data_version_id,baseline_run_id,run_id,target,reason,policy=None,source_option_id=None):
        from inventory_decision_scenario import apply
        with self.lock:
            service,identity=self._inventory_decision_scope(pid,data_version_id,baseline_run_id,run_id,target)
            changes=apply(copy.deepcopy(service.drafts.baseline),reason=reason,policy=policy,source_option_id=source_option_id)
            payload=dict(identity=identity,baseline_sha256=service.drafts.digest,changes=changes)
            fingerprint=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
            return dict(identity=identity,fingerprint=fingerprint,changes=changes,simulation_only=True,
                engine_executed=False,calculation_submitted=False)

    def create_inventory_decision_draft(self,pid,data_version_id,baseline_run_id,run_id,target,reason,
            source_sha256,fingerprint,acknowledge_limits,request_id,policy=None,source_option_id=None):
        from inventory_decision_scenario import ACTION
        with self.lock:
            if acknowledge_limits is not True:raise InputError('INVENTORY_DECISION_ACK_REQUIRED')
            preview=self.preview_inventory_decision(pid,data_version_id,baseline_run_id,run_id,target,reason,policy,source_option_id)
            if source_sha256!=preview['identity']['source_sha256']:raise InputError('SOURCE_SHA_MISMATCH')
            if fingerprint!=preview['fingerprint']:raise InputError('INVENTORY_DECISION_PREVIEW_CHANGED: 请重新核对当前预览')
            arguments={**preview['changes']['conditions'],'reason':preview['changes']['reason'],
                'confirmation':dict(identity=preview['identity'],fingerprint=fingerprint,acknowledge_limits=True)}
            response=self._create_scoped_draft(pid,data_version_id,baseline_run_id,ACTION,arguments,request_id)
            return {**response,'identity':preview['identity'],'fingerprint':fingerprint,'simulation_only':True,
                'acknowledge_limits':True,'engine_executed':False,'calculation_submitted':False,
                'run':{'state':'NOT_RUN'} if not response['draft']['run_id'] else {'solve_run_id':response['draft']['run_id']}}

    def _accessory_identity(self,pid,data_version_id,run_id,target):
        from accessory_supply import enabled
        record=self.store.get(pid);self.store.snapshot(pid)
        if record['data_version_id']!=data_version_id:raise InputError('DATA_VERSION_MISMATCH')
        service=self.service(pid);service.drafts.verify_baseline()
        if not enabled(service.drafts.baseline):raise InputError('UNSUPPORTED_PROFILE_CAPABILITY: accessory_supply')
        view=service.selected_view(target)
        if view.get('run',{}).get('solve_run_id')!=run_id:raise InputError('ACCESSORY_RUN_TARGET_MISMATCH')
        data=service.drafts.baseline if target=='baseline' else service.drafts.for_run(target)['input']
        identity=dict(plan_id=pid,data_version_id=data_version_id,baseline_run_id=service.baseline_id(),run_id=run_id,target=target,profile=data['rules']['manufacturing_profile'],snapshot_sha256=record['snapshot_sha256'])
        return service,identity,data,view

    def accessory_supply_view(self,pid,data_version_id,run_id,target='baseline'):
        from accessory_supply import summary
        with self.lock:
            _,identity,data,view=self._accessory_identity(pid,data_version_id,run_id,target)
            validation=view.get('interpretation',{}).get('accessory_supply')
            return dict(identity=identity,option=summary(data),simulation_only=True,validation=validation,
                purchase_orders=validation.get('summary',{}).get('purchase_orders') if isinstance(validation,dict) else None)

    def preview_accessory_supply(self,pid,data_version_id,baseline_run_id,run_id,target,source_id,reason):
        from accessory_scenario import apply
        from accessory_supply import summary
        with self.lock:
            service,identity,data,_=self._accessory_identity(pid,data_version_id,run_id,target)
            if baseline_run_id!=service.baseline_id():raise InputError('BASELINE_RUN_MISMATCH')
            if target!='baseline' or run_id!=baseline_run_id:raise InputError('ACCESSORY_REQUIRES_BASELINE')
            if service.compute().run(run_id).get('state')!='SUCCEEDED':raise InputError('BASELINE_NOT_READY')
            candidate=copy.deepcopy(data);changes=apply(candidate,source_id,reason)
            return dict(identity=identity,original=summary(data),proposed=summary(candidate),changes=changes,simulation_only=True,calculation_submitted=False,engine_executed=False)

    def create_accessory_supply_draft(self,pid,data_version_id,baseline_run_id,run_id,target,source_id,reason,request_id):
        with self.lock:
            preview=self.preview_accessory_supply(pid,data_version_id,baseline_run_id,run_id,target,source_id,reason)
            response=self._create_scoped_draft(pid,data_version_id,baseline_run_id,'enable_accessory_supply',dict(source_id=source_id,reason=reason),request_id)
            return {**response,'identity':preview['identity'],'simulation_only':True,'run':{'state':'NOT_RUN'} if not response['draft']['run_id'] else {'solve_run_id':response['draft']['run_id']}}

    def _firm_demand_scope(self,pid,data_version_id,baseline_run_id,run_id,target):
        from firm_scenario import PROFILES
        from planning_capabilities import profile_id,supports
        record=self.store.get(pid);self.store.snapshot(pid)
        if record['data_version_id']!=data_version_id:raise InputError('DATA_VERSION_MISMATCH')
        service=self.service(pid);service.drafts.verify_baseline()
        if service.baseline_id()!=baseline_run_id:raise InputError('BASELINE_RUN_MISMATCH')
        if target!='baseline' or run_id!=baseline_run_id:raise InputError('FIRM_REQUIRES_CURRENT_BASELINE')
        if service.compute().run(run_id).get('state')!='SUCCEEDED':raise InputError('BASELINE_NOT_READY')
        profile=profile_id(service.drafts.baseline)
        if profile not in PROFILES or not supports(profile,'actions','firm_demand'):raise InputError('UNSUPPORTED_PROFILE_CAPABILITY: firm_demand')
        return service,dict(plan_id=pid,data_version_id=data_version_id,baseline_run_id=baseline_run_id,
            run_id=run_id,target=target,profile=profile,snapshot_sha256=record['snapshot_sha256'])

    def preview_firm_demand(self,pid,data_version_id,baseline_run_id,run_id,target,hypothesis,reason,simulation_permission):
        from firm_scenario import apply
        with self.lock:
            service,identity=self._firm_demand_scope(pid,data_version_id,baseline_run_id,run_id,target)
            changes=apply(copy.deepcopy(service.drafts.baseline),hypothesis,reason,simulation_permission)
            return dict(identity=identity,hypothesis=changes['added_demand'],input_hypothesis=changes['hypothesis'],before=changes['before'],after=changes['after'],
                changes=changes,simulation_only=True,engine_executed=False,calculation_submitted=False)

    def create_firm_demand_draft(self,pid,data_version_id,baseline_run_id,run_id,target,hypothesis,reason,simulation_permission,request_id):
        with self.lock:
            _,identity=self._firm_demand_scope(pid,data_version_id,baseline_run_id,run_id,target)
            response=self._create_scoped_draft(pid,data_version_id,baseline_run_id,'add_firm_demand',
                dict(hypothesis=hypothesis,reason=reason,simulation_permission=simulation_permission),request_id)
            return {**response,'identity':identity,'simulation_only':True,
                'run':{'state':'NOT_RUN'} if not response['draft']['run_id'] else {'solve_run_id':response['draft']['run_id']}}

    def _foundation_forecast_scope(self,pid,data_version_id,baseline_run_id,run_id,target,*,creating=False):
        from foundation_forecast import available
        from planning_capabilities import profile_id
        record=self.store.get(pid);self.store.snapshot(pid)
        if record['data_version_id']!=data_version_id:raise InputError('DATA_VERSION_MISMATCH')
        service=self.service(pid);service.drafts.verify_baseline()
        if service.baseline_id()!=baseline_run_id or run_id!=baseline_run_id:raise InputError('BASELINE_RUN_MISMATCH')
        if service.compute().run(baseline_run_id).get('state')!='SUCCEEDED':raise InputError('BASELINE_NOT_READY')
        if not available(service.drafts.baseline):raise InputError('UNSUPPORTED_PROFILE_CAPABILITY')
        if creating and target!='baseline':raise InputError('FORECAST_TARGET_MISMATCH')
        if target!='baseline':service.drafts.get(target)
        return service,dict(plan_id=pid,data_version_id=data_version_id,baseline_run_id=baseline_run_id,
            run_id=run_id,target=target,profile=profile_id(service.drafts.baseline),snapshot_sha256=record['snapshot_sha256'])

    def foundation_forecast_view(self,pid,data_version_id,baseline_run_id,run_id,target='baseline'):
        from foundation_forecast import project
        with self.lock:
            service,identity=self._foundation_forecast_scope(pid,data_version_id,baseline_run_id,run_id,target)
            return project(service.drafts.baseline,identity,drafts=service.drafts.list())

    def preview_foundation_forecast(self,pid,data_version_id,baseline_run_id,run_id,target,bucket,gross_quantity,reason):
        from foundation_forecast import preview
        with self.lock:
            service,identity=self._foundation_forecast_scope(pid,data_version_id,baseline_run_id,run_id,target,creating=True)
            return preview(service.drafts.baseline,identity,bucket,gross_quantity,reason,service.drafts.digest)

    def create_foundation_forecast_draft(self,pid,data_version_id,baseline_run_id,run_id,target,bucket,gross_quantity,reason,confirmation,request_id):
        from foundation_forecast import ACTION
        with self.lock:
            _,identity=self._foundation_forecast_scope(pid,data_version_id,baseline_run_id,run_id,target,creating=True)
            return self._create_scoped_draft(pid,data_version_id,baseline_run_id,ACTION,
                dict(identity=identity,bucket=bucket,gross_quantity=gross_quantity,reason=reason,confirmation=confirmation),request_id)

    def _forecast_override_scope(self,pid,data_version_id,baseline_run_id,run_id,target):
        from planning_capabilities import profile_id,supports
        from forecast_override import PROFILES
        record=self.store.get(pid);self.store.snapshot(pid)
        if record['data_version_id']!=data_version_id:raise InputError('DATA_VERSION_MISMATCH')
        service=self.service(pid)
        service.drafts.verify_baseline()
        if service.baseline_id()!=baseline_run_id:raise InputError('BASELINE_RUN_MISMATCH')
        if run_id!=baseline_run_id:raise InputError('FORECAST_RUN_MISMATCH: 预测覆盖只能从当前基线运行创建独立草案')
        if target!='baseline':raise InputError('FORECAST_TARGET_MISMATCH: 请返回原始基线选择预测桶')
        if service.compute().run(run_id).get('state')!='SUCCEEDED':raise InputError('BASELINE_NOT_READY')
        profile=profile_id(service.drafts.baseline)
        if profile not in PROFILES or not supports(profile,'actions','forecast_override'):
            raise InputError('UNSUPPORTED_PROFILE_CAPABILITY: 当前模型未认证预测毛需求覆盖')
        identity=dict(plan_id=pid,data_version_id=data_version_id,baseline_run_id=baseline_run_id,
            run_id=run_id,target=target,profile=profile,snapshot_sha256=record['snapshot_sha256'])
        return service,identity

    def preview_forecast_override(self,pid,data_version_id,baseline_run_id,run_id,target,bucket,gross_quantity,reason):
        from forecast_override import apply
        with self.lock:
            service,identity=self._forecast_override_scope(pid,data_version_id,baseline_run_id,run_id,target)
            changes=apply(copy.deepcopy(service.drafts.baseline),bucket,gross_quantity,reason)
            return dict(identity=identity,bucket=changes['bucket'],before=changes['before'],after=changes['after'],
                reason=changes['reason'],calculation_basis=changes['calculation_basis'],engine_executed=False,
                calculation_submitted=False,simulation_only=True)

    def create_forecast_override_draft(self,pid,data_version_id,baseline_run_id,run_id,target,bucket,gross_quantity,reason,request_id):
        with self.lock:
            _,identity=self._forecast_override_scope(pid,data_version_id,baseline_run_id,run_id,target)
            response=self._create_scoped_draft(pid,data_version_id,baseline_run_id,'set_forecast_gross_override',
                dict(bucket=bucket,gross_quantity=gross_quantity,reason=reason),request_id)
            return {**response,'identity':identity,'run':{'state':'NOT_RUN'} if not response['draft']['run_id'] else {'solve_run_id':response['draft']['run_id']}}

    def demand_decision_view(self,pid,data_version_id,run_id,target,kind):
        from service import artifact_root
        from planning_capabilities import profile_id
        with self.lock:
            model=self.run_analysis(pid,data_version_id,run_id,target)
            folder=artifact_root()/run_id
            data=json.loads((folder/'canonical_input.json').read_bytes())
            result=json.loads((folder/'PlanResult.json').read_bytes())
            identity={**model['identity'],'profile':profile_id(data),'result_hash':result['result_hash']}
            if kind=='forecast':
                from demand_planning import project
                service=self.service(pid);service.drafts.verify_baseline()
                draft=service.drafts.get(target) if target!='baseline' else None
                if draft:
                    from forecast_override import verify
                    verify(service.drafts.baseline,draft)
                if draft and draft.get('run_id')!=run_id:raise InputError('RUN_NOT_IN_PLAN')
                return project(data,result,identity,baseline_data=service.drafts.baseline,
                    changes=draft['changes'] if draft else None)
            else:
                from inventory_availability import project
            return project(data,result,identity)

    def inventory_policy_view(self,pid,data_version_id,run_id,target='baseline',**filters):
        from service import artifact_root
        from inventory_policy import project
        with self.lock:
            model=self.run_analysis(pid,data_version_id,run_id,target)
            data=json.loads((artifact_root()/run_id/'canonical_input.json').read_bytes())
            return project(data,model,model['identity'],**filters)

    def preview_execution_checkpoint(self,pid,data_version_id,baseline_run_id,checkpoint):
        from manufacturing_operations import preview_checkpoint
        with self.lock:
            record=self.store.get(pid);snapshot=self.store.snapshot(pid)
            if record['data_version_id']!=data_version_id:raise InputError('DATA_VERSION_MISMATCH')
            service=self.service(pid);service.drafts.verify_baseline()
            if service.baseline_id()!=baseline_run_id:raise InputError('BASELINE_RUN_MISMATCH')
            if service.compute().run(baseline_run_id).get('state')!='SUCCEEDED':raise InputError('BASELINE_NOT_READY')
            version=snapshot['data_version']
            self.imports.verify_version_sources(version)
            if version['business_data'].get('execution_checkpoint'):raise InputError('NESTED_CHECKPOINT_UNSUPPORTED')
            value=preview_checkpoint(version['business_data']['plan_input'],checkpoint)
            value['identity']=dict(plan_id=pid,data_version_id=data_version_id,run_id=baseline_run_id,target='baseline',snapshot_sha256=record['snapshot_sha256'])
            return value

    def manufacturing_work_view(self,pid,data_version_id,run_id,target='baseline'):
        from service import artifact_root
        from manufacturing_operations import project_work
        with self.lock:
            model=self.run_analysis(pid,data_version_id,run_id,target)
            folder=artifact_root()/run_id
            data=json.loads((folder/'canonical_input.json').read_bytes());result=json.loads((folder/'PlanResult.json').read_bytes())
            snapshot=self.store.snapshot(pid);record=snapshot['data_version']['business_data'].get('execution_checkpoint')
            value=project_work(data,result,record,model['identity'])
            native=model.get('manufacturing_analysis',{})
            if native.get('status') in {'PRESENT','EMPTY_CHECKED'} and native.get('validation',{}).get('status')=='PASS':
                observed={t['id']:t for t in native.get('tasks',[])}
                for task in value['tasks']:
                    if task['id'] in observed:
                        for field in ['resource_ids','status','native_reference','enterprise_reference','identity_scope','sources']:
                            task[field]=copy.deepcopy(observed[task['id']][field])
            value.update(evidence_files=model['evidence_files'],resource_evidence=model['resource_analysis']['status'],inventory_evidence=model['inventory_analysis']['status'])
            checks={k:model.get(k,{}).get('validation',{}) for k in ['manufacturing_analysis','resource_analysis','inventory_analysis','demand_analysis']}
            hard=result.get('assessment',{}).get('constraint_check')=='FAIL' or any(v.get('status')=='FAIL' for v in checks.values())
            value['validation']=dict(status='FAIL' if hard else 'PASS' if native.get('validation',{}).get('status')=='PASS' else 'NOT_CHECKED',
                native_constraint_check=result.get('assessment',{}).get('constraint_check','NOT_CHECKED'),components=checks,
                scope='manufacturing observation; not actual execution verification')
            if record:value['execution']['proof'].update(parent_plan_id=record['parent_plan_id'],parent_data_version_id=record['parent_data_version_id'])
            value['next_versions']=[]
            if target=='baseline':
                plans_by_version={p['data_version_id']:p for p in self.store.list()}
                for summary in self.imports.versions():
                    candidate=self.imports.version(summary['id'])
                    cp=candidate['business_data'].get('execution_checkpoint',{})
                    if cp.get('parent_plan_id')==pid and cp.get('parent_data_version_id')==data_version_id:
                        value['next_versions'].append(dict(data_version_id=candidate['id'],checkpoint_id=cp['proof']['checkpoint_id'],
                            plan=plans_by_version.get(candidate['id']),evidence_class='SYNTHETIC_RESEARCH_ASSUMPTION' if cp['proof']['confirmation']['declaration']=='SYNTHETIC_ACTUAL_EXECUTION' else 'CONFIRMED_EXECUTION_RECORD'))
            return value

    def rolling_comparison(self,pid,data_version_id,run_id,parent_plan_id,parent_run_id):
        from manufacturing_operations import verify_parent,operation_totals
        with self.lock:
            child_snapshot=self.store.snapshot(pid);parent_snapshot=self.store.snapshot(parent_plan_id)
            proof=verify_parent(child_snapshot['data_version'],parent_snapshot['data_version'],self.store.get(parent_plan_id))
            child=self.manufacturing_work_view(pid,data_version_id,run_id,'baseline')
            parent=self.manufacturing_work_view(parent_plan_id,parent_snapshot['data_version']['id'],parent_run_id,'baseline')
            cv=self.service(pid).baseline_view();pv=self.service(parent_plan_id).baseline_view()
            return dict(kind='CROSS_VERSION_ROLLING_COMPARISON',parent=parent['identity'],child=child['identity'],proof=proof,
                parent_operations=operation_totals(parent['tasks']),next_operations=operation_totals(child['tasks']),
                parent_deliveries=pv['result']['deliveries'],next_deliveries=cv['result']['deliveries'],
                checkpoint_validation=cv['interpretation'].get('checkpoint_validation',{'status':'NOT_CHECKED'}),
                task_matching='NOT_SUPPORTED_ACROSS_RUNS',execution_permission_granted=False)

    def create_execution_checkpoint(self,pid,data_version_id,baseline_run_id,checkpoint):
        """Confirm a bounded historical prefix into an isolated existing version."""
        with self.lock:
            # The final write has the same certified prefix boundary as preview;
            # direct HTTP clients cannot bypass it with a fifth shipment event.
            self.preview_execution_checkpoint(pid,data_version_id,baseline_run_id,checkpoint)
            record=self.store.get(pid);self.store.snapshot(pid)
            if record['data_version_id']!=data_version_id:raise InputError('DATA_VERSION_MISMATCH')
            service=self.service(pid)
            if service.baseline_id()!=baseline_run_id:raise InputError('BASELINE_RUN_MISMATCH')
            if service.compute().run(baseline_run_id)['state']!='SUCCEEDED':raise InputError('BASELINE_NOT_READY')
            value=self.imports.save_execution_checkpoint(data_version_id,checkpoint,parent_plan_id=pid,
                                                          parent_snapshot_sha256=record['snapshot_sha256'])
            return {'status':'CHECKPOINT_VERSION_CREATED','parent_plan_id':pid,'parent_data_version_id':data_version_id,
                    'baseline_run_id':baseline_run_id,'data_version_id':value['version_id'],
                    'deduplicated':value['deduplicated'],'checkpoint':value['checkpoint'],'calculation_submitted':False}

    def create_purchase_delay_draft(self,pid,data_version_id,baseline_run_id,reference,new_receipt_at,request_id):
        return self._create_scoped_draft(pid,data_version_id,baseline_run_id,'delay_confirmed_purchase',
            {'reference':reference,'new_receipt_at':new_receipt_at},request_id)

    def create_approved_mo_draft(self,pid,data_version_id,baseline_run_id,reference,new_start_at,request_id):
        return self._create_scoped_draft(pid,data_version_id,baseline_run_id,'simulate_approved_mo_move',
            {'reference':reference,'new_start_at':new_start_at},request_id)

    def approved_mo_windows(self,pid,data_version_id,baseline_run_id,reference):
        from approved_simulation import legal_windows
        with self.lock:
            record=self.store.get(pid);self.store.snapshot(pid)
            if record['data_version_id']!=data_version_id:raise InputError('DATA_VERSION_MISMATCH')
            service=self.service(pid)
            if service.baseline_id()!=baseline_run_id:raise InputError('BASELINE_RUN_MISMATCH')
            if service.compute().run(baseline_run_id).get('state')!='SUCCEEDED':raise InputError('BASELINE_NOT_READY')
            return {'plan_id':pid,'data_version_id':data_version_id,'baseline_run_id':baseline_run_id,'reference':reference,'windows':legal_windows(service.drafts.baseline,reference),'simulation_only':True}

    def create_source_terms_draft(self,pid,data_version_id,baseline_run_id,source_id,request_id,capacity_quantity=None,minimum_order_quantity=None):
        args={'source_id':source_id}
        if capacity_quantity is not None:args['capacity_quantity']=capacity_quantity
        if minimum_order_quantity is not None:args['minimum_order_quantity']=minimum_order_quantity
        return self._create_scoped_draft(pid,data_version_id,baseline_run_id,'simulate_priority_source_terms',args,request_id)

    def create_resource_unavailability_draft(self,pid,data_version_id,baseline_run_id,resource_id,start_at,end_at,request_id):
        return self._create_scoped_draft(pid,data_version_id,baseline_run_id,'set_resource_unavailable',
            {'resource_id':resource_id,'start_at':start_at,'end_at':end_at},request_id)

    def run_analysis(self,pid,data_version_id,run_id,target='baseline',**filters):
        """Read current P3-owned historical evidence without creating a run or ledger."""
        from service import artifact_root
        from planning_analysis import read_analysis,select_analysis
        with self.lock:
            record=self.store.get(pid)
            if record['data_version_id']!=data_version_id:raise InputError('DATA_VERSION_MISMATCH')
            if not re.fullmatch(r'API-[a-f0-9]{16}',run_id):raise InputError('INVALID_RUN_ID')
            service=self.service(pid)
            targets=(['baseline'] if service.baseline_id()==run_id else [])
            targets += service.drafts.run_targets(run_id)
            if len(targets)!=1 or target!=targets[0]:raise InputError('RUN_NOT_IN_PLAN: 运行与计划/方案身份不一致')
            run=read_run(service,run_id)
            if run.get('state')!='SUCCEEDED':raise InputError('ANALYSIS_RUN_NOT_SUCCEEDED: 此运行未产生正式成功结果')
            identity={'plan_id':pid,'data_version_id':data_version_id,'run_id':run_id,
                      'snapshot_sha256':record['snapshot_sha256'],'target':target,
                      'scenario_id':pid if target=='baseline' else target}
        with planning_read_scope(service,reuse=True) as reads:
            return select_analysis(read_analysis(artifact_root()/run_id,identity,owner=service,reuse=reads['reuse_immutable']),**filters)

    def period_reports(self,pid,data_version_id,run_id,target='baseline',grain='day',start=None,end=None):
        from planning_reports import period_reports
        return period_reports(self.run_analysis(pid,data_version_id,run_id,target),grain,start,end)

    def exceptions(self,pid,data_version_id,run_id,target='baseline'):
        from service import artifact_root
        from planning_decision import exception_projection,read_table
        with self.lock:
            model=self.run_analysis(pid,data_version_id,run_id,target)
            folder=artifact_root()/run_id
            data=json.loads((folder/'canonical_input.json').read_bytes())
            result=json.loads((folder/'PlanResult.json').read_bytes())
            from planning_capabilities import profile_id
            value=exception_projection(data,result,{**model['identity'],'profile':profile_id(data),'result_hash':result['result_hash']},
                read_table(folder,'constraint',run_id),read_table(folder,'problem',run_id))
            # Constraint/Problem collection states must remain readable even when
            # the optional table is absent; selected_view requires all raw tables.
            value['validation']['business_status']='INVALID_SCHEDULE' if value['validation']['known_fail'] else None
            value['evidence_files']=copy.deepcopy(model.get('evidence_files',[]))
            value['schema_version']='planner-worklist/1'
            return value

    def scenario_review(self,pid,data_version_id,baseline_run_id,draft_ids=None):
        from service import artifact_root
        from planning_decision import candidate_projection,read_table,endpoint
        from planning_capabilities import profile_id
        with self.lock,ExitStack() as scopes:
            record=self.store.get(pid)
            if record['data_version_id']!=data_version_id:raise InputError('DATA_VERSION_MISMATCH')
            service=self.service(pid)
            reads=scopes.enter_context(planning_read_scope(service,reuse=True))
            if service.baseline_id()!=baseline_run_id:raise InputError('BASELINE_RUN_MISMATCH')
            drafts=service.drafts.metadata(limit=None)['rows'];lookup={d['id']:d for d in drafts}
            selected=list(lookup)[:2] if draft_ids is None else ([] if draft_ids=='' else draft_ids.split(','))
            if len(selected)>3 or len(set(selected))!=len(selected):raise InputError('INVALID_REVIEW_SELECTION')
            if any(d not in lookup for d in selected):raise InputError('DRAFT_NOT_IN_PLAN')
            for target in selected:lookup[target]=service.drafts.get(target)
            data=service.drafts.baseline;available=[];candidates=[]
            run_records=reads['review_status']
            def run_status(rid):
                if not rid:return None
                if rid not in run_records:
                    actual=read_run(service,rid)
                    if actual.get('solve_run_id')!=rid:raise InputError('REVIEW_RUN_IDENTITY_MISMATCH')
                    from agent import redact
                    projected={k:copy.deepcopy(actual[k]) for k in ('solve_run_id','state','failure_code','created_at','started_at','finished_at') if k in actual}
                    if isinstance(projected.get('failure_code'),str):projected['failure_code']=redact(projected['failure_code'])
                    run_records[rid]=projected
                return copy.deepcopy(run_records[rid])
            for d in drafts:
                status=run_status(d.get('run_id'));state=status.get('state') if status is not None else 'NOT_RUN'
                available.append({'id':d['id'],'intent':d.get('changes',{}).get('intent'),'run_id':d.get('run_id'),'state':d['state'],'calculation_state':state})
            for target in ['baseline',*selected]:
                d=lookup.get(target);rid=baseline_run_id if target=='baseline' else d.get('run_id')
                candidate_key=(pid,data_version_id,baseline_run_id,target,rid)
                if candidate_key in reads['candidates']:
                    candidates.append(copy.deepcopy(reads['candidates'][candidate_key]));continue
                status=run_status(rid);state=status.get('state') if status is not None else 'NOT_RUN'
                view=None;model=None;constraints={'status':'NOT_COLLECTED','records':[]}
                if state=='SUCCEEDED':
                    # This existing path checks immutable plan/version/run/scenario/context and source SHA.
                    model=self.run_analysis(pid,data_version_id,rid,target)
                    constraints=read_table(artifact_root()/rid,'constraint',rid)
                    from agent import AgentError
                    try:view=service.selected_view(target)
                    except (AgentError,InputError) as exc:
                        candidates.append({'target':target,'label':'原始基线' if d is None else d['changes']['intent'],
                            'run_id':rid,'run_status':status,'calculation_state':state,'business_status':'VALIDATION_INCOMPLETE',
                            'comparable':False,'reason':str(exc),'changes':d.get('changes') if d else None,
                            'deliveries':[],'inventory':[],'constraints':{'status':constraints['status'],'count':len(constraints['records'])},'validation':{}})
                        continue
                    if view['context'].get('manufacturing_profile',profile_id(data))!=profile_id(data):
                        raise InputError('REVIEW_PROFILE_MISMATCH')
                candidate=candidate_projection(target,'原始基线' if d is None else d.get('changes',{}).get('intent',target),state,view,rid,constraints,d.get('changes') if d else None)
                # Failure/async diagnosis is additive; keep successful evidence
                # byte-for-byte stable so metadata does not stale prior reviews.
                if state!='SUCCEEDED':candidate['run_status']=status
                line_by_id={line['id']:line for line in (d['input'] if d else data)['demand_lines']}
                foundation=profile_id(data)=='foundation-planning-projection-v1'
                if foundation:
                    from p22.foundation_projection import firm_order_refs
                    source_references=firm_order_refs(d['input'] if d else data)
                for line in candidate['deliveries']:
                    source=line_by_id.get(line['demand_id'])
                    if source is None:raise InputError('REVIEW_DEMAND_SCOPE_MISMATCH')
                    line.update({key:source[key] for key in ['item_id','location_id','uom']})
                    if foundation:
                        line['order_ref']=source_references[source['id']]
                        line['demand_kind']=source.get('kind','FIRM_ORDER')
                    else:
                        line['order_ref']=source['order_ref'];line['demand_kind']=source.get('kind')
                if model is not None:
                    manufacturing=model['manufacturing_analysis']
                    if manufacturing['validation']['status']=='FAIL':
                        candidate.update(comparable=False,reason='制造原生来源存在核验失败，不能比较合法指标。',deliveries=[],inventory=[])
                        candidate['validation']['manufacturing']=manufacturing['validation']
                    if candidate['comparable']:
                        candidate['professional']={'identity':model['identity'],'manufacturing':manufacturing,
                            'resources':model['resource_analysis'],'documents':model['document_analysis'],
                            'evidence_files':model['evidence_files'],'constraints':constraints}
                        if d and d.get('changes',{}).get('action')=='simulate_professional_policy':
                            from p22.professional_outcome import outcome,assessment_eligible
                            assessment=view.get('professional_assessment') or outcome(service,data,d,service.baseline_view(),view)
                            candidate['professional']['policy_assessment']=assessment
                            if not assessment_eligible(assessment):
                                candidate.update(comparable=False,reason='专业审计或原订单逐期交付保护未通过，不能作为合法可比方案。',deliveries=[],inventory=[])
                reads['candidates'][candidate_key]=copy.deepcopy(candidate)
                candidates.append(candidate)
            return {'identity':{'plan_id':pid,'data_version_id':data_version_id,'baseline_run_id':baseline_run_id,'profile':profile_id(data)},
                    'endpoint':endpoint(data),'timezone':data['site']['timezone'],'available_drafts':available,'candidates':candidates,
                    'limits':['同计划独立模拟，各自基于原始基线；不自动选择最优或授予执行许可。','缺少或失效证据的指标不按零展示；库存按物料、地点与单位分别比较。']}

    def review_notes(self,pid,data_version_id,baseline_run_id,draft_ids=None):
        from planning_reviews import review_gate,read_notes,note_state
        with self.lock,ExitStack() as scopes:
            record=self.store.get(pid)
            if record['data_version_id']!=data_version_id:raise InputError('DATA_VERSION_MISMATCH')
            service=self.service(pid)
            scopes.enter_context(planning_read_scope(service,reuse=True))
            if service.baseline_id()!=baseline_run_id:raise InputError('BASELINE_RUN_MISMATCH')
            drafts={d['id']:d for d in service.drafts.metadata(limit=None)['rows']}
            selected=list(drafts)[:2] if draft_ids is None else ([] if draft_ids=='' else draft_ids.split(','))
            if len(selected)>3 or len(set(selected))!=len(selected):raise InputError('INVALID_REVIEW_SELECTION')
            if any(t not in drafts for t in selected):raise InputError('DRAFT_NOT_IN_PLAN')
            gates={};errors={};resolved={}
            stored_notes=read_notes(service.drafts)
            needed=list(dict.fromkeys([*selected,*[n['identity']['target'] for n in stored_notes if n['identity']['target'] in drafts]]))
            def obtain(target,projection=None):
                try:
                    if projection is None:projection=self.scenario_review(pid,data_version_id,baseline_run_id,target)
                    resolved[target]=review_gate(projection,target)
                except (InputError,AgentError,DraftError) as exc:
                    errors[target]=str(exc);resolved[target]=None
            # One request shares each immutable baseline projection. This is
            # not a cross-request cache: every later read authenticates again.
            for offset in range(0,len(needed),3):
                chunk=needed[offset:offset+3]
                try:projection=self.scenario_review(pid,data_version_id,baseline_run_id,','.join(chunk))
                except (InputError,AgentError,DraftError):
                    # One corrupt target must not invalidate unrelated notes.
                    for target in chunk:obtain(target)
                else:
                    for target in chunk:obtain(target,projection)
            for target in selected:
                if resolved.get(target) is not None:gates[target]=resolved[target]
            notes=[{**note,'review_state':note_state(note,resolved.get(note['identity']['target']))} for note in stored_notes]
            from planning_capabilities import profile_id
            identity=dict(plan_id=pid,data_version_id=data_version_id,baseline_run_id=baseline_run_id,profile=profile_id(service.drafts.baseline))
            return dict(identity=identity,gates=gates,ineligible=errors,notes=notes,storage='EXISTING_P3_DRAFT_DATABASE',execution_authorized=False)

    def save_review_note(self,pid,data_version_id,baseline_run_id,target,fingerprint,recommendation,reviewer,reason,object_scope,next_action,acknowledge_limits):
        from planning_reviews import review_gate,make_note,save_note
        self.store.snapshot(pid)  # Explicit write boundary retains the complete independent Plan check.
        with self.lock,planning_read_scope(self.service(pid),fresh=True):
            projection=self.scenario_review(pid,data_version_id,baseline_run_id,target)
            gate=review_gate(projection,target)
            note=make_note(gate,fingerprint,recommendation,reviewer,reason,object_scope,next_action,acknowledge_limits)
            save_note(self.service(pid).drafts,note)
            return {**note,'review_state':'CURRENT_REVIEW'}

    def order_run_evidence(self,pid,data_version_id,run_id,order_line_id,*,target=None,require_valid_run=False):
        """Read a P3-owned run through the existing projection and R1 readers."""
        from service import artifact_root
        from diagnosis.context import bind_context,ContextError
        from diagnosis.tools import READ_TOOLS,read_tool,ToolError
        from order_evidence import TABLES
        with self.lock:
            record=self.store.get(pid)
            if record['data_version_id']!=data_version_id:raise InputError('DATA_VERSION_MISMATCH')
            if not re.fullmatch(r'API-[a-f0-9]{16}',run_id):raise InputError('INVALID_RUN_ID')
            # Authenticate the original Plan's pinned read projection. Full DATA
            # reconstruction remains available at snapshot/write/audit boundaries.
            self.store.read_source(pid)
            service=self.service(pid)
            targets=(['baseline'] if service.baseline_id()==run_id else [])
            targets += service.drafts.run_targets(run_id)
            if len(targets)!=1:raise InputError('RUN_NOT_IN_PLAN: 运行不是该计划当前基线或已保存草案的唯一关联运行')
            if target is not None and target!=targets[0]:raise InputError('RUN_NOT_IN_PLAN: 运行与指定方案不一致')
            if require_valid_run and service.compute().run(run_id).get('state')!='SUCCEEDED':
                raise InputError('INVESTIGATION_RUN_NOT_SUCCEEDED: 此运行没有正式成功的订单计划')
            folder=artifact_root()/run_id
            try:manifest=json.loads((folder/'run-manifest.json').read_text(encoding='utf-8'))
            except FileNotFoundError:raise InputError('EVIDENCE_NOT_COLLECTED: 缺少运行清单') from None
            except (OSError,ValueError):raise InputError('EVIDENCE_UNREADABLE: 运行清单不可读取') from None
            expected={'run_id':run_id,'plan_id':pid,'data_version_id':data_version_id,'snapshot_sha256':record['snapshot_sha256']}
            if not isinstance(manifest,dict) or any(manifest.get(k)!=v for k,v in expected.items()):raise InputError('RUN_SOURCE_MISMATCH')
            entries=manifest.get('files')
            if not isinstance(entries,dict):raise InputError('EVIDENCE_UNREADABLE: 运行清单文件索引无效')
            native={}
            for table in (*TABLES,'problem'):
                name='raw/'+table+'.json';digest=entries.get(name)
                if not digest:raise InputError('EVIDENCE_NOT_COLLECTED: '+name)
                if not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest):raise InputError('EVIDENCE_UNREADABLE: '+name+' 的摘要格式无效')
                try:content=(folder/name).read_bytes()
                except OSError:raise InputError('EVIDENCE_UNREADABLE: '+name) from None
                if hashlib.sha256(content).hexdigest()!=digest:raise InputError('EVIDENCE_INTEGRITY_MISMATCH: '+name)
                try:
                    value=json.loads(content)
                    rows=value if isinstance(value,list) else value['rows'] if 'rows' in value else value['results']
                    if not isinstance(rows,list) or any(not isinstance(r,dict) for r in rows):raise ValueError()
                except (ValueError,KeyError,TypeError):raise InputError('EVIDENCE_UNREADABLE: '+name) from None
                native[table]={'status':'PRESENT' if rows else 'EMPTY','row_count':len(rows),
                    'source':{'solve_run_id':run_id,'artifact':name,'sha256':digest},'relation_scope':'RUN_LEVEL_ONLY'}
                # These are run-wide observations, never inferred order allocations.
                if table in {'problem','purchaseorder','operationplanmaterial','operationplanresource'}:native[table]['records']=rows
            view=(self._investigation_view(service,targets[0],folder,manifest)
                  if require_valid_run else service.selected_view(targets[0]))
            if require_valid_run and view.get('interpretation',{}).get('business_status')=='INVALID_SCHEDULE':
                raise InputError('INVALID_SCHEDULE: FAIL，当前结果存在已知业务违规，不作为合法订单计划展示')
            source=view.get('context',{}).get('enterprise_source',{})
            if any(source.get(k)!=v for k,v in {'plan_id':pid,'data_version_id':data_version_id,'snapshot_sha256':record['snapshot_sha256']}.items()):raise InputError('RUN_SOURCE_MISMATCH')
            if view.get('result',{}).get('solve_run_id')!=run_id:raise InputError('RUN_SOURCE_MISMATCH')
            projection=view.get('order_evidence') or {}
            orders=[o for o in projection.get('orders',[]) if o['demand_id']==order_line_id]
            if len(orders)!=1:raise InputError('ORDER_LINE_NOT_FOUND: 请使用精确订单行编号，不使用共享客户订单引用')
            try:
                bound=bind_context(pid,targets[0],view,{'type':'order','id':order_line_id,'order_id':order_line_id},'',run_id)
                queries={name:read_tool(bound,name) for name in READ_TOOLS}
            except (ContextError,ToolError) as error:raise InputError(str(error)) from None
            return {'status':'PRESENT','scope':bound['scope'],'order':orders[0],'queries':queries,
                'native_tables':native,'problem_order_link':{'status':'UNSUPPORTED','reason':'现有订单投影未实现 Problem 的订单归属，运行级记录不自动成为本订单原因。'},
                'confirmed_supply_windows':view.get('interpretation',{}).get('confirmed_supply_windows'),
                'validation_scope':view.get('context',{}).get('validation_scope'),
                'limits':['原生关联不是因果证明；运行级采购、库存和资源事件不等于本订单的分配。','空约束或空关联不证明没有约束，也不证明所有方案无解。'],
                'workbench_url':'/workspace/#/plans/'+pid+'?target='+targets[0]}

    def _investigation_view(self,service,target,folder,manifest):
        """Bounded parsed-view reuse, never a replacement source of planning facts.

        Rehash all inputs read by PlanningService even on a cache hit. A size or
        mtime cache would miss same-size tampering. No cache is persisted.
        """
        core=['canonical_input.json','enterprise-context.json','native_input.json','PlanResult.json']
        # Let the owning reader report incomplete sources using its existing rules.
        if any(not (folder/name).is_file() for name in core):return service.selected_view(target)
        paths=[folder/name for name in core]+list((folder/'raw').glob('*.json'))+list((folder/'raw').glob('*.xml'))
        provenance=folder/'EngineProvenance-v1.json'
        if provenance.is_file():paths.append(provenance)
        foundation='foundation_projection' in getattr(getattr(service,'drafts',None),'baseline',{})
        signature=[]
        for path in sorted(paths):
            name=path.relative_to(folder).as_posix()
            expected=manifest.get('files',{}).get(name)
            if foundation and name.startswith('raw/') and not expected:
                raise InputError('EVIDENCE_INTEGRITY_MISMATCH: '+name+' 未列入运行清单')
            try:digest=hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:raise InputError('EVIDENCE_UNREADABLE: '+name) from None
            if expected and expected!=digest:raise InputError('EVIDENCE_INTEGRITY_MISMATCH: '+name)
            signature.append((name,digest))
        signature.append(('manifest',hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()))
        signature.append(('baseline',service.baseline_id()))
        if target!='baseline':signature.append(('draft',hashlib.sha256(json.dumps(service.drafts.get(target),sort_keys=True).encode()).hexdigest()))
        key=(str(folder),target);token=tuple(signature);cached=self._investigation_views.get(key)
        if cached and cached[0]==token:return copy.deepcopy(cached[1])
        self._investigation_views.pop(key,None)
        view=service.selected_view(target)
        self._investigation_views[key]=(token,copy.deepcopy(view))
        if len(self._investigation_views)>4:self._investigation_views.pop(next(iter(self._investigation_views)))
        return view

    def _create_scoped_draft(self,pid,data_version_id,baseline_run_id,action,arguments,request_id):
        with self.lock:
            record=self.store.get(pid);self.store.snapshot(pid)
            if record['data_version_id']!=data_version_id:raise InputError('DATA_VERSION_MISMATCH: 请求不属于该计划的数据版本')
            service=self.service(pid)
            if service.baseline_id()!=baseline_run_id:raise InputError('BASELINE_RUN_MISMATCH: 基线运行已变化，请重新读取计划上下文')
            if service.compute().run(baseline_run_id)['state']!='SUCCEEDED':raise InputError('BASELINE_NOT_READY: 基线尚未计算成功')
            from planning_capabilities import profile_id,supports
            feature={'set_foundation_forecast_gross':'foundation_forecast_management','simulate_distribution_leadtime':'distribution_leadtime','simulate_resource_capacity':'resource_capacity','simulate_outstanding_receipt':'outstanding_receipt','simulate_inventory_decision':'inventory_decision','simulate_controlled_conditions':'controlled_conditions','add_firm_demand':'firm_demand','enable_accessory_supply':'accessory_supply','set_demand_priority':'demand_priority','set_forecast_gross_override':'forecast_override','propose_due_change':'due_change','delay_confirmed_purchase':'purchase_delay',
                     'simulate_plan_editor':'plan_editor','simulate_approved_mo_move':'approved_mo_move','simulate_priority_source_terms':'priority_source_terms',
                     'set_resource_unavailable':'resource_unavailability'}.get(action)
            if feature and not __import__('planning_capabilities').supports_input(service.drafts.baseline,'actions',feature):
                raise InputError('UNSUPPORTED_PROFILE_CAPABILITY: '+feature+'，此profile尚未认证该操作')
            draft=service.drafts.propose(action,arguments,request_id=request_id)
            return {'plan_id':pid,'data_version_id':data_version_id,'baseline_run_id':baseline_run_id,
                    'snapshot_sha256':record['snapshot_sha256'],'draft':service.public_draft(draft),
                    'workbench_url':'/workspace/#/plans/'+pid+'?target='+draft['id'],
                    'storage':'EXISTING_P3_PLAN_WORKSPACE','calculation_submitted':bool(draft['run_id'])}

    def retry(self,pid,request_id):
        if not re.fullmatch('[a-f0-9]{32}',request_id):raise InputError('INVALID_RETRY_ID: 重试需要独立请求标识')
        with self.lock:
            service=self.service(pid);folder=service.root/'retries';folder.mkdir(exist_ok=True)
            receipt=folder/(request_id+'.json')
            if receipt.exists():
                record=json.loads(receipt.read_text(encoding='utf-8'))
                if record.get('kind')=='INITIAL_BASELINE_RECOVERY':return record['run']
            else:
                try:baseline_id=service.baseline_id()
                except AgentError as exc:
                    if str(exc)!='BASELINE_NOT_READY':raise
                    # PlanStore may already have committed before the original
                    # Worker rejected input. Bootstrap reuses the exact original
                    # idempotency key, also reconciling a lost accepted response.
                    run=service.bootstrap()
                    record={'kind':'INITIAL_BASELINE_RECOVERY','previous_run':None,'run':run,
                            'plan_id':pid,'bootstrap_idempotency_key':'p1-baseline-'+service.drafts.digest}
                    with receipt.open('x',encoding='utf-8') as f:json.dump(record,f,ensure_ascii=False,indent=2)
                    return run
                previous=service.compute().run(baseline_id)
                invalid_result=(previous['state']=='SUCCEEDED' and service.view(previous['solve_run_id']).get('result',{}).get('assessment',{}).get('constraint_check')=='FAIL')
                if previous['state']!='FAILED' and not invalid_result:raise InputError('RETRY_REQUIRES_FAILED: 当前计算未失败，不会重复提交')
                if previous.get('failure_code')=='RECOVERY_REQUIRED':raise InputError('RECOVERY_REVIEW_REQUIRED: 服务在计算中中断，需要先核对引擎任务是否已结束；不会盲目重算')
                run=service.submit(service.drafts.baseline,'p22-retry-'+pid+'-'+request_id)
                record={'previous_run':previous,'run':run}
                with receipt.open('x',encoding='utf-8') as f:json.dump(record,f,ensure_ascii=False,indent=2)
            # Recover a lost response without reverting a newer attempt.
            if service.baseline_id()==record['previous_run']['solve_run_id']:
                pointer=service.root/'baseline-run.json';temp=service.root/('baseline-run-'+request_id+'.tmp')
                temp.write_text(json.dumps(record['run'],ensure_ascii=False),encoding='utf-8');temp.replace(pointer)
            return record['run']
