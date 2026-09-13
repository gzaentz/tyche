"""Check LinkedIn field values against this run's saved HarvestAPI responses."""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys
from typing import Any, Optional
from urllib.parse import unquote, urlsplit

import budget_guard
import deepline


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


def _entity(value, kind):
    return unquote(urlsplit(value.strip()).path.split("/")[2]).casefold() if _linkedin_url(value, kind) else None


def _text(value):
    return " ".join(value.split()).casefold() if isinstance(value, str) else None


def _saved_profile(run_file, source, url, kind, routes):
    rid = source.get("route_id")
    if not isinstance(rid, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", rid):
        raise ValueError("requires a saved HarvestAPI route ID")
    matching = [r for r in routes if isinstance(r, dict) and r.get("route_id") == rid]
    if len(matching) != 1:
        raise ValueError("requires one matching HarvestAPI route")
    route = matching[0]
    receipt = budget_guard.read_object(Path(run_file).resolve(strict=True).parent / "receipts" / (rid + ".json"))
    tool = source.get("tool")
    if (source.get("provider") != "deepline" or source.get("operation") != "execute"
            or not isinstance(tool, str) or "harvestapi" not in tool.casefold()
            or not re.sub(r"[^a-z0-9]", "", tool.casefold()).endswith("getcompany" if kind == "company" else "getprofile")
            or any(route.get(k) != source.get(k) for k in ("provider", "operation", "tool"))
            or receipt.get("provider") != "deepline" or receipt.get("operation") != "execute"
            or receipt.get("tool") != tool or receipt.get("receipt_status") != "complete"
            or receipt.get("status") not in {"ok", "partial"}
            or route.get("provider_status") != receipt.get("status")
            or receipt.get("pending_verification")):
        raise ValueError("requires a completed successful HarvestAPI response")
    if receipt.get("run_fingerprint") != budget_guard.run_fingerprint(run_file):
        raise ValueError("saved HarvestAPI response belongs to another run or lacks run identity")
    if route.get("request_fingerprint") != receipt.get("request_fingerprint") or not route.get("request_fingerprint"):
        raise ValueError("saved HarvestAPI response does not match the route request")
    response = receipt.get("provider_response")
    if not isinstance(response, dict) or "body" not in response:
        raise ValueError("requires the captured HarvestAPI response body")
    # Reuse provider normalization on the captured body. Reviewer-authored
    # evidence text and edited normalized results cannot supply field values.
    normalized = deepline._execute_output(response["body"], tool, "company" if kind == "company" else "contact")
    if normalized.get("status") not in {"ok", "partial"} or normalized.get("pending_verification"):
        raise ValueError("captured HarvestAPI response did not return usable profile data")
    profiles = [p for p in normalized.get("results", []) if isinstance(p, dict)
                and _entity(url, kind) is not None and _entity(p.get("linkedinUrl"), kind) == _entity(url, kind)]
    if len(profiles) != 1:
        raise ValueError("captured HarvestAPI response must contain exactly one matching LinkedIn entity")
    raw = {k: v for k, v in profiles[0].items() if k not in {"employee_range", "country", "state", "city"}}
    return deepline.normalize_evidence(raw, tool=tool, entity_type="company" if kind == "company" else "contact")


def linkedin_receipt_errors(document, run_file, *, fill_missing=False):
    """Read-only by default; review saves may fill missing values before validation."""
    if not isinstance(document, dict) or not isinstance(document.get("accepted", []), list):
        return ["results must be an object with an accepted array"]
    errors = []
    for index, row in enumerate(document.get("accepted", [])):
        if not isinstance(row, dict):
            continue
        base = f"accepted[{index}]"
        contacts = [(f"{base}.primary_contact", row.get("primary_contact"))]
        backups = row.get("backup_contacts", [])
        contacts += [(f"{base}.backup_contacts[{i}]", c) for i, c in enumerate(backups if isinstance(backups, list) else [])]
        entities = [(f"{base}.company", row.get("company"), "company", "employee_range_evidence", ("employee_range",))]
        entities += [(p, c, "in", "location_evidence", ("country", "state", "city")) for p, c in contacts]
        for path, entity, kind, evidence_field, fields in entities:
            if not isinstance(entity, dict):
                continue  # The structural contract reports missing entities.
            evidence = entity.get(evidence_field)
            evidence = evidence if isinstance(evidence, dict) else {}
            source = evidence.get("source")
            try:
                profile = _saved_profile(run_file, source if isinstance(source, dict) else {},
                                         evidence.get("evidence_url"), kind, document.get("routes", []))
            except (OSError, ValueError, TypeError, KeyError) as exc:
                errors.append(f"{path}.{evidence_field}: {exc}")
                continue
            for field in fields:
                actual, supplied = profile.get(field), entity.get(field)
                if fill_missing and not supplied and actual:
                    entity[field] = supplied = actual
                if field in {"city", "state"} and not supplied and not actual:
                    continue
                if field == "employee_range":
                    matches = employee_range_bounds(actual) is not None and employee_range_bounds(supplied) == employee_range_bounds(actual)
                else:
                    aliases = {_text(actual)}
                    if field == "country":
                        location = profile.get("location")
                        location = location if isinstance(location, dict) else {}
                        parsed = location.get("parsed")
                        parsed = parsed if isinstance(parsed, dict) else {}
                        aliases.update(_text(parsed.get(k)) for k in ("countryFull", "country", "countryCode"))
                        aliases.add(_text(location.get("countryCode")))
                    matches = bool(_text(supplied)) and _text(supplied) in aliases
                if not matches:
                    errors.append(f"{path}.{field} must match the saved HarvestAPI LinkedIn value ({actual!r})")
    return errors


if __name__ == "__main__":
    try:
        errors = linkedin_receipt_errors(json.load(sys.stdin), Path(sys.argv[1]))
    except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
        errors = [str(exc)]
    print(json.dumps({"valid": not errors, "errors": errors}))
    sys.exit(2 if errors else 0)
