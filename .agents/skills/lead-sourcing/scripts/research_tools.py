"""Run-bound research tools. Existing helpers own dispatch, persistence and gates."""

import copy
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import uuid

import budget_guard as budget
import deepline
import email_receipts
import research_input
import run_attempt as runner
import scrapingdog


def obj(properties, required=()):
    return dict(type="object", properties=properties, required=list(required), additionalProperties=False)


STRING = {"type": "string", "minLength": 1}
OBJECT = {"type": "object"}
REFERENCE = {**STRING, "description": "Saved result reference returned by lookup or inspect: route-id:index."}
EVIDENCE = {"type": "object", "additionalProperties": True, "properties": {
    "ref": REFERENCE, "text": STRING, "date": {**STRING, "description": "Verified date in YYYY-MM-DD form."},
    "date_basis": {"enum": ["published", "posted", "updated", "observed_current"]}, "signal": STRING}}
QUALIFICATION_CHECK = obj({"criterion": STRING, "importance": {"enum": ["required", "preferred"]},
    "status": {"enum": ["pass", "fail", "unknown"]}, "claim": STRING, "signal": STRING,
    "evidence": {"type": "array", "items": EVIDENCE}}, ("criterion", "importance", "status", "claim", "evidence"))
CHECK = obj({"target": STRING, "purpose": STRING, "phase": {"enum": [
    "account_discovery", "account_verification", "contact_discovery", "contact_verification", "email_validation"]},
    "provider": {"enum": ["deepline", "scrapingdog"]}, "tool": STRING, "inputs": OBJECT,
    "approach": STRING, "max_cost_credits": {"type": "number", "minimum": 0},
    "status_read": {"type": "boolean"}}, ("target", "purpose", "phase", "inputs"))
COMPANY = obj({"target": STRING, "decision": {"enum": ["hold_account", "qualify_account", "hold_contact", "reject", "accept"]},
    "reason": STRING, "company": OBJECT, "qualification_checks": {"type": "array", "items": QUALIFICATION_CHECK},
    "account_fit": EVIDENCE, "signal_evidence": EVIDENCE,
    "intent_details": {**STRING, "description": "One natural paragraph: state each verified signal and its date, explain its relevance in a following sentence, then close with why the activity matters now. If the ICP describes the target's product/service, connect the signals to that offering and its operations. Avoid repeating qualification filters or inventing a seller's offering. Keep inferred needs conditional."},
    "primary_contact": OBJECT, "backup_contacts": {"type": "array", "items": OBJECT}}, ("target", "decision", "reason"))
WEB = obj({"target": STRING, "purpose": STRING, "query": STRING,
    "operation": {"enum": ["search_query", "open", "find", "click"]},
    "response": OBJECT}, ("target", "purpose", "query", "response"))
SOURCE = obj({"ref": REFERENCE, "state": {"enum": ["exhausted", "continuable", "blocked"]},
    "reason": STRING, "continuations": {"type": "array", "items": REFERENCE}}, ("ref", "state", "reason"))
