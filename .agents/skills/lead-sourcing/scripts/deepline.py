#!/usr/bin/env python3
"""Small JSON adapter for the installed Deepline CLI.

The adapter deliberately does not call Deepline over HTTP.  The CLI owns
authentication and provider selection; this module only translates a small,
stable JSON contract into ``deepline tools`` commands.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from provider_output import ResponseFile, load_json, response_body
from budget_guard import guarded_call


STATUSES = {
    "ok",
    "no_results",
    "partial",
    "rate_limited",
    "auth_failed",
    "quota_exceeded",
    "timeout",
    "schema_error",
    "provider_error",
    "config_error",
}

_SECRET_KEY = re.compile(
    r"(?:api[_-]?key|access[_-]?key|secret|token|password|authorization|cookie|credential|private[_-]?key)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")
_URL_SECRET = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?key|token|secret|password|signature)=[^&#\s]+)"
)
_INLINE_SECRET = re.compile(
    r'''(?i)((?:["']?(?:api[_-]?key|access[_-]?key|private[_-]?key|credentials?|token|secret|password|signature|authorization|cookie)["']?)\s*[=:]\s*)(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|(?:Basic|Bearer)\s+[^,;\s]+|[^,;\s}]+)'''
)
_SECRET_HEADER = re.compile(r"(?im)^(\s*(?:authorization|(?:set-)?cookie)\s*:\s*)[^\r\n]*")
_STATUS_WORDS = {status: status for status in STATUSES}
_DEEPLINE_BIN = "DEEPLINE_BIN"
_EMPTY_CONTAINER_KEYS = {
    "toolResponse",
    "tool_response",
    "rawV2",
    "raw_v2",
    "raw",
    "getters",
    "extractedLists",
    "extracted_lists",
}
_PROVIDER_ERROR_STATUSES = {"rate_limited", "auth_failed", "quota_exceeded", "timeout", "provider_error"}
_FAILURE_STATUSES = _PROVIDER_ERROR_STATUSES | {"schema_error", "config_error"}
_CONTACT_RECORD_KEYS = {
    "contact",
    "contact_name",
    "person",
    "person_name",
    "full_name",
    "fullName",
    "first_name",
    "firstName",
    "last_name",
    "lastName",
}
_SCALAR_RESULT_KEYS = ("count", "total")
_EXTRACTED_RESULT_KEYS = (
    "suggestions",
    "results",
    "items",
    "records",
    "data",
    "rows",
    "values",
    "preview",
    "elements",
    "matches",
    "evidence",
)


def _extracted_list_records(value: Any) -> List[Any]:
    """Extract rows from serialized Deepline list/getter output.

    ``deepline tools execute --json`` normally exposes provider rows through
    ``toolResponse.raw``. Some tools, including autocomplete tools, expose
    only a declared list such as ``suggestions`` (or a serialized dataset
    preview) in the command envelope. Keep this handling scoped to the
    extracted-list container so a generic ``{"value": ...}`` response is not
    accepted as a provider envelope by accident.
    """

    if isinstance(value, list):
        return value
    if not isinstance(value, dict):
        return []
    empty_rows: Optional[List[Any]] = None
    # The CLI may include several lists in this container. Only inspect known
    # result-bearing keys first, because metadata lists such as ``columns``
    # can appear before the actual provider rows.
    for key in _EXTRACTED_RESULT_KEYS:
        candidate = value.get(key)
        if isinstance(candidate, list):
            if candidate:
                return candidate
            empty_rows = candidate
            continue
        if not isinstance(candidate, dict):
            continue
        if any(row_key in candidate for row_key in ("value", "label")):
            return [candidate]
        rows = _extracted_list_records(candidate)
        if rows:
            return rows
    # A serialized single row can be represented directly in the container.
    if any(key in value for key in ("value", "label")):
        return [value]
    return empty_rows or []


def _is_extracted_list_envelope(value: Any) -> bool:
    """Return whether a serialized extracted-list container has a known shape."""

    if isinstance(value, list):
        return True
    if not isinstance(value, dict):
        return False
    if not value:
        return True
    if any(key in value for key in ("value", "label")):
        return True
    for key in _EXTRACTED_RESULT_KEYS:
        if key not in value:
            continue
        candidate = value[key]
        if isinstance(candidate, list):
            return True
        if isinstance(candidate, dict) and _is_extracted_list_envelope(candidate):
            return True
    return False


def _scalar_result(value: Any) -> Optional[Dict[str, Any]]:
    """Return a valid count/total summary object as one result row."""

    if not isinstance(value, dict):
        return None
    if not any(
        key in value
        and isinstance(value[key], (int, float))
        and not isinstance(value[key], bool)
        for key in _SCALAR_RESULT_KEYS
    ):
        return None
    return value


class InputError(ValueError):
    """An invalid local request."""


class ConfigError(RuntimeError):
    """A local invocation/configuration failure."""


class CallTimeout(RuntimeError):
    """The bounded Deepline process timeout elapsed."""

    def __init__(self, message, stdout="", stderr=""):
        super().__init__(message)
        self.stdout = stdout.decode("utf-8", errors="replace") if isinstance(stdout, bytes) else (stdout or "")
        self.stderr = stderr.decode("utf-8", errors="replace") if isinstance(stderr, bytes) else (stderr or "")


def redact(value: Any) -> Any:
    """Remove likely credentials from arbitrary provider data before output."""

    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        for key, item in value.items():
            if _SECRET_KEY.search(str(key)):
                result[str(key)] = "[REDACTED]"
            else:
                result[str(key)] = redact(item)
        return result
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = _SECRET_HEADER.sub(r"\1[REDACTED]", value)
        value = _BEARER.sub(r"\1[REDACTED]", value)
        value = _URL_SECRET.sub(lambda match: match.group(1).split("=", 1)[0] + "=[REDACTED]", value)
        return _INLINE_SECRET.sub(r"\1[REDACTED]", value)
    return value


def _safe_error(message: str) -> Dict[str, str]:
    """Return one bounded, redacted failure line without echoing the command."""

    redacted = str(redact(message or ""))
    lines = [re.sub(r"\s+", " ", line).strip() for line in redacted.splitlines()]
    lines = [line for line in lines if line]
    diagnostic = next(
        (
            line
            for line in lines
            if any(
                marker in line.lower()
                for marker in ("error", "failed", "unknown", "invalid", "not found")
            )
        ),
        lines[0] if lines else "Deepline command failed",
    )
    if len(diagnostic) > 500:
        diagnostic = diagnostic[:497].rstrip() + "..."
    return {"message": diagnostic}


def _json_from_text(text: str) -> Any:
    """Decode JSON despite a CLI notice before the JSON payload."""

    if not isinstance(text, str) or not text.strip():
        raise ValueError("empty provider response")
    # Preserve undecodable CLI bytes in the receipt, never as a parsed result.
    text.encode("utf-8")
    try:
        return load_json(text)
    except json.JSONDecodeError:
        if text.lstrip().startswith(("{", "[")):
            raise
    # Skip a leading CLI notice, never a malformed enclosing JSON document.
    start = re.search(r"(?m)^[ \t]*[\[{]", text)
    if start:
        return load_json(text[start.start():])
    raise ValueError("provider response was not JSON")


def _first(mapping: Dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in mapping and mapping[name] not in (None, ""):
            return mapping[name]
    return None


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, dict):
        value = _first(value, "name", "value", "text", "title", "url")
    if value is None:
        return None
    return str(value).strip() or None


def _domain(value: Any) -> Optional[str]:
    text = _text(value)
    if not text:
        return None
    candidate = text if "://" in text else "https://" + text
    host = urlparse(candidate).hostname
    if host:
        host = host.lower().rstrip(".")
        if host.startswith("www."):
            host = host[4:]
        return host
    return text.lower().strip().strip("/")


def _is_linkedin_url(value: Any) -> bool:
    text = _text(value)
    if not text:
        return False
    candidate = text if "://" in text else "https://" + text
    host = urlparse(candidate).hostname
    return bool(host) and (
        host.lower() == "linkedin.com" or host.lower().endswith(".linkedin.com")
    )


def _is_linkedin_company_url(value: Any) -> bool:
    """Return whether a URL is a LinkedIn company page, not a person profile."""

    text = _text(value)
    if not text:
        return False
    candidate = text if "://" in text else "https://" + text
    parsed = urlparse(candidate)
    host = parsed.hostname
    return bool(host) and (
        host.lower() == "linkedin.com" or host.lower().endswith(".linkedin.com")
    ) and parsed.path.lower().startswith("/company/")


def _artifact_refs(row: Dict[str, Any]) -> Any:
    for key in (
        "raw_artifact_refs",
        "raw_artifact_ref",
        "artifact_refs",
        "artifact_ref",
        "artifact",
        "rawV2",
        "raw_v2",
        "raw",
    ):
        if key in row and row[key] not in (None, "", [], {}):
            return redact(row[key])
    return None


def _is_email_validation_record(value: Any) -> bool:
    """Return whether a row looks like a scalar email-validation result."""

    if not isinstance(value, dict):
        return False
    email = value.get("address")
    if not isinstance(email, str) or not email.strip():
        person_fields = _CONTACT_RECORD_KEYS | {
            "contact_url",
            "person_url",
            "profile_url",
            "contact_title",
            "person_title",
            "job_title",
            "current_title",
        }
        if any(field in value for field in person_fields):
            return False
        email = value.get("email")
    status = value.get("status")
    if not isinstance(email, str) or not email.strip():
        return False
    if not isinstance(status, str) or not status.strip():
        return False
    if isinstance(value.get("result"), str) and value["result"].strip().lower() in {
        "deliverable", "risky", "undeliverable", "unknown",
    }:
        return True
    normalized = status.strip().lower().replace("-", "_")
    return normalized not in _STATUS_WORDS and normalized not in {
        "success",
        "succeeded",
        "complete",
        "completed",
        "failed",
        "failure",
        "error",
    }


def normalize_evidence(
    row: Any,
    provider: str = "deepline",
    tool: Optional[str] = None,
    entity_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Normalize one provider row while retaining useful provider metadata."""

    source: Dict[str, Any] = row if isinstance(row, dict) else {"value": row}
    nested_contact = source.get("contact")
    if isinstance(nested_contact, dict) and (entity_type or "").strip().lower() not in {
        "account", "company", "organization"
    }:
        # Some enrichment tools return contact fields inside a scalar envelope.
        source = dict(source)
        source["contact_details"] = nested_contact
        for key, nested_key in (("full_name", "name"), ("contact_email", "email"), ("domain", "domain")):
            value = nested_contact.get(nested_key)
            if source.get(key) is None and isinstance(value, str) and value.strip():
                source[key] = value
    result: Dict[str, Any] = redact(source)
    is_email_validation = _is_email_validation_record(source)
    basic_info = source.get("basic_info")
    basic_info = basic_info if isinstance(basic_info, dict) else {}
    summary = source.get("summary")
    summary = summary if isinstance(summary, dict) else {}
    link = source.get("link")
    link = link if isinstance(link, dict) else {}
    entity_label = (entity_type or "").strip().lower()
    company_entity = entity_label in {"account", "company", "organization"}
    contact_entity = entity_label in {"contact", "person"}
    nested_company_identity = (
        not contact_entity
        and _text(summary.get("name")) is not None
        and any(_text(link.get(key)) is not None for key in ("domain", "website"))
        and _is_linkedin_company_url(link.get("linkedin"))
    )
    positions = source.get("currentPositions")
    positions = positions if isinstance(positions, list) else []
    current_position = next(
        (
            position
            for position in positions
            if isinstance(position, dict) and position.get("current") is True
        ),
        next((position for position in positions if isinstance(position, dict)), {}),
    )
    result["company"] = _text(
        _first(source, "company", "company_name", "account", "organization")
    ) or _text(_first(basic_info, "name", "company", "company_name")) or _text(
        _first(current_position, "companyName", "company_name")
    ) or (_text(summary.get("name")) if nested_company_identity else None)
    result["company_linkedin_url"] = _text(
        _first(source, "company_linkedin_url", "companyLinkedinUrl")
    ) or _text(_first(current_position, "companyLinkedinUrl", "company_linkedin_url")) or (
        _text(link.get("linkedin")) if nested_company_identity else None
    )
    domain_value = _first(source, "domain", "company_domain")
    if _is_linkedin_url(domain_value):
        domain_value = None
    if domain_value in (None, ""):
        for key in ("website", "company_url"):
            candidate = source.get(key)
            if candidate not in (None, "") and not _is_linkedin_url(candidate):
                domain_value = candidate
                break
    if domain_value in (None, ""):
        domain_value = _first(
            basic_info, "primary_domain", "domain", "website", "company_url"
        )
    if domain_value in (None, "") and nested_company_identity:
        domain_value = _first(link, "domain", "website")
    result["domain"] = None if _is_linkedin_url(domain_value) else _domain(domain_value)
    result["signal"] = _text(_first(source, "signal", "signal_type", "intent", "type", "category"))
    result["evidence_url"] = _text(_first(source, "evidence_url", "source_url", "url", "link", "source"))
    result["evidence_date"] = _text(_first(source, "evidence_date", "date", "published_at", "published", "timestamp"))
    result["evidence_text"] = _text(_first(source, "evidence_text", "text", "snippet", "description", "evidence", "content"))
    result["provider"] = _text(_first(source, "provider")) or provider
    result["tool"] = _text(_first(source, "tool", "tool_name")) or tool

    # Contact-capable tools use several common names for person data. Keep the
    # source fields untouched, but expose stable contact fields for callers
    # that request a people/entity route. A bare ``name`` is only considered a
    # contact when another contact-shaped field is present; otherwise company
    # rows with a name do not gain a misleading contact.
    contact = _text(
        _first(
            source,
            "contact",
            "contact_name",
            "person",
            "person_name",
            "full_name",
            "fullName",
        )
    )
    if contact is None:
        first = _text(_first(source, "first_name", "firstName"))
        last = _text(_first(source, "last_name", "lastName"))
        contact = " ".join(part for part in (first, last) if part) or None
    contact_hint = any(
        key in source
        for key in (
            "profile_url",
            "contact_url",
            "person_url",
            "contact_title",
            "person_title",
            "job_title",
            "role",
            "email",
            "first_name",
            "last_name",
            "profileUrl",
            "linkedinUrl",
            "contactTitle",
            "jobTitle",
            "firstName",
            "lastName",
        )
    )
    if contact is None and contact_hint:
        contact = _text(_first(source, "name"))
    contact_url = _text(
        _first(
            source,
            "contact_url",
            "person_url",
            "profile_url",
            "linkedin_profile_url",
            "profileUrl",
        )
    )
    if contact_url is None:
        generic_linkedin_url = _text(_first(source, "linkedin_url", "linkedinUrl"))
        if not _is_linkedin_company_url(generic_linkedin_url):
            contact_url = generic_linkedin_url
    if _is_linkedin_company_url(contact_url):
        contact_url = None
    contact_hint = contact_hint or contact_url is not None
    contact_title = _text(
        _first(
            source,
            "contact_title",
            "person_title",
            "job_title",
            "role",
            "headline",
            "current_title",
            "currentTitle",
            "jobTitle",
            "contactTitle",
        )
    ) or _text(_first(current_position, "title"))
    contact_email = _text(
        _first(
            source,
            "contact_email",
            "person_email",
            "email",
            "email_address",
            "emailAddress",
        )
    )
    has_contact = not company_entity and not is_email_validation and any(
        value is not None for value in (contact, contact_url, contact_title, contact_email)
    )
    if has_contact:
        result["contact"] = contact
        result["contact_name"] = contact
        result["full_name"] = contact
        result["contact_url"] = contact_url
        result["contact_title"] = contact_title
        result["current_title"] = contact_title
        result["contact_email"] = contact_email
    if is_email_validation:
        result["email"] = _text(_first(source, "address", "email"))
        result["email_status"] = _text(source.get("result") or source.get("status"))
        result["email_sub_status"] = _text(source.get("sub_status"))
    if entity_type:
        result["entity_type"] = entity_type
    elif is_email_validation:
        result["entity_type"] = "email_validation"
    elif has_contact:
        # Only infer an entity type when contact-shaped fields make the intent
        # clear. The caller may provide any explicit wrapper label instead.
        result["entity_type"] = "contact"
    elif result.get("company"):
        result["entity_type"] = "company"
    refs = _artifact_refs(source)
    if refs is not None:
        result["raw_artifact_refs"] = refs
    return result


