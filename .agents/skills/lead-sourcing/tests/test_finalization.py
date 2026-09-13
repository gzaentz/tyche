"""Offline checks of reviewed state -> strict delivery -> saved workbook."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_output_contract import cost_result, shortfall_result
from test_stop_policy import STARTED_AT, action
from linkedin_fixtures import write_linkedin_receipts
import budget_guard
import run_attempt
from email_fixtures import write_email_receipts


def completed_document():
    document = cost_result([], accepted_contacts=2)
    document.pop("stop_reason")
    document["stop_audit"] = {"route_frontier": [
        {"route_id": r["route_id"], "state": "exhausted", "reason": "Source reviewed.",
         "exhaustion_basis": "no_new_unique_candidates"} for r in document["routes"]]}
    document["stop_check"] = {"started_at": STARTED_AT, "next_actions": []}
    return document


class FinalizationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "results.json"

    def save(self, document):
        self.path.write_text(json.dumps(document))
        write_linkedin_receipts(self.path, document)
        self.path.write_text(json.dumps(document))
        return self.path.read_bytes()

    def test_finalization_derives_metadata_without_changing_evidence_or_ledger(self):
        document = completed_document()
        self.save(document)
        budget_guard.initialize(self.path, max_usd=1, scrapingdog_usd_per_credit=0.1)
        ledger_before = budget_guard.ledger_path(self.path).read_bytes()
        receipts_before = {p.name: p.read_bytes() for p in self.path.parent.joinpath("receipts").iterdir()}
        checked = run_attempt.finalize_run(self.path)
        after = json.loads(self.path.read_text())
        self.assertTrue(checked["delivery_allowed"])
        self.assertEqual(after["stop_reason"], "target_met")
        self.assertTrue(after["stop_audit"]["frontier_complete"])
        for field in ("accepted", "unresolved", "rejected", "routes", "request", "stop_check"):
            self.assertEqual(after[field], document[field])
        self.assertEqual(checked["results_sha256"], hashlib.sha256(self.path.read_bytes()).hexdigest())
        first = self.path.read_bytes()
        run_attempt.finalize_run(self.path)
        self.assertEqual(self.path.read_bytes(), first)
        self.assertEqual(budget_guard.ledger_path(self.path).read_bytes(), ledger_before)
        self.assertEqual({p.name: p.read_bytes() for p in self.path.parent.joinpath("receipts").iterdir()}, receipts_before)

    def test_refusals_are_atomic_and_do_not_hide_work(self):
        for failure in ("shortfall", "required_email", "open_review", "unrecorded_call", "unknown_required"):
            with self.subTest(failure=failure):
                document = completed_document()
                if failure == "shortfall":
                    document["request"]["target_count"] = 3
                    document["stop_check"]["next_actions"] = [action("more-research")]
                elif failure == "required_email":
                    document["request"]["contact_fields"] = ["email"]
                elif failure == "open_review":
                    document["stop_audit"]["route_frontier"][0]["state"] = "continuable"
                elif failure == "unknown_required":
                    document["accepted"][0]["qualification_checks"] = [{
                        "criterion": "required signal", "importance": "required", "status": "unknown",
                        "claim": "No dated source", "evidence": []}]
                before = self.save(document)
                if failure == "unrecorded_call":
                    budget_guard.initialize(self.path, max_usd=1, scrapingdog_usd_per_credit=0.1)
                    budget_guard.reserve({"run_file": str(self.path), "route_id": "pending", "max_cost_credits": 0.1}, "deepline")
                with self.assertRaises(ValueError):
                    run_attempt.finalize_run(self.path)
                self.assertEqual(self.path.read_bytes(), before)

    def test_explicit_time_limit_keeps_unfinished_research_visible(self):
        document = shortfall_result(frontier_state="continuable", stop_reason="time_limit_reached")
        document["budget"] = {"limits": {"deepline_credits": 5, "scrapingdog_credits": 0}}
        document["routes"][0].update(provider="public_web", paid_calls=0)
        document["request"]["max_duration_seconds"] = 1
        document["stop_check"] = {"started_at": STARTED_AT, "next_actions": [action("more-research")]}
        self.save(document)
        checked = run_attempt.finalize_run(self.path)
        self.assertTrue(checked["delivery_allowed"])
        after = json.loads(self.path.read_text())
        self.assertEqual(after["stop_check"], document["stop_check"])
        self.assertEqual(after["stop_audit"]["route_frontier"], document["stop_audit"]["route_frontier"])

    def test_pending_job_cannot_be_hidden_by_completion_labels(self):
        document = completed_document()
        route = dict(document["routes"][0], route_id="pending-job", phase="email_validation", provider_status="partial")
        document["routes"].append(route)
        document["stop_audit"]["route_frontier"].append({
            "route_id": "pending-job", "state": "exhausted", "reason": "Incorrectly marked complete",
            "exhaustion_basis": "no_new_unique_candidates"})
        before = self.save(document)
        (self.path.parent / "receipts/pending-job.json").write_text(json.dumps({
            "pending_verification": {"id": "job-123", "email": "ada@example.org"}}))
        with self.assertRaisesRegex(ValueError, "Pending verification"):
            run_attempt.finalize_run(self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_pending_job_requires_exact_receipted_status_continuation(self):
        from test_output_contract import accepted_email_result
        document = accepted_email_result()
        document["stop_audit"] = {"route_frontier": [
            {"route_id": "pending", "continuation_route_ids": ["email-validation-1"]},
            {"route_id": "email-validation-1", "state": "exhausted"}]}
        route = document["routes"][0]
        route["status_read"] = True
        self.save(document)
        write_email_receipts(self.path, document)
        rp = self.path.parent / "receipts/email-validation-1.json"
        saved = json.loads(rp.read_text())
        saved["attempt"]["request"]["payload"]["id"] = "job-123"
        rp.write_text(json.dumps(saved))
        pending = {"id": "job-123", "email": "ada@example.org"}
        self.assertTrue(run_attempt._verification_finished(self.path, document, "pending", pending))
        self.assertFalse(run_attempt._verification_finished(self.path, document, "pending", dict(pending, id="other-job")))
        self.assertFalse(run_attempt._verification_finished(self.path, document, "pending", dict(pending, email="someone@example.org")))
        saved["provider_response"]["body"] = {"status": "error", "error": "Failed status read"}
        rp.write_text(json.dumps(saved))
        self.assertFalse(run_attempt._verification_finished(self.path, document, "pending", pending))

    def test_export_command_finalizes_and_verifies_the_client_workbook(self):
        from test_client_output import client_document
        from test_export_xlsx import EXPORTER_PATH, read_first_sheet_rows
        node = os.environ.get("TYCHE_WORKSPACE_NODE")
        modules = os.environ.get("TYCHE_WORKSPACE_NODE_MODULES")
        if not node or not modules:
            self.skipTest("Codex workbook runtime is not configured")
        document = client_document()
        document.pop("stop_reason")
        document.update(rejected=[], unresolved=[], budget={"limits": {"deepline_credits": 5}})
        document["routes"][0]["accepted_leads_before_call"] = 0
        document["stop_audit"] = {"route_frontier": [
            {"route_id": r["route_id"], "state": "exhausted", "reason": "Reviewed source",
             "exhaustion_basis": "no_new_unique_candidates"} for r in document["routes"]]}
        document["stop_check"] = {"started_at": STARTED_AT, "next_actions": []}
        row = document["accepted"][0]
        row["qualification_checks"] = [{"criterion": "Hiring", "signal": "HIRING", "importance": "preferred",
            "status": "pass", "claim": "Hiring a warehouse integrations lead", "evidence": [{
                "url": "https://example.com/jobs/integrations", "date": "2026-08-20", "date_basis": "published",
                "source": {"provider": "public_web", "operation": "search", "tool": "web", "route_id": "hiring-source"},
                "text": "Opened a warehouse integrations lead role on August 20, 2026 to connect inventory systems."}]}]
        row["intent_details"] = (
            "Example Products connected its acquired warehouse to a shared WMS on August 12, 2026. "
            "The integration supports inventory visibility and fulfillment across the combined operation. "
            "It opened a warehouse integrations lead role on August 20, 2026. "
            "That role focuses on connecting inventory systems, indicating continuing integration work. "
            "Together these changes point to an active effort to coordinate fulfillment for its packaged goods business.")
        self.save(document)
        result = subprocess.run([node, str(EXPORTER_PATH), str(self.path)],
                                text=True, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout.splitlines()[-1])["exported"])
        after = json.loads(self.path.read_text())
        self.assertEqual(after["stop_reason"], "target_met")
        checked = json.loads((self.path.parent / "validation.json").read_text())
        self.assertTrue(checked["delivery_allowed"])
        rows = read_first_sheet_rows(self.path.parent / "leads.xlsx")
        self.assertNotIn("Intent Signal", rows[0])
        for field, expected in (("Intent Details", row["intent_details"]), ("Description", row["company"]["description"])):
            self.assertEqual(rows[1][rows[0].index(field)], expected)
        signals = rows[1][rows[0].index("Signals")]
        self.assertIn("2026-08-12", signals)
        self.assertIn("2026-08-20", signals)


if __name__ == "__main__":
    unittest.main()
