"""These tests run inside real Odoo; APS unit wire peers cannot replace them."""
import copy
import hashlib
import json

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPublication(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.write({"aps_interop_enabled": True, "aps_interop_test_company": True,
            "aps_interop_enterprise_id": "TEST-E", "aps_interop_branch_id": "TEST-B"})
        cls.publisher = cls.env.ref("base.user_admin")
        cls.publisher.write({"groups_id": [Command.link(cls.env.ref("aps_interop.group_test_publisher").id)]})
        cls.raw = cls.env["product.product"].create({"name": "INTEROP test raw", "type": "consu"})
        cls.product = cls.env["product.product"].create({"name": "INTEROP test FG", "type": "consu"})
        cls.bom = cls.env["mrp.bom"].create({"product_tmpl_id": cls.product.product_tmpl_id.id,
            "product_qty": 1, "company_id": cls.company.id,
            "bom_line_ids": [Command.create({"product_id": cls.raw.id, "product_qty": 1})]})
        cls.service = cls.env["aps.interop.command"].with_user(cls.publisher)

    def command(self, identifier="CMD-odoo-test", **changes):
        result = {"schema": "aps-publication/1", "command_id": identifier,
            "enterprise_id": "TEST-E", "branch_id": "TEST-B", "data_version_id": "DATA-test",
            "plan_id": "PLAN-test", "run_id": "API-test", "suggestion_id": "MO-test",
            "action_type": "CREATE_MO", "test_company_id": self.company.id,
            "operator_confirmation": {"scope": "ISOLATED_TEST_COMPANY_ONLY", "actor_id": "test-operator",
                "record_id": "CONF-odoo-test", "session_id": "SESSION-odoo-test", "authentication": "ODOO_TEST", "confirmed": True},
            "payload": {"product_id": self.product.id, "bom_id": self.bom.id,
                "uom_id": self.product.uom_id.id, "quantity": "20",
                "start_at": "2026-11-02T09:00:00+08:00", "end_at": "2026-11-02T10:00:00+08:00"}}
        result.update(changes)
        return result

    def test_create_is_draft_and_repeated_command_has_one_business_effect(self):
        request = self.command()
        first = self.service.publish(request)
        second = self.service.publish(request)
        self.assertEqual(first, second)
        self.assertEqual(first["state"], "ACCEPTED")
        expected = hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode("utf8")).hexdigest()
        self.assertIn("request_hash", first)
        self.assertEqual(first["request_hash"], expected)
        self.assertIn("product_id", first["external_document"])
        self.assertIn("bom_id", first["external_document"])
        self.assertEqual(first["external_document"]["product_id"], self.product.id)
        self.assertEqual(first["external_document"]["bom_id"], self.bom.id)
        mo = self.env["mrp.production"].browse(first["external_document"]["id"])
        self.assertEqual(mo.state, "draft")
        self.assertEqual(mo.qty_produced, 0)
        self.assertEqual(self.env["mrp.production"].search_count([
            ("aps_interop_suggestion_key", "=", mo.aps_interop_suggestion_key)]), 1)

    def test_changed_payload_same_command_is_rejected_without_new_mo(self):
        request = self.command()
        receipt = self.service.publish(request)
        changed = copy.deepcopy(request)
        changed["payload"]["quantity"] = "19"
        conflict = self.service.publish(changed)
        self.assertEqual(conflict["state"], "REJECTED")
        self.assertEqual(conflict["code"], "COMMAND_HASH_CONFLICT")
        self.assertEqual(self.service.get_receipt(request["command_id"])["external_document"]["id"],
                         receipt["external_document"]["id"])

    def test_same_suggestion_cannot_bypass_idempotency_with_a_new_command_id(self):
        first = self.service.publish(self.command())
        second = self.service.publish(self.command("CMD-second"))
        self.assertEqual(second["state"], "REJECTED")
        self.assertEqual(second["code"], "SUGGESTION_ALREADY_PUBLISHED")
        self.assertEqual(second["existing_command_id"], first["command_id"])

    def test_frozen_and_stale_reschedule_do_not_change_business_dates(self):
        first = self.service.publish(self.command())
        mo = self.env["mrp.production"].browse(first["external_document"]["id"])
        before = mo.date_start
        payload = dict(self.command()["payload"], external_document_id=mo.id,
            expected_write_date="2000-01-01T00:00:00.000000", start_at="2026-11-03T09:00:00+08:00",
            end_at="2026-11-03T10:00:00+08:00")
        stale = self.service.publish(self.command("CMD-stale", action_type="RESCHEDULE_MO", payload=payload))
        self.assertEqual(stale["code"], "EXTERNAL_VERSION_STALE")
        mo.aps_interop_frozen = True
        payload["expected_write_date"] = self.service.get_receipt(first["command_id"])["current_external_document"]["write_date"]
        frozen = self.service.publish(self.command("CMD-frozen", action_type="RESCHEDULE_MO",
            suggestion_id="MO-frozen", payload=payload))
        self.assertEqual(frozen["code"], "EXTERNAL_ORDER_PROTECTED")
        self.assertEqual(mo.date_start, before)

    def test_disabled_company_and_nonpublisher_are_denied(self):
        self.company.aps_interop_enabled = False
        result = self.service.publish(self.command())
        self.assertEqual(result["code"], "TEST_COMPANY_DISABLED")
        public = self.env["aps.interop.command"].with_user(self.env.ref("base.public_user"))
        with self.assertRaises(AccessError):
            public.get_receipt("missing")
