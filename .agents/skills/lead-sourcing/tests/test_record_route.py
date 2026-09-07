import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("record_route", SCRIPTS / "record_route.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RouteRecordingTests(unittest.TestCase):
    def setUp(self):
        self.document = {"routes": [], "accepted": [{"preserved": True}], "stop_audit": {"frontier_complete": False, "route_frontier": []}}
        self.frontier = {"route_id": "one", "phase": "account_discovery", "provider": "public_web", "operation": "search", "request_summary": "query one", "state": "untried", "reason": "planned"}

    def test_plan_finish_preserves_other_state_and_is_idempotent(self):
        planned = MODULE.record(self.document, self.frontier)
        finished = dict(self.frontier, state="continuable", reason="reviewed")
        receipt = {**{key: self.frontier[key] for key in MODULE.IDENTITY}, "provider_status": "ok"}
        result = MODULE.record(planned, finished, receipt)
        self.assertEqual(MODULE.record(result, finished, receipt), result)
        self.assertEqual(result["accepted"], self.document["accepted"])
        self.assertFalse(result["stop_audit"]["frontier_complete"])
        self.assertEqual(self.document["routes"], [])
        with self.assertRaises(ValueError):
            MODULE.record(result, finished, dict(receipt, provider_status="no_results"))

    def test_rejects_unplanned_execution_and_reused_query(self):
        with self.assertRaises(ValueError):
            MODULE.record(self.document, self.frontier, {key: self.frontier[key] for key in MODULE.IDENTITY})
        planned = MODULE.record(self.document, self.frontier)
        with self.assertRaises(ValueError):
            MODULE.record(planned, dict(self.frontier, request_summary="another query"))

    def test_error_cannot_be_exhausted(self):
        planned = MODULE.record(self.document, self.frontier)
        receipt = {**{key: self.frontier[key] for key in MODULE.IDENTITY}, "provider_status": "timeout"}
        with self.assertRaises(ValueError):
            MODULE.record(planned, dict(self.frontier, state="exhausted", exhaustion_basis="no_results"), receipt)

    def test_estimate_can_settle_once_without_changing_attempt(self):
        planned = MODULE.record(self.document, self.frontier)
        receipt = {**{key: self.frontier[key] for key in MODULE.IDENTITY}, "provider_status": "ok", "cost_basis": "estimated", "cost_credits": None, "cost_upper_bound_credits": 0.1}
        finished = dict(self.frontier, state="continuable")
        pending = MODULE.record(planned, finished, receipt)
        actual = dict(receipt, cost_basis="actual", cost_credits=0.07, cost_upper_bound_credits=0.07)
        settled = MODULE.record(pending, finished, actual)
        self.assertEqual(settled["routes"][0]["cost_credits"], 0.07)
        for changed in (dict(actual, cost_credits=0.08), dict(actual, provider_status="no_results")):
            with self.assertRaises(ValueError):
                MODULE.record(settled, finished, changed)
        with self.assertRaises(ValueError):
            MODULE.record(pending, finished, dict(actual, cost_credits=0.2, cost_upper_bound_credits=0.2))

    def test_atomic_failure_and_lock_preserve_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results.json"
            original = json.dumps(self.document)
            path.write_text(original)
            with self.assertRaises(ValueError):
                MODULE.persist(path, {"frontier": {}})
            self.assertEqual(path.read_text(), original)
            lock = path.with_name("results.json.lock")
            lock.write_text("another writer")
            with self.assertRaises(FileExistsError):
                MODULE.persist(path, {"frontier": self.frontier})
            self.assertEqual(lock.read_text(), "another writer")
            lock.unlink()
            MODULE.persist(path, {"frontier": self.frontier})
            self.assertEqual(len(json.loads(path.read_text())["stop_audit"]["route_frontier"]), 1)


if __name__ == "__main__":
    unittest.main()
