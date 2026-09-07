import io
from http.client import HTTPResponse
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError

from test_provider_scripts import DEEPLINE, SCRAPINGDOG


class ProviderBoundaryTests(unittest.TestCase):
    def request(self, adapter):
        if adapter is DEEPLINE:
            return {"operation": "execute", "tool": "fixture", "payload": {}, "limit": 1}
        return {"operation": "google_search", "query": "wholesalers", "limit": 1}

    def invoke(self, adapter, raw, path=None, request=None):
        method = "_invoke" if adapter is DEEPLINE else "_http_get"
        wire = (0, raw, "") if adapter is DEEPLINE else (200, raw, None)
        args = ["--input", json.dumps(request if request is not None else self.request(adapter))]
        if path is not None:
            args.extend(["--output-file", str(path)])
        with mock.patch.object(adapter, method, return_value=wire) as provider, \
             mock.patch.dict(os.environ, {"SCRAPINGDOG_API_KEY": "fixture-only"}), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            code = adapter.main(args)
        output = json.loads(stdout.getvalue(), parse_constant=self.reject_constant)
        return code, output, provider.call_count

    @staticmethod
    def reject_constant(value):
        raise AssertionError("stdout must be strict JSON: " + value)

    def test_malformed_outer_json_cannot_promote_a_valid_nested_fragment(self):
        payloads = [
            '{"error": broken, "results": []}',
            '{"results": [{"company":"Example","domain":"example.test"}]',
            '{"unexpected":\n {"results": []}',
            '{"results": []} {"error": "provider failed"}',
            '{"results": []}\nprovider failed',
        ]
        for raw in payloads:
            for prefix in ("", "Update available\n"):
                with self.subTest(raw=raw, prefix=prefix), tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "response.json"
                    code, body, calls = self.invoke(DEEPLINE, prefix + raw, path)
                    self.assertEqual((code, calls), (0, 1))
                    self.assertEqual(body["status"], "schema_error")
                    self.assertEqual(body["error_stage"], "response")
                    self.assertEqual(body.get("results", []), [])
                    self.assertEqual(json.loads(path.read_text())["provider_response"]["body"], prefix + raw)

    def test_valid_notice_prefixed_and_multiline_json_still_work(self):
        for raw in ('{"results": []}', '[{"company": "Example"}]'):
            for prefix in ("", "  \n", "Update available\n", "notice\n  "):
                with self.subTest(raw=raw, prefix=prefix):
                    self.assertEqual(DEEPLINE._json_from_text(prefix + raw), json.loads(raw))

    def test_every_truncated_prefix_stays_unresolved_including_email_validation(self):
        samples = [
            (DEEPLINE, '{"results":[{"company":"Example","domain":"example.test"}],"billing":{"credits_charged":0.28}}', None),
            (DEEPLINE, '{"toolResponse":{"raw":{"email":"owner@example.test","status":"valid"}},"billing":{"credits_charged":0.28}}', "email_validation"),
            (SCRAPINGDOG, '{"organic_results":[{"title":"Example","link":"https://example.test"}],"pagination":{"next_page_token":"page2"}}', None),
        ]
        for adapter, raw, entity_type in samples:
            request = self.request(adapter)
            if entity_type:
                request["entity_type"] = entity_type
            for length in range(1, len(raw)):
                with self.subTest(adapter=adapter.__name__, length=length, entity_type=entity_type):
                    code, body, calls = self.invoke(adapter, raw[:length], request=request)
                    self.assertEqual((code, calls), (0, 1))
                    self.assertEqual(body["status"], "schema_error")
                    self.assertEqual(body.get("results", []), [])

    def test_nonfinite_known_envelopes_are_diagnostic_not_results(self):
        for adapter in (DEEPLINE, SCRAPINGDOG):
            key = "results" if adapter is DEEPLINE else "organic_results"
            for token in ("NaN", "Infinity", "-Infinity", "1e9999"):
                for in_record in (False, True):
                    raw = ('{"' + key + '": [{"title":"Example", "value":' + token + '}]}'
                           if in_record else '{"' + key + '": [], "value":' + token + '}')
                    with self.subTest(adapter=adapter.__name__, token=token, in_record=in_record), tempfile.TemporaryDirectory() as directory:
                        path = Path(directory) / "response.json"
                        code, body, calls = self.invoke(adapter, raw, path)
                        self.assertEqual((code, calls), (0, 1))
                        self.assertEqual(body["status"], "schema_error")
                        self.assertEqual(body.get("results", []), [])
                        saved = json.loads(path.read_text(), parse_constant=self.reject_constant)
                        self.assertEqual(saved["receipt_status"], "complete")
                        self.assertEqual(saved["provider_response"]["body"], raw)

    def test_nonfinite_input_is_rejected_before_provider_dispatch(self):
        for adapter in (DEEPLINE, SCRAPINGDOG):
            for field in ("limit", "timeout_seconds", "payload"):
                for value in (float("inf"), float("-inf"), float("nan")):
                    request = self.request(adapter)
                    request[field] = {"value": value} if field == "payload" else value
                    with self.subTest(adapter=adapter.__name__, field=field, value=value):
                        code, body, calls = self.invoke(adapter, '{"results": []}', request=request)
                        self.assertEqual((code, calls), (2, 0))
                        self.assertEqual(body["status"], "schema_error")
                        self.assertEqual(body["error_stage"], "request")

    def test_incomplete_http_response_is_saved_without_results_or_retry(self):
        raw = b'{"organic_results":[{"title":"Example"}],"api_key":"fixture-secret"}'
        for status, encoding in ((200, "chunked"), (429, "chunked"), (200, "length"), (429, "length")):
            wire = (f"HTTP/1.1 {status} Test\r\nTransfer-Encoding: chunked\r\n\r\n{len(raw):x}\r\n".encode()
                    + raw + b"\r\n" if encoding == "chunked" else
                    f"HTTP/1.1 {status} Test\r\nContent-Length: {len(raw) + 100}\r\n\r\n".encode() + raw)
            connection = mock.Mock()
            connection.makefile.return_value = io.BytesIO(wire)
            response = HTTPResponse(connection)
            response.begin()
            behavior = ({"return_value": response} if status == 200 else
                        {"side_effect": HTTPError("https://api.scrapingdog.com", status, "limited", {}, response)})
            with self.subTest(status=status, encoding=encoding), tempfile.TemporaryDirectory() as directory, \
                 mock.patch.object(SCRAPINGDOG, "urlopen", **behavior) as provider, \
                 mock.patch.dict(os.environ, {"SCRAPINGDOG_API_KEY": "fixture-only"}), \
                 mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                path = Path(directory) / "response.json"
                code = SCRAPINGDOG.main(["--input", json.dumps(self.request(SCRAPINGDOG)), "--output-file", str(path)])
                body = json.loads(stdout.getvalue())
                self.assertEqual((code, provider.call_count), (0, 1))
                self.assertEqual(body["status"], "provider_error" if status == 200 else "rate_limited")
                self.assertEqual(body.get("results", []), [])
                saved = json.loads(path.read_text())
                self.assertEqual(saved["receipt_status"], "complete")
                self.assertTrue(saved["provider_response"]["incomplete"])
                self.assertEqual(saved["provider_response"]["http_status"], status)
                self.assertIn("Example", saved["provider_response"]["body"])
                self.assertNotIn("fixture-secret", path.read_text())
                self.assertTrue(response.closed)


if __name__ == "__main__":
    unittest.main()
