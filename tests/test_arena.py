"""Offline integration journeys. Provider/model replies are explicit fixtures."""

import base64
import copy
import json
import os
from pathlib import Path
import socketserver
import subprocess
import sys
import tempfile
import threading
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tyche_arena.broker import Broker, BrokerError, BrokerRefusal
from tyche_arena.runtime import request_for
from tyche_arena.output import companies
from research_tools import ResearchTools
import budget_guard

ICP = {"intent_details_policy": "intent_details_v1", "contact_policy": "contacts_v1",
       "industry": "Manufacturing", "required_attribute": "Manufactures products for retailers",
       "intent_signals": ["Recently integrated an acquired warehouse"], "intent_max_age_days": 365,
       "target_roles": ["Director of Supply Chain"], "target_seniority": "Director+",
       "contact_geography": {"countries": ["US"], "regions": ["OH"], "cities": ["Columbus"]},
       "excluded_companies": ["excluded.example.com"]}
COMPANY_URL = "https://www.linkedin.com/company/example-products"
PERSON_URL = "https://www.linkedin.com/in/ada-example"
PARAGRAPH = ("Example Products connected its acquired warehouse to a shared WMS on August 12, 2026. "
             "The project covers inventory visibility and fulfillment across the combined operation. "
             "This recent integration may increase its need to coordinate stock and orders between warehouses.")


def lookup(tool, inputs, phase="account_verification", **extra):
    return {"checks": [{"target": "example.com", "purpose": "Verify fixture evidence", "phase": phase,
                        "tool": tool, "inputs": inputs, **extra}]}


def scenario():
    company = yield "tyche_lookup", lookup("harvestapi_get_company", {"url": COMPANY_URL})
    company_ref = company["lookups"][0]["results"][0]["ref"]
    pages = yield "tyche_lookup", lookup("generic_http_request", {"url": "https://example.com/news", "method": "GET"})
    refs = [row["ref"] for row in pages["lookups"][0]["results"]]
    yield "tyche_review", {"companies": [{"target": "example.com", "decision": "qualify_account", "reason": "Company and signal verified",
        "company": {"ref": company_ref, "industry": "Manufacturing", "sub_industry": "Textiles",
            "description": "Example Products manufactures packaged goods, tools, and accessories. It supplies retailers with consumer products.",
            "classification_note": "Canonical taxonomy classification"},
        "account_fit": {"ref": refs[0], "fit_claim": "Manufacturing account"},
        "qualification_checks": [
            {"requirement_ref": "attribute:0", "status": "pass", "claim": "Manufactures consumer products for retailers", "evidence": [{"ref": refs[0]}]},
            {"requirement_ref": "signal:0", "status": "pass", "claim": "Connected an acquired warehouse to a shared WMS", "evidence": [{"ref": refs[1]}]}],
        "intent_details": PARAGRAPH}],
        "sources": [{"ref": pages["lookups"][0]["route"], "state": "exhausted", "reason": "Both fixture sources reviewed"}]}
    profile = yield "tyche_lookup", lookup("harvestapi_get_profile", {"url": PERSON_URL, "main": "true"}, "contact_verification")
    profile_ref = profile["lookups"][0]["results"][0]["ref"]
    yield "tyche_review", {"companies": [{"target": "example.com", "decision": "hold_contact", "reason": "Verify selected email",
        "primary_contact": {"ref": profile_ref, "requested_role": "Director of Supply Chain", "role_match": "exact"}}]}
    enriched = yield "tyche_lookup", lookup("harvestapi_get_profile", {"findEmail": "true"}, "contact_discovery", contact_ref=profile_ref)
    profile_ref = enriched["lookups"][0]["results"][0]["ref"]
    yield "tyche_review", {"companies": [{"target": "example.com", "decision": "hold_contact", "reason": "Select enriched profile",
        "primary_contact": {"ref": profile_ref, "requested_role": "Director of Supply Chain", "role_match": "exact"}}]}
    email = yield "tyche_lookup", lookup("zerobounce_validate", {"email": "ada@example.com"}, "email_validation", contact_ref=profile_ref)
    email_ref = email["lookups"][0]["results"][0]["ref"]
    yield "tyche_review", {"companies": [{"target": "example.com", "decision": "accept", "reason": "Verified company and current buyer",
        "primary_contact": {"email_ref": email_ref}}]}
    packet = yield "tyche_finish", {}
    assert packet["status"] == "review_required", packet
    final = yield "tyche_finish", {"review_ref": packet["review_ref"]}
    assert final["delivery_allowed"], final


