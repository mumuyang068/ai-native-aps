"""A durable command outbox, not a second order or inventory database.

The external Owner must atomically deduplicate command and suggestion identity.
SQLite here protects only local intent/receipts; it is no remote transaction.
"""
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
from datetime import datetime, timezone


def encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def digest(value):return hashlib.sha256(encoded(value).encode()).hexdigest()
def now():return datetime.now(timezone.utc).isoformat()


class ExternalBusinessRejected(Exception):
    """An explicit synchronous remote refusal, not a lost/unknown result."""
    def __init__(self,code,detail):
        self.code,self.detail=code,detail
        super().__init__(code+':'+detail)


class PublicationService:
    def __init__(self,root,targets,preview):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/'publication.sqlite3';self.targets=targets;self.build_preview=preview
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, semantic_key TEXT UNIQUE NOT NULL, record TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS transitions (id INTEGER PRIMARY KEY, command_id TEXT NOT NULL, at TEXT NOT NULL, state TEXT NOT NULL, detail TEXT NOT NULL)')

    def _db(self):return sqlite3.connect(self.path,timeout=10)

    def _target(self,key):
        target=self.targets.get(key)
        if not target or target.get('scope')!='ISOLATED_TEST_COMPANY_ONLY':raise ValueError('PUBLICATION_TARGET_DISABLED')
        return target

    def preview(self,target_id,selection):
        target=self._target(target_id)
        command=self.build_preview(target_id,copy.deepcopy(selection))
        if any(command.get(k)!=target.get(k) for k in ('enterprise_id','branch_id')) or command.get('test_company_id')!=target.get('company_id'):
            raise ValueError('PUBLICATION_TARGET_SCOPE_MISMATCH')
        if command.get('action_type') not in target.get('actions',[]):raise ValueError('PUBLICATION_ACTION_UNSUPPORTED')
        for field in ('enterprise_id','branch_id','data_version_id','plan_id','run_id','suggestion_id'):
            if not isinstance(command.get(field),str) or not command[field] or len(command[field])>512:raise ValueError('PUBLICATION_IDENTITY_REQUIRED')
        if not isinstance(command.get('payload'),dict):raise ValueError('PUBLICATION_PAYLOAD_REQUIRED')
        command['schema']='aps-publication/1'
        return dict(target_id=target_id,selection=copy.deepcopy(selection),command=command,
            fingerprint=digest(dict(target_id=target_id,selection=selection,command=command)),scope=target['scope'])

    def get(self,command_id):
        with self._db() as db:row=db.execute('SELECT record FROM commands WHERE id=?',(command_id,)).fetchone()
        if row is None:raise ValueError('PUBLICATION_NOT_FOUND')
        return json.loads(row[0])

    def list(self,plan_id):
        with self._db() as db:rows=db.execute('SELECT record FROM commands ORDER BY rowid DESC').fetchall()
        return [r for row in rows if (r:=json.loads(row[0]))['command']['plan_id']==plan_id]

    def observe(self,command_id,*,plan_id):
        record=self.get(command_id)
        if record['command']['plan_id']!=plan_id:raise ValueError('PUBLICATION_PLAN_MISMATCH')
        target=self._target(record['target_id'])
        if target.get('kind')!='ODOO':raise ValueError('PROFESSIONAL_OBSERVATION_UNSUPPORTED')
        receipt=target['adapter'].lookup(command_id)
        if not receipt or receipt['request_hash']!=record['command']['request_hash'] or receipt.get('state')!='ACCEPTED':
            raise ValueError('ACCEPTED_PUBLICATION_RECEIPT_REQUIRED')
        external=receipt['external_document']
        snapshot=target['adapter'].inspect_manufacturing(external['id'])
        if snapshot['production']['id']!=external['id'] or snapshot['production']['company_id']!=external['company_id']:
            raise ValueError('PUBLICATION_EXTERNAL_IDENTITY_MISMATCH')
        requested=record['command'].get('requested_schedule')
        if not requested:raise ValueError('PROFESSIONAL_MAPPING_NOT_AVAILABLE')
        from interop.odoo_source import assess_professional_schedule
        accepted=receipt.get('professional_snapshot')
        if accepted and (accepted['production']['id']!=external['id'] or accepted['production']['company_id']!=external['company_id']):
            raise ValueError('PUBLICATION_ACCEPTANCE_IDENTITY_MISMATCH')
        return dict(command_id=command_id,plan_id=plan_id,data_version_id=record['command']['data_version_id'],
            run_id=record['command']['run_id'],observed_at=now(),snapshot=snapshot,
            accepted_assessment=assess_professional_schedule(requested,accepted) if accepted else None,
            accepted_version=accepted.get('version') if accepted else None,
            assessment=assess_professional_schedule(requested,snapshot),effects='READ_ONLY_NO_EXECUTION_NO_WORKER')

    def _save(self,record,state,detail):
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            current=json.loads(db.execute('SELECT record FROM commands WHERE id=?',(record['command_id'],)).fetchone()[0])
            # Concurrent recovery must not regress an accepted receipt to a
            # transient error, or silently replace its external identity.
            old=current.get('receipt');incoming=record.get('receipt')
            if old and incoming and old.get('external_document_id')!=incoming.get('external_document_id'):
                record['receipt']=old;state='RECONCILIATION_REQUIRED'
                detail=dict(code='CONCURRENT_EXTERNAL_IDENTITY_CONFLICT',observed=incoming)
            elif old and not incoming:record['receipt']=old
            if state=='UNKNOWN' and current['state'] in ('ACCEPTED','REJECTED','EXECUTED','PARTIALLY_EXECUTED','RECONCILIATION_REQUIRED'):
                return current
            ranks={'SUBMITTED':0,'UNKNOWN':0,'ACCEPTED':1,'PARTIALLY_EXECUTED':2,'EXECUTED':3}
            if state in ranks and (current['state'] in ('REJECTED','RECONCILIATION_REQUIRED') or
                ranks.get(current['state'],-1)>ranks[state]):return current
            if state=='REJECTED' and old and old.get('state')!='REJECTED':
                record['receipt']=old;state='RECONCILIATION_REQUIRED';detail=dict(code='EXTERNAL_ACCEPT_REJECT_CONFLICT',observed=incoming)
            record['state']=state;record['updated_at']=now()
            db.execute('UPDATE commands SET record=? WHERE id=?',(encoded(record),record['command_id']))
            db.execute('INSERT INTO transitions(command_id,at,state,detail) VALUES(?,?,?,?)',
                (record['command_id'],record['updated_at'],state,encoded(detail)))
        return record

    def submit(self,target_id,selection,fingerprint,*,request_id,confirmed,principal):
        target=self._target(target_id)
        if confirmed is not True or not principal or not principal.get('subject'):
            raise ValueError('TEST_COMPANY_CONFIRMATION_REQUIRED')
        if not isinstance(request_id,str) or not 1<=len(request_id)<=200:raise ValueError('PUBLICATION_REQUEST_ID_REQUIRED')
        cid='PUB-'+digest(dict(target=target_id,actor=principal['subject'],request=request_id))[:32]
        try:existing=self.get(cid)
        except ValueError as exc:
            if str(exc)!='PUBLICATION_NOT_FOUND':raise
            existing=None
        if existing:
            if existing['selection']!=selection or existing['fingerprint']!=fingerprint:raise ValueError('IDEMPOTENCY_KEY_CONFLICT')
            return existing if existing['state'] not in ('SUBMITTED','UNKNOWN') else self.reconcile(cid)
        preview=self.preview(target_id,selection)
        if fingerprint!=preview['fingerprint']:raise ValueError('PUBLICATION_PREVIEW_CHANGED')
        command=preview['command'];command['command_id']=cid
        command['operator_confirmation']=dict(scope='ISOLATED_TEST_COMPANY_ONLY',confirmed=True,
            actor_id=principal['subject'],record_id=cid,session_id=principal.get('session_id'),authentication=principal.get('authentication'))
        command['request_hash']=digest(command)
        semantic=digest([target_id,*[command[k] for k in ('enterprise_id','branch_id','data_version_id','run_id','suggestion_id','action_type')]])
        record=dict(command_id=cid,command=command,target_id=target_id,selection=copy.deepcopy(selection),
            fingerprint=fingerprint,state='SUBMITTED',created_at=now(),updated_at=now(),receipt=None)
        try:
            with self._db() as db:
                db.execute('INSERT INTO commands VALUES(?,?,?)',(cid,semantic,encoded(record)))
                db.execute('INSERT INTO transitions(command_id,at,state,detail) VALUES(?,?,?,?)',(cid,now(),'SUBMITTED','{}'))
        except sqlite3.IntegrityError:
            # Another process may have inserted this exact command after the
            # read. Recover it; a different command for one suggestion is denied.
            try:other=self.get(cid)
            except ValueError:raise ValueError('SUGGESTION_ALREADY_SUBMITTED') from None
            if other['command']!=command:raise ValueError('IDEMPOTENCY_KEY_CONFLICT')
            return self.reconcile(cid)
        return self._send(record,target)

    def _receipt(self,record,receipt):
        valid=(isinstance(receipt,dict) and receipt.get('command_id')==record['command_id'] and
            receipt.get('request_hash')==record['command']['request_hash'] and
            receipt.get('state') in ('ACCEPTED','REJECTED','EXECUTED','PARTIALLY_EXECUTED'))
        prior=record.get('receipt')
        if valid and receipt['state']!='REJECTED':
            valid=bool(receipt.get('external_document_id'))
        if prior and (not isinstance(receipt,dict) or receipt.get('external_document_id')!=prior.get('external_document_id')):valid=False
        if not valid:return self._save(record,'RECONCILIATION_REQUIRED',dict(code='EXTERNAL_RECEIPT_IDENTITY_CONFLICT',observed=receipt))
        record['receipt']=copy.deepcopy(receipt)
        return self._save(record,receipt['state'],dict(receipt=receipt))

    def _send(self,record,target):
        try:receipt=target['adapter'].send(copy.deepcopy(record['command']))
        except ExternalBusinessRejected as exc:
            receipt=dict(command_id=record['command_id'],request_hash=record['command']['request_hash'],
                state='REJECTED',code=exc.code,authority='EXPLICIT_TARGET_ERROR_NO_DURABLE_RECEIPT')
        except (ConnectionError,TimeoutError):
            return self._save(record,'UNKNOWN',dict(code='EXTERNAL_STATUS_UNKNOWN'))
        # Explicit target business refusals use a receipt. Unexpected local
        # coding/contract errors propagate; they are not certified as rejection.
        return self._receipt(record,receipt)

    def reconcile(self,command_id,*,retry=False):
        record=self.get(command_id);target=self._target(record['target_id'])
        try:receipt=target['adapter'].lookup(command_id)
        except ExternalBusinessRejected as exc:
            # A refusal to read cannot establish whether the earlier write
            # committed. Preserve the original state/identity for recovery.
            return self._save(record,'UNKNOWN',dict(code=exc.code,phase='LOOKUP_REFUSED'))
        except (ConnectionError,TimeoutError):return self._save(record,'UNKNOWN',dict(code='EXTERNAL_STATUS_UNKNOWN'))
        if receipt is not None:return self._receipt(record,receipt)
        if record['state']=='REJECTED':return record
        if record.get('receipt'):return self._save(record,'RECONCILIATION_REQUIRED',dict(code='EXTERNAL_RECEIPT_DISAPPEARED'))
        # Resubmit only the same durably confirmed command to an idempotent
        # external Owner. Never allocate a replacement command after a timeout.
        if retry:return self._send(record,target)
        return record
