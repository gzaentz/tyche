from __future__ import annotations

from datetime import datetime, timedelta, timezone
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_output_contract import VALIDATOR, VALIDATOR_PATH, cost_result, shortfall_result


NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
STARTED_AT = "2026-09-07T11:00:00+00:00"


def add_catalog_review(document):
    """Successful current capability review for fixtures testing genuine stops."""
    route = dict(route_id="catalog-review", scope="discovery", phase="account_discovery",
                 provider="deepline", operation="search", entity_type="tool_catalog", provider_status="ok",
                 paid_calls=0, cost_basis="actual", cost_credits=0, cost_upper_bound_credits=0,
                 rows_returned=1, rows_usable=0, request_summary="Alternative free and paid capabilities")
    document["routes"].append(route)
    document["stop_check"]["catalog_review_route_ids"] = [route["route_id"]]
    if "stop_audit" in document:
        for prior in document["stop_audit"]["route_frontier"]:
            prior.setdefault("scope", "discovery")
        document["stop_audit"]["route_frontier"].append(dict(
            route_id=route["route_id"], scope="discovery", state="exhausted",
            exhaustion_basis="no_new_unique_candidates", reason="Alternative capabilities reviewed."))
    return document


def action(
    action_id: str,
    *,
    scope: str = "discovery",
    provider: str = "public_web",
    paid_calls: int = 0,
    cost_upper_bound_credits: float | None = 0,
    description: str = "Search the next source for qualifying companies.",
    blocker: dict | None = None,
) -> dict:
    value = {
        "id": action_id,
        "scope": scope,
        "description": description,
        "provider": provider,
        "paid_calls": paid_calls,
        "cost_upper_bound_credits": cost_upper_bound_credits,
    }
    if blocker is not None:
        value["blocker"] = blocker
    return value


def stop_document(
    actions: list[dict],
    *,
    target_count: int = 1,
    accepted: list[dict] | None = None,
    unresolved: list[dict] | None = None,
    routes: list[dict] | None = None,
    limits: dict | None = None,
    started_at: str = STARTED_AT,
    max_duration_seconds: int | None = None,
) -> dict:
    accepted = [] if accepted is None else accepted
    unresolved = [] if unresolved is None else unresolved
    routes = [] if routes is None else routes
    limits = {
        "deepline_credits": 10,
        "scrapingdog_credits": 10,
        **(limits or {}),
    }
    spent = {}
    for provider in ("deepline", "scrapingdog"):
        provider_routes = [
            row
            for row in routes
            if row.get("provider") == provider and row.get("paid_calls", 0) > 0
        ]
        # A null spend is the existing representation when a paid receipt is
        # estimated or otherwise not confirmed; its upper bound is still used
        # by calculate_cost_summary and the stop policy.
        spent[f"{provider}_credits"] = (
            None
            if any(row.get("cost_credits") is None for row in provider_routes)
            else sum(row.get("cost_credits", 0) for row in provider_routes)
        )
    document = {
        "request": {"target_count": target_count},
        "summary": {"accepted_companies": len(accepted)},
        "accepted": accepted,
        "rejected": [],
        "unresolved": unresolved,
        "routes": routes,
        "budget": {
            "limits": limits,
            "spent": {"deepline_credits": spent.get("deepline_credits", 0),
                       "scrapingdog_credits": spent.get("scrapingdog_credits", 0)},
            "paid_calls": sum(row.get("paid_calls", 0) for row in routes),
            "status": "unknown" if any(value is None for value in spent.values()) else "within_budget",
        },
        "stop_check": {"started_at": started_at, "next_actions": actions},
    }
    if max_duration_seconds is not None:
        document["request"]["max_duration_seconds"] = max_duration_seconds
    return document


