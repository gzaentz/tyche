import json
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlparse

from test_provider_scripts import SCRAPINGDOG, FakeResponse


class ScrapingDogCoverageTests(unittest.TestCase):
    def test_discovery_routes_keep_filters_and_nonempty_evidence(self):
        cases = [
            ("google_local", {"query": "housing", "location": "Limburg", "country": "nl", "page": 2}, "/google_local",
             {"query": "housing", "location": "Limburg", "country": "nl", "page": "2"},
             {"local_results": [{"title": "Example Housing", "website": "https://example.com", "description": "New campus"}]}, "local_result"),
            ("universal_search", {"query": "housing", "country": "nl", "language": "nl"}, "/search",
             {"query": "housing", "country": "nl", "language": "nl"},
             {"organic_results": [{"company": "Example Housing", "link": "https://example.com", "snippet": "New campus"}]}, "search_result"),
            ("linkedin_jobs", {"query": "engineer", "location": "Boston", "page": 2, "work_type": "2", "filter_by_company": "123"}, "/jobs",
             {"field": "engineer", "location": "Boston", "page": "2", "work_type": "2", "filter_by_company": "123"},
             {"jobs": [{"company_name": "Example Housing", "link": "https://example.com", "description": "New campus"}]}, "hiring"),
        ]
        for operation, request, endpoint, params, response, signal in cases:
            with self.subTest(operation=operation):
                with mock.patch.dict(SCRAPINGDOG.os.environ, {"SCRAPINGDOG_API_KEY": "private-value"}), mock.patch.object(SCRAPINGDOG, "urlopen", return_value=FakeResponse(json.dumps(response))) as call:
                    body, code = SCRAPINGDOG.run({"operation": operation, **request, "limit": 1})
                parsed = urlparse(call.call_args.args[0].full_url)
                self.assertEqual(parsed.path, endpoint)
                query = parse_qs(parsed.query)
                for key, value in params.items():
                    self.assertEqual(query[key], [value])
                self.assertEqual(code, 0)
                self.assertEqual(body["status"], "ok")
                self.assertEqual(len(body["results"]), 1)
                row = body["results"][0]
                self.assertEqual(row["company"], "Example Housing")
                self.assertEqual(row["domain"], "example.com")
                self.assertEqual(row["evidence_text"], "New campus")
                self.assertEqual(row["signal"], signal)
                self.assertNotIn("private-value", json.dumps(body))
                self.assertEqual(call.call_count, 1)

    def test_every_alias_matches_its_canonical_request_and_result(self):
        inputs = {
            "linkedin_person": {"id": "ada-example"},
            "linkedin_job": {"job_id": "123"},
            "google_maps": {"query": "housing"},
            "google_maps_place": {"place_id": "place-123"},
            "youtube_transcript": {"video_id": "video-123"},
        }
        row = {"name": "Example", "title": "Example", "description": "Evidence", "text": "Evidence", "url": "https://example.com"}
        with mock.patch.dict(SCRAPINGDOG.os.environ, {"SCRAPINGDOG_API_KEY": "private-value"}):
            for alias, canonical in SCRAPINGDOG.OPERATION_ALIASES.items():
                with self.subTest(alias=alias):
                    a = SCRAPINGDOG.validate_request({"operation": alias, **inputs[canonical]})
                    c = SCRAPINGDOG.validate_request({"operation": canonical, **inputs[canonical]})
                    self.assertEqual(SCRAPINGDOG._params(a), SCRAPINGDOG._params(c))
                    actual = SCRAPINGDOG.normalize_result(row, alias)
                    expected = SCRAPINGDOG.normalize_result(row, canonical)
                    for field in ("company", "domain", "evidence_text", "evidence_url", "signal"):
                        self.assertEqual(actual[field], expected[field])
