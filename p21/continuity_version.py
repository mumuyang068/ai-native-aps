"""Continuity metadata for ImportStore's immutable Canonical version Owner.

This validates the declared batch ledger, not ERP/MES/WMS business truth. The
Canonical carrier is verified by ImportStore; optional batch paths add separate
hash evidence. The sync Owner remains responsible for accepting source batches.
"""
import copy
from datetime import datetime
import hashlib
from pathlib import Path
import re

from p2.preflight import digest


def instant(value, code='INVALID_SYNC_RECEIPT'):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError()
        return result
    except (ValueError, TypeError, AttributeError):
        raise ValueError(code + ': an explicit timezone is required') from None


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 2048


def _cursor(value):
    return _text(value) or (isinstance(value, int) and not isinstance(value, bool) and value >= 0)


def _batch(record, system, *, verify_originals=True):
    if not isinstance(record, dict):
        raise ValueError('INVALID_SYNC_RECEIPT: invalid source batch')
    if (not _text(record.get('batch_id')) or not _cursor(record.get('cursor'))
        or not isinstance(record.get('source_sha256'), str)
        or not re.fullmatch('[0-9a-f]{64}', record['source_sha256'])
        or 'previous_batch_id' not in record
        or (record['previous_batch_id'] is not None and not _text(record['previous_batch_id']))
        or record.get('system', system) != system):
        raise ValueError('INVALID_SYNC_RECEIPT: source batch identity, hash and cursor are required')
    if record['batch_id'] == record['previous_batch_id']:
        raise ValueError('INVALID_SYNC_RECEIPT: source batch cannot follow itself')
    if 'as_of' in record:
        instant(record['as_of'])
    if 'imported_at' in record:
        instant(record['imported_at'])
    if verify_originals and record.get('source_path') is not None:
        # Resolve before checking the extension, so a JSON symlink cannot expose
        # a database or runtime XML through this optional hash-verification path.
        try:
            path = Path(record['source_path']).resolve(strict=True)
            if path.suffix.lower() not in {'.json', '.jsonl', '.csv', '.xlsx'} or not path.is_file():
                raise ValueError()
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except (OSError, ValueError, TypeError):
            raise ValueError('SOURCE_CHANGED: batch original is missing or is not a supported source carrier') from None
        if actual != record['source_sha256']:
            raise ValueError('SOURCE_CHANGED: batch original hash does not match receipt')


def batch_identity(record, system):
    """Stable source-ledger identity, independent of a local original's location."""
    return {'system': system, **{k: record.get(k) for k in
        ('batch_id', 'source_sha256', 'cursor', 'previous_batch_id', 'as_of', 'mode')}}


def batches(source):
    for head in source['batches']:
        chain = head.get('chain')
        for record in chain if chain else [head]:
            yield batch_identity(record, head['system'])


def source_chain_scope(source):
    """A live branch owns a chain; its changing revision is only a watermark.

    Missing scope retains the original single-enterprise replay contract.
    An explicitly malformed scope cannot silently join that legacy chain.
    """
    if 'source_scope' not in source:return None
    scope=source['source_scope']
    if (not isinstance(scope,dict) or set(scope)!={'connector_id','branch_id','source_revision'}
        or not isinstance(scope.get('connector_id'),str) or not re.fullmatch(r'live-[A-Za-z0-9_-]{1,60}',scope['connector_id'])
        or not isinstance(scope.get('branch_id'),str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,60}',scope['branch_id'])
        or type(scope.get('source_revision')) is not int or scope['source_revision']<0):
        raise ValueError('INVALID_SOURCE_SCOPE: explicit live connector, branch and source revision required')
    return scope['connector_id'],scope['branch_id']


