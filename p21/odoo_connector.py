"""Bounded Odoo adapter using the existing Connector journal and P2 Owner.

There is one real ERP source, not fabricated ERP/MES/WMS continuity streams.
Versions remain original ImportStore versions; this is only a sync receipt.
"""
import copy
import json
import uuid
from pathlib import Path
from interop.odoo_adapter import OdooAdapter,OdooBusinessRejected
from interop.odoo_planning import build_snapshot, readiness
from p21.canonical import digest
from p21.connector_service import ConnectorService, now
from p21.source_sync import _write


class OdooPlanningSource:
    def __init__(self,config):
        from scripts.data_link_guard import safe_source
        self.id=config['id'];self.enterprise_id=config['enterprise_id']
        self.policy=json.loads(safe_source(config['professional_policy_file']).read_text('utf8'))
        self.sale_ids=config['sale_ids']
        self.product_ids=set(config['products'].values());self.bom_id=config['bom_id']
        self.scope=dict(company_id=config['company_id'],sale_ids=copy.deepcopy(self.sale_ids),product_ids=sorted(self.product_ids),bom_id=self.bom_id)
        self.client=OdooAdapter(**{k:config[k] for k in ['base_url','database','username','password','company_id']},enabled=config.get('enabled') is True)
        self.fingerprint=digest({k:v for k,v in config.items() if k!='password'} | {'policy_sha256':digest(self.policy)})

    def capture(self):
        c=self.client.capture_planning_facts(self.sale_ids)
        if c['enterprise_id']!=self.enterprise_id:raise ValueError('ODOO_ENTERPRISE_SCOPE_MISMATCH')
        if set(c['scope']['product_ids'])!=self.product_ids or {b['id'] for b in c['documents']['mrp.bom']}!={self.bom_id}:raise ValueError('ODOO_APPROVED_PRODUCT_SCOPE_MISMATCH')
        return c

    def test_connection(self):
        c=self.capture()
        return dict(status='CONNECTED',source='REAL_ODOO',company_id=c['company_id'],readiness=readiness(c,self.policy))


def source_revision(c):return digest({'documents':c['source_sha256'],'revisions':c['revisions']})


def capture_scope(capture):
    return copy.deepcopy(capture['scope'])|dict(company_id=capture['company_id'],
        warehouse_ids=[row['id'] for row in capture['documents'].get('stock.warehouse',[])])


def source_changes(previous,current):
    """Compare native identities and values, never treat a snapshot as events."""
    old=(previous or {}).get('documents',{});new=current['documents'];changes=[]
    summary=dict(added=0,updated=0,removed=0,unchanged=0,late=0,conflicts=0)
    for model in sorted(set(old)|set(new)):
        left={r['id']:r for r in old.get(model,[])};right={r['id']:r for r in new.get(model,[])}
        for identifier in sorted(set(left)|set(right)):
            before=left.get(identifier);after=right.get(identifier)
            if before==after:summary['unchanged']+=1;continue
            kind='ADDED' if before is None else 'REMOVED' if after is None else 'CHANGED'
            summary[{'ADDED':'added','REMOVED':'removed','CHANGED':'updated'}[kind]]+=1
            fields={key:dict(previous=(before or {}).get(key),current=(after or {}).get(key))
                    for key in sorted(set(before or {})|set(after or {})) if (before or {}).get(key)!=(after or {}).get(key)}
            changes.append(dict(model=model,id=identifier,classification=kind,fields=fields,
                previous_revision=(previous or {}).get('revisions',{}).get(model,{}).get(str(identifier)),
                current_revision=current.get('revisions',{}).get(model,{}).get(str(identifier)),
                source_known_at=current['captured_at']))
    return summary,changes


