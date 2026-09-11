import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
import sys
from unittest.mock import Mock, patch

from test_stop_policy import NOW, action, stop_document
from test_output_contract import VALIDATOR
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import budget_guard
import run_attempt as runner


def reviewed(document):
    scopes = {"discovery"} | {VALIDATOR._company_key(r) for r in document.get("unresolved", [])
                              if r.get("stage") in {"account", "contact"}}
    for scope in scopes - {None}:
        rid = "catalog-" + scope
        document["routes"].append(dict(route_id=rid, provider="deepline", operation="search",
            scope=scope, entity_type="tool_catalog", provider_status="ok", paid_calls=0,
            cost_basis="actual", cost_credits=0, cost_upper_bound_credits=0))
        document["stop_check"].setdefault("catalog_review_route_ids", []).append(rid)
    return document


class PersistencePolicyTests(unittest.TestCase):
    def test_saved_failed_run_cannot_pass_stop_check(self):
        path = Path(__file__).parent / "fixtures" / "tablecloth_premature_stop.json"
        doc = json.loads(path.read_text())
        before = copy.deepcopy(doc)
        result = VALIDATOR.evaluate_stop(doc)
        self.assertEqual(result["decision"], "repair_state")
        self.assertTrue(any("provider and scope" in e or "recovered" in e for e in result["errors"]))
        self.assertEqual(doc, before)

    def test_matching_but_recovered_error_cannot_stop(self):
        doc = stop_document([action("blocked", provider="deepline", paid_calls=1, cost_upper_bound_credits=1,
            blocker={"kind": "access_unavailable", "reason": "error", "evidence_route_id": "bad"})], routes=[
            dict(route_id="bad", scope="discovery", provider="deepline", operation="company_search", provider_status="provider_error", paid_calls=0),
            dict(route_id="fixed", scope="discovery", provider="deepline", operation="company_search", provider_status="ok", paid_calls=0)])
        self.assertIn("recovered error", " ".join(VALIDATOR.evaluate_stop(doc, now=NOW)["errors"]))

    def test_recovered_predispatch_outcome_cannot_stop(self):
        next_action = action("blocked", provider="deepline", blocker={
            "kind": "access_unavailable", "reason": "network denied", "evidence_route_id": "denied"})
        doc = stop_document([next_action], routes=[dict(route_id="recovered", scope="discovery",
            provider="deepline", operation="company_search", provider_status="ok", paid_calls=0)])
        doc["unresolved"] = [dict(stage="route", route_id="denied", scope="discovery",
            provider="deepline", operation="company_search", provider_status="provider_error")]
        doc["stop_audit"] = {"route_frontier": [{"route_id": "denied"}, {"route_id": "recovered"}]}
        self.assertEqual(VALIDATOR._blocker_error(next_action, doc), "recovered error cannot justify stopping")
        doc["stop_audit"]["route_frontier"].reverse()
        self.assertIsNone(VALIDATOR._blocker_error(next_action, doc))

    def test_shortfall_needs_current_catalog_review(self):
        doc = stop_document([action("too-costly", provider="deepline", paid_calls=1, cost_upper_bound_credits=20)])
        self.assertEqual(VALIDATOR.evaluate_stop(doc, now=NOW)["catalog_review_required"], ["discovery"])
        reviewed(doc)
        self.assertEqual(VALIDATOR.evaluate_stop(doc, now=NOW)["decision"], "budget_exhausted")
        doc["routes"].append(dict(route_id="another-attempt", provider="public_web", paid_calls=0))
        self.assertEqual(VALIDATOR.evaluate_stop(doc, now=NOW)["decision"], "continue")

    def test_catalog_outage_does_not_require_an_impossible_successful_refresh(self):
        doc = reviewed(stop_document([action("blocked", provider="deepline", paid_calls=0,
            blocker={"kind": "access_unavailable", "reason": "catalog access denied",
                     "evidence_route_id": "catalog-discovery"})]))
        doc["routes"][-1]["provider_status"] = "auth_failed"
        self.assertEqual(VALIDATOR.evaluate_stop(doc, now=NOW)["decision"], "provider_stop")
        doc["stop_check"]["next_actions"].append(action("public-alternative"))
        decision = VALIDATOR.evaluate_stop(doc, now=NOW)
        self.assertEqual(decision["decision"], "continue")
        self.assertIn("public-alternative", decision["eligible_actions"])

    def test_run_wide_catalog_review_covers_multiple_company_recovery_plans(self):
        doc = reviewed(stop_document([]))
        self.assertEqual(VALIDATOR._missing_catalog_review(
            doc, {"discovery", "one.example", "two.example"}), [])

    def test_untried_research_cannot_be_hidden_by_unaffordable_database_action(self):
        doc = reviewed(stop_document([dict(action("database", provider="deepline", paid_calls=1,
            cost_upper_bound_credits=20), approach="database")]))
        doc["stop_audit"] = {"route_frontier": [dict(route_id="research", scope="discovery",
            approach="multilingual-web-research", state="untried")]}
        decision = VALIDATOR.evaluate_stop(doc, now=NOW)
        self.assertEqual(decision["decision"], "continue")
        self.assertEqual(decision["missing_routes"], ["research"])

    def test_stagnation_changes_approach_not_just_provider(self):
        doc = stop_document([dict(action("same"), approach="database"),
                             dict(action("changed"), approach="french-product-pages")])
        doc["routes"] = [dict(route_id=str(i), provider="public_web", paid_calls=0, rows_returned=10,
            provider_status="ok", approach="database", progress_before=[]) for i in range(2)]
        result = VALIDATOR.evaluate_stop(doc, now=NOW)
        self.assertTrue(result["strategy_change_required"])
        self.assertEqual(result["eligible_actions"], ["changed"])
        doc["unresolved"] = [dict(stage="account", candidate={"domain": "new.example"}, qualification_checks=[
            dict(criterion="custom cutting", importance="required", status="pass", evidence=[{"url": "https://new.example"}])])]
        self.assertFalse(VALIDATOR.stalled_approaches(doc))

    def test_unknown_is_unresolved_and_owner_aliases_are_excluded(self):
        row = dict(stage="account", candidate={"company": "New Shop", "domain": "new.example"},
                   reason_code="not_icp_fit", qualification_checks=[
                       dict(criterion="headcount", importance="required", status="unknown", evidence=[])])
        doc = stop_document([action("free")])
        doc["rejected"] = [row]
        self.assertTrue(VALIDATOR.qualification_errors(doc))
        doc["rejected"], doc["unresolved"] = [], [row]
        self.assertEqual(VALIDATOR.qualification_errors(doc), [])
        doc["request"]["icp"] = {"exclusions": ["Tissage de Luz"]}
        row["candidate"]["owner_group"] = "Tissage de Luz"
        self.assertTrue(VALIDATOR.excluded_company(doc["request"], row))


class AttemptExecutionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "results.json"
        self.doc = stop_document([], target_count=25)
        self.doc["request"]["contact_fields"] = []
        self.doc["stop_audit"] = {"route_frontier": []}
        self.path.write_text(json.dumps(self.doc))
        budget_guard.initialize(self.path, max_usd=2, scrapingdog_usd_per_credit=0.1)

    def spec(self, rid="one", query="custom tablecloths", approach="product-search", paid=False):
        return {"action": dict(action(rid, provider="deepline", paid_calls=int(paid),
                                      cost_upper_bound_credits=0.2 if paid else 0),
                               phase="account_discovery", approach=approach),
                "request": {"operation": "execute", "tool": "fixture-search", "payload": {"query": query}}
                    if paid else {"operation": "search", "query": query}}

    def free_response(self, request, capture):
        return {"provider": "deepline", "operation": "search", "status": "ok", "results": [{"tool": "fixture"}]}, 0

    def paid_response(self, request, capture):
        return budget_guard.guarded_call(request, "deepline", lambda: (
            {"provider": "deepline", "operation": "execute", "status": "no_results", "results": [],
             "billing": {"credits_charged": 0.1, "cost_usd": 0.01}}, 0))

    def test_review_demotion_keeps_receipts_and_executes_next_planned_action(self):
        self.doc["accepted"] = [{"company": {"domain": f"company-{i}.example"}} for i in range(7)]
        self.path.write_text(json.dumps(self.doc))
        runner.run_attempt(self.path, self.spec("before-review", paid=True), execute=self.paid_response)
        ledger_before = budget_guard.load_ledger(self.path)
        receipt_path = self.path.parent / "receipts/before-review.json"
        receipt_before = receipt_path.read_bytes()
        doc = json.loads(self.path.read_text())
        removed = doc["accepted"][1:]
        doc["accepted"] = doc["accepted"][:1]
        doc["unresolved"] = [dict(stage="account", candidate={"domain": r["company"]["domain"]},
            reason_code="missing_account_evidence", reason_text="Confirm this project's steel specification.")
            for r in removed]
        self.path.write_text(json.dumps(doc))
        next_spec = self.spec("after-review", query="different project evidence", paid=True)
        result = runner.run_attempt(self.path, next_spec, execute=self.paid_response)
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["provider_status"], "no_results")
        self.assertEqual(result["stop_decision"]["decision"], "continue")
        self.assertIn("company-1.example", result["stop_decision"]["missing_scopes"])
        after = budget_guard.load_ledger(self.path)
        self.assertEqual(after["calls"]["before-review"], ledger_before["calls"]["before-review"])
        self.assertEqual(after["calls"]["after-review"]["accepted_leads_before_call"], 1)
        self.assertEqual(receipt_path.read_bytes(), receipt_before)
        self.assertEqual(budget_guard.audit_ledger(self.path, json.loads(self.path.read_text())), [])

    def company_specs(self):
        specs = [self.spec(f"check-{i}", query=f"company-{i}.example", paid=True) for i in range(3)]
        for i, spec in enumerate(specs):
            spec["action"].update(phase="account_verification", scope=f"company-{i}.example")
        return specs

    def test_three_provider_calls_overlap_with_one_result_writer(self):
        gate = threading.Barrier(3, timeout=5)
        writers, providers = set(), set()
        mutate = runner.mutate

        def write(*args):
            writers.add(threading.get_ident())
            return mutate(*args)

        def execute(request, capture):
            def remote():
                providers.add(threading.get_ident())
                gate.wait()  # Fails if network work is serialized with the ledger.
                return {"status": "no_results", "results": [],
                        "billing": {"credits_charged": 0.1, "cost_usd": 0.01}}, 0
            return budget_guard.guarded_call(request, "deepline", remote)

        with patch.object(runner, "mutate", side_effect=write):
            result = runner.run_batch(self.path, self.company_specs(), execute=execute)
        self.assertEqual(result["exit_code"], 0, result)
        self.assertEqual(len(providers), 3)
        self.assertEqual(writers, {threading.get_ident()})
        doc = json.loads(self.path.read_text())
        self.assertEqual(len(doc["routes"]), 3)
        self.assertEqual(doc["stop_check"]["next_actions"], [])
        self.assertAlmostEqual(doc["budget"]["spent"]["deepline_credits"], 0.3)
        self.assertEqual(budget_guard.audit_ledger(self.path, doc), [])

    def test_batch_shape_and_dependent_checks_refused_before_any_dispatch(self):
        specs = self.company_specs()
        same_company, same_id, discovery, invalid = [copy.deepcopy(specs) for _ in range(4)]
        same_company[1]["action"].update(scope=specs[0]["action"]["scope"], phase="contact_discovery")
        same_id[1]["action"]["id"] = specs[0]["action"]["id"]
        discovery[1]["action"]["phase"] = "account_discovery"
        invalid[1]["action"]["scope"] = None
        before = self.path.read_bytes()
        execute = Mock()
        for batch in ([], specs + [specs[0]], same_company, same_id, discovery, invalid):
            with self.subTest(batch=batch), self.assertRaises(ValueError):
                runner.run_batch(self.path, batch, execute=execute)
        execute.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(budget_guard.load_ledger(self.path)["calls"], {})

    def test_pending_calls_cannot_spend_siblings_budget_or_verification_reserve(self):
        for name, limits, usd, verification in (
            ("credits", {"deepline_credits": 0.3}, 2, 0),
            ("dollars-and-verification", {}, 0.05, 0.2),
        ):
            with self.subTest(cap=name):
                folder = self.path.parent / name
                folder.mkdir()
                path = folder / "results.json"
                doc = copy.deepcopy(self.doc)
                doc["budget"]["limits"].update(limits)
                path.write_text(json.dumps(doc))
                budget_guard.initialize(path, max_usd=usd, scrapingdog_usd_per_credit=0.1,
                                        verification_reserve_credits=verification)
                ready, refused = threading.Barrier(3, timeout=5), threading.Barrier(3, timeout=5)
                sent = []

                def execute(request, capture):
                    ready.wait()
                    def remote():
                        sent.append(request["spend"]["route_id"])
                        refused.wait()  # Hold the reservation until both siblings are refused.
                        return {"status": "no_results", "results": [],
                                "billing": {"credits_charged": 0.1, "cost_usd": 0.01}}, 0
                    body, code = budget_guard.guarded_call(request, "deepline", remote)
                    if body.get("request_sent") is False:
                        refused.wait()
                    return body, code

                result = runner.run_batch(path, self.company_specs(), execute=execute)
                self.assertEqual(len(sent), 1, result)
                self.assertEqual(result["exit_code"], 2)
                self.assertEqual(sum(r.get("provider_status") == "quota_exceeded" for r in result["attempts"]), 2)
                doc = json.loads(path.read_text())
                self.assertEqual(doc["budget"]["paid_calls"], 1)
                self.assertEqual(len(doc["routes"]), 3)
                self.assertEqual(budget_guard.audit_ledger(path, doc), [])

    def test_failed_member_preserves_siblings_and_prevents_redispatch(self):
        def execute(request, capture):
            if request["spend"]["route_id"] == "check-1":
                budget_guard.reserve(request["spend"], "deepline")
                capture({"job_id": "uncertain-job"})
                raise RuntimeError("remote outcome unknown")
            return self.paid_response(request, capture)

        result = runner.run_batch(self.path, self.company_specs(), execute=execute)
        self.assertEqual(result["exit_code"], 2)
        self.assertEqual(result["attempts"][1]["error_stage"], "dispatch")
        self.assertEqual(len(json.loads(self.path.read_text())["routes"]), 2)
        ledger = budget_guard.load_ledger(self.path)
        self.assertEqual(len(ledger["calls"]), 3)
        self.assertIsNone(ledger["calls"]["check-1"]["actual_credits"])
        specs = self.company_specs()
        for spec in specs:
            spec["action"]["id"] += "-retry"
        replay = Mock()
        repeated = runner.run_batch(self.path, specs, execute=replay)
        replay.assert_not_called()
        self.assertEqual(repeated["exit_code"], 2)
        saved = json.loads(Path(result["attempts"][1]["receipt_file"]).read_text())
        self.assertEqual(saved["provider_response"]["job_id"], "uncertain-job")
        saved.update(status="timeout", results=[])
        runner.finish_attempt(self.path, "check-1", saved)
        self.assertEqual(budget_guard.load_ledger(self.path), ledger)
        self.assertEqual(budget_guard.audit_ledger(self.path, json.loads(self.path.read_text())), [])

    def test_record_failure_can_complete_without_repeating_successful_siblings(self):
        finish = runner.finish_attempt
        def interrupted(path, rid, body):
            if rid == "check-1":
                raise OSError("interrupted state write")
            return finish(path, rid, body)
        with patch.object(runner, "finish_attempt", side_effect=interrupted):
            result = runner.run_batch(self.path, self.company_specs(), execute=self.paid_response)
        self.assertEqual(result["exit_code"], 2)
        self.assertEqual(result["attempts"][1]["error_stage"], "record")
        self.assertEqual(len(json.loads(self.path.read_text())["routes"]), 2)
        ledger = budget_guard.load_ledger(self.path)
        saved = json.loads(Path(result["attempts"][1]["receipt_file"]).read_text())
        runner.finish_attempt(self.path, "check-1", saved)
        runner.finish_attempt(self.path, "check-1", saved)
        self.assertEqual(budget_guard.load_ledger(self.path), ledger)
        self.assertEqual(budget_guard.audit_ledger(self.path, json.loads(self.path.read_text())), [])

    def test_batch_keeps_company_qualification_gate_and_saves_other_checks(self):
        specs = self.company_specs()
        specs[1]["action"]["phase"] = "contact_discovery"
        result = runner.run_batch(self.path, specs, execute=self.paid_response)
        self.assertEqual(result["attempts"][1]["error_stage"], "prepare")
        self.assertIn("account-qualified", result["attempts"][1]["error"])
        self.assertEqual(len(budget_guard.load_ledger(self.path)["calls"]), 2)
        self.assertEqual(budget_guard.audit_ledger(self.path, json.loads(self.path.read_text())), [])

    def test_batch_cli_saves_actual_wrapper_receipts_and_passes_stop_check(self):
        stub = self.path.parent / "fake-deepline"
        stub.write_text(f"#!{sys.executable}\nimport json\n"
                        "print(json.dumps({'status': 'no_results', 'results': [], "
                        "'billing': {'credits_charged': 0.1, 'cost_usd': 0.01}}))\n")
        stub.chmod(0o700)
        files = []
        for spec in self.company_specs():
            path = self.path.parent / (spec["action"]["id"] + ".json")
            path.write_text(json.dumps(spec))
            files.append(str(path))
        result = subprocess.run([sys.executable, runner.__file__, str(self.path), "--batch-files", *files],
                                env=dict(os.environ, DEEPLINE_BIN=str(stub)), capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        attempts = json.loads(result.stdout)["attempts"]
        self.assertEqual(len(attempts), 3)
        for attempt in attempts:
            receipt = json.loads(Path(attempt["receipt_file"]).read_text())
            self.assertEqual(receipt["receipt_status"], "complete")
            self.assertEqual(receipt["spend_receipt"]["state"], "settled")
        checked = subprocess.run([sys.executable, str(Path(runner.__file__).with_name("validate_run.py")),
                                  str(self.path), "--check-stop"], capture_output=True, text=True, timeout=15)
        self.assertEqual(checked.returncode, 0, checked.stderr + checked.stdout)
        self.assertFalse(json.loads(checked.stdout)["delivery_allowed"])

    def test_public_web_batch_plans_then_completes_without_provider_dispatch(self):
        specs = self.company_specs()
        for spec in specs:
            spec["action"].update(provider="public_web", paid_calls=0, cost_upper_bound_credits=0)
            spec["request"] = {"operation": "search", "query": spec["action"]["scope"]}
        execute = Mock()
        result = runner.run_batch(self.path, specs, execute=execute, plan_only=True)
        execute.assert_not_called()
        self.assertEqual(result["exit_code"], 0)
        for attempt in result["attempts"]:
            saved = json.loads(Path(attempt["receipt_file"]).read_text())
            saved.update(status="ok", operation="search", results=[{"url": "https://example.org"}])
            runner.finish_attempt(self.path, attempt["route_id"], saved)
        self.assertEqual(len(json.loads(self.path.read_text())["routes"]), 3)
        self.assertEqual(budget_guard.load_ledger(self.path)["calls"], {})

    def test_records_receipt_cost_and_retires_action(self):
        result = runner.run_attempt(self.path, self.spec(paid=True), execute=self.paid_response)
        doc = json.loads(self.path.read_text())
        self.assertEqual(doc["budget"]["spent"]["deepline_credits"], 0.1)
        self.assertEqual(doc["routes"][0]["rows_usable"], 0)
        self.assertEqual(doc["routes"][0]["progress_before"], [])
        self.assertEqual(doc["stop_check"]["next_actions"], [])
        self.assertTrue(Path(result["receipt_file"]).exists())
        self.assertEqual(result["stop_decision"]["decision"], "continue")

    def test_invalid_state_update_preserves_the_saved_run(self):
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "original file preserved"):
            runner.mutate(self.path, lambda d: d.update(summary={}))
        self.assertEqual(self.path.read_bytes(), before)

    def test_refresh_derives_counts_and_preserves_unresolved_work(self):
        doc = copy.deepcopy(self.doc)
        doc["accepted"] = [{"company": {"domain": "one.example"}, "backup_contacts": [{}]}]
        doc["rejected"] = [{"stage": "account", "candidate": {"domain": "excluded.example"},
                            "reason_code": "explicit_exclusion"}]
        doc["unresolved"] = [{"stage": "account", "candidate": {"domain": "unknown.example"},
                              "reason_code": "missing_evidence"}]
        doc["routes"] = [{"provider": "deepline", "paid_calls": 1, "cost_credits": None}]
        doc["stop_audit"].update(frontier_complete=False, provider_call_capacity={"deepline": "available"})
        before = copy.deepcopy(doc)
        runner.refresh(doc)
        self.assertEqual(doc["summary"], dict(target_count=25, accepted_companies=1, accepted_contacts=1,
                                             backup_contacts=1, rejected_rows=1, unresolved_rows=1))
        self.assertEqual(doc["stop_audit"]["target_shortfall"], 24)
        self.assertEqual(doc["stop_audit"]["candidate_companies_reviewed"], 3)
        self.assertEqual(doc["stop_audit"]["substantive_account_reviews"], 2)
        self.assertEqual(doc["stop_audit"]["exclusion_only_rejections"], 1)
        self.assertEqual(doc["stop_audit"]["provider_call_capacity"]["deepline"], "unknown")
        self.assertFalse(doc["stop_audit"]["frontier_complete"])
        for field in ("accepted", "rejected", "unresolved", "routes", "stop_check"):
            self.assertEqual(doc[field], before[field])

    def test_duplicate_under_new_id_is_not_dispatched(self):
        runner.run_attempt(self.path, self.spec(paid=True), execute=self.paid_response)
        execute = Mock()
        with self.assertRaisesRegex(ValueError, "already attempted"):
            runner.run_attempt(self.path, self.spec("two", paid=True), execute=execute)
        execute.assert_not_called()

    def test_budget_refusal_happens_before_execution(self):
        spec = self.spec(paid=True)
        spec["action"]["cost_upper_bound_credits"] = 50
        execute = Mock()
        with self.assertRaisesRegex(ValueError, "not eligible"):
            runner.run_attempt(self.path, spec, execute=execute)
        execute.assert_not_called()
        self.assertFalse(budget_guard.load_ledger(self.path)["calls"])

    def test_resume_after_saved_response_does_not_charge_twice(self):
        from unittest.mock import patch
        with patch.object(runner, "finish_attempt", side_effect=OSError("simulated interruption")):
            with self.assertRaises(OSError):
                runner.run_attempt(self.path, self.spec(paid=True), execute=self.paid_response)
        before = budget_guard.load_ledger(self.path)
        saved = json.loads((self.path.parent / "receipts/one.json").read_text())
        runner.finish_attempt(self.path, "one", saved)
        runner.finish_attempt(self.path, "one", saved)
        self.assertEqual(budget_guard.load_ledger(self.path), before)
        self.assertEqual(len(json.loads(self.path.read_text())["routes"]), 1)

    def test_pending_billed_call_cannot_repeat(self):
        def interrupted(request, capture):
            budget_guard.reserve(request["spend"], "deepline")
            capture({"job_id": "pending-job"})
            raise OSError("remote outcome unknown")
        with self.assertRaises(OSError):
            runner.run_attempt(self.path, self.spec(paid=True), execute=interrupted)
        with self.assertRaisesRegex(ValueError, "already attempted"):
            runner.run_attempt(self.path, self.spec("new-id", paid=True), execute=Mock())
        self.assertEqual(len(budget_guard.load_ledger(self.path)["calls"]), 1)
        saved = json.loads((self.path.parent / "receipts/one.json").read_text())
        self.assertEqual(saved["provider_response"], {"job_id": "pending-job"})
        self.assertEqual(saved["progress_before"], [])
        self.assertEqual(len(saved["request_fingerprint"]), 64)
        self.assertEqual(saved["attempt"]["action"]["scope"], "discovery")
        self.assertEqual(saved["attempt"]["action"]["approach"], "product-search")
        self.assertEqual(saved["attempt"]["request"]["payload"], {"query": "custom tablecloths"})
        self.assertNotIn("spend", saved["attempt"]["request"])

    def test_public_web_planning_and_recording_do_not_call_a_provider(self):
        spec = self.spec()
        spec["action"]["provider"] = "public_web"
        spec["request"] = {"operation": "search", "query": "custom tablecloths"}
        execute = Mock()
        result = runner.run_attempt(self.path, spec, execute=execute, plan_only=True)
        execute.assert_not_called()
        saved = json.loads(Path(result["receipt_file"]).read_text())
        saved.update(status="ok", operation="search", results=[{"url": "https://example.org"}])
        runner.finish_attempt(self.path, "one", saved)
        self.assertEqual(json.loads(self.path.read_text())["routes"][0]["rows_returned"], 1)
        self.assertEqual(budget_guard.load_ledger(self.path)["calls"], {})

    def test_excluded_company_blocked_before_contact_spend(self):
        self.doc["request"]["icp"] = {"exclusions": ["Tissage de Luz"]}
        self.doc["unresolved"] = [dict(stage="contact", candidate={"domain": "tissagedeluz.com", "company": "Tissage de Luz"},
            account_fit={"evidence_url": "https://tissagedeluz.com", "evidence_text": "Custom tablecloths"})]
        self.path.write_text(json.dumps(self.doc))
        spec = self.spec(paid=True)
        spec["action"].update(scope="tissagedeluz.com", phase="contact_discovery")
        execute = Mock()
        with self.assertRaisesRegex(ValueError, "excluded"):
            runner.run_attempt(self.path, spec, execute=execute)
        execute.assert_not_called()

    def test_catalog_does_not_count_as_a_sourcing_batch(self):
        runner.run_attempt(self.path, self.spec(), execute=self.free_response)
        runner.run_attempt(self.path, self.spec("two", query="different capabilities"), execute=self.free_response)
        doc = json.loads(self.path.read_text())
        self.assertEqual(doc["stop_check"]["catalog_review_route_ids"], ["one", "two"])
        self.assertFalse(VALIDATOR.stalled_approaches(doc))

    def test_provider_execution_cannot_be_mislabeled_as_catalog(self):
        spec = self.spec(paid=True)
        spec["action"]["entity_type"] = "tool_catalog"
        execute = Mock()
        with self.assertRaisesRegex(ValueError, "reserved for live catalog"):
            runner.run_attempt(self.path, spec, execute=execute)
        execute.assert_not_called()

    def test_only_confirmed_free_pending_status_reads_can_repeat(self):
        spec = self.spec(paid=True)
        spec["action"].update(status_read=True, cost_upper_bound_credits=0)
        spec["request"].update(tool="fixture-get-job", payload={"id": "existing-job"})

        def pending(request, capture):
            return budget_guard.guarded_call(request, "deepline", lambda: (
                {"provider": "deepline", "status": "partial", "results": [],
                 "billing": {"credits_charged": 0, "cost_usd": 0}}, 0))

        runner.run_attempt(self.path, spec, execute=pending)
        spec["action"]["id"] = "second-read"
        runner.run_attempt(self.path, spec, execute=pending)
        self.assertEqual(len(budget_guard.load_ledger(self.path)["calls"]), 2)
        spec["action"].update(id="paid-repeat", cost_upper_bound_credits=1)
        with self.assertRaisesRegex(ValueError, "free Deepline job-status"):
            runner.run_attempt(self.path, spec, execute=Mock())

    def test_status_read_flag_cannot_repeat_a_job_submission(self):
        spec = self.spec(paid=True)
        runner.run_attempt(self.path, spec, execute=self.paid_response)
        spec["action"].update(id="not-a-getter", status_read=True, cost_upper_bound_credits=0)
        with self.assertRaisesRegex(ValueError, "already attempted"):
            runner.run_attempt(self.path, spec, execute=Mock())


if __name__ == "__main__":
    unittest.main()
