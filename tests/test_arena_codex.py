"""TYCHE-only offline contracts; no Leadpoet imports, Codex or live providers."""

from contextlib import contextmanager
import copy
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import tomllib
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tyche_arena import runtime
from tyche_arena.broker import Broker, BrokerError, BrokerRefusal
from tyche_arena.input import request_for
from tyche_arena.mcp import LAB_TOOLS, LabTools, model_result
from tyche_arena.output import companies
from research_tools import ResearchTools
import budget_guard

ICP = {"intent_details_policy": "intent_details_v1", "contact_policy": "contacts_v1",
       "industry": "Manufacturing", "required_attribute": "Manufactures products for retailers",
       "intent_signals": ["Recently integrated an acquired warehouse", "Recently announced a strategic partnership"],
       "intent_max_age_days": 365,
       "bonus_intents": [{"intent_signal": "Recently announced a strategic partnership", "intent_category": "PARTNERSHIP", "intent_max_age_days": 90}],
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


def scenario(finish_tool="tyche_finish"):
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
    if finish_tool is None:
        return
    packet = yield finish_tool, {}
    assert packet["status"] == "review_required", packet
    final = yield finish_tool, {"review_ref": packet["review_ref"]}
    assert final["checkpoint_saved"], final
    assert final["delivery_allowed"] == (finish_tool == "tyche_finish"), final



class ProviderFixture:
    def __init__(self):
        self.frames = []
        self.provider_responses = []

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
        body = {**data[tool], "billing": {"credits_charged": rate, "cost_usd": round(rate * .1, 8)}, "request_id": "fixture-request-" + str(len(self.frames))}
        self.provider_responses.append((copy.deepcopy(parameters), copy.deepcopy(body)))
        return body


@pytest.fixture
def lab(tmp_path, monkeypatch):
    fixture = ProviderFixture()
    fixture.processes = []
    fixture.sessions = []
    fixture.research = []
    fixture.mode = "deliver"
    fixture.program = scenario
    fixture.after_program = lambda tools: None
    fixture.output = tmp_path / "companies.json"
    monkeypatch.setenv("LAB_ARENA_COMPANY_LIMIT", "1")
    monkeypatch.setenv("LAB_ARENA_WORKER_SOCKET", str(tmp_path / "worker.sock"))
    monkeypatch.setenv("LAB_ARENA_OUTPUT_PATH", str(fixture.output))
    monkeypatch.setenv("LAB_ARENA_EVALUATION_DATE", "2026-09-15")
    monkeypatch.delenv("TYCHE_REQUEST_FILE", raising=False)
    original_mkdtemp = runtime.tempfile.mkdtemp
    monkeypatch.setattr(runtime.tempfile, "mkdtemp", lambda **kwargs: original_mkdtemp(prefix="run-", dir=tmp_path))

    def request(self, operation, parameters):
        assert operation == "deepline.execute"
        fixture.frames.append(copy.deepcopy(parameters))
        return 200, {}, fixture.provider(parameters)

    monkeypatch.setattr(Broker, "request", request)

    def checkpoint(rows):
        fixture.output.write_text(json.dumps({"companies": rows}))

    monkeypatch.setitem(sys.modules, "lab_arena_checkpoint", SimpleNamespace(write=checkpoint))

    @contextmanager
    def session(**selection):
        fixture.sessions.append(selection)
        codex_home = tmp_path / "codex-home"
        codex_home.mkdir()
        (codex_home / "config.toml").write_text('model_provider = "arena"\n[model_providers.arena]\nwire_api = "responses"\n')
        environment = {"CODEX_HOME": str(codex_home), "HOME": str(codex_home), "PYTHONPATH": "/agent:/agent/source:/agent/deps"}
        try:
            yield environment
        finally:
            fixture.session_closed = True

    fixture.host = SimpleNamespace(session=session, CODEX_BINARY="/usr/local/bin/codex")
    monkeypatch.setattr(runtime, "require_lab", lambda: fixture.host)
    monkeypatch.setattr(runtime.os, "killpg", lambda *args: None)

    class Process:
        pid = 999999
        returncode = 0

        def __init__(self, command, **kwargs):
            fixture.processes.append(self)
            self.command, self.kwargs = command, kwargs
            self.stdout = io.BytesIO(b"fixture diagnostics\n")
            self.waited = False
            self.run_dir = Path(command[command.index("-C") + 1])
            config = tomllib.loads((Path(kwargs["env"]["CODEX_HOME"]) / "config.toml").read_text())
            fixture.config = config
            assert config["model_providers"]["arena"]["wire_api"] == "responses"
            assert kwargs["start_new_session"]
            assert kwargs["stdin"].read().startswith(b"Research the authoritative")

        def wait(self, timeout=None):
            if self.waited:
                return self.returncode
            self.waited = True
            if fixture.mode == "timeout":
                raise subprocess.TimeoutExpired(self.command, timeout)
            if fixture.mode == "prose":
                (self.run_dir / "final.txt").write_text("I delivered all the leads.")
                return 0
            arguments = fixture.config["mcp_servers"]["tyche"]["args"]
            tools = LabTools(Path(arguments[arguments.index("--run-file") + 1]), float(arguments[-1]))
            fixture.research.append(tools)
            program = fixture.program()
            command = next(program)
            while True:
                result = tools.call(*command)
                try:
                    command = program.send(result)
                except StopIteration:
                    break
            fixture.after_program(tools)
            if fixture.mode == "partial_timeout":
                raise subprocess.TimeoutExpired(self.command, timeout)
            if fixture.mode == "partial_error":
                self.returncode = 1
            if fixture.mode == "tamper":
                fixture.output.write_text('{"companies": []}')
            return 0

    monkeypatch.setattr(runtime.subprocess, "Popen", Process)
    return fixture


def test_trigger_returns_reviewed_checkpoint_with_codex_configuration(lab):
    from harness import run_icp

    rows = run_icp(ICP)
    assert json.loads(lab.output.read_text()) == {"companies": rows}
    assert len(rows) == 1
    assert rows[0]["contact"]["email_source"] == {
        "provider": "harvestapi", "tool": "harvestapi_get_profile", "record_id": "profile-123"}
    assert rows[0]["intent_signals"][0]["matched_icp_signal"] == 0
    assert lab.sessions == [{"model": "openai/gpt-5.6-luna", "reasoning_effort": "xhigh"}]
    assert "service_tier" not in lab.config
    assert lab.config["mcp_servers"]["tyche"]["required"]
    assert "PYTHONPATH" in lab.config["mcp_servers"]["tyche"]["env_vars"]
    assert "model_catalog_json" not in lab.config  # retain native Codex model behavior
    assert "features.image_generation=false" in lab.processes[0].command
    assert "agents.enabled=false" in lab.processes[0].command
    assert "features.multi_agent_v2=false" in lab.processes[0].command
    assert lab.config["model_auto_compact_token_limit"] == 16000
    assert lab.session_closed and len(lab.processes) == 1
    assert lab.processes[0].command[0] == "/usr/local/bin/codex"
    assert not (lab.research[0].research.path.parent / "leads.xlsx").exists()
    ledger = budget_guard.load_ledger(lab.research[0].research.path)
    assert ledger["calls"] and all(call["actual_credits"] is not None for call in ledger["calls"].values())
    with pytest.raises(ValueError, match="delivered"):
        lab.research[0].call("tyche_review", {})
    with pytest.raises(ValueError, match="initialized"):
        lab.research[0].call("tyche_start", {})


@pytest.mark.parametrize("mode,error", [("prose", ValueError), ("tamper", ValueError), ("timeout", subprocess.TimeoutExpired)])
def test_failed_or_fabricated_completion_never_returns_leads(lab, mode, error):
    lab.mode = mode
    with pytest.raises(error):
        runtime.run(ICP)
    assert len(lab.processes) == 1 and lab.session_closed
    assert (lab.processes[0].run_dir / "failure.json").exists()


def test_stale_evidence_and_wrong_profile_email_block_delivery(lab, monkeypatch):
    runtime.run(ICP)
    run_file = lab.research[0].research.path
    import linkedin_receipts
    original = linkedin_receipts._saved_profile

    def altered(*args, **kwargs):
        profile = original(*args, **kwargs)
        if args[3] == "in":
            profile["emails"] = [{"email": "someone-else@example.com"}]
        return profile

    with monkeypatch.context() as patch:
        patch.setattr(linkedin_receipts, "_saved_profile", altered)
        with pytest.raises(ValueError, match="absent from the selected"):
            companies(run_file, ICP)
    document = json.loads(run_file.read_text())
    document["accepted"][0]["intent_details"] += " Changed after approval."
    run_file.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="current final evidence review"):
        companies(run_file, ICP)


