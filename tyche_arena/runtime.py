"""TYCHE's Codex research loop, hosted only by Leadpoet lab PR #198."""

import importlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time

from . import ROOT, SKILL
from .broker import Broker
from .input import request_for
from .output import companies
from research_tools import ResearchTools

MODEL = "openai/gpt-5.6-luna"
REASONING_EFFORT = "xhigh"
RESEARCH_SECONDS = 2640
RUN_SECONDS = 2670
MAX_LOG_BYTES = 64 * 1024


def require_lab():
    """Refuse local/legacy execution before starting a model or provider call."""
    if ROOT != Path("/agent/source"):
        raise RuntimeError("This harness runs only in the Leadpoet lab execute sandbox")
    for name in ("LAB_ARENA_WORKER_SOCKET", "LAB_ARENA_WEB_EGRESS_SOCKET"):
        value = os.environ.get(name, "")
        if not Path(value).is_absolute() or not Path(value).is_socket():
            raise RuntimeError("PR #198 lab socket is missing: " + name)
    if os.environ.get("LAB_ARENA_OUTPUT_PATH") != "/output/companies.json":
        raise RuntimeError("The lab output mount is missing")
    try:
        runtime = importlib.import_module("lab_arena_codex")
        checkpoint = importlib.import_module("lab_arena_checkpoint")
    except ImportError as exc:
        raise RuntimeError("Leadpoet PR #198 runtime must be installed before using this bundle") from exc
    if (Path(runtime.__file__).resolve() != Path("/agent/lab_arena_codex.py")
            or Path(checkpoint.__file__).resolve() != Path("/agent/lab_arena_checkpoint.py")
            or not callable(getattr(runtime, "session", None))
            or runtime.CODEX_BINARY != "/usr/local/bin/codex"
            or not os.access(runtime.CODEX_BINARY, os.X_OK)):
        raise RuntimeError("The host-mounted PR #198 Codex runtime is unavailable")
    return runtime


def instructions():
    skill = (SKILL / "SKILL.md").read_text().replace("(references/", "(" + str(SKILL / "references") + "/")
    return skill + "\n\n# Leadpoet lab execution context\n" + (
        "You are already the isolated TYCHE sourcing worker. Use the shared research loop above. "
        "The lab initialized the authoritative ICP, budget and deadline. Start with tyche_inspect; "
        "do not call tyche_start, alter the request, launch another Codex process, or use global skills. "
        "Use native TYCHE tools for all lookups, state changes, reviews and delivery. "
        "Read the shared references at the absolute paths above. No shell bookkeeping or direct provider calls. "
        "The lab owns isolation, credentials, model/provider costs and quotas. "
        "Hosted web search is disabled. Use catalogued Deepline research operations, such as exa_search and exa_contents. "
        "Catalog metadata is bundled; unlisted tools and ScrapingDog are unavailable in this adapter. "
        "Read original_text and requirements from tyche_inspect before research. "
        "arena_signal_0 is mandatory; later signals are optional bonuses with their own age limits. "
        "Preserve contact_geography and target_seniority for the selected contact. "
        "First review a HarvestAPI profile with main='true'. Then use its contact_ref with "
        "harvestapi_get_profile and findEmail='true', omitting main and other add-ons. "
        "Review that enriched profile as primary_contact.ref, select its returned email and apply the normal email gate. "
        "Keep the profile's provider record ID in the saved raw receipt. "
        "Company HQ country/state and stage must come from observed evidence. "
        "Intent Details must be one plain paragraph of at most 2000 characters. "
        "Begin final evidence review before the research deadline. Inspect original source passages in "
        "tyche_finish's packet; use tyche_inspect with field='evidence_review' if a view is truncated. "
        "Approve only the current review_ref. Successful tyche_finish saves and checkpoints reviewed lab JSON. "
        "For this lab run, JSON replaces the workbook, preview and local cost report. "
        "After successful delivery, end the turn immediately. Final prose is not company output. "
        "Do not claim delivery when finish is blocked; report the actual blocker."
    )


