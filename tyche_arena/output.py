"""Map reviewed TYCHE records to Arena's current intent-details/contact schema."""

import json
import ipaddress
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit

import budget_guard
import linkedin_receipts
import run_attempt
from validate_run import _identity
from .constraints import check_contact


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + " must be nonempty text")
    return value.strip()


def evidence_value(evidence, key):
    return evidence.get(key, evidence.get("evidence_" + key))


def public_url(value):
    parsed = urlsplit(text(value, "URL"))
    host = (parsed.hostname or "").rstrip(".").lower()
    if (parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password
            or host == "localhost" or host.endswith((".internal", ".invalid", ".local", ".localhost", ".onion", ".test"))):
        raise ValueError("Arena requires a public HTTP URL")
    parsed.port  # Reject malformed ports too.
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host or not any(c.isalpha() for c in host.rsplit(".", 1)[1]):
            raise ValueError("Arena requires a public HTTP URL")
    else:
        if not address.is_global:
            raise ValueError("Arena requires a public HTTP URL")
    return value


def companies(run_file, icp):
    document = budget_guard.read_object(run_file)
    if json.loads(document["request"]["original_text"]) != icp:
        raise ValueError("Arena delivery ICP differs from the saved request")
    if document.get("final_review", {}).get("review_ref") != run_attempt.review_fingerprint(document):
        raise ValueError("Approve the current final evidence review before Arena delivery")
    _, validation = run_attempt.delivery_preflight(run_file, document)
    if not validation["delivery_allowed"]:
        raise ValueError("; ".join(validation["errors"]))
    output = []
    kinds = {_identity(signal["kind"]): index for index, signal in enumerate(document["request"]["buying_signals"])}
    for row in document["accepted"]:
        company, person = row["company"], row["primary_contact"]
        check_contact(person, icp)
        signals = []
        attribute = None
        for check in row["qualification_checks"]:
            if check["status"] != "pass":
                continue
            proof = next((e for e in check.get("evidence", []) if evidence_value(e, "url")), None)
            if not proof:
                continue
            if _identity(check.get("signal")) in kinds:
                signals.append({"matched_icp_signal": kinds[_identity(check["signal"])], "description": check["claim"],
                    "date": evidence_value(proof, "date"), "url": evidence_value(proof, "url")})
            if icp.get("required_attribute") and not check.get("signal") and _identity(check["criterion"]) == _identity(icp["required_attribute"]):
                attribute = {"text": icp["required_attribute"], "passed": True,
                    "evidence_url": evidence_value(proof, "url"), "evidence_quote": evidence_value(proof, "text"),
                    "explanation": check["claim"]}
        if not signals or icp.get("required_attribute") and attribute is None:
            raise ValueError("Arena requires mapped signal and required-attribute evidence")
        for signal in signals:
            public_url(signal["url"])
        if attribute:
            public_url(attribute["evidence_url"])
        paragraph = text(row.get("intent_details"), "intent_details")
        if (len(paragraph) > 2000 or re.search(r"\n\s*\n|(?:^|\n)\s*(?:#{1,6}\s|[-*•]\s|\d+[.)]\s|>)", paragraph)
                or "```" in paragraph or any(unicodedata.category(c) in {"Cc", "Cf", "Cs"} and c not in "\r\n\t" for c in paragraph)):
            raise ValueError("Arena intent_details requires one plain paragraph of at most 2000 characters")
        source = (person.get("location_evidence") or person)["source"]
        profile = linkedin_receipts._saved_profile(run_file, source, person["linkedin_url"], "in",
            document["routes"], company.get("linkedin_url"))
        receipt = run_attempt.read_receipt(run_file, source["route_id"])["result"]
        if receipt["attempt"]["request"].get("payload", {}).get("findEmail") != "true":
            raise ValueError("Arena email must come from HarvestAPI get_profile with findEmail=true")
        # Use only the selected raw profile, never an LLM-authored email_source.
        emails = profile.get("emails", [])
        observed = {str(e.get("email") if isinstance(e, dict) else e).strip().casefold() for e in emails}
        if profile.get("email"):
            observed.add(str(profile["email"]).strip().casefold())
        if person["email"].strip().casefold() not in observed:
            raise ValueError("Arena email is absent from the selected provider profile")
        record_id = text(profile.get("recordId") or profile.get("record_id") or profile.get("id"), "HarvestAPI record ID")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/~-]{0,199}", record_id):
            raise ValueError("HarvestAPI record ID violates Arena's source contract")
        contact = {"full_name": person["full_name"], "role": person["current_title"],
            "linkedin_url": person["linkedin_url"], "email": person["email"],
            "location": {"country": person["country"], **({"region": person["state"]} if person.get("state") else {}),
                         **({"city": person["city"]} if person.get("city") else {})},
            "email_source": {"provider": "harvestapi", "tool": "harvestapi_get_profile", "record_id": record_id}}
        output.append({"company_name": company["canonical_name"],
            "company_website": public_url(company.get("website") or "https://" + company["domain"]),
            "company_linkedin": company["linkedin_url"], "industry": company["industry"],
            "employee_count": company["employee_range"], "company_stage": company.get("company_stage", ""),
            "country": text(company.get("hq_country"), "company country"), "state": company.get("hq_state", ""),
            "intent_details": " ".join(paragraph.split()), "intent_signals": signals,
            "required_attribute": attribute, "contact": contact})
    if len(output) > min(5, document["request"]["target_count"]):
        raise ValueError("Arena company limit exceeded")
    return output


def deliver(run_file, validation, icp, checkpoint=None):
    rows = companies(run_file, icp)
    result = {"companies": rows}
    path = Path(run_file).with_name("companies.json")
    payload = json.dumps(result, ensure_ascii=True, allow_nan=False).encode()
    if len(payload) > 512 * 1024:
        raise ValueError("Arena output exceeds 512 KiB")
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)
    Path(run_file).with_name("validation.json").write_text(json.dumps(validation, indent=2) + "\n")
    if checkpoint:
        checkpoint(rows)
    return {"delivery_allowed": True, "companies": rows, "output": str(path)}
