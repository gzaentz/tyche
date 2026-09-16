#!/usr/bin/env python3
"""Execute a sourcing action or up to three independent company checks."""

import argparse
import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import re
import sys

import budget_guard
import research_input
from email_receipts import check_fallback, validator_for_tool, verification_finished
from email_receipts import email_work
from linkedin_receipts import contact_verification_errors, email_identity_fields
from provider_output import ResponseFile, load_json
from source_receipts import read_receipt, request_fingerprint as _fingerprint
from record_route import AUDIT_IDENTITY, IDENTITY, mutate, record
from validate_run import (BLOCKING_PROVIDER_STATUSES, DETERMINATE_PROVIDER_STATUSES, _company_key,
                          calculate_cost_summary, calculate_review_counts, evaluate_stop, excluded_company,
                          progress_snapshot, qualification_errors, _reviewed_company_scopes, accepted_errors,
                          validate_run, stalled_approaches, _research_key)

ATTEMPT_STATUSES = DETERMINATE_PROVIDER_STATUSES | BLOCKING_PROVIDER_STATUSES


def review_fingerprint(document):
    reviewed = {k: document.get(k) for k in ("request", "accepted", "unresolved", "rejected")}
    reviewed["source_reviews"] = [{k: route.get(k) for k in ("route_id", "state", "reason", "continuation_route_ids")}
        for route in document.get("stop_audit", {}).get("route_frontier", [])]
    return hashlib.sha256(json.dumps(reviewed, sort_keys=True).encode()).hexdigest()


def start_run(run_file, setup):
    """Initialize or resume the existing records from one interpreted request."""
    run_file = Path(run_file).resolve()
    existing = budget_guard.read_object(run_file) if run_file.exists() else None
    ledger_file = run_file.with_name(run_file.name + ".budget.json")
    ledger = budget_guard.read_object(ledger_file) if ledger_file.exists() else None
    document, options = research_input.start_document(run_file, setup, existing=existing, ledger=ledger)
    refresh(document)
    run_file.parent.mkdir(parents=True, exist_ok=True)
    budget_guard.create_run(run_file, document, **options)
    saved = budget_guard.read_object(run_file)
    return run_status(saved, evaluate_stop(saved, execution_budget=budget_guard.load_ledger(run_file)))


def run_lookup(run_file, lookup, *, execute=None, plan_only=False):
    """Adapt research choices to the one existing dispatch/recovery path."""
    is_batch = isinstance(lookup, list)
    values = lookup if is_batch else [lookup]
    if not 1 <= len(values) <= 3:
        raise ValueError("provide one lookup or at most three independent lookups")
    specs = [research_input.prepare_lookup(value, f"lookup[{index}]") for index, value in enumerate(values)]
    # Validate every envelope before reading contracts or creating state.
    for index, spec in enumerate(specs):
        adapter, action, request = _validate_spec(spec, f"lookup[{index}]", plan_only=plan_only)
        if action["provider"] == "deepline" and request["operation"] == "execute":
            document = budget_guard.read_object(Path(run_file))
            route = next((r for r in reversed(document.get("routes", [])) if r.get("provider") == "deepline"
                          and r.get("operation") == "describe" and r.get("tool") == request["tool"]
                          and r.get("provider_status") == "ok"), None)
            if route is None:
                raise ValueError(f"Describe {request['tool']} in this run before execution")
            receipt = read_receipt(run_file, route["route_id"])["result"]
            research_input.check_tool_contract(receipt, request)
    result = (run_batch(run_file, specs, execute=execute, plan_only=plan_only) if is_batch else
              run_attempt(run_file, specs[0], execute=execute, plan_only=plan_only))
    result["review_due"] = review_reminder(budget_guard.read_object(Path(run_file)))
    return result


def refresh(document):
    """Recompute bookkeeping only; never qualify leads or declare routes exhausted."""
    accepted = document.get("accepted", [])
    count, target = len(accepted), document["request"]["target_count"]
    document.setdefault("summary", {}).update(
        target_count=target, accepted_companies=count, accepted_contacts=count,
        backup_contacts=sum(len(row.get("backup_contacts", [])) for row in accepted),
        rejected_rows=len(document.get("rejected", [])), unresolved_rows=len(document.get("unresolved", [])))
    routes = document.get("routes", [])
    spent = {}
    for provider in ("deepline", "scrapingdog"):
        calls = [r for r in routes if r.get("provider") == provider and r.get("paid_calls", 0)]
        spent[f"{provider}_credits"] = (None if any(r.get("cost_credits") is None for r in calls)
                                        else float(sum(budget_guard.amount(r["cost_credits"], "cost") for r in calls)))
    if isinstance(document.get("budget"), dict):
        document["budget"].update(spent=spent, paid_calls=sum(r.get("paid_calls", 0) for r in routes),
                                  status="unknown" if None in spent.values() else "within_budget")
    audit = document.setdefault("stop_audit", {})
    audit.update(calculate_review_counts(document), target_shortfall=max(0, target - count))
    capacity = audit.setdefault("provider_call_capacity", {})
    for provider in ("deepline", "scrapingdog"):
        capacity.setdefault(provider, "unknown")
        if spent[f"{provider}_credits"] is None:
            capacity[provider] = "unknown"
    if document.get("schema_version") in {"1.1", "1.2"}:
        document["cost_summary"] = calculate_cost_summary(document)
    return document