def validate_receipt(source, enterprise_id, *, verify_originals=True):
    """Check receipt scope/chain; full original audit remains the default.

    The indexed Owner may explicitly use False for an already authenticated
    parent receipt. It must separately verify every new original before save;
    this flag is never an authorization supplied by a public request.
    """
    if (not isinstance(source, dict) or not _text(source.get('enterprise_id'))
        or not isinstance(source.get('batches'), list) or not source['batches']):
        raise ValueError('INVALID_SYNC_RECEIPT: enterprise, knowledge clock and source batches are required')
    if source['enterprise_id'] != enterprise_id:
        raise ValueError('ENTERPRISE_IDENTITY_MISMATCH: sync receipt belongs to another enterprise')
    source_chain_scope(source)
    cutoff = instant(source.get('as_of'))
    seen = set()
    for head in source['batches']:
        system = head.get('system') if isinstance(head, dict) else None
        if not _text(system) or system in seen:
            raise ValueError('INVALID_SYNC_RECEIPT: exactly one current batch per source system is required')
        seen.add(system)
        _batch(head, system, verify_originals=verify_originals)
        chain = head.get('chain')
        if chain is not None:
            if not isinstance(chain, list) or not chain:
                raise ValueError('INVALID_SYNC_RECEIPT: batch chain must be nonempty')
            ids = set()
            previous = None
            for record in chain:
                _batch(record, system, verify_originals=verify_originals)
                if record['batch_id'] in ids or (previous is not None and record['previous_batch_id'] != previous):
                    raise ValueError('SOURCE_BATCH_PARENT_MISMATCH: source chain is not contiguous')
                ids.add(record['batch_id']); previous = record['batch_id']
            if batch_identity(chain[-1], system) != batch_identity(head, system):
                raise ValueError('SOURCE_BATCH_PARENT_MISMATCH: source chain does not end at the declared current batch')
        for record in chain if chain else [head]:
            if 'as_of' in record and instant(record['as_of']) > cutoff:
                raise ValueError('INVALID_SYNC_RECEIPT: source batch exceeds the knowledge cutoff')
    result = copy.deepcopy(source)
    result['batches'].sort(key=lambda row: row['system'])
    return result


def version_enterprise(version):
    """Read immutable identity without pretending an indexed commit is Canonical."""
    if version.get('version_mode') == 'CONTINUITY_STATE_VERSION':
        return version.get('business_data', {}).get('state_commit', {}).get('enterprise_id')
    return version.get('canonical', {}).get('enterprise_id')


def source_vector(source):
    return {(b['system'], b['batch_id']) for b in source.get('batches', [])}


