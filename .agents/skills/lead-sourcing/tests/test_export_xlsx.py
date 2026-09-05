from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
EXPORTER_PATH = ROOT / "scripts" / "export_xlsx.mjs"
CONTRACT_PATH = ROOT / "references" / "output-contract.md"

EXPECTED_COLUMNS = [
    "Name",
    "Email",
    "Role",
    "Company",
    "LinkedIn",
    "Website",
    "Company LinkedIn",
    "Industry",
    "Sub Industry",
    "City",
    "State",
    "Country",
    "HQ State",
    "HQ Country",
    "Employee Count",
    "Description",
    "Intent Details",
    "Phone",
]

ROWS_HELPER = """
import fs from "node:fs/promises";
import { pathToFileURL } from "node:url";

try {
  const [modulePath, inputPath] = process.argv.slice(-2);
  const exporter = await import(pathToFileURL(modulePath).href);
  const document = JSON.parse(await fs.readFile(inputPath, "utf8"));
  process.stdout.write(JSON.stringify({
    columns: exporter.XLSX_COLUMNS,
    rows: exporter.rowsFor(document),
  }));
} catch (error) {
  process.stderr.write(JSON.stringify({
    error: error instanceof Error ? error.message : String(error),
  }));
  process.exitCode = 2;
}
"""


def accepted_document(contact_fields: list[str] | None = None) -> dict:
    effective_fields = ["email"] if contact_fields is None else contact_fields
    request = {} if contact_fields is None else {"contact_fields": contact_fields}
    contact = {
        "full_name": "Ada Example",
        "current_title": "Director of Supply Chain",
        "contact_url": "https://example.com/team/ada",
        "linkedin_url": "https://www.linkedin.com/in/ada-example",
        "city": "Columbus",
        "state": "Ohio",
        "country": "United States",
    }
    routes = []
    if "email" in effective_fields:
        contact.update(
            {
                "email": "ada@example.com",
                "email_validation": {
                    "email": "ada@example.com",
                    "status": "valid",
                    "sub_status": None,
                    "source": {
                        "provider": "deepline",
                        "validator": "zerobounce",
                        "operation": "execute",
                        "tool": "runtime-email-validator",
                        "route_id": "email-validation-1",
                    },
                },
            }
        )
        routes.append(
            {
                "route_id": "email-validation-1",
                "phase": "email_validation",
                "provider": "deepline",
                "operation": "execute",
                "tool": "runtime-email-validator",
                "provider_status": "ok",
                "paid_calls": 1,
            }
        )
    if "phone" in effective_fields:
        contact["phone"] = "+1 555 010 0200"

    return {
        "request": request,
        "routes": routes,
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
                    "evidence_text": (
                        "The company connected its acquired warehouse to one WMS.\n"
                        "The project covers inventory visibility & fulfillment."
                    ),
                    "evidence_url": "https://example.com/news/wms-project",
                },
                "primary_contact": contact,
            }
        ],
    }


def read_first_sheet_rows(workbook_path: pathlib.Path) -> list[list[str]]:
    main_namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    tag = lambda name: f"{{{main_namespace}}}{name}"

    with zipfile.ZipFile(workbook_path) as archive:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall(tag("si")):
                shared_strings.append("".join(node.text or "" for node in item.iter(tag("t"))))

        root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        rows: list[list[str]] = []
        for row in root.iter(tag("row")):
            values: list[str] = []
            for cell in row.findall(tag("c")):
                cell_type = cell.attrib.get("t")
                value_node = cell.find(tag("v"))
                if cell_type == "inlineStr":
                    value = "".join(
                        node.text or "" for node in cell.iter(tag("t"))
                    )
                elif value_node is None or value_node.text is None:
                    value = ""
                elif cell_type == "s":
                    value = shared_strings[int(value_node.text)]
                else:
                    value = value_node.text
                values.append(value)
            rows.append(values)
        return rows


class ExportXlsxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.node = os.environ.get("TYCHE_WORKSPACE_NODE") or shutil.which("node")

    def run_rows_json(self, document: dict) -> subprocess.CompletedProcess[str]:
        if not self.node:
            self.skipTest("Node.js is not available")
        with tempfile.TemporaryDirectory() as directory:
            source = pathlib.Path(directory) / "results.json"
            source.write_text(json.dumps(document), encoding="utf-8")
            return subprocess.run(
                [
                    self.node,
                    "--input-type=module",
                    "--eval",
                    ROWS_HELPER,
                    "tyche-row-test",
                    str(EXPORTER_PATH),
                    str(source),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

    def test_exact_header_matches_the_output_contract(self):
        text = CONTRACT_PATH.read_text(encoding="utf-8")
        match = re.search(
            r"## `leads\.xlsx` contract.*?```text\n([^\n]+)\n```",
            text,
            re.S,
        )
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1).split(","), EXPECTED_COLUMNS)

    def test_maps_all_contact_and_company_columns(self):
        result = self.run_rows_json(accepted_document(["email", "phone"]))
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["columns"], EXPECTED_COLUMNS)
        self.assertEqual(len(payload["rows"]), 1)
        row = payload["rows"][0]
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
        self.assertEqual(row["Employee Count"], 240)
        self.assertEqual(
            row["Description"], "Makes packaged goods, tools, and accessories."
        )
        self.assertIn("Signal: warehouse_system_integration", row["Intent Details"])
        self.assertIn("Date: 2026-08-12", row["Intent Details"])
        self.assertIn("inventory visibility & fulfillment", row["Intent Details"])
        self.assertEqual(row["Phone"], "+1 555 010 0200")

    def test_zero_accepted_rows_returns_only_the_fixed_columns(self):
        result = self.run_rows_json(
            {"request": {"contact_fields": []}, "accepted": []}
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload, {"columns": EXPECTED_COLUMNS, "rows": []})

    def test_explicitly_unrequested_email_and_phone_stay_blank(self):
        result = self.run_rows_json(accepted_document([]))
        self.assertEqual(result.returncode, 0, result.stderr)
        row = json.loads(result.stdout)["rows"][0]
        self.assertEqual(row["Email"], "")
        self.assertEqual(row["Phone"], "")

    def test_omitted_contact_fields_defaults_to_validated_email(self):
        result = self.run_rows_json(accepted_document())
        self.assertEqual(result.returncode, 0, result.stderr)
        row = json.loads(result.stdout)["rows"][0]
        self.assertEqual(row["Email"], "ada@example.com")
        self.assertEqual(row["Phone"], "")

    def test_requested_missing_contact_field_fails_closed(self):
        document = accepted_document(["email"])
        document["accepted"][0]["primary_contact"].pop("email")
        result = self.run_rows_json(document)
        self.assertEqual(result.returncode, 2)
        self.assertIn("primary_contact.email is required", result.stderr)

    def test_missing_or_invalid_email_validation_fails_closed(self):
        missing = accepted_document()
        missing["accepted"][0]["primary_contact"].pop("email_validation")
        result = self.run_rows_json(missing)
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires a Deepline ZeroBounce receipt", result.stderr)

        invalid = accepted_document()
        invalid["accepted"][0]["primary_contact"]["email_validation"]["status"] = "INVALID"
        result = self.run_rows_json(invalid)
        self.assertEqual(result.returncode, 2)
        self.assertIn("status must be valid", result.stderr)

        unaccounted = accepted_document()
        unaccounted["routes"][0]["paid_calls"] = 0
        result = self.run_rows_json(unaccounted)
        self.assertEqual(result.returncode, 2)
        self.assertIn("successful paid Deepline validation route", result.stderr)

    def test_only_explicit_valid_zerobounce_status_exports(self):
        for status in ("valid", " VALID ", "Valid"):
            with self.subTest(status=status):
                document = accepted_document()
                document["accepted"][0]["primary_contact"]["email_validation"]["status"] = status
                result = self.run_rows_json(document)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["rows"][0]["Email"], "ada@example.com")

        for status in ("invalid", "catch-all", "spamtrap", "abuse", "do_not_mail", "unknown", " DO_NOT_MAIL ", "new_status"):
            with self.subTest(status=status):
                document = accepted_document()
                document["accepted"][0]["primary_contact"]["email_validation"]["status"] = status
                result = self.run_rows_json(document)
                self.assertEqual(result.returncode, 2)
                self.assertIn("status must be valid", result.stderr)

    def test_missing_optional_values_stay_blank(self):
        document = accepted_document([])
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
        for key in ("linkedin_url", "city", "state", "country"):
            contact.pop(key)
        result = self.run_rows_json(document)
        self.assertEqual(result.returncode, 0, result.stderr)
        row = json.loads(result.stdout)["rows"][0]
        self.assertEqual(row["Website"], "https://example.com")
        self.assertEqual(row["LinkedIn"], "")
        self.assertEqual(row["Company LinkedIn"], "")
        self.assertEqual(row["Employee Count"], "")

    def test_malformed_accepted_row_fails_closed(self):
        result = self.run_rows_json(
            {"request": {"contact_fields": []}, "accepted": [{}]}
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires company and primary_contact objects", result.stderr)

    def test_cli_requires_the_explicit_codex_runtime_path(self):
        if not self.node:
            self.skipTest("Node.js is not available")
        result = subprocess.run(
            [self.node, str(EXPORTER_PATH), "results.json", "leads.xlsx"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--node-modules is required", result.stderr)

    def test_writes_valid_styled_workbook_when_runtime_is_configured(self):
        node_modules = os.environ.get("TYCHE_WORKSPACE_NODE_MODULES")
        if not self.node or not node_modules:
            self.skipTest("Codex workbook runtime is not configured")

        with tempfile.TemporaryDirectory() as directory:
            source = pathlib.Path(directory) / "results.json"
            destination = pathlib.Path(directory) / "leads.xlsx"
            source.write_text(
                json.dumps(accepted_document(["email", "phone"])),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    self.node,
                    str(EXPORTER_PATH),
                    str(source),
                    str(destination),
                    "--node-modules",
                    node_modules,
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(destination.is_file())
            self.assertFalse(pathlib.Path(f"{destination}.inspect.ndjson").exists())

            with zipfile.ZipFile(destination) as archive:
                names = set(archive.namelist())
                self.assertIn("xl/worksheets/sheet1.xml", names)
                self.assertIn("xl/styles.xml", names)
                self.assertIn("xl/tables/table1.xml", names)
                for name in names:
                    if name.endswith(".xml"):
                        ET.fromstring(archive.read(name))
                worksheet_xml = archive.read("xl/worksheets/sheet1.xml")
                table_xml = archive.read("xl/tables/table1.xml")
                self.assertIn(b'showGridLines="0"', worksheet_xml)
                self.assertIn(b'tableParts count="1"', worksheet_xml)
                self.assertIn(b'name="LeadsTable"', table_xml)
                self.assertIn(b'ref="A1:R2"', table_xml)

            rows = read_first_sheet_rows(destination)
            self.assertEqual(rows[0], EXPECTED_COLUMNS)
            self.assertEqual(rows[1][0:4], [
                "Ada Example",
                "ada@example.com",
                "Director of Supply Chain",
                "Example Products, Inc.",
            ])
            self.assertIn("inventory visibility & fulfillment", rows[1][16])

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
