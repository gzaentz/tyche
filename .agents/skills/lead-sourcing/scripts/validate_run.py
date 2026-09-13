#!/usr/bin/env python3
"""Validate TYCHE run-completion and route-exhaustion invariants."""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import re
import sys
import unicodedata
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, timezone
from typing import Any, Optional


ACTIONABLE_FRONTIER_STATES = {"untried", "continuable"}
FINAL_FRONTIER_STATES = {"exhausted", "blocked"}
PAID_PROVIDERS = {"deepline", "scrapingdog"}
SUPPORTED_RESULT_SCHEMA_VERSIONS = {"1.0", "1.1", "1.2"}
DETERMINATE_PROVIDER_STATUSES = {"ok", "partial", "no_results"}
EMAIL_FALLBACK_FAILURES = {"provider_error", "timeout", "rate_limited", "auth_failed", "quota_exceeded"}
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
NEXT_LEAD_REVIEW_CREDITS = Decimal("5")


def _identity(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    value = re.sub(r"^https?://", "", value.strip().casefold()).rstrip("/")
    value = re.sub(r"^www\.", "", value)
    value = unicodedata.normalize("NFKD", value)
    return "".join(c for c in value if c.isalnum())


def excluded_company(request: dict, row: dict) -> bool:
    """Match known company/owner aliases; discovering ownership still needs evidence."""
    company = row.get("company")
    company = company if isinstance(company, dict) else row.get("candidate", row)
    if not isinstance(company, dict):
        return False
    names = [company.get(k) for k in ("canonical_name", "company", "domain", "owner_group")]
    aliases = company.get("aliases", [])
    if isinstance(aliases, list):
        names.extend(aliases)
    icp = request.get("icp", {})
    exclusions = request.get("exclusions", [])
    exclusions = list(exclusions) if isinstance(exclusions, list) else []
    if isinstance(icp, dict) and isinstance(icp.get("exclusions"), list):
        exclusions.extend(icp["exclusions"])
    return bool({_identity(n) for n in names} & ({_identity(n) for n in exclusions} - {""}))


def qualification_errors(document: dict) -> list[str]:
    errors = []
    owners = set()
    for state in ("accepted", "rejected", "unresolved"):
        for index, row in enumerate(document.get(state, [])):
            if not isinstance(row, dict):
                continue
            path = f"{state}[{index}]"
            checks = row.get("qualification_checks", [])
            if not isinstance(checks, list):
                errors.append(f"{path}.qualification_checks must be an array")
                continue
            required = [c for c in checks if isinstance(c, dict) and c.get("importance") == "required"]
            # Compare structured facts to the saved ICP, never to a new band
            # improvised in a row's prose (e.g. rejecting 3 against 1-200).
            request = document.get("request", {})
            icp = request.get("icp", {}) if isinstance(request, dict) else {}
            band = icp.get("company_size", {}) if isinstance(icp, dict) else {}
            company = row.get("company", row.get("candidate", {}))
            employee_range = company.get("employee_range") if isinstance(company, dict) else None
            bounds = employee_range_bounds(employee_range)
            if employee_range is not None and bounds is None:
                errors.append(f"{path}: employee_range must be a LinkedIn range")
            elif bounds is not None and isinstance(band, dict) and band:
                lower, upper = band.get("min_employees", 0), band.get("max_employees")
                if type(lower) is int and (upper is None or type(upper) is int):
                    start, end = bounds
                    fits = start >= lower and (upper is None or end is not None and end <= upper)
                    outside = end is not None and end < lower or upper is not None and start > upper
                    size_checks = [c for c in required if _identity(c.get("criterion")) in {"companysize", "employeecount", "employeerange"}]
                    if any(c.get("status") == "pass" and not fits or c.get("status") == "fail" and not outside for c in size_checks):
                        errors.append(f"{path}: company_size decision contradicts request.icp.company_size")
                    if not fits and (state == "accepted" or (state == "unresolved" and row.get("stage") == "contact")):
                        errors.append(f"{path}: employee_range is outside or only partly inside request.icp.company_size")
            failed = [c for c in required if c.get("status") == "fail" and c.get("evidence")]
            if state == "accepted" or (state == "unresolved" and row.get("stage") == "contact"):
                if excluded_company(document.get("request", {}), row):
                    errors.append(f"{path}: excluded company cannot pass the account gate")
                if any(c.get("status") != "pass" or not c.get("evidence") for c in required):
                    errors.append(f"{path}: missing or failed required evidence must remain account-unresolved")
            if state == "rejected" and row.get("reason_code") == "not_icp_fit" and not failed:
                errors.append(f"{path}: not_icp_fit requires an evidenced required failure; unknown is unresolved")
            if state == "accepted":
                company = row.get("company", {})
                owner = _identity(company.get("owner_group")) if isinstance(company, dict) else ""
                if owner and owner in owners:
                    errors.append(f"{path}: duplicate owner group")
                owners.add(owner)
    return errors


def progress_snapshot(document: dict) -> list[str]:
    """Stable verified milestones, not returned rows or model-written progress totals."""
    facts = set()
    for state in ("accepted", "unresolved", "rejected"):
        for row in document.get(state, []):
            if not isinstance(row, dict) or not (key := _company_key(row)):
                continue
            if state == "accepted":
                facts.add(f"{key}:lead")
            if state == "accepted" or (state == "unresolved" and row.get("stage") == "contact"):
                facts.add(f"{key}:account")
            checks = row.get("qualification_checks", [])
            for check in checks if isinstance(checks, list) else []:
                if (isinstance(check, dict) and check.get("importance") == "required"
                        and check.get("status") in {"pass", "fail"} and check.get("evidence")):
                    facts.add(f"{key}:evidence:{_identity(check.get('criterion'))}")
            contact = row.get("primary_contact", row.get("candidate", {}))
            if (isinstance(contact, dict) and contact.get("full_name")
                    and contact.get("current_title") and contact.get("evidence")):
                facts.add(f"{key}:buyer:{_identity(contact['full_name'])}")
    return sorted(facts)


def stalled_approaches(document: dict) -> set[str]:
    attempts = [r for r in document.get("routes", []) if isinstance(r, dict)
                and r.get("entity_type") != "tool_catalog" and isinstance(r.get("approach"), str)
                and isinstance(r.get("progress_before"), list)
                and all(isinstance(k, str) for k in r["progress_before"])
                and r.get("provider_status") in {"ok", "no_results"}]
    if len(attempts) < 2:
        return set()
    first, second = attempts[-2:]
    if (set(second["progress_before"]) - set(first["progress_before"])
            or set(progress_snapshot(document)) - set(second["progress_before"])):
        return set()
    return {first["approach"], second["approach"]}


def _route_scope(route: dict) -> Optional[str]:
    return route.get("scope") or ("discovery" if route.get("phase") == "account_discovery" else _company_key(route))


def _approach_key(value: str) -> str:
    """A new version label does not make a new research strategy."""
    value = re.sub(r"(^|[-_\s])v\d+(?=$|[-_\s])", r"\1", value.casefold())
    return re.sub(r"[-_\s]+", "-", value).strip("-")


def _reviewed_company_scopes(document: dict) -> set[str]:
    """Park reviewed gaps using existing receipts and frontier, without a new state."""
    frontier = document.get("stop_audit", {}).get("route_frontier", [])
    completed = {r.get("route_id") for r in frontier if isinstance(r, dict)
                 and _nonempty_text(r.get("route_id")) and r.get("state") == "exhausted"
                 and _nonempty_text(r.get("reason")) and _nonempty_text(r.get("exhaustion_basis"))}
    latest = {}
    for route in document.get("routes", []):
        if (isinstance(route, dict) and route.get("entity_type") != "tool_catalog"
                and _nonempty_text(_route_scope(route))):
            latest[_route_scope(route)] = route
    rows_by_scope = {}
    for row in document.get("unresolved", []):
        if isinstance(row, dict) and row.get("stage") in {"account", "contact"} and (scope := _company_key(row)):
            rows_by_scope.setdefault(scope, []).append(row)
    reviewed = set()
    for scope, rows in rows_by_scope.items():
        route = latest.get(scope, {})
        phases = ({"account_discovery", "account_verification"} if any(r["stage"] == "account" for r in rows)
                  else {"contact_discovery", "contact_verification", "email_validation"})
        if (all(_nonempty_text(r.get("reason_text")) for r in rows) and route.get("phase") in phases
                and _nonempty_text(route.get("route_id")) and route["route_id"] in completed
                and route.get("provider_status") in {"ok", "no_results"}):
            reviewed.add(scope)
    return reviewed


def _missing_exhaustion_review(document: dict, scopes: set[str]) -> list[str]:
    """Review discovery saturation and the remaining company gaps.

    This reviews the recorded search, not the completeness of an entire market.
    Reuse receipts and progress snapshots instead of adding another state log.
    """
    audit = document.get("stop_audit", {})
    frontier = audit.get("route_frontier", []) if isinstance(audit, dict) else []
    if (not isinstance(frontier, list) or not frontier or audit.get("frontier_complete") is not True
            or any(not isinstance(r, dict) or r.get("state") not in FINAL_FRONTIER_STATES for r in frontier)):
        return sorted(scopes)
    completed = {r.get("route_id") for r in frontier
                 if _nonempty_text(r.get("route_id")) and r.get("state") == "exhausted" and _nonempty_text(r.get("reason"))
                 and _nonempty_text(r.get("exhaustion_basis"))}
    current = progress_snapshot(document)
    reviewed = _reviewed_company_scopes(document)
    missing = []
    for scope in sorted(scopes):
        attempts = [r for r in document.get("routes", []) if isinstance(r, dict)
                    and _route_scope(r) == scope and r.get("entity_type") != "tool_catalog"]
        if scope != "discovery":
            if scope not in reviewed:
                missing.append(scope)
            continue
        pair = attempts[-2:]
        if (len(pair) != 2 or any(not _nonempty_text(r.get("route_id")) or r["route_id"] not in completed
                or r.get("provider_status") not in {"ok", "no_results"}
                or not _nonempty_text(r.get("approach"))
                or not isinstance(r.get("request_fingerprint"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", r["request_fingerprint"])
                or not isinstance(r.get("progress_before"), list)
                or not all(isinstance(f, str) for f in r["progress_before"]) for r in pair)
                or _approach_key(pair[0]["approach"]) == _approach_key(pair[1]["approach"])
                or pair[0]["request_fingerprint"] == pair[1]["request_fingerprint"]):
            missing.append(scope)
            continue

        def facts(snapshot):
            # More unrelated or rejected shops are not discovery success.
            return {f for f in snapshot if f.endswith((":account", ":lead"))}

        first, second = (facts(r["progress_before"]) for r in pair)
        if second - first or facts(current) - second:
            missing.append(scope)
    return missing


def _blocker_error(action: dict, document: dict) -> Optional[str]:
    blocker = action["blocker"]
    rows = document.get("routes", []) + [r for r in document.get("unresolved", []) if isinstance(r, dict) and r.get("stage") == "route"]
    evidence = next((r for r in rows if isinstance(r, dict) and r.get("route_id") == blocker.get("evidence_route_id")), None)
    if (not _nonempty_text(blocker.get("reason")) or not evidence
            or evidence.get("provider_status") not in BLOCKING_PROVIDER_STATUSES):
        return "blocker needs a reason and blocking receipt/outcome evidence_route_id"
    candidate = evidence.get("candidate", {})
    provider = evidence.get("provider") or (candidate.get("provider") if isinstance(candidate, dict) else None)
    if provider != action.get("provider") or _route_scope(evidence) != action.get("scope"):
        return "blocker receipt must match this provider and scope"
    if action.get("tool") and evidence.get("tool", evidence.get("operation")) != action["tool"]:
        return "blocker receipt must match this tool"
    frontier = document.get("stop_audit", {}).get("route_frontier", [])
    descendants = {evidence["route_id"]}
    for _ in frontier:
        for route in frontier:
            if route.get("route_id") in descendants:
                descendants.update(route.get("continuation_route_ids", []))
    # Pre-dispatch failures can exist only as route outcomes. Their position in
    # the append-only frontier still establishes whether a recovery came later.
    positions = {r.get("route_id"): i for i, r in enumerate(frontier)}
    outcome_only = not any(r.get("route_id") == evidence["route_id"] for r in document.get("routes", []))
    evidence_position = positions.get(evidence["route_id"])
    later = False
    for route in document.get("routes", []):
        if route.get("route_id") == evidence["route_id"]:
            later = True
            continue
        same_tool = (route.get("provider") == provider and _route_scope(route) == _route_scope(evidence)
                     and route.get("tool", route.get("operation")) == evidence.get("tool", evidence.get("operation")))
        later_outcome = outcome_only and (route.get("route_id") in descendants or (
            evidence_position is not None and positions.get(route.get("route_id"), -1) > evidence_position))
        if ((later or later_outcome) and (same_tool or route.get("route_id") in descendants)
                and route.get("provider_status") in DETERMINATE_PROVIDER_STATUSES):
            return "recovered error cannot justify stopping"
    return None


def _missing_catalog_review(document: dict, scopes: set[str]) -> list[str]:
    """Reuse this run's capability review; ordinary research does not expire it."""
    routes = [r for r in document.get("routes", []) if isinstance(r, dict)]
    refs = document.get("stop_check", {}).get("catalog_review_route_ids", [])
    if not isinstance(refs, list):
        return sorted(scopes)
    reviewed = {_route_scope(r) for r in routes
                if r.get("route_id") in refs
                and r.get("provider") == "deepline" and r.get("entity_type") == "tool_catalog"
                and r.get("operation") == "search"
                and r.get("provider_status") in {"ok", "no_results"} | BLOCKING_PROVIDER_STATUSES}
    # Capabilities are run-wide; do not repeat an identical catalog query for
    # every company. Recovery actions and blocker evidence remain scope-specific.
    return [] if "discovery" in reviewed else sorted(scopes - reviewed)


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


def calculate_review_counts(document: dict[str, Any]) -> dict[str, int]:
    """Derive review counts from outcomes, without inferring qualification."""
    accepted = document.get("accepted", [])
    account_rows = _account_outcomes(document)
    accepted_keys = {key for key in map(_company_key, accepted) if key is not None}
    reviewed_keys = {key for key in map(_company_key, accepted + account_rows) if key is not None}
    reasons_by_key: dict[str, set[str]] = {}
    for row in account_rows:
        key, reason = _company_key(row), row.get("reason_code")
        if key is not None and isinstance(reason, str):
            reasons_by_key.setdefault(key, set()).add(reason)
    exclusion_keys = {key for key, reasons in reasons_by_key.items()
                      if reasons == {"explicit_exclusion"} and key not in accepted_keys}
    return {
        "candidate_companies_reviewed": len(reviewed_keys),
        "exclusion_only_rejections": len(exclusion_keys),
        "substantive_account_reviews": len(reviewed_keys - exclusion_keys),
        "duplicate_candidates": sum(row.get("reason_code") == "duplicate_domain" for row in account_rows),
    }


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
    validator: str = "zerobounce",
) -> None:
    """Validate ZeroBounce and its optional single BounceBan fallback."""

    email = _nonempty_text(contact.get("email"))
    receipt = contact.get("email_validation")
    if email is None:
        if receipt is not None:
            errors.append(f"{contact_path}.email_validation requires a stored email")
        return
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        errors.append(f"{contact_path}.email is invalid")
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
    outage = (
        validator == "zerobounce" and "status" in receipt and receipt["status"] is None
        and isinstance(receipt.get("provider_status"), str)
        and receipt["provider_status"] in EMAIL_FALLBACK_FAILURES
    )
    if "provider_status" in receipt and not outage:
        errors.append(f"{contact_path}.email_validation.provider_status requires a service failure with null verdict")
    eligible_fallback = outage or (status or "").casefold() in {"catch-all", "unknown"}
    if status is None and not outage:
        errors.append(
            f"{contact_path}.email_validation.status is unresolved or missing"
        )
    elif validator == "bounceban":
        if status.casefold() != "success" or str(receipt.get("result", "")).strip().casefold() != "deliverable":
            errors.append(f"{contact_path}.email_validation requires BounceBan success and result deliverable")
        if "fallback" in receipt:
            errors.append(f"{contact_path}.email_validation cannot chain fallbacks")
    elif eligible_fallback and isinstance(receipt.get("fallback"), dict):
        fallback = receipt["fallback"]
        _validate_email_receipt(
            {"email": email, "email_validation": fallback},
            f"{contact_path}.email_validation.fallback", routes_by_id, errors, "bounceban",
        )
        first_id = _nonempty_text(receipt.get("source", {}).get("route_id")) if isinstance(receipt.get("source"), dict) else None
        next_id = _nonempty_text(fallback.get("source", {}).get("route_id")) if isinstance(fallback.get("source"), dict) else None
        ids = list(routes_by_id)
        if first_id in ids and next_id in ids and ids.index(next_id) <= ids.index(first_id):
            errors.append(f"{contact_path}.email_validation fallback must use a distinct later route")
    elif (status or "").casefold() != "valid":
        errors.append(f"{contact_path}.email_validation.status must be valid")
    if validator == "zerobounce" and "fallback" in receipt and not eligible_fallback:
        errors.append(f"{contact_path}.email_validation fallback requires catch-all, unknown, or a recorded service failure")

    source = receipt.get("source")
    if not isinstance(source, dict):
        errors.append(f"{contact_path}.email_validation.source is required")
        return
    if str(source.get("provider", "")).strip().casefold() != "deepline":
        errors.append(
            f"{contact_path}.email_validation.source.provider must be deepline"
        )
    if str(source.get("validator", "")).strip().casefold() != validator:
        errors.append(
            f"{contact_path}.email_validation.source.validator must be {validator}"
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
    if outage:
        if route.get("provider_status") != receipt["provider_status"]:
            errors.append(f"email validation route {route_id} must match the recorded service failure")
    elif route.get("provider_status") not in {"ok", "partial"}:
        errors.append(
            f"email validation route {route_id} is unresolved or unsuccessful"
        )
    paid_calls = route.get("paid_calls")
    if validator == "bounceban" and paid_calls != 1:
        errors.append(f"email validation route {route_id} must record exactly one fallback call")
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
    """Enforce the route-cost contract introduced in version 1.1."""

    if document.get("schema_version") not in {"1.1", "1.2"}:
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
        # An unknown total cannot erase charges that are already confirmed.
        if limit is not None and max(known_costs[provider], actual or Decimal("0")) > limit:
            errors.append(
                f"budget.spent.{provider}_credits exceeds limit {limit}"
            )


def _validate_next_lead_budget(document: dict[str, Any], errors: list[str]) -> None:
    """Enforce the optional Deepline allowance for finding the next lead.

    The allowance is grouped by the number of accepted leads before a call.
    This makes route changes and rejected candidates part of the same budget
    window. This is an explicitly requested hard limit, never a default.
    The default strategy-review warning is reported separately in progress.
    """

    request = document.get("request")
    request_budget = request.get("budget") if isinstance(request, dict) else None
    output_budget = document.get("budget")
    output_limits = output_budget.get("limits") if isinstance(output_budget, dict) else None

    configured: list[tuple[str, Decimal]] = []
    for path, source in (
        ("request.budget.max_deepline_credits_per_next_lead", request_budget),
        ("budget.limits.max_deepline_credits_per_next_lead", output_limits),
    ):
        if not isinstance(source, dict) or path.rsplit(".", 1)[-1] not in source:
            continue
        value = _decimal(source.get(path.rsplit(".", 1)[-1]))
        if value is None or value < 0:
            errors.append(f"{path} must be a non-negative number")
            continue
        configured.append((path, value))

    if not configured:
        return
    if len(configured) == 2 and configured[0][1] != configured[1][1]:
        errors.append(
            "request.budget.max_deepline_credits_per_next_lead must match "
            "budget.limits.max_deepline_credits_per_next_lead"
        )
    limit = configured[0][1]

    routes = document.get("routes", [])
    if not isinstance(routes, list):
        return
    grouped_costs: dict[int, Decimal] = {}
    for index, route in enumerate(routes):
        if not isinstance(route, dict):
            continue
        if route.get("provider") != "deepline":
            continue
        paid_calls = route.get("paid_calls")
        if not isinstance(paid_calls, int) or isinstance(paid_calls, bool) or paid_calls <= 0:
            continue

        accepted_before = route.get("accepted_leads_before_call")
        if not isinstance(accepted_before, int) or isinstance(accepted_before, bool) or accepted_before < 0:
            errors.append(
                f"routes[{index}].accepted_leads_before_call is required when the "
                "next-lead Deepline allowance is active"
            )
            continue
        basis = route.get("cost_basis")
        if basis == "actual":
            charge = _decimal(route.get("cost_credits"))
        elif basis == "estimated":
            charge = _decimal(route.get("cost_upper_bound_credits"))
        else:
            charge = None
        if charge is None or charge < 0:
            errors.append(
                f"routes[{index}] has unknown Deepline cost and cannot prove the "
                "next-lead allowance"
            )
            continue
        grouped_costs[accepted_before] = grouped_costs.get(accepted_before, Decimal("0")) + charge

        # Check each dispatch against all spend recorded so far at its count
        # or higher. Later demotions do not invalidate previously legal calls.
        total = sum(cost for group, cost in grouped_costs.items() if group >= accepted_before)
        if total > limit:
            errors.append(
                "Deepline next-lead allowance exceeded for "
                f"routes[{index}] at accepted_leads_before_call={accepted_before}: {total} > {limit} credits"
            )


def calculate_progress(document: dict[str, Any]) -> dict[str, Any]:
    """Derive a read-only work summary; warnings do not change completion rules."""
    accepted = document.get("accepted", [])
    accepted = accepted if isinstance(accepted, list) else []
    accepted_keys = {_company_key(row) for row in accepted} - {None}
    buckets: dict[str, dict[str, dict[str, Any]]] = {"account": {}, "contact": {}}
    failures: set[str] = set()
    rows = document.get("unresolved", [])
    for index, row in enumerate(rows if isinstance(rows, list) else []):
        if not isinstance(row, dict):
            continue
        stage = row.get("stage")
        if stage == "route":
            failures.add(_nonempty_text(row.get("route_id")) or f"outcome:{index}")
            continue
        key = _company_key(row)
        if stage not in buckets or key is None or key in accepted_keys:
            continue
        entry = buckets[stage].setdefault(key, {
            "candidate": row.get("candidate", {}), "reasons": [],
            "qualification_checks": [],
        })
        entry["reasons"].append({"code": row.get("reason_code"), "detail": row.get("reason_text")})
        entry["qualification_checks"].extend(row.get("qualification_checks", []) or [])

    # Conflicting account evidence takes precedence over a contact-stage label.
    for key in buckets["account"]:
        buckets["contact"].pop(key, None)
    confirmed = Decimal("0")
    maximum = Decimal("0")
    unknown = False
    routes = document.get("routes", [])
    for index, route in enumerate(routes if isinstance(routes, list) else []):
        if not isinstance(route, dict):
            continue
        if route.get("provider_status") in BLOCKING_PROVIDER_STATUSES:
            failures.add(_nonempty_text(route.get("route_id")) or f"receipt:{index}")
        if route.get("provider") != "deepline" or not isinstance(route.get("paid_calls"), int) or route["paid_calls"] < 1:
            continue
        group = route.get("accepted_leads_before_call")
        if type(group) is not int or group < 0:
            unknown = True
            continue
        if group < len(accepted):
            continue
        basis = route.get("cost_basis")
        amount = _decimal(route.get("cost_credits") if basis == "actual" else route.get("cost_upper_bound_credits"))
        if basis not in {"actual", "estimated"} or amount is None or amount < 0:
            unknown = True
            continue
        maximum += amount
        if basis == "actual":
            confirmed += amount
    warnings = []
    review_due = maximum >= NEXT_LEAD_REVIEW_CREDITS
    if review_due:
        warnings.append("Review the sourcing strategy: at least 5 Deepline credits are charged or reserved since the last completed lead. This warning is not a spending cap or a stop reason.")
    if unknown:
        warnings.append("Next-lead cost is incomplete because some paid routes lack a usable cost or accepted-lead count. Unknown charges are not free.")
    email_gaps = {"missing_email", "email_invalid", "email_validation_unresolved"}
    buyers = {
        key for key, row in buckets["contact"].items()
        if _nonempty_text(row["candidate"].get("full_name"))
        and _nonempty_text(row["candidate"].get("current_title"))
        and row["reasons"] and all(reason["code"] in email_gaps for reason in row["reasons"])
    }
    return {
        "accepted_companies": len(accepted_keys),
        "stages": {
            "company_fit": len(accepted_keys | set(buckets["contact"])),
            "buyer_verified": len(accepted_keys | buyers),
            "completed_leads": len(accepted_keys),
        },
        "account_evidence_missing": len(buckets["account"]),
        "contact_completion_missing": len(buckets["contact"]),
        "provider_or_route_failures": len(failures),
        "unresolved_accounts": list(buckets["account"].values()),
        "unresolved_contacts": list(buckets["contact"].values()),
        "deepline_since_last_lead": {
            "confirmed_credits": _json_decimal(confirmed),
            "maximum_credits": None if unknown else _json_decimal(maximum),
            "strategy_review_due": True if review_due else (None if unknown else False),
        },
        "warnings": warnings,
    }


def validate_continuations(frontier: dict[str, dict[str, Any]], errors: list[str]) -> None:
    graph: dict[str, list[str]] = {}
    for route_id, item in frontier.items():
        refs = item.get("continuation_route_ids", [])
        if not isinstance(refs, list) or any(not isinstance(ref, str) or not ref.strip() for ref in refs):
            errors.append(f"route {route_id} continuation_route_ids must be an array of route IDs")
            continue
        if len(refs) != len(set(refs)):
            errors.append(f"route {route_id} has duplicate continuation_route_ids")
        graph[route_id] = refs
        if item.get("state") == "exhausted" and item.get("exhaustion_basis") in {"continuation_exhausted", "query_family_exhausted"} and not refs:
            errors.append(f"exhausted route {route_id} must reference its continuation attempts")
        for ref in refs:
            if ref not in frontier:
                errors.append(f"route {route_id} references missing continuation {ref}")
            elif item.get("state") == "exhausted" and frontier[ref].get("state") not in FINAL_FRONTIER_STATES:
                errors.append(f"exhausted route {route_id} has actionable continuation {ref}")
    # Iterative traversal also catches mutually exhausted routes supporting each other.
    finished: set[str] = set()
    for start in graph:
        active: set[str] = set()
        stack = [(start, False)]
        while stack:
            route_id, leaving = stack.pop()
            if leaving:
                active.discard(route_id)
                finished.add(route_id)
            elif route_id in active:
                errors.append(f"continuation cycle includes route {route_id}")
                break
            elif route_id not in finished:
                active.add(route_id)
                stack.append((route_id, True))
                stack.extend((ref, False) for ref in graph.get(route_id, []) if ref in graph)


def _validate_client_output(accepted: list, errors: list[str]) -> None:
    taxonomy_path = pathlib.Path(__file__).resolve().parents[1] / "assets" / "leadpoet_industry_taxonomy.json"
    taxonomy = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    for index, row in enumerate(accepted):
        if not isinstance(row, dict):
            continue
        path = f"accepted[{index}]"
        narrative = row.get("intent_details")
        if not isinstance(narrative, str) or not narrative.strip():
            errors.append(f"{path}.intent_details must be a non-empty string")
        company = row.get("company")
        if not isinstance(company, dict):
            continue
        note = company.get("classification_note")
        if "classification_note" in company and (not isinstance(note, str) or not note.strip()):
            errors.append(f"{path}.company.classification_note must be a non-empty string")
        industry, subindustry = company.get("industry"), company.get("sub_industry")
        if "industry" not in company and "sub_industry" not in company:
            if not isinstance(note, str) or not note.strip():
                errors.append(f"{path}.company.classification_note is required for unresolved classification")
        elif (
            not isinstance(industry, str)
            or not isinstance(subindustry, str)
            or industry not in taxonomy["parent_industries"]
            or industry not in taxonomy["subindustry_parents"].get(subindustry, [])
        ):
            errors.append(f"{path}.company requires an exact canonical industry/sub_industry pair")

def _linkedin_url(value: Any, kind: str) -> bool:
    """Require a direct company/profile URL, not a post, search or lookalike host."""
    return isinstance(value, str) and re.fullmatch(
        rf"https?://(?:[a-z0-9-]+\.)*linkedin\.com/{kind}/[a-z0-9_%~.-]+/?(?:[?#][^\s]*)?",
        value.strip(), re.IGNORECASE,
    ) is not None


def employee_range_bounds(value: Any) -> Optional[tuple[int, Optional[int]]]:
    """Read a published range without converting a member count into a band."""
    if not isinstance(value, str):
        return None
    value = re.sub(r"[\s,]", "", value).replace("–", "-").replace("—", "-")
    match = re.fullmatch(r"(\d+)(?:-(\d+)|(\+))", value)
    if not match:
        return None
    lower, upper = int(match[1]), int(match[2]) if match[2] else None
    return (lower, upper) if upper is None or upper >= lower else None


def _validate_harvest_evidence(evidence: Any, linkedin: Any, kind: str, path: str,
                              routes: dict, errors: list[str]) -> None:
    evidence = evidence if isinstance(evidence, dict) else {}
    url = evidence.get("evidence_url")
    if not _linkedin_url(url, kind):
        errors.append(f"{path}.evidence_url requires the LinkedIn /{kind}/ source")
    elif _linkedin_url(linkedin, kind):
        slug = lambda value: re.search(rf"/{kind}/([^/?#]+)", value, re.IGNORECASE)[1].casefold()
        if slug(url) != slug(linkedin):
            errors.append(f"{path}.evidence_url must match the same LinkedIn entity")
    try:
        date = evidence.get("evidence_date")
        if not isinstance(date, str) or datetime.strptime(date, "%Y-%m-%d").strftime("%Y-%m-%d") != date:
            raise ValueError
    except ValueError:
        errors.append(f"{path}.evidence_date requires an ISO calendar date")
    if evidence.get("evidence_date_basis") != "observed_current" or not _nonempty_text(evidence.get("evidence_text")):
        errors.append(f"{path} requires observed_current evidence and the LinkedIn source text")
    source = evidence.get("source")
    source = source if isinstance(source, dict) else {}
    tool = source.get("tool")
    route_id = source.get("route_id")
    matching = routes.get(route_id.strip(), []) if isinstance(route_id, str) else []
    if (source.get("provider") != "deepline" or source.get("operation") != "execute"
            or not isinstance(tool, str) or "harvestapi" not in tool.casefold()
            or not _identity(tool).endswith("getcompany" if kind == "company" else "getprofile")
            or len(matching) != 1
            or matching[0].get("provider") != "deepline"
            or matching[0].get("operation") != "execute"
            or matching[0].get("tool") != tool
            or matching[0].get("provider_status") not in {"ok", "partial"}):
        errors.append(f"{path}.source requires a matching successful HarvestAPI execute route")


def linkedin_field_errors(document: dict) -> list[str]:
    """Required LinkedIn field enrichment, independent of email/phone opt-outs."""
    errors = []
    routes: dict[str, list[dict]] = {}
    for route in document.get("routes", []):
        if isinstance(route, dict) and isinstance(route.get("route_id"), str):
            routes.setdefault(route["route_id"].strip(), []).append(route)
    for index, row in enumerate(document.get("accepted", [])):
        if not isinstance(row, dict):
            continue
        path = f"accepted[{index}]"
        company = row.get("company")
        company = company if isinstance(company, dict) else {}
        if employee_range_bounds(company.get("employee_range")) is None:
            errors.append(f"{path}.company.employee_range requires the LinkedIn employee range")
        _validate_harvest_evidence(company.get("employee_range_evidence"), company.get("linkedin_url"),
                                  "company", f"{path}.company.employee_range_evidence", routes, errors)
        contacts = [(f"{path}.primary_contact", row.get("primary_contact"))]
        backups = row.get("backup_contacts", [])
        contacts += [(f"{path}.backup_contacts[{i}]", c) for i, c in enumerate(backups if isinstance(backups, list) else [])]
        for contact_path, contact in contacts:
            if not isinstance(contact, dict):
                continue
            country = _nonempty_text(contact.get("country"))
            if not country or country.casefold() in {"unknown", "n/a", "na", "none", "null", "remote", "-"}:
                errors.append(f"{contact_path}.country is required from the person's LinkedIn location")
            for field in ("city", "state"):
                if field in contact and contact[field] is not None and not _nonempty_text(contact[field]):
                    errors.append(f"{contact_path}.{field} must be text when supplied")
            _validate_harvest_evidence(contact.get("location_evidence"), contact.get("linkedin_url", contact.get("contact_url")),
                                      "in", f"{contact_path}.location_evidence", routes, errors)
    return errors


def accepted_errors(document: dict) -> list[str]:
    """Shared accepted-lead contract for saving a review and final delivery."""
    errors = linkedin_field_errors(document)
    request, accepted = document.get("request", {}), document.get("accepted", [])
    if document.get("schema_version") == "1.2":
        _validate_client_output(accepted, errors)
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

    return errors


def evaluate_stop(document: Any, *, now: Optional[datetime] = None, execution_budget=None) -> dict[str, Any]:
    """Check next actions independently of self-declared exhausted route labels."""
    errors: list[str] = []
    result: dict[str, Any] = {"decision": "repair_state", "eligible_actions": [], "errors": errors}
    if not isinstance(document, dict):
        errors.append("results must be an object")
        return result
    check = document.get("stop_check")
    request = document.get("request", {})
    if not isinstance(check, dict) or not isinstance(request, dict):
        errors.append("stop_check and request objects are required")
        return result
    target = request.get("target_count")
    accepted = document.get("accepted")
    if type(target) is not int or target < 1 or not isinstance(accepted, list):
        errors.append("positive target_count and accepted array are required")
        return result
    try:
        started = datetime.fromisoformat(check["started_at"].replace("Z", "+00:00"))
        current = now or datetime.now(timezone.utc)
        if started.utcoffset() is None or current.utcoffset() is None or started > current:
            raise ValueError("timestamp must be timezone-aware and not in the future")
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        errors.append(f"stop_check.started_at: {exc}")
        return result
    duration = request.get("max_duration_seconds")
    if duration is not None and (type(duration) is not int or duration <= 0):
        errors.append("max_duration_seconds must be a positive integer or null")
        return result
    result.update(checked_at=current.isoformat(), elapsed_seconds=(current - started).total_seconds())
    _validate_budget_accounting(document, errors)
    _validate_cost_accounting(document, errors)
    _validate_next_lead_budget(document, errors)
    if errors:
        return result
    if len(accepted) >= target:
        result["decision"] = "target_met"
        return result
    if duration is not None and result["elapsed_seconds"] >= duration:
        result["decision"] = "time_limit_reached"
        return result

    actions = check.get("next_actions")
    if not isinstance(actions, list):
        errors.append("stop_check.next_actions must be an array")
        return result
    for field in ("routes", "unresolved", "rejected"):
        if not isinstance(document.get(field, []), list):
            errors.append(f"{field} must be an array")
    request_budget = request.get("budget", {})
    if not isinstance(request_budget, dict):
        errors.append("request.budget must be an object")
    if errors:
        return result
    scopes = {"discovery"}
    for row in document.get("unresolved", []):
        if isinstance(row, dict) and row.get("stage") in {"account", "contact"}:
            key = _company_key(row)
            if key:
                scopes.add(key)
    reviewed = _reviewed_company_scopes(document)
    covered: set[str] = set(reviewed)
    result["parked_scopes"] = sorted(reviewed)
    ids: set[str] = set()
    budget = document.get("budget", {})
    limits = budget.get("limits", {}) if isinstance(budget, dict) else {}
    if not isinstance(limits, dict):
        errors.append("budget.limits must be an object")
        return result
    costs = calculate_cost_summary(document)
    next_lead_limit = limits.get("max_deepline_credits_per_next_lead", request_budget.get("max_deepline_credits_per_next_lead"))
    blocked_kinds: list[str] = []
    needs_pricing = False
    stalled = stalled_approaches(document)
    stalled_keys = {_approach_key(a) for a in stalled}
    strategy_changes = []
    for action in actions:
        if not isinstance(action, dict):
            errors.append("each next action must be an object")
            continue
        aid, scope = action.get("id"), action.get("scope")
        if not isinstance(aid, str) or not aid.strip() or aid in ids:
            errors.append("next action IDs must be nonempty and unique")
            continue
        ids.add(aid)
        if not isinstance(scope, str) or not scope.strip() or not _nonempty_text(action.get("description")):
            errors.append(f"{aid}: scope and concrete action description are required")
            continue
        covered.add(scope)
        blocker = action.get("blocker")
        if blocker is not None:
            if not isinstance(blocker, dict) or not isinstance(blocker.get("kind"), str) or blocker["kind"] not in {"approval_required", "access_unavailable", "required_input"}:
                errors.append(f"{aid}: invalid concrete blocker")
                continue
            problem = _blocker_error(action, document)
            if problem:
                errors.append(f"{aid}: {problem}")
            blocked_kinds.append(blocker["kind"])
            continue
        provider = action.get("provider")
        bound = action.get("cost_upper_bound_credits")
        calls = action.get("paid_calls")
        if "entity_type" in action and not _nonempty_text(action["entity_type"]):
            errors.append(f"{aid}: entity_type must be a nonempty string")
            continue
        if not isinstance(provider, str) or provider not in PAID_PROVIDERS | {"public_web"} or type(calls) is not int or calls < 0:
            errors.append(f"{aid}: valid provider and nonnegative paid_calls are required")
            continue
        if bound is not None and (isinstance(bound, bool) or not isinstance(bound, (int, float)) or not math.isfinite(bound) or bound < 0):
            errors.append(f"{aid}: cost bound must be finite and nonnegative or null")
            continue
        if provider == "public_web" and (bound != 0 or calls != 0):
            errors.append(f"{aid}: public_web actions must have zero provider cost and paid calls")
            continue
        approach = action.get("approach")
        repeated_recovery = scope in reviewed and any(
            _route_scope(r) == scope and r.get("entity_type") != "tool_catalog"
            and r.get("provider_status") in {"ok", "no_results"}
            and ((isinstance(approach, str) and isinstance(r.get("approach"), str)
                  and _approach_key(approach) == _approach_key(r["approach"]))
                 or (action.get("request_fingerprint")
                     and action["request_fingerprint"] == r.get("request_fingerprint")))
            for r in document.get("routes", []) if isinstance(r, dict))
        if action.get("entity_type") != "tool_catalog" and (
                ((stalled or scope in reviewed) and not _nonempty_text(approach))
                or repeated_recovery
                or (isinstance(approach, str) and _approach_key(approach) in stalled_keys)):
            strategy_changes.append(aid)
            continue
        if bound == 0 and calls == 0:
            result["eligible_actions"].append(aid)
            continue
        if bound is None:
            needs_pricing = True
            continue
        maximum = costs.get(provider, {}).get("maximum_credits")
        cap = limits.get(f"{provider}_credits")
        if cap is None or _decimal(cap) is None or _decimal(cap) < 0:
            errors.append(f"{aid}: a finite nonnegative provider credit cap is required")
            continue
        if maximum is None:
            needs_pricing = True
            continue
        if cap is not None and Decimal(str(maximum)) + Decimal(str(bound)) > Decimal(str(cap)):
            continue
        if provider == "deepline" and next_lead_limit is not None:
            since_last = calculate_progress(document)["deepline_since_last_lead"]["maximum_credits"]
            if since_last is None:
                needs_pricing = True
                continue
            if Decimal(str(since_last)) + Decimal(str(bound)) > Decimal(str(next_lead_limit)):
                continue
        if execution_budget is not None:
            from budget_guard import BudgetError, check_allowance
            try:
                check_allowance(execution_budget, provider, bound, len(accepted),
                                verification=provider == "deepline" and action.get("entity_type") == "email_validation")
            except BudgetError:
                continue
            except (KeyError, TypeError, ArithmeticError) as exc:
                errors.append(f"execution budget: {exc}")
                continue
        result["eligible_actions"].append(aid)
    if errors:
        result["eligible_actions"] = []
        return result
    missing = sorted(scopes - covered)
    missing_routes = []
    for route in document.get("stop_audit", {}).get("route_frontier", []):
        if route.get("state") not in ACTIONABLE_FRONTIER_STATES:
            continue
        candidates = [a for a in actions if not a.get("blocker") and (
            a.get("id") == route.get("route_id") or a.get("id") in route.get("continuation_route_ids", [])
            or (_route_scope(route) == a.get("scope") and (
                not route.get("approach") or route["approach"] == a.get("approach"))))]
        if not candidates:
            missing_routes.append(route.get("route_id"))
    result.update(strategy_change_required=bool(strategy_changes), stalled_approaches=sorted(stalled),
                  missing_routes=missing_routes)
    if document.get("stop_reason") == "no_productive_route" and not actions:
        review_missing = _missing_exhaustion_review(document, scopes)
        catalog_missing = _missing_catalog_review(document, scopes)
        result.update(exhaustion_review_required=review_missing,
                      catalog_review_required=catalog_missing)
        if not review_missing and not catalog_missing:
            result["decision"] = "no_productive_route"
            return result
    if result["eligible_actions"] or missing or missing_routes or needs_pricing or strategy_changes:
        result.update(decision="continue", missing_scopes=missing, pricing_required=needs_pricing)
    elif missing_review := _missing_catalog_review(document, scopes):
        result.update(decision="continue", catalog_review_required=missing_review)
    elif actions and len(blocked_kinds) == len(actions):
        result["decision"] = "input_or_configuration_stop" if any(k != "access_unavailable" for k in blocked_kinds) else "provider_stop"
    else:
        result["decision"] = "budget_exhausted"
    return result


def validate_run(document: Any, *, require_stop_check: bool = False, now: Optional[datetime] = None, execution_budget=None) -> list[str]:
    errors: list[str] = []
    if not isinstance(document, dict):
        return ["results.json must contain one JSON object"]
    schema_version = document.get("schema_version")
    if "schema_version" in document and (
        not isinstance(schema_version, str)
        or schema_version not in SUPPORTED_RESULT_SCHEMA_VERSIONS
    ):
        errors.append("schema_version must be 1.0, 1.1 or 1.2")
    for collection in ("accepted", "rejected", "unresolved", "routes"):
        if not isinstance(document.get(collection, []), list):
            errors.append(f"{collection} must be an array")
    if errors:
        return errors

    request = document.get("request", {})
    summary = document.get("summary", {})
    accepted = document.get("accepted", [])
    if not isinstance(request, dict) or not isinstance(summary, dict):
        return ["request and summary must be objects"]
    if require_stop_check or "stop_check" in document:
        errors.extend(qualification_errors(document))

    target = request.get("target_count")
    if not isinstance(target, int) or isinstance(target, bool) or target < 1:
        return ["request.target_count must be a positive integer"]

    accepted_count = len(accepted)
    if summary.get("accepted_companies") != accepted_count:
        errors.append("summary.accepted_companies must equal len(accepted)")

    errors.extend(accepted_errors(document))

    _validate_budget_accounting(document, errors)
    _validate_cost_accounting(document, errors)
    _validate_next_lead_budget(document, errors)

    stop_reason = document.get("stop_reason")
    stop_check = None
    if require_stop_check or "stop_check" in document:
        stop_check = evaluate_stop(document, now=now, execution_budget=execution_budget)
        errors.extend(error for error in stop_check["errors"] if error not in errors)
        decision = stop_check["decision"]
        if decision == "continue":
            errors.append("stop check requires continuation: affordable actions, missing recovery/discovery actions, or unresolved pricing remain")
        elif decision != "repair_state" and decision != stop_reason:
            errors.append(f"stop_reason must match stop check decision: {decision}")
    elif stop_reason == "time_limit_reached":
        errors.append("time_limit_reached requires stop_check and an explicit max_duration_seconds")
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

    for field, expected in calculate_review_counts(document).items():
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
            if basis == "no_results" and any(
                receipt.get("provider_status") != "no_results" or receipt.get("rows_returned", 0) != 0
                for receipt in receipts_by_id.get(route_id, [])
            ):
                errors.append(f"exhausted route {route_id} claims no_results despite a nonempty or different outcome")
        elif (
            item.get("state") == "blocked"
            and statuses
            and route_id not in route_outcome_ids
        ):
            if not statuses & BLOCKING_PROVIDER_STATUSES:
                errors.append(
                    f"blocked route {route_id} requires a blocking provider status"
                )

    validate_continuations(frontier_by_id, errors)

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
        limit_reached = stop_check is not None and not stop_check["errors"] and stop_check["decision"] in {"time_limit_reached", "budget_exhausted"}
        if actionable and not limit_reached:
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
    if stop_check is None and isinstance(capacity, dict) and shortfall and stop_reason == "budget_exhausted":
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

    if stop_check is None and shortfall and stop_reason == "provider_stop":
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
    parser.add_argument("--check-stop", action="store_true", help="evaluate continuation before the next action; draft results are allowed")
    policy = parser.add_mutually_exclusive_group()
    policy.add_argument("--require-stop-check", action="store_true", help="require the current stopping policy (default)")
    policy.add_argument("--legacy-stop-policy", action="store_true", help="read-only validation of historical reports without the new stop check; never use for a current run")
    parser.add_argument(
        "--show-cost-summary",
        action="store_true",
        help="include the route-derived cost summary in validator output",
    )
    parser.add_argument("--show-progress", action="store_true", help="include unresolved-company groups and nonblocking strategy warnings")
    args = parser.parse_args()
    try:
        document = json.loads(args.results.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "delivery_allowed": False, "errors": [str(exc)]}))
        return 2

    from budget_guard import audit_ledger, load_ledger
    try:
        execution_budget = load_ledger(args.results, allow_unbound=args.legacy_stop_policy and not args.check_stop)
    except (ValueError, OSError) as exc:
        output = {"errors": [f"budget ledger: {exc}"], "valid": False, "delivery_allowed": False}
        if args.check_stop:
            output.update(decision="repair_state", eligible_actions=[])
        print(json.dumps(output, sort_keys=True))
        return 2
    ledger_errors = audit_ledger(args.results, document, state=execution_budget,
                                 allow_unbound=args.legacy_stop_policy and not args.check_stop) if execution_budget is not None else []
    if ledger_errors:
        execution_budget = None
    if args.check_stop:
        output = evaluate_stop(document, execution_budget=execution_budget)
        output["delivery_allowed"] = False
        output["errors"].extend(ledger_errors)
        if output["errors"]:
            output.update(decision="repair_state", eligible_actions=[])
        print(json.dumps(output, sort_keys=True))
        return 2 if output["errors"] else 0
    checked_at = datetime.now(timezone.utc)
    errors = validate_run(document, require_stop_check=not args.legacy_stop_policy, now=checked_at, execution_budget=execution_budget)
    errors.extend(ledger_errors)
    output: dict[str, Any] = {"valid": not errors, "errors": errors, "stop_policy": "legacy" if args.legacy_stop_policy else "strict"}
    stop_check = evaluate_stop(document, now=checked_at, execution_budget=execution_budget)
    stop_check["errors"].extend(ledger_errors)
    if stop_check["errors"]:
        stop_check.update(decision="repair_state", eligible_actions=[])
    output["stop_decision"] = stop_check
    # A successful planning or legacy check cannot authorize client delivery.
    output["delivery_allowed"] = not errors and not args.legacy_stop_policy and stop_check["decision"] in {
        "target_met", "budget_exhausted", "time_limit_reached", "provider_stop", "input_or_configuration_stop", "no_productive_route",
    }
    if args.show_cost_summary and isinstance(document, dict):
        output["calculated_cost_summary"] = calculate_cost_summary(document)
    if args.show_progress and isinstance(document, dict):
        output["progress"] = calculate_progress(document)
    print(json.dumps(output, sort_keys=True))
    return 0 if not errors else 2


if __name__ == "__main__":
    sys.exit(main())
