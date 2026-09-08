import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from test_provider_scripts import ROOT, load_script
from budget_guard import BudgetError, audit_ledger, initialize, ledger_path, read_object, reserve, settle
from test_stop_policy import NOW, action, stop_document
from test_output_contract import VALIDATOR, cost_result


DEEPLINE = load_script("deepline", budgeted=False)
SCRAPINGDOG = load_script("scrapingdog", budgeted=False)


class BudgetGuardTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "results.json"
        self.document = {"request": {"target_count": 10, "contact_fields": []},
                         "accepted": [], "routes": [], "budget": {"paid_calls": 0, "limits": {
                             "deepline_credits": 50, "scrapingdog_credits": 50, "max_paid_calls": 100}}}
        self.write()

    def write(self):
        self.path.write_text(json.dumps(self.document), encoding="utf-8")

    def init(self, **options):
        return initialize(self.path, scrapingdog_usd_per_credit=0.1, **options)

    def spend(self, route="one", cost=30):
        return {"run_file": str(self.path), "route_id": route, "max_cost_credits": cost}

    def request(self, **spend):
        return {"operation": "execute", "tool": "fixture", "payload": {"size": 1},
                "spend": self.spend(**spend)}

    def test_shared_default_five_dollars_includes_both_providers_and_pending_calls(self):
        self.init()
        reserve(self.spend(cost=30), "deepline")
        with self.assertRaisesRegex(BudgetError, "shared USD cap"):
            reserve(self.spend("two", 21), "scrapingdog")
        reserve(self.spend("three", 20), "scrapingdog")
        self.assertEqual(len(read_object(ledger_path(self.path))["calls"]), 2)

    def test_zero_cap_disables_provider_even_for_zero_cost_call(self):
        self.document["budget"]["limits"]["deepline_credits"] = 0
        self.write()
        self.init()
        with self.assertRaisesRegex(BudgetError, "disabled"):
            reserve(self.spend(cost=0), "deepline")

    def test_explicit_dollar_and_paid_call_caps(self):
        self.document["budget"]["limits"]["max_paid_calls"] = 1
        self.write()
        self.init(max_usd=1)
        with self.assertRaisesRegex(BudgetError, "shared USD cap"):
            reserve(self.spend(cost=11), "deepline")
        reserve(self.spend(cost=1), "deepline")
        with self.assertRaisesRegex(BudgetError, "paid-call cap"):
            reserve(self.spend("two", 1), "deepline")

    def test_unpriced_or_invalid_inputs_never_reach_provider(self):
        self.init()
        for invalid in (None, True, -1, "NaN", "Infinity", {}, "unknown"):
            with self.subTest(invalid=invalid), mock.patch.object(DEEPLINE, "_invoke") as provider:
                body, code = DEEPLINE.run(self.request(cost=invalid))
                self.assertEqual(code, 2)
                self.assertFalse(body["request_sent"])
                provider.assert_not_called()

    def test_both_adapters_require_budget_but_catalog_reads_do_not(self):
        with mock.patch.dict(os.environ, {"SCRAPINGDOG_API_KEY": "fixture"}):
            for adapter, request, method in (
                (DEEPLINE, {"operation": "execute", "tool": "fixture", "payload": {}}, "_invoke"),
                (SCRAPINGDOG, {"operation": "google_search", "query": "company"}, "_http_get"),
            ):
                with self.subTest(adapter=adapter.__name__), mock.patch.object(adapter, method) as provider:
                    body, code = adapter.run(request)
                    self.assertEqual((code, body["error_stage"]), (2, "budget"))
                    provider.assert_not_called()
        with mock.patch.object(DEEPLINE, "_invoke", return_value=(0, '{"results":[]}', "")) as provider:
            self.assertEqual(DEEPLINE.run({"operation": "search", "query": "companies"})[1], 0)
            provider.assert_called_once()

    def test_cli_rejects_unbudgeted_execution_without_calling_binary(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/deepline.py"), "--input",
                                 '{"operation":"execute","tool":"fixture","payload":{}}'],
                                env={**os.environ, "DEEPLINE_BIN": "/must-not-be-started"},
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(json.loads(result.stdout)["request_sent"])

    def test_reservation_exists_before_dispatch_and_receipted_cost_releases_difference(self):
        self.init()

        def dispatch(*_args):
            call = read_object(ledger_path(self.path))["calls"]["one"]
            self.assertEqual(call["maximum_credits"], "30")
            self.assertIsNone(call["actual_credits"])
            return 0, '{"results":[],"billing":{"credits_charged":10,"cost_usd":1}}', ""

        with mock.patch.object(DEEPLINE, "_invoke", side_effect=dispatch) as provider:
            body, code = DEEPLINE.run(self.request())
            self.assertEqual((code, body["spend_receipt"]["state"]), (0, "settled"))
            provider.assert_called_once()
        reserve(self.spend("two", 40), "scrapingdog")
        with self.assertRaises(BudgetError):
            reserve(self.spend("three", 0.01), "deepline")

    def test_timeout_stays_reserved_and_same_id_cannot_retry(self):
        self.init()
        with mock.patch.object(DEEPLINE, "_invoke", side_effect=DEEPLINE.CallTimeout("timeout")) as provider:
            body, code = DEEPLINE.run(self.request())
            self.assertEqual((code, body["status"]), (0, "timeout"))
            self.assertEqual(body["spend_receipt"]["state"], "reserved")
            retry, code = DEEPLINE.run(self.request())
            self.assertEqual(code, 2)
            self.assertFalse(retry["request_sent"])
            provider.assert_called_once()
        with self.assertRaises(BudgetError):
            reserve(self.spend("two", 21), "deepline")

    def test_interrupted_process_keeps_reservation_without_stale_execution_lock(self):
        self.init()
        script = "import os,sys; from budget_guard import reserve; reserve({'run_file':sys.argv[1], 'route_id':'crashed', 'max_cost_credits':40}, 'deepline'); os._exit(7)"
        result = subprocess.run([sys.executable, "-c", script, str(self.path)],
                                env={**os.environ, "PYTHONPATH": str(ROOT / "scripts")}, timeout=10)
        self.assertEqual(result.returncode, 7)
        with self.assertRaisesRegex(BudgetError, "shared USD cap"):
            reserve(self.spend("after-restart", 11), "deepline")
        reserve(self.spend("fits", 10), "deepline")

    def test_concurrent_processes_cannot_share_the_last_balance(self):
        self.init()
        script = """import sys
from budget_guard import reserve
try:
    reserve({'run_file':sys.argv[1], 'route_id':sys.argv[2], 'max_cost_credits':30}, 'deepline')
except (ValueError, OSError):
    sys.exit(2)
"""
        processes = [subprocess.Popen([sys.executable, "-c", script, str(self.path), str(index)],
                                     env={**os.environ, "PYTHONPATH": str(ROOT / "scripts")}) for index in range(8)]
        codes = [process.wait(timeout=10) for process in processes]
        self.assertEqual(codes.count(0), 1)
        self.assertEqual(len(read_object(ledger_path(self.path))["calls"]), 1)

    def test_verification_allowance_is_protected_and_consumed_only_by_verification(self):
        self.init(verification_reserve_credits=5)
        reserve(self.spend(cost=45), "deepline")
        with self.assertRaises(BudgetError):
            reserve(self.spend("discovery", 1), "deepline")
        reserve(self.spend("verify", 5), "deepline", verification=True)
        self.assertEqual(len(read_object(ledger_path(self.path))["calls"]), 2)

    def test_email_requires_explicitly_priced_verification_reserve(self):
        del self.document["request"]["contact_fields"]
        self.write()
        with self.assertRaisesRegex(BudgetError, "verification_reserve_credits"):
            self.init()

    def test_scrapingdog_unknown_billing_is_reserved_even_on_success(self):
        self.init()
        with mock.patch.dict(os.environ, {"SCRAPINGDOG_API_KEY": "fixture"}), \
             mock.patch.object(SCRAPINGDOG, "_http_get", return_value=(200, '{"organic_results":[]}', {})):
            body, code = SCRAPINGDOG.run({"operation": "google_search", "query": "company", "spend": self.spend()})
            self.assertEqual((code, body["spend_receipt"]["state"]), (0, "reserved"))
        with self.assertRaises(BudgetError):
            reserve(self.spend("two", 21), "deepline")

    def test_charge_above_bound_is_preserved_and_blocks_later_dispatch(self):
        self.init()
        with mock.patch.object(DEEPLINE, "_invoke", return_value=(0,
                '{"results":[],"billing":{"credits_charged":31}}', "")):
            body, code = DEEPLINE.run(self.request())
        self.assertEqual(code, 2)
        self.assertIn("above", body["budget_error"])
        self.assertEqual(read_object(ledger_path(self.path))["calls"]["one"]["actual_credits"], "31")
        with self.assertRaises(BudgetError):
            reserve(self.spend("two", 1), "deepline")

    def test_cannot_reset_ledger_or_ignore_unrecorded_spend(self):
        self.init()
        reserve(self.spend(), "deepline")
        with self.assertRaisesRegex(BudgetError, "already exists"):
            self.init(max_usd=100)
        self.document["routes"].append({"route_id": "bypass", "paid_calls": 1})
        self.write()
        with self.assertRaisesRegex(BudgetError, "missing from ledger"):
            reserve(self.spend("two", 1), "deepline")

    def test_existing_paid_runs_cannot_initialize_an_empty_ledger(self):
        self.document["routes"].append({"route_id": "old", "paid_calls": 1})
        self.write()
        with self.assertRaisesRegex(BudgetError, "before the first paid call"):
            self.init()

    def test_optional_per_next_lead_cap(self):
        self.document["budget"]["limits"]["max_deepline_credits_per_next_lead"] = 3
        self.write()
        self.init()
        reserve(self.spend(cost=2), "deepline")
        with self.assertRaisesRegex(BudgetError, "per-next-lead"):
            reserve(self.spend("two", 2), "deepline")

    def test_lock_and_disk_failure_prevent_dispatch(self):
        self.init()
        lock = Path(str(ledger_path(self.path)) + ".lock")
        lock.touch()
        with mock.patch.object(DEEPLINE, "_invoke") as provider:
            self.assertEqual(DEEPLINE.run(self.request())[1], 2)
            provider.assert_not_called()
        lock.unlink()
        with mock.patch("budget_guard.os.replace", side_effect=OSError("disk full")), \
             mock.patch.object(DEEPLINE, "_invoke") as provider:
            self.assertEqual(DEEPLINE.run(self.request())[1], 2)
            provider.assert_not_called()
        self.assertEqual(read_object(ledger_path(self.path))["calls"], {})

    def test_caps_cannot_be_raised_or_lowered_silently_in_report(self):
        self.init()
        for cap in (0, 100):
            self.document["budget"]["limits"]["deepline_credits"] = cap
            self.write()
            with self.assertRaisesRegex(BudgetError, "limits changed"):
                reserve(self.spend(cost=1), "deepline")

    def test_cannot_omit_explicit_next_lead_cap_from_ledger(self):
        self.document["request"]["budget"] = {"max_deepline_credits_per_next_lead": 1}
        self.write()
        with self.assertRaisesRegex(BudgetError, "next-lead limit changed"):
            self.init()
        self.assertFalse(ledger_path(self.path).exists())

    def test_nonempty_or_corrupt_ledgers_cannot_be_reset(self):
        path = ledger_path(self.path)
        for raw in ("{}", "invalid JSON"):
            path.write_text(raw)
            with self.assertRaises(ValueError):
                self.init()
            self.assertEqual(path.read_text(), raw)

    def test_partial_response_does_not_release_possibly_incomplete_billing(self):
        self.init()
        with mock.patch.object(DEEPLINE, "_invoke", return_value=(0,
                '{"status":"partial","results":[],"billing":{"credits_charged":1}}', "")):
            body, code = DEEPLINE.run(self.request())
        self.assertEqual((code, body["spend_receipt"]["state"]), (0, "reserved"))
        with self.assertRaises(BudgetError):
            reserve(self.spend("two", 21), "deepline")

    def test_usd_only_overcharge_blocks_further_spend(self):
        self.init()
        with mock.patch.object(DEEPLINE, "_invoke", return_value=(0,
                '{"results":[],"billing":{"cost_usd":4}}', "")):
            body, code = DEEPLINE.run(self.request())
        self.assertEqual(code, 2)
        self.assertIn("above", body["budget_error"])
        self.assertEqual(read_object(ledger_path(self.path))["calls"]["one"]["actual_usd"], "4")

    def test_audit_requires_every_dispatched_call_and_matching_amounts(self):
        self.init()
        path, route_id = reserve(self.spend(cost=2), "deepline")
        self.assertTrue(audit_ledger(self.path, self.document))
        route = {"route_id": route_id, "provider": "deepline", "paid_calls": 1,
                 "accepted_leads_before_call": 0, "cost_basis": "estimated", "cost_credits": None,
                 "cost_upper_bound_credits": 2}
        self.document["routes"] = [route]
        self.assertEqual(audit_ledger(self.path, self.document), [])
        settle(path, route_id, {"credits_charged": 1})
        self.assertTrue(audit_ledger(self.path, self.document))
        route.update(cost_basis="actual", cost_credits=1, cost_upper_bound_credits=1)
        self.assertEqual(audit_ledger(self.path, self.document), [])
        route["cost_credits"] = 0
        self.assertTrue(audit_ledger(self.path, self.document))

    def test_stop_and_full_validator_cli_detect_omitted_execution(self):
        self.init()
        reserve(self.spend(cost=2), "deepline")
        for flags in (["--check-stop"], []):
            result = subprocess.run([sys.executable, str(ROOT / "scripts/validate_run.py"), str(self.path), *flags],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2)
            self.assertTrue(any("execution ledger" in error for error in json.loads(result.stdout)["errors"]))

    def test_missing_priced_rate_and_false_reserve_are_refused(self):
        with self.assertRaisesRegex(BudgetError, "USD-per-credit"):
            initialize(self.path)
        with self.assertRaises(BudgetError):
            self.init(verification_reserve_credits=False)

    def test_dollar_receipt_releases_only_the_dollar_reservation(self):
        self.init()
        path, route_id = reserve(self.spend(cost=30), "deepline")
        settle(path, route_id, {"cost_usd": 1})
        call = read_object(path)["calls"][route_id]
        self.assertIsNone(call["actual_credits"])
        self.assertEqual(call["actual_usd"], "1")
        with self.assertRaisesRegex(BudgetError, "deepline credit cap"):
            reserve(self.spend("unknown-credits-still-count", 21), "deepline")
        reserve(self.spend("different-provider", 21), "scrapingdog")

    def test_zero_dollar_receipt_is_not_missing_billing(self):
        self.init()
        path, route_id = reserve(self.spend(cost=30), "deepline")
        settle(path, route_id, {"cost_usd": 0})
        reserve(self.spend("fits-dollar-cap", 50), "scrapingdog")

    def planned_document(self, actions, routes):
        self.document = stop_document(actions, target_count=10, routes=routes,
                                      limits=self.document["budget"]["limits"])
        self.write()
        return VALIDATOR.evaluate_stop(self.document, now=NOW, execution_budget=read_object(ledger_path(self.path)))

    def test_stop_check_shared_usd_cap_matches_dispatch_and_does_not_reserve(self):
        self.init()
        reserve(self.spend("deepline-spent", 10), "deepline")
        reserve(self.spend("scrapingdog-spent", 40), "scrapingdog")
        routes = [{"route_id": route_id, "provider": provider, "paid_calls": 1, "cost_credits": None,
                   "cost_upper_bound_credits": bound, "cost_basis": "estimated", "accepted_leads_before_call": 0}
                  for route_id, provider, bound in (("deepline-spent", "deepline", 10), ("scrapingdog-spent", "scrapingdog", 40))]
        result = self.planned_document([action("next", provider="deepline", paid_calls=1, cost_upper_bound_credits=1)], routes)
        self.assertEqual((result["decision"], result["eligible_actions"]), ("budget_exhausted", []))
        self.assertEqual(result["errors"], [])
        with self.assertRaisesRegex(BudgetError, "shared USD cap"):
            reserve(self.spend("next", 1), "deepline")
        self.assertEqual(len(read_object(ledger_path(self.path))["calls"]), 2)

    def test_stop_cli_excludes_discovery_but_allows_reserved_verification(self):
        self.init(verification_reserve_credits=5)
        reserve(self.spend("discovery-spent", 45), "deepline")
        routes = [{"route_id": "discovery-spent", "provider": "deepline", "paid_calls": 1,
                   "cost_credits": None, "cost_upper_bound_credits": 45, "cost_basis": "estimated",
                   "accepted_leads_before_call": 0}]
        actions = [action("discover", provider="deepline", paid_calls=1, cost_upper_bound_credits=1),
                   dict(action("verify", provider="deepline", paid_calls=1, cost_upper_bound_credits=5),
                        entity_type="email_validation")]
        result = self.planned_document(actions, routes)
        self.assertEqual(result["eligible_actions"], ["verify"])
        completed = subprocess.run([sys.executable, str(ROOT / "scripts/validate_run.py"), str(self.path), "--check-stop"],
                                   capture_output=True, text=True, timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["eligible_actions"], ["verify"])
        self.assertEqual(len(read_object(ledger_path(self.path))["calls"]), 1)
        reserve(self.spend("verify", 5), "deepline", verification=True)


