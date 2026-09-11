#!/usr/bin/env python3
"""Persist one planned route or attempt receipt without running providers."""

import argparse
import copy
import json
import os
from pathlib import Path
import stat
import tempfile

from validate_run import validate_continuations


IDENTITY = ("route_id", "phase", "provider", "operation", "request_summary")
AUDIT_IDENTITY = ("scope", "approach", "request_fingerprint", "entity_type", "status_read")
STATES = {"untried", "continuable", "exhausted", "blocked"}
COST_FIELDS = {"cost_credits", "cost_upper_bound_credits", "cost_basis"}


def record(document, frontier, receipt=None):
    result = copy.deepcopy(document)
    if not isinstance(frontier, dict) or any(
        not isinstance(frontier.get(key), str) or not frontier[key].strip()
        for key in (*IDENTITY, "reason")
    ):
        raise ValueError("frontier requires non-empty identity fields and reason")
    if frontier.get("state") not in STATES:
        raise ValueError("invalid frontier state")
    audit = result.get("stop_audit")
    if not isinstance(audit, dict) or not isinstance(audit.get("route_frontier"), list):
        raise ValueError("initialize the run audit and seeded frontier first")
    routes = result.setdefault("routes", [])
    route_id = frontier["route_id"]
    matches = [row for row in audit["route_frontier"] if row["route_id"] == route_id]
    previous = [row for row in routes if row["route_id"] == route_id]
    if len(matches) > 1 or len(previous) > 1:
        raise ValueError("duplicate existing route ID")
    if matches and any(matches[0].get(key) != frontier[key] for key in IDENTITY):
        raise ValueError("continuations require a new route ID")
    if matches and any(key in matches[0] and matches[0][key] != frontier.get(key) for key in AUDIT_IDENTITY):
        raise ValueError("attempt scope, approach and request identity are immutable")
    if receipt is not None:
        if not isinstance(receipt, dict) or any(receipt.get(key) != frontier[key] for key in IDENTITY):
            raise ValueError("receipt must match the planned route")
        if any(key in frontier and receipt.get(key) != frontier[key] for key in AUDIT_IDENTITY):
            raise ValueError("receipt must match the planned attempt metadata")
        if previous and previous[0] != receipt:
            old = previous[0]
            amount = receipt.get("cost_credits")
            bound = old.get("cost_upper_bound_credits")
            settled = (
                old.get("cost_credits") is None
                and receipt.get("cost_basis") == "actual"
                and type(amount) in (int, float) and amount >= 0
                and type(bound) in (int, float) and amount <= bound
                and receipt.get("cost_upper_bound_credits") == amount
                and {k: v for k, v in old.items() if k not in COST_FIELDS}
                == {k: v for k, v in receipt.items() if k not in COST_FIELDS}
            )
            if not settled:
                raise ValueError("attempt receipts are immutable except bounded cost settlement")
            old.update({key: receipt[key] for key in COST_FIELDS})
        if not previous:
            if not matches:
                raise ValueError("plan the route before recording execution")
            routes.append(copy.deepcopy(receipt))
    if frontier["state"] == "exhausted":
        known = receipt if receipt is not None else (previous[0] if previous else {})
        if known.get("provider_status") not in {"ok", "partial", "no_results"}:
            raise ValueError("exhaustion requires a determinate attempt receipt")
        if not frontier.get("exhaustion_basis"):
            raise ValueError("exhaustion requires an explicit basis")
        if frontier["exhaustion_basis"] == "no_results" and (
            known.get("provider_status") != "no_results" or known.get("rows_returned", 0) != 0
        ):
            raise ValueError("no_results exhaustion requires an empty no-results receipt")
    if matches:
        matches[0].clear()
        matches[0].update(copy.deepcopy(frontier))
    else:
        if receipt is None and frontier["state"] != "untried":
            raise ValueError("new routes must start untried")
        audit["route_frontier"].append(copy.deepcopy(frontier))
    errors = []
    validate_continuations({row["route_id"]: row for row in audit["route_frontier"]}, errors)
    if errors:
        raise ValueError("; ".join(errors))
    return result


def mutate(path, update):
    """Apply one state update under the existing lock and atomic-write checks."""
    path = Path(path)
    lock = path.with_name(path.name + ".lock")
    fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    temporary = None
    try:
        os.close(fd)
        original = path.lstat()
        if not stat.S_ISREG(original.st_mode):
            raise OSError("results must be a regular file, not a symlink")
        document = json.loads(path.read_text(encoding="utf-8"))
        result = update(document)
        if not isinstance(result, dict):
            raise ValueError("state update must return a results object; original file preserved")
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=str(path.parent), delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(result, stream, indent=2, ensure_ascii=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(str(temporary), original.st_mode & 0o777)
        current = path.lstat()
        if any(getattr(current, field) != getattr(original, field) for field in
               ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")):
            raise OSError("results changed during the update; reread before recording")
        os.replace(str(temporary), str(path))
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
        lock.unlink()


def persist(path, update):
    return mutate(path, lambda document: record(document, update["frontier"], update.get("receipt")))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results")
    parser.add_argument("--input", required=True, help="JSON object with frontier and optional receipt")
    args = parser.parse_args()
    try:
        persist(args.results, json.loads(args.input))
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(2, str(error) + "\n")
    print(json.dumps({"recorded": True}))


if __name__ == "__main__":
    main()
