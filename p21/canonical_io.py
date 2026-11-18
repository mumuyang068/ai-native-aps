"""File carriers for the shared typed contract, using the existing P2 XLSX reader."""
import copy
import csv
import hashlib
import io
import json
import os
import uuid
from pathlib import Path

from p2.preflight import load_workbook, column
from p21.canonical import DOMAIN, assemble, digest

META_SHEET='业务快照'
MANIFEST='business-source.json'


def archive_original(originals,raw):
    """Publish complete immutable bytes, with no exists/create race.

    A same-directory hard-link is an atomic, no-overwrite publication on the
    supported local filesystems. Competing receivers validate the winning
    complete original; an existing mismatched archive is never repaired here.
    """
    sha=hashlib.sha256(raw).hexdigest();target=originals/(sha+'.json')
    temporary=originals/(sha+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('xb') as output:
            output.write(raw);output.flush();os.fsync(output.fileno())
        try:os.link(temporary,target)
        except FileExistsError:
            if hashlib.sha256(target.read_bytes()).hexdigest()!=sha:raise ValueError('SOURCE_CHANGED: 已归档原件不一致')
    finally:
        temporary.unlink(missing_ok=True)
    return sha


def structured_content(snapshot, originals, *, raw=None,parent=None):
    """JSON is an original carrier of the SAME typed tables, not a new model.

    Only input fields are accepted. Confirmation, Canonical objects, local file
    paths and row-evidence hashes are always produced by the existing Owners.
    """
    fields={'enterprise_id','tables','planning_at','horizon','timezone'}
    if not isinstance(snapshot,dict) or not fields<=set(snapshot) or set(snapshot)-fields-{'mapping','mode','parent_version_id'}:
        raise ValueError('STRUCTURED_INPUT_FIELDS')
    tables=snapshot['tables']
    if not isinstance(tables,dict) or not tables or len(tables)>len(DOMAIN):
        raise ValueError('INVALID_DOMAIN_MAPPING')
    if any(not isinstance(rows,list) or len(rows)>20000 or any(not isinstance(r,dict) for r in rows) for rows in tables.values()):
        raise ValueError('INVALID_ENTITY_TABLE')
    if raw is None:raw=json.dumps(snapshot,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')
    elif not isinstance(raw,bytes) or json.loads(raw)!=snapshot:raise ValueError('STRUCTURED_ORIGINAL_MISMATCH')
    if len(raw)>48*1024*1024:raise ValueError('SOURCE_PACKAGE_TOO_LARGE')
    sha=archive_original(originals,raw)
    from p21.record_mapping import project,enterprise_identity
    delta=snapshot.get('mode')=='INCREMENTAL_UPSERT'
    if 'mode' in snapshot and not delta:raise ValueError('DELTA_MODE_UNSUPPORTED')
    if not delta and 'parent_version_id' in snapshot:raise ValueError('DELTA_MODE_REQUIRED')
    if delta:
        from p21.structured_delta import prepare_delta
        prepared=prepare_delta(snapshot,parent,sha=sha)
        tables,refs,mapping_issues,mapping=(prepared[k] for k in ('tables','source_refs','mapping_issues','mapping'))
        metrics=prepared['metrics']
    else:
        tables,refs,mapping_issues,mapping=project(tables,snapshot.get('mapping'),sha)
        metrics={'mapped_records':sum(map(len,tables.values())),'reused_records':0,'csv_generated':0,'csv_parsed':0}
    canonical=assemble(enterprise_identity(snapshot['enterprise_id'],mapping),tables,planning_at=snapshot['planning_at'],horizon=snapshot['horizon'],
        timezone=snapshot['timezone'],source_refs=refs)
    if mapping_issues:
        canonical['issues'].extend(mapping_issues);canonical['data_status']='DATA_BLOCKED'
    source_files=copy.deepcopy(parent['business_data']['source_files']) if delta else []
    source_files.append(dict(filename='business-records.json',sha256=sha,extension='.json',source_kind='STRUCTURED_DELTA' if delta else 'STRUCTURED_SOURCE'))
    result=dict(canonical=canonical,source={'sheets':{}},source_files=source_files,file_sha256=digest([('business-records.json',sha)]),
        filename='business-records.json',input_transport='STRUCTURED_JSON',
        ingestion={'contract':'canonical-business-v1','mapping':copy.deepcopy(mapping),
            'mapping_sha256':digest(mapping),'rules_revision':'canonical-business-v1',
            'metrics':metrics,'mode':'INCREMENTAL_UPSERT' if delta else 'FULL_SNAPSHOT'})
    if delta:
        result['parent_version_id']=parent['id']
        result['ingestion']['parent_version_id']=parent['id']
        result['ingestion']['parent_semantic_sha256']=parent['canonical']['semantic_sha256']
        result['ingestion']['parent_business_sha256']=digest(parent['business_data'])
    return result


def metadata(tables, enterprise_id, planning_at, horizon, timezone):
    return {'schema_revision':'canonical-business-v1','enterprise_id':enterprise_id,
            'planning_at':planning_at,'horizon':horizon,'timezone':timezone,
            'table_mapping':{k:k for k in tables},
            'nested_fields':{k:sorted({f for r in rows for f,v in r.items() if isinstance(v,(dict,list))}) for k,rows in tables.items()}}


def workbook_bytes(tables, *, enterprise_id, planning_at, horizon, timezone):
    from openpyxl import Workbook
    book=Workbook();meta=book.active;meta.title=META_SHEET;meta.append(['field','value'])
    for k,v in metadata(tables,enterprise_id,planning_at,horizon,timezone).items():
        meta.append([k,json.dumps(v,ensure_ascii=False)])
    for kind,rows in tables.items():
        sheet=book.create_sheet(kind)
        headers=sorted({k for row in rows for k in row})+['__present_fields']
        sheet.append(headers)
        for row in rows:
            values=[json.dumps(row[k],ensure_ascii=False) if isinstance(row.get(k),(list,dict)) else row.get(k) for k in headers[:-1]]
            sheet.append(values+[json.dumps(list(row),ensure_ascii=False)])
    result=io.BytesIO();book.save(result);return result.getvalue()


def csv_package(tables, *, enterprise_id, planning_at, horizon, timezone):
    meta=metadata(tables,enterprise_id,planning_at,horizon,timezone)
    meta['encoding']='JSON_CELL_VALUES_V1';meta['table_mapping']={k+'.csv':k for k in tables}
    result=[(MANIFEST,json.dumps(meta,ensure_ascii=False).encode())]
    for kind,rows in tables.items():
        text=io.StringIO(newline='');writer=csv.writer(text)
        headers=sorted({k for row in rows for k in row})+['__present_fields'];writer.writerow(headers)
        for row in rows:
            writer.writerow([json.dumps(row.get(k),ensure_ascii=False) for k in headers[:-1]]+[json.dumps(list(row))])
        result.append((kind+'.csv',text.getvalue().encode('utf-8-sig')))
    return result


def parse_workbook(source, sha):
    if META_SHEET not in source['sheets']:return None
    tab=source['sheets'][META_SHEET]
    meta={r[0]:json.loads(r[1]) for r in tab['rows'] if len(r)>1 and r[0] is not None}
    if meta.get('schema_revision')!='canonical-business-v1':raise ValueError('CANONICAL_SCHEMA_UNSUPPORTED')
    tables={};refs={}
    for sheet,kind in meta['table_mapping'].items():
        if kind not in DOMAIN or sheet not in source['sheets'] or kind in tables:raise ValueError('INVALID_DOMAIN_MAPPING')
        raw=source['sheets'][sheet]
        if raw['formulas']:raise ValueError('FORMULA_REQUIRES_REVIEW: '+sheet)
        tables[kind]=[];refs[kind]=[]
        for n,values in enumerate(raw['rows'],raw['header_row']+1):
            cells=dict(zip(raw['headers'],values))
            present=json.loads(cells.pop('__present_fields'))
            row={k:cells.get(k) for k in present}
            for field in meta.get('nested_fields',{}).get(kind,[]):
                if row.get(field) is not None:row[field]=json.loads(row[field])
            tables[kind].append(row)
            refs[kind].append({'file_sha256':sha,'sheet':sheet,'row':n,
                'fields':{k:sheet+'!'+column(raw['headers'].index(k)+1)+str(n) for k in present}})
    if set(source['sheets'])!={META_SHEET,*meta['table_mapping']}:
        raise ValueError('UNMAPPED_SOURCE_TABLE: 每张业务表都必须有明确领域用途')
    return assemble(meta['enterprise_id'],tables,planning_at=meta['planning_at'],horizon=meta['horizon'],timezone=meta['timezone'],source_refs=refs)


def parse_csv_package(files, originals):
    if not 1<=len(files)<=32 or sum(len(content) for _,content in files)>48*1024*1024:raise ValueError('SOURCE_PACKAGE_TOO_LARGE')
    if len({name for name,_ in files})!=len(files):raise ValueError('DUPLICATE_SOURCE_FILENAME')
    for name,content in files:
        if name.replace('\\','/').split('/')[-1]!=name or len(name)>160 or not content or len(content)>10*1024*1024 or Path(name).suffix.lower() not in {'.csv','.json'}:raise ValueError('INVALID_SOURCE_FILE')
    named=dict(files)
    if MANIFEST not in named:return None
    meta=json.loads(named[MANIFEST].decode('utf-8-sig'))
    if meta.get('schema_revision')!='canonical-business-v1' or meta.get('encoding')!='JSON_CELL_VALUES_V1':raise ValueError('CANONICAL_SCHEMA_UNSUPPORTED')
    if set(named)!={MANIFEST,*meta['table_mapping']}:raise ValueError('UNMAPPED_SOURCE_TABLE')
    tables={};refs={};stored=[];sheets={}
    for filename,content in files:
        clean=filename.replace('\\','/').split('/')[-1]
        if clean!=filename or len(clean)>160 or not content or len(content)>10*1024*1024:raise ValueError('INVALID_SOURCE_FILE')
        ext=Path(clean).suffix.lower()
        if ext not in {'.csv','.json'}:raise ValueError('INVALID_SOURCE_FILE')
        sha=hashlib.sha256(content).hexdigest();p=originals/(sha+ext)
        if not p.exists():p.write_bytes(content)
        stored.append({'filename':clean,'sha256':sha,'extension':ext,'source_kind':'UPLOADED_FILE'})
        if filename==MANIFEST:continue
        kind=meta['table_mapping'][filename]
        if kind not in DOMAIN or kind in tables:raise ValueError('INVALID_DOMAIN_MAPPING')
        # Source evidence JSON can exceed the stdlib's 128 KiB default. Each
        # file is already bounded above to 10 MiB; retain that same finite cap.
        # Set a process-wide floor rather than restoring it across concurrent
        # imports, which could truncate another reader mid-parse.
        if csv.field_size_limit()<10*1024*1024:csv.field_size_limit(10*1024*1024)
        reader=csv.DictReader(io.StringIO(content.decode('utf-8-sig')))
        tables[kind]=[];refs[kind]=[];display=[]
        for n,cells in enumerate(reader,2):
            if n>20001:raise ValueError('SOURCE_TABLE_TOO_LARGE')
            present=json.loads(cells.pop('__present_fields'))
            row={k:json.loads(cells[k]) for k in present}
            tables[kind].append(row);display.append([cells.get(k) for k in reader.fieldnames])
            refs[kind].append({'file_sha256':sha,'sheet':filename,'row':n,'fields':{k:filename+':'+str(n)+':'+k for k in present}})
        sheets[filename]={'headers':reader.fieldnames,'rows':display,'header_row':1,'formulas':[],'cells':{}}
    canonical=assemble(meta['enterprise_id'],tables,planning_at=meta['planning_at'],horizon=meta['horizon'],timezone=meta['timezone'],source_refs=refs)
    return {'canonical':canonical,'source':{'sheets':sheets},'source_files':stored,
            'file_sha256':digest(sorted((f['filename'],f['sha256']) for f in stored)),
            'filename':' + '.join(f['filename'] for f in stored)}


def refresh(task):
    canonical=task['canonical'];groups=list(canonical['tables']);confirmed=set(groups).issubset(task['confirmations'])
    valid=canonical['data_status']!='DATA_BLOCKED'
    task['canonical_groups']=groups
    task['validation']={'issues':copy.deepcopy(canonical['issues']),'blocking_count':len(canonical['issues'])}
    task['readiness']={'ready':False,'confirmed':confirmed,'data_accepted':valid,
                       'blocking_count':len(canonical['issues']), 'planning_status':canonical['planning_readiness']['status'],
                       'can_save_facts':confirmed and valid}
    task['state']='FACTS_CONFIRMED' if confirmed else 'PENDING_REVIEW'
    if not valid:task['state']='FACTS_BLOCKED'
    task['ingestion_stages']={
        'reception':{'status':'RECEIVED','originals_retained':True,'transport':task.get('input_transport','FILE')},
        'business':{'status':'BLOCKED' if not valid else 'CONFIRMED' if confirmed else 'PENDING_CONFIRMATION',
            'issues':copy.deepcopy(canonical['issues']),'confirmed_groups':list(task['confirmations']),
            'requires_confirmation':not confirmed,'partial_publication_supported':False},
        'planning':{'status':canonical['planning_readiness']['status'],'ready':False,
            'issues':copy.deepcopy(canonical['planning_readiness']['blocking_issues']),
            'requires_policy_projection':True}}
    task['duplicate_records']=[];task['version_mode']='CANONICAL_BUSINESS_VERSION'
    task['current_version_id']=None
    return task