def test_lab_only_guard_blocks_local_execution_before_starting(monkeypatch):
    monkeypatch.setattr(runtime, "ROOT", Path("/tmp/local-tyche"))
    with pytest.raises(RuntimeError, match="only in the Leadpoet lab"):
        runtime.run(ICP)


def test_generated_primary_bonus_order_and_age_limits():
    extra = {"intent_signal": "Opened a distribution center", "max_age_days": 30}
    request = request_for({**ICP, "bonus_intents": ICP["bonus_intents"] + [extra]}, 2, 2640)
    assert [s["importance"] for s in request["buying_signals"]] == ["required", "preferred", "preferred"]
    assert [s["max_age_days"] for s in request["buying_signals"]] == [365, 90, 30]
    assert [s["kind"] for s in request["buying_signals"]] == ["arena_signal_0", "arena_signal_1", "arena_signal_2"]
    assert json.loads(request["original_text"])["contact_geography"] == ICP["contact_geography"]
    assert request["signal_match_mode"] == "all"


def test_advertised_mcp_contract_fits_pr198_structural_bounds():
    from tyche_tools import serve

    incoming = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n')
    outgoing = io.StringIO()
    serve(SimpleNamespace(), incoming, outgoing, tools=LAB_TOOLS)
    tools = json.loads(outgoing.getvalue())["result"]["tools"]
    assert {t["name"] for t in tools} == {"tyche_lookup", "tyche_review", "tyche_inspect", "tyche_finish", "tyche_checkpoint"}
    request = {"model": runtime.MODEL, "input": "Research", "tools": [
        {"type": "function", "name": t["name"], "parameters": t["inputSchema"]} for t in tools]}

    def bounds(value, depth=0):
        assert depth <= 12
        if isinstance(value, dict):
            assert len(value) <= 64
            for child in value.values():
                bounds(child, depth + 1)
        elif isinstance(value, list):
            assert len(value) <= 128
            for child in value:
                bounds(child, depth + 1)
    bounds(request)
    assert len(json.dumps(request).encode()) < 1_000_000
    assert len(runtime.instructions()) < 32000
    assert "web" not in LAB_TOOLS["tyche_review"][1]["properties"]
    preview = model_result({"status": "review_required", "review_ref": "abc", "text": "x" * 40000})
    assert preview["truncated"] and preview["review_ref"] == "abc"
    assert "incomplete" in preview["next"]


