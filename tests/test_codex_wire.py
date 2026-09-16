"""Optional native Codex audit. No Leadpoet execution or paid requests.

The original PR #198 incompatibility at 2558d4bc is an explicit expected failure,
not validation of the updated upstream protocol. See docs/leadpoet-codex-audit.md.
"""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tyche_arena import runtime


def unsupported_fields(body):
    """Inspect the relevant closed PR #198 fields; not a full gateway validator."""
    errors = []
    if set(body.get("reasoning", {})) - {"effort", "summary"}:
        errors.append("reasoning.context")
    for item in body.get("input", []):
        if item.get("type", "message") not in {"message", "function_call", "function_call_output",
                "custom_tool_call", "custom_tool_call_output", "reasoning"}:
            errors.append("input." + str(item.get("type")))
        if item.get("type") in {"function_call", "custom_tool_call"} and "namespace" in item:
            errors.append("input.call.namespace")
        if item.get("type") in {"function_call_output", "custom_tool_call_output"} and not isinstance(item.get("output"), str):
            errors.append("input.call_output.content_array")
    for tool in body.get("tools") or []:
        if tool.get("type") not in {"function", "custom"} or "defer_loading" in tool:
            errors.append("tools." + str(tool.get("type")))
    if nesting_depth(body) > 12:
        errors.append("operation.depth>12")
    return errors


def nesting_depth(value):
    if isinstance(value, dict):
        return max((1 + nesting_depth(child) for child in value.values()), default=0)
    if isinstance(value, list):
        return max((1 + nesting_depth(child) for child in value), default=0)
    return 0


@pytest.mark.skipif(not os.environ.get("TYCHE_TEST_CODEX_BINARY"),
                    reason="set TYCHE_TEST_CODEX_BINARY to Codex 0.154.0 for the offline wire audit")
