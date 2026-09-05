import unittest

from test_provider_scripts import DEEPLINE


class NestedContactTests(unittest.TestCase):
    def test_nested_contact_enrichment_preserves_fields_without_inventing_role(self):
        for name, domain, email in (
            ("Alex Example", "alpha.test", "alex@alpha.test"),
            ("Sam Sample", "beta.test", "sam@beta.test"),
        ):
            with self.subTest(domain=domain):
                contact = {"name": name, "domain": domain, "email": email}
                payload = {"toolResponse": {"rawV2": {"contact": contact}}}
                result = DEEPLINE._execute_output(payload, "enrichment")
                row = result["results"][0]
                self.assertEqual(result["status"], "ok")
                self.assertEqual(row["full_name"], name)
                self.assertEqual(row["contact_email"], email)
                self.assertEqual(row["domain"], domain)
                self.assertEqual(row["contact_details"], contact)
                self.assertIsNone(row["current_title"])
                self.assertIsNone(row["company"])
                self.assertIs(payload["toolResponse"]["rawV2"]["contact"], contact)

    def test_nested_fields_do_not_replace_outer_fields_or_company_records(self):
        payload = {"domain": "outer.test", "contact_email": "outer@outer.test",
                   "contact": {"name": "Alex Example", "domain": "inner.test", "email": "inner@inner.test"}}
        row = DEEPLINE.normalize_evidence(payload, "deepline", "enrichment")
        self.assertEqual(row["domain"], "outer.test")
        self.assertEqual(row["contact_email"], "outer@outer.test")
        company = DEEPLINE.normalize_evidence({"company": "Example", "contact": payload["contact"]}, "deepline", "enrichment", "company")
        self.assertNotIn("contact_email", company)
        self.assertIsNone(company["domain"])

    def test_failed_enrichment_remains_failed(self):
        result = DEEPLINE._execute_output({"status": "provider_error", "contact": {"name": "Alex Example", "email": "alex@alpha.test"}}, "enrichment")
        self.assertEqual(result["status"], "provider_error")


if __name__ == "__main__":
    unittest.main()