def test_provider_deadlines_quotas_and_no_model_fallback(tmp_path):
    broker = Broker(tmp_path / "missing.sock", time.monotonic() - 1)
    args = {"tool": "harvestapi_get_company", "payload": {}}
    with pytest.raises(BrokerRefusal, match="deadline"):
        broker.request("deepline.execute", args)
    broker.deadline = time.monotonic() + 30
    broker.calls = 30
    with pytest.raises(BrokerRefusal, match="quota"):
        broker.request("deepline.execute", args)
    for operation in ("openrouter.chat", "openrouter.responses"):
        with pytest.raises(ValueError, match="Unsupported"):
            broker.request(operation, {})
    broker.calls = 0
    tools = ResearchTools(tmp_path / "research/results.json", execute=broker.execute)
    tools.start(request=request_for(ICP, 1, 20), max_usd=.5)
    failed = tools.call("tyche_lookup", lookup("harvestapi_get_company", {"url": COMPANY_URL}))
    assert failed["lookups"][0]["status"] == "timeout"
    assert all(call["actual_credits"] is None for call in budget_guard.load_ledger(tools.path)["calls"].values())
    with pytest.raises(BrokerRefusal, match="blocked_after_uncertain"):
        broker.request("deepline.execute", args)


def test_trickled_response_uses_one_absolute_wait_limit(monkeypatch):
    from tyche_arena import broker

    clock = iter([0.0, 0.4, 0.8, 1.2])
    monkeypatch.setattr(broker.time, "monotonic", lambda: next(clock))
    connection = SimpleNamespace(settimeout=lambda value: None, recv=lambda size: b"x")
    with pytest.raises(TimeoutError, match="wait limit"):
        Broker._receive(connection, 4, deadline=1.0)


