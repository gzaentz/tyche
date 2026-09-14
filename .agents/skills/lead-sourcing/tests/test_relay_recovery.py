"""Real child-process failures, with fixture providers and no network calls."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from test_research_tools import FixtureProvider, check
from test_research_interface import setup_request
from research_tools import ResearchTools
from tyche_tools import SandboxedTools
import budget_guard


class RelayRecoveryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "results.json"
        self.tools = ResearchTools(self.path, execute=FixtureProvider())
        self.tools.start(setup_request()["request"])
        self.relay = SandboxedTools(self.path)
        self.addCleanup(self.relay.close)
        self.metadata = {"codex/sandbox-state-meta": {
            "permissionProfile": {"mode": "workspace-write"},
            "sandboxCwd": Path(__file__).resolve().parents[4].as_uri()}}
        self.launches = []
        self.popen = subprocess.Popen

    def launch(self, code):
        def factory(command, **options):
            self.launches.append(command)
            if len(self.launches) == 1:
                return self.popen([sys.executable, "-u", "-c", code], **options)
            # Replace only the sandbox executable in this offline fixture. The
            # production command and identical metadata are checked below.
            return self.popen(command[4:], **options)
        return factory

    def test_saved_state_read_reconnects_once_with_identical_policy_and_run(self):
        crash = "import sys,os; sys.stdin.readline(); os._exit(7)"
        before = self.path.read_bytes(), budget_guard.ledger_path(self.path).read_bytes()
        with patch("tyche_tools.subprocess.Popen", side_effect=self.launch(crash)):
            result = self.relay.call("tyche_inspect", {}, self.metadata)
        self.assertEqual(result["summary"]["accepted_companies"], 0)
        self.assertEqual(self.relay.restarts, 1)
        self.assertEqual(len(self.launches), 2)
        self.assertEqual(self.launches[0], self.launches[1])
        self.assertEqual(self.launches[0][:3], ["codex", "sandbox", "--sandbox-state-json"])
        self.assertEqual(json.loads(self.launches[0][3]), self.metadata["codex/sandbox-state-meta"])
        self.assertEqual(before, (self.path.read_bytes(), budget_guard.ledger_path(self.path).read_bytes()))

    def test_lost_paid_response_preserves_reservation_and_never_replays(self):
        tests = str(Path(__file__).resolve().parent)
        script = f'''import os,sys
sys.path.insert(0, {tests!r})
from test_research_tools import FixtureProvider
from research_tools import ResearchTools
from tyche_tools import serve
provider=FixtureProvider()
def execute(request,capture):
    def lose_reply(raw):
        capture(raw)
        os._exit(9)
    return provider(request,lose_reply)
serve(ResearchTools({str(self.path)!r},execute=execute))
'''
        with patch("tyche_tools.subprocess.Popen", side_effect=self.launch(script)):
            result = self.relay.call("tyche_lookup", {"checks": [check()]}, self.metadata)
            self.assertEqual(result["status"], "recovery_required")
            self.assertEqual(result["child_exit_code"], 9)
            ledger = budget_guard.load_ledger(self.path)
            self.assertEqual(len(ledger["calls"]), 1)
            self.assertIsNone(next(iter(ledger["calls"].values()))["actual_credits"])
            before = budget_guard.ledger_path(self.path).read_bytes()
            self.relay.call("tyche_inspect", {}, self.metadata)
            self.assertEqual(before, budget_guard.ledger_path(self.path).read_bytes())
            with self.assertRaisesRegex(ValueError, "already attempted"):
                self.relay.call("tyche_lookup", {"checks": [check()]}, self.metadata)
            self.assertEqual(before, budget_guard.ledger_path(self.path).read_bytes())

    def test_repeated_connection_failure_blocks_without_a_restart_loop(self):
        code = "import sys,os; sys.stdin.readline(); os._exit(8)"
        def factory(command, **options):
            self.launches.append(command)
            return self.popen([sys.executable, "-u", "-c", code], **options)
        with patch("tyche_tools.subprocess.Popen", side_effect=factory):
            result = self.relay.call("tyche_inspect", {}, self.metadata)
            self.assertEqual(result["status"], "operationally_blocked")
            self.assertEqual(result["child_exit_code"], 8)
            again = self.relay.call("tyche_inspect", {}, self.metadata)
            self.assertEqual(again["status"], "operationally_blocked")
        self.assertEqual(len(self.launches), 2)

    def test_concurrent_readers_share_one_replacement_without_crossing_futures(self):
        crash = "import sys,os,time; sys.stdin.readline(); time.sleep(.1); os._exit(7)"
        launch = self.launch(crash)
        def delayed_replacement(command, **options):
            if len(self.launches) == 1:
                time.sleep(.15)  # Expose the interval between reserving a restart and assigning its child.
            return launch(command, **options)
        with patch("tyche_tools.subprocess.Popen", side_effect=delayed_replacement), ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(self.relay.call, "tyche_inspect", {}, self.metadata) for _ in range(3)]
            results = [f.result(timeout=10) for f in futures]
        self.assertTrue(all(r.get("summary", {}).get("accepted_companies") == 0 for r in results), results)
        self.assertEqual(len(self.launches), 2)

    def test_malformed_child_message_finishes_pending_call_instead_of_hanging(self):
        crash = 'import sys,os; sys.stdin.readline(); print("{}",flush=True); os._exit(6)'
        with patch("tyche_tools.subprocess.Popen", side_effect=self.launch(crash)):
            result = self.relay.call("tyche_review", {}, self.metadata)
        self.assertEqual(result["status"], "recovery_required")
        self.assertIn("Expected a JSON-RPC result object", result["reason"])


if __name__ == "__main__":
    unittest.main()
