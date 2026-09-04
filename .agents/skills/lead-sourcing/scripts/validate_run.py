#!/usr/bin/env python3
"""Validate TYCHE run-completion and route-exhaustion invariants."""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Optional


ACTIONABLE_FRONTIER_STATES = {"untried", "continuable"}
FINAL_FRONTIER_STATES = {"exhausted", "blocked"}
PAID_PROVIDERS = {"deepline", "scrapingdog"}
SUPPORTED_RESULT_SCHEMA_VERSIONS = {"1.0", "1.1"}
DETERMINATE_PROVIDER_STATUSES = {"ok", "partial", "no_results"}
BLOCKING_PROVIDER_STATUSES = {
    "rate_limited",
    "auth_failed",
    "quota_exceeded",
    "timeout",
    "schema_error",
    "provider_error",
    "config_error",
}
ROUTE_OUTCOME_RECEIPT_STATUSES = {
    "provider_status": BLOCKING_PROVIDER_STATUSES,
    "budget_exhausted": {"quota_exceeded"},
    "route_not_connected": {"config_error"},
    "timeout_unknown": {"timeout"},
}
CONTACT_FIELD_NAMES = {"email", "phone"}
COST_BASES = {"actual", "estimated", "unknown"}
DEEPLINE_USD_PER_CREDIT = Decimal("0.10")
COST_OUTPUT_QUANTUM = Decimal("0.0001")


def _company_key(row: Any) -> Optional[str]:
    if not isinstance(row, dict):
        return None
    company = row.get("company")
    if isinstance(company, dict):
        domain = company.get("domain")
        name = company.get("canonical_name")
    else:
        candidate = row.get("candidate", {})
        if not isinstance(candidate, dict):
            return None
        domain = candidate.get("domain")
        name = candidate.get("company")
    if isinstance(domain, str) and domain.strip():
        value = domain.strip().lower()
        return value[4:] if value.startswith("www.") else value
    if isinstance(name, str) and name.strip():
        return "name:" + " ".join(name.lower().split())
    return None


