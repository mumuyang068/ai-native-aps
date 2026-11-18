"""Exact read projections over existing APS Owners, never an alternate fact store.

Registry reads do not construct IntelligenceOwner.overview. Native demand reads
authenticate the consumed JSON members of the immutable run before reusing the
original work-list projection. Resource/inventory legality remains with the
existing analysis Owner; its global audit is not replaced with a local guess.
"""
import base64
import copy
import hashlib
import json
import time
from datetime import datetime

from planning_adapter import InputError


IDENTITY = ('plan_id','data_version_id','baseline_run_id','run_id','target')
FILTERS = {
    'demand_risks': {'order_line_id','item_id','location_id','customer_id','status','kind','start','end'},
    'resources': {'resource_id','operation_id','reference','role','start','end'},
    'inventory': {'item_id','location_id','uom','lot_id','status','kind','record_id','reference','start','end'},
    'manufacturing': {'job_id','order_line_id','operation_id','reference','status','kind','record_id','item_id','location_id','start','end'},
    'purchases': {'reference','source_id','supplier_id','item_id','location_id','status','start','end'},
    'forecast': {'version_id','row_id','dataset_id','item_id','location_id','customer_id','uom','status','start','end'},
    'inventory_research': {'inventory_id','source_id','item_id','location_id','uom','status'},
    'promising': {'quote_id','item_id','location_id','customer_id','source_id','status','start','end'},
    'schedule': {'search_id','reference','resource_id','operation_id','status'},
    'schedule_result': {'reference','status'},
    'versions': {'version_id'},
}


class QueryError(InputError): pass


def encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def digest(value): return hashlib.sha256(encoded(value).encode()).hexdigest()


def at(value):
    try:
        result=datetime.fromisoformat(value.replace('Z','+00:00'))
        if result.utcoffset() is None: raise ValueError()
        return result
    except (ValueError,TypeError,AttributeError): raise QueryError('EXPLICIT_QUERY_TIMEZONE_REQUIRED') from None


def validate_query(view,filters,page_size):
    if view not in FILTERS: raise QueryError('PROFESSIONAL_VIEW_UNSUPPORTED')
    if not isinstance(filters,dict) or set(filters)-FILTERS[view]: raise QueryError('TOOL_FILTER_NOT_APPLICABLE')
    if type(page_size) is not int or not 1<=page_size<=100: raise QueryError('QUERY_PAGE_BUDGET')
    if any(not isinstance(v,str) or not 0<len(v)<=200 for v in filters.values()): raise QueryError('QUERY_FILTER_INVALID')
    if 'start' in filters: at(filters['start'])
    if 'end' in filters: at(filters['end'])
    if 'start' in filters and 'end' in filters and at(filters['start'])>=at(filters['end']): raise QueryError('INVALID_ANALYSIS_RANGE')
    if view=='inventory' and not {'item_id','location_id'}<=set(filters): raise QueryError('EXACT_INVENTORY_DIMENSION_REQUIRED')


