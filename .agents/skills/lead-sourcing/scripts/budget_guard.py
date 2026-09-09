#!/usr/bin/env python3
"""Reserve provider spend before dispatch, using one durable ledger per run."""

import argparse
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import stat
import tempfile


PROVIDERS = ("deepline", "scrapingdog")


class BudgetError(ValueError):
    pass


def amount(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise BudgetError(f"{name} requires a finite nonnegative amount")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise BudgetError(f"invalid {name}") from exc
    if not result.is_finite() or result < 0:
        raise BudgetError(f"{name} requires a finite nonnegative amount")
    return result


def count(value, name):
    if type(value) is not int or value < 0:
        raise BudgetError(f"{name} requires a nonnegative integer")
    return value


def ledger_path(run_file):
    if not isinstance(run_file, (str, Path)) or not str(run_file).strip():
        raise BudgetError("spend.run_file is required")
    path = Path(run_file).resolve(strict=True)
    return path.with_name(path.name + ".budget.json")


def read_object(path):
    if not stat.S_ISREG(path.lstat().st_mode):
        raise BudgetError("budget state must be a regular file, not a symlink")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BudgetError("budget state must be a JSON object")
    return value


def load_ledger(run_file):
    path = ledger_path(run_file)
    return read_object(path) if os.path.lexists(path) else None


@contextmanager
def transaction(path):
    # Use the route writer's fail-closed lock convention; never expire a lock.
    lock = path.with_name(path.name + ".lock")
    fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    temporary = None
    try:
        state = read_object(path) if os.path.lexists(path) else {}
        yield state
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(state, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            directory = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
        lock.unlink()


def initialize(run_file, *, max_usd=None, scrapingdog_usd_per_credit=None, verification_reserve_credits=None):
    path = ledger_path(run_file)
    document = read_object(Path(run_file).resolve(strict=True))
    request = document["request"]
    target = count(request["target_count"], "target_count")
    if target == 0:
        raise BudgetError("target_count must be positive")
    if any(row.get("paid_calls", 0) for row in document.get("routes", [])) or document["budget"].get("paid_calls", 0):
        raise BudgetError("initialize before the first paid call; existing paid runs need billing reconciliation")
    limits = document["budget"]["limits"]
    request_limits = request.get("budget", {})
    if any(key != "max_paid_calls" and key in request_limits and request_limits[key] != value
           for key, value in limits.items()):
        raise BudgetError("request.budget and budget.limits must agree")
    credits = {provider: str(amount(limits[f"{provider}_credits"], provider)) for provider in PROVIDERS}
    rates = {"deepline": "0.10", "scrapingdog": None}
    if scrapingdog_usd_per_credit is not None:
        rates["scrapingdog"] = str(amount(scrapingdog_usd_per_credit, "ScrapingDog USD rate"))
    for provider in PROVIDERS:
        if Decimal(credits[provider]) > 0 and (rates[provider] is None or Decimal(rates[provider]) <= 0):
            raise BudgetError(f"a positive USD-per-credit rate is required for enabled {provider}")
    email_required = "email" in request.get("contact_fields", ["email"])
    if email_required and verification_reserve_credits is None:
        raise BudgetError("email is required: price and supply verification_reserve_credits before discovery")
    reserve = amount(0 if verification_reserve_credits is None else verification_reserve_credits, "verification reserve")
    cap = amount(max_usd if max_usd is not None else str(Decimal("0.50") * target), "USD cap")
    if reserve > Decimal(credits["deepline"]) or reserve * Decimal(rates["deepline"]) > cap:
        raise BudgetError("verification reserve exceeds the run budget")
    next_lead = limits.get("max_deepline_credits_per_next_lead")
    with transaction(path) as state:
        if state or path.exists():
            raise BudgetError("budget ledger already exists; resume it instead of resetting spend")
        state.update(version=1, run_file=str(Path(run_file).resolve()), credit_limits=credits,
                     usd_limit=str(cap), usd_per_credit=rates,
                     next_lead_limit=None if next_lead is None else str(amount(next_lead, "next-lead cap")),
                     verification_reserve_credits=str(reserve), calls={}, blocked=None)
        check_limits(document, state)
    return path


def check_limits(document, state):
    expected = {f"{provider}_credits": Decimal(state["credit_limits"][provider]) for provider in PROVIDERS}
    if state["next_lead_limit"] is not None:
        expected["max_deepline_credits_per_next_lead"] = Decimal(state["next_lead_limit"])
    limits = document["budget"]["limits"]
    requested = document["request"].get("budget", {})
    if any(amount(limits.get(key), key) != value or
           (key in requested and amount(requested[key], key) != value) for key, value in expected.items()):
        raise BudgetError("budget limits changed; reconcile the existing ledger before further paid work")
    if state["next_lead_limit"] is None and any(
        source.get("max_deepline_credits_per_next_lead") is not None for source in (limits, requested)
    ):
        raise BudgetError("next-lead limit changed after ledger initialization")


def check_allowance(state, provider, bound, accepted_count, *, verification=False):
    """Use the same affordability calculation for planning and locked dispatch."""
    if state.get("blocked"):
        raise BudgetError(state["blocked"])
    if Decimal(state["credit_limits"][provider]) == 0:
        raise BudgetError(f"{provider} is disabled by its zero credit cap")
    entry = dict(provider=provider, maximum_credits=str(amount(bound, "maximum call cost")),
                 actual_credits=None, actual_usd=None, verification=verification,
                 accepted_leads_before_call=accepted_count)
    credits = {name: Decimal(0) for name in PROVIDERS}
    usd = verified = since_last_lead = Decimal(0)
    for call in [*state["calls"].values(), entry]:
        name = call["provider"]
        charge = amount(call["maximum_credits"] if call["actual_credits"] is None else call["actual_credits"], "reserved charge")
        credits[name] += charge
        usd += (charge * amount(state["usd_per_credit"][name], "USD rate")
                if call["actual_usd"] is None else amount(call["actual_usd"], "receipted USD"))
        if call["verification"]:
            verified += charge
        if name == "deepline" and call["accepted_leads_before_call"] == accepted_count:
            since_last_lead += charge
    hold = max(Decimal(0), Decimal(state["verification_reserve_credits"]) - verified)
    credits["deepline"] += hold
    usd += hold * Decimal(state["usd_per_credit"]["deepline"])
    if usd > Decimal(state["usd_limit"]):
        raise BudgetError("shared USD cap would be exceeded, including pending calls and verification reserve")
    for name in PROVIDERS:
        if credits[name] > Decimal(state["credit_limits"][name]):
            raise BudgetError(f"{name} credit cap would be exceeded, including reservations")
    if provider == "deepline" and state["next_lead_limit"] is not None and since_last_lead > Decimal(state["next_lead_limit"]):
        raise BudgetError("per-next-lead Deepline cap would be exceeded")
    return entry


def reserve(spend, provider, *, verification=False):
    if not isinstance(spend, dict):
        raise BudgetError("paid calls require spend with run_file, route_id and max_cost_credits")
    path = ledger_path(spend.get("run_file"))
    route_id = spend.get("route_id")
    if not isinstance(route_id, str) or not route_id.strip():
        raise BudgetError("spend.route_id is required")
    bound = amount(spend.get("max_cost_credits"), "maximum call cost")
    document = read_object(Path(spend["run_file"]).resolve(strict=True))
    accepted = document.get("accepted")
    if not isinstance(accepted, list):
        raise BudgetError("accepted must be an array")
    with transaction(path) as state:
        if state.get("version") != 1:
            raise BudgetError("initialize the run budget ledger before any paid call")
        if state["run_file"] != str(Path(spend["run_file"]).resolve()):
            raise BudgetError("ledger belongs to a different run; do not copy or reset budget state")
        check_limits(document, state)
        calls = state["calls"]
        if any(call["accepted_leads_before_call"] > len(accepted) for call in calls.values()):
            raise BudgetError("accepted count moved backwards; reconcile the ledger before further paid work")
        if route_id in calls:
            raise BudgetError("route_id already reserved or charged; do not repeat a possibly billed call")
        # Catch recorded calls made outside the ledger instead of forgetting them.
        if any(row.get("paid_calls", 0) and row.get("route_id") not in calls for row in document.get("routes", [])):
            raise BudgetError("paid route missing from ledger; reconcile billing before further execution")
        calls[route_id] = check_allowance(state, provider, bound, len(accepted), verification=verification)
    return path, route_id


def settle(path, route_id, billing):
    with transaction(path) as state:
        call = state["calls"][route_id]
        if call["actual_credits"] is not None:
            raise BudgetError("charge is already settled")
        charge = amount(billing["credits_charged"], "billing.credits_charged") if "credits_charged" in billing else None
        usd = amount(billing["cost_usd"], "billing.cost_usd") if "cost_usd" in billing else None
        call.update(actual_credits=None if charge is None else str(charge), actual_usd=None if usd is None else str(usd))
        bound = Decimal(call["maximum_credits"])
        if (charge is not None and charge > bound) or (usd is not None and usd > bound * Decimal(state["usd_per_credit"][call["provider"]])):
            state["blocked"] = "provider billed above its reserved bound; reconcile pricing before further paid calls"
        return state["blocked"]


def guarded_call(request, provider, execute):
    try:
        path, route_id = reserve(request.get("spend"), provider,
                                 verification=provider == "deepline" and request.get("entity_type") == "email_validation")
    except (ValueError, OSError, KeyError, TypeError, ArithmeticError, AttributeError) as exc:
        return {"status": "quota_exceeded", "error_stage": "budget", "provider": provider,
                "error": {"message": str(exc)}, "request_sent": False}, 2
    body, code = execute()
    body["spend_receipt"] = {"route_id": route_id, "ledger": str(path), "state": "reserved"}
    billing = body.get("billing") if provider == "deepline" else None
    if isinstance(billing, dict) and billing and body.get("status") not in {"partial", "timeout"}:
        try:
            error = settle(path, route_id, billing)
            body["spend_receipt"]["state"] = "settled" if "credits_charged" in billing else "reserved"
            if error:
                body["budget_error"], code = error, 2
        except (ValueError, OSError, KeyError, TypeError, ArithmeticError, AttributeError) as exc:
            body["budget_error"], code = f"settlement failed; reservation retained: {exc}", 2
    return body, code


def audit_ledger(run_file, document, *, state=None):
    """Cross-check final route accounting against dispatched calls, when present."""
    errors = []
    try:
        state = load_ledger(run_file) if state is None else state
        if state is None:
            return errors
        if state.get("version") != 1:
            raise BudgetError("invalid budget ledger version")
        if state["run_file"] != str(Path(run_file).resolve()):
            raise BudgetError("ledger belongs to a different run")
        check_limits(document, state)
        if state.get("blocked"):
            errors.append(state["blocked"])
        routes = document.get("routes", [])
        paid = {row["route_id"]: row for row in routes if row.get("paid_calls", 0)}
        if set(paid) != set(state["calls"]):
            errors.append("paid route IDs must match the execution ledger; record every reserved call")
        for route_id in set(paid) & set(state["calls"]):
            route, call = paid[route_id], state["calls"][route_id]
            if route.get("provider") != call["provider"] or route.get("paid_calls") != 1:
                errors.append(f"{route_id}: provider and paid_calls must match the ledger")
            if call["provider"] == "deepline" and route.get("accepted_leads_before_call") != call["accepted_leads_before_call"]:
                errors.append(f"{route_id}: accepted-lead count must match the ledger")
            actual = call["actual_credits"]
            basis = "estimated" if actual is None else "actual"
            bound = call["maximum_credits"] if actual is None else actual
            if route.get("cost_basis") != basis or amount(route.get("cost_upper_bound_credits"), "route bound") != Decimal(bound):
                errors.append(f"{route_id}: cost basis and bound must match the ledger")
            if (actual is None and route.get("cost_credits") is not None) or (actual is not None and amount(route.get("cost_credits"), "route charge") != Decimal(actual)):
                errors.append(f"{route_id}: actual cost must match the ledger")
    except (ValueError, OSError, KeyError, TypeError, ArithmeticError, AttributeError) as exc:
        errors.append(f"budget ledger: {exc}")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", help="existing results.json, before the first paid call")
    parser.add_argument("--max-usd", help="explicit shared cap; default is USD 0.50 per requested lead")
    parser.add_argument("--scrapingdog-usd-per-credit", help="conservative current-plan rate; required when enabled")
    parser.add_argument("--verification-reserve-credits", help="Deepline allowance protected for email verification")
    args = parser.parse_args()
    try:
        path = initialize(args.results, max_usd=args.max_usd,
                          scrapingdog_usd_per_credit=args.scrapingdog_usd_per_credit,
                          verification_reserve_credits=args.verification_reserve_credits)
    except (ValueError, OSError, KeyError, TypeError, ArithmeticError, AttributeError) as exc:
        parser.exit(2, str(exc) + "\n")
    print(json.dumps({"ledger": str(path)}))


if __name__ == "__main__":
    main()
