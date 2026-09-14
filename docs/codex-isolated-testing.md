# Test TYCHE without global Codex instructions

## Ordinary requests in Codex

The repository's `AGENTS.md` routes actual lead/company sourcing requests to
the isolated launcher. In a new Codex conversation opened in this repository,
you can say "Source 10 leads for [ICP]". You do not need to mention isolation.
Code changes, questions, and reviews of existing results stay in the outer
conversation. "Change X, then source Y" runs the code change first and then
starts the isolated sourcing test.

This is instruction-based routing by Codex, not a hard-coded keyword filter or
an application hook. Existing conversations need to read the new `AGENTS.md`
or be restarted to pick up the rule. It does not apply outside this repository.
The isolated child receives `TYCHE_ISOLATED_RUN=1`; its instructions and the
launcher both prevent recursive launches.

The outer agent writes the request and relevant user constraints to a run's
`request.txt`, then calls `--exec-file`. It passes task context, not global
instructions or the whole conversation. The outer agent reviews saved outputs
before reporting success. Continuations must retain the same ledger and
remaining budget; a fresh rerun is a separate billable sourcing run.

## Host-terminal execution

Launch the wrapper from the host terminal. In Codex, use the terminal tool's
`sandbox_permissions: "require_escalated"` option when available, with this
repository as `workdir`, and follow normal approval review. Use this for the
first sourcing launch, continuations, and setup checks. The wrapper does not
request escalation itself; the outer agent chooses the terminal tool settings.
When running manually, start the command in your regular terminal.

On 2026-09-11, launching inside an outer Codex command sandbox failed with
`reserve managed loopback proxy listeners`. The same isolated launcher started
successfully from the approved host terminal, retaining its own workspace-write
sandbox and network proxy. Host execution preserves the separate temporary
profile and local-only instructions/skills. It does not require network
allowlist edits or sandbox-bypass flags.

If the current tool cannot request host execution, or approval review denies it,
report the restriction and retain the saved request. Do not route around that
decision through another tool or silently switch to sourcing in the outer chat.

## Manual launch

Keep developing normally in the Codex desktop app. Launch a separate, fresh
Codex CLI test against this same checkout:

```bash
python3 scripts/codex_tyche.py
```

Each launch creates a private temporary Codex profile. It reuses the existing
file-based Codex login and execution-policy rules, but does not import global
AGENTS.md, user configuration, plugins, apps, or memories. It discovers skills
through Codex itself, disables every skill outside this repository's
`.agents/skills`, and checks the actual loaded instruction sources before
starting. Repository instructions and `.codex/config.toml` still apply. The
launcher pins `gpt-5.6-luna` with `xhigh` (Extra High) reasoning and the `fast`
service tier (the accelerated 1.5× mode when the account exposes it).

The child disables Deepline CLI self-updates and global skill synchronization
using `DEEPLINE_NO_AUTO_UPDATE=1` and `DEEPLINE_SKIP_SKILLS_SYNC=1`. This keeps
research on the installed runtime without npm downloads during provider calls.
CLI compatibility checks remain enabled; install any required CLI update
through normal host setup before starting a new run.

Your global configuration is not edited. Codex's built-in system instructions
and managed permissions remain in force. This isolates supplied context; it is
not a filesystem security boundary preventing all possible external reads.

## Native research tools

File-backed runs register five local tools only in the temporary profile:
`tyche_start`, `tyche_lookup`, `tyche_review`, `tyche_inspect`, `tyche_finish`.
The run file comes from the launcher's request-file directory, not model input.
Provider credentials and bundled runtime paths are forwarded as environment
variables; values are never copied into the temporary config or prompt.
The launcher also supplies its start timestamp, so native run timing includes
initialization and setup. Resuming an existing run keeps its original clock.

The stdio relay advertises Codex's `codex/sandbox-state-meta` capability. On the
first tool call it starts one child through `codex sandbox --sandbox-state-json`
using that exact caller metadata. This matters: a standalone workspace sandbox
does not by itself reproduce the worker's managed network proxy. Missing or
changed metadata fails before further research. The project network allowlist
and worker settings remain unchanged. `--check` verifies discovery; `--smoke`
also exercises the sandboxed read-only tool call without sourcing providers.

The child uses the existing helpers, one ledger and three provider dispatch
slots shared by native calls. Research decisions and source exhaustion remain
explicit LLM inputs. Built-in web search stays available separately; one review
call saves its observed evidence alongside findings, without a plan-file cycle.

Closing the connection cancels queued requests and lets dispatched work save
receipts where possible. Forced process termination can still leave an uncertain
provider outcome; retain its reservation and reconcile instead of retrying.
No daemon survives intentionally between runs. Legacy interactive/`--exec`
sessions keep the CLI helper path because they do not supply a bound run file.

## Checks

Check isolation and initialize a session with the same project sandbox and
network settings used for sourcing, including its network proxy. Run this from
the host terminal; it starts no model turn and makes no sourcing-provider calls:

```bash
python3 scripts/codex_tyche.py --check
```

After the same startup check, run one read-only model smoke test that reads the
local skill and reports its deliverables, without sourcing or provider calls:

```bash
python3 scripts/codex_tyche.py --smoke
```

`--check` verifies session initialization, not model-service connectivity or
provider credentials. `--smoke` additionally verifies a model response; its
model turn runs read-only with command networking disabled.

Run a supplied request without the terminal UI:

```bash
python3 scripts/codex_tyche.py --exec 'Use $lead-sourcing. <request and explicit budget>'
```

For a multiline request saved in a file:

```bash
python3 scripts/codex_tyche.py --exec-file reports/<run-id>/request.txt
```

Load provider environment variables in the launching shell as described in
README.md. This launcher does not source `.env` automatically. Paid sourcing
still requires the existing budgets and adapters. With a ChatGPT Codex login,
model calls use that login's allowance; provider charges remain separate.

For `--exec-file`, the launcher saves `model-usage/<invocation-id>.json` beside
the request file. It reads per-response usage from the worker's session journal
inside the existing temporary profile and reconciles it with the final CLI JSON
totals. Only model/request identities, timestamps, numeric input, cached input,
cache writes, output and reasoning output, and dated pricing are retained in
the receipt. Prompts and tool output are not copied into it. Pricing is applied
per response so cumulative input does not accidentally trigger long-context
rates. Reasoning tokens are already included in output and are not billed twice.

Each continuation gets its own receipt. Failed or interrupted invocations retain
observed usage but remain incomplete when final totals cannot be reconciled.
Capture failures do not interrupt the worker; afterward the launcher exits
nonzero and marks cost incomplete. This does not invalidate saved leads or
authorize rerunning paid calls.

The launcher automatically writes `run-costs.json` from `results.json` and every
invocation receipt before deleting the temporary profile. Missing results or
usage remain explicitly incomplete, never free. The launcher uses a temporary
session journal for this path instead of `--ephemeral`; sandbox, network, local
context isolation and cleanup are unchanged. Other execution modes remain
ephemeral. An already running invocation cannot gain retrospective capture.
The old `tokens used` footer excludes cached input; it is not a pricing breakdown.

To recalculate the report after provider billing is reconciled:

```bash
python3 scripts/run_costs.py reports/<run-id>/results.json
```

The scope is only the TYCHE run: its provider calls and sourcing workers,
including retries and continuations. Outer chat, monitoring and development
costs are excluded and must not be supplied to this report. Missing worker
usage makes the combined estimate and per-lead cost null, while preserving the
known subtotal. Provider confirmed/maximum figures retain unsettled reservations.
A complete Standard API-equivalent calculation has status `calculated`; known
bounds use `estimated_range`; missing components use `incomplete`. These statuses
refer to the Standard equivalent, not actual billing. Fast/priority premiums,
hosted-tool fees and subscription allocation are not priced; do not label the
result an actual full-cost invoice. Actual per-run billed dollars require billing
records from the account/provider; a ChatGPT token journal does not supply them.
Historical runs without model receipts cannot be reconstructed from token
totals alone. Preserve the original reports and add a separate cost audit.

When a provider response has no billing fields, its outcome alone cannot settle
the charge. Deepline's read-only `billing usage --limit 50 --json` can supply the
final `charge_state`, credits and request IDs. Match these to saved provider
`job_id` values. Reconciliation entries can combine several `chargeGroupIds`;
count a group once and require every member to belong to the run. An explicit
`free` entry with zero credits settles a no-result request at zero. An absent
entry remains unknown. This ledger reconciliation is separate from automatic
worker usage capture; never rerun a paid request to discover its bill.

The temporary profile and its session history are removed when the launcher
exits. Files saved in the project, including sourcing results and receipts,
remain. Start a fresh launch after editing the skill to avoid stale context.
This is a local test workflow, not the production job/recovery host.

The launcher uses the installed Codex app-server's discovery protocol. It was
checked with Codex CLI 0.154.0-alpha.6.2 and fails closed if instruction-source reporting
or skill discovery is unavailable. Existing desktop conversations already
contain their earlier context; this launcher does not clean or modify them.

## Workbook finalization and usage reconciliation

The launcher supplies `TYCHE_WORKSPACE_NODE`, `TYCHE_WORKSPACE_NODE_MODULES`
and `TYCHE_WORKSPACE_PYTHON` from the installed desktop bundle when available,
preserving explicit environment overrides. Other hosts configure these paths
once; runtime dependencies are never downloaded during research. Finalize saved
reviewed results with:

```bash
node .agents/skills/lead-sourcing/scripts/export_xlsx.mjs reports/<run-id>/results.json
```

It uses full strict validation, verifies the
exported workbook's lead/source values, and saves validation, inspection and PNG
preview files beside the workbook. The preview still requires visual review.

Model receipts retain numeric usage, response identities and explicit
`compacted.compaction_response_id` linkage from the isolated worker journal.
If the CLI excludes linked compaction responses, reconciliation compares the
ordinary responses to its total while pricing **all** captured responses. No
response is excluded based on a guessed token difference. Missing linkage,
missing usage, or unexplained differences remain incomplete. Private compaction
messages and replacement histories are not retained. API-equivalent estimates
remain distinct from actual model billing.
