"""Run-bound research tools. Existing helpers own dispatch, persistence and gates."""

import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import uuid
from urllib.parse import urlsplit, urlunsplit

import budget_guard as budget
import deepline
import email_receipts
import linkedin_receipts
import research_input
import provider_pricing
import run_attempt as runner
import scrapingdog
from source_receipts import FUNDING_TOOL, funding_record
from validate_run import request_requirements, required_attribute_errors


def obj(properties, required=()):
    return dict(type="object", properties=properties, required=list(required), additionalProperties=False)


STRING = {"type": "string", "minLength": 1}
OBJECT = {"type": "object"}
REFERENCE = {**STRING, "description": "Saved result reference returned by lookup or inspect: route-id:index."}
EVIDENCE = {"type": "object", "additionalProperties": True, "properties": {
    "ref": REFERENCE, "text": STRING, "date": {**STRING, "description": "Verified date in YYYY-MM-DD form."},
    "date_basis": {"enum": ["published", "posted", "updated", "observed_current"]}, "signal": STRING}}
QUALIFICATION_CHECK = obj({"criterion": STRING, "requirement_ref": {**STRING, "description": "Select attribute:N or signal:N from inspect().requirements. Omit criterion for a new check; retain its criterion when explicitly remapping a legacy signal check."}, "importance": {"enum": ["required", "preferred"], "description": "Code supplies importance for a selected requirement."},
    "status": {"enum": ["pass", "fail", "unknown"]}, "claim": {**STRING, "description": "Explain why the saved source satisfies this exact requirement. Preserve its event status, date and strength. Current observations alone do not prove duration or acceleration. Unsupported required claims remain unknown."}, "signal": STRING,
    "evidence": {"type": "array", "items": EVIDENCE}}, ("status", "claim", "evidence"))
CHECK = obj({"target": STRING, "purpose": STRING, "phase": {"enum": [
    "account_discovery", "account_verification", "contact_discovery", "contact_verification", "email_validation"]},
    "provider": {"enum": ["deepline", "scrapingdog"]}, "tool": STRING, "inputs": OBJECT,
    "contact_ref": {**REFERENCE, "description": "Reviewed profile for email work. Code supplies native name, company domain and LinkedIn inputs; supply email or provider options when needed."},
    "approach": STRING, "max_cost_credits": {"type": "number", "minimum": 0},
    "status_read": {"type": "boolean"}}, ("target", "purpose", "inputs"))
COMPANY = obj({"target": STRING, "decision": {"enum": ["hold_account", "qualify_account", "hold_contact", "reject", "accept"]},
    "reason": STRING, "company": OBJECT, "qualification_checks": {"type": "array", "items": QUALIFICATION_CHECK},
    "account_fit": EVIDENCE, "signal_evidence": EVIDENCE,
    "intent_details": {**STRING, "description": "One natural paragraph: state each verified signal and its date, explain its relevance in a following sentence, then close with why the activity matters now. If the ICP describes the target's product/service, connect the signals to that offering and its operations. Avoid repeating qualification filters or inventing a seller's offering. Keep inferred needs conditional."},
    "primary_contact": OBJECT, "backup_contacts": {"type": "array", "items": OBJECT}}, ("target", "decision", "reason"))
WEB = obj({"target": STRING, "purpose": STRING, "query": STRING,
    "operation": {"enum": ["search_query", "open", "find", "click"]},
    "response": obj({"status": STRING, "operation": STRING, "error": {},
        "results": {"type": "array", "items": {**OBJECT, "description": "One observed source: url, text excerpt, and date/date_basis when supplied. Do not paste a serialized tool transcript."}}},
        ("status", "results"))}, ("target", "purpose", "query", "response"))
SOURCE = obj({"ref": REFERENCE, "state": {"enum": ["exhausted", "continuable", "blocked"]},
    "reason": STRING, "continuations": {"type": "array", "items": REFERENCE}}, ("ref", "state", "reason"))
class OperationalBlock(ValueError):
    """A saved provider/setup failure, distinct from correctable research inputs."""


class ReferenceError(ValueError):
    """A saved selection needs correction; never guess a replacement."""

    def __init__(self, reference, reason):
        self.reference = reference
        super().__init__(f"Invalid saved reference {reference!r}: {reason}")


