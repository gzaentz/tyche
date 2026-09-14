"""Match a bounded read-only billing page to this run's uncertain calls."""

import hashlib
import json
import os
from pathlib import Path
from collections import Counter

import budget_guard as budget
import deepline
from record_route import mutate


FIELDS = ("id", "request_id", "provider", "operation", "status", "charge_state", "credits", "delta", "created_at")


def matching_charge(receipt, rows):
    ids = {receipt.get(key) for key in ("job_id", "request_id") if receipt.get(key)}
    matches = [row for row in rows if row.get("request_id") in ids and row.get("operation") == receipt.get("tool")]
    if len(matches) != 1:
        return None
    row = matches[0]
    if not isinstance(row.get("provider"), str) or not str(receipt.get("tool", "")).casefold().startswith(row["provider"].casefold() + "_"):
        return None
    groups = row.get("metadata", {}).get("chargeGroupIds", []) if isinstance(row.get("metadata", {}), dict) else None
    if not isinstance(groups, list) or len(groups) > 1:
        return None  # A grouped amount may include calls outside this run.
    if not row.get("id") or row.get("status") != "completed" or row.get("charge_state") != "posted":
        return None
    try:
        charge = budget.amount(row.get("credits"), "posted credits")
        if type(row.get("delta")) not in (int, float) or -row["delta"] != float(charge):
            return None
    except (ValueError, TypeError):
        return None
    proof = {key: row[key] for key in FIELDS if key in row}
    if groups:
        proof["metadata"] = {"chargeGroupIds": groups}
    return proof


def reconcile(run_file, *, fetch=None, refresh=False):
    """Read at most one recent page per changed call set; never execute a tool.

    Unmatched, ambiguous, pending and identifier-less charges remain reserved.
    The saved ledger proof permits local synchronization after interruption.
    """
    run_file = Path(run_file).resolve(strict=True)
    document = budget.read_object(run_file)
    ledger = budget.load_ledger(run_file)
    routes = {row["route_id"]: row for row in document.get("routes", [])}
    receipts = {}
    for rid, call in ledger["calls"].items():
        if call["provider"] != "deepline" or call["actual_credits"] is not None or rid not in routes:
            continue
        receipt = budget.read_object(run_file.parent / "receipts" / (rid + ".json"))
        if (receipt.get("run_fingerprint") != budget.run_fingerprint(run_file)
                or receipt.get("request_fingerprint") != routes[rid].get("request_fingerprint")):
            raise ValueError("Billing reconciliation requires this run's matching receipt")
        if receipt.get("job_id") or receipt.get("request_id"):
            receipts[rid] = receipt
    signature = hashlib.sha256(json.dumps(sorted(ledger["calls"])).encode()).hexdigest()
    status_path = run_file.parent / "billing-status.json"
    status = budget.read_object(status_path) if status_path.exists() else {}
    if receipts and (refresh or status.get("attempt_signature") != signature):
        status = {"attempt_signature": signature, "matched": [], "unmatched": sorted(receipts)}
        try:
            if fetch is None:
                code, stdout, _ = deepline._invoke([os.environ.get("DEEPLINE_BIN") or "deepline",
                    "billing", "usage", "--limit", "200", "--json"], 20)
                if code:
                    raise ValueError("Read-only billing lookup unavailable; reservations retained")
                payload = json.loads(stdout)
            else:
                payload = fetch()
            rows = payload.get("recent", {}).get("entries")
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError("Billing response has no recognized recent-call rows")
            if payload.get("org_id"):
                status["billing_org_id"] = payload["org_id"]
            matched = {rid: matching_charge(receipt, rows) for rid, receipt in receipts.items()}
            counts = Counter(proof["id"] for proof in matched.values() if proof)
            with budget.transaction(budget.ledger_path(run_file)) as saved:
                budget.check_run_identity(run_file, saved)
                used = {call["billing_evidence"]["id"] for call in saved["calls"].values() if call.get("billing_evidence")}
                for rid, receipt in receipts.items():
                    call = saved["calls"][rid]
                    proof = matched[rid]
                    if proof is None or counts[proof["id"]] != 1 or proof["id"] in used or call["actual_credits"] is not None:
                        continue
                    call.update(actual_credits=str(budget.amount(proof["credits"], "posted credits")), billing_evidence=proof)
                    if budget.price_overrun(call, saved):
                        saved["blocked"] = budget.PRICE_OVERRUN
                    status["matched"].append(rid)
                    status["unmatched"].remove(rid)
        except (ValueError, OSError, KeyError, TypeError, deepline.ConfigError, deepline.CallTimeout) as exc:
            status["error"] = str(exc)[:500]
        with budget.transaction(status_path) as saved:
            saved.clear()
            saved.update(status)
    # No original response is rewritten. Cost fields are derived from the
    # ledger; a crash between ledger settlement and this write is repairable.
    ledger = budget.load_ledger(run_file)
    def synchronize(saved):
        for route in saved.get("routes", []):
            call = ledger["calls"].get(route["route_id"], {})
            if call.get("billing_evidence"):
                actual = float(budget.amount(call["actual_credits"], "posted credits"))
                route.update(cost_credits=actual, cost_upper_bound_credits=actual, cost_basis="actual")
        from run_attempt import refresh
        refresh(saved)
        return saved
    if any(call.get("billing_evidence") for call in ledger["calls"].values()):
        mutate(run_file, synchronize)
    return status


def evidence_error(run_file, route, call):
    proof = call.get("billing_evidence")
    if not proof:
        return None
    receipt = budget.read_object(Path(run_file).resolve().parent / "receipts" / (route["route_id"] + ".json"))
    matched = matching_charge(receipt, [proof])
    if (receipt.get("run_fingerprint") != budget.run_fingerprint(run_file)
            or receipt.get("request_fingerprint") != route.get("request_fingerprint")
            or matched is None or budget.amount(matched["credits"], "posted credits") != budget.amount(call["actual_credits"], "ledger credits")):
        return "posted billing evidence does not match the saved request and charge"
    return None
