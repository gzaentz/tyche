"""Offline journeys through concise inputs and the existing persistence paths."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import test_attempt_execution as fixtures
from test_save_review import company

runner, guard = fixtures.runner, fixtures.budget_guard


def setup_request():
    return {"request": {"target_count": 5,
        "icp": {"industries": ["Payments infrastructure"], "geographies": ["Singapore"],
                "custom_criteria": ["Current Series B; partnership or expansion required; hiring is a bonus"],
                "exclusions": ["tazapay.com"]},
        "requested_roles": ["Head of Payments", "Chief Operating Officer"],
        "contact_role_groups": {"primary": ["Head of Payments"], "secondary": ["Chief Operating Officer"]},
        "buying_signals": [{"kind": "PARTNERSHIP", "importance": "required", "query": "Required partnership or market expansion"},
                           {"kind": "HIRING", "importance": "preferred", "query": "Preferred integrations or ops hiring", "max_age_days": 90}],
        "time_window": {"max_age_days": 365}, "contact_fields": []}}


def catalog(request, capture):
    return {"provider": "deepline", "operation": request["operation"], "status": "ok", "results": [{
        "toolId": request.get("tool", "fixture-search"), "connected": True, "callable": True,
        "inputSchema": {"fields": [{"name": "query", "type": "string", "required": True}],
                        "jsonSchema": {"properties": {"query": {"type": "string"}}, "additionalProperties": False}},
        "pricing": {"creditsPerUnit": 0.2}}]}, 0


def lookup(domain="builder.example", query="payment product"):
    return {"scope": domain, "phase": "account_verification", "purpose": "Check the current business and funding stage",
            "approach": "current-company-profile", "max_cost_credits": 0.2,
            "request": {"operation": "execute", "tool": "fixture-search", "payload": {"query": query}}}


class StartRunTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "fresh-run/results.json"
        self.setup = setup_request()

    def test_start_and_resume_preserve_request_clock_evidence_and_spend(self):
        status = runner.start_run(self.path, self.setup)
        initial = json.loads(self.path.read_text())
        self.assertEqual(status["request"]["contacts_per_company"], 1)
        for key, value in self.setup["request"].items():
            self.assertEqual(initial["request"][key], value)
        self.assertEqual(guard.load_ledger(self.path)["usd_limit"], "2.5")
        self.assertNotIn("stop_reason", initial)  # A draft is not a stopped run.
        runner.run_lookup(self.path, {"request": {"operation": "describe", "tool": "fixture-search"}}, execute=catalog)
        runner.run_lookup(self.path, lookup(), execute=fixtures.AttemptExecutionTests().paid_response)
        before = self.path.read_bytes()
        ledger = guard.ledger_path(self.path).read_bytes()
        receipts = {p.name: p.read_bytes() for p in self.path.parent.joinpath("receipts").glob("*.json")}
        runner.start_run(self.path, self.setup)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(guard.ledger_path(self.path).read_bytes(), ledger)
        self.assertEqual({p.name: p.read_bytes() for p in self.path.parent.joinpath("receipts").glob("*.json")}, receipts)
        self.assertEqual(json.loads(self.path.read_text())["stop_check"]["started_at"], initial["stop_check"]["started_at"])

    def test_bad_request_or_unpriced_email_writes_neither_file(self):
        variants = [dict(self.setup, request={}), *[copy.deepcopy(self.setup) for _ in range(5)]]
        variants[1]["request"]["requested_roles"] = ["Unrelated role"]
        variants[2]["request"].pop("contact_fields")
        variants[3]["request"]["budget"] = {"deepline_credits": "25", "hard_stop": True}
        variants[4]["request"]["budget"] = {"hard_stop": False}
        variants[5]["request"].pop("requested_roles")
        variants[5]["request"].pop("contact_role_groups")
        for setup in variants:
            with self.subTest(setup=setup), self.assertRaises(ValueError):
                runner.start_run(self.path, setup)
            self.assertFalse(self.path.exists())
            self.assertFalse(self.path.with_name("results.json.budget.json").exists())

    def test_role_groups_supply_combined_list_and_preserve_it_on_resume(self):
        expected = self.setup["request"].pop("requested_roles")
        before_input = copy.deepcopy(self.setup)
        status = runner.start_run(self.path, self.setup)
        self.assertEqual(status["request"]["requested_roles"], expected)
        self.assertEqual(self.setup, before_input)
        before, ledger = self.path.read_bytes(), guard.ledger_path(self.path).read_bytes()
        runner.start_run(self.path, self.setup)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(guard.ledger_path(self.path).read_bytes(), ledger)
        changed = copy.deepcopy(self.setup)
        changed["request"]["contact_role_groups"]["secondary"] = ["Head of Sales"]
        with self.assertRaises(ValueError):
            runner.start_run(self.path, changed)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(guard.ledger_path(self.path).read_bytes(), ledger)

    def test_dollar_cap_with_partial_budget_derives_credits_and_preserves_spending(self):
        self.setup.update(max_usd=5)
        self.setup["request"]["budget"] = {"hard_stop": True}
        runner.start_run(self.path, self.setup)
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved["request"]["budget"], {
            "deepline_credits": 50, "scrapingdog_credits": 0, "hard_stop": True})
        self.assertEqual(guard.load_ledger(self.path)["usd_limit"], "5")
        runner.run_lookup(self.path, {"request": {"operation": "describe", "tool": "fixture-search"}}, execute=catalog)
        runner.run_lookup(self.path, lookup(), execute=fixtures.AttemptExecutionTests().paid_response)
        before, ledger = self.path.read_bytes(), guard.ledger_path(self.path).read_bytes()
        runner.start_run(self.path, self.setup)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(guard.ledger_path(self.path).read_bytes(), ledger)
        with self.assertRaises(ValueError):
            runner.start_run(self.path, {**self.setup, "max_usd": 6})
        self.assertEqual(guard.ledger_path(self.path).read_bytes(), ledger)

    def test_partial_budget_resumes_saved_restrictions_and_explicit_zero_stays_disabled(self):
        self.setup["request"]["budget"] = {"hard_stop": True, "deepline_credits": 0,
            "scrapingdog_credits": 0, "max_deepline_credits_per_next_lead": 1}
        runner.start_run(self.path, {**self.setup, "max_usd": 5})
        ledger = guard.ledger_path(self.path).read_bytes()
        partial = copy.deepcopy(self.setup)
        partial["request"]["budget"] = {"hard_stop": True}
        runner.start_run(self.path, partial)
        self.assertEqual(guard.ledger_path(self.path).read_bytes(), ledger)
        self.assertEqual(json.loads(self.path.read_text())["request"]["budget"], self.setup["request"]["budget"])
        with self.assertRaisesRegex(ValueError, "disabled"):
            guard.check_allowance(guard.load_ledger(self.path), "deepline", .1, 0)

    def test_explicit_contacts_cap_and_email_reserve_survive_resume(self):
        self.setup["request"].update(contacts_per_company=3, contact_fields=["email"])
        self.setup["request"]["max_duration_seconds"] = None
        self.setup.update(max_usd=2, verification_reserve_credits=1)
        runner.start_run(self.path, self.setup)
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved["request"]["contacts_per_company"], 3)
        before, ledger = self.path.read_bytes(), guard.ledger_path(self.path).read_bytes()
        for key, value in [("max_usd", 3), ("verification_reserve_credits", 2), ("started_at", "2020-01-01T00:00:00Z")]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                runner.start_run(self.path, {**self.setup, key: value})
            self.assertEqual(self.path.read_bytes(), before)
            self.assertEqual(guard.ledger_path(self.path).read_bytes(), ledger)

    def test_interrupted_initialization_retains_original_settings(self):
        replace = guard.os.replace
        def interrupt(source, destination):
            if Path(destination).resolve() == self.path.resolve():
                raise OSError("run save interrupted after ledger write")
            return replace(source, destination)
        with patch.object(guard.os, "replace", side_effect=interrupt), self.assertRaises(OSError):
            runner.start_run(self.path, self.setup)
        ledger_path = self.path.with_name("results.json.budget.json")
        ledger = json.loads(ledger_path.read_text())
        self.assertFalse(self.path.exists())
        changed = copy.deepcopy(self.setup)
        changed["request"]["icp"]["geographies"] = ["United States"]
        with self.assertRaises(ValueError):
            runner.start_run(self.path, changed)
        runner.start_run(self.path, self.setup)
        self.assertEqual(json.loads(ledger_path.read_text()), ledger)
        self.assertEqual(json.loads(self.path.read_text())["stop_check"]["started_at"], ledger["initial_started_at"])

    def test_stdin_start_then_readonly_status(self):
        command = [sys.executable, runner.__file__, str(self.path)]
        subprocess.run(command + ["--start-file", "-"], input=json.dumps(self.setup), text=True, capture_output=True, check=True)
        before = self.path.read_bytes()
        status = subprocess.run(command + ["--status"], text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(status.stdout)["request"]["contacts_per_company"], 1)
        self.assertEqual(self.path.read_bytes(), before)

    def test_legacy_resume_does_not_backfill_defaults_or_reset_records(self):
        setup = self.setup
        document = copy.deepcopy(setup["request"])
        document["budget"] = {"deepline_credits": 25, "hard_stop": True}
        self.path.parent.mkdir()
        saved = fixtures.stop_document([], target_count=5)
        saved["request"] = document
        saved["budget"]["limits"] = {"deepline_credits": 25, "scrapingdog_credits": 0}
        self.path.write_text(json.dumps(saved))
        guard.initialize(self.path)
        before = json.loads(self.path.read_text())
        ledger = guard.ledger_path(self.path).read_bytes()
        runner.start_run(self.path, {"request": document})
        self.assertEqual(json.loads(self.path.read_text()), before)
        self.assertEqual(guard.ledger_path(self.path).read_bytes(), ledger)

    def test_missing_results_after_spending_cannot_be_reinitialized(self):
        runner.start_run(self.path, self.setup)
        runner.run_lookup(self.path, {"request": {"operation": "describe", "tool": "fixture-search"}}, execute=catalog)
        runner.run_lookup(self.path, lookup(), execute=fixtures.AttemptExecutionTests().paid_response)
        ledger_path = guard.ledger_path(self.path)
        ledger = ledger_path.read_bytes()
        self.path.unlink()  # Simulate lost state in this temporary test run only.
        with self.assertRaisesRegex(ValueError, "reconcile missing state"):
            runner.start_run(self.path, self.setup)
        self.assertFalse(self.path.exists())
        self.assertEqual(ledger_path.read_bytes(), ledger)

    def test_empty_existing_state_is_not_treated_as_fresh(self):
        runner.start_run(self.path, self.setup)
        ledger_path = guard.ledger_path(self.path)
        original = self.path.read_bytes()
        self.path.write_text("{}")
        with self.assertRaisesRegex(ValueError, "existing run state is empty"):
            runner.start_run(self.path, self.setup)
        self.path.write_bytes(original)
        ledger_path.write_text("{}")
        with self.assertRaisesRegex(ValueError, "reconcile missing state"):
            runner.start_run(self.path, self.setup)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(ledger_path.read_text(), "{}")


class LookupTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AttemptExecutionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.path = self.fixture.path

    def describe(self):
        return runner.run_lookup(self.path, {"request": {"operation": "describe", "tool": "fixture-search"}}, execute=catalog)

    def test_reuses_description_and_keeps_duplicate_paid_protection(self):
        self.describe()
        paid = Mock(side_effect=self.fixture.paid_response)
        output = runner.run_lookup(self.path, lookup(), execute=paid)
        body = json.loads(self.path.read_text())
        route = body["routes"][-1]
        self.assertEqual(route["scope"], "builder.example")
        self.assertTrue(route["route_id"].startswith("lookup-"))
        self.assertEqual(route["cost_credits"], 0.1)
        self.assertEqual(runner.read_receipt(self.path, route["route_id"])["result"]["status"], "no_results")
        before = guard.ledger_path(self.path).read_bytes()
        with self.assertRaisesRegex(ValueError, "already attempted"):
            runner.run_lookup(self.path, lookup(), execute=paid)
        self.assertEqual(paid.call_count, 1)
        self.assertEqual(guard.ledger_path(self.path).read_bytes(), before)
        self.assertEqual(output["exit_code"], 0)

    def test_aliases_share_normalization_and_paid_duplicate_protection(self):
        runner.run_lookup(self.path, {"request": {"op": " CATALOG-DESCRIBE ", "name": " fixture-search "}}, execute=catalog)
        value = lookup()
        value["request"] = {"op": " EXECUTE ", "name": "fixture-search", "input": {"query": "payment product"}}
        original = copy.deepcopy(value)
        paid = Mock(side_effect=self.fixture.paid_response)
        runner.run_lookup(self.path, value, execute=paid)
        self.assertEqual(value, original)
        route = json.loads(self.path.read_text())["routes"][-1]
        self.assertEqual((route["operation"], route["paid_calls"], route["cost_credits"]), ("execute", 1, 0.1))
        ledger = guard.ledger_path(self.path).read_bytes()
        legacy = self.fixture.spec("repeat", query="payment product", paid=True)
        with self.assertRaisesRegex(ValueError, "already attempted"):
            runner.run_attempt(self.path, legacy, execute=paid)
        self.assertEqual(paid.call_count, 1)
        self.assertEqual(guard.ledger_path(self.path).read_bytes(), ledger)

    def test_both_input_formats_use_adapter_normalization(self):
        for provider, request in (
                ("deepline", {"operation": "EXECUTE", "tool": "fixture-search", "payload": {"query": "payments"}}),
                ("deepline", {"op": "tools_search", "q": " payments "}),
                ("scrapingdog", {"op": " GOOGLE_SEARCH ", "q": " payments "})):
            with self.subTest(provider=provider, request=request):
                value = dict(lookup(), provider=provider, request=request)
                concise = runner.research_input.prepare_lookup(value)
                legacy = {"action": copy.deepcopy(concise["action"]), "request": request}
                _, action, normalized = runner._validate_spec(legacy)
                _, concise_action, concise_request = runner._validate_spec(concise)
                self.assertEqual(concise_request, normalized)
                self.assertEqual(concise_action, action)

    def test_bad_payloads_and_phase_fail_before_any_planning_or_dispatch(self):
        self.describe()
        invalid = [lookup(), lookup(), lookup(), lookup()]
        invalid[0]["request"]["payload"] = {"q": "typo"}
        invalid[1]["request"]["payload"] = {"query": 3}
        invalid[2]["phase"] = "preferred_signal"
        invalid[3]["request"]["payload"]["unexpected"] = "field"
        before = self.path.read_bytes()
        ledger = guard.ledger_path(self.path).read_bytes()
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                runner.run_lookup(self.path, [lookup("other.example"), value], execute=lambda *_: self.fail("dispatched"))
            self.assertEqual(self.path.read_bytes(), before)
            self.assertEqual(guard.ledger_path(self.path).read_bytes(), ledger)

    def test_input_errors_name_the_batch_item_and_the_correction(self):
        cases = [(dict(lookup(), provider="web"), r"lookup\[1\].provider.*deepline, scrapingdog, public_web"),
                 (dict(lookup(), scope=""), r"lookup\[1\].scope must be a non-empty string"),
                 (dict(lookup(), action={}), r"lookup\[1\] has unexpected fields: action")]
        before, ledger = self.path.read_bytes(), guard.ledger_path(self.path).read_bytes()
        for bad, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                runner.run_lookup(self.path, [lookup("other.example"), bad], execute=lambda *_: self.fail("dispatched"))
            self.assertEqual(self.path.read_bytes(), before)
            self.assertEqual(guard.ledger_path(self.path).read_bytes(), ledger)

    def test_review_reminder_omits_automatically_closed_no_result_routes(self):
        self.describe()
        result = runner.run_lookup(self.path, lookup(), execute=self.fixture.paid_response)
        self.assertEqual(result["review_due"], {"count": 0, "scopes": [], "sources": []})
        result = runner.run_lookup(self.path, lookup("other.example", query="other"), execute=self.fixture.paid_response)
        self.assertEqual(result["review_due"]["count"], 0)
        doc = json.loads(self.path.read_text())
        self.assertEqual(doc["unresolved"], [])  # Code did not invent a qualification judgment.
        builder_route = next(r["route_id"] for r in doc["routes"] if r.get("scope") == "builder.example")
        result = runner.save_review(self.path, {"companies": [{"state": "unresolved", "row": company("builder.example")}],
            "routes": [{"route_id": builder_route, "reason": "Reviewed; the dated signal remains unknown"}]})
        self.assertEqual(result["review_due"], {"count": 0, "scopes": [], "sources": []})

    def test_missing_description_and_unknown_price_never_dispatch(self):
        with self.assertRaisesRegex(ValueError, "Describe"):
            runner.run_lookup(self.path, lookup(), execute=lambda *_: self.fail("dispatched"))
        self.describe()
        value = lookup()
        value.pop("max_cost_credits")
        with self.assertRaisesRegex(ValueError, "not eligible"):
            runner.run_lookup(self.path, value, execute=lambda *_: self.fail("dispatched"))
        self.assertEqual(guard.load_ledger(self.path)["calls"], {})

    def test_three_company_lookups_use_existing_concurrent_dispatch(self):
        self.describe()
        barrier = threading.Barrier(3)
        def execute(request, capture):
            barrier.wait(timeout=5)
            return self.fixture.paid_response(request, capture)
        result = runner.run_lookup(self.path, [lookup(f"{n}.example", str(n)) for n in range(3)], execute=execute)
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(len(guard.load_ledger(self.path)["calls"]), 3)
        self.assertEqual(len(json.loads(self.path.read_text())["routes"]), 4)

    def test_free_catalog_batch_can_share_discovery_scope(self):
        result = runner.run_lookup(self.path, [{"request": {"operation": "describe", "tool": tool}}
            for tool in ("fixture-a", "fixture-b", "fixture-c")], execute=catalog)
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(guard.load_ledger(self.path)["calls"], {})
        self.assertEqual(len(json.loads(self.path.read_text())["routes"]), 3)

    def test_web_plan_and_complete_own_metadata(self):
        runner.run_lookup(self.path, {"provider": "public_web", "scope": "discovery", "phase": "account_discovery",
            "purpose": "Find dated payment partnerships", "request": {"operation": "search_query", "query": "Singapore payments partnerships"}}, plan_only=True)
        doc = json.loads(self.path.read_text())
        rid = doc["stop_audit"]["route_frontier"][0]["route_id"]
        response = {"status": "ok", "results": [{"url": "https://builder.example/news", "text": "Observed announcement"}]}
        runner.complete_public_web(self.path, rid, response)
        receipt = runner.read_receipt(self.path, rid)["result"]
        self.assertEqual(receipt["results"], response["results"])
        self.assertEqual(receipt["run_fingerprint"], guard.run_fingerprint(self.path))


class IncrementalReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AttemptExecutionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.path = self.fixture.path
        self.doc = json.loads(self.path.read_text())
        self.doc["unresolved"] = [company("builder.example"), company("untouched.example")]
        self.path.write_text(json.dumps(self.doc))

    def test_incremental_fact_and_judgment_preserve_unrelated_data(self):
        source = {"url": "https://builder.example/latest", "text": "Project still not dated"}
        review = {"companies": [{"scope": "builder.example", "company": {"hq_country": "Singapore"},
            "qualification_checks": [{"criterion": "recent_intent", "importance": "required", "status": "unknown",
                "claim": "The project exists but its date remains unknown", "evidence": [source]}]}]}
        runner.save_review(self.path, review)
        saved = json.loads(self.path.read_text())
        row = next(r for r in saved["unresolved"] if r["candidate"]["domain"] == "builder.example")
        self.assertEqual(row["candidate"]["employee_range"], "1-10")
        self.assertEqual(row["qualification_checks"][0], self.doc["unresolved"][0]["qualification_checks"][0])
        self.assertEqual(saved["request"], self.doc["request"])
        self.assertIn(company("untouched.example"), saved["unresolved"])
        runner.save_review(self.path, review)
        self.assertEqual(json.loads(self.path.read_text()), saved)
        review["companies"][0]["qualification_checks"][0].update(status="fail", claim="The documented date is outside the window",
            evidence=[{"url": "https://builder.example/dated", "text": "The project was completed in 2019"}])
        review["companies"][0].update(state="rejected", reason_text="Dated evidence is outside the required window")
        runner.save_review(self.path, review)
        row = json.loads(self.path.read_text())["rejected"][0]
        self.assertEqual(row["reason_code"], "not_icp_fit")
        self.assertEqual(row["qualification_checks"][1]["evidence"], review["companies"][0]["qualification_checks"][0]["evidence"])
        self.assertEqual(row["candidate"]["hq_country"], "Singapore")

    def test_criterion_variants_replace_one_judgment_and_remove_omitted_signal(self):
        check = {"criterion": "  Buyer   Hiring ", "importance": "preferred", "status": "unknown",
                 "signal": "HIRING", "claim": "No dated opening verified", "evidence": []}
        runner.save_review(self.path, {"companies": [{"scope": "builder.example", "qualification_checks": [check]}]})
        check.pop("signal")
        check.update(criterion="BUYER hiring", status="pass", claim="An operations role is open",
                     evidence=[{"url": "https://builder.example/careers", "text": "Operations role open"}])
        for name in ("BUYER hiring", "buyer hiring", " buyer   hiring "):
            check["criterion"] = name
            runner.save_review(self.path, {"companies": [{"scope": "builder.example", "qualification_checks": [check]}]})
        row = next(r for r in json.loads(self.path.read_text())["unresolved"] if r["candidate"]["domain"] == "builder.example")
        self.assertEqual(len(row["qualification_checks"]), 3)
        saved = row["qualification_checks"][-1]
        self.assertEqual((saved["criterion"], saved["status"]), ("buyer hiring", "pass"))
        self.assertNotIn("signal", saved)
        self.assertEqual(saved["evidence"], check["evidence"])
        check.update(criterion="buyer hiring", signal="MARKET_EXPANSION")
        runner.save_review(self.path, {"companies": [{"scope": "builder.example", "qualification_checks": [check]}]})
        row = next(r for r in json.loads(self.path.read_text())["unresolved"] if r["candidate"]["domain"] == "builder.example")
        self.assertEqual(row["qualification_checks"][-1]["signal"], "MARKET_EXPANSION")

    def test_duplicate_criterion_updates_and_ambiguous_saved_checks_are_atomic(self):
        check = {"criterion": "recent_intent", "importance": "required", "status": "unknown",
                 "claim": "The date remains unknown", "evidence": []}
        for existing_duplicates in (False, True):
            with self.subTest(existing_duplicates=existing_duplicates):
                doc = copy.deepcopy(self.doc)
                if existing_duplicates:
                    doc["unresolved"][0]["qualification_checks"].append(dict(check, criterion=" RECENT_INTENT "))
                self.path.write_text(json.dumps(doc))
                before, ledger = self.path.read_bytes(), guard.ledger_path(self.path).read_bytes()
                incoming = [check] if existing_duplicates else [check, dict(check, criterion=" RECENT_INTENT ")]
                with self.assertRaisesRegex(ValueError, "duplicate|multiple"):
                    runner.save_review(self.path, {"companies": [{"scope": "builder.example", "company": {"hq_country": "Singapore"},
                                                                  "qualification_checks": incoming}]})
                self.assertEqual(self.path.read_bytes(), before)
                self.assertEqual(guard.ledger_path(self.path).read_bytes(), ledger)

    def test_identity_change_unsupported_rejection_and_incomplete_acceptance_are_atomic(self):
        before, ledger = self.path.read_bytes(), guard.ledger_path(self.path).read_bytes()
        for update in ({"company": {"domain": "other.example"}}, {"state": "accepted"},
                       {"state": "rejected", "reason_text": "Not found"}, {"state": "arbitrary"}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                runner.save_review(self.path, {"companies": [{"scope": "builder.example", **update}]})
            self.assertEqual(self.path.read_bytes(), before)
            self.assertEqual(guard.ledger_path(self.path).read_bytes(), ledger)


class CombinedWebReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AttemptExecutionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.path = self.fixture.path

    def review(self, rid="web"):
        return {"companies": [{"scope": "builder.example", "reason_text": "Recent date remains unverified",
            "qualification_checks": [{"criterion": "recent_intent", "importance": "required", "status": "unknown",
                "claim": "A project is described without a verified date", "evidence": []}]}],
            "routes": [{"route_id": rid, "reason": "Reviewed the observed project page",
                "response": {"status": "ok", "results": [{"url": "https://builder.example/news", "text": "Project described"}]}}]}

    def test_stdin_lookup_then_combined_review_persists_without_input_files(self):
        command = [sys.executable, runner.__file__, str(self.path)]
        request = {"provider": "public_web", "scope": "builder.example", "phase": "account_verification",
            "purpose": "Review the dated project", "request": {"operation": "open", "url": "https://builder.example/news"}}
        before_files = set(self.path.parent.iterdir())
        subprocess.run(command + ["--lookup-file", "-", "--plan-only"], input=json.dumps(request), text=True, capture_output=True, check=True)
        rid = json.loads(self.path.read_text())["stop_audit"]["route_frontier"][-1]["route_id"]
        receipt = self.path.parent / "receipts" / (rid + ".json")
        metadata = json.loads(receipt.read_text())
        ledger = guard.ledger_path(self.path).read_bytes()
        result = subprocess.run(command + ["--review-file", "-"], input=json.dumps(self.review(rid)), text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout)["review_due"]["count"], 0)
        doc = json.loads(self.path.read_text())
        self.assertEqual(len(doc["unresolved"]), 1)
        self.assertEqual(doc["stop_audit"]["route_frontier"][-1]["state"], "exhausted")
        saved = json.loads(receipt.read_text())
        self.assertEqual(saved["results"], self.review(rid)["routes"][0]["response"]["results"])
        for key in ("attempt", "request_fingerprint", "run_fingerprint", "accepted_before", "progress_before"):
            self.assertEqual(saved[key], metadata[key])
        self.assertEqual(guard.ledger_path(self.path).read_bytes(), ledger)
        self.assertEqual(set(self.path.parent.iterdir()) - before_files, {self.path.parent / "receipts"})
        original = json.loads(self.path.read_text()), receipt.read_bytes()
        subprocess.run(command + ["--review-file", "-"], input=json.dumps(self.review(rid)), text=True, capture_output=True, check=True)
        self.assertEqual((json.loads(self.path.read_text()), receipt.read_bytes()), original)

    def test_invalid_review_retains_observation_for_safe_retry(self):
        receipt = self.fixture.plan_web()
        bad = self.review()
        bad["companies"][0]["state"] = "accepted"  # Incomplete contact/evidence fails the existing gate.
        ledger = guard.ledger_path(self.path).read_bytes()
        with self.assertRaises(ValueError):
            runner.save_review(self.path, bad)
        self.assertEqual(json.loads(self.path.read_text())["accepted"], [])
        saved = receipt.read_bytes()
        self.assertEqual(json.loads(saved)["status"], "ok")
        runner.save_review(self.path, self.review())
        self.assertEqual(receipt.read_bytes(), saved)
        self.assertEqual(guard.ledger_path(self.path).read_bytes(), ledger)

    def test_all_observations_are_checked_before_writing_and_paid_receipts_are_protected(self):
        first = self.fixture.plan_web("first")
        spec = self.fixture.spec("second")
        spec["action"]["provider"] = "public_web"
        spec["request"] = {"operation": "search", "query": "Another source"}
        second = Path(runner.run_attempt(self.path, spec, plan_only=True)["receipt_file"])
        review = self.review("first")
        review["routes"].extend(self.review("second")["routes"])
        for extra in ({"run_fingerprint": "other-run"}, {"operation": "wrong"}):
            bad = copy.deepcopy(review)
            bad["routes"][1]["response"].update(extra)
            original = self.path.read_bytes(), first.read_bytes(), second.read_bytes(), guard.ledger_path(self.path).read_bytes()
            with self.subTest(extra=extra), self.assertRaisesRegex(ValueError, r"review.routes\[1\].response"):
                runner.save_review(self.path, bad)
            self.assertEqual((self.path.read_bytes(), first.read_bytes(), second.read_bytes(), guard.ledger_path(self.path).read_bytes()), original)
        runner.run_attempt(self.path, self.fixture.spec("paid", paid=True), execute=self.fixture.paid_response)
        paid = self.path.parent / "receipts/paid.json"
        before, ledger = paid.read_bytes(), guard.ledger_path(self.path).read_bytes()
        with self.assertRaisesRegex(ValueError, "only for planned public-web"):
            runner.save_review(self.path, self.review("paid"))
        self.assertEqual(paid.read_bytes(), before)
        self.assertEqual(guard.ledger_path(self.path).read_bytes(), ledger)

    def test_interrupted_recording_recovers_from_same_combined_review(self):
        receipt = self.fixture.plan_web()
        with patch.object(runner, "finish_attempt", side_effect=OSError("interrupted")), self.assertRaises(OSError):
            runner.save_review(self.path, self.review())
        saved = receipt.read_bytes()
        self.assertEqual(json.loads(self.path.read_text())["routes"], [])
        runner.save_review(self.path, self.review())
        self.assertEqual(receipt.read_bytes(), saved)
        self.assertEqual(len(json.loads(self.path.read_text())["routes"]), 1)

    def test_missing_input_file_explains_direct_input(self):
        result = subprocess.run([sys.executable, runner.__file__, str(self.path), "--lookup-file", str(self.path.parent / "missing.json")],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Input file does not exist", result.stderr)
        self.assertIn("JSON on stdin", result.stderr)


class SavedWorkbookJourneyTests(unittest.TestCase):
    def test_start_lookup_incremental_review_and_saved_workbook(self):
        """Use only the public helpers, with captured synthetic provider responses."""
        from test_client_output import client_document
        from test_export_xlsx import EXPORTER_PATH, read_first_sheet_rows
        import deepline
        node, modules = os.environ.get("TYCHE_WORKSPACE_NODE"), os.environ.get("TYCHE_WORKSPACE_NODE_MODULES")
        if not node or not modules:
            self.skipTest("Codex workbook runtime is not configured")
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "results.json"
        sample = client_document()
        request = sample["request"]
        request["buying_signals"] = [{"kind": "PRODUCT_LAUNCH", "importance": "required", "query": "Recent operational integration"},
                                    {"kind": "HIRING", "importance": "preferred", "query": "Preferred operations hiring", "max_age_days": 90}]
        runner.start_run(path, {"request": request, "verification_reserve_credits": 0.1})
        row = sample["accepted"][0]
        # Facts are fixtures; no live research or provider is called in this test.
        runner.run_lookup(path, {"provider": "public_web", "scope": "example.com", "phase": "account_verification",
            "purpose": "Review the business and dated integration", "request": {"operation": "open", "url": "https://example.com/news"}}, plan_only=True)
        rid = json.loads(path.read_text())["stop_audit"]["route_frontier"][-1]["route_id"]
        observed = {"status": "ok", "results": [{"url": "https://example.com/news",
            "text": row["account_fit"]["evidence_text"] + " " + row["signal_evidence"]["evidence_text"]}]}
        for evidence in (row["account_fit"], row["signal_evidence"], row["primary_contact"]):
            evidence["source"] = {"provider": "public_web", "operation": "open", "route_id": rid}
        runner.save_review(path, {"companies": [{"scope": "example.com", "company": row["company"],
            "reason_text": "Company evidence reviewed; contact checks pending"}],
            "routes": [{"route_id": rid, "response": observed, "reason": "Reviewed factual business and integration evidence"}]})

        # Replace an unresolved observation with selected current evidence.
        # Each replacement judgment states its current signal classification.
        observations = [("archive", "unknown", "Archived operations vacancy; current hiring unverified."),
                        ("careers", "pass", "An operations manager position is open on August 20, 2026.")]
        runner.run_lookup(path, {"provider": "public_web", "scope": "example.com", "phase": "account_verification",
            "purpose": "Review operations hiring", "request": {"operation": "search_query", "query": "Example Products operations jobs"}}, plan_only=True)
        source_id = json.loads(path.read_text())["stop_audit"]["route_frontier"][-1]["route_id"]
        runner.complete_public_web(path, source_id, {"status": "ok", "results": [
            {"url": "https://example.com/" + slug, "text": facts} for slug, _, facts in observations]})
        receipt_path = path.parent / "receipts" / (source_id + ".json")
        original_receipt = receipt_path.read_bytes()
        for slug, status, facts in observations:
            url = "https://example.com/" + slug
            evidence = {"url": url, "date": "2026-08-20", "date_basis": "observed_current", "text": facts,
                        "source": {"provider": "public_web", "operation": "search_query", "route_id": source_id}}
            check = {"criterion": "hiring" if status == "unknown" else " HIRING ", "importance": "preferred",
                     "status": status, "claim": facts, "evidence": [evidence]}
            check["signal"] = "HIRING"
            runner.save_review(path, {"companies": [{"scope": "example.com", "qualification_checks": [check]}],
                "routes": [{"route_id": source_id, "reason": "Reviewed the hiring observation"}]})
        row["intent_details"] = (
            "Example Products connected its acquired warehouse to a shared WMS on August 12, 2026. "
            "The integration helps coordinate inventory and fulfillment across its warehouses. "
            "An operations manager position was open on August 20, 2026. "
            "That hiring suggests a need for operational capacity as the shared system is adopted. "
            "Together, the integration and hiring make warehouse coordination relevant to its consumer-products business now."
        )

        def describe(request, capture):
            return {"provider": "deepline", "operation": "describe", "tool": request["tool"], "status": "ok",
                    "results": [{"toolId": request["tool"], "connected": True, "callable": True,
                                 "inputSchema": {"fields": []}}]}, 0
        tools = ["harvestapi_get_company", "harvestapi_get_profile", "zerobounce_validate"]
        runner.run_lookup(path, [{"request": {"operation": "describe", "tool": tool}} for tool in tools], execute=describe)

        def perform(tool, phase, raw, payload):
            def execute(request, capture):
                def send():
                    response = {"exit_code": 0, "body": {"status": "ok", "element": raw}, "stderr": ""}
                    capture(response)
                    body, code = deepline.normalize_response(request, response)
                    body["billing"] = {"credits_charged": 0.03, "cost_usd": 0.003}
                    return body, code
                return guard.guarded_call(request, "deepline", send)
            result = runner.run_lookup(path, {"scope": "example.com", "phase": phase, "purpose": "Verify " + tool,
                "max_cost_credits": 0.03, "request": {"operation": "execute", "tool": tool, "payload": payload}}, execute=execute)
            self.assertEqual(result["exit_code"], 0, result)
            saved = json.loads(path.read_text())["routes"][-1]
            return {k: saved[k] for k in ("provider", "operation", "tool", "route_id")}

        company = row["company"]
        size = company["employee_range_evidence"]
        size["source"] = perform(tools[0], "account_verification", {"name": company["canonical_name"],
            "linkedinUrl": size["evidence_url"], "employeeCountRange": {"start": 201, "end": 500}}, {"url": size["evidence_url"]})
        runner.save_review(path, {"companies": [{"scope": "example.com", "stage": "contact",
            "company": {"employee_range_evidence": size}, "account_fit": row["account_fit"],
            "qualification_checks": [{"criterion": "product launch", "signal": "PRODUCT_LAUNCH", "status": "pass",
                "claim": "Recent integration verified", "evidence": [{
                    **{key: row["signal_evidence"]["evidence_" + key] for key in ("url", "date", "date_basis", "text")},
                    "source": row["signal_evidence"]["source"]}]}], "intent_details": row["intent_details"],
            "reason_text": "Business and signal reviewed; verifying current contact details"}],
            "routes": [{"route_id": size["source"]["route_id"], "reason": "Reviewed published LinkedIn size"}]})

        contact = row["primary_contact"]
        location = contact["location_evidence"]
        location["source"] = perform(tools[1], "contact_verification", {"fullName": contact["full_name"],
            "currentPosition": [{"companyName": company["canonical_name"], "title": contact["current_title"],
                                 "companyLinkedinUrl": company["linkedin_url"]}],
            "linkedinUrl": location["evidence_url"], "location": {"linkedinText": location["evidence_text"],
                "parsed": {"countryFull": contact["country"], "state": contact.get("state"), "city": contact.get("city")}}},
            {"url": location["evidence_url"]})
        runner.save_review(path, {"companies": [{"scope": "example.com", "stage": "contact",
            "primary_contact": {k: v for k, v in contact.items() if k not in {"email", "email_validation"}},
            "reason_text": "Current LinkedIn identity, employer and requested role reviewed before email work"}]})
        email_source = perform(tools[2], "email_validation", {"email": contact["email"], "status": "valid", "sub_status": "catch_all"},
                               {"email": contact["email"]})
        contact.pop("email_validation")  # Existing review fills this from the original response.
        runner.save_review(path, {"companies": [{"scope": "example.com", "state": "accepted", "primary_contact": contact}],
            "routes": [{"route_id": source["route_id"], "reason": "Reviewed exact contact evidence"}
                       for source in (location["source"], email_source)]})
        before = json.loads(path.read_text())
        self.assertEqual(before["accepted"][0]["primary_contact"]["email_validation"]["status"], "valid")
        ledger = guard.ledger_path(path).read_bytes()
        exported = subprocess.run([node, str(EXPORTER_PATH), str(path)], text=True, capture_output=True, timeout=60)
        self.assertEqual(exported.returncode, 0, exported.stderr)
        self.assertTrue(json.loads((path.parent / "validation.json").read_text())["delivery_allowed"])
        rows = read_first_sheet_rows(path.parent / "leads.xlsx")
        for header, value in (("Description", company["description"]), ("Intent Details", row["intent_details"]),
                              ("Contact Country", contact["country"]), ("Company Employee Range", company["employee_range"])):
            self.assertEqual(rows[1][rows[0].index(header)], value)
        self.assertNotIn("Intent Signal", rows[0])
        signals = rows[1][rows[0].index("Signals")]
        self.assertIn("HIRING\nObserved on: 2026-08-20", signals)
        self.assertIn(observations[1][2], signals)
        self.assertIn("https://example.com/careers", signals)
        self.assertNotIn(observations[0][2], signals)
        self.assertNotIn("https://example.com/archive", signals)
        self.assertEqual(before["accepted"][0]["qualification_checks"][0]["evidence"], [evidence])
        self.assertEqual(receipt_path.read_bytes(), original_receipt)
        self.assertIn(observations[0][2], original_receipt.decode())
        self.assertEqual(json.loads(path.read_text())["accepted"], before["accepted"])
        self.assertEqual(guard.ledger_path(path).read_bytes(), ledger)


if __name__ == "__main__":
    unittest.main()