def _contact_gate(document, action, run_file=None):
    # Catalog reads describe available tools; they do not look up contacts.
    if action.get("provider") == "deepline" and action.get("operation") in {"search", "describe"}:
        return
    if action["phase"] not in {"contact_discovery", "contact_verification", "email_validation"}:
        return
    # Draft problems belong to their company. Keep the full-document gate at
    # delivery; an unrelated candidate must not block discovery or recovery.
    scoped = dict(document, rejected=[])
    for state in ("accepted", "unresolved"):
        scoped[state] = [r for r in document.get(state, [])
                         if isinstance(r, dict) and _company_key(r) == action["scope"]]
    problems = qualification_errors(scoped, run_file=run_file)
    if problems:
        raise ValueError("; ".join(problems))
    rows = [r for r in document.get("accepted", []) + document.get("unresolved", [])
            if isinstance(r, dict) and _company_key(r) == action["scope"]
            and (r in document.get("accepted", []) or r.get("stage") == "contact")]
    if not rows or any(excluded_company(document["request"], r) for r in rows):
        raise ValueError("contact lookup requires a non-excluded, account-qualified company")
    for row in rows:
        checks = [c for c in row.get("qualification_checks", []) if c.get("importance") == "required"]
        fit = row.get("account_fit", {})
        if ((checks and all(c.get("status") == "pass" and c.get("evidence") for c in checks))
                or (fit.get("evidence_url") and fit.get("evidence_text"))):
            return
    raise ValueError("contact lookup requires passing account evidence; unknown stays unresolved")


def pending_source_reviews(document):
    """Attempted sources still open in the existing frontier, including discovery."""
    attempted = {r["route_id"] for r in document.get("routes", [])}
    return [{"ref": r["route_id"], "target": r.get("scope"), "reason": r.get("reason")}
            for r in document.get("stop_audit", {}).get("route_frontier", [])
            if r["route_id"] in attempted and r.get("state") in {"untried", "continuable"}]


def review_reminder(document):
    """Compact advice from the same pending sources used at finalization."""
    pending = pending_source_reviews(document)
    scopes = list(dict.fromkeys(r["target"] for r in pending))
    return {"count": len(pending), "scopes": scopes[:3], "sources": pending[:3]}


def strategy_reminder(document):
    """Advisory history only; reuse saved reviews and the existing progress check."""
    if len(document.get("accepted", [])) >= document["request"]["target_count"]:
        return {"count": 0, "items": []}
    reviewed = {r["route_id"] for r in document.get("stop_audit", {}).get("route_frontier", [])
                if r.get("state") == "exhausted" and r.get("reason")}
    terminal = {_company_key(r) for state in ("accepted", "rejected") for r in document.get(state, [])}
    groups = {}
    for route in document.get("routes", []):
        key = _research_key(route)
        if (key is not None and key[0] not in terminal and route.get("entity_type") != "tool_catalog"
                and route.get("provider_status") in {"ok", "no_results"}):
            groups.setdefault(key, []).append(route)
    items = []
    progress = progress_snapshot(document)
    for (scope, phase), routes in groups.items():
        pair = routes[-2:]
        if (len(pair) != 2 or any(r["route_id"] not in reviewed for r in pair)
                or not stalled_approaches(document, pair[-1])):
            continue
        items.append({"target": scope, "phase": phase,
                      "remaining_work": "verified buyer contact details" if phase == "contact_discovery"
                          and any(f.startswith(scope + ":buyer:") for f in progress) else phase,
                      "sources": [r["route_id"] for r in pair],
                      "tools": list(dict.fromkeys(r.get("tool") or r.get("provider") for r in pair))})
    result = {"count": len(items), "items": items}
    if items:
        result["next"] = (
            "Two reviewed attempts added no saved milestone in these company/phases. "
            "Check the remaining evidence gap and reuse saved results. If the same gap persists, "
            "consult tools.md and choose another tool, source or research method. Correct a known "
            "input error when useful; keyword/page changes alone may repeat the same method. "
            "This is advice, not a block or proof of exhaustion; independent verification stays eligible.")
    return result


def _email_gate(run_file, document, action, request):
    if not email_work(action, request):
        return
    _contact_gate(document, dict(action, phase="contact_discovery"), run_file)
    rows = [r for state in ("accepted", "unresolved") for r in document.get(state, [])
            if _company_key(r) == action["scope"]]
    contacts = [(r.get("company", r.get("candidate", {})), c) for r in rows
                for c in [r.get("primary_contact", {}), *r.get("backup_contacts", [])] if isinstance(c, dict)]
    payload = request.get("payload", request)
    name = payload.get("full_name", payload.get("fullName", payload.get("name"))) or " ".join(
        str(payload.get(a, payload.get(b, ""))) for a, b in (("first_name", "firstName"), ("last_name", "lastName"))).strip()
    url = payload.get("linkedin_url", payload.get("linkedinUrl", payload.get("profile_url", payload.get("url", ""))))
    email = payload.get("email")
    reference = action.get("contact_ref")
    if reference:
        contacts = [(company, c) for company, c in contacts if c.get("profile_ref") == reference
                    and reference.split(":")[0] == (c.get("location_evidence") or c).get("source", {}).get("route_id")]
    elif name:
        contacts = [(company, c) for company, c in contacts if str(c.get("full_name") or "").casefold() == name.casefold()]
    if "linkedin.com/in/" in str(url):
        contacts = [(company, c) for company, c in contacts
                    if str(c.get("linkedin_url") or "").rstrip("/").casefold() == url.rstrip("/").casefold()]
    if not reference and not name and "linkedin.com/in/" not in str(url):
        matches = [(company, c) for company, c in contacts if isinstance(email, str)
                   and str(c.get("email") or "").casefold() == email.casefold()]
        # Without another selected identity, email work belongs to the saved
        # primary contact. A verified backup cannot unlock an unverified primary.
        contacts = matches or [(r.get("company", r.get("candidate", {})), r.get("primary_contact", {})) for r in rows]
    errors = ["Select and review the intended contact's saved HarvestAPI profile first"]
    if len(contacts) == 1:
        company, contact = contacts[0]
        errors = contact_verification_errors(document, run_file, company, contact)
    if errors:
        raise ValueError("Email work requires verified identity, current company and requested-role match before spending: " + "; ".join(errors))
    if reference:
        fields = email_identity_fields(document, run_file, company, contact)
        for key in fields.keys() & payload.keys():
            actual, expected = str(payload[key]).strip().casefold(), fields[key].strip().casefold()
            if key in {"url", "profile_url", "linkedin_url", "linkedinUrl"}:
                actual, expected = actual.rstrip("/"), expected.rstrip("/")
            if actual != expected:
                raise ValueError(f"Email input {key} conflicts with the selected profile; omit it and use contact_ref")
    return company, contact


