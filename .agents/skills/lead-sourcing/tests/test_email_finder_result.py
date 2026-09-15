from __future__ import annotations

import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import deepline


class EmailFinderResultTests(unittest.TestCase):
    def test_bare_email_response_preserves_address_and_charge_without_validation(self):
        for key in ("rawV2", "raw"):
            for serialized in (False, True):
                with self.subTest(key=key, serialized=serialized):
                    raw = {"email": "buyer@example.test"}
                    parsed = {
                        "status": "completed",
                        "toolResponse": {key: json.dumps(raw) if serialized else raw},
                        "billing": {"credits_charged": 0.28, "cost_usd": 0.028},
                    }
                    result = deepline._execute_output(parsed, "limadata_find_work_email")
                    self.assertEqual(result["status"], "ok")
                    self.assertEqual(len(result["results"]), 1)
                    row = result["results"][0]
                    self.assertEqual(row["email"], raw["email"])
                    self.assertIsNone(row["full_name"])
                    self.assertNotIn("email_status", row)
                    self.assertFalse(deepline._is_email_validation_record(row))
                    self.assertEqual(result["billing"], parsed["billing"])

    def test_bare_email_does_not_hide_failure_or_accept_malformed_values(self):
        for email in (None, "", [], "not-an-address", "two@example.test other@example.test"):
            with self.subTest(email=email):
                result = deepline._execute_output(
                    {"toolResponse": {"rawV2": {"email": email}}}, "email_finder")
                self.assertEqual(result["status"], "schema_error")
                self.assertEqual(result["results"], [])
        failed = {"toolResponse": {"rawV2": {
            "status": "FAILED", "output": {"email": "buyer@example.test"}}}}
        result = deepline._execute_output(failed, "email_finder")
        self.assertEqual(result["status"], "provider_error")
        self.assertEqual(result["results"], [])


if __name__ == "__main__":
    unittest.main()
