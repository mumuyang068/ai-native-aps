"""Output protection at the existing host-held MCP credential boundary.

Not an authentication store or an OS sandbox. Credentials stay in the trusted
MCP host; business tools expose no connection/configuration tool.
"""
import argparse
from contextlib import contextmanager, redirect_stderr
import json
import logging
import os
import re


@contextmanager
def private_process_diagnostics():
    """Only for dedicated MCP/bridge CLI processes, never the product host.

    SDK protocol validation happens before our tool handler and may log raw
    parameters. Suppress that process's unstructured diagnostics regardless
    of content; structured safe replies and audit files remain available.
    """
    previous=logging.root.manager.disable
    with open(os.devnull,'w') as sink, redirect_stderr(sink):
        logging.disable(logging.CRITICAL)
        try:yield
        finally:logging.disable(previous)


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, 'Invalid command line; use --help.\n')


class SensitiveOutput:
    _fields = frozenset({'authorization', 'proxy_authorization', 'token', 'access_token',
                         'refresh_token', 'password', 'secret', 'credential', 'api_key',
                         'owner_key', 'operator_key', 'confirmation_nonce'})

    def __init__(self, secrets=()):
        self._secrets = tuple(sorted({s for s in secrets if isinstance(s, str) and s}, key=len, reverse=True))

    def contains(self, value):
        text = json.dumps(value, ensure_ascii=False, default=str)
        return any(s in text for s in self._secrets)

    def text(self, value):
        for secret in self._secrets:
            value = value.replace(secret, '[REDACTED]')
        return re.sub(r'(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+', 'Bearer [REDACTED]', value)

    def clean(self, value):
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {self.text(str(k)): '[REDACTED]' if str(k).lower().replace('-', '_') in self._fields
                    and isinstance(v, str) else self.clean(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            items = [self.clean(v) for v in value]
            return tuple(items) if isinstance(value, tuple) else items
        if hasattr(value, 'model_copy'):
            return value.model_copy(update={k: self.clean(v) for k, v in value.__dict__.items()})
        return value


class SafeDiagnostics:
    """Discard arbitrary child stderr, retain structured safe MCP errors."""
    def __init__(self, output, boundary):
        self.output, self.boundary = output, boundary

    def write(self, text):
        if text.strip():
            self.output.write('MCP_CHILD_DIAGNOSTIC_SUPPRESSED\n')
        return len(text)

    def flush(self):
        self.output.flush()

    def fileno(self):
        # stdio_client passes this descriptor directly to the subprocess. A
        # secret can span arbitrary writes, so never chunk-filter raw stderr.
        import os
        if not hasattr(self, '_null'):
            self._null = open(os.devnull, 'w')
        return self._null.fileno()

    def close(self):
        if hasattr(self, '_null'):
            self._null.close()
