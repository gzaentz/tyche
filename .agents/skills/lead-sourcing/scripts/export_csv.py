#!/usr/bin/env python3
"""Export accepted TYCHE company-contact pairs to the fixed sales CSV."""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
import sys
from typing import Any, Iterable
from urllib.parse import urlsplit


CSV_COLUMNS = [
    "Name",
    "Email",
    "Role",
    "Company",
    "LinkedIn",
    "Website",
    "Company LinkedIn",
    "Industry",
    "Sub Industry",
    "City",
    "State",
    "Country",
    "HQ State",
    "HQ Country",
    "Employee Count",
    "Description",
    "Intent Details",
    "Phone",
]


class ExportError(ValueError):
    """Raised when a structured result cannot produce a safe CSV row."""


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def _is_linkedin_url(value: str) -> bool:
    if not value:
        return False
    try:
        host = (urlsplit(value).hostname or "").lower()
    except ValueError:
        return False
    return host == "linkedin.com" or host.endswith(".linkedin.com")


def _contact_linkedin(contact: dict[str, Any]) -> str:
    explicit = _text(contact.get("linkedin_url"))
    if explicit:
        return explicit
    contact_url = _text(contact.get("contact_url"))
    return contact_url if _is_linkedin_url(contact_url) else ""


def _website(company: dict[str, Any]) -> str:
    explicit = _text(company.get("website"))
    if explicit:
        return explicit
    domain = _text(company.get("domain"))
    if not domain:
        return ""
    if domain.startswith(("http://", "https://")):
        return domain
    return "https://" + domain.lstrip("/")


def _intent_details(signal: dict[str, Any]) -> str:
    parts: list[str] = []
    values = (
        ("Signal", signal.get("signal")),
        ("Date", signal.get("evidence_date")),
        ("Details", signal.get("evidence_text")),
        ("Source", signal.get("evidence_url")),
    )
    for label, value in values:
        text = _text(value)
        if text:
            parts.append(f"{label}: {text}")
    return "; ".join(parts)


def iter_rows(document: Any) -> Iterable[dict[str, str]]:
    if not isinstance(document, dict):
        raise ExportError("results.json must contain one JSON object")
    accepted = document.get("accepted")
    if not isinstance(accepted, list):
        raise ExportError("results.json accepted must be an array")

    for index, accepted_row in enumerate(accepted):
        if not isinstance(accepted_row, dict):
            raise ExportError(f"accepted[{index}] must be an object")
        company = _object(accepted_row.get("company"))
        contact = _object(accepted_row.get("primary_contact"))
        signal = _object(accepted_row.get("signal_evidence"))
        if not company or not contact:
            raise ExportError(
                f"accepted[{index}] requires company and primary_contact objects"
            )

        yield {
            "Name": _text(contact.get("full_name")),
            "Email": _text(contact.get("email")),
            "Role": _text(contact.get("current_title")),
            "Company": _text(company.get("canonical_name")),
            "LinkedIn": _contact_linkedin(contact),
            "Website": _website(company),
            "Company LinkedIn": _text(company.get("linkedin_url")),
            "Industry": _text(company.get("industry")),
            "Sub Industry": _text(company.get("sub_industry")),
            "City": _text(contact.get("city")),
            "State": _text(contact.get("state")),
            "Country": _text(contact.get("country")),
            "HQ State": _text(company.get("hq_state")),
            "HQ Country": _text(company.get("hq_country")),
            "Employee Count": _text(company.get("employee_count")),
            "Description": _text(company.get("description")),
            "Intent Details": _intent_details(signal),
            "Phone": _text(contact.get("phone")),
        }


def export_csv(document: Any, destination: pathlib.Path) -> int:
    rows = list(iter_rows(document))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export accepted TYCHE rows to the fixed sales CSV."
    )
    parser.add_argument("results", type=pathlib.Path)
    parser.add_argument("destination", type=pathlib.Path)
    args = parser.parse_args()

    try:
        document = json.loads(args.results.read_text(encoding="utf-8"))
        count = export_csv(document, args.destination)
    except (OSError, json.JSONDecodeError, ExportError) as exc:
        print(json.dumps({"exported": False, "error": str(exc)}), file=sys.stderr)
        return 2

    print(json.dumps({"exported": True, "rows": count, "path": str(args.destination)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