TOOLS = {
    "tyche_start": ("Interpret the ICP once; initialize the bound run before other tools. Save each buying signal with importance required or preferred. Preserve supplied product_service as {description, perspective: seller or target}. Supply contact_role_groups or requested_roles; with groups, omit the duplicate requested_roles list and code derives their union. Set max_usd to the approved dollar cap; code supplies default provider credits. Explicit provider caps remain binding. Repeating the same request resumes without resetting spending. Email verification reserve is calculated automatically; omit verification_reserve_credits for ordinary runs.",
        obj({"request": {**OBJECT, "description": "Required: target_count; icp with non-signal must-haves in required_attributes and optional company_types/industries/geographies/exclusions; buying_signals [{kind, importance: required|preferred, query, max_age_days?}]; requested_roles or contact_role_groups {primary, secondary}; time_window {max_age_days}. Optional: product_service {description, perspective: seller|target}, contact_fields, contacts_per_company, signal_match_mode any|all. The launcher supplies original_text; compare it with the interpretation before paid research."}, "max_usd": {"type": "number", "minimum": 0},
             "verification_reserve_credits": {"type": "number", "minimum": 0},
             "scrapingdog_usd_per_credit": {"type": "number", "exclusiveMinimum": 0}}, ("request",))),
    "tyche_lookup": ("Execute 1–3 independent research choices, at most one check per company in a batch. Run discovery pilots singly. Choose the target, tool and native inputs; supply phase for non-email research. Email finder/validator phases are derived. For email work pass contact_ref from the reviewed profile; omit routine names, company domain and LinkedIn inputs. Code supplies them from the receipt. Schemas, pricing, receipts and IDs are managed here. operationally_blocked means save remaining judgments and report the blocker; more discovery or finalization cannot repair it. Use inspect(query=...) to find a capability. Never retry an uncertain paid call; inspect(recover=reference) records its saved response without dispatch. max_cost_credits is only a verified whole-call bound for pricing the catalog cannot express.",
        obj({"checks": {"type": "array", "items": CHECK, "minItems": 1, "maxItems": 3}}, ("checks",))),
    "tyche_review": ("Save judgments and changed fields only. With a Harvest ref, omit receipt-owned names, URLs, size/location fields and their evidence; code supplies them. Company example: {ref, industry, sub_industry, description}. Contact example: {ref, requested_role, role_match}; code derives the role group. Select requirement_ref from inspect().requirements for each required attribute or signal. Code supplies criterion, signal and importance; retain criterion only when replacing an old check. Store signals once in qualification_checks. Keep observed wording in claim/evidence. Do not tag geography or general fit as a signal. The primary signal field and workbook are derived from these checks. A replacement check without signal removes its prior signal label. Evidence normally needs only {ref} to reuse saved URL, text and date; override text/date only when source interpretation requires it. For URL-free Aviato funding attributes, keep the saved date/text and explain the stage judgment in claim; signals still need URLs. Select an email validation result with email_ref to supply its exact address and verdict. Never infer a rejection from missing evidence. Include observed web results and reference them as web:0:0. Selecting a successful single-result company/profile getter or email verdict closes that lookup. Review other sources and pagination explicitly with sources.",
        obj({"companies": {"type": "array", "items": COMPANY}, "web": {"type": "array", "items": WEB},
             "sources": {"type": "array", "items": SOURCE}})),
    "tyche_inspect": ("Read compact run/company state or saved results. query searches the free capability catalog; tool returns cached inputs/pricing. Describe only capabilities needed for the next step. Use ref=route with offset/limit (1–10) to page saved results, or field to select a nested field from a result, tool, company or run. Use field=requirements for selectable request criteria, field=costs for saved costs, field=pending_sources to page open saved lookups (including discovery), or target plus field=evidence_review for claims beside saved source excerpts. Other target fields select the saved company record directly. recover records an unrecorded saved response without dispatch; it does not settle unknown billing. Full receipts remain on disk.",
        obj({"target": STRING, "ref": REFERENCE, "field": STRING, "tool": STRING, "query": STRING,
             "recover": REFERENCE, "offset": {"type": "integer", "minimum": 0},
             "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 10}, "refresh": {"type": "boolean"}})),
    "tyche_finish": ("Resolve mechanical preflight gaps first. Compare the final packet claims with its saved source excerpts, dates and writing, then pass its review_ref to export that reviewed version. Use tyche_review to correct findings first; changed evidence requires a fresh review packet. Returns actionable research gaps or strict validated artifacts. Final run-only costs are refreshed when model usage closes.",
        obj({"commentary": STRING, "review_ref": STRING})),
}


def validate(value, schema, path="input", root=None):
    """Validate the small shared tool schemas; provider contracts remain native."""
    root = schema if root is None else root
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
            unknown = sorted(value.keys() - fields.keys())
            if unknown:
                locations = ["input." + k for k in unknown if path != "input" and k in root.get("properties", {})]
                nested = [f"{path}.{parent}.{key}" for key in unknown for parent, child in fields.items()
                          if key in child.get("properties", {})]
                raise ValueError(f"{path} has unknown fields: {unknown}; allowed fields: {sorted(fields)}."
                                 + (f" Top-level fields belong at: {locations}." if locations else "")
                                 + (f" Nested fields belong at: {nested}." if nested else ""))
        missing = set(schema.get("required", [])) - value.keys()
        if missing:
            raise ValueError(f"{path} missing fields: {', '.join(sorted(missing))}")
        for key in fields.keys() & value.keys():
            validate(value[key], fields[key], path + "." + key, root)
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", float("inf")):
            raise ValueError(f"{path} has {len(value)} items; allowed count: {schema.get('minItems', 0)}–{schema.get('maxItems', 'unbounded')}")
        for index, item in enumerate(value):
            validate(item, schema.get("items", {}), f"{path}[{index}]", root)
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


def contract_view(value, path=""):
    """Presentation only. Keep input names/constraints; abbreviate long help/lists."""
    if isinstance(value, dict):
        return {k: contract_view(v, f"{path}.{k}" if path else k) for k, v in value.items()}
    if isinstance(value, list):
        if path.rsplit(".", 1)[-1] in {"enum", "examples"} and len(value) > 20:
            return {"preview": value[:20], "total": len(value), "detail_field": path}
        return [contract_view(v, f"{path}.{i}") for i, v in enumerate(value)]
    if isinstance(value, str) and path.rsplit(".", 1)[-1] in {"description", "title"} and len(value) > 400:
        return value[:400] + f"… [full text: inspect field={path}]"
    return value


def reference_paths(value, reference, path="input"):
    if isinstance(value, dict):
        return [p for k, v in value.items() for p in reference_paths(v, reference, f"{path}.{k}")]
    if isinstance(value, list):
        return [p for i, v in enumerate(value) for p in reference_paths(v, reference, f"{path}[{i}]")]
    return [path] if value == reference else []


class ResearchTools:
    def __init__(self, run_file, *, execute=None, readonly=False, environment=None):
        if Path(run_file).is_symlink():
            raise ValueError("Bound run file must be a regular file, not a symlink")
        self.path = Path(run_file).resolve()
        self.execute = execute
        self.readonly = readonly
        self.environment = dict(os.environ if environment is None else environment)
        self._billing_checked = False
        self._catalog_lock = threading.RLock()
        self._review_lock = threading.RLock()
        self._dispatch_slots = threading.BoundedSemaphore(3)

    def call(self, name, arguments):
        if name not in TOOLS:
            raise ValueError("Unknown TYCHE tool")
        validate(arguments, TOOLS[name][1])
        if self.readonly and (name != "tyche_inspect" or any(k in arguments for k in ("tool", "query", "recover"))):
            raise ValueError("This read-only startup check cannot research or change a run")
        try:
            return getattr(self, name.removeprefix("tyche_"))(**arguments)
        except OperationalBlock as exc:
            return self._blocked_result(exc)
        except ReferenceError as exc:
            raise self._reference_correction(exc, arguments) from exc

    def _reference_correction(self, error, arguments):
        target = arguments.get("target") or next((c.get("target") for c in arguments.get("companies", [])
            if reference_paths(c, error.reference)), None)
        paths = reference_paths(arguments, error.reference)
        return ValueError(f"{', '.join(paths) or 'input.ref'}: {error}. "
                          f"Saved choices: {json.dumps(self._reference_choices(error.reference, target))}. "
                          "Choose the source that supports the claim; no replacement was selected.")

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
                error = OperationalBlock if tool in {"harvestapi_get_company", "harvestapi_get_profile"} else ValueError
                raise error("Tool description unavailable: " + tool)
            contract = matches[0]
            if tool in {"harvestapi_get_company", "harvestapi_get_profile"} and (
                    contract.get("disabled") or contract.get("connected") is False or contract.get("callable") is False):
                raise OperationalBlock(tool + ": required tool is unavailable; restore access and refresh its description.")
            return contract

    _price = staticmethod(provider_pricing.call_credits)

    def _operational_block(self):
        """Derive service blocks from saved receipts/ledger, never company fit."""
        if not self.path.exists():
            return None
        document = self._document()
        ledger = budget.load_ledger(self.path)
        if ledger.get("blocked"):
            return ledger["blocked"]
        # Email failures retain the existing eligible fallback path. A failed
        # mandatory LinkedIn service cannot be replaced by more discovery.
        for tool in ("harvestapi_get_company", "harvestapi_get_profile"):
            route = next((r for r in reversed(document["routes"]) if r.get("tool") == tool), None)
            if not route:
                continue
            status = route.get("provider_status")
            if status in {"auth_failed", "quota_exceeded"}:
                return f"{tool}: {status}; inspect the saved receipt {route['route_id']} and restore provider access."
            if route.get("operation") == "describe" and status == "ok":
                body = runner.read_receipt(self.path, route["route_id"])["result"]
                contract = next((r for r in body.get("results", []) if r.get("toolId", r.get("id")) == tool), {})
                if contract.get("disabled") or contract.get("connected") is False or contract.get("callable") is False:
                    return f"{tool}: required tool is unavailable; restore provider access and refresh its description."
                try:
                    self._price(contract, {"main": "true"} if tool == "harvestapi_get_profile" else {})
                except ValueError as exc:
                    return f"{tool}: {exc}"
        return None

    def _clear_operational_status(self):
        path = self.path.parent / "operational-status.json"
        if path.exists() and not self._operational_block():
            with budget.transaction(path) as saved:
                saved.clear()
                saved.update(status="ready", run_file=str(self.path), delivery_allowed=False)

    def _blocked_result(self, reason):
        result = {"status": "operationally_blocked", "delivery_allowed": False, "reason": str(reason),
                  "run_file": str(self.path), "resume": "Preserve this run and its ledger. Save any remaining judgments, then report the blocker to the monitor. Resume after pricing/access is repaired; refresh the affected free description. Do not repeat uncertain paid calls, reject companies, or claim exhausted research to close an operational failure."}
        if self.path.exists():
            document = self._document()
            result.update(summary=document.get("summary", {}), costs=runner.calculate_cost_summary(document))
        path = self.path.parent / "operational-status.json"
        result["status_file"] = str(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with budget.transaction(path) as saved:
            saved.clear()
            saved.update(result)
        return result

    def start(self, request, **options):
        with self._catalog_lock:
            request = copy.deepcopy(request)
            if self.path.exists():
                original = self._document()["request"].get("original_text")
            else:
                source = self.environment.get("TYCHE_REQUEST_FILE")
                original = Path(source).read_text(encoding="utf-8") if source else None
            if original is not None:
                if request.get("original_text", original) != original:
                    raise ValueError("original_text must match the bound original request")
                request["original_text"] = original
            # Reject malformed requests before even a free catalog request.
            research_input.normalize_request(request, self.path)
            if self.path.exists():
                runner.start_run(self.path, {"request": request, **options})
                blocker = self._operational_block()
                if blocker:
                    return self._blocked_result(blocker)
                self._clear_operational_status()
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
            # These free prerequisites are independent and save to distinct files.
            # Await all of them before creating a ledger or allowing paid research.
            with ThreadPoolExecutor(max_workers=3) as pool:
                pending = [(tool, pool.submit(self._startup_price, tool, filename, inputs, options["started_at"]))
                           for tool, filename, inputs in prepared]
                receipts = []
                for tool, future in pending:
                    response, unit = future.result()
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
            self._clear_operational_status()
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
            raise OperationalBlock("Required verification price unavailable for " + tool + ": " + str(exc) +
                " No paid research has started. Report this prerequisite to the monitor; do not guess a price, "
                "research replacement companies, or try to finalize an uninitialized run.") from exc

    def lookup(self, checks):
        if not self.path.exists():
            return self.inspect()
        blocker = self._operational_block()
        if blocker:
            return self._blocked_result(blocker)
        specs = []
        for index, original in enumerate(checks):
            item = copy.deepcopy(original)
            provider = item.get("provider", "deepline")
            if provider == "deepline":
                if email_receipts.validator_for_tool(item.get("tool")):
                    item["phase"] = "email_validation"
                elif email_receipts.email_work(
                        {"paid_calls": 1}, {"tool": item.get("tool", ""), "payload": item["inputs"]}):
                    item["phase"] = "contact_discovery"
            if not item.get("phase"):
                raise ValueError("Choose phase for non-email research: account_discovery, account_verification, contact_discovery or contact_verification")
            if provider == "deepline":
                if not item.get("tool"):
                    raise ValueError("Deepline lookup requires the selected tool ID")
                contract = self._description(item.get("tool"))
                request = {"operation": "execute", "tool": item["tool"], "payload": item["inputs"]}
                if item.get("contact_ref"):
                    action = dict(scope=item["target"], phase=item["phase"], contact_ref=item["contact_ref"],
                                  paid_calls=1, status_read=item.get("status_read", False))
                    if not email_receipts.email_work(action, request):
                        raise ValueError("contact_ref is for email work on a reviewed profile")
                    document = self._document()
                    company, contact = runner._email_gate(self.path, document, action, request)
                    fields = linkedin_receipts.email_identity_fields(document, self.path, company, contact)
                    schema = contract.get("inputSchema", {})
                    allowed = set(schema.get("jsonSchema", {}).get("properties", {})) | {f["name"] for f in schema.get("fields", [])}
                    for key in allowed & fields.keys():
                        item["inputs"].setdefault(key, fields[key])
                    for key in fields.keys() - allowed:
                        item["inputs"].pop(key, None)
                try:
                    research_input.check_tool_contract({"results": [contract]}, request)
                except ValueError as exc:
                    raise ValueError(f"input.checks[{index}].inputs ({item['tool']}): {exc}. No paid call was made.") from exc
                try:
                    cost = self._price(contract, item["inputs"], item.get("max_cost_credits"))
                except ValueError as exc:
                    if item["tool"] == "harvestapi_get_company" or provider_pricing.profile_price(contract, item["inputs"]):
                        raise OperationalBlock(item["tool"] + ": " + str(exc)) from exc
                    raise
            else:
                request = item["inputs"]
                if "max_cost_credits" not in item:
                    raise ValueError("ScrapingDog needs a verified whole-call max_cost_credits for the selected operation")
                cost = item["max_cost_credits"]
            spec = dict(provider=provider, scope=item["target"], phase=item["phase"], purpose=item["purpose"],
                        request=request, max_cost_credits=cost)
            if provider == "deepline" and contract.get("pricing", {}).get("creditsPerUnit") is None:
                stored = provider_pricing.profile_price(contract, item["inputs"])
                if stored:
                    spec["pricing_basis"] = copy.deepcopy(stored)
            for field in ("approach", "status_read", "contact_ref"):
                if field in item:
                    spec[field] = item[field]
            specs.append(spec)
        result = runner.run_lookup(self.path, specs if len(specs) > 1 else specs[0], execute=self._execute)
        attempts = result.get("attempts", [result])
        output = {"lookups": [self._lookup_view(a) for a in attempts], "progress": self._overview()}
        blocker = self._operational_block()
        if blocker:
            output.update(self._blocked_result(blocker))
        return output

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
        if email_receipts.validator_for_tool(body.get("tool")) and recorded:
            document = self._document()
            route = next(r for r in document["routes"] if r["route_id"] == rid)
            view["email_decisions"] = email_receipts.route_decisions(self.path, document, route)
        if recorded and body.get("status") in {"provider_error", "no_results", "partial", "timeout"}:
            view["recovery_note"] = "This outcome is already recorded. Recovering it cannot resolve unknown billing; preserve the bound until provider billing evidence is available."
        return view

    @staticmethod
    def _field(value, field):
        prefix = []
        for key in field.split("."):
            try:
                selected = value[int(key)] if isinstance(value, list) else value[key]
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                if isinstance(value, dict):
                    available = [".".join(prefix + [k]) for k in value]
                    matches = [".".join(prefix + [k, key]) for k, v in value.items() if isinstance(v, dict) and key in v]
                else:
                    available = f"indices 0–{len(value) - 1}" if isinstance(value, list) and value else []
                    matches = []
                raise ValueError(f"Unknown field {field!r} at {'.'.join(prefix + [key])!r}; available fields: {available}."
                                 + (f" Matching nested fields: {matches}." if matches else "")) from exc
            value = selected
            prefix.append(key)
        return value

    @staticmethod
    def _description_view(contract):
        # Execution still uses the complete saved contract. The researcher needs
        # native inputs and pricing, not duplicate SDK/getter implementation help.
        keys = ("toolId", "id", "description", "inputSchema", "pricing", "connected", "callable",
                "disabled", "disabledReason", "asyncGetAction", "asyncFlow", "defaultExecutionMode")
        view = {k: contract_view(contract[k], k) for k in keys if k in contract}
        if contract.get("toolId", contract.get("id")) == "harvestapi_get_profile":
            view["stored_planning_prices"] = provider_pricing.PROFILE_PRICES
        output = contract.get("outputSchema")
        view["output_fields"] = [{k: f[k] for k in ("name", "type") if k in f}
                                 for f in output.get("fields", [])] if isinstance(output, dict) else []
        view["detail_note"] = ("Reuse this description. Long help/enum previews are abbreviated; inspect(tool=..., field=...) "
            "reads saved detail. Select field=inputSchema for complete inputs in one call, or a narrower object for its complete subtree; text/lists use offset/limit. Execution checks the full saved contract and price; refresh only after a contract/access change.")
        return view

    def _reference_choices(self, reference, target=None):
        routes = [r for r in self._document().get("routes", []) if r.get("operation") not in {"describe", "search"}
                  or r.get("provider") != "deepline"]
        matching = [r for r in routes if r["route_id"] == reference.split(":")[0]]
        routes = matching or [r for r in routes if target is None or r.get("scope") == target][-4:]
        choices = []
        for route in routes:
            try:
                saved = runner.read_receipt(self.path, route["route_id"])["result"]
            except (OSError, ValueError):
                continue
            rows = saved.get("results", [])
            if saved.get("receipt_status") != "complete" or saved.get("status") not in {"ok", "partial", "no_results"}:
                continue
            choices.append({"route": route["route_id"], "target": route.get("scope"), "tool": route.get("tool"),
                            "result_refs": [f"{route['route_id']}:{i}" for i, row in enumerate(rows) if isinstance(row, dict)][:10],
                            "result_count": len(rows)})
        return choices

    def _receipt(self, reference):
        rid = reference.split(":")[0]
        try:
            return runner.read_receipt(self.path, rid)
        except FileNotFoundError as exc:
            raise ReferenceError(reference, "Unknown saved result reference") from exc

    def _resolve(self, reference):
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]{0,95}):(\d+)", reference)
        if not match:
            raise ReferenceError(reference, "Select a result reference returned by lookup or inspect: route-id:index")
        rid, index = match[1], int(match[2])
        try:
            saved = self._receipt(rid)["result"]
        except ReferenceError as exc:
            raise ReferenceError(reference, "Unknown saved result reference") from exc
        if saved.get("receipt_status") != "complete" or saved.get("status") not in {"ok", "no_results", "partial"}:
            raise ValueError("Selected response is incomplete; recover its receipt first")
        body = saved
        if saved.get("provider") == "deepline" and saved.get("operation") == "execute":
            body, _ = deepline.normalize_response(saved["attempt"]["request"], saved["provider_response"])
        rows = body.get("results", [])
        if index >= len(rows) or not isinstance(rows[index], dict):
            raise ReferenceError(reference, "Selected result index does not exist")
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
        reference = value.pop("ref")
        row, source, _ = self._resolve(reference)
        date, basis = self._evidence_date(row, value)
        evidence = {"url": row.get("evidence_url") or row.get("url") or row.get("contact_url") or row.get("company_linkedin_url"),
                    "date": date or self._document()["request"]["as_of_date"],
                    "date_basis": basis,
                    "text": row.get("evidence_text") or row.get("text") or row.get("snippet"), "source": source}
        if source.get("tool") == FUNDING_TOOL and evidence["url"] is None:
            source["result_index"] = int(reference.rsplit(":", 1)[1])
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
        reference = value.pop("ref")
        row, source, _ = self._resolve(reference)
        expected = "harvestapi_get_profile" if person else "harvestapi_get_company"
        if source.get("tool") != expected:
            raise ValueError("Company/contact selection requires a saved " + expected + " result. "
                "Fetch that getter using the selected entity's LinkedIn URL or ID, then review its returned ref. "
                "Search matches alone cannot supply the required verified fields.")
        evidence = {"evidence_url": row.get("contact_url") if person else row.get("company_linkedin_url"),
                    "evidence_date": self._document()["request"]["as_of_date"], "evidence_date_basis": "observed_current",
                    "evidence_text": "Current LinkedIn profile fields returned by HarvestAPI.", "source": source}
        if person:
            facts = {"profile_ref": reference, "full_name": row.get("contact_name"), "current_title": row.get("contact_title"),
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
        if patch_primary and previous and ("ref" not in value or contact.get("linkedin_url") == previous.get("linkedin_url")):
            for key in ("full_name", "linkedin_url", "contact_url"):
                if key in value and value[key] != previous.get(key):
                    raise ValueError("Select a new profile ref when changing contact identity")
            previous = copy.deepcopy(previous)
            if "email" in contact and contact["email"] != previous.get("email"):
                previous.pop("email_validation", None)
                previous.pop("email_source", None)
            contact = {**previous, **contact}
        request = self._document()["request"]
        role = research_input.canonical_requested_role(contact.get("requested_role"), request.get("requested_roles", []))
        if role:
            contact["requested_role"] = role
            for group, roles in request.get("contact_role_groups", {}).items():
                if role in roles:
                    contact["role_group"] = group
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
            validator = email_receipts.validator_for_tool(source["tool"])
            validation = {**result, "source": {**source, "validator": validator}}
            if validator == "bounceban":
                routes = self._document()["routes"]
                original, original_source = email_receipts.original_validation(self.path, routes, contact["email"])
                ids = [r.get("route_id") for r in routes]
                if not original or not email_receipts.fallback_allowed(original) or ids.index(original_source["route_id"]) >= ids.index(source["route_id"]):
                    raise ValueError("Select an eligible same-email ZeroBounce receipt before its BounceBan result")
                validation = {**original, "source": original_source, "fallback": validation}
            contact["email_validation"] = validation
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
        validate(list(web), {"type": "array", "items": WEB}, "input.web")
        # Expansion of partial contact updates and the existing atomic save
        # share one lock; concurrent reviews cannot overwrite newer fields.
        with self._review_lock:
            aliases = {}
            try:
                return self._review(companies, web, sources, aliases)
            except ValueError as exc:
                if aliases:
                    message = f"{exc}. Web observations were saved as {json.dumps(aliases)}; reuse these references when correcting the judgment."
                    raise (ReferenceError(exc.reference, message) if isinstance(exc, ReferenceError) else ValueError(message)) from exc
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
        # A reviewed point lookup has no next page. Keep discovery and batch
        # source decisions explicit; do not close work merely because it ran.
        closed = {r["route_id"] for r in self._document()["stop_audit"].get("route_frontier", []) if r.get("state") == "exhausted"}
        for item in companies:
            selections = [item.get("company", {}), item.get("primary_contact", {}), *item.get("backup_contacts", [])]
            for value in selections:
                for key in ("ref", "email_ref"):
                    if key not in value:
                        continue
                    reference = refs(value[key])
                    rid = reference.split(":")[0]
                    if rid in routes or rid in closed:
                        continue
                    saved = self._receipt(rid)["result"]
                    tool = saved.get("tool")
                    if (saved.get("status") == "ok" and saved.get("receipt_status") == "complete"
                            and len(saved.get("results", [])) == 1 and not saved.get("pending_verification")
                            and (tool in {"harvestapi_get_company", "harvestapi_get_profile"}
                                 or email_receipts.validator_for_tool(tool))):
                        routes[rid] = {"route_id": rid, "state": "exhausted",
                                       "reason": "Selected single-result lookup reviewed and saved"}
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
                                         if c.get("status") == "unknown" and c.get("importance") != "preferred"]
                                        + required_attribute_errors(document["request"], row, runner._company_key(row)),
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
                "completion_candidates": self._completion_candidates(document, decision),
                "blocked_actions": decision.get("blocked_actions", {}), "operational_block": self._operational_block()}

    def _completion_candidates(self, document, stop):
        """Derived advice only: the LLM still chooses the next useful research action."""
        if len(document.get("accepted", [])) >= document["request"]["target_count"]:
            return []
        candidates = []
        for row in document.get("unresolved", []):
            if row.get("stage") != "contact":
                continue
            target = runner._company_key(row)
            contact = row.get("primary_contact", {})
            company = row.get("company", row.get("candidate", {}))
            missing = linkedin_receipts.contact_verification_errors(document, self.path, company, contact)
            verified = not missing
            if not contact.get("country"):
                missing.append("Contact country is still missing from the selected LinkedIn profile")
            missing.extend("Company " + field + " still needs review" for field in ("industry", "sub_industry", "description") if not company.get(field))
            email = contact.get("email")
            validation = contact.get("email_validation", {})
            usable = False
            if email and validation.get("source"):
                try:
                    chosen = validation.get("fallback", validation)
                    usable = email_receipts.decision(self.path, document["routes"], chosen["source"], email)["usable"]
                except (ValueError, OSError, KeyError):
                    pass
            if not usable and "email" in document["request"].get("contact_fields", ["email"]):
                missing.append("Select an existing valid email receipt, or complete email discovery/validation after the profile")
            actions = {a["id"] for a in document.get("stop_check", {}).get("next_actions", []) if a.get("scope") == target}
            blocked = {k: v for k, v in stop.get("blocked_actions", {}).items() if k in actions}
            saved_emails = []
            for route in document["routes"]:
                if route.get("scope") == target and route.get("phase") == "email_validation":
                    try:
                        saved_emails.extend(d for d in email_receipts.route_decisions(self.path, document, route) if d["usable"])
                    except (ValueError, OSError, KeyError):
                        pass
            candidates.append({"target": target, "profile_verified": verified, "email_usable": usable, "missing": missing,
                "saved_valid_emails": saved_emails,
                "blocked_actions": blocked, "next": "Complete and review this qualified candidate before more discovery when affordable; choose another route if concretely blocked."})
        candidates.sort(key=lambda c: (-int(bool(c["saved_valid_emails"])), -int(c["profile_verified"])))
        return candidates[:3]

    def inspect(self, **options):
        try:
            return self._inspect(**options)
        except ReferenceError as exc:
            raise self._reference_correction(exc, options) from exc

    def _inspect(self, target=None, ref=None, field=None, tool=None, query=None, recover=None, offset=0, limit=10, refresh=False):
        if sum(v is not None for v in (target, ref, tool, query, recover)) > 1:
            raise ValueError("Inspect one company, result, capability query, tool or recovery reference at a time")
        if refresh and not tool:
            raise ValueError("refresh applies only to a selected tool description")
        if not self.path.exists() and tool not in TOOLS:
            status = self.path.parent / "operational-status.json"
            if status.exists():
                return budget.read_object(status)
            return {"status": "not_started", "next": "Use tyche_start with the interpreted request"}
        if tool:
            if tool in TOOLS:
                description, schema = TOOLS[tool]
                contract = {"toolId": tool, "description": description, "inputSchema": schema}
            else:
                contract = self._description(tool, refresh=refresh)
                self._clear_operational_status()
            if not field:
                return {"tool": self._description_view(contract)}
            value = self._field(contract, field)
            if isinstance(value, str):
                return {"tool": value[offset:offset + 1800], "total_characters": len(value),
                        "next_offset": offset + 1800 if offset + 1800 < len(value) else None}
            if isinstance(value, list):
                return {"tool": copy.deepcopy(value[offset:offset + limit]),
                        "total": len(value), "next_offset": offset + limit if offset + limit < len(value) else None}
            return {"tool": copy.deepcopy(value)}
        if query:
            result = runner.run_lookup(self.path, {"request": {"operation": "search", "query": query}}, execute=self._execute)
            return self._lookup_view(result, offset, limit)
        if recover:
            rid = recover.split(":")[0]
            saved = self._receipt(rid)["result"]
            runner.finish_attempt(self.path, rid, saved)
            return {"recovered": rid, "progress": self._overview()}
        if ref:
            if ":" not in ref:
                receipt = self._receipt(ref)
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
            if field == "evidence_review":
                sources = {}
                return {"requirements": request_requirements(document["request"]),
                        "company": self._company_review(rows[0], sources) if rows else None, "sources": sources}
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
            if field == "requirements":
                return {"requirements": request_requirements(self._document()["request"])}
            if field == "costs":
                return {"costs": self._cost_summary()}
            if field == "pending_sources":
                pending = runner.pending_source_reviews(self._document())
                return {"items": pending[offset:offset + limit], "total": len(pending),
                        "next_offset": offset + limit if offset + limit < len(pending) else None}
            try:
                return {"value": compact(self._field(self._document(), field))}
            except ValueError as exc:
                raise ValueError(f"input.field: {exc} Derived fields: requirements, costs, pending_sources.") from exc
        return {"request": self._document()["request"], "requirements": request_requirements(self._document()["request"]),
                "cached_descriptions": sorted({r["tool"] for r in self._document().get("routes", [])
                    if r.get("operation") == "describe" and r.get("provider_status") == "ok" and r.get("tool")}),
                "tool_guidance": "Mandatory verification prerequisites are checked. Choose research for the next evidence gap; do not inventory future phases first. Reuse cached descriptions with inspect(tool=...) when needed; no catalog search is needed for these IDs.",
                "request_review": "Compare original_text with these interpreted must-haves and preferences before paid research. Every explicit non-signal must-have belongs in icp.required_attributes; each needs its own evidence check. Only the user can change the criteria.", **self._overview()}

    def _company_review(self, row, sources, receipts=None):
        """Show claims beside receipt excerpts; semantic judgment stays with the LLM."""
        receipts = {} if receipts is None else receipts
        def url_key(value):
            parsed = urlsplit(value or "")
            return urlunsplit((parsed.scheme.casefold(), parsed.netloc.casefold(), parsed.path.rstrip("/"), parsed.query, ""))
        def evidence(value, company_fact=False):
            view = {k: value.get("evidence_" + k, value.get(k)) for k in ("url", "date", "date_basis", "text")}
            view["text"] = compact(view["text"])
            rid = value.get("source", {}).get("route_id")
            try:
                if not rid:
                    raise ValueError("No saved receipt reference")
                if company_fact and view["url"] is None and "result_index" in value.get("source", {}):
                    record = funding_record(self.path, self._document(), company, value)
                    ref = f"{rid}:{value['source']['result_index']}"
                    sources[ref] = {**view, "provider": "deepline", "tool": FUNDING_TOOL,
                                    "record": {k: record[k] for k in ("id", "name", "stage", "announcedOn", "moneyRaised", "currency") if k in record}}
                    view["source_refs"] = [ref]
                    return view
                if rid not in receipts:
                    saved = self._receipt(rid)["result"]
                    if saved.get("receipt_status") != "complete" or saved.get("status") not in {"ok", "partial"}:
                        raise ValueError("Source receipt is incomplete or has no usable evidence")
                    if saved.get("provider") == "deepline" and saved.get("operation") == "execute":
                        saved, _ = deepline.normalize_response(saved["attempt"]["request"], saved["provider_response"])
                    receipts[rid] = saved
                matches = []
                for index, result in enumerate(receipts[rid].get("results", [])):
                    address = next((result.get(k) for k in ("evidence_url", "url", "contact_url", "company_linkedin_url") if result.get(k)), None)
                    if not view["url"] or url_key(address) != url_key(view["url"]):
                        continue
                    ref = f"{rid}:{index}"
                    matches.append(ref)
                    sources[ref] = {"url": address,
                        "text": compact(result.get("evidence_text") or result.get("text") or result.get("snippet") or json.dumps(runner._harvest_display(result))),
                        "date": result.get("evidence_date", result.get("date")),
                        "date_basis": result.get("evidence_date_basis", result.get("date_basis"))}
                if not matches:
                    raise ValueError("The selected URL is absent from the saved receipt; select its actual source")
                view["source_refs"] = matches
            except (ValueError, OSError, KeyError) as exc:
                view["source_error"] = str(exc)
            return view
        def contact(person):
            view = {k: person.get(k) for k in ("full_name", "current_title", "company", "requested_role", "role_match", "linkedin_url", "country", "state", "city", "email")}
            verdict = person.get("email_validation", {})
            fields = ("status", "result", "provider_status")
            view["email_validation"] = {k: verdict.get(k) for k in fields}
            if verdict.get("fallback"):
                view["email_validation"]["fallback"] = {k: verdict["fallback"].get(k) for k in fields}
            return view
        company = row.get("company", row.get("candidate", {}))
        checks = [{**{k: check.get(k) for k in ("criterion", "signal", "importance", "status", "claim")},
                   "evidence": [evidence(e, company_fact=not check.get("signal")) for e in check.get("evidence", [])]}
                  for check in row.get("qualification_checks", [])]
        review = {"company": {k: company.get(k) for k in ("canonical_name", "domain", "industry", "sub_industry", "description", "employee_range")},
                  "account_fit": evidence(row.get("account_fit", {})), "qualification_checks": checks,
                  "intent_details": row.get("intent_details"),
                  "primary_contact": contact(row.get("primary_contact", {})),
                  "backup_contacts": [contact(person) for person in row.get("backup_contacts", [])]}
        primary = row.get("signal_evidence", {})
        if primary and not primary.get("criterion"):
            review["signal_evidence"] = {"signal": primary.get("signal"), **evidence(primary)}
        return review

    def _cost_summary(self):
        path = self.path.parent / "run-costs.json"
        if path.exists():
            report = budget.read_object(path)
            return {k: report.get(k) for k in ("status", "scope", "basis", "provider_usd",
                "worker_standard_api_equivalent_usd", "combined_standard_equivalent_usd", "missing", "limitations")}
        return {"provider": runner.calculate_cost_summary(self._document()),
                "model": "Final run-only model usage closes after worker exit; the launcher refreshes the cost report."}

    def finish(self, commentary=None, review_ref=None):
        with self._review_lock:
            return self._finish(commentary, review_ref)

    def _finish(self, commentary, review_ref):
        if not self.path.exists():
            return self.inspect()
        if self.execute is None:
            from billing_reconciliation import reconcile
            fresh = not self._billing_checked
            self._billing_checked = True
            reconcile(self.path, refresh=fresh)
        blocker = self._operational_block()
        if blocker:
            return self._blocked_result(blocker)
        progress = self._overview()
        document = self._document()
        pending_sources = runner.pending_source_reviews(document)
        if progress["stop"] in {"continue", "repair_state"}:
            return {"status": "needs_research", "delivery_allowed": False, "progress": progress,
                    "pending_sources": pending_sources,
                    "next": "Review pending_sources from saved receipts with inspect/review; no repeated lookup is needed to save a source decision. Resolve other research gaps using lookup/review. Prefer affordable completion_candidates; no export has run. Do not invent rejected companies or repeat unchanged finalization."}
        _, preflight = runner.delivery_preflight(self.path, document, check_review=False)
        if preflight["errors"]:
            return {"status": "needs_repair", "delivery_allowed": False, "errors": preflight["errors"],
                    "pending_sources": pending_sources,
                    "next": "Resolve these mechanical gaps with review/inspect before final evidence review. No approval or export has occurred."}
        expected = runner.review_fingerprint(document)
        approval = document.get("final_review", {})
        if (review_ref is not None and review_ref != expected) or (review_ref is None and approval.get("review_ref") != expected):
            sources, receipts = {}, {}
            companies = [self._company_review(row, sources, receipts) for row in document.get("accepted", [])]
            source_errors = []
            for company in companies:
                evidence = [company["account_fit"], company.get("signal_evidence", {})] + [
                    e for c in company["qualification_checks"] for e in c["evidence"]]
                source_errors.extend(company["company"]["domain"] + ": " + e["source_error"] for e in evidence if "source_error" in e)
            if source_errors:
                return {"status": "needs_repair", "delivery_allowed": False, "errors": source_errors,
                        "companies": companies, "sources": sources,
                        "next": "Correct the source references using the saved receipts. inspect(target=..., field=evidence_review) shows claims and source excerpts. No final approval has occurred."}
            return {"status": "review_required", "delivery_allowed": False, "review_ref": expected,
                    "request": document["request"], "requirements": request_requirements(document["request"]),
                    "instructions": "Review before approving: compare every account with the original must-haves, preferences, geography and product/service context. Check the original source passages for company identity, event status, date and claim strength; your earlier paraphrase is not independent evidence. General career descriptions or role-family lists do not establish current vacancies; current observations alone do not establish duration, repetition or acceleration. Keep unsupported preferred signals unknown. Signals contain verified facts/date/source. Intent Details: state each verified signal, follow it with a sentence explaining its relevance, then end with how the evidence together relates to the requested product/service (seller offering for seller perspective; target offering for target perspective). Explain the company situation; merely calling a contact a timely buyer is insufficient. Keep inferred needs conditional. Description is exactly two factual business sentences. Correct with tyche_review, request a fresh packet, then approve its review_ref. This remains LLM source review, not an automatic semantic pass.",
                    "companies": companies, "sources": sources}
        if approval.get("review_ref") != expected:
            def approve(saved):
                if runner.review_fingerprint(saved) != expected:
                    raise ValueError("Research changed during review; request the current review packet")
                saved["final_review"] = {"review_ref": expected, "reviewed_at": datetime.now(timezone.utc).isoformat()}
                return saved
            runner.mutate(self.path, approve)
        if commentary is not None or not (self.path.parent / "research-commentary.md").exists():
            commentary = commentary or "No additional research commentary supplied."
            (self.path.parent / "research-commentary.md").write_text(commentary + "\n", encoding="utf-8")
        exporter = Path(__file__).with_name("export_xlsx.mjs")
        node = self.environment.get("TYCHE_WORKSPACE_NODE", "node")
        result = subprocess.run([node, str(exporter), str(self.path)], capture_output=True, text=True, timeout=180, env=self.environment)
        if result.returncode:
            return {"status": "needs_repair", "delivery_allowed": False,
                    "errors": [(result.stderr or result.stdout)[-9000:]], "progress": self._overview(),
                    "next": "Correct the named saved fields or source reviews with tyche_review, then finish again. Do not read implementation code or repeat unchanged finalization."}
        # The final launcher pass adds closed model usage without rewriting
        # research prose or changing the validated results/workbook.
        report = subprocess.run([self.environment.get("TYCHE_WORKSPACE_PYTHON", "python3"),
            str(Path(__file__).resolve().parents[4] / "scripts/run_costs.py"), str(self.path)],
            capture_output=True, text=True, timeout=30, env=self.environment)
        if report.returncode:
            raise ValueError("Workbook saved; report needs repair: " + report.stderr[-2000:])
        return {"export": json.loads(result.stdout.strip().splitlines()[-1]), "progress": self._overview(),
                "delivery_allowed": True, "cost_summary": self._cost_summary(),
                "report": str(self.path.parent / "report.md"), "costs": str(self.path.parent / "run-costs.json"),
                "preview": str(self.path.parent / "leads-preview.png"), "validation": str(self.path.parent / "validation.json")}
