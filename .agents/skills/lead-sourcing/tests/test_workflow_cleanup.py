import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock

import test_attempt_execution as fixtures
from test_output_contract import VALIDATOR, VALIDATOR_PATH, cost_result, shortfall_result
from test_stop_policy import NOW, STARTED_AT, action, add_catalog_review

runner = fixtures.runner


def exhausted_result():
    doc = cost_result([], accepted_contacts=0)
    doc["request"]["target_count"] = 25
    doc["stop_reason"] = "no_productive_route"
    doc["stop_check"] = {"started_at": STARTED_AT, "next_actions": []}
    doc["stop_audit"] = copy.deepcopy(shortfall_result()["stop_audit"])
    doc["stop_audit"].update(target_shortfall=25, route_frontier=[])
    add_attempts(doc, "discovery")
    add_catalog_review(doc)
    return doc


def add_attempts(doc, scope):
    snapshot = VALIDATOR.progress_snapshot(doc)
    for approach in ("localized-product-pages", "specialist-directory"):
        rid = scope + "-" + approach
        doc["routes"].append(dict(route_id=rid, scope=scope, approach=approach,
            phase="account_discovery" if scope == "discovery" else "account_verification",
            provider="public_web", operation="search", paid_calls=0, cost_basis="actual",
            request_fingerprint=hashlib.sha256(rid.encode()).hexdigest(),
            cost_credits=0, cost_upper_bound_credits=0, provider_status="no_results",
            rows_returned=0, rows_usable=0, progress_before=snapshot))
        doc["stop_audit"]["route_frontier"].append(dict(route_id=rid, scope=scope,
            state="exhausted", exhaustion_basis="no_results",
            reason="This materially different search returned no further evidence."))


class ScopedResearchTests(unittest.TestCase):
    def setUp(self):
        # Reuse the real adapter/ledger fixture, without inheriting its test cases.
        self.fixture = fixtures.AttemptExecutionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.path = self.fixture.path
        self.bad = dict(stage="contact", candidate={"domain": "needs-review.example"},
            reason_code="missing_contact_evidence", reason_text="Owner evidence is missing.",
            qualification_checks=[dict(criterion="owner", importance="required",
                                       status="unknown", evidence=[])])
        self.fixture.doc["unresolved"] = [self.bad]
        self.path.write_text(json.dumps(self.fixture.doc))

    def test_unrelated_catalog_and_discovery_run_without_repairing_a_draft(self):
        for spec in (self.fixture.spec("catalog"), self.fixture.spec("research", paid=True)):
            result = runner.run_attempt(self.path, spec, execute=(self.fixture.paid_response
                if spec["action"]["paid_calls"] else self.fixture.free_response))
            self.assertEqual(result["exit_code"], 0)
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved["unresolved"], [self.bad])
        self.assertTrue(VALIDATOR.qualification_errors(saved))
        self.assertTrue(VALIDATOR.validate_run(saved, require_stop_check=True, now=NOW))

    def test_unrelated_contact_lookup_runs_but_affected_company_still_fails(self):
        doc = json.loads(self.path.read_text())
        good = dict(stage="contact", candidate={"domain": "qualified.example"},
            qualification_checks=[dict(criterion="product", importance="required", status="pass",
                                       evidence=[{"url": "https://qualified.example/products"}])])
        doc["unresolved"].append(good)
        self.path.write_text(json.dumps(doc))
        spec = self.fixture.spec("qualified-owner", paid=True)
        spec["action"].update(phase="contact_discovery", scope="qualified.example")
        self.assertEqual(runner.run_attempt(self.path, spec,
            execute=self.fixture.paid_response)["exit_code"], 0)
        spec = self.fixture.spec("unqualified-owner", paid=True)
        spec["action"].update(phase="contact_discovery", scope="needs-review.example")
        dispatch = Mock()
        with self.assertRaisesRegex(ValueError, "missing or failed required evidence"):
            runner.run_attempt(self.path, spec, execute=dispatch)
        dispatch.assert_not_called()


