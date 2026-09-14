"""Native tool journeys use fixture provider responses, never paid services."""
import copy
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import budget_guard as budget
import deepline
import research_tools
from research_tools import ResearchTools
import run_attempt as runner
from test_research_interface import setup_request
from tyche_tools import SandboxedTools, serve


class FixtureProvider:
    def __init__(self):
        self.requests = []
        self.raw = {"status": "ok", "element": {"name": "ExamplePay", "website": "https://example.test",
            "linkedinUrl": "https://www.linkedin.com/company/examplepay/",
            "employeeCountRange": {"start": 51, "end": 200}}}
        self.rate = .2
        self.delay = 0
        self.peak = self.active = 0
        self.lock = threading.Lock()

    def __call__(self, request, capture):
        self.requests.append(copy.deepcopy(request))
        tool = request.get("tool", "fixture-search")
        if request["operation"] != "execute":
            key = "email" if tool in {"zerobounce_validate", "bounceban_verify_single"} else "url" if tool.startswith("harvestapi") else "query"
            return {"provider": "deepline", "operation": request["operation"], "status": "ok", "results": [{
                "toolId": tool, "callable": True, "connected": True,
                "inputSchema": {"fields": [{"name": key, "required": True, "type": "string"}],
                    "jsonSchema": {"properties": {key: {"type": "string"}}, "additionalProperties": False}},
                "pricing": {"creditsPerUnit": self.rate, "unit": "call"}}]}, 0
        def dispatch():
            with self.lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
            try:
                time.sleep(self.delay)
                raw = {"exit_code": 0, "body": copy.deepcopy(self.raw), "stderr": ""}
                raw["body"]["billing"] = {"credits_charged": self.rate, "cost_usd": round(self.rate * .1, 8)}
                capture(raw)
                return deepline.normalize_response(request, raw)
            finally:
                with self.lock:
                    self.active -= 1
        return budget.guarded_call(request, "deepline", dispatch)


def check(target="example.test", **options):
    return {"target": target, "phase": "account_verification", "purpose": "Check company fit",
            "tool": "harvestapi_get_company", "inputs": {"url": "https://www.linkedin.com/company/" + target}, **options}


class ResearchToolTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "run/results.json"
        self.provider = FixtureProvider()
        self.tools = ResearchTools(self.path, execute=self.provider)
        self.request = setup_request()["request"]

    def start(self, **options):
        return self.tools.call("tyche_start", {"request": self.request, **options})

    def lookup(self, *checks):
        return self.tools.call("tyche_lookup", {"checks": list(checks or [check()])})

    def test_start_resume_and_cached_describe_need_no_manual_bookkeeping(self):
        self.start()
        result = self.lookup()
        ref = result["lookups"][0]["results"][0]["ref"]
        self.assertEqual(self.tools.inspect(ref=ref)["facts"]["employee_range"], "51-200")
        self.lookup(check("another.test"))
        self.assertEqual(len([r for r in self.provider.requests if r["operation"] == "describe"]), 1)
        ledger = budget.ledger_path(self.path).read_bytes()
        self.start()
        self.assertEqual(budget.ledger_path(self.path).read_bytes(), ledger)
        with self.assertRaisesRegex(ValueError, "already attempted"):
            self.lookup()
        self.assertEqual(budget.audit_ledger(self.path, json.loads(self.path.read_text())), [])

    def test_start_prices_verification_and_reuses_saved_description(self):
        self.request["contact_fields"] = ["email"]
        self.start()
        self.assertEqual(float(budget.load_ledger(self.path)["verification_reserve_credits"]), 1)
        self.tools.inspect(tool="zerobounce_validate")
        self.start()
        self.assertEqual(len(self.provider.requests), 1)

    def test_schema_and_unknown_price_fail_before_paid_dispatch(self):
        self.start()
        with self.assertRaises(ValueError):
            self.lookup(check(inputs={"wrong": "field"}))
        self.assertFalse(budget.load_ledger(self.path)["calls"])
        self.provider.rate = None
        with self.assertRaisesRegex(ValueError, "whole-call price"):
            self.lookup(check(tool="unknown-priced-tool", inputs={"query": "fit"}))
        self.assertFalse(budget.load_ledger(self.path)["calls"])
        for bad in ({"checks": []}, {"checks": [check()] * 4}, {"checks": [dict(check(), command="echo")]}, {"checks": [check(max_cost_credits=float("nan"))]}):
            with self.assertRaises(ValueError):
                self.tools.call("tyche_lookup", bad)

    def test_failed_free_pricing_read_can_resume_without_resetting_clock(self):
        self.request["contact_fields"] = ["email"]
        self.tools.execute = lambda request, capture: ({"provider":"deepline", "operation":"describe", "status":"provider_error", "results":[]}, 2)
        with self.assertRaisesRegex(ValueError, "price unavailable"):
            self.start()
        original = json.loads((self.path.parent / "verification-tool.json").read_text())
        self.tools.execute = self.provider
        self.start()
        self.assertEqual(json.loads(self.path.read_text())["stop_check"]["started_at"], original["started_at"])
        self.assertEqual(len(list(self.path.parent.glob("verification-tool-*.json"))), 1)
        self.assertFalse(budget.load_ledger(self.path)["calls"])

    def test_multiple_batches_share_three_dispatch_slots_and_lose_no_writes(self):
        self.start()
        self.provider.delay = .1
        batches = [[check(f"company-{i}-{j}.test") for j in range(3)] for i in range(3)]
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda batch: self.lookup(*batch), batches))
        self.assertEqual(sum(len(r["lookups"]) for r in results), 9)
        self.assertEqual(self.provider.peak, 3)
        self.assertEqual(len(budget.load_ledger(self.path)["calls"]), 9)
        self.assertEqual(budget.audit_ledger(self.path, json.loads(self.path.read_text())), [])

    def test_receipt_recovery_uses_saved_response_without_redispatch(self):
        self.start()
        self.tools.inspect(tool="harvestapi_get_company")
        with patch.object(runner, "finish_attempt", side_effect=OSError("interrupted after capture")), self.assertRaises(OSError):
            self.lookup()
        rid = next(iter(budget.load_ledger(self.path)["calls"]))
        self.assertEqual(self.tools.inspect()["pending"][0]["ref"], rid)
        calls = len(self.provider.requests)
        self.tools.inspect(recover=rid)
        self.assertEqual(len(self.provider.requests), calls)
        self.assertEqual(budget.audit_ledger(self.path, json.loads(self.path.read_text())), [])
        with self.assertRaisesRegex(ValueError, "already attempted"):
            self.lookup()

    def test_cap_and_uncertain_charges_survive_native_retry(self):
        self.start(max_usd=.025)
        result = self.lookup(*[check(f"company-{i}.test") for i in range(3)])
        self.assertEqual(len(result["lookups"]), 3)
        self.assertEqual(len(budget.load_ledger(self.path)["calls"]), 1)
        self.assertEqual(budget.audit_ledger(self.path, json.loads(self.path.read_text())), [])

        other = ResearchTools(self.path.parent.parent / "uncertain/results.json", execute=lambda request, capture:
            self.provider(request, capture) if request["operation"] != "execute" else budget.guarded_call(request, "deepline", lambda: (
                {"provider": "deepline", "operation": "execute", "tool": request["tool"], "status": "timeout", "results": []}, 2)))
        other.start(self.request)
        outcome = other.lookup([check()])["lookups"][0]
        ledger = budget.load_ledger(other.path)
        self.assertIsNone(ledger["calls"][outcome["route"]]["actual_credits"])
        self.assertEqual(ledger["calls"][outcome["route"]]["maximum_credits"], "0.2")
        with self.assertRaisesRegex(ValueError, "already attempted"):
            other.lookup([check()])

    def test_unqualified_contacts_and_incomplete_acceptance_are_rejected(self):
        self.start()
        with self.assertRaisesRegex(ValueError, "account-qualified"):
            self.lookup(check(phase="contact_discovery"))
        self.assertFalse(budget.load_ledger(self.path)["calls"])
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.tools.review(companies=[{"target": "example.test", "decision": "accept", "reason": "No evidence"}])
        self.assertEqual(self.path.read_bytes(), before)

    def test_inspection_pages_long_sources_without_losing_saved_text(self):
        self.start()
        text = "verified text " * 800
        observed = self.tools.review(web=[{"target": "discovery", "purpose": "Read long page", "query": "long source",
            "response": {"status": "ok", "results": [{"url": "https://example.test", "text": text}]}}])
        ref = observed["web_references"]["web:0"] + ":0"
        found, offset = "", 0
        while offset is not None:
            page = self.tools.inspect(ref=ref, field="text", offset=offset)
            found += page["text"]
            offset = page["next_offset"]
        self.assertEqual(found, text)

    def test_sandbox_relay_requires_host_metadata_and_refuses_policy_drift(self):
        relay = SandboxedTools(self.path)
        with patch("tyche_tools.subprocess.Popen") as launch:
            with self.assertRaisesRegex(ValueError, "sandbox metadata"):
                relay.call("tyche_inspect", {}, {})
            state = {"permissionProfile": {"mode": "read-only"}, "sandboxCwd": self.path.parent.as_uri()}
            with self.assertRaisesRegex(ValueError, "differs"):
                relay.call("tyche_inspect", {}, {"codex/sandbox-state-meta": state})
            state["sandboxCwd"] = Path(__file__).resolve().parents[4].as_uri()
            relay.state = {**state, "permissionProfile": {"mode": "restricted"}}
            with self.assertRaisesRegex(ValueError, "Sandbox changed"):
                relay.call("tyche_inspect", {}, {"codex/sandbox-state-meta": state})
            launch.assert_not_called()

    def test_web_review_is_one_handoff_and_retry_preserves_receipt(self):
        self.start()
        web = {"target": "example.test", "purpose": "Read product", "query": "https://example.test/product",
            "operation": "open", "response": {"status": "ok", "results": [{"url": "https://example.test/product",
                "text": "ExamplePay provides merchant payment processing."}]}}
        company = {"target": "example.test", "decision": "hold_account", "reason": "Funding still needs review",
                   "company": {"canonical_name": "ExamplePay"},
                   "account_fit": {"ref": "web:0:0", "date_basis": "observed_current"}}
        result = self.tools.review(companies=[company], web=[web])
        rid = result["web_references"]["web:0"]
        before = (self.path.parent / "receipts" / (rid + ".json")).read_bytes()
        self.tools.review(companies=[company], web=[web], sources=[{"ref": "web:0", "state": "exhausted", "reason": "Page reviewed"}])
        self.assertEqual((self.path.parent / "receipts" / (rid + ".json")).read_bytes(), before)
        self.assertEqual(len(json.loads(self.path.read_text())["routes"]), 1)
        web["response"]["results"][0]["text"] = "Changed claim"
        with self.assertRaisesRegex(ValueError, "cannot be replaced"):
            self.tools.review(web=[web])

    def test_raw_receipt_authority_and_foreign_reference_rejection(self):
        self.start()
        ref = self.lookup()["lookups"][0]["results"][0]["ref"]
        receipt = self.path.parent / "receipts" / (ref.split(":")[0] + ".json")
        saved = json.loads(receipt.read_text())
        saved["results"][0]["employee_range"] = "10,001+"
        receipt.write_text(json.dumps(saved))
        self.assertEqual(self.tools.inspect(ref=ref)["facts"]["employee_range"], "51-200")
        saved["run_fingerprint"] = "foreign-run"
        receipt.write_text(json.dumps(saved))
        with self.assertRaisesRegex(ValueError, "another run"):
            self.tools.inspect(ref=ref)

    def test_readonly_transport_lists_tools_and_refuses_mutation(self):
        stream = io.StringIO('\n'.join(json.dumps(m) for m in [
            {"id": 1, "method": "initialize"}, {"id": 2, "method": "tools/list"}]) + '\n')
        output = io.StringIO()
        serve(ResearchTools(self.path, readonly=True), stream, output)
        messages = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(messages[1]["result"]["tools"]), 5)
        session = ResearchTools(self.path, readonly=True)
        self.assertEqual(session.call("tyche_inspect", {})["status"], "not_started")
        with self.assertRaisesRegex(ValueError, "read-only"):
            session.call("tyche_start", {"request": self.request})

    def test_complete_native_journey_saves_and_checks_the_workbook(self):
        if not os.environ.get("TYCHE_WORKSPACE_NODE_MODULES"):
            self.skipTest("Bundled workbook runtime not configured")
        if os.environ.get("TYCHE_TEST_ARTIFACTS"):
            directory = Path(os.environ["TYCHE_TEST_ARTIFACTS"])
            directory.mkdir(parents=True, exist_ok=False)
            self.path = directory / "results.json"
            self.tools = ResearchTools(self.path, execute=self.provider)
        from test_client_output import client_document
        from test_export_xlsx import read_first_sheet_rows
        template = client_document()
        row = template["accepted"][0]
        company, person = row["company"], row["primary_contact"]
        self.request = template["request"]
        self.request["target_count"] = 1
        self.request["buying_signals"] = [{"kind": row["signal_evidence"]["signal"], "query": "Recent warehouse integration"}]
        self.start()
        self.provider.raw = {"status": "ok", "element": {"name": company["canonical_name"],
            "website": company["website"], "linkedinUrl": company["linkedin_url"],
            "employeeCountRange": {"start": 201, "end": 500},
            "locations": [{"headquarter": True, "country": "United States", "geographicArea": "Ohio"}]}}
        selected = self.lookup(check("example.com", inputs={"url": company["linkedin_url"]}))["lookups"][0]["results"][0]["ref"]
        research = {"target": "example.com", "decision": "qualify_account", "reason": "Product and recent integration verified",
            "company": {"ref": selected, **{k: company[k] for k in ("industry", "sub_industry", "description", "classification_note")}},
            "account_fit": {"ref": "web:0:0", "fit_claim": row["account_fit"]["fit_claim"]},
            "signal_evidence": {"ref": "web:0:1", "signal": row["signal_evidence"]["signal"]},
            "intent_details": row["intent_details"]}
        observed = {"target": "example.com", "purpose": "Read product and project announcement", "query": "example.com project announcement",
            "response": {"status": "ok", "results": [{k: evidence[k] for k in ("evidence_url", "evidence_text", "evidence_date", "evidence_date_basis")}
                        for evidence in (row["account_fit"], row["signal_evidence"])]}}
        self.tools.call("tyche_review", {"companies": [research], "web": [observed],
            "sources": [{"ref": "web:0", "state": "exhausted", "reason": "Both pages reviewed"}]})
        self.provider.raw = {"status": "ok", "element": {"linkedinUrl": person["linkedin_url"], "firstName": "Ada", "lastName": "Example",
            "currentPosition": [{"companyName": company["canonical_name"], "title": person["current_title"], "companyLinkedinUrl": company["linkedin_url"]}],
            "location": {"linkedinText": "Columbus, Ohio, United States", "parsed": {"city": "Columbus", "state": "Ohio", "countryFull": "United States"}}}}
        profile = self.lookup(check("example.com", phase="contact_verification", tool="harvestapi_get_profile", inputs={"url": person["linkedin_url"]}))["lookups"][0]["results"][0]["ref"]
        self.tools.review(companies=[{"target": "example.com", "decision": "hold_contact", "reason": "Validate selected work email",
            "primary_contact": {"ref": profile, "requested_role": person["requested_role"], "role_match": "exact", "email": person["email"]}}])
        self.provider.raw = {"status": "ok", "data": {"address": person["email"], "status": "valid", "sub_status": "", "domain_is_catch_all": True}}
        verifier = self.lookup(check("example.com", phase="email_validation", tool="zerobounce_validate", inputs={"email": person["email"]}))["lookups"][0]["results"][0]["ref"]
        with self.assertRaises(ValueError):
            self.lookup(check("example.com", phase="email_validation", tool="bounceban_verify_single", inputs={"email": person["email"]}))
        self.tools.review(companies=[{"target": "example.com", "decision": "accept", "reason": "Reviewed account and buyer are complete",
                                      "primary_contact": {"email_ref": verifier}}],
                          sources=[{"ref": ref, "state": "exhausted", "reason": "Selected returned evidence reviewed"}
                                   for ref in (selected, profile, verifier)])
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved["accepted"][0]["primary_contact"]["email_validation"]["status"], "valid")
        self.assertEqual(saved["accepted"][0]["primary_contact"]["country"], "United States")
        self.assertIn("leads_ready_at", saved["stop_check"])
        result = self.tools.call("tyche_finish", {"commentary": "Offline fixture. Required evidence and the selected buyer were reviewed; no live research was performed."})
        validation = json.loads((self.path.parent / "validation.json").read_text())
        self.assertTrue(validation["delivery_allowed"])
        self.assertIn("completed_at", validation)
        import hashlib
        self.assertEqual(validation["results_sha256"], hashlib.sha256(self.path.read_bytes()).hexdigest())
        cells = read_first_sheet_rows(Path(result["export"]["path"]))[1]
        self.assertEqual(cells[9:12], ["Columbus", "Ohio", "United States"])
        self.assertEqual(cells[14], "201-500")
        report = Path(result["report"]).read_text()
        self.assertIn("Offline fixture.", report)
        self.assertIn("Accepted 1 of 1", report)
        self.assertIn("Standard API equivalent", report)
        self.assertTrue(Path(result["preview"]).is_file())


if __name__ == "__main__":
    unittest.main()
