import io
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError

from test_provider_scripts import DEEPLINE, SCRAPINGDOG
from provider_output import ResponseFile


class ProviderOutputTests(unittest.TestCase):
    def request(self, adapter):
        if adapter is DEEPLINE:
            return {"operation": "execute", "tool": "test-tool", "payload": {}, "limit": 1}
        return {"operation": "google_search", "query": "wholesalers", "limit": 1}

    def invoke(self, adapter, path, response):
        target = "_invoke" if adapter is DEEPLINE else "_http_get"
        wire = (0, json.dumps(response), "") if adapter is DEEPLINE else (200, json.dumps(response), None)
        with mock.patch.object(adapter, target, return_value=wire) as call, \
             mock.patch.dict("os.environ", {"SCRAPINGDOG_API_KEY": "local-key"}), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            code = adapter.main(["--input", json.dumps(self.request(adapter)), "--output-file", str(path)])
        return code, json.loads(stdout.getvalue()), call.call_count

    def test_full_response_survives_normalized_row_limit_and_is_redacted(self):
        for adapter in (DEEPLINE, SCRAPINGDOG):
            with self.subTest(adapter=adapter.__name__), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "response.json"
                rows = [{"title": "One", "link": "https://one.example"},
                        {"title": "Two", "link": "https://two.example"}]
                raw = {"results" if adapter is DEEPLINE else "organic_results": rows, "api_key": "private-value"}
                code, body, count = self.invoke(adapter, path, raw)
                saved = json.loads(path.read_text())
                self.assertEqual((code, count), (0, 1))
                self.assertEqual(len(body["results"]), 1)
                self.assertEqual(saved["receipt_status"], "complete")
                self.assertEqual(len(saved["provider_response"]["body"][next(iter(raw))]), 2)
                self.assertNotIn("private-value", path.read_text())
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_schema_failure_retains_original_response_without_retry(self):
        for adapter in (DEEPLINE, SCRAPINGDOG):
            with self.subTest(adapter=adapter.__name__), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "response.json"
                raw = {"unexpected_provider_field": "needed for diagnosis", "password": "private-value"}
                code, body, count = self.invoke(adapter, path, raw)
                self.assertEqual(count, 1)
                self.assertEqual(body["status"], "schema_error")
                self.assertEqual(body["error_stage"], "response")
                self.assertEqual(json.loads(path.read_text())["provider_response"]["body"]["unexpected_provider_field"], raw["unexpected_provider_field"])

    def test_existing_or_unwritable_destination_prevents_dispatch(self):
        for adapter in (DEEPLINE, SCRAPINGDOG):
            with self.subTest(adapter=adapter.__name__), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "existing.json"
                path.write_text("user data")
                for destination in (path, Path(directory) / "missing" / "file.json"):
                    with mock.patch.object(adapter, "run") as run, mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                        code = adapter.main(["--input", json.dumps(self.request(adapter)), "--output-file", str(destination)])
                    self.assertEqual(code, 2)
                    self.assertEqual(json.loads(stdout.getvalue())["status"], "config_error")
                    run.assert_not_called()
                self.assertEqual(path.read_text(), "user data")

    def test_response_is_saved_before_normalizer_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "response.json"
            def normalize(*args):
                saved = json.loads(path.read_text())
                self.assertEqual(saved["receipt_status"], "response_received")
                self.assertEqual(saved["provider_response"]["body"]["results"], [])
                return {"status": "no_results", "results": []}
            with mock.patch.object(DEEPLINE, "_execute_output", side_effect=normalize):
                self.invoke(DEEPLINE, path, {"results": []})

    def test_failure_after_dispatch_preserves_response_and_never_repeats_call(self):
        original = ResponseFile._write
        def fail_final(writer, document):
            if document.get("receipt_status") == "complete":
                raise OSError("disk unavailable")
            return original(writer, document)
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(ResponseFile, "_write", fail_final):
            path = Path(directory) / "response.json"
            code, body, count = self.invoke(DEEPLINE, path, {"results": []})
            self.assertEqual((code, count), (2, 1))
            self.assertIn("receipt_error", body)
            self.assertEqual(json.loads(path.read_text())["receipt_status"], "response_received")

    def test_concurrent_file_change_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "response.json"
            writer = ResponseFile(path, DEEPLINE.redact)
            path.write_text("concurrent user edit")
            writer.capture({"results": []})
            self.assertFalse(writer.finish({"status": "ok"}))
            self.assertEqual(path.read_text(), "concurrent user edit")

    def test_local_request_error_is_distinct_and_makes_no_provider_call(self):
        for adapter in (DEEPLINE, SCRAPINGDOG):
            target = "_invoke" if adapter is DEEPLINE else "_http_get"
            with mock.patch.object(adapter, target) as call, mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                code = adapter.main(["--input", '{"operation":"not-supported"}'])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(stdout.getvalue())["error_stage"], "request")
            call.assert_not_called()

    def test_http_error_body_is_saved_beyond_old_diagnostic_limit(self):
        raw = {"error": "quota exceeded", "diagnostic": "x" * 6000, "api_key": "private-value"}
        error = HTTPError("https://api.scrapingdog.com", 429, "limited", {}, io.BytesIO(json.dumps(raw).encode()))
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(SCRAPINGDOG, "urlopen", side_effect=error) as call, \
             mock.patch.dict("os.environ", {"SCRAPINGDOG_API_KEY": "local-key"}), mock.patch("sys.stdout", new_callable=io.StringIO):
            path = Path(directory) / "error.json"
            SCRAPINGDOG.main(["--input", json.dumps(self.request(SCRAPINGDOG)), "--output-file", str(path)])
            saved = json.loads(path.read_text())
            self.assertEqual(len(saved["provider_response"]["body"]["diagnostic"]), 6000)
            self.assertEqual(saved["status"], "rate_limited")
            self.assertNotIn("private-value", path.read_text())
            self.assertEqual(call.call_count, 1)

    def test_quoted_credentials_in_non_json_text_are_redacted(self):
        text = 'not JSON: "api_key": "private value", cookie="session-secret"'
        for adapter in (DEEPLINE, SCRAPINGDOG):
            redacted = adapter.redact(text)
            self.assertNotIn("private value", redacted)
            self.assertNotIn("session-secret", redacted)


if __name__ == "__main__":
    unittest.main()
