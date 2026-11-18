"""Task-local Agent credentials and a separate server-only Owner channel.

This is an isolated research access boundary, not enterprise SSO. Absence of a
configuration is visible as uncertified local compatibility; it never creates
an authenticated principal. Route Owners decide whether legacy local access is
permitted. A declared but invalid configuration always fails closed.
"""
import hmac
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


PERMISSIONS = frozenset({'READ', 'TASK_METADATA', 'RESEARCH_PROPOSAL'})
CONFIG_ENV = 'PLANNING_AGENT_ACCESS_FILE'
OWNER_ENV = 'PLANNING_INTERNAL_OWNER_KEY'
SCOPE = 'TASK_LOCAL_RESEARCH_NOT_ENTERPRISE_SSO'


class AgentAccessError(ValueError):
    def __init__(self, code, http_status=401):
        self.code = code
        self.http_status = http_status
        super().__init__(code)


def _configuration_invalid():
    raise AgentAccessError('AGENT_ACCESS_CONFIGURATION_INVALID', 503)


def _key(value):
    return (isinstance(value, str) and 32 <= len(value) <= 512
            and all(33 <= ord(character) <= 126 for character in value))


def _identifiers(value):
    if (not isinstance(value, list) or not value or len(value) > 1000
            or any(not isinstance(item, str) or not item or len(item) > 100
                   or item.strip() != item or '*' in item for item in value)
            or len(set(value)) != len(value)):
        _configuration_invalid()
    return tuple(sorted(value))


def _expiry(value):
    if not isinstance(value, str) or len(value) > 100:
        _configuration_invalid()
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.utcoffset() is None:
            _configuration_invalid()
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        _configuration_invalid()


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            _configuration_invalid()
        result[name] = value
    return result


@dataclass(frozen=True)
class _Client:
    client_id: str
    key: str = field(repr=False)
    plan_ids: tuple[str, ...]
    permissions: tuple[str, ...]
    expires_at: datetime | None = None


class AgentAccess:
    """Immutable configuration snapshot; credentials stay out of projections."""

    def __init__(self, clients=(), owner_key=None, *, configured=False, clock=None):
        self.configured = configured
        self._clients = tuple(clients)
        self._owner_key = owner_key
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @classmethod
    def from_env(cls, environ=None, *, clock=None):
        env = os.environ if environ is None else environ
        if CONFIG_ENV not in env:
            return cls(clock=clock)
        path = env.get(CONFIG_ENV)
        owner_key = env.get(OWNER_ENV)
        if not isinstance(path, str) or not path.strip() or not _key(owner_key):
            _configuration_invalid()
        try:
            source = Path(path)
            if not source.is_file() or source.stat().st_size > 1024 * 1024:
                _configuration_invalid()
            value = json.loads(source.read_text('utf-8-sig'), object_pairs_hook=_unique_object)
        except (OSError, UnicodeError, ValueError, TypeError):
            _configuration_invalid()
        if not isinstance(value, dict) or set(value) != {'clients'}:
            _configuration_invalid()
        rows = value['clients']
        if not isinstance(rows, list) or not 1 <= len(rows) <= 1000:
            _configuration_invalid()
        clients = []
        required = {'client_id', 'key', 'plan_ids', 'permissions'}
        for row in rows:
            if (not isinstance(row, dict) or not required <= set(row)
                    or set(row) - required - {'expires_at'}):
                _configuration_invalid()
            client_id = row['client_id']
            if (not isinstance(client_id, str)
                    or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', client_id)
                    or not _key(row['key'])
                    or hmac.compare_digest(row['key'], owner_key)):
                _configuration_invalid()
            plan_ids = _identifiers(row['plan_ids'])
            permissions = _identifiers(row['permissions'])
            if not set(permissions) <= PERMISSIONS:
                _configuration_invalid()
            if any(client.client_id == client_id or hmac.compare_digest(client.key, row['key'])
                   for client in clients):
                _configuration_invalid()
            clients.append(_Client(client_id, row['key'], plan_ids, permissions,
                                   _expiry(row['expires_at']) if 'expires_at' in row else None))
        return cls(clients, owner_key, configured=True, clock=clock)

    def status(self):
        return dict(status='TASK_LOCAL_AGENT_ACCESS_READY' if self.configured
                    else 'LOCAL_TRUST_COMPAT_NOT_CERTIFIED',
                    configured=self.configured, client_count=len(self._clients),
                    permissions=sorted(PERMISSIONS), scope=SCOPE,
                    enterprise_sso_certified=False)

    def _require_configured(self):
        if not self.configured:
            raise AgentAccessError('AGENT_ACCESS_NOT_CONFIGURED', 503)

    def _now(self):
        value = self._clock()
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise AgentAccessError('AGENT_ACCESS_CLOCK_INVALID', 503)
        return value.astimezone(timezone.utc)

    def authenticate_agent(self, authorization):
        self._require_configured()
        match = (re.fullmatch(r'Bearer ([!-~]{32,512})', authorization, flags=re.IGNORECASE)
                 if isinstance(authorization, str) else None)
        if not match:
            raise AgentAccessError('AGENT_ACCESS_UNAUTHORIZED')
        token = match.group(1)
        selected = None
        for client in self._clients:
            if hmac.compare_digest(client.key, token):
                selected = client
        if selected is None:
            raise AgentAccessError('AGENT_ACCESS_UNAUTHORIZED')
        if selected.expires_at is not None and selected.expires_at <= self._now():
            raise AgentAccessError('AGENT_ACCESS_EXPIRED')
        return dict(kind='AGENT_CLIENT', client_id=selected.client_id, auth_method='BEARER',
                    plan_ids=list(selected.plan_ids), permissions=list(selected.permissions),
                    scope=SCOPE, enterprise_sso_certified=False)

    def authorize_agent(self, authorization, plan_id, permission):
        principal = self.authenticate_agent(authorization)
        if plan_id not in principal['plan_ids']:
            raise AgentAccessError('AGENT_ACCESS_PLAN_FORBIDDEN', 403)
        if permission not in PERMISSIONS or permission not in principal['permissions']:
            raise AgentAccessError('AGENT_ACCESS_PERMISSION_FORBIDDEN', 403)
        return principal

    def authenticate_owner(self, x_aps_owner):
        self._require_configured()
        if not _key(x_aps_owner) or not hmac.compare_digest(self._owner_key, x_aps_owner):
            raise AgentAccessError('OWNER_ACCESS_UNAUTHORIZED')
        return dict(kind='INTERNAL_OWNER', client_id='APS_INTERNAL_OWNER',
                    auth_method='SERVER_INTERNAL_KEY', scope=SCOPE,
                    enterprise_sso_certified=False)

    def internal_owner_headers(self):
        """Server-only dispatch helper; never include this in a tool response."""
        self._require_configured()
        return {'X-APS-Owner': self._owner_key}
