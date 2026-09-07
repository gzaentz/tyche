import copy
import unittest

from test_output_contract import VALIDATOR, cost_result, shortfall_result
from test_record_route import MODULE as RECORDER


class RecoveryTests(unittest.TestCase):
    def paid_route(self, amount=5.07, basis="estimated"):
        return {"route_id": "paid", "provider": "deepline", "paid_calls": 1,
                "provider_status": "ok", "accepted_leads_before_call": 1,
                "cost_basis": basis, "cost_credits": amount if basis == "actual" else None,
                "cost_upper_bound_credits": amount}

    def test_default_warning_neither_stops_nor_mutates_the_run(self):
        document = cost_result([self.paid_route()])
        before = copy.deepcopy(document)
        progress = VALIDATOR.calculate_progress(document)
        self.assertEqual(VALIDATOR.validate_run(document), [])
        self.assertTrue(progress["deepline_since_last_lead"]["strategy_review_due"])
        self.assertEqual(progress["deepline_since_last_lead"]["maximum_credits"], 5.07)
        self.assertEqual(document, before)
        document["budget"]["limits"]["max_deepline_credits_per_next_lead"] = 5
        self.assertTrue(any("next-lead allowance exceeded" in e for e in VALIDATOR.validate_run(document)))

    def test_warning_never_overrides_total_budget(self):
        document = cost_result([self.paid_route()])
        document["budget"]["limits"]["deepline_credits"] = 5
        self.assertTrue(VALIDATOR.validate_run(document))

    def test_unknown_or_unmarked_cost_is_not_free(self):
        for change in ({"cost_basis": "unknown", "cost_upper_bound_credits": None},
                       {"accepted_leads_before_call": None}):
            document = cost_result([{**self.paid_route(), **change}])
            progress = VALIDATOR.calculate_progress(document)
            self.assertIsNone(progress["deepline_since_last_lead"]["maximum_credits"])
            self.assertIsNone(progress["deepline_since_last_lead"]["strategy_review_due"])
            self.assertTrue(progress["warnings"])

    def test_completed_lead_resets_warning_without_resetting_total_cost(self):
        route = self.paid_route(8)
        route["accepted_leads_before_call"] = 0
        document = cost_result([route])
        progress = VALIDATOR.calculate_progress(document)
        self.assertFalse(progress["deepline_since_last_lead"]["strategy_review_due"])
        self.assertEqual(VALIDATOR.calculate_cost_summary(document)["deepline"]["maximum_credits"], 8)

    def test_sanitized_wholesale_shortfall_retains_qualified_account(self):
        document = cost_result([self.paid_route()])
        checks = [{"criterion": "B2B wholesale and annual turnover", "importance": "required",
                   "status": "pass", "claim": "Meets the account gate", "evidence": []}]
        contact = {"stage": "contact", "candidate": {"company": "Wholesale B", "domain": "b.example"},
                   "reason_code": "missing_email", "reason_text": "Owner verified. Read the official staff page next.",
                   "qualification_checks": checks}
        document["unresolved"] = [contact, copy.deepcopy(contact),
            {"stage": "account", "candidate": {"company": "Wholesale C", "domain": "c.example"},
             "reason_code": "missing_account_evidence", "reason_text": "Turnover unknown. Requires registry accounts."},
            {"stage": "route", "route_id": "failed", "candidate": {"company": "Provider attempt"}},
            {"stage": "contact", "candidate": {"domain": "example-0.org"}, "reason_code": "contact_target_shortfall"}]
        document["routes"].append({"route_id": "failed", "provider_status": "schema_error"})
        before = copy.deepcopy(document)
        progress = VALIDATOR.calculate_progress(document)
        self.assertEqual(progress["accepted_companies"], 1)
        self.assertEqual(progress["account_evidence_missing"], 1)
        self.assertEqual(progress["contact_completion_missing"], 1)
        self.assertEqual(progress["provider_or_route_failures"], 1)
        self.assertEqual(progress["unresolved_contacts"][0]["qualification_checks"][0], checks[0])
        self.assertEqual(document, before)

    def linked_document(self, child_state="exhausted"):
        document = shortfall_result()
        root = document["stop_audit"]["route_frontier"][0]
        root.update(exhaustion_basis="continuation_exhausted", continuation_route_ids=["follow-up"])
        document["routes"][0]["provider_status"] = "ok"
        document["routes"].append({"route_id": "follow-up", "provider_status": "no_results", "rows_returned": 0})
        document["stop_audit"]["route_frontier"].append({"route_id": "follow-up", "state": child_state,
            "reason": "The follow-up query returned no companies.", "exhaustion_basis": "no_results"})
        return document

    def test_specific_terminal_follow_up_allows_source_exhaustion(self):
        self.assertEqual(VALIDATOR.validate_run(self.linked_document()), [])

    def test_paid_failure_cannot_close_an_actionable_public_follow_up(self):
        document = self.linked_document("continuable")
        document["stop_audit"]["route_frontier"][1]["provider"] = "public_web"
        errors = VALIDATOR.validate_run(document)
        self.assertTrue(any("actionable continuation" in e for e in errors))
        self.assertTrue(any("run must continue" in e for e in errors))

    def test_generic_review_does_not_establish_continuation_exhaustion(self):
        document = self.linked_document()
        root = document["stop_audit"]["route_frontier"][0]
        root.pop("continuation_route_ids")
        root["reason"] = "This source was reviewed and all candidates resolved."
        self.assertTrue(any("must reference" in e for e in VALIDATOR.validate_run(document)))

    def test_missing_duplicate_and_circular_references_fail(self):
        for refs in (["missing"], ["follow-up", "follow-up"], ["route-1"]):
            document = self.linked_document()
            document["stop_audit"]["route_frontier"][0]["continuation_route_ids"] = refs
            self.assertTrue(VALIDATOR.validate_run(document))
        document = self.linked_document()
        document["stop_audit"]["route_frontier"][1].update(
            exhaustion_basis="continuation_exhausted", continuation_route_ids=["route-1"])
        self.assertTrue(any("cycle" in e for e in VALIDATOR.validate_run(document)))

    def test_nonempty_response_cannot_be_relabelled_no_results(self):
        document = shortfall_result()
        document["routes"][0].update(provider_status="ok", rows_returned=3)
        self.assertTrue(any("claims no_results" in e for e in VALIDATOR.validate_run(document)))

    def test_recorder_refuses_exhaustion_before_follow_up_finishes(self):
        document = self.linked_document("untried")
        root = document["stop_audit"]["route_frontier"][0]
        root.update(phase="account_discovery", provider="public_web", operation="search", request_summary="wholesalers")
        receipt = {**{key: root[key] for key in RECORDER.IDENTITY}, "provider_status": "ok"}
        document["routes"][0] = receipt
        with self.assertRaisesRegex(ValueError, "actionable continuation"):
            RECORDER.record(document, root)


if __name__ == "__main__":
    unittest.main()
