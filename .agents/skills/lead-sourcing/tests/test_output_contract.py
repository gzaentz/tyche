from __future__ import annotations

import json
import importlib.util
import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "references" / "output-contract.md"
SKILL = ROOT / "SKILL.md"
VALIDATOR_PATH = ROOT / "scripts" / "validate_run.py"

SPEC = importlib.util.spec_from_file_location("tyche_validate_run", VALIDATOR_PATH)
assert SPEC and SPEC.loader
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


def load_schemas():
    blocks = re.findall(r"```json\n(.*?)\n```", CONTRACT.read_text(encoding="utf-8"), re.S)
    return [json.loads(block) for block in blocks]


def validate_extensions(request):
    """Validate the new request semantics not expressible in JSON Schema."""

    schema = load_schemas()[0]
    mode = request.get("signal_match_mode", "any")
    if mode not in schema["properties"]["signal_match_mode"]["enum"]:
        raise ValueError("signal_match_mode must be any or all")
    for signal in request.get("buying_signals", []):
        minimum = signal.get("min_age_days", 0)
        maximum = signal.get("max_age_days")
        if maximum is not None and minimum > maximum:
            raise ValueError("min_age_days must not exceed max_age_days")


def shortfall_result(frontier_state="exhausted", stop_reason="no_productive_route"):
    result = {
        "request": {"target_count": 1},
        "summary": {"accepted_companies": 0},
        "accepted": [],
        "rejected": [],
        "unresolved": [],
        "routes": [{"route_id": "route-1", "provider_status": "no_results"}],
        "stop_reason": stop_reason,
        "stop_audit": {
            "target_shortfall": 1,
            "candidate_companies_reviewed": 0,
            "substantive_account_reviews": 0,
            "exclusion_only_rejections": 0,
            "duplicate_candidates": 0,
            "frontier_complete": True,
            "provider_call_capacity": {
                "deepline": "available",
                "scrapingdog": "available",
                "paid_calls_remaining": 2,
            },
            "route_frontier": [
                {
                    "route_id": "route-1",
                    "state": frontier_state,
                    "reason": "No new unique candidates remained.",
                }
            ],
        },
    }
    if frontier_state == "exhausted":
        result["stop_audit"]["route_frontier"][0]["exhaustion_basis"] = "no_results"
    return result


def email_validation_receipt(status="valid", route_id="email-validation-1", tool="live-validator"):
    return {
        "email": "ada@example.org",
        "status": status,
        "sub_status": None,
        "source": {
            "provider": "deepline",
            "validator": "zerobounce",
            "operation": "execute",
            "tool": tool,
            "route_id": route_id,
        },
    }


def accepted_email_result(status="valid"):
    return {
        "request": {"target_count": 1},
        "summary": {"accepted_companies": 1},
        "accepted": [
            {
                "company": {"canonical_name": "Example", "domain": "example.org"},
                "primary_contact": {
                    "full_name": "Ada Example",
                    "email": "ada@example.org",
                    "email_validation": email_validation_receipt(status=status),
                },
            }
        ],
        "routes": [
            {
                "route_id": "email-validation-1",
                "phase": "email_validation",
                "provider": "deepline",
                "operation": "execute",
                "tool": "live-validator",
                "provider_status": "ok",
                "paid_calls": 1,
            }
        ],
        "stop_reason": "target_met",
    }


