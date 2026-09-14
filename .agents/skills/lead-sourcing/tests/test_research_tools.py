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
import email_receipts
import research_input
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

    def test_launcher_clock_includes_setup_and_is_preserved_on_resume(self):
        from datetime import datetime, timedelta, timezone
        started = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat()
        with patch.dict(os.environ, {"TYCHE_RUN_STARTED_AT": started}):
            self.start()
        self.assertEqual(json.loads(self.path.read_text())["stop_check"]["started_at"], started)
        self.assertGreaterEqual(self.tools.inspect()["elapsed_seconds"], 90)
        with patch.dict(os.environ, {"TYCHE_RUN_STARTED_AT": datetime.now(timezone.utc).isoformat()}):
            self.start()
        self.assertEqual(json.loads(self.path.read_text())["stop_check"]["started_at"], started)

    def test_research_budget_refusal_explains_protected_verification_reserve(self):
        self.request["contact_fields"] = ["email"]
        self.start(max_usd=.05, verification_reserve_credits=.4)
        with self.assertRaisesRegex(ValueError, "reserved for email verification") as error:
            self.lookup()
        self.assertIn("cap $0.05", str(error.exception))
        self.assertIn("Verification can use its reserve", str(error.exception))
        self.assertFalse(budget.load_ledger(self.path)["calls"])
        document = json.loads(self.path.read_text())
        action = document["stop_check"]["next_actions"][0]
        self.assertEqual(action["scope"], "example.test")
        self.assertIn(action["id"], self.tools.inspect()["blocked_actions"])
        self.assertNotIn(action["id"], {r["route_id"] for r in document["routes"]})
        self.assertFalse((self.path.parent / "receipts" / (action["id"] + ".json")).exists())
        budget.check_allowance(budget.load_ledger(self.path), "deepline", .2, 0, verification=True)

    def test_email_finder_is_discovery_and_cannot_claim_verification_reserve(self):
        spec = research_input.prepare_lookup({"provider": "deepline", "scope": "example.test", "phase": "contact_discovery",
            "purpose": "Find the selected person's work email", "max_cost_credits": 1,
            "request": {"operation": "execute", "tool": "zerobounce_email_finder", "payload": {"first_name": "Alex", "last_name": "Buyer", "domain": "example.test"}}})
        _, action, request = runner._validate_spec(copy.deepcopy(spec))
        self.assertEqual(action["phase"], "contact_discovery")
        self.assertNotEqual(request.get("entity_type"), "email_validation")
        spec["action"]["phase"] = "email_validation"
        with self.assertRaisesRegex(ValueError, "cannot spend its protected reserve"):
            runner._validate_spec(spec)
        for tool in ("zerobounce_email_finder", "zerobounce_get_credits", "zerobounce_score"):
            self.assertIsNone(email_receipts.validator_for_tool(tool))
        for tool, family in (("zerobounce_validate", "zerobounce"), ("bounceban_verify_single", "bounceban"),
                             ("bounceban_get_verification", "bounceban"), ("bounceban_verify_single_result", "bounceban")):
            self.assertEqual(email_receipts.validator_for_tool(tool), family)

    def test_single_verification_cannot_be_resubmitted_as_a_free_status_read(self):
        contract = {"toolId": "bounceban_verify_single", "inputSchema": {"jsonSchema": {"properties": {"email": {"type": "string"}}}},
                    "pricing": {"unit": "result", "creditsPerUnit": .06}}
        self.assertEqual(self.tools._price(contract, {"email": "buyer@example.test"}), .06)
        with self.assertRaisesRegex(ValueError, "below the catalog-derived"):
            self.tools._price(contract, {"email": "buyer@example.test"}, 0)
        spec = research_input.prepare_lookup({"provider": "deepline", "scope": "example.test", "phase": "email_validation",
            "purpose": "Read pending verification", "max_cost_credits": 0, "status_read": True,
            "request": {"operation": "execute", "tool": "bounceban_verify_single", "payload": {"email": "buyer@example.test"}}})
        with self.assertRaisesRegex(ValueError, "do not resubmit"):
            runner._validate_spec(spec)

    def test_missing_current_title_or_employer_cannot_pass_delivery(self):
        from test_client_output import client_document
        document = client_document()
        document["accepted"][0]["primary_contact"].update(current_title=None, company=None)
        errors = runner.accepted_errors(document)
        for field in ("current_title", "company"):
            self.assertTrue(any("primary_contact." + field in error and "verified current value" in error for error in errors))

    def test_spending_pause_preserves_review_without_clearing_ledger_or_allowing_calls(self):
        self.start()
        ref = self.lookup()["lookups"][0]["results"][0]["ref"]
        reason = "provider billed above its reserved bound; reconcile pricing before further paid calls"
        with budget.transaction(budget.ledger_path(self.path)) as ledger:
            ledger["blocked"] = reason
        before = budget.ledger_path(self.path).read_bytes()
        calls = len(self.provider.requests)
        result = self.tools.review(companies=[{"target": "example.test", "decision": "hold_account",
            "reason": "Funding remains unverified", "company": {"ref": ref}}])
        self.assertEqual(result["progress"]["budget"]["blocked"], reason)
        self.assertEqual(json.loads(self.path.read_text())["unresolved"][0]["reason_text"], "Funding remains unverified")
        with self.assertRaisesRegex(ValueError, "reconcile pricing"):
            self.lookup(check("another.test"))
        self.assertEqual(len(self.provider.requests), calls)
        self.assertEqual(budget.ledger_path(self.path).read_bytes(), before)

    def test_all_selected_field_conflicts_are_reported_before_web_is_saved(self):
        self.start()
        self.provider.raw["element"]["locations"] = [{"headquarter": True, "country": "Canada"}]
        ref = self.lookup()["lookups"][0]["results"][0]["ref"]
        before = self.path.read_bytes()
        receipts = sorted((self.path.parent / "receipts").glob("*.json"))
        web = [{"target": "example.test", "purpose": "Review the announcement", "query": "example.test news",
                "response": {"status": "ok", "results": [{"url": "https://example.test/news", "text": "An observed company announcement"}]}}]
        company = {"target": "example.test", "decision": "hold_account", "reason": "Funding still needs research",
                   "company": {"ref": ref, "website": "https://www.example.test/", "hq_country": "United States",
                               "employee_range_evidence": {}}}
        with self.assertRaises(ValueError) as error:
            self.tools.review(companies=[company], web=web)
        for field in ("website", "hq_country", "employee_range_evidence"):
            self.assertIn(field, str(error.exception))
        self.assertIn("Keep the selected ref", str(error.exception))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(sorted((self.path.parent / "receipts").glob("*.json")), receipts)
        company["company"] = {"ref": ref}
        self.tools.review(companies=[company], web=web)
        self.assertEqual(len(json.loads(self.path.read_text())["unresolved"]), 1)

    def test_missing_company_hq_does_not_discard_reviewed_fields(self):
        self.start()
        ref = self.lookup()["lookups"][0]["results"][0]["ref"]
        facts = self.tools._harvest({"ref": ref, "hq_country": "United States", "hq_state": "Minnesota"}, "example.test")
        self.assertEqual(facts["hq_country"], "United States")
        self.assertEqual(facts["hq_state"], "Minnesota")
        self.assertEqual(facts["employee_range"], "51-200")

    def test_publication_date_alias_is_retained_and_missing_date_is_not_invented(self):
        self.start()
        result = self.tools.review(web=[{"target": "example.test", "purpose": "Review publication", "query": "example.test news",
            "response": {"status": "ok", "results": [
                {"url": "https://example.test/news", "text": "A dated announcement", "published_date": "2026-02-09"},
                {"url": "https://example.test/about", "text": "Current company information"}]}}])
        route = result["web_references"]["web:0"]
        evidence = self.tools._evidence({"ref": route + ":0", "date_basis": "published"})
        self.assertEqual(evidence["date"], "2026-02-09")
        with self.assertRaisesRegex(ValueError, "no publication/event date"):
            self.tools._evidence({"ref": route + ":1", "date_basis": "published"})
        current = self.tools._evidence({"ref": route + ":1"})
        self.assertEqual(current["date_basis"], "observed_current")

    def test_missing_attached_web_date_fails_before_saving_and_corrected_retry_works(self):
        self.start()
        web = [{"target": "example.test", "purpose": "Review event", "query": "example event",
                "response": {"status": "ok", "results": [{"url": "https://example.test/news", "text": "Opened a plant"}]}}]
        companies = [{"target": "example.test", "decision": "hold_account", "reason": "More evidence needed",
                      "signal_evidence": {"ref": "web:0:0", "signal": "FACILITY_OPENING", "date_basis": "published"}}]
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "no publication/event date"):
            self.tools.review(companies=companies, web=web)
        self.assertEqual(self.path.read_bytes(), before)
        web[0]["response"]["results"][0]["published_date"] = json.loads(self.path.read_text())["request"]["as_of_date"]
        result = self.tools.review(companies=companies, web=web)
        self.assertIn("web:0", result["web_references"])

    def test_failed_judgment_returns_reusable_saved_web_reference(self):
        self.start()
        web = [{"target": "example.test", "purpose": "Read observed page", "query": "observed page",
                "response": {"status": "ok", "results": [{"url": "https://example.test", "text": "Verified page text"}]}}]
        with self.assertRaisesRegex(ValueError, "Web observations were saved as") as error:
            self.tools.review(web=web, companies=[{"target": "example.test", "decision": "accept", "reason": "Missing contact evidence"}])
        rid = json.loads(self.path.read_text())["routes"][-1]["route_id"]
        self.assertIn(rid, str(error.exception))
        self.assertEqual(self.tools.inspect(ref=rid + ":0")["facts"]["text"], "Verified page text")

    def test_native_review_contract_names_judgment_fields_before_saving_observations(self):
        self.start()
        payload = {"companies": [{"target": "example.test", "decision": "hold_account", "reason": "Review fit",
            "qualification_checks": [{"criterion": "product", "status": "unknown", "evidence": []}]}],
            "web": [{"target": "example.test", "purpose": "Review source", "query": "company information",
                "response": {"status": "ok", "results": [{"url": "https://example.test", "text": "Observed facts"}]}}]}
        before = self.path.read_bytes()
        receipt_count = len(list((self.path.parent / "receipts").glob("*.json"))) if (self.path.parent / "receipts").exists() else 0
        with self.assertRaisesRegex(ValueError, "missing fields: claim, importance"):
            self.tools.call("tyche_review", payload)
        check = payload["companies"][0]["qualification_checks"][0]
        check.update(importance="required", claim="Product fit still needs review",
            evidence=[{"ref": "web:0:0", "date_basis": "2026-09-14"}])
        with self.assertRaisesRegex(ValueError, "date_basis must be one of"):
            self.tools.call("tyche_review", payload)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(list((self.path.parent / "receipts").glob("*.json"))), receipt_count)
        check["evidence"][0]["date_basis"] = "observed_current"
        result = self.tools.call("tyche_review", payload)
        self.assertEqual(result["saved_companies"], ["example.test"])

    def test_signal_age_is_checked_at_account_gate_and_delivery(self):
        request = {"as_of_date": "2026-09-14", "time_window": {"max_age_days": 365},
                   "buying_signals": [{"kind": "FACILITY_OPENING", "max_age_days": 365}, {"kind": "HIRING", "max_age_days": 90}]}
        row = {"stage": "contact", "candidate": {"domain": "example.test"},
               "signal_evidence": {"signal": "FACILITY_OPENING", "evidence_date": "2025-04-01"}}
        document = {"request": request, "unresolved": [row]}
        self.assertIn("outside the requested", ";".join(runner.qualification_errors(document)))
        row["stage"] = "account"
        self.assertEqual(runner.qualification_errors(document), [])
        row["stage"] = "contact"
        for date, valid in [("2025-09-14", True), ("2025-09-13", False), ("2026-09-15", False)]:
            row["signal_evidence"]["evidence_date"] = date
            self.assertEqual(not runner.qualification_errors(document), valid)
        row["signal_evidence"] = {"signal": "HIRING", "evidence_date": "2026-06-15"}
        self.assertIn("0–90 day", ";".join(runner.qualification_errors(document)))
        document["accepted"], document["unresolved"] = [row], []
        self.assertIn("0–90 day", ";".join(runner.qualification_errors(document)))

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

    def test_saved_result_pages_keep_absolute_references_without_new_calls(self):
        self.start()
        rows = [{"url": f"https://example.test/{i}", "text": f"Observed result {i}"} for i in range(13)]
        observed = self.tools.review(web=[{"target": "discovery", "purpose": "Read result list", "query": "company signals",
            "response": {"status": "ok", "results": rows}}])
        rid = observed["web_references"]["web:0"]
        calls = len(self.provider.requests)
        page = self.tools.inspect(ref=rid)
        self.assertEqual(page["next_offset"], 10)
        page = self.tools.inspect(ref=rid, offset=page["next_offset"])
        self.assertEqual([r["ref"] for r in page["results"]], [f"{rid}:{i}" for i in range(10, 13)])
        self.assertIsNone(page["next_offset"])
        self.assertEqual(self.tools.inspect(ref=page["results"][2]["ref"])["facts"], rows[12])
        self.assertEqual(len(self.provider.requests), calls)

    def test_tool_view_omits_sdk_help_but_preserves_cached_native_contract(self):
        self.start()
        def annotated(request, capture):
            body, code = self.provider(request, capture)
            if request["operation"] == "describe":
                body["results"][0].update(usageGuidance={"sdk_help": "irrelevant SDK help " * 1000},
                    outputSchema={"fields": [{"name": "element", "type": "object"}],
                                  "jsonSchema": {"properties": {"element": {"type": "object"}}}})
            return body, code
        self.tools.execute = annotated
        view = self.tools.inspect(tool="harvestapi_get_company")["tool"]
        self.assertNotIn("usageGuidance", view)
        self.assertEqual(view["inputSchema"]["fields"][0]["name"], "url")
        self.assertEqual(view["pricing"]["creditsPerUnit"], .2)
        self.assertEqual(view["output_fields"][0]["name"], "element")
        self.assertEqual(self.tools._description_view({"outputSchema": None})["output_fields"], [])
        calls = len(self.provider.requests)
        schema = self.tools.inspect(tool="harvestapi_get_company", field="outputSchema.jsonSchema")["tool"]
        self.assertEqual(schema["properties"]["element"]["type"], "object")
        self.lookup()
        self.assertEqual(len([r for r in self.provider.requests if r["operation"] == "describe"]), 1)
        self.assertEqual(len(self.provider.requests), calls + 1)

    def test_catalog_pages_show_usable_tools_and_keep_original_evidence_indices(self):
        self.start()
        rows = [{"toolId": "monitor", "callable": False, "deployCommand": "monitor setup"}] * 10
        rows += [{"toolId": f"research_{i}", "callable": True, "description": f"Research capability {i}",
                  "usageGuidance": "SDK material"} for i in range(12)]
        def search(request, capture):
            body, code = self.provider(request, capture)
            body["results"] = copy.deepcopy(rows)
            return body, code
        self.tools.execute = search
        view = self.tools.inspect(query="research")
        rid = view["route"]
        self.assertEqual(view["result_count"], 12)
        self.assertEqual(view["non_callable_count"], 10)
        self.assertEqual(view["results"][0]["ref"], f"{rid}:10")
        self.assertNotIn("usageGuidance", view["results"][0]["facts"])
        calls = len(self.provider.requests)
        page = self.tools.inspect(ref=rid, offset=view["next_offset"])
        self.assertEqual([r["ref"] for r in page["results"]], [f"{rid}:20", f"{rid}:21"])
        self.assertIsNone(page["next_offset"])
        self.assertEqual(self.tools.inspect(ref=f"{rid}:21")["facts"], rows[21])
        self.assertEqual(runner.read_receipt(self.path, rid)["result"]["results"], rows)
        self.assertEqual(len(self.provider.requests), calls)
        rows[:] = rows[:10]
        view = self.tools.inspect(query="no match")
        self.assertEqual(view["results"], [])
        self.assertIn("No callable tools matched", view["catalog_note"])

    def test_inspection_selects_company_and_run_fields_instead_of_ignoring_them(self):
        self.start()
        self.tools.review(companies=[{"target": "example.test", "decision": "hold_account", "reason": "Needs funding evidence",
                                     "company": {"canonical_name": "ExamplePay"}, "intent_details": "Saved research paragraph."}])
        before = self.path.read_bytes(), budget.ledger_path(self.path).read_bytes(), len(self.provider.requests)
        selected = self.tools.inspect(target="example.test", field="company.candidate.canonical_name")
        self.assertEqual(selected, {"value": "ExamplePay"})
        self.assertEqual(self.tools.inspect(target="example.test", field="candidate.canonical_name"), selected)
        self.assertEqual(self.tools.call("tyche_inspect", {"target": "example.test", "field": "intent_details"}),
                         {"value": "Saved research paragraph."})
        self.assertEqual(self.tools.inspect(target="example.test", field="route_count"), {"value": 0})
        with self.assertRaisesRegex(ValueError, "Unknown company field.*saved fields"):
            self.tools.inspect(target="example.test", field="missing")
        with self.assertRaisesRegex(ValueError, "Unknown company field"):
            self.tools.inspect(target="missing.test", field="intent_details")
        self.assertEqual((self.path.read_bytes(), budget.ledger_path(self.path).read_bytes(), len(self.provider.requests)), before)
        self.assertEqual(self.tools.inspect(field="stop_check.started_at")["value"],
                         json.loads(self.path.read_text())["stop_check"]["started_at"])
        with self.assertRaises(KeyError):
            self.tools.inspect(field="nonexistent")

    def test_progress_reports_required_gaps_without_promoting_optional_hiring(self):
        self.start()
        checks = [{"criterion": name, "importance": importance, "status": "unknown", "claim": "Needs research", "evidence": []}
                  for name, importance in [("current funding stage", "required"), ("hiring bonus", "preferred")]]
        self.tools.review(companies=[{"target": "example.test", "decision": "hold_account", "reason": "Funding is unresolved",
                                     "company": {"canonical_name": "ExamplePay"}, "qualification_checks": checks}])
        self.assertEqual(self.tools.inspect()["companies"][0]["missing"], ["current funding stage"])
        self.assertEqual(self.tools.inspect(target="example.test", field="qualification_checks")["value"], checks)

    def test_recorded_provider_error_explains_recovery_without_releasing_unknown_cost(self):
        self.start()
        calls = []
        def failing(request, capture):
            if request["operation"] != "execute":
                return self.provider(request, capture)
            calls.append(request)
            def dispatch():
                raw = {"exit_code": 1, "body": {"error": "Bad request"}, "stderr": ""}
                capture(raw)
                return deepline.normalize_response(request, raw)
            return budget.guarded_call(request, "deepline", dispatch)
        self.tools.execute = failing
        found = self.lookup()["lookups"][0]
        self.assertTrue(found["recorded"])
        self.assertEqual(runner.read_receipt(self.path, found["route"])["result"]["receipt_status"], "complete")
        self.assertIn("cannot resolve unknown billing", found["recovery_note"])
        before = budget.ledger_path(self.path).read_bytes()
        self.tools.inspect(recover=found["route"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(budget.ledger_path(self.path).read_bytes(), before)
        self.assertIsNone(budget.load_ledger(self.path)["calls"][found["route"]]["actual_credits"])

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
            "primary_contact": {"ref": profile, "requested_role": person["requested_role"], "role_match": "exact"}}])
        self.provider.raw = {"status": "ok", "data": {"address": person["email"], "status": "valid", "sub_status": "", "domain_is_catch_all": True}}
        verifier = self.lookup(check("example.com", phase="email_validation", tool="zerobounce_validate", inputs={"email": person["email"]}))["lookups"][0]["results"][0]["ref"]
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "conflicts with the contact email"):
            self.tools.review(companies=[{"target": "example.com", "decision": "accept", "reason": "Conflicting address is refused",
                "primary_contact": {"email_ref": verifier, "email": "other@example.com"}}])
        self.assertEqual(self.path.read_bytes(), before)
        with self.assertRaises(ValueError):
            self.lookup(check("example.com", phase="email_validation", tool="bounceban_verify_single", inputs={"email": person["email"]}))
        self.tools.review(companies=[{"target": "example.com", "decision": "accept", "reason": "Reviewed account and buyer are complete",
                                      "primary_contact": {"email_ref": verifier}}],
                          sources=[{"ref": ref, "state": "exhausted", "reason": "Selected returned evidence reviewed"}
                                   for ref in (selected, profile, verifier)])
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved["accepted"][0]["primary_contact"]["email"], person["email"])
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