def test_connect_time_does_not_extend_the_provider_send_deadline(monkeypatch):
    from tyche_arena import broker

    now = [0.0]
    monkeypatch.setattr(broker.time, "monotonic", lambda: now[0])

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def settimeout(self, value):
            self.timeout = value

        def connect(self, path):
            now[0] = 120.0

        def sendall(self, frame):
            assert self.timeout == 5.0
            raise TimeoutError("fixture send exceeded remaining time")

    monkeypatch.setattr(broker.socket, "socket", lambda *args: Connection())
    with pytest.raises(BrokerError, match="do not retry"):
        Broker("/tmp/fixture.sock", 1000).request("deepline.execute", {"tool": "exa_search", "payload": {}})


def test_runtime_requires_the_pinned_lab_helper(monkeypatch):
    monkeypatch.setattr(runtime, "ROOT", Path("/agent/source"))
    monkeypatch.setattr(Path, "is_socket", lambda self: True)
    monkeypatch.setattr(runtime.os, "access", lambda *args: True)
    for name in ("LAB_ARENA_WORKER_SOCKET", "LAB_ARENA_WEB_EGRESS_SOCKET"):
        monkeypatch.setenv(name, "/run/lab_arena/" + name + ".sock")
    monkeypatch.setenv("LAB_ARENA_OUTPUT_PATH", "/output/companies.json")
    helper = SimpleNamespace(__file__="/agent/lab_arena_codex.py", CODEX_VERSION=runtime.CODEX_VERSION,
                             CODEX_BINARY="/usr/local/bin/codex", session=lambda **kwargs: None)
    checkpoint = SimpleNamespace(__file__="/agent/lab_arena_checkpoint.py")
    monkeypatch.setattr(runtime.importlib, "import_module", lambda name: helper if name == "lab_arena_codex" else checkpoint)
    assert runtime.require_lab() is helper
    helper.CODEX_VERSION = "different-version"
    with pytest.raises(RuntimeError, match="unavailable"):
        runtime.require_lab()
    helper.CODEX_VERSION = runtime.CODEX_VERSION
    helper.__file__ = "/tmp/copied-lab-helper.py"
    with pytest.raises(RuntimeError, match="unavailable"):
        runtime.require_lab()


