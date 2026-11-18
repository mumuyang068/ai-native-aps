"""Thin, source-visible reader. Business mapping stays with continuity_sources/P2.

Configuration is private server configuration, never an API-supplied path. Only
explicit JSON carriers are readable; World Truth is not part of this contract.
"""
import copy
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Protocol

from scripts.data_link_guard import safe_source, SourceGuard
from p22.continuity_sources import ENTERPRISE, PROFILE

SYSTEMS = ('ERP', 'WMS', 'MES')
TABLES = {'ERP': {'purchase_orders','purchase_receipts','sales_orders','shipments'},
          'WMS': {'finished_goods','lots','movements','quality_events','receipts'},
          'MES': {'manufacturing_tasks','material_issues','operations','resource_unavailability'}}


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def instant(value):
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.utcoffset() is None:raise ValueError('SOURCE_TIMEZONE_REQUIRED')
    return result


def validate_source_document(d,system,cutoff,mode):
    """One visible-source schema contract shared by replay and live readers."""
    if not isinstance(d,dict) or d.get('schema')!='source-visibility/1':raise ValueError('SOURCE_SCHEMA_CHANGED:'+system)
    if d.get('system')!=system or d.get('enterprise_id')!=ENTERPRISE:raise ValueError('SOURCE_IDENTITY_MISMATCH:'+system)
    if d.get('mode')!=mode or instant(d['as_of'])>instant(cutoff):raise ValueError('SOURCE_SCOPE_MISMATCH:'+system)
    if not isinstance(d.get('tables'),dict) or set(d['tables'])-TABLES[system]:raise ValueError('SOURCE_TABLE_SCHEMA_CHANGED:'+system)
    required=TABLES[system]-({'resource_unavailability'} if system=='MES' else set())
    if mode=='FULL_SNAPSHOT' and not required<=set(d['tables']):raise ValueError('SOURCE_TABLE_SCHEMA_CHANGED:'+system)
    for table,rows in d['tables'].items():
        if not isinstance(rows,list):raise ValueError('SOURCE_TABLE_SCHEMA_CHANGED:'+system+':'+table)
        for row in rows:
            if not isinstance(row,dict) or not {'id','source_known_at','business_occurred_at'}<=set(row):raise ValueError('SOURCE_ROW_SCHEMA_CHANGED:'+system+':'+table)
            if instant(row['source_known_at'])>instant(d['as_of']) or instant(row['business_occurred_at'])>instant(row['source_known_at']):
                raise ValueError('SOURCE_VISIBILITY_TIME_CONFLICT:'+system+':'+str(row['id']))
    return d


class ConnectorAdapter(Protocol):
    """Future readers change transport/mapping, not Canonical or Worker."""
    enterprise_id:str
    fingerprint:str
    def describe_source(self) -> dict: ...
    def test_connection(self) -> dict: ...
    def checkpoint(self, checkpoint:str) -> dict: ...
    def read_snapshot(self, system:str, checkpoint:str) -> tuple: ...
    def read_changes(self, system:str, checkpoint:str, previous_as_of:str) -> list: ...
    def declaration_keys(self) -> list[str]: ...
    def verify_read_set(self, keys:list[str]) -> None: ...
    def build_snapshot(self, documents:dict) -> dict: ...