class ProfessionalQueries:
    def __init__(self,plans,plan_id,*,registry=None):
        self.plans=plans; self.pid=plan_id; self.registry=registry

    def _scope(self,identity,view):
        if set(identity)!=set(IDENTITY) or identity['plan_id']!=self.pid: raise QueryError('TOOL_SCOPE_MISMATCH')
        service,full=self.plans.professional_query_scope(self.pid,identity,view)
        if any(full.get(k)!=identity[k] for k in IDENTITY): raise QueryError('TOOL_SCOPE_MISMATCH')
        return service,full

    @staticmethod
    def _row(kind,record,owner,pointer,*,artifact=None,original=None,dimensions=None,links=None):
        source=dict(owner=owner,pointer=pointer,sha256=digest(record if original is None else original))
        if artifact: source['artifact']=artifact
        fact_kind={'ResearchRegistry':'RESEARCH_ARTIFACT','HoldStore':'RESEARCH_PROMISE_LIFECYCLE',
            'CanonicalInput':'CONFIRMED_SOURCE_DECLARATION',
            'planning_decision.exception_projection':'SOURCE_DEMAND_WITH_NATIVE_PLANNED_RESULT',
            'EnterprisePlans.run_analysis':'NATIVE_PLANNED_RESULT',
            'optimization.service.read':'FINITE_OPTIMIZATION_RESEARCH_RESULT',
            'EnterprisePlans.plan_editor_options':'SOURCE_AND_NATIVE_ELIGIBILITY_PROJECTION',
            'PlanStore.snapshot':'CONFIRMED_DATA_VERSION_METADATA'}.get(owner,'EXACT_DOMAIN_RECORD')
        result=dict(kind=kind,fact_kind=record.get('fact_kind',fact_kind),record=copy.deepcopy(record),source=source)
        if dimensions: result['resolved_dimensions']=copy.deepcopy(dimensions)
        if links: result['owner_links']=copy.deepcopy(links)
        if original is not None and original!=record: result['projection_status']='OWNER_RECORD_WITH_CONTEXT_PROJECTION'
        return result

    def _registry(self):
        if self.registry is None:
            from p22.intelligence_registry import ResearchRegistry
            self.registry=ResearchRegistry(self.plans.store,self.pid)
        return self.registry

    def _detail_rows(self,kind,record,owner,pointer,*,artifact=None,dimensions=None,detail_kind=None):
        """Page nested domain evidence at its original pointers, without truncation.

        A fragment explicitly names every moved field. Its children remain exact
        retrievable records, including provenance arrays belonging to one lot.
        """
        detail_kind=detail_kind or kind
        if isinstance(record,list):
            row=self._row(kind,dict(count=len(record),detail_pointer=pointer),owner,pointer,
                artifact=artifact,original=record,dimensions=dimensions)
            row['record_id']='EVID-'+digest(dict(owner=owner,artifact=artifact,pointer=pointer))[:24]
            rows=[row]
            for index,child in enumerate(record):
                added=self._detail_rows(detail_kind,child,owner,pointer+'/'+str(index),
                    artifact=artifact,dimensions=dimensions,detail_kind=detail_kind)
                for child_row in added:child_row.setdefault('owner_links',{}).setdefault('parent_record_id',row['record_id'])
                rows.extend(added)
            return rows
        value=copy.deepcopy(record) if isinstance(record,dict) else dict(value=copy.deepcopy(record))
        def needs_details(item):
            return isinstance(item,(dict,list)) and (len(encoded(item).encode())>5000
                or isinstance(item,list) and len(item)>24
                or any(needs_details(child) for child in (item.values() if isinstance(item,dict) else item)))
        moved={}
        for key,item in list(value.items()):
            if needs_details(item):
                field=key.replace('~','~0').replace('/','~1')
                path=pointer+'/'+field
                moved[key]=dict(pointer=path,sha256=digest(item),count=len(item))
                value.pop(key)
        if moved:value['detail_fields']=moved
        row=self._row(kind,value,owner,pointer,artifact=artifact,original=record,dimensions=dimensions)
        row['record_id']='EVID-'+digest(dict(owner=owner,artifact=artifact,pointer=pointer))[:24]
        rows=[row]
        for key in moved:
            item=record[key];path=moved[key]['pointer']
            children=enumerate(item) if isinstance(item,list) else sorted(item.items())
            for index,child in children:
                child_path=path+'/'+str(index).replace('~','~0').replace('/','~1')
                children_rows=self._detail_rows(detail_kind,child,owner,child_path,
                    artifact=artifact,dimensions=dimensions,detail_kind=detail_kind)
                for child_row in children_rows:child_row.setdefault('owner_links',{}).setdefault('parent_record_id',row['record_id'])
                rows.extend(children_rows)
        return rows

    def _inventory_rows(self,series,index):
        base='/inventory_analysis/series/'+str(index);owner='EnterprisePlans.run_analysis'
        dimensions={k:series[k] for k in ('item_id','location_id','uom')}
        arrays=('events','opening_lots','opening_sources','opening_source')
        header={k:v for k,v in series.items() if k not in arrays}
        header['evidence_counts']={k:len(series.get(k,[])) for k in arrays[:3]}
        rows=self._detail_rows('inventory_series',header,owner,base,dimensions=dimensions,detail_kind='inventory_source')
        rows[0]['source']['sha256']=digest(series)
        rows[0]['projection_status']='OWNER_RECORD_WITH_CONTEXT_PROJECTION'
        lots=series.get('opening_lots',[])
        def lot_dimensions(lot):
            lot_id=next((lot.get(k) for k in ('lot_id','batch_id','id','position_id') if lot.get(k)),None)
            status=next((lot.get(k) for k in ('quality_status','status','state') if lot.get(k)),None)
            return dict(dimensions,**({'lot_id':lot_id} if lot_id else {}),**({'status':status} if status else {}))
        by_id={lot_dimensions(lot).get('lot_id'):lot for lot in lots}
        for li,lot in enumerate(lots):
            rows.extend(self._detail_rows('inventory_lot',lot,owner,base+'/opening_lots/'+str(li),
                dimensions=lot_dimensions(lot),detail_kind='inventory_source'))
        for si,source in enumerate(series.get('opening_sources',[])):
            dims=lot_dimensions(lots[si]) if si<len(lots) else dimensions
            rows.extend(self._detail_rows('inventory_source',source,owner,base+'/opening_sources/'+str(si),dimensions=dims))
        opening=series.get('opening_source')
        if opening is not None:
            if isinstance(opening,dict) and isinstance(opening.get('positions'),list):
                metadata={k:v for k,v in opening.items() if k!='positions'}
                metadata['position_count']=len(opening['positions'])
                added=self._detail_rows('inventory_source',metadata,owner,base+'/opening_source',dimensions=dimensions)
                added[0]['source']['sha256']=digest(opening)
                added[0]['projection_status']='OWNER_RECORD_WITH_CONTEXT_PROJECTION'
                rows.extend(added)
                for pi,position in enumerate(opening['positions']):
                    lot=by_id.get(position.get('position_id'),position)
                    rows.extend(self._detail_rows('inventory_source',position,owner,base+'/opening_source/positions/'+str(pi),
                        dimensions=lot_dimensions(lot)))
            else:rows.extend(self._detail_rows('inventory_source',opening,owner,base+'/opening_source',dimensions=dimensions))
        for ei,event in enumerate(series.get('events',[])):
            added=self._detail_rows('inventory_event',event,owner,base+'/events/'+str(ei),
                dimensions=dimensions,detail_kind='inventory_source')
            added[0]['record']={**dimensions,**added[0]['record']}
            added[0]['projection_status']='OWNER_RECORD_WITH_CONTEXT_PROJECTION'
            rows.extend(added)
        return rows

    def _research(self,service,identity,view,filters):
        """Select the original registry/hold records before display projection."""
        registry=self._registry(); rows=[]; summary={}; sources=[]
        def emit(kind,record,artifact,pointer='',original=None):
            rows.append(self._row(kind,record,'ResearchRegistry',pointer,artifact=artifact,original=original))
        if view=='forecast':
            from p22.intelligence_service import _forecast_display,_draft_display
            versions=([registry.get('version',filters['version_id'])] if filters.get('version_id') else
                      [] if filters.get('dataset_id') and filters.get('row_id') else registry.list('version'))
            revoked={r['version_id'] for r in registry.list('revocation')}
            for version in versions:
                if filters.get('dataset_id') and version.get('dataset_id')!=filters['dataset_id']: continue
                artifact='professional_artifacts/version/'+version['id']
                value=_forecast_display(dict(version,effective_status='WITHDRAWN' if version['id'] in revoked else version['status']))
                if not filters.get('row_id'):
                    emit('forecast_version',{k:v for k,v in value.items() if k!='rows'},artifact,original=version)
                for index,row in enumerate(value.get('rows',[])):
                    if filters.get('row_id') and row.get('id')!=filters['row_id']: continue
                    emit('forecast_row',dict(row,forecast_version_id=version['id'],version_sha256=version.get('sha256')),
                         artifact,'/rows/'+str(index),original=version['rows'][index])
            draft=service.drafts.get(identity['target']) if identity['target']!='baseline' else None
            display=_draft_display(draft)
            if display and not filters.get('dataset_id'):
                adopted=display.get('adopted',{}).get('forecast_version_id')
                for index,row in enumerate(display.get('changes',{}).get('netting',{}).get('rows',[])):
                    version=row.get('version_id',adopted)
                    if filters.get('version_id') and version!=filters['version_id']: continue
                    if filters.get('row_id') and filters['row_id'] not in {row.get('id'),row.get('forecast_id')}: continue
                    rows.append(self._row('forecast_netting_row',dict(row,forecast_version_id=version),'DraftStore',
                        '/changes/netting/rows/'+str(index),artifact='draft/'+draft['id'],original=row))
            if not filters.get('version_id') and not filters.get('row_id'):
                datasets=([registry.get('dataset',filters['dataset_id'])] if filters.get('dataset_id') else registry.list('dataset'))
                for dataset in datasets:
                    artifact='professional_artifacts/dataset/'+dataset['id']; groups={}
                    for row in dataset.get('rows',[]): groups.setdefault(tuple(row.get(k) for k in ('item_id','location_id','customer_id','uom')),[]).append(row)
                    emit('forecast_dataset',{k:v for k,v in dataset.items() if k not in {'rows','backtest'}}|
                         dict(rows=len(dataset.get('rows',[])),series=len(groups)),artifact,original=dataset)
                    for group in groups.values():
                        for row in sorted(group,key=lambda r:r['bucket_start'])[-52:]:
                            index=dataset['rows'].index(row)
                            emit('forecast_history_row',{k:row.get(k) for k in ('item_id','location_id','customer_id','uom','bucket_start','bucket_end','quantity','censored')}|
                                 dict(dataset_id=dataset['id'],dataset_sha256=dataset.get('sha256')),artifact,'/rows/'+str(index),original=row)
                from p22.intelligence_service import read_research_jobs
                for job in read_research_jobs(registry):
                    if filters.get('dataset_id') and job.get('parameters',{}).get('dataset_id')!=filters['dataset_id']: continue
                    emit('forecast_job',job,'professional_artifacts/job/'+job['id'])
            data=service.drafts.baseline
            summary.update(supported_forecast_models=['Naive','SeasonalNaive','AutoETS','CrostonSBA','TSB'],
                forecast_context=dict(original_policy_status='FORECAST_POLICY_PENDING',
                    original_gross=copy.deepcopy(data['foundation_projection']['source_tables']['forecasts']),draft=display),
                registry_state_semantics='ORIGINAL_OWNER_JOB_RESTART_RECOVERY;NO_SOLVE')
            # Use the same Owner template generator as the full workbench.
            from p22.intelligence_service import netting_templates
            summary['netting_templates']=netting_templates(versions,revoked,data)
        elif view=='inventory_research':
            records=[registry.get('inventory',filters['inventory_id'])] if filters.get('inventory_id') else registry.list('inventory')
            for record in records: emit('inventory_candidate',record,'professional_artifacts/inventory/'+record['id'])
        else:
            from order_promising import HoldStore
            store=HoldStore(self.plans.store.folder(self.pid)/'intelligence'/'promising')
            records=[store.get(filters['quote_id'])] if filters.get('quote_id') else store.list()
            wanted={k:identity[k] for k in IDENTITY}
            for record in records:
                if record['identity']!=wanted and record['state'] not in {'HELD','COMMITTED_SIMULATED'}: continue
                rows.append(self._row('research_quote',record,'HoldStore','',artifact='promising/quote/'+record['id']))
            summary['reservation_scope']='EXACT_CURRENT_RUN_AND_DISCLOSED_ACTIVE_RESERVATIONS'
        return rows,summary,sources,'PRESENT'

    def _purchases(self,service,identity,filters):
        data=service.drafts.baseline
        draft=service.drafts.for_run(identity['target']) if identity['target']!='baseline' else None
        effective=draft['input'] if draft else data
        sources={r['id']:(i,r) for i,r in enumerate(data.get('supply_sources',[]))}
        purchases={r['id']:(i,r) for i,r in enumerate(data.get('existing_orders',[])) if r['type']=='PO'}
        rows=[]
        for kind,records,base in [('purchase',[r for r in data.get('existing_orders',[]) if r['type']=='PO'],'/existing_orders'),
                                   ('purchase_receipt',effective.get('purchase_receipts',[]),'/purchase_receipts'),
                                   ('source_terms',data.get('supply_sources',[]),'/supply_sources')]:
            for index,record in enumerate(records):
                po=record; links={}; source=record
                if kind=='purchase_receipt':
                    if record.get('po_id') not in purchases: raise QueryError('INVALID_EVIDENCE')
                    _,po=purchases[record['po_id']];links['purchase_id']=po['id']
                if kind in {'purchase','purchase_receipt'}:
                    if po.get('supply_source_id') not in sources: raise QueryError('INVALID_EVIDENCE')
                    si,source=sources[po['supply_source_id']]
                    links.update(supply_source_id=source['id'],source_pointer='/supply_sources/'+str(si))
                dimensions={k:source[k] for k in ('item_id','location_id','supplier_id') if k in source}
                if not self._matches(dict(kind=kind,record=record,resolved_dimensions=dimensions,owner_links=links),'purchases',filters):continue
                pointer=base+'/'+str(data['existing_orders'].index(record) if kind=='purchase' else index)
                rows.append(self._row(kind,record,'CanonicalInput',pointer,artifact='canonical_input.json',dimensions=dimensions,links=links))
        return rows,dict(timezone=data['site']['timezone'],horizon=data['horizon'],
            quantity_semantics='SOURCE_PURCHASE_AND_INDIVIDUAL_RECEIPTS;QC_NOT_SUMMED_INTO_RELEASED_SUPPLY'),[], 'PRESENT'

    def _demand(self,service,identity,filters):
        from service import artifact_root
        from planning_decision import exception_projection,read_table
        from planning_capabilities import profile_id
        from p22.query_reads import immutable_read
        folder=artifact_root()/identity['run_id']
        record=self.plans.store.get(self.pid); full=dict(identity,snapshot_sha256=record['snapshot_sha256'])
        artifacts=service.certified_artifacts(identity['run_id'],identity['target'])
        data=artifacts['data'];result=artifacts['result'];files=artifacts['manifest']['files']
        sources=[dict(artifact=name,sha256=files[name]) for name in ('canonical_input.json','PlanResult.json','enterprise-context.json')]
        projection=immutable_read(service,folder,full,'demand-worklist-v1',lambda manifest:
            exception_projection(data,result,dict(full,profile=profile_id(data),result_hash=result['result_hash']),
                read_table(folder,'constraint',identity['run_id']),read_table(folder,'problem',identity['run_id'])))
        demands={r['id']:r for r in data['demand_lines']}; rows=[]
        for index,row in enumerate(projection['demands']):
            customer=demands[row['demand_id']].get('customer_id')
            if not self._matches(dict(kind='demand',record=row,resolved_dimensions=dict(customer_id=customer)),'demand_risks',filters):continue
            rows.append(self._row('demand',row,'planning_decision.exception_projection','/demands/'+str(index),
                dimensions=dict(customer_id=customer)))
        summary={k:projection[k] for k in ('timezone','planning_at','horizon_end','endpoint','limits','validation','evidence')}
        return rows,summary,sources,'PRESENT'

    def _resource_declarations(self,service,identity,analysis,dimensions):
        """Flatten the bound continuity source, never infer outages from gaps."""
        expected=(service.drafts.baseline if identity['target']=='baseline'
            else service.drafts.for_run(identity['target'])['input'])
        locations=expected.get('foundation_projection',{}).get('source_tables',{}).get('locations',[])
        if not any('continuity_bundle' in row for row in locations):return []
        from service import artifact_root
        file_hash=next((r['sha256'] for r in analysis.get('evidence_files',[])
            if r.get('artifact')=='canonical_input.json'),None)
        if not file_hash:raise QueryError('EVIDENCE_NOT_COLLECTED')
        artifacts=service.certified_artifacts(identity['run_id'],identity['target']);data=artifacts['data']
        if (file_hash!=artifacts['manifest']['files']['canonical_input.json'] or data!=expected or artifacts['result']['input_hash']!=analysis['identity']['input_hash']
            or data.get('foundation_projection',{}).get('source_version_id')!=identity['data_version_id']):
            raise QueryError('RUN_SOURCE_MISMATCH')
        # Original component certification already performed the continuity
        # audit. This projection reads its same manifest-bound source members.
        rows=[];window=analysis['window']
        locations=data['foundation_projection']['source_tables']['locations']
        for li,location in enumerate(locations):
            document=location.get('continuity_bundle',{}).get('documents',{}).get('MES',{})
            for index,original in enumerate(document.get('tables',{}).get('resource_unavailability',[])):
                rid=original['resource_id']
                if rid not in dimensions:continue
                if at(original['end'])<=at(window['start_at']) or at(original['start'])>=at(window['end_at']):continue
                source_identity={k:copy.deepcopy(document[k]) for k in
                    ('enterprise_id','system','source_id','branch_id','source_revision','source_cursor','as_of') if k in document}
                source_identity.update({k:copy.deepcopy(original[k]) for k in
                    ('source_record_revision','business_occurred_at','source_known_at','aps_read_at') if k in original})
                source_identity['record_id']=original['id']
                refs=document.get('event_refs',{}).get('resource_unavailability',{}).get(original['id'])
                if refs is not None:source_identity['event_ids']=copy.deepcopy(refs)
                record=dict(id=original['id'],resource_id=rid,start_at=original['start'],end_at=original['end'],
                    reason=original.get('reason'),status='SOURCE_DECLARED_UNAVAILABLE',source_identity=source_identity)
                if 'announced_at' in original:record['announced_at']=original['announced_at']
                pointer=('/foundation_projection/source_tables/locations/'+str(li)+
                    '/continuity_bundle/documents/MES/tables/resource_unavailability/'+str(index))
                row=self._row('source_resource_unavailability',record,'CanonicalInput',pointer,
                    artifact='canonical_input.json',original=original,dimensions=dimensions[rid])
                row['source']['artifact_sha256']=file_hash
                rows.append(row)
        return rows

    def _analysis(self,service,identity,view,filters):
        from service import artifact_root
        from planning_analysis import read_analysis,select_analysis
        if service.compute().run(identity['run_id']).get('state')!='SUCCEEDED':raise QueryError('ANALYSIS_RUN_NOT_SUCCEEDED')
        selected={k:v for k,v in filters.items() if k in {'resource_id','item_id','location_id','start','end'}}
        record=self.plans.store.get(self.pid)
        bound=dict(identity,snapshot_sha256=record['snapshot_sha256'],scenario_id=self.pid if identity['target']=='baseline' else identity['target'])
        raw=select_analysis(read_analysis(artifact_root()/identity['run_id'],bound,
            components=('resource_analysis' if view=='resources' else 'inventory_analysis',),owner=service,reuse=True),**selected)
        component=raw['resource_analysis' if view=='resources' else 'inventory_analysis']; rows=[]
        if view=='resources':
            resource_dimensions={r['id']:{k:r[k] for k in ('role','pool_id') if k in r} for r in component.get('resources',[])}
            for key,kind in [('resources','resource'),('intervals','resource_interval')]:
                for index,row in enumerate(component.get(key,[])):
                    rows.append(self._row(kind,row,'EnterprisePlans.run_analysis','/resource_analysis/'+key+'/'+str(index),
                        dimensions=resource_dimensions.get(row.get('resource_id',row.get('id')))))
            rows.extend(self._resource_declarations(service,identity,raw,resource_dimensions))
        else:
            for index,series in enumerate(component.get('series',[])):
                rows.extend(self._inventory_rows(series,index))
        summary={k:raw[k] for k in ('timezone','window','planning_at','horizon_end') if k in raw}
        summary['component_validation']=component.get('validation',{})
        if view=='inventory': summary['inventory_risk']=component.get('risk',{})
        return rows,summary,raw.get('evidence_files',[]),component.get('status','NOT_COLLECTED')

    def _manufacturing(self,service,identity,filters):
        """Read existing-job execution and same-run plans from their exact Owners."""
        from p22.continuity_read_model import continuity_wip_context
        artifacts=service.certified_artifacts(identity['run_id'],identity['target'])
        data,result=artifacts['data'],artifacts['result']
        view=service.selected_view(identity['target'])
        if view.get('run',{}).get('solve_run_id')!=identity['run_id'] or result.get('solve_run_id')!=identity['run_id']:
            raise QueryError('RUN_SOURCE_MISMATCH')
        continuity=continuity_wip_context(view.get('interpretation'),identity['run_id'])
        if continuity and continuity.get('status')=='INVALID_EVIDENCE':raise QueryError(continuity.get('code','CONTINUITY_RUN_MISMATCH'))
        source=data.get('foundation_projection',{}).get('source_tables',{})
        operations={o['id']:o for o in source.get('operations',[])}
        demands={d['id']:d.get('order_ref') for d in source.get('demand_lines',[])}
        selected_order=demands.get(filters.get('order_line_id'),filters.get('order_line_id'))
        recipes={};rows=[];actual_sources={}
        for ri,recipe in enumerate(source.get('recipes',[])):
            job=recipe.get('continuity_job_id')
            if not job or filters.get('job_id') and filters['job_id']!=job:continue
            order=recipe.get('actual_order_id')
            if selected_order and selected_order!=order:continue
            dimensions=dict(job_id=job,order_id=order,item_id=recipe.get('output_item_id'),location_id=recipe.get('location_id'))
            source_dimensions=dict(dimensions,item_id=recipe.get('original_product_id',recipe.get('output_item_id')))
            recipes[recipe['id']]=dimensions
            pointer='/foundation_projection/source_tables/recipes/'+str(ri)
            arrays={'actual_operation_records':'actual_operation','issued_materials':'material_issue','source_refs':'manufacturing_source'}
            if 'actual_operation_records' not in recipe:arrays['completed_prefix']='actual_operation'
            fields=('id','continuity_job_id','actual_order_id','original_job_quantity','completed_quantities',
                'finished_received_quantity','shipped_quantity','remaining_quantity','remaining_from_operation_id','remaining_operation_ids')
            record={k:copy.deepcopy(recipe[k]) for k in fields if k in recipe}
            record.update(job_id=job,order_id=order)
            added=self._detail_rows('manufacturing_job',record,'CanonicalInput',pointer,artifact='canonical_input.json',dimensions=dimensions,detail_kind='manufacturing_source')
            added[0]['source']['sha256']=digest(recipe)
            added[0]['projection_status']='OWNER_RECORD_WITH_CONTEXT_PROJECTION'
            rows.extend(added)
            for key,kind in arrays.items():
                for index,actual in enumerate(recipe.get(key,[])):
                    # Stage recipes repeat the job's same execution evidence.
                    # Emit each exact source record once across recipes, while
                    # retaining distinct revisions and every native stage below.
                    source_key=(job,kind,digest(actual))
                    first_recipe=actual_sources.setdefault(source_key,ri)
                    if first_recipe!=ri:continue
                    added=self._detail_rows(kind,actual,'CanonicalInput',pointer+'/'+key+'/'+str(index),
                        artifact='canonical_input.json',dimensions=source_dimensions,detail_kind='manufacturing_source')
                    if kind in {'actual_operation','material_issue'}:
                        for row in added:row['fact_kind']='CONFIRMED_EXECUTION_SOURCE'
                    rows.extend(added)
        parents={m['id']:recipes[m['recipe_id']] for key in ('manufacture_suggestions','existing_manufacturing_orders')
            for m in result.get(key,[]) if m.get('recipe_id') in recipes}
        native={}
        for oi,operation in enumerate(result.get('operations',[])):
            dims=parents.get(operation.get('manufacture_id'))
            if dims is None:continue
            declared=operations.get(operation.get('operation_id'),{})
            observed=dict(operation,**({'actual_operation_id':declared['actual_operation_id']} if declared.get('actual_operation_id') else {}))
            native[operation['id']]=dict(dims,**({'operation_id':declared['actual_operation_id']} if declared.get('actual_operation_id') else {}))
            added=self._detail_rows('remaining_operation',observed,'PlanResult','/operations/'+str(oi),
                artifact='PlanResult.json',dimensions=native[operation['id']],detail_kind='manufacturing_source')
            added[0]['source']['sha256']=digest(operation)
            for row in added:row['fact_kind']='NATIVE_PLANNED_RESULT'
            rows.extend(added)
        for li,load in enumerate(result.get('resource_loads',[])):
            if load.get('operation_id') not in native:continue
            added=self._detail_rows('remaining_resource',load,'PlanResult','/resource_loads/'+str(li),
                artifact='PlanResult.json',dimensions=native[load['operation_id']],detail_kind='manufacturing_source')
            for row in added:row['fact_kind']='NATIVE_PLANNED_RESULT'
            rows.extend(added)
        audit_status='NOT_COLLECTED' if continuity is None else continuity.get('audit_status','NOT_COLLECTED')
        jobs={d['job_id']:d for d in recipes.values()}
        audit=(view.get('interpretation',{}).get('foundation_validation',{}).get('checks') or {}).get('continuity') or {}
        for wi,wip in enumerate(audit.get('wip',[])):
            if wip.get('job_id') not in jobs:continue
            rows.extend(self._detail_rows('wip_audit',wip,'p22.continuity_audit.audit',
                '/interpretation/foundation_validation/checks/continuity/wip/'+str(wi),dimensions=jobs[wip['job_id']],detail_kind='manufacturing_source'))
        summary=dict(continuity_audit_status=audit_status,continuation_certification=(continuity or {}).get('continuation_certification','NOT_CERTIFIED'),
            actual_and_planned_quantities='SEPARATE_SOURCE_EXECUTION_AND_SAME_RUN_NATIVE_RESULT',
            source_as_of=(continuity or {}).get('source_as_of'))
        files=artifacts['manifest']['files']
        sources=[dict(artifact=name,sha256=files[name]) for name in ('canonical_input.json','PlanResult.json','enterprise-context.json')]
        status='INVALID_EVIDENCE' if audit_status=='FAIL' else 'PRESENT' if audit_status=='PASS' else 'NOT_COLLECTED'
        return rows,summary,sources,status

    def _schedule(self,service,identity,filters):
        from optimization import service as optimizer
        # The original editor scope owns capabilities and source version checks.
        _,full=self.plans._plan_editor_scope(self.pid,**{k:identity[k] for k in IDENTITY if k!='plan_id'})
        rows=[]; summary={}
        if filters.get('search_id'):
            job=optimizer.read(service,full,filters['search_id']); edit=job.get('edit') or {}
            rows.append(self._row('optimization',{k:v for k,v in job.items() if k!='edit'}|
                dict(edit={k:v for k,v in edit.items() if k!='assignments'}),'optimization.service.read',''))
            for index,row in enumerate(edit.get('assignments',[])):
                rows.append(self._row('schedule_assignment',row,'optimization.service.read','/edit/assignments/'+str(index)))
            summary['optimization']={k:job[k] for k in ('search_id','state','algorithm','candidate_status','fingerprint') if k in job}
            summary['optimization']['assignment_count']=len(edit.get('assignments',[]))
        else:
            # Existing editor options contain authoritative eligibility/preview
            # contracts. They are retained rather than duplicated in this API.
            raw=self.plans.plan_editor_options(self.pid,**{k:identity[k] for k in IDENTITY if k!='plan_id'})
            for key in ('orders','proposal_tasks','resources','calendars','resource_skills'):
                for index,row in enumerate(raw.get(key,[])): rows.append(self._row(key,row,'EnterprisePlans.plan_editor_options','/'+key+'/'+str(index)))
            if raw.get('contract'):rows.append(self._row('schedule_contract',raw['contract'],'EnterprisePlans.plan_editor_options','/contract'))
            summary['latest_optimization']=optimizer.latest(service,full)
        return rows,summary,[],'PRESENT'

    def _schedule_result(self,service,identity):
        """Project original native adoption/comparison without changing review gates."""
        raw=service.selected_view(identity['target'])
        if raw.get('run',{}).get('solve_run_id')!=identity['run_id']:
            raise QueryError('RUN_SOURCE_MISMATCH')
        audit=raw.get('interpretation',{}).get('plan_editor_validation')
        if raw.get('run',{}).get('state')!='SUCCEEDED' or not isinstance(audit,dict):
            return [],dict(run=raw.get('run',{}),native_validation=None),[],'NOT_COLLECTED'
        rows=[self._row('native_schedule_assignment',row,'PlanningService.selected_view',
                '/interpretation/plan_editor_validation/assignments/'+str(index))
            for index,row in enumerate(audit.get('assignments',[]))]
        summary=dict(run=raw['run'],native_validation={k:v for k,v in audit.items() if k not in {'assignments','setup_sequence'}},
            native_comparison=raw.get('diff',{}).get('plan_editor'),
            assignment_count=len(audit.get('assignments',[])),
            certification_scope='ORIGINAL_NATIVE_AUDIT_AND_COMPARISON;NOT_OPTIMIZER_PREDICTION')
        return rows,summary,[],('INVALID_EVIDENCE' if audit.get('status')=='FAIL' else 'PRESENT' if audit.get('status')=='PASS' else 'NOT_COLLECTED')

    @staticmethod
    def _matches(row,view,filters):
        record=row['record']; nested=record.get('request',{}) if view=='promising' else {}
        if view=='schedule_result':nested=record.get('after',{})
        if view=='inventory_research':nested=(record.get('request') or {}).get('dimension',{})
        dimensions=row.get('resolved_dimensions',{});links=row.get('owner_links',{})
        aliases={'order_line_id':('demand_id','id'),'resource_id':('resource_id','id'),'operation_id':('operation_id','actual_operation_id','operation'),
            'reference':('reference','native_reference','source_native_reference','purchase_reference','po_id','receipt_reference','id','purchase_id'),
            'inventory_id':('id',),'quote_id':('id',),'source_id':('source_id','supply_source_id','id'),
            'status':('status','state','quality_status','effective_status')}
        ignored={'version_id','row_id','dataset_id','search_id','start','end'}
        for key,value in filters.items():
            if view in {'inventory','manufacturing'} and key in {'kind','record_id'}:
                if row.get(key)!=value:return False
                continue
            if view=='manufacturing' and key=='order_line_id':continue  # resolved through the explicit source order reference above
            if key=='version_id' and view=='versions':
                if record.get('id')!=value:return False
                continue
            if key in ignored:continue
            if key=='source_id' and view=='promising':
                slices=record.get('source_slices',[])+(record.get('calculation') or {}).get('source_slices',[])
                if not any(value==s.get('event_id') or value in s.get('source_refs',[]) for s in slices):return False
                continue
            keys=aliases.get(key,(key,))
            if (not any(record.get(k,nested.get(k,dimensions.get(k,links.get(k))))==value for k in keys)
                and not (view in {'inventory','manufacturing'} and any(dimensions.get(k)==value for k in keys))):return False
        if (filters.get('start') or filters.get('end')) and view!='inventory' and row['kind']!='resource':
            keys=('start','occurred_at','at') if view=='manufacturing' else ()
            start=next((record.get(k,nested.get(k)) for k in ('due_at','bucket_start','physical_receipt_at','expected_receipt_at','receipt_at','start_at','effective_from',*keys) if record.get(k,nested.get(k))),None)
            end=record.get('bucket_end',record.get('end_at',record.get('actual_end',record.get('end',start)) if view=='manufacturing' else start))
            if not start:return False
            if filters.get('start') and (at(end)<at(filters['start']) or end!=start and at(end)==at(filters['start'])):return False
            if filters.get('end') and at(start)>=at(filters['end']):return False
        return True

    def read(self,identity,view,filters=None,*,page_size=20,cursor=None):
        started=time.perf_counter(); filters={} if filters is None else filters
        validate_query(view,filters,page_size)
        if cursor is not None and (not isinstance(cursor,str) or not 1<=len(cursor)<=3000):raise QueryError('CURSOR_INVALID')
        service,full=self._scope(identity,view); scoped=time.perf_counter()
        if view in {'forecast','inventory_research','promising'}:rows,summary,sources,status=self._research(service,identity,view,filters)
        elif view=='purchases':rows,summary,sources,status=self._purchases(service,identity,filters)
        elif view=='demand_risks':rows,summary,sources,status=self._demand(service,identity,filters)
        elif view in {'inventory','resources'}:rows,summary,sources,status=self._analysis(service,identity,view,filters)
        elif view=='manufacturing':rows,summary,sources,status=self._manufacturing(service,identity,filters)
        elif view=='schedule':rows,summary,sources,status=self._schedule(service,identity,filters)
        elif view=='schedule_result':rows,summary,sources,status=self._schedule_result(service,identity)
        else:
            version=service.query_version_metadata
            rows=[self._row('data_version',{k:v for k,v in version.items() if k!='source_sync_receipt'},'PlanStore.read_source','/version_metadata')]
            summary=dict(snapshot_sha256=full['snapshot_sha256']);sources=[];status='PRESENT'
        built=time.perf_counter(); total=len(rows);rows=[r for r in rows if self._matches(r,view,filters)]
        scope=digest(dict(identity=identity,view=view,filters=filters,page_size=page_size))
        fingerprint=digest(dict(rows=rows,summary=summary,sources=sources,status=status))
        offset=0
        if cursor:
            try:
                value=json.loads(base64.b64decode(cursor,altchars=b'-_',validate=True))
                if set(value)!={'scope','fingerprint','offset'} or type(value['offset']) is not int or value['offset']<0:raise ValueError()
            except (ValueError,TypeError,KeyError): raise QueryError('CURSOR_INVALID') from None
            if value['scope']!=scope:raise QueryError('CURSOR_SCOPE_MISMATCH')
            if value['fingerprint']!=fingerprint:raise QueryError('CURSOR_EVIDENCE_CHANGED')
            offset=value['offset']
        if offset>len(rows):raise QueryError('CURSOR_INVALID')
        page=[]
        for row in rows[offset:offset+page_size]:
            if len(encoded(page+[row]).encode())>38000:
                if not page:raise QueryError('EVIDENCE_RECORD_TOO_LARGE')
                break
            page.append(row)
        end=offset+len(page)
        next_cursor=base64.urlsafe_b64encode(encoded(dict(scope=scope,fingerprint=fingerprint,offset=end)).encode()).decode() if end<len(rows) else None
        finished=time.perf_counter()
        present=status if status in {'NOT_COLLECTED','UNSUPPORTED','UNREADABLE','INVALID_EVIDENCE'} else 'PRESENT' if rows else 'EMPTY_CHECKED'
        return dict(schema_version='professional-query/1',identity=full,result_status=present,rows=page,summary=summary,
            source_artifacts=sources,snapshot_fingerprint=fingerprint,
            checked_scope=dict(identity=identity,view=view,filters=filters,query_mode='EXACT_DOMAIN_QUERY',
                fact_semantics='SOURCE_DECLARATIONS_OR_NATIVE_PLANNED_RESULT_AS_LABELLED;NO_EXECUTION',
                relation_scope='EXACT_OWNER_REFERENCES_ONLY;NO_INFERRED_ALLOCATION_OR_UNIQUE_CAUSE'),
            page=dict(returned=len(page),matched=len(rows),total=total,offset=offset,next_cursor=next_cursor,truncated=next_cursor is not None),
            timings=dict(scope_seconds=scoped-started,owner_query_seconds=built-scoped,projection_seconds=finished-built,
                total_seconds=finished-started,cache='IMMUTABLE_OWNER_CONTENT_REUSE;DYNAMIC_SCOPE_FRESH'),simulation_only=True,execution_authorized=False)


