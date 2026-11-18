"""Connector orchestration over the existing SourceSync and ImportStore Owners.

JSON here is a recoverable sync-intent journal, not another enterprise store.
P2 owns facts/confirmation/version transactions. SyncStore owns source cursors.
No path in this module starts planning or modifies an existing PlanResult.
"""
import copy
import json
import os
import re
import threading
import uuid
from datetime import datetime,timezone
from pathlib import Path

from p21.continuity_version import validate_receipt
from p21.demo_connector import DemoEnterpriseConnector, SYSTEMS, digest
from p21.source_sync import SyncStore, state_equivalent, _write


def now():return datetime.now(timezone.utc).isoformat()


def anomaly(exc,system=None,member=None):
    """Keep logical responsibility without echoing OS paths or source values."""
    code=str(exc).split(':',1)[0]
    if not re.fullmatch('[A-Z][A-Z0-9_]{0,100}',code):code='SOURCE_READ_OR_MAPPING_FAILED'
    value=dict(code=code)
    if system in SYSTEMS:value['system']=system
    if member and re.fullmatch(r'[A-Za-z0-9_/-]{1,100}',member):value['source_member']=member
    if isinstance(exc,KeyError) and exc.args and exc.args[0] in {'as_of','tables','source_known_at','business_occurred_at','event_refs','id'}:
        value['field']=exc.args[0]
    return value