class ExhaustionReviewTests(unittest.TestCase):
    def test_parked_company_stays_unresolved_while_fresh_discovery_runs(self):
        doc = exhausted_result()
        doc["unresolved"] = [dict(stage="account", candidate={"domain": "pending.example"},
            reason_text="Dated steel-project evidence is still missing after the saved source review.")]
        add_attempts(doc, "pending.example")
        doc["stop_check"]["next_actions"] = [dict(action("fresh"), approach="dated-planning-news")]
        before = copy.deepcopy(doc)
        decision = VALIDATOR.evaluate_stop(doc, now=NOW)
        self.assertEqual(decision["decision"], "continue")
        self.assertEqual(decision["eligible_actions"], ["fresh"])
        self.assertEqual(decision["parked_scopes"], ["pending.example"])
        self.assertEqual(decision["missing_scopes"], [])
        self.assertEqual(doc, before)
        # A useful new source can reopen the company, but changing the version
        # of an exhausted approach or relabeling the same request cannot.
        for approach, fingerprint, allowed in (
                ("SPECIALIST_directory-v117", None, False),
                ("new-label", doc["routes"][-1]["request_fingerprint"], False),
                ("dated-project-drawings", None, True)):
            recovery = dict(action("recover", scope="pending.example"), approach=approach,
                description="Check the newly discovered council drawings for this named project.")
            if fingerprint:
                recovery["request_fingerprint"] = fingerprint
            doc["stop_check"]["next_actions"] = [before["stop_check"]["next_actions"][0], recovery]
            decision = VALIDATOR.evaluate_stop(doc, now=NOW)
            self.assertEqual("recover" in decision["eligible_actions"], allowed)
            self.assertIn("fresh", decision["eligible_actions"])

    def test_parked_company_does_not_hide_pending_work_or_provider_failures(self):
        doc = exhausted_result()
        doc["unresolved"] = [dict(stage="account", candidate={"domain": "pending.example"},
            reason_text="Size is unknown.")]
        add_attempts(doc, "pending.example")
        doc["stop_check"]["next_actions"] = [dict(action("fresh"), approach="dated-planning-news")]
        doc["stop_audit"]["route_frontier"].append(dict(route_id="unchecked-drawings",
            scope="pending.example", state="untried", approach="project-drawings"))
        decision = VALIDATOR.evaluate_stop(doc, now=NOW)
        self.assertIn("unchecked-drawings", decision["missing_routes"])
        doc["routes"][-1]["provider_status"] = "timeout"
        decision = VALIDATOR.evaluate_stop(doc, now=NOW)
        self.assertEqual(decision["parked_scopes"], [])
        self.assertIn("pending.example", decision["missing_scopes"])

    def test_version_only_strategy_change_cannot_authorize_work_or_exhaustion(self):
        doc = exhausted_result()
        doc["stop_check"]["next_actions"] = [dict(action("renamed"), approach="specialist-directory-v118")]
        self.assertEqual(VALIDATOR.evaluate_stop(doc, now=NOW)["eligible_actions"], [])
        doc["stop_check"]["next_actions"] = []
        doc["routes"][1]["approach"] = doc["routes"][0]["approach"] + "-v118"
        self.assertEqual(VALIDATOR.evaluate_stop(doc, now=NOW)["decision"], "continue")

    def test_parking_reviews_every_gap_and_the_correct_research_stage(self):
        doc = exhausted_result()
        doc["unresolved"] = [dict(stage="account", candidate={"domain": "pending.example"},
            reason_text="Steel-project evidence is missing after review.")]
        add_attempts(doc, "pending.example")
        self.assertEqual(VALIDATOR._reviewed_company_scopes(doc), {"pending.example"})
        doc["unresolved"].append(dict(stage="contact", candidate={"domain": "pending.example"}))
        self.assertEqual(VALIDATOR._reviewed_company_scopes(doc), set())
        doc["unresolved"].pop()
        doc["routes"][-1]["phase"] = "email_validation"
        self.assertEqual(VALIDATOR._reviewed_company_scopes(doc), set())
        doc["unresolved"][0].update(stage="contact", reason_text="The account qualifies; no valid buyer email was found.")
        self.assertEqual(VALIDATOR._reviewed_company_scopes(doc), {"pending.example"})

    def test_full_strict_cli_delivers_honest_shortfall_without_using_up_budget(self):
        doc = exhausted_result()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results.json"
            path.write_text(json.dumps(doc))
            before = path.read_bytes()
            result = subprocess.run([sys.executable, str(VALIDATOR_PATH), str(path)],
                capture_output=True, text=True, timeout=10)
            output = json.loads(result.stdout)
            self.assertEqual(result.returncode, 0, output)
            self.assertTrue(output["delivery_allowed"])
            self.assertEqual(output["stop_decision"]["decision"], "no_productive_route")
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(doc["summary"]["accepted_companies"], 0)
            self.assertEqual(doc["budget"]["spent"]["deepline_credits"], 0)

    def test_one_search_same_approach_errors_or_missing_snapshots_cannot_stop(self):
        for change in ("one", "same", "duplicate_request", "missing_fingerprint", "failure", "snapshot", "open", "implicit"):
            with self.subTest(change=change):
                doc = exhausted_result()
                if change == "one": doc["routes"].pop(0)
                if change == "same": doc["routes"][1]["approach"] = doc["routes"][0]["approach"]
                if change == "duplicate_request": doc["routes"][1]["request_fingerprint"] = doc["routes"][0]["request_fingerprint"]
                if change == "missing_fingerprint": doc["routes"][1].pop("request_fingerprint")
                if change == "failure": doc["routes"][1]["provider_status"] = "provider_error"
                if change == "snapshot": doc["routes"][1].pop("progress_before")
                if change == "open": doc["stop_audit"]["route_frontier"][1]["state"] = "continuable"
                if change == "implicit": doc["stop_reason"] = "provider_stop"
                self.assertEqual(VALIDATOR.evaluate_stop(doc, now=NOW)["decision"], "continue")

    def test_new_qualified_company_or_planned_action_prevents_exhaustion(self):
        doc = exhausted_result()
        doc["unresolved"] = [dict(stage="contact", candidate={"domain": "new.example"})]
        self.assertIn("discovery", VALIDATOR.evaluate_stop(doc, now=NOW)["exhaustion_review_required"])
        doc = exhausted_result()
        doc["stop_check"]["next_actions"] = [dict(action("real-next-search"), approach="third-source")]
        self.assertEqual(VALIDATOR.evaluate_stop(doc, now=NOW)["eligible_actions"], ["real-next-search"])

    def test_company_review_can_add_facts_without_forcing_more_empty_searches(self):
        doc = exhausted_result()
        doc["unresolved"] = [dict(stage="account", candidate={"domain": "pending.example"},
            reason_text="The legal notice confirms size but names no owner. Available sources were reviewed; ownership remains unverified.")]
        self.assertEqual(VALIDATOR.evaluate_stop(doc, now=NOW)["exhaustion_review_required"], ["pending.example"])
        add_attempts(doc, "pending.example")
        self.assertEqual(VALIDATOR.evaluate_stop(doc, now=NOW)["catalog_review_required"], [])
        # Just one substantive company review is enough; its receipt is retained.
        doc["routes"].pop(3)
        doc["stop_audit"]["route_frontier"].pop(3)
        self.assertEqual(VALIDATOR.evaluate_stop(doc, now=NOW)["decision"], "no_productive_route")
        doc["unresolved"][0]["qualification_checks"] = [dict(criterion="size", importance="required",
            status="pass", evidence=[{"url": "https://pending.example/about"}])]
        self.assertEqual(VALIDATOR.evaluate_stop(doc, now=NOW)["decision"], "no_productive_route")
        doc["unresolved"][0].pop("reason_text")
        self.assertEqual(VALIDATOR.evaluate_stop(doc, now=NOW)["exhaustion_review_required"], ["pending.example"])

    def test_failed_latest_company_review_cannot_claim_research_exhaustion(self):
        doc = exhausted_result()
        doc["unresolved"] = [dict(stage="account", candidate={"domain": "pending.example"},
            reason_text="Owner is unresolved.")]
        add_attempts(doc, "pending.example")
        doc["routes"][-1]["provider_status"] = "provider_error"
        self.assertEqual(VALIDATOR.evaluate_stop(doc, now=NOW)["exhaustion_review_required"], ["pending.example"])

    def test_duplicate_requests_fail_full_cli_even_with_different_labels(self):
        doc = exhausted_result()
        doc["routes"][1]["request_fingerprint"] = doc["routes"][0]["request_fingerprint"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results.json"
            path.write_text(json.dumps(doc))
            result = subprocess.run([sys.executable, str(VALIDATOR_PATH), str(path)],
                capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(json.loads(result.stdout)["delivery_allowed"])

    def test_qualification_and_accounting_still_block_final_delivery(self):
        doc = exhausted_result()
        doc["accepted"] = [dict(company={"domain": "unverified.example"}, primary_contact={})]
        doc["request"]["contact_fields"] = ["email"]
        self.assertTrue(VALIDATOR.validate_run(doc, require_stop_check=True, now=NOW))
        doc = exhausted_result()
        doc["budget"]["paid_calls"] = 10
        self.assertEqual(VALIDATOR.evaluate_stop(doc, now=NOW)["decision"], "repair_state")

    def test_malformed_exhaustion_reference_does_not_crash_the_stop_check(self):
        doc = exhausted_result()
        doc["stop_audit"]["route_frontier"][0]["route_id"] = []
        doc["routes"][0]["route_id"] = []
        self.assertEqual(VALIDATOR.evaluate_stop(doc, now=NOW)["decision"], "continue")


if __name__ == "__main__":
    unittest.main()
