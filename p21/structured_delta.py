"""Pure postimage preparation over an authenticated ImportStore parent version.

The caller authenticates the parent's stored fingerprint and original carriers,
archives the incoming original, then uses the existing Canonical assembly,
confirmation and version transaction. This helper owns no store or source cursor.
"""
import copy
import re
from datetime import datetime

from p21.canonical import digest


def _clock(value):
    try:
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        if parsed.tzinfo is None or parsed.utcoffset() is None:raise ValueError()
        return parsed
    except (AttributeError,TypeError,ValueError):
        raise ValueError('DELTA_CLOCK_INVALID') from None


def prepare_delta(snapshot,parent,*,sha):
    """Return complete tables/evidence while mapping only incoming postimages.

    Absence never removes a fact. A supplied ID replaces its entire prior row,
    including its evidence, without copying missing fields from the old row.
    Required fields, references and conservation remain the Canonical Owner's
    full-assembly responsibility; a prepared delta is never a validation receipt.
    """
    if not isinstance(snapshot,dict) or snapshot.get('mode')!='INCREMENTAL_UPSERT':
        raise ValueError('DELTA_MODE_UNSUPPORTED')
    if not isinstance(sha,str) or not re.fullmatch('[0-9a-f]{64}',sha):
        raise ValueError('DELTA_ORIGINAL_SHA_INVALID')
    if not isinstance(parent,dict) or not isinstance(parent.get('canonical'),dict):
        raise ValueError('DELTA_PARENT_REQUIRED')
    c=parent['canonical'];business=parent.get('business_data',{})
    if snapshot.get('parent_version_id')!=parent.get('id') or not parent.get('id'):
        raise ValueError('DELTA_PARENT_ID_MISMATCH')
    from p21.record_mapping import enterprise_identity
    if enterprise_identity(snapshot.get('enterprise_id'),business.get('ingestion',{}).get('mapping'))!=c.get('enterprise_id'):
        raise ValueError('DELTA_ENTERPRISE_MISMATCH')
    if business.get('corrections'):
        raise ValueError('DELTA_PARENT_CORRECTIONS_REQUIRE_SOURCE')
    if 'source_sync_receipt' in parent or 'source_sync_receipt' in business or any(
            'continuity_bundle' in row for row in c.get('tables',{}).get('locations',[])):
        raise ValueError('DELTA_CONTINUITY_OWNER_REQUIRED')
    if c.get('source_conflicts') or c.get('source_retractions'):
        raise ValueError('DELTA_PARENT_SOURCE_UNRESOLVED')
    if c.get('data_status') not in {'DATA_ACCEPTED','DATA_ACCEPTED_WITH_UNRESOLVED_FACTS'} or c.get('issues'):
        raise ValueError('DELTA_PARENT_NOT_ACCEPTED')
    confirmations=parent.get('confirmations',{})
    if (not c.get('tables') or not isinstance(confirmations,dict) or
            any(not isinstance(confirmations.get(kind),dict) or
                not confirmations[kind].get('reviewer') or not confirmations[kind].get('content_sha256')
                for kind in c['tables']) or
            not c.get('objects') or any(o.get('factual_status')!='CONFIRMED_FACT' for o in c['objects'])):
        raise ValueError('DELTA_PARENT_NOT_CONFIRMED')
    if _clock(snapshot.get('planning_at'))<_clock(c.get('planning_at')):
        raise ValueError('DELTA_CLOCK_REGRESSION')
    tables=snapshot.get('tables')
    if (not isinstance(tables,dict) or not tables or
            any(not isinstance(rows,list) or len(rows)>20000 or any(not isinstance(row,dict) for row in rows)
                for rows in tables.values())):
        raise ValueError('DELTA_TABLES_INVALID')
    ingestion=business.get('ingestion',{})
    mapping=ingestion.get('mapping')
    if not isinstance(mapping,dict) or not isinstance(mapping.get('tables'),dict):
        raise ValueError('DELTA_PARENT_MAPPING_REQUIRED')
    if ingestion.get('mapping_sha256')!=digest(mapping):
        raise ValueError('DELTA_PARENT_MAPPING_CHANGED')
    if 'mapping' in snapshot and snapshot['mapping']!=mapping:
        raise ValueError('DELTA_MAPPING_CHANGED')
    if not set(tables)<=set(mapping['tables']):
        raise ValueError('DELTA_MAPPING_TABLE_UNKNOWN')
    subset=copy.deepcopy(mapping)
    subset['tables']={name:subset['tables'][name] for name in tables}
    from p21.record_mapping import project
    incoming,source_refs,issues,_=project(tables,subset,sha)
    if any('continuity_bundle' in row for row in incoming.get('locations',[])):
        raise ValueError('DELTA_CONTINUITY_OWNER_REQUIRED')
    # References identify the full inherited configuration, rather than the
    # implementation detail of selecting only tables present in this delta.
    for refs in source_refs.values():
        for ref in refs:ref['mapping_sha256']=digest(mapping)
    result=copy.deepcopy(c['tables']);refs=copy.deepcopy(c.get('source_refs',{}))
    for kind,rows in result.items():
        if not isinstance(refs.get(kind),list) or len(refs[kind])!=len(rows):
            raise ValueError('DELTA_PARENT_EVIDENCE_REQUIRED')
    metrics=dict(mapped_records=sum(map(len,incoming.values())),
        reused_records=sum(map(len,result.values())),unchanged_postimages=0,csv_generated=0,csv_parsed=0)
    for kind,rows in incoming.items():
        result.setdefault(kind,[]);refs.setdefault(kind,[])
        positions={row['id']:n for n,row in enumerate(result[kind])}
        seen=set()
        for row,reference in zip(rows,source_refs[kind]):
            key=row.get('id')
            if not isinstance(key,str) or not key.strip():
                raise ValueError('DELTA_POSTIMAGE_IDENTITY_REQUIRED')
            if key in seen:raise ValueError('DELTA_DUPLICATE_IDENTITY')
            seen.add(key)
            if key in positions:
                n=positions[key]
                metrics['unchanged_postimages']+=int(row==result[kind][n])
                metrics['reused_records']-=1
                result[kind][n]=copy.deepcopy(row);refs[kind][n]=copy.deepcopy(reference)
            else:
                positions[key]=len(result[kind])
                result[kind].append(copy.deepcopy(row));refs[kind].append(copy.deepcopy(reference))
    return dict(tables=result,source_refs=refs,mapping_issues=issues,
        mapping=copy.deepcopy(mapping),metrics=metrics)
