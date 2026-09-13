"""LinkedIn field receipts for accepted-lead test documents (no provider calls)."""


from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def add_linkedin_fields(document):
    document["routes"] = list(document.get("routes", []))
    for index, row in enumerate(document.get("accepted", [])):
        company = row["company"]
        company.setdefault("employee_range", "201-500")
        entities = [(company, "employee_range_evidence", "company", company["employee_range"])]
        for contact in [row["primary_contact"], *row.get("backup_contacts", [])]:
            contact.setdefault("country", "United States")
            entities.append((contact, "location_evidence", "profile", ", ".join(
                contact[k] for k in ("city", "state", "country") if contact.get(k))))
        for offset, (entity, field, kind, source_text) in enumerate(entities):
            rid = f"harvest-fields-{index}-{offset}"
            tool = f"harvestapi_get_{kind}"
            url = entity.get("linkedin_url", entity.get("contact_url"))
            path = "company" if kind == "company" else "in"
            if not isinstance(url, str) or f"linkedin.com/{path}/" not in url:
                url = f"https://www.linkedin.com/{path}/fixture-{index}-{offset}"
            entity.setdefault(field, {
                "evidence_url": url, "evidence_date": "2026-09-01",
                "evidence_date_basis": "observed_current", "evidence_text": source_text,
                "source": {"provider": "deepline", "operation": "execute", "tool": tool, "route_id": rid},
            })
            if not any(r.get("route_id") == rid for r in document.setdefault("routes", [])):
                document["routes"].append({
                    "route_id": rid, "phase": "account_verification" if kind == "company" else "contact_verification",
                    "provider": "deepline", "operation": "execute", "tool": tool,
                    "provider_status": "ok", "paid_calls": 0,
                    "cost_credits": 0, "cost_upper_bound_credits": 0, "cost_basis": "actual",
                    "accepted_leads_before_call": None,
                })
    return document


def write_linkedin_receipts(run_file, document):
    """Save immutable source fixtures before exercising review/delivery mutations."""
    import hashlib
    import json
    from pathlib import Path
    from budget_guard import run_fingerprint
    from linkedin_receipts import employee_range_bounds

    directory = Path(run_file).parent / "receipts"
    for row in document.get("accepted", []):
        entities = [(row["company"], "employee_range_evidence", "company")]
        entities += [(c, "location_evidence", "profile") for c in [row["primary_contact"], *row.get("backup_contacts", [])]]
        for entity, field, kind in entities:
            evidence = entity[field]
            source = evidence["source"]
            rid = source["route_id"]
            fingerprint = hashlib.sha256(rid.encode()).hexdigest()
            route = next(r for r in document["routes"] if r["route_id"] == rid)
            route["request_fingerprint"] = fingerprint
            profile = {"linkedinUrl": evidence["evidence_url"], "name": "Fixture"}
            if kind == "company":
                lower, upper = employee_range_bounds(entity["employee_range"])
                profile["employeeCountRange"] = {"start": lower, "end": upper}
            else:
                profile["firstName"] = "Fixture"
                profile["location"] = {"linkedinText": evidence["evidence_text"],
                    "parsed": {"countryFull": entity.get("country"), "state": entity.get("state"), "city": entity.get("city")}}
            receipt = {"receipt_status": "complete", "status": "ok", **source,
                "request_fingerprint": fingerprint, "run_fingerprint": run_fingerprint(run_file),
                "provider_response": {"exit_code": 0, "body": {"status": "ok", "element": profile}, "stderr": ""}}
            directory.mkdir(exist_ok=True)
            (directory / (rid + ".json")).write_text(json.dumps(receipt))
