from __future__ import annotations

import copy
import json
import unittest
from unittest import mock

from test_provider_scripts import DEEPLINE, FakeProcess
from test_email_fallback import with_fallback
from test_output_contract import VALIDATOR, accepted_email_result
import test_export_xlsx as exporter


class ValidatorEnvelopeTests(unittest.TestCase):
    def test_failed_envelopes_never_promote_positive_verdicts(self):
        rows = [
            {"address": "ada@example.com", "status": "valid"},
            {"email": "ada@example.com", "status": "success", "result": "deliverable"},
        ]
        for status in sorted(DEEPLINE._FAILURE_STATUSES):
            for row in rows:
                for nested in (False, True):
                    for exit_code in (0, 1):
                        with self.subTest(status=status, row=row, nested=nested, exit_code=exit_code):
                            response = {"status": status, "toolResponse": {"raw": row}}
                            if nested:
                                response = {"status": "completed", "toolResponse": {"status": status, "raw": row}}
                            with mock.patch.object(DEEPLINE, "_invoke", return_value=(exit_code, json.dumps(response), "")) as call:
                                body, code = DEEPLINE.run({"operation": "execute", "tool": "validator", "entity_type": "email_validation", "payload": {"email": "ada@example.com"}})
                            self.assertEqual(code, 0)
                            self.assertEqual(body["status"], status)
                            self.assertEqual(body.get("results", []), [])
                            self.assertEqual(call.call_count, 1)

    def test_nonzero_command_cannot_promote_unexplained_valid_result(self):
        response = {"toolResponse": {"raw": {"address": "ada@example.com", "status": "valid"}}}
        with mock.patch.object(DEEPLINE, "_invoke", return_value=(1, json.dumps(response), "failed")):
            body, _ = DEEPLINE.run({"operation": "execute", "tool": "validator", "entity_type": "email_validation", "payload": {"email": "ada@example.com"}})
        self.assertEqual(body["status"], "provider_error")
        self.assertEqual(body.get("results", []), [])

    def test_partial_validation_stays_partial(self):
        response = {"status": "partial", "toolResponse": {"raw": {"address": "ada@example.com", "status": "valid"}}}
        self.assertEqual(DEEPLINE._execute_output(response, "validator", "email_validation")["status"], "partial")

    def test_pending_jobs_keep_id_without_a_deliverability_verdict(self):
        for status in ("queue", "verifying"):
            for email in (None, "ada@example.com"):
                with self.subTest(status=status, email=email):
                    raw = {"id": "job-123", "status": status, "try_again_at": 1788714810, "api_key": "private-value"}
                    if email:
                        raw["email"] = email
                    response = {"status": "completed", "results": [], "toolResponse": {"raw": raw}}
                    body = DEEPLINE._execute_output(response, "validator", "email_validation")
                    self.assertEqual(body["status"], "partial")
                    self.assertEqual(body["results"], [])
                    self.assertEqual(body["pending_verification"]["id"], "job-123")
                    self.assertNotIn("private-value", json.dumps(body))

    def test_observed_billing_survives_without_promoting_estimates(self):
        for entity, raw in (("company", {"company": "Example", "domain": "example.com"}), ("email_validation", {"address": "ada@example.com", "status": "valid"})):
            response = {"toolResponse": {"raw": raw}, "billing": {"credits_charged": 0.28, "cost_usd": 0.028, "api_key": "private-value"}}
            body = DEEPLINE._execute_output(response, "tool", entity)
            self.assertEqual(body["billing"], {"credits_charged": 0.28, "cost_usd": 0.028})
            self.assertNotIn("billing", DEEPLINE._execute_output({"toolResponse": {"raw": raw}}, "tool", entity))
            for bad in (True, -1, "0.28", float("nan"), float("inf"), 10 ** 400):
                response["billing"] = {"credits_charged": bad, "cost_usd": bad}
                self.assertNotIn("billing", DEEPLINE._execute_output(response, "tool", entity))

    def test_policy_rejection_does_not_override_nested_auth_failure(self):
        response = {"status": "failed", "error": {"message": "default send policy rejected the address"},
                    "toolResponse": {"status": "auth_failed", "raw": {"address": "ada@example.com", "status": "catch-all"}}}
        body = DEEPLINE._execute_output(response, "validator", "email_validation")
        self.assertNotIn(body["status"], {"ok", "partial"})
        self.assertEqual(body["results"], [])

    def test_billing_is_not_prorated_by_output_limit_or_lost_on_failure(self):
        response = {"results": [{"company": "First"}, {"company": "Second"}],
                    "billing": {"credits_charged": 2, "cost_usd": .2}}
        body = DEEPLINE._execute_output(response, "tool", "company", limit=1)
        self.assertEqual(len(body["results"]), 1)
        self.assertEqual(body["billing"]["credits_charged"], 2)
        response.update(status="failed", error={"message": "provider failed"})
        with mock.patch.object(DEEPLINE, "_invoke", return_value=(1, json.dumps(response), "")):
            body, _ = DEEPLINE.run({"operation": "execute", "tool": "tool", "payload": {}})
        self.assertEqual(body["status"], "provider_error")
        self.assertEqual(body["billing"]["credits_charged"], 2)


class ReceiptHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        exporter.ExportXlsxTests.setUpClass()

    def test_padded_route_ids_do_not_bypass_order_checks(self):
        for factory, check in (
            (accepted_email_result, lambda d: not VALIDATOR.validate_run(d)),
            (exporter.accepted_document, lambda d: exporter.ExportXlsxTests().run_rows_json(d).returncode == 0),
        ):
            for reverse in (False, True):
                for pad_routes in (False, True):
                    with self.subTest(factory=factory.__name__, reverse=reverse, pad_routes=pad_routes):
                        document = with_fallback(factory())
                        receipt = document["accepted"][0]["primary_contact"]["email_validation"]
                        for r in (receipt, receipt["fallback"]):
                            r["source"]["route_id"] = " " + r["source"]["route_id"] + " "
                        if pad_routes:
                            for route in document["routes"]:
                                route["route_id"] = " " + route["route_id"] + " "
                        if reverse:
                            document["routes"].reverse()
                        self.assertEqual(check(document), not reverse)

    def test_export_checks_stored_primary_email_even_when_not_requested(self):
        document = exporter.accepted_document()
        document["request"]["contact_fields"] = []
        document["accepted"][0]["primary_contact"]["email_validation"]["status"] = "invalid"
        result = exporter.ExportXlsxTests().run_rows_json(document)
        self.assertEqual(result.returncode, 2)

    def test_all_stored_emails_have_consistent_syntax_validation(self):
        for factory, check in (
            (accepted_email_result, lambda d: not VALIDATOR.validate_run(d)),
            (exporter.accepted_document, lambda d: exporter.ExportXlsxTests().run_rows_json(d).returncode == 0),
        ):
            for email in ("ada@@example.com", "ada@example", "ada\n@example.com", "not-an-email"):
                for backup in (False, True):
                    with self.subTest(factory=factory.__name__, email=email, backup=backup):
                        document = factory()
                        row = document["accepted"][0]
                        contact = row["primary_contact"]
                        if backup:
                            contact = copy.deepcopy(contact)
                            row["backup_contacts"] = [contact]
                        contact["email"] = email
                        contact["email_validation"]["email"] = email
                        self.assertFalse(check(document))