TOOLS = {
    "tyche_start": ("Interpret the ICP once; initialize the bound run before other tools. Supply contact_role_groups or requested_roles; with groups, omit the duplicate requested_roles list and code derives their union. Set max_usd to the approved dollar cap; code supplies default provider credits. Explicit provider caps remain binding. Repeating the same request resumes without resetting spending. Email verification is priced automatically when the catalog supplies a rate.",
        obj({"request": OBJECT, "max_usd": {"type": "number", "minimum": 0},
             "verification_reserve_credits": {"type": "number", "minimum": 0},
             "scrapingdog_usd_per_credit": {"type": "number", "exclusiveMinimum": 0}}, ("request",))),
    "tyche_lookup": ("Execute 1–3 independent research choices, at most one check per company in a batch. Run discovery pilots singly. Choose the target, phase, tool and native inputs. Schemas, pricing, receipts and IDs are managed here. Use inspect(query=...) to find a capability. Never retry an uncertain paid call; inspect(recover=reference) records its saved response without dispatch. max_cost_credits is only a verified whole-call bound for pricing the catalog cannot express.",
        obj({"checks": {"type": "array", "items": CHECK, "minItems": 1, "maxItems": 3}}, ("checks",))),
    "tyche_review": ("Save judgments and changed fields only. With a Harvest ref, omit receipt-owned names, URLs, size/location fields and their evidence; code supplies them. Company example: {ref, industry, sub_industry, description}. Contact example: {ref, requested_role, role_match, role_group}. Evidence normally needs only {ref} to reuse saved URL, text and date; override text/date only when source interpretation requires it. Select an email validation result with email_ref to supply its exact address and verdict. Never infer a rejection from missing evidence. Include observed web results and reference them as web:0:0. Review source continuation/exhaustion explicitly with sources; saving a fact does not exhaust a source.",
        obj({"companies": {"type": "array", "items": COMPANY}, "web": {"type": "array", "items": WEB},
             "sources": {"type": "array", "items": SOURCE}})),
    "tyche_inspect": ("Read compact run/company state or saved results. query searches the free capability catalog; tool returns cached inputs/pricing. Describe only capabilities needed for the next step. Use ref=route with offset/limit (1–10) to page saved results, or field to select a nested field from a result, tool, company or run. With target, fields such as intent_details and qualification_checks select the saved company record directly. recover records an unrecorded saved response without dispatch; it does not settle unknown billing. Full receipts remain on disk.",
        obj({"target": STRING, "ref": REFERENCE, "field": STRING, "tool": STRING, "query": STRING,
             "recover": REFERENCE, "offset": {"type": "integer", "minimum": 0},
             "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 10}, "refresh": {"type": "boolean"}})),
    "tyche_finish": ("After reviewing evidence and writing, save report commentary and run the existing strict validator and workbook exporter. Returns artifact paths or specific unresolved problems. Final run-only costs are refreshed by the launcher when model usage closes.",
        obj({"commentary": STRING})),
}


def validate(value, schema, path="input"):
    """Validate the small shared tool schemas; provider contracts remain native."""
    kinds = {"object": isinstance(value, dict), "array": isinstance(value, list),
             "string": isinstance(value, str), "integer": type(value) is int,
             "number": type(value) in (int, float), "boolean": type(value) is bool}
    if schema.get("type") in kinds and not kinds[schema["type"]]:
        raise ValueError(f"{path} requires {schema['type']}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} must be one of {schema['enum']}")
    if isinstance(value, dict):
        fields = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            research_input.object_fields(value, set(fields), path)
        missing = set(schema.get("required", [])) - value.keys()
        if missing:
            raise ValueError(f"{path} missing fields: {', '.join(sorted(missing))}")
        for key in fields.keys() & value.keys():
            validate(value[key], fields[key], path + "." + key)
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", float("inf")):
            raise ValueError(f"{path} has an invalid number of items")
        for index, item in enumerate(value):
            validate(item, schema.get("items", {}), f"{path}[{index}]")
    if isinstance(value, str) and len(value.strip()) < schema.get("minLength", 0):
        raise ValueError(f"{path} must not be empty")
    if type(value) in (int, float):
        number = budget.amount(value, path)
        if number < schema.get("minimum", 0) or "exclusiveMinimum" in schema and number <= schema["exclusiveMinimum"]:
            raise ValueError(f"{path} is below its minimum")
        if "maximum" in schema and number > schema["maximum"]:
            raise ValueError(f"{path} exceeds its maximum of {schema['maximum']}")


def compact(value, depth=0):
    """Bound presentation only; expose omitted data through inspect(ref, field)."""
    if isinstance(value, str):
        return value if len(value) <= 1800 else value[:1800] + "… [truncated; inspect a specific field]"
    if isinstance(value, list):
        items = [compact(v, depth + 1) for v in value[:10]]
        return items + ([{"more_items": len(value) - 10}] if len(value) > 10 else [])
    if isinstance(value, dict):
        if depth >= 5:
            return {"available_fields": list(value)}
        return {k: compact(v, depth + 1) for k, v in value.items() if k not in {
            "provider_response", "progress_before", "attempt", "request_fingerprint", "run_fingerprint",
            "logo", "logos", "photo", "profilePicture", "coverPicture", "backgroundCover", "backgroundCovers", "similarOrganizations"}}
    return value


class ResearchTools:
    def __init__(self, run_file, *, execute=None, readonly=False):
        if Path(run_file).is_symlink():
            raise ValueError("Bound run file must be a regular file, not a symlink")
        self.path = Path(run_file).resolve()
        self.execute = execute
        self.readonly = readonly
        self._catalog_lock = threading.RLock()
        self._review_lock = threading.RLock()
        self._dispatch_slots = threading.BoundedSemaphore(3)

    def call(self, name, arguments):
        if name not in TOOLS:
            raise ValueError("Unknown TYCHE tool")
        validate(arguments, TOOLS[name][1])
        if self.readonly and (name != "tyche_inspect" or any(k in arguments for k in ("tool", "query", "recover"))):
            raise ValueError("This read-only startup check cannot research or change a run")
        return getattr(self, name.removeprefix("tyche_"))(**arguments)

    def _execute(self, request, capture):
        with self._dispatch_slots:
            if self.execute:
                return self.execute(request, capture)
            adapter = deepline if request.get("operation") in {"search", "describe", "execute"} else scrapingdog
            return adapter.run(request, capture)

    def _document(self):
        document = budget.read_object(self.path)
        budget.load_ledger(self.path)  # Check the bound run identity on reads too.
        return document

    def _description(self, tool, *, refresh=False):
        with self._catalog_lock:
            document = self._document()
            routes = [r for r in document["routes"] if r.get("operation") == "describe"
                      and r.get("tool") == tool and r.get("provider_status") == "ok"]
            if routes and not refresh:
                body = runner.read_receipt(self.path, routes[-1]["route_id"])["result"]
            else:
                result = runner.run_lookup(self.path, {"request": {"operation": "describe", "tool": tool}}, execute=self._execute)
                body = result["result"]
            matches = [r for r in body.get("results", []) if tool in {r.get("toolId"), r.get("id"), r.get("tool")}]
            if body.get("status") != "ok" or len(matches) != 1:
                raise ValueError("Tool description unavailable: " + tool)
            return matches[0]

    @staticmethod
    def _price(contract, inputs, override=None):
        pricing = contract.get("pricing", {})
        rate, unit = pricing.get("creditsPerUnit"), pricing.get("unit")
        fields = {f["name"]: f for f in contract.get("inputSchema", {}).get("fields", [])}
        quantity = None
        if unit in ("call", "request"):
            quantity = 1
        elif unit == "page" and "page" in fields and not any(k in fields for k in ("pages", "maxPages", "max_pages")):
            quantity = 1  # A page number chooses one page, not that many pages.
        elif unit == "result":
            if "limit" in fields:
                quantity = inputs.get("limit", fields["limit"].get("default"))
            elif contract.get("toolId", contract.get("id")) in {
                    "zerobounce_validate", "bounceban_verify_single", "hunter_email_finder", "datagma_find_email"}:
                quantity = 1
        bound = None
        if rate is not None and type(quantity) is int and quantity > 0:
            bound = budget.amount(rate, "catalog price") * quantity
        if override is not None:
            supplied = budget.amount(override, "verified whole-call price bound")
            if bound is not None and supplied < bound:
                raise ValueError("Supplied price bound is below the catalog-derived whole-call cost")
            return float(supplied)
        if bound is None:
            raise ValueError("Catalog cannot establish a whole-call price bound; inspect this tool and supply max_cost_credits only from verified pricing. No paid call was made.")
        return float(bound)

    def start(self, request, **options):
        with self._catalog_lock:
            # Reject malformed requests before even a free catalog request.
            research_input.normalize_request(request, self.path)
            if self.path.exists():
                runner.start_run(self.path, {"request": request, **options})
                return self.inspect()
            ledger_file = self.path.with_name(self.path.name + ".budget.json")
            original = budget.read_object(ledger_file).get("initial_started_at") if ledger_file.exists() else None
            options["started_at"] = original or os.environ.get("TYCHE_RUN_STARTED_AT") or datetime.now(timezone.utc).isoformat()
            # Check mandatory verification before spending on research. The
            # catalog receipts join the existing run ledger after initialization.
            prepared = []
            if "email" in request.get("contact_fields", ["email"]):
                prepared.append(("zerobounce_validate", "verification-tool.json", {"email": "pricing@example.invalid"}))
            prepared += [("harvestapi_get_company", "company-tool.json", {}),
                         ("harvestapi_get_profile", "profile-tool.json", {"main": "true"})]
            receipts = []
            for tool, filename, inputs in prepared:
                response, unit = self._startup_price(tool, filename, inputs, options["started_at"])
                options["started_at"] = original or response.get("started_at", options["started_at"])
                if tool == "zerobounce_validate" and "verification_reserve_credits" not in options:
                    options["verification_reserve_credits"] = float(Decimal(str(unit)) * request["target_count"])
                receipts.append((tool, response))
            runner.start_run(self.path, {"request": request, **options})
            for tool, response in receipts:
                def replay(_request, capture):
                    if "provider_response" in response:
                        capture(response["provider_response"])
                    return copy.deepcopy(response), 0
                runner.run_lookup(self.path, {"request": {"operation": "describe", "tool": tool}}, execute=replay)
            return self.inspect()

    def _startup_price(self, tool, filename, inputs, started_at):
        from provider_output import ResponseFile
        def price(body):
            contracts = [r for r in body.get("results", []) if r.get("toolId", r.get("id")) == tool]
            if body.get("status") != "ok" or len(contracts) != 1:
                raise ValueError("catalog description unavailable")
            contract = contracts[0]
            if contract.get("disabled") or contract.get("callable") is False or contract.get("connected") is False:
                raise ValueError("required tool is unavailable")
            return self._price(contract, inputs)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        path = self.path.parent / filename
        if path.exists():
            response = budget.read_object(path)
            started_at = response.get("started_at", started_at)
            try:
                return response, price(response)
            except ValueError:
                # Retry only this free catalog read, preserving the old receipt
                # and original clock. Never reuse a failed/unpriced prerequisite.
                path.rename(path.with_name(path.stem + "-" + uuid.uuid4().hex + ".json"))
        query = {"operation": "describe", "tool": tool}
        capture = ResponseFile(path, deepline.redact, metadata={"started_at": started_at})
        response, _ = self._execute(deepline._validate_request(query), capture.capture)
        if not capture.finish(response):
            raise ValueError("Required verification pricing could not be saved")
        response = budget.read_object(path)
        try:
            return response, price(response)
        except ValueError as exc:
            raise ValueError("Required verification price unavailable for " + tool + ": " + str(exc) +
                " No paid research has started. Report this prerequisite to the monitor; do not guess a price, "
                "research replacement companies, or try to finalize an uninitialized run.") from exc

    def lookup(self, checks):
        specs = []
        for item in checks:
            provider = item.get("provider", "deepline")
            if provider == "deepline":
                if not item.get("tool"):
                    raise ValueError("Deepline lookup requires the selected tool ID")
                contract = self._description(item.get("tool"))
                request = {"operation": "execute", "tool": item["tool"], "payload": item["inputs"]}
                research_input.check_tool_contract({"results": [contract]}, request)
                cost = self._price(contract, item["inputs"], item.get("max_cost_credits"))
            else:
                request = item["inputs"]
                if "max_cost_credits" not in item:
                    raise ValueError("ScrapingDog needs a verified whole-call max_cost_credits for the selected operation")
                cost = item["max_cost_credits"]
            spec = dict(provider=provider, scope=item["target"], phase=item["phase"], purpose=item["purpose"],
                        request=request, max_cost_credits=cost)
            for field in ("approach", "status_read"):
                if field in item:
                    spec[field] = item[field]
            specs.append(spec)
        result = runner.run_lookup(self.path, specs if len(specs) > 1 else specs[0], execute=self._execute)
        attempts = result.get("attempts", [result])
        return {"lookups": [self._lookup_view(a) for a in attempts], "progress": self._overview()}

    def _lookup_view(self, attempt, offset=0, limit=10):
        body = attempt.get("result", {})
        rid = body.get("attempt", {}).get("action", {}).get("id") or attempt.get("route_id")
        if not rid and attempt.get("receipt_file"):
            rid = Path(attempt["receipt_file"]).stem
        rows = body.get("results", [])
        catalog = body.get("provider") == "deepline" and body.get("operation") == "search"
        indexed = [(i, row) for i, row in enumerate(rows)
                   if not catalog or row.get("callable") is not False]
        catalog_fields = ("toolId", "id", "displayName", "description", "provider", "callable", "connected", "disabled", "disabledReason")
        recorded = any(r.get("route_id") == rid for r in self._document().get("routes", [])) if rid else False
        view = {"route": rid, "status": body.get("status", "error"), "recorded": recorded,
                "error": compact(attempt.get("error", body.get("error"))),
                "results": [{"ref": f"{rid}:{i}", "facts": compact(
                    {k: r[k] for k in catalog_fields if k in r} if catalog else runner._harvest_display(r))}
                            for i, r in indexed[offset:offset + limit]],
                "result_count": len(indexed), "next_offset": offset + limit if offset + limit < len(indexed) else None,
                "pending_verification": body.get("pending_verification")}
        if catalog:
            view["non_callable_count"] = len(rows) - len(indexed)
            view["catalog_note"] = ("Choose a tool ID and inspect(tool=...) for its native inputs and pricing."
                if indexed else "No callable tools matched. Try a short provider or capability term. Non-callable catalog entries remain saved in the receipt.")
        if body.get("tool") == "harvestapi_search_leads" and rows:
            view["selection_note"] = ("These are discovery matches. After choosing a current company/role match, "
                "fetch harvestapi_get_profile with its LinkedIn URL or profile ID before selecting its ref in review. "
                "Use that profile read to resolve missing parsed location and verify the selected person's identity.")
        if recorded and body.get("status") in {"provider_error", "no_results", "partial", "timeout"}:
            view["recovery_note"] = "This outcome is already recorded. Recovering it cannot resolve unknown billing; preserve the bound until provider billing evidence is available."
        return view

    @staticmethod
    def _field(value, field):
        for key in field.split("."):
            value = value[int(key)] if isinstance(value, list) else value[key]
        return value

    @staticmethod
    def _description_view(contract):
        # Execution still uses the complete saved contract. The researcher needs
        # native inputs and pricing, not duplicate SDK/getter implementation help.
        keys = ("toolId", "id", "description", "inputSchema", "pricing", "connected", "callable",
                "disabled", "disabledReason", "asyncGetAction", "asyncFlow", "defaultExecutionMode")
        view = {k: contract[k] for k in keys if k in contract}
        output = contract.get("outputSchema")
        view["output_fields"] = output.get("fields", []) if isinstance(output, dict) else []
        return view

    def _resolve(self, reference):
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]{0,95}):(\d+)", reference)
        if not match:
            raise ValueError("Select a result reference returned by lookup or inspect")
        rid, index = match[1], int(match[2])
        saved = runner.read_receipt(self.path, rid)["result"]
        if saved.get("receipt_status") != "complete" or saved.get("status") not in {"ok", "no_results", "partial"}:
            raise ValueError("Selected response is incomplete; recover its receipt first")
        body = saved
        if saved.get("provider") == "deepline" and saved.get("operation") == "execute":
            body, _ = deepline.normalize_response(saved["attempt"]["request"], saved["provider_response"])
        rows = body.get("results", [])
        if index >= len(rows) or not isinstance(rows[index], dict):
            raise ValueError("Selected result index does not exist")
        source = {k: saved[k] for k in ("provider", "operation", "tool") if k in saved}
        source["route_id"] = rid
        return copy.deepcopy(rows[index]), source, saved

    @staticmethod
    def _evidence_date(row, value):
        date = next((row.get(k) for k in ("evidence_date", "date", "published_date", "publishedDate", "publication_date") if row.get(k)), None)
        basis = value.get("date_basis", value.get("evidence_date_basis",
                    row.get("evidence_date_basis", row.get("date_basis", "published" if date else "observed_current"))))
        if basis != "observed_current" and not (date or value.get("date") or value.get("evidence_date")):
            raise ValueError("Selected source has no publication/event date. Supply a verified date, or use observed_current only for current-state evidence.")
        return date, basis

    def _evidence(self, value, signal=False):
        if not isinstance(value, dict) or "ref" not in value:
            return copy.deepcopy(value)
        value = copy.deepcopy(value)
        row, source, _ = self._resolve(value.pop("ref"))
        date, basis = self._evidence_date(row, value)
        evidence = {"url": row.get("evidence_url") or row.get("url") or row.get("contact_url") or row.get("company_linkedin_url"),
                    "date": date or self._document()["request"]["as_of_date"],
                    "date_basis": basis,
                    "text": row.get("evidence_text", row.get("text")), "source": source}
        if signal:
            evidence = {"evidence_" + k if k != "source" else k: v for k, v in evidence.items()}
            value = {"evidence_" + k if k in {"url", "date", "date_basis", "text"} else k: v for k, v in value.items()}
        evidence.update(value)
        if evidence.get("source") != source:
            raise ValueError("Selected evidence source cannot be replaced")
        return evidence

    def _harvest(self, value, target, person=False):
        value = copy.deepcopy(value)
        if "ref" not in value:
            return value
        row, source, _ = self._resolve(value.pop("ref"))
        expected = "harvestapi_get_profile" if person else "harvestapi_get_company"
        if source.get("tool") != expected:
            raise ValueError("Company/contact selection requires a saved " + expected + " result. "
                "Fetch that getter using the selected entity's LinkedIn URL or ID, then review its returned ref. "
                "Search matches alone cannot supply the required verified fields.")
        evidence = {"evidence_url": row.get("contact_url") if person else row.get("company_linkedin_url"),
                    "evidence_date": self._document()["request"]["as_of_date"], "evidence_date_basis": "observed_current",
                    "evidence_text": "Current LinkedIn profile fields returned by HarvestAPI.", "source": source}
        if person:
            facts = {"full_name": row.get("contact_name"), "current_title": row.get("contact_title"),
                     "company": row.get("company"), "domain": target, "linkedin_url": row.get("contact_url"),
                     "contact_url": row.get("contact_url"), **{k: row.get(k) for k in ("country", "state", "city")},
                     "location_evidence": evidence, **evidence}
        else:
            if row.get("domain") and row["domain"].removeprefix("www.") != target.removeprefix("www."):
                raise ValueError("Selected LinkedIn company domain differs from this company; reconcile identity")
            facts = {"domain": target, "canonical_name": row.get("company"), "linkedin_url": row.get("company_linkedin_url"),
                     "website": row.get("website"), "employee_range": row.get("employee_range"), "employee_range_evidence": evidence}
            hq = next((r for r in row.get("locations", []) if r.get("headquarter") is True), {})
            parsed = hq.get("parsed", {})
            hq_fields = dict(hq_country=parsed.get("countryFull", parsed.get("country", hq.get("country"))),
                             hq_state=parsed.get("state", hq.get("geographicArea")))
            # Missing optional company HQ fields are not contrary evidence.
            # The reviewer may supply them from other verified company sources.
            facts.update({k: v for k, v in hq_fields.items() if v})
        # Reviewers choose roles and prose; receipt-owned identity fields cannot
        # silently override a different person or company.
        conflicts = sorted(key for key in facts.keys() & value.keys() if facts[key] != value[key])
        if conflicts:
            supplied = sorted(facts.keys() & value.keys())
            raise ValueError("Selected LinkedIn value conflicts with " + ", ".join(conflicts)
                             + ". Keep the selected ref and omit these automatically supplied fields: "
                             + ", ".join(supplied) + ". Reconcile a different identity by selecting its correct ref.")
        return {**facts, **value}

    def _contact(self, value, target, *, patch_primary=True):
        contact = self._harvest(value, target, person=True)
        previous = next((r.get("primary_contact", {}) for state in ("accepted", "unresolved")
                         for r in self._document().get(state, []) if runner._company_key(r) == target), {})
        if patch_primary and previous and "ref" not in value:
            for key in ("full_name", "linkedin_url", "contact_url"):
                if key in value and value[key] != previous.get(key):
                    raise ValueError("Select a new profile ref when changing contact identity")
            previous = copy.deepcopy(previous)
            if "email" in contact and contact["email"] != previous.get("email"):
                previous.pop("email_validation", None)
                previous.pop("email_source", None)
            contact = {**previous, **contact}
        ref = contact.pop("email_ref", None)
        if ref:
            row, source, _ = self._resolve(ref)
            selected_email = row.get("address") or row.get("email")
            if not isinstance(selected_email, str) or not selected_email.strip():
                raise ValueError("Selected email result must identify an exact address")
            if contact.get("email") and (not isinstance(contact["email"], str)
                    or contact["email"].strip().casefold() != selected_email.strip().casefold()):
                raise ValueError("Selected email result conflicts with the contact email; select the matching receipt or explicitly change the email")
            if not contact.get("email"):
                contact["email"] = selected_email.strip()
            result = email_receipts.saved_result(self.path, self._document()["routes"], source, contact["email"])
            contact["email_validation"] = {**result, "source": {**source, "validator": email_receipts.validator_for_tool(source["tool"])}}
        return contact

    def _observe_web(self, item):
        spec = dict(provider="public_web", scope=item["target"], phase="account_discovery" if item["target"] == "discovery" else "account_verification",
                    purpose=item["purpose"], request={"operation": item.get("operation", "search_query"), "query": item["query"]})
        prepared = research_input.prepare_lookup(spec)
        _, action, _ = runner._validate_spec(prepared, plan_only=True)
        prior = next((r for r in reversed(self._document()["stop_audit"].get("route_frontier", []))
                      if r.get("request_fingerprint") == action["request_fingerprint"]), None)
        if prior:
            rid = prior["route_id"]
        else:
            result = runner.run_lookup(self.path, spec, plan_only=True)
            rid = Path(result["receipt_file"]).stem
        try:
            runner.complete_public_web(self.path, rid, item["response"], check_stop=False)
        except ValueError as exc:
            raise ValueError(f"{exc}. Existing web reference: {rid}. Inspect and reuse the saved observation; put revised interpretation in company evidence.") from exc
        return rid

    def review(self, companies=(), web=(), sources=()):
        # Expansion of partial contact updates and the existing atomic save
        # share one lock; concurrent reviews cannot overwrite newer fields.
        with self._review_lock:
            aliases = {}
            try:
                return self._review(companies, web, sources, aliases)
            except ValueError as exc:
                if aliases:
                    raise ValueError(f"{exc}. Web observations were saved as {json.dumps(aliases)}; reuse these references when correcting the judgment.") from exc
                raise

    def _review(self, companies, web, sources, aliases):
        # Validate selected provider facts before persisting attached web
        # observations. Input corrections should not create partial web saves.
        def check_web_dates(value):
            if isinstance(value, dict):
                match = re.fullmatch(r"web:(\d+):(\d+)", str(value.get("ref", "")))
                if match:
                    try:
                        row = web[int(match[1])]["response"]["results"][int(match[2])]
                    except (IndexError, KeyError, TypeError) as exc:
                        raise ValueError("Web evidence reference does not select an attached result. web: aliases only refer to observations attached to this call; use the returned lookup reference for an already saved source.") from exc
                    self._evidence_date(row, value)
                for child in value.values():
                    check_web_dates(child)
            elif isinstance(value, list):
                for child in value:
                    check_web_dates(child)
        check_web_dates(list(companies))
        selected = copy.deepcopy(list(companies))
        for item in selected:
            target = item["target"]
            if "company" in item:
                item["company"] = self._harvest(item["company"], target)
            if "primary_contact" in item:
                item["primary_contact"] = self._contact(item["primary_contact"], target)
            if "backup_contacts" in item:
                item["backup_contacts"] = [self._contact(c, target, patch_primary=False) for c in item["backup_contacts"]]
        for i, item in enumerate(web):
            aliases[f"web:{i}"] = self._observe_web(item)
        def refs(value):
            if isinstance(value, dict):
                return {k: refs(v) for k, v in value.items()}
            if isinstance(value, list):
                return [refs(v) for v in value]
            if isinstance(value, str):
                for alias, rid in aliases.items():
                    if value == alias or value.startswith(alias + ":"):
                        return rid + value[len(alias):]
            return value
        updates = []
        for item in refs(selected):
            target, decision = item["target"], item["decision"]
            change = {"scope": target, "state": {"accept": "accepted", "reject": "rejected"}.get(decision, "unresolved"),
                      "stage": "contact" if decision in {"qualify_account", "hold_contact"} else "account", "reason_text": item["reason"]}
            for key in ("qualification_checks", "account_fit", "signal_evidence", "intent_details"):
                if key in item:
                    change[key] = copy.deepcopy(item[key])
            for check in change.get("qualification_checks", []):
                check["evidence"] = [self._evidence(e) for e in check.get("evidence", [])]
            for key in ("account_fit", "signal_evidence"):
                if key in change:
                    change[key] = self._evidence(change[key], signal=True)
            for key in ("company", "primary_contact", "backup_contacts"):
                if key in item:
                    change[key] = item[key]
            updates.append(change)
        routes = {}
        for source in refs(list(sources)):
            rid = source["ref"].split(":")[0]
            route = routes.setdefault(rid, {"route_id": rid, "state": source["state"],
                                           "reasons": [], "continuation_route_ids": []})
            if route["state"] != source["state"]:
                raise ValueError("Results from " + rid + " have conflicting source decisions; choose one decision for that lookup.")
            if source["reason"] not in route["reasons"]:
                route["reasons"].append(source["reason"])
            route["continuation_route_ids"] = list(dict.fromkeys(route["continuation_route_ids"] +
                [r.split(":")[0] for r in source.get("continuations", [])]))
        for route in routes.values():
            route["reason"] = "\n".join(route.pop("reasons"))
        runner.save_review(self.path, {"companies": updates, "routes": list(routes.values())})
        def timing(document):
            if len(document.get("accepted", [])) >= document["request"]["target_count"]:
                document["stop_check"].setdefault("leads_ready_at", datetime.now(timezone.utc).isoformat())
            else:
                document["stop_check"].pop("leads_ready_at", None)
            return document
        runner.mutate(self.path, timing)
        return {"saved_companies": [c["scope"] for c in updates], "web_references": aliases, "progress": self._overview()}

    def _overview(self):
        document = self._document()
        ledger = budget.load_ledger(self.path)
        totals = runner.calculate_cost_summary(document)
        rows = []
        for state in ("accepted", "unresolved", "rejected"):
            for row in document.get(state, []):
                rows.append({"target": runner._company_key(row), "state": state, "stage": row.get("stage"),
                             "missing": [c.get("criterion") for c in row.get("qualification_checks", [])
                                         if c.get("status") == "unknown" and c.get("importance") != "preferred"],
                             "reason": row.get("reason_text")})
        decision = runner.evaluate_stop(document, execution_budget=ledger)
        completed = {r["route_id"] for r in document["routes"]}
        pending = [{"ref": r["route_id"], "target": r.get("scope"), "reason": r.get("reason")}
                   for r in document["stop_audit"].get("route_frontier", []) if r["route_id"] not in completed]
        return {"summary": document.get("summary", {}), "companies": rows[:12], "company_count": len(rows),
                "elapsed_seconds": decision.get("elapsed_seconds"),
                "budget": {"cap_usd": ledger["usd_limit"], "costs": totals, "blocked": ledger.get("blocked")},
                "pending": pending[:12],
                "review_due": runner.review_reminder(document), "stop": decision["decision"], "errors": decision["errors"],
                "blocked_actions": decision.get("blocked_actions", {})}

    def inspect(self, target=None, ref=None, field=None, tool=None, query=None, recover=None, offset=0, limit=10, refresh=False):
        if sum(v is not None for v in (target, ref, tool, query, recover)) > 1:
            raise ValueError("Inspect one company, result, capability query, tool or recovery reference at a time")
        if refresh and not tool:
            raise ValueError("refresh applies only to a selected tool description")
        if not self.path.exists():
            return {"status": "not_started", "next": "Use tyche_start with the interpreted request"}
        if tool:
            contract = self._description(tool, refresh=refresh)
            return {"tool": compact(self._field(contract, field)) if field else self._description_view(contract)}
        if query:
            result = runner.run_lookup(self.path, {"request": {"operation": "search", "query": query}}, execute=self._execute)
            return self._lookup_view(result, offset, limit)
        if recover:
            rid = recover.split(":")[0]
            saved = runner.read_receipt(self.path, rid)["result"]
            runner.finish_attempt(self.path, rid, saved)
            return {"recovered": rid, "progress": self._overview()}
        if ref:
            if ":" not in ref:
                receipt = runner.read_receipt(self.path, ref)
                return {"value": compact(self._field(receipt["result"], field))} if field else self._lookup_view(receipt, offset, limit)
            value, source, _ = self._resolve(ref)
            if field:
                value = self._field(value, field)
            if isinstance(value, list):
                return {"source": source, "items": compact(value[offset:offset + limit]), "total": len(value)}
            if isinstance(value, str):
                return {"source": source, "text": value[offset:offset + 1800], "total_characters": len(value),
                        "next_offset": offset + 1800 if offset + 1800 < len(value) else None}
            return {"source": source, "facts": compact(value)}
        if target:
            document = self._document()
            rows = [r for state in ("accepted", "unresolved", "rejected") for r in document.get(state, []) if runner._company_key(r) == target]
            routes = [r for r in document["routes"] if r.get("scope") == target]
            value = {"company": rows[0] if rows else None, "route_count": len(routes),
                    "recent_sources": [{"ref": r["route_id"], "purpose": r.get("request_summary"),
                                        "phase": r.get("phase"), "status": r.get("provider_status"), "rows": r.get("rows_returned")} for r in routes[-limit:]]}
            if field:
                # Preserve response-wrapper paths while accepting record fields
                # directly, as researchers use them in review inputs.
                try:
                    selected = self._field(value, field)
                except (KeyError, IndexError, TypeError, ValueError):
                    try:
                        selected = self._field(value["company"], field)
                    except (KeyError, IndexError, TypeError, ValueError) as exc:
                        fields = sorted((value["company"] or {}).keys())
                        raise ValueError(f"Unknown company field {field!r}; saved fields: {fields}. "
                                         "Inspection metadata: route_count, recent_sources.") from exc
                return {"value": compact(selected)}
            value["company"] = compact(value["company"])
            return value
        if field:
            return {"value": compact(self._field(self._document(), field))}
        return {"request": self._document()["request"], **self._overview()}

    def finish(self, commentary=None):
        with self._review_lock:
            return self._finish(commentary)

    def _finish(self, commentary):
        if commentary is not None or not (self.path.parent / "research-commentary.md").exists():
            commentary = commentary or "No additional research commentary supplied."
            (self.path.parent / "research-commentary.md").write_text(commentary + "\n", encoding="utf-8")
        exporter = Path(__file__).with_name("export_xlsx.mjs")
        node = os.environ.get("TYCHE_WORKSPACE_NODE", "node")
        result = subprocess.run([node, str(exporter), str(self.path)], capture_output=True, text=True, timeout=180)
        if result.returncode:
            raise ValueError((result.stderr or result.stdout)[-9000:])
        # The final launcher pass adds closed model usage without rewriting
        # research prose or changing the validated results/workbook.
        report = subprocess.run([os.environ.get("TYCHE_WORKSPACE_PYTHON", "python3"),
            str(Path(__file__).resolve().parents[4] / "scripts/run_costs.py"), str(self.path)],
            capture_output=True, text=True, timeout=30)
        if report.returncode:
            raise ValueError("Workbook saved; report needs repair: " + report.stderr[-2000:])
        return {"export": json.loads(result.stdout.strip().splitlines()[-1]), "progress": self._overview(),
                "report": str(self.path.parent / "report.md"), "costs": str(self.path.parent / "run-costs.json"),
                "preview": str(self.path.parent / "leads-preview.png"), "validation": str(self.path.parent / "validation.json")}