def run_status(document, decision):
    """The saved request and actionable work, without replaying the entire audit."""
    return {"request": document["request"], "summary": document.get("summary", {}),
            "review_due": review_reminder(document),
            "next_actions": document.get("stop_check", {}).get("next_actions", []),
            "pending_routes": [{k: r.get(k) for k in ("route_id", "scope", "state", "reason")}
                               for r in document.get("stop_audit", {}).get("route_frontier", [])
                               if r.get("state") in {"untried", "continuable"}],
            "stop_decision": decision}


def delivery_preflight(run_file, document, *, check_review=True):
    """Check a proposed final state without writing, shared by review and export."""
    document = refresh(copy.deepcopy(document))
    ledger = budget_guard.load_ledger(run_file)
    problems = budget_guard.audit_ledger(run_file, document, state=ledger)
    if check_review and document.get("final_review") and document["final_review"].get("review_ref") != review_fingerprint(document):
        problems.append("Research changed after final review; request and approve the current review packet")
    decision = evaluate_stop(document, execution_budget=ledger)
    problems.extend(decision["errors"])
    stop = decision["decision"]
    if stop in {"continue", "repair_state"}:
        problems.append("Run still needs work: " + json.dumps(decision))
    document["stop_reason"] = stop
    document["stop_audit"]["frontier_complete"] = True
    problems.extend(validate_run(document, require_stop_check=True, execution_budget=ledger, run_file=run_file))
    return document, dict(valid=not problems, errors=list(dict.fromkeys(problems)), stop_policy="strict",
                          delivery_allowed=not problems, stop_decision=decision,
                          calculated_cost_summary=calculate_cost_summary(document))


def finalize_run(run_file):
    """Prepare derived completion fields only after review and full delivery checks."""
    checked = {}

    def update(document):
        document, result = delivery_preflight(run_file, document)
        if result["errors"]:
            raise ValueError("; ".join(result["errors"]))
        checked.update(result)
        # Bind the exporter to the exact bytes validated inside the state lock.
        saved = json.dumps(document, indent=2, ensure_ascii=True, allow_nan=False) + "\n"
        checked["results_sha256"] = hashlib.sha256(saved.encode("utf-8")).hexdigest()
        return document

    mutate(run_file, update)
    return checked