class FixtureService:
    def __init__(self):
        self.frames = []
        self.errors = []
        self.program = scenario()
        self.started = False

    def provider(self, parameters):
        tool = parameters["tool"]
        data = {
            "harvestapi_get_company": {"status": "ok", "element": {"name": "Example Products",
                "website": "https://example.com", "linkedinUrl": COMPANY_URL,
                "employeeCountRange": {"start": 201, "end": 500},
                "locations": [{"headquarter": True, "country": "United States", "geographicArea": "Ohio"}]}},
            "harvestapi_get_profile": {"status": "ok", "element": {"id": "profile-123", "linkedinUrl": PERSON_URL,
                "firstName": "Ada", "lastName": "Example", "emails": [{"email": "ada@example.com", "status": "valid"}],
                "currentPosition": [{"companyName": "Example Products", "title": "Director of Supply Chain", "companyLinkedinUrl": COMPANY_URL}],
                "location": {"parsed": {"countryFull": "United States", "state": "Ohio", "city": "Columbus"}}}},
            "zerobounce_validate": {"status": "ok", "data": {"address": "ada@example.com", "status": "valid", "sub_status": ""}},
            "generic_http_request": {"results": [
                {"url": "https://example.com/about", "text": "Example Products manufactures packaged goods, tools and accessories for retailers.", "date": "2026-08-10"},
                {"url": "https://example.com/news/wms-project", "text": "On August 12, 2026, the company connected its acquired warehouse to one WMS. The project covers inventory visibility and fulfillment.", "date": "2026-08-12"}]}}
        rate = {"harvestapi_get_company": .03, "harvestapi_get_profile": .14, "zerobounce_validate": .28, "generic_http_request": 0}[tool]
        if tool == "harvestapi_get_profile" and parameters["payload"].get("main") == "true":
            rate = .03
            data[tool]["element"].pop("emails")
        return {**data[tool], "billing": {"credits_charged": rate, "cost_usd": round(rate * .1, 8)}, "request_id": "fixture-request-" + str(len(self.frames))}

    def model(self, parameters):
        messages = parameters["messages"]
        last = messages[-1]
        try:
            if self.started:
                assert last["role"] == "tool", last
                try:
                    content = json.loads(last["content"])
                except ValueError:
                    raise AssertionError(last["content"]) from None
                name, arguments = self.program.send(content)
            else:
                self.started = True
                name, arguments = next(self.program)
            message = {"role": "assistant", "content": None, "tool_calls": [{"id": "tool-" + str(len(self.frames)),
                "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]}
            reason = "tool_calls"
        except StopIteration:
            message, reason = {"role": "assistant", "content": "Done"}, "stop"
        return {"id": "chat-fixture", "object": "chat.completion", "created": 1, "model": parameters["model"], "provider": "OpenAI",
                "choices": [{"index": 0, "message": message, "finish_reason": reason}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


@pytest.fixture
def service(tmp_path):
    service = FixtureService()
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            size = int.from_bytes(Broker._receive(self.request, 4), "big")
            raw = Broker._receive(self.request, size)
            frame = json.loads(raw)
            service.frames.append(frame)
            try:
                if os.environ.get("LEADPOET_SOURCE"):
                    from lab_arena.shim import decode_operation_frame
                    decode_operation_frame(raw)  # Real receiver's parameter checks.
                body = service.model(frame["parameters"]) if frame["operation_id"] == "openrouter.chat" else service.provider(frame["parameters"])
                result = {"status": 200, "headers": {}, "body_b64": base64.b64encode(json.dumps(body).encode()).decode()}
            except BaseException as exc:
                service.errors.append(exc)
                result = {"error": "fixture_failed"}
            encoded = json.dumps(result).encode()
            self.request.sendall(len(encoded).to_bytes(4, "big") + encoded)
    socket_dir = tempfile.TemporaryDirectory(prefix="ta-", dir="/tmp")
    socket_path = Path(socket_dir.name) / "worker.sock"
    server = socketserver.ThreadingUnixStreamServer(str(socket_path), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    service.socket_path = socket_path
    yield service
    server.shutdown()
    server.server_close()
    thread.join()
    socket_dir.cleanup()
    assert not service.errors, service.errors


@pytest.fixture(autouse=True)
def host_contract(monkeypatch):
    if os.environ.get("LEADPOET_SOURCE"):
        monkeypatch.syspath_prepend(os.environ["LEADPOET_SOURCE"])
        monkeypatch.syspath_prepend(str(Path(os.environ["LEADPOET_SOURCE"]) / "lab_arena"))
    monkeypatch.setenv("LAB_ARENA_EVALUATION_DATE", "2026-09-15")
    monkeypatch.delenv("TYCHE_REQUEST_FILE", raising=False)


def test_trigger_to_host_saved_output(tmp_path, service, monkeypatch):
    if not os.environ.get("LEADPOET_SOURCE"):
        pytest.skip("Set LEADPOET_SOURCE to test the actual receiver")
    from lab_arena.agent_entrypoint import run
    from lab_arena.output import output_document_from_bytes
    from qualification.contact_models import validate_contact_claim
    import lab_arena_checkpoint
    checkpoints = []
    original_write = lab_arena_checkpoint.write
    def write(rows, **kwargs):
        original_write(rows, **kwargs)
        checkpoints.append(copy.deepcopy(rows))
    monkeypatch.setattr(lab_arena_checkpoint, "write", write)
    input_path, output_path = tmp_path / "icp.json", tmp_path / "companies.json"
    input_path.write_text(json.dumps({"icp": ICP, "company_limit": 1}))
    monkeypatch.setenv("LAB_ARENA_WORKER_SOCKET", str(service.socket_path))
    monkeypatch.setenv("LAB_ARENA_OUTPUT_PATH", str(output_path))
    run(source_dir=ROOT, input_path=input_path, output_path=output_path)
    result = output_document_from_bytes(output_path.read_bytes(), require_intent_dates=True,
        expected_schema_version="leadpoet.lab_arena.output.v5")
    assert len(result["companies"]) == 1
    row = result["companies"][0]
    assert row["intent_signals"][0]["matched_icp_signal"] == 0
    assert row["required_attribute"]["text"] == ICP["required_attribute"]
    assert validate_contact_claim(row["contact"])["location"]["country"] == "US"
    assert row["contact"]["email_source"]["record_id"] == "profile-123"
    assert sum(f["operation_id"] == "deepline.execute" for f in service.frames) == 5
    assert "arena-host" not in json.dumps(service.frames)
    assert len(checkpoints) == 2  # Reviewed callback and actual host entrypoint.
    assert checkpoints[0] == checkpoints[1]


@pytest.fixture
def reviewed(tmp_path, service):
    from tyche_arena.output import deliver
    run_file = tmp_path / "research/results.json"
    broker = Broker(service.socket_path, time.monotonic() + 30)
    tools = ResearchTools(run_file, execute=broker.execute,
        deliver=lambda path, validation: deliver(path, validation, ICP))
    tools.start(request=request_for(ICP, 1, 20), max_usd=.5)
    program = scenario()
    command = next(program)
    while True:
        result = tools.call(*command)
        try:
            command = program.send(result)
        except StopIteration:
            break
    return tools, broker


def test_json_finish_shares_gate_and_preserves_receipts(reviewed):
    tools, broker = reviewed
    assert (tools.path.parent / "validation.json").exists()
    assert not (tools.path.parent / "leads.xlsx").exists()
    before = tools.path.read_bytes()
    calls = copy.deepcopy(broker.calls)
    assert tools.finish()["delivery_allowed"]
    assert broker.calls == calls
    assert tools.path.read_bytes() == before
    assert budget_guard.load_ledger(tools.path)["calls"]


def test_stale_review_blocks_output(reviewed):
    tools, _ = reviewed
    document = json.loads(tools.path.read_text())
    document["accepted"][0]["intent_details"] += " Changed after approval."
    tools.path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="current final evidence review"):
        companies(tools.path, ICP)


def test_wrong_email_source_fails_delivery(reviewed):
    tools, _ = reviewed
    from unittest.mock import patch
    import linkedin_receipts
    original = linkedin_receipts._saved_profile
    def altered(*args, **kwargs):
        profile = original(*args, **kwargs)
        if args[3] == "in":
            profile["emails"] = [{"email": "other@example.com"}]
        return profile
    with patch.object(linkedin_receipts, "_saved_profile", altered), pytest.raises(ValueError, match="absent from the selected"):
        companies(tools.path, ICP)


def test_quotas_deadline_and_unsupported_tools_block_before_dispatch(tmp_path):
    broker = Broker(tmp_path / "missing.sock", time.monotonic() - 1)
    with pytest.raises(BrokerError, match="deadline"):
        broker.request("openrouter.chat", {})
    broker.deadline = time.monotonic() + 30
    broker.calls["deepline"] = 30
    with pytest.raises(BrokerError, match="quota"):
        broker.request("deepline.execute", {"tool": "harvestapi_get_company"})
    with pytest.raises(ValueError, match="catalog"):
        broker.request("deepline.execute", {"tool": "unapproved"})


def test_unknown_bill_blocks_another_call(tmp_path):
    broker = Broker(tmp_path / "missing.sock", time.monotonic() + 30)
    tools = ResearchTools(tmp_path / "research/results.json", execute=broker.execute)
    tools.start(request=request_for(ICP, 1, 20), max_usd=.5)
    failed = tools.call("tyche_lookup", lookup("harvestapi_get_company", {"url": COMPANY_URL}))
    assert failed["lookups"][0]["status"] == "timeout"
    before = dict(broker.calls)
    with pytest.raises(ValueError, match="already attempted or pending"):
        tools.call("tyche_lookup", lookup("harvestapi_get_company", {"url": COMPANY_URL}))
    assert broker.calls == before
    with pytest.raises(BrokerRefusal, match="blocked_after_uncertain"):
        broker.request("deepline.execute", {"tool": "harvestapi_get_company", "payload": {"url": "https://www.linkedin.com/company/another"}})
    assert broker.calls == before
    calls = budget_guard.load_ledger(tools.path)["calls"].values()
    assert all(call["actual_credits"] is None for call in calls)


def test_current_contract_and_original_constraints_are_preserved():
    source = {**ICP, "contact_geography": {"countries": ["US"]}, "target_seniority": "Director",
              "employee_count": ["11-50", "201-500"], "product_service": "Textiles"}
    request = request_for(source, 2, 255)
    assert json.loads(request["original_text"]) == source
    assert request["target_count"] == 2
    assert request["product_service"]["perspective"] == "target"
    assert "11-50" in request["icp"]["required_attributes"][-1]
    from validate_run import excluded_company
    assert excluded_company(request, {"company": {"domain": "excluded.example.com"}})
    assert not excluded_company(request, {"company": {"domain": "example.com"}})
    with pytest.raises(ValueError, match="supports"):
        request_for({}, 1, 255)


@pytest.mark.parametrize("change, message", [
    ({"required_attribute": {"text": "Manufacturing"}}, "structured attributes"),
    ({"intent_signals": [{"description": "Warehouse expansion"}]}, "structured signals"),
    ({"intent_signals": ["Expansion", "Expansion"]}, "unique"),
    ({"target_seniority": ["Director"]}, "must be text"),
    ({"contact_geography": {"countries": ["Atlantis"]}}, "unrecognized country"),
    ({"bonus_intents": [{"text": "Expansion"}]}, "bonus_intents"),
])
def test_unsupported_icp_fails_before_dispatch(change, message):
    with pytest.raises(ValueError, match=message):
        request_for({**ICP, **change}, 1, 255)


def test_contact_restrictions_fail_closed():
    from tyche_arena.constraints import check_contact
    person = {"country": "United States", "state": "Ohio", "city": "Columbus", "current_title": "Director of Supply Chain"}
    check_contact(person, ICP)
    check_contact({**person, "current_title": "VP of Supply Chain"}, ICP)
    for changed in ({"country": "Canada"}, {"state": "California"}, {"city": "Cincinnati"}, {"state": ""}):
        with pytest.raises(ValueError, match="contact_geography"):
            check_contact({**person, **changed}, ICP)
    with pytest.raises(ValueError, match="seniority"):
        check_contact({**person, "current_title": "Supply Chain Manager"}, ICP)


@pytest.mark.parametrize("code, status", [("budget_exhausted", "quota_exceeded"), ("invalid_request", "schema_error")])
def test_worker_refusal_is_distinct_from_unknown_transport(tmp_path, monkeypatch, code, status):
    broker = Broker(tmp_path / "missing.sock", time.monotonic() + 30)
    def refuse(*args):
        raise BrokerRefusal(code)
    monkeypatch.setattr(broker, "request", refuse)
    tools = ResearchTools(tmp_path / "research/results.json", execute=broker.execute)
    tools.start(request=request_for(ICP, 1, 20), max_usd=.5)
    result = tools.call("tyche_lookup", lookup("harvestapi_get_company", {"url": COMPANY_URL}))
    assert result["lookups"][0]["status"] == status
    assert not broker.provider_blocked
    captures = [json.loads(path.read_text()) for path in tools.path.parent.rglob("*.json")]
    assert any(code in json.dumps(row) and '"dispatched": false' in json.dumps(row) for row in captures)
    assert all(call["actual_credits"] is None for call in budget_guard.load_ledger(tools.path)["calls"].values())


def test_text_completion_cannot_bypass_delivery(tmp_path):
    import asyncio
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.exceptions import UnexpectedModelBehavior
    from tyche_arena.runtime import research
    broker = Broker(tmp_path / "missing.sock", time.monotonic() + 30)
    model = FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart("Done")]))
    with pytest.raises(UnexpectedModelBehavior):
        asyncio.run(research(ICP, tmp_path, broker, limit=1, duration=20, model=model))
    assert broker.calls == {"deepline": 0, "openrouter": 0}
    assert not (tmp_path / "companies.json").exists()


def test_bundle_passes_real_source_admission(tmp_path):
    if not os.environ.get("LEADPOET_SOURCE"):
        pytest.skip("Set LEADPOET_SOURCE to test source admission")
    from scripts.build_arena_bundle import build
    from lab_arena.source_bundle import write_source_archive, validate_source_directory
    directory = build(tmp_path / "bundle")
    assert validate_source_directory(directory, require_license=True) == directory
    from lab_arena.runner import _validated_requirements
    assert len(_validated_requirements(directory / "requirements.txt")) == 3
    write_source_archive(directory, tmp_path / "source.tar.gz")
    assert (directory / ".agents/skills/lead-sourcing/scripts/research_tools.py").exists()
    assert not (directory / "reports").exists()
    assert not (directory / "tests").exists()


def test_packaged_bundle_runs_without_checkout(tmp_path, service):
    if not os.environ.get("LEADPOET_SOURCE"):
        pytest.skip("Set LEADPOET_SOURCE to test the actual receiver")
    from scripts.build_arena_bundle import build
    from lab_arena.output import output_document_from_bytes
    directory = build(tmp_path / "bundle")
    input_path, output_path = tmp_path / "icp.json", tmp_path / "companies.json"
    input_path.write_text(json.dumps({"icp": ICP, "company_limit": 1}))
    script = ("from pathlib import Path; import sys; from lab_arena.agent_entrypoint import run; "
              "run(source_dir=Path(sys.argv[1]),input_path=Path(sys.argv[2]),output_path=Path(sys.argv[3])); "
              "import tyche_arena; assert tyche_arena.ROOT == Path(sys.argv[1])")
    subprocess.run([sys.executable, "-c", script, str(directory), str(input_path), str(output_path)],
        cwd=tmp_path, env={"PYTHONPATH": os.environ["LEADPOET_SOURCE"],
            "LAB_ARENA_WORKER_SOCKET": str(service.socket_path), "LAB_ARENA_OUTPUT_PATH": str(output_path),
            "LAB_ARENA_EVALUATION_DATE": "2026-09-15"}, check=True, capture_output=True, text=True, timeout=310)
    document = output_document_from_bytes(output_path.read_bytes(), require_intent_dates=True,
        expected_schema_version="leadpoet.lab_arena.output.v5")
    assert document["companies"][0]["company_name"] == "Example Products"


def test_large_tool_views_are_explicitly_incomplete():
    from tyche_arena.runtime import model_result
    packet = {"status": "review_required", "review_ref": "current-review", "sources": "x" * 50000}
    result = model_result(packet)
    assert result["truncated"] is True
    assert result["review_ref"] == "current-review"
    assert "evidence_review" in result["next"]
    assert len(json.dumps(result)) < 32000
    assert model_result(["x" * 50000])["truncated"] is True
