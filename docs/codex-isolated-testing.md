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

The temporary profile and its session history are removed when the launcher
exits. Files saved in the project, including sourcing results and receipts,
remain. Start a fresh launch after editing the skill to avoid stale context.
This is a local test workflow, not the production job/recovery host.

The launcher uses the installed Codex app-server's discovery protocol. It was
checked with Codex CLI 0.153.4 and fails closed if instruction-source reporting
or skill discovery is unavailable. Existing desktop conversations already
contain their earlier context; this launcher does not clean or modify them.
