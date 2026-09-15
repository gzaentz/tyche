# TYCHE in the Leadpoet lab

This bundle implements `harness.run_icp(icp) -> list[dict]` for the Codex lab
runtime in [Leadpoet PR #198](https://github.com/leadpoet/leadpoet/pull/198).
Codex drives TYCHE's existing research tools and source-review workflow. The lab
provides the executable, isolation, model transport, credentials and budgets.
There is no additional model SDK, agent framework or running service to deploy.
The local TYCHE launcher and its workbook delivery remain unchanged.

## Execution

```text
Lab calls harness.run_icp(icp)
  → create isolated request, ledger and receipts under /tmp
  → lab_arena_codex.session → /usr/local/bin/codex exec
  → native TYCHE MCP tools → lab worker → Deepline
  → final evidence review → strict validation → JSON checkpoint
  → revalidate saved output → return companies to the lab
```

The adapter calls PR #198's `session(model=..., reasoning_effort=...)`, adds
the TYCHE MCP configuration to that session's isolated `CODEX_HOME`, and runs
one Codex process while the session remains open. PR #198 owns the Responses
bridge and sends `openrouter.responses` through the lab worker. TYCHE never
implements or replaces that model transport.

TYCHE's four run-bound research tools are exposed through its shared MCP
transport. The host initializes the request, so `tyche_start` is unavailable
to the model. The lab already isolates the process in gVisor; the adapter does
not invoke the desktop launcher's nested sandbox relay. Shared qualification,
email, accounting, stopping and final evidence-review gates still apply.
Only the final delivery callback changes from a workbook to reviewed JSON.

The default is `openai/gpt-5.6-luna` with `xhigh` reasoning, matching the local
launcher's model family and effort. The round must include that model in its
price table and support it through OpenRouter Responses. Availability has not
been verified with a paid call. The local launcher's Fast setting is omitted:
PR #198's closed request schema does not accept `service_tier`. There is no
automatic fallback to another model or personal Codex login.

The bundle refuses execution outside `/agent/source` or without the lab's
two socket mounts, host-mounted runtime helpers, executable and output path.
It is for new parallel-execution lab rounds, not historical or local runs.
The research deadline is 2,640 seconds; the Codex process is bounded at 2,670
seconds, leaving room inside the lab's 2,700-second window. The outer signed
deadline and quotas always remain authoritative. Timeout/error paths close the
session, kill the process group and save bounded diagnostics. They never
relaunch a potentially billed call or silently deliver unfinished records.

## Input and output

- Supports `intent_details_v1` and `contacts_v1`, with the lab's v5 company
  output and `LAB_ARENA_COMPANY_LIMIT` of 1–5.
- Keeps the original ICP, roles, exclusions, company criteria, required
  attribute, contact geography and seniority. Primary signals and required
  attributes must be text. The primary signal at index 0 is mandatory.
  Generated `bonus_intents` remain optional, preserve scoring order, and use
  their individual age limits.
- Contact email must appear in the selected HarvestAPI `get_profile` receipt
  requested with `findEmail: "true"`. Its actual provider record ID is emitted
  as `contact.email_source.record_id`. Local route IDs and Deepline request IDs
  are not substituted for lab broker call IDs. The lab's verifier remains the
  authority on factual fit and provenance.
- `tyche_finish` first produces the existing evidence packet. Approval of its
  current `review_ref` runs strict validation, maps only accepted records, saves
  `companies.json` and `validation.json`, then calls `lab_arena_checkpoint.write`.
  Delivered state is closed to further research changes.
- After Codex exits, the harness validates the records again against the
  original ICP and requires identical saved and checkpointed JSON. It returns
  the company list for the lab's normal entrypoint. Final text alone is never
  delivery. Checkpoints contain only reviewed output; the adapter does not
  periodically publish unreviewed drafts.

## Provider boundary

Provider research uses only the lab's `deepline.execute` operation. The
bundled public catalog covers the 21 approved tools at the inspected PR #198
revision. Metadata reads are local; no Deepline CLI installation is needed
inside the lab. ScrapingDog and manually injected web observations are not
exposed by this adapter. There is no direct-provider fallback.

Raw provider receipts, billing, identities and local reservations are retained.
Unknown billing keeps its reservation and blocks additional paid research.
The local provider allowance is USD 0.50 per requested company; model costs
are separate and enforced by the lab. The adapter caps provider calls at 30
per MCP session; the lab enforces authoritative attempt quotas. Catalog prices
are planning inputs, never a substitute for provider billing receipts.

## Package and enable

```sh
python3 scripts/build_arena_bundle.py /tmp/tyche-codex-bundle
```

The builder stages an allowlist of source files, shared instructions/references,
taxonomy assets, the public catalog, `requirements.txt` and the license. It
excludes reports, local settings, credentials, tests and Git history. The only
Python package dependency is `geonamescache==3.0.2`, used for country/region
validation. Submit the staged directory through the existing lab source-bundle
and baseline promotion process; do not install the desktop launcher in the lab.

Before enabling a round, Leadpoet PR #198 must be merged and its Codex-equipped
image and migration `258-lab-arena-codex-cost-reconciliation.sql` deployed.
The selected round must admit the model and install this source bundle's
dependency. Existing rounds retain their frozen baseline. This TYCHE PR does
not deploy, promote a baseline, change subnet infrastructure, or modify PR #198.

To refresh free public tool metadata after the lab allowlist changes:

```sh
python3 scripts/refresh_arena_catalog.py /path/to/leadpoet
```

## Verification and limits

Compatibility was reviewed against PR #198 commit
`2558d4bc418046ac9146c7992032405034150601`. Its session signature, mounted paths,
Responses allowlist/limits, provider frames, checkpoint writer and receiver
input/output contracts were read as source, without executing Leadpoet.

```sh
python -m pytest tests/test_arena_codex.py -q
python -m unittest discover -s .agents/skills/lead-sourcing/tests -p test_research_tools.py
```

TYCHE-only offline fixtures cover the trigger through reviewed saved output,
MCP configuration/schema bounds, primary/bonus semantics, stale evidence,
wrong email provenance, false text completion, changed checkpoints, quotas,
uncertain billing and timeout cleanup. The process, checkpoint host and paid
responses are fixtures; these checks do not execute Codex or Leadpoet.

Actual Codex-to-MCP execution inside the deployed lab image, model availability,
live provider behavior and sourcing quality remain unverified. A lab smoke run
is the remaining integration check before promotion; unit checks cannot prove
that deployed journey. It was not run as part of this code-only integration.
