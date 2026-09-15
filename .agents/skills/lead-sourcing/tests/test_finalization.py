"""Offline checks of reviewed state -> strict delivery -> saved workbook."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_output_contract import VALIDATOR_PATH, cost_result, shortfall_result
from test_stop_policy import STARTED_AT, action
from linkedin_fixtures import write_linkedin_receipts
import budget_guard
import run_attempt


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
        route = dict(document["routes"][0], route_id="pending-job", phase="email_validation", provider_status="partial",
                     tool="bounceban_verify_single", request_fingerprint="pending-request")
        document["routes"].append(route)
        document["stop_audit"]["route_frontier"].append({
            "route_id": "pending-job", "state": "exhausted", "reason": "Incorrectly marked complete",
            "exhaustion_basis": "no_new_unique_candidates"})
        run_attempt.refresh(document)
        document["stop_reason"] = "target_met"
        document["stop_audit"]["frontier_complete"] = True
        before = self.save(document)
        pending = {"status": "verifying", "id": "job-123"}
        receipt = {"provider": "deepline", "operation": "execute", "tool": route["tool"],
            "receipt_status": "complete", "status": "partial", "request_fingerprint": route["request_fingerprint"],
            "run_fingerprint": budget_guard.run_fingerprint(self.path), "pending_verification": pending,
            "attempt": {"request": {"operation": "execute", "tool": route["tool"], "payload": {"email": "ada@example.org"}}},
            "provider_response": {"exit_code": 0, "body": pending, "stderr": ""}}
        (self.path.parent / "receipts/pending-job.json").write_text(json.dumps(receipt))
        checked = subprocess.run([sys.executable, str(VALIDATOR_PATH), str(self.path)],
                                 capture_output=True, text=True, timeout=10)
        self.assertEqual(checked.returncode, 2)
        self.assertFalse(json.loads(checked.stdout)["delivery_allowed"])
        with self.assertRaisesRegex(ValueError, "Pending verification") as raised:
            run_attempt.finalize_run(self.path)
        self.assertEqual(json.loads(checked.stdout)["errors"], [str(raised.exception)])
        self.assertEqual(self.path.read_bytes(), before)

        # The same gates allow completion once the saved job has a real verdict.
        getter = dict(route, route_id="finished-job", tool="bounceban_get_verification",
                      provider_status="ok", status_read=True, request_fingerprint="getter-request")
        document["routes"].append(getter)
        document["stop_audit"]["route_frontier"][-1].update(
            continuation_route_ids=["finished-job"], exhaustion_basis="continuation_exhausted")
        document["stop_audit"]["route_frontier"].append({"route_id": "finished-job", "state": "exhausted",
            "reason": "Completed verdict reviewed", "exhaustion_basis": "no_new_unique_candidates"})
        completed = dict(receipt, tool=getter["tool"], status="ok", request_fingerprint=getter["request_fingerprint"],
            attempt={"request": {"operation": "execute", "tool": getter["tool"], "payload": {"id": "job-123"}}},
            provider_response={"exit_code": 0, "body": {"status": "success", "result": "deliverable", "email": "ada@example.org"}, "stderr": ""})
        completed.pop("pending_verification")
        (self.path.parent / "receipts/finished-job.json").write_text(json.dumps(completed))
        run_attempt.refresh(document)
        self.save(document)
        checked = subprocess.run([sys.executable, str(VALIDATOR_PATH), str(self.path)],
                                 capture_output=True, text=True, timeout=10)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertTrue(json.loads(checked.stdout)["delivery_allowed"])
        self.assertTrue(run_attempt.finalize_run(self.path)["delivery_allowed"])

    def test_unused_research_does_not_block_validation_or_finalization(self):
        document = completed_document()
        document["stop_audit"]["route_frontier"].append({
            "route_id": "unused-next-source", "state": "untried", "reason": "Optional next search"})
        document["stop_check"]["next_actions"] = [action("unused-next-source")]
        run_attempt.refresh(document)
        document["stop_reason"] = "target_met"
        document["stop_audit"]["frontier_complete"] = True
        self.save(document)
        before = self.path.read_bytes()
        checked = subprocess.run([sys.executable, str(VALIDATOR_PATH), str(self.path)],
                                 capture_output=True, text=True, timeout=10)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertTrue(json.loads(checked.stdout)["delivery_allowed"])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(run_attempt.finalize_run(self.path)["delivery_allowed"])
        after = json.loads(self.path.read_text())
        self.assertEqual(after["stop_audit"]["route_frontier"], document["stop_audit"]["route_frontier"])
        self.assertEqual(after["stop_check"], document["stop_check"])

    def test_attempted_review_blocks_both_validation_and_finalization(self):
        document = completed_document()
        document["stop_audit"]["route_frontier"][0]["state"] = "continuable"
        run_attempt.refresh(document)
        document["stop_reason"] = "target_met"
        document["stop_audit"]["frontier_complete"] = True
        before = self.save(document)
        checked = subprocess.run([sys.executable, str(VALIDATOR_PATH), str(self.path)],
                                 capture_output=True, text=True, timeout=10)
        self.assertEqual(checked.returncode, 2)
        errors = json.loads(checked.stdout)["errors"]
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("Review attempted routes", errors[0])
        with self.assertRaises(ValueError) as raised:
            run_attempt.finalize_run(self.path)
        self.assertEqual(str(raised.exception), errors[0])
        self.assertEqual(self.path.read_bytes(), before)

    def test_native_finish_checks_routes_before_issuing_review_packet(self):
        from research_tools import ResearchTools
        document = completed_document()
        document["stop_audit"]["route_frontier"][0]["state"] = "continuable"
        run_attempt.refresh(document)
        before = self.save(document)
        budget_guard.initialize(self.path, max_usd=1, scrapingdog_usd_per_credit=0.1)
        tools = ResearchTools(self.path, execute=lambda *args: self.fail("No provider call expected"))
        result = tools.finish()
        self.assertEqual(result["status"], "needs_repair")
        self.assertIn("Review attempted routes", " ".join(result["errors"]))
        self.assertNotIn("review_ref", result)
        self.assertTrue(result["pending_sources"])
        self.assertEqual(self.path.read_bytes(), before)

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
        document["stop_audit"]["route_frontier"].append({
            "route_id": "unused-next-source", "state": "untried", "reason": "Optional next search"})
        document["stop_check"]["next_actions"] = [action("unused-next-source")]
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
        self.assertEqual(after["stop_audit"]["route_frontier"], document["stop_audit"]["route_frontier"])
        self.assertEqual(after["stop_check"], document["stop_check"])
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
