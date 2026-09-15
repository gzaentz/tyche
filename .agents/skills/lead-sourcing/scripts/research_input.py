"""Translate concise research inputs into the existing run/attempt/review records.

No dispatch, company qualification, strategy selection or automatic retries live
here. The research agent supplies those judgments; existing helpers enforce them.
"""

import copy
from datetime import datetime, timezone
import hashlib
import importlib
from pathlib import Path
import re
import uuid

import budget_guard


def object_fields(value, allowed, label):
    hint = f"{label} accepts only: {', '.join(sorted(allowed))}"
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object; {hint}")
    unexpected = set(value) - set(allowed)
    if unexpected:
        raise ValueError(f"{label} has unexpected fields: {', '.join(sorted(map(str, unexpected)))}; {hint}")


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def strings(value, label, *, empty=False):
    if not isinstance(value, list) or not empty and not value:
        raise ValueError(f"{label} must be a {'non-empty ' if not empty else ''}array")
    for item in value:
        text(item, label)


def normalize_request(value, run_file, *, saved=None, started_at=None):
    """Apply mechanical defaults to new inputs; never infer roles or intent."""
    allowed = {"target_count", "icp", "buying_signals", "requested_roles", "time_window",
               "budget", "contact_fields", "contacts_per_company", "contact_role_groups",
               "signal_match_mode", "run_id", "as_of_date", "max_duration_seconds", "product_service", "original_text"}
    object_fields(value, allowed, "request")
    request = copy.deepcopy(value)
    prior = saved or {}
    if "original_text" in request:
        text(request["original_text"], "original_text")
    if "product_service" in request:
        offering = request["product_service"]
        object_fields(offering, {"description", "perspective"}, "product_service")
        text(offering.get("description"), "product_service.description")
        if offering.get("perspective") not in {"seller", "target"}:
            raise ValueError("product_service.perspective must be seller or target")
    target = budget_guard.count(request.get("target_count"), "target_count")
    if not target:
        raise ValueError("target_count must be positive")
    if not isinstance(request.get("icp"), dict) or not request["icp"]:
        raise ValueError("icp must contain the user criteria")
    icp = request["icp"]
    object_fields(icp, {"company_types", "industries", "geographies", "company_size",
                       "required_attributes", "exclusions", "custom_criteria"}, "icp")
    for key, values in icp.items():
        if key == "company_size":
            object_fields(values, {"min_employees", "max_employees"}, "company_size")
            if not values:
                raise ValueError("company_size must specify a bound")
            for name, number in values.items():
                budget_guard.count(number, name)
            if values.get("min_employees", 0) > values.get("max_employees", float("inf")):
                raise ValueError("company_size bounds are reversed")
        else:
            strings(values, "icp." + key)
    if "contact_role_groups" in request:
        groups = request["contact_role_groups"]
        object_fields(groups, {"primary", "secondary"}, "contact_role_groups")
        strings(groups.get("primary"), "primary roles")
        strings(groups.get("secondary"), "secondary roles", empty=True)
        request.setdefault("requested_roles", list(dict.fromkeys(groups["primary"] + groups["secondary"])))
    strings(request.get("requested_roles"), "requested_roles")
    if not isinstance(request.get("buying_signals"), list) or not request["buying_signals"]:
        raise ValueError("buying_signals must contain the agent's interpreted signals")
    window = request.get("time_window")
    object_fields(window, {"max_age_days", "as_of_date"}, "time_window")
    if not budget_guard.count(window.get("max_age_days"), "time_window.max_age_days"):
        raise ValueError("time_window.max_age_days must be positive")
    from validate_run import _identity, signal_request_errors
    if prior and (errors := signal_request_errors(prior)):
        raise ValueError("Invalid saved request: " + "; ".join(errors))
    signal_keys = set()
    for signal in request["buying_signals"]:
        object_fields(signal, {"kind", "query", "min_age_days", "max_age_days", "source_preferences", "importance"}, "signal")
        text(signal.get("kind"), "signal.kind")
        key = _identity(signal["kind"])
        if not key or key in signal_keys:
            raise ValueError("signal kinds must be distinct, non-empty identifiers within this request")
        signal_keys.add(key)
        # New runs make this distinction explicit. Resuming an older request
        # must not rewrite its criteria or budget fingerprint.
        previous = [s for s in prior.get("buying_signals", []) if _identity(s.get("kind")) == key]
        if len(previous) == 1 and "importance" in previous[0]:
            signal.setdefault("importance", previous[0]["importance"])
        elif not prior:
            signal.setdefault("importance", "required")
        if "importance" in signal and signal["importance"] not in {"required", "preferred"}:
            raise ValueError("signal.importance must be required or preferred")
        if "query" in signal:
            text(signal["query"], "signal.query")
        if "source_preferences" in signal:
            strings(signal["source_preferences"], "source_preferences", empty=True)
        lower = budget_guard.count(signal.get("min_age_days", 0), "signal.min_age_days")
        upper = budget_guard.count(signal.get("max_age_days", window["max_age_days"]), "signal.max_age_days")
        if not upper or lower > upper:
            raise ValueError("signal age bounds are invalid")
    date = (started_at or datetime.now(timezone.utc).isoformat())[:10]
    fallback_id = Path(run_file).parent.name
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", fallback_id):
        fallback_id = "run-" + hashlib.sha256(str(Path(run_file).resolve()).encode()).hexdigest()[:16]
    defaults = {"contact_fields": ["email"], "contacts_per_company": 1,
                "signal_match_mode": "any", "run_id": fallback_id,
                "as_of_date": window.get("as_of_date", date)}
    for key, default in defaults.items():
        request.setdefault(key, copy.deepcopy(prior.get(key, default)))
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", text(request["run_id"], "run_id")):
        raise ValueError("run_id must be a safe identifier")
    for value in [request["as_of_date"], *([window["as_of_date"]] if "as_of_date" in window else [])]:
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("as_of_date must be YYYY-MM-DD")
        datetime.strptime(value, "%Y-%m-%d")
    if request["signal_match_mode"] not in {"any", "all"}:
        raise ValueError("signal_match_mode must be any or all")
    count = request["contacts_per_company"]
    if type(count) is not int or not 1 <= count <= 3:
        raise ValueError("contacts_per_company must be 1-3")
    if request.get("max_duration_seconds") is not None and not budget_guard.count(request["max_duration_seconds"], "max_duration_seconds"):
        raise ValueError("max_duration_seconds must be positive")
    return request