class OdooConnectorService(ConnectorService):
    def __init__(self,imports,adapter):
        super().__init__(imports,adapter,scope_id=adapter.id)

    def _public(self,record):
        if record['status']=='REUSED':
            return self._public(self._record(record['reused_sync_id']))|dict(request_id=record['id'])
        value=super()._public(record)
        for name in ('object_changes','source_scope','source_snapshot','professional_policy'):
            if name in record:value[name]=copy.deepcopy(record[name])
        return value

    def get_sync_record(self,identifier):
        with self.lock:
            self._recover()
            return self._public(self._record(identifier))

    def import_context(self,identifier):
        with self.lock:
            matches=[r for r in self._records() if r.get('import_id')==identifier]
            if not matches:return None
            record=matches[-1]
            return dict(connector_id=self.adapter.id,sync_id=record['id'],enterprise_id=self.adapter.enterprise_id,status=record['status'])

    def version_context(self,identifier):
        """Locate the confirmation that produced this DATA, not a recent request."""
        with self.lock:
            matches=[r for r in self._records() if r.get('version_id')==identifier and r['status']=='COMMITTED']
            if not matches:return None
            record=matches[-1]
            version=self.imports.version(identifier)
            if version['canonical']['enterprise_id']!=self.adapter.enterprise_id:raise ValueError('ODOO_CONNECTOR_SOURCE_IDENTITY_CHANGED')
            return dict(connector_id=self.adapter.id,sync_id=record['id'],enterprise_id=self.adapter.enterprise_id,
                version_id=identifier,source_as_of=record['as_of'],current_version_id=(self._head() or {}).get('version_id'))

    def _finish_odoo(self,record,version_id):
        head=self._head()
        if (head or {}).get('version_id') not in (record.get('parent_version_id'),version_id):raise ValueError('SOURCE_HEAD_CONFLICT')
        self.imports.verify_version_sources(self.imports.version(version_id))
        _write(self.root/'connector-head.json',dict(version_id=version_id,source_revision=record['source_revision'],configuration_fingerprint=record['configuration_fingerprint'],as_of=record['as_of'],sync_id=record['id']))
        record.update(status='COMMITTED',version_id=version_id,committed_at=now(),planning_ready=False)
        self._save(record);return self._public(record)

    def _recover(self):
        for record in self._records():
            if record['status']!='COMMITTING':continue
            task=self.imports.get(record['import_id'])
            if task.get('current_version_id'):
                version=self.imports.version(task['current_version_id'])
                if version['canonical']['semantic_sha256']!=record['canonical_semantic_sha256']:raise ValueError('SOURCE_RECOVERY_IDENTITY_MISMATCH')
                self._finish_odoo(record,version['id'])

    def get_sync_status(self):
        with self.lock:
            self._recover();head=self._head();records=[r for r in self._records() if r['status']!='REUSED']
            def brief(record):return {k:v for k,v in self._public(record).items() if k!='object_changes'}
            last=brief(self._record(head['sync_id'])) if head else None
            pending=[r for r in records if r['status'] not in ('COMMITTED','UNCHANGED')]
            connection=self.root/'connection-test.json'
            return dict(id=self.adapter.id,name='Odoo 18 · 制造与库存',enterprise_id=self.adapter.enterprise_id,
                configured=True,status='SYNCED' if head else 'NOT_SYNCED',current_version_id=(head or {}).get('version_id'),
                company_id=self.adapter.policy.get('company_id'),
                configured_scope=copy.deepcopy(getattr(self.adapter,'scope',{})),
                connection=json.loads(connection.read_text('utf8')) if connection.exists() else dict(status='NOT_CHECKED',checked_at=None),
                last_sync=last,preview=brief(pending[-1]) if pending else None,
                last_read_at=next((r['as_of'] for r in reversed(records) if r.get('as_of')),None),
                readiness=(last or {}).get('readiness',dict(ready=False,status='NOT_CONFIRMED',blocking_issues=[])),
                professional_policy=dict(id=self.adapter.policy['policy_id'],status=self.adapter.policy['status'],source='APPROVED_INDEPENDENT_POLICY_NOT_ERP'),
                source_kind='REAL_ODOO',checkpoints=[dict(id='CURRENT',name='当前来源')],
                source_scope=next((r['source_scope'] for r in reversed(records) if r.get('source_scope')),None),
                history=[brief(r) for r in records[-30:]][::-1],scope='ISOLATED_TEST_COMPANY_ONLY')

    def test_connection(self):
        try:
            result=self.adapter.test_connection()|dict(checked_at=now())
        except OdooBusinessRejected as exc:
            result=dict(status='REJECTED',checked_at=now(),code=exc.code)
        except (OSError,ValueError):
            result=dict(status='UNAVAILABLE',checked_at=now(),code='ODOO_CONNECTION_UNAVAILABLE')
        _write(self.root/'connection-test.json',result)
        return result

    def _previous_capture(self,head):
        if not head:return None
        return self.imports.version(head['version_id'])['canonical']['tables']['locations'][0]['odoo_bundle']['capture']

    def _check_source_identity(self,capture,head):
        if not head:return
        version=self.imports.version(head['version_id']);self.imports.verify_version_sources(version)
        old=version['canonical']['tables']['locations'][0]['odoo_bundle']['capture']
        if any(old[k]!=capture[k] for k in ('database','company_id','enterprise_id')):
            raise ValueError('ODOO_CONNECTOR_SOURCE_IDENTITY_CHANGED')

    def preview_sync(self,checkpoint,*,request_id=None):
        if checkpoint!='CURRENT':raise ValueError('ODOO_CURRENT_SCOPE_ONLY')
        with self.lock:
            self._recover()
            identifier=request_id or 'SYNC-'+uuid.uuid4().hex
            path=self._path(identifier)
            if path.exists():return self._public(self._record(identifier))
            started=now()
            pending=dict(id=identifier,status='READING',checkpoint=checkpoint,created_at=started,
                enterprise_id=self.adapter.enterprise_id,groups=[],sources=[],anomalies=[],
                readiness=dict(ready=False,blocking_issues=['SOURCE_NOT_CAPTURED']),import_id=None,
                summary=dict(added=None,updated=None,removed=None,unchanged=None,late=None,conflicts=None))
            self._save(pending)
            try:capture=self.adapter.capture()
            except OSError:
                pending.update(status='BLOCKED',anomalies=[dict(code='ODOO_SOURCE_UNAVAILABLE')])
                self._save(pending);return self._public(pending)
            try:return self._preview_captured(checkpoint,capture,identifier,started)
            except (ValueError,OSError,KeyError,TypeError) as exc:
                code=str(exc).split(':',1)[0]
                if not code or not all(c.isupper() or c.isdigit() or c=='_' for c in code):code='ODOO_SOURCE_MAPPING_OR_SAVE_FAILED'
                record=self._record(identifier)
                record.update(status='BLOCKED',anomalies=[dict(code=code)])
                self._save(record)
                raise

    def _preview_captured(self,checkpoint,capture,identifier,started):
        rev=source_revision(capture);head=self._head();self._check_source_identity(capture,head)
        if head and head['source_revision']==rev and head['configuration_fingerprint']==self.adapter.fingerprint:
            record=copy.deepcopy(self._record(head['sync_id']))
            record.update(id=identifier,created_at=started,status='UNCHANGED',object_changes=[],
                parent_version_id=head['version_id'],source_scope=capture_scope(capture),
                as_of=capture['captured_at'],summary=dict(added=0,updated=0,removed=0,unchanged=sum(map(len,capture['documents'].values())),late=0,conflicts=0))
            record['source_snapshot']=dict(sha256=capture['source_sha256'],captured_at=capture['captured_at'],transaction=capture['transaction'])
            record['sources']=[dict(system='ODOO',status='CAPTURED',as_of=capture['captured_at'])]
            self._save(record);return self._public(record)
        for old in reversed(self._records()):
            if old.get('source_revision')==rev and old.get('parent_version_id')==(head or {}).get('version_id') and old['status']=='READY' and old['configuration_fingerprint']==self.adapter.fingerprint:
                self._save(dict(id=identifier,created_at=started,status='REUSED',reused_sync_id=old['id']))
                return self._public(old)|dict(request_id=identifier)
        summary,changes=source_changes(self._previous_capture(head),capture)
        ready=readiness(capture,self.adapter.policy)
        task=None
        record=dict(id=identifier,created_at=started,status='READY',checkpoint=checkpoint,
            enterprise_id=self.adapter.enterprise_id,as_of=capture['captured_at'],source_revision=rev,
            parent_version_id=(head or {}).get('version_id'),configuration_fingerprint=self.adapter.fingerprint,
            import_id=task['id'] if task else None,revision=task['revision'] if task else None,groups=task['canonical_groups'] if task else [],
            canonical_semantic_sha256=task['canonical']['semantic_sha256'] if task else None,
            readiness=ready,sources=[dict(system='ODOO',status='CAPTURED',as_of=capture['captured_at'])],
            summary=summary,object_changes=changes,
            source_scope=capture_scope(capture),
            source_snapshot=dict(sha256=capture['source_sha256'],captured_at=capture['captured_at'],transaction=capture['transaction']),
            professional_policy=dict(id=self.adapter.policy['policy_id'],status=self.adapter.policy['status'],source='APPROVED_INDEPENDENT_POLICY_NOT_ERP'),
            anomalies=[dict(code=code) for code in ready['blocking_issues']],
            ingestion_mode='SCOPED_ERP_DOMAIN_REVALIDATION')
        # Preserve the received native evidence before a P2 mapping/save
        # can fail. Receiving facts never confirms a Canonical version.
        record['status']='READING' if ready['ready'] else 'BLOCKED'
        self._save(record)
        if ready['ready']:
            task=self.imports.upload_structured(build_snapshot(capture,self.adapter.policy))
            record.update(status='READY',import_id=task['id'],revision=task['revision'],groups=task['canonical_groups'],
                canonical_semantic_sha256=task['canonical']['semantic_sha256'])
            self._save(record)
        return self._public(record)

    def commit_sync(self,preview_id,*,revision,reviewer,groups,authorization=None):
        with self.lock:
            self._recover();record=self._record(preview_id)
            if record['status']=='COMMITTED':return self._public(record)
            if record['status'] not in ('READY','COMMITTING'):raise ValueError('SYNC_PREVIEW_NOT_READY')
            if not reviewer.strip() or sorted(groups)!=sorted(record['groups']):raise ValueError('CONFIRMATION_REQUIRED')
            if self.adapter.fingerprint!=record['configuration_fingerprint']:raise ValueError('CONNECTOR_CONFIGURATION_CHANGED')
            if (self._head() or {}).get('version_id')!=record['parent_version_id']:raise ValueError('SOURCE_HEAD_CONFLICT')
            current=self.adapter.capture();self._check_source_identity(current,self._head())
            if source_revision(current)!=record['source_revision']:raise ValueError('SOURCE_REVISION_CONFLICT')
            task=self.imports.get(record['import_id']);self.imports._expect(task,revision)
            if task.get('corrections') or task['canonical']['semantic_sha256']!=record['canonical_semantic_sha256']:raise ValueError('CONNECTOR_SOURCE_REVIEW_CHANGED')
            record.update(status='COMMITTING',reviewer=reviewer)
            if authorization:record['reviewer_identity']=copy.deepcopy(authorization)
            self._save(record)
            task=self.imports.confirm(task['id'],revision,reviewer,groups)
            saved=self.imports.save_version(task['id'],task['revision'])
            return self._finish_odoo(record,saved['version_id'])
