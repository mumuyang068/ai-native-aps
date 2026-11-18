"""Bounded reuse of certified immutable run content inside the original read Owner.

Live run status, draft bindings and authorization are deliberately not cached.
Strict original readers remain available for an explicit fresh/full audit.
"""
import copy
import hashlib
import json
from collections import OrderedDict

from planning_adapter import InputError


def immutable_read(owner,folder,identity,kind,build,*,fresh=False):
    folder=folder.resolve()
    with owner.lock:
        cache=getattr(owner,'_query_read_cache',None)
        if cache is None:cache=owner._query_read_cache=OrderedDict()
        # A requested audit cannot leave its old successful entry usable on failure.
        if fresh:
            for old in list(cache):
                if old[0]==str(folder):cache.pop(old)
        try:content=(folder/'run-manifest.json').read_bytes();manifest=json.loads(content)
        except (OSError,ValueError):raise InputError('EVIDENCE_UNREADABLE: 运行清单不可读取') from None
        if not isinstance(manifest,dict) or any(manifest.get(k)!=identity.get(k) for k in
                ('run_id','plan_id','data_version_id','snapshot_sha256')):
            raise InputError('RUN_SOURCE_MISMATCH')
        seal=hashlib.sha256(content).hexdigest()
        key=(str(folder),tuple(sorted(identity.items())),kind,seal)
        if key not in cache:
            value=build(manifest)
            cache[key]=value
            while len(cache)>12:cache.popitem(last=False)
        else:cache.move_to_end(key)
        return copy.deepcopy(cache[key])


def artifact_members(folder,manifest,names):
    values={};files=manifest.get('files',{})
    if not isinstance(files,dict):raise InputError('EVIDENCE_UNREADABLE')
    for name in names:
        if name not in files:raise InputError('EVIDENCE_NOT_COLLECTED: '+name)
        path=(folder/name).resolve()
        if not path.is_relative_to(folder.resolve()):raise InputError('INVALID_EVIDENCE_PATH')
        try:content=path.read_bytes()
        except OSError:raise InputError('EVIDENCE_UNREADABLE: '+name) from None
        if hashlib.sha256(content).hexdigest()!=files[name]:raise InputError('EVIDENCE_INTEGRITY_MISMATCH: '+name)
        try:values[name]=json.loads(content)
        except ValueError:raise InputError('EVIDENCE_UNREADABLE: '+name) from None
        if not isinstance(values[name],dict):raise InputError('EVIDENCE_UNREADABLE: '+name)
    return values