def test_mcp_exits_when_its_parent_dies_despite_separate_process_group(tmp_path):
    # Real Python processes, no Codex/Leadpoet/network. Reproduce Codex 0.154.0's
    # process_group(0) MCP launcher, then abruptly kill the owning process.
    marker = tmp_path / "child.json"
    child_code = "\n".join([
        "import json, os, sys, threading, time",
        "from pathlib import Path",
        "from tyche_arena.mcp import watch_parent",
        "parent = os.getppid()",
        "threading.Thread(target=watch_parent, args=(parent, threading.Event()), daemon=True).start()",
        "Path(sys.argv[1]).write_text(json.dumps({'pid': os.getpid(), 'pgid': os.getpgrp(), 'parent': parent}))",
        "time.sleep(90)",
    ])
    parent_code = "\n".join([
        "import subprocess, sys, time",
        "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2]], start_new_session=True)",
        "time.sleep(90)",
    ])
    environment = {**os.environ, "PYTHONPATH": str(ROOT)}
    parent = subprocess.Popen([sys.executable, "-c", parent_code, child_code, str(marker)],
                              env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    child = None
    try:
        # Startup is not the behavior under test; allow a loaded CI/desktop
        # host to initialize Python within the production MCP startup budget.
        deadline = time.monotonic() + 30
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert marker.exists(), "fixture child did not initialize"
        child = json.loads(marker.read_text())
        assert child["parent"] == parent.pid and child["pgid"] == child["pid"]
        parent.kill()
        # The orphan holds both inherited pipes until watch_parent exits it.
        stdout, stderr = parent.communicate(timeout=5)
        assert not stderr, stderr.decode()
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=5)
        if child:
            try:
                os.kill(child["pid"], signal.SIGKILL)
            except ProcessLookupError:
                pass
        parent.stdout.close()
        parent.stderr.close()


def test_checkpoint_failure_never_reports_delivery(lab, monkeypatch):
    def failed_write(rows):
        raise OSError("fixture output mount is unavailable")

    monkeypatch.setattr(sys.modules["lab_arena_checkpoint"], "write", failed_write)
    with pytest.raises(OSError, match="output mount"):
        runtime.run(ICP)
    assert not lab.output.exists()
    assert lab.research[0].delivered is False


def test_final_review_has_time_for_two_brokered_model_responses():
    # PR #198's complete model socket wait can be 185s per turn. Final packet
    # review and approval must both fit after research stops on a shortfall.
    assert runtime.RUN_SECONDS - runtime.RESEARCH_SECONDS >= 2 * 185 + 30


@pytest.mark.parametrize("mode", ["partial_timeout", "partial_error", "deliver"])
def test_partial_checkpoint_survives_unfinished_research(lab, monkeypatch, mode):
    from harness import run_icp

    monkeypatch.setenv("LAB_ARENA_COMPANY_LIMIT", "5")
    lab.program = lambda: scenario("tyche_checkpoint")
    lab.mode = mode

    def continue_research(tools):
        assert not tools.delivered
        before = json.loads(lab.output.read_text())
        # A real tool review creates a new, unfinished candidate after delivery.
        tools.call("tyche_review", {"companies": [{"target": "unfinished.example.com",
            "decision": "hold_account", "reason": "Still checking the required signal"}]})
        saved = json.loads(tools.research.path.read_text())
        assert saved["request"]["target_count"] == 5
        assert saved["unresolved"]
        assert tools.call("tyche_finish", {})["delivery_allowed"] is False
        assert json.loads(lab.output.read_text()) == before
        # An interrupted subsequent reservation must not discard the checkpoint.
        with budget_guard.transaction(tools.research.path.with_name("results.json.budget.json")) as ledger:
            ledger["blocked"] = "Later call billing is uncertain; do not retry"

    lab.after_program = continue_research
    rows = run_icp(ICP)
    assert len(rows) == 1
    assert json.loads(lab.output.read_text()) == {"companies": rows}
    assert json.loads(lab.research[0].research.path.read_text())["request"]["target_count"] == 5
    assert (lab.processes[0].run_dir / "failure.json").exists() == (mode != "deliver")
    assert len(lab.processes) == 1 and lab.session_closed


def test_accepted_but_unreviewed_leads_are_not_checkpointed(lab, monkeypatch):
    monkeypatch.setenv("LAB_ARENA_COMPANY_LIMIT", "5")
    lab.program = lambda: scenario(None)
    lab.mode = "partial_timeout"
    with pytest.raises(subprocess.TimeoutExpired):
        runtime.run(ICP)
    assert not lab.output.exists()
    assert len(json.loads(lab.research[0].research.path.read_text())["accepted"]) == 1