def _source_sync_context(plans,record,data):
    """Project the bound version's receipt, never a connector's current head.

    Source preview/confirmation has no scope in the existing Agent tool schema.
    Reading connector history deliberately avoids get_sync_status: that method
    can recover and publish a saved commit, which discovery must not trigger.
    """
    result=dict(status='NOT_DECLARED',data_version_id=record['data_version_id'],
        configured=False,agent_read_only=True,
        preview=dict(status='SYNC_OPERATOR_SCOPE_REQUIRED'),last_commit=None)
    imports=getattr(plans,'imports',None)
    if imports is None:return result
    metadata=hasattr(imports,'version_metadata')
    try:
        if metadata:version=imports.version_metadata(record['data_version_id'])
        elif record['id'] in getattr(plans,'services',{}) and hasattr(plans.services[record['id']],'query_version_metadata'):
            version=plans.services[record['id']].query_version_metadata;metadata=True
        else:version=imports.version(record['data_version_id'])
    except (OSError,ValueError,TypeError,KeyError):
        return dict(result,status='UNAVAILABLE',code='SOURCE_SYNC_VERSION_UNAVAILABLE')
    receipt=version.get('source_sync_receipt')
    if not receipt:return result

    def pick(value,fields):
        return {k:copy.deepcopy(value[k]) for k in fields if k in value}
    def readiness(value):
        return {k:v for k,v in value.items() if k in
            {'data_accepted','confirmed','planning_ready','facts_ready','native_preview_ready'} and type(v) is bool}
    try:
        enterprise=version.get('enterprise_id') if metadata else version.get('canonical',{}).get('enterprise_id')
        foundation=data.get('foundation_projection',{})
        policy_enterprise=foundation.get('policy',{}).get('enterprise_id')
        source_version=foundation.get('source_version_id')
        if (version.get('id')!=record['data_version_id'] or not enterprise
            or receipt.get('enterprise_id')!=enterprise
            or (policy_enterprise is not None and policy_enterprise!=enterprise)
            or (source_version is not None and source_version!=record['data_version_id'])
            or ('data_version_sha256' in record and (version.get('version_sha256') if metadata else digest(version))!=record['data_version_sha256'])
            or version.get('business_data',{}).get('source_sync_receipt',receipt)!=receipt):
            raise ValueError()
        cutoff=at(receipt['as_of'])
        def batch(value):
            if value.get('as_of') and at(value['as_of'])>cutoff:raise ValueError()
            row=pick(value,('system','batch_id','source_sha256','cursor','previous_batch_id','as_of','mode'))
            if 'chain' in value:row['chain']=[batch(r) for r in value['chain']]
            return row
        batches=[batch(row) for row in receipt['batches']]
        source=pick(receipt,('enterprise_id','as_of','trigger','source_scope'))
        source['batches']=batches
        continuity=version.get('business_data',{}).get('continuity',{})
        if (continuity.get('enterprise_id',enterprise)!=enterprise
            or (continuity.get('as_of') and at(continuity['as_of'])>cutoff)):
            raise ValueError()
        result.update(status='BOUND_VERSION',enterprise_id=enterprise,as_of=receipt['as_of'],
            source_sync_receipt=source,
            continuity=pick(continuity,('schema_version','enterprise_id','as_of','business_change',
                'business_change_scope','planning_scope_change','source_change','batch_originals')),
            systems=[dict(id=row['system'],name=row['system'],cursor=row.get('cursor'),
                as_of=row.get('as_of',receipt['as_of'])) for row in batches],
            readiness=dict(planning_ready=version.get('planning_ready') is True))
    except (ValueError,KeyError,TypeError,AttributeError):
        return dict(status='INVALID_EVIDENCE',data_version_id=record['data_version_id'],
            code='SOURCE_SYNC_SCOPE_MISMATCH',agent_read_only=True,
            preview=dict(status='SYNC_OPERATOR_SCOPE_REQUIRED'))

    try:
        from p21.connector_service import configured_service,configured_services
        scope=receipt.get('source_scope',{})
        connector=(configured_services(imports).get(scope.get('connector_id')) if scope else configured_service(imports))
        if connector is None:return dict(result,connection_status='NOT_CONFIGURED')
        if connector.adapter.enterprise_id!=enterprise:
            return dict(result,connection_status='SCOPE_MISMATCH')
        # An enterprise match alone is insufficient: this plan's exact version
        # must have its own committed receipt. Never expose other plans/history.
        bound=[r for r in connector._records() if r.get('enterprise_id')==enterprise
            and r.get('version_id')==record['data_version_id'] and r.get('status')=='COMMITTED'
            and r.get('as_of')==receipt['as_of'] and r.get('sync_receipt',receipt)==receipt]
        if not bound:return dict(result,connection_status='NO_BOUND_COMMIT')
        commit=bound[-1]
        description=connector.adapter.describe_source()
        result.update(configured=True,connection_status='BOUND_COMMIT',
            connector=pick(description,('id','name','mapping_profile','source_schema','transport','cursor_strategy')),
            last_commit=pick(commit,('id','status','version_id','enterprise_id','checkpoint','as_of','committed_at','planning_ready')))
        result['readiness'].update(readiness(commit.get('readiness',{})))
        names={s['id']:s.get('name',s['id']) for s in description.get('systems',[])}
        for system in result['systems']:system['name']=names.get(system['id'],system['id'])
    except (ImportError,OSError,ValueError,KeyError,TypeError,AttributeError):
        result['connection_status']='UNAVAILABLE'
    return result


