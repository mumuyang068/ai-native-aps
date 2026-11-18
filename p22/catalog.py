"""Small PlanStore catalog projections; no planning, snapshot, or result reads."""
import copy
import json
import re
import sqlite3

from agent import AgentError
from planning_adapter import InputError


def catalog_context(snapshot):
    """Project only data already authenticated/in memory during Plan creation."""
    from plan_context import quantities
    version=snapshot['data_version'];canonical=version.get('canonical',{})
    data=snapshot['plan_input'];receipt=version.get('source_sync_receipt') or {}
    readiness=snapshot.get('planning_readiness',{})
    # The original supported-order/priority Owner treats missing kind as Firm.
    # Explicit UNKNOWN/GROSS values never acquire that default.
    lines=data.get('demand_lines',[])
    firm=[r for r in lines if r.get('kind','FIRM_ORDER')=='FIRM_ORDER']
    other_kinds=sorted({r['kind'] for r in lines if r.get('kind','FIRM_ORDER') not in {'FIRM_ORDER','NET_FORECAST'}})
    as_of=receipt.get('as_of') or readiness.get('source_known_at')
    return dict(status='PRESENT',enterprise_id=canonical.get('enterprise_id'),enterprise_name=None,
        source_name=version.get('filename'),source_as_of=as_of,
        source_as_of_basis='SOURCE_SYNC_RECEIPT' if receipt.get('as_of') else 'PROJECTION_SOURCE_KNOWN_AT' if as_of else None,
        planning_at=data.get('planning_at'),
        demand=dict(line_count=len(firm),requested=quantities(firm),basis='CONFIRMED_PLAN_INPUT_FIRM_ORDER',
            other_demands=[dict(kind=kind,line_count=sum(r.get('kind')==kind for r in lines),
                requested=quantities([r for r in lines if r.get('kind')==kind])) for kind in other_kinds]),
        basis='SEALED_PLAN_READ_SOURCE')


def _empty_context(status='NOT_COLLECTED'):
    return dict(status=status,enterprise_id=None,enterprise_name=None,source_name=None,source_as_of=None,
        source_as_of_basis=None,planning_at=None,demand=dict(line_count=None,requested=None,basis='NOT_COLLECTED'),
        basis='SEALED_PLAN_READ_SOURCE')


def _context(store,record):
    try:source=store.read_metadata(record['id'],record=record)
    except InputError:return _empty_context('INVALID_EVIDENCE')
    if source is None:return _empty_context()
    if source.get('catalog'):return copy.deepcopy(source['catalog'])
    # Old sealed headers can supply identity/source, but do not contain quantities.
    # Missing quantities stay unknown; no snapshot or Canonical bootstrap occurs.
    metadata=source.get('version_metadata',{});context=source.get('context',{})
    value=_empty_context('PARTIAL');receipt=metadata.get('source_sync_receipt') or {}
    value.update(enterprise_id=metadata.get('enterprise_id'),source_name=metadata.get('filename'),
        source_as_of=receipt.get('as_of'),source_as_of_basis='SOURCE_SYNC_RECEIPT' if receipt.get('as_of') else None)
    links=context.get('demand_customer_links')
    if isinstance(links,dict):value['demand'].update(linked_line_count=len(links),basis='SEALED_CUSTOMER_LINK_COUNT_NOT_CLASSIFIED')
    return value


def catalog(plans,*,offset=0,limit=20,compute=None):
    from drafts import DraftStore
    from service import ComputeClient
    result=plans.store.page(offset=offset,limit=limit);items=[];requested={}
    metadata={};imports=getattr(plans,'imports',None)
    if result['items'] and hasattr(imports,'version_directory_metadata'):
        try:metadata=imports.version_directory_metadata([r['data_version_id'] for r in result['items']])
        except (OSError,sqlite3.Error,ValueError):metadata={}
    for record in result['items']:
        pid=record['id'];folder=plans.store.folder(pid)/'workspace'
        business=_context(plans.store,record)
        version_meta=metadata.get(record['data_version_id'],{})
        if not business['source_as_of'] and version_meta.get('business_as_of'):
            business.update(source_as_of=version_meta['business_as_of'],source_as_of_basis=version_meta['basis'])
        run=dict(status='NOT_SUBMITTED',state=None,solve_run_id=None,updated_at=None,
            native_result_verified=False,saved_submission_state=None)
        path=folder/'baseline-run.json'
        if path.is_file():
            try:
                if path.stat().st_size>64*1024:raise ValueError()
                saved=json.loads(path.read_text('utf-8'));rid=saved['solve_run_id']
                if not isinstance(rid,str) or not re.fullmatch(r'API-[a-f0-9]{16}',rid):raise ValueError()
                run.update(status='UNAVAILABLE',solve_run_id=rid,saved_submission_state=saved.get('state'))
                requested[rid]=pid
            except (OSError,ValueError,KeyError,TypeError):run.update(status='INVALID_EVIDENCE')
        try:candidates=DraftStore.directory_summary(folder)
        except (OSError,sqlite3.Error,ValueError):
            candidates=dict(status='UNAVAILABLE',count=None,has_drafts=None,pending_decision_count=None,
                needs_review=None,review_count=None,review_status='UNAVAILABLE')
        business.update(run=run,candidates=candidates)
        items.append(dict(record,business=business))
    if requested:
        try:
            current=(compute or ComputeClient()).run_metadata(list(requested))
            if not isinstance(current,dict) or current.get('schema_version')!='solve-run-metadata/1':raise AgentError('RUN_METADATA_SCHEMA_INVALID')
            rows=current.get('items',[])
            if not isinstance(rows,list) or len(rows)>len(requested):raise AgentError('RUN_METADATA_SCOPE_MISMATCH')
            found={}
            for row in rows:
                if not isinstance(row,dict):raise AgentError('RUN_METADATA_SCHEMA_INVALID')
                rid=row.get('solve_run_id')
                if rid not in requested or rid in found:raise AgentError('RUN_METADATA_SCOPE_MISMATCH')
                found[rid]=row
            for item in items:
                run=item['business']['run'];row=found.get(run['solve_run_id'])
                if row is None:continue
                if row.get('scenario_id')!=item['id'] or row.get('state') not in {'QUEUED','RUNNING','SUCCEEDED','FAILED'}:
                    run['status']='INVALID_EVIDENCE';continue
                run.update(status='PRESENT',state=row['state'],updated_at=row.get('finished_at') or row.get('started_at') or row.get('created_at'))
        except (AgentError,OSError,ValueError,KeyError,TypeError) as exc:
            # Saved submission state is explicitly historical. Never silently
            # call each plan's expensive service/result path as a fallback.
            code=str(exc).split(':',1)[0] if isinstance(exc,AgentError) else 'RUN_METADATA_UNAVAILABLE'
            for item in items:
                run=item['business']['run']
                if run['solve_run_id'] in requested:
                    run.update(status='INVALID_EVIDENCE' if code.startswith('RUN_METADATA_') and code!='RUN_METADATA_UNAVAILABLE' else 'UNAVAILABLE',reason=code)
    return dict(schema_version='plan-catalog/1',items=items,page=result['page'],
        execution_authorized=False,checked_scope='BOUNDED_PLAN_METADATA_NO_NATIVE_CERTIFICATION')
