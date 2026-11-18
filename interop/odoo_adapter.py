"""Explicitly enabled, loopback-only client for the scoped Odoo transaction addon.

Transport failures deliberately propagate: a caller must reconcile the original
command, never replace it with a fresh manufacturing request.
"""
import copy
import hashlib
import ipaddress
import json
import math
import re
from urllib.parse import urlsplit
from xml.parsers.expat import ExpatError
from xmlrpc.client import Fault, ProtocolError, ResponseError, SafeTransport, ServerProxy, Transport
from interop.publication import ExternalBusinessRejected


class OdooBusinessRejected(ExternalBusinessRejected,PermissionError):
    """A target ERP permission/business error explicitly refused this call."""
    def __init__(self, code, detail):
        super().__init__(code,detail)


class OdooTransportUncertain(ConnectionError):
    """There is no trustworthy receipt; reconcile the original command ID."""


def command_hash(command):
    unsigned = {key: value for key, value in command.items() if key != "request_hash"}
    return hashlib.sha256(json.dumps(unsigned, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


class _TimeoutTransport(Transport):
    def __init__(self, timeout):
        super().__init__()
        self.timeout = timeout

    def make_connection(self, host):
        connection = super().make_connection(host)
        connection.timeout = self.timeout
        return connection


class _TimeoutSafeTransport(SafeTransport):
    def __init__(self, timeout):
        super().__init__()
        self.timeout = timeout

    def make_connection(self, host):
        connection = super().make_connection(host)
        connection.timeout = self.timeout
        return connection


class OdooAdapter:
    """One database and one isolated company; credentials are never logged."""
    def __init__(self, base_url, database, username, password, company_id, *, enabled=False, timeout=15):
        endpoint = urlsplit(base_url)
        try:
            loopback = ipaddress.ip_address(endpoint.hostname or "").is_loopback
            port = endpoint.port
        except ValueError:
            loopback, port = False, None
        if (not loopback or endpoint.scheme not in ("http", "https") or endpoint.username
                or endpoint.password or endpoint.path not in ("", "/") or endpoint.query
                or endpoint.fragment or (port is not None and port <= 0)):
            raise ValueError("ODOO_ENDPOINT_REQUIRES_LITERAL_LOOPBACK_ORIGIN")
        if not isinstance(database, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,63}", database):
            raise ValueError("ODOO_DATABASE_INVALID")
        if not isinstance(company_id, int) or isinstance(company_id, bool) or company_id <= 0:
            raise ValueError("ODOO_COMPANY_INVALID")
        if not username or not password:
            raise ValueError("ODOO_CREDENTIALS_REQUIRED")
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 120:
            raise ValueError("ODOO_TIMEOUT_INVALID")
        self.base_url = base_url.rstrip("/")
        self.database, self.username, self._password = database, username, password
        self.company_id, self.enabled, self.timeout = company_id, bool(enabled), timeout
        self._transport_class = _TimeoutSafeTransport if endpoint.scheme == "https" else _TimeoutTransport

    def _rpc(self, method, arguments):
        if not self.enabled:
            raise PermissionError("ODOO_DISABLED")
        try:
            return self._execute_rpc(method, arguments)
        except Fault as exc:
            # Original Odoo 18 base/controllers/rpc.py maps AccessDenied to 3,
            # AccessError to 4, and UserError/Warning to 2 without a traceback.
            if exc.faultCode in (3, 4):
                raise OdooBusinessRejected("ODOO_PERMISSION_DENIED", exc.faultString) from exc
            if exc.faultCode == 2:
                raise OdooBusinessRejected("ODOO_BUSINESS_REJECTED", exc.faultString) from exc
            explicit = re.findall(r"^(?:odoo\.exceptions\.)?(AccessError|AccessDenied|UserError|ValidationError):\s*(.*)",
                                  exc.faultString, re.MULTILINE)
            if explicit:
                category, detail = explicit[-1]
                code = "ODOO_PERMISSION_DENIED" if category in ("AccessError", "AccessDenied") else "ODOO_BUSINESS_REJECTED"
                raise OdooBusinessRejected(code, detail) from exc
            raise OdooTransportUncertain("ODOO_RPC_RESULT_UNCERTAIN") from exc
        except ProtocolError as exc:
            if exc.errcode in (401, 403):
                raise OdooBusinessRejected("ODOO_PERMISSION_DENIED", "Target HTTP authentication was denied") from exc
            raise OdooTransportUncertain("ODOO_HTTP_RESULT_UNCERTAIN:" + str(exc.errcode)) from exc
        except PermissionError:
            raise
        except (OSError, ResponseError, ExpatError) as exc:
            raise OdooTransportUncertain("ODOO_TRANSPORT_RESULT_UNCERTAIN") from exc

    def _execute_rpc(self, method, arguments):
        with ServerProxy(self.base_url + "/xmlrpc/2/common", allow_none=True,
                         transport=self._transport_class(self.timeout)) as common:
            uid = common.authenticate(self.database, self.username, self._password, {})
        if not isinstance(uid, int) or isinstance(uid, bool) or uid <= 0:
            raise OdooBusinessRejected("ODOO_AUTHENTICATION_FAILED","Target authentication was denied")
        with ServerProxy(self.base_url + "/xmlrpc/2/object", allow_none=True,
                         transport=self._transport_class(self.timeout)) as models:
            return models.execute_kw(self.database, uid, self._password,
                "aps.interop.command", method, arguments,
                {"context": {"allowed_company_ids": [self.company_id], "lang": "en_US"}})

    def _receipt(self, value, command_id, expected_hash=None):
        if not isinstance(value, dict) or value.get("command_id") != command_id:
            raise OdooTransportUncertain("ODOO_RECEIPT_IDENTITY_MISMATCH")
        if value.get("state") not in ("ACCEPTED", "REJECTED"):
            raise OdooTransportUncertain("ODOO_RECEIPT_STATE_INVALID")
        target_hash = value.get("request_hash")
        if (not isinstance(target_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", target_hash)
                or expected_hash is not None and target_hash != expected_hash):
            raise OdooTransportUncertain("ODOO_RECEIPT_HASH_MISMATCH")
        result = copy.deepcopy(value)
        if value["state"] == "ACCEPTED":
            external = value.get("external_document", {})
            if (external.get("system") != "odoo" or external.get("company_id") != self.company_id
                    or not isinstance(external.get("id"), int) or external["id"] <= 0):
                raise OdooTransportUncertain("ODOO_RECEIPT_COMPANY_MISMATCH")
        external = value.get("external_document")
        if isinstance(external, dict) and external.get("id"):
            if external.get("company_id") != self.company_id:
                raise OdooTransportUncertain("ODOO_RECEIPT_COMPANY_MISMATCH")
            result["external_document_id"] = f"odoo:{self.company_id}:mrp.production:{external['id']}"
            current = value.get("current_external_document") or external
            if current.get("company_id") != self.company_id or current.get("id") != external["id"]:
                raise OdooTransportUncertain("ODOO_RECEIPT_IDENTITY_MISMATCH")
            result["external_state"] = current.get("state", False)
        return result

    def capture_planning_facts(self, sale_ids):
        value=self._rpc('capture_planning_facts',[self.company_id,sale_ids])
        if (not isinstance(value,dict) or value.get('schema')!='odoo-planning-facts/1'
                or value.get('company_id')!=self.company_id or value.get('database')!=self.database
                or set(value.get('scope',{}).get('sale_ids',[]))!=set(sale_ids)):
            raise OdooTransportUncertain('ODOO_SOURCE_SCOPE_MISMATCH')
        return value

    def send(self, command):
        if not self.enabled:
            raise PermissionError("ODOO_DISABLED")
        if not isinstance(command, dict):
            raise ValueError("ODOO_COMMAND_INVALID")
        value = copy.deepcopy(command)
        if value.get("schema") != "aps-publication/1":
            raise ValueError("ODOO_COMMAND_SCHEMA_INVALID")
        for key in ("command_id", "enterprise_id", "branch_id", "data_version_id", "plan_id", "run_id", "suggestion_id"):
            if not isinstance(value.get(key), str) or not value[key].strip() or len(value[key]) > 200:
                raise ValueError("ODOO_COMMAND_IDENTITY_INVALID:" + key)
        if value.get("test_company_id") != self.company_id:
            raise PermissionError("ODOO_COMMAND_COMPANY_MISMATCH")
        if value.get("action_type") not in ("CREATE_MO", "RESCHEDULE_MO", "SCHEDULE_MO"):
            raise ValueError("ODOO_COMMAND_ACTION_UNSUPPORTED")
        confirmation = value.get("operator_confirmation")
        if (not isinstance(confirmation, dict) or confirmation.get("scope") != "ISOLATED_TEST_COMPANY_ONLY"
                or confirmation.get("confirmed") is not True
                or any(not isinstance(confirmation.get(name), str) or not confirmation[name].strip()
                       for name in ("actor_id", "record_id"))):
            raise ValueError("ODOO_COMMAND_OPERATOR_CONFIRMATION_REQUIRED")
        digest = command_hash(value)
        if value.get("request_hash") not in (None, digest):
            raise ValueError("ODOO_COMMAND_HASH_MISMATCH")
        value["request_hash"] = digest
        return self._receipt(self._rpc("publish", [value]), value["command_id"], digest)

    def lookup(self, command_id):
        if not isinstance(command_id, str) or not command_id.strip() or len(command_id) > 200:
            raise ValueError("ODOO_COMMAND_IDENTITY_INVALID")
        receipt = self._rpc("get_receipt", [command_id])
        return None if receipt is False else self._receipt(receipt, command_id)

    def inspect_manufacturing(self, production_id):
        if type(production_id) is not int or production_id <= 0:
            raise ValueError('ODOO_MANUFACTURING_ID_INVALID')
        snapshot = self._rpc('inspect_manufacturing', [production_id])
        if (not isinstance(snapshot, dict) or snapshot.get('schema') != 'odoo-manufacturing-snapshot/1'
                or snapshot.get('production', {}).get('id') != production_id
                or snapshot['production'].get('company_id') != self.company_id):
            raise OdooTransportUncertain('ODOO_OBSERVATION_IDENTITY_MISMATCH')
        return snapshot
