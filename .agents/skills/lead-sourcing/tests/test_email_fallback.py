from __future__ import annotations

import copy
import json
import unittest

from test_output_contract import VALIDATOR, accepted_email_result, load_schemas
import test_export_xlsx as exporter_tests


def with_fallback(document, status="catch-all"):
    receipt = document["accepted"][0]["primary_contact"]["email_validation"]
    receipt["status"] = status
    receipt["fallback"] = {
        "email": receipt["email"], "status": "success", "result": "deliverable",
        "score": 99,
        "source": {
            "provider": "deepline", "validator": "bounceban", "operation": "execute",
            "tool": "dynamic-fallback-tool", "route_id": "fallback-1",
        },
    }
    document["routes"].append({
        "route_id": "fallback-1", "phase": "email_validation", "provider": "deepline",
        "operation": "execute", "tool": "dynamic-fallback-tool",
        "provider_status": "ok", "paid_calls": 1,
    })
    return document


class EmailFallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        exporter_tests.ExportXlsxTests.setUpClass()

    def check_both(self, status="catch-all", mutate=None, passes=True):
        for factory, check in (
            (accepted_email_result, lambda d: not VALIDATOR.validate_run(d)),
            (exporter_tests.accepted_document, lambda d: exporter_tests.ExportXlsxTests().run_rows_json(d).returncode == 0),
        ):
            document = with_fallback(factory(), status)
            if mutate:
                mutate(document, document["accepted"][0]["primary_contact"]["email_validation"])
            self.assertEqual(check(document), passes, json.dumps(document))

    def test_only_catch_all_or_unknown_verdicts_can_use_fallback(self):
        for status in ("catch-all", "unknown", " UNKNOWN "):
            with self.subTest(status=status):
                self.check_both(status)
        for status in ("valid", "invalid", "do_not_mail", "spamtrap", "abuse", "", "provider_error", "new_status"):
            with self.subTest(status=status):
                self.check_both(status, passes=False)

    def test_verdict_is_not_api_success(self):
        for verdict in ("risky", "unknown", "undeliverable", "success", "", None):
            with self.subTest(verdict=verdict):
                self.check_both(mutate=lambda d, r: r["fallback"].update(result=verdict), passes=False)
        self.check_both(mutate=lambda d, r: r["fallback"].update(status="failed"), passes=False)
        self.check_both(mutate=lambda d, r: r["fallback"].update(status=" SUCCESS ", result=" DELIVERABLE "))

    def test_service_failures_allow_one_deliverable_fallback(self):
        for status in ("provider_error", "timeout", "rate_limited", "auth_failed", "quota_exceeded"):
            def outage(document, receipt):
                receipt.update(status=None, provider_status=status)
                document["routes"][0].update(provider_status=status)
            with self.subTest(status=status):
                self.check_both(mutate=outage)

    def test_outage_must_match_failed_route_and_not_override_verdicts(self):
        mutations = [
            lambda d, r: r.pop("provider_status"),
            lambda d, r: r.update(provider_status=[]),
            lambda d, r: r.update(provider_status={}),
            lambda d, r: r.update(provider_status=None),
            lambda d, r: r.pop("status"),
            lambda d, r: r.update(status=""),
            lambda d, r: r.update(status="invalid"),
            lambda d, r: r.update(status="do_not_mail"),
            lambda d, r: r.update(status="spamtrap"),
            lambda d, r: r.update(status="abuse"),
            lambda d, r: r.update(status="unknown"),
            lambda d, r: d["routes"][0].update(provider_status="ok"),
            lambda d, r: d["routes"][0].update(provider_status="timeout"),
            lambda d, r: d["routes"][0].update(paid_calls=0),
            lambda d, r: d["routes"].pop(0),
            lambda d, r: r["fallback"].update(email="other@example.com"),
            lambda d, r: r["fallback"].update(result="risky"),
            lambda d, r: d["routes"][-1].update(provider_status="provider_error"),
            lambda d, r: r["fallback"].update(fallback={}),
            lambda d, r: r.pop("fallback"),
            lambda d, r: d["routes"].reverse(),
        ]
        for index, mutation in enumerate(mutations):
            def outage(document, receipt):
                receipt.update(status=None, provider_status="provider_error")
                document["routes"][0].update(provider_status="provider_error")
                mutation(document, receipt)
            with self.subTest(index=index):
                self.check_both(mutate=outage, passes=False)
        for status in ("ok", "partial", "no_results", "schema_error", "config_error"):
            def not_outage(document, receipt):
                receipt.update(status=None, provider_status=status)
                document["routes"][0].update(provider_status=status)
            with self.subTest(status=status):
                self.check_both(mutate=not_outage, passes=False)

    def test_both_receipts_require_exact_email_source_and_ordered_paid_routes(self):
        mutations = [
            lambda d, r: r["fallback"].update(email="other@example.com"),
            lambda d, r: r.update(email="other@example.com"),
            lambda d, r: r["fallback"].pop("result"),
            lambda d, r: r["fallback"].pop("source"),
            lambda d, r: r["source"].update(validator="bounceban"),
            lambda d, r: r["fallback"]["source"].update(validator="zerobounce"),
            lambda d, r: r["fallback"]["source"].update(tool="wrong-tool"),
            lambda d, r: r["fallback"]["source"].update(route_id=d["routes"][0]["route_id"]),
            lambda d, r: d["routes"].reverse(),
            lambda d, r: d["routes"][-1].update(provider_status="timeout"),
            lambda d, r: d["routes"][-1].update(paid_calls=0),
            lambda d, r: d["routes"][-1].update(paid_calls=2),
            lambda d, r: r["fallback"].update(fallback={}),
            lambda d, r: d["routes"].append(copy.deepcopy(d["routes"][-1])),
        ]
        for index, mutation in enumerate(mutations):
            with self.subTest(index=index):
                self.check_both(mutate=mutation, passes=False)

    def test_backups_use_same_gate(self):
        def backup(document, receipt):
            row = document["accepted"][0]
            row["backup_contacts"] = [copy.deepcopy(row["primary_contact"])]
            row["primary_contact"]["email_validation"]["status"] = "valid"
            row["primary_contact"]["email_validation"].pop("fallback")
        self.check_both(mutate=backup)
        def bad_backup(document, receipt):
            backup(document, receipt)
            document["accepted"][0]["backup_contacts"][0]["email_validation"]["fallback"]["result"] = "risky"
        self.check_both(mutate=bad_backup, passes=False)

    def test_schema_preserves_original_and_single_fallback(self):
        definitions = load_schemas()[1]["$defs"]
        self.assertEqual(definitions["email_validation"]["properties"]["fallback"]["$ref"], "#/$defs/bounceban_validation")
        fallback = definitions["bounceban_validation"]
        self.assertFalse(fallback["additionalProperties"])
        self.assertIn("result", fallback["required"])
        self.assertNotIn("fallback", fallback["properties"])


if __name__ == "__main__":
    unittest.main()
