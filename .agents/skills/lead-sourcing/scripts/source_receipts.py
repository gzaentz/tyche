"""Read run-bound receipts and verify structured company-fact provenance."""

from pathlib import Path
import hashlib
import json
import re

import budget_guard
import deepline
from linkedin_receipts import _entity


FUNDING_TOOL = "aviato_get_company_funding_rounds"


def request_fingerprint(provider, request):
    ignored = {"spend", "timeout_seconds", "entity_type", "output_file", "target_company_linkedin_url"}
    if provider == "deepline":
        ignored.update({"limit", "input", "name", "op", "q"})
    payload = {k: v for k, v in request.items() if k not in ignored}
    encoded = json.dumps([provider, payload], sort_keys=True, ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def read_receipt(run_file, route_id):
    """Read a saved response without dispatching or changing accounting."""
    if not isinstance(route_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", route_id):
        raise ValueError("invalid route ID")
    run_file = Path(run_file).resolve(strict=True)
    path = run_file.parent / "receipts" / (route_id + ".json")
    body = budget_guard.read_object(path)
    if body.get("run_fingerprint") != budget_guard.run_fingerprint(run_file):
        raise ValueError("saved response belongs to another run or lacks run identity; preserve it and reconcile its origin")
    document = budget_guard.read_object(run_file)
    routes = document.get("routes", []) + document.get("stop_audit", {}).get("route_frontier", [])
    if not any(isinstance(route, dict) and route.get("route_id") == route_id for route in routes):
        raise ValueError("saved response has no planned route in this run")
    for route in routes:
        if isinstance(route, dict) and route.get("route_id") == route_id:
            if any(route.get(key) != body.get(key) for key in ("request_fingerprint", "provider")):
                raise ValueError("saved response does not match this route's request/provider; preserve it and reconcile its origin")
    return {"route_id": route_id, "receipt_file": str(path), "result": body}


def funding_record(run_file, document, company, evidence):
    """Verify one captured funding row; the LLM still judges the requested stage."""
    if run_file is None:
        raise ValueError("structured funding evidence requires the saved run and receipts")
    source = evidence.get("source", {})
    index = source.get("result_index")
    if (source.get("provider") != "deepline" or source.get("operation") != "execute"
            or source.get("tool") != FUNDING_TOOL or type(index) is not int or index < 0):
        raise ValueError("select a saved structured funding result with its exact result index")
    saved = read_receipt(run_file, source.get("route_id"))["result"]
    routes = [r for r in document.get("routes", []) if r.get("route_id") == source["route_id"]]
    if (len(routes) != 1 or any(saved.get(k) != source.get(k) or routes[0].get(k) != source.get(k)
                               for k in ("provider", "operation", "tool"))
            or saved.get("receipt_status") != "complete" or saved.get("status") not in {"ok", "partial"}
            or saved.get("pending_verification") or routes[0].get("provider_status") != saved.get("status")):
        raise ValueError("structured funding evidence requires a completed successful receipt")
    request = saved.get("attempt", {}).get("request", {})
    if request.get("operation") != "execute" or request.get("tool") != FUNDING_TOOL:
        raise ValueError("captured request does not match the funding tool")
    if request_fingerprint("deepline", request) != saved.get("request_fingerprint"):
        raise ValueError("captured funding request does not match its saved fingerprint")
    identifier = request.get("payload", {}).get("website") or request.get("payload", {}).get("linkedinUrl")
    linkedin = _entity(identifier, "company")
    domain = deepline._domain(identifier) if not linkedin else None
    if not (linkedin and linkedin == _entity(company.get("linkedin_url"), "company")
            or domain and domain == deepline._domain(company.get("domain"))):
        raise ValueError("funding request must identify this company by its website or LinkedIn URL")
    raw = saved.get("provider_response")
    if not isinstance(raw, dict) or "body" not in raw:
        raise ValueError("structured funding evidence requires the captured provider response")
    normalized, _ = deepline.normalize_response(request, raw)
    rows = normalized.get("results", [])
    if normalized.get("status") not in {"ok", "partial"} or normalized.get("pending_verification") or index >= len(rows):
        raise ValueError("selected funding result is absent from the captured response")
    row = rows[index]
    if (row.get("domain") and row["domain"] != deepline._domain(company.get("domain"))
            or row.get("company_linkedin_url") and _entity(row["company_linkedin_url"], "company") != _entity(company.get("linkedin_url"), "company")):
        raise ValueError("funding response identifies a different company")
    if not row.get("stage") or not row.get("evidence_date") or not row.get("evidence_text"):
        raise ValueError("funding record must contain a stage, announcement date and supporting text")
    for field, expected in (("date", row["evidence_date"]), ("date_basis", "published"),
                            ("text", row["evidence_text"])):
        if evidence.get(field) != expected:
            raise ValueError(f"structured funding {field} must match the captured record; put interpretation in claim")
    return row