class ConnectorService:
    def __init__(self, imports, adapter, *, scope_id=None):
        self.imports=imports;self.adapter=adapter
        if scope_id is not None and not re.fullmatch('live-[A-Za-z0-9_-]{1,60}',scope_id):raise ValueError('CONNECTOR_SCOPE_INVALID')
        self.root=imports.root/'source-sync'
        if scope_id:self.root=self.root/scope_id
        self.root.mkdir(parents=True,exist_ok=True)
        self.sync=SyncStore(self.root,adapter.enterprise_id,materialize=getattr(imports,'materialize_continuity',None))
        (self.root/'tasks').mkdir(exist_ok=True)
        self.lock=threading.RLock()

    def _path(self,identifier):
        if not isinstance(identifier,str) or not re.fullmatch(r'SYNC-[a-f0-9]{32}',identifier):raise ValueError('SYNC_TASK_NOT_FOUND')
        return self.root/'tasks'/(identifier+'.json')

    def _record(self,identifier):
        try:return json.loads(self._path(identifier).read_text('utf-8'))
        except FileNotFoundError:raise ValueError('SYNC_TASK_NOT_FOUND') from None

    def _save(self,record):_write(self._path(record['id']),record)

    def _records(self):
        return sorted([json.loads(p.read_text('utf-8')) for p in (self.root/'tasks').glob('SYNC-*.json')],key=lambda r:r['created_at'])

    def _head(self):
        p=self.root/'connector-head.json'
        return json.loads(p.read_text('utf-8')) if p.exists() else None

    def _public(self,record):
        allowed={'id','status','checkpoint','as_of','enterprise_id','parent_version_id','import_id','revision','groups',
            'summary','sources','anomalies','readiness','version_id','committed_at','reviewer','planning_ready','created_at','error','source_scope','ingestion_stages','ingestion_mode','fallback_reason','validation_metrics'}
        result={k:copy.deepcopy(v) for k,v in record.items() if k in allowed}
        if record.get('import_id') and record.get('status') not in {'COMMITTED','UNCHANGED'}:
            task=self.imports.get(record['import_id'])
            result['revision']=task['revision']
            if task.get('ingestion_stages'):result['ingestion_stages']=copy.deepcopy(task['ingestion_stages'])
        if result['status']=='COMMITTING':
            result.update(status='READY',recovery_state='CONFIRMED_VERSION_SAVE_PENDING')
        if result['status']=='READING':
            result.update(status='BLOCKED',anomalies=[dict(code='SOURCE_READ_INTERRUPTED')])
        return result

    def _recover(self):
        """A saved, confirmed P2 version is sufficient to finish cursor publication.

        Never create confirmation or save a version on a GET. A crash before
        version-save needs the explicit commit retry, using its new revision.
        """
        for record in self._records():
            if record['status']!='COMMITTING' or not record.get('import_id'):continue
            task=self.imports.get(record['import_id']);version_id=task.get('current_version_id')
            if not version_id:continue
            indexed=record.get('ingestion_mode')=='CONTINUITY_STATE_INCREMENT'
            version=self.imports.version_metadata(version_id) if indexed else self.imports.version(version_id)
            if version.get('source_sync_receipt')==validate_receipt(record['sync_receipt'],record['enterprise_id']) and version.get('parent_version_id')==record['parent_version_id']:
                # P2 already confirmed/saved this exact version. Verify its
                # retained NEW originals, not a mutable remote CURRENT that
                # may legitimately have advanced since the process died.
                if not indexed:self.imports.verify_version_sources(version)
                self._finish(record,version_id)

    def _finish(self,record,version_id):
        stage=self.sync.stage(record['id'])
        stage.publish(record['expected_heads'],data_version_id=version_id)
        task=self.imports.get(record['import_id'])
        record.update(status='COMMITTED',version_id=version_id,committed_at=record.get('committed_at') or now(),
            planning_ready=False,revision=task['revision'],readiness=task['readiness'],ingestion_stages=task.get('ingestion_stages'))
        record.pop('error',None)
        # Replays of an ancestor must never roll the connector head backwards.
        head=self._head()
        if not head or head['id']==record['id'] or record['parent_version_id']==head['version_id']:
            _write(self.root/'connector-head.json',self._public(record))
        self._save(record)
        return self._public(record)

    def get_sync_status(self):
        with self.lock:
            self._recover();head=self._head();records=self._records()
            value=self.adapter.describe_source()
            for source in value['systems']:
                batch=self.sync.head(source['id'])
                source.update(status='UNAVAILABLE' if value.get('source_availability')=='UNAVAILABLE' else 'SYNCED' if batch else 'NOT_SYNCED',cursor=batch['cursor'] if batch else None,
                              as_of=batch['as_of'] if batch else None)
            pending=[r for r in records if r['status'] not in {'COMMITTED','UNCHANGED'}]
            value.update(configured=True,status='UNAVAILABLE' if value.get('source_availability')=='UNAVAILABLE' else 'SYNCED' if head else 'NOT_SYNCED',current_version_id=head['version_id'] if head else None,
                current_checkpoint=head['checkpoint'] if head else None,last_sync=head,
                history=[self._public(r) for r in records[-30:]][::-1],preview=self._public(pending[-1]) if pending else None)
            return value

    def preview_sync(self,checkpoint):
        with self.lock:
            self._recover();point=self.adapter.checkpoint(checkpoint);head=self._head()
            fallback=None
            if head and callable(getattr(self.imports,'continuity_meta',None)):
                result,fallback=self._preview_indexed(checkpoint,point,head)
                if result is not None:return result
            # Validate bytes again even when the checkpoint was committed earlier.
            snapshots={};system=None
            try:
                for system in SYSTEMS:snapshots[system]=self.adapter.read_snapshot(system,checkpoint)
            except (ValueError,OSError,KeyError,TypeError) as exc:
                record=dict(id='SYNC-'+uuid.uuid4().hex,status='BLOCKED',checkpoint=checkpoint,as_of=point['as_of'],
                    enterprise_id=self.adapter.enterprise_id,parent_version_id=head['version_id'] if head else None,
                    created_at=now(),summary=dict(added=0,updated=0,unchanged=0,late=0,conflicts=1),sources=[],
                    anomalies=[anomaly(exc,system,checkpoint+'/'+system+'/snapshot' if system else None)])
                if point.get('source_scope'):record['source_scope']=point['source_scope']
                self._save(record);return self._public(record)
            evidence=digest({s:r[1]['sha256'] for s,r in snapshots.items()}|{'config':self.adapter.fingerprint})
            for old in reversed(self._records()):
                if old['checkpoint']==checkpoint and old.get('source_fingerprint')==evidence:
                    if old['status']=='COMMITTED':return dict(self._public(old),status='UNCHANGED')
                    if old['status'] in {'READY','COMMITTING'} and old['expected_heads']=={s:self.sync.head(s)['batch_id'] for s in SYSTEMS if self.sync.head(s)}:
                        return self._public(old)
            if any(r['status']=='COMMITTING' for r in self._records()):raise ValueError('SYNC_COMMIT_RETRY_REQUIRED')
            if head and point['as_of']<=head['as_of']:raise ValueError('KNOWLEDGE_CLOCK_REGRESSION')
            record=dict(id='SYNC-'+uuid.uuid4().hex,status='READING',checkpoint=checkpoint,as_of=point['as_of'],
                enterprise_id=self.adapter.enterprise_id,parent_version_id=head['version_id'] if head else None,
                created_at=now(),source_fingerprint=evidence,configuration_fingerprint=self.adapter.fingerprint,
                summary=dict(added=0,updated=0,unchanged=0,late=0,conflicts=0),sources=[],anomalies=[],
                read_keys=[value[1]['key'] for value in snapshots.values()]+self.adapter.declaration_keys())
            if fallback:record['fallback_reason']=fallback
            if point.get('source_scope'):record['source_scope']=copy.deepcopy(point['source_scope'])
            stage=self.sync.stage(record['id']);record['expected_heads']=stage.expected_heads
            self._save(record);documents={};receipts=[];classifications={};late=set();system=None
            try:
                for system in SYSTEMS:
                    before=stage.current(system)
                    incoming=self.adapter.read_changes(system,checkpoint,before['as_of']) if before else [snapshots[system]]
                    chain=[]
                    for doc,reference,raw in incoming:
                        if reference['key'] not in record['read_keys']:record['read_keys'].append(reference['key'])
                        # Archive under the SAME source Owner. P2 later verifies
                        # this durable raw original, not a mutable external path.
                        path=self.root/'raw'/(reference['sha256']+'.json')
                        if not path.exists():path.write_bytes(raw)
                        prior=stage.current(system)
                        batch=stage.apply(path,previous_batch_id=prior['batch_id'] if prior else None)
                        if batch['status']=='BLOCKED' or not batch['projection_ready']:
                            record['anomalies'].extend(dict(system=system,code=p.get('reason',p.get('classification','SOURCE_CONFLICT')),**{k:p[k] for k in ('record_id','table') if k in p}) for p in batch['pending'])
                            raise ValueError('SOURCE_REVIEW_REQUIRED:'+system)
                        if batch['status']=='DUPLICATE':continue
                        chain.append({k:batch[k] for k in ('system','batch_id','source_sha256','cursor','previous_batch_id','source_path','imported_at','as_of','mode')})
                        for change in batch['changes']:
                            token=(system,change.get('table'),change.get('record_id'))
                            if classifications.get(token)!='ADDED':classifications[token]=change['classification']
                            if 'LATE_ARRIVAL' in change.get('flags',[]):late.add(token)
                    current=stage.current(system)
                    if not current or not state_equivalent(current['state'],snapshots[system][0]['tables']):raise ValueError('SOURCE_SNAPSHOT_INCREMENT_MISMATCH:'+system)
                    documents[system]=dict(snapshots[system][0],tables=current['state'],event_refs=current['event_refs'])
                    receipt=chain[-1] if chain else {k:current[k] for k in ('system','batch_id','source_sha256','cursor','previous_batch_id','source_path','imported_at','as_of','mode')}
                    if len(chain)>1:receipt=dict(receipt,chain=chain)
                    receipts.append(receipt)
                    record['sources'].append(dict(system=system,status='PREVIEWED',as_of=current['as_of'],cursor=current['cursor'],batches=len(chain)))
                system=None
                snapshot=self.adapter.build_snapshot(documents)
                task=self.imports.upload_structured(snapshot)
                record.update(import_id=task['id'],revision=task['revision'],groups=task['canonical_groups'],
                    readiness=task['readiness'],ingestion_stages=task.get('ingestion_stages'))
                if not task['readiness']['data_accepted']:
                    record['anomalies'].extend({k:copy.deepcopy(issue[k]) for k in ('code','table','row','id','field','target_id','target_table') if k in issue}
                        for issue in task['validation']['issues'])
                    raise ValueError('P2_DATA_NOT_ACCEPTED')
                record.update(status='READY',import_id=task['id'],revision=task['revision'],groups=task['canonical_groups'],readiness=task['readiness'],
                    sync_receipt=validate_receipt(dict(enterprise_id=snapshot['enterprise_id'],as_of=snapshot['planning_at'],batches=receipts,trigger='USER_REQUESTED_CONNECTOR_SYNC',
                        **({'source_scope':record['source_scope']} if record.get('source_scope') else {})),snapshot['enterprise_id']))
                for kind,key in [('ADDED','added'),('UPDATED','updated'),('UNCHANGED','unchanged'),('DUPLICATE','unchanged')]:
                    record['summary'][key]+=sum(c==kind for c in classifications.values())
                record['summary']['late']=len(late)
            except (ValueError,KeyError,TypeError,OSError) as exc:
                record['status']='BLOCKED'
                record['anomalies'].append(anomaly(exc,system))
                record['summary']['conflicts']=len(record['anomalies'])
            self._save(record)
            return self._public(record)

    def _preview_indexed(self,checkpoint,point,head):
        """Normal changed-source path: actual deltas + authenticated point reads.

        Unsupported domain changes retain the complete legacy audit with an
        explicit reason. Validation failures are blocked, never hidden by that
        fallback. SourceSync and P2 still own publication and confirmation.
        """
        from p22.continuity_state import prepare_changes
        from p21.demo_connector import validate_source_document
        prior=self._record(head['id'])
        if prior.get('configuration_fingerprint')!=self.adapter.fingerprint:return None,'CONNECTOR_CONFIGURATION_REVALIDATION_REQUIRED'
        if point['as_of']==head['as_of'] and point.get('source_scope')==head.get('source_scope') and prior.get('ingestion_mode')=='CONTINUITY_STATE_INCREMENT':
            self.adapter.verify_read_set(prior['read_keys'])
            return dict(self._public(prior),status='UNCHANGED'),None
        same_clock_revision_advance=(point['as_of']==head['as_of'] and isinstance(point.get('source_scope'),dict) and
            isinstance(head.get('source_scope'),dict) and point['source_scope'].get('connector_id')==head['source_scope'].get('connector_id') and
            point['source_scope'].get('branch_id')==head['source_scope'].get('branch_id') and
            type(point['source_scope'].get('source_revision')) is int and point['source_scope']['source_revision']>head['source_scope'].get('source_revision',-1))
        if point['as_of']<head['as_of'] or (point['as_of']==head['as_of'] and not same_clock_revision_advance):
            return None,'COMMITTED_CHECKPOINT_REVALIDATION_REQUIRED'
        if any(r['status']=='COMMITTING' for r in self._records()):raise ValueError('SYNC_COMMIT_RETRY_REQUIRED')
        parent=self.imports.continuity_meta(head['version_id'])
        actual_heads={s:self.sync.head(s)['batch_id'] for s in SYSTEMS if self.sync.head(s)}
        parent_heads={s:r['batch_id'] if isinstance(r,dict) else r for s,r in parent['source_heads'].items()}
        if parent_heads!=actual_heads:raise ValueError('SOURCE_HEAD_CONFLICT')
        from p21.continuity_version import batch_identity
        if any(isinstance(parent['source_heads'][s],dict) and batch_identity(self.sync.head(s),s)!=batch_identity(parent['source_heads'][s],s) for s in actual_heads):
            raise ValueError('SOURCE_HEAD_METADATA_MISMATCH')
        record=dict(id='SYNC-'+uuid.uuid4().hex,status='READING',checkpoint=checkpoint,as_of=point['as_of'],
            enterprise_id=self.adapter.enterprise_id,parent_version_id=head['version_id'],created_at=now(),
            configuration_fingerprint=self.adapter.fingerprint,ingestion_mode='CONTINUITY_STATE_INCREMENT',
            summary=dict(added=0,updated=0,unchanged=0,late=0,conflicts=0),sources=[],anomalies=[],read_keys=[])
        if point.get('source_scope'):record['source_scope']=copy.deepcopy(point['source_scope'])
        incoming={};postimages=[];system=None
        try:
            for system in SYSTEMS:
                before=self.sync.head(system)
                values=self.adapter.read_changes(system,checkpoint,before['as_of'])
                if len(values)!=1:return None,'MULTI_BATCH_SOURCE_CHAIN_FULL_REVALIDATION'
                doc,reference,raw=values[0]
                validate_source_document(doc,system,point['as_of'],'INCREMENTAL_UPSERT')
                if doc['as_of']!=point['as_of'] or doc.get('previous_as_of')!=before['as_of']:raise ValueError('INCREMENTAL_CURSOR_GAP')
                if doc.get('source_delta_mode')=='FULL_SOURCE_DOMAIN_FALLBACK':return None,doc.get('source_delta_fallback_reason','SOURCE_DOMAIN_REVALIDATION_REQUIRED')
                if doc.get('unresolved_events') or any(doc.get('absent_from_snapshot',{}).values()):return None,'SOURCE_ABSENCE_OR_UNRESOLVED_FULL_REVALIDATION'
                import hashlib
                if hashlib.sha256(raw).hexdigest()!=reference['sha256'] or json.loads(raw.decode('utf-8-sig'))!=doc:raise ValueError('SOURCE_CHANGED')
                incoming[system]=values[0];record['read_keys'].append(reference['key'])
                for table,rows in doc['tables'].items():
                    for row in rows:
                        image=dict(system=system,table=table,row=row,event_refs=doc.get('event_refs',{}).get(table,{}).get(row['id']),source_sha256=reference['sha256'])
                        if doc.get('record_actions',{}).get(table,{}).get(row['id']):image['action']=doc['record_actions'][table][row['id']]
                        postimages.append(image)
            if not postimages:return None,'FULL_WATERMARK_ADVANCE_WITHOUT_OBJECT_CHANGE'
            if any(c['system']=='MES' or (c['system']=='WMS' and c['table'] in {'finished_goods','movements'}) for c in postimages):
                return None,'MANUFACTURING_DOMAIN_REVALIDATION_REQUIRED'
            declarations=self.adapter.declarations()
            # The original continuity Owner persists exactly these reference
            # roles, never unrelated inherited stock/MO/history. Compare the
            # same declared business contract; retain the complete raw key.
            from p22.continuity_sources import declaration
            declarations['reference']={'tables':declaration(declarations['reference'])}
            record['read_keys']+=self.adapter.declaration_keys()
            metadata=dict(parent,previous_source_as_of=parent['source_as_of'],source_as_of=point['as_of'],
                previous_declarations_sha256=parent['declarations_sha256'],declarations=declarations,declarations_sha256=digest(declarations))
            checked=prepare_changes(postimages,lambda s,t,**q:self.imports.continuity_rows(head['version_id'],s,t,**q),metadata)
            if checked['issues']:
                record['anomalies']=checked['issues'];raise ValueError('CONTINUITY_CHANGESET_REJECTED')
            if checked['fallback_reason']:return None,checked['fallback_reason']
            record['source_fingerprint']=digest({s:r[1]['sha256'] for s,r in incoming.items()}|{'config':self.adapter.fingerprint})
            for old in reversed(self._records()):
                if (old.get('ingestion_mode')==record['ingestion_mode'] and old.get('source_fingerprint')==record['source_fingerprint'] and
                        old.get('parent_version_id')==record['parent_version_id'] and old.get('expected_heads')==actual_heads and old['status']=='READY'):
                    return self._public(old),None
            stage=self.sync.stage(record['id']);record['expected_heads']=stage.expected_heads;self._save(record)
            receipts=[]
            for system,(doc,reference,raw) in incoming.items():
                path=self.root/'raw'/(reference['sha256']+'.json')
                if not path.exists():path.write_bytes(raw)
                batch=stage.apply_indexed(path,parent_version_id=head['version_id'],changes=checked['changes'])
                receipts.append({k:batch[k] for k in ('system','batch_id','source_sha256','cursor','previous_batch_id','source_path','imported_at','as_of','mode')})
                record['sources'].append(dict(system=system,status='PREVIEWED',as_of=batch['as_of'],cursor=batch['cursor'],batches=1))
            receipt=validate_receipt(dict(enterprise_id=self.adapter.enterprise_id,as_of=point['as_of'],batches=receipts,
                trigger='USER_REQUESTED_CONNECTOR_SYNC',**({'source_scope':record['source_scope']} if record.get('source_scope') else {})),self.adapter.enterprise_id)
            task=self.imports.upload_continuity_changes(head['version_id'],source_as_of=point['as_of'],source_scope=record.get('source_scope'),
                declarations=declarations,changes=checked['changes'],receipt=receipt,validation=checked)
            record.update(status='READY',import_id=task['id'],revision=task['revision'],groups=task['canonical_groups'],readiness=task['readiness'],
                ingestion_stages=task.get('ingestion_stages'),sync_receipt=receipt,validation_metrics=checked['metrics'],
                source_fingerprint=digest({s:r[1]['sha256'] for s,r in incoming.items()}|{'config':self.adapter.fingerprint}))
            for change in checked['changes']:
                key={'ADDED':'added','UPDATED':'updated','UNCHANGED':'unchanged','DUPLICATE':'unchanged'}.get(change['classification'])
                if key:record['summary'][key]+=1
                if 'LATE_ARRIVAL' in change.get('flags',[]):record['summary']['late']+=1
            if not task['readiness']['data_accepted']:raise ValueError('P2_DATA_NOT_ACCEPTED')
        except (ValueError,KeyError,TypeError,OSError) as exc:
            record['status']='BLOCKED';record['anomalies'].append(anomaly(exc,system));record['summary']['conflicts']=len(record['anomalies'])
        self._save(record)
        return self._public(record),None

    def commit_sync(self,preview_id,*,revision,reviewer,groups):
        with self.lock:
            self._recover();record=self._record(preview_id)
            if record['status']=='COMMITTED':return self._public(record)
            if record['status'] not in {'READY','COMMITTING'}:raise ValueError('SYNC_PREVIEW_NOT_READY')
            if not isinstance(reviewer,str) or not reviewer.strip() or len(reviewer)>100 or not isinstance(groups,list) or sorted(groups)!=sorted(record['groups']):raise ValueError('CONFIRMATION_REQUIRED')
            if self.adapter.fingerprint!=record['configuration_fingerprint']:raise ValueError('CONNECTOR_CONFIGURATION_CHANGED')
            task=self.imports.get(record['import_id']);self.imports._expect(task,revision)
            if task.get('corrections'):raise ValueError('CONNECTOR_SOURCE_REVIEW_CHANGED')
            live_heads={s:self.sync.head(s)['batch_id'] for s in SYSTEMS if self.sync.head(s)}
            if live_heads!=record['expected_heads']:raise ValueError('SOURCE_HEAD_CONFLICT')
            self.adapter.verify_read_set(record['read_keys'])
            record.update(status='COMMITTING',reviewer=reviewer.strip());self._save(record)
            # Confirmation is always the existing P2 action, with the actual
            # caller's explicit reviewer/groups. No Agent confirmation shortcut.
            def save_version():
                confirmed=self.imports.confirm(task['id'],revision,reviewer.strip(),groups)
                saved=self.imports.save_version(task['id'],confirmed['revision'],parent_version_id=record['parent_version_id'],sync_receipt=record['sync_receipt'])
                return saved['version_id']
            publication=self.sync.stage(preview_id).publish_after(record['expected_heads'],save_version)
            return self._finish(record,publication['data_version_id'])