def save_review(run_file, review):
    """Preserve optional web observations, then atomically save judgments; never dispatch."""
    if not isinstance(review, dict) or set(review) - {"companies", "routes", "next_actions"}:
        raise ValueError("review accepts only companies, routes and next_actions; the saved request is authoritative")
    for key in ("companies", "routes", "next_actions"):
        if not isinstance(review.get(key, []), list):
            raise ValueError(f"review.{key} must be an array")
    review = copy.deepcopy(review)
    observations = []
    seen = set()
    # Validate every attached response before saving any. Receipts are saved
    # first so a rejected/interrupted judgment can be retried without research.
    for index, item in enumerate(review.get("routes", [])):
        label = f"review.routes[{index}]"
        research_input.object_fields(item, {"route_id", "reason", "state", "continuation_route_ids", "response"}, label)
        route_id = research_input.text(item.get("route_id"), label + ".route_id")
        research_input.text(item.get("reason"), label + ".reason")
        if route_id in seen:
            raise ValueError(f"{label}.route_id duplicates {route_id}; review each route once")
        seen.add(route_id)
        if "response" in item:
            response = item.pop("response")
            try:
                _public_web_observation(read_receipt(run_file, route_id)["result"], response)
            except ValueError as exc:
                raise ValueError(f"{label}.response: {exc}") from exc
            observations.append((route_id, response))
    for route_id, response in observations:
        complete_public_web(run_file, route_id, response, check_stop=False)
    result = {}

    def update(document):
        changed = set()
        for item in review.get("companies", []):
            if "row" not in item:
                item = research_input.company_update(document, item)
            state, row = item["state"], copy.deepcopy(item["row"])
            if state not in {"accepted", "unresolved", "rejected"} or not isinstance(row, dict):
                raise ValueError("company review requires an accepted, unresolved or rejected row")
            if state != "accepted" and row.get("stage") not in {"account", "contact"}:
                raise ValueError("company review requires an account or contact stage")
            scope = _company_key(row)
            if not scope or scope in changed:
                raise ValueError("review each canonical company once")
            changed.add(scope)
            for collection in ("accepted", "unresolved", "rejected"):
                document[collection] = [r for r in document.get(collection, [])
                    if not (_company_key(r) == scope and (collection == "accepted" or r.get("stage") in {"account", "contact"}))]
            document[state].append(row)
        scoped = dict(document)
        for state in ("accepted", "unresolved", "rejected"):
            scoped[state] = [r for r in document.get(state, []) if _company_key(r) in changed]
        problems = accepted_errors(scoped, run_file=run_file, fill_missing=True) + qualification_errors(scoped, run_file=run_file)
        if problems:
            raise ValueError("; ".join(problems))

        closed, reviewed_scopes = set(), set(changed)
        for item in review.get("routes", []):
            if not isinstance(item, dict) or set(item) - {"route_id", "reason", "state", "continuation_route_ids"}:
                raise ValueError("route review accepts route_id, reason, state and continuation_route_ids")
            rid = item["route_id"]
            entry = next(r for r in document["stop_audit"]["route_frontier"] if r["route_id"] == rid)
            receipt = next(r for r in document["routes"] if r["route_id"] == rid)
            reviewed_scopes.add(entry.get("scope"))
            state = item.get("state", "exhausted")
            links = list(dict.fromkeys(entry.get("continuation_route_ids", []) + item.get("continuation_route_ids", [])))
            if state == "exhausted" and receipt.get("provider_status") == "partial":
                saved = budget_guard.read_object(Path(run_file).parent / "receipts" / (rid + ".json"))
                pending = saved.get("pending_verification")
                if pending and not verification_finished(run_file, document, rid, pending, links):
                    raise ValueError("pending verification needs its saved job's status continuation")
            if state not in {"exhausted", "continuable", "blocked"}:
                raise ValueError("review a completed attempt, not an untried route")
            if state == "blocked" and receipt.get("provider_status") in DETERMINATE_PROVIDER_STATUSES:
                raise ValueError("a successful receipt cannot become a provider blocker")
            if not isinstance(item.get("reason"), str) or not item["reason"].strip():
                raise ValueError("route review requires the reason this source is complete or still open")
            entry = {**entry, **item, "state": state}
            if links:
                entry["continuation_route_ids"] = links
            if state == "exhausted":
                entry["exhaustion_basis"] = ("continuation_exhausted" if entry.get("continuation_route_ids") else
                    "no_results" if receipt.get("provider_status") == "no_results" and not receipt.get("rows_returned")
                    else "no_new_unique_candidates")
                closed.add(rid)
            else:
                entry.pop("exhaustion_basis", None)
            document = record(document, entry)

        actions = document["stop_check"]["next_actions"]
        parked = _reviewed_company_scopes(document)
        terminal = {_company_key(r) for state in ("accepted", "rejected") for r in document.get(state, [])
                    if state == "accepted" or r.get("stage") == "account"}
        supplied = review.get("next_actions", [])
        supplied_ids = {a["id"] for a in supplied}
        active_ids = {rid for r in document["stop_audit"]["route_frontier"]
                      if r.get("state") in {"untried", "continuable"}
                      for rid in [r["route_id"], *r.get("continuation_route_ids", [])]}
        # Retire only completed work and the reviewed companies' speculative
        # follow-ups. Explicit new actions below can reopen a concrete source.
        actions[:] = [a for a in actions if a["id"] not in closed | supplied_ids
                      and (a["id"] in active_ids or a.get("scope") not in reviewed_scopes & (parked | terminal))] + copy.deepcopy(supplied)
        account_pending = {_company_key(row) for state in ("unresolved", "rejected")
                           for row in document.get(state, []) if row.get("stage") == "account"} & changed
        planned_ids = {row["route_id"] for row in document["stop_audit"]["route_frontier"]}
        # A downgraded account needs evidence first. Preserve dispatched work
        # for recovery; retire only speculative contact steps that cannot run.
        actions[:] = [a for a in actions if a["id"] in planned_ids
                      or a.get("scope") not in account_pending
                      or a.get("phase") not in {"contact_discovery", "contact_verification", "email_validation"}]
        refresh(document)
        ledger = budget_guard.load_ledger(run_file)
        decision = evaluate_stop(document, execution_budget=ledger)
        # A spending pause must not discard research judgments. Keep every
        # accounting consistency check and expose the unchanged pause in status.
        problems = budget_guard.audit_ledger(run_file, document, state=ledger) + decision["errors"]
        problems = [error for error in problems if error != ledger.get("blocked")]
        if problems:
            raise ValueError("; ".join(problems))
        result.update(run_status(document, decision))
        return document

    mutate(run_file, update)
    return result


