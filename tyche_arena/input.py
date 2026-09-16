"""Translate the lab ICP without changing its primary/bonus signal semantics."""

from datetime import datetime, timezone
import json
import os

from .constraints import validate_constraints


def signals_for(icp):
    """Match Arena's primary/bonus signal order and per-signal age bounds."""
    signals = icp.get("intent_signals") or [icp.get("intent_signal")]
    if not isinstance(signals, list) or not signals or any(not isinstance(v, str) or not v.strip() for v in signals):
        raise ValueError("intent_signals must be a nonempty list of text; structured signals are unsupported")
    signals = [value.strip() for value in signals]
    if len(set(signals)) != len(signals):
        raise ValueError("intent_signals must be unique to preserve Arena's signal indexes")
    age = icp.get("intent_max_age_days", 365)
    if type(age) is not int or age < 1:
        raise ValueError("intent_max_age_days must be positive")
    bonuses = icp.get("bonus_intents") or []
    if not isinstance(bonuses, list):
        raise ValueError("bonus_intents must be a list")
    ages = {}
    for bonus in bonuses:
        if not isinstance(bonus, dict):
            raise ValueError("bonus_intents must contain signal objects")
        signal = bonus.get("intent_signal") or bonus.get("signal") or bonus.get("text")
        if not isinstance(signal, str) or not signal.strip():
            raise ValueError("bonus_intents needs intent_signal, signal or text")
        signal = signal.strip()
        days = bonus.get("max_age_days")
        if days is None:
            days = bonus.get("intent_max_age_days", age)
        if type(days) is not int or days < 1:
            raise ValueError("bonus intent max_age_days must be positive")
        ages[signal] = days
        if signal not in signals:
            signals.append(signal)
    return [{"kind": f"arena_signal_{index}", "query": signal,
             "importance": "required" if index == 0 else "preferred", "max_age_days": ages.get(signal, age)}
            for index, signal in enumerate(signals)]


def request_for(icp, limit, duration):
    if not isinstance(icp, dict):
        raise ValueError("ICP must be an object")
    if icp.get("intent_details_policy") != "intent_details_v1" or icp.get("contact_policy") != "contacts_v1":
        raise ValueError("This bundle supports intent_details_v1 + contacts_v1 rounds")
    validate_constraints(icp)
    if icp.get("required_attribute") is not None and not isinstance(icp["required_attribute"], str):
        raise ValueError("required_attribute must be text; structured attributes are unsupported")
    roles = icp.get("target_roles")
    if not isinstance(roles, list) or not roles or any(not isinstance(v, str) or not v.strip() for v in roles):
        raise ValueError("target_roles must be a nonempty list of text")
    signals = signals_for(icp)
    criteria = {}
    exclusions = icp.get("excluded_companies") or []
    if not isinstance(exclusions, list) or any(not isinstance(v, str) or not v.strip() for v in exclusions):
        raise ValueError("excluded_companies must be a list of text")
    if exclusions:
        criteria["exclusions"] = exclusions
    for source, target in (("industry", "industries"), ("geography", "geographies")):
        if icp.get(source):
            criteria[target] = [icp[source]]
    attributes = [icp["required_attribute"]] if icp.get("required_attribute") else []
    for key in ("sub_industry", "company_stage", "country", "state"):
        if icp.get(key):
            attributes.append(key + ": " + str(icp[key]))
    if icp.get("employee_count"):
        attributes.append("Employee range is one of: " + json.dumps(icp["employee_count"]))
    if attributes:
        criteria["required_attributes"] = attributes
    criteria.setdefault("custom_criteria", [icp.get("prompt") or "Match the supplied company criteria"])
    request = {"target_count": limit, "icp": criteria, "requested_roles": roles,
        "buying_signals": signals,
        "signal_match_mode": "all", "time_window": {"max_age_days": icp.get("intent_max_age_days", 365)},
        "contact_fields": ["email"], "max_duration_seconds": duration,
        "original_text": json.dumps(icp, ensure_ascii=True, allow_nan=False),
        "as_of_date": os.environ.get("LAB_ARENA_EVALUATION_DATE") or datetime.now(timezone.utc).date().isoformat()}
    if icp.get("product_service"):
        request["product_service"] = {"description": icp["product_service"], "perspective": "target"}
    return request
