"""Request-local receipts for successful, pure annual projection validation.

The receipt contains only a digest of the complete input, never authenticated
files, permission decisions, source-store reads, or a mutable validation result.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import hashlib
import json


@dataclass
class _Receipts:
    active: bool = True
    successful: set[str] = field(default_factory=set)


_current = ContextVar('annual_projection_validation_scope', default=None)
_YEAR_POLICY = 'APS-SIM-YEAR-01-POLICY-V1'


@contextmanager
def validation_scope():
    # AnyIO copies the context into sync worker threads. Sharing this small
    # mutable receipt object lets dependencies and their endpoint use the same
    # request, while separate requests always receive distinct objects.
    receipts = _Receipts()
    token = _current.set(receipts)
    try:
        yield
    finally:
        # Revoke copies of the context retained by child tasks as well.
        receipts.active = False
        receipts.successful.clear()
        _current.reset(token)


def _input_digest(data):
    # JSON encoders accept tuples, subclasses and non-string dictionary keys.
    # Those can compare differently in the original validator, so only plain
    # JSON input trees qualify. Everything else keeps the original full path.
    pending = [data]
    visited = set()
    while pending:
        value = pending.pop()
        kind = type(value)
        if kind in (dict, list):
            if id(value) in visited:
                continue
            visited.add(id(value))
            if kind is dict:
                if any(type(key) is not str for key in value):
                    return None
                pending.extend(value.values())
            else:
                pending.extend(value)
        elif kind not in (str, int, float, bool, type(None)):
            return None
    try:
        # Preserve mapping order too: the source adapter derives ordered rows
        # from declaration dictionaries, even though the projection seal sorts
        # its keys. Identical seals do not imply identical validator inputs.
        encoded = json.dumps(data, ensure_ascii=False,
                             separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, OverflowError, RecursionError):
        return None
    return hashlib.sha256(encoded).hexdigest()


def validate_annual_once(data, validator):
    receipts = _current.get()
    if receipts is None or not receipts.active:
        return validator(data)
    meta = data.get('foundation_projection', {})
    policy = meta.get('policy', {}) if type(meta) is dict else None
    year = policy.get('year_policy', {}) if type(policy) is dict else None
    if type(year) is not dict or year.get('id') != _YEAR_POLICY:
        return validator(data)
    key = _input_digest(data)
    if key is None:
        return validator(data)
    if key in receipts.successful:
        return data
    result = validator(data)
    # A failed validator never reaches this point. Also avoid publishing a
    # receipt if a caller changed a shared input during its full validation.
    if receipts.active and key == _input_digest(data):
        receipts.successful.add(key)
    return result


class AnnualValidationScopeMiddleware:
    """Keep one scope around the complete HTTP request, including sync work."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        with validation_scope():
            await self.app(scope, receive, send)