def _validate_spec(spec, label="input", *, plan_only=False):
    """Normalize input without touching run state, receipts, budgets or providers."""
    if not isinstance(spec, dict):
        raise ValueError(f"{label} must be an object containing action and request objects")
    for field in ("action", "request"):
        if not isinstance(spec.get(field), dict):
            raise ValueError(f'{label}.{field} must be an object; legacy attempts use {{"action": {{...}}, "request": {{...}}}}. Use --lookup-file for research inputs without an action envelope.')
    action, request = copy.deepcopy(spec["action"]), copy.deepcopy(spec["request"])
    if not isinstance(action.get("id"), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", action["id"]):
        raise ValueError(f"{label}.action.id must be a safe unique route ID")
    for field in ("scope", "description", "phase", "approach", "provider"):
        if not isinstance(action.get(field), str) or not action[field].strip():
            raise ValueError(f"{label}.action.{field} must be a non-empty string")
    if action["phase"] not in {"account_discovery", "account_verification", "contact_discovery", "contact_verification", "email_validation"}:
        raise ValueError(f"{label}.action.phase must be account_discovery, account_verification, contact_discovery, contact_verification, or email_validation")
    provider = action["provider"]
    if provider == "public_web" and not plan_only:
        raise ValueError("public web: use --plan-only, then record the observed result with --complete")
    if plan_only and provider != "public_web":
        raise ValueError("--plan-only is for external public-web actions, not provider calls")
    adapter, request = research_input.normalize_provider_request(provider, request, label)
    operation = request.get("operation")
    if not isinstance(operation, str) or not operation.strip():
        raise ValueError(f"{label}.request.operation must be a non-empty string")
    paid = int(provider == "scrapingdog" or (provider == "deepline" and operation == "execute"))
    if type(action.get("paid_calls")) is not int or action["paid_calls"] != paid:
        raise ValueError(f"{label}.action.paid_calls must match the wrapper operation (execute is reserved even if priced free)")
    if "cost_upper_bound_credits" not in action:
        raise ValueError(f"{label}.action.cost_upper_bound_credits is required; use null only for unknown pricing")
    bound = action["cost_upper_bound_credits"]
    if bound is not None:
        if isinstance(bound, bool) or not isinstance(bound, (int, float)):
            raise ValueError(f"{label}.action.cost_upper_bound_credits must be a finite nonnegative number or null")
        budget_guard.amount(bound, f"{label}.action.cost_upper_bound_credits")
    if provider == "public_web" and bound != 0:
        raise ValueError(f"{label}.action.cost_upper_bound_credits must be zero for public web")
    if "status_read" in action and type(action["status_read"]) is not bool:
        raise ValueError(f"{label}.action.status_read must be boolean")
    if action.get("status_read") and (provider != "deepline" or operation != "execute"
                                     or action.get("cost_upper_bound_credits") != 0):
        raise ValueError("status_read requires a described free Deepline job-status getter")
    if action.get("status_read") and validator_for_tool(request.get("tool")) and not request.get("payload", {}).get("id"):
        raise ValueError("A verification status read requires the saved pending job id and its status getter; do not resubmit the email-verification request")
    is_verification = ("email_validation" in {request.get("entity_type"), action.get("entity_type")}
                       or provider == "deepline" and operation == "execute"
                       and validator_for_tool(request.get("tool")))
    if provider == "deepline" and operation in {"search", "describe"}:
        action["entity_type"] = "tool_catalog"
    elif "tool_catalog" in {request.get("entity_type"), action.get("entity_type")}:
        raise ValueError("tool_catalog is reserved for live catalog operations")
    elif provider == "deepline" and action["phase"] == "email_validation" and not validator_for_tool(request.get("tool")):
        raise ValueError(f"{label}: email_validation requires a supported validation operation; email finders use contact_discovery and cannot spend its protected reserve")
    elif is_verification and action["phase"] != "email_validation":
        raise ValueError(f"{label}.action.phase must be email_validation for an email-validation request")
    if action.get("entity_type") and action["entity_type"] != "tool_catalog":
        request["entity_type"] = action["entity_type"]
    if action["phase"] == "email_validation" and action.get("entity_type") != "tool_catalog":
        action["entity_type"] = request["entity_type"] = "email_validation"
    action["operation"] = operation
    if request.get("tool"):
        action["tool"] = request["tool"]
    action["request_fingerprint"] = _fingerprint(provider, request)
    return adapter, action, request


def _prepare(run_file, validated):
    adapter, action, request = validated
    provider, operation, fingerprint = action["provider"], action["operation"], action["request_fingerprint"]
    prepared = {}

    def plan(document):
        refresh(document)
        _contact_gate(document, action, run_file)
        _email_gate(run_file, document, action, request)
        if provider == "deepline" and operation == "execute" and request.get("tool") == "harvestapi_get_profile":
            for row in document.get("accepted", []) + document.get("unresolved", []):
                company = row.get("company", row.get("candidate", {}))
                if _company_key(row) == action["scope"] and isinstance(company, dict) and company.get("linkedin_url"):
                    # Local normalization context only; never sent in the provider payload.
                    request["target_company_linkedin_url"] = company["linkedin_url"]
                    break
        audit = document.setdefault("stop_audit", {})
        frontier = audit.setdefault("route_frontier", [])
        # An explicit free catalog refresh may recover a schema/price change
        # before substantive research. Paid request deduplication is unchanged.
        recent = [] if provider == "deepline" and operation in {"search", "describe"} else frontier
        matches = [r for r in recent if r.get("request_fingerprint") == fingerprint]
        if matches:
            previous = next((r for r in document.get("routes", [])
                             if r.get("route_id") == matches[-1]["route_id"]), {})
            # Only reread an explicitly free, completed status call that reported
            # a job still in progress. Never resubmit a job or an uncertain call.
            if not (action.get("status_read") and matches[-1].get("status_read")
                    and previous.get("provider_status") == "partial"
                    and previous.get("cost_credits") in (None, 0)
                    and previous.get("cost_upper_bound_credits") == 0):
                raise ValueError("request already attempted or pending; recover its receipt or choose a changed request")
        if any(r["route_id"] == action["id"] for r in frontier):
            raise ValueError("route ID already planned; resume its receipt instead of redispatching")
        if provider == "deepline" and not action.get("status_read"):
            check_fallback(run_file, document, request)
        actions = document["stop_check"]["next_actions"]
        actions[:] = [a for a in actions if a["id"] != action["id"]] + [action]
        decision = evaluate_stop(document, execution_budget=budget_guard.load_ledger(run_file))
        # Saving an already observed source for an accepted company remains
        # possible during final review. This executes no provider call and
        # does not reopen discovery or extend a user-specified time limit.
        review_observation = (decision["decision"] == "target_met" and provider == "public_web"
            and action["phase"] == "account_verification" and action["paid_calls"] == 0
            and action["scope"] in {_company_key(row) for row in document["accepted"]})
        if action["id"] not in decision["eligible_actions"] and not review_observation:
            reason = decision.get("blocked_actions", {}).get(action["id"])
            if reason:
                # Keep the agent's concrete, unaffordable choice for the stop
                # audit. No route, receipt or reservation is created. Otherwise
                # the agent has to rebuild next_actions by hand to explain why
                # this work cannot proceed at the remaining budget.
                prepared["refusal"] = reason
                return document
            raise ValueError("action not eligible: " + (reason or json.dumps(decision)))
        metadata = {k: action[k] for k in AUDIT_IDENTITY if k in action}
        entry = dict(route_id=action["id"], phase=action["phase"], provider=provider,
                     operation=operation, request_summary=action["description"], state="untried",
                     reason="Planned bounded attempt.", **metadata)
        document = record(document, entry)
        entry.update(state="blocked", reason="Dispatch pending; recover the saved response before any retry.")
        document = record(document, entry)
        prepared.update(action=action, frontier=entry, progress_before=progress_snapshot(document),
                        accepted_before=len(document["accepted"]))
        return document

    mutate(run_file, plan)
    if "refusal" in prepared:
        raise ValueError("action not eligible: " + prepared["refusal"])
    if action["paid_calls"]:
        request["spend"] = {"run_file": str(run_file), "route_id": action["id"],
                            "max_cost_credits": action["cost_upper_bound_credits"]}
    return adapter, request, prepared


def finish_attempt(run_file, route_id, body, *, check_stop=True):
    """Record a saved response without dispatching anything (also the resume path)."""
    def finish(document):
        if body.get("run_fingerprint") != budget_guard.run_fingerprint(run_file):
            raise ValueError("saved response belongs to another run or lacks run identity; preserve it and reconcile its origin")
        ledger = budget_guard.load_ledger(run_file)
        entry = next(r for r in document["stop_audit"]["route_frontier"] if r["route_id"] == route_id)
        old = next((r for r in document["routes"] if r["route_id"] == route_id), None)
        if old:
            return document
        action = next(a for a in document["stop_check"]["next_actions"] if a["id"] == route_id)
        if body.get("request_fingerprint") != entry["request_fingerprint"] or body.get("provider") != entry["provider"]:
            raise ValueError("saved response does not match this request/provider")
        status = body.get("status")
        if status not in ATTEMPT_STATUSES:
            raise ValueError("save a normalized response with a determinate provider status")
        paid = action["paid_calls"]
        call = ledger.get("calls", {}).get(route_id) if ledger else None
        if paid and call is None and body.get("request_sent") is False:
            paid = 0
        if paid and call is None:
            raise ValueError("paid response has no reservation; preserve it and reconcile, never redispatch")
        actual = float(call["actual_credits"]) if call and call["actual_credits"] is not None else (0 if not paid else None)
        bound = actual if actual is not None else float(call["maximum_credits"])
        results = body.get("results", [])
        if not isinstance(results, list):
            raise ValueError("normalized results must be an array")
        receipt = {k: entry[k] for k in IDENTITY}
        receipt.update({k: entry[k] for k in AUDIT_IDENTITY if k in entry})
        receipt.update(hypothesis=action["description"], pilot_max_rows=10, paid_calls=paid,
                       rows_returned=len(results), rows_usable=0, provider_status=status,
                       cost_credits=actual, cost_upper_bound_credits=bound,
                       cost_basis="actual" if actual is not None else "estimated",
                       accepted_leads_before_call=body["accepted_before"], progress_before=body["progress_before"])
        if action.get("tool"):
            receipt["tool"] = action["tool"]
        entry = dict(entry, state="continuable" if status in DETERMINATE_PROVIDER_STATUSES else "blocked",
                     reason="Response saved; assess evidence and choose the next useful action." if status in DETERMINATE_PROVIDER_STATUSES else "Provider failure; retain receipt and change route.")
        if status == "no_results" and not results:
            entry.update(state="exhausted", exhaustion_basis="no_results",
                         reason="This exact request returned no results; broader discovery remains open.")
        if action.get("entity_type") == "tool_catalog" and status in {"ok", "no_results"}:
            entry.update(state="exhausted", exhaustion_basis="no_new_unique_candidates",
                         reason="Catalog response saved; live capabilities are available for route choice.")
        if action.get("entity_type") == "tool_catalog":
            document["stop_check"].setdefault("catalog_review_route_ids", []).append(route_id)
        document = record(document, entry, receipt)
        document["stop_check"]["next_actions"] = [a for a in document["stop_check"]["next_actions"] if a["id"] != route_id]
        return refresh(document)
    mutate(run_file, finish)
    if check_stop:
        document = budget_guard.read_object(run_file)
        return evaluate_stop(document, execution_budget=budget_guard.load_ledger(run_file))


def _start_attempt(run_file, validated):
    budget_guard.load_ledger(run_file)  # Refuse imported state before planning or dispatch.
    adapter, request, prepared = _prepare(run_file, validated)
    receipts = run_file.parent / "receipts"
    receipts.mkdir(exist_ok=True)
    output = receipts / (prepared["action"]["id"] + ".json")
    redact = adapter.redact if adapter else lambda value: value
    metadata = {k: prepared[k] for k in ("progress_before", "accepted_before")}
    metadata.update(run_fingerprint=budget_guard.run_fingerprint(run_file),
                    request_fingerprint=prepared["action"]["request_fingerprint"], provider=prepared["action"]["provider"])
    # Keep audit identity recoverable even if the main draft is damaged.
    metadata["attempt"] = copy.deepcopy({"action": prepared["action"],
                           "request": {k: v for k, v in request.items() if k != "spend"}})
    capture = ResponseFile(output, redact, metadata=metadata)
    return adapter, request, capture


def _dispatch(adapter, request, capture, *, execute=None, plan_only=False):
    metadata = capture.metadata
    if plan_only:
        if not capture.finish(dict(metadata, status="pending")):
            raise OSError("public-web plan could not be saved; recover its receipt before dispatch")
        return {"pending": True, "receipt_file": str(capture.path), **metadata}
    body, code = (execute or adapter.run)(request, capture.capture)
    # Adapters can return valid JSON with exit 0 for a provider-level failure.
    # Keep their nonzero codes, but never report a failed attempt as success.
    code = code or (0 if body.get("status") in DETERMINATE_PROVIDER_STATUSES else 2)
    body.update(metadata)
    if not capture.finish(body):
        raise OSError("response could not be saved; recover the captured response, do not repeat the provider call")
    return {"receipt_file": str(capture.path), "provider_status": body.get("status"),
            "exit_code": code, "result": body}


def run_attempt(run_file, spec, *, execute=None, plan_only=False):
    run_file = Path(run_file).resolve(strict=True)
    prepared = _start_attempt(run_file, _validate_spec(spec, plan_only=plan_only))
    result = _dispatch(*prepared, execute=execute, plan_only=plan_only)
    if not plan_only:
        result["stop_decision"] = finish_attempt(run_file, spec["action"]["id"], result["result"])
    return result


def run_batch(run_file, specs, *, execute=None, plan_only=False):
    """One writer plans/records; at most three workers dispatch to their own receipts."""
    run_file = Path(run_file).resolve(strict=True)
    if not isinstance(specs, list) or not 1 <= len(specs) <= 3:
        raise ValueError("a batch requires 1-3 independent company checks")
    ids, scopes, inputs = set(), set(), []
    for index, spec in enumerate(specs):
        validated = _validate_spec(spec, f"batch[{index}]", plan_only=plan_only)
        action = validated[1]
        rid, scope = action["id"], action["scope"]
        scope = scope.strip().lower()
        catalog = action.get("entity_type") == "tool_catalog"
        if rid in ids or not catalog and scope in scopes:
            raise ValueError("batch actions need unique route IDs and distinct canonical company scopes")
        if not catalog and action["phase"] not in {"account_verification", "contact_discovery", "contact_verification", "email_validation"}:
            raise ValueError("batch mode is for company checks; run discovery pilots separately as a single lookup object, not an array")
        ids.add(rid)
        if not catalog:
            scopes.add(scope)
        inputs.append(validated)

    results, prepared = [], []
    # Save every plan before any network work. A refused member does not discard
    # its siblings, and a failed dispatch is never automatically resubmitted.
    for validated in inputs:
        result = {"route_id": validated[1]["id"], "exit_code": 0}
        results.append(result)
        try:
            prepared.append((result, _start_attempt(run_file, validated)))
        except Exception as exc:
            result.update(exit_code=2, error=str(exc), error_stage="prepare")

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_dispatch, *args, execute=execute, plan_only=plan_only): result
                   for result, args in prepared}
        for future in as_completed(futures):
            result = futures[future]
            stage = "dispatch"
            try:
                result.update(future.result())
                if not plan_only:
                    stage = "record"
                    finish_attempt(run_file, result["route_id"], result["result"], check_stop=False)
            except Exception as exc:
                result.update(exit_code=2, error=str(exc), error_stage=stage,
                              receipt_file=str(run_file.parent / "receipts" / (result["route_id"] + ".json")))
    document = budget_guard.read_object(run_file)
    return {"attempts": results, "exit_code": 2 if any(r["exit_code"] for r in results) else 0,
            "stop_decision": evaluate_stop(document, execution_budget=budget_guard.load_ledger(run_file))}


