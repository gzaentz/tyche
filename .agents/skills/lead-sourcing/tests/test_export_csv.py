from __future__ import annotations

import csv
import importlib.util
import json
import pathlib
import re
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
EXPORTER_PATH = ROOT / "scripts" / "export_csv.py"
CONTRACT_PATH = ROOT / "references" / "output-contract.md"

SPEC = importlib.util.spec_from_file_location("tyche_export_csv", EXPORTER_PATH)
assert SPEC and SPEC.loader
EXPORTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORTER)


def accepted_document() -> dict:
    return {
        "accepted": [
            {
                "company": {
                    "canonical_name": "Example Products, Inc.",
                    "domain": "example.com",
                    "website": "https://www.example.com/products",
                    "linkedin_url": "https://www.linkedin.com/company/example-products",
                    "industry": "Manufacturing",
                    "sub_industry": "Consumer products",
                    "hq_state": "Ohio",
                    "hq_country": "United States",
                    "employee_count": 240,
                    "description": "Makes packaged goods, tools, and accessories.",
                },
                "signal_evidence": {
                    "signal": "warehouse_system_integration",
                    "evidence_date": "2026-08-12",
                    "evidence_text": "The company connected its acquired warehouse to one WMS.\nThe project covers inventory visibility.",
                    "evidence_url": "https://example.com/news/wms-project",
                },
                "primary_contact": {
                    "full_name": "Ada Example",
                    "email": "ada@example.com",
                    "current_title": "Director of Supply Chain",
                    "contact_url": "https://example.com/team/ada",
                    "linkedin_url": "https://www.linkedin.com/in/ada-example",
                    "city": "Columbus",
                    "state": "Ohio",
                    "country": "United States",
                    "phone": "+1 555 010 0200",
                },
            }
        ]
    }


class ExportCsvTests(unittest.TestCase):
    def export(self, document: dict) -> tuple[list[str], list[dict[str, str]], str]:
        with tempfile.TemporaryDirectory() as directory:
            destination = pathlib.Path(directory) / "leads.csv"
            count = EXPORTER.export_csv(document, destination)
            raw = destination.read_text(encoding="utf-8")
            with destination.open(encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                rows = list(reader)
                fieldnames = list(reader.fieldnames or [])
        self.assertEqual(count, len(rows))
        return fieldnames, rows, raw

    def test_exact_header_matches_the_output_contract(self):
        text = CONTRACT_PATH.read_text(encoding="utf-8")
        match = re.search(
            r"## `leads\.csv` contract.*?```text\n([^\n]+)\n```",
            text,
            re.S,
        )
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1).split(","), EXPORTER.CSV_COLUMNS)

    def test_exports_all_contact_and_company_columns(self):
        fieldnames, rows, raw = self.export(accepted_document())
        self.assertEqual(fieldnames, EXPORTER.CSV_COLUMNS)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["Name"], "Ada Example")
        self.assertEqual(row["Email"], "ada@example.com")
        self.assertEqual(row["Role"], "Director of Supply Chain")
        self.assertEqual(row["Company"], "Example Products, Inc.")
        self.assertEqual(row["LinkedIn"], "https://www.linkedin.com/in/ada-example")
        self.assertEqual(row["Website"], "https://www.example.com/products")
        self.assertEqual(
            row["Company LinkedIn"],
            "https://www.linkedin.com/company/example-products",
        )
        self.assertEqual(row["Industry"], "Manufacturing")
        self.assertEqual(row["Sub Industry"], "Consumer products")
        self.assertEqual(row["City"], "Columbus")
        self.assertEqual(row["State"], "Ohio")
        self.assertEqual(row["Country"], "United States")
        self.assertEqual(row["HQ State"], "Ohio")
        self.assertEqual(row["HQ Country"], "United States")
        self.assertEqual(row["Employee Count"], "240")
        self.assertEqual(
            row["Description"], "Makes packaged goods, tools, and accessories."
        )
        self.assertIn("Signal: warehouse_system_integration", row["Intent Details"])
        self.assertIn("Date: 2026-08-12", row["Intent Details"])
        self.assertIn("Source: https://example.com/news/wms-project", row["Intent Details"])
        self.assertEqual(row["Phone"], "+1 555 010 0200")
        self.assertIn('"Example Products, Inc."', raw)
        self.assertIn('"Signal: warehouse_system_integration;', raw)
        self.assertIn("one WMS.\nThe project covers inventory visibility.", raw)

    def test_empty_accepted_list_writes_header_only(self):
        fieldnames, rows, raw = self.export({"accepted": []})
        self.assertEqual(fieldnames, EXPORTER.CSV_COLUMNS)
        self.assertEqual(rows, [])
        self.assertEqual(raw.splitlines(), [",".join(EXPORTER.CSV_COLUMNS)])

    def test_missing_optional_values_stay_blank(self):
        document = accepted_document()
        company = document["accepted"][0]["company"]
        contact = document["accepted"][0]["primary_contact"]
        for key in (
            "website",
            "linkedin_url",
            "industry",
            "sub_industry",
            "hq_state",
            "hq_country",
            "employee_count",
            "description",
        ):
            company.pop(key)
        for key in ("email", "linkedin_url", "city", "state", "country", "phone"):
            contact.pop(key)
        _, rows, _ = self.export(document)
        row = rows[0]
        self.assertEqual(row["Website"], "https://example.com")
        self.assertEqual(row["LinkedIn"], "")
        self.assertEqual(row["Company LinkedIn"], "")
        self.assertEqual(row["Email"], "")
        self.assertEqual(row["Phone"], "")
        self.assertEqual(row["Employee Count"], "")

    def test_contact_url_can_fill_linkedin_only_for_linkedin(self):
        document = accepted_document()
        contact = document["accepted"][0]["primary_contact"]
        contact.pop("linkedin_url")
        contact["contact_url"] = "https://uk.linkedin.com/in/ada-example"
        _, rows, _ = self.export(document)
        self.assertEqual(
            rows[0]["LinkedIn"], "https://uk.linkedin.com/in/ada-example"
        )

    def test_malformed_accepted_row_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = pathlib.Path(directory) / "leads.csv"
            with self.assertRaises(EXPORTER.ExportError):
                EXPORTER.export_csv({"accepted": [{}]}, destination)
            self.assertFalse(destination.exists())

    def test_schema_allows_export_metadata_without_making_it_required(self):
        blocks = re.findall(
            r"```json\n(.*?)\n```",
            CONTRACT_PATH.read_text(encoding="utf-8"),
            re.S,
        )
        result_schema = json.loads(blocks[1])
        company = result_schema["$defs"]["company"]
        contact = result_schema["$defs"]["contact"]
        self.assertEqual(company["required"], ["canonical_name", "domain"])
        self.assertTrue(
            {
                "website",
                "linkedin_url",
                "industry",
                "sub_industry",
                "hq_state",
                "hq_country",
                "employee_count",
                "description",
            }.issubset(company["properties"])
        )
        self.assertTrue(
            {"linkedin_url", "city", "state", "country"}.issubset(
                contact["properties"]
            )
        )


if __name__ == "__main__":
    unittest.main()
