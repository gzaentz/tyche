"""LinkedIn field receipts for accepted-lead test documents (no provider calls)."""


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