def start_document(run_file, setup, *, existing=None, ledger=None):
    """Produce validated initialization inputs; budget_guard owns persistence."""
    object_fields(setup, {"request", "max_usd", "scrapingdog_usd_per_credit",
                          "verification_reserve_credits", "started_at"}, "setup")
    existing, ledger = existing or {}, ledger or {}
    started = existing.get("stop_check", {}).get("started_at") or ledger.get("initial_started_at") or setup.get("started_at") or datetime.now(timezone.utc).isoformat()
    text(started, "started_at")
    parsed = datetime.fromisoformat(started.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("started_at must include a timezone")
    if setup.get("started_at", started) != started:
        raise ValueError("resume must preserve the original started_at")
    request = normalize_request(setup.get("request"), run_file,
                                saved=existing.get("request"), started_at=started)
    if existing and setup.get("request") == existing.get("request"):
        request = copy.deepcopy(existing["request"])
    cap = setup.get("max_usd", ledger.get("usd_limit", request["target_count"] * 0.5))
    options = dict(max_usd=cap,
        scrapingdog_usd_per_credit=setup.get("scrapingdog_usd_per_credit", ledger.get("usd_per_credit", {}).get("scrapingdog")),
        verification_reserve_credits=setup.get("verification_reserve_credits", ledger.get("verification_reserve_credits")))
    budget_defaults = existing.get("request", {}).get("budget", {
        "deepline_credits": float(budget_guard.amount(cap, "max_usd") / budget_guard.amount("0.10", "rate")),
        "scrapingdog_credits": 0, "hard_stop": True})
    request.setdefault("budget", copy.deepcopy(budget_defaults))
    budget = request["budget"]
    object_fields(budget, {"deepline_credits", "scrapingdog_credits", "hard_stop", "max_paid_calls",
                           "max_deepline_credits_per_next_lead"}, "request.budget")
    if budget.get("hard_stop") is not True:
        raise ValueError("request.budget.hard_stop must be true")
    if not any(provider + "_credits" in budget for provider in budget_guard.PROVIDERS):
        # A dollar cap plus hard_stop needs the same mechanical allocation as
        # an omitted budget. Explicit provider caps, including zero, still win.
        budget = request["budget"] = {**copy.deepcopy(budget_defaults), **budget}
    for key, value in budget.items():
        if key == "max_paid_calls":
            budget_guard.count(value, key)
        elif key != "hard_stop" and type(value) not in (int, float):
            raise ValueError(f"request.budget.{key} must be a number")
    for provider in budget_guard.PROVIDERS:
        if not existing:
            budget.setdefault(provider + "_credits", 0)
        budget_guard.amount(budget.get(provider + "_credits", 0), provider)
    from validate_run import accepted_errors
    errors = accepted_errors({"request": request, "accepted": []})
    if errors:
        raise ValueError("; ".join(errors))
    if existing and request != existing["request"]:
        raise ValueError("request differs from saved run; resume the authoritative criteria")
    limits = {provider + "_credits": 0 for provider in budget_guard.PROVIDERS}
    limits.update({k: v for k, v in budget.items() if k != "hard_stop"})
    document = dict(schema_version="1.2", run_id=request.get("run_id", existing.get("run_id")), retrieved_at=started,
        request=request, budget={"limits": limits,
                                "spent": {"deepline_credits": 0, "scrapingdog_credits": 0}, "paid_calls": 0, "status": "within_budget"},
        routes=[], accepted=[], rejected=[], unresolved=[], summary={},
        stop_check={"started_at": started, "next_actions": []}, stop_audit={"route_frontier": []})
    return document, options


def normalize_provider_request(provider, request, label):
    """Use the existing adapter contract for both research and legacy inputs."""
    if provider not in ("deepline", "scrapingdog", "public_web"):
        raise ValueError(f"{label}.provider must use an existing provider wrapper: deepline, scrapingdog, public_web")
    if not isinstance(request, dict):
        raise ValueError(f"{label}.request must contain the provider operation and inputs")
    request = copy.deepcopy(request)
    adapter = None if provider == "public_web" else importlib.import_module(provider)
    if adapter:
        try:
            request = adapter._validate_request(request) if provider == "deepline" else adapter.validate_request(request)
        except ValueError as exc:
            raise ValueError(f"{label}.request: {exc}") from exc
    return adapter, request


def prepare_lookup(value, label="lookup"):
    """The agent selects target, purpose and request; derive bookkeeping only."""
    object_fields(value, {"provider", "request", "scope", "phase", "purpose", "approach",
                          "max_cost_credits", "status_read", "pricing_basis", "contact_ref"}, label)
    provider = value.get("provider", "deepline")
    _, request = normalize_provider_request(provider, value.get("request"), label)
    catalog = provider == "deepline" and request.get("operation") in {"search", "describe"}
    paid = provider == "scrapingdog" or provider == "deepline" and request.get("operation") == "execute"
    purpose = value.get("purpose", f"Inspect {request.get('tool') or request.get('query', '')}" if catalog else None)
    phase = "account_discovery" if catalog else value.get("phase")
    scope = "discovery" if catalog else text(value.get("scope"), label + ".scope").casefold().removeprefix("www.")
    action = dict(id="lookup-" + uuid.uuid4().hex[:24], scope=scope, phase=phase,
        description=text(purpose, label + ".purpose"), approach=value.get("approach", "capability-discovery" if catalog else purpose),
        provider=provider, paid_calls=int(paid), cost_upper_bound_credits=value.get("max_cost_credits") if paid else 0)
    if "status_read" in value:
        action["status_read"] = value["status_read"]
    if "pricing_basis" in value:
        action["pricing_basis"] = copy.deepcopy(value["pricing_basis"])
    if "contact_ref" in value:
        action["contact_ref"] = text(value["contact_ref"], label + ".contact_ref")
    return {"action": action, "request": request}


def check_tool_contract(receipt, request):
    """Check live availability and named input fields before paid dispatch.

    The provider remains responsible for its complete native schema. This
    catches missing/misspelled top-level inputs without inventing tool schemas.
    """
    matches = [row for row in receipt.get("results", []) if isinstance(row, dict)
               and request["tool"] in {row.get("toolId"), row.get("id"), row.get("tool")}]
    if len(matches) != 1:
        raise ValueError("saved description does not identify this tool; refresh its description")
    contract = matches[0]
    if contract.get("disabled") or contract.get("connected") is False or contract.get("callable") is False:
        raise ValueError("saved tool description reports this operation unavailable")
    schema = contract.get("inputSchema")
    if not isinstance(schema, dict):
        raise ValueError("saved description has no input schema; refresh its description")
    native = schema.get("jsonSchema", {})
    fields = schema.get("fields", [])
    if (not isinstance(native, dict) or not isinstance(fields, list)
            or any(not isinstance(f, dict) or not isinstance(f.get("name"), str) for f in fields)):
        raise ValueError("saved description has malformed input fields; refresh its description")
    payload = request["payload"]
    required = set(native.get("required", [])) | {f["name"] for f in fields if f.get("required")}
    missing = required - set(payload)
    if missing:
        raise ValueError("provider payload missing required fields: " + ", ".join(sorted(missing)))
    if native.get("additionalProperties") is False and set(payload) - set(native.get("properties", {})):
        allowed = sorted(native.get("properties", {}))
        unknown = sorted(set(payload) - set(allowed))
        raise ValueError(f"provider payload contains fields absent from the saved input schema: {unknown}; allowed fields: {allowed}")
    for field in fields:
        name, kind = field.get("name"), field.get("type")
        if name not in payload:
            continue
        value = payload[name]
        valid = {"string": isinstance(value, str), "boolean": type(value) is bool,
                 "integer": type(value) is int, "number": type(value) in (int, float),
                 "object": isinstance(value, dict), "array": isinstance(value, list)}
        if kind in valid and not valid[kind]:
            raise ValueError(f"provider payload.{name} must be {kind}")
    if request["tool"] == "hunter_email_finder" and "last_name" in payload:
        last = payload["last_name"]
        if isinstance(last, str) and sum(c.isalpha() for c in last) < 2:
            raise ValueError("Hunter requires at least two surname letters; the selected surname is too short for this endpoint. Verify the full surname or choose an eligible LinkedIn-based lookup. Do not guess a name. No paid call was made.")


def _criterion_key(value):
    return " ".join(text(value, "criterion").split()).casefold()


def canonical_requested_role(value, roles):
    """Resolve spacing/case and unambiguous C-suite abbreviations in saved roles."""
    aliases = {"ceo": "chief executive officer", "coo": "chief operating officer"}
    # Sector-dependent abbreviations need an explicit choice from saved roles.
    def key(role):
        return " ".join(role.casefold().split()) if isinstance(role, str) else ""
    exact = [r for r in roles if key(r) == key(value)]
    if len(exact) == 1:
        return exact[0]
    expanded = aliases.get(key(value), key(value))
    matches = [r for r in roles if aliases.get(key(r), key(r)) == expanded]
    return matches[0] if len(matches) == 1 else value


def company_update(document, item):
    """Apply explicit field/criterion updates, preserving unrelated evidence."""
    object_fields(item, {"scope", "state", "stage", "reason_code", "reason_text", "company",
                         "qualification_checks", "account_fit", "signal_evidence", "intent_details",
                         "primary_contact", "backup_contacts"}, "company update")
    from validate_run import _company_key
    scope = text(item.get("scope"), "company update.scope").casefold().removeprefix("www.")
    matches = [(state, row) for state in ("accepted", "unresolved", "rejected") for row in document.get(state, [])
               if _company_key(row) == scope and (state == "accepted" or row.get("stage") in {"account", "contact"})]
    if len(matches) > 1:
        raise ValueError("company has multiple saved records; reconcile before updating")
    old_state, old = matches[0] if matches else ("unresolved", {})
    row = copy.deepcopy(old)
    state = item.get("state", old_state)
    if state not in {"accepted", "unresolved", "rejected"}:
        raise ValueError("company state must be accepted, unresolved or rejected")
    company = copy.deepcopy(row.pop("company", row.pop("candidate", {"domain": scope})))
    if "company" in item:
        if not isinstance(item["company"], dict):
            raise ValueError("company facts must be an object")
        company.update(copy.deepcopy(item["company"]))
    if _company_key({"company": company}) != scope:
        raise ValueError("company update cannot change its canonical identity")
    row["company" if state == "accepted" else "candidate"] = company
    checks = row.setdefault("qualification_checks", [])
    if "qualification_checks" in item:
        if not isinstance(item["qualification_checks"], list):
            raise ValueError("qualification_checks must be an array")
        seen = set()
        for check in item["qualification_checks"]:
            object_fields(check, {"criterion", "importance", "status", "claim", "evidence", "signal", "requirement_ref"}, "qualification check")
            check = copy.deepcopy(check)
            if "requirement_ref" in check:
                from validate_run import request_requirements
                options = request_requirements(document["request"])
                selected = next((r for r in options if r["ref"] == check["requirement_ref"]), None)
                if selected is None:
                    raise ValueError("Unknown requirement_ref; select one of " + str(options))
                check.pop("requirement_ref")
                check.setdefault("criterion", selected["label"])
                if selected["ref"].startswith("attribute:"):
                    if _criterion_key(check["criterion"]) != _criterion_key(selected["label"]):
                        raise ValueError("An attribute check's criterion must match its selected requirement; omit criterion to derive it")
                    if check.get("signal"):
                        raise ValueError("A required attribute is not a buying signal")
                else:
                    if check.get("signal", selected["label"]) != selected["label"]:
                        raise ValueError("Omit signal when selecting requirement_ref; code supplies the saved kind")
                    check["signal"] = selected["label"]
                if check.get("importance", selected["importance"]) != selected["importance"]:
                    raise ValueError("Importance must match the selected requirement")
                check["importance"] = selected["importance"]
            key = _criterion_key(check.get("criterion"))
            if key in seen:
                raise ValueError(f"duplicate criterion update: {key}")
            if check.get("signal"):
                from validate_run import requested_signal
                signal = requested_signal(document["request"], check["signal"])
                if signal:
                    check["signal"] = signal["kind"]
                    if "importance" in signal:
                        if check.get("importance", signal["importance"]) != signal["importance"]:
                            raise ValueError("signal importance must match the saved request")
                        check["importance"] = signal["importance"]
            if check.get("importance") not in {"required", "preferred"} or check.get("status") not in {"pass", "fail", "unknown"}:
                raise ValueError("each criterion needs one explicit importance and status")
            text(check.get("claim"), "claim")
            if not isinstance(check.get("evidence"), list) or check["status"] != "unknown" and not check["evidence"]:
                raise ValueError("pass/fail requires evidence; unknown may have an empty evidence array")
            seen.add(key)
            matches = [index for index, saved in enumerate(checks) if _criterion_key(saved.get("criterion")) == key]
            if len(matches) > 1:
                raise ValueError(f"multiple saved checks for criterion {key}; reconcile before updating")
            # A replacement judgment owns its classification as well as facts.
            # Omitted signal labels must not survive a corrected judgment.
            update = copy.deepcopy(check)
            update["criterion"] = key
            if matches:
                checks[matches[0]] = update
            else:
                checks.append(update)
    for key in ("account_fit", "signal_evidence", "intent_details", "primary_contact", "backup_contacts"):
        if key in item:
            # These are explicit complete replacements, never an implicit
            # recursive merge of contacts or conflicting source identities.
            row[key] = copy.deepcopy(item[key])
    primary = row.get("signal_evidence") or {}
    canonical = (not primary or primary.get("criterion") or
                 any(check.get("signal") == primary.get("signal") and any(
                     not primary.get("evidence_url") or (e.get("url"), e.get("date")) ==
                     (primary.get("evidence_url"), primary.get("evidence_date")) for e in check.get("evidence", []))
                     for check in checks if check.get("signal")))
    if canonical and (primary.get("criterion") or any(check.get("signal") for check in checks)
                      or any(check.get("signal") for check in old.get("qualification_checks", []))):
        # Qualification checks are authoritative. Retain the legacy primary
        # field as a derived view for existing validators and old integrations.
        signals = [check for check in checks if check.get("signal") and check.get("status") == "pass"]
        row.pop("signal_evidence", None)
        if signals and signals[0].get("evidence"):
            evidence = signals[0]["evidence"][0]
            row["signal_evidence"] = {"criterion": signals[0]["criterion"], "signal": signals[0]["signal"], **{
                "evidence_" + key if key in {"url", "date", "date_basis", "text"} else key: value
                for key, value in evidence.items()}}
    if state == "accepted":
        for key in ("stage", "reason_code", "reason_text"):
            row.pop(key, None)
        row.setdefault("backup_contacts", [])
        row["contact_candidate_count"] = int(bool(row.get("primary_contact"))) + len(row["backup_contacts"])
        row["backup_shortfall"] = max(0, document["request"].get("contacts_per_company", 1) - row["contact_candidate_count"])
    else:
        row.setdefault("stage", item.get("stage", "account"))
        if state != old_state or item.get("stage", row["stage"]) != row["stage"] or "reason_code" not in row:
            row["reason_code"] = ("not_icp_fit" if state == "rejected" else
                                  "missing_contact_evidence" if item.get("stage", row["stage"]) == "contact" else "missing_account_evidence")
        for key in ("stage", "reason_code", "reason_text"):
            if key in item:
                row[key] = item[key]
            text(row.get(key), "company update." + key)
    return {"state": state, "row": row}
