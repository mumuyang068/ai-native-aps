"""Small ERP-owned atomic handoff; no execution or inventory API is exposed."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math

from psycopg2.errors import SerializationFailure, UniqueViolation
from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.float_utils import float_compare


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _timestamp(value, *, require_zone=True):
    if not isinstance(value, str):
        raise ValueError("Timestamp must be an ISO string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if require_zone and parsed.tzinfo is None:
        raise ValueError("Planned timestamps require an explicit timezone")
    return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed


class Company(models.Model):
    _inherit = "res.company"

    aps_interop_enabled = fields.Boolean(default=False, groups="base.group_system")
    aps_interop_test_company = fields.Boolean(default=False, groups="base.group_system")
    aps_interop_enterprise_id = fields.Char(groups="base.group_system")
    aps_interop_branch_id = fields.Char(groups="base.group_system")


class Production(models.Model):
    _inherit = "mrp.production"

    aps_interop_suggestion_key = fields.Char(copy=False, readonly=True, index=True)
    aps_interop_frozen = fields.Boolean(default=False, copy=False)
    _sql_constraints = [("aps_suggestion_unique", "unique(company_id, aps_interop_suggestion_key)",
                         "An APS manufacturing suggestion already has an external order.")]


class PublicationCommand(models.Model):
    _name = "aps.interop.command"
    _description = "APS isolated company publication receipt"
    _order = "id desc"

    command_id = fields.Char(required=True, readonly=True, index=True)
    request_hash = fields.Char(required=True, readonly=True)
    suggestion_key = fields.Char(readonly=True, index=True)
    company_id = fields.Many2one("res.company", required=True, readonly=True, index=True)
    request_json = fields.Json(required=True, readonly=True)
    receipt_json = fields.Json(required=True, readonly=True)
    state = fields.Selection([("ACCEPTED", "Accepted"), ("REJECTED", "Rejected")], required=True, readonly=True)
    external_document_id = fields.Many2one("mrp.production", readonly=True, ondelete="restrict")
    operator_user_id = fields.Many2one("res.users", required=True, readonly=True)
    _sql_constraints = [
        ("command_unique", "unique(company_id, command_id)", "An APS command ID may be used only once."),
        ("suggestion_unique", "unique(company_id, suggestion_key)", "An APS suggestion is already published."),
    ]

    @api.model
    def _authorize(self):
        if not self.env.user.has_group("aps_interop.group_test_publisher"):
            raise AccessError("APS_TEST_PUBLISHER_REQUIRED")

    @api.model
    def _company(self, identifier):
        if not isinstance(identifier, int) or isinstance(identifier, bool) or identifier not in self.env.companies.ids:
            raise AccessError("APS_TEST_COMPANY_NOT_ALLOWED")
        company = self.env["res.company"].browse(identifier).sudo()
        if not company.exists() or not company.aps_interop_test_company:
            raise AccessError("APS_ISOLATED_TEST_COMPANY_REQUIRED")
        return company

    @api.model
    def _external(self, production):
        production.invalidate_recordset()
        production.check_access("read")
        return {"system": "odoo", "id": production.id, "name": production.name,
            "state": production.state, "company_id": production.company_id.id,
            # Microseconds are intentional: second-truncated RPC read dates are not a CAS token.
            "write_date": production.write_date.isoformat(timespec="microseconds"),
            "quantity": production.product_qty, "uom_id": production.product_uom_id.id,
            "product_id": production.product_id.id, "bom_id": production.bom_id.id,
            "source_location_id": production.location_src_id.id,
            "destination_location_id": production.location_dest_id.id,
            "picking_type_id": production.picking_type_id.id,
            "warehouse_id": production.picking_type_id.warehouse_id.id,
            "start_at": production.date_start.isoformat() if production.date_start else False,
            "end_at": production.date_finished.isoformat() if production.date_finished else False}

    @api.model
    def _actual(self, production):
        return {"produced": production.qty_produced,
            "operations": [{"id": work.id, "state": work.state, "produced": work.qty_produced}
                for work in production.workorder_ids],
            "stock_effect": "READ_FROM_ODOO_STOCK_ONLY"}

    @api.model
    def _require_unstarted(self, production):
        if (production.state not in ("draft", "confirmed") or production.aps_interop_frozen
                or production.qty_produced or any(work.state in ("progress", "done", "cancel")
                    or work.qty_produced or work.time_ids for work in production.workorder_ids)
                or any(move.state == "done" or move.picked for move in production.move_raw_ids | production.move_finished_ids)):
            raise ValueError("EXTERNAL_ORDER_PROTECTED:Executed, consumed, frozen or closed work cannot be rescheduled")

    @api.model
    def get_receipt(self, command_id):
        self._authorize()
        if not isinstance(command_id, str) or not command_id.strip() or len(command_id) > 200:
            raise ValidationError("APS_COMMAND_ID_INVALID")
        row = self.sudo().search([("command_id", "=", command_id),
            ("company_id", "in", self.env.companies.ids)], limit=1)
        if not row:
            return False
        self._company(row.company_id.id)
        response = dict(row.receipt_json)
        # Old target receipts can be safely enriched only from the target's own
        # immutable, independently verified command record.
        response["request_hash"] = row.request_hash
        if row.external_document_id:
            production = self.env["mrp.production"].browse(row.external_document_id.id)
            response["current_external_document"] = self._external(production)
            response["actual_quantities"] = self._actual(production)
        return response

    @api.model
    def _record(self, company, command, digest, receipt, *, suggestion_key=False, production=False):
        receipt = dict(receipt, request_hash=digest)
        self.sudo().create({"company_id": company.id, "command_id": command["command_id"],
            "request_hash": digest, "suggestion_key": suggestion_key, "request_json": command,
            "receipt_json": receipt, "state": receipt["state"],
            "external_document_id": production.id if production else False,
            "operator_user_id": self.env.uid})
        return receipt

    @api.model
    def _reject(self, command_id, code, message, **extra):
        return dict(command_id=command_id, state="REJECTED", code=code, message=message, **extra)

    @api.model
    def publish(self, command):
        self._authorize()
        if not isinstance(command, dict) or command.get("schema") != "aps-publication/1":
            raise ValidationError("APS_COMMAND_SCHEMA_INVALID")
        for name in ("command_id", "enterprise_id", "branch_id", "data_version_id", "plan_id", "run_id", "suggestion_id"):
            if not isinstance(command.get(name), str) or not command[name].strip() or len(command[name]) > 200:
                raise ValidationError("APS_COMMAND_IDENTITY_INVALID:" + name)
        if command.get("action_type") not in ("CREATE_MO", "RESCHEDULE_MO", "SCHEDULE_MO"):
            raise ValidationError("APS_COMMAND_ACTION_UNSUPPORTED")
        company = self._company(command.get("test_company_id"))
        digest = _digest({key: value for key, value in command.items() if key != "request_hash"})
        if command.get("request_hash") not in (None, digest):
            raise ValidationError("APS_COMMAND_HASH_MISMATCH")
        suggestion = _digest([command[name] for name in ("enterprise_id", "branch_id", "data_version_id",
            "run_id", "suggestion_id", "action_type")])
        # Serialize the command and its business suggestion in a stable order.
        for token in sorted({_digest([company.id, command["command_id"]]), _digest([company.id, suggestion])}):
            signed = int.from_bytes(bytes.fromhex(token[:16]), "big", signed=True)
            self.env.cr.execute("SELECT pg_advisory_xact_lock(%s)", (signed,))
        previous = self.sudo().search([("company_id", "=", company.id),
            ("command_id", "=", command["command_id"])], limit=1)
        if previous:
            if previous.request_hash != digest:
                return self._reject(command["command_id"], "COMMAND_HASH_CONFLICT", "The original command is immutable.",
                                    request_hash=digest)
            return dict(previous.receipt_json, request_hash=previous.request_hash)
        existing = self.sudo().search([("company_id", "=", company.id), ("suggestion_key", "=", suggestion)], limit=1)
        if existing:
            receipt = self._reject(command["command_id"], "SUGGESTION_ALREADY_PUBLISHED",
                "Reconcile the already published suggestion.", existing_command_id=existing.command_id,
                external_document=existing.receipt_json.get("external_document", False))
            return self._record(company, command, digest, receipt)
        if not company.aps_interop_enabled:
            receipt = self._reject(command["command_id"], "TEST_COMPANY_DISABLED", "Publication is disabled for this test company.")
            return self._record(company, command, digest, receipt)
        if (company.aps_interop_enterprise_id != command["enterprise_id"]
                or company.aps_interop_branch_id != command["branch_id"]):
            receipt = self._reject(command["command_id"], "TEST_ENTERPRISE_SCOPE_MISMATCH", "Enterprise or branch is not enabled.")
            return self._record(company, command, digest, receipt)
        try:
            # A business rejection rolls back only this proposed change, then records its receipt.
            with self.env.cr.savepoint():
                production = self._apply(company, command, suggestion)
                receipt = {"command_id": command["command_id"], "state": "ACCEPTED",
                    "external_document": self._external(production), "actual_quantities": self._actual(production)}
                if command['action_type'] == 'SCHEDULE_MO':
                    receipt['professional_snapshot'] = self.inspect_manufacturing(production.id)
                return self._record(company, command, digest, receipt, suggestion_key=suggestion, production=production)
        except UniqueViolation as exc:
            # Repeatable-read snapshots may predate an advisory-lock wait; let Odoo retry
            # the whole transaction. Both the MO and receipt roll back together.
            raise SerializationFailure("Concurrent APS publication; retry the original command") from exc
        except (ValueError, InvalidOperation, UserError) as exc:
            code, separator, message = str(exc).partition(":")
            if not separator or not code.startswith("EXTERNAL_"):
                code, message = "EXTERNAL_BUSINESS_REJECTED", str(exc)
            receipt = self._reject(command["command_id"], code, message.strip())
            return self._record(company, command, digest, receipt)

    @api.model
    def _apply(self, company, command, suggestion):
        confirmation = command.get("operator_confirmation")
        if (not isinstance(confirmation, dict) or confirmation.get("scope") != "ISOLATED_TEST_COMPANY_ONLY"
                or confirmation.get("confirmed") is not True
                or any(not isinstance(confirmation.get(name), str) or not confirmation[name].strip()
                       for name in ("actor_id", "record_id"))):
            raise ValueError("EXTERNAL_OPERATOR_CONFIRMATION_REQUIRED:Isolated Operator confirmation metadata is required")
        payload = command.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("EXTERNAL_PAYLOAD_INVALID:A manufacturing payload is required")
        allowed = {"product_id", "bom_id", "uom_id", "quantity", "start_at", "end_at",
                   "external_document_id", "expected_write_date"}
        if command['action_type']=='CREATE_MO':allowed |= {'sale_order_id','expected_sale_revision'}
        if command['action_type'] == 'SCHEDULE_MO':
            allowed |= {'operations', 'constraints', 'expected_workorders', 'expected_master_sha256', 'expected_publication_command_id'}
        if set(payload) - allowed:
            raise ValueError("EXTERNAL_PAYLOAD_UNSUPPORTED:Payload contains unsupported changes")
        for name in ("product_id", "bom_id", "uom_id"):
            if not isinstance(payload.get(name), int) or isinstance(payload[name], bool) or payload[name] <= 0:
                raise ValueError("EXTERNAL_PAYLOAD_INVALID:Positive ERP IDs are required")
        quantity = Decimal(str(payload.get("quantity")))
        if not quantity.is_finite() or quantity <= 0 or not math.isfinite(float(quantity)):
            raise ValueError("EXTERNAL_QUANTITY_INVALID:Quantity must be finite and positive")
        start, finish = _timestamp(payload.get("start_at")), _timestamp(payload.get("end_at"))
        if start >= finish:
            raise ValueError("EXTERNAL_WINDOW_INVALID:Manufacturing start must precede its end")
        product = self.env["product.product"].browse(payload["product_id"]).exists()
        bom = self.env["mrp.bom"].browse(payload["bom_id"]).exists()
        unit = self.env["uom.uom"].browse(payload["uom_id"]).exists()
        if not product or not bom or not unit:
            raise ValueError("EXTERNAL_MASTER_NOT_FOUND:Product, BOM, or unit does not exist")
        for record in (product, bom, unit):
            record.check_access("read")
        if (product.company_id and product.company_id.id != company.id
                or bom.company_id and bom.company_id.id != company.id
                or bom.product_tmpl_id != product.product_tmpl_id
                or bom.product_id and bom.product_id != product or bom.type != "normal"
                or unit != product.uom_id):
            raise ValueError("EXTERNAL_MASTER_MISMATCH:Only the matching company BOM and original stock unit are supported")
        production_model = self.env["mrp.production"].with_company(company)
        if command["action_type"] == "CREATE_MO":
            if payload.get("external_document_id") or payload.get("expected_write_date"):
                raise ValueError("EXTERNAL_ACTION_MISMATCH:Creation cannot replace an existing manufacturing order")
            if production_model.search_count([("company_id", "=", company.id), ("aps_interop_suggestion_key", "=", suggestion)]):
                raise ValueError("EXTERNAL_MAPPING_CONFLICT:An external order already exists; reconcile its identity")
            values={"product_id": product.id, "bom_id": bom.id,
                "product_qty": float(quantity), "product_uom_id": unit.id, "date_start": start,
                "date_finished": finish, "company_id": company.id, "origin": "APS:" + suggestion,
                "aps_interop_suggestion_key": suggestion, "qty_producing": 0}
            if payload.get('sale_order_id'):
                sale=self.env['sale.order'].browse(payload['sale_order_id']).exists()
                if not sale or sale.company_id!=company or sale.state!='sale':raise ValueError('EXTERNAL_SALE_SCOPE_MISMATCH')
                sale.check_access('read')
                if sale.write_date!=_timestamp(payload.get('expected_sale_revision'),require_zone=False):raise ValueError('EXTERNAL_SALE_REVISION_STALE')
                lines=sale.order_line.filtered(lambda r:r.product_id==product and r.product_uom==unit)
                if not lines or float(quantity)>sum(lines.mapped('qty_to_deliver')):raise ValueError('EXTERNAL_SALE_QUANTITY_MISMATCH')
                values.update(origin=sale.name,procurement_group_id=sale.procurement_group_id.id)
            elif payload.get('expected_sale_revision'):raise ValueError('EXTERNAL_SALE_SCOPE_MISMATCH')
            return production_model.create(values)
        if command["action_type"] not in ("RESCHEDULE_MO", "SCHEDULE_MO"):
            raise ValueError("EXTERNAL_ACTION_UNSUPPORTED:Only creation and protected rescheduling are available")
        identifier = payload.get("external_document_id")
        if not isinstance(identifier, int) or isinstance(identifier, bool) or identifier <= 0:
            raise ValueError("EXTERNAL_DOCUMENT_REQUIRED:Rescheduling requires an existing order identity")
        production = production_model.browse(identifier).exists()
        if not production or production.company_id.id != company.id:
            raise ValueError("EXTERNAL_DOCUMENT_NOT_FOUND:No order exists in the configured test company")
        production.check_access("write")
        self.env.cr.execute("SELECT id FROM mrp_production WHERE id=%s FOR UPDATE", (production.id,))
        production.invalidate_recordset()
        expected = _timestamp(payload.get("expected_write_date"), require_zone=False)
        if production.write_date != expected:
            raise ValueError("EXTERNAL_VERSION_STALE:The manufacturing order changed since preview")
        self._require_unstarted(production)
        if (production.product_id != product or production.bom_id != bom or production.product_uom_id != unit
                or float_compare(production.product_qty, float(quantity), precision_rounding=unit.rounding)):
            raise ValueError("EXTERNAL_IDENTITY_MISMATCH:Rescheduling cannot change product, BOM, unit or quantity")
        if command['action_type'] == 'SCHEDULE_MO':
            return self._schedule(production, payload, command)
        if production.workorder_ids:
            raise ValueError("EXTERNAL_OPERATION_RESCHEDULE_UNSUPPORTED:Per-operation dates require the SCHEDULE_MO contract")
        production.write({"date_start": start, "date_finished": finish})
        return production