def bind(canonical, source, parent_id, versions):
    """Return (immutable continuity metadata, duplicate version id or None)."""
    enterprise = canonical['enterprise_id']
    lookup = {v['id']: v for v in versions}
    parent = lookup.get(parent_id) if parent_id is not None else None
    if parent_id is not None and parent is None:
        raise ValueError('PARENT_VERSION_NOT_FOUND: explicit parent does not exist')
    if parent is not None and version_enterprise(parent) != enterprise:
        raise ValueError('ENTERPRISE_IDENTITY_MISMATCH: parent belongs to another enterprise')
    scope=source_chain_scope(source)
    if parent is not None and source_chain_scope(parent.get('business_data',{}).get('source_sync_receipt',{}))!=scope:
        raise ValueError('CONTINUITY_SOURCE_SCOPE_MISMATCH: parent belongs to another connector or branch')

    current_batches = {(b['system'], b['batch_id']): b for b in batches(source)}
    vector = source_vector(source)
    chain_versions = [v for v in versions if version_enterprise(v) == enterprise
                      and 'source_sync_receipt' in v.get('business_data', {})
                      and source_chain_scope(v['business_data']['source_sync_receipt'])==scope]
    # Ancestry is an identity contract, including compact commits. A missing or
    # relabelled ancestor cannot become an apparent new source-chain root.
    latest = {}
    ancestor = parent
    visited = set()
    while ancestor is not None:
        if ancestor['id'] in visited:
            raise ValueError('CONTINUITY_HEAD_CONFLICT: stored ancestry contains a cycle')
        visited.add(ancestor['id'])
        if version_enterprise(ancestor) != enterprise:
            raise ValueError('ENTERPRISE_IDENTITY_MISMATCH: ancestor belongs to another enterprise')
        ancestor_source = ancestor.get('business_data', {}).get('source_sync_receipt', {})
        if source_chain_scope(ancestor_source) != scope:
            raise ValueError('CONTINUITY_SOURCE_SCOPE_MISMATCH: ancestor belongs to another connector or branch')
        for head in ancestor_source.get('batches', []):
            latest.setdefault(head['system'], head)
        ancestor_id = ancestor.get('parent_version_id')
        ancestor = lookup.get(ancestor_id)
        if ancestor_id is not None and ancestor is None:
            raise ValueError('PARENT_VERSION_NOT_FOUND: stored ancestry is incomplete')
    for old in chain_versions:
        previous_source = old['business_data']['source_sync_receipt']
        for prior in batches(previous_source):
            current = current_batches.get((prior['system'], prior['batch_id']))
            if current is not None and current != prior:
                raise ValueError('SOURCE_BATCH_CONFLICT: source batch id already binds different content or cursor')
        prior_vector = source_vector(previous_source)
        if vector == prior_vector:
            if 'canonical' not in old:
                raise ValueError('CONTINUITY_MATERIALIZATION_REQUIRED: replay needs the exact confirmed state')
            if (source['as_of'] != previous_source['as_of']
                or source.get('source_scope') != previous_source.get('source_scope')
                or canonical['semantic_sha256'] != old['canonical']['semantic_sha256']
                or parent_id != old.get('parent_version_id')):
                raise ValueError('SOURCE_BATCH_CONFLICT: replayed source vector cannot bind another scope or business state')
            return None, old['id']

    if parent is None:
        if chain_versions:
            raise ValueError('CONTINUITY_PARENT_REQUIRED: enterprise already has a continuous version chain')
    else:
        if 'canonical' not in parent:
            raise ValueError('CONTINUITY_MATERIALIZATION_REQUIRED: full save needs the exact confirmed parent')
        if any(v.get('parent_version_id') == parent_id for v in versions):
            raise ValueError('CONTINUITY_HEAD_CONFLICT: parent already has an immutable successor')
        if chain_versions and parent_id not in {v['id'] for v in chain_versions}:
            raise ValueError('CONTINUITY_HEAD_CONFLICT: parent is outside the enterprise continuity chain')
        parent_source = parent.get('business_data', {}).get('source_sync_receipt')
        previous_as_of = parent_source['as_of'] if parent_source else parent['canonical']['planning_at']
        if instant(source['as_of']) < instant(previous_as_of):
            raise ValueError('KNOWLEDGE_CLOCK_REGRESSION: knowledge cutoff cannot move backwards')
        if instant(canonical['planning_at']) < instant(parent['canonical']['planning_at']):
            raise ValueError('PLANNING_CLOCK_REGRESSION: planning origin cannot move backwards')
        # Recover the latest receipt for every system, including systems omitted
        # by an otherwise valid incremental version.
        for head in source['batches']:
            prior = latest.get(head['system'])
            if prior and head['batch_id'] == prior['batch_id']:
                continue
            first = head.get('chain', [head])[0]
            if first['previous_batch_id'] != (prior['batch_id'] if prior else None):
                raise ValueError('SOURCE_BATCH_PARENT_MISMATCH: source chain does not continue the parent source cursor')
    if parent is None and any(head.get('chain', [head])[0]['previous_batch_id'] is not None for head in source['batches']):
        raise ValueError('SOURCE_BATCH_PARENT_MISMATCH: initial version must declare the complete source chain')
    records = [r for head in source['batches'] for r in head.get('chain', [head])]
    metadata = {'schema_version': 'canonical-continuity-v1', 'enterprise_id': enterprise,
        'parent_version_id': parent_id, 'as_of': source['as_of'],
        'business_change': 'INITIAL' if parent is None else 'UNCHANGED' if digest(canonical['tables']) == digest(parent['canonical']['tables']) else 'CHANGED',
        'business_change_scope': 'CANONICAL_TABLE_CONTENT_INCLUDING_SOURCE_EVIDENCE_NOT_PHYSICAL_LEDGER',
        'planning_scope_change': 'INITIAL' if parent is None else 'UNCHANGED' if canonical['semantic_sha256'] == parent['canonical']['semantic_sha256'] else 'CHANGED',
        'source_change': 'INITIAL' if parent is None else 'CHANGED',
        'batch_originals': 'HASH_VERIFIED' if all(r.get('source_path') for r in records) else 'METADATA_ONLY'}
    if scope is not None:metadata['source_scope']=copy.deepcopy(source['source_scope'])
    return metadata, None