def discover(plans,plan_id=None,*,allowed_plan_ids=None):
    if allowed_plan_ids is not None:
        if not isinstance(allowed_plan_ids,(list,tuple,set)) or any(not isinstance(pid,str) for pid in allowed_plan_ids):
            raise QueryError('AGENT_PLAN_GRANT_INVALID')
        allowed=set(allowed_plan_ids)
        if plan_id is not None and plan_id not in allowed:raise QueryError('AGENT_PLAN_SCOPE_MISMATCH')
    else:allowed=None
    records=[plans.store.get(plan_id)] if plan_id else plans.store.list()
    # Filter the metadata catalog before any service/snapshot/run lookup.
    if allowed is not None:records=[r for r in records if r['id'] in allowed]
    contexts=[]
    for record in records:
        current=plans.get(record['id']); run=current.get('run',{});rid=run.get('solve_run_id')
        from planning_capabilities import profile_id,supports
        from plan_editor import available
        data=plans.service(record['id']).drafts.baseline;profile=profile_id(data)
        research=data.get('foundation_projection',{}).get('intelligence_scope',{}).get('revision')=='advanced-planning-research-v1'
        contexts.append(dict(plan_id=record['id'],data_version_id=record['data_version_id'],
            baseline_run_id=rid,run_id=rid,target='baseline',run_state=run.get('state','NOT_SUBMITTED'),
            snapshot_sha256=record['snapshot_sha256'],capabilities=dict(professional_queries='professional-query/1',profile=profile,
                native_result_queries=run.get('state')=='SUCCEEDED',resources=supports(profile,'analyses','resources'),
                inventory=supports(profile,'analyses','inventory'),forecast_research=research,inventory_research=research,
                order_promising=research,plan_editor=available(data)),
            source_refs=[dict(owner='PlanStore',record_id=record['id'])],
            source_sync=_source_sync_context(plans,record,data)))
    return dict(schema_version='professional-contexts/1',contexts=contexts,
        checked_scope=dict(plan_id=plan_id,scope='AUTHENTICATED_TASK_LOCAL_PLAN_GRANT' if allowed is not None else 'THIS_AUTHORIZED_LOCAL_INSTANCE_ONLY',
            **(dict(allowed_plan_ids=sorted(allowed)) if allowed is not None else {}),metadata_only=True),
        execution_authorized=False)


