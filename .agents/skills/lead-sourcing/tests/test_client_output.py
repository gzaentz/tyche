from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
EXPORTER_PATH = ROOT / "scripts" / "export_xlsx.mjs"
VALIDATOR_PATH = ROOT / "scripts" / "validate_run.py"
TAXONOMY_PATH = ROOT / "assets" / "leadpoet_industry_taxonomy.json"

_export_tests = importlib.util.spec_from_file_location(
    "export_xlsx_fixtures", ROOT / "tests" / "test_export_xlsx.py"
)
assert _export_tests and _export_tests.loader
_export_module = importlib.util.module_from_spec(_export_tests)
_export_tests.loader.exec_module(_export_module)
accepted_document = _export_module.accepted_document
EXPECTED_LEGACY_COLUMNS = _export_module.EXPECTED_COLUMNS

_validator_spec = importlib.util.spec_from_file_location("client_output_validator", VALIDATOR_PATH)
assert _validator_spec and _validator_spec.loader
VALIDATOR = importlib.util.module_from_spec(_validator_spec)
_validator_spec.loader.exec_module(VALIDATOR)

ROWS_HELPER = """
import fs from "node:fs/promises";
import { pathToFileURL } from "node:url";
try {
  const [modulePath, inputPath] = process.argv.slice(-2);
  const exporter = await import(pathToFileURL(modulePath).href);
  const document = JSON.parse(await fs.readFile(inputPath, "utf8"));
  const before = JSON.stringify(document);
  const clientOutput = document.schema_version === "1.2";
  const rows = exporter.rowsFor(document);
  const sources = clientOutput ? exporter.sourcesFor(document) : [];
  const after = JSON.stringify(document);
  process.stdout.write(JSON.stringify({
    legacy: exporter.XLSX_COLUMNS,
    client: exporter.CLIENT_XLSX_COLUMNS,
    rows,
    sources,
    unchanged: before === after,
  }));
} catch (error) {
  process.stderr.write(JSON.stringify({error: error instanceof Error ? error.message : String(error)}));
  process.exitCode = 2;
}
"""


def _source(provider: str, route_id: str) -> dict:
    return {
        "provider": provider,
        "operation": "execute",
        "tool": "evidence-tool",
        "route_id": route_id,
    }


def client_document(*, date_basis: str = "published", schema_version: str = "1.2") -> dict:
    document = accepted_document(["email"])
    document["schema_version"] = schema_version
    document["retrieved_at"] = "2026-09-01T12:34:56Z"
    row = document["accepted"][0]
    row["intent_details"] = (
        "Example Products connected its acquired warehouse to a shared WMS on August 12, 2026. "
        "The project covers inventory visibility and fulfillment across the combined operation. "
        "This recent integration may increase its need to coordinate stock and orders between warehouses."
    )
    row["company"]["description"] = (
        "Example Products, Inc. manufactures packaged goods, tools, and accessories. "
        "It supplies retailers with consumer products."
    )
    row["company"]["sub_industry"] = "Textiles"
    row["company"]["classification_note"] = "Canonical taxonomy classification"
    row["account_fit"] = {
        "fit_claim": "Manufacturing account",
        "evidence_url": "https://example.com/about",
        "evidence_date": "2026-08-10",
        "evidence_date_basis": date_basis,
        "evidence_text": "Example Products manufactures packaged goods, tools and accessories for retailers.",
        "source": _source("public_web", "fit-1"),
    }
    row["signal_evidence"].update(
        {
            "evidence_date_basis": date_basis,
            "evidence_text": "On August 12, 2026, the company connected its acquired warehouse to one WMS. The project covers inventory visibility & fulfillment.",
            "source": _source("public_web", "signal-1"),
        }
    )
    row["primary_contact"].update(
        {
            "requested_role": "Director of Supply Chain",
            "role_match": "exact",
            "company": "Example Products, Inc.",
            "domain": "example.com",
            "evidence_url": "https://example.com/team/ada",
            "evidence_date": "2026-08-11",
            "evidence_date_basis": date_basis,
            "evidence_text": "Ada leads supply chain operations.",
            "source": _source("public_web", "contact-1"),
        }
    )
    document["summary"] = {"accepted_companies": 1, "accepted_contacts": 1}
    document["request"].update(
        {
            "target_count": 1,
            "icp": {"geographies": ["US"]},
            "requested_roles": ["Director of Supply Chain"],
            "time_window": {"max_age_days": 270},
            "budget": {"deepline_credits": 5, "hard_stop": True},
        }
    )
    document["stop_reason"] = "target_met"
    for route in document["routes"]:
        cost = 0.1 if route.get("paid_calls") else 0
        route.update({"cost_credits": cost, "cost_upper_bound_credits": cost, "cost_basis": "actual"})
    document["cost_summary"] = VALIDATOR.calculate_cost_summary(document)
    return document


