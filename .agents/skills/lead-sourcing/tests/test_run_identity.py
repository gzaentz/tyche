"""Run provenance without provider calls or changes to historical artifacts."""

import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

import test_attempt_execution as fixtures

budget_guard = fixtures.budget_guard
runner = fixtures.runner


class RunIdentityTests(unittest.TestCase):
    def setUp(self):
        self.source = self.new_run()
        self.target = self.new_run()

    def new_run(self):
        fixture = fixtures.AttemptExecutionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def execute(self, fixture, **kwargs):
        return runner.run_attempt(fixture.path, fixture.spec(paid=True),
                                  execute=fixture.paid_response, **kwargs)

    def cli(self, fixture, *flags, validator=False):
        script = Path(runner.__file__).with_name("validate_run.py") if validator else Path(runner.__file__)
        return subprocess.run([sys.executable, str(script), str(fixture.path), *flags],
                              capture_output=True, text=True, timeout=10)

    def test_copy_and_path_rewrite_cannot_resume_review_or_pass_validation(self):
        self.execute(self.source)
        # Reproduce the incident: copy the completed state and rewrite all paths.
        for old, new in ((self.source.path, self.target.path),
                         (budget_guard.ledger_path(self.source.path), budget_guard.ledger_path(self.target.path))):
            new.write_text(old.read_text().replace(str(self.source.path), str(self.target.path)))
        files = (self.source.path, self.target.path, budget_guard.ledger_path(self.source.path),
                 budget_guard.ledger_path(self.target.path))
        before = {p: p.read_bytes() for p in files}
        execute = Mock()
        for paid in (False, True):
            with self.assertRaisesRegex(ValueError, "run identity"):
                runner.run_attempt(self.target.path, self.target.spec("next", paid=paid), execute=execute)
        with self.assertRaisesRegex(ValueError, "run identity"):
            runner.save_review(self.target.path, {})
        execute.assert_not_called()
        for flags in ([], ["--check-stop"]):
            result = self.cli(self.target, *flags, validator=True)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            output = json.loads(result.stdout)
            self.assertFalse(output["delivery_allowed"])
            self.assertIn("run identity", " ".join(output["errors"]))
        self.assertEqual(self.cli(self.target, "--status").returncode, 2)
        self.assertEqual({p: p.read_bytes() for p in files}, before)

    def test_receipt_from_identical_request_in_another_run_cannot_be_completed(self):
        original = self.execute(self.source)["result"]
        with patch.object(runner, "finish_attempt", side_effect=OSError("interrupted after capture")):
            with self.assertRaises(OSError):
                self.execute(self.target)
        path = self.target.path.parent / "receipts/one.json"
        genuine = path.read_bytes()
        own = json.loads(genuine)
        self.assertEqual(original["request_fingerprint"], own["request_fingerprint"])
        self.assertNotEqual(original["run_fingerprint"], own["run_fingerprint"])
        ledger = budget_guard.ledger_path(self.target.path)
        before = (self.target.path.read_bytes(), ledger.read_bytes())
        path.write_text(json.dumps(original))
        result = self.cli(self.target, "--complete", "one")
        self.assertEqual(result.returncode, 2)
        self.assertIn("another run", result.stderr)
        self.assertEqual((self.target.path.read_bytes(), ledger.read_bytes()), before)
        # Recover the genuine captured response in place, with no second charge.
        path.write_bytes(genuine)
        runner.finish_attempt(self.target.path, "one", own)
        runner.finish_attempt(self.target.path, "one", own)
        self.assertEqual(ledger.read_bytes(), before[1])
        self.assertEqual(budget_guard.audit_ledger(self.target.path, json.loads(self.target.path.read_text())), [])
        with self.assertRaisesRegex(ValueError, "another run"):
            runner.finish_attempt(self.target.path, "one", original)

    def test_missing_receipt_identity_is_not_silently_backfilled(self):
        body = self.execute(self.source)["result"]
        body.pop("run_fingerprint")
        before = self.source.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "lacks run identity"):
            runner.finish_attempt(self.source.path, "one", body)
        self.assertEqual(self.source.path.read_bytes(), before)

    def test_unbound_legacy_ledger_is_read_only_and_never_reset(self):
        self.execute(self.source)
        path = budget_guard.ledger_path(self.source.path)
        state = budget_guard.read_object(path)
        state.pop("run_fingerprint")
        path.write_text(json.dumps(state))
        before = path.read_bytes()
        document = json.loads(self.source.path.read_text())
        self.assertEqual(budget_guard.audit_ledger(self.source.path, document, allow_unbound=True), [])
        self.assertTrue(budget_guard.audit_ledger(self.source.path, document))
        with self.assertRaisesRegex(ValueError, "run identity"):
            budget_guard.reserve({"run_file": str(self.source.path), "route_id": "next", "max_cost_credits": 0.1}, "deepline")
        with self.assertRaises(ValueError):
            budget_guard.initialize(self.source.path, scrapingdog_usd_per_credit=0.1)
        historical = json.loads(self.cli(self.source, "--legacy-stop-policy", validator=True).stdout)
        self.assertFalse(historical["delivery_allowed"])
        self.assertNotIn("run identity", " ".join(historical["errors"]))
        planning = self.cli(self.source, "--legacy-stop-policy", "--check-stop", validator=True)
        self.assertEqual(planning.returncode, 2)
        self.assertIn("run identity", planning.stdout)
        self.assertEqual(path.read_bytes(), before)

    def test_wrong_binding_is_refused_even_for_historical_audit(self):
        state = budget_guard.load_ledger(self.source.path)
        state["run_file"] = str(self.target.path.resolve())
        errors = budget_guard.audit_ledger(self.target.path, self.target.doc, state=state, allow_unbound=True)
        self.assertIn("run identity", " ".join(errors))

    def test_foreign_ledger_replacement_during_call_cannot_be_settled(self):
        spend = {"route_id": "one", "max_cost_credits": 0.2}
        for fixture in (self.source, self.target):
            budget_guard.reserve(dict(spend, run_file=str(fixture.path)), "deepline")
        target_path = budget_guard.ledger_path(self.target.path)
        state = budget_guard.load_ledger(self.source.path)
        state["run_file"] = str(self.target.path.resolve())
        target_path.write_text(json.dumps(state))
        before = target_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "run identity"):
            budget_guard.settle(target_path, "one", {"credits_charged": 0.1})
        self.assertEqual(target_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
