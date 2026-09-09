import copy
import json
import unittest
from unittest.mock import patch

from test_provider_scripts import DEEPLINE


class NoMatchEnvelopeTests(unittest.TestCase):
    HUNTER_MISS = {"ok": False, "error": {
        "message": "not_found: The domain does not exist in our database",
        "code": "UPSTREAM_NOT_FOUND", "details": {"statusCode": 404}}}

    def hunter_result(self, body, **overrides):
        request = {"operation": "execute", "tool": "hunter_companies_find",
                   "entity_type": "company", "limit": 1, **overrides}
        with patch.object(DEEPLINE, "_invoke", return_value=(1, json.dumps(body), "CLI update notice")):
            return DEEPLINE._run_command(request, ["fake-cli"], 10)[0]

    def test_hunter_known_data_absence_keeps_billing_and_empty_results(self):
        body = {**self.HUNTER_MISS, "billing": {"credits_charged": 0, "cost_usd": 0}}
        result = self.hunter_result(body)
        self.assertEqual(result["status"], "no_results")
        self.assertEqual(result["results"], [])
        self.assertEqual(result["evidence"], [])
        self.assertEqual(result["billing"]["credits_charged"], 0)
        self.assertIn("not_found", result["error"]["message"])

    def test_hunter_no_match_is_scoped_to_company_execute(self):
        for overrides in ({"tool": "different_tool"}, {"operation": "describe"},
                          {"entity_type": "email_validation"}):
            self.assertNotEqual(self.hunter_result(self.HUNTER_MISS, **overrides)["status"], "no_results")

    def test_generic_404_and_conflicting_hunter_errors_are_not_empty_success(self):
        for field, value in (("message", "not_found: endpoint unavailable"),
                             ("code", "AUTH_FAILED"), ("details", {"statusCode": 403})):
            body = copy.deepcopy(self.HUNTER_MISS)
            body["error"][field] = value
            self.assertNotEqual(self.hunter_result(body)["status"], "no_results")
        for extra in ({"results": [{"company": "Unexpected"}]}, {"status": "timeout"}):
            self.assertNotEqual(self.hunter_result({**self.HUNTER_MISS, **extra})["status"], "no_results")

    def test_canonical_no_match_keeps_billing_and_empty_results(self):
        body = {"status": "no_result", "toolResponse": {
            "rawV2": {"data": {"error": True, "error_code": "NO_MATCH", "status": "no_result"},
                      "meta": {"status": 400}},
            "raw": {"error": True, "error_code": "NO_MATCH", "status": "no_result"}},
            "billing": {"credits_charged": 0, "cost_usd": 0}}
        result = DEEPLINE._execute_output(body, "prospeo_enrich_company", limit=1)
        self.assertEqual(result["status"], "no_results")
        self.assertEqual(result["results"], [])
        self.assertEqual(result["evidence"], [])
        self.assertEqual(result["billing"]["credits_charged"], 0)

    def test_failures_are_not_overridden_by_nested_no_match(self):
        for status in ("auth_failed", "timeout", "schema_error", "provider_error"):
            body = {"status": status, "toolResponse": {"raw": {"status": "no_result"}}}
            self.assertEqual(DEEPLINE._execute_output(body, "test")["status"], status)

    def test_conflicting_envelope_error_does_not_pass(self):
        for extra in ({"error": "authentication failed"}, {"toolResponse": {"status": "auth_failed"}}):
            result = DEEPLINE._execute_output({"status": "no_result", **extra}, "test")
            self.assertIn(result["status"], DEEPLINE._FAILURE_STATUSES)

    def test_unknown_and_nested_only_no_match_stay_unresolved(self):
        for body in ({"unexpected": 1}, {"toolResponse": {"raw": {"error_code": "NO_MATCH"}}}):
            self.assertEqual(DEEPLINE._execute_output(body, "test")["status"], "schema_error")

    def test_conflicting_positive_row_is_not_promoted(self):
        result = DEEPLINE._execute_output({"status": "no_result", "results": [{"company": "Unexpected"}]}, "test", "company")
        self.assertEqual(result["status"], "schema_error")
        self.assertEqual(result["results"], [])
        self.assertEqual(result["entity_type"], "company")


if __name__ == "__main__":
    unittest.main()
