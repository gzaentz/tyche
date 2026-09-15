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
            key = "email" if tool in {"zerobounce_validate", "bounceban_verify_single"} else "url" if tool.startswith("harvestapi") else "website" if tool == "aviato_get_company_funding_rounds" else "query"
            fields = ["first_name", "last_name", "domain"] if tool in {"fixture_email_finder", "hunter_email_finder"} else [key]
            return {"provider": "deepline", "operation": request["operation"], "status": "ok", "results": [{
                "toolId": tool, "callable": True, "connected": True,
                "inputSchema": {"fields": [{"name": field, "required": True, "type": "string"} for field in fields],
                    "jsonSchema": {"properties": {field: {"type": "string"} for field in fields}, "additionalProperties": False}},
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

    def qualifying_signal(self, ref):
        return [{"criterion": "partnership", "signal": "PARTNERSHIP", "status": "pass",
                 "claim": "Fixture partnership reviewed", "evidence": [{"ref": ref,
                     "text": "Fixture company announced the requested partnership."}]}]

    def saved_funding(self, target="example.test"):
        rows = [{"id": "round-c", "name": "Series C - ExamplePay", "stage": "Series C",
                 "announcedOn": "2024-11-28T00:00:00.000Z", "moneyRaised": 45000000}]
        self.provider.raw = {"toolResponse": {"rawV2": {"fundingRounds": rows}},
                             "output_preview": {"kind": "list", "rowCount": len(rows), "preview": rows}}
        return self.lookup(check(target, tool="aviato_get_company_funding_rounds",
            inputs={"website": "https://" + target}))["lookups"][0]["results"][0]["ref"]

    def selected_contact(self, target="example.test", first="Ada", last="Example"):
        """A reviewed account and current person, all from local provider fixtures."""
        ref = self.lookup(check(target))["lookups"][0]["results"][0]["ref"]
        self.tools.review(companies=[{"target": target, "decision": "qualify_account", "reason": "Verified fit",
            "company": {"ref": ref}, "account_fit": {"ref": ref, "text": "Provides payments infrastructure"},
            "qualification_checks": self.qualifying_signal(ref)}])
        self.provider.raw = {"status": "ok", "element": {"linkedinUrl": "https://www.linkedin.com/in/ada-example/",
            "firstName": first, "lastName": last, "currentPosition": [{"companyName": "ExamplePay",
                "companyLinkedinUrl": "https://www.linkedin.com/company/examplepay/", "title": "Head of Payments"}],
            "location": {"parsed": {"countryFull": "Singapore"}}}}
        profile = self.lookup(check(target, phase="contact_verification", tool="harvestapi_get_profile",
            inputs={"url": "https://www.linkedin.com/in/ada-example/"}))["lookups"][0]["results"][0]["ref"]
        self.tools.review(companies=[{"target": target, "decision": "hold_contact", "reason": "Selected current buyer",
            "primary_contact": {"ref": profile, "requested_role": "Head of Payments", "role_match": "exact"}}])
        return profile

    def test_original_request_is_bound_once_and_available_on_resume(self):
        source = self.path.parent.parent / "original.txt"
        source.write_text("Find multi-site businesses; hiring is preferred.")
        self.tools.environment["TYCHE_REQUEST_FILE"] = str(source)
        self.assertEqual(self.start()["request"]["original_text"], source.read_text())
        before = self.path.read_bytes()
        source.write_text("Changed outside the saved run")
        self.start()
        self.assertEqual(before, self.path.read_bytes())
        self.assertIn("multi-site", self.tools.inspect()["request"]["original_text"])

    def test_required_attribute_cannot_be_omitted_before_contact_spend(self):
        self.request["icp"]["required_attributes"] = ["Operates multiple sites"]
        self.start()
        ref = self.lookup()["lookups"][0]["results"][0]["ref"]
        row = {"target": "example.test", "decision": "qualify_account", "reason": "Reviewed company",
               "company": {"ref": ref}, "account_fit": {"ref": ref},
               "qualification_checks": self.qualifying_signal(ref)}
        with self.assertRaisesRegex(ValueError, "required attribute"):
            self.tools.review(companies=[row])
        row["qualification_checks"].append({"requirement_ref": "attribute:0", "status": "pass",
            "claim": "The source identifies two operating sites", "evidence": [{"ref": ref, "text": "Two operating sites are listed"}]})
        self.tools.call("tyche_review", {"companies": [row]})
        saved = json.loads(self.path.read_text())["unresolved"][0]
        self.assertEqual(saved["qualification_checks"][-1]["criterion"], "operates multiple sites")
        self.assertNotIn("requirement_ref", saved["qualification_checks"][-1])

    def test_review_shows_saved_source_not_only_the_rewritten_claim(self):
        self.start()
        self.tools.review(companies=[{"target": "example.test", "decision": "hold_account", "reason": "Review source strength",
            "account_fit": {"ref": "web:0:0"},
            "qualification_checks": [{"criterion": "hiring", "importance": "preferred", "status": "unknown",
                "claim": "Persistence is unverified", "evidence": [{"ref": "web:0:0", "text": "Unproven persistence claim"}]}]}],
            web=[{"target": "example.test", "purpose": "Inspect hiring evidence", "query": "careers",
                "operation": "open", "response": {"status": "ok", "results": [
                    {"url": "https://example.test/jobs", "text": "One current vacancy. No posting date is shown."}]}}])
        packet = self.tools.inspect(target="example.test", field="evidence_review")
        proof = next(iter(packet["sources"].values()))
        self.assertEqual(proof["text"], "One current vacancy. No posting date is shown.")
        self.assertIsNone(proof["date"])
        self.assertEqual(packet["company"]["qualification_checks"][0]["evidence"][0]["text"], "Unproven persistence claim")
        self.assertEqual(json.loads(self.path.read_text())["unresolved"][0]["qualification_checks"][0]["status"], "unknown")

    def test_selected_point_lookups_close_without_closing_searches(self):
        self.start()
        profile = self.selected_contact()
        search = self.lookup(check(tool="fixture_search", inputs={"query": "more companies"}))["lookups"][0]
        frontier = {r["route_id"]: r for r in json.loads(self.path.read_text())["stop_audit"]["route_frontier"]}
        self.assertEqual(frontier[profile.split(":")[0]]["state"], "exhausted")
        self.assertEqual(frontier[search["route"]]["state"], "continuable")

    def test_review_preserves_backup_contacts_and_fallback_verdicts(self):
        row = {"primary_contact": {"full_name": "Primary Buyer", "email_validation": {"status": "valid"}},
               "backup_contacts": [{"full_name": "Secondary Buyer", "requested_role": "COO",
                   "email_validation": {"status": "unknown", "fallback": {"status": "success", "result": "deliverable"}}}]}
        view = self.tools._company_review(row, {})
        self.assertEqual(view["primary_contact"]["email_validation"]["status"], "valid")
        self.assertEqual(view["backup_contacts"][0]["requested_role"], "COO")
        self.assertEqual(view["backup_contacts"][0]["email_validation"]["fallback"]["result"], "deliverable")

    def test_review_preserves_independent_legacy_signal_beside_other_checks(self):
        row = {"signal_evidence": {"signal": "Expansion", "evidence_text": "Opened a new location"},
               "qualification_checks": [{"signal": "Hiring", "status": "unknown", "evidence": []}]}
        view = self.tools._company_review(row, {})
        self.assertEqual(view["signal_evidence"]["signal"], "Expansion")
        self.assertEqual(view["signal_evidence"]["text"], "Opened a new location")

    def test_email_phase_is_derived_and_initial_rejected_before_paid_lookup(self):
        self.start()
        ref = self.selected_contact(last="H.")
        self.tools.inspect(tool="hunter_email_finder")
        before = budget.ledger_path(self.path).read_bytes()
        attempted = len([r for r in self.provider.requests if r.get("operation") == "execute"])
        item = check(tool="hunter_email_finder", contact_ref=ref, inputs={})
        item.pop("phase")
        with self.assertRaisesRegex(ValueError, "surname letters"):
            self.lookup(item)
        self.assertEqual(before, budget.ledger_path(self.path).read_bytes())
        self.assertEqual(attempted, len([r for r in self.provider.requests if r.get("operation") == "execute"]))

    def test_profile_email_addon_uses_account_gate_without_an_explicit_phase(self):
        self.start()
        item = check(tool="harvestapi_get_profile", contact_ref="unverified:0",
                     inputs={"url": "https://www.linkedin.com/in/unknown/", "findEmail": True})
        item.pop("phase")
        before = budget.ledger_path(self.path).read_bytes()
        attempted = len([r for r in self.provider.requests if r.get("operation") == "execute"])
        with self.assertRaisesRegex(ValueError, "account-qualified company"):
            self.lookup(item)
        self.assertEqual(before, budget.ledger_path(self.path).read_bytes())
        self.assertEqual(attempted, len([r for r in self.provider.requests if r.get("operation") == "execute"]))

    def test_malformed_saved_requirements_return_an_actionable_error(self):
        self.start()
        document = json.loads(self.path.read_text())
        for fields in ({"icp": None}, {"buying_signals": [None]}, {"buying_signals": [{}]},
                       {"icp": {"required_attributes": "not a list"}}):
            damaged = copy.deepcopy(document)
            damaged["request"].update(fields)
            with self.subTest(fields=fields), patch.object(self.tools, "_document", return_value=damaged):
                with self.assertRaisesRegex(ValueError, "Restore the original saved request"):
                    self.tools.call("tyche_inspect", {"field": "requirements"})

    def test_unknown_reference_and_cost_inspection_are_actionable(self):
        self.start()
        current = self.lookup()["lookups"][0]["route"]
        with self.assertRaisesRegex(ValueError, current):
            self.tools.inspect(ref="mistyped-reference:0")
        self.assertIn("costs", self.tools.inspect(field="costs"))

    def test_email_reference_supplies_receipt_names_and_rejects_conflicting_identity(self):
        self.start()
        ref = self.selected_contact(first="Aaron", last="Blocher-Rubin, PhD, BCBA")
        before = budget.ledger_path(self.path).read_bytes()
        for fields in ({"first_name": "Other"}, {"domain": "another.test"}):
            with self.subTest(fields=fields), self.assertRaisesRegex(ValueError, "conflicts with the selected profile"):
                self.lookup(check(tool="fixture_email_finder", phase="contact_discovery", contact_ref=ref, inputs=fields))
        self.assertEqual(before, budget.ledger_path(self.path).read_bytes())
        self.provider.raw = {"status": "ok", "data": {"email": "aaron@example.test"}}
        self.lookup(check(tool="fixture_email_finder", phase="contact_discovery", contact_ref=ref,
                          inputs={"full_name": "Aaron Blocher-Rubin", "linkedin_url": "https://www.linkedin.com/in/ada-example/"}))
        sent = [r for r in self.provider.requests if r.get("operation") == "execute" and r.get("tool") == "fixture_email_finder"]
        self.assertEqual(sent[0]["payload"], {"first_name": "Aaron", "last_name": "Blocher-Rubin", "domain": "example.test"})
        self.assertNotIn("contact_ref", sent[0]["payload"])
        self.assertEqual(len([r for r in self.provider.requests if r.get("operation") == "execute" and r.get("tool") == "harvestapi_get_profile"]), 1)

    def test_role_group_is_derived_without_changing_requested_roles(self):
        self.request["contact_role_groups"] = {"primary": ["Chief Executive Officer"], "secondary": ["Head of Payments"]}
        self.request["requested_roles"] = ["Chief Executive Officer", "Head of Payments"]
        self.start()
        self.selected_contact()
        self.tools.review(companies=[{"target": "example.test", "decision": "hold_contact", "reason": "Reviewed equivalent senior role",
            "primary_contact": {"requested_role": "CEO", "role_match": "approved_family"}}])
        contact = json.loads(self.path.read_text())["unresolved"][0]["primary_contact"]
        self.assertEqual(contact["requested_role"], "Chief Executive Officer")
        self.assertEqual(contact["role_group"], "primary")
        self.assertEqual(json.loads(self.path.read_text())["request"]["requested_roles"], self.request["requested_roles"])

    def test_email_reference_removes_clinical_credentials_without_editing_profile(self):
        self.start()
        ref = self.selected_contact(first="Jaime", last="Scourfield, MS, BCBA, LBA")
        self.provider.raw = {"status": "ok", "data": {"email": "jaime@example.test"}}
        self.lookup(check(tool="fixture_email_finder", phase="contact_discovery", contact_ref=ref, inputs={}))
        sent = [r for r in self.provider.requests if r.get("operation") == "execute"
                and r.get("tool") == "fixture_email_finder"]
        self.assertEqual(sent[0]["payload"], {
            "first_name": "Jaime", "last_name": "Scourfield", "domain": "example.test"})
        saved = json.loads(self.path.read_text())["unresolved"][0]["primary_contact"]
        self.assertEqual(saved["full_name"], "Jaime Scourfield, MS, BCBA, LBA")

    def test_signal_corrections_replace_derived_view_and_preserve_contact(self):
        source = {"url": "https://example.test/jobs", "date": "2026-06-01", "date_basis": "published", "text": "One nursing vacancy"}
        check = dict(criterion="hiring", importance="required", status="pass", claim="Rapid hiring", signal="HIRING", evidence=[source])
        row = {"candidate": {"domain": "example.test"}, "stage": "account", "reason_code": "missing_account_evidence", "reason_text": "Review",
               "qualification_checks": [check], "primary_contact": {"full_name": "Saved Buyer", "email": "saved@example.test"},
               "signal_evidence": {"signal": "HIRING", "evidence_text": "Different stale wording"}}
        doc = {"request": {}, "unresolved": [row]}
        update = {"scope": "example.test", "qualification_checks": [dict(check, claim="Source supports one vacancy")], "reason_text": "Correction"}
        saved = research_input.company_update(doc, update)["row"]
        self.assertEqual(saved["signal_evidence"]["evidence_text"], "One nursing vacancy")
        self.assertEqual(saved["primary_contact"], row["primary_contact"])

        legacy = copy.deepcopy(row)
        legacy["signal_evidence"].update(evidence_url="https://example.test/earlier", evidence_date="2026-05-01")
        result = research_input.company_update({"request": {}, "unresolved": [legacy]}, update)["row"]
        self.assertEqual(result["signal_evidence"], legacy["signal_evidence"])
        self.assertEqual(result["qualification_checks"][0]["evidence"], [source])
        corrected = dict(check, status="unknown", claim="A single vacancy does not demonstrate rapid hiring")
        corrected.pop("signal")
        update["qualification_checks"] = [corrected]
        doc["unresolved"] = [saved]
        saved = research_input.company_update(doc, update)["row"]
        self.assertNotIn("signal", saved["qualification_checks"][0])
        self.assertNotIn("signal_evidence", saved)
        self.assertEqual(saved["primary_contact"], row["primary_contact"])

    def test_source_review_changes_invalidate_approval_but_cost_updates_do_not(self):
        doc = {"request": {}, "accepted": [], "unresolved": [], "rejected": [],
               "routes": [{"cost_credits": None}], "stop_audit": {"route_frontier": [
                   {"route_id": "source-1", "state": "exhausted", "reason": "Reviewed"}]}}
        before = runner.review_fingerprint(doc)
        doc["routes"][0]["cost_credits"] = 1
        self.assertEqual(runner.review_fingerprint(doc), before)
        doc["stop_audit"]["route_frontier"][0]["reason"] = "Reopened after a contradictory source"
        self.assertNotEqual(runner.review_fingerprint(doc), before)

    def test_missing_source_excerpt_has_a_specific_diagnostic(self):
        from validate_run import source_evidence_error
        evidence = {"url": "https://example.test/news", "date": "2026-06-01", "date_basis": "published",
                    "source": {"provider": "public_web", "operation": "open", "route_id": "source-1"}}
        self.assertEqual(source_evidence_error(evidence, "qualification"),
                         "qualification requires dated source evidence; missing or invalid: text (supporting source excerpt)")

    def test_profile_gate_precedes_email_spend_and_reuses_current_profile(self):
        self.start()
        ref = self.lookup()["lookups"][0]["results"][0]["ref"]
        self.tools.review(companies=[{"target": "example.test", "decision": "qualify_account", "reason": "Account passed",
            "company": {"ref": ref}, "account_fit": {"ref": ref, "text": "Provides payments"},
            "qualification_checks": self.qualifying_signal(ref)}])
        email = check(phase="email_validation", tool="zerobounce_validate", inputs={"email": "ada@example.test"})
        before = budget.ledger_path(self.path).read_bytes()
        with self.assertRaisesRegex(ValueError, "Email work requires verified identity"):
            self.lookup(email)
        for tool, phase, payload in (
                ("fixture_email_finder", "contact_discovery", {"first_name": "Ada", "last_name": "Example", "domain": "example.test"}),
                ("harvestapi_get_profile", "contact_verification", {"url": "https://www.linkedin.com/in/ada-example/", "findEmail": True})):
            spec = research_input.prepare_lookup({"scope": "example.test", "phase": phase,
                "purpose": "Email enrichment", "max_cost_credits": .2,
                "request": {"operation": "execute", "tool": tool, "payload": payload}})
            with self.subTest(tool=tool), self.assertRaisesRegex(ValueError, "Email work requires verified identity"):
                runner.run_attempt(self.path, spec, execute=lambda *_: self.fail("Provider must not run"))
        self.assertEqual(before, budget.ledger_path(self.path).read_bytes())
        self.assertFalse(any(r.get("tool") == "zerobounce_validate" and r["operation"] == "execute" for r in self.provider.requests))
        # Resolve the profile once and reuse it for both email finding and validation.
        self.provider.raw = {"status": "ok", "element": {"linkedinUrl": "https://www.linkedin.com/in/ada-example/",
            "firstName": "Ada", "lastName": "Example", "currentPosition": [{"companyName": "ExamplePay", "title": "Head of Payments",
                "companyLinkedinUrl": "https://www.linkedin.com/company/examplepay/"}]}}
        profile = self.lookup(check(phase="contact_verification", tool="harvestapi_get_profile",
            inputs={"url": "https://www.linkedin.com/in/ada-example/"}))["lookups"][0]["results"][0]["ref"]
        self.tools.review(companies=[{"target": "example.test", "decision": "hold_contact", "reason": "Current identity and role verified",
            "primary_contact": {"ref": profile, "requested_role": "Head of Payments", "role_match": "exact"}}])
        self.provider.raw = {"status": "ok", "data": {"address": "ada@example.test", "status": "valid", "domain_is_catch_all": True}}
        result = self.lookup(email)["lookups"][0]
        self.assertTrue(result["email_decisions"][0]["usable"])
        self.assertFalse(result["email_decisions"][0]["fallback_allowed"])
        saved = self.tools.inspect()["completion_candidates"][0]
        self.assertTrue(saved["profile_verified"])
        self.assertEqual(saved["saved_valid_emails"][0]["email"], "ada@example.test")
        self.assertEqual(sum(r["operation"] == "execute" and r.get("tool") == "harvestapi_get_profile" for r in self.provider.requests), 1)

    def test_completion_advice_surfaces_legacy_valid_email_with_unfinished_profile(self):
        self.start()
        self.selected_contact()
        self.provider.raw = {"status": "ok", "data": {"address": "ada@example.test", "status": "valid"}}
        self.lookup(check(phase="email_validation", tool="zerobounce_validate", inputs={"email": "ada@example.test"}))
        # Replay the older Arizona shape: paid email saved, profile still missing.
        document = json.loads(self.path.read_text())
        candidate = document["unresolved"][0]
        candidate.pop("primary_contact")
        other = copy.deepcopy(candidate)
        other["candidate"]["domain"] = "other.example"
        document["unresolved"].insert(0, other)
        self.path.write_text(json.dumps(document))
        before = budget.ledger_path(self.path).read_bytes()
        calls = len(self.provider.requests)
        due = self.tools.inspect()["completion_candidates"]
        self.assertEqual(due[0]["target"], "example.test")
        self.assertFalse(due[0]["profile_verified"])
        self.assertEqual(due[0]["saved_valid_emails"][0]["email"], "ada@example.test")
        self.assertIn("Select and review", due[0]["missing"][0])
        self.assertEqual(len(self.provider.requests), calls)
        self.assertEqual(budget.ledger_path(self.path).read_bytes(), before)

    def test_banner_single_vacancy_review_keeps_rapid_hiring_unresolved(self):
        self.request["buying_signals"] = [{"kind": "RAPID_HIRING", "query": "Rapid clinical hiring"}]
        self.start()
        finding = {"target": "banner.example", "decision": "hold_account", "reason": "A single vacancy does not establish rapid hiring",
            "qualification_checks": [{"criterion": "rapid hiring", "importance": "required", "status": "unknown",
                "claim": "One current nursing vacancy is insufficient to infer rapid hiring", "signal": "RAPID_HIRING",
                "evidence": [{"ref": "web:0:0"}]}]}
        result = self.tools.review(companies=[finding], web=[{"target": "banner.example", "purpose": "Review hiring strength",
            "query": "https://banner.example/careers", "operation": "open", "response": {"status": "ok", "results": [
                {"url": "https://banner.example/careers", "text": "Banner Health lists one current nursing vacancy."}]}}])
        self.assertEqual(result["progress"]["summary"]["accepted_companies"], 0)
        with self.assertRaisesRegex(ValueError, "required signal coverage"):
            self.tools.review(companies=[{"target": "banner.example", "decision": "qualify_account", "reason": "Try to continue with the unresolved required claim"}])
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved["unresolved"][0]["qualification_checks"][0]["status"], "unknown")
        self.assertFalse(saved["rejected"])

    def test_wrong_identity_employer_role_or_foreign_profile_cannot_buy_email(self):
        self.start()
        ref = self.selected_contact()
        original = self.path.read_bytes()
        receipt = self.path.parent / "receipts" / (ref.split(":")[0] + ".json")
        raw = receipt.read_bytes()
        variants = [("full_name", "Someone Else"), ("current_title", "Sales Manager"),
                    ("requested_role", "Unrequested role"), ("role_match", "guessed")]
        for field, value in variants:
            doc = json.loads(original)
            doc["unresolved"][0]["primary_contact"][field] = value
            self.path.write_text(json.dumps(doc))
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "Email work requires verified identity"):
                self.lookup(check(phase="email_validation", tool="zerobounce_validate", inputs={"email": "ada@example.test"}))
        self.path.write_bytes(original)
        for foreign in (False, True):
            saved = json.loads(raw)
            if foreign:
                saved["run_fingerprint"] = "another-run"
            else:
                position = saved["provider_response"]["body"]["element"]["currentPosition"][0]
                position.update(companyName="Other employer", companyLinkedinUrl="https://www.linkedin.com/company/other/")
            receipt.write_text(json.dumps(saved))
            before = budget.ledger_path(self.path).read_bytes()
            with self.assertRaisesRegex(ValueError, "Email work requires verified identity"):
                self.lookup(check(phase="email_validation", tool="zerobounce_validate", inputs={"email": "ada@example.test"}))
            self.assertEqual(before, budget.ledger_path(self.path).read_bytes())

    def test_verified_backup_cannot_unlock_email_work_for_unverified_primary(self):
        self.start()
        self.selected_contact()
        document = json.loads(self.path.read_text())
        row = document["unresolved"][0]
        row["backup_contacts"] = [row.pop("primary_contact")]
        row["primary_contact"] = {"full_name": "Unverified Person"}
        self.path.write_text(json.dumps(document))
        before = budget.ledger_path(self.path).read_bytes()
        with self.assertRaisesRegex(ValueError, "Email work requires verified identity"):
            self.lookup(check(phase="email_validation", tool="zerobounce_validate", inputs={"email": "unverified@example.test"}))
        self.assertEqual(budget.ledger_path(self.path).read_bytes(), before)

    def test_finish_without_completion_returns_actionable_work_without_export(self):
        self.start()
        self.selected_contact()
        before = budget.ledger_path(self.path).read_bytes()
        with patch("research_tools.subprocess.run") as exported:
            result = self.tools.finish()
        self.assertEqual(result["status"], "needs_research")
        self.assertEqual(result["progress"]["completion_candidates"][0]["target"], "example.test")
        self.assertFalse(result["delivery_allowed"])
        exported.assert_not_called()
        self.assertEqual(before, budget.ledger_path(self.path).read_bytes())

    def test_incomplete_finish_exposes_saved_discovery_reviews_without_more_spending(self):
        self.start()
        lookup = self.lookup(check(target="discovery", phase="account_discovery",
            tool="fixture-search", inputs={"query": "companies"}))["lookups"][0]
        before = budget.ledger_path(self.path).read_bytes()
        calls = len(self.provider.requests)
        with patch("research_tools.subprocess.run") as exported:
            result = self.tools.finish()
        self.assertEqual(result["status"], "needs_research")
        self.assertFalse(result["delivery_allowed"])
        self.assertEqual(result["progress"]["review_due"]["count"], 1)
        self.assertEqual(result["progress"]["review_due"]["sources"], result["pending_sources"])
        self.assertEqual(self.tools.inspect(field="pending_sources")["items"], result["pending_sources"])
        self.assertEqual([s["ref"] for s in result["pending_sources"]], [lookup["route"]])
        self.assertEqual(result["pending_sources"][0]["target"], "discovery")
        self.tools.review(sources=[{"ref": lookup["route"], "state": "exhausted",
            "reason": "Fixture results reviewed; no further page or qualifying evidence."}])
        self.assertNotIn(lookup["route"], [s["ref"] for s in self.tools.finish().get("pending_sources", [])])
        self.assertEqual(self.tools.inspect()["review_due"]["count"], 0)
        self.assertEqual(len(self.provider.requests), calls)
        self.assertEqual(before, budget.ledger_path(self.path).read_bytes())
        exported.assert_not_called()

    def test_source_reminders_ignore_closed_aliases_and_page_all_open_sources(self):
        self.start()
        first = self.lookup(check(target="old-alias"))["lookups"][0]["route"]
        self.tools.review(sources=[{"ref": first, "state": "exhausted", "reason": "Wrong identity; source reviewed"}])
        for i in range(4):
            self.lookup(check(target="discovery", phase="account_discovery", approach=f"source family {i}",
                              tool="fixture-search", inputs={"query": str(i)}))
        progress = self.tools.inspect()["review_due"]
        self.assertEqual((progress["count"], progress["scopes"]), (4, ["discovery"]))
        self.assertEqual(len(progress["sources"]), 3)
        a = self.tools.inspect(field="pending_sources", limit=2)
        b = self.tools.inspect(field="pending_sources", offset=a["next_offset"], limit=2)
        self.assertIsNone(b["next_offset"])
        self.assertEqual(a["items"] + b["items"], self.tools.finish()["pending_sources"])

    def test_native_fallback_reuses_both_receipts_and_exposes_no_repeat_decision(self):
        self.start()
        self.selected_contact()
        self.provider.raw = {"status": "ok", "data": {"address": "ada@example.test", "status": "unknown"}}
        original = self.lookup(check(phase="email_validation", tool="zerobounce_validate", inputs={"email": "ada@example.test"}))["lookups"][0]
        self.assertTrue(original["email_decisions"][0]["fallback_allowed"])
        self.provider.raw = {"status": "ok", "data": {"email": "ada@example.test", "status": "success", "result": "deliverable"}}
        fallback = self.lookup(check(phase="email_validation", tool="bounceban_verify_single", inputs={"email": "ada@example.test"}))["lookups"][0]
        self.assertTrue(fallback["email_decisions"][0]["usable"])
        before = budget.ledger_path(self.path).read_bytes()
        self.tools.review(companies=[{"target": "example.test", "decision": "hold_contact", "reason": "Validated fallback selected",
            "primary_contact": {"email_ref": fallback["results"][0]["ref"]}}])
        saved = json.loads(self.path.read_text())["unresolved"][0]["primary_contact"]["email_validation"]
        self.assertEqual(saved["source"]["route_id"], original["route"])
        self.assertEqual(saved["fallback"]["source"]["route_id"], fallback["route"])
        self.assertEqual(saved["fallback"]["result"], "deliverable")
        reread = self.tools.inspect(ref=original["route"])
        self.assertFalse(reread["email_decisions"][0]["fallback_allowed"])
        self.assertIn("already attempted", reread["email_decisions"][0]["next"])
        self.assertTrue(self.tools.inspect()["completion_candidates"][0]["email_usable"])
        self.assertEqual(budget.ledger_path(self.path).read_bytes(), before)

    def test_start_resume_and_cached_describe_need_no_manual_bookkeeping(self):
        self.start()
        result = self.lookup()
        ref = result["lookups"][0]["results"][0]["ref"]
        self.assertEqual(self.tools.inspect(ref=ref)["facts"]["employee_range"], "51-200")
        self.lookup(check("another.test"))
        self.assertEqual(len([r for r in self.provider.requests if r["operation"] == "describe"]), 2)
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
        self.assertEqual(len(self.provider.requests), 3)

    def test_free_startup_prerequisites_overlap_and_expose_reusable_descriptions(self):
        self.request["contact_fields"] = ["email"]
        ready = threading.Barrier(3)
        def concurrent_catalog(request, capture):
            self.assertEqual(request["operation"], "describe")
            ready.wait(timeout=3)
            return self.provider(request, capture)
        self.tools.execute = concurrent_catalog
        result = self.start()
        expected = ["harvestapi_get_company", "harvestapi_get_profile", "zerobounce_validate"]
        self.assertEqual(result["cached_descriptions"], expected)
        self.assertFalse(budget.load_ledger(self.path)["calls"])
        self.assertEqual(float(budget.load_ledger(self.path)["verification_reserve_credits"]), 1)
        self.tools.execute = lambda *args: self.fail("Saved contracts must not be requested again")
        for tool in expected:
            self.tools.inspect(tool=tool)
        self.assertEqual(self.start()["cached_descriptions"], expected)
        self.assertFalse(budget.load_ledger(self.path)["calls"])

    def test_explicit_verification_reserve_keeps_prerequisite_check(self):
        self.request["contact_fields"] = ["email"]
        self.start(verification_reserve_credits=.4)
        self.assertEqual(float(budget.load_ledger(self.path)["verification_reserve_credits"]), .4)
        self.assertEqual({r["tool"] for r in self.provider.requests},
                         {"zerobounce_validate", "harvestapi_get_company", "harvestapi_get_profile"})

    def test_unpriced_mandatory_profile_stops_before_research_and_recovers_free(self):
        available = False
        def catalog(request, capture):
            body, code = self.provider(request, capture)
            if request.get("tool") == "harvestapi_get_profile" and not available:
                body["results"][0]["pricing"] = {"unit": "usage", "creditsPerUnit": None,
                    "displayText": "Calculated after execution from returned usage."}
            return body, code
        self.tools.execute = catalog
        with patch("provider_pricing.profile_price", return_value=None):
            result = self.start()
        self.assertEqual(result["status"], "operationally_blocked")
        self.assertIn("harvestapi_get_profile", result["reason"])
        self.assertFalse(result["delivery_allowed"])
        self.assertEqual(self.tools.inspect()["status"], "operationally_blocked")
        self.assertEqual(self.tools.finish()["status"], "operationally_blocked")
        self.assertEqual(self.lookup()["status"], "operationally_blocked")
        original = json.loads((self.path.parent / "profile-tool.json").read_text())
        self.assertFalse(self.path.exists())
        self.assertFalse(self.path.with_name(self.path.name + ".budget.json").exists())
        self.assertTrue(all(r["operation"] == "describe" for r in self.provider.requests))
        available = True
        with patch("provider_pricing.profile_price", return_value=None):
            self.start()
        self.assertEqual(json.loads(self.path.read_text())["stop_check"]["started_at"], original["started_at"])
        self.assertEqual(len(list(self.path.parent.glob("profile-tool-*.json"))), 1)
        self.assertFalse(budget.load_ledger(self.path)["calls"])
        before = len(self.provider.requests)
        self.tools.inspect(tool="harvestapi_get_profile")
        self.start()
        self.assertEqual(len(self.provider.requests), before)

    def test_unavailable_required_company_tool_stops_before_research(self):
        def unavailable(request, capture):
            body, code = self.provider(request, capture)
            body["results"][0]["connected"] = False
            return body, code
        self.tools.execute = unavailable
        result = self.start()
        self.assertEqual(result["status"], "operationally_blocked")
        self.assertIn("required tool is unavailable", result["reason"])
        self.assertFalse(self.path.exists())
        self.assertTrue(all(r["operation"] == "describe" for r in self.provider.requests))

    def test_stored_profile_price_starts_and_records_actual_billing_and_provenance(self):
        self.provider.rate = .03
        def catalog(request, capture):
            body, code = self.provider(request, capture)
            if request.get("tool") == "harvestapi_get_profile" and request["operation"] == "describe":
                contract = body["results"][0]
                contract["pricing"] = {"unit": "usage", "creditsPerUnit": None}
                contract["inputSchema"]["jsonSchema"]["properties"]["main"] = {"type": "string"}
            return body, code
        self.tools.execute = catalog
        self.start()
        profile = check(tool="harvestapi_get_profile", inputs={"url": "https://www.linkedin.com/in/example", "main": "true"})
        result = self.lookup(profile)
        rid = result["lookups"][0]["route"]
        receipt = runner.read_receipt(self.path, rid)["result"]
        self.assertEqual(receipt["attempt"]["action"]["pricing_basis"]["basis"], "measured_planning_price")
        charge = budget.load_ledger(self.path)["calls"][rid]
        self.assertEqual(float(charge["maximum_credits"]), .03)
        self.assertEqual(float(charge["actual_credits"]), .03)
        self.assertEqual(budget.audit_ledger(self.path, json.loads(self.path.read_text())), [])
        # A changed actual price must be preserved, block new spending and
        # report the failure without trying to export or reset its ledger.
        self.provider.rate = .04
        result = self.lookup(check("second.test", tool="harvestapi_get_profile",
            inputs={"url": "https://www.linkedin.com/in/second", "main": "true"}))
        self.assertEqual(result["status"], "operationally_blocked")
        charge = budget.load_ledger(self.path)["calls"][result["lookups"][0]["route"]]
        self.assertEqual(float(charge["actual_credits"]), .04)
        before = len(self.provider.requests)
        self.assertEqual(self.lookup(check("third.test"))["status"], "operationally_blocked")
        self.assertEqual(len(self.provider.requests), before)

    def test_profile_planning_rates_are_option_specific_and_catalog_wins(self):
        contract = {"toolId": "harvestapi_get_profile", "pricing": {"unit": "usage", "creditsPerUnit": None}}
        inputs = {"url": "https://www.linkedin.com/in/example"}
        self.assertEqual(self.tools._price(contract, inputs), .05)
        self.assertEqual(self.tools._price(contract, dict(inputs, main="true")), .03)
        self.assertEqual(self.tools._price(contract, dict(inputs, findEmail="true")), .14)
        with self.assertRaisesRegex(ValueError, "below"):
            self.tools._price(contract, dict(inputs, findEmail="true"), .05)
        for extra in ({"findEmail": "false"}, {"main": "true", "findEmail": "true"}, {"skipSmtp": "true"},
                      {"includeAboutProfile": "true"}, {"main": "false"}, {"main": True}, {"newAddon": "true"}):
            for override in (None, .05, 1):
                with self.subTest(extra=extra, override=override), self.assertRaisesRegex(ValueError, "No whole-call price"):
                    self.tools._price(contract, dict(inputs, **extra), override)
        contract["pricing"] = {"unit": "call", "creditsPerUnit": .08}
        self.assertEqual(self.tools._price(contract, dict(inputs, main="true")), .08)
        contract["pricing"] = {"unit": "usage", "creditsPerUnit": .08}
        with self.assertRaisesRegex(ValueError, "No whole-call price"):
            self.tools._price(contract, inputs)

    def test_required_auth_failure_blocks_discovery_without_rejecting_companies(self):
        self.start()
        self.provider.raw = {"error": "unauthorized API key"}
        result = self.lookup()
        self.assertEqual(result["status"], "operationally_blocked")
        self.assertIn("auth_failed", result["reason"])
        self.assertEqual(json.loads(self.path.read_text())["rejected"], [])
        calls = len(self.provider.requests)
        self.assertEqual(self.lookup(check("second.test"))["status"], "operationally_blocked")
        self.assertEqual(len(self.provider.requests), calls)
        ledger = budget.ledger_path(self.path).read_bytes()
        self.tools.inspect(tool="harvestapi_get_company", refresh=True)
        self.assertIsNone(self.tools.inspect()["operational_block"])
        self.assertEqual(json.loads((self.path.parent / "operational-status.json").read_text())["status"], "ready")
        self.assertEqual(budget.ledger_path(self.path).read_bytes(), ledger)
        with self.assertRaisesRegex(ValueError, "already attempted"):
            self.lookup()

    def test_no_result_is_a_research_gap_not_an_operational_block(self):
        self.start()
        self.provider.raw = {"status": "ok", "element": None}
        result = self.lookup()
        self.assertNotIn("status", result)
        self.assertIsNone(result["progress"]["operational_block"])

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
        blocked = self.lookup(check("another.test"))
        self.assertEqual(blocked["status"], "operationally_blocked")
        with patch.object(research_tools.subprocess, "run") as export:
            self.assertEqual(self.tools.finish()["status"], "operationally_blocked")
            export.assert_not_called()
        self.assertFalse(blocked["delivery_allowed"])
        self.assertEqual(json.loads(Path(blocked["status_file"]).read_text())["reason"], reason)
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

    def test_funding_reference_supplies_saved_date_and_text_without_manual_copy(self):
        self.start()
        rows = [
            {"name": "Series B - ExamplePay", "announcedOn": "2024-11-28T00:00:00.000Z"},
            {"name": "Undated round - ExamplePay"}]
        self.provider.raw = {"toolResponse": {"rawV2": {"fundingRounds": rows}},
                             "output_preview": {"kind": "list", "rowCount": len(rows), "preview": rows}}
        results = self.lookup(check(tool="aviato_get_company_funding_rounds", inputs={"website": "https://example.test"}))["lookups"][0]["results"]
        evidence = self.tools._evidence({"ref": results[0]["ref"]})
        self.assertEqual((evidence["date"], evidence["date_basis"], evidence["text"]),
                         ("2024-11-28", "published", "Series B - ExamplePay"))
        with self.assertRaisesRegex(ValueError, "no publication/event date"):
            self.tools._evidence({"ref": results[1]["ref"]})

    def test_url_free_funding_reuses_raw_receipt_for_company_review(self):
        self.request["icp"]["required_attributes"] = ["Funding stage is Series C or later"]
        self.start()
        company_ref = self.lookup()["lookups"][0]["results"][0]["ref"]
        funding_ref = self.saved_funding()
        before = budget.ledger_path(self.path).read_bytes()
        calls = len(self.provider.requests)
        self.tools.review(companies=[{"target": "example.test", "decision": "qualify_account", "reason": "Reviewed stage and fit",
            "company": {"ref": company_ref}, "account_fit": {"ref": company_ref},
            "qualification_checks": self.qualifying_signal(company_ref) + [{"requirement_ref": "attribute:0", "status": "pass",
                "claim": "Funding history records Series C; this is a stage check, not a recent event", "evidence": [{"ref": funding_ref}]}]}])
        saved = json.loads(self.path.read_text())
        evidence = saved["unresolved"][0]["qualification_checks"][-1]["evidence"][0]
        self.assertIsNone(evidence["url"])
        self.assertEqual(evidence["source"]["result_index"], 0)
        self.assertFalse(runner.qualification_errors(saved, run_file=self.path))
        self.assertTrue(runner.qualification_errors(saved))  # No receipt access cannot pass.
        packet = self.tools.inspect(target="example.test", field="evidence_review")
        self.assertEqual(packet["sources"][funding_ref]["record"]["id"], "round-c")
        self.assertEqual(packet["sources"][funding_ref]["date"], "2024-11-28")
        self.assertEqual(len(self.provider.requests), calls)
        self.assertEqual(budget.ledger_path(self.path).read_bytes(), before)

    def test_url_free_funding_rejects_wrong_identity_tampering_and_signal_use(self):
        from source_receipts import funding_record
        from validate_run import qualification_evidence_error, source_evidence_error
        self.request["icp"]["required_attributes"] = ["Funding stage is Series C or later"]
        self.start()
        ref = self.saved_funding()
        evidence = self.tools._evidence({"ref": ref})
        document = json.loads(self.path.read_text())
        company = {"domain": "example.test"}
        check_value = {"criterion": self.request["icp"]["required_attributes"][0]}
        receipt_path = self.path.parent / "receipts" / (ref.split(":")[0] + ".json")
        receipt_bytes = receipt_path.read_bytes()
        original = json.loads(receipt_bytes)
        for label, replacement in (("foreign run", {"run_fingerprint": "other"}),
                ("wrong request", {"request_fingerprint": "other"}),
                ("incomplete", {"receipt_status": "pending"}),
                ("unknown outcome", {"status": "error"}),
                ("pending", {"pending_verification": {"id": "pending"}}),
                ("missing raw", {"provider_response": None})):
            with self.subTest(label=label):
                receipt_path.write_text(json.dumps({**original, **replacement}))
                with self.assertRaises(ValueError):
                    funding_record(self.path, document, company, evidence)
        receipt_path.write_bytes(receipt_bytes)
        for field, value in (("date", "2026-01-01"), ("text", "Different claim"), ("date_basis", "observed_current")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                funding_record(self.path, document, company, {**evidence, field: value})
        for field, value in (("result_index", 3), ("result_index", True), ("tool", "web_search")):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                funding_record(self.path, document, company, {**evidence, "source": {**evidence["source"], field: value}})
        with self.assertRaisesRegex(ValueError, "identify this company"):
            funding_record(self.path, document, {"domain": "other.test"}, evidence)
        altered = copy.deepcopy(original)
        altered["attempt"]["request"]["payload"]["website"] = "https://other.test"
        receipt_path.write_text(json.dumps(altered))
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            funding_record(self.path, document, {"domain": "other.test"}, evidence)
        altered = copy.deepcopy(original)
        altered["results"][0]["stage"] = "Invented Series D"
        receipt_path.write_text(json.dumps(altered))
        self.assertEqual(funding_record(self.path, document, company, evidence)["stage"], "Series C")
        altered["provider_response"]["body"]["toolResponse"]["rawV2"]["fundingRounds"][0]["website"] = "https://other.test"
        altered["provider_response"]["body"]["output_preview"]["preview"][0]["website"] = "https://other.test"
        receipt_path.write_text(json.dumps(altered))
        with self.assertRaisesRegex(ValueError, "different company"):
            funding_record(self.path, document, company, evidence)
        receipt_path.write_bytes(receipt_bytes)
        self.assertIsNone(qualification_evidence_error(evidence, "stage", document, company, check_value, self.path))
        for disallowed in ({**check_value, "signal": "FUNDING"}, {"criterion": "unrequested stage"}):
            self.assertIn("signals require a source URL", qualification_evidence_error(evidence, "signal", document, company, disallowed, self.path))
        self.assertIn("HTTP/HTTPS", source_evidence_error(evidence, "account_fit"))
        receipt_path.unlink()
        self.assertIn("No such file", qualification_evidence_error(evidence, "stage", document, company, check_value, self.path))

    def test_compatible_result_reviews_merge_into_one_saved_route_decision(self):
        self.start()
        observed = self.tools.review(web=[{"target": "discovery", "purpose": "Read funding sources", "query": "funding sources",
            "response": {"status": "ok", "results": [{"text": "First source"}, {"text": "Second source"}]}}])
        rid = observed["web_references"]["web:0"]
        self.tools.review(sources=[{"ref": rid + ":0", "state": "exhausted", "reason": "First source reviewed"},
                                   {"ref": rid + ":1", "state": "exhausted", "reason": "Second source reviewed"}])
        saved = json.loads(self.path.read_text())
        route = next(r for r in saved["stop_audit"]["route_frontier"] if r["route_id"] == rid)
        self.assertEqual(route["state"], "exhausted")
        self.assertEqual(route["reason"], "First source reviewed\nSecond source reviewed")
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "conflicting source decisions"):
            self.tools.review(sources=[{"ref": rid + ":0", "state": "exhausted", "reason": "Done"},
                                       {"ref": rid + ":1", "state": "blocked", "reason": "Not reviewed"}])
        self.assertEqual(self.path.read_bytes(), before)

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
        with self.assertRaisesRegex(ValueError, "missing fields: claim"):
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
        blocked = self.start()
        self.assertEqual(blocked["status"], "operationally_blocked")
        self.assertIn("price unavailable", blocked["reason"])
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
        view = self.tools.inspect(tool="harvestapi_get_company", refresh=True)["tool"]
        self.assertNotIn("usageGuidance", view)
        self.assertEqual(view["inputSchema"]["fields"][0]["name"], "url")
        self.assertEqual(view["pricing"]["creditsPerUnit"], .2)
        self.assertEqual(view["output_fields"][0]["name"], "element")
        self.assertEqual(self.tools._description_view({"outputSchema": None})["output_fields"], [])
        calls = len(self.provider.requests)
        schema = self.tools.inspect(tool="harvestapi_get_company", field="outputSchema.jsonSchema")["tool"]
        self.assertEqual(schema["properties"]["element"]["type"], "object")
        self.lookup()
        self.assertEqual(len([r for r in self.provider.requests if r["operation"] == "describe"]), 3)
        self.assertEqual(len(self.provider.requests), calls + 1)

    def test_long_contract_help_is_compact_and_saved_detail_is_losslessly_pageable(self):
        self.start()
        description = "Allowed category guidance. " * 1200
        enum = [f"category-{i}" for i in range(60)]
        full = {}
        def described(request, capture):
            body, code = self.provider(request, capture)
            if request["operation"] == "describe":
                contract = body["results"][0]
                contract["inputSchema"]["fields"][0]["description"] = description
                contract["inputSchema"]["jsonSchema"].update(required=["url"], properties={
                    "url": {"type": "string", "description": description},
                    "category": {"type": "string", "enum": enum},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 1}})
                full.update(copy.deepcopy(contract))
            return body, code
        self.tools.execute = described
        view = self.tools.inspect(tool="harvestapi_get_company", refresh=True)["tool"]
        self.assertLess(len(json.dumps(view)), len(json.dumps(full)) // 10)
        schema = view["inputSchema"]["jsonSchema"]
        self.assertEqual(schema["required"], ["url"])
        self.assertEqual(schema["properties"]["limit"], full["inputSchema"]["jsonSchema"]["properties"]["limit"])
        field = schema["properties"]["category"]["enum"]["detail_field"]
        self.assertEqual(self.tools.inspect(tool="harvestapi_get_company", field=field, offset=20)["tool"], enum[20:30])
        calls = len(self.provider.requests)
        restored, offset = "", 0
        while offset is not None:
            page = self.tools.inspect(tool="harvestapi_get_company", field="inputSchema.fields.0.description", offset=offset)
            restored += page["tool"]
            offset = page["next_offset"]
        self.assertEqual(restored, description)
        self.assertEqual(self.tools._description("harvestapi_get_company"), full)
        with self.assertRaisesRegex(ValueError, "input.checks\\[0\\].inputs.*missing required fields: url"):
            self.lookup(check(inputs={"category": "category-50"}))
        self.assertEqual(len(self.provider.requests), calls)
        self.assertFalse(budget.load_ledger(self.path)["calls"])
        self.lookup()
        self.assertEqual(len(self.provider.requests), calls + 1)

    def test_reference_corrections_include_exact_input_path_and_saved_choices(self):
        self.start()
        ref = self.lookup()["lookups"][0]["results"][0]["ref"]
        row = {"target": "example.test", "decision": "hold_account", "reason": "Review fit",
               "account_fit": {"ref": "mistyped:0"}}
        before = self.path.read_bytes(), budget.ledger_path(self.path).read_bytes(), len(self.provider.requests)
        for bad in ("mistyped:0", ref.rsplit(":", 1)[0] + ":999"):
            row["account_fit"]["ref"] = bad
            with self.assertRaises(ValueError) as error:
                self.tools.call("tyche_review", {"companies": [row]})
            self.assertIn("input.companies[0].account_fit.ref", str(error.exception))
            self.assertIn(ref, str(error.exception))
            self.assertIn("example.test", str(error.exception))
            self.assertEqual((self.path.read_bytes(), budget.ledger_path(self.path).read_bytes(), len(self.provider.requests)), before)
        row["account_fit"]["ref"] = ref
        self.tools.call("tyche_review", {"companies": [row]})
        self.assertEqual(len(self.provider.requests), before[2])

    def test_input_corrections_show_valid_fields_and_leave_paid_work_untouched(self):
        self.start()
        before = self.path.read_bytes(), len(self.provider.requests)
        with self.assertRaisesRegex(ValueError, r"input.companies\[0\].*input.sources"):
            self.tools.call("tyche_review", {"companies": [{"target": "example.test", "decision": "hold_account",
                "reason": "Needs evidence", "sources": []}]})
        with self.assertRaisesRegex(ValueError, r"input.limit exceeds its maximum of 10"):
            self.tools.call("tyche_inspect", {"limit": 50})
        with self.assertRaisesRegex(ValueError, r"input.checks has 4 items; allowed count: 1–3"):
            self.lookup(*[check() for _ in range(4)])
        with self.assertRaisesRegex(ValueError, r"input.checks\[0\].inputs.*wrong.*allowed fields: \['url'\]"):
            self.lookup(check(inputs={"url": "https://example.test", "wrong": "value"}))
        with self.assertRaisesRegex(ValueError, r"input.field.*stop_audit.route_frontier"):
            self.tools.call("tyche_inspect", {"field": "route_frontier"})
        self.assertEqual((self.path.read_bytes(), len(self.provider.requests)), before)
        self.assertFalse(budget.load_ledger(self.path)["calls"])

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
        with self.assertRaisesRegex(ValueError, "available fields"):
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
        self.assertEqual(len([r for r in json.loads(self.path.read_text())["routes"] if r["provider"] == "public_web"]), 1)
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
        self.request["icp"]["required_attributes"] = ["Funding stage is Series C or later"]
        original = self.path.parent.parent / "request.txt"
        original.write_text("Find one company matching the supplied fixture criteria; verify one current buyer.")
        self.tools.environment["TYCHE_REQUEST_FILE"] = str(original)
        self.start()
        self.provider.raw = {"status": "ok", "element": {"name": company["canonical_name"],
            "website": company["website"], "linkedinUrl": company["linkedin_url"],
            "employeeCountRange": {"start": 201, "end": 500},
            "locations": [{"headquarter": True, "country": "United States", "geographicArea": "Ohio"}]}}
        selected = self.lookup(check("example.com", inputs={"url": company["linkedin_url"]}))["lookups"][0]["results"][0]["ref"]
        funding_ref = self.saved_funding("example.com")
        research = {"target": "example.com", "decision": "qualify_account", "reason": "Product and recent integration verified",
            "company": {"ref": selected, **{k: company[k] for k in ("industry", "sub_industry", "description", "classification_note")}},
            "account_fit": {"ref": "web:0:0", "fit_claim": row["account_fit"]["fit_claim"]},
            "qualification_checks": [{"criterion": "recent integration", "signal": row["signal_evidence"]["signal"],
                "status": "pass", "claim": "Recent integration verified", "evidence": [{"ref": "web:0:1"}]},
                {"requirement_ref": "attribute:0", "status": "pass", "claim": "Captured funding history records Series C",
                 "evidence": [{"ref": funding_ref}]}],
            "intent_details": row["intent_details"]}
        observed = {"target": "example.com", "purpose": "Read product and project announcement", "query": "example.com project announcement",
            "response": {"status": "ok", "results": [{k: evidence[k] for k in ("evidence_url", "evidence_text", "evidence_date", "evidence_date_basis")}
                        for evidence in (row["account_fit"], row["signal_evidence"])]}}
        self.tools.call("tyche_review", {"companies": [research], "web": [observed],
            "sources": [{"ref": "web:0", "state": "exhausted", "reason": "Both pages reviewed"},
                        {"ref": funding_ref, "state": "exhausted", "reason": "Funding history reviewed"}]})
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
        original_check = saved["accepted"][0]["qualification_checks"][0]
        wrong_source = copy.deepcopy(original_check)
        wrong_source["evidence"][0]["url"] = "https://example.com/not-in-this-receipt"
        self.tools.review(companies=[{"target": "example.com", "decision": "accept", "reason": "Source URL needs review",
            "qualification_checks": [wrong_source]}])
        blocked = self.tools.finish()
        self.assertEqual(blocked["status"], "needs_repair")
        self.assertTrue(any("absent from the saved receipt" in error for error in blocked["errors"]))
        self.assertNotIn("review_ref", blocked)
        self.tools.review(companies=[{"target": "example.com", "decision": "accept", "reason": "Correct saved source selected",
            "qualification_checks": [original_check]}])
        packet = self.tools.finish()
        self.assertEqual(packet["status"], "review_required")
        self.assertEqual(packet["request"], saved["request"])
        self.assertEqual(packet["request"]["original_text"], original.read_text())
        self.assertFalse((self.path.parent / "leads.xlsx").exists())
        revised_signal = {"criterion": "recent integration", "importance": "required", "status": "pass",
            "claim": "The integration announcement is supported", "signal": row["signal_evidence"]["signal"],
            "evidence": [{"ref": "web:0:0"}]}
        self.tools.review(companies=[{"target": "example.com", "decision": "hold_account", "reason": "Final source interpretation needs correction",
            "qualification_checks": [dict(revised_signal, status="unknown", claim="Review the release wording")]}],
            web=[{"target": "example.com", "purpose": "Final source review", "query": "example.com actual release text",
                "operation": "open", "response": {"status": "ok", "results": [{"url": row["signal_evidence"]["evidence_url"],
                    "date": row["signal_evidence"]["evidence_date"], "text": "Released its warehouse integration."}]}}],
            sources=[{"ref": "web:0", "state": "exhausted", "reason": "Reviewed release meaning and date"}])
        corrected = json.loads(self.path.read_text())["unresolved"][0]
        self.assertEqual(corrected["primary_contact"], saved["accepted"][0]["primary_contact"])
        self.assertEqual(len([r for r in self.provider.requests if r.get("tool") == "zerobounce_validate" and r.get("operation") == "execute"]), 1)
        revised_signal["evidence"] = next(c["evidence"] for c in corrected["qualification_checks"] if c.get("signal"))
        self.tools.review(companies=[{"target": "example.com", "decision": "accept", "reason": "Final writing and source meaning reviewed",
            "qualification_checks": [revised_signal],
            "intent_details": row["intent_details"] + " Its manufacturing business depends on coordinated fulfillment."}])
        stale = self.tools.finish(review_ref=packet["review_ref"])
        self.assertEqual(stale["status"], "review_required")
        self.assertNotEqual(stale["review_ref"], packet["review_ref"])
        with patch("research_tools.subprocess.run", return_value=type("FailedExport", (), {"returncode": 1, "stderr": "Temporary export failure", "stdout": ""})()):
            failed = self.tools.call("tyche_finish", {"review_ref": stale["review_ref"], "commentary": "Offline fixture. Required evidence and the selected buyer were reviewed; no live research was performed."})
        self.assertEqual(failed["status"], "needs_repair")
        # A new process can finish the already reviewed snapshot without
        # another review token, email request or reconstructed company record.
        resumed = ResearchTools(self.path, execute=self.provider)
        result = resumed.finish()
        validation = json.loads((self.path.parent / "validation.json").read_text())
        self.assertTrue(validation["delivery_allowed"])
        self.assertIn("completed_at", validation)
        import hashlib
        self.assertEqual(validation["results_sha256"], hashlib.sha256(self.path.read_bytes()).hexdigest())
        cells = read_first_sheet_rows(Path(result["export"]["path"]))[1]
        self.assertEqual(cells[9:12], ["Columbus", "Ohio", "United States"])
        self.assertEqual(cells[14], "201-500")
        self.assertEqual(cells[16].count("Released its warehouse integration."), 1)
        report = Path(result["report"]).read_text()
        self.assertIn("Offline fixture.", report)
        self.assertIn("Accepted 1 of 1", report)
        self.assertIn("Standard API equivalent", report)
        self.assertTrue(Path(result["preview"]).is_file())
        import zipfile
        with zipfile.ZipFile(result["export"]["path"]) as workbook:
            xml = "\n".join(workbook.read(name).decode() for name in workbook.namelist() if name.endswith(".xml"))
        self.assertIn("Saved receipt: " + funding_ref, xml)
        self.assertIn("aviato_get_company_funding_rounds", xml)


if __name__ == "__main__":
    unittest.main()