class BudgetValidationTests(unittest.TestCase):
    def test_target_and_time_do_not_bypass_budget_errors(self):
        route = {"route_id": "paid", "provider": "deepline", "paid_calls": 1,
                 "cost_credits": 51, "cost_upper_bound_credits": 51, "cost_basis": "actual"}
        for accepted, duration in (([{}], None), ([], 1)):
            with self.subTest(accepted=accepted):
                document = stop_document([], accepted=accepted, max_duration_seconds=duration,
                                         routes=[route], limits={"deepline_credits": 50})
                result = VALIDATOR.evaluate_stop(document, now=NOW)
                self.assertEqual(result["decision"], "repair_state")
                self.assertTrue(any("exceeds limit" in error for error in result["errors"]))

    def test_unknown_cost_cannot_hide_confirmed_overspend_in_full_or_stop_validation(self):
        routes = [
            {"route_id": "known", "provider": "deepline", "paid_calls": 1, "cost_basis": "actual",
             "cost_credits": 51, "cost_upper_bound_credits": 51},
            {"route_id": "unknown", "provider": "deepline", "paid_calls": 1, "cost_basis": "unknown",
             "cost_credits": None, "cost_upper_bound_credits": None},
        ]
        document = cost_result(routes)
        document["budget"]["limits"]["deepline_credits"] = 50
        document["stop_check"] = {"started_at": NOW.isoformat(), "next_actions": []}
        for errors in (VALIDATOR.validate_run(document), VALIDATOR.evaluate_stop(document, now=NOW)["errors"]):
            self.assertTrue(any("exceeds limit" in error for error in errors), errors)
            self.assertEqual(errors.count("budget.spent.deepline_credits exceeds limit 50"), 1)


if __name__ == "__main__":
    unittest.main()
