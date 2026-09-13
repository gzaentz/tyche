import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_output_contract import VALIDATOR_PATH, cost_result, shortfall_result
from test_stop_policy import STARTED_AT, action, stop_document
from linkedin_fixtures import write_linkedin_receipts


def checkpoint_document():
    routes = [
        {"route_id": "confirmed", "provider": "deepline", "provider_status": "ok",
         "paid_calls": 28, "cost_basis": "actual", "cost_credits": 7.41,
         "cost_upper_bound_credits": 7.41},
        {"route_id": "uncertain", "provider": "deepline", "provider_status": "partial",
         "paid_calls": 1, "cost_basis": "estimated", "cost_credits": None,
         "cost_upper_bound_credits": 24.07},
    ]
    document = cost_result(routes, accepted_contacts=5)
    document["request"]["target_count"] = 25
    document["budget"]["limits"].update(scrapingdog_credits=0, max_paid_calls=40)
    document.pop("stop_reason")
    document["unresolved"] = [
        {"stage": "account", "reason_code": "missing_contact_evidence",
         "reason_text": "Requested buyer evidence remains incomplete.",
         "candidate": {"company": f"Pending {index}", "domain": f"pending-{index}.org"}}
        for index in range(20)
    ]
    document["stop_audit"] = copy.deepcopy(shortfall_result()["stop_audit"])
    document["stop_audit"].update(
        target_shortfall=20, candidate_companies_reviewed=25, substantive_account_reviews=25,
        provider_call_capacity={"deepline": "unknown", "scrapingdog": "unavailable",
                                "paid_calls_remaining": 11},
        route_frontier=[
            {"route_id": route["route_id"], "state": "exhausted",
             "reason": "Returned rows reviewed.", "exhaustion_basis": "no_new_unique_candidates"}
            for route in document["routes"]
        ],
    )
    document["stop_check"] = {"started_at": STARTED_AT, "next_actions": [
        action("discover-companies"),
        *[action(f"recover-{index}", scope=f"pending-{index}.org") for index in range(20)],
        action("paid-recovery", provider="deepline", paid_calls=1, cost_upper_bound_credits=0.55),
    ]}
    return document


class DeliveryGateTests(unittest.TestCase):
    def run_cli(self, document, *flags):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            write_linkedin_receipts(path, document)
            path.write_text(json.dumps(document), encoding="utf-8")
            before = path.read_bytes()
            files_before = {p: p.read_bytes() for p in Path(directory).rglob("*") if p.is_file()}
            result = subprocess.run(
                [sys.executable, str(VALIDATOR_PATH), str(path), *flags],
                capture_output=True, text=True, timeout=10,
            )
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual({p: p.read_bytes() for p in Path(directory).rglob("*") if p.is_file()}, files_before)
            return result.returncode, json.loads(result.stdout)

    def test_old_40_call_cap_cannot_authorize_shortfall_or_block_paid_recovery(self):
        for paid_calls in (40, 41):
            with self.subTest(paid_calls=paid_calls):
                document = checkpoint_document()
                document["routes"][0]["paid_calls"] = paid_calls - 1
                document["budget"]["paid_calls"] = paid_calls
                document["stop_audit"]["provider_call_capacity"]["paid_calls_remaining"] = 0
                code, output = self.run_cli(document)
                self.assertEqual(code, 2)
                self.assertFalse(output["delivery_allowed"])
                self.assertEqual(output["stop_decision"]["decision"], "continue")
                self.assertIn("paid-recovery", output["stop_decision"]["eligible_actions"])
                self.assertEqual(len(output["errors"]), 1, output["errors"])
                self.assertIn("requires continuation", output["errors"][0])

    def test_five_of_25_checkpoint_requires_continuation_and_blocks_delivery(self):
        document = checkpoint_document()
        code, output = self.run_cli(document, "--show-cost-summary")
        self.assertEqual(code, 2)
        self.assertFalse(output["valid"])
        self.assertFalse(output["delivery_allowed"])
        self.assertEqual(output["stop_decision"]["decision"], "continue")
        self.assertEqual(len(output["stop_decision"]["eligible_actions"]), 22)
        self.assertEqual(output["calculated_cost_summary"]["deepline"]["maximum_credits"], 31.48)
        self.assertEqual(len(output["errors"]), 1, output["errors"])
        self.assertIn("requires continuation", output["errors"][0])

        # Planning success is deliberately distinct from final-delivery success.
        code, planning = self.run_cli(document, "--check-stop")
        self.assertEqual(code, 0)
        self.assertEqual(planning["decision"], "continue")
        self.assertFalse(planning["delivery_allowed"])

    def test_full_target_allows_delivery_only_after_full_strict_validation(self):
        document = cost_result([], accepted_contacts=25)
        document["stop_check"] = {"started_at": STARTED_AT, "next_actions": []}
        code, output = self.run_cli(document)
        self.assertEqual(code, 0, output)
        self.assertTrue(output["valid"])
        self.assertTrue(output["delivery_allowed"])
        self.assertEqual(output["stop_decision"]["decision"], "target_met")

        document["request"]["contact_fields"] = ["email"]
        code, output = self.run_cli(document)
        self.assertEqual(code, 2)
        self.assertEqual(output["stop_decision"]["decision"], "target_met")
        self.assertFalse(output["delivery_allowed"])
        self.assertTrue(any("requires requested email" in error for error in output["errors"]))

    def test_valid_explicit_limit_allows_shortfall_without_closing_routes(self):
        document = shortfall_result(frontier_state="continuable", stop_reason="time_limit_reached")
        document["request"]["max_duration_seconds"] = 1
        document["stop_check"] = {"started_at": STARTED_AT, "next_actions": [action("more-research")]}
        code, output = self.run_cli(document)
        self.assertEqual(code, 0, output)
        self.assertTrue(output["delivery_allowed"])
        self.assertEqual(output["stop_decision"]["decision"], "time_limit_reached")

    def test_valid_budget_stop_allows_shortfall(self):
        from test_stop_policy import add_catalog_review
        document = shortfall_result(frontier_state="continuable", stop_reason="budget_exhausted")
        planned = stop_document(
            [action("paid", provider="deepline", paid_calls=1, cost_upper_bound_credits=2)],
            limits={"deepline_credits": 1},
        )
        document.update(budget=planned["budget"], stop_check=planned["stop_check"])
        document["routes"][0]["paid_calls"] = 0
        add_catalog_review(document)
        code, output = self.run_cli(document)
        self.assertEqual(code, 0, output)
        self.assertTrue(output["delivery_allowed"])
        self.assertEqual(output["stop_decision"]["decision"], "budget_exhausted")

    def test_corrupt_accounting_requires_repair_and_blocks_delivery(self):
        document = checkpoint_document()
        document["budget"]["paid_calls"] = 0
        code, output = self.run_cli(document)
        self.assertEqual(code, 2)
        self.assertFalse(output["delivery_allowed"])
        self.assertEqual(output["stop_decision"]["decision"], "repair_state")
        self.assertEqual(output["stop_decision"]["eligible_actions"], [])

    def test_legacy_success_never_authorizes_current_delivery(self):
        code, output = self.run_cli(shortfall_result(), "--legacy-stop-policy")
        self.assertEqual(code, 0, output)
        self.assertTrue(output["valid"])
        self.assertFalse(output["delivery_allowed"])


if __name__ == "__main__":
    unittest.main()
