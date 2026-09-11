#!/usr/bin/env python3
"""Execute a sourcing action or up to three independent company checks."""

import argparse
import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import importlib
import json
from pathlib import Path
import re

import budget_guard
from provider_output import ResponseFile, load_json
from record_route import AUDIT_IDENTITY, IDENTITY, mutate, record
from validate_run import (DETERMINATE_PROVIDER_STATUSES, _company_key,
                          calculate_cost_summary, calculate_review_counts, evaluate_stop, excluded_company,
                          progress_snapshot, qualification_errors)


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


def _contact_gate(document, action):
    if action["phase"] not in {"contact_discovery", "contact_verification", "email_validation"}:
        return
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


def _fingerprint(provider, request):
    ignored = {"spend", "timeout_seconds", "entity_type", "output_file"}
    if provider == "deepline":
        ignored.update({"limit", "input", "name", "op", "q"})
    payload = {k: v for k, v in request.items() if k not in ignored}
    encoded = json.dumps([provider, payload], sort_keys=True, ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _prepare(run_file, spec):
    action, request = copy.deepcopy(spec["action"]), copy.deepcopy(spec["request"])
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", action.get("id", "")):
        raise ValueError("action.id must be a safe unique route ID")
    for field in ("scope", "description", "phase", "approach", "provider"):
        if not isinstance(action.get(field), str) or not action[field].strip():
            raise ValueError(f"action.{field} is required")
    if action["phase"] not in {"account_discovery", "account_verification", "contact_discovery", "contact_verification", "email_validation"}:
        raise ValueError("invalid sourcing phase")
    provider = action["provider"]
    if provider not in {"deepline", "scrapingdog", "public_web"}:
        raise ValueError("use an existing provider wrapper")
    adapter = None if provider == "public_web" else importlib.import_module(provider)
    if adapter:
        request = (adapter._validate_request(request) if provider == "deepline" else adapter.validate_request(request))
    operation = request.get("operation")
    paid = int(provider == "scrapingdog" or (provider == "deepline" and operation == "execute"))
    if action.get("paid_calls") != paid:
        raise ValueError("paid_calls must match the wrapper operation (execute is reserved even if priced free)")
    if "status_read" in action and type(action["status_read"]) is not bool:
        raise ValueError("status_read must be boolean")
    if action.get("status_read") and (provider != "deepline" or operation != "execute"
                                     or action.get("cost_upper_bound_credits") != 0):
        raise ValueError("status_read requires a described free Deepline job-status getter")
    if provider == "deepline" and operation in {"search", "describe"}:
        action["entity_type"] = "tool_catalog"
    elif "tool_catalog" in {request.get("entity_type"), action.get("entity_type")}:
        raise ValueError("tool_catalog is reserved for live catalog operations")
    if action.get("entity_type") and action["entity_type"] != "tool_catalog":
        request["entity_type"] = action["entity_type"]
    if action["phase"] == "email_validation":
        action["entity_type"] = request["entity_type"] = "email_validation"
    action["operation"] = operation
    if request.get("tool"):
        action["tool"] = request["tool"]
    fingerprint = _fingerprint(provider, request)
    action["request_fingerprint"] = fingerprint
    prepared = {}

    def plan(document):
        refresh(document)
        problems = qualification_errors(document)
        if problems:
            raise ValueError("; ".join(problems))
        _contact_gate(document, action)
        audit = document.setdefault("stop_audit", {})
        frontier = audit.setdefault("route_frontier", [])
        # Catalog refreshes can repeat after new substantive work, not in a loop.
        recent = frontier
        if action.get("entity_type") == "tool_catalog":
            last = max((i for i, r in enumerate(frontier) if r.get("entity_type") != "tool_catalog"), default=-1)
            recent = frontier[last + 1:]
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
        actions = document["stop_check"]["next_actions"]
        actions[:] = [a for a in actions if a["id"] != action["id"]] + [action]
        decision = evaluate_stop(document, execution_budget=budget_guard.load_ledger(run_file))
        if action["id"] not in decision["eligible_actions"]:
            raise ValueError("action not eligible: " + json.dumps(decision))
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
    if paid:
        request["spend"] = {"run_file": str(run_file), "route_id": action["id"],
                            "max_cost_credits": action["cost_upper_bound_credits"]}
    return adapter, request, prepared


def finish_attempt(run_file, route_id, body):
    """Record a saved response without dispatching anything (also the resume path)."""
    def finish(document):
        entry = next(r for r in document["stop_audit"]["route_frontier"] if r["route_id"] == route_id)
        old = next((r for r in document["routes"] if r["route_id"] == route_id), None)
        if old:
            return document
        action = next(a for a in document["stop_check"]["next_actions"] if a["id"] == route_id)
        if body.get("request_fingerprint") != entry["request_fingerprint"] or body.get("provider") != entry["provider"]:
            raise ValueError("saved response does not match this request/provider")
        status = body.get("status")
        if status not in DETERMINATE_PROVIDER_STATUSES | {"rate_limited", "auth_failed", "quota_exceeded", "timeout", "schema_error", "provider_error", "config_error"}:
            raise ValueError("save a normalized response with a determinate provider status")
        paid = action["paid_calls"]
        ledger = budget_guard.load_ledger(run_file)
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
    document = budget_guard.read_object(run_file)
    return evaluate_stop(document, execution_budget=budget_guard.load_ledger(run_file))


def _start_attempt(run_file, spec, *, plan_only=False):
    if spec["action"]["provider"] == "public_web" and not plan_only:
        raise ValueError("public web: use --plan-only, then record the observed result with --complete")
    if plan_only and spec["action"]["provider"] != "public_web":
        raise ValueError("--plan-only is for external public-web actions, not provider calls")
    adapter, request, prepared = _prepare(run_file, spec)
    receipts = run_file.parent / "receipts"
    receipts.mkdir(exist_ok=True)
    output = receipts / (prepared["action"]["id"] + ".json")
    redact = adapter.redact if adapter else lambda value: value
    metadata = {k: prepared[k] for k in ("progress_before", "accepted_before")}
    metadata.update(request_fingerprint=prepared["action"]["request_fingerprint"], provider=prepared["action"]["provider"])
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
    body.update(metadata)
    if not capture.finish(body):
        raise OSError("response could not be saved; recover the captured response, do not repeat the provider call")
    return {"receipt_file": str(capture.path), "provider_status": body.get("status"),
            "exit_code": code, "result": body}


def run_attempt(run_file, spec, *, execute=None, plan_only=False):
    run_file = Path(run_file).resolve(strict=True)
    prepared = _start_attempt(run_file, spec, plan_only=plan_only)
    result = _dispatch(*prepared, execute=execute, plan_only=plan_only)
    if not plan_only:
        result["stop_decision"] = finish_attempt(run_file, spec["action"]["id"], result["result"])
    return result


def run_batch(run_file, specs, *, execute=None, plan_only=False):
    """One writer plans/records; at most three workers dispatch to their own receipts."""
    run_file = Path(run_file).resolve(strict=True)
    if not isinstance(specs, list) or not 1 <= len(specs) <= 3:
        raise ValueError("a batch requires 1-3 independent company checks")
    ids, scopes = set(), set()
    for spec in specs:
        action = spec["action"]
        rid, scope = action["id"], action["scope"]
        if not isinstance(rid, str) or not isinstance(scope, str):
            raise ValueError("batch route IDs and company scopes must be strings")
        scope = scope.strip().lower()
        if rid in ids or scope in scopes:
            raise ValueError("batch actions need unique route IDs and distinct canonical company scopes")
        if action["phase"] not in {"account_verification", "contact_discovery", "contact_verification", "email_validation"}:
            raise ValueError("batch mode is for company checks; run discovery pilots separately")
        if (action["provider"] == "public_web") != plan_only:
            raise ValueError("public-web batches require --plan-only; provider batches cannot use it")
        ids.add(rid)
        scopes.add(scope)

    results, prepared = [], []
    # Save every plan before any network work. A refused member does not discard
    # its siblings, and a failed dispatch is never automatically resubmitted.
    for spec in specs:
        result = {"route_id": spec["action"]["id"], "exit_code": 0}
        results.append(result)
        try:
            prepared.append((result, _start_attempt(run_file, spec, plan_only=plan_only)))
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
                    finish_attempt(run_file, result["route_id"], result["result"])
            except Exception as exc:
                result.update(exit_code=2, error=str(exc), error_stage=stage,
                              receipt_file=str(run_file.parent / "receipts" / (result["route_id"] + ".json")))
    return {"attempts": results, "exit_code": 2 if any(r["exit_code"] for r in results) else 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--input-file", type=Path, help="JSON containing action and wrapper request")
    mode.add_argument("--batch-files", type=Path, nargs="+", help="1-3 attempt files for distinct company checks")
    mode.add_argument("--complete", help="record this route's saved normalized receipt, without dispatch")
    parser.add_argument("--plan-only", action="store_true", help="reserve public-web work before using the browser/search tool")
    args = parser.parse_args()
    try:
        if args.complete:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", args.complete):
                raise ValueError("invalid route ID")
            body = load_json((args.results.parent / "receipts" / (args.complete + ".json")).read_text())
            result = finish_attempt(args.results, args.complete, body)
        elif args.batch_files:
            result = run_batch(args.results, [load_json(path.read_text()) for path in args.batch_files],
                               plan_only=args.plan_only)
        else:
            result = run_attempt(args.results, load_json(args.input_file.read_text()), plan_only=args.plan_only)
        print(json.dumps(result, ensure_ascii=True, allow_nan=False))
        if args.batch_files:
            return result["exit_code"]
    except (ValueError, OSError, KeyError, TypeError, StopIteration) as exc:
        parser.exit(2, str(exc) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
