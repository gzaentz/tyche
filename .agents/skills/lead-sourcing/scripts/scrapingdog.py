#!/usr/bin/env python3
"""Minimal, bounded ScrapingDog HTTP adapter for lead-sourcing evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http.client import IncompleteRead
from html.parser import HTMLParser
import json
import math
import os
import re
import socket
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from provider_output import ResponseFile, load_json, response_body


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
OPERATIONS = {
    "google_search",
    "universal_search",
    "scrape",
    "linkedin_company",
    "linkedin_person",
    "linkedin_job",
    "google_jobs",
    "linkedin_jobs",
    "google_maps",
    "google_maps_place",
    "google_local",
    "google_ai_mode",
    "google_news",
    "linkedin_post",
    "x_profile",
    "x_post",
    "youtube_search",
    "youtube_video",
    "youtube_transcript",
    "google_ads_transparency",
    "google_patents",
    "google_patent_details",
    "tiktok_profile",
    "tiktok_post",
    "tiktok_ads",
}
OPERATION_ALIASES = {
    "linkedin_profile": "linkedin_person",
    "linkedin_person_profile": "linkedin_person",
    "linkedin_job_details": "linkedin_job",
    "linkedin_job_overview": "linkedin_job",
    "google_maps_search": "google_maps",
    "google_maps_lookup": "google_maps",
    "google_place": "google_maps_place",
    "google_places": "google_maps_place",
    "google_maps_places": "google_maps_place",
    "youtube_transcripts": "youtube_transcript",
}
API_HOST = "https://api.scrapingdog.com"
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
_DATE_HINT = re.compile(
    r"(?i)(?:\b(?:today|yesterday)\b|\b\d+\+?\s+(?:minute|hour|day|week|month|year)s?\s+ago\b|"
    r"\b\d{4}-\d{2}-\d{2}\b|\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
    r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?)\s+\d{1,2},?\s+\d{4}\b)"
)
_MAX_EVIDENCE_TEXT_CHARS = 1200
_TRUNCATION_MARKER = " ... [truncated] ... "


class InputError(ValueError):
    pass


class ConfigError(RuntimeError):
    pass


class ProviderResponseError(RuntimeError):
    def __init__(self, status: str, message: str = "provider request failed", response=None) -> None:
        super().__init__(message)
        self.status = status
        self.response = response


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if _SECRET_KEY.search(str(key)) else redact(item)
            for key, item in value.items()
        }
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


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, dict):
        for key in ("name", "value", "text", "title", "url"):
            if value.get(key) not in (None, ""):
                value = value[key]
                break
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _date_text(value: Any) -> Optional[str]:
    """Extract common date/freshness fields without stringifying containers."""

    if isinstance(value, dict):
        value = _first(
            value,
            "date",
            "posted_at",
            "posted",
            "published_at",
            "published",
            "value",
            "text",
        )
    elif isinstance(value, list):
        for item in value:
            result = _date_text(item)
            if result and _DATE_HINT.search(result):
                return result
        return None
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if math.isfinite(number) and abs(number) >= 100_000_000:
            if abs(number) >= 100_000_000_000:
                number /= 1000
            try:
                return datetime.fromtimestamp(number, timezone.utc).date().isoformat()
            except (OverflowError, OSError, ValueError):
                pass
    return _text(value)


def _date_hint_from_text(value: Any) -> Optional[str]:
    text = _text(value)
    if not text:
        return None
    match = _DATE_HINT.search(text)
    return match.group(0) if match else None


def _bounded_text(value: Any, maximum: int = _MAX_EVIDENCE_TEXT_CHARS) -> Optional[str]:
    text = _text(value)
    if not text:
        return None
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= maximum:
        return text
    remaining = maximum - len(_TRUNCATION_MARKER)
    head = int(remaining * 0.75)
    tail = remaining - head
    return text[:head].rstrip() + _TRUNCATION_MARKER + text[-tail:].lstrip()


class _VisibleTextParser(HTMLParser):
    """Collect user-visible HTML text while ignoring page chrome payloads."""

    _SKIP_TAGS = {"head", "script", "style", "noscript", "svg"}
    _BREAK_TAGS = {
        "address",
        "article",
        "aside",
        "br",
        "div",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "li",
        "main",
        "nav",
        "p",
        "section",
        "table",
        "td",
        "th",
        "tr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        lowered = tag.lower()
        if lowered in self._SKIP_TAGS:
            self.skip_depth += 1
        elif self.skip_depth == 0 and lowered in self._BREAK_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in self._SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1
        elif self.skip_depth == 0 and lowered in self._BREAK_TAGS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self.skip_depth == 0 and data.strip():
            self.parts.append(data)


def _visible_html_text(value: Any) -> Optional[str]:
    text = _text(value)
    if not text:
        return None
    if "<" not in text or ">" not in text:
        return _bounded_text(text)
    parser = _VisibleTextParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        return _bounded_text(re.sub(r"<[^>]+>", " ", text))
    visible = " ".join(parser.parts)
    return _bounded_text(visible) or _bounded_text(re.sub(r"<[^>]+>", " ", text))


def _domain(value: Any) -> Optional[str]:
    text = _text(value)
    if not text:
        return None
    candidate = text if "://" in text else "https://" + text
    host = urlparse(candidate).hostname
    if not host:
        return text.lower().strip("/")
    host = host.lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def _is_linkedin_url(value: Any) -> bool:
    text = _text(value)
    if not text:
        return False
    candidate = text if "://" in text else "https://" + text
    host = urlparse(candidate).hostname
    return bool(host) and (host.lower() == "linkedin.com" or host.lower().endswith(".linkedin.com"))


def _first(row: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _link(value: Any) -> Any:
    if isinstance(value, list):
        for item in value:
            link = _link(item)
            if link:
                return link
        return None
    if isinstance(value, dict):
        return _first(
            value,
            "link",
            "url",
            "href",
            "canonical_url",
            "video_url",
            "play_url",
            "download_url",
        )
    return value


def _param_value(value: Any) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def _copy_optional(params: Dict[str, Any], request: Dict[str, Any], keys: Sequence[str]) -> None:
    """Copy provider options while retaining explicit false/zero values."""

    for key in keys:
        if request.get(key) not in (None, ""):
            params[key] = _param_value(request[key])


def _operation_kind(operation: str) -> str:
    return OPERATION_ALIASES.get(operation, operation)


def _contact_name(source: Dict[str, Any]) -> Optional[str]:
    contact = _first(
        source,
        "contact",
        "contact_name",
        "person",
        "person_name",
        "full_name",
        "fullName",
    )
    if contact in (None, ""):
        first = _text(_first(source, "first_name", "firstName"))
        last = _text(_first(source, "last_name", "lastName"))
        contact = " ".join(part for part in (first, last) if part) or None
    return _text(contact)


def _contact_url(source: Dict[str, Any]) -> Optional[str]:
    return _text(
        _link(
            _first(
                source,
                "contact_url",
                "person_url",
                "profile_url",
                "linkedin_url",
                "profile_link",
                "url",
            )
        )
    )


def _is_current_marker(value: Any) -> bool:
    if value is True:
        return True
    text = _text(value)
    if not text:
        return False
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    return normalized in {"present", "current", "now", "ongoing", "to date"}


def _current_experience(value: Any) -> Dict[str, Any]:
    """Return the first current LinkedIn experience, never a former role."""

    if not isinstance(value, list):
        return {}
    for item in value:
        if not isinstance(item, dict):
            continue
        current_flag = _first(item, "is_current", "isCurrent", "current", "currently")
        end_value = _first(
            item,
            "ends_at",
            "end_date",
            "endDate",
            "endsAt",
            "end",
            "to",
        )
        if (
            current_flag is True
            or _is_current_marker(current_flag)
            or end_value in (None, "")
            or _is_current_marker(end_value)
        ):
            return item
    return {}


def _add_contact_fields(
    result: Dict[str, Any],
    source: Dict[str, Any],
    title_override: Optional[str] = None,
) -> None:
    contact = _contact_name(source)
    contact_url = _contact_url(source)
    contact_title = _text(
        _first(
            source,
            "contact_title",
            "person_title",
            "job_title",
            "headline",
            "role",
            "position",
            "current_title",
            "currentTitle",
            "jobTitle",
        )
    )
    if title_override:
        contact_title = title_override
    contact_email = _text(
        _first(source, "contact_email", "person_email", "email", "email_address")
    )
    if any(value is not None for value in (contact, contact_url, contact_title, contact_email)):
        result["contact"] = contact
        result["contact_name"] = contact
        result["full_name"] = contact
        result["contact_url"] = contact_url
        result["contact_title"] = contact_title
        result["current_title"] = contact_title
        result["contact_email"] = contact_email


def normalize_result(row: Any, operation: str, index: int = 0) -> Dict[str, Any]:
    """Return a compact evidence row with stable fields for all operations."""

    source = row if isinstance(row, dict) else {"value": row}
    operation_kind = _operation_kind(operation)
    domain_value = _first(
        source,
        "domain",
        "company_domain",
        "company_url",
        "website",
        "link",
        "url",
    )
    person_contact_source = source
    person_current_title: Optional[str] = None
    person_company_url = source.get("company_url")
    if operation_kind == "linkedin_person":
        signal = "person_profile"
        # ScrapingDog's profile response uses company_url for the LinkedIn
        # company profile. It is not the company's canonical web domain.
        domain_value = _first(source, "domain", "company_domain")
        if _is_linkedin_url(domain_value):
            domain_value = None
        current_experience = _current_experience(source.get("experience"))
        company = _first(
            current_experience,
            "company",
            "company_name",
            "current_company",
            "organization",
            "employer",
        )
        if company in (None, ""):
            company = _first(
                source,
                "company",
                "company_name",
                "current_company",
                "organization",
                "employer",
            )
        person_current_title = _text(
            _first(
                current_experience,
                "position",
                "job_position",
                "job_title",
                "title",
                "role",
            )
        )
        if not person_current_title:
            person_current_title = _text(
                _first(
                    source,
                    "contact_title",
                    "person_title",
                    "job_title",
                    "headline",
                    "role",
                    "position",
                    "current_title",
                    "currentTitle",
                )
            )
        experience_domain = _first(
            current_experience,
            "domain",
            "company_domain",
        )
        if experience_domain not in (None, ""):
            domain_value = experience_domain
        elif not _is_linkedin_url(current_experience.get("website")):
            experience_website = current_experience.get("website")
            if experience_website not in (None, ""):
                domain_value = experience_website
        if current_experience.get("company_url") not in (None, ""):
            person_company_url = current_experience.get("company_url")
        if domain_value in (None, "") and not _is_linkedin_url(source.get("website")):
            source_website = source.get("website")
            if source_website not in (None, ""):
                domain_value = source_website
        person_contact_source = dict(source)
        if person_current_title:
            person_contact_source["contact_title"] = person_current_title
        evidence_url = _link(
            _first(
                source,
                "evidence_url",
                "linkedin_url",
                "profile_url",
                "profile_link",
                "url",
            )
        )
        text = _first(source, "about", "summary", "headline", "description", "text")
        evidence_date = _first(source, "evidence_date", "updated_at", "date")
    elif operation_kind == "linkedin_job":
        signal = "hiring"
        company = _first(source, "company", "company_name", "employer", "companyName")
        evidence_url = _link(
            _first(
                source,
                "evidence_url",
                "job_url",
                "job_link",
                "share_link",
                "job_apply_link",
                "linkedin_url",
                "link",
                "url",
            )
        )
        text = _first(
            source,
            "description",
            "job_description",
            "snippet",
            "title",
            "job_title",
            "job_position",
            "text",
        )
        evidence_date = _first(
            source,
            "evidence_date",
            "date",
            "job_posting_time",
            "posted_at",
            "posted_date",
            "date_posted",
            "posted",
            "published_at",
            "published_date",
        )
    elif operation_kind in {"google_maps", "google_maps_place"}:
        signal = "place_profile" if operation_kind == "google_maps_place" else "maps_result"
        company = _first(
            source,
            "company",
            "company_name",
            "business_name",
            "title",
            "name",
        )
        # A place payload normally has no source URL. A provider-supplied
        # website is useful evidence when present; place IDs are metadata, not
        # a fabricated URL.
        evidence_url = _link(
            _first(source, "evidence_url", "source_url", "website", "link", "url")
        )
        text = _first(source, "description", "address", "type", "types", "text")
        evidence_date = _first(source, "evidence_date", "date", "updated_at")
    elif operation_kind == "google_local":
        signal = "local_result"
        company = _first(source, "company", "company_name", "business_name", "title", "name")
        evidence_url = _link(_first(source, "evidence_url", "source_url", "website", "link", "url"))
        text = _first(source, "description", "address", "type", "types", "snippet", "text")
        evidence_date = _first(source, "evidence_date", "date", "updated_at")
    elif operation_kind == "google_ai_mode":
        signal = "ai_mode_result"
        company = _first(source, "company", "company_name", "organization", "advertiser_name")
        evidence_url = _link(
            _first(source, "evidence_url", "source_url", "reference_url", "references", "sources", "links", "link", "url")
        )
        text = _first(source, "answer", "answer_text", "response", "text", "snippet", "description", "title")
        evidence_date = _first(source, "evidence_date", "date", "published_at", "updated_at")
    elif operation_kind == "google_news":
        signal = "news_result"
        company = _first(source, "company", "company_name", "organization")
        evidence_url = _link(_first(source, "evidence_url", "source_url", "link", "url"))
        text = _first(source, "title", "headline", "snippet", "description", "text")
        evidence_date = _first(
            source,
            "evidence_date",
            "published_at",
            "published_date",
            "date",
            "datetime",
            "lastUpdated",
        )
    elif operation_kind == "linkedin_post":
        signal = "linkedin_post"
        domain_value = _first(source, "domain", "company_domain", "website")
        company = _first(source, "company", "company_name", "organization")
        evidence_url = _link(_first(source, "evidence_url", "post_url", "source_url", "canonical_url", "link", "url"))
        text = _first(source, "text", "content", "post_text", "description", "title")
        evidence_date = _first(source, "evidence_date", "published_at", "created_at", "date")
    elif operation_kind == "x_profile":
        signal = "x_profile"
        domain_value = _first(source, "domain", "company_domain", "website")
        company = _first(source, "company", "company_name", "organization")
        evidence_url = _link(_first(source, "evidence_url", "profile_url", "source_url", "link", "url"))
        text = _first(source, "bio", "description", "headline", "text", "name")
        evidence_date = _first(source, "evidence_date", "updated_at", "date")
    elif operation_kind == "x_post":
        signal = "x_post"
        domain_value = _first(source, "domain", "company_domain", "website")
        company = _first(source, "company", "company_name", "organization")
        evidence_url = _link(_first(source, "evidence_url", "post_url", "source_url", "link", "url"))
        text = _first(source, "text", "content", "post_text", "description", "title")
        evidence_date = _first(source, "evidence_date", "published_at", "created_at", "date")
    elif operation_kind == "youtube_search":
        signal = "youtube_search"
        domain_value = _first(source, "domain", "company_domain", "website")
        company = _first(source, "company", "company_name", "channel_name", "channel", "organization")
        evidence_url = _link(_first(source, "evidence_url", "video_url", "source_url", "link", "url"))
        text = _first(source, "title", "video_title", "snippet", "description", "text")
        evidence_date = _first(source, "evidence_date", "published_at", "published_date", "date")
    elif operation_kind == "youtube_video":
        signal = "youtube_video"
        domain_value = _first(source, "domain", "company_domain", "website")
        company = _first(source, "company", "company_name", "channel_name", "channel", "organization")
        evidence_url = _link(_first(source, "evidence_url", "video_url", "source_url", "link", "url"))
        text = _first(source, "title", "video_title", "description", "text", "content")
        evidence_date = _first(source, "evidence_date", "published_at", "published_date", "published_time", "date")
    elif operation_kind == "youtube_transcript":
        signal = "youtube_transcript"
        domain_value = _first(source, "domain", "company_domain", "website")
        company = _first(source, "company", "company_name", "channel_name", "channel", "organization")
        evidence_url = _link(_first(source, "evidence_url", "video_url", "source_url", "link", "url"))
        text = _first(source, "transcript", "text", "content", "transcription")
        evidence_date = _first(source, "evidence_date", "published_at", "date")
    elif operation_kind == "google_ads_transparency":
        signal = "ads_transparency"
        company = _first(source, "company", "company_name", "advertiser_name", "advertiser", "organization")
        evidence_url = _link(_first(source, "evidence_url", "creative_url", "source_url", "link", "url"))
        text = _first(
            source,
            "description",
            "ad_text",
            "title",
            "text",
            "snippet",
            "advertiser",
            "advertiser_name",
            "format",
        )
        evidence_date = _first(
            source,
            "evidence_date",
            "last_shown",
            "first_shown",
            "end_date",
            "start_date",
            "date",
            "created_at",
        )
    elif operation_kind in {"google_patents", "google_patent_details"}:
        signal = "patent_detail" if operation_kind == "google_patent_details" else "patent_result"
        company = _first(
            source,
            "company",
            "company_name",
            "assignee",
            "assignees",
            "assignee_name",
            "organization",
        )
        if isinstance(company, list):
            company = company[0] if company else None
        evidence_url = _link(
            _first(source, "evidence_url", "patent_url", "publication_url", "pdf", "source_url", "link", "url")
        )
        text = _first(source, "title", "patent_title", "abstract", "snippet", "description", "text")
        evidence_date = _first(
            source,
            "evidence_date",
            "publication_date",
            "grant_date",
            "filing_date",
            "date",
        )
    elif operation_kind == "tiktok_profile":
        signal = "tiktok_profile"
        domain_value = _first(source, "domain", "company_domain", "website")
        company = _first(source, "company", "company_name", "organization")
        evidence_url = _link(_first(source, "evidence_url", "profile_url", "source_url", "link", "url"))
        text = _first(source, "bio", "description", "headline", "text", "name")
        evidence_date = _first(source, "evidence_date", "updated_at", "date")
    elif operation_kind == "tiktok_post":
        signal = "tiktok_post"
        domain_value = _first(source, "domain", "company_domain", "website")
        company = _first(source, "company", "company_name", "organization")
        evidence_url = _link(_first(source, "evidence_url", "post_url", "source_url", "canonical_url", "link", "url"))
        text = _first(source, "text", "content", "description", "title")
        evidence_date = _first(source, "evidence_date", "published_at", "created_at", "date")
    elif operation_kind == "tiktok_ads":
        signal = "tiktok_ads"
        company = _first(source, "company", "company_name", "advertiser_name", "advertiser", "organization")
        evidence_url = _link(
            _first(
                source,
                "evidence_url",
                "creative_url",
                "source_url",
                "link",
                "url",
                "videos",
                "image_urls",
            )
        )
        text = _first(
            source,
            "description",
            "ad_text",
            "title",
            "text",
            "snippet",
            "name",
        )
        evidence_date = _first(
            source,
            "evidence_date",
            "last_shown_date",
            "first_shown_date",
            "end_date",
            "start_date",
            "date",
            "created_at",
        )
    elif operation_kind in {"google_jobs", "linkedin_jobs"}:
        signal = "hiring"
        company = _first(source, "company", "company_name", "employer", "companyName")
        evidence_url = _link(_first(source, "apply_link", "apply_url", "apply_links", "job_apply_link", "job_url", "job_link", "share_link", "link", "url"))
        text = _first(source, "description", "job_description", "snippet", "title", "job_title", "job_position")
        evidence_date = _first(
            source,
            "evidence_date",
            "date",
            "job_posting_time",
            "posted_at",
            "posted_date",
            "date_posted",
            "posted",
            "published_at",
            "published_date",
            "detected_extensions",
            "extensions",
        )
    elif operation == "linkedin_company":
        signal = "company_profile"
        domain_value = _first(source, "domain", "company_domain")
        if _is_linkedin_url(domain_value):
            domain_value = None
        if domain_value in (None, ""):
            for key in ("website", "company_url"):
                candidate = source.get(key)
                if candidate not in (None, "") and not _is_linkedin_url(candidate):
                    domain_value = candidate
                    break
        company = _first(source, "name", "company", "company_name", "title")
        evidence_url = _link(
            _first(source, "url", "linkedin_url", "company_link", "website", "link")
        )
        text = _first(source, "description", "about", "tagline", "headline", "industry")
        evidence_date = _first(source, "updated_at", "date")
    elif operation == "scrape":
        signal = "web_page"
        company = _first(source, "company", "company_name")
        evidence_url = _link(_first(source, "evidence_url", "target_url", "url", "source_url"))
        text = _first(source, "text", "content", "html", "body", "data")
        evidence_date = _first(source, "date", "published_at")
    else:
        signal = "search_result"
        company = _first(source, "company", "company_name", "organization")
        evidence_url = _link(_first(source, "link", "url", "displayed_link", "source_url"))
        text = _first(source, "snippet", "description", "title", "text")
        evidence_date = _first(source, "date", "published_at")

    provider_metadata = {
        "rank": source.get("rank", index + 1),
        "job_id": source.get("job_id") or source.get("jobId"),
        "linkedin_id": (
            source.get("id") or source.get("company_id")
            if operation_kind in {"linkedin_company", "linkedin_person"}
            else None
        ),
        "profile_id": source.get("profile_id") or source.get("public_identifier"),
        "profileId": source.get("profileId"),
        "tweet_id": source.get("tweet_id") or source.get("tweetId"),
        "video_id": source.get("video_id")
        or source.get("videoId")
        or source.get("v")
        or (source.get("id") if operation_kind in {"youtube_video", "youtube_search"} else None),
        "patent_id": source.get("patent_id") or source.get("publication_number"),
        "advertiser_id": source.get("advertiser_id"),
        "ad_id": source.get("ad_creative_id")
        or source.get("ad_id")
        or (source.get("id") if operation_kind in {"google_ads_transparency", "tiktok_ads"} else None),
        "ad_format": source.get("format")
        or (source.get("type") if operation_kind in {"google_ads_transparency", "tiktok_ads"} else None),
        "first_shown": source.get("first_shown") or source.get("first_shown_date"),
        "last_shown": source.get("last_shown") or source.get("last_shown_date"),
        "estimated_audience": source.get("estimated_audience"),
        "spend": source.get("spent"),
        "impressions": source.get("impression"),
        "username": source.get("username"),
        "post_id": source.get("post_id")
        or source.get("postId")
        or (source.get("id") if operation_kind in {"linkedin_post", "tiktok_post"} else None),
        "company_url": person_company_url,
        "place_id": source.get("place_id") or source.get("placeId"),
        "data_id": source.get("data_id") or source.get("dataId"),
        "ludocid": source.get("ludocid"),
        "title": _text(_first(source, "title", "job_title", "job_position")),
        "location": _text(_first(source, "location", "job_location")),
        "industry": _text(source.get("industry")),
        "company_size": _text(
            _first(source, "company_size", "company_size_on_linkedin", "employee_count")
        ),
        "address": _text(source.get("address")),
        "phone": _text(source.get("phone")),
        "rating": source.get("rating"),
        "reviews": source.get("reviews"),
        "gps_coordinates": source.get("gps_coordinates"),
    }
    provider_metadata = {
        key: value for key, value in provider_metadata.items() if value not in (None, "")
    }
    raw_text = _text(text)
    normalized_text = (
        _visible_html_text(raw_text)
        if operation_kind == "scrape"
        else _bounded_text(raw_text)
    )
    normalized_date = _date_text(evidence_date) or _date_hint_from_text(raw_text)
    result: Dict[str, Any] = {
        "company": _text(company),
        "domain": None if _is_linkedin_url(domain_value) else _domain(domain_value),
        "signal": signal,
        "evidence_url": _text(evidence_url),
        "evidence_date": normalized_date,
        "evidence_text": normalized_text,
        "provider": "scrapingdog",
        "operation": operation,
        "provider_metadata": provider_metadata,
    }
    if operation_kind == "linkedin_person":
        _add_contact_fields(result, person_contact_source, person_current_title)
    # Keep explicit raw/artifact references without returning a full, potentially
    # large provider response.
    for key in ("raw_artifact_refs", "artifact_ref", "artifact_refs", "raw_url"):
        if source.get(key) not in (None, "", [], {}):
            result["raw_artifact_refs"] = redact(source[key])
            break
    return redact(result)


def _timeout_seconds(value: Any, default: float = 30.0) -> float:
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise InputError("timeout_seconds must be a number") from exc
    if not math.isfinite(number) or number <= 0:
        raise InputError("timeout_seconds must be greater than zero")
    return min(number, 60.0)


def _limit(value: Any, default: int = 10) -> int:
    if value is None:
        return default
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise InputError("limit must be an integer") from exc
    if result <= 0:
        raise InputError("limit must be greater than zero")
    return min(result, 20)


def _bounded_limit(value: Any, wrapper_limit: int) -> int:
    """Bound a provider result count by both provider and wrapper limits."""

    bound = _limit(wrapper_limit)
    return min(_limit(value, bound), bound)


def _linkedin_id(request: Dict[str, Any]) -> Optional[str]:
    value = request.get("id", request.get("company_id"))
    if isinstance(value, str) and value.strip():
        return value.strip().strip("/").split("/")[-1]
    url = request.get("url", request.get("company_url"))
    if isinstance(url, str):
        match = re.search(r"linkedin\.com/company/([^/?#]+)", url, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _profile_id(request: Dict[str, Any]) -> Optional[str]:
    value = request.get("id", request.get("profile_id", request.get("public_identifier")))
    if isinstance(value, str) and value.strip():
        return value.strip().strip("/").split("/")[-1]
    for key in ("url", "profile_url", "person_url", "linkedin_url"):
        url = request.get(key)
        if not isinstance(url, str):
            continue
        match = re.search(r"linkedin\.com/in/([^/?#]+)", url, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _job_id(request: Dict[str, Any]) -> Optional[str]:
    value = request.get("job_id", request.get("id"))
    if isinstance(value, str) and value.strip():
        return value.strip().strip("/").split("/")[-1]
    for key in ("url", "job_url", "job_link", "linkedin_url"):
        url = request.get(key)
        if not isinstance(url, str):
            continue
        match = re.search(r"linkedin\.com/jobs/view/([^/?#]+)", url, flags=re.IGNORECASE)
        if match:
            return match.group(1)
        query_match = re.search(r"(?:[?&])currentJobId=([^&#]+)", url, flags=re.IGNORECASE)
        if query_match:
            return query_match.group(1)
    return None


def _place_identifier(request: Dict[str, Any]) -> Optional[str]:
    for key in ("data_id", "place_id", "ludocid"):
        value = request.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _string_identifier(request: Dict[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        value = request.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _youtube_video_id(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    if "://" not in candidate:
        return candidate
    parsed = urlparse(candidate)
    host = (parsed.hostname or "").lower()
    if host == "youtu.be":
        return parsed.path.strip("/").split("/", 1)[0] or None
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        query = parsed.query
        match = re.search(r"(?:^|&)v=([^&]+)", query)
        if match:
            return match.group(1)
        for prefix in ("/shorts/", "/embed/", "/live/"):
            if parsed.path.startswith(prefix):
                return parsed.path[len(prefix) :].split("/", 1)[0] or None
    return None


def _looks_like_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def validate_request(request: Any) -> Dict[str, Any]:
    if not isinstance(request, dict):
        raise InputError("input must be a JSON object")
    operation = request.get("operation", request.get("op"))
    if not isinstance(operation, str):
        raise InputError("operation must be one of the supported ScrapingDog operations")
    operation = operation.strip().lower()
    operation_kind = _operation_kind(operation)
    if operation_kind not in OPERATIONS:
        raise InputError("operation must be one of the supported ScrapingDog operations")
    result = dict(request)
    result["operation"] = operation
    result["operation_kind"] = operation_kind
    if "api_key" in result:
        raise InputError("api_key input is not supported; use the environment")
    api_key = os.environ.get("SCRAPINGDOG_API_KEY")
    if not isinstance(api_key, str) or not api_key.strip():
        raise ConfigError("ScrapingDog API key is not configured")
    result["api_key"] = api_key.strip()
    if operation_kind in {
        "google_search",
        "universal_search",
        "google_jobs",
        "linkedin_jobs",
        "google_maps",
        "google_local",
        "google_ai_mode",
        "google_news",
        "google_patents",
    }:
        query = result.get("query", result.get("q"))
        if operation_kind == "linkedin_jobs":
            query = result.get("field", query)
        if operation_kind == "google_maps" and not isinstance(query, str):
            query = None
        if operation_kind == "google_maps" and not query and _place_identifier(result):
            # Google Maps search is query-based. Place-specific requests use
            # the explicit google_maps_place operation below.
            raise InputError("google_maps requires a non-empty query")
        if not isinstance(query, str) or not query.strip():
            raise InputError(f"{operation} requires a non-empty query")
        result["query"] = query.strip()
        if operation_kind == "linkedin_jobs":
            result["field"] = query.strip()
        if operation_kind == "google_maps" and result.get("page") not in (None, "", 0, "0"):
            if result.get("ll") in (None, ""):
                raise InputError("google_maps pagination requires ll")
        if operation_kind == "google_ai_mode" and result.get("uule") not in (None, "") and result.get("location") not in (None, ""):
            raise InputError("google_ai_mode does not allow uule with location")
    if operation == "scrape":
        target = result.get("url", result.get("target_url"))
        if not isinstance(target, str) or urlparse(target).scheme not in {"http", "https"} or not urlparse(target).netloc:
            raise InputError("scrape requires an http(s) url")
        result["url"] = target
    if operation_kind == "linkedin_company":
        company_id = _linkedin_id(result)
        if not company_id:
            raise InputError("linkedin_company requires a company id or LinkedIn company URL")
        result["company_id"] = company_id
    if operation_kind == "linkedin_person":
        profile_id = _profile_id(result)
        if not profile_id:
            raise InputError("linkedin_profile requires a person id or LinkedIn profile URL")
        result["profile_id"] = profile_id
    if operation_kind == "linkedin_job":
        job_id = _job_id(result)
        if not job_id:
            raise InputError("linkedin_job requires a job id or LinkedIn job URL")
        result["job_id"] = job_id
    if operation_kind == "google_maps_place" and not _place_identifier(result):
        raise InputError(
            "google_maps_place requires data_id, place_id, or ludocid"
        )
    if operation_kind == "linkedin_post":
        post_id = _string_identifier(result, "id", "post_id")
        if not post_id:
            raise InputError("linkedin_post requires a post id")
        result["post_id"] = post_id
    if operation_kind == "x_profile":
        profile_id = _string_identifier(result, "profileId", "profile_id", "id")
        if not profile_id:
            raise InputError("x_profile requires profileId")
        result["profileId"] = profile_id
    if operation_kind == "x_post":
        tweet_id = _string_identifier(result, "tweetId", "tweet_id", "id")
        if not tweet_id:
            raise InputError("x_post requires tweetId")
        result["tweetId"] = tweet_id
    if operation_kind in {"youtube_search"}:
        search_query = _string_identifier(result, "search_query")
        if not search_query:
            raise InputError("youtube_search requires search_query")
        result["search_query"] = search_query
    if operation_kind in {"youtube_video", "youtube_transcript"}:
        video_value = result.get("v", result.get("video_id"))
        video_id = _youtube_video_id(video_value)
        if not video_id:
            video_id = _youtube_video_id(result.get("url"))
        if not video_id:
            raise InputError(f"{operation} requires v or a supported YouTube URL")
        result["v"] = video_id
    if operation_kind == "google_ads_transparency":
        advertiser_id = _string_identifier(result, "advertiser_id")
        text = _string_identifier(result, "text")
        if not advertiser_id and not text:
            raise InputError("google_ads_transparency requires advertiser_id or text")
        if advertiser_id:
            result["advertiser_id"] = advertiser_id
        if text:
            result["text"] = text
        platform = result.get("platform")
        if platform not in (None, ""):
            if not isinstance(platform, str) or platform.strip().upper() not in {
                "PLAY",
                "MAPS",
                "SEARCH",
                "SHOPPING",
                "YOUTUBE",
            }:
                raise InputError(
                    "google_ads_transparency platform must be PLAY, MAPS, "
                    "SEARCH, SHOPPING, or YOUTUBE"
                )
            result["platform"] = platform.strip().upper()
        political_ads = result.get("political_ads")
        if political_ads not in (None, "", False, 0, "false", "0") and result.get("region") in (None, ""):
            raise InputError("google_ads_transparency political_ads requires region")
    if operation_kind == "google_patent_details":
        patent_id = _string_identifier(result, "patent_id")
        if not patent_id:
            raise InputError("google_patent_details requires patent_id")
        result["patent_id"] = patent_id
    if operation_kind == "tiktok_profile":
        username = _string_identifier(result, "username")
        if not username:
            raise InputError("tiktok_profile requires username")
        result["username"] = username
    if operation_kind == "tiktok_post":
        post_url = _string_identifier(result, "url")
        username = _string_identifier(result, "username")
        post_id = _string_identifier(result, "post_id")
        if post_url and not _looks_like_url(post_url):
            raise InputError("tiktok_post url must be an http(s) URL")
        if not post_url and not (username and post_id):
            raise InputError("tiktok_post requires url or username and post_id")
        if username:
            result["username"] = username
        if post_id:
            result["post_id"] = post_id
    if operation_kind == "tiktok_ads":
        query = _string_identifier(result, "query")
        advertiser_id = _string_identifier(result, "advertiser_id")
        if not query and not advertiser_id:
            raise InputError("tiktok_ads requires query or advertiser_id")
        if query:
            result["query"] = query
        if advertiser_id:
            result["advertiser_id"] = advertiser_id
        query_type = result.get("query_type")
        if query_type in (None, "") and advertiser_id and not query:
            query_type = 2
        if query_type not in (None, ""):
            try:
                query_type = int(query_type)
            except (TypeError, ValueError, OverflowError) as exc:
                raise InputError("tiktok_ads query_type must be 1 or 2") from exc
            if query_type not in {1, 2}:
                raise InputError("tiktok_ads query_type must be 1 or 2")
            if query_type == 1 and not query:
                raise InputError("tiktok_ads query_type 1 requires query")
            if query_type == 2 and not advertiser_id:
                raise InputError("tiktok_ads query_type 2 requires advertiser_id")
            result["query_type"] = query_type
    if result.get("base_url") not in (None, ""):
        raise InputError("base_url override is not supported")
    result["base_url"] = API_HOST
    result["timeout_seconds"] = _timeout_seconds(result.get("timeout_seconds"))
    result["limit"] = _limit(result.get("limit"))
    return result


def _params(request: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    operation = request.get("operation_kind", _operation_kind(request["operation"]))
    params: Dict[str, Any] = {"api_key": request["api_key"]}
    if operation == "google_search":
        path = "/google"
        params["query"] = request["query"]
        for key in (
            "page",
            "country",
            "language",
            "domain",
            "advance_search",
            "mob_search",
        ):
            if request.get(key) not in (None, ""):
                params[key] = _param_value(request[key])
        params["results"] = _limit(request.get("results"), request["limit"])
    elif operation == "universal_search":
        path = "/search"
        params["query"] = request["query"]
        for key in ("country", "language"):
            if request.get(key) not in (None, ""):
                params[key] = _param_value(request[key])
    elif operation == "scrape":
        path = "/scrape"
        params["url"] = request["url"]
        for key in ("dynamic", "premium", "wait", "country"):
            if request.get(key) not in (None, ""):
                params[key] = _param_value(request[key])
    elif operation == "linkedin_company":
        path = "/profile"
        params.update({"type": "company", "id": request["company_id"]})
    elif operation == "linkedin_person":
        path = "/profile"
        params.update({"type": "profile", "id": request["profile_id"]})
        for key in ("premium", "webhook"):
            if request.get(key) not in (None, ""):
                params[key] = _param_value(request[key])
    elif operation == "linkedin_job":
        path = "/jobs"
        params["job_id"] = request["job_id"]
    elif operation == "google_jobs":
        path = "/google_jobs"
        params["query"] = request["query"]
        for key in (
            "country",
            "language",
            "uule",
            "domain",
            "next_page_token",
            "chips",
            "lrad",
            "ltype",
            "uds",
        ):
            if request.get(key) not in (None, ""):
                params[key] = _param_value(request[key])
    elif operation == "linkedin_jobs":
        path = "/jobs"
        params["field"] = request["field"]
        for key in (
            "geoid",
            "location",
            "page",
            "sort_by",
            "job_type",
            "exp_level",
            "work_type",
            "filter_by_company",
        ):
            if request.get(key) not in (None, ""):
                params[key] = _param_value(request[key])
    elif operation == "google_maps":
        path = "/google_maps"
        params["query"] = request["query"]
        _copy_optional(
            params,
            request,
            ("ll", "domain", "language", "country", "data", "place_id", "type", "page"),
        )
    elif operation == "google_maps_place":
        path = "/google_maps/places"
        _copy_optional(params, request, ("data_id", "place_id", "ludocid", "country"))
    elif operation == "google_local":
        path = "/google_local"
        params["query"] = request["query"]
        _copy_optional(
            params,
            request,
            ("location", "uule", "country", "language", "domain", "ludocid", "tbs", "page"),
        )
    elif operation == "google_ai_mode":
        path = "/google/ai_mode"
        params["query"] = request["query"]
        _copy_optional(params, request, ("country", "language", "uule", "location", "safe", "html"))
    elif operation == "google_news":
        path = "/google_news"
        params["query"] = request["query"]
        params["results"] = _bounded_limit(request.get("results"), request["limit"])
        _copy_optional(
            params,
            request,
            ("country", "page", "domain", "language", "lr", "uule", "tbs", "safe", "nfpr", "html"),
        )
    elif operation == "linkedin_post":
        path = "/profile/post"
        params["id"] = request["post_id"]
    elif operation == "x_profile":
        path = "/x/profile"
        params["profileId"] = request["profileId"]
    elif operation == "x_post":
        path = "/x/post"
        params["tweetId"] = request["tweetId"]
    elif operation == "youtube_search":
        path = "/youtube/search"
        params["search_query"] = request["search_query"]
        _copy_optional(params, request, ("country", "language", "sp"))
    elif operation == "youtube_video":
        path = "/youtube/video"
        params["v"] = request["v"]
        _copy_optional(params, request, ("country", "language"))
    elif operation == "youtube_transcript":
        path = "/youtube/transcripts"
        params["v"] = request["v"]
        _copy_optional(params, request, ("country", "language"))
    elif operation == "google_ads_transparency":
        path = "/google/ads_transparency"
        if request.get("advertiser_id") not in (None, ""):
            params["advertiser_id"] = request["advertiser_id"]
        if request.get("text") not in (None, ""):
            params["text"] = request["text"]
        _copy_optional(
            params,
            request,
            ("platform", "political_ads", "region", "start_date", "end_date", "creative_format", "next_page_token", "html"),
        )
        params["num"] = _bounded_limit(request.get("num"), request["limit"])
    elif operation == "google_patents":
        path = "/google_patents"
        params["query"] = request["query"]
        _copy_optional(
            params,
            request,
            ("page", "sort", "clustered", "dups", "patents", "scholar", "before", "after", "inventor", "assignee", "country", "language", "status", "type", "litigation"),
        )
        params["num"] = _bounded_limit(request.get("num"), request["limit"])
    elif operation == "google_patent_details":
        path = "/google_patents/details"
        params["patent_id"] = request["patent_id"]
        _copy_optional(params, request, ("language", "html"))
    elif operation == "tiktok_profile":
        path = "/tiktok/profile"
        params["username"] = request["username"]
    elif operation == "tiktok_post":
        path = "/tiktok/post"
        if request.get("url") not in (None, ""):
            params["url"] = request["url"]
        else:
            params["username"] = request["username"]
            params["post_id"] = request["post_id"]
    elif operation == "tiktok_ads":
        path = "/tiktok/ads"
        _copy_optional(params, request, ("query", "advertiser_id", "query_type", "country", "time_period", "sort_by", "next_page_token"))
    else:
        raise InputError("unsupported ScrapingDog operation")
    return path, params


def _classify_status(status_code: int, body: str = "") -> str:
    lowered = body.lower()
    if (
        status_code == 429
        or "rate limit" in lowered
        or "too many requests" in lowered
    ):
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
    if status_code in {401, 403} or any(
        marker in lowered
        for marker in ("unauthorized", "forbidden", "invalid api key", "authentication")
    ):
        return "auth_failed"
    return "provider_error"


def _response_bytes(response: Any) -> bytes:
    try:
        data = response.read(4 * 1024 * 1024 + 1)
        remaining = getattr(response, "length", None)
        if type(remaining) is int and remaining > 0 and len(data) <= 4 * 1024 * 1024:
            raise IncompleteRead(data, remaining)
    except IncompleteRead as exc:
        raise ProviderResponseError("provider_error", "incomplete provider response", response={
            "http_status": getattr(response, "status", getattr(response, "code", None)),
            "incomplete": True,
            "body": exc.partial[:4 * 1024 * 1024].decode("utf-8", errors="replace"),
        }) from exc
    if len(data) > 4 * 1024 * 1024:
        raise ProviderResponseError("provider_error", "provider response is too large")
    return data


def _http_get(url: str, timeout_seconds: float) -> Tuple[int, str, Any]:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "lead-sourcing/1.0"})
    response = None
    try:
        response = urlopen(request, timeout=timeout_seconds)
        status_code = int(getattr(response, "status", getattr(response, "code", 200)))
        raw = _response_bytes(response)
        return status_code, raw.decode("utf-8", errors="replace"), response
    except HTTPError as exc:
        try:
            raw = _response_bytes(exc)
            return exc.code, raw.decode("utf-8", errors="replace"), exc
        except (ProviderResponseError, OSError, UnicodeError) as read_error:
            raise ProviderResponseError(_classify_status(exc.code, ""),
                                        response=getattr(read_error, "response", None)) from read_error
        finally:
            exc.close()
    except (socket.timeout, TimeoutError) as exc:
        raise ProviderResponseError("timeout") from exc
    except URLError as exc:
        reason = str(getattr(exc, "reason", ""))
        if "timed out" in reason.lower() or "timeout" in reason.lower():
            raise ProviderResponseError("timeout") from exc
        raise ProviderResponseError("provider_error") from exc
    except ProviderResponseError:
        raise
    except (OSError, UnicodeError) as exc:
        raise ProviderResponseError("provider_error") from exc
    finally:
        if response is not None and hasattr(response, "close"):
            response.close()


def _json_payload(body: str) -> Any:
    try:
        return load_json(body)
    except ValueError as exc:
        raise ProviderResponseError("schema_error") from exc


_CONDITIONAL_RECORD_LIST_KEYS = {
    "google_local": ("local_results", "results", "items"),
    "google_ai_mode": (
        "text_blocks",
        "references",
        "shopping_results",
        "inline_images",
        "local_results",
        "organic_results",
        "results",
        "items",
    ),
    "google_news": ("news_results", "organic_results", "results", "items"),
    "linkedin_post": ("posts", "results", "items"),
    "x_profile": ("profiles", "results", "items"),
    "x_post": ("posts", "results", "items"),
    "youtube_search": (
        "channel_results",
        "video_results",
        "shorts_results",
        "movie_results",
        "videos",
        "results",
        "items",
    ),
    "youtube_video": ("videos", "results", "items"),
    "youtube_transcript": ("transcripts", "segments", "results", "items"),
    "google_ads_transparency": (
        "ad_creatives",
        "ads",
        "ad_results",
        "results",
        "items",
    ),
    "google_patents": ("organic_results", "patents", "results", "items"),
    "google_patent_details": ("results", "items"),
    "tiktok_profile": ("profiles", "results", "items"),
    "tiktok_post": ("posts", "results", "items"),
    "tiktok_ads": ("ads", "ad_results", "results", "items"),
}
_CONDITIONAL_RECORD_OBJECT_KEYS = {
    "google_local": ("data", "result", "response"),
    "google_ai_mode": ("answer", "response", "data", "result"),
    "google_news": ("data", "result", "response"),
    "linkedin_post": ("post", "data", "result", "response"),
    "x_profile": ("profile", "data", "result", "response"),
    "x_post": ("post", "data", "result", "response"),
    "youtube_search": ("data", "result", "response"),
    "youtube_video": ("video", "data", "result", "response"),
    "youtube_transcript": ("transcript", "data", "result", "response"),
    "google_ads_transparency": ("data", "result", "response"),
    "google_patents": ("data", "result", "response"),
    "google_patent_details": ("patent", "patent_details", "data", "result", "response"),
    "tiktok_profile": ("profile", "data", "result", "response"),
    "tiktok_post": ("post", "data", "result", "response"),
    "tiktok_ads": ("data", "result", "response"),
}
_CONDITIONAL_DIRECT_FIELDS = {
    "google_local": ("title", "name", "address", "website", "place_id"),
    "google_ai_mode": ("answer", "answer_text", "text", "summary", "text_blocks", "references", "sources"),
    "google_news": ("title", "headline", "snippet", "link", "url", "published_at"),
    # LinkedIn post responses are not fixed in the public documentation. These
    # are common identity/content fields, so unknown successful objects remain
    # schema errors instead of being silently accepted.
    "linkedin_post": ("id", "post_id", "text", "content", "post_text", "url"),
    "x_profile": ("profileId", "profile_id", "username", "name", "bio", "description", "url"),
    "x_post": ("tweetId", "tweet_id", "id", "text", "content", "post_text", "url"),
    "youtube_search": ("video_id", "videoId", "title", "snippet", "url", "link"),
    "youtube_video": ("video_id", "videoId", "v", "title", "description", "url", "link"),
    "youtube_transcript": ("transcript", "transcription", "text", "content", "segments"),
    "google_ads_transparency": ("advertiser_id", "advertiser_name", "ad_id", "title", "description", "text"),
    "google_patents": ("patent_id", "publication_number", "title", "abstract", "assignee"),
    "google_patent_details": ("patent_id", "publication_number", "title", "abstract", "description"),
    "tiktok_profile": ("username", "user", "name", "bio", "description", "url"),
    "tiktok_post": ("post_id", "id", "text", "content", "description", "url"),
    "tiktok_ads": ("advertiser_id", "advertiser_name", "ad_id", "title", "description", "text"),
}


def _conditional_records(payload: Any, operation: str) -> List[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    saw_empty_list = False
    combined_records: List[Any] = []
    combine_lists = operation in {"google_ai_mode", "youtube_search"}
    for key in _CONDITIONAL_RECORD_LIST_KEYS[operation]:
        if key not in payload:
            continue
        candidate = payload.get(key)
        if isinstance(candidate, list):
            if candidate:
                if combine_lists:
                    if operation == "youtube_search" and key == "shorts_results":
                        for item in candidate:
                            if not isinstance(item, dict):
                                continue
                            shorts = item.get("shorts")
                            if isinstance(shorts, list):
                                combined_records.extend(
                                    short for short in shorts if isinstance(short, dict)
                                )
                            elif any(
                                item.get(field) not in (None, "", [], {})
                                for field in _CONDITIONAL_DIRECT_FIELDS[operation]
                            ):
                                combined_records.append(item)
                    else:
                        combined_records.extend(candidate)
                    continue
                return candidate
            saw_empty_list = True
            continue
        if isinstance(candidate, dict):
            return [candidate] if candidate else []
    if combined_records:
        return combined_records
    for key in _CONDITIONAL_RECORD_OBJECT_KEYS[operation]:
        if key not in payload:
            continue
        candidate = payload.get(key)
        if isinstance(candidate, dict):
            if operation == "youtube_video" and isinstance(payload.get("channel"), dict):
                candidate = dict(candidate)
                candidate.setdefault("channel", payload["channel"])
            return [candidate] if candidate else []
        if isinstance(candidate, list):
            return candidate
    direct_keys = _CONDITIONAL_DIRECT_FIELDS[operation]
    if any(payload.get(key) not in (None, "", [], {}) for key in direct_keys):
        return [payload] if payload else []
    if any(key in payload for key in direct_keys):
        return []
    if saw_empty_list:
        return []
    return []


def _conditional_known_schema(payload: Any, operation: str) -> bool:
    if isinstance(payload, list):
        if operation == "linkedin_post":
            direct_keys = _CONDITIONAL_DIRECT_FIELDS[operation]
            return all(
                isinstance(item, dict) and any(key in item for key in direct_keys)
                for item in payload
            )
        return all(isinstance(item, dict) for item in payload)
    if not isinstance(payload, dict):
        return False
    for key in _CONDITIONAL_RECORD_LIST_KEYS[operation]:
        if key in payload:
            return isinstance(payload.get(key), list) and all(
                isinstance(item, dict) for item in payload[key]
            )
    for key in _CONDITIONAL_RECORD_OBJECT_KEYS[operation]:
        if key in payload and isinstance(payload.get(key), (dict, list)):
            return True
    for key in _CONDITIONAL_DIRECT_FIELDS[operation]:
        if key in payload:
            return True
    return False


def _records(payload: Any, operation: str) -> List[Any]:
    operation = _operation_kind(operation)
    if operation == "scrape":
        if isinstance(payload, str):
            return [{"url": None, "content": payload}] if payload.strip() else []
        if isinstance(payload, dict):
            content = _first(payload, "html", "body", "content", "text", "data", "result")
            return [payload] if content not in (None, "", [], {}) else []
        return []
    if operation == "linkedin_company":
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            saw_wrapper = False
            for key in ("company", "profile", "data", "result"):
                if key not in payload:
                    continue
                saw_wrapper = True
                candidate = payload.get(key)
                if isinstance(candidate, dict):
                    return [candidate] if candidate else []
                if isinstance(candidate, list):
                    return candidate
            if saw_wrapper:
                return []
            return [payload] if payload else []
    if operation == "linkedin_person":
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            saw_wrapper = False
            for key in ("profile", "person", "data", "result"):
                if key not in payload:
                    continue
                saw_wrapper = True
                candidate = payload.get(key)
                if isinstance(candidate, dict):
                    return [candidate] if candidate else []
                if isinstance(candidate, list):
                    return candidate
            if saw_wrapper:
                return []
            return [payload] if payload else []
        return []
    if operation == "linkedin_job":
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            saw_wrapper = False
            for key in ("job", "job_details", "data", "result"):
                if key not in payload:
                    continue
                saw_wrapper = True
                candidate = payload.get(key)
                if isinstance(candidate, dict):
                    return [candidate] if candidate else []
                if isinstance(candidate, list):
                    return candidate
            if saw_wrapper:
                return []
            return [payload] if payload else []
        return []
    if operation in {"google_maps", "google_maps_place"}:
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            keys = {
                "google_maps": ("search_results", "local_results", "results", "items"),
                "google_maps_place": ("place", "place_details", "data", "result"),
            }[operation]
            for key in keys:
                if key not in payload:
                    continue
                candidate = payload.get(key)
                if isinstance(candidate, dict):
                    return [candidate] if candidate else []
                if isinstance(candidate, list):
                    return candidate
            return [payload] if payload else []
        return []
    if operation in _CONDITIONAL_RECORD_LIST_KEYS:
        return _conditional_records(payload, operation)
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    keys = {
        "google_search": ("organic_results", "results", "items"),
        "universal_search": ("organic_results", "results", "items"),
        "google_jobs": ("jobs_results", "jobs", "results"),
        "linkedin_jobs": ("jobs_results", "jobs", "results"),
    }[operation]
    for key in keys:
        if isinstance(payload.get(key), list):
            return payload[key]
    for key in ("data", "result", "response"):
        if isinstance(payload.get(key), (dict, list)):
            return _records(payload[key], operation)
    return []


def _known_schema(payload: Any, operation: str) -> bool:
    """Recognize an empty result envelope separately from an unknown response."""

    operation = _operation_kind(operation)
    if operation == "scrape":
        if isinstance(payload, str):
            return True
        return isinstance(payload, dict) and any(key in payload for key in ("html", "body", "content", "text", "data", "result"))
    if operation == "linkedin_company":
        if isinstance(payload, list):
            return all(isinstance(item, dict) for item in payload)
        if isinstance(payload, dict):
            identity_fields = (
                "name",
                "company_name",
                "description",
                "about",
                "industry",
                "id",
            )
            if any(payload.get(key) not in (None, "", [], {}) for key in identity_fields):
                return True
            wrappers = [payload.get(key) for key in ("company", "profile", "data", "result") if key in payload]
            return bool(wrappers) and any(isinstance(value, (dict, list)) for value in wrappers)
        return False
    if operation == "linkedin_person":
        if isinstance(payload, list):
            return all(isinstance(item, dict) for item in payload)
        if isinstance(payload, dict):
            identity_fields = (
                "fullName",
                "full_name",
                "first_name",
                "last_name",
                "headline",
                "about",
                "public_identifier",
            )
            if any(payload.get(key) not in (None, "", [], {}) for key in identity_fields):
                return True
            wrappers = [
                payload.get(key)
                for key in ("profile", "person", "data", "result")
                if key in payload
            ]
            return bool(wrappers) and any(isinstance(value, (dict, list)) for value in wrappers)
        return False
    if operation == "linkedin_job":
        if isinstance(payload, list):
            return all(isinstance(item, dict) for item in payload)
        if isinstance(payload, dict):
            identity_fields = (
                "job_id",
                "jobId",
                "title",
                "job_title",
                "company_name",
                "description",
                "job_description",
            )
            if any(payload.get(key) not in (None, "", [], {}) for key in identity_fields):
                return True
            wrappers = [
                payload.get(key)
                for key in ("job", "job_details", "data", "result")
                if key in payload
            ]
            return bool(wrappers) and any(isinstance(value, (dict, list)) for value in wrappers)
        return False
    if operation in {"google_maps", "google_maps_place"}:
        if isinstance(payload, list):
            return all(isinstance(item, dict) for item in payload)
        if not isinstance(payload, dict):
            return False
        keys = {
            "google_maps": ("search_results", "local_results", "results", "items"),
            "google_maps_place": ("title", "name", "address", "place_id", "data_id", "place"),
        }[operation]
        if any(key in payload for key in keys):
            if operation == "google_maps_place" and any(
                payload.get(key) not in (None, "", [], {})
                for key in ("title", "name", "address", "place_id", "data_id")
            ):
                return True
            return any(isinstance(payload.get(key), (dict, list)) for key in keys)
        wrappers = [
            payload.get(key)
            for key in ("data", "result", "response", "place", "place_details")
            if key in payload
        ]
        return bool(wrappers) and any(
            isinstance(value, (dict, list)) for value in wrappers
        )
    if operation in _CONDITIONAL_RECORD_LIST_KEYS:
        return _conditional_known_schema(payload, operation)
    if isinstance(payload, list):
        return all(isinstance(item, dict) for item in payload)
    if not isinstance(payload, dict):
        return False
    keys = {
        "google_search": ("organic_results", "results", "items"),
        "universal_search": ("organic_results", "results", "items"),
        "google_jobs": ("jobs_results", "jobs", "results"),
        "linkedin_jobs": ("jobs_results", "jobs", "results"),
    }[operation]
    present = [key for key in keys if key in payload]
    if present:
        return any(isinstance(payload.get(key), list) for key in present)
    for key in ("data", "result", "response"):
        if isinstance(payload.get(key), (dict, list)) and _known_schema(payload[key], operation):
            return True
    return False


def _continuation_cursor(payload: Any) -> Optional[str]:
    """Return an opaque result cursor without treating it as an API credential."""

    if not isinstance(payload, dict):
        return None
    for key in ("next_page_token", "nextPageToken", "next_token"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:2000]
    for key in (
        "pagination",
        "scrapingdog_pagination",
        "data",
        "result",
        "response",
    ):
        cursor = _continuation_cursor(payload.get(key))
        if cursor:
            return cursor
    return None


def run(request: Dict[str, Any], capture=None) -> Tuple[Dict[str, Any], int]:
    request = validate_request(request)
    operation = request["operation"]
    operation_kind = request["operation_kind"]
    path, params = _params(request)
    url = request["base_url"] + path + "?" + urlencode(params)
    status_code = 0
    body = ""
    try:
        status_code, body, _ = _http_get(url, request["timeout_seconds"])
        if capture is not None:
            try:
                original = load_json(body)
            except ValueError:
                original = body
            capture({"http_status": status_code, "body": response_body(original, body)})
        if status_code < 200 or status_code >= 300:
            return {"status": _classify_status(status_code, body), "provider": "scrapingdog", "operation": request["operation"]}, 0
        if status_code == 202:
            # The provider accepted the request but did not return a result.
            # This adapter has no polling contract, so the outcome is unresolved.
            return {
                "status": "provider_error",
                "provider": "scrapingdog",
                "operation": operation,
                "http_status": 202,
            }, 0
        payload: Any = body if operation_kind == "scrape" and not body.lstrip().startswith(("{", "[")) else _json_payload(body)
    except ProviderResponseError as exc:
        if capture is not None and exc.response is not None:
            capture(exc.response)
        status = _classify_status(status_code, body) if exc.status == "provider_error" and body else exc.status
        output = {"status": status, "provider": "scrapingdog", "operation": operation}
        if status == "schema_error":
            output["error_stage"] = "response"
        return output, 0
    if isinstance(payload, dict) and payload.get("success") is False:
        message = json.dumps(redact(payload), ensure_ascii=False)
        return {"status": _classify_status(status_code, message), "provider": "scrapingdog", "operation": operation}, 0
    if isinstance(payload, dict) and any(
        payload.get(key) not in (None, "", [], {}) for key in ("error", "errors")
    ):
        message = json.dumps(redact(payload), ensure_ascii=False)
        return {"status": _classify_status(status_code, message), "provider": "scrapingdog", "operation": operation}, 0
    if not _known_schema(payload, operation_kind):
        return {"status": "schema_error", "error_stage": "response", "provider": "scrapingdog", "operation": operation}, 0
    records = _records(payload, operation_kind)
    status = "partial" if isinstance(payload, dict) and payload.get("partial") else ("ok" if records else "no_results")
    if operation_kind == "scrape":
        records = [dict(item, target_url=request["url"]) if isinstance(item, dict) else {"content": item, "target_url": request["url"]} for item in records]
    results = [normalize_result(item, operation_kind, index) for index, item in enumerate(records[: request["limit"]])]
    output = {
        "status": status,
        "provider": "scrapingdog",
        "operation": operation,
        "results": results,
        "result_count": len(results),
    }
    continuation_cursor = _continuation_cursor(payload)
    if continuation_cursor:
        output["continuation_cursor"] = continuation_cursor
    return output, 0


def _read_cli_input(argv: Optional[Sequence[str]] = None) -> Any:
    parser = argparse.ArgumentParser(description="Run a bounded ScrapingDog lead-sourcing operation")
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
        body, code = {"status": "schema_error", "error_stage": "request", "error": {"message": str(exc)}}, 2
    except ConfigError as exc:
        body, code = {"status": "config_error", "error": {"message": str(exc)}}, 2
    if receipt is not None and not receipt.finish(body):
        body = dict(body, receipt_error="Response file could not be finalized. Preserve this output; do not repeat a possibly billed request.")
        code = 2
    sys.stdout.write(json.dumps(redact(body), ensure_ascii=False, separators=(",", ":")) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