def _harvest_display(value):
    """Project known LinkedIn rows for review, keeping a path to complete receipts."""
    omitted = {"similarOrganizations", "logo", "logos", "backgroundCover",
               "backgroundCovers", "profilePicture", "coverPicture", "photo"}
    if isinstance(value, dict):
        if value.get("entity_type") in {"contact", "person", "company", "account", "organization"}:
            fields = {"entity_type", "provider", "tool", "company", "company_linkedin_url", "domain",
                      "name", "linkedinUrl", "employee_range", "employeeCountRange", "employeeCount",
                      "description", "tagline", "industries", "specialities", "companyType", "foundedOn",
                      "locations", "location", "location_text", "country", "state", "city",
                      "contact_name", "contact_url", "contact_title", "contact_email", "headline",
                      "current_positions", "position_review", "email_candidates", "missing_fields",
                      "evidence_url", "evidence_date", "evidence_text", "signal"}
            projected = {key: _harvest_display(item) for key, item in value.items() if key in fields}
            projected["omitted_fields"] = sorted(set(value) - fields)
            return projected
        return {key: _harvest_display(item) for key, item in value.items() if key not in omitted}
    if isinstance(value, list):
        return [_harvest_display(item) for item in value]
    return value


def cli_output(result):
    """Trim repeated audit metadata only from stdout; saved receipts stay complete."""
    output = {k: v for k, v in result.items() if k != "progress_before"}
    if "attempts" in output:
        output["attempts"] = [cli_output(attempt) for attempt in output["attempts"]]
    if isinstance(output.get("result"), dict):
        body = {k: v for k, v in output["result"].items()
                if k not in {"progress_before", "accepted_before", "request_fingerprint", "attempt", "provider_response"}}
        if "results" in body and body.get("evidence") == body["results"]:
            body.pop("evidence", None)
        if output.get("receipt_file") and str(body.get("tool", "")).startswith("harvestapi_"):
            for field in ("results", "evidence"):
                if field in body:
                    body[field] = _harvest_display(body[field])
            body["display_note"] = "Compact LinkedIn facts; omitted fields and full provider data remain in receipt_file."
        output["result"] = body
    return output


