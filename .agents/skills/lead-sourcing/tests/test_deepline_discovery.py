from __future__ import annotations

import importlib.util
import io
import json
import pathlib
import sys
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("deepline_discovery", ROOT / "scripts" / "deepline.py")
assert SPEC and SPEC.loader
DEEPLINE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DEEPLINE)


def resource(kind, resource_id, attributes, relationships=None):
    value = {"type": kind, "id": resource_id, "attributes": attributes}
    if relationships is not None:
        value["relationships"] = relationships
    return value


def relation(kind, resource_id):
    return {"data": {"type": kind, "id": resource_id}}


def news_event(company_relationships=None):
    relationships = dict(company_relationships or {})
    relationships["most_relevant_source"] = relation("news_article", "article-1")
    return resource(
        "news_event",
        "event-1",
        {
            "category": "acquires",
            "article_sentence": "Acme acquired Beta.",
            "summary": "Transaction announced.",
            "effective_date": "2026-08-31",
            "found_at": "2026-09-03T20:00:00Z",
            "planning": True,
        },
        relationships,
    )


class DeeplineDiscoveryTests(unittest.TestCase):
    def test_jsonapi_news_resolves_one_company_and_linked_source(self):
        envelope = {
            "data": [news_event({"company1": relation("company", "acme")})],
            "included": [
                resource("company", "acme", {"company_name": "Acme", "domain": "acme.test"}),
                resource(
                    "news_article",
                    "article-1",
                    {
                        "url": "https://news.test/acme-beta",
                        "published_at": "2026-09-01",
                        "title": "Acme buys Beta",
                        "author": "Reporter",
                        "body": "Full article.",
                    },
                ),
            ],
            "meta": {"request_id": "safe", "api_key": "secret"},
            "links": {"self": "https://api.test/events?token=secret"},
        }

        body = DEEPLINE._execute_output(envelope, "predictleads_news")
        row = body["results"][0]
        self.assertEqual(body["status"], "ok")
        self.assertEqual(row["company"], "Acme")
        self.assertEqual(row["domain"], "acme.test")
        self.assertEqual(row["signal"], "acquires")
        self.assertEqual(row["evidence_url"], "https://news.test/acme-beta")
        self.assertEqual(row["evidence_date"], "2026-09-01")
        self.assertEqual(row["event_date"], "2026-08-31")
        self.assertEqual(row["evidence_text"], "Acme acquired Beta.")
        self.assertEqual(row["attributes"]["found_at"], "2026-09-03T20:00:00Z")
        self.assertNotEqual(row["evidence_date"], row["attributes"]["found_at"])
        self.assertEqual(body["meta"]["api_key"], "[REDACTED]")
        self.assertIn("token=[REDACTED]", body["links"]["self"])

    def test_nested_string_raw_v2_beats_extracted_lists_and_keeps_two_companies_ambiguous(self):
        envelope = {
            "data": [
                news_event(
                    {
                        "company1": relation("company", "acme"),
                        "company2": relation("company", "beta"),
                    }
                )
            ],
            "included": [
                resource("company", "acme", {"company_name": "Acme", "domain": "acme.test"}),
                resource("company", "beta", {"company_name": "Beta", "domain": "beta.test"}),
                resource("news_article", "article-1", {"url": "https://news.test/deal", "published_at": "2026-09-01"}),
            ],
        }
        parsed = {
            "status": "completed",
            "extractedLists": {"results": [{"company_name": "Wrong"}]},
            "toolResponse": {"rawV2": json.dumps(envelope)},
        }

        body = DEEPLINE._execute_output(parsed, "predictleads_news")
        row = body["results"][0]
        self.assertEqual(row["id"], "event-1")
        self.assertIsNone(row["company"])
        self.assertIsNone(row["domain"])
        self.assertEqual(
            [(item["relationship"], item["company"]) for item in row["related_companies"]],
            [("company1", "Acme"), ("company2", "Beta")],
        )

    def test_recognized_result_nesting_and_legacy_raw_fallback_keep_included(self):
        envelope = {
            "data": [news_event({"company1": relation("company", "acme")})],
            "included": [
                resource("company", "acme", {"company_name": "Acme", "domain": "acme.test"}),
                resource("news_article", "article-1", {"url": "https://news.test/1", "published_at": "2026-09-01"}),
            ],
        }
        for parsed in (
            {"toolResponse": {"rawV2": {"result": envelope}}},
            {"toolResponse": {"rawV2": {"unexpected": True}, "raw": envelope}},
        ):
            with self.subTest(parsed=parsed):
                row = DEEPLINE._execute_output(parsed, "predictleads_news")["results"][0]
                self.assertEqual(row["company"], "Acme")
                self.assertEqual(row["evidence_url"], "https://news.test/1")

    def test_single_resource_company2_and_generic_attributes_stay_structured(self):
        job = resource(
            "job_opening",
            "job-1",
            {
                "url": "https://jobs.test/1",
                "title": "VP Sales",
                "job_title": "VP Sales",
                "description": "A current opening.",
            },
            {"company2": relation("company", "acme")},
        )
        body = DEEPLINE._execute_output(
            {"data": job, "included": [resource("company", "acme", {"company_name": "Acme", "domain": "acme.test"})]},
            "predictleads_jobs",
            "buying_signal",
        )
        row = body["results"][0]
        self.assertEqual(row["company"], "Acme")
        self.assertEqual(row["evidence_url"], "https://jobs.test/1")
        self.assertEqual(row["evidence_text"], "A current opening.")
        self.assertEqual(row["attributes"]["job_title"], "VP Sales")
        self.assertEqual(row["entity_type"], "buying_signal")
        self.assertNotIn("contact", row)
        self.assertNotIn("current_title", row)

    def test_missing_and_conflicting_company_included_rows_fail_closed(self):
        event = news_event({"company1": relation("company", "acme")})
        cases = {
            "missing": [],
            "conflicting": [
                resource("company", "acme", {"company_name": "Acme", "domain": "acme.test"}),
                resource("company", "acme", {"company_name": "Other", "domain": "other.test"}),
            ],
        }
        for name, included in cases.items():
            with self.subTest(name=name):
                row = DEEPLINE._execute_output(
                    {"data": [event], "included": included}, "predictleads_news"
                )["results"][0]
                self.assertIsNone(row["company"])
                self.assertIsNone(row["domain"])
                self.assertEqual(row["related_companies"][0]["id"], "acme")
                self.assertIsNone(row["related_companies"][0]["company"])
                self.assertEqual(row["relationships"]["company1"]["data"]["id"], "acme")

        malformed = news_event({
            "company1": relation("company", "acme"),
            "company2": {"data": {"type": "company"}},
        })
        row = DEEPLINE._execute_output(
            {
                "data": [malformed],
                "included": [resource("company", "acme", {"company_name": "Acme", "domain": "acme.test"})],
            },
            "predictleads_news",
        )["results"][0]
        self.assertIsNone(row["company"])
        self.assertEqual(row["relationships"]["company2"]["data"], {"type": "company"})

    def test_harvest_posts_do_not_infer_people_or_reposts_as_contacts(self):
        person_post = {
            "id": "post-1",
            "linkedinUrl": "https://www.linkedin.com/posts/person-1",
            "content": "We are expanding.",
            "author": {"id": "person-1", "name": "Jane Doe", "linkedinUrl": "https://www.linkedin.com/in/jane-doe"},
            "postedAt": {"postedAgoText": "2d", "timestamp": 1788400000},
            "repost": {"content": "Original content", "author": {"name": "Other Person"}},
            "repostedBy": {"name": "Jane Doe"},
        }
        company_post = {
            "id": "post-2",
            "linkedinUrl": "https://www.linkedin.com/posts/acme-2",
            "content": "New facility opened.",
            "author": {"id": "acme", "name": "Acme", "linkedinUrl": "https://www.linkedin.com/company/acme"},
            "postedAt": {"date": "2026-09-02", "timestamp": 1788400000, "postedAgoText": "2d"},
        }
        sparse_post = {
            "id": "post-3",
            "linkedinUrl": "https://www.linkedin.com/feed/update/urn:li:activity:3",
            "content": "Sparse but recognizable post.",
        }
        body = DEEPLINE._execute_output(
            {"elements": [person_post, company_post, sparse_post]}, "harvestapi_posts"
        )
        person, company, sparse = body["results"]
        self.assertIsNone(person["company"])
        self.assertIsNone(person["domain"])
        self.assertIsNone(person["evidence_date"])
        self.assertNotIn("contact", person)
        self.assertNotIn("full_name", person)
        self.assertEqual(person["repost"], person_post["repost"])
        self.assertEqual(company["company"], "Acme")
        self.assertIsNone(company["domain"])
        self.assertEqual(company["company_linkedin_url"], "https://www.linkedin.com/company/acme")
        self.assertEqual(company["evidence_date"], "2026-09-02")
        self.assertIsNone(sparse["company"])
        self.assertIsNone(sparse["evidence_date"])
        self.assertNotIn("contact", sparse)
        self.assertTrue(all(row["entity_type"] == "signal" for row in body["results"]))

    def test_harvest_cursor_survives_main_redaction_but_raw_token_does_not(self):
        envelope = {
            "elements": [],
            "pagination": {
                "pageNumber": 1,
                "pageSize": 10,
                "paginationToken": "opaque-next-page",
                "totalElements": 20,
            },
        }
        body = DEEPLINE._execute_output(envelope, "harvestapi_posts")
        with mock.patch.object(DEEPLINE, "run", return_value=(body, 0)), mock.patch(
            "sys.stdout", new_callable=io.StringIO
        ) as stdout:
            code = DEEPLINE.main(["--input", '{"operation":"search","query":"x"}'])
        emitted = json.loads(stdout.getvalue())
        pagination = emitted["pagination"]
        self.assertEqual(code, 0)
        self.assertEqual(pagination["paginationToken"], "[REDACTED]")
        self.assertEqual(pagination["next_cursor"], "opaque-next-page")

    def test_selected_provider_envelope_failures_override_empty_results(self):
        harvest = {
            "status": 429,
            "error": "429 rate limit",
            "elements": [],
            "pagination": {"pageNumber": 1},
        }
        body = DEEPLINE._execute_output(
            {"toolResponse": {"rawV2": harvest}}, "harvestapi_posts"
        )
        self.assertEqual(body["status"], "rate_limited")
        self.assertIn("429", body["error"]["message"])
        self.assertEqual(body["results"], [])

        jsonapi = {"status": "partial", "data": [], "included": [], "errors": [{"detail": "quota exceeded"}]}
        body = DEEPLINE._execute_output(jsonapi, "predictleads_news")
        self.assertEqual(body["status"], "quota_exceeded")
        self.assertIn("quota exceeded", body["error"]["message"])
        self.assertEqual(body["results"], [])

        malformed_status = {"status": {"code": 500}, "data": [], "included": []}
        body = DEEPLINE._execute_output(malformed_status, "predictleads_news")
        self.assertEqual(body["status"], "schema_error")

    def test_structured_parsing_is_execute_only_and_outer_partial_wins(self):
        envelope = {"data": [news_event()], "included": []}
        catalog = DEEPLINE._catalog_output("search", envelope)
        self.assertNotIn("related_companies", json.dumps(catalog))

        body = DEEPLINE._execute_output(
            {"status": "partial", "toolResponse": {"rawV2": {**envelope, "errors": [{"detail": "quota exceeded"}]}}},
            "predictleads_news",
        )
        self.assertEqual(body["status"], "quota_exceeded")

        body = DEEPLINE._execute_output(
            {"status": "partial", "toolResponse": {"rawV2": envelope}},
            "predictleads_news",
        )
        self.assertEqual(body["status"], "partial")


if __name__ == "__main__":
    unittest.main()
