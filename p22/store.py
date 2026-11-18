"""Plan identity and immutable snapshots. Run states remain owned by RunStore."""
import copy
import json
import re
import sqlite3
import uuid
import hashlib
from pathlib import Path
from p22.contracts import validate_version,digest,encoded
from planning_adapter import InputError
from run_store import now

class PlanStore:
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/'plans.sqlite3'
        with self.db() as db:db.execute('CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY, version_id TEXT UNIQUE, version_hash TEXT, snapshot_hash TEXT, record TEXT)')

    def db(self):return sqlite3.connect(self.path,timeout=10)
    def folder(self,plan_id):
        if not re.fullmatch(r'PLAN-[a-f0-9]{16}',plan_id):raise InputError('PLAN_NOT_FOUND: 找不到所选企业计划')
        return self.root/'plans'/plan_id

    def create(self,version,projection=None):
        validate_version(version);vhash=digest(version)
        canonical=version['business_data'].get('schema_version')=='canonical-business-v1'
        if canonical:
            from p22.foundation_projection import validate,require
            require(projection is not None,'PROJECTION_REQUIRED')
            validate(projection['plan_input'])
            require(projection['plan_input']['foundation_projection']['source_version_id']==version['id'] and projection['plan_input']['foundation_projection']['canonical_semantic_sha256']==version['canonical']['semantic_sha256'],'PROJECTION_VERSION_MISMATCH')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT record,version_hash FROM plans WHERE version_id=?',(version['id'],)).fetchone()
            if row:
                if row[1]!=vhash:raise InputError('VERSION_CHANGED: 已保存数据版本被修改，不能覆盖旧计划')
                record=json.loads(row[0]);snap=self.snapshot(record['id'])
                if canonical and snap['plan_input']['foundation_projection']['projection_sha256']!=projection['plan_input']['foundation_projection']['projection_sha256']:raise InputError('PROJECTION_CHANGED: 为不同规划条件保存新派生版本，不覆盖旧计划')
                return record
            pid='PLAN-'+uuid.uuid4().hex[:16]
            data=copy.deepcopy(projection['plan_input'] if canonical else version['business_data']['plan_input'])
            data.update(snapshot_id='SNAP-'+pid,scenario_id=pid,scenario_revision=1)
            snap={'snapshot_schema':'p22-input-1','data_version':copy.deepcopy(version),'plan_input':data,'version_sha256':vhash,
                  'conversion':{'adapter_profile':projection['context']['adapter_profile'] if canonical else version['business_data']['adapter_profile'],'customer_mapping':'EXPLICIT_CONFIRMED','quantity_policy':'INDEPENDENT_SNAPSHOT_NO_ACCUMULATION'}}
            if canonical:snap['projection_context']=copy.deepcopy(projection['context']);snap['planning_readiness']=copy.deepcopy(projection['readiness'])
            record={'id':pid,'data_version_id':version['id'],'data_version_sha256':vhash,'snapshot_sha256':digest(snap),'created_at':now()}
            folder=self.folder(pid);folder.mkdir(parents=True)
            for filename,value in [('snapshot.json',snap),('canonical_input.json',data),('conversion-log.json',snap['conversion'])]:
                with (folder/filename).open('x',encoding='utf-8') as f:f.write(encoded(value))
            record=self._save_read_source(record,snap)
            db.execute('INSERT INTO plans VALUES(?,?,?,?,?)',(pid,version['id'],vhash,record['snapshot_sha256'],encoded(record)))
        return record

    def get(self,pid):
        self.folder(pid)
        with self.db() as db:row=db.execute('SELECT record FROM plans WHERE id=?',(pid,)).fetchone()
        if not row:raise InputError('PLAN_NOT_FOUND: 找不到所选企业计划')
        return json.loads(row[0])

    def list(self):
        with self.db() as db:return [json.loads(r[0]) for r in db.execute('SELECT record FROM plans ORDER BY rowid DESC')]

    def page(self,*,offset=0,limit=20):
        if type(offset) is not int or not 0<=offset<=10000 or type(limit) is not int or not 1<=limit<=20:
            raise InputError('QUERY_PAGE_BUDGET')
        with self.db() as db:
            rows=db.execute('SELECT record FROM plans ORDER BY rowid DESC LIMIT ? OFFSET ?',(limit+1,offset)).fetchall()
        more=len(rows)>limit;items=[json.loads(r[0]) for r in rows[:limit]]
        return dict(items=items,page=dict(offset=offset,limit=limit,returned=len(items),has_more=more,
            next_offset=offset+len(items) if more else None))

    def _save_read_source(self,record,snapshot):
        """A small derived context pinned by the original Plan record, not a new fact authority."""
        version=snapshot['data_version'];business=snapshot.get('projection_context',version['business_data'])
        context={k:copy.deepcopy(v) for k,v in business.items() if k not in {'plan_input','native_preview','lineage'}}
        context.update(plan_id=record['id'],data_version_id=record['data_version_id'],snapshot_sha256=record['snapshot_sha256'])
        metadata={k:copy.deepcopy(version[k]) for k in ('id','import_id','import_revision','filename','created_at',
            'planning_ready','engine_executed','version_mode','parent_version_id','source_sync_receipt') if k in version}
        metadata.update(enterprise_id=version.get('canonical',{}).get('enterprise_id'),
            canonical_semantic_sha256=version.get('canonical',{}).get('semantic_sha256'),
            version_sha256=record['data_version_sha256'],immutable_fingerprint=record['data_version_sha256'])
        value=dict(schema_version='plan-read-source/1',identity={k:record[k] for k in
            ('id','data_version_id','data_version_sha256','snapshot_sha256')},context=context,
            canonical_input_sha256=hashlib.sha256(encoded(snapshot['plan_input']).encode()).hexdigest(),version_metadata=metadata)
        from p22.catalog import catalog_context
        value['catalog']=catalog_context(snapshot)
        content=encoded(value);seal=hashlib.sha256(content.encode()).hexdigest();path=self.folder(record['id'])/('read-source-'+seal+'.json')
        try:
            with path.open('x',encoding='utf8',newline='') as stream:stream.write(content)
        except FileExistsError:
            if path.read_bytes()!=content.encode():raise InputError('READ_SOURCE_CHANGED') from None
        return dict(record,read_source_sha256=seal)

    def read_metadata(self,pid,*,record=None):
        """The existing sealed small projection only; no old-snapshot upgrade.

        A directory is allowed to lack old metadata. It must not silently turn
        a list read into a complete snapshot restore or a native archive audit.
        """
        record=self.get(pid) if record is None else record
        if record.get('id')!=pid:raise InputError('PLAN_IDENTITY_CHANGED')
        seal=record.get('read_source_sha256')
        if not seal:return None
        try:
            if not re.fullmatch(r'[a-f0-9]{64}',seal):raise ValueError()
            path=self.folder(pid)/('read-source-'+seal+'.json')
            if path.stat().st_size>2*1024*1024:raise ValueError()
            content=path.read_bytes()
            if hashlib.sha256(content).hexdigest()!=seal:raise ValueError()
            source=json.loads(content)
            if source['identity']!={k:record[k] for k in ('id','data_version_id','data_version_sha256','snapshot_sha256')}:raise ValueError()
            return source
        except (OSError,ValueError,KeyError,TypeError):raise InputError('READ_SOURCE_CHANGED: 计划读取投影与已确认版本不一致') from None

    def read_source(self,pid):
        """Restore the certified Plan input/context without constructing a full DATA snapshot.

        Old records are upgraded once through the original strict snapshot Owner.
        New Plan creation writes this derived metadata inside its existing transaction.
        """
        record=self.get(pid)
        if not record.get('read_source_sha256'):
            snapshot=self.snapshot(pid)
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                current=json.loads(db.execute('SELECT record FROM plans WHERE id=?',(pid,)).fetchone()[0])
                if not current.get('read_source_sha256'):
                    current=self._save_read_source(current,snapshot)
                    db.execute('UPDATE plans SET record=? WHERE id=?',(encoded(current),pid))
                record=current
        try:
            seal=record['read_source_sha256']
            if not re.fullmatch(r'[a-f0-9]{64}',seal):raise ValueError()
            content=(self.folder(pid)/('read-source-'+seal+'.json')).read_bytes()
            if hashlib.sha256(content).hexdigest()!=seal:raise ValueError()
            source=json.loads(content)
            if source['identity']!={k:record[k] for k in ('id','data_version_id','data_version_sha256','snapshot_sha256')}:raise ValueError()
            raw=(self.folder(pid)/'canonical_input.json').read_bytes()
            if hashlib.sha256(raw).hexdigest()!=source['canonical_input_sha256']:raise ValueError()
            source['plan_input']=json.loads(raw)
        except (OSError,ValueError,KeyError,TypeError):raise InputError('READ_SOURCE_CHANGED: 计划读取投影与已确认版本不一致') from None
        return source

    def snapshot(self,pid):
        record=self.get(pid)
        try:
            snap=json.loads((self.folder(pid)/'snapshot.json').read_text(encoding='utf-8'))
            canonical=json.loads((self.folder(pid)/'canonical_input.json').read_text(encoding='utf-8'))
            if digest(snap)!=record['snapshot_sha256'] or canonical!=snap['plan_input']:raise ValueError()
            if digest(snap['data_version'])!=record['data_version_sha256']:raise ValueError()
            validate_version(snap['data_version'])
            if 'foundation_projection' in snap['plan_input']:
                from p22.foundation_projection import validate,require
                validate(snap['plan_input'])
                require(snap['plan_input']['foundation_projection']['canonical_semantic_sha256']==snap['data_version']['canonical']['semantic_sha256'],'PROJECTION_VERSION_MISMATCH')
        except (OSError,ValueError,KeyError):raise InputError('SNAPSHOT_CHANGED: 输入快照校验失败，已停止使用') from None
        return snap
