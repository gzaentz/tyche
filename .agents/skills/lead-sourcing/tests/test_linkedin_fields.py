from __future__ import annotations

import copy
import json
import unittest

import test_deepline_discovery as provider
import test_export_xlsx as xlsx
from test_client_output import client_document, VALIDATOR


class HarvestLinkedInNormalizationTests(unittest.TestCase):
    def normalize(self, element, kind):
        body = provider.DEEPLINE._execute_output(
            {"element": element, "status": "ok"}, f"harvestapi_get_{kind}",
            "company" if kind == "company" else "contact",
        )
        self.assertEqual(body["status"], "ok")
        self.assertEqual(len(body["results"]), 1)
        return body["results"][0]

    def test_profile_location_uses_person_fields_and_preserves_raw_location(self):
        location = {"linkedinText": "Columbus, Ohio, United States", "countryCode": "US",
                    "parsed": {"countryFull": "United States", "country": "US", "state": "Ohio", "city": "Columbus"}}
        row = self.normalize({"firstName": "Ada", "lastName": "Example",
                              "linkedinUrl": "https://linkedin.com/in/ada-example", "location": location,
                              "headquarter": {"country": "Canada", "city": "Toronto"}}, "profile")
        self.assertEqual([row[k] for k in ("country", "state", "city")], ["United States", "Ohio", "Columbus"])
        self.assertEqual(row["location"], location)
        self.assertEqual(row["location_text"], location["linkedinText"])

    def test_country_code_only_does_not_invent_city_or_use_job_location(self):
        row = self.normalize({"firstName": "Ada", "linkedinUrl": "https://linkedin.com/in/ada-example",
                              "location": {"linkedinText": "United Kingdom", "countryCode": "GB"},
                              "currentPositions": [{"location": "London", "current": True}]}, "profile")
        self.assertEqual(row["country"], "GB")
        self.assertIsNone(row["city"])
        self.assertIsNone(row["state"])

    def test_company_range_is_not_associated_member_count(self):
        company = {"name": "Example", "website": "https://example.com", "linkedinUrl": "https://linkedin.com/company/example",
                   "employeeCount": 9999, "employeeCountRange": {"start": 11, "end": 50}}
        row = self.normalize(company, "company")
        self.assertEqual(row["employee_range"], "11-50")
        self.assertEqual(row["employeeCount"], 9999)
        self.assertNotIn("contact", row)
        company["employeeCountRange"] = {"start": 10001}
        self.assertEqual(self.normalize(company, "company")["employee_range"], "10001+")
        for band in (None, {}, {"start": 11}, {"start": 50, "end": 11}, {"start": True, "end": 50}):
            company["employeeCountRange"] = band
            self.assertNotIn("employee_range", self.normalize(company, "company"))


class LinkedInFieldContractTests(unittest.TestCase):
    setUpClass = classmethod(xlsx.ExportXlsxTests.setUpClass.__func__)
    run_rows_json = xlsx.ExportXlsxTests.run_rows_json
    def check_fields(self, document, valid, diagnostic=None):
        errors = VALIDATOR.linkedin_field_errors(document)
        result = self.run_rows_json(document)
        self.assertEqual(not errors, valid, errors)
        self.assertEqual(result.returncode == 0, valid, result.stderr)
        if diagnostic:
            self.assertIn(diagnostic, " ".join(errors))
        if valid:
            return json.loads(result.stdout)["rows"][0]

    def test_country_required_but_city_state_optional_after_email_opt_out(self):
        document = client_document()
        document["request"]["contact_fields"] = []
        contact = document["accepted"][0]["primary_contact"]
        contact.pop("city")
        contact.pop("state")
        row = self.check_fields(document, True)
        self.assertEqual(row["Contact City"], "")
        self.assertEqual(row["Contact State"], "")
        self.assertEqual(row["Contact Country"], "United States")
        for value in (None, "", " ", "Unknown", "Remote", 42):
            contact["country"] = value
            self.check_fields(document, False, ".country")

    def test_range_required_and_exact_count_is_never_a_fallback(self):
        document = client_document()
        company = document["accepted"][0]["company"]
        company["employee_count"] = 999999
        self.assertEqual(self.check_fields(document, True)["Company Employee Range"], "201-500")
        for value in (None, "", 240, "240", "500-201", "unknown"):
            company["employee_range"] = value
            self.check_fields(document, False, "employee_range")
        company["employee_range"] = "1,001–5,000"
        self.assertEqual(self.check_fields(document, True)["Company Employee Range"], "1001-5000")

    def test_field_evidence_must_match_harvest_getter_and_entity(self):
        for owner, field in (("company", "employee_range_evidence"), ("primary_contact", "location_evidence")):
            for mutation in ("missing", "wrong_entity", "other_provider", "failed_route", "search_route", "missing_date"):
                with self.subTest(owner=owner, mutation=mutation):
                    document = client_document()
                    entity = document["accepted"][0][owner]
                    evidence = entity[field]
                    route = next(r for r in document["routes"] if r["route_id"] == evidence["source"]["route_id"])
                    if mutation == "missing": entity.pop(field)
                    elif mutation == "wrong_entity": evidence["evidence_url"] += "-other"
                    elif mutation == "other_provider": evidence["source"]["provider"] = "public_web"
                    elif mutation == "failed_route": route["provider_status"] = "timeout"
                    elif mutation == "missing_date": evidence.pop("evidence_date")
                    else: route["tool"] = evidence["source"]["tool"] = "harvestapi_search_posts"
                    self.check_fields(document, False, field)

    def test_backup_country_gate_and_sources_export(self):
        document = client_document()
        contact = document["accepted"][0]["primary_contact"]
        backup = copy.deepcopy(contact)
        backup.pop("country")
        document["accepted"][0]["backup_contacts"] = [backup]
        self.check_fields(document, False, "backup_contacts[0].country")

    def test_company_size_uses_entire_range_and_ignores_exact_count(self):
        document = client_document()
        document["request"]["icp"] = {"company_size": {"min_employees": 11, "max_employees": 200}}
        company = document["accepted"][0]["company"]
        company["employee_count"] = 99
        for band, passes in (("11-50", True), ("51-200", True), ("201-500", False), ("1-50", False), ("10001+", False)):
            company["employee_range"] = band
            self.assertEqual(not VALIDATOR.qualification_errors(document), passes, band)


if __name__ == "__main__":
    unittest.main()
