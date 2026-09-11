import copy
from fractions import Fraction
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

from test_output_contract import VALIDATOR, VALIDATOR_PATH, cost_result


class ProgressMatrixTests(unittest.TestCase):
    def test_400_mixed_cost_sequences_match_independent_fraction_oracle(self):
        rng = random.Random(20260908)
        for case in range(400):
            accepted = rng.randrange(1, 4)
            routes = []
            for index in range(rng.randrange(1, 15)):
                basis = rng.choice(["actual", "estimated", "unknown"])
                value = rng.choice([0, .01, .28, .55, 1.1, 2.51, 4.99, 5, 5.01])
                routes.append({"route_id": f"r{index}", "provider": rng.choice(["deepline", "scrapingdog"]),
                    "paid_calls": 1, "provider_status": rng.choice(["ok", "schema_error", "no_results"]),
                    "accepted_leads_before_call": rng.choice(list(range(accepted + 4)) + [None]),
                    "cost_basis": basis, "cost_credits": value if basis == "actual" else None,
                    "cost_upper_bound_credits": value if basis != "unknown" else None})
            document = cost_result(routes, accepted)
            before = copy.deepcopy(document)
            current = [r for r in routes if r["provider"] == "deepline"
                       and r["accepted_leads_before_call"] is not None
                       and r["accepted_leads_before_call"] >= accepted]
            unmarked = any(r["provider"] == "deepline" and r["accepted_leads_before_call"] is None for r in routes)
            unknown = unmarked or any(r["cost_basis"] == "unknown" for r in current)
            confirmed = sum((Fraction(str(r["cost_credits"])) for r in current if r["cost_basis"] == "actual"), Fraction())
            maximum = sum((Fraction(str(r["cost_upper_bound_credits"])) for r in current if r["cost_basis"] != "unknown"), Fraction())
            output = VALIDATOR.calculate_progress(document)["deepline_since_last_lead"]
            self.assertEqual(output["confirmed_credits"], float(confirmed), case)
            self.assertEqual(output["maximum_credits"], None if unknown else float(maximum), case)
            self.assertEqual(output["strategy_review_due"], True if maximum >= 5 else (None if unknown else False), case)
            self.assertEqual(document, before, case)

    def test_strategy_boundary_and_explicit_cap_are_independent(self):
        for basis in ("actual", "estimated"):
            for amount in (0, 4.99, 5, 5.01, 10):
                route = {"route_id": "paid", "provider": "deepline", "paid_calls": 1,
                         "accepted_leads_before_call": 1, "cost_basis": basis,
                         "cost_credits": amount if basis == "actual" else None,
                         "cost_upper_bound_credits": amount}
                for cap in (None, 0, 5, 10):
                    with self.subTest(basis=basis, amount=amount, cap=cap):
                        document = cost_result([route])
                        if cap is not None:
                            document["request"]["budget"] = {"max_deepline_credits_per_next_lead": cap}
                            document["budget"]["limits"]["max_deepline_credits_per_next_lead"] = cap
                        errors = VALIDATOR.validate_run(document)
                        self.assertEqual(bool(errors), cap is not None and amount > cap, errors)
                        self.assertEqual(VALIDATOR.calculate_progress(document)["deepline_since_last_lead"]["strategy_review_due"], amount >= 5)

    def test_progress_cli_does_not_turn_a_warning_into_a_failure(self):
        route = {"route_id": "paid", "provider": "deepline", "paid_calls": 1,
                 "accepted_leads_before_call": 1, "cost_basis": "actual",
                 "cost_credits": 5.06, "cost_upper_bound_credits": 5.06}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results.json"
            document = cost_result([route])
            document["stop_check"] = {"started_at": "2026-01-01T00:00:00Z", "next_actions": []}
            path.write_text(json.dumps(document))
            original = path.read_bytes()
            result = subprocess.run([sys.executable, str(VALIDATOR_PATH), str(path), "--show-progress", "--show-cost-summary"],
                                    text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            self.assertTrue(output["valid"])
            self.assertTrue(output["progress"]["warnings"])
            self.assertEqual(output["calculated_cost_summary"]["deepline"]["confirmed_credits"], 5.06)
            self.assertEqual(path.read_bytes(), original)

    def test_account_and_contact_groups_keep_independent_companies(self):
        document = {"accepted": [{"company": {"domain": "done.example"}}], "unresolved": [
            {"stage": "account", "candidate": {"company": "A", "domain": "WWW.a.example"}, "reason_code": "missing_account_evidence"},
            {"stage": "account", "candidate": {"company": "A", "domain": "a.example"}, "reason_code": "missing_account_evidence"},
            {"stage": "contact", "candidate": {"company": "A", "domain": "a.example"}, "reason_code": "missing_email"},
            {"stage": "contact", "candidate": {"company": "B", "domain": "b.example"}, "reason_code": "missing_email"},
            {"stage": "contact", "candidate": {"domain": "done.example"}, "reason_code": "contact_target_shortfall"},
            {"stage": "route", "candidate": {"company": "Provider attempt"}, "route_id": "failed"},
        ], "routes": [{"route_id": "failed", "provider_status": "timeout"}]}
        summary = VALIDATOR.calculate_progress(document)
        self.assertEqual(summary["accepted_companies"], 1)
        self.assertEqual(summary["account_evidence_missing"], 1)
        self.assertEqual(summary["contact_completion_missing"], 1)
        self.assertEqual(summary["provider_or_route_failures"], 1)
        self.assertEqual(summary["unresolved_contacts"][0]["candidate"]["company"], "B")

    def test_funnel_keeps_buyer_and_email_completion_separate(self):
        document = {"accepted": [{"company": {"domain": "done.example"}}], "unresolved": [
            {"stage": "account", "candidate": {"domain": "size.example"}, "reason_code": "missing_account_evidence"},
            {"stage": "contact", "candidate": {"domain": "role.example", "full_name": "Person", "current_title": "Manager"}, "reason_code": "current_role_unverified"},
            {"stage": "contact", "candidate": {"domain": "email.example", "full_name": "Owner", "current_title": "Founder"}, "reason_code": "missing_email"},
        ]}
        self.assertEqual(VALIDATOR.calculate_progress(document)["stages"],
                         {"company_fit": 3, "buyer_verified": 2, "completed_leads": 1})


if __name__ == "__main__":
    unittest.main()
