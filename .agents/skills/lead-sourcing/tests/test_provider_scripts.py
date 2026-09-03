from __future__ import annotations

import importlib.util
import io
import json
import pathlib
import socket
import subprocess
import sys
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse


ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DEEPLINE = load_script("deepline")
SCRAPINGDOG = load_script("scrapingdog")


class FakeProcess:
    def __init__(self, stdout: str, stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class FakeResponse:
    def __init__(self, body, status=200):
        self.body = body if isinstance(body, bytes) else body.encode("utf-8")
        self.status = status
        self.code = status
        self.closed = False

    def read(self, _size=None):
        return self.body

    def close(self):
        self.closed = True


class ProviderScriptTests(unittest.TestCase):
    def test_help_output_is_available_for_both_adapters(self):
        for path in (ROOT / "scripts" / "deepline.py", ROOT / "scripts" / "scrapingdog.py"):
            completed = subprocess.run(
                [sys.executable, str(path), "--help"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0)
            self.assertIn("--input", completed.stdout)

    def test_deepline_execute_uses_payload_file_and_normalizes_raw_v2(self):
        seen = {}

        def fake_run(command, **kwargs):
            seen["command"] = command
            seen["timeout"] = kwargs["timeout"]
            payload_path = command[command.index("--input") + 1][1:]
            seen["payload_path"] = payload_path
            with open(payload_path, encoding="utf-8") as handle:
                seen["payload"] = json.load(handle)
            return FakeProcess(
                "update available\n"
                '{"toolResponse":{"rawV2":{"results":[{"company_name":"Acme",'
                '"website":"https://www.acme.test/", "signal":"hiring",'
                '"source_url":"https://example.test/evidence", "snippet":"Hiring"}]}}}'
            )

        request = {
            "operation": "execute",
            "tool": "company_search",
            "payload": {"query": "fintech", "api_key": "payload-only-secret"},
            "timeout_seconds": 9,
        }
        with mock.patch.object(DEEPLINE.subprocess, "run", side_effect=fake_run):
            body, code = DEEPLINE.run(request)
        self.assertEqual(code, 0)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["results"][0]["company"], "Acme")
        self.assertEqual(body["results"][0]["domain"], "acme.test")
        self.assertEqual(body["results"][0]["evidence_url"], "https://example.test/evidence")
        self.assertEqual(
            seen["payload"], {"query": "fintech", "api_key": "payload-only-secret"}
        )
        self.assertEqual(seen["timeout"], 9.0)
        self.assertIn("--input", seen["command"])
        self.assertTrue(seen["command"][seen["command"].index("--input") + 1].startswith("@"))
        self.assertNotIn("fintech", " ".join(seen["command"]))
        self.assertNotIn("payload-only-secret", " ".join(seen["command"]))
        self.assertNotIn("--wait", seen["command"])
        self.assertNotIn("payload-only-secret", json.dumps(body))
        self.assertFalse(pathlib.Path(seen["payload_path"]).exists())

    def test_deepline_execute_limit_is_wrapper_only_and_capped_at_ten(self):
        seen = {}
        rows = [
            {"company_name": f"Company {index}", "website": f"c{index}.test"}
            for index in range(12)
        ]

        def fake_run(command, **_kwargs):
            payload_path = command[command.index("--input") + 1][1:]
            with open(payload_path, encoding="utf-8") as handle:
                seen["payload"] = json.load(handle)
            return FakeProcess(json.dumps({"toolResponse": {"rawV2": {"results": rows}}}))

        with mock.patch.object(DEEPLINE.subprocess, "run", side_effect=fake_run):
            body, code = DEEPLINE.run(
                {
                    "operation": "execute",
                    "tool": "company_search",
                    "payload": {"query": "inventory"},
                    "limit": 20,
                }
            )

        self.assertEqual(code, 0)
        self.assertEqual(len(body["results"]), 10)
        self.assertEqual(seen["payload"], {"query": "inventory"})

        with self.assertRaises(DEEPLINE.InputError):
            DEEPLINE._validate_request(
                {
                    "operation": "execute",
                    "tool": "company_search",
                    "payload": {},
                    "limit": 0,
                }
            )

    def test_deepline_leading_json_notice_and_provider_outcomes(self):
        self.assertEqual(DEEPLINE._json_from_text("notice\n[1, 2]"), [1, 2])

        with mock.patch.object(
            DEEPLINE.subprocess,
            "run",
            return_value=FakeProcess("", "429 rate limit", returncode=1),
        ):
            body, code = DEEPLINE.run({"operation": "search", "query": "software"})
        self.assertEqual(code, 0)
        self.assertEqual(body["status"], "rate_limited")
        self.assertEqual(body["error"]["message"], "429 rate limit")

        with mock.patch.object(
            DEEPLINE.subprocess,
            "run",
            return_value=FakeProcess(
                "Example: deepline tools execute test_rate_limit",
                "error: unknown option '--wait' api_key=provider-secret",
                returncode=1,
            ),
        ):
            body, code = DEEPLINE.run({"operation": "search", "query": "software"})
        self.assertEqual(code, 0)
        self.assertEqual(body["status"], "provider_error")
        self.assertIn("unknown option", body["error"]["message"])
        self.assertNotIn("provider-secret", json.dumps(body))

        with mock.patch.object(
            DEEPLINE.subprocess,
            "run",
            return_value=FakeProcess(
                'Update available\n{"status":"error","error":{"message":"provider rejected the filter"}}',
                returncode=1,
            ),
        ):
            body, code = DEEPLINE.run({"operation": "search", "query": "software"})
        self.assertEqual(code, 0)
        self.assertEqual(body["status"], "provider_error")
        self.assertEqual(body["error"]["message"], "provider rejected the filter")

    def test_deepline_uses_bin_override_and_documented_describe_command(self):
        seen = {}

        def fake_run(command, **kwargs):
            seen["command"] = command
            return FakeProcess('{"name":"company_search","description":"find companies"}')

        with mock.patch.dict(DEEPLINE.os.environ, {"DEEPLINE_BIN": "/opt/deepline"}, clear=False), mock.patch.object(
            DEEPLINE.subprocess, "run", side_effect=fake_run
        ):
            body, code = DEEPLINE.run({"operation": "describe", "tool": "company_search"})
        self.assertEqual(code, 0)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["results"], [{"name": "company_search", "description": "find companies"}])
        self.assertEqual(seen["command"][:3], ["/opt/deepline", "tools", "describe"])

    def test_deepline_does_not_turn_unknown_or_row_status_into_no_results(self):
        with mock.patch.object(DEEPLINE.subprocess, "run", return_value=FakeProcess('{"unexpected":true}')):
            body, code = DEEPLINE.run({"operation": "execute", "tool": "company_search", "payload": {}})
        self.assertEqual(code, 0)
        self.assertEqual(body["status"], "schema_error")

        with mock.patch.object(
            DEEPLINE.subprocess, "run", return_value=FakeProcess('{"unexpected":true}')
        ):
            search_body, _ = DEEPLINE.run({"operation": "search", "query": "companies"})
            describe_body, _ = DEEPLINE.run(
                {"operation": "describe", "tool": "company_search"}
            )
        self.assertEqual(search_body["status"], "schema_error")
        self.assertEqual(describe_body["status"], "schema_error")

        with mock.patch.object(
            DEEPLINE.subprocess,
            "run",
            return_value=FakeProcess('{"results":[{"status":"error","company":"Acme"}]}'),
        ):
            body, code = DEEPLINE.run({"operation": "execute", "tool": "company_search", "payload": {}})
        self.assertEqual(code, 0)
        self.assertEqual(body["status"], "ok")

        with mock.patch.object(DEEPLINE.subprocess, "run", return_value=FakeProcess('{"tools":[]}')):
            body, code = DEEPLINE.run({"operation": "search", "query": "nothing"})
        self.assertEqual(code, 0)
        self.assertEqual(body["status"], "no_results")

        with mock.patch.object(DEEPLINE.subprocess, "run", return_value=FakeProcess('{"toolResponse":{"rawV2":{}}}')):
            body, code = DEEPLINE.run({"operation": "execute", "tool": "company_search", "payload": {}})
        self.assertEqual(code, 0)
        self.assertEqual(body["status"], "no_results")

        with mock.patch.object(DEEPLINE.subprocess, "run", return_value=FakeProcess('{"error":"bad response"}')):
            body, code = DEEPLINE.run({"operation": "describe", "tool": "company_search"})
        self.assertEqual(code, 0)
        self.assertEqual(body["status"], "provider_error")

        with mock.patch.object(DEEPLINE.subprocess, "run", return_value=FakeProcess('{"status":"rate_limited"}')):
            body, code = DEEPLINE.run({"operation": "execute", "tool": "company_search", "payload": {}})
        self.assertEqual(code, 0)
        self.assertEqual(body["status"], "rate_limited")

        with mock.patch.object(
            DEEPLINE.subprocess,
            "run",
            return_value=FakeProcess(
                '{"status":"rate_limited","error":{"message":"Slow down token=provider-secret"}}'
            ),
        ):
            body, code = DEEPLINE.run(
                {"operation": "execute", "tool": "company_search", "payload": {}}
            )
        self.assertEqual(code, 0)
        self.assertEqual(body["status"], "rate_limited")
        self.assertIn("Slow down", body["error"]["message"])
        self.assertNotIn("provider-secret", json.dumps(body))

        with mock.patch.object(DEEPLINE.subprocess, "run", return_value=FakeProcess('{"toolResponse":{"rawV2":{"status":"error","company":"Acme"}}}')):
            body, code = DEEPLINE.run({"operation": "execute", "tool": "company_search", "payload": {}})
        self.assertEqual(code, 0)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["results"][0]["company"], "Acme")

    def test_deepline_accepts_element_shapes_and_uses_longer_execute_default(self):
        seen = {}

        def fake_run(command, **kwargs):
            seen["timeout"] = kwargs["timeout"]
            return FakeProcess(
                '{"toolResponse":{"rawV2":{"elements":'
                '[{"company_name":"Acme","website":"https://acme.test"}]}}}'
            )

        with mock.patch.object(DEEPLINE.subprocess, "run", side_effect=fake_run):
            body, code = DEEPLINE.run(
                {"operation": "execute", "tool": "company_search", "payload": {}}
            )
        self.assertEqual(code, 0)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["results"][0]["company"], "Acme")
        self.assertEqual(seen["timeout"], 240.0)

        parsed = {
            "toolResponse": {
                "rawV2": {"element": {"company_name": "Solo", "website": "solo.test"}}
            }
        }
        self.assertEqual(DEEPLINE._records(parsed)[0]["company_name"], "Solo")

    def test_deepline_normalizes_current_linkedin_position(self):
        body = DEEPLINE._execute_output(
            {
                "toolResponse": {
                    "rawV2": {
                        "elements": [
                            {
                                "firstName": "Alex",
                                "lastName": "Rivera",
                                "linkedinUrl": "https://www.linkedin.com/in/alex-rivera",
                                "currentPositions": [
                                    {
                                        "companyName": "Acme",
                                        "companyLinkedinUrl": "https://www.linkedin.com/company/acme",
                                        "title": "Logistics Director",
                                        "current": True,
                                    }
                                ],
                            }
                        ]
                    }
                }
            },
            "harvestapi_search_leads",
            "contact",
        )

        row = body["results"][0]
        self.assertEqual(row["company"], "Acme")
        self.assertEqual(row["company_linkedin_url"], "https://www.linkedin.com/company/acme")
        self.assertEqual(row["full_name"], "Alex Rivera")
        self.assertEqual(row["current_title"], "Logistics Director")

    def test_deepline_current_and_legacy_envelopes_preserve_all_rows(self):
        rows = [
            {"company_name": "Acme", "website": "acme.test"},
            {"company_name": "Beta", "website": "beta.test"},
        ]
        envelopes = [
            {"toolResponse": {"rawV2": {"data": rows}}},
            {"toolResponse": {"raw": rows}},
            {"raw": rows},
        ]
        for envelope in envelopes:
            with self.subTest(envelope=list(envelope)):
                body = DEEPLINE._execute_output(envelope, "company_search")
                self.assertEqual(body["status"], "ok")
                self.assertEqual(
                    [row["company"] for row in body["results"]], ["Acme", "Beta"]
                )

    def test_deepline_scalar_count_envelope_is_one_normalized_result(self):
        response = {
            "status": "completed",
            "toolResponse": {
                "raw": {"count": 0, "total": 0, "provider_note": "empty"}
            },
        }
        body = DEEPLINE._execute_output(response, "discolike_count")
        self.assertEqual(body["status"], "ok")
        self.assertEqual(len(body["results"]), 1)
        self.assertEqual(body["results"][0]["count"], 0)
        self.assertEqual(body["results"][0]["total"], 0)
        self.assertEqual(body["results"][0]["provider_note"], "empty")

        summary_body = DEEPLINE._execute_output(
            {"summary": {"count": 7, "total": 12}}, "discolike_count"
        )
        self.assertEqual(summary_body["status"], "ok")
        self.assertEqual(summary_body["results"][0]["count"], 7)
        self.assertEqual(summary_body["results"][0]["total"], 12)

    def test_deepline_normalizes_scalar_email_validation_without_treating_status_as_route_failure(self):
        response = {
            "status": "completed",
            "toolResponse": {
                "raw": {
                    "address": "ada@example.com",
                    "status": "invalid",
                    "sub_status": "mailbox_not_found",
                    "processed_at": "2026-09-03 12:00:00",
                }
            },
        }
        body = DEEPLINE._execute_output(
            response, "runtime-email-validator", "email_validation"
        )

        self.assertEqual(body["status"], "ok")
        self.assertEqual(len(body["results"]), 1)
        row = body["results"][0]
        self.assertEqual(row["email"], "ada@example.com")
        self.assertEqual(row["email_status"], "invalid")
        self.assertEqual(row["email_sub_status"], "mailbox_not_found")
        self.assertEqual(row["entity_type"], "email_validation")
        self.assertNotIn("contact", row)

        contact_row = {
            "full_name": "Ada Example",
            "job_title": "VP Sales",
            "email": "ada@example.com",
            "status": "valid",
        }
        contact = DEEPLINE.normalize_evidence(contact_row, entity_type="contact")
        self.assertEqual(contact["full_name"], "Ada Example")
        self.assertEqual(contact["current_title"], "VP Sales")
        self.assertEqual(contact["entity_type"], "contact")

    def test_deepline_keeps_explicit_zerobounce_status_when_default_verdict_fails(self):
        response = json.dumps(
            {
                "status": "failed",
                "error": {"message": "default send policy rejected the address"},
                "toolResponse": {
                    "raw": {
                        "address": "ada@example.com",
                        "status": "catch-all",
                        "sub_status": None,
                    }
                },
            }
        )
        with mock.patch.object(
            DEEPLINE.subprocess,
            "run",
            return_value=FakeProcess(response, returncode=1),
        ):
            body, code = DEEPLINE.run(
                {
                    "operation": "execute",
                    "tool": "runtime-email-validator",
                    "entity_type": "email_validation",
                    "payload": {"email": "ada@example.com"},
                    "limit": 1,
                }
            )

        self.assertEqual(code, 0)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["results"][0]["email_status"], "catch-all")

    def test_deepline_non_numeric_count_shape_remains_schema_error(self):
        body = DEEPLINE._execute_output(
            {"toolResponse": {"raw": {"count": "unknown"}}},
            "discolike_count",
        )
        self.assertEqual(body["status"], "schema_error")

    def test_deepline_autocomplete_suggestions_are_normalized_from_declared_lists(self):
        response = (
            '{"status":"completed","extractedLists":{"suggestions":'
            '[{"value":"Global Health"},{"value":"Humanitarian Aid"}]}}'
        )
        with mock.patch.object(
            DEEPLINE.subprocess, "run", return_value=FakeProcess(response)
        ):
            body, code = DEEPLINE.run(
                {
                    "operation": "execute",
                    "tool": "crustdata_v3_company_search_autocomplete",
                    "payload": {"field": "industry", "query": "health", "limit": 2},
                }
            )
        self.assertEqual(code, 0)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(
            [row["value"] for row in body["results"]],
            ["Global Health", "Humanitarian Aid"],
        )
        self.assertTrue(all(row["company"] is None for row in body["results"]))
        self.assertTrue(all("contact" not in row for row in body["results"]))

        raw_response = (
            '{"status":"completed","toolResponse":{"raw":{"suggestions":'
            '[{"value":"Global Health"}]}}}'
        )
        with mock.patch.object(
            DEEPLINE.subprocess, "run", return_value=FakeProcess(raw_response)
        ):
            raw_body, raw_code = DEEPLINE.run(
                {
                    "operation": "execute",
                    "tool": "crustdata_v3_company_search_autocomplete",
                    "payload": {"field": "industry", "query": "health", "limit": 1},
                }
            )
        self.assertEqual(raw_code, 0)
        self.assertEqual(raw_body["status"], "ok")
        self.assertEqual(raw_body["results"][0]["value"], "Global Health")

        preview_response = (
            '{"status":"completed","output_preview":{"kind":"list",'
            '"rowCount":1,"columns":["value"],"preview":'
            '[{"value":"Humanitarian Aid"}]}}'
        )
        with mock.patch.object(
            DEEPLINE.subprocess, "run", return_value=FakeProcess(preview_response)
        ):
            preview_body, preview_code = DEEPLINE.run(
                {
                    "operation": "execute",
                    "tool": "crustdata_v3_company_search_autocomplete",
                    "payload": {"field": "industry", "query": "aid", "limit": 1},
                }
            )
        self.assertEqual(preview_code, 0)
        self.assertEqual(preview_body["status"], "ok")
        self.assertEqual(preview_body["results"][0]["value"], "Humanitarian Aid")

    def test_deepline_autocomplete_handles_nested_suggestion_preview_and_empty_lists(self):
        nested = {
            "toolResponse": {
                "rawV2": {
                    "data": {
                        "extracted_lists": {
                            "suggestions": {"preview": [{"label": "Education"}]}
                        }
                    }
                }
            }
        }
        body = DEEPLINE._execute_output(
            nested, "crustdata_v3_company_search_autocomplete"
        )
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["results"][0]["label"], "Education")
        self.assertIsNone(body["results"][0]["company"])

        empty = {
            "status": "completed",
            "extractedLists": {"suggestions": []},
        }
        empty_body = DEEPLINE._execute_output(
            empty, "crustdata_v3_company_search_autocomplete"
        )
        self.assertEqual(empty_body["status"], "no_results")
        self.assertEqual(empty_body["results"], [])

        malformed = {
            "status": "completed",
            "extractedLists": {"suggestions": {"unexpected": "shape"}},
        }
        malformed_body = DEEPLINE._execute_output(
            malformed, "crustdata_v3_company_search_autocomplete"
        )
        self.assertEqual(malformed_body["status"], "schema_error")

    def test_deepline_extracted_lists_prefer_result_keys_over_metadata_lists(self):
        for result_key in ("suggestions", "results", "items", "records", "data"):
            with self.subTest(result_key=result_key):
                response = {
                    "status": "completed",
                    "extractedLists": {
                        "columns": ["value"],
                        "metadata": [{"name": "ignored"}],
                        result_key: [{"value": result_key}],
                    },
                }
                body = DEEPLINE._execute_output(response, "autocomplete")
                self.assertEqual(body["status"], "ok")
                self.assertEqual(body["results"][0]["value"], result_key)

    def test_invalid_deepline_cli_input_is_schema_error_and_single_json(self):
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "deepline.py"), "--input", "[]"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(len(completed.stdout.strip().splitlines()), 1)
        self.assertEqual(json.loads(completed.stdout)["status"], "schema_error")

    def test_deepline_subprocess_timeout_is_explicit_and_keeps_tool_context(self):
        with mock.patch.object(
            DEEPLINE.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(["deepline"], 1),
        ):
            body, code = DEEPLINE.run(
                {"operation": "execute", "tool": "company_search", "payload": {}}
            )
        self.assertEqual(code, 0)
        self.assertEqual(body["status"], "timeout")
        self.assertEqual(body["tool"], "company_search")

    def test_deepline_contact_aliases_and_entity_type_are_wrapper_metadata(self):
        seen = {}

        def fake_run(command, **kwargs):
            payload_path = command[command.index("--input") + 1][1:]
            with open(payload_path, encoding="utf-8") as handle:
                seen["payload"] = json.load(handle)
            seen["command"] = command
            return FakeProcess(
                '{"results":[{"company_name":"Acme", "company_domain":"acme.test",'
                '"full_name":"Jane Rivera", "job_title":"VP Sales",'
                '"linkedin_url":"https://www.linkedin.com/in/jane-rivera",'
                '"source_url":"https://acme.test/team", "evidence_text":"Current role"}]}'
            )

        request = {
            "operation": "execute",
            "tool": "runtime-person-tool",
            "entity_type": "contact",
            "payload": {"query": "Acme VP Sales"},
        }
        with mock.patch.object(DEEPLINE.subprocess, "run", side_effect=fake_run):
            body, code = DEEPLINE.run(request)
        self.assertEqual(code, 0)
        self.assertEqual(body["entity_type"], "contact")
        result = body["results"][0]
        self.assertEqual(result["entity_type"], "contact")
        self.assertEqual(result["contact"], "Jane Rivera")
        self.assertEqual(result["contact_name"], "Jane Rivera")
        self.assertEqual(result["full_name"], "Jane Rivera")
        self.assertEqual(result["contact_title"], "VP Sales")
        self.assertEqual(result["current_title"], "VP Sales")
        self.assertEqual(
            result["contact_url"], "https://www.linkedin.com/in/jane-rivera"
        )
        self.assertEqual(seen["payload"], {"query": "Acme VP Sales"})
        self.assertNotIn("entity_type", " ".join(seen["command"]))

    def test_deepline_contact_aliases_do_not_fabricate_missing_evidence(self):
        result = DEEPLINE.normalize_evidence(
            {"account": "Acme", "person_name": "Jane Rivera", "role": "VP Sales"},
            entity_type="contact",
        )
        self.assertEqual(result["contact"], "Jane Rivera")
        self.assertEqual(result["contact_title"], "VP Sales")
        self.assertIsNone(result["evidence_url"])
        self.assertIsNone(result["evidence_text"])

        linkedin_company_profile = DEEPLINE.normalize_evidence(
            {
                "company_name": "Acme",
                "company_url": "https://www.linkedin.com/company/acme",
                "full_name": "Jane Rivera",
                "job_title": "VP Sales",
            },
            entity_type="contact",
        )
        self.assertIsNone(linkedin_company_profile["domain"])
        self.assertEqual(
            linkedin_company_profile["company_url"],
            "https://www.linkedin.com/company/acme",
        )

        linkedin_domain_field = DEEPLINE.normalize_evidence(
            {
                "company_name": "Acme",
                "domain": "https://www.linkedin.com/company/acme",
                "website": "https://acme.test",
                "full_name": "Jane Rivera",
                "job_title": "VP Sales",
            },
            entity_type="contact",
        )
        self.assertEqual(linkedin_domain_field["domain"], "acme.test")

    def test_deepline_company_entity_does_not_fabricate_contact_fields(self):
        result = DEEPLINE.normalize_evidence(
            {
                "company_name": "Acme",
                "domain": "acme.test",
                "linkedin_url": "linkedin.com/company/acme",
            },
            entity_type="company",
        )
        self.assertEqual(result["entity_type"], "company")
        self.assertEqual(result["linkedin_url"], "linkedin.com/company/acme")
        self.assertNotIn("contact", result)
        self.assertNotIn("contact_url", result)

        inferred_company = DEEPLINE.normalize_evidence(
            {
                "company_name": "Acme",
                "domain": "acme.test",
                "linkedin_url": "https://www.linkedin.com/company/acme",
            }
        )
        self.assertEqual(inferred_company["entity_type"], "company")
        self.assertNotIn("contact_url", inferred_company)

    def test_deepline_v3_company_basic_info_normalizes_identity_without_dropping_source_fields(self):
        row = {
            "basic_info": {
                "name": "Global Health Partners",
                "primary_domain": "https://www.globalhealthpartners.example/",
                "professional_network_url": "https://www.linkedin.com/company/global-health-partners",
            },
            "headcount": {"total": 275},
            "locations": {"headquarters": "New York, United States"},
        }
        result = DEEPLINE.normalize_evidence(row, entity_type="company")
        self.assertEqual(result["company"], "Global Health Partners")
        self.assertEqual(result["domain"], "globalhealthpartners.example")
        self.assertEqual(result["basic_info"], row["basic_info"])
        self.assertEqual(result["headcount"], {"total": 275})
        self.assertEqual(result["entity_type"], "company")
        self.assertNotIn("contact", result)

        explicit = DEEPLINE.normalize_evidence(
            {
                "company_name": "Explicit Company",
                "domain": "explicit.example",
                "basic_info": {
                    "name": "Nested Company",
                    "primary_domain": "nested.example",
                },
            },
            entity_type="company",
        )
        self.assertEqual(explicit["company"], "Explicit Company")
        self.assertEqual(explicit["domain"], "explicit.example")

    def test_deepline_recognizes_direct_camelcase_contact_objects(self):
        direct = {
            "fullName": "Jane Rivera",
            "firstName": "Jane",
            "lastName": "Rivera",
            "currentTitle": "VP Sales",
            "profileUrl": "https://www.linkedin.com/in/jane-rivera",
        }
        self.assertEqual(DEEPLINE._records(direct), [direct])
        self.assertTrue(DEEPLINE._known_envelope(direct))
        result = DEEPLINE.normalize_evidence(direct)
        self.assertEqual(result["full_name"], "Jane Rivera")
        self.assertEqual(result["current_title"], "VP Sales")
        self.assertEqual(
            result["contact_url"], "https://www.linkedin.com/in/jane-rivera"
        )

        parts_only = DEEPLINE.normalize_evidence(
            {"firstName": "Jane", "lastName": "Rivera", "jobTitle": "VP Sales"}
        )
        self.assertEqual(parts_only["full_name"], "Jane Rivera")
        self.assertEqual(parts_only["current_title"], "VP Sales")

    def test_missing_local_provider_configuration_is_explicit_json(self):
        clean_environment = {
            key: value
            for key, value in dict(SCRAPINGDOG.os.environ).items()
            if key != "SCRAPINGDOG_API_KEY"
        }
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "scrapingdog.py"),
                "--input",
                '{"operation":"google_search","query":"Acme"}',
            ],
            capture_output=True,
            text=True,
            check=False,
            env=clean_environment,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(len(completed.stdout.strip().splitlines()), 1)
        self.assertEqual(json.loads(completed.stdout)["status"], "config_error")

        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "deepline.py"),
                "--input",
                '{"operation":"search","query":"companies"}',
            ],
            capture_output=True,
            text=True,
            check=False,
            env={**clean_environment, "DEEPLINE_BIN": "/missing/deepline"},
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(len(completed.stdout.strip().splitlines()), 1)
        self.assertEqual(json.loads(completed.stdout)["status"], "config_error")

    def test_scrapingdog_google_search_is_normalized_and_key_is_not_returned(self):
        seen = {}

        def fake_urlopen(request, timeout):
            seen["url"] = request.full_url
            seen["timeout"] = timeout
            return FakeResponse(
                '{"organic_results":[{"rank":1,"title":"Acme",'
                '"link":"https://www.acme.test/about","snippet":"New hiring"}]}'
            )

        with mock.patch.dict(SCRAPINGDOG.os.environ, {"SCRAPINGDOG_API_KEY": "do-not-return-this-key"}, clear=False), mock.patch.object(SCRAPINGDOG, "urlopen", side_effect=fake_urlopen):
            body, code = SCRAPINGDOG.run(
                {
                    "operation": "google_search",
                    "query": "Acme hiring",
                    "timeout_seconds": 7,
                }
            )
        self.assertEqual(code, 0)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["results"][0]["domain"], "acme.test")
        self.assertEqual(body["results"][0]["evidence_text"], "New hiring")
        self.assertEqual(body["results"][0]["provider_metadata"]["title"], "Acme")
        self.assertNotIn("do-not-return-this-key", json.dumps(body))
        self.assertIn("/google?", seen["url"])
        self.assertNotIn("do-not-return-this-key", body.get("request_url", ""))
        self.assertEqual(seen["timeout"], 7.0)

    def test_scrapingdog_operation_paths(self):
        payloads = {
            "universal_search": '{"organic_results":[]}',
            "scrape": "<html>Acme</html>",
            "linkedin_company": '{"name":"Acme"}',
            "google_jobs": '{"jobs_results":[]}',
            "linkedin_jobs": '{"jobs":[]}',
        }
        requests = {
            "universal_search": {"query": "Acme"},
            "scrape": {"url": "https://acme.test"},
            "linkedin_company": {"id": "acme"},
            "google_jobs": {"query": "Acme engineer"},
            "linkedin_jobs": {"field": "engineer"},
        }
        paths = {}

        def fake_urlopen(request, timeout):
            operation = paths["current"]
            paths[operation] = request.full_url.split("?", 1)[0]
            return FakeResponse(payloads[operation])

        with mock.patch.dict(SCRAPINGDOG.os.environ, {"SCRAPINGDOG_API_KEY": "secret"}, clear=False), mock.patch.object(SCRAPINGDOG, "urlopen", side_effect=fake_urlopen):
            for operation, extra in requests.items():
                paths["current"] = operation
                body, code = SCRAPINGDOG.run({"operation": operation, **extra})
                self.assertEqual(code, 0)
                self.assertIn(body["status"], {"ok", "no_results"})
        self.assertEqual(paths["universal_search"], "https://api.scrapingdog.com/search")
        self.assertEqual(paths["scrape"], "https://api.scrapingdog.com/scrape")
        self.assertEqual(paths["linkedin_company"], "https://api.scrapingdog.com/profile")
        self.assertEqual(paths["google_jobs"], "https://api.scrapingdog.com/google_jobs")
        self.assertEqual(paths["linkedin_jobs"], "https://api.scrapingdog.com/jobs")

    def test_scrapingdog_timeout_is_provider_outcome(self):
        with mock.patch.dict(SCRAPINGDOG.os.environ, {"SCRAPINGDOG_API_KEY": "secret"}, clear=False), mock.patch.object(SCRAPINGDOG, "urlopen", side_effect=socket.timeout()):
            body, code = SCRAPINGDOG.run(
                {"operation": "universal_search", "query": "Acme"}
            )
        self.assertEqual(code, 0)
        self.assertEqual(body["status"], "timeout")

    def test_scrapingdog_uses_fixed_host_and_normalizes_job_and_scrape_fields(self):
        seen = {}

        def fake_urlopen(request, timeout):
            seen["url"] = request.full_url
            if "/scrape?" in request.full_url:
                return FakeResponse('{"html":"Acme page"}')
            return FakeResponse('{"jobs_results":[{"company_name":"Acme",'
                '"job_url":"https://jobs.example/acme", "posted_date":"2026-01-02"}]}')

        with mock.patch.dict(SCRAPINGDOG.os.environ, {"SCRAPINGDOG_API_KEY": "secret"}, clear=False), mock.patch.object(SCRAPINGDOG, "urlopen", side_effect=fake_urlopen):
            body, code = SCRAPINGDOG.run({"operation": "scrape", "url": "https://acme.test/about", "dynamic": False})
            self.assertEqual(body["results"][0]["evidence_url"], "https://acme.test/about")
            self.assertIn("dynamic=false", seen["url"])
            body, code = SCRAPINGDOG.run({"operation": "google_jobs", "query": "Acme"})
        self.assertEqual(body["results"][0]["evidence_url"], "https://jobs.example/acme")
        self.assertEqual(body["results"][0]["evidence_date"], "2026-01-02")
        self.assertTrue(seen["url"].startswith("https://api.scrapingdog.com/google_jobs?"))

        official_shape = SCRAPINGDOG.normalize_result(
            {
                "title": "Security Engineer",
                "company_name": "Acme",
                "apply_links": [{"link": "https://jobs.example/acme-security"}],
                "extensions": ["Full-time", "2 days ago"],
            },
            "google_jobs",
        )
        self.assertEqual(official_shape["evidence_date"], "2 days ago")

        long_description = "A" * 1800 + " Job posted on August 17, 2026."
        bounded_job = SCRAPINGDOG.normalize_result(
            {"title": "VP Sales", "company_name": "Acme", "description": long_description},
            "google_jobs",
        )
        self.assertEqual(bounded_job["evidence_date"], "August 17, 2026")
        self.assertLessEqual(
            len(bounded_job["evidence_text"]), SCRAPINGDOG._MAX_EVIDENCE_TEXT_CHARS
        )
        self.assertIn("[truncated]", bounded_job["evidence_text"])

        html = (
            "<html><head><style>hidden</style>" + "x" * 5000 + "</head>"
            "<body><main><h1>Acme careers</h1><p>Hiring two sales leaders.</p>"
            "<script>secret page state</script></main></body></html>"
        )
        scrape_row = SCRAPINGDOG.normalize_result(
            SCRAPINGDOG._records(html, "scrape")[0], "scrape"
        )
        self.assertIn("Acme careers", scrape_row["evidence_text"])
        self.assertIn("Hiring two sales leaders", scrape_row["evidence_text"])
        self.assertNotIn("hidden", scrape_row["evidence_text"])
        self.assertNotIn("secret page state", scrape_row["evidence_text"])

    def test_scrapingdog_forwards_documented_filters_and_caps_bounds(self):
        seen = {}

        def fake_urlopen(request, timeout):
            seen["params"] = parse_qs(urlparse(request.full_url).query)
            seen["timeout"] = timeout
            return FakeResponse('{"organic_results":[],"next_page_token":"page-2"}')

        request = {
            "operation": "google_search",
            "query": "Acme hiring",
            "results": 500,
            "page": 2,
            "country": "us",
            "language": "en",
            "domain": "google.com",
            "advance_search": True,
            "mob_search": False,
            "timeout_seconds": 500,
        }
        with mock.patch.dict(
            SCRAPINGDOG.os.environ, {"SCRAPINGDOG_API_KEY": "secret"}, clear=False
        ), mock.patch.object(SCRAPINGDOG, "urlopen", side_effect=fake_urlopen):
            body, code = SCRAPINGDOG.run(request)
        self.assertEqual(code, 0)
        self.assertEqual(body["status"], "no_results")
        self.assertEqual(seen["params"]["results"], ["20"])
        self.assertEqual(seen["params"]["advance_search"], ["true"])
        self.assertEqual(seen["params"]["mob_search"], ["false"])
        self.assertEqual(seen["params"]["language"], ["en"])
        self.assertEqual(seen["timeout"], 60.0)
        self.assertEqual(body["continuation_cursor"], "page-2")

    def test_scrapingdog_accepts_top_level_company_list_and_rejects_unknown_schema(self):
        with mock.patch.dict(SCRAPINGDOG.os.environ, {"SCRAPINGDOG_API_KEY": "secret"}, clear=False), mock.patch.object(
            SCRAPINGDOG,
            "urlopen",
            return_value=FakeResponse(
                '[{"company_name":"Acme","id":"acme",'
                '"company_link":"https://www.linkedin.com/company/acme",'
                '"industry":"Software","company_size":"51-200"}]'
            ),
        ):
            body, code = SCRAPINGDOG.run({"operation": "linkedin_company", "id": "acme"})
        self.assertEqual(code, 0)
        self.assertEqual(body["results"][0]["company"], "Acme")
        self.assertEqual(
            body["results"][0]["evidence_url"], "https://www.linkedin.com/company/acme"
        )
        self.assertEqual(body["results"][0]["provider_metadata"]["industry"], "Software")

        with mock.patch.dict(
            SCRAPINGDOG.os.environ, {"SCRAPINGDOG_API_KEY": "secret"}, clear=False
        ), mock.patch.object(
            SCRAPINGDOG,
            "urlopen",
            return_value=FakeResponse('{"data":[{"company_name":"Nested Acme"}]}'),
        ):
            nested_body, nested_code = SCRAPINGDOG.run(
                {"operation": "linkedin_company", "id": "nested-acme"}
            )
        self.assertEqual(nested_code, 0)
        self.assertEqual(nested_body["results"][0]["company"], "Nested Acme")

        with mock.patch.dict(SCRAPINGDOG.os.environ, {"SCRAPINGDOG_API_KEY": "secret"}, clear=False), mock.patch.object(
            SCRAPINGDOG, "urlopen", return_value=FakeResponse('{"success":true,"unexpected":true}')
        ):
            body, code = SCRAPINGDOG.run({"operation": "google_search", "query": "Acme"})
        self.assertEqual(code, 0)
        self.assertEqual(body["status"], "schema_error")

    def test_scrapingdog_error_and_malformed_response_classes_are_distinct(self):
        self.assertEqual(SCRAPINGDOG._classify_status(429), "rate_limited")
        self.assertEqual(SCRAPINGDOG._classify_status(401), "auth_failed")
        self.assertEqual(SCRAPINGDOG._classify_status(402), "provider_error")
        self.assertEqual(
            SCRAPINGDOG._classify_status(402, "insufficient credits"),
            "quota_exceeded",
        )
        self.assertEqual(SCRAPINGDOG._classify_status(410, "No content"), "provider_error")

        with mock.patch.dict(
            SCRAPINGDOG.os.environ, {"SCRAPINGDOG_API_KEY": "secret"}, clear=False
        ), mock.patch.object(
            SCRAPINGDOG, "urlopen", return_value=FakeResponse("not json")
        ):
            body, code = SCRAPINGDOG.run({"operation": "google_search", "query": "Acme"})
        self.assertEqual(code, 0)
        self.assertEqual(body["status"], "schema_error")

        for status_code, expected in (
            (410, "provider_error"),
            (429, "rate_limited"),
            (503, "provider_error"),
        ):
            error = HTTPError(
                "https://api.scrapingdog.com/google",
                status_code,
                "error",
                {},
                io.BytesIO(b"provider error"),
            )
            with mock.patch.dict(
                SCRAPINGDOG.os.environ, {"SCRAPINGDOG_API_KEY": "secret"}, clear=False
            ), mock.patch.object(SCRAPINGDOG, "urlopen", side_effect=error):
                http_body, http_code = SCRAPINGDOG.run(
                    {"operation": "google_search", "query": "Acme"}
                )
            self.assertEqual(http_code, 0)
            self.assertEqual(http_body["status"], expected)

        with mock.patch.dict(
            SCRAPINGDOG.os.environ, {"SCRAPINGDOG_API_KEY": "secret"}, clear=False
        ), mock.patch.object(
            SCRAPINGDOG,
            "urlopen",
            return_value=FakeResponse('{"status":"queued"}', status=202),
        ):
            queued_body, queued_code = SCRAPINGDOG.run(
                {"operation": "google_search", "query": "Acme"}
            )
        self.assertEqual(queued_code, 0)
        self.assertEqual(queued_body["status"], "provider_error")
        self.assertEqual(queued_body["http_status"], 202)

        with mock.patch.dict(
            SCRAPINGDOG.os.environ, {"SCRAPINGDOG_API_KEY": "secret"}, clear=False
        ):
            with self.assertRaises(SCRAPINGDOG.InputError):
                SCRAPINGDOG.run(
                    {"operation": "google_search", "query": "Acme", "api_key": "inline"}
                )
            with self.assertRaises(SCRAPINGDOG.InputError):
                SCRAPINGDOG.run(
                    {
                        "operation": "google_search",
                        "query": "Acme",
                        "base_url": "https://example.test",
                    }
                )

    def test_scrapingdog_linkedin_profile_request_and_contact_normalization(self):
        seen = {}

        def fake_urlopen(request, timeout):
            seen["path"] = urlparse(request.full_url).path
            seen["params"] = parse_qs(urlparse(request.full_url).query)
            seen["timeout"] = timeout
            return FakeResponse(
                '{"fullName":"Jane Rivera", "public_identifier":"jane-rivera",'
                '"headline":"Former role", "company_name":"FormerCo",'
                '"company_url":"https://www.linkedin.com/company/formerco/", "experience":['
                '{"position":"Former VP", "company_name":"FormerCo",'
                '"ends_at":"2024-01-01"},'
                '{"position":"VP Sales", "company_name":"Acme",'
                '"company_url":"https://www.linkedin.com/company/acme/",'
                '"starts_at":"2025-01-01"}],'
                '"about":"Current role"}'
            )

        request = {
            "operation": "linkedin_profile",
            "url": "https://www.linkedin.com/in/jane-rivera/",
            "premium": True,
            "webhook": False,
            "timeout_seconds": 8,
        }
        with mock.patch.dict(
            SCRAPINGDOG.os.environ, {"SCRAPINGDOG_API_KEY": "profile-secret"}, clear=False
        ), mock.patch.object(SCRAPINGDOG, "urlopen", side_effect=fake_urlopen):
            body, code = SCRAPINGDOG.run(request)
        self.assertEqual(code, 0)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(seen["path"], "https://api.scrapingdog.com/profile".replace("https://api.scrapingdog.com", ""))
        self.assertEqual(seen["params"]["type"], ["profile"])
        self.assertEqual(seen["params"]["id"], ["jane-rivera"])
        self.assertEqual(seen["params"]["premium"], ["true"])
        self.assertEqual(seen["params"]["webhook"], ["false"])
        result = body["results"][0]
        self.assertEqual(result["contact"], "Jane Rivera")
        self.assertEqual(result["contact_title"], "VP Sales")
        self.assertEqual(result["company"], "Acme")
        self.assertIsNone(result["domain"])
        self.assertEqual(
            result["provider_metadata"]["company_url"],
            "https://www.linkedin.com/company/acme/",
        )
        self.assertIsNone(result["evidence_url"])

        former_only = SCRAPINGDOG.normalize_result(
            {
                "fullName": "Jane Rivera",
                "experience": [
                    {
                        "position": "Former VP",
                        "company_name": "FormerCo",
                        "ends_at": "2024-01-01",
                    }
                ],
            },
            "linkedin_profile",
        )
        self.assertIsNone(former_only["contact_title"])

    def test_scrapingdog_exact_job_details_and_maps_place_requests(self):
        seen = []

        def fake_urlopen(request, timeout):
            parsed = urlparse(request.full_url)
            params = parse_qs(parsed.query)
            seen.append((parsed.path, params))
            if parsed.path == "/jobs":
                return FakeResponse(
                    '[{"job_position":"Security Engineer", "company_name":"Acme",'
                    '"job_posting_time":"2 days ago",'
                    '"job_description":"Build secure systems", "job_id":"12345",'
                    '"job_apply_link":"https://www.linkedin.com/jobs/view/12345"}]'
                )
            if parsed.path == "/google_maps":
                return FakeResponse(
                    '{"search_results":[{"title":"Acme HQ", "place_id":"p-1",'
                    '"website":"https://acme.test", "address":"1 Main St"}]}'
                )
            return FakeResponse(
                '{"title":"Acme HQ", "place_id":"p-1", "address":"1 Main St",'
                '"description":"Corporate office"}'
            )

        with mock.patch.dict(
            SCRAPINGDOG.os.environ, {"SCRAPINGDOG_API_KEY": "maps-secret"}, clear=False
        ), mock.patch.object(SCRAPINGDOG, "urlopen", side_effect=fake_urlopen):
            job_body, job_code = SCRAPINGDOG.run(
                {
                    "operation": "linkedin_job_details",
                    "url": "https://www.linkedin.com/jobs/view/12345/?trk=jobs",
                }
            )
            maps_body, maps_code = SCRAPINGDOG.run(
                {
                    "operation": "google_maps",
                    "query": "Acme headquarters",
                    "ll": "@40.7,-74.0,12z",
                    "country": "us",
                }
            )
            place_body, place_code = SCRAPINGDOG.run(
                {"operation": "google_maps_place", "place_id": "p-1"}
            )
        self.assertEqual(job_code, 0)
        self.assertEqual(job_body["status"], "ok")
        self.assertEqual(job_body["results"][0]["evidence_url"], "https://www.linkedin.com/jobs/view/12345")
        self.assertEqual(job_body["results"][0]["evidence_date"], "2 days ago")
        self.assertEqual(job_body["results"][0]["evidence_text"], "Build secure systems")
        self.assertEqual(seen[0][0], "/jobs")
        self.assertEqual(seen[0][1]["job_id"], ["12345"])
        self.assertEqual(maps_code, 0)
        self.assertEqual(maps_body["results"][0]["company"], "Acme HQ")
        self.assertEqual(maps_body["results"][0]["domain"], "acme.test")
        self.assertEqual(seen[1][0], "/google_maps")
        self.assertEqual(seen[1][1]["query"], ["Acme headquarters"])
        self.assertEqual(place_code, 0)
        self.assertEqual(place_body["results"][0]["provider_metadata"]["place_id"], "p-1")
        self.assertEqual(seen[2][0], "/google_maps/places")
        self.assertEqual(seen[2][1]["place_id"], ["p-1"])

    def test_scrapingdog_new_operations_keep_status_and_redaction_boundaries(self):
        with mock.patch.dict(
            SCRAPINGDOG.os.environ, {"SCRAPINGDOG_API_KEY": "redaction-secret"}, clear=False
        ), mock.patch.object(
            SCRAPINGDOG,
            "urlopen",
            return_value=FakeResponse(
                '{"title":"Acme HQ", "description":"token=provider-secret"}'
            ),
        ):
            body, code = SCRAPINGDOG.run(
                {"operation": "google_maps_place", "data_id": "data-1"}
            )
        self.assertEqual(code, 0)
        self.assertEqual(body["status"], "ok")
        self.assertNotIn("provider-secret", json.dumps(body))
        self.assertIsNone(body["results"][0]["evidence_url"])

        with mock.patch.dict(
            SCRAPINGDOG.os.environ, {"SCRAPINGDOG_API_KEY": "maps-secret"}, clear=False
        ), self.assertRaises(SCRAPINGDOG.InputError):
            SCRAPINGDOG.validate_request(
                {"operation": "google_maps_place", "place_id": ""}
            )

    def test_scrapingdog_conditional_operations_construct_bounded_get_requests(self):
        cases = [
            ("google_ai_mode", {"query": "Acme", "location": "New York", "safe": True}, "/google/ai_mode", {"query": "Acme", "location": "New York", "safe": "true"}, {"answer": "Acme"}),
            ("google_news", {"query": "Acme", "results": 50, "limit": 3, "country": "us", "page": 2}, "/google_news", {"query": "Acme", "results": "3", "country": "us", "page": "2"}, {"news_results": []}),
            ("linkedin_post", {"id": "urn:li:activity:1"}, "/profile/post", {"id": "urn:li:activity:1"}, {"post": {"id": "p1", "text": "hello"}}),
            ("x_profile", {"profile_id": "alice"}, "/x/profile", {"profileId": "alice"}, {"profile": {"username": "alice"}}),
            ("x_post", {"tweet_id": "123"}, "/x/post", {"tweetId": "123"}, {"post": {"tweet_id": "123", "text": "hello"}}),
            ("youtube_search", {"search_query": "Acme", "country": "us", "language": "en", "sp": "CAI"}, "/youtube/search", {"search_query": "Acme", "country": "us", "language": "en", "sp": "CAI"}, {"video_results": []}),
            ("youtube_video", {"url": "https://youtu.be/abc123", "country": "us"}, "/youtube/video", {"v": "abc123", "country": "us"}, {"video": {"video_id": "abc123", "title": "Acme"}}),
            ("youtube_transcript", {"video_id": "abc123", "country": "us", "language": "en"}, "/youtube/transcripts", {"v": "abc123", "country": "us", "language": "en"}, {"transcript": "Acme transcript"}),
            ("google_ads_transparency", {"advertiser_id": "adv-1", "political_ads": False, "num": 50, "limit": 2, "platform": "SEARCH"}, "/google/ads_transparency", {"advertiser_id": "adv-1", "political_ads": "false", "num": "2", "platform": "SEARCH"}, {"ad_creatives": []}),
            ("google_patents", {"query": "Acme", "num": 50, "limit": 3, "assignee": "Acme", "before": "20250101"}, "/google_patents", {"query": "Acme", "num": "3", "assignee": "Acme", "before": "20250101"}, {"organic_results": []}),
            ("google_patent_details", {"patent_id": "US-1", "language": "en", "html": True}, "/google_patents/details", {"patent_id": "US-1", "language": "en", "html": "true"}, {"patent": {"patent_id": "US-1", "title": "Acme"}}),
            ("tiktok_profile", {"username": "acme"}, "/tiktok/profile", {"username": "acme"}, {"profile": {"username": "acme"}}),
            ("tiktok_post", {"username": "acme", "post_id": "p1"}, "/tiktok/post", {"username": "acme", "post_id": "p1"}, {"post": {"post_id": "p1", "text": "hello"}}),
            ("tiktok_ads", {"advertiser_id": "adv-1", "country": "US", "time_period": "30d"}, "/tiktok/ads", {"advertiser_id": "adv-1", "country": "US", "time_period": "30d"}, {"ads": []}),
        ]
        seen = []

        def fake_urlopen(request, timeout):
            parsed = urlparse(request.full_url)
            seen.append((parsed.path, parse_qs(parsed.query)))
            operation = cases[len(seen) - 1][0]
            return FakeResponse(json.dumps(next(item[4] for item in cases if item[0] == operation)))

        with mock.patch.dict(
            SCRAPINGDOG.os.environ, {"SCRAPINGDOG_API_KEY": "conditional-secret"}, clear=False
        ), mock.patch.object(SCRAPINGDOG, "urlopen", side_effect=fake_urlopen):
            for operation, request, expected_path, expected_params, _ in cases:
                body, code = SCRAPINGDOG.run({"operation": operation, **request})
                self.assertEqual(code, 0)
                self.assertIn(body["status"], {"ok", "no_results"})
                self.assertEqual(body["operation"], operation)

        self.assertEqual(len(seen), len(cases))
        for (operation, _, expected_path, expected_params, _), (path, params) in zip(cases, seen):
            with self.subTest(operation=operation):
                self.assertEqual(path, expected_path)
                for key, expected in expected_params.items():
                    self.assertEqual(params[key], [str(expected)])

    def test_scrapingdog_conditional_normalization_and_empty_envelopes_are_conservative(self):
        fixtures = [
            ("google_ai_mode", {"answer": "Acme expanded", "source_url": "https://news.test/a"}, "ai_mode_result", "Acme expanded", "https://news.test/a"),
            ("google_news", {"title": "Acme funding", "link": "https://news.test/a", "lastUpdated": "2026-01-02"}, "news_result", "Acme funding", "https://news.test/a"),
            ("linkedin_post", {"id": "p1", "text": "Acme launch", "url": "https://www.linkedin.com/posts/acme_p1"}, "linkedin_post", "Acme launch", "https://www.linkedin.com/posts/acme_p1"),
            ("x_profile", {"profile_id": "alice", "handle": "alice", "description": "Acme operator", "url": "https://x.com/alice"}, "x_profile", "Acme operator", "https://x.com/alice"),
            ("x_post", {"tweet_id": "t1", "text": "Acme launch", "url": "https://x.com/acme/status/t1"}, "x_post", "Acme launch", "https://x.com/acme/status/t1"),
            ("youtube_search", {"video_id": "v1", "title": "Acme demo", "channel": {"name": "Acme"}, "link": "https://youtube.com/watch?v=v1"}, "youtube_search", "Acme demo", "https://youtube.com/watch?v=v1"),
            ("youtube_video", {"video_id": "v1", "title": "Acme demo", "description": "Details"}, "youtube_video", "Acme demo", None),
            ("youtube_transcript", {"video_id": "v1", "transcript": "Acme transcript"}, "youtube_transcript", "Acme transcript", None),
            ("google_ads_transparency", {"advertiser_name": "Acme", "description": "Acme ad", "creative_url": "https://ads.test/a"}, "ads_transparency", "Acme ad", "https://ads.test/a"),
            ("google_patents", {"publication_number": "US-1", "title": "Acme patent", "assignee": "Acme"}, "patent_result", "Acme patent", None),
            ("google_patent_details", {"patent_id": "US-1", "title": "Acme patent", "publication_url": "https://patents.test/US-1"}, "patent_detail", "Acme patent", "https://patents.test/US-1"),
            ("tiktok_profile", {"username": "acme", "bio": "Acme operator", "profile_url": "https://tiktok.com/@acme"}, "tiktok_profile", "Acme operator", "https://tiktok.com/@acme"),
            ("tiktok_post", {"post_id": "p1", "description": "Acme launch", "canonical_url": "https://tiktok.com/@acme/video/p1"}, "tiktok_post", "Acme launch", "https://tiktok.com/@acme/video/p1"),
            ("tiktok_ads", {"advertiser_name": "Acme", "text": "Acme ad"}, "tiktok_ads", "Acme ad", None),
        ]
        for operation, row, signal, evidence_text, evidence_url in fixtures:
            with self.subTest(operation=operation):
                result = SCRAPINGDOG.normalize_result(row, operation)
                self.assertEqual(result["signal"], signal)
                self.assertEqual(result["evidence_text"], evidence_text)
                self.assertEqual(result["evidence_url"], evidence_url)

        empty_envelopes = {
            "google_ai_mode": {"results": []},
            "google_news": {"news_results": []},
            "linkedin_post": {"posts": []},
            "x_profile": {"profiles": []},
            "x_post": {"posts": []},
            "youtube_search": {"video_results": []},
            "youtube_video": {"video": {}},
            "youtube_transcript": {"transcript": ""},
            "google_ads_transparency": {"ad_creatives": []},
            "google_patents": {"organic_results": []},
            "google_patent_details": {"patent": {}},
            "tiktok_profile": {"profile": {}},
            "tiktok_post": {"post": {}},
            "tiktok_ads": {"ads": []},
        }
        for operation, payload in empty_envelopes.items():
            with self.subTest(empty_operation=operation):
                self.assertTrue(SCRAPINGDOG._known_schema(payload, operation))
                self.assertEqual(SCRAPINGDOG._records(payload, operation), [])

        ai_payload = {
            "text_blocks": [{"type": "paragraph", "snippet": "Acme expanded", "links": [{"link": "https://acme.test/news"}]}],
            "references": [],
            "local_results": [],
        }
        self.assertTrue(SCRAPINGDOG._known_schema(ai_payload, "google_ai_mode"))
        ai_row = SCRAPINGDOG.normalize_result(SCRAPINGDOG._records(ai_payload, "google_ai_mode")[0], "google_ai_mode")
        self.assertEqual(ai_row["evidence_text"], "Acme expanded")
        self.assertEqual(ai_row["evidence_url"], "https://acme.test/news")

        linkedin_company = SCRAPINGDOG.normalize_result(
            {
                "name": "Acme",
                "domain": "https://www.linkedin.com/company/acme",
                "website": "https://acme.test",
                "url": "https://www.linkedin.com/company/acme",
            },
            "linkedin_company",
        )
        self.assertEqual(linkedin_company["domain"], "acme.test")

        youtube_search_payload = {
            "channel_results": [{"title": "Acme channel"}],
            "video_results": [{"video_id": "v1", "title": "Acme demo"}],
            "shorts_results": [
                {
                    "shorts": [
                        {
                            "video_id": "v2",
                            "title": "Acme short",
                            "link": "https://www.youtube.com/shorts/v2",
                        }
                    ],
                    "position": 2,
                }
            ],
            "movie_results": [],
            "pagination": {"next_page_token": "youtube-next"},
        }
        self.assertTrue(
            SCRAPINGDOG._known_schema(youtube_search_payload, "youtube_search")
        )
        self.assertEqual(
            len(SCRAPINGDOG._records(youtube_search_payload, "youtube_search")), 3
        )
        self.assertEqual(
            SCRAPINGDOG._continuation_cursor(youtube_search_payload), "youtube-next"
        )
        self.assertEqual(
            SCRAPINGDOG._continuation_cursor(
                {"scrapingdog_pagination": {"next_page_token": "ads-next"}}
            ),
            "ads-next",
        )

        google_ad = SCRAPINGDOG.normalize_result(
            {
                "advertiser_id": "AR1",
                "advertiser": "Acme",
                "ad_creative_id": "CR1",
                "format": "image",
                "link": "https://ads.test/creative",
                "first_shown": 1704067200,
                "last_shown": 1706745600,
            },
            "google_ads_transparency",
        )
        self.assertEqual(google_ad["company"], "Acme")
        self.assertEqual(google_ad["evidence_url"], "https://ads.test/creative")
        self.assertEqual(google_ad["evidence_date"], "2024-02-01")
        self.assertEqual(google_ad["provider_metadata"]["ad_id"], "CR1")
        self.assertEqual(google_ad["provider_metadata"]["ad_format"], "image")
        self.assertNotIn("linkedin_id", google_ad["provider_metadata"])

        tiktok_ad = SCRAPINGDOG.normalize_result(
            {
                "id": "ad-1",
                "name": "Acme Summer Sale",
                "type": "video",
                "first_shown_date": "2024-06-01",
                "last_shown_date": "2024-06-30",
                "videos": [{"video_url": "https://video.test/acme.mp4"}],
                "estimated_audience": "1M - 5M",
                "spent": "10K - 50K",
                "impression": "5M - 10M",
            },
            "tiktok_ads",
        )
        self.assertEqual(tiktok_ad["evidence_text"], "Acme Summer Sale")
        self.assertEqual(tiktok_ad["evidence_url"], "https://video.test/acme.mp4")
        self.assertEqual(tiktok_ad["evidence_date"], "2024-06-30")
        self.assertEqual(tiktok_ad["provider_metadata"]["ad_id"], "ad-1")
        self.assertEqual(tiktok_ad["provider_metadata"]["ad_format"], "video")
        self.assertNotIn("linkedin_id", tiktok_ad["provider_metadata"])

        tiktok_post = SCRAPINGDOG.normalize_result(
            {
                "id": "post-1",
                "canonical_url": "https://www.tiktok.com/@acme/video/post-1",
                "description": "Acme launch",
                "created_at": 1704067200,
            },
            "tiktok_post",
        )
        self.assertEqual(tiktok_post["evidence_date"], "2024-01-01")
        self.assertEqual(tiktok_post["provider_metadata"]["post_id"], "post-1")

        youtube_payload = {
            "video": {"id": "v1", "title": "Acme demo", "description": "Details"},
            "channel": {"name": "Acme", "link": "https://youtube.com/@acme"},
        }
        youtube_row = SCRAPINGDOG.normalize_result(
            SCRAPINGDOG._records(youtube_payload, "youtube_video")[0], "youtube_video"
        )
        self.assertEqual(youtube_row["company"], "Acme")
        self.assertEqual(youtube_row["provider_metadata"]["video_id"], "v1")

        missing = SCRAPINGDOG.normalize_result({"answer": "No source attached"}, "google_ai_mode")
        self.assertIsNone(missing["evidence_url"])
        redacted = SCRAPINGDOG.normalize_result({"id": "p1", "text": "token=provider-secret"}, "linkedin_post")
        self.assertNotIn("provider-secret", json.dumps(redacted))

    def test_scrapingdog_conditional_validation_rejects_unsafe_or_ambiguous_inputs(self):
        with mock.patch.dict(
            SCRAPINGDOG.os.environ, {"SCRAPINGDOG_API_KEY": "conditional-secret"}, clear=False
        ):
            invalid_requests = [
                {"operation": "google_ai_mode", "query": "Acme", "uule": "x", "location": "New York"},
                {"operation": "google_ads_transparency", "text": "Acme", "political_ads": True},
                {"operation": "google_ads_transparency", "text": "Acme", "platform": "GOOGLE"},
                {"operation": "youtube_video", "url": "https://example.test/video"},
                {"operation": "tiktok_post", "username": "acme"},
                {"operation": "tiktok_ads", "query": "Acme", "query_type": 3},
                {"operation": "tiktok_ads", "query": "Acme", "query_type": 2},
                {"operation": "tiktok_ads", "advertiser_id": "adv-1", "query_type": 1},
                {"operation": "instagram_profile", "username": "acme"},
            ]
            for request in invalid_requests:
                with self.subTest(operation=request["operation"]), self.assertRaises(SCRAPINGDOG.InputError):
                    SCRAPINGDOG.validate_request(request)

            ads_request = SCRAPINGDOG.validate_request(
                {"operation": "google_ads_transparency", "text": "Acme", "limit": 3}
            )
            _, ads_params = SCRAPINGDOG._params(ads_request)
            self.assertEqual(ads_params["num"], 3)

            tiktok_ads_request = SCRAPINGDOG.validate_request(
                {"operation": "tiktok_ads", "advertiser_id": "adv-1"}
            )
            _, tiktok_ads_params = SCRAPINGDOG._params(tiktok_ads_request)
            self.assertEqual(tiktok_ads_params["query_type"], 2)


if __name__ == "__main__":
    unittest.main()
