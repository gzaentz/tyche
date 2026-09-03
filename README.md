# TYCHE

TYCHE is a company-first lead-sourcing skill for Codex. It finds companies
that match an ideal customer profile, proves a current buying signal, and then
finds a requested-role contact at each accepted company.

TYCHE is not a separate agent server or LLM runtime. Codex supplies the agent
loop and strategy. The project supplies the sourcing rules, evidence gates,
provider adapters, budget controls, and output contract.

## What it does

- Sources companies before people.
- Checks company fit and buying-signal evidence separately.
- Searches for contacts only after a company passes the account gate.
- Requires a current title and company match for every accepted contact.
- Uses live Deepline capability discovery instead of fixed Deepline tool IDs.
- Supports bounded ScrapingDog operations through one local adapter.
- Keeps accepted, rejected, unresolved, and provider-error states separate.
- Produces an audit report, structured JSON, and a clean CSV.
- Does not send outreach or write to a CRM.

## How it works

```text
Codex
  -> lead-sourcing/SKILL.md
  -> strategy and bounded provider pilots
  -> Deepline CLI adapter or ScrapingDog adapter
  -> company gate
  -> contact gate
  -> reports/<run-id>/{report.md,results.json,leads.csv}
```

The main instructions are in
[SKILL.md](.agents/skills/lead-sourcing/SKILL.md). Provider operations are in
[tools.md](.agents/skills/lead-sourcing/references/tools.md). The exact input,
JSON, and CSV contracts are in
[output-contract.md](.agents/skills/lead-sourcing/references/output-contract.md).

## Requirements

- Codex CLI or Codex desktop with project-local skill support
- Python 3.9 or later
- The Deepline CLI, installed and authenticated, for Deepline routes
- A ScrapingDog API key for ScrapingDog routes

OpenRouter is not required. TYCHE uses the model that runs the Codex harness.

## Setup

Clone the repository and enter it:

```bash
git clone https://github.com/gzaentz/tyche.git
cd tyche
```

Confirm that the Deepline CLI can connect:

```bash
deepline health --json
```

Make the ScrapingDog key available to the process that starts Codex:

```bash
export SCRAPINGDOG_API_KEY="your-key"
```

The Deepline adapter delegates authentication to the installed Deepline CLI.
If your Deepline setup uses `DEEPLINE_API_KEY`, export it in the same shell.
If the `deepline` executable is not on `PATH`, set `DEEPLINE_BIN` to its
absolute path.

You can keep local values in `.env`, but the adapters do not load that file.
Load it before you start Codex if you choose to use one:

```bash
set -a
source .env
set +a
```

`.env` is ignored by Git. Never put a provider key in an adapter JSON request,
prompt, report, or commit.

## Run in Codex

Start Codex from the repository root. You can invoke the skill explicitly:

```text
Use $lead-sourcing.

Source 5 US B2B SaaS companies with 50-500 employees. Exclude agencies and
consultancies. Each company must be hiring several sales roles, with evidence
from the last 30 days. Find one Chief Revenue Officer, VP Sales, or Head of
Sales at each accepted company. Do not find email or phone.

Hard caps: 5 Deepline credits, 10 ScrapingDog credits, and 4 paid calls.
```

The agent normalizes the request, discovers current provider capabilities,
runs small pilots, checks evidence, and writes the three output files under a
new `reports/<run-id>/` directory.

## Budget behavior

- Every provider has its own hard credit cap.
- A material route starts with one paid call and at most 10 returned rows.
- Deepline catalog `search` and `describe` calls are read-only.
- The agent checks the live Deepline schema and price before `execute`.
- An uncertain paid result is not retried automatically.
- If a provider does not report invoice usage, TYCHE records actual spend as
  unknown instead of zero.
- A cap of zero disables that provider.

Prices in the provider catalog are planning estimates. Check the current
provider plan before a live run.

## Output

Each run creates exactly:

```text
reports/<run-id>/
|-- report.md
|-- results.json
`-- leads.csv
```

- `report.md` is the human audit record. It includes route, evidence, cost,
  decision, and stop receipts.
- `results.json` contains accepted, rejected, and unresolved outcomes under the
  versioned schema.
- `leads.csv` contains one row per accepted company and primary contact.

Email and phone fields stay absent from JSON and blank in CSV unless the input
requests them. Generated reports are ignored by Git because evidence and
contact data become stale.

## Provider adapters

The adapters use only Python's standard library.

### Deepline

The Deepline adapter calls the installed CLI and emits one redacted JSON
object:

```bash
python3 .agents/skills/lead-sourcing/scripts/deepline.py \
  --input '{"operation":"search","query":"companies with current hiring"}'
```

Use `search`, then `describe`, then a bounded `execute`. An `execute` call can
spend provider credits.

### ScrapingDog

The ScrapingDog adapter makes one bounded request and returns normalized
evidence:

```bash
python3 .agents/skills/lead-sourcing/scripts/scrapingdog.py \
  --input '{"operation":"google_jobs","query":"B2B SaaS sales","country":"us","limit":10}'
```

This example is a paid provider call. The adapter reads the key only from
`SCRAPINGDOG_API_KEY`.

## Test

Run the focused adapter suite:

```bash
python3 -m unittest discover \
  -s .agents/skills/lead-sourcing/tests \
  -p 'test_provider_scripts.py'
```

The tests cover request bounds, provider statuses, error redaction, current-role
normalization, response-envelope handling, evidence truncation, and HTML text
extraction.

## Project boundaries

TYCHE has no runtime dependency on `Sourcing_model` or `pp`. It has no server,
database, queue, browser harness, fixed provider router, CRM write, or outreach
action. Add those only when a real use case requires them.