def _jsonapi_resource(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("id"), str)
        and isinstance(value.get("type"), str)
        and isinstance(value.get("attributes"), dict)
        and (
            "relationships" not in value
            or isinstance(value.get("relationships"), dict)
        )
    )


def _is_linkedin_post_url(value: Any) -> bool:
    text = _text(value)
    if not text:
        return False
    parsed = urlparse(text if "://" in text else "https://" + text)
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()
    return (host == "linkedin.com" or host.endswith(".linkedin.com")) and path.startswith(
        ("/posts/", "/feed/update/", "/pulse/")
    )


def _structured_execute_envelope(value: Any) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Select a complete JSON:API or Harvest envelope for execute only."""

    if isinstance(value, str):
        try:
            value = _json_from_text(value)
        except ValueError:
            return None
    if not isinstance(value, dict):
        return None
    data = value.get("data")
    resources = data if isinstance(data, list) else [data]
    if (
        isinstance(data, (dict, list))
        and all(_jsonapi_resource(resource) for resource in resources)
        and ("included" not in value or isinstance(value.get("included"), list))
    ):
        return "jsonapi", value
    elements = value.get("elements")
    if isinstance(elements, list) and all(
        isinstance(post, dict)
        and isinstance(post.get("id"), str)
        and _is_linkedin_post_url(post.get("linkedinUrl"))
        and any(
            key in post
            for key in ("content", "author", "postedAt", "repost", "repostedBy")
        )
        for post in elements
    ):
        return "harvest", value
    for key in ("toolResponse", "tool_response", "rawV2", "raw_v2", "result", "response", "output", "raw"):
        selected = _structured_execute_envelope(value.get(key))
        if selected:
            return selected
    return None


def _included_index(included: Any) -> Tuple[Dict[Tuple[str, str], Dict[str, Any]], set]:
    index: Dict[Tuple[str, str], Dict[str, Any]] = {}
    conflicts = set()
    for resource in included if isinstance(included, list) else []:
        if not _jsonapi_resource(resource):
            continue
        key = (resource["type"], resource["id"])
        if key in index and index[key] != resource:
            conflicts.add(key)
        else:
            index[key] = resource
    return index, conflicts


def _relationship_refs(resource: Dict[str, Any], relationship: str) -> List[Dict[str, Any]]:
    relationships = resource.get("relationships", {})
    relation = relationships.get(relationship) if isinstance(relationships, dict) else None
    data = relation.get("data") if isinstance(relation, dict) else None
    refs = data if isinstance(data, list) else [data]
    return [
        ref
        for ref in refs
        if isinstance(ref, dict)
        and isinstance(ref.get("type"), str)
        and isinstance(ref.get("id"), str)
    ]


def _normalize_jsonapi(
    envelope: Dict[str, Any], tool: str, entity_type: Optional[str]
) -> List[Dict[str, Any]]:
    data = envelope.get("data")
    resources = data if isinstance(data, list) else [data]
    included, conflicts = _included_index(envelope.get("included"))
    results = []
    for resource in resources:
        attributes = resource["attributes"]
        row = redact(resource)
        related_companies = []
        company_keys = set()
        unresolved_company = False
        for relationship, relation in resource.get("relationships", {}).items():
            relation_data = relation.get("data") if isinstance(relation, dict) else None
            relation_refs = relation_data if isinstance(relation_data, list) else [relation_data]
            if any(
                isinstance(ref, dict)
                and ref.get("type") == "company"
                and not isinstance(ref.get("id"), str)
                for ref in relation_refs
            ):
                unresolved_company = True
            for ref in _relationship_refs(resource, relationship):
                if ref["type"] != "company":
                    continue
                key = (ref["type"], ref["id"])
                linked = None if key in conflicts else included.get(key)
                linked_attributes = linked.get("attributes", {}) if linked else {}
                related_companies.append(
                    {
                        "relationship": relationship,
                        "id": ref["id"],
                        "company": _text(linked_attributes.get("company_name")),
                        "domain": _domain(linked_attributes.get("domain")),
                    }
                )
                company_keys.add(key)
                unresolved_company = unresolved_company or linked is None
        source = None
        source_refs = _relationship_refs(resource, "most_relevant_source")
        if len(source_refs) == 1 and source_refs[0]["type"] == "news_article":
            key = (source_refs[0]["type"], source_refs[0]["id"])
            if key not in conflicts:
                source = included.get(key)
        source_attributes = source.get("attributes", {}) if source else {}
        row.update(
            {
                "company": None,
                "domain": None,
                "signal": _text(attributes.get("category")) or resource["type"],
                "evidence_url": _text(source_attributes.get("url"))
                or _text(attributes.get("url")),
                "evidence_date": _text(source_attributes.get("published_at"))
                or _text(attributes.get("published_at")),
                "evidence_text": _text(
                    _first(
                        attributes,
                        "article_sentence",
                        "summary",
                        "description",
                        "content",
                        "title",
                    )
                ),
                "provider": "deepline",
                "tool": tool,
                "entity_type": entity_type or "signal",
            }
        )
        if "effective_date" in attributes:
            row["event_date"] = _text(attributes.get("effective_date"))
        if related_companies:
            row["related_companies"] = related_companies
        if len(company_keys) == 1 and not unresolved_company:
            row["company"] = related_companies[0]["company"]
            row["domain"] = related_companies[0]["domain"]
        if source is not None:
            row["related_source"] = redact(source)
        results.append(redact(row))
    return results


def _normalize_harvest(
    envelope: Dict[str, Any], tool: str, entity_type: Optional[str]
) -> List[Dict[str, Any]]:
    results = []
    for post in envelope["elements"]:
        author = post.get("author")
        author = author if isinstance(author, dict) else {}
        author_url = _text(author.get("linkedinUrl"))
        company_author = _is_linkedin_company_url(author_url)
        posted_at = post.get("postedAt")
        posted_at = posted_at if isinstance(posted_at, dict) else {}
        row = redact(post)
        row.update(
            {
                "company": _text(author.get("name")) if company_author else None,
                "domain": None,
                "company_linkedin_url": author_url if company_author else None,
                "signal": "linkedin_post",
                "evidence_url": _text(post.get("linkedinUrl")),
                "evidence_date": _text(posted_at.get("date")),
                "evidence_text": _text(post.get("content")),
                "provider": "deepline",
                "tool": tool,
                "entity_type": entity_type or "signal",
            }
        )
        results.append(redact(row))
    return results


def _structured_metadata(kind: str, envelope: Dict[str, Any]) -> Dict[str, Any]:
    metadata = {
        key: redact(envelope[key])
        for key in ("meta", "links", "pagination")
        if key in envelope
    }
    pagination = envelope.get("pagination")
    if kind == "harvest" and isinstance(pagination, dict):
        cursor = pagination.get("paginationToken")
        if isinstance(cursor, str) and cursor:
            safe_pagination = metadata.setdefault("pagination", {})
            if isinstance(safe_pagination, dict):
                safe_pagination["next_cursor"] = redact(cursor)
    return metadata


def _output_preview_metadata(value: Any) -> Dict[str, Any]:
    """Retain observed preview counts without inferring provider completeness."""

    if not isinstance(value, dict):
        return {}
    for key in ("output_preview", "outputPreview"):
        preview = value.get(key)
        if not isinstance(preview, dict):
            continue
        metadata = {
            field: redact(preview[field])
            for field in ("kind", "rowCount", "columns")
            if field in preview
        }
        rows = next(
            (
                preview.get(field)
                for field in ("rows", "preview", "items")
                if isinstance(preview.get(field), list)
            ),
            None,
        )
        if rows is not None:
            metadata["returnedRowCount"] = len(rows)
        return {"output_preview": metadata} if metadata else {}
    return {}


def _structured_status(envelope: Dict[str, Any]) -> Optional[str]:
    """Read route failure state only from a selected provider envelope."""

    status = envelope.get("status")
    if status is not None and (
        isinstance(status, bool) or not isinstance(status, (str, int, float))
    ):
        return "schema_error"
    error = envelope.get("error") or envelope.get("errors")
    diagnostic = json.dumps(redact(error), ensure_ascii=False) if error not in (None, "", [], {}) else ""
    if status == 429:
        return "rate_limited"
    if status == 401 or status == 403:
        return "auth_failed"
    if isinstance(status, (int, float)) and not isinstance(status, bool) and status >= 400:
        return "provider_error"
    if isinstance(status, str):
        normalized = status.strip().lower().replace("-", "_")
        if normalized in _FAILURE_STATUSES:
            return normalized
        if normalized in {"failed", "failure", "error"}:
            return _classify_error(diagnostic) if diagnostic else "provider_error"
        if normalized.isdigit() and int(normalized) >= 400:
            return _classify_error(normalized + " " + diagnostic)
    if diagnostic:
        return _classify_error(diagnostic)
    if envelope.get("partial") is True:
        return "partial"
    if isinstance(status, str) and normalized in _STATUS_WORDS:
        return normalized
    if isinstance(status, str) and normalized in {"success", "succeeded", "complete", "completed"}:
        return "ok"
    return None


def _records(value: Any) -> List[Any]:
    """Extract rows from common Deepline response envelopes."""

    if isinstance(value, str):
        try:
            return _records(_json_from_text(value))
        except ValueError:
            return []
    if isinstance(value, list):
        return value
    if not isinstance(value, dict):
        return []
    empty_direct: Optional[List[Any]] = None
    for key in (
        "evidence",
        "results",
        "items",
        "records",
        "rows",
        "matches",
        "tools",
        "getters",
        "elements",
        "suggestions",
    ):
        candidate = value.get(key)
        if isinstance(candidate, list):
            if candidate:
                return candidate
            empty_direct = candidate
    for key in ("extractedLists", "extracted_lists"):
        candidate = value.get(key)
        if isinstance(candidate, (dict, list)):
            records = _extracted_list_records(candidate)
            if records:
                return records
    for key in (
        "data",
        "output",
        "result",
        "response",
        "summary",
        "toolResponse",
        "tool_response",
        "rawV2",
        "raw_v2",
        "raw",
        "getters",
        "element",
    ):
        candidate = value.get(key)
        if isinstance(candidate, (dict, list, str)):
            found = _records(candidate)
            if found:
                return found
    # The CLI emits a bounded row preview when it materializes a declared list
    # but the raw provider envelope does not contain the list inline.
    for key in ("output_preview", "outputPreview"):
        candidate = value.get(key)
        if isinstance(candidate, dict):
            for preview_key in ("rows", "preview", "items"):
                rows = candidate.get(preview_key)
                if isinstance(rows, list):
                    return rows
            summary = candidate.get("summary")
            scalar = _scalar_result(summary)
            if scalar is not None:
                return [scalar]
    if empty_direct is not None:
        return empty_direct
    # A single row is useful for a provider that returns one company/evidence
    # object or one contact object. Contact-shaped direct responses are common
    # for person search tools and must not be treated as an unknown envelope.
    if any(
        key in value
        for key in (
            "company",
            "company_name",
            "domain",
            "signal",
            "evidence_text",
            "url",
        )
    ) or any(key in value for key in _CONTACT_RECORD_KEYS):
        return [value]
    scalar = _scalar_result(value)
    if scalar is not None:
        return [scalar]
    if _is_email_validation_record(value):
        return [value]
    return []


def _email_validation_output(
    parsed: Any, tool: str, limit: int, command_failed: bool = False
) -> Optional[Dict[str, Any]]:
    """Preserve explicit validator results even when its default verdict drops them."""

    records = [
        record
        for record in _records(parsed)[:limit]
        if _is_email_validation_record(record)
    ]
    containers = [parsed] if isinstance(parsed, dict) else []
    containers += [parsed[key] for key in ("toolResponse", "tool_response")
                   if isinstance(parsed, dict) and isinstance(parsed.get(key), dict)]
    pending = None
    for container in containers:
        for candidate in [container] + [container.get(key) for key in ("rawV2", "raw_v2", "raw")]:
            if (isinstance(candidate, dict) and candidate.get("status") in ("queue", "verifying")
                    and isinstance(candidate.get("id"), str) and candidate["id"].strip()):
                pending = redact({key: candidate[key] for key in ("id", "status", "email", "try_again_at") if key in candidate})
                break
        if pending:
            break
    if not records and not pending:
        return None
    statuses = [_envelope_status(container) for container in containers]
    error = _envelope_error(parsed)
    failure = next((status for status in statuses if status in _FAILURE_STATUSES), None)
    # Only the observed default-policy rejection can preserve a non-positive
    # verdict across CLI failure. Transport/auth failures never become success.
    policy_rejection = (
        failure in {None, "provider_error"} and error is not None
        and all(status not in _FAILURE_STATUSES - {"provider_error"} for status in statuses)
        and error["message"].strip().casefold() == "default send policy rejected the address"
        and bool(records) and not pending
        and all(record.get("result") is None and str(record.get("status", "")).strip().casefold()
                in {"invalid", "do_not_mail", "spamtrap", "abuse", "catch-all", "unknown"}
                for record in records)
    )
    if (failure or error or command_failed) and not policy_rejection:
        body = {
            "status": failure or (_classify_error(error["message"]) if error else "provider_error"),
            "provider": "deepline", "operation": "execute", "tool": tool,
            "entity_type": "email_validation", "results": [], "evidence": [],
            "provider_response": redact(parsed), **_execution_metadata(parsed),
        }
        if error:
            body["error"] = error
        return body
    if pending:
        return {
            "status": "partial", "provider": "deepline", "operation": "execute", "tool": tool,
            "entity_type": "email_validation", "results": [], "evidence": [],
            "pending_verification": pending, "provider_response": redact(parsed),
            **_execution_metadata(parsed),
        }
    evidence = [
        normalize_evidence(record, "deepline", tool, "email_validation")
        for record in records
    ]
    return {
        "status": "partial" if "partial" in statuses else "ok",
        "provider": "deepline",
        "operation": "execute",
        "tool": tool,
        "entity_type": "email_validation",
        "results": evidence,
        "evidence": evidence,
        **_execution_metadata(parsed),
    }


def _execution_metadata(parsed: Any) -> Dict[str, Any]:
    metadata = _output_preview_metadata(parsed)
    billing = parsed.get("billing") if isinstance(parsed, dict) else None
    if isinstance(billing, dict):
        amounts = {key: value for key in ("credits_charged", "cost_usd")
                   if isinstance((value := billing.get(key)), (int, float))
                   and not isinstance(value, bool) and 0 <= value <= sys.float_info.max and math.isfinite(value)}
        if amounts:
            metadata["billing"] = amounts
    return metadata


def _envelope_status(value: Any) -> Optional[str]:
    """Read status only from response envelopes, never from result rows."""

    def direct_status(mapping: Dict[str, Any]) -> Optional[str]:
        for key in ("status", "provider_status", "outcome", "state"):
            candidate = mapping.get(key)
            if isinstance(candidate, str):
                lowered = candidate.strip().lower().replace("-", "_")
                if lowered in _STATUS_WORDS:
                    return lowered
                if lowered in {"success", "succeeded", "complete", "completed"}:
                    return "ok"
                if lowered in {"empty", "none", "not_found", "notfound", "no_result"}:
                    return "no_results"
                if lowered in {"failed", "failure", "error"}:
                    return "provider_error"
        if mapping.get("success") is False or mapping.get("ok") is False:
            detail = mapping.get("error") or mapping.get("errors") or mapping.get("message") or ""
            return _classify_error(json.dumps(redact(detail), ensure_ascii=False)) if detail else "provider_error"
        if mapping.get("partial") is True:
            return "partial"
        if mapping.get("error") not in (None, "", [], {}):
            return _classify_error(json.dumps(redact(mapping.get("error")), ensure_ascii=False))
        if mapping.get("errors") not in (None, "", [], {}):
            return _classify_error(json.dumps(redact(mapping.get("errors")), ensure_ascii=False))
        return None

    if not isinstance(value, dict):
        return None
    status = direct_status(value)
    if status:
        return status
    # toolResponse is the only nested object treated as an envelope.  rawV2,
    # raw, getters, and result/data payloads may themselves contain a row whose
    # `status` field is provider data, not a route-level failure.
    for key in ("toolResponse", "tool_response"):
        candidate = value.get(key)
        if isinstance(candidate, dict):
            status = direct_status(candidate)
            if status:
                return status
    return None


def _envelope_error(value: Any) -> Optional[Dict[str, str]]:
    """Extract a bounded error message only from recognized response envelopes."""

    if not isinstance(value, dict):
        return None

    def error_value(mapping: Dict[str, Any]) -> Any:
        for key in ("error", "errors"):
            candidate = mapping.get(key)
            if candidate not in (None, "", [], {}):
                if isinstance(candidate, dict):
                    return _first(candidate, "message", "detail", "description", "code") or candidate
                return candidate
        if mapping.get("success") is False or mapping.get("ok") is False:
            return _first(mapping, "message", "detail", "description")
        return None

    candidate = error_value(value)
    if candidate in (None, "", [], {}):
        for key in ("toolResponse", "tool_response"):
            nested = value.get(key)
            if isinstance(nested, dict):
                candidate = error_value(nested)
                if candidate not in (None, "", [], {}):
                    break
    if candidate in (None, "", [], {}):
        return None
    message = candidate if isinstance(candidate, str) else json.dumps(candidate, ensure_ascii=False)
    return _safe_error(message)


def _known_envelope(value: Any) -> bool:
    """Return whether the CLI response has a recognized envelope shape."""

    if isinstance(value, list):
        return True
    if not isinstance(value, dict):
        if isinstance(value, str):
            try:
                return _known_envelope(_json_from_text(value))
            except ValueError:
                return False
        return False
    if _scalar_result(value) is not None:
        return True
    if any(
        key in value
        for key in (
            "evidence",
            "results",
            "items",
            "records",
            "rows",
            "matches",
            "tools",
            "elements",
            "suggestions",
        )
    ):
        return True
    if any(
        key in value
        for key in (
            "company",
            "company_name",
            "domain",
            "signal",
            "evidence_text",
            "evidence_url",
        )
    ) or any(key in value for key in _CONTACT_RECORD_KEYS):
        return True
    if _is_email_validation_record(value):
        return True
    for key in ("extractedLists", "extracted_lists"):
        if key in value:
            candidate = value[key]
            if candidate in (None, ""):
                return True
            if isinstance(candidate, (dict, list)):
                return _is_extracted_list_envelope(candidate)
    for key in ("output_preview", "outputPreview"):
        candidate = value.get(key)
        if isinstance(candidate, dict):
            if any(isinstance(candidate.get(preview_key), list) for preview_key in ("rows", "preview", "items")):
                return True
    for key in (
        "toolResponse",
        "tool_response",
        "response",
        "summary",
        "data",
        "output",
        "result",
        "rawV2",
        "raw_v2",
        "raw",
        "getters",
        "element",
    ):
        if key in value:
            candidate = value[key]
            if key in _EMPTY_CONTAINER_KEYS and candidate in (None, "", [], {}):
                return True
            if isinstance(candidate, (dict, list, str)) and _known_envelope(candidate):
                return True
    return False


def _classify_error(text: str, timed_out: bool = False) -> str:
    if (
        timed_out
        or "timeout" in text.lower()
        or "timed out" in text.lower()
        or "timed_out" in text.lower()
    ):
        return "timeout"
    lowered = text.lower()
    if any(marker in lowered for marker in ("429", "rate limit", "rate_limit", "too many requests")):
        return "rate_limited"
    if any(
        marker in lowered
        for marker in (
            "quota",
            "credit balance",
            "credits exhausted",
            "insufficient credits",
            "insufficient_credits",
            "insufficient balance",
            "insufficient_balance",
        )
    ):
        return "quota_exceeded"
    if any(
        marker in lowered
        for marker in (
            "401",
            "403",
            "unauthorized",
            "forbidden",
            "authentication",
            "invalid api key",
            "auth failed",
            "auth_failed",
            "not connected",
            "missing credentials",
        )
    ):
        return "auth_failed"
    if any(
        marker in lowered
        for marker in (
            "invalid_json",
            "validation_error",
            "schema error",
            "schema_error",
            "invalid input",
            "invalid_input",
        )
    ):
        return "schema_error"
    return "provider_error"


def _timeout_seconds(value: Any, default: float = 30.0, maximum: float = 120.0) -> float:
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise InputError("timeout_seconds must be a number") from exc
    if not math.isfinite(number) or number <= 0:
        raise InputError("timeout_seconds must be greater than zero")
    return min(number, maximum)


def _result_limit(value: Any, default: int = 10, maximum: int = 10) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise InputError("limit must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise InputError("limit must be an integer") from exc
    if number != value or number <= 0:
        raise InputError("limit must be a positive integer")
    return min(number, maximum)


def _validate_request(request: Any) -> Dict[str, Any]:
    if not isinstance(request, dict):
        raise InputError("input must be a JSON object")
    operation = request.get("operation", request.get("op"))
    if not isinstance(operation, str):
        raise InputError("operation is required")
    operation = operation.strip().lower().replace("-", "_")
    aliases = {
        "catalog_search": "search",
        "tools_search": "search",
        "catalog_describe": "describe",
        "tool_describe": "describe",
    }
    operation = aliases.get(operation, operation)
    if operation not in {"search", "describe", "execute"}:
        raise InputError("operation must be search, describe, or execute")
    request = dict(request)
    request["operation"] = operation
    if "entity_type" in request:
        entity_type = request["entity_type"]
        if not isinstance(entity_type, str) or not entity_type.strip():
            raise InputError("entity_type must be a non-empty string")
        # This is wrapper metadata only. It is deliberately not added to the
        # payload sent to the live Deepline tool, whose schema is discovered at
        # runtime and must not be guessed here.
        request["entity_type"] = entity_type.strip()
    if operation == "search":
        query = request.get("query", request.get("q"))
        if not isinstance(query, str) or not query.strip():
            raise InputError("search requires a non-empty query")
        request["query"] = query.strip()
    elif operation == "describe":
        tool = request.get("tool", request.get("name"))
        if not isinstance(tool, str) or not tool.strip():
            raise InputError("describe requires a tool name")
        request["tool"] = tool.strip()
    else:
        tool = request.get("tool", request.get("name"))
        payload = request.get("payload", request.get("input"))
        if not isinstance(tool, str) or not tool.strip():
            raise InputError("execute requires a tool name")
        if not isinstance(payload, dict):
            raise InputError("execute payload must be a JSON object")
        request["tool"] = tool.strip()
        request["payload"] = payload
        # This wrapper-only bound keeps every execute pilot within the skill's
        # maximum returned-row limit. It is not sent to the provider tool.
        request["limit"] = _result_limit(request.get("limit"))
    # Paid execute calls can take longer than catalog reads. A longer default
    # reduces the risk that a local timeout tempts a caller to repeat a paid
    # call whose remote outcome is unknown.
    if operation == "execute":
        request["timeout_seconds"] = _timeout_seconds(
            request.get("timeout_seconds"), default=240.0, maximum=780.0
        )
    else:
        request["timeout_seconds"] = _timeout_seconds(request.get("timeout_seconds"))
    return request


def _invoke(command: Sequence[str], timeout_seconds: float) -> Tuple[int, str, str]:
    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CallTimeout("provider command timed out", exc.stdout, exc.stderr) from exc
    except (FileNotFoundError, PermissionError, OSError) as exc:
        raise ConfigError("deepline CLI could not be started") from exc
    return int(completed.returncode), completed.stdout or "", completed.stderr or ""


def _catalog_output(
    operation: str,
    parsed: Any,
    tool: Optional[str] = None,
    entity_type: Optional[str] = None,
) -> Dict[str, Any]:
    records = _records(parsed)
    envelope_status = _envelope_status(parsed)
    description_fields = {
        "toolId",
        "id",
        "name",
        "displayName",
        "description",
        "inputSchema",
        "pricing",
    }
    direct_description = (
        operation == "describe"
        and isinstance(parsed, dict)
        and bool(parsed)
        and bool(description_fields.intersection(parsed))
        and not records
        and envelope_status not in _FAILURE_STATUSES
    )
    if direct_description:
        records = [parsed]
    status = envelope_status
    if not status:
        status = "ok" if records else ("no_results" if _known_envelope(parsed) else "schema_error")
    if not _known_envelope(parsed) and not direct_description:
        status = status if status in _PROVIDER_ERROR_STATUSES else "schema_error"
    body: Dict[str, Any] = {
        "status": status,
        "provider": "deepline",
        "operation": operation,
        "results": redact(records),
    }
    if status in _FAILURE_STATUSES:
        error = _envelope_error(parsed)
        if error:
            body["error"] = error
    if tool:
        body["tool"] = tool
    if entity_type:
        body["entity_type"] = entity_type
    return body


def _execute_output(
    parsed: Any,
    tool: str,
    entity_type: Optional[str] = None,
    limit: int = 10,
) -> Dict[str, Any]:
    if entity_type and entity_type.strip().casefold() == "email_validation":
        validation = _email_validation_output(parsed, tool, limit)
        if validation is not None:
            return validation
    structured = _structured_execute_envelope(parsed)
    metadata: Dict[str, Any] = _execution_metadata(parsed)
    if structured:
        kind, envelope = structured
        records = (
            _normalize_jsonapi(envelope, tool, entity_type)
            if kind == "jsonapi"
            else _normalize_harvest(envelope, tool, entity_type)
        )[:limit]
        metadata.update(_structured_metadata(kind, envelope))
    else:
        records = _records(parsed)[:limit]
    outer_status = _envelope_status(parsed)
    selected_status = _structured_status(envelope) if structured else None
    statuses = (outer_status, selected_status)
    status = next(
        (candidate for candidate in statuses if candidate in _FAILURE_STATUSES),
        "partial" if "partial" in statuses else selected_status or outer_status,
    )
    if outer_status == "no_results":
        # A canonical empty outcome need not contain a row-shaped payload.
        nested = parsed.get("toolResponse", parsed.get("tool_response", {}))
        nested_status = _envelope_status(nested)
        error = _envelope_error(parsed)
        outcome = "schema_error" if records else "no_results"
        if nested_status in _FAILURE_STATUSES or error:
            outcome = nested_status if nested_status in _FAILURE_STATUSES else _classify_error(json.dumps(error))
        body = {"status": outcome, "provider": "deepline", "operation": "execute",
                "tool": tool, "results": [], "evidence": [], **metadata}
        if error:
            body["error"] = error
        if entity_type:
            body["entity_type"] = entity_type
        return body
    if not structured and not _known_envelope(parsed):
        body = {
            "status": status if status in _PROVIDER_ERROR_STATUSES else "schema_error",
            "provider": "deepline",
            "operation": "execute",
            "tool": tool,
            "results": [],
            "evidence": [],
        }
        error = _envelope_error(parsed)
        if error:
            body["error"] = error
        if entity_type:
            body["entity_type"] = entity_type
        if entity_type and entity_type.strip().casefold() == "email_validation":
            body["provider_response"] = redact(parsed)
        body.update(metadata)
        return body
    if status in _PROVIDER_ERROR_STATUSES or status == "schema_error":
        final_status = status
    elif status == "partial":
        final_status = "partial"
    elif records:
        final_status = "ok"
    else:
        final_status = "no_results"
    evidence = records if structured else [
        normalize_evidence(record, "deepline", tool, entity_type) for record in records
    ]
    if structured and final_status in _FAILURE_STATUSES:
        evidence = []
    body = {
        "status": final_status,
        "provider": "deepline",
        "operation": "execute",
        "tool": tool,
        "results": evidence,
        "evidence": evidence,
    }
    if final_status in _FAILURE_STATUSES:
        error = _envelope_error(parsed) or (
            _envelope_error(envelope) if structured else None
        )
        if error:
            body["error"] = error
    if entity_type:
        body["entity_type"] = entity_type
    body.update(metadata)
    return body


def run(request: Dict[str, Any], capture=None) -> Tuple[Dict[str, Any], int]:
    """Run one validated request and return (JSON body, process exit code)."""

    request = _validate_request(request)
    if request["operation"] == "execute":
        return guarded_call(request, "deepline", lambda: _run_validated(request, capture))
    return _run_validated(request, capture)


def _run_validated(request: Dict[str, Any], capture=None) -> Tuple[Dict[str, Any], int]:
    operation = request["operation"]
    timeout_seconds = request["timeout_seconds"]
    deepline_bin = os.environ.get(_DEEPLINE_BIN, "").strip() or "deepline"
    if operation == "search":
        command = [deepline_bin, "tools", "search", request["query"], "--json"]
    elif operation == "describe":
        command = [deepline_bin, "tools", "describe", request["tool"], "--json"]
    else:
        payload_file = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8", delete=False) as handle:
                json.dump(request["payload"], handle, ensure_ascii=False)
                payload_file = handle.name
            command = [
                deepline_bin,
                "tools",
                "execute",
                request["tool"],
                "--input",
                "@" + payload_file,
                "--json",
            ]
            try:
                return _run_command(request, command, timeout_seconds, capture)
            finally:
                if payload_file:
                    try:
                        os.unlink(payload_file)
                    except OSError:
                        pass
        except OSError as exc:
            raise ConfigError("could not create Deepline payload file") from exc
    return _run_command(request, command, timeout_seconds, capture)


def _run_command(request: Dict[str, Any], command: Sequence[str], timeout_seconds: float, capture=None) -> Tuple[Dict[str, Any], int]:
    try:
        returncode, stdout, stderr = _invoke(command, timeout_seconds)
    except CallTimeout as exc:
        if capture is not None and (exc.stdout or exc.stderr):
            try:
                partial = _json_from_text(exc.stdout)
            except ValueError:
                partial = None
            capture({"timed_out": True, "body": response_body(partial, exc.stdout), "stderr": exc.stderr})
        body = {
            "status": "timeout",
            "provider": "deepline",
            "operation": request["operation"],
        }
        if request.get("tool"):
            body["tool"] = request["tool"]
        if request.get("entity_type"):
            body["entity_type"] = request["entity_type"]
        return body, 0
    except ConfigError:
        raise
    parsed: Any = None
    if stdout.strip():
        try:
            parsed = _json_from_text(stdout)
        except ValueError:
            parsed = None
    if capture is not None:
        capture({"exit_code": returncode, "body": response_body(parsed, stdout), "stderr": stderr})
    # Hunter's observed data-absence error is not an endpoint or transport 404.
    if (request["operation"] == "execute" and request.get("tool") == "hunter_companies_find"
            and request.get("entity_type") == "company" and isinstance(parsed, dict)
            and set(parsed) <= {"ok", "error", "billing"} and parsed.get("ok") is False
            and parsed.get("error") == {
                "message": "not_found: The domain does not exist in our database",
                "code": "UPSTREAM_NOT_FOUND", "details": {"statusCode": 404}}):
        return {
            "status": "no_results", "provider": "deepline", "operation": "execute",
            "tool": request["tool"], "entity_type": "company", "results": [], "evidence": [],
            "error": _envelope_error(parsed), **_execution_metadata(parsed),
        }, 0
    if returncode != 0:
        if (
            parsed is not None
            and str(request.get("entity_type", "")).strip().casefold()
            == "email_validation"
        ):
            validation = _email_validation_output(
                parsed, request["tool"], request["limit"], command_failed=True
            )
            if validation is not None:
                return validation, 0
        # A current CLI can print an update notice before a structured provider
        # error. Prefer that parsed error over notice/help text. Otherwise,
        # prefer stderr because stdout help examples can contain status words.
        parsed_error = _envelope_error(parsed)
        parsed_status = _envelope_status(parsed)
        diagnostic = stderr.strip() or stdout.strip()
        if parsed_error:
            status = (
                parsed_status
                if parsed_status in _FAILURE_STATUSES
                else _classify_error(parsed_error["message"])
            )
            error = parsed_error
        else:
            status = _classify_error(diagnostic)
            error = _safe_error(diagnostic)
        body = {
            "status": status,
            "provider": "deepline",
            "operation": request["operation"],
            "error": error,
            **_execution_metadata(parsed),
        }
        if status == "schema_error":
            body["error_stage"] = "provider"
        if request.get("tool"):
            body["tool"] = request["tool"]
        if request.get("entity_type"):
            body["entity_type"] = request["entity_type"]
        return body, 0
    if parsed is None:
        body = {
            "status": "schema_error",
            "error_stage": "response",
            "provider": "deepline",
            "operation": request["operation"],
        }
        if request.get("tool"):
            body["tool"] = request["tool"]
        if request.get("entity_type"):
            body["entity_type"] = request["entity_type"]
        return body, 0
    if request["operation"] == "execute":
        body = _execute_output(
            parsed,
            request["tool"],
            request.get("entity_type"),
            request["limit"],
        )
    else:
        body = _catalog_output(request["operation"], parsed, request.get("tool"), request.get("entity_type"))
    if body.get("status") == "schema_error":
        body["error_stage"] = "provider" if _envelope_status(parsed) == "schema_error" else "response"
    return body, 0


def _read_cli_input(argv: Optional[Sequence[str]] = None) -> Any:
    parser = argparse.ArgumentParser(description="Run a bounded Deepline catalog or execute operation")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--input", help="JSON request object")
    group.add_argument("--input-file", help="Path to a JSON request object")
    parser.add_argument("--output-file", help="New file for the full redacted response; never overwritten")
    args = parser.parse_args(argv)
    try:
        raw = args.input
        if args.input_file:
            with open(args.input_file, "r", encoding="utf-8") as handle:
                raw = handle.read()
        return load_json(raw), args.output_file
    except (OSError, ValueError) as exc:
        raise InputError("input must contain valid JSON") from exc


def main(argv: Optional[Sequence[str]] = None) -> int:
    receipt = None
    try:
        request, output_file = _read_cli_input(argv)
        if output_file:
            try:
                receipt = ResponseFile(output_file, redact)
            except OSError:
                raise ConfigError("response output must be a new writable file; request was not sent")
        body, code = run(request, capture=receipt.capture) if receipt else run(request)
    except InputError as exc:
        body, code = {"status": "schema_error", "error_stage": "request", "error": _safe_error(str(exc))}, 2
    except ConfigError as exc:
        body, code = {"status": "config_error", "error": _safe_error(str(exc))}, 2
    if receipt is not None and not receipt.finish(body):
        body = dict(body, receipt_error="Response file could not be finalized. Preserve this output; do not repeat a possibly billed request.")
        code = 2
    # One compact JSON object is the only stdout output.  Operational detail is
    # intentionally omitted to keep credentials from appearing in logs.
    sys.stdout.write(json.dumps(redact(body), ensure_ascii=True, separators=(",", ":")) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