def register(app,plans,guarded):
    from fastapi import Depends,HTTPException,Request
    from p21.api import local_import_client
    @app.get('/v1/enterprise-plans/{pid}/professional-overview',dependencies=[Depends(local_import_client)])
    def overview(request:Request,pid:str,draft_id:str|None=None):
        if set(request.query_params)-{'draft_id'} or any(len(request.query_params.getlist(k))!=1 for k in request.query_params):
            raise HTTPException(422,detail=dict(code='UNKNOWN_QUERY_FIELD'))
        if draft_id is not None and (not isinstance(draft_id,str) or not __import__('re').fullmatch(r'DRAFT-[a-f0-9]{16}',draft_id)):
            raise HTTPException(422,detail=dict(code='DRAFT_NOT_FOUND'))
        value=guarded(lambda:plans.professional_overview(pid,draft_id=draft_id))
        principal=getattr(request.state,'agent_principal',None)
        if principal is not None:value['channel_grant']=dict(plan_id=pid,permissions=sorted(principal['permissions']))
        return value
    @app.get('/v1/professional-contexts',dependencies=[Depends(local_import_client)])
    def contexts(request:Request,plan_id:str|None=None):
        if set(request.query_params)-{'plan_id'} or any(len(request.query_params.getlist(k))!=1 for k in request.query_params):
            raise HTTPException(422,detail=dict(code='UNKNOWN_QUERY_FIELD'))
        principal=getattr(request.state,'agent_principal',None)
        return guarded(lambda:discover(plans,plan_id,allowed_plan_ids=principal['plan_ids'] if principal is not None else None))
    @app.get('/v1/enterprise-plans/{pid}/professional-query',dependencies=[Depends(local_import_client)])
    def query(request:Request,pid:str,data_version_id:str,baseline_run_id:str,run_id:str,target:str,view:str,
              filters:str='{}',page_size:int=20,cursor:str|None=None):
        allowed={'data_version_id','baseline_run_id','run_id','target','view','filters','page_size','cursor'}
        if set(request.query_params)-allowed or any(len(request.query_params.getlist(k))!=1 for k in request.query_params):
            raise HTTPException(422,detail=dict(code='UNKNOWN_QUERY_FIELD'))
        try: selected=json.loads(filters)
        except (ValueError,TypeError):raise HTTPException(422,detail=dict(code='QUERY_FILTER_INVALID')) from None
        identity=dict(plan_id=pid,data_version_id=data_version_id,baseline_run_id=baseline_run_id,run_id=run_id,target=target)
        return guarded(lambda:plans.professional_query(pid,identity,view,selected,page_size=page_size,cursor=cursor))