class OutputContractExtensionTests(unittest.TestCase):
    def test_omitted_contact_fields_remains_valid_and_defaults_to_email(self):
        request = {
            "target_count": 1,
            "icp": {"geographies": ["US"]},
            "buying_signals": [{"kind": "leadership_change", "max_age_days": 270}],
            "requested_roles": ["Executive Director"],
            "time_window": {"max_age_days": 270},
            "budget": {"scrapingdog_credits": 15, "hard_stop": True},
        }
        validate_extensions(request)
        self.assertNotIn("signal_match_mode", request)
        self.assertNotIn("min_age_days", request["buying_signals"][0])
        self.assertNotIn("contact_role_groups", request)
        input_schema, result_schema = load_schemas()
        self.assertEqual(
            input_schema["properties"]["contact_fields"]["default"], ["email"]
        )
        self.assertEqual(
            result_schema["$defs"]["request_snapshot"]["properties"]["contact_fields"]["default"],
            ["email"],
        )

    def test_email_validation_receipt_is_deepline_zerobounce_with_dynamic_tool(self):
        _, result_schema = load_schemas()
        validation = result_schema["$defs"]["email_validation"]
        source = validation["properties"]["source"]
        self.assertEqual(source["properties"]["provider"]["const"], "deepline")
        self.assertEqual(source["properties"]["validator"]["const"], "zerobounce")
        self.assertEqual(source["properties"]["operation"]["const"], "execute")
        self.assertNotIn("const", source["properties"]["tool"])
        self.assertEqual(
            result_schema["$defs"]["contact"]["properties"]["email_validation"]["$ref"],
            "#/$defs/email_validation",
        )
        self.assertIn(
            "email_validation",
            result_schema["$defs"]["route"]["properties"]["phase"]["enum"],
        )

    def test_contact_role_groups_are_optional_and_role_group_is_traceable(self):
        input_schema, result_schema = load_schemas()

        for schema in (input_schema, result_schema):
            properties = (
                schema["properties"]
                if schema is input_schema
                else schema["$defs"]["request_snapshot"]["properties"]
            )
            required = (
                schema["required"]
                if schema is input_schema
                else schema["$defs"]["request_snapshot"]["required"]
            )
            self.assertIn("contact_role_groups", properties)
            self.assertIn("requested_roles", required)
            self.assertNotIn("contact_role_groups", required)

            group = schema["$defs"]["contact_role_groups"]
            self.assertEqual(group["required"], ["primary", "secondary"])
            self.assertFalse(group["additionalProperties"])
            self.assertEqual(group["properties"]["primary"]["minItems"], 1)
            self.assertTrue(group["properties"]["primary"]["uniqueItems"])
            self.assertTrue(group["properties"]["secondary"]["uniqueItems"])

        contact = result_schema["$defs"]["contact"]
        self.assertNotIn("role_group", contact["required"])
        self.assertEqual(contact["properties"]["role_group"]["enum"], ["primary", "secondary"])
        accepted_company = result_schema["$defs"]["accepted_company"]
        self.assertNotIn("contacts", accepted_company["properties"])
        self.assertEqual(
            accepted_company["properties"]["backup_contacts"]["items"]["$ref"],
            "#/$defs/contact",
        )

    def test_grouped_roles_use_requested_roles_union_and_secondary_fallback(self):
        input_schema, result_schema = load_schemas()
        groups = {
            "primary": ["Executive Director", "CEO"],
            "secondary": ["Chief Program Officer", "VP Programs"],
        }
        request = {
            "requested_roles": [
                "Executive Director",
                "CEO",
                "Chief Program Officer",
                "VP Programs",
            ],
            "contact_role_groups": groups,
        }
        self.assertEqual(
            set(request["requested_roles"]),
            set(groups["primary"] + groups["secondary"]),
        )
        self.assertEqual(
            input_schema["properties"]["contact_role_groups"]["$ref"],
            "#/$defs/contact_role_groups",
        )
        self.assertEqual(
            result_schema["$defs"]["request_snapshot"]["properties"]["contact_role_groups"]["$ref"],
            "#/$defs/contact_role_groups",
        )

        skill_text = SKILL.read_text(encoding="utf-8").lower()
        contract_text = CONTRACT.read_text(encoding="utf-8").lower()
        self.assertIn("search and rank", skill_text)
        self.assertIn("valid fallbacks", skill_text)
        self.assertIn("no primary-role", skill_text)
        self.assertIn("secondary-role contact remains eligible", contract_text)
        self.assertIn("must not be", skill_text)
        self.assertIn("rejected only because it is secondary", skill_text)
        self.assertIn('role_group: "secondary"', skill_text)

    def test_completion_validator_enforces_group_union_and_role_traceability(self):
        result = shortfall_result()
        result["request"].update(
            {
                "requested_roles": ["Executive Director", "Chief Program Officer"],
                "contact_role_groups": {
                    "primary": ["Executive Director"],
                    "secondary": ["Chief Program Officer"],
                },
            }
        )
        self.assertEqual(VALIDATOR.validate_run(result), [])

        result["request"]["requested_roles"] = ["Executive Director"]
        errors = VALIDATOR.validate_run(result)
        self.assertIn(
            "request.requested_roles must equal the contact_role_groups union", errors
        )

        reached = {
            "request": {
                "target_count": 1,
                "contact_fields": [],
                "requested_roles": ["Executive Director", "Chief Program Officer"],
                "contact_role_groups": {
                    "primary": ["Executive Director"],
                    "secondary": ["Chief Program Officer"],
                },
            },
            "summary": {"accepted_companies": 1},
            "accepted": [
                {
                    "company": {"canonical_name": "Example", "domain": "example.org"},
                    "primary_contact": {
                        "full_name": "Ada Example",
                        "requested_role": "Chief Program Officer",
                        "role_group": "secondary",
                    },
                }
            ],
            "stop_reason": "target_met",
        }
        self.assertEqual(VALIDATOR.validate_run(reached), [])

        reached["accepted"][0]["primary_contact"]["role_group"] = "primary"
        errors = VALIDATOR.validate_run(reached)
        self.assertTrue(any("does not match role_group" in error for error in errors))

    def test_new_signal_fields_and_qualification_check_are_declared(self):
        input_schema, result_schema = load_schemas()
        self.assertEqual(
            input_schema["properties"]["signal_match_mode"]["enum"], ["any", "all"]
        )
        self.assertIn("min_age_days", input_schema["$defs"]["signal"]["properties"])
        self.assertIn("min_age_days", result_schema["$defs"]["signal"]["properties"])

        check = result_schema["$defs"]["qualification_check"]
        self.assertEqual(
            check["required"], ["criterion", "importance", "status", "claim", "evidence"]
        )
        self.assertEqual(check["properties"]["importance"]["enum"], ["required", "preferred"])
        self.assertEqual(check["properties"]["status"]["enum"], ["pass", "fail", "unknown"])
        self.assertEqual(check["properties"]["evidence"]["items"]["$ref"], "#/$defs/evidence")
        self.assertIn("qualification_checks", result_schema["$defs"]["accepted_company"]["properties"])
        self.assertIn("qualification_checks", result_schema["$defs"]["outcome_row"]["properties"])
        self.assertIn("account_fit", result_schema["$defs"]["accepted_company"]["properties"])
        self.assertIn("signal_evidence", result_schema["$defs"]["accepted_company"]["properties"])
        self.assertIn("stop_audit", result_schema["properties"])
        self.assertIn(
            "public_web", result_schema["$defs"]["route"]["properties"]["provider"]["enum"]
        )
        self.assertIn(
            "explicit_exclusion", result_schema["$defs"]["reason_code"]["enum"]
        )

        request = {
            "target_count": 1,
            "icp": {"geographies": ["US"]},
            "buying_signals": [{"kind": "leadership_change", "min_age_days": 90, "max_age_days": 270}],
            "signal_match_mode": "any",
            "requested_roles": ["Executive Director"],
            "time_window": {"max_age_days": 270},
            "budget": {"deepline_credits": 5, "hard_stop": True},
        }
        validate_extensions(request)

    def test_invalid_signal_mode_and_reversed_bounds_fail(self):
        base = {
            "target_count": 1,
            "icp": {"geographies": ["US"]},
            "buying_signals": [{"kind": "leadership_change", "max_age_days": 270}],
            "requested_roles": ["Executive Director"],
            "time_window": {"max_age_days": 270},
            "budget": {"deepline_credits": 5, "hard_stop": True},
        }
        invalid_mode = dict(base, signal_match_mode="all_of")
        with self.assertRaises(ValueError):
            validate_extensions(invalid_mode)

        invalid_bounds = dict(
            base,
            buying_signals=[{"kind": "leadership_change", "min_age_days": 271, "max_age_days": 270}],
        )
        with self.assertRaises(ValueError):
            validate_extensions(invalid_bounds)

    def test_unknown_is_distinct_from_fail(self):
        check = {"criterion": "annual_revenue", "importance": "required", "status": "unknown", "claim": "No reliable filing found", "evidence": []}
        self.assertNotEqual(check["status"], "fail")
        self.assertIn(check["status"], ["pass", "fail", "unknown"])

    def test_actionable_route_prevents_shortfall_stop(self):
        for state in ("untried", "continuable"):
            with self.subTest(state=state):
                errors = VALIDATOR.validate_run(shortfall_result(frontier_state=state))
                self.assertTrue(any("run must continue" in error for error in errors))

    def test_exhausted_frontier_allows_no_productive_route(self):
        self.assertEqual(VALIDATOR.validate_run(shortfall_result()), [])

    def test_exhausted_route_requires_attempt_receipt(self):
        result = shortfall_result()
        result["routes"] = []
        errors = VALIDATOR.validate_run(result)
        self.assertIn("exhausted routes missing attempt receipts: route-1", errors)

    def test_route_ids_are_unique_per_receipt_and_outcome_attempt(self):
        duplicate_receipt = shortfall_result()
        duplicate_receipt["routes"].append(
            {"route_id": "route-1", "provider_status": "no_results"}
        )
        errors = VALIDATOR.validate_run(duplicate_receipt)
        self.assertIn("routes contain duplicate route_id attempts: route-1", errors)

        duplicate_outcome = shortfall_result(frontier_state="blocked", stop_reason="provider_stop")
        duplicate_outcome["routes"] = []
        duplicate_outcome["unresolved"] = [
            {"stage": "route", "route_id": "route-1", "reason_code": "route_not_connected"},
            {"stage": "route", "route_id": "route-1", "reason_code": "budget_exhausted"},
        ]
        duplicate_outcome["stop_audit"]["provider_call_capacity"] = {
            "deepline": "unavailable",
            "scrapingdog": "unavailable",
            "paid_calls_remaining": 0,
        }
        self.assertIn(
            "route outcomes contain duplicate route_id attempts: route-1",
            VALIDATOR.validate_run(duplicate_outcome),
        )

    def test_completed_receipt_and_blocking_continuation_need_distinct_route_ids(self):
        result = shortfall_result(frontier_state="blocked", stop_reason="provider_stop")
        result["routes"][0]["provider_status"] = "ok"
        result["unresolved"] = [
            {"stage": "route", "route_id": "route-1", "reason_code": "budget_exhausted"}
        ]
        result["stop_audit"]["provider_call_capacity"] = {
            "deepline": "unavailable",
            "scrapingdog": "unavailable",
            "paid_calls_remaining": 0,
        }
        errors = VALIDATOR.validate_run(result)
        self.assertTrue(any("completed receipt" in error for error in errors))

    def test_blocking_receipt_and_matching_outcome_can_share_route_id(self):
        result = shortfall_result(frontier_state="blocked", stop_reason="provider_stop")
        result["routes"][0]["provider_status"] = "provider_error"
        result["unresolved"] = [
            {"stage": "route", "route_id": "route-1", "reason_code": "provider_status"}
        ]
        result["stop_audit"]["provider_call_capacity"] = {
            "deepline": "unavailable",
            "scrapingdog": "unavailable",
            "paid_calls_remaining": 0,
        }
        self.assertEqual(VALIDATOR.validate_run(result), [])

    def test_provider_error_cannot_be_exhausted(self):
        result = shortfall_result()
        result["routes"][0]["provider_status"] = "provider_error"
        errors = VALIDATOR.validate_run(result)
        self.assertTrue(any("blocking provider status" in error for error in errors))

    def test_every_accepted_contact_uses_requested_role_and_role_group(self):
        result = {
            "request": {
                "target_count": 1,
                "contact_fields": [],
                "requested_roles": ["Executive Director", "Chief Program Officer"],
                "contact_role_groups": {
                    "primary": ["Executive Director"],
                    "secondary": ["Chief Program Officer"],
                },
            },
            "summary": {"accepted_companies": 1},
            "accepted": [
                {
                    "company": {"canonical_name": "Example", "domain": "example.org"},
                    "primary_contact": {
                        "full_name": "Ada Example",
                        "requested_role": "Executive Director",
                        "role_group": "primary",
                    },
                    "backup_contacts": [
                        {
                            "full_name": "Bea Example",
                            "requested_role": "Chief Program Officer",
                            "role_group": "secondary",
                        }
                    ],
                }
            ],
            "stop_reason": "target_met",
        }
        self.assertEqual(VALIDATOR.validate_run(result), [])

        result["accepted"][0]["backup_contacts"][0]["requested_role"] = "Chief Financial Officer"
        errors = VALIDATOR.validate_run(result)
        self.assertTrue(any("backup_contacts[0].requested_role is not" in error for error in errors))

        result["accepted"][0]["backup_contacts"][0]["requested_role"] = "Chief Program Officer"
        result["accepted"][0]["backup_contacts"][0]["role_group"] = "primary"
        errors = VALIDATOR.validate_run(result)
        self.assertTrue(any("backup_contacts[0] requested_role does not match role_group" in error for error in errors))

    def test_blocked_route_requires_attempt_or_outcome_receipt(self):
        result = shortfall_result(frontier_state="blocked", stop_reason="provider_stop")
        result["routes"] = []
        result["stop_audit"]["provider_call_capacity"] = {
            "deepline": "unavailable",
            "scrapingdog": "unavailable",
            "paid_calls_remaining": 0,
        }
        errors = VALIDATOR.validate_run(result)
        self.assertIn(
            "blocked routes missing attempt or route-outcome receipts: route-1", errors
        )

        result["unresolved"] = [
            {"stage": "route", "route_id": "route-1", "reason_code": "route_not_connected"}
        ]
        self.assertEqual(VALIDATOR.validate_run(result), [])

    def test_frontier_must_be_attested_complete(self):
        result = shortfall_result()
        result["stop_audit"]["frontier_complete"] = False
        errors = VALIDATOR.validate_run(result)
        self.assertIn("stop_audit.frontier_complete must be true", errors)

    def test_budget_stop_requires_known_unavailable_capacity(self):
        for state in ("available", "unknown"):
            with self.subTest(state=state):
                result = shortfall_result(stop_reason="budget_exhausted")
                result["stop_audit"]["provider_call_capacity"]["deepline"] = state
                result["stop_audit"]["provider_call_capacity"]["scrapingdog"] = "unavailable"
                errors = VALIDATOR.validate_run(result)
                self.assertTrue(any("budget_exhausted" in error for error in errors))

        result = shortfall_result(stop_reason="budget_exhausted")
        result["stop_audit"]["provider_call_capacity"] = {
            "deepline": "unavailable",
            "scrapingdog": "unavailable",
            "paid_calls_remaining": 0,
        }
        self.assertEqual(VALIDATOR.validate_run(result), [])

    def test_actionable_public_web_route_blocks_budget_stop(self):
        result = shortfall_result(
            frontier_state="continuable", stop_reason="budget_exhausted"
        )
        result["stop_audit"]["route_frontier"][0]["provider"] = "public_web"
        result["stop_audit"]["provider_call_capacity"] = {
            "deepline": "unavailable",
            "scrapingdog": "unavailable",
            "paid_calls_remaining": 0,
        }
        errors = VALIDATOR.validate_run(result)
        self.assertTrue(any("run must continue" in error for error in errors))

    def test_provider_stop_fails_while_paid_provider_is_available(self):
        result = shortfall_result(frontier_state="blocked", stop_reason="provider_stop")
        result["routes"][0]["provider_status"] = "provider_error"
        errors = VALIDATOR.validate_run(result)
        self.assertTrue(any("provider_stop is invalid" in error for error in errors))

    def test_budget_accounting_matches_route_receipts(self):
        result = shortfall_result()
        result["routes"][0].update(
            {"provider": "deepline", "paid_calls": 1, "cost_credits": 1.5}
        )
        result["budget"] = {
            "limits": {
                "deepline_credits": 5,
                "scrapingdog_credits": 5,
                "max_paid_calls": 2,
            },
            "spent": {"deepline_credits": 1.5, "scrapingdog_credits": 0},
            "paid_calls": 1,
            "status": "within_budget",
        }
        self.assertEqual(VALIDATOR.validate_run(result), [])

        result["budget"]["paid_calls"] = 2
        result["budget"]["spent"]["deepline_credits"] = 2
        errors = VALIDATOR.validate_run(result)
        self.assertTrue(any("route paid-call sum" in error for error in errors))
        self.assertTrue(any("known route cost sum" in error for error in errors))

    def test_unknown_route_cost_requires_unknown_spend_status_and_capacity(self):
        result = shortfall_result()
        result["routes"][0].update(
            {"provider": "scrapingdog", "paid_calls": 1, "cost_credits": None}
        )
        result["budget"] = {
            "limits": {
                "deepline_credits": 5,
                "scrapingdog_credits": 5,
                "max_paid_calls": 2,
            },
            "spent": {"deepline_credits": 0, "scrapingdog_credits": None},
            "paid_calls": 1,
            "status": "unknown",
        }
        result["stop_audit"]["provider_call_capacity"]["scrapingdog"] = "unknown"
        self.assertEqual(VALIDATOR.validate_run(result), [])

        result["budget"]["spent"]["scrapingdog_credits"] = 5
        result["budget"]["status"] = "within_budget"
        result["stop_audit"]["provider_call_capacity"]["scrapingdog"] = "available"
        errors = VALIDATOR.validate_run(result)
        self.assertTrue(any("must be null" in error for error in errors))
        self.assertTrue(any("budget.status must be unknown" in error for error in errors))

    def test_known_spend_and_paid_calls_cannot_exceed_caps(self):
        result = shortfall_result()
        result["routes"][0].update(
            {"provider": "deepline", "paid_calls": 2, "cost_credits": 6}
        )
        result["budget"] = {
            "limits": {
                "deepline_credits": 5,
                "scrapingdog_credits": 5,
                "max_paid_calls": 1,
            },
            "spent": {"deepline_credits": 6, "scrapingdog_credits": 0},
            "paid_calls": 2,
            "status": "exhausted",
        }
        errors = VALIDATOR.validate_run(result)
        self.assertTrue(any("deepline_credits exceeds limit" in error for error in errors))
        self.assertTrue(any("paid_calls exceeds limit" in error for error in errors))

    def test_target_and_shortfall_stop_reasons_are_consistent(self):
        reached = {
            "request": {"target_count": 1, "contact_fields": []},
            "summary": {"accepted_companies": 1},
            "accepted": [
                {
                    "company": {"canonical_name": "Example", "domain": "example.org"},
                    "primary_contact": {"full_name": "Ada Example"},
                }
            ],
            "stop_reason": "target_met",
        }
        self.assertEqual(VALIDATOR.validate_run(reached), [])

        short = shortfall_result()
        short.pop("stop_audit")
        errors = VALIDATOR.validate_run(short)
        self.assertIn("a target shortfall requires stop_audit", errors)

    def test_duplicate_domains_and_missing_requested_email_fail(self):
        contact = {"full_name": "Ada Example"}
        result = {
            "request": {"target_count": 2, "contact_fields": ["email"]},
            "summary": {"accepted_companies": 2},
            "accepted": [
                {
                    "company": {"canonical_name": "Example", "domain": "www.example.org"},
                    "primary_contact": contact,
                },
                {
                    "company": {"canonical_name": "Example duplicate", "domain": "example.org"},
                    "primary_contact": contact,
                },
            ],
            "stop_reason": "target_met",
        }
        errors = VALIDATOR.validate_run(result)
        self.assertTrue(any("duplicate canonical domains" in error for error in errors))
        self.assertEqual(sum("requires requested email" in error for error in errors), 2)

    def test_default_email_and_zerobounce_receipt_are_required(self):
        result = accepted_email_result()
        self.assertEqual(VALIDATOR.validate_run(result), [])

        result["accepted"][0]["primary_contact"].pop("email")
        result["accepted"][0]["primary_contact"].pop("email_validation")
        errors = VALIDATOR.validate_run(result)
        self.assertIn(
            "accepted[0].primary_contact requires requested email", errors
        )

        result = accepted_email_result()
        result["accepted"][0]["primary_contact"].pop("email_validation")
        errors = VALIDATOR.validate_run(result)
        self.assertTrue(any("ZeroBounce email_validation receipt" in error for error in errors))

    def test_only_explicit_zerobounce_invalid_status_fails(self):
        for status in ("valid", "catch-all", "spamtrap", "abuse", "do_not_mail", "unknown"):
            with self.subTest(status=status):
                self.assertEqual(VALIDATOR.validate_run(accepted_email_result(status)), [])

        errors = VALIDATOR.validate_run(accepted_email_result(" INVALID "))
        self.assertTrue(any("email_validation.status is invalid" in error for error in errors))

    def test_missing_status_or_unsuccessful_validation_route_is_unresolved(self):
        missing_status = accepted_email_result()
        missing_status["accepted"][0]["primary_contact"]["email_validation"]["status"] = ""
        errors = VALIDATOR.validate_run(missing_status)
        self.assertTrue(any("status is unresolved or missing" in error for error in errors))

        failed_route = accepted_email_result()
        failed_route["routes"][0]["provider_status"] = "timeout"
        errors = VALIDATOR.validate_run(failed_route)
        self.assertTrue(any("route email-validation-1 is unresolved" in error for error in errors))

        unaccounted_route = accepted_email_result()
        unaccounted_route["routes"][0]["paid_calls"] = 0
        errors = VALIDATOR.validate_run(unaccounted_route)
        self.assertTrue(any("must record its paid Deepline call" in error for error in errors))

    def test_email_validation_receipt_must_match_route_and_provider(self):
        wrong_provider = accepted_email_result()
        source = wrong_provider["accepted"][0]["primary_contact"]["email_validation"]["source"]
        source["provider"] = "public_web"
        errors = VALIDATOR.validate_run(wrong_provider)
        self.assertTrue(any("source.provider must be deepline" in error for error in errors))

        wrong_tool = accepted_email_result()
        source = wrong_tool["accepted"][0]["primary_contact"]["email_validation"]["source"]
        source["tool"] = "different-live-tool"
        errors = VALIDATOR.validate_run(wrong_tool)
        self.assertTrue(any("source.tool must match route" in error for error in errors))

    def test_stored_backup_email_must_pass_the_same_gate(self):
        result = accepted_email_result()
        backup_receipt = email_validation_receipt(
            status="invalid", route_id="email-validation-2", tool="second-live-validator"
        )
        backup_receipt["email"] = "bea@example.org"
        result["accepted"][0]["backup_contacts"] = [
            {
                "full_name": "Bea Example",
                "email": "bea@example.org",
                "email_validation": backup_receipt,
            }
        ]
        result["routes"].append(
            {
                "route_id": "email-validation-2",
                "phase": "email_validation",
                "provider": "deepline",
                "operation": "execute",
                "tool": "second-live-validator",
                "provider_status": "ok",
                "paid_calls": 1,
            }
        )

        errors = VALIDATOR.validate_run(result)
        self.assertTrue(
            any("backup_contacts[0].email_validation.status is invalid" in error for error in errors)
        )

    def test_stop_audit_counts_unique_company_reviews(self):
        result = shortfall_result()
        result["request"]["target_count"] = 2
        result["stop_audit"].update(
            {
                "target_shortfall": 2,
                "candidate_companies_reviewed": 1,
                "substantive_account_reviews": 0,
                "exclusion_only_rejections": 1,
            }
        )
        result["rejected"] = [
            {
                "stage": "account",
                "reason_code": "explicit_exclusion",
                "candidate": {"company": "Excluded Org", "domain": "excluded.org"},
            }
        ]
        self.assertEqual(VALIDATOR.validate_run(result), [])

        result["stop_audit"]["substantive_account_reviews"] = 1
        errors = VALIDATOR.validate_run(result)
        self.assertIn("stop_audit.substantive_account_reviews must equal 0", errors)

    def test_mixed_exclusion_and_review_is_substantive(self):
        result = shortfall_result()
        result["rejected"] = [
            {
                "stage": "account",
                "reason_code": "explicit_exclusion",
                "candidate": {"company": "Mixed Org", "domain": "mixed.org"},
            },
            {
                "stage": "account",
                "reason_code": "not_icp_fit",
                "candidate": {"company": "Mixed Org", "domain": "mixed.org"},
            },
        ]
        result["stop_audit"].update(
            {
                "candidate_companies_reviewed": 1,
                "substantive_account_reviews": 1,
                "exclusion_only_rejections": 0,
            }
        )
        self.assertEqual(VALIDATOR.validate_run(result), [])


if __name__ == "__main__":
    unittest.main()