def _public_web_observation(body, response):
    """Validate an observation against the original plan without changing it."""
    try:
        research_input.object_fields(response, {"status", "operation", "results", "error"}, "web response")
    except ValueError as exc:
        raise ValueError(f"{exc}; receipt metadata is helper-owned") from exc
    if response.get("status") not in ATTEMPT_STATUSES or not isinstance(response.get("results"), list):
        raise ValueError("web response needs an observed result or failure status and a results array; pending outcomes must be recovered")
    if response["status"] == "no_results" and response["results"]:
        raise ValueError("no_results cannot contain result rows")
    if body.get("provider") != "public_web":
        raise ValueError("observed responses are only for planned public-web checks; recover provider receipts with --complete")
    operation = body["attempt"]["request"]["operation"]
    observed = dict(response, operation=response.get("operation", operation))
    if observed["operation"] != operation:
        raise ValueError("web response operation does not match the planned request")
    if body.get("status") != "pending" and any(body.get(key) != value for key, value in observed.items()):
        raise ValueError("a saved response cannot be replaced; use --complete to recover it")
    return observed


def complete_public_web(run_file, route_id, response, *, check_stop=True):
    """Attach observed web results to their saved plan, never to a paid receipt."""
    run_file = Path(run_file).resolve(strict=True)
    receipt = read_receipt(run_file, route_id)
    path = Path(receipt["receipt_file"])
    with budget_guard.transaction(path) as body:
        # Recheck identity while holding the receipt lock. Save before recording
        # run state so --complete can recover without another search.
        read_receipt(run_file, route_id)
        observed = _public_web_observation(body, response)
        if body.get("status") == "pending":
            body.update(observed, receipt_status="complete")
    return finish_attempt(run_file, route_id, body, check_stop=check_stop)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--start-file", type=Path, help="initialize/resume from {request, max_usd, verification_reserve_credits}; - reads stdin")
    mode.add_argument("--lookup-file", type=Path, help="research target/purpose/provider request, or up to three; - reads stdin")
    mode.add_argument("--input-file", type=Path, help="one action/request object or an array of 1-3 independent company checks")
    mode.add_argument("--batch-files", type=Path, nargs="+", help="1-3 company checks, as attempt files or one JSON array")
    mode.add_argument("--complete", help="record this route's saved normalized receipt, without dispatch")
    mode.add_argument("--receipt", help="read a saved route's compact evidence without dispatching or writing")
    mode.add_argument("--review-file", type=Path, help="save company decisions, observed web responses and route reviews together; - reads stdin")
    mode.add_argument("--status", action="store_true", help="show the authoritative request and compact current work")
    mode.add_argument("--finalize", action="store_true", help="prepare reviewed completion metadata and run strict delivery validation")
    parser.add_argument("--plan-only", action="store_true", help="reserve public-web work before using the browser/search tool")
    parser.add_argument("--response-file", type=Path, help="with --complete, attach observed web results without editing receipt metadata")
    args = parser.parse_args()
    if args.response_file and (not args.complete or args.plan_only):
        parser.error("--response-file requires --complete and cannot be used with --plan-only")
    if args.plan_only and not (args.lookup_file or args.input_file or args.batch_files):
        parser.error("--plan-only requires a lookup or attempt input")
    try:
        def read_input(path):
            try:
                return load_json(sys.stdin.read() if str(path) == "-" else path.read_text(encoding="utf-8"))
            except FileNotFoundError as exc:
                raise ValueError(f"Input file does not exist: {path}. Pass - and supply JSON on stdin to avoid a temporary file.") from exc
        if args.start_file:
            result = start_run(args.results, read_input(args.start_file))
        elif args.lookup_file:
            result = run_lookup(args.results, read_input(args.lookup_file), plan_only=args.plan_only)
        elif args.finalize:
            result = finalize_run(args.results)
        elif args.status:
            document = budget_guard.read_object(args.results)
            result = run_status(document, evaluate_stop(document, execution_budget=budget_guard.load_ledger(args.results)))
        elif args.review_file:
            result = save_review(args.results, read_input(args.review_file))
            result = {"request_file": str(args.results), **{k: v for k, v in result.items() if k != "request"}}
        elif args.complete:
            result = (complete_public_web(args.results, args.complete, read_input(args.response_file))
                      if args.response_file else
                      finish_attempt(args.results, args.complete, read_receipt(args.results, args.complete)["result"]))
        elif args.receipt:
            result = read_receipt(args.results, args.receipt)
        elif args.batch_files:
            specs = [load_json(path.read_text()) for path in args.batch_files]
            if len(specs) == 1 and isinstance(specs[0], list):
                specs = specs[0]
            result = run_batch(args.results, specs, plan_only=args.plan_only)
        else:
            spec = load_json(args.input_file.read_text())
            execute = run_batch if isinstance(spec, list) else run_attempt
            result = execute(args.results, spec, plan_only=args.plan_only)
        print(json.dumps(cli_output(result), ensure_ascii=True, allow_nan=False))
        if args.batch_files or args.input_file or args.lookup_file:
            return result.get("exit_code", 0)
    except (ValueError, OSError, KeyError, TypeError, StopIteration) as exc:
        parser.exit(2, str(exc) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
