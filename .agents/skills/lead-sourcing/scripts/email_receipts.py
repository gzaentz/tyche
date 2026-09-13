"""Read email decisions from this run's captured validator responses."""

from pathlib import Path
import re

import budget_guard
import deepline

FAILURES = {"provider_error", "timeout", "rate_limited", "auth_failed", "quota_exceeded"}


def _text(value):
    return value.strip().casefold() if isinstance(value, str) else ""


class OtherEmail(ValueError):
    """A valid saved receipt applies to a different address."""


def receipt_path(run_file, rid):
    if not isinstance(rid, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", rid):
        raise ValueError("email validation requires a saved route ID")
    return Path(run_file).resolve(strict=True).parent / "receipts" / (rid + ".json")


def saved_result(run_file, routes, source, email):
    """Return the exact address verdict, never a domain-level catch-all flag."""
    rid = source.get("route_id")
    if not isinstance(rid, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", rid):
        raise ValueError("email validation requires a saved route ID")
    matching = [r for r in routes if isinstance(r, dict) and r.get("route_id") == rid]
    if len(matching) != 1:
        raise ValueError("email validation requires one matching route")
    route = matching[0]
    saved = budget_guard.read_object(receipt_path(run_file, rid))
    if (source.get("provider") != "deepline" or source.get("operation") != "execute"
            or any(route.get(k) != source.get(k) or saved.get(k) != source.get(k)
                   for k in ("provider", "operation", "tool"))
            or route.get("phase") != "email_validation" or saved.get("receipt_status") != "complete"
            or saved.get("pending_verification") or route.get("provider_status") != saved.get("status")):
        raise ValueError("requires a completed matching email-validation receipt")
    if saved.get("run_fingerprint") != budget_guard.run_fingerprint(run_file):
        raise ValueError("email receipt belongs to another run or lacks run identity")
    if not route.get("request_fingerprint") or route["request_fingerprint"] != saved.get("request_fingerprint"):
        raise ValueError("email receipt does not match the route request")
    response = saved.get("provider_response", {})
    if not isinstance(response, dict) or "body" not in response:
        raise ValueError("email receipt lacks the original provider response")
    # Re-read the captured body; edited normalized results are not evidence.
    records = [r for r in deepline._records(response["body"]) if deepline._is_email_validation_record(r)]
    matches = [r for r in records if _text(r.get("address", r.get("email"))) == _text(email)]
    if len(matches) == 1:
        record = matches[0]
        verdict = {"email": email, "status": _text(record.get("status"))}
        if "result" in record:
            verdict["result"] = _text(record["result"])
        return verdict
    # Service failures often omit the address. Bind those to the original request.
    requested = saved.get("attempt", {}).get("request", {}).get("payload", {}).get("email")
    if not matches and (records or _text(requested) and _text(requested) != _text(email)):
        raise OtherEmail("saved receipt applies to another email")
    if not records and saved.get("status") in FAILURES and _text(requested) == _text(email):
        return {"email": email, "status": None, "provider_status": saved["status"]}
    raise ValueError("original provider response must identify exactly one matching email")


def _sources(routes, validator):
    for route in reversed(routes):
        if (isinstance(route, dict) and route.get("provider") == "deepline" and route.get("operation") == "execute"
                and route.get("phase") == "email_validation"
                and validator in _text(route.get("tool"))):
            yield {"provider": "deepline", "operation": "execute", "validator": validator,
                   "tool": route["tool"], "route_id": route["route_id"]}


def fallback_allowed(verdict):
    return verdict.get("status") in {"catch-all", "unknown"} or (
        verdict.get("status") is None and verdict.get("provider_status") in FAILURES)


def check_fallback(run_file, document, request):
    """Refuse unnecessary or repeated BounceBan dispatch before reserving spend."""
    if request.get("operation") != "execute" or "bounceban" not in _text(request.get("tool")):
        return
    email = request.get("payload", {}).get("email")
    if not _text(email):
        raise ValueError("BounceBan verification requires an exact email")
    routes = document.get("routes", [])
    found = None
    for source in _sources(routes, "zerobounce"):
        try:
            found = saved_result(run_file, routes, source, email)
        except OtherEmail:
            continue
        break
    if found is None or not fallback_allowed(found):
        raise ValueError("BounceBan requires a saved same-email ZeroBounce catch-all/unknown or service failure; valid and hard-negative verdicts cannot use fallback")
    # A changed mode or route ID is not permission to repeat a billed verification.
    for source in _sources(routes + document.get("stop_audit", {}).get("route_frontier", []), "bounceban"):
        saved = budget_guard.read_object(receipt_path(run_file, source["route_id"]))
        requested = saved.get("attempt", {}).get("request", {}).get("payload", {}).get("email")
        if _text(requested) == _text(email):
            raise ValueError("BounceBan was already attempted for this email; recover its saved job")


def email_receipt_errors(document, run_file, *, fill_missing=False):
    errors, routes = [], document.get("routes", [])
    for index, row in enumerate(document.get("accepted", [])):
        if not isinstance(row, dict):
            continue
        contacts = [(f"accepted[{index}].primary_contact", row.get("primary_contact"))]
        backups = row.get("backup_contacts", [])
        contacts += [(f"accepted[{index}].backup_contacts[{i}]", c) for i, c in enumerate(backups if isinstance(backups, list) else [])]
        for path, contact in contacts:
            if not isinstance(contact, dict) or not _text(contact.get("email")):
                continue
            email = contact["email"]
            receipt = contact.get("email_validation")
            if receipt is None and fill_missing:
                for source in _sources(routes, "zerobounce"):
                    try:
                        verdict = saved_result(run_file, routes, source, email)
                    except OtherEmail:
                        continue
                    except (ValueError, OSError) as exc:
                        errors.append(f"{path}.email_validation: {exc}")
                        break
                    receipt = contact["email_validation"] = dict(verdict, source=source)
                    break
            pending = [(path + ".email_validation", receipt)]
            if isinstance(receipt, dict) and "fallback" in receipt:
                pending.append((path + ".email_validation.fallback", receipt["fallback"]))
            for field, supplied in pending:
                if not isinstance(supplied, dict):
                    continue  # Existing structural checks report missing receipts.
                try:
                    source = supplied.get("source")
                    actual = saved_result(run_file, routes, source if isinstance(source, dict) else {}, email)
                    for key in ("email", "status", "result", "provider_status"):
                        if key not in actual:
                            if key in supplied:
                                raise ValueError(f"{key} is absent from the saved provider verdict")
                            continue
                        if fill_missing and key not in supplied:
                            supplied[key] = actual[key]
                        if key not in supplied or _text(supplied[key]) != _text(actual[key]) or (supplied[key] is None) != (actual[key] is None):
                            raise ValueError(f"{key} must match the saved provider verdict ({actual[key]!r})")
                except (ValueError, OSError, TypeError, KeyError) as exc:
                    errors.append(f"{field}: {exc}")
    return errors