class ClientOutputTests(unittest.TestCase):
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
                [self.node, "--input-type=module", "--eval", ROWS_HELPER,
                 "tyche-client-output-test", str(EXPORTER_PATH), str(source)],
                check=False, capture_output=True, text=True,
            )

    def test_client_columns_separate_signals_from_prose_and_legacy_is_unchanged(self):
        document = client_document()
        result = self.run_rows_json(document)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["legacy"], EXPECTED_LEGACY_COLUMNS)
        self.assertEqual(
            payload["client"],
            EXPECTED_LEGACY_COLUMNS[:16] + ["Intent Signal", "Signals"] + EXPECTED_LEGACY_COLUMNS[16:],
        )
        self.assertEqual(payload["rows"][0]["Intent Signal"], "warehouse_system_integration")
        row = payload["rows"][0]
        self.assertEqual(row["Description"], document["accepted"][0]["company"]["description"])
        self.assertEqual(row["Intent Details"], document["accepted"][0]["intent_details"])
        self.assertIn("Source date: 2026-08-12", row["Signals"])
        self.assertIn("inventory visibility & fulfillment", row["Signals"])
        self.assertIn("Source: https://example.com/news/wms-project", row["Signals"])
        self.assertTrue(payload["unchanged"])

    def test_signals_include_only_confirmed_tagged_checks_and_preserve_sources(self):
        document = client_document(date_basis="observed_current")
        evidence = {"url": "https://example.com/jobs/product-manager", "date": "2026-08-20",
                    "date_basis": "observed_current", "text": "Open product manager role.",
                    "source": _source("public_web", "hiring-1")}
        document["accepted"][0]["qualification_checks"] = [
            {"criterion": "Hiring", "signal": "HIRING", "importance": "preferred", "status": "pass",
             "claim": "Hiring a product manager", "evidence": [evidence, dict(evidence)]},
            {"criterion": "Funding", "signal": "FUNDING", "importance": "preferred", "status": "unknown",
             "claim": "Not verified", "evidence": [dict(evidence, text="Funding not verified.")]},
            {"criterion": "Expansion", "signal": "EXPANSION", "importance": "preferred", "status": "fail",
             "claim": "Not expanding", "evidence": [dict(evidence, text="Expansion did not proceed.")]},
            {"criterion": "employee_count", "importance": "required", "status": "pass",
             "claim": "Size fits", "evidence": [dict(evidence, text="Employee size evidence.")]},
        ]
        result = self.run_rows_json(document)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        signals = payload["rows"][0]["Signals"]
        self.assertIn("Observed on: 2026-08-12", signals)
        self.assertIn("HIRING\nObserved on: 2026-08-20\nOpen product manager role.", signals)
        self.assertEqual(signals.count(evidence["url"]), 1)
        for value in ("Source date:", "FUNDING", "EXPANSION", "Employee size evidence."):
            self.assertNotIn(value, signals)
        self.assertTrue(any(row["Field"] == "Signals" and row["Source URL"] == evidence["url"]
                            for row in payload["sources"]))
        self.assertTrue(any(row["Field"] == "Funding" for row in payload["sources"]))
        self.assertTrue(payload["unchanged"])

    def test_legacy_document_keeps_legacy_row_shape_and_values(self):
        document = accepted_document(["email", "phone"])
        payload = json.loads(self.run_rows_json(document).stdout)
        self.assertEqual(payload["legacy"], EXPECTED_LEGACY_COLUMNS)
        self.assertEqual(set(payload["rows"][0]), set(EXPECTED_LEGACY_COLUMNS))
        self.assertNotIn("Intent Signal", payload["rows"][0])
        self.assertEqual(payload["rows"][0]["Intent Details"].split("; ")[0], "Signal: warehouse_system_integration")
        self.assertTrue(payload["unchanged"])

    def test_sources_for_maps_all_evidence_and_preserves_text(self):
        document = client_document()
        document["accepted"][0]["qualification_checks"] = [
            {
                "criterion": "employee_count",
                "importance": "required",
                "status": "pass",
                "claim": "Large enough",
                "evidence": [{
                    "url": "https://example.com/size",
                    "date": "2026-08-09",
                    "date_basis": "updated",
                    "text": "Exact evidence: 240 employees & growing.",
                    "source": _source("public_web", "qual-1"),
                }],
            }
        ]
        payload = json.loads(self.run_rows_json(document).stdout)
        by_field = {row["Field"]: row for row in payload["sources"]}
        self.assertEqual(
            set(payload["sources"][0]),
            {"Company", "Domain", "Field", "Signal", "Evidence Date", "Date Basis", "Observed On", "Source URL", "Evidence Text"},
        )
        self.assertEqual(by_field["Description"]["Company"], "Example Products, Inc.")
        self.assertEqual(by_field["Description"]["Signal"], "")
        self.assertEqual(by_field["Signals"]["Signal"], "warehouse_system_integration")
        self.assertEqual(by_field["Role"]["Signal"], "")
        self.assertEqual(by_field["employee_count"]["Signal"], "")
        self.assertEqual(by_field["employee_count"]["Evidence Text"], "Exact evidence: 240 employees & growing.")
        self.assertTrue(payload["unchanged"])

    def test_sources_for_observed_current_separates_evidence_date_and_observed_on(self):
        document = client_document(date_basis="observed_current")
        payload = json.loads(self.run_rows_json(document).stdout)
        fit = next(row for row in payload["sources"] if row["Field"] == "Description")
        self.assertEqual(fit["Evidence Date"], "")
        self.assertEqual(fit["Date Basis"], "observed_current")
        self.assertEqual(fit["Observed On"], "2026-08-10")

    def test_sources_for_published_evidence_keeps_original_date_and_retrieval_day(self):
        payload = json.loads(self.run_rows_json(client_document()).stdout)
        fit = next(row for row in payload["sources"] if row["Field"] == "Description")
        self.assertEqual(fit["Evidence Date"], "2026-08-10")
        self.assertEqual(fit["Observed On"], "2026-09-01")

    def test_classification_note_is_an_industry_source_with_blank_url(self):
        payload = json.loads(self.run_rows_json(client_document()).stdout)
        industry = next(row for row in payload["sources"] if row["Field"] == "Industry")
        self.assertEqual(industry["Signal"], "")
        self.assertEqual(industry["Source URL"], "")
        self.assertEqual(industry["Evidence Text"], "Canonical taxonomy classification")
        self.assertEqual(industry["Evidence Date"], "")
        self.assertEqual(industry["Date Basis"], "")
        self.assertEqual(industry["Observed On"], "")

    def test_sources_for_rejects_missing_required_source_evidence(self):
        for field in ("account_fit", "signal_evidence", "primary_contact"):
            with self.subTest(field=field):
                document = client_document()
                document["accepted"][0][field].pop("source")
                result = self.run_rows_json(document)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(field, result.stderr)

    def test_exporter_rejects_unsupported_schema_versions(self):
        result = self.run_rows_json(client_document(schema_version="9.9"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("schema_version", result.stderr)

    def test_validator_accepts_exact_taxonomy_pair(self):
        self.assertEqual(VALIDATOR.validate_run(client_document()), [])

    def test_validator_accepts_absent_taxonomy_with_nonempty_classification_note(self):
        document = client_document()
        document["accepted"][0]["company"].pop("industry")
        document["accepted"][0]["company"].pop("sub_industry")
        self.assertEqual(VALIDATOR.validate_run(document), [])

    def test_validator_rejects_unknown_pair_and_guesses(self):
        document = client_document()
        document["accepted"][0]["company"].update({"industry": "Manufacturing", "sub_industry": "Textiles-ish"})
        errors = VALIDATOR.validate_run(document)
        self.assertTrue(any("taxonomy" in error or "industry" in error for error in errors))

    def test_exporter_rejects_unknown_and_partial_taxonomy_pairs(self):
        for company_update in (
            {"industry": "Manufacturing", "sub_industry": "Textiles-ish"},
            {"industry": "Manufacturing"},
            {"sub_industry": "Textiles"},
        ):
            with self.subTest(company_update=company_update):
                document = client_document()
                company = document["accepted"][0]["company"]
                company.pop("industry", None)
                company.pop("sub_industry", None)
                company.update(company_update)
                result = self.run_rows_json(document)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("industry", result.stderr)

    def test_exporter_accepts_a_known_multi_parent_taxonomy_pair_and_rejects_wrong_parent(self):
        document = client_document()
        document["accepted"][0]["company"].update(
            {"industry": "Software", "sub_industry": "Video Conferencing"}
        )
        self.assertEqual(self.run_rows_json(document).returncode, 0)
        document["accepted"][0]["company"].update(
            {"industry": "Manufacturing", "sub_industry": "Video Conferencing"}
        )
        result = self.run_rows_json(document)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("industry", result.stderr)

    def test_validator_rejects_partial_taxonomy_and_bad_classification_note(self):
        for mutate in (
            lambda company: company.pop("sub_industry"),
        ):
            with self.subTest(mutate=mutate):
                document = client_document()
                mutate(document["accepted"][0]["company"])
                self.assertTrue(VALIDATOR.validate_run(document))

    def test_missing_taxonomy_requires_nonempty_string_classification_note(self):
        for note in (None, "", "   ", 42):
            with self.subTest(note=note):
                document = client_document()
                company = document["accepted"][0]["company"]
                company.pop("industry")
                company.pop("sub_industry")
                company["classification_note"] = note
                errors = VALIDATOR.validate_run(document)
                self.assertTrue(any("classification_note" in error for error in errors))

    def test_exporter_rejects_missing_or_malformed_intent_details(self):
        for narrative in (None, "", "   ", 42):
            with self.subTest(narrative=narrative):
                document = client_document()
                document["accepted"][0]["intent_details"] = narrative
                result = self.run_rows_json(document)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("intent_details", result.stderr)

    def test_validator_rejects_missing_or_malformed_intent_details(self):
        for narrative in (None, "", "   ", 42):
            with self.subTest(narrative=narrative):
                document = client_document()
                document["accepted"][0]["intent_details"] = narrative
                self.assertTrue(any("intent_details" in error for error in VALIDATOR.validate_run(document)))

    def test_description_is_required_by_validator_and_exporter(self):
        for description in (None, "", "   ", 42):
            with self.subTest(description=description):
                document = client_document()
                document["accepted"][0]["company"]["description"] = description
                self.assertTrue(any("company.description" in error for error in VALIDATOR.validate_run(document)))
                result = self.run_rows_json(document)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("company.description", result.stderr)

    def test_validator_keeps_1_0_and_1_1_legacy_documents_valid(self):
        for version in ("1.0", "1.1"):
            document = client_document(schema_version=version)
            document["accepted"][0]["company"].pop("classification_note", None)
            self.assertEqual(VALIDATOR.validate_run(document), [], version)

    def test_validator_rejects_unsupported_versions_and_corrupts_1_2_cost_summary(self):
        unsupported = client_document(schema_version="9.9")
        self.assertTrue(any("schema_version" in error for error in VALIDATOR.validate_run(unsupported)))
        corrupt = client_document()
        corrupt["cost_summary"]["accepted_leads"] = 99
        self.assertTrue(any("cost_summary" in error for error in VALIDATOR.validate_run(corrupt)))

    def test_taxonomy_asset_has_exact_parent_list_and_mapping_shape(self):
        taxonomy = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
        self.assertEqual(taxonomy["parent_industries"], sorted(taxonomy["parent_industries"]))
        self.assertEqual(len(taxonomy["parent_industries"]), 50)
        self.assertEqual(len(taxonomy["subindustry_parents"]), 848)
        self.assertEqual(taxonomy["version"], 1)
        self.assertEqual(len(taxonomy["provenance"]["sha256"]), 64)
        self.assertEqual(taxonomy["subindustry_parents"]["Textiles"], ["Manufacturing"])
        self.assertEqual(taxonomy["subindustry_parents"]["Software"], ["Software"])
        for parents in taxonomy["subindustry_parents"].values():
            self.assertTrue(parents)
            self.assertTrue(set(parents).issubset(taxonomy["parent_industries"]))

    def test_actual_client_workbook_has_sources_typed_dates_and_full_prose(self):
        from linkedin_fixtures import write_linkedin_receipts
        node_modules = os.environ.get("TYCHE_WORKSPACE_NODE_MODULES")
        if not self.node or not node_modules:
            self.skipTest("Bundled workbook runtime is not configured")
        document = client_document(date_basis="observed_current")
        document["accepted"][0]["signal_evidence"]["evidence_date_basis"] = "published"
        document["accepted"][0]["account_fit"]["evidence_text"] = '=HYPERLINK("https://example.com","untrusted source text")'
        with tempfile.TemporaryDirectory() as directory:
            source = pathlib.Path(directory) / "results.json"
            destination = pathlib.Path(directory) / "leads.xlsx"
            source.write_text(json.dumps(document), encoding="utf-8")
            write_linkedin_receipts(source, document)
            source.write_text(json.dumps(document), encoding="utf-8")
            result = subprocess.run(
                [self.node, str(EXPORTER_PATH), str(source), str(destination), "--node-modules", node_modules],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout.splitlines()[-1])["columns"], 20)
            rows = _export_module.read_first_sheet_rows(destination)
            self.assertEqual(rows[0], EXPECTED_LEGACY_COLUMNS[:16] + ["Intent Signal", "Signals"] + EXPECTED_LEGACY_COLUMNS[16:])
            self.assertEqual(rows[1][16], document["accepted"][0]["signal_evidence"]["signal"])
            self.assertIn(document["accepted"][0]["signal_evidence"]["evidence_url"], rows[1][17])
            self.assertEqual(rows[1][18], document["accepted"][0]["intent_details"])
            source_rows = _export_module.read_first_sheet_rows(destination, sheet_number=2)
            self.assertEqual(source_rows[1][8], document["accepted"][0]["account_fit"]["evidence_text"])
            tag = lambda name: "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}" + name
            with zipfile.ZipFile(destination) as archive:
                workbook = ET.fromstring(archive.read("xl/workbook.xml"))
                self.assertEqual([sheet.attrib["name"] for sheet in workbook.iter(tag("sheet"))], ["Leads", "Sources"])
                self.assertIn(b'A1:T2', archive.read("xl/tables/table1.xml"))
                self.assertIn(b'SourcesTable', archive.read("xl/tables/table2.xml"))
                sheet = ET.fromstring(archive.read("xl/worksheets/sheet2.xml"))
                cells = {cell.attrib["r"]: cell for cell in sheet.iter(tag("c"))}
                for address in ["G2", "E3", "G3"]:
                    self.assertNotIn(cells[address].attrib.get("t"), ["s", "inlineStr"])
                    self.assertGreater(float(cells[address].find(tag("v")).text), 40000)
                if "E2" in cells:
                    self.assertIsNone(cells["E2"].find(tag("v")))
                self.assertIn(b'yyyy-mm-dd', archive.read("xl/styles.xml"))
                self.assertEqual(list(sheet.iter(tag("f"))), [])

    def test_invalid_client_evidence_does_not_overwrite_destination(self):
        if not self.node:
            self.skipTest("Node.js is not available")
        document = client_document()
        document["accepted"][0]["signal_evidence"].pop("evidence_url")
        with tempfile.TemporaryDirectory() as directory:
            source = pathlib.Path(directory) / "results.json"
            destination = pathlib.Path(directory) / "leads.xlsx"
            source.write_text(json.dumps(document), encoding="utf-8")
            destination.write_bytes(b"existing workbook")
            result = subprocess.run(
                [self.node, str(EXPORTER_PATH), str(source), str(destination), "--node-modules", directory],
                capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("signal_evidence", result.stderr)
            self.assertEqual(destination.read_bytes(), b"existing workbook")


if __name__ == "__main__":
    unittest.main()