def test_checkpoint_needs_current_review_and_can_be_updated(lab, monkeypatch):
    monkeypatch.setenv("LAB_ARENA_COMPANY_LIMIT", "5")
    lab.program = lambda: scenario(None)

    def approve_incrementally(tools):
        packet = tools.call("tyche_checkpoint", {})
        assert packet["status"] == "review_required"
        assert packet["companies"] and packet["sources"]
        assert not lab.output.exists()
        changed = PARAGRAPH + " Better coordination may support fulfillment reliability."
        tools.call("tyche_review", {"companies": [{"target": "example.com", "decision": "accept",
            "reason": "Clarified the conditional relevance", "intent_details": changed}]})
        fresh = tools.call("tyche_checkpoint", {"review_ref": packet["review_ref"]})
        assert fresh["status"] == "review_required" and fresh["review_ref"] != packet["review_ref"]
        assert not lab.output.exists()
        saved = tools.call("tyche_checkpoint", {"review_ref": fresh["review_ref"]})
        assert saved["checkpoint_saved"] and not saved["delivery_allowed"]
        assert json.loads(lab.output.read_text())["companies"][0]["intent_details"] == changed
        tools.call("tyche_review", {"companies": [{"target": "example.com", "decision": "accept",
            "reason": "Use the concise reviewed description", "intent_details": PARAGRAPH}]})
        newer = tools.call("tyche_checkpoint", {})
        assert newer["status"] == "review_required"
        # Unapproved revisions do not overwrite a published checkpoint.
        assert json.loads(lab.output.read_text())["companies"][0]["intent_details"] == changed
        assert tools.call("tyche_checkpoint", {"review_ref": newer["review_ref"]})["checkpoint_saved"]

    lab.after_program = approve_incrementally
    assert runtime.run(ICP)[0]["intent_details"] == PARAGRAPH


@pytest.mark.parametrize("corruption", ["contact", "evidence", "provenance"])
def test_partial_checkpoint_preserves_qualification_and_contact_gates(lab, monkeypatch, corruption):
    monkeypatch.setenv("LAB_ARENA_COMPANY_LIMIT", "5")
    lab.program = lambda: scenario(None)

    def corrupt(tools):
        path = tools.research.path
        saved = json.loads(path.read_text())
        row = saved["accepted"][0]
        if corruption == "contact":
            row["primary_contact"].pop("email")
        elif corruption == "evidence":
            row["qualification_checks"][0]["status"] = "unknown"
        else:
            row["primary_contact"]["email"] = "someone-else@example.com"
        path.write_text(json.dumps(saved))
        result = tools.call("tyche_checkpoint", {})
        assert result["status"] == "needs_repair", result
        assert not result["checkpoint_saved"]
        assert not lab.output.exists()

    lab.after_program = corrupt
    with pytest.raises(ValueError, match="No reviewed TYCHE checkpoint"):
        runtime.run(ICP)


def test_failed_partial_checkpoint_keeps_previous_host_output(lab, monkeypatch):
    monkeypatch.setenv("LAB_ARENA_COMPANY_LIMIT", "5")
    lab.program = lambda: scenario("tyche_checkpoint")

    def failed_update(tools):
        previous = lab.output.read_bytes()
        local_previous = tools.research.path.with_name("companies.json").read_bytes()
        snapshot = tools.research.path.with_name("checkpoint-results.json").read_bytes()
        tools.call("tyche_review", {"companies": [{"target": "example.com", "decision": "accept",
            "reason": "Clarified the relevance", "intent_details": PARAGRAPH + " Better coordination may help."}]})
        packet = tools.call("tyche_checkpoint", {})
        def fail(rows):
            raise OSError("fixture output mount is unavailable")
        tools.write_checkpoint = fail
        with pytest.raises(OSError, match="output mount"):
            tools.call("tyche_checkpoint", {"review_ref": packet["review_ref"]})
        assert lab.output.read_bytes() == previous
        assert tools.research.path.with_name("companies.json").read_bytes() == local_previous
        assert tools.research.path.with_name("checkpoint-results.json").read_bytes() == snapshot
        assert not tools.delivered

    lab.after_program = failed_update
    assert len(runtime.run(ICP)) == 1
