"""Persistent source-object indexes owned by the original ImportStore transaction.

Only immutable references are copied between state commits. Full Canonical
materialization remains explicit, at the planning/audit boundary. This is not a
second fact database and does not publish source cursors or confirm facts.
"""
import copy
import hashlib
import json
import re
import uuid
from pathlib import Path

from p2.preflight import digest
from p21.validation import now
from p21.continuity_version import instant, validate_receipt, source_chain_scope
from p21.source_sync import _business, _hash

RELATIONS=('order_id','po_id','receipt_id','lot_id','job_id','resource_id','operator_id')


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS business_states (version_id TEXT PRIMARY KEY, data TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS business_object_revisions (revision_id TEXT PRIMARY KEY, data TEXT NOT NULL)')
    columns=', '.join(k+' TEXT' for k in RELATIONS)
    db.execute('CREATE TABLE IF NOT EXISTS business_state_members (version_id TEXT NOT NULL, system TEXT NOT NULL, kind TEXT NOT NULL, business_id TEXT NOT NULL, revision_id TEXT NOT NULL, '+columns+', PRIMARY KEY(version_id,system,kind,business_id))')
    for field in RELATIONS:
        db.execute(f'CREATE INDEX IF NOT EXISTS business_state_{field} ON business_state_members(version_id,system,kind,{field})')
    db.execute('CREATE TABLE IF NOT EXISTS version_read_metadata (version_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, data TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS state_materializations (version_id TEXT PRIMARY KEY, state_revision TEXT NOT NULL, data TEXT NOT NULL)')
    db.execute("CREATE INDEX IF NOT EXISTS version_parent_index ON versions(json_extract(data,'$.parent_version_id'))")


def metadata(db,version,fingerprint):
    from p22.contracts import digest as version_digest
    result={k:version.get(k) for k in ('id','import_id','import_revision','filename','created_at','planning_ready','engine_executed','version_mode','parent_version_id','source_sync_receipt') if k in version}
    c=version.get('canonical',{})
    state=version.get('business_data',{}).get('state_commit',{})
    result.update(enterprise_id=c.get('enterprise_id',state.get('enterprise_id')),canonical_semantic_sha256=c.get('semantic_sha256'),
        confirmation_status='CONFIRMED' if version.get('confirmations') else 'UNCONFIRMED',
        immutable_fingerprint=fingerprint,version_sha256=version_digest(version))
    db.execute('INSERT OR REPLACE INTO version_read_metadata VALUES(?,?,?)',(version['id'],fingerprint,json.dumps(result,ensure_ascii=False)))
    return result


def version_metadata(store,identifier):
    with store.transaction() as db:
        row=db.execute('SELECT v.fingerprint,m.fingerprint,m.data FROM versions v LEFT JOIN version_read_metadata m ON v.id=m.version_id WHERE v.id=?',(identifier,)).fetchone()
        if not row:raise ValueError('VERSION_NOT_FOUND: 找不到数据版本')
        if row[1]==row[0]:return json.loads(row[2])
        raw=db.execute('SELECT data FROM versions WHERE id=?',(identifier,)).fetchone()[0]
        version=json.loads(raw)
        # Legacy compatibility is one explicit, persisted index bootstrap.
        if digest(version['business_data'])!=row[0]:raise ValueError('VERSION_CHANGED: 版本指纹不一致')
        if version.get('version_mode')=='CONTINUITY_STATE_VERSION':
            cached=db.execute('SELECT data FROM state_materializations WHERE version_id=?',(identifier,)).fetchone()
            if cached:version=json.loads(cached[0])
        return metadata(db,version,row[0])


def _insert_row(db,version_id,system,kind,row,refs):
    revision=_hash({'business':_business(row),'event_refs':sorted(set(refs))})
    entry=dict(row=copy.deepcopy(row),revision=revision,event_refs=list(refs))
    rid=digest(entry)
    db.execute('INSERT OR IGNORE INTO business_object_revisions VALUES(?,?)',(rid,json.dumps(entry,ensure_ascii=False)))
    fields=(version_id,system,kind,row['id'],rid,*[row.get(k) for k in RELATIONS])
    db.execute('INSERT OR REPLACE INTO business_state_members VALUES('+','.join('?' for _ in fields)+')',fields)
    return rid


def _state(db,identifier):
    row=db.execute('SELECT data FROM business_states WHERE version_id=?',(identifier,)).fetchone()
    return json.loads(row[0]) if row else None


def ensure(store,identifier):
    with store.connect() as db:
        state=_state(db,identifier)
    if state:return state
    version=store.version(identifier)
    # A legacy version is admitted only through its original strict audit.
    store.verify_version_sources(version)
    canonical=version.get('canonical',{})
    locations=canonical.get('tables',{}).get('locations',[])
    if len(locations)!=1 or not isinstance(locations[0].get('continuity_bundle'),dict):raise ValueError('CONTINUITY_SOURCE_BUNDLE_REQUIRED')
    bundle=locations[0]['continuity_bundle'];documents=bundle['documents']
    declarations={k:bundle[k] for k in ('definition','operating','reference')}
    enterprise=canonical['enterprise_id']
    if declarations['definition']['enterprise_id']!=enterprise or set(documents)!={'ERP','WMS','MES'}:raise ValueError('CONTINUITY_IDENTITY_MISMATCH')
    receipt=version.get('source_sync_receipt')
    heads={h['system']:copy.deepcopy(h) for h in receipt['batches']} if receipt else {}
    scope=copy.deepcopy(receipt.get('source_scope')) if receipt else None
    headers={s:{k:copy.deepcopy(v) for k,v in d.items() if k not in {'tables','event_refs'}} for s,d in documents.items()}
    source_at=receipt['as_of'] if receipt else canonical['planning_at']
    state=dict(version_id=identifier,parent_version_id=version.get('parent_version_id'),enterprise_id=enterprise,scope=scope,
        declarations=declarations,declarations_sha256=digest(declarations),source_as_of=source_at,source_heads=heads,
        document_headers=headers,state_revision=digest({'version':identifier,'fingerprint':digest(version['business_data'])}),
        source_files=copy.deepcopy(version['business_data'].get('source_files',[])),index_status='LEGACY_STATE_INDEX_BOOTSTRAP')
    with store.transaction() as db:
        existing=_state(db,identifier)
        if existing:return existing
        count=0
        for system,doc in documents.items():
            if doc['enterprise_id']!=enterprise:raise ValueError('CONTINUITY_IDENTITY_MISMATCH')
            for kind,rows in doc['tables'].items():
                for row in rows:
                    refs=doc.get('event_refs',{}).get(kind,{}).get(row['id'])
                    if not refs:raise ValueError('SOURCE_REVISION_EVIDENCE_REQUIRED')
                    _insert_row(db,identifier,system,kind,row,refs);count+=1
        state['indexed_records']=count
        db.execute('INSERT INTO business_states VALUES(?,?)',(identifier,json.dumps(state,ensure_ascii=False)))
    return state


def rows(store,identifier,system,kind,*,ids=None,where=None):
    from p21.demo_connector import TABLES
    if system not in TABLES or kind not in TABLES[system]:raise ValueError('SOURCE_TABLE_SCHEMA_CHANGED')
    if where and not set(where)<=set(RELATIONS):raise ValueError('CONTINUITY_RELATION_INDEX_UNSUPPORTED')
    ensure(store,identifier)
    conditions=['m.version_id=?','m.system=?','m.kind=?'];args=[identifier,system,kind]
    if ids is not None:
        if not isinstance(ids,list) or len(ids)>1000 or any(not isinstance(x,str) for x in ids):raise ValueError('CONTINUITY_LOOKUP_INVALID')
        if not ids:return []
        conditions.append('m.business_id IN ('+','.join('?' for _ in ids)+')');args.extend(ids)
    for k,v in (where or {}).items():conditions.append('m.'+k+'=?');args.append(v)
    with store.connect() as db:
        result=db.execute('SELECT m.business_id,m.revision_id,r.data FROM business_state_members m LEFT JOIN business_object_revisions r ON r.revision_id=m.revision_id WHERE '+' AND '.join(conditions)+' ORDER BY m.business_id',args).fetchall()
    values=[]
    for key,rid,raw in result:
        if raw is None:raise ValueError('BUSINESS_OBJECT_REVISION_MISSING')
        entry=json.loads(raw)
        if digest(entry)!=rid or entry.get('row',{}).get('id')!=key:raise ValueError('BUSINESS_OBJECT_REVISION_CHANGED')
        values.append(entry)
    return values


def refresh(task):
    change=task['state_change'];groups=task['canonical_groups']
    confirmed=bool(groups) and set(groups)<=set(task['confirmations'])
    issues=change['validation']['issues']
    valid=not issues and not change['validation'].get('fallback_reason')
    task.update(state='FACTS_BLOCKED' if not valid else 'FACTS_CONFIRMED' if confirmed else 'PENDING_REVIEW',
        validation={'issues':copy.deepcopy(issues),'blocking_count':len(issues)},duplicate_records=[],version_mode='CONTINUITY_STATE_VERSION',
        readiness=dict(ready=False,confirmed=confirmed,data_accepted=valid,can_save_facts=confirmed and valid,planning_status='NOT_PROJECTED',blocking_count=len(issues)))
    task['ingestion_stages']=dict(reception=dict(status='RECEIVED',originals_retained=True,transport='SOURCE_CONNECTOR_INCREMENT'),
        business=dict(status='BLOCKED' if not valid else 'CONFIRMED' if confirmed else 'PENDING_CONFIRMATION',confirmed_groups=list(task['confirmations']),requires_confirmation=not confirmed,partial_publication_supported=False,issues=copy.deepcopy(issues)),
        planning=dict(status='NOT_PROJECTED',ready=False,requires_policy_projection=True,issues=[]))
    return task


def _verify_new(changes,receipt):
    """Prove every postimage is in a new retained original; no parent replay."""
    carriers={};files=[]
    for head in receipt['batches']:
        for record in head.get('chain',[head]):
            path=record.get('source_path')
            if path:
                p=Path(path).resolve(strict=True)
                if p.suffix.lower()!='.json':raise ValueError('SOURCE_CHANGED: JSON source required')
                raw=p.read_bytes()
                if hashlib.sha256(raw).hexdigest()!=record['source_sha256']:raise ValueError('SOURCE_CHANGED')
                doc=json.loads(raw);carriers[record['source_sha256']]=doc
                if doc.get('enterprise_id')!=receipt['enterprise_id'] or doc.get('system')!=head['system']:raise ValueError('CONTINUITY_IDENTITY_MISMATCH')
                if instant(doc.get('as_of'))!=instant(receipt['as_of']):raise ValueError('CONTINUITY_SOURCE_CLOCK_MISMATCH')
                scope=receipt.get('source_scope')
                if scope and (doc.get('branch_id')!=scope['branch_id'] or type(doc.get('source_revision')) is not int or doc['source_revision']!=scope['source_revision']):raise ValueError('CONTINUITY_SOURCE_SCOPE_MISMATCH')
                header={k:copy.deepcopy(v) for k,v in doc.items() if k not in {'tables','event_refs','record_actions','absent_from_snapshot'}}
                files.append(dict(sha256=record['source_sha256'],extension='.json',filename=p.name,source_kind='CONTINUITY_INCREMENT',source_path=str(p),system=head['system'],document_header=header))
    for c in changes:
        doc=carriers.get(c['source_sha256'])
        if doc is None or doc.get('enterprise_id')!=receipt['enterprise_id'] or doc.get('system')!=c['system'] or not any(r==c['row'] for r in doc.get('tables',{}).get(c['table'],[])) or sorted(set(doc.get('event_refs',{}).get(c['table'],{}).get(c['id'],[])))!=sorted(set(c['event_refs'])):
            raise ValueError('SOURCE_CHANGED: postimage is not bound to retained original')
    return files


def upload(store,parent_version_id,*,source_as_of,source_scope,declarations,changes,receipt,validation):
    from p22.continuity_state import prepare_changes
    parent=ensure(store,parent_version_id)
    source=validate_receipt(receipt,parent['enterprise_id'],verify_originals=False)
    if source['as_of']!=source_as_of or source.get('source_scope')!=source_scope:raise ValueError('CONTINUITY_SOURCE_SCOPE_MISMATCH')
    if source_chain_scope(source)!=source_chain_scope({'source_scope':parent['scope']} if parent['scope'] else {}):raise ValueError('CONTINUITY_SOURCE_SCOPE_MISMATCH')
    meta=dict(parent,previous_source_as_of=parent['source_as_of'],source_as_of=source_as_of,
        previous_declarations_sha256=parent['declarations_sha256'],declarations=declarations,declarations_sha256=digest(declarations))
    checked=prepare_changes(changes,lambda s,k,**q:rows(store,parent_version_id,s,k,**q),meta)
    if checked['issues']:raise ValueError(checked['issues'][0]['code'])
    if checked.get('fallback_reason'):raise ValueError(checked['fallback_reason'])
    if not checked['changes']:raise ValueError('CONTINUITY_CHANGESET_EMPTY')
    files=_verify_new(checked['changes'],source)
    for record in files:
        before=parent['document_headers'][record['system']];after=record['document_header']
        for key in ('source_id','branch_id'):
            if key in before and after.get(key)!=before[key]:raise ValueError('CONTINUITY_SOURCE_SCOPE_MISMATCH')
    from p21.canonical_io import archive_original
    for record in files:
        if archive_original(store.root/'originals',Path(record['source_path']).read_bytes())!=record['sha256']:raise ValueError('SOURCE_CHANGED')
    for h in source['batches']:
        before=parent['source_heads'].get(h['system']);first=h.get('chain',[h])[0]
        if before and h['batch_id']==before['batch_id']:
            if any(h.get(k)!=before.get(k) for k in ('cursor','source_sha256')):raise ValueError('SOURCE_BATCH_CONFLICT')
        elif first['previous_batch_id']!=(before['batch_id'] if before else None):raise ValueError('SOURCE_BATCH_PARENT_MISMATCH')
    change=dict(parent_version_id=parent_version_id,parent_state_revision=parent['state_revision'],enterprise_id=parent['enterprise_id'],source_as_of=source_as_of,
        source_scope=source_scope,declarations_sha256=parent['declarations_sha256'],changes=checked['changes'],validation=checked,metrics=checked['metrics'],receipt=source,source_files=files)
    sha=digest(change)
    with store.transaction() as db:
        old=db.execute('SELECT data FROM tasks WHERE sha=?',(sha,)).fetchone()
        if old:return dict(refresh(json.loads(old[0])),deduplicated=True)
        groups=sorted({c['system']+'.'+c['table'] for c in checked['changes']})
        task=dict(id='IMP-'+uuid.uuid4().hex[:12],file_sha256=sha,filename='企业来源增量',revision=1,state_change=change,
            source={'sheets':{}},mappings={},customers={},confirmations={},corrections=[],ai_events=[],canonical_groups=groups,
            source_files=files,source_package=dict(kind='CONTINUITY_INCREMENT',files=files),version_ids=[],current_version_id=None,parent_version_id=parent_version_id,
            created_at=now(),updated_at=now(),history=[dict(at=now(),action='CONTINUITY_CHANGES_RECEIVED',changed_records=checked['metrics']['changed_records'])],deduplicated=False)
        refresh(task)
        db.execute('INSERT INTO tasks VALUES(?,?,?)',(task['id'],sha,json.dumps(task,ensure_ascii=False)))
    return task


def save(store,db,task,parent_version_id,receipt):
    refresh(task);change=task['state_change']
    if not task['readiness']['confirmed']:raise ValueError('CONFIRMATION_REQUIRED: 请核对来源变化')
    if not task['readiness']['data_accepted']:raise ValueError('INVALID_BUSINESS_FACTS')
    if parent_version_id!=change['parent_version_id'] or receipt is None or validate_receipt(receipt,change['enterprise_id'],verify_originals=False)!=change['receipt']:raise ValueError('SOURCE_SYNC_RECEIPT_REQUIRED')
    parent=_state(db,parent_version_id)
    if not parent or parent['state_revision']!=change['parent_state_revision']:raise ValueError('VERSION_CHANGED')
    old=db.execute('SELECT id FROM versions WHERE task_id=?',(task['id'],)).fetchone()
    if old:return dict(version_id=old[0],deduplicated=True,task=task)
    if db.execute("SELECT id FROM versions WHERE json_extract(data,'$.parent_version_id')=? LIMIT 1",(parent_version_id,)).fetchone():raise ValueError('CONTINUITY_HEAD_CONFLICT')
    _verify_new(change['changes'],change['receipt'])
    identifier='DATA-'+uuid.uuid4().hex[:12]
    state=dict(parent,version_id=identifier,parent_version_id=parent_version_id,source_as_of=change['source_as_of'],scope=copy.deepcopy(change['source_scope']),index_status='INCREMENTAL_STATE')
    state['source_heads']=dict(parent['source_heads'],**{h['system']:h for h in change['receipt']['batches']})
    state['document_headers']=copy.deepcopy(parent['document_headers'])
    for record in change['source_files']:
        state['document_headers'][record['system']].update(record['document_header'])
    state['source_files']=parent['source_files']+change['source_files']
    state['state_revision']=digest(dict(parent=parent['state_revision'],change=task['file_sha256']))
    copied=db.execute('INSERT INTO business_state_members SELECT ?,system,kind,business_id,revision_id,'+','.join(RELATIONS)+' FROM business_state_members WHERE version_id=?',(identifier,parent_version_id)).rowcount
    for c in change['changes']:_insert_row(db,identifier,c['system'],c['table'],c['row'],c['event_refs'])
    state['metrics']=dict(change['metrics'],membership_references_copied=copied,object_payloads_written=len(change['changes']))
    db.execute('INSERT INTO business_states VALUES(?,?)',(identifier,json.dumps(state,ensure_ascii=False)))
    binding={k:state[k] for k in ('enterprise_id','parent_version_id','state_revision','source_as_of','declarations_sha256')}
    business=dict(schema_version='canonical-state-commit-v1',state_commit=binding,source_sync_receipt=change['receipt'])
    version=dict(id=identifier,import_id=task['id'],import_revision=task['revision'],created_at=now(),filename=task['filename'],file_sha256=task['file_sha256'],business_data=business,
        confirmations=copy.deepcopy(task['confirmations']),corrections=[],mappings={},planning_ready=False,engine_executed=False,version_mode='CONTINUITY_STATE_VERSION',
        parent_version_id=parent_version_id,source_sync_receipt=change['receipt'])
    fp=digest(business)
    db.execute('INSERT INTO versions VALUES(?,?,?,?)',(identifier,task['id'],fp,json.dumps(version,ensure_ascii=False)))
    metadata(db,version,fp)
    task['version_ids'].append(identifier);task['current_version_id']=identifier;task['revision']+=1
    task['history'].append(dict(at=now(),action='VERSION_SAVED',version_id=identifier));store._write(db,task)
    return dict(version_id=identifier,deduplicated=False,task=task)



def verify_membership(store,identifier,seen=None):
    """Full reference audit at materialization; ordinary point reads do not scan."""
    seen=set() if seen is None else seen
    if identifier in seen:raise ValueError('CONTINUITY_HEAD_CONFLICT')
    seen.add(identifier)
    state=ensure(store,identifier)
    with store.connect() as db:
        stored=db.execute('SELECT fingerprint,data FROM versions WHERE id=?',(identifier,)).fetchone()
        version=json.loads(stored[1])
        if digest(version['business_data'])!=stored[0]:raise ValueError('VERSION_CHANGED')
        missing=db.execute('SELECT 1 FROM business_state_members m LEFT JOIN business_object_revisions r ON m.revision_id=r.revision_id WHERE m.version_id=? AND r.revision_id IS NULL LIMIT 1',(identifier,)).fetchone()
        if missing:raise ValueError('BUSINESS_OBJECT_REVISION_MISSING')
        actual={tuple(r[:3]):r[3] for r in db.execute('SELECT system,kind,business_id,revision_id FROM business_state_members WHERE version_id=?',(identifier,))}
    if version.get('version_mode')=='CONTINUITY_STATE_VERSION':
        binding=version['business_data']['state_commit']
        source=version['business_data']['source_sync_receipt']
        if (state.get('version_id')!=identifier
            or any(state.get(k)!=v for k,v in binding.items())
            or version.get('parent_version_id')!=binding.get('parent_version_id')
            or version.get('source_sync_receipt')!=source
            or state.get('scope')!=source.get('source_scope')
            or digest(state['declarations'])!=binding.get('declarations_sha256')
            or any(state['source_heads'].get(h['system'])!=h for h in source['batches'])):
            raise ValueError('VERSION_CHANGED: compact state binding differs from its immutable commit')
        parent=state['parent_version_id'];verify_membership(store,parent,seen)
        with store.connect() as db:
            task=store._read(db,version['import_id'])
            expected={tuple(r[:3]):r[3] for r in db.execute('SELECT system,kind,business_id,revision_id FROM business_state_members WHERE version_id=?',(parent,))}
            parent_state=_state(db,parent)
        change=task['state_change']
        if digest(change)!=task['file_sha256'] or state['state_revision']!=digest(dict(parent=parent_state['state_revision'],change=task['file_sha256'])):raise ValueError('VERSION_CHANGED')
        for c in change['changes']:
            entry=dict(row=c['row'],revision=c['revision'],event_refs=c['event_refs'])
            expected[c['system'],c['table'],c['id']]=digest(entry)
    else:
        expected={}
        docs=version['canonical']['tables']['locations'][0]['continuity_bundle']['documents']
        for system,doc in docs.items():
            for kind,values in doc['tables'].items():
                for row in values:
                    refs=doc['event_refs'][kind][row['id']]
                    entry=dict(row=row,revision=_hash({'business':_business(row),'event_refs':sorted(set(refs))}),event_refs=refs)
                    expected[system,kind,row['id']]=digest(entry)
    if expected!=actual:raise ValueError('BUSINESS_STATE_MEMBERSHIP_CHANGED')


def materialize(store,identifier):
    state=ensure(store,identifier)
    verify_membership(store,identifier)
    docs={s:copy.deepcopy(h)|dict(tables={},event_refs={}) for s,h in state['document_headers'].items()}
    with store.connect() as db:
        records=db.execute('SELECT m.system,m.kind,m.business_id,m.revision_id,r.data FROM business_state_members m LEFT JOIN business_object_revisions r ON r.revision_id=m.revision_id WHERE m.version_id=? ORDER BY m.system,m.kind,m.business_id',(identifier,)).fetchall()
    for system,kind,key,rid,payload in records:
        if payload is None:raise ValueError('BUSINESS_OBJECT_REVISION_MISSING')
        entry=json.loads(payload)
        if digest(entry)!=rid or entry.get('row',{}).get('id')!=key:raise ValueError('BUSINESS_OBJECT_REVISION_CHANGED')
        doc=docs[system]
        doc['tables'].setdefault(kind,[]).append(entry['row']);doc['event_refs'].setdefault(kind,{})[key]=entry['event_refs']
    for system,doc in docs.items():
        # This is an explicit full view of confirmed state, not the raw delta.
        doc.update(as_of=state['source_as_of'],knowledge_cutoff=state['source_as_of'],mode='FULL_SNAPSHOT',absence_semantics='MISSING_REVIEW_NOT_CANCELLATION')
        doc.pop('previous_as_of',None)
    return dict(copy.deepcopy(state['declarations']),documents=docs)


def materialized_version(store,compact):
    identifier=compact['id'];state=ensure(store,identifier)
    verify_membership(store,identifier)
    with store.connect() as db:
        cached=db.execute('SELECT state_revision,data FROM state_materializations WHERE version_id=?',(identifier,)).fetchone()
        meta=db.execute('SELECT fingerprint,data FROM version_read_metadata WHERE version_id=?',(identifier,)).fetchone()
    if cached and cached[0]==state['state_revision']:
        result=json.loads(cached[1])
        from p22.contracts import digest as version_digest
        if (not meta or meta[0]!=digest(compact['business_data'])
            or json.loads(meta[1]).get('version_sha256')!=version_digest(result)
            or result.get('id')!=identifier or result.get('parent_version_id')!=compact.get('parent_version_id')
            or result.get('state_binding')!=compact['business_data']['state_commit']
            or result.get('source_sync_receipt')!=compact['source_sync_receipt']
            or result.get('canonical',{}).get('enterprise_id')!=state['enterprise_id']
            or result.get('business_data',{}).get('canonical')!=result.get('canonical')):
            raise ValueError('VERSION_CHANGED: cached materialization differs from its confirmed state')
        return result
    from p22.continuity_sources import build_snapshot
    from p21.canonical import assemble
    snapshot=build_snapshot(**materialize(store,identifier))
    carrier=json.dumps(snapshot,ensure_ascii=False,sort_keys=True).encode();sha=hashlib.sha256(carrier).hexdigest()
    from p21.canonical_io import archive_original
    if archive_original(store.root/'originals',carrier)!=sha:raise ValueError('SOURCE_CHANGED')
    refs={k:[dict(file_sha256=sha,table=k,row_id=r['id'],state_revision=state['state_revision']) for r in rr] for k,rr in snapshot['tables'].items()}
    canonical=assemble(snapshot['enterprise_id'],snapshot['tables'],planning_at=snapshot['planning_at'],horizon=snapshot['horizon'],timezone=snapshot['timezone'],source_refs=refs)
    if canonical['issues']:raise ValueError('CONTINUITY_MATERIALIZATION_BLOCKED')
    groups=list(canonical['tables']);proof=digest(compact['confirmations'])
    confirmations={g:dict(reviewer='; '.join(sorted({v['reviewer'] for v in compact['confirmations'].values()})),method='CONFIRMED_STATE_DERIVATION',content_sha256=proof,source_confirmation_groups=list(compact['confirmations'])) for g in groups}
    for obj in canonical['objects']:obj['factual_status']='CONFIRMED_FACT'
    result=copy.deepcopy(compact)
    result.update(canonical=canonical,confirmations=confirmations,state_binding=copy.deepcopy(compact['business_data']['state_commit']))
    result['business_data']=dict(schema_version='canonical-business-v1',canonical=canonical,source_files=[dict(sha256=sha,extension='.json',filename=sha+'.json',source_kind='CONFIRMED_STATE_MATERIALIZATION')],
        corrections=[],source_sync_receipt=compact['source_sync_receipt'],state_commit=compact['business_data']['state_commit'])
    with store.transaction() as db:
        db.execute('INSERT OR REPLACE INTO state_materializations VALUES(?,?,?)',(identifier,state['state_revision'],json.dumps(result,ensure_ascii=False)))
        metadata(db,result,digest(compact['business_data']))
    return result


def audit(store,version):
    """Explicit strict audit reopens inherited/new originals and rebuilds state."""
    identifier=version['id'];state=ensure(store,identifier)
    with store.connect() as db:
        compact=json.loads(db.execute('SELECT data FROM versions WHERE id=?',(identifier,)).fetchone()[0])
        fp=db.execute('SELECT fingerprint FROM versions WHERE id=?',(identifier,)).fetchone()[0]
    if digest(compact['business_data'])!=fp or version.get('state_binding')!=compact['business_data']['state_commit']:raise ValueError('VERSION_CHANGED')
    with store.connect() as db:task=store._read(db,compact['import_id'])
    change=task['state_change']
    if digest(change)!=task['file_sha256'] or any(v.get('content_sha256')!=digest(change) for v in task['confirmations'].values()):raise ValueError('VERSION_CHANGED')
    files=_verify_new(change['changes'],compact['source_sync_receipt'])
    for record in files:
        stored_header=state['document_headers'][record['system']]
        if any(stored_header.get(k)!=v for k,v in record['document_header'].items()):raise ValueError('CONTINUITY_SOURCE_SCOPE_MISMATCH')
    for f in state['source_files']:
        p=Path(f['source_path']) if f.get('source_path') else store.root/'originals'/(f['sha256']+f['extension'])
        if p.resolve().suffix.lower() not in {'.json','.csv','.xlsx'} or hashlib.sha256(p.read_bytes()).hexdigest()!=f['sha256']:raise ValueError('SOURCE_CHANGED')
    from p22.foundation_projection import validate_facts
    validate_facts(version)
    store._verify_canonical_sources(version['canonical'],version['business_data']['source_files'],replay_mapping=False)
    expected=materialized_version(store,compact)
    if digest(expected)!=digest(version):raise ValueError('VERSION_CHANGED')
    from p22.continuity_sources import build_snapshot
    if build_snapshot(**materialize(store,identifier))['tables']!=version['canonical']['tables']:raise ValueError('CONTINUITY_CANONICAL_REBUILD_MISMATCH')
