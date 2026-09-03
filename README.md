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
- Supports explicit `any`/`all` signal matching and per-signal age bounds.
- Records required and preferred qualification checks as `pass`, `fail`, or
  `unknown`, with separate supporting evidence.
- Searches for contacts only after a company passes the account gate.
- Supports optional primary and secondary contact-role groups. Primary roles
  are searched first; secondary roles are valid fallbacks and are never
  rejected only because they are secondary.
- Requires a current title and company match for every accepted contact.
- Requires email by default and validates every stored email with ZeroBounce
  through Deepline. Only an explicit `invalid` status is rejected.
- Uses live Deepline capability discovery instead of fixed Deepline tool IDs.
- Supports bounded ScrapingDog operations through one local adapter.
- Keeps accepted, rejected, unresolved, and provider-error states separate.
- Keeps an auditable paid and public-web route frontier, and continues refilling
  until the target is met or every remaining route is exhausted or blocked.
- Produces an audit report, structured JSON, and a clean Excel workbook.
- Does not send outreach or write to a CRM.

## How it works

```text
Codex
  -> lead-sourcing/SKILL.md
  -> strategy and bounded provider pilots
  -> Deepline CLI adapter or ScrapingDog adapter
  -> company gate
  -> contact gate
  -> Deepline ZeroBounce email gate (unless explicitly opted out)
  -> reports/<run-id>/{report.md,results.json,leads.xlsx}
```

The main instructions are in
[SKILL.md](.agents/skills/lead-sourcing/SKILL.md). Provider operations are in
[tools.md](.agents/skills/lead-sourcing/references/tools.md). The exact input,
JSON, and Excel workbook contracts are in
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

The Deepline adapter delegates authentication, including managed ZeroBounce
access, to the installed Deepline CLI. A separate ZeroBounce key is not needed.
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

Email is the default contact field. If a request does not mention contact
fields, TYCHE requires one email for each accepted primary contact and validates
the exact address with ZeroBounce through Deepline. The demo above says “Do not
find email or phone,” so it is an explicit opt-out. A structured request can
use `contact_fields: []` to opt out or `["phone"]` to request phone only.

For grouped contact requests, keep `requested_roles` as the required union of
the optional `contact_role_groups.primary` and `.secondary` arrays. TYCHE
searches and ranks primary roles first, then uses secondary roles when no
primary-role contact passes. A selected contact can still be the output
`primary_contact` when it is a secondary-role fallback; `role_group` records
that distinction when it is known. Requests without role groups keep the
legacy `requested_roles` behavior.

## Budget behavior

- Every provider has its own hard credit cap.
- A material route starts with one paid call and at most 10 returned rows.
- Deepline catalog `search` and `describe` calls are read-only.
- The agent checks the live Deepline schema and price before `execute`.
- Each ZeroBounce validation is a Deepline execution and counts against both
  the Deepline credit cap and the total paid-call cap.
- An uncertain paid result is not retried automatically.
- An uncertain or failed call does not end the run when another route, query,
  page, tool, or provider remains available.
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
`-- leads.xlsx
```

- `report.md` is the human audit record. It includes route, evidence, cost,
  decision, and stop receipts.
- `results.json` contains accepted, rejected, and unresolved outcomes under the
  versioned schema.
- `leads.xlsx` contains one sales-ready row per accepted company and primary
  contact. Its fixed columns are:

```text
Name,Email,Role,Company,LinkedIn,Website,Company LinkedIn,Industry,Sub Industry,City,State,Country,HQ State,HQ Country,Employee Count,Description,Intent Details,Phone
```

Generate it from the structured result instead of assembling rows by hand. The
Codex agent first loads the bundled workspace dependencies, then passes the
returned Node and node_modules paths to the exporter:

```bash
<bundled-node> .agents/skills/lead-sourcing/scripts/export_xlsx.mjs \
  reports/<run-id>/results.json \
  reports/<run-id>/leads.xlsx \
  --node-modules <bundled-node-modules>
```

The exporter keeps the exact column order, adds filters and clear headers, and
formats long text for review. The workbook library comes from the Codex
harness, so TYCHE does not add an npm dependency. Full evidence, run state,
rejected rows, unresolved rows, and backup contacts stay in `results.json` and
`report.md`.

Contact objects may include `role_group` (`primary` or `secondary`) for
traceability. The output slot `primary_contact` is separate from this role
group and may contain a valid secondary fallback.

Email is required by default. Every exported email has a matching Deepline
ZeroBounce receipt in `results.json`; only an explicit `invalid` status is
blocked. All other explicit statuses pass, including statuses that may be poor
choices for outbound email. Email and phone stay absent from JSON and blank in
the workbook when the input explicitly opts out of them. Generated reports are
ignored by Git because evidence and contact data become stale.

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
spend provider credits. For email validation, search for a current ZeroBounce
validator and keep its returned tool ID as runtime data. Do not fix that ID in
the project.

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

Run the focused skill suite:

```bash
python3 -m unittest discover \
  -s .agents/skills/lead-sourcing/tests \
  -p 'test_*.py'
```

The tests cover request bounds, provider statuses, error redaction, current-role
normalization, response-envelope handling, evidence truncation, HTML text
extraction, workbook export, output-contract semantics, budget accounting, and
completion stops.

Validate a completed run's target and route-exhaustion receipt with:

```bash
python3 .agents/skills/lead-sourcing/scripts/validate_run.py \
  reports/<run-id>/results.json
```

A short run fails validation while any recorded route is untried or
continuable. This check does not relax provider credit or paid-call caps.

## Project boundaries

TYCHE has no runtime dependency on `Sourcing_model` or `pp`. It has no server,
database, queue, browser harness, fixed provider router, CRM write, or outreach
action. Add those only when a real use case requires them.