def tool_configuration(run_file, deadline):
    args = ["-B", "-m", "tyche_arena.mcp", "--run-file", str(run_file), "--deadline", str(deadline)]
    forwarded = ["PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED", "LAB_ARENA_WORKER_SOCKET",
                 "LAB_ARENA_WEB_EGRESS_SOCKET", "LAB_ARENA_OUTPUT_PATH", "LAB_ARENA_EVALUATION_DATE"]
    return ('\n[mcp_servers.tyche]\ncommand = ' + json.dumps(sys.executable)
            + '\nargs = ' + json.dumps(args) + '\ncwd = ' + json.dumps(str(run_file.parent))
            + '\nenv_vars = ' + json.dumps(forwarded)
            + '\nrequired = true\nstartup_timeout_sec = 40\ntool_timeout_sec = 180\n'
              'default_tools_approval_mode = "approve"\n')


def launch(runtime, run_dir, deadline, remaining):
    # session owns the Responses bridge and isolated provider configuration.
    # Configure MCP there, rather than relying on untrusted project config.
    with runtime.session(model=MODEL, reasoning_effort=REASONING_EFFORT) as environment:
        environment["TYCHE_ISOLATED_RUN"] = "1"
        config = Path(environment["CODEX_HOME"]) / "config.toml"
        additions = ('developer_instructions = ' + json.dumps(instructions())
                     + '\nmodel_auto_compact_token_limit = 16000\ntool_output_token_limit = 4000\n')
        config.write_text(additions + config.read_text() + tool_configuration(run_dir / "results.json", deadline))
        prompt = ("Research the authoritative saved ICP with native TYCHE tools. Start with tyche_inspect. "
                  "Finish through reviewed JSON delivery within " + str(RESEARCH_SECONDS) + " seconds.")
        # No retry/relaunch: a lost model or provider response may already bill.
        with tempfile.TemporaryFile() as incoming:
            incoming.write(prompt.encode())
            incoming.seek(0)
            process = subprocess.Popen(
                [runtime.CODEX_BINARY, "exec", "--skip-git-repo-check", "--ephemeral", "--color", "never",
                 "-C", str(run_dir), "-o", str(run_dir / "final.txt"), "-"],
                stdin=incoming, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                env=environment, start_new_session=True,
            )
            tail = bytearray()

            def drain():
                with process.stdout:
                    for block in iter(lambda: process.stdout.read(8192), b""):
                        tail.extend(block)
                        del tail[:-MAX_LOG_BYTES]

            reader = threading.Thread(target=drain, daemon=True)
            reader.start()
            try:
                process.wait(timeout=remaining)
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                reader.join(timeout=5)
                (run_dir / "codex.log").write_bytes(tail)
            if process.returncode:
                raise RuntimeError("Lab Codex exited with status " + str(process.returncode))


def run(icp):
    runtime = require_lab()
    limit = int(os.environ["LAB_ARENA_COMPANY_LIMIT"])
    if not 1 <= limit <= 5:
        raise ValueError("LAB_ARENA_COMPANY_LIMIT must be 1 through 5")
    request = request_for(icp, limit, RESEARCH_SECONDS)
    started = time.monotonic()
    run_dir = Path(tempfile.mkdtemp(prefix="tyche-arena-", dir="/tmp"))
    run_file = run_dir / "results.json"
    broker = Broker(os.environ["LAB_ARENA_WORKER_SOCKET"], started + RESEARCH_SECONDS)
    try:
        ResearchTools(run_file, execute=broker.execute).start(request=request, max_usd=0.5 * limit)
        launch(runtime, run_dir, started + RESEARCH_SECONDS, RUN_SECONDS - (time.monotonic() - started))
        # Prose/exit status alone cannot deliver. Revalidate reviewed receipts
        # against the original ICP, then require the exact saved/checkpointed JSON.
        rows = companies(run_file, icp)
        for path in (run_dir / "companies.json", Path(os.environ["LAB_ARENA_OUTPUT_PATH"])):
            if path.stat().st_size > 512 * 1024 or json.loads(path.read_text()) != {"companies": rows}:
                raise ValueError("Lab output differs from the current reviewed TYCHE records")
        return rows
    except Exception as exc:
        (run_dir / "failure.json").write_text(json.dumps({"error": type(exc).__name__, "message": str(exc)[:2000]}))
        raise
    finally:
        broker.stopped.set()