def _account_outcomes(document: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for state in ("rejected", "unresolved"):
        values = document.get(state, [])
        if isinstance(values, list):
            rows.extend(
                row
                for row in values
                if isinstance(row, dict) and row.get("stage") == "account"
            )
    return rows


def _normalized_role(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    return " ".join(value.casefold().split())


def _effective_contact_fields(
    request: dict[str, Any], errors: list[str]
) -> set[str]:
    """Return normalized fields, applying the default email requirement."""

    if "contact_fields" not in request:
        return {"email"}
    fields = request.get("contact_fields")
    if not isinstance(fields, list):
        errors.append("request.contact_fields must be an array")
        return set()
    normalized: list[str] = []
    for index, field in enumerate(fields):
        if not isinstance(field, str) or field not in CONTACT_FIELD_NAMES:
            errors.append(
                f"request.contact_fields[{index}] must be email or phone"
            )
            continue
        normalized.append(field)
    if len(normalized) != len(set(normalized)):
        errors.append("request.contact_fields must not contain duplicates")
    return set(normalized)


def _nonempty_text(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _validate_email_receipt(
    contact: dict[str, Any],
    contact_path: str,
    routes_by_id: dict[str, list[dict[str, Any]]],
    errors: list[str],
) -> None:
    """Require a determinate Deepline ZeroBounce receipt for one stored email."""

    email = _nonempty_text(contact.get("email"))
    receipt = contact.get("email_validation")
    if email is None:
        if receipt is not None:
            errors.append(f"{contact_path}.email_validation requires a stored email")
        return
    if not isinstance(receipt, dict):
        errors.append(
            f"{contact_path}.email requires a Deepline ZeroBounce email_validation receipt"
        )
        return

    receipt_email = _nonempty_text(receipt.get("email"))
    if receipt_email is None:
        errors.append(f"{contact_path}.email_validation.email is required")
    elif receipt_email.casefold() != email.casefold():
        errors.append(
            f"{contact_path}.email_validation.email must match the contact email"
        )

    status = _nonempty_text(receipt.get("status"))
    if status is None:
        errors.append(
            f"{contact_path}.email_validation.status is unresolved or missing"
        )
    elif status.casefold() == "invalid":
        errors.append(f"{contact_path}.email_validation.status is invalid")

    source = receipt.get("source")
    if not isinstance(source, dict):
        errors.append(f"{contact_path}.email_validation.source is required")
        return
    if str(source.get("provider", "")).strip().casefold() != "deepline":
        errors.append(
            f"{contact_path}.email_validation.source.provider must be deepline"
        )
    if str(source.get("validator", "")).strip().casefold() != "zerobounce":
        errors.append(
            f"{contact_path}.email_validation.source.validator must be zerobounce"
        )
    operation = _nonempty_text(source.get("operation"))
    if operation != "execute":
        errors.append(
            f"{contact_path}.email_validation.source.operation must be execute"
        )
    tool = _nonempty_text(source.get("tool"))
    if tool is None:
        errors.append(f"{contact_path}.email_validation.source.tool is required")
    route_id = _nonempty_text(source.get("route_id"))
    if route_id is None:
        errors.append(f"{contact_path}.email_validation.source.route_id is required")
        return

    matching_routes = routes_by_id.get(route_id, [])
    if len(matching_routes) != 1:
        errors.append(
            f"{contact_path}.email_validation.source.route_id must identify one route receipt"
        )
        return
    route = matching_routes[0]
    if route.get("provider") != "deepline":
        errors.append(f"email validation route {route_id} must use deepline")
    if route.get("phase") != "email_validation":
        errors.append(f"email validation route {route_id} has the wrong phase")
    if route.get("operation") != "execute":
        errors.append(f"email validation route {route_id} must use execute")
    if tool is not None and route.get("tool") != tool:
        errors.append(
            f"{contact_path}.email_validation.source.tool must match route {route_id}"
        )
    if route.get("provider_status") not in {"ok", "partial"}:
        errors.append(
            f"email validation route {route_id} is unresolved or unsuccessful"
        )
    paid_calls = route.get("paid_calls")
    if not isinstance(paid_calls, int) or isinstance(paid_calls, bool) or paid_calls < 1:
        errors.append(
            f"email validation route {route_id} must record its paid Deepline call"
        )


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _decimal(value: Any) -> Optional[Decimal]:
    if not _number(value):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _json_decimal(value: Decimal, quantum: Optional[Decimal] = None) -> int | float:
    """Return a stable JSON number, with optional deterministic rounding."""

    if quantum is not None:
        value = value.quantize(quantum, rounding=ROUND_HALF_UP)
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def calculate_cost_summary(document: dict[str, Any]) -> dict[str, Any]:
    """Calculate provider cost bounds from route receipts.

    Legacy routes without ``cost_basis`` remain unclassified and unknown. This
    keeps the calculator useful for old artifacts without promoting legacy
    planning values to confirmed actual costs or changing their validation.
    """

    summary = document.get("summary", {})
    accepted_contacts = summary.get("accepted_contacts") if isinstance(summary, dict) else None
    if (
        not isinstance(accepted_contacts, int)
        or isinstance(accepted_contacts, bool)
        or accepted_contacts < 0
    ):
        accepted = document.get("accepted", [])
        accepted_contacts = len(accepted) if isinstance(accepted, list) else 0

    provider_totals: dict[str, dict[str, Any]] = {
        provider: {
            "confirmed": Decimal("0"),
            "maximum": Decimal("0"),
            "unknown": False,
        }
        for provider in PAID_PROVIDERS
    }
    has_estimated = False
    has_unknown = False
    routes = document.get("routes", [])
    if not isinstance(routes, list):
        routes = []

    for route in routes:
        if not isinstance(route, dict):
            continue
        provider = route.get("provider")
        paid_calls = route.get("paid_calls")
        if provider not in PAID_PROVIDERS or not isinstance(paid_calls, int) or paid_calls < 1:
            continue

        cost = _decimal(route.get("cost_credits"))
        upper = _decimal(route.get("cost_upper_bound_credits"))
        basis = route.get("cost_basis")
        if basis not in COST_BASES:
            basis = "unknown"

        totals = provider_totals[provider]
        if basis == "actual" and cost is not None and cost >= 0:
            totals["confirmed"] += cost
            totals["maximum"] += cost
        elif basis == "estimated" and upper is not None and upper >= 0:
            totals["maximum"] += upper
            has_estimated = True
        else:
            totals["unknown"] = True
            has_unknown = True

    if has_unknown:
        status = "unknown"
    elif has_estimated:
        status = "estimated_range"
    else:
        status = "exact"

    providers: dict[str, dict[str, int | float | None]] = {}
    for provider in sorted(PAID_PROVIDERS):
        totals = provider_totals[provider]
        providers[provider] = {
            "confirmed_credits": _json_decimal(totals["confirmed"]),
            "maximum_credits": None
            if totals["unknown"]
            else _json_decimal(totals["maximum"]),
        }

    deepline = providers["deepline"]
    deepline_confirmed_usd = (
        Decimal(str(deepline["confirmed_credits"])) * DEEPLINE_USD_PER_CREDIT
    )
    deepline_maximum_usd = (
        None
        if deepline["maximum_credits"] is None
        else Decimal(str(deepline["maximum_credits"])) * DEEPLINE_USD_PER_CREDIT
    )
    deepline_summary: dict[str, int | float | None] = {
        "usd_per_credit": _json_decimal(DEEPLINE_USD_PER_CREDIT),
        "confirmed_credits": deepline["confirmed_credits"],
        "maximum_credits": deepline["maximum_credits"],
        "confirmed_usd": _json_decimal(deepline_confirmed_usd, COST_OUTPUT_QUANTUM),
        "maximum_usd": None
        if deepline_maximum_usd is None
        else _json_decimal(deepline_maximum_usd, COST_OUTPUT_QUANTUM),
    }

    if accepted_contacts == 0:
        per_lead = {"minimum": None, "maximum": None}
    else:
        denominator = Decimal(accepted_contacts)
        per_lead = {
            "minimum": _json_decimal(
                deepline_confirmed_usd / denominator, COST_OUTPUT_QUANTUM
            ),
            "maximum": None
            if deepline_maximum_usd is None
            else _json_decimal(
                deepline_maximum_usd / denominator, COST_OUTPUT_QUANTUM
            ),
        }

    return {
        "status": status,
        "accepted_leads": accepted_contacts,
        "deepline": deepline_summary,
        "scrapingdog": providers["scrapingdog"],
        "deepline_cost_per_lead_usd": per_lead,
    }


def _validate_cost_accounting(document: dict[str, Any], errors: list[str]) -> None:
    """Enforce the version 1.1 route-cost and summary contract."""

    if document.get("schema_version") != "1.1":
        return

    summary = document.get("summary", {})
    accepted_contacts = summary.get("accepted_contacts") if isinstance(summary, dict) else None
    if (
        not isinstance(accepted_contacts, int)
        or isinstance(accepted_contacts, bool)
        or accepted_contacts < 0
    ):
        errors.append("summary.accepted_contacts must be a non-negative integer for cost accounting")
    else:
        accepted = document.get("accepted", [])
        if isinstance(accepted, list) and accepted_contacts != len(accepted):
            errors.append("summary.accepted_contacts must equal len(accepted) for cost accounting")

    routes = document.get("routes", [])
    if not isinstance(routes, list):
        errors.append("routes must be an array for cost accounting")
        routes = []
    for index, route in enumerate(routes):
        if not isinstance(route, dict):
            continue
        path = f"routes[{index}]"
        missing = [
            field
            for field in ("cost_credits", "cost_upper_bound_credits", "cost_basis")
            if field not in route
        ]
        if missing:
            errors.append(f"{path} requires cost fields: {', '.join(missing)}")
            continue

        paid_calls = route.get("paid_calls")
        provider = route.get("provider")
        cost = _decimal(route.get("cost_credits"))
        upper = _decimal(route.get("cost_upper_bound_credits"))
        basis = route.get("cost_basis")
        if basis not in COST_BASES:
            errors.append(f"{path}.cost_basis must be actual, estimated, or unknown")
            continue
        if not isinstance(paid_calls, int) or isinstance(paid_calls, bool) or paid_calls < 0:
            continue
        if provider == "public_web" and paid_calls > 0:
            errors.append(f"{path}.paid_calls must be 0 for public_web")

        if paid_calls == 0:
            if basis != "actual" or cost != Decimal("0") or upper != Decimal("0"):
                errors.append(
                    f"{path} with no paid call must use actual cost with 0 actual and upper-bound credits"
                )
        elif basis == "actual":
            if cost is None or cost < 0:
                errors.append(f"{path}.cost_credits must be non-negative for actual cost")
            if upper is None or upper < 0:
                errors.append(
                    f"{path}.cost_upper_bound_credits must be non-negative for actual cost"
                )
            elif cost is not None and upper != cost:
                errors.append(
                    f"{path}.cost_upper_bound_credits must equal actual cost_credits"
                )
        elif basis == "estimated":
            if route.get("cost_credits") is not None:
                errors.append(f"{path}.cost_credits must be null for estimated cost")
            if upper is None or upper < 0:
                errors.append(
                    f"{path}.cost_upper_bound_credits must be a non-negative estimate"
                )
        else:
            if route.get("cost_credits") is not None:
                errors.append(f"{path}.cost_credits must be null for unknown cost")
            if route.get("cost_upper_bound_credits") is not None:
                errors.append(
                    f"{path}.cost_upper_bound_credits must be null for unknown cost"
                )

    expected = calculate_cost_summary(document)
    actual = document.get("cost_summary")
    if actual != expected:
        errors.append(
            "cost_summary must equal calculated route cost summary: "
            + json.dumps(expected, sort_keys=True)
        )

    budget = document.get("budget")
    limits = budget.get("limits") if isinstance(budget, dict) else None
    spent = budget.get("spent") if isinstance(budget, dict) else None
    if isinstance(limits, dict):
        for provider in sorted(PAID_PROVIDERS):
            limit = _decimal(limits.get(f"{provider}_credits"))
            maximum = _decimal(expected[provider]["maximum_credits"])
            actual_spend = (
                _decimal(spent.get(f"{provider}_credits"))
                if isinstance(spent, dict)
                else None
            )
            if (
                limit is not None
                and maximum is not None
                and maximum > limit
                and not (actual_spend is not None and actual_spend > limit)
            ):
                errors.append(
                    f"cost_summary.{provider}.maximum_credits exceeds "
                    f"budget.limits.{provider}_credits {limit}"
                )


def _validate_budget_accounting(document: dict[str, Any], errors: list[str]) -> None:
    """Validate exact route accounting when the output budget is present."""

    budget = document.get("budget")
    if budget is None:
        return
    if not isinstance(budget, dict):
        errors.append("budget must be an object")
        return
    routes = document.get("routes", [])
    if not isinstance(routes, list):
        errors.append("routes must be an array")
        return

    route_paid_calls = 0
    known_costs = {provider: Decimal("0") for provider in PAID_PROVIDERS}
    unknown_cost_providers: set[str] = set()
    for index, route in enumerate(routes):
        if not isinstance(route, dict):
            continue
        paid_calls = route.get("paid_calls")
        if not isinstance(paid_calls, int) or isinstance(paid_calls, bool) or paid_calls < 0:
            errors.append(f"routes[{index}].paid_calls must be a non-negative integer")
            continue
        route_paid_calls += paid_calls
        provider = route.get("provider")
        if provider not in PAID_PROVIDERS:
            continue
        cost = route.get("cost_credits")
        if paid_calls > 0 and cost is None:
            unknown_cost_providers.add(provider)
        elif cost is not None:
            value = _decimal(cost)
            if value is None or value < 0:
                errors.append(f"routes[{index}].cost_credits must be a non-negative number or null")
            else:
                known_costs[provider] += value

    paid_calls = budget.get("paid_calls")
    if not isinstance(paid_calls, int) or isinstance(paid_calls, bool) or paid_calls < 0:
        errors.append("budget.paid_calls must be a non-negative integer")
    elif paid_calls != route_paid_calls:
        errors.append(
            f"budget.paid_calls must equal route paid-call sum ({route_paid_calls})"
        )

    spent = budget.get("spent")
    if not isinstance(spent, dict):
        errors.append("budget.spent must be an object")
        spent = {}
    for provider in sorted(PAID_PROVIDERS):
        actual = spent.get(f"{provider}_credits")
        if provider in unknown_cost_providers:
            if actual is not None:
                errors.append(
                    f"budget.spent.{provider}_credits must be null when a paid route cost is unknown"
                )
        else:
            expected = known_costs[provider]
            actual_decimal = _decimal(actual)
            if actual_decimal is None:
                errors.append(
                    f"budget.spent.{provider}_credits must equal known route cost sum {expected}"
                )
            elif actual_decimal != expected:
                errors.append(
                    f"budget.spent.{provider}_credits must equal known route cost sum {expected}"
                )

    status = budget.get("status")
    if unknown_cost_providers:
        if status != "unknown":
            errors.append("budget.status must be unknown when any paid route cost is unknown")
    elif status == "unknown":
        errors.append("budget.status cannot be unknown when all paid route costs are known")
    if status == "within_budget":
        if unknown_cost_providers or any(
            _decimal(spent.get(f"{provider}_credits")) is None
            for provider in PAID_PROVIDERS
        ):
            errors.append("within_budget requires known actual provider spend")

    limits = budget.get("limits")
    if not isinstance(limits, dict):
        errors.append("budget.limits must be an object")
        limits = {}
    for provider in sorted(PAID_PROVIDERS):
        limit = _decimal(limits.get(f"{provider}_credits"))
        actual = _decimal(spent.get(f"{provider}_credits"))
        if limit is not None and actual is not None and actual > limit:
            errors.append(
                f"budget.spent.{provider}_credits exceeds limit {limit}"
            )
    call_limit = limits.get("max_paid_calls")
    if (
        isinstance(call_limit, int)
        and not isinstance(call_limit, bool)
        and call_limit >= 0
        and isinstance(paid_calls, int)
        and not isinstance(paid_calls, bool)
        and paid_calls > call_limit
    ):
        errors.append(f"budget.paid_calls exceeds limit {call_limit}")


def validate_run(document: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(document, dict):
        return ["results.json must contain one JSON object"]
    schema_version = document.get("schema_version")
    if "schema_version" in document and (
        not isinstance(schema_version, str)
        or schema_version not in SUPPORTED_RESULT_SCHEMA_VERSIONS
    ):
        errors.append("schema_version must be 1.0 or 1.1")

    request = document.get("request", {})
    summary = document.get("summary", {})
    accepted = document.get("accepted", [])
    if not isinstance(request, dict) or not isinstance(summary, dict):
        return ["request and summary must be objects"]
    if not isinstance(accepted, list):
        return ["accepted must be an array"]

    target = request.get("target_count")
    if not isinstance(target, int) or isinstance(target, bool) or target < 1:
        return ["request.target_count must be a positive integer"]

    accepted_count = len(accepted)
    if summary.get("accepted_companies") != accepted_count:
        errors.append("summary.accepted_companies must equal len(accepted)")

    grouped_roles = request.get("contact_role_groups")
    normalized_groups: dict[str, set[str]] = {}
    if grouped_roles is not None:
        if not isinstance(grouped_roles, dict):
            errors.append("request.contact_role_groups must be an object")
        else:
            flattened: list[str] = []
            for group_name in ("primary", "secondary"):
                values = grouped_roles.get(group_name)
                if not isinstance(values, list):
                    errors.append(
                        f"request.contact_role_groups.{group_name} must be an array"
                    )
                    normalized_groups[group_name] = set()
                    continue
                normalized = [
                    role for role in map(_normalized_role, values) if role is not None
                ]
                if len(normalized) != len(set(normalized)):
                    errors.append(
                        f"request.contact_role_groups.{group_name} contains duplicate roles"
                    )
                normalized_groups[group_name] = set(normalized)
                flattened.extend(normalized)

            if len(flattened) != len(set(flattened)):
                errors.append(
                    "request.contact_role_groups must not repeat a role across groups"
                )
            requested_roles = request.get("requested_roles")
            if not isinstance(requested_roles, list):
                errors.append(
                    "request.requested_roles must be an array when contact_role_groups is present"
                )
            else:
                normalized_requested = [
                    role
                    for role in map(_normalized_role, requested_roles)
                    if role is not None
                ]
                if len(normalized_requested) != len(set(normalized_requested)):
                    errors.append("request.requested_roles contains duplicate roles")
                if set(normalized_requested) != set(flattened):
                    errors.append(
                        "request.requested_roles must equal the contact_role_groups union"
                    )

    accepted_domains: list[str] = []
    requested_fields = _effective_contact_fields(request, errors)
    route_rows = document.get("routes", [])
    routes_by_id: dict[str, list[dict[str, Any]]] = {}
    if isinstance(route_rows, list):
        for route in route_rows:
            if not isinstance(route, dict):
                continue
            route_id = route.get("route_id")
            if isinstance(route_id, str) and route_id.strip():
                routes_by_id.setdefault(route_id.strip(), []).append(route)
    requested_roles = request.get("requested_roles")
    normalized_requested_roles = {
        role
        for role in map(_normalized_role, requested_roles)
        if role is not None
    } if isinstance(requested_roles, list) else set()
    for index, row in enumerate(accepted):
        if not isinstance(row, dict):
            errors.append(f"accepted[{index}] must be an object")
            continue
        company = row.get("company")
        domain = company.get("domain") if isinstance(company, dict) else None
        if not isinstance(domain, str) or not domain.strip():
            errors.append(f"accepted[{index}] requires a canonical company domain")
        else:
            canonical = domain.strip().lower()
            accepted_domains.append(
                canonical[4:] if canonical.startswith("www.") else canonical
            )

        primary = row.get("primary_contact")
        if not isinstance(primary, dict):
            errors.append(f"accepted[{index}] requires primary_contact")
            continue
        contacts_to_validate: list[tuple[str, dict[str, Any]]] = [
            (f"accepted[{index}].primary_contact", primary)
        ]
        for collection_name in ("backup_contacts",):
            collection = row.get(collection_name)
            if collection is None:
                continue
            if not isinstance(collection, list):
                errors.append(f"accepted[{index}].{collection_name} must be an array")
                continue
            for contact_index, contact in enumerate(collection):
                if not isinstance(contact, dict):
                    errors.append(
                        f"accepted[{index}].{collection_name}[{contact_index}] must be an object"
                    )
                    continue
                contacts_to_validate.append(
                    (f"accepted[{index}].{collection_name}[{contact_index}]", contact)
                )
        for contact_path, contact in contacts_to_validate:
            requested_role = _normalized_role(contact.get("requested_role"))
            if normalized_requested_roles and requested_role not in normalized_requested_roles:
                errors.append(
                    f"{contact_path}.requested_role is not in request.requested_roles"
                )
            role_group = contact.get("role_group")
            if role_group is not None:
                if role_group not in {"primary", "secondary"}:
                    errors.append(f"{contact_path}.role_group is invalid")
                elif grouped_roles is not None and requested_role not in normalized_groups.get(
                    role_group, set()
                ):
                    errors.append(
                        f"{contact_path} requested_role does not match role_group"
                    )
            if "email" in contact or "email_validation" in contact:
                _validate_email_receipt(
                    contact, contact_path, routes_by_id, errors
                )
        for field in requested_fields:
            value = primary.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(
                    f"accepted[{index}].primary_contact requires requested {field}"
                )
            elif field == "email" and (
                "@" not in value
                or " " in value
                or value.startswith("@")
                or value.endswith("@")
            ):
                errors.append(f"accepted[{index}].primary_contact.email is invalid")

    duplicate_domains = sorted(
        domain for domain in set(accepted_domains) if accepted_domains.count(domain) > 1
    )
    if duplicate_domains:
        errors.append(
            "accepted companies contain duplicate canonical domains: "
            + ", ".join(duplicate_domains)
        )

    _validate_budget_accounting(document, errors)
    _validate_cost_accounting(document, errors)

    stop_reason = document.get("stop_reason")
    shortfall = max(0, target - accepted_count)
    if shortfall == 0:
        if stop_reason != "target_met":
            errors.append("a run that reaches target_count must stop with target_met")
    elif stop_reason == "target_met":
        errors.append("target_met is invalid while accepted companies are below target_count")

    audit = document.get("stop_audit")
    if shortfall and not isinstance(audit, dict):
        errors.append("a target shortfall requires stop_audit")
        return errors
    if not isinstance(audit, dict):
        return errors

    if audit.get("target_shortfall") != shortfall:
        errors.append("stop_audit.target_shortfall is inconsistent with the target")
    if audit.get("frontier_complete") is not True:
        errors.append("stop_audit.frontier_complete must be true")

    account_rows = _account_outcomes(document)
    reviewed_keys = {
        key
        for key in [_company_key(row) for row in accepted + account_rows]
        if key is not None
    }
    accepted_keys = {key for key in map(_company_key, accepted) if key is not None}
    reasons_by_key: dict[str, set[str]] = {}
    for row in account_rows:
        key = _company_key(row)
        reason = row.get("reason_code")
        if key is not None and isinstance(reason, str):
            reasons_by_key.setdefault(key, set()).add(reason)
    exclusion_keys = {
        key
        for key, reasons in reasons_by_key.items()
        if reasons == {"explicit_exclusion"} and key not in accepted_keys
    }
    duplicate_count = sum(
        1 for row in account_rows if row.get("reason_code") == "duplicate_domain"
    )
    expected_counts = {
        "candidate_companies_reviewed": len(reviewed_keys),
        "exclusion_only_rejections": len(exclusion_keys),
        "substantive_account_reviews": len(reviewed_keys - exclusion_keys),
        "duplicate_candidates": duplicate_count,
    }
    for field, expected in expected_counts.items():
        if audit.get(field) != expected:
            errors.append(f"stop_audit.{field} must equal {expected}")

    frontier = audit.get("route_frontier")
    if not isinstance(frontier, list):
        errors.append("stop_audit.route_frontier must be an array")
        return errors

    frontier_by_id: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(frontier):
        if not isinstance(item, dict):
            errors.append(f"route_frontier[{index}] must be an object")
            continue
        route_id = item.get("route_id")
        if not isinstance(route_id, str) or not route_id.strip():
            errors.append(f"route_frontier[{index}].route_id must be non-empty")
            continue
        if route_id in frontier_by_id:
            errors.append(f"route_frontier contains duplicate route_id {route_id}")
        frontier_by_id[route_id] = item

    route_receipts = [
        route for route in document.get("routes", []) if isinstance(route, dict)
    ]
    receipts_by_id: dict[str, list[dict[str, Any]]] = {}
    for route in route_receipts:
        route_id = route.get("route_id")
        if isinstance(route_id, str):
            receipts_by_id.setdefault(route_id, []).append(route)
    duplicate_receipt_ids = sorted(
        route_id for route_id, rows in receipts_by_id.items() if len(rows) > 1
    )
    if duplicate_receipt_ids:
        errors.append(
            "routes contain duplicate route_id attempts: "
            + ", ".join(duplicate_receipt_ids)
        )
    attempted_ids = set(receipts_by_id)
    missing_receipts = sorted(attempted_ids - set(frontier_by_id))
    if missing_receipts:
        errors.append(
            "attempted routes missing from route_frontier: " + ", ".join(missing_receipts)
        )

    route_outcomes = [
        row
        for state in ("rejected", "unresolved")
        for row in document.get(state, [])
        if isinstance(row, dict)
        and row.get("stage") == "route"
        and isinstance(row.get("route_id"), str)
    ]
    route_outcomes_by_id: dict[str, list[dict[str, Any]]] = {}
    for outcome in route_outcomes:
        route_outcomes_by_id.setdefault(outcome["route_id"], []).append(outcome)
    duplicate_outcome_ids = sorted(
        route_id for route_id, rows in route_outcomes_by_id.items() if len(rows) > 1
    )
    if duplicate_outcome_ids:
        errors.append(
            "route outcomes contain duplicate route_id attempts: "
            + ", ".join(duplicate_outcome_ids)
        )
    route_outcome_ids = set(route_outcomes_by_id)
    missing_outcome_frontier = sorted(route_outcome_ids - set(frontier_by_id))
    if missing_outcome_frontier:
        errors.append(
            "route outcomes missing from route_frontier: "
            + ", ".join(missing_outcome_frontier)
        )
    shared_route_ids = sorted(set(receipts_by_id) & route_outcome_ids)
    reused_route_ids = [
        route_id
        for route_id in shared_route_ids
        if any(
            not any(
                receipt.get("provider_status") in ROUTE_OUTCOME_RECEIPT_STATUSES.get(
                    outcome.get("reason_code"), set()
                )
                for receipt in receipts_by_id[route_id]
            )
            for outcome in route_outcomes_by_id[route_id]
        )
    ]
    if reused_route_ids:
        errors.append(
            "route_id reused across a completed receipt and a separate route outcome: "
            + ", ".join(reused_route_ids)
        )
    unsupported_exhaustion = sorted(
        route_id
        for route_id, item in frontier_by_id.items()
        if item.get("state") == "exhausted" and route_id not in attempted_ids
    )
    if unsupported_exhaustion:
        errors.append(
            "exhausted routes missing attempt receipts: "
            + ", ".join(unsupported_exhaustion)
        )
    unsupported_blocks = sorted(
        route_id
        for route_id, item in frontier_by_id.items()
        if item.get("state") == "blocked"
        and route_id not in attempted_ids | route_outcome_ids
    )
    if unsupported_blocks:
        errors.append(
            "blocked routes missing attempt or route-outcome receipts: "
            + ", ".join(unsupported_blocks)
        )

    for route_id, item in frontier_by_id.items():
        statuses = {
            receipt.get("provider_status")
            for receipt in receipts_by_id.get(route_id, [])
            if isinstance(receipt.get("provider_status"), str)
        }
        if item.get("state") == "exhausted":
            blocking = sorted(statuses & BLOCKING_PROVIDER_STATUSES)
            if blocking:
                errors.append(
                    f"exhausted route {route_id} has blocking provider status: "
                    + ", ".join(blocking)
                )
            if not statuses & DETERMINATE_PROVIDER_STATUSES:
                errors.append(
                    f"exhausted route {route_id} requires a determinate attempt receipt"
                )
            basis = item.get("exhaustion_basis")
            if not isinstance(basis, str) or not basis.strip():
                errors.append(f"exhausted route {route_id} requires exhaustion_basis")
        elif (
            item.get("state") == "blocked"
            and statuses
            and route_id not in route_outcome_ids
        ):
            if not statuses & BLOCKING_PROVIDER_STATUSES:
                errors.append(
                    f"blocked route {route_id} requires a blocking provider status"
                )

    if shortfall:
        if not frontier:
            errors.append("a target shortfall requires at least one route-frontier item")
        actionable = sorted(
            route_id
            for route_id, item in frontier_by_id.items()
            if item.get("state") in ACTIONABLE_FRONTIER_STATES
        )
        invalid_states = sorted(
            route_id
            for route_id, item in frontier_by_id.items()
            if item.get("state") not in FINAL_FRONTIER_STATES | ACTIONABLE_FRONTIER_STATES
        )
        if actionable:
            errors.append(
                "run must continue while route_frontier is actionable: "
                + ", ".join(actionable)
            )
        if invalid_states:
            errors.append(
                "route_frontier has invalid or missing states: "
                + ", ".join(invalid_states)
            )
        for route_id, item in frontier_by_id.items():
            if item.get("state") in FINAL_FRONTIER_STATES:
                reason = item.get("reason")
                if not isinstance(reason, str) or not reason.strip():
                    errors.append(f"final route {route_id} requires a reason")

    capacity = audit.get("provider_call_capacity")
    if not isinstance(capacity, dict):
        errors.append("stop_audit.provider_call_capacity must be an object")
    else:
        budget = document.get("budget")
        spent = budget.get("spent") if isinstance(budget, dict) else None
        if isinstance(spent, dict):
            for provider in PAID_PROVIDERS:
                if (
                    spent.get(f"{provider}_credits") is None
                    and capacity.get(provider) != "unknown"
                ):
                    errors.append(
                        f"stop_audit.provider_call_capacity.{provider} must be unknown when actual spend is unknown"
                    )
    if isinstance(capacity, dict) and shortfall and stop_reason == "budget_exhausted":
        available = sorted(
            provider
            for provider in PAID_PROVIDERS
            if capacity.get(provider) == "available"
        )
        unknown = sorted(
            provider
            for provider in PAID_PROVIDERS
            if capacity.get(provider) == "unknown"
        )
        if available:
            errors.append(
                "budget_exhausted is invalid while a paid provider is available: "
                + ", ".join(available)
            )
        if unknown:
            errors.append(
                "budget_exhausted requires known provider capacity; unknown: "
                + ", ".join(unknown)
            )

    if shortfall and stop_reason == "provider_stop":
        blocked_routes = [
            item for item in frontier_by_id.values() if item.get("state") == "blocked"
        ]
        if not blocked_routes:
            errors.append("provider_stop requires at least one blocked route")
        if isinstance(capacity, dict):
            available = sorted(
                provider
                for provider in PAID_PROVIDERS
                if capacity.get(provider) == "available"
            )
            if available:
                errors.append(
                    "provider_stop is invalid while a paid provider is available: "
                    + ", ".join(available)
                )

    if shortfall and stop_reason == "no_productive_route":
        if not any(
            item.get("state") == "exhausted" for item in frontier_by_id.values()
        ):
            errors.append("no_productive_route requires at least one exhausted route")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate TYCHE run-completion and route-exhaustion invariants."
    )
    parser.add_argument("results", type=pathlib.Path)
    parser.add_argument(
        "--show-cost-summary",
        action="store_true",
        help="include the route-derived cost summary in validator output",
    )
    args = parser.parse_args()
    try:
        document = json.loads(args.results.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "errors": [str(exc)]}))
        return 2

    errors = validate_run(document)
    output: dict[str, Any] = {"valid": not errors, "errors": errors}
    if args.show_cost_summary:
        output["calculated_cost_summary"] = calculate_cost_summary(document)
    print(json.dumps(output, sort_keys=True))
    return 0 if not errors else 2


if __name__ == "__main__":
    sys.exit(main())