class DemoEnterpriseConnector:
    def __init__(self, configuration):
        c=copy.deepcopy(configuration)
        if c.get('schema')!='demo-enterprise-connector/1':raise ValueError('CONNECTOR_CONFIGURATION_SCHEMA')
        if c.get('enterprise_id')!=ENTERPRISE:raise ValueError('CONNECTOR_ENTERPRISE_IDENTITY_MISMATCH')
        if c.get('id')!='demo-garment' or c.get('mapping_profile')!=PROFILE:raise ValueError('CONNECTOR_MAPPING_PROFILE_UNSUPPORTED')
        if set(c.get('declarations',{}))!={'definition','operating','reference'}:raise ValueError('CONNECTOR_DECLARATIONS_REQUIRED')
        if not c.get('checkpoints') or set(c.get('changes',{}))!=set(SYSTEMS):raise ValueError('CONNECTOR_CHECKPOINTS_REQUIRED')
        for point, record in c['checkpoints'].items():
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',point) or set(record.get('snapshots',{}))!=set(SYSTEMS):raise ValueError('CONNECTOR_CHECKPOINT_INVALID')
            instant(record['as_of'])
        for key, record in c['files'].items():
            p=safe_source(record['path'])  # Deny SQL/links before stat or content access.
            if p.suffix.lower()!='.json' or not re.fullmatch('[a-f0-9]{64}',record['sha256']):raise ValueError('CONNECTOR_SOURCE_CARRIER_INVALID')
        for system, keys in c['changes'].items():
            if keys!=sorted(set(keys)) or any(not re.fullmatch(r'\d{4}-\d{2}-\d{2}/'+system+'/incremental',key) or key not in c['files'] for key in keys):
                raise ValueError('CONNECTOR_INCREMENTAL_MANIFEST_INVALID')
        self.configuration=c
        self.enterprise_id=ENTERPRISE
        self.fingerprint=digest(c)
        self.guard=SourceGuard([r['path'] for r in c['files'].values()],Path(next(iter(c['files'].values()))['path']).parent/'unused-copy-target')

    def describe_source(self):
        c=self.configuration
        return dict(id=c['id'],name=c['name'],enterprise_id=ENTERPRISE,mapping_profile=PROFILE,
            source_schema='source-visibility/1',transport='EXPLICIT_LOCAL_VISIBLE_JSON',
            modes=['FULL_SNAPSHOT','INCREMENTAL_UPSERT'],cursor_strategy='PER_SYSTEM_AS_OF_AND_IMMUTABLE_BATCH_REVISION',
            systems=[dict(id=s,name=s,source_id=ENTERPRISE+'/'+s,mapping_profile=PROFILE+'/'+s) for s in SYSTEMS],
            checkpoints=[dict(id=k,as_of=v['as_of']) for k,v in c['checkpoints'].items()],
            automatic_sync=False,automatic_solve=False,execution_authorized=False)

    def _read(self, key):
        record=self.configuration['files'].get(key)
        if not record:raise ValueError('SOURCE_MANIFEST_MEMBER_REQUIRED')
        try:
            path=self.guard.source(record['path'])
            if path.stat().st_size>10*1024*1024:raise ValueError('SOURCE_TOO_LARGE')
            raw=self.guard.read_bytes(path)
        except (OSError,PermissionError) as exc:
            # Public errors identify the logical member, never credentials/host paths.
            raise ValueError('SOURCE_UNAVAILABLE:'+key) from exc
        if hashlib.sha256(raw).hexdigest()!=record['sha256']:raise ValueError('SOURCE_CHANGED:'+key)
        try:document=json.loads(raw.decode('utf-8-sig'))
        except (ValueError,UnicodeError):raise ValueError('SOURCE_JSON_INVALID:'+key) from None
        return document,dict(key=key,path=str(path),sha256=record['sha256']),raw

    def _source(self,key,system,cutoff,mode):
        d,r,raw=self._read(key)
        validate_source_document(d,system,cutoff,mode)
        return d,r,raw

    def checkpoint(self,point):
        if point not in self.configuration['checkpoints']:raise ValueError('CONNECTOR_CHECKPOINT_NOT_FOUND')
        return self.configuration['checkpoints'][point]

    def read_snapshot(self,system,checkpoint):
        if system not in SYSTEMS:raise ValueError('SOURCE_IDENTITY_MISMATCH')
        point=self.checkpoint(checkpoint)
        value=self._source(point['snapshots'][system],system,point['as_of'],'FULL_SNAPSHOT')
        if value[0]['as_of']!=point['as_of']:raise ValueError('SOURCE_CHECKPOINT_MISMATCH:'+system)
        return value

    def read_changes(self,system,checkpoint,previous_as_of):
        if system not in SYSTEMS:raise ValueError('SOURCE_IDENTITY_MISMATCH')
        point=self.checkpoint(checkpoint);before=instant(previous_as_of);until=instant(point['as_of'])
        if before>until:raise ValueError('KNOWLEDGE_CLOCK_REGRESSION')
        # Manifest dates filter BEFORE opening any member. Future sources are never read.
        keys=[k for k in self.configuration['changes'][system] if before.date().isoformat()<k[:10]<=until.date().isoformat()]
        result=[];last=previous_as_of
        for key in keys:
            value=self._source(key,system,point['as_of'],'INCREMENTAL_UPSERT');doc=value[0]
            if doc.get('previous_as_of')!=last:raise ValueError('INCREMENTAL_CURSOR_GAP:'+system)
            result.append(value);last=doc['as_of']
        if before<until and last!=point['as_of']:raise ValueError('INCREMENTAL_CURSOR_GAP:'+system)
        return result

    def declarations(self):
        return {name:self._read(key)[0] for name,key in self.configuration['declarations'].items()}

    def declaration_keys(self):
        return list(self.configuration['declarations'].values())

    def verify_read_set(self,keys):
        for key in keys:self._read(key)

    def build_snapshot(self,documents):
        from p22.continuity_sources import build_snapshot
        return build_snapshot(**self.declarations(),documents=documents)

    def test_connection(self):
        errors=[];systems=[];first=min(self.configuration['checkpoints'],key=lambda k:instant(self.checkpoint(k)['as_of']))
        for system in SYSTEMS:
            try:self.read_snapshot(system,first);systems.append(dict(system=system,status='CONNECTED'))
            except (ValueError,KeyError,TypeError) as exc:
                code=str(exc).split(':')[0];errors.append(dict(system=system,code=code));systems.append(dict(system=system,status='UNAVAILABLE',code=code))
        try:self.declarations()
        except (ValueError,KeyError,TypeError) as exc:errors.append(dict(system='DECLARATION',code=str(exc).split(':')[0]))
        return dict(status='CONNECTED' if not errors else 'UNAVAILABLE',systems=systems,errors=errors)
