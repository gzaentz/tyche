import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from test_provider_scripts import DEEPLINE, SCRAPINGDOG, ROOT
from provider_output import ResponseFile


FAKE_CLI = """#!/usr/bin/env python3
import os, sys, time
with open(os.environ['FIXTURE_CALL_LOG'], 'a') as stream:
    stream.write('called\\n')
sys.stdout.write(os.environ['FIXTURE_RESPONSE'])
sys.stdout.flush()
time.sleep(float(os.environ.get('FIXTURE_SLEEP', '0')))
raise SystemExit(int(os.environ.get('FIXTURE_EXIT', '0')))
"""


class ResponseFaultTests(unittest.TestCase):
    def request(self, adapter):
        if adapter is DEEPLINE:
            return {"operation": "execute", "tool": "fixture", "payload": {}, "limit": 1}
        return {"operation": "google_search", "query": "wholesalers", "limit": 1}

    def call(self, adapter, path, raw):
        method = "_invoke" if adapter is DEEPLINE else "_http_get"
        wire = (0, raw, "") if adapter is DEEPLINE else (200, raw, None)
        with mock.patch.object(adapter, method, return_value=wire) as provider, \
             mock.patch.dict(os.environ, {"SCRAPINGDOG_API_KEY": "fixture-only"}), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            code = adapter.main(["--input", json.dumps(self.request(adapter)), "--output-file", str(path)])
        return code, json.loads(stdout.getvalue()), provider.call_count

    def test_nonfinite_response_is_preserved_as_diagnostic_text(self):
        for adapter in (DEEPLINE, SCRAPINGDOG):
            for token in ("NaN", "Infinity", "-Infinity", "1e9999"):
                with self.subTest(adapter=adapter.__name__, token=token), tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "receipt.json"
                    raw = '{"unexpected_value": ' + token + ', "api_key": "fixture-secret"}'
                    code, body, calls = self.call(adapter, path, raw)
                    saved = json.loads(path.read_text())
                    self.assertEqual(calls, 1)
                    self.assertEqual(saved["receipt_status"], "complete")
                    self.assertIn(token, saved["provider_response"]["body"])
                    self.assertNotIn("fixture-secret", path.read_text())
                    self.assertNotIn("receipt_error", body)

    def test_plaintext_diagnostics_hide_basic_and_escaped_credentials(self):
        samples = [
            ('Authorization: Basic Zml4dHVyZTpwdw==\n', 'Zml4dHVyZTpwdw=='),
            ('private_key="fixture-private-value"', 'fixture-private-value'),
            ('credentials="fixture-credential-value"', 'fixture-credential-value'),
            ('api_key="prefix\\"fixture-secret-suffix"', 'fixture-secret-suffix'),
            ('Cookie: session=one; csrf=fixture-cookie-value\n', 'fixture-cookie-value'),
        ]
        for adapter in (DEEPLINE, SCRAPINGDOG):
            for raw, secret in samples:
                with self.subTest(adapter=adapter.__name__, raw=raw), tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "receipt.json"
                    self.call(adapter, path, raw)
                    self.assertNotIn(secret, path.read_text())
                    self.assertNotIn(secret, adapter.redact(raw))

    def test_capture_write_failure_can_recover_without_another_provider_call(self):
        original = ResponseFile._write
        def fail_capture(writer, document):
            if document.get("receipt_status") == "response_received":
                raise OSError("injected transient disk failure")
            return original(writer, document)
        for adapter in (DEEPLINE, SCRAPINGDOG):
            with self.subTest(adapter=adapter.__name__), tempfile.TemporaryDirectory() as directory, \
                 mock.patch.object(ResponseFile, "_write", fail_capture):
                path = Path(directory) / "receipt.json"
                raw = '{"results": []}' if adapter is DEEPLINE else '{"organic_results": []}'
                code, body, calls = self.call(adapter, path, raw)
                self.assertEqual((code, calls), (0, 1))
                self.assertEqual(json.loads(path.read_text())["receipt_status"], "complete")
                self.assertNotIn("receipt_error", body)
                self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_preflight_sync_failure_prevents_provider_call(self):
        for adapter in (DEEPLINE, SCRAPINGDOG):
            with self.subTest(adapter=adapter.__name__), tempfile.TemporaryDirectory() as directory, \
                 mock.patch("provider_output.os.fsync", side_effect=OSError("injected disk failure")):
                path = Path(directory) / "receipt.json"
                code, body, calls = self.call(adapter, path, '{"results": []}')
                self.assertEqual((code, calls), (2, 0))
                self.assertEqual(body["status"], "config_error")

    def test_existing_symlink_is_never_followed(self):
        for adapter in (DEEPLINE, SCRAPINGDOG):
            for dangling in (False, True):
                with self.subTest(adapter=adapter.__name__, dangling=dangling), tempfile.TemporaryDirectory() as directory:
                    target = Path(directory) / "user-file"
                    if not dangling:
                        target.write_text("preserve me")
                    path = Path(directory) / "receipt.json"
                    path.symlink_to(target)
                    code, body, calls = self.call(adapter, path, '{"results": []}')
                    self.assertEqual((code, calls), (2, 0))
                    self.assertTrue(path.is_symlink())
                    self.assertEqual(target.exists(), not dangling)
                    if not dangling:
                        self.assertEqual(target.read_text(), "preserve me")

    def test_normalizer_exception_leaves_received_response_on_disk(self):
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(DEEPLINE, "_execute_output", side_effect=RuntimeError("injected normalizer fault")):
            path = Path(directory) / "receipt.json"
            with self.assertRaisesRegex(RuntimeError, "normalizer fault"):
                self.call(DEEPLINE, path, '{"results": [], "billing": {"credits_charged": 0.28}}')
            saved = json.loads(path.read_text())
            self.assertEqual(saved["receipt_status"], "response_received")
            self.assertEqual(saved["provider_response"]["body"]["billing"]["credits_charged"], 0.28)
            self.assertEqual(list(Path(directory).iterdir()), [path])


class CliBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.provider = self.directory / "fake-deepline"
        self.provider.write_text(FAKE_CLI)
        self.provider.chmod(0o700)
        self.calls = self.directory / "calls.log"
        self.path = self.directory / "response.json"
        self.run_file = self.directory / "results.json"
        self.run_file.write_text(json.dumps({"request": {"target_count": 1}, "accepted": [], "routes": [],
            "budget": {"paid_calls": 0, "limits": {"deepline_credits": 5,
                "scrapingdog_credits": 0, "max_paid_calls": 5}}}))
        from budget_guard import initialize
        initialize(self.run_file, verification_reserve_credits=1)

    def run_cli(self, raw, *, sleep=0, exit_code=0, timeout=5):
        env = dict(os.environ, DEEPLINE_BIN=str(self.provider), FIXTURE_CALL_LOG=str(self.calls),
                   FIXTURE_RESPONSE=raw, FIXTURE_SLEEP=str(sleep), FIXTURE_EXIT=str(exit_code))
        request = {"operation": "execute", "tool": "fixture", "entity_type": "email_validation",
                   "payload": {"email": "owner@example.test"}, "timeout_seconds": timeout,
                   "spend": {"run_file": str(self.run_file), "route_id": "fixture", "max_cost_credits": 1}}
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "deepline.py"),
                               "--input", json.dumps(request), "--output-file", str(self.path)],
                              env=env, text=True, capture_output=True, timeout=10)

    def test_real_cli_success_then_same_path_refusal_calls_provider_once(self):
        raw = '{"toolResponse":{"raw":{"email":"owner@example.test","status":"valid"}}}'
        first = self.run_cli(raw)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(json.loads(first.stdout)["results"][0]["status"], "valid")
        original = self.path.read_bytes()
        second = self.run_cli(raw)
        self.assertEqual(second.returncode, 2)
        self.assertEqual(self.calls.read_text().splitlines(), ["called"])
        self.assertEqual(self.path.read_bytes(), original)

    def test_real_cli_remote_failure_keeps_billing_and_does_not_retry(self):
        raw = '{"status":"schema_error","error":"bad provider input","billing":{"credits_charged":0.28}}'
        result = self.run_cli(raw, exit_code=1)
        body = json.loads(result.stdout)
        self.assertEqual(body["status"], "schema_error")
        self.assertEqual(body["error_stage"], "provider")
        self.assertEqual(json.loads(self.path.read_text())["provider_response"]["body"]["billing"]["credits_charged"], .28)
        self.assertEqual(self.calls.read_text().splitlines(), ["called"])

    def test_timeout_preserves_partial_output_without_promoting_valid_email(self):
        raw = '{"toolResponse":{"raw":{"email":"owner@example.test","status":"valid"}},"billing":{"credits_charged":0.28}}'
        # Allow interpreter startup before testing a timeout with partial output.
        result = self.run_cli(raw, sleep=6, timeout=3)
        body = json.loads(result.stdout)
        self.assertEqual(body["status"], "timeout")
        self.assertEqual(body.get("results", []), [])
        saved = json.loads(self.path.read_text())
        self.assertIn("provider_response", saved)
        self.assertIn("0.28", json.dumps(saved["provider_response"]))
        self.assertEqual(self.calls.read_text().splitlines(), ["called"])

    def test_timeout_bytes_are_retained_with_no_deliverability_promotion(self):
        raw = b'{"toolResponse":{"raw":{"email":"owner@example.test","status":"valid"}}}'
        expired = subprocess.TimeoutExpired("fixture", .01, output=raw, stderr=b'api_key="fixture-secret"')
        with mock.patch.object(DEEPLINE.subprocess, "run", side_effect=expired) as call, \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            request = {"operation": "execute", "tool": "fixture", "payload": {}, "entity_type": "email_validation"}
            DEEPLINE.main(["--input", json.dumps(request), "--output-file", str(self.path)])
        body = json.loads(stdout.getvalue())
        self.assertEqual(body["status"], "timeout")
        self.assertEqual(body.get("results", []), [])
        saved = json.loads(self.path.read_text())
        self.assertTrue(saved["provider_response"]["timed_out"])
        self.assertNotIn("fixture-secret", self.path.read_text())
        self.assertEqual(call.call_count, 1)


if __name__ == "__main__":
    unittest.main()
