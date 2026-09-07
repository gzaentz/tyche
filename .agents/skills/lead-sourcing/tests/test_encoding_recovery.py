import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from test_provider_scripts import DEEPLINE, SCRAPINGDOG


INVALID_UTF8_CLI = r'''#!/usr/bin/env python3
import os

with open(os.environ["FIXTURE_CALL_LOG"], "a", encoding="utf-8") as stream:
    stream.write("called\n")
os.write(1, b'{"results":[{"company":"Caf\xc3\xa9","source_url":"https://source.example/evidence","text":"broken\xff"}]}')
'''


class EncodingRecoveryTests(unittest.TestCase):
    def request(self, adapter):
        if adapter is DEEPLINE:
            return {"operation": "execute", "tool": "fixture", "payload": {}, "limit": 1}
        return {"operation": "google_search", "query": "wholesalers", "limit": 1}

    @staticmethod
    def parse_strict_stdout(callback, encoding="utf-8"):
        raw_stdout = io.BytesIO()
        stdout = io.TextIOWrapper(raw_stdout, encoding=encoding, errors="strict")
        with mock.patch("sys.stdout", stdout):
            code = callback()
            stdout.flush()
        return code, json.loads(raw_stdout.getvalue().decode("utf-8", errors="strict"))

    @staticmethod
    def contains_string(value, expected):
        if isinstance(value, str):
            return expected in value
        if isinstance(value, dict):
            return any(EncodingRecoveryTests.contains_string(item, expected) for item in value.values())
        if isinstance(value, list):
            return any(EncodingRecoveryTests.contains_string(item, expected) for item in value)
        return False

    def test_escaped_lone_surrogate_keeps_receipt_and_stdout_parseable(self):
        company = "Caf\u00e9 \u00d8resund"
        source = "https://source.example/evidence/\u00e9"
        raw_by_adapter = {
            DEEPLINE: (
                '{"toolResponse":{"rawV2":{"results":[{"company_name":'
                + json.dumps(company, ensure_ascii=False)
                + ',"source_url":'
                + json.dumps(source, ensure_ascii=False)
                + ',"text":"bad\\ud800"}]}}}'
            ),
            SCRAPINGDOG: (
                '{"organic_results":[{"title":'
                + json.dumps(company, ensure_ascii=False)
                + ',"link":'
                + json.dumps(source, ensure_ascii=False)
                + ',"snippet":"bad\\ud800"}]}'
            ),
        }
        for adapter, raw in raw_by_adapter.items():
            method = "_invoke" if adapter is DEEPLINE else "_http_get"
            wire = (0, raw, "") if adapter is DEEPLINE else (200, raw, None)
            with self.subTest(adapter=adapter.__name__), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "response.json"
                with mock.patch.object(adapter, method, return_value=wire) as provider, \
                     mock.patch.dict(os.environ, {"SCRAPINGDOG_API_KEY": "fixture-key"}, clear=True):
                    code, body = self.parse_strict_stdout(
                        lambda: adapter.main(
                            [
                                "--input",
                                json.dumps(self.request(adapter)),
                                "--output-file",
                                str(path),
                            ]
                        )
                    )
                saved = json.loads(path.read_text(encoding="utf-8"))
                self.assertIn(code, (0, 2))
                self.assertIn(body["status"], {"ok", "no_results", "schema_error", "provider_error"})
                self.assertEqual(provider.call_count, 1)
                self.assertEqual(saved["receipt_status"], "complete")
                self.assertTrue(self.contains_string(saved, company))
                self.assertTrue(self.contains_string(saved, source))

    def test_international_text_survives_ascii_stdout(self):
        company = "Caf\u00e9 \u00d8resund \u6771\u4eac"
        for adapter in (DEEPLINE, SCRAPINGDOG):
            rows = {"results": [{"company": company}]} if adapter is DEEPLINE else {"organic_results": [{"title": company}]}
            raw = json.dumps(rows, ensure_ascii=False)
            method = "_invoke" if adapter is DEEPLINE else "_http_get"
            wire = (0, raw, "") if adapter is DEEPLINE else (200, raw, None)
            with self.subTest(adapter=adapter.__name__), mock.patch.object(adapter, method, return_value=wire) as provider, \
                 mock.patch.dict(os.environ, {"SCRAPINGDOG_API_KEY": "fixture-key"}):
                code, body = self.parse_strict_stdout(
                    lambda: adapter.main(["--input", json.dumps(self.request(adapter))]), encoding="ascii")
                self.assertEqual((code, provider.call_count), (0, 1))
                self.assertEqual(body["status"], "ok")
                self.assertTrue(self.contains_string(body["results"], company))

    def test_invalid_bytes_in_notice_do_not_promote_nested_valid_email(self):
        raw = '\udcff\n{"toolResponse":{"raw":{"email":"owner@example.test","status":"valid"}}}'
        with mock.patch.object(DEEPLINE, "_invoke", return_value=(0, raw, "")) as provider:
            body, code = DEEPLINE.run({**self.request(DEEPLINE), "entity_type": "email_validation"})
        self.assertEqual((code, provider.call_count), (0, 1))
        self.assertEqual(body["status"], "schema_error")
        self.assertEqual(body.get("results", []), [])

    def test_real_deepline_cli_invalid_utf8_is_diagnostic_without_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            provider = directory / "fake-deepline"
            provider.write_text(INVALID_UTF8_CLI, encoding="utf-8")
            provider.chmod(0o700)
            calls = directory / "calls.log"
            path = directory / "response.json"
            env = {
                "DEEPLINE_BIN": str(provider),
                "FIXTURE_CALL_LOG": str(calls),
                "PATH": os.environ.get("PATH", ""),
            }
            request = {
                "operation": "execute",
                "tool": "fixture",
                "payload": {},
                "limit": 1,
            }
            with mock.patch.dict(os.environ, env, clear=True):
                code, body = self.parse_strict_stdout(
                    lambda: DEEPLINE.main(
                        ["--input", json.dumps(request), "--output-file", str(path)]
                    )
                )
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn(code, (0, 2))
            self.assertIn(body["status"], {"schema_error", "provider_error"})
            self.assertEqual(saved["receipt_status"], "complete")
            self.assertIn(b"broken\xff", saved["provider_response"]["body"].encode("utf-8", errors="surrogateescape"))
            self.assertEqual(calls.read_text(encoding="utf-8").splitlines(), ["called"])


if __name__ == "__main__":
    unittest.main()
