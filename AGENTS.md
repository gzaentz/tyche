# TYCHE request routing

Use the user's intent to choose the execution path for this repository.

## Sourcing requests

Requests to source leads, find matching companies and buyers, build an ICP
account list, or run a real sourcing test use the isolated TYCHE launcher by
default. Ordinary wording such as "source 10 leads for this ICP" is sufficient.
Do not perform the sourcing in the outer conversation or use global sourcing
skills to implement it. An explicit user request to use another execution path
takes precedence.

Before launching, check whether this process is already isolated:

```bash
python3 -c 'import os; print(os.environ.get("TYCHE_ISOLATED_RUN", "0"))'
```

- If it prints `1`, or the developer context says this is already the isolated
  TYCHE runtime, execute the request with the project-local `lead-sourcing`
  skill. Never launch `scripts/codex_tyche.py` again from that runtime.
- Otherwise, create a unique run directory under `reports/` and write a UTF-8
  `request.txt` containing the user's sourcing request, relevant user-provided
  constraints and authorization from this conversation, and the run directory
  for saved artifacts. Preserve the requested ICP, target, roles, fields,
  exclusions, budget and explicit time limit. When omitted, leave defaults to
  the local sourcing skill. Do not copy global instructions, skill contents,
  credentials, or the entire conversation into the request.
- Launch it from the host terminal with a separate argument for the request-file
  path:

  ```bash
  python3 scripts/codex_tyche.py --exec-file reports/<run-id>/request.txt
  ```

For Codex terminal tools, request host execution with
`sandbox_permissions: "require_escalated"` when that option is available, and
set `workdir` to this repository. Use this path for the first launch and any
continuations; do not first attempt the job inside the outer task's command
sandbox. The nested Codex process needs to create its own network-proxy
listeners, which the outer sandbox can block. Follow the tool's normal approval
review. If host execution is unavailable or denied, report that specific
blocker and preserve the saved request; do not bypass the restriction through
another tool or silently source in the outer conversation.

Host execution changes where the launcher starts. Keep the launcher's temporary
profile, local-only instruction/skill checks, Luna/xhigh/Fast selection, and the
child's workspace-write sandbox and network policy. Do not add sandbox-bypass
flags or change network allowlists to work around a startup failure.

Use a file-writing tool or properly quoted heredoc to create the request file.
Never interpolate the user's request into shell command text. Follow README.md
for provider environment setup without printing credentials. Use a running
tool session to follow progress; do not launch a duplicate job while it runs.

When the process returns, inspect the saved artifacts and use the full strict
validator before describing the run as delivered. Process exit alone is not
success. If more sourcing is needed, pass the saved run path, ledger, original
authorization and remaining budget, plus validation errors, to another isolated
invocation. Continue that run's accounting; never reset its budget or repeat an
uncertain billed call. Treat a requested fresh comparison run as a separate run
with its own explicitly or previously authorized test budget.

## Development, questions and checks

Keep code changes, explanations, hypothetical examples, and read-only review of
existing results in the normal conversation. Quoted sourcing examples in a
question or documentation are not instructions to run a job.

- For "change X, then source/test Y", make and check the code change first,
  then route the requested sourcing test through the launcher.
- For a setup check, use `python3 scripts/codex_tyche.py --check` through the
  same host-terminal path. It checks session startup with the sourcing network
  configuration, without starting a model turn or making provider calls.
- For a quick model smoke test with no sourcing-provider calls, use
  `python3 scripts/codex_tyche.py --smoke` through that host-terminal path.

These instructions route work; they do not change system instructions,
permissions, provider budgets, or the sourcing/output contracts. See
`docs/codex-isolated-testing.md` for the launcher lifecycle and limitations.