def configured_service(imports):
    path=os.environ.get('PLANNING_CONNECTOR_CONFIG_FILE')
    if not path:return None
    # Only an administrator-controlled environment path, never from HTTP input.
    from scripts.data_link_guard import safe_source
    source=safe_source(path)
    if source.suffix.lower()!='.json' or source.stat().st_size>1024*1024:raise ValueError('CONNECTOR_CONFIGURATION_INVALID')
    return ConnectorService(imports,DemoEnterpriseConnector(json.loads(source.read_text('utf-8-sig'))))


def configured_services(imports):
    """Explicit registry; each derived enterprise has independent source heads."""
    registry={}
    odoo_path=os.environ.get('PLANNING_ODOO_CONNECTOR_CONFIG_FILE')
    if odoo_path:
        from scripts.data_link_guard import safe_source
        from p21.odoo_connector import OdooPlanningSource,OdooConnectorService
        config=json.loads(safe_source(odoo_path).read_text('utf-8-sig'))
        adapter=OdooPlanningSource(config)
        registry[adapter.id]=OdooConnectorService(imports,adapter)
    replay=configured_service(imports)
    if replay is not None:registry['demo-garment']=replay
    path=os.environ.get('PLANNING_LIVE_CONNECTOR_CONFIG_FILE')
    if not path:return registry
    from scripts.data_link_guard import safe_source
    from p21.live_connector import LiveEnterpriseConnector
    source=safe_source(path)
    if source.suffix.lower()!='.json' or source.stat().st_size>1024*1024:raise ValueError('LIVE_CONNECTOR_CONFIGURATION_INVALID')
    value=json.loads(source.read_text('utf-8-sig'))
    if value.get('schema')!='live-source-connections/1' or not isinstance(value.get('connections'),list):raise ValueError('LIVE_CONNECTOR_CONFIGURATION_INVALID')
    for record in value['connections']:
        adapter=LiveEnterpriseConnector(record)
        if adapter.id in registry:raise ValueError('CONNECTOR_SCOPE_DUPLICATE')
        registry[adapter.id]=ConnectorService(imports,adapter,scope_id=adapter.id)
    return registry