class StopPolicyTests(unittest.TestCase):
    def test_premature_stop_with_469_dollars_remaining_is_rejected(self):
        routes = [
            {"route_id": "spent", "provider": "deepline", "provider_status": "ok",
             "paid_calls": 9, "cost_basis": "actual", "cost_credits": 2.25,
             "cost_upper_bound_credits": 2.25},
            {"route_id": "reserved", "provider": "deepline", "provider_status": "partial",
             "paid_calls": 1, "cost_basis": "estimated", "cost_credits": None,
             "cost_upper_bound_credits": 0.85},
        ]
        document = cost_result(routes)
        document["request"]["target_count"] = 10
        document["budget"]["limits"].update(deepline_credits=50, max_paid_calls=150)
        document["stop_reason"] = "no_productive_route"
        document["stop_audit"] = copy.deepcopy(shortfall_result()["stop_audit"])
        document["stop_audit"].update(target_shortfall=9, candidate_companies_reviewed=1,
            substantive_account_reviews=1,
            provider_call_capacity={"deepline": "unknown", "scrapingdog": "available", "paid_calls_remaining": 140},
            route_frontier=[dict(route_id=r["route_id"], state="exhausted",
                reason="All returned records reviewed.", exhaustion_basis="no_new_unique_candidates") for r in document["routes"]])
        self.assertEqual(VALIDATOR.validate_run(document), [])
        document["stop_check"] = {"started_at": STARTED_AT, "next_actions": [
            action("independent-discovery", provider="deepline", paid_calls=1, cost_upper_bound_credits=0.55)]}
        result = VALIDATOR.evaluate_stop(document, now=NOW)
        self.assertEqual(result["eligible_actions"], ["independent-discovery"])
        errors = VALIDATOR.validate_run(document, require_stop_check=True, now=NOW)
        self.assertTrue(any("requires continuation" in error for error in errors), errors)

    def test_time_stop_keeps_unfinished_routes_actionable(self):
        document = shortfall_result(frontier_state="continuable", stop_reason="time_limit_reached")
        document["request"]["max_duration_seconds"] = 60
        document["stop_check"] = {"started_at": (NOW - timedelta(seconds=60)).isoformat(),
            "next_actions": [action("more-research")]}
        before = copy.deepcopy(document)
        self.assertEqual(VALIDATOR.validate_run(document, require_stop_check=True, now=NOW), [])
        self.assertEqual(document, before)
        self.assertTrue(VALIDATOR.validate_run(document, require_stop_check=True, now=NOW - timedelta(seconds=1)))

    def test_budget_stop_before_overspend_without_closing_pending_routes(self):
        document = shortfall_result(frontier_state="continuable", stop_reason="budget_exhausted")
        planned = stop_document([action("paid", provider="deepline", paid_calls=1, cost_upper_bound_credits=2)],
            limits={"deepline_credits": 1})
        document.update(budget=planned["budget"], stop_check=planned["stop_check"])
        document["routes"][0]["paid_calls"] = 0
        add_catalog_review(document)
        self.assertEqual(VALIDATOR.evaluate_stop(document, now=NOW)["decision"], "budget_exhausted")
        self.assertEqual(VALIDATOR.validate_run(document, require_stop_check=True, now=NOW), [])

    def test_explicit_next_lead_cap_is_preserved(self):
        document = stop_document([action("too-much", provider="deepline", paid_calls=1, cost_upper_bound_credits=0.6),
            action("free")], limits={"max_deepline_credits_per_next_lead": 0.5})
        result = VALIDATOR.evaluate_stop(document, now=NOW)
        self.assertEqual(result["eligible_actions"], ["free"])

    def test_no_actions_does_not_prove_exhaustion(self):
        result = VALIDATOR.evaluate_stop(stop_document([]), now=NOW)
        self.assertEqual(result["decision"], "continue")
        self.assertEqual(result["missing_scopes"], ["discovery"])

    def test_invalid_draft_state_returns_errors_not_tracebacks(self):
        for field in ("routes", "unresolved", "rejected"):
            document = stop_document([action("free")])
            document[field] = None
            self.assertTrue(VALIDATOR.evaluate_stop(document, now=NOW)["errors"])
        for field, value in (("provider", []), ("paid_calls", True), ("cost_upper_bound_credits", float("nan"))):
            document = stop_document([action("valid"), action("invalid")])
            document["stop_check"]["next_actions"][1][field] = value
            result = VALIDATOR.evaluate_stop(document, now=NOW)
            self.assertEqual(result["decision"], "repair_state")
            self.assertEqual(result["eligible_actions"], [])

    def test_time_limit_is_positive_and_start_cannot_reset_into_future(self):
        for limit in (0, -1, True, "60"):
            document = stop_document([action("free")])
            document["request"]["max_duration_seconds"] = limit
            self.assertTrue(VALIDATOR.evaluate_stop(document, now=NOW)["errors"])
        for started in ("bad", "2026-09-07T11:00:00", (NOW + timedelta(seconds=1)).isoformat()):
            self.assertTrue(VALIDATOR.evaluate_stop(stop_document([], started_at=started), now=NOW)["errors"])

    def test_cli_defaults_to_strict_and_does_not_mutate_historical_report(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results.json"
            path.write_text(json.dumps(shortfall_result()))
            before = path.read_bytes()
            strict = subprocess.run([sys.executable, str(VALIDATOR_PATH), str(path)], capture_output=True, text=True, timeout=10)
            legacy = subprocess.run([sys.executable, str(VALIDATOR_PATH), str(path), "--legacy-stop-policy"], capture_output=True, text=True, timeout=10)
            self.assertEqual(strict.returncode, 2, strict.stderr)
            self.assertEqual(json.loads(strict.stdout)["stop_policy"], "strict")
            self.assertEqual(legacy.returncode, 0, legacy.stderr)
            self.assertEqual(json.loads(legacy.stdout)["stop_policy"], "legacy")
            self.assertEqual(path.read_bytes(), before)

    def test_target_reached_stops_even_with_no_next_action(self):
        document = stop_document(
            [],
            accepted=[
                {
                    "company": {"canonical_name": "Example", "domain": "example.org"},
                    "primary_contact": {},
                }
            ],
        )

        result = VALIDATOR.evaluate_stop(document, now=NOW)

        self.assertEqual(result["decision"], "target_met")
        self.assertEqual(result["errors"], [])

    def test_useful_affordable_action_continues_past_exhausted_frontier_label(self):
        document = stop_document([action("public-follow-up")])
        document["stop_audit"] = {
            "route_frontier": [{"route_id": "old", "state": "exhausted"}]
        }

        result = VALIDATOR.evaluate_stop(document, now=NOW)

        self.assertEqual(result["decision"], "continue")
        self.assertEqual(result["eligible_actions"], ["public-follow-up"])

    def test_explicit_duration_expires_at_exact_boundary(self):
        document = stop_document(
            [action("still-available")],
            started_at=(NOW - timedelta(seconds=60)).isoformat(),
            max_duration_seconds=60,
        )

        before = VALIDATOR.evaluate_stop(document, now=NOW - timedelta(seconds=1))
        at_boundary = VALIDATOR.evaluate_stop(document, now=NOW)

        self.assertEqual(before["decision"], "continue")
        self.assertEqual(at_boundary["decision"], "time_limit_reached")

    def test_missing_duration_does_not_create_an_implicit_time_limit(self):
        document = stop_document([action("old-but-useful")], started_at="2020-01-01T00:00:00+00:00")

        result = VALIDATOR.evaluate_stop(document, now=NOW)

        self.assertEqual(result["decision"], "continue")
        self.assertEqual(result["eligible_actions"], ["old-but-useful"])

    def test_budget_uses_confirmed_and_estimated_costs_not_legacy_call_cap(self):
        routes = [
            {
                "route_id": "confirmed-deepline",
                "provider": "deepline",
                "paid_calls": 1,
                "cost_basis": "actual",
                "cost_credits": 2,
                "cost_upper_bound_credits": 2,
            },
            {
                "route_id": "estimated-deepline",
                "provider": "deepline",
                "paid_calls": 1,
                "cost_basis": "estimated",
                "cost_credits": None,
                "cost_upper_bound_credits": 3,
            },
            {
                "route_id": "confirmed-scrapingdog",
                "provider": "scrapingdog",
                "paid_calls": 1,
                "cost_basis": "actual",
                "cost_credits": 1,
                "cost_upper_bound_credits": 1,
            },
        ]
        document = stop_document(
            [
                action("deepline-at-cap", provider="deepline", paid_calls=1, cost_upper_bound_credits=5),
                action("deepline-over-cap", provider="deepline", paid_calls=1, cost_upper_bound_credits=5.01),
                action("scrapingdog-past-legacy-call-cap", provider="scrapingdog", paid_calls=2, cost_upper_bound_credits=0.1),
            ],
            routes=routes,
            limits={"deepline_credits": 10, "scrapingdog_credits": 10, "max_paid_calls": 4},
        )

        result = VALIDATOR.evaluate_stop(document, now=NOW)

        self.assertEqual(result["decision"], "continue")
        self.assertEqual(result["eligible_actions"], ["deepline-at-cap", "scrapingdog-past-legacy-call-cap"])

    def test_action_over_credit_cap_is_not_eligible_but_free_action_is(self):
        document = stop_document(
            [
                action("over-cap", provider="deepline", paid_calls=1, cost_upper_bound_credits=11),
                action("free-search"),
            ],
            limits={"deepline_credits": 10},
        )

        result = VALIDATOR.evaluate_stop(document, now=NOW)

        self.assertEqual(result["decision"], "continue")
        self.assertEqual(result["eligible_actions"], ["free-search"])

    def test_unknown_action_price_requests_pricing_instead_of_budget_stop(self):
        document = stop_document([action("unpriced", provider="deepline", paid_calls=1, cost_upper_bound_credits=None)])

        result = VALIDATOR.evaluate_stop(document, now=NOW)

        self.assertEqual(result["decision"], "continue")
        self.assertTrue(result["pricing_required"])
        self.assertEqual(result["eligible_actions"], [])

    def test_one_blocked_action_does_not_stop_another_available_action(self):
        routes = [{"route_id": "timeout-1", "provider_status": "timeout", "paid_calls": 0,
                   "provider": "public_web", "scope": "discovery"}]
        blocked = action(
            "blocked-source",
            blocker={
                "kind": "access_unavailable",
                "reason": "Provider access timed out.",
                "evidence_route_id": "timeout-1",
            },
        )
        document = stop_document([blocked, action("available-source")], routes=routes)

        result = VALIDATOR.evaluate_stop(document, now=NOW)

        self.assertEqual(result["decision"], "continue")
        self.assertEqual(result["eligible_actions"], ["available-source"])

    def test_blocker_must_have_actual_blocking_evidence(self):
        document = stop_document(
            [
                action(
                    "fake-blocked",
                    blocker={
                        "kind": "approval_required",
                        "reason": "Approval is pending.",
                        "evidence_route_id": "successful-1",
                    },
                )
            ],
            routes=[{"route_id": "successful-1", "provider_status": "ok", "paid_calls": 0}],
        )

        result = VALIDATOR.evaluate_stop(document, now=NOW)

        self.assertTrue(any("blocking receipt" in error for error in result["errors"]))
        self.assertEqual(result["decision"], "repair_state")

    def test_blocker_can_use_a_route_outcome_as_evidence(self):
        document = stop_document(
            [
                action(
                    "unconnected-source",
                    blocker={
                        "kind": "required_input",
                        "reason": "The source is not connected.",
                        "evidence_route_id": "route-outcome-1",
                    },
                )
            ],
            unresolved=[
                {
                    "stage": "route",
                    "route_id": "route-outcome-1",
                    "reason_code": "route_not_connected",
                    "reason_text": "The provider is not connected.",
                    "provider_status": "config_error",
                    "provider": "public_web", "scope": "discovery",
                }
            ],
        )

        add_catalog_review(document)
        result = VALIDATOR.evaluate_stop(document, now=NOW)

        self.assertEqual(result["errors"], [])
        self.assertEqual(result["decision"], "input_or_configuration_stop")

    def test_absent_discovery_action_requires_continuation(self):
        document = stop_document([action("recover-acme", scope="acme.example")])

        result = VALIDATOR.evaluate_stop(document, now=NOW)

        self.assertEqual(result["decision"], "continue")
        self.assertIn("discovery", result["missing_scopes"])

    def test_unresolved_company_without_recovery_action_requires_continuation(self):
        document = stop_document(
            [action("discover-more")],
            unresolved=[
                {
                    "stage": "account",
                    "candidate": {"company": "Acme", "domain": "acme.example"},
                }
            ],
        )

        result = VALIDATOR.evaluate_stop(document, now=NOW)

        self.assertEqual(result["decision"], "continue")
        self.assertIn("acme.example", result["missing_scopes"])

    def test_strict_validation_requires_stop_check_but_legacy_mode_remains_available(self):
        legacy = shortfall_result()

        strict_errors = VALIDATOR.validate_run(legacy, require_stop_check=True)
        legacy_errors = VALIDATOR.validate_run(legacy, require_stop_check=False)

        self.assertTrue(any("stop_check" in error for error in strict_errors))
        self.assertEqual(legacy_errors, [])

    def test_stop_check_requires_started_at_and_next_actions(self):
        document = stop_document([])
        document["stop_check"] = {}

        result = VALIDATOR.evaluate_stop(document, now=NOW)

        self.assertTrue(any("started_at" in error for error in result["errors"]))

        document = stop_document([action("valid")])
        document["stop_check"].pop("next_actions")
        result = VALIDATOR.evaluate_stop(document, now=NOW)
        self.assertTrue(any("next_actions" in error for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
