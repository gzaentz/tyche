import copy
import json
import subprocess
import sys
import unittest

import test_attempt_execution as fixtures
from test_output_contract import VALIDATOR

runner = fixtures.runner


def company(domain, count=3, size_status="pass"):
    return {"stage": "account", "candidate": {"company": "Example Builder", "domain": domain,
            "employee_count": count}, "reason_code": "missing_account_evidence",
            "reason_text": "Size fits the requested range; the dated steel-project signal remains unknown.",
            "qualification_checks": [
                {"criterion": "company_size", "importance": "required", "status": size_status,
                 "claim": "The company reports three employees.",
                 "evidence": [{"url": "https://" + domain + "/about", "text": "3 employees"}]},
                {"criterion": "recent_intent", "importance": "required", "status": "unknown",
                 "claim": "A dated steel project has not been established.", "evidence": []}]}


class SaveReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AttemptExecutionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.path = self.fixture.path
        self.doc = self.fixture.doc
        self.doc["request"]["icp"] = {"company_size": {"min_employees": 1, "max_employees": 200}}
        self.doc["unresolved"] = [company("builder.example"), company("untouched.example")]
        self.path.write_text(json.dumps(self.doc))

    def attempt(self, rid="review-source"):
        spec = self.fixture.spec(rid, query=rid, paid=True)
        spec["action"].update(scope="builder.example", phase="account_verification")
        runner.run_attempt(self.path, spec, execute=self.fixture.paid_response)

    def test_one_cli_review_parks_company_closes_source_and_keeps_discovery(self):
        self.attempt()
        before = json.loads(self.path.read_text())
        before["stop_check"]["next_actions"] = [
            dict(fixtures.action("speculative", scope="builder.example"), approach="old-source"),
            dict(fixtures.action("fresh"), approach="new-discovery-source")]
        self.path.write_text(json.dumps(before))
        ledger = self.path.with_suffix(".json.budget.json").read_bytes()
        receipt = (self.path.parent / "receipts/review-source.json").read_bytes()
        review = {"companies": [{"state": "unresolved", "row": company("builder.example")}],
                  "routes": [{"route_id": "review-source", "reason": "Saved source checked; no dated project evidence."}]}
        review_path = self.path.parent / "review.json"
        review_path.write_text(json.dumps(review))
        command = [sys.executable, runner.__file__, str(self.path), "--review-file", str(review_path)]
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        output = json.loads(result.stdout)
        self.assertEqual(output["request"], before["request"])
        self.assertEqual([a["id"] for a in output["next_actions"]], ["fresh"])
        self.assertIn("builder.example", output["stop_decision"]["parked_scopes"])
        self.assertIn("fresh", output["stop_decision"]["eligible_actions"])
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved["routes"], before["routes"])
        self.assertEqual(saved["summary"]["unresolved_rows"], 2)
        self.assertIn(company("untouched.example"), saved["unresolved"])
        subprocess.run(command, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(self.path.read_text()), saved)
        self.assertEqual(self.path.with_suffix(".json.budget.json").read_bytes(), ledger)
        self.assertEqual((self.path.parent / "receipts/review-source.json").read_bytes(), receipt)
        status = subprocess.run([sys.executable, runner.__file__, str(self.path), "--status"],
                                capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(status.stdout)["request"], before["request"])
        self.assertNotIn("routes", json.loads(status.stdout))
        self.assertEqual(json.loads(self.path.read_text()), saved)

    def test_invalid_review_is_atomic_and_cannot_change_request_or_charges(self):
        self.attempt()
        before = self.path.read_bytes()
        for review in (
                {"request": {"icp": {"company_size": {"min_employees": 10}}}},
                {"companies": [{"state": "rejected", "row": dict(company("builder.example", size_status="fail"), reason_code="not_icp_fit")}]},
                {"companies": [{"state": "unresolved", "row": company("builder.example")}],
                 "routes": [{"route_id": "missing", "reason": "not a real saved attempt"}]}):
            with self.assertRaises((ValueError, StopIteration)):
                runner.save_review(self.path, review)
            self.assertEqual(self.path.read_bytes(), before)

    def test_cannot_close_failed_or_pending_attempts_as_exhausted(self):
        self.attempt()
        doc = json.loads(self.path.read_text())
        doc["routes"][-1]["provider_status"] = "timeout"
        self.path.write_text(json.dumps(doc))
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "determinate"):
            runner.save_review(self.path, {"routes": [{"route_id": "review-source", "reason": "Reviewed"}]})
        self.assertEqual(self.path.read_bytes(), before)

    def test_route_only_review_retires_speculation_but_preserves_open_work(self):
        self.attempt()
        doc = json.loads(self.path.read_text())
        doc["stop_check"]["next_actions"] = [dict(fixtures.action("speculation", scope="builder.example"), approach="old-source"),
            dict(fixtures.action("pending-drawings", scope="builder.example"), approach="drawings")]
        # An independent, unfinished source must remain visible after parking.
        doc["stop_audit"]["route_frontier"].append({"route_id": "pending-drawings", "scope": "builder.example",
            "state": "untried", "phase": "account_verification", "provider": "public_web", "operation": "search",
            "request_summary": "New project drawings", "reason": "Planned source check", "approach": "drawings"})
        self.path.write_text(json.dumps(doc))
        result = runner.save_review(self.path, {"routes": [{"route_id": "review-source", "reason": "This source has no further evidence."}]})
        self.assertEqual([a["id"] for a in result["next_actions"]], ["pending-drawings"])
        self.assertIn("pending-drawings", result["stop_decision"]["eligible_actions"])
        self.assertEqual(result["stop_decision"]["decision"], "continue")
        before = self.path.read_bytes()
        with self.assertRaises(StopIteration):
            runner.save_review(self.path, {"routes": [{"route_id": "pending-drawings", "reason": "Cannot close without a receipt"}]})
        self.assertEqual(self.path.read_bytes(), before)

    def test_company_transitions_keep_email_failures_and_other_company_data(self):
        self.attempt()
        doc = json.loads(self.path.read_text())
        email_failure = {"stage": "email_validation", "reason_code": "email_invalid",
                         "candidate": {"domain": "builder.example", "email": "bad@builder.example"}}
        doc["rejected"] = [email_failure]
        self.path.write_text(json.dumps(doc))
        row = company("builder.example")
        row["qualification_checks"][1].update(status="fail", evidence=[{"url": "https://builder.example/project", "text": "Not a buyer"}])
        row["reason_code"] = "not_icp_fit"
        runner.save_review(self.path, {"companies": [{"state": "rejected", "row": row}]})
        saved = json.loads(self.path.read_text())
        self.assertIn(email_failure, saved["rejected"])
        self.assertIn(company("untouched.example"), saved["unresolved"])
        self.assertEqual(saved["summary"]["unresolved_rows"], 1)
        self.assertEqual(saved["summary"]["rejected_rows"], 2)

    def test_size_is_checked_against_saved_icp_for_passes_and_rejections(self):
        for count, size_status, error in ((3, "pass", False), (3, "fail", True),
                                         (201, "pass", True), (201, "fail", False),
                                         (1, "pass", False), (200, "pass", False)):
            doc = copy.deepcopy(self.doc)
            doc["unresolved"] = [company("builder.example", count, size_status)]
            self.assertEqual(bool(VALIDATOR.qualification_errors(doc)), error, (count, size_status))
        doc = copy.deepcopy(self.doc)
        doc["unresolved"] = [company("builder.example", 201)]
        doc["unresolved"][0]["stage"] = "contact"
        self.assertTrue(VALIDATOR.qualification_errors(doc))
        doc["unresolved"][0]["candidate"].pop("employee_count")
        doc["unresolved"][0]["stage"] = "account"
        self.assertEqual(VALIDATOR.qualification_errors(doc), [])

    def test_incomplete_accepted_lead_is_refused_without_retiring_contact_work(self):
        doc = json.loads(self.path.read_text())
        doc["schema_version"] = "1.2"
        doc["request"]["contact_fields"] = ["email"]
        doc["stop_check"]["next_actions"] = [dict(fixtures.action("find-email", scope="builder.example"), approach="buyer-email")]
        self.path.write_text(json.dumps(doc));before = self.path.read_bytes()
        row = {"company": {"canonical_name": "Builder", "domain": "builder.example"}}
        with self.assertRaisesRegex(ValueError, "primary_contact|intent_details"):
            runner.save_review(self.path, {"companies": [{"state": "accepted", "row": row}]})
        self.assertEqual(self.path.read_bytes(), before)
        row.update(intent_details="Current steel project", primary_contact={"full_name": "Example Buyer"})
        row["company"]["classification_note"] = "Unclassified"
        with self.assertRaisesRegex(ValueError, "requested email"):
            runner.save_review(self.path, {"companies": [{"state": "accepted", "row": row}]})
        self.assertEqual(self.path.read_bytes(), before)
        row["primary_contact"].update(email="buyer@builder.example")
        with self.assertRaisesRegex(ValueError, "email_validation"):
            runner.save_review(self.path, {"companies": [{"state": "accepted", "row": row}]})
        self.assertEqual(self.path.read_bytes(), before)

    def test_partial_email_job_requires_status_recovery_instead_of_exhaustion(self):
        self.attempt()
        doc = json.loads(self.path.read_text());doc["routes"][-1]["provider_status"] = "partial"
        self.path.write_text(json.dumps(doc));before = self.path.read_bytes()
        rp = self.path.parent / "receipts/review-source.json";receipt = json.loads(rp.read_text())
        receipt.update(status="partial", pending_verification={"job_id": "already-dispatched"});rp.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(ValueError, "status continuation"):
            runner.save_review(self.path, {"routes": [{"route_id": "review-source", "reason": "No rows yet"}]})
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
