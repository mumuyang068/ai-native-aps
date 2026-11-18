"""Task-local reviewer authentication, separate from Agent tool authority.

This is a local research confirmation channel, not enterprise SSO or production
approval. Its configured credential is never exposed by MCP or task reads.
"""
from datetime import datetime, timezone
import copy
import hmac
import os
import secrets
import threading
import time
import uuid

COOKIE = 'aps_research_operator'


class OperatorAuthorization:
    def __init__(self, *, credential=None, subject=None, clock=time.time, ttl_seconds=1200):
        self._credential = os.environ.get('PLANNING_AGENT_OPERATOR_KEY', '') if credential is None else credential
        self.subject = subject or os.environ.get('PLANNING_AGENT_OPERATOR_ID', 'local-research-reviewer')
        self.clock = clock
        self.ttl_seconds = ttl_seconds
        self._sessions = {}
        self._lock = threading.RLock()

    def login(self, credential):
        if len(self._credential) < 32:
            raise ValueError('OPERATOR_CHANNEL_NOT_CONFIGURED')
        if not isinstance(credential, str) or not hmac.compare_digest(credential, self._credential):
            raise ValueError('OPERATOR_CREDENTIAL_INVALID')
        token = secrets.token_urlsafe(40)
        principal = dict(subject=self.subject, authentication='TASK_LOCAL_OPERATOR_CREDENTIAL',
            session_id='OPERATOR-'+uuid.uuid4().hex[:16], scope='RESEARCH_ONLY_NO_ENTERPRISE_EXECUTION',
            authenticated_at=datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(),
            expires_at=datetime.fromtimestamp(self.clock()+self.ttl_seconds, timezone.utc).isoformat())
        with self._lock:
            now = self.clock()
            self._sessions = {key:value for key,value in self._sessions.items() if value[0] > now}
            self._sessions[token] = (now+self.ttl_seconds, principal)
        return token, copy.deepcopy(principal)

    def principal(self, token):
        if not isinstance(token, str):
            return None
        with self._lock:
            value = self._sessions.get(token)
            if not value:
                return None
            if value[0] <= self.clock():
                self._sessions.pop(token, None)
                return None
            return copy.deepcopy(value[1])

    def logout(self, token):
        with self._lock:
            self._sessions.pop(token, None)

    def status(self, token):
        principal = self.principal(token)
        return dict(configured=len(self._credential)>=32, authenticated=principal is not None,
            principal=principal, scope='RESEARCH_ONLY_NO_ENTERPRISE_EXECUTION',
            channel='TASK_LOCAL_OPERATOR', enterprise_sso_certified=False)
