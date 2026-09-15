# TYCHE

**Open-source lead sourcing with evidence, verified contacts, and spending controls.**

TYCHE finds companies that match your ideal customer profile, checks the facts
and buying signals you care about, then finds people in your requested roles.
It runs in Codex and delivers an Excel lead list, structured JSON, and an audit
report with sources and costs.

[Quick start](#quick-start) · [Requests and defaults](#requests-and-defaults) ·
[Outputs](#outputs) · [Build on TYCHE](#build-on-tyche)

**Leadpoet Arena:** [Trigger TYCHE through `harness.run_icp(icp)`](docs/leadpoet-arena.md)
and receive reviewed company/contact JSON using the existing broker and output
contract. This optional runtime uses PydanticAI; local Codex sourcing stays the same.

## What it does

- **Qualifies companies first.** Checks company fit and buying signals separately,
  preserving required criteria, preferences, and missing evidence.
- **Finds the right people.** Verifies current roles and company identity, with
  primary and fallback role groups when requested.
- **Validates email.** Requires a verified email by default, with explicit opt-outs
  and receipt-backed validation.
- **Controls provider spending.** Reserves costs before paid calls and keeps the
  same budget and receipts through interruptions.
- **Delivers traceable results.** Saves accepted, rejected, and unresolved outcomes;
  validates the result and workbook before delivery.

## Quick start

### 1. Prepare your environment

You need:

- [Codex CLI](https://learn.chatgpt.com/docs/codex/cli) on `PATH`, with a reusable
  file-based login (`codex login`). You can submit requests from Codex desktop.
- Python 3.9 or later, plus an installed, authenticated Deepline CLI for provider
  research, LinkedIn verification, and email validation.
- The Codex workbook runtime: Node.js, Python, and `@oai/artifact-tool`. The launcher
  discovers the installed desktop bundle. Other hosts must configure the
  [runtime paths](docs/codex-isolated-testing.md#workbook-finalization-and-usage-reconciliation)
  before sourcing; installing the Codex CLI alone does not supply the exporter.
- A `SCRAPINGDOG_API_KEY` if you want to use ScrapingDog routes.

```bash
git clone https://github.com/gzaentz/tyche.git
cd tyche
deepline health --json
```

The adapters use the installed Deepline CLI's authentication; a separate
ZeroBounce key is unnecessary. If applicable, export `DEEPLINE_API_KEY` and
`SCRAPINGDOG_API_KEY` in the environment that launches Codex. Set `DEEPLINE_BIN`
to the executable's absolute path if it is outside `PATH`.

Local `.env` files are **not loaded automatically**. In a Bash or Zsh terminal,
load your own file before starting Codex:

```bash
set -a
source .env
set +a
```

Keep credentials out of committed files.

### 2. Check the launcher

Run this from your regular host terminal:

```bash
python3 scripts/codex_tyche.py --check
```

This checks isolation and session startup without a model turn or provider
calls. It does not verify model-service connectivity or provider credentials.
Use `--smoke` for an optional read-only model response with no provider calls.
See [launcher setup and troubleshooting](docs/codex-isolated-testing.md).

The [launcher](scripts/codex_tyche.py) pins **`gpt-5.6-luna`**, **`xhigh` reasoning**,
and the **`fast` service tier**. It loads project-local sourcing instructions in
an isolated session while retaining the worker's sandbox and network policy.

### 3. Ask for leads

Open this repository in Codex and describe the companies, signals, roles, and
budget you want. For example:

```text
Source 5 US B2B SaaS companies with 50–500 employees. Exclude agencies and
consultancies. Each must have posted at least 3 sales openings in the last
30 days. Find one CRO, VP Sales, or Head of Sales with a verified work email
at each accepted company. Spend at most $5 total on sourcing providers.
```

[AGENTS.md](AGENTS.md) routes sourcing requests through the isolated launcher
and saves each request under `reports/<run-id>/request.txt`. Code changes and
questions stay in your ordinary Codex session.

For a terminal-driven run, create a unique directory under `reports/`, save your
request and that directory path in a UTF-8 `request.txt`, then run from the host
terminal, replacing `<run-id>` with your directory name:

```bash
python3 scripts/codex_tyche.py --exec-file reports/<run-id>/request.txt
```

## Requests and defaults

| Setting | Behavior |
| --- | --- |
| Companies | Give a target count, geography, industry, size, and exclusions. Distinguish must-haves from preferences. |
| Buying signals | Specify the evidence and date window. Required signals match **any** by default; ask for **all** when each is mandatory. |
| Contacts | One contact per company by default; request up to three. You can name primary roles and fallback roles. |
| Contact data | Verified email by default. Explicitly request no email or phone (`contact_fields: []`) to opt out, or request phone only. |
| Provider budget | **$0.50 × requested leads** when omitted. An explicit budget, including zero, overrides this default. Separate provider caps also apply. |
| Time | Set a deadline when needed. The 30-minute research benchmark is not an automatic cutoff. |

Every stored email must pass ZeroBounce or its eligible BounceBan fallback,
with matching receipts. Accepted contacts require verified LinkedIn country;
company size uses the published LinkedIn employee range. See the
[input and output contract](.agents/skills/lead-sourcing/references/output-contract.md)
for exact fields and evidence rules.

Provider budgets cover **provider charges only**. Model usage and combined cost
are reported separately, with estimates and unknown charges labeled. Paid-call
counts are audit data, not stopping limits. Uncertain paid requests retain their
reservations and are not automatically repeated.

TYCHE continues until it meets the target or documents a valid stopping reason,
such as an explicit time limit, an exhausted budget, or no productive remaining
route. Shortfalls and operational blockers remain visible. It does not send
outreach or write to a CRM.

## Outputs

Each run saves its files under `reports/<run-id>/`:

| File | Contents |
| --- | --- |
| `leads.xlsx` | One row per accepted company and primary contact, with company details, signals, intent, and a **Sources** worksheet. |
| `results.json` | Versioned accepted, rejected, and unresolved records with evidence and accounting. |
| `report.md` | Human-readable findings, shortfalls, decisions, sources, and provider costs. |
| `run-costs.json` | Provider and isolated-worker model usage/cost summary, including estimates and missing usage. |

The run also retains validation, workbook previews, provider receipts, and
`results.json.budget.json`. Keep the entire directory to resume with the same
scope and accounting. Model cost excludes the outer development conversation.

Delivery requires the full validator to return **`delivery_allowed: true`**,
a verified saved workbook, and review of its preview. A process exit or model
message alone does not establish completion. Partial files remain progress
artifacts until they pass the delivery checks.

## Build on TYCHE

Codex chooses sources, queries, follow-ups, and qualification judgments. Local
Python tools handle execution, spending reservations, receipts, and validation;
the Node exporter builds the workbook.

```text
Request → isolated Codex session → research and evidence review
                                → provider adapters + budget ledger
                                → strict validation → JSON / report / Excel
```

File-backed runs expose five native tools over local MCP:

| Tool | Purpose |
| --- | --- |
| `tyche_start` | Initialize or resume the request, budget, and verification reserve. |
| `tyche_lookup` | Run a selected provider tool or up to three independent checks; reserve spending and save receipts. |
| `tyche_review` | Save facts, evidence, and qualification decisions. |
| `tyche_inspect` | Read saved state, discover tools, and inspect schemas, pricing, or receipts. |
| `tyche_finish` | Validate reviewed results, export and verify the workbook, and write the report. |

### Where to work

| Change | Start here |
| --- | --- |
| Research behavior and qualification | [Sourcing skill](.agents/skills/lead-sourcing/SKILL.md) and [workflow rules](.agents/skills/lead-sourcing/references/workflow-rules.md) |
| Provider selection | [Tool guide](.agents/skills/lead-sourcing/references/tools.md) |
| Tool inputs, adapters, and budgets | [Native tool and adapter contracts](.agents/skills/lead-sourcing/references/adapter-io.md) |
| JSON fields and workbook layout | [Output contract](.agents/skills/lead-sourcing/references/output-contract.md) |
| Model settings, isolation, and recovery | [Launcher](scripts/codex_tyche.py) and [runtime guide](docs/codex-isolated-testing.md) |
| Application integration | [Platform integration design](docs/platform-integration.md) — worker hosting and a protected provider gateway are planned, not shipped. |

Provider capabilities and prices are discovered live. Extend the existing
adapters and preserve evidence gates, budget reservations, and uncertain-charge
reconciliation. The local ledger is writable by the worker; a hosted product
must enforce authoritative spending and credential access in its backend.

### Verify changes

Run the local test suites from the repository root:

```bash
python3 -m unittest discover -s .agents/skills/lead-sourcing/tests -p 'test_*.py'
python3 -m unittest discover -s scripts -p 'test_*.py'
```

Validate a saved run with:

```bash
python3 .agents/skills/lead-sourcing/scripts/validate_run.py reports/<run-id>/results.json
```

Use the full validator for delivery; `--check-stop` alone can return a successful
exit while research still needs to continue. For exporter dependencies, manual
export, and receipt reconciliation, see the [runtime guide](docs/codex-isolated-testing.md).

## License

[GNU AGPL-3.0](LICENSE).

---

<p align="center">
  <img src="docs/assets/tyche-characters.svg" alt="Tyche rendered with Unicode block characters, wearing an ornate crown beside a child and a fruit-filled cornucopia." width="640">
</p>