@pytest.mark.parametrize("admit_native,compact", [(False, False), (True, False), (True, True)])
def test_native_codex_lab_boundary(tmp_path, monkeypatch, admit_native, compact):
    binary = os.environ["TYCHE_TEST_CODEX_BINARY"]
    assert subprocess.check_output([binary, "--version"], text=True).strip() == "codex-cli 0.154.0"
    assert Path(binary).resolve().with_name("codex-code-mode-host").is_file(), "Install the full Codex package, including its code-mode companion"
    observed = []
    calls = []
    compactions = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            size = int(self.headers["Content-Length"])
            if size > 1_000_000:
                self.send_error(413)
                return
            body = json.loads(self.rfile.read(size))
            (tmp_path / ("request-" + str(len(observed)) + ".json")).write_text(json.dumps(body, indent=2))
            observed.append({"path": self.path, "body": body})
            errors = unsupported_fields(body)
            if not admit_native:
                # Stop before inference even if future configuration fits this
                # subset. This case only audits the native request boundary.
                payload = json.dumps({"error": {"message": "; ".join(errors) or "offline audit complete"}}).encode()
                self.send_response(400)
            else:
                # A hypothetical native-compatible upstream, not PR #198.
                # Replies are scripted; no model inference occurs.
                if not any(item.get("type") == "additional_tools" and item.get("tools") for item in body["input"]):
                    compactions.append(True)
                    output = [{"type": "message", "id": "compact-msg", "role": "assistant", "status": "completed",
                               "content": [{"type": "output_text", "text": "Continue the offline tool check.", "annotations": []}]}]
                elif len(calls) < 2:
                    calls.append(len(calls) + 1)
                    code = "const t = ALL_TOOLS.find(t => t.name.endsWith('tyche_inspect')); if (!t) throw new Error('TYCHE MCP tool missing'); text(await tools[t.name]({}));"
                    output = [{"type": "custom_tool_call", "id": "ct-" + str(len(calls)),
                               "call_id": "call-" + str(len(calls)), "name": "exec", "namespace": "functions",
                               "input": code, "status": "completed"}]
                else:
                    output = [{"type": "message", "id": "final-msg", "role": "assistant", "status": "completed",
                               "content": [{"type": "output_text", "text": "TYCHE_CODEX_WIRE_OK", "annotations": []}]}]
                document = {"id": "resp-" + str(len(observed)), "object": "response", "created_at": 1789488000,
                            "model": runtime.MODEL, "status": "completed", "output": output,
                            "usage": {"input_tokens": 17000 if compact and len(observed) == 1 else 100,
                                      "output_tokens": 20, "total_tokens": 17020 if compact and len(observed) == 1 else 120}}
                events = [("response.created", {"response": {**document, "status": "in_progress", "output": []}})]
                for index, item in enumerate(output):
                    events.append(("response.output_item.added", {"output_index": index, "item": item}))
                    if item["type"] == "message":
                        events.append(("response.output_text.delta", {"item_id": item["id"], "output_index": index,
                                                                     "content_index": 0, "delta": item["content"][0]["text"]}))
                    events.append(("response.output_item.done", {"output_index": index, "item": item}))
                events.append(("response.completed", {"response": document}))
                payload = "".join("event: " + kind + "\ndata: " + json.dumps({"type": kind, "sequence_number": n, **data}) + "\n\n"
                                  for n, (kind, data) in enumerate(events)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    fixture = tmp_path / "mcp_fixture.py"
    fixture.write_text("\n".join([
        "import os, threading",
        "from tyche_arena.mcp import LAB_TOOLS, watch_parent",
        "from tyche_tools import serve",
        "threading.Thread(target=watch_parent, args=(os.getppid(), threading.Event()), daemon=True).start()",
        "class Fixture:",
        "    def call(self, name, arguments):",
        "        assert name == 'tyche_inspect' and arguments == {}",
        "        return {'status': 'TYCHE_OFFLINE_TOOL_OK'}",
        "serve(Fixture(), tools=LAB_TOOLS)",
    ]))
    config = ["model = " + json.dumps(runtime.MODEL), 'model_reasoning_effort = "xhigh"',
              'model_provider = "fixture"', 'approval_policy = "never"', 'sandbox_mode = "read-only"',
              'web_search = "disabled"', 'check_for_update_on_startup = false',
              '[features]', 'apps = false', 'multi_agent = false', 'shell_snapshot = false',
              'enable_request_compression = false', '[model_providers.fixture]', 'name = "Fixture"',
              'base_url = "http://127.0.0.1:' + str(server.server_port) + '/v1"',
              'wire_api = "responses"', 'requires_openai_auth = false', 'supports_websockets = false',
              'request_max_retries = 0', 'stream_max_retries = 0']
    (codex_home / "config.toml").write_text("\n".join(config) + "\n")
    environment = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(codex_home), "CODEX_HOME": str(codex_home),
                   "PYTHONPATH": str(ROOT), "LANG": "en_US.UTF-8", "NO_PROXY": "127.0.0.1,localhost"}

    @contextmanager
    def session(**kwargs):
        assert kwargs == {"model": runtime.MODEL, "reasoning_effort": runtime.REASONING_EFFORT}
        yield environment

    # Replace only the host-bound MCP startup guard, which intentionally refuses
    # a local invocation. Keep the production launch, CLI flags and LAB_TOOLS.
    original_configuration = runtime.tool_configuration

    def fixture_configuration(run_file, deadline):
        return original_configuration(run_file, deadline).replace(
            json.dumps(["-B", "-m", "tyche_arena.mcp", "--run-file", str(run_file), "--deadline", str(deadline)]),
            json.dumps([str(fixture)]))

    monkeypatch.setattr(runtime, "tool_configuration", fixture_configuration)
    try:
        if admit_native:
            runtime.launch(SimpleNamespace(session=session, CODEX_BINARY=binary), tmp_path, 0, 40)
        else:
            with pytest.raises(RuntimeError, match="Lab Codex exited"):
                runtime.launch(SimpleNamespace(session=session, CODEX_BINARY=binary), tmp_path, 0, 40)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    log = (tmp_path / "codex.log").read_text()
    assert observed, log
    assert "Code Mode is unavailable" not in log, log
    assert all(row["path"] == "/v1/responses" for row in observed)
    body = observed[0]["body"]
    additional = [item for item in body["input"] if item.get("type") == "additional_tools"]
    tools = [tool for item in additional for tool in item.get("tools", [])] + (body.get("tools") or [])
    namespaces = {tool["name"]: [child["name"] for child in tool.get("tools", [])]
                  for tool in tools if tool.get("type") == "namespace"}
    assert "multi_agent_v1" not in namespaces and "collaboration" not in namespaces
    assert "image_gen" not in namespaces
    # Luna exposes MCP tools through the code-mode exec schema/description.
    encoded_tools = json.dumps(body)
    for name in ("tyche_inspect", "tyche_lookup", "tyche_review", "tyche_finish"):
        assert name in encoded_tools, "MCP tool did not reach the model request: " + name
    summary = {"codex": runtime.CODEX_VERSION, "model": body["model"], "reasoning": body.get("reasoning"),
               "input_types": sorted({item.get("type", "message") for item in body["input"]}),
               "tool_namespaces": namespaces, "request_bytes": len(json.dumps(body).encode()),
               "operation_depth": nesting_depth(body),
               "unsupported_fields": sorted({error for row in observed for error in unsupported_fields(row["body"])})}
    (tmp_path / "wire-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    errors = unsupported_fields(body)
    if admit_native:
        assert (tmp_path / "final.txt").read_text().strip() == "TYCHE_CODEX_WIRE_OK"
        assert len(calls) == 2
        assert sum(any(item.get("type") == "custom_tool_call_output" and "TYCHE_OFFLINE_TOOL_OK" in json.dumps(item)
                       for item in row["body"]["input"]) for row in observed[1:]) >= 2, "Native Codex did not receive both MCP results"
        if compact:
            assert compactions, "Codex did not compact the context"
        else:
            assert len(observed) == 3
    elif errors:
        assert set(errors) == {"reasoning.context", "input.additional_tools", "operation.depth>12"}, summary
        pytest.xfail("PR #198 rejects native Luna: " + ", ".join(errors))
