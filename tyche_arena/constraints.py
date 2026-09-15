"""Small deterministic contact checks aligned with Arena's current contract."""

from functools import lru_cache
import re
import unicodedata

import geonamescache


def norm(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(c for c in value if not unicodedata.combining(c))
    return re.sub(r"[\W_]+", " ", value.casefold()).strip()


@lru_cache(maxsize=1)
def locations():
    cache = geonamescache.GeonamesCache()
    countries = {norm(row[key]): row["iso"] for row in cache.get_countries().values()
                 for key in ("name", "iso", "iso3")}
    countries.update({norm(key): value for key, value in {
        "UK": "GB", "U.K.": "GB", "Great Britain": "GB", "United States of America": "US",
        "U.S.A.": "US", "U.S.": "US", "South Korea": "KR", "North Korea": "KP", "Russia": "RU",
        "Vietnam": "VN", "Laos": "LA", "Bolivia": "BO", "Tanzania": "TZ", "Venezuela": "VE"}.items()})
    states = {norm(row[key]): norm(row["name"]) for row in cache.get_us_states().values() for key in ("name", "code")}
    states.update({"dc": "district of columbia", "district of columbia": "district of columbia"})
    return countries, states


def location(value, part, country):
    countries, states = locations()
    key = norm(value)
    if part == "country":
        return countries.get(key, "")
    if part == "region" and re.fullmatch(r"US-[A-Za-z]{2}", str(value), re.IGNORECASE):
        return states.get(norm(value[3:]), "") if country == "US" else ""
    return states.get(key, key) if part == "region" and country == "US" else key


def seniority_levels(requested):
    canonical = {"c level": "c_level", "c suite": "c_level", "executive": "c_level",
                 "vp": "vp", "vice president": "vp", "head": "head", "head of": "head",
                 "director": "director", "manager": "manager"}
    key = norm(requested)
    if not key:
        return None
    if key in {"vp above", "vp and above", "vice president above", "vice president and above"} or (
            "+" in requested and key in {"vp", "vice president"}):
        return {"vp", "c_level"}
    if key in {"director above", "director and above"} or "+" in requested and key == "director":
        return {"director", "head", "vp", "c_level"}
    if key not in canonical:
        raise ValueError("Unsupported Arena target_seniority")
    return {canonical[key]}


def validate_constraints(icp):
    seniority = icp.get("target_seniority") or ""
    if not isinstance(seniority, str):
        raise ValueError("target_seniority must be text")
    seniority_levels(seniority)
    geography = icp.get("contact_geography") or {}
    if not isinstance(geography, dict) or set(geography) - {"countries", "regions", "cities"}:
        raise ValueError("contact_geography supports countries, regions and cities")
    for key, values in geography.items():
        if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
            raise ValueError("contact_geography." + key + " must be a list of text")
        if key == "countries" and any(not location(v, "country", "") for v in values):
            raise ValueError("contact_geography contains an unrecognized country")


def check_contact(person, icp):
    country = location(person.get("country"), "country", "")
    if not country:
        raise ValueError("Arena contact country is unrecognized")
    for plural, part, field in (("countries", "country", "country"), ("regions", "region", "state"), ("cities", "city", "city")):
        allowed = (icp.get("contact_geography") or {}).get(plural) or []
        if allowed:
            actual = location(person.get(field), part, country)
            if not actual or actual not in {location(v, part, country) for v in allowed}:
                raise ValueError("Arena contact_geography mismatch: " + part)
    levels = seniority_levels(icp.get("target_seniority") or "")
    if levels:
        title = norm(person.get("current_title"))
        expansions = {"ceo": "chief executive officer", "coo": "chief operating officer", "cto": "chief technology officer",
            "cio": "chief information officer", "cfo": "chief financial officer", "cmo": "chief marketing officer",
            "cro": "chief revenue officer", "ciso": "chief information security officer", "chro": "chief human resources officer",
            "svp": "senior vice president", "evp": "executive vice president", "vp": "vice president", "dir": "director"}
        title = " ".join(expansions.get(word, word) for word in title.split())
        if re.search(r"\bchief\b.*\bofficer\b|\b(owner|founder)\b|managing partner|managing director", title) or (
                re.search(r"\bpresident\b", title) and "vice president" not in title):
            level = "c_level"
        else:
            level = next((level for marker, level in (("vice president", "vp"), ("head ", "head"),
                         ("director", "director"), ("manager", "manager")) if marker in title + " "), "other")
        if level not in levels:
            raise ValueError("Arena target_seniority mismatch")
