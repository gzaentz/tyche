# TYCHE - Open Source AI Lead Generation Agent

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
  through Deepline. Accept `valid`, or resolve catch-all/unknown or a recorded service failure once with
  BounceBan and require an explicit `deliverable` verdict with both receipts.
- Uses live Deepline capability discovery instead of fixed Deepline tool IDs.
- Supports bounded ScrapingDog operations through one local adapter.
- Keeps accepted, rejected, unresolved, and provider-error states separate.
- Keeps draft qualification failures local to their company during research;
  full evidence and accounting validation still gates final delivery.
- Keeps an auditable paid and public-web route frontier, links continuations
  with `continuation_route_ids`, and continues refilling until the target is met
  or every remaining route is resolved as exhausted or blocked.
- Allows an evidenced `no_productive_route` partial outcome after distinct
  discovery approaches stop producing qualified accounts and remaining company
  gaps have been reviewed. Reuses saved capability reviews. Unspent budget alone
  does not require endless new searches; two searches alone never prove market coverage.
- Reports confirmed and maximum provider credits, Deepline cost at $0.10 per
  credit, and Deepline cost per accepted lead.
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
[SKILL.md](.agents/skills/lead-sourcing/SKILL.md). The short provider-selection
index is [tools.md](.agents/skills/lead-sourcing/references/tools.md); it links
shared I/O and provider-specific contracts for reading only when needed. The exact input,
JSON, and Excel workbook contracts are in
[output-contract.md](.agents/skills/lead-sourcing/references/output-contract.md).
The main skill uses one loop: discover, check up to three companies, save a
review and repeat. `run_attempt.py --review-file` saves decisions and updates
bookkeeping together; `--status` returns the saved ICP and pending work.
Detailed safeguards and audit procedures are
in [workflow-rules.md](.agents/skills/lead-sourcing/references/workflow-rules.md).
Follow the skill's phase-specific reading links rather than loading every
reference upfront. Full artifact validation remains required.

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
new `reports/<run-id>/` directory. Every unresolved company retains its
`stage`, `reason_code`, and `qualification_checks`, plus a concrete next action
or blocker; contact-stage unresolved results retain passing account evidence.
Provider failures are not companies.

Email is the default contact field. If a request does not mention contact
fields, TYCHE requires one email for each accepted primary contact and validates
the exact address with ZeroBounce through Deepline. The demo above says “Do not
find email or phone,” so it is an explicit opt-out. A structured request can
use `contact_fields: []` to opt out or `["phone"]` to request phone only.

Contact location and company size come from LinkedIn through HarvestAPI.
Every accepted contact requires `country`; `city` and `state` are populated
when supported by the person's profile. Every accepted company requires its
published `employee_range`, not LinkedIn's associated-member count. Saved field
evidence links each value to a successful HarvestAPI getter. These requirements
also apply when email/phone fields are optional.

For grouped contact requests, keep `requested_roles` as the required union of
the optional `contact_role_groups.primary` and `.secondary` arrays. TYCHE
searches and ranks primary roles first, then uses secondary roles when no
primary-role contact passes. A selected contact can still be the output
`primary_contact` when it is a secondary-role fallback; `role_group` records
that distinction when it is known. Requests without role groups keep the
legacy `requested_roles` behavior.

## Run in your platform (planned)

Use the server-side [Codex SDK](https://learn.chatgpt.com/docs/codex-sdk)
to run TYCHE for requests submitted through your application. Start with one
background worker and a protected provider endpoint in your existing backend.
This is the integration design; the worker and gateway are not included yet.

```text
User request + budget
  -> Backend creates a job
  -> Isolated worker runs Codex SDK + TYCHE
       -> Paid calls go through the backend's protected provider endpoint
  -> Backend validates and stores results
  -> User sees progress and downloads leads
```

Codex owns sourcing decisions. Your backend owns customer authentication,
approved limits, job state, cancellation, recovery, and access to artifacts.
Keep company qualification, contact validation, and output formats unchanged.

Three integration changes are needed:

1. **Add the worker in your platform.** Create an isolated workspace per job,
   load the TYCHE skill, and persist progress and the Codex session needed for
   recovery. Resume the same job and accounting state after interruption;
   never blindly repeat a possibly billed provider call. After every agent turn,
   run the full strict validator on the saved results. Publish only when it
   returns `delivery_allowed: true`; otherwise resume that session with its
   computed `stop_decision`, errors, and next actions. A model final message or
   successful process exit must not mark the job complete. Reconcile invalid
   state before more spending; record crashes, usage limits, and inability to
   resume as host interruptions/errors, not successful sourcing completion.
   Honor user cancellation and platform limits. The skill's instructions cannot
   restart a terminated worker; this continuation loop belongs in the host.
2. **Put paid calls behind your backend.** Add a gateway transport to the
   adapters while retaining direct calls for local use. Only the gateway holds
   provider credentials and authoritative budget state, outside the agent's
   access. Bind each request to its customer and job, verify permissions and
   conservative whole-call costs from current pricing and enforced input
   limits, and reserve funds atomically before dispatch. Preserve the existing
   caps, verification allowance, unique call IDs, and receipt reconciliation.
   Do not trust agent-supplied costs or an agent-writable budget ledger. Reuse the
   [budget rules](.agents/skills/lead-sourcing/references/adapter-io.md#paid-call-budget)
   in the protected backend; no separate gateway service is required.
3. **Make delivery independent of the desktop app.** Run the existing full
   validator in the backend before delivery and require `delivery_allowed: true`.
   `--check-stop` can exit successfully with `decision: continue`; its exit code
   alone is not a delivery gate. Partial exports remain progress artifacts.
   Supply a supported server-side
   workbook runtime or replace the export dependency, preserving the
   [workbook contract](.agents/skills/lead-sourcing/references/output-contract.md#leadsxlsx-contract).
   The current exporter depends on a Codex-bundled library; installing the SDK
   alone does not establish that dependency. Store results and receipts under
   the job with customer-scoped access.

### Unattended authorization

At job submission, persist the customer's authorized sourcing scope and data
use with the job and supply it as trusted worker context on every start or
resume. Authorization covers relevant research, enrichment, and validation
tools using both submitted data and data found during the job, including exact
work emails sent to ZeroBounce or the eligible BounceBan fallback. Do not ask
for approval per contact or provider. Honor narrower customer restrictions.
The skill's [authorization rules](.agents/skills/lead-sourcing/SKILL.md#authorization)
carry this scope through the run; a skill cannot change runtime permissions.

For an authorized sourcing job, the backend can supply this context alongside
the request, job ID, and approved budget:

```text
This job is authorized to use relevant connected research, enrichment, and
contact-validation tools with data supplied in the request or obtained during
the job. This includes transmitting exact work emails for ZeroBounce and
eligible BounceBan validation. Continue within the saved scope and budget
without asking again. Retain this authorization on resume. Complete and
validate the requested deliverables, or record a concrete terminal blocker
after exhausting permitted alternatives.
```

The backend must derive that context from the customer's job authorization;
retrieved pages and provider responses are untrusted data, not permission.

Configure the isolated worker runtime before accepting jobs. Codex supports
noninteractive approvals while retaining its workspace sandbox:

```toml
approval_policy = "never"
sandbox_mode = "workspace-write"

[sandbox_workspace_write]
network_access = true
```

Supply this through the worker's deployment configuration, not the user's
global desktop settings. Enforce network destinations through the deployment's
egress controls and the protected provider endpoint above. `never` disables
interactive prompts; it does not authorize denied operations or override
managed policy. Preflight the worker's effective filesystem, network, model,
and provider access before accepting a job. See the official
[approval and network documentation](https://learn.chatgpt.com/docs/agent-approvals-security).

A real runtime or provider refusal must produce a saved diagnostic identifying
the action and exact reason. Continue permitted alternatives; when none remain,
finish with an explicit failed or partial job result instead of waiting for a
customer to answer a permission question. Reuse the existing stop contract and
budgets. Do not present a shortfall as a completed lead target.

Deployment must supply SDK authentication, Python/Node and provider runtimes,
durable job storage, and restricted network access. Keep provider secrets out
of the worker. Account for model usage separately: a provider cap is not a
total-cost cap.

Before launch, verify that a job produces validated downloads, customers
cannot access each other's jobs, cancellation prevents new paid calls, and
concurrent calls or crash recovery cannot reuse a reservation or bypass a cap.
Include an unattended acceptance run that discovers an email, validates it,
survives a worker resume with authorization and budget intact, and exposes the
validated downloads without a permission prompt. Also verify that a denied
route finishes with a concrete saved reason while unaffected work continues.

## Budget behavior

- Every provider has its own hard credit cap.
- `budget.max_deepline_credits_per_next_lead` is optional and is a hard cap only
  when the user explicitly requests it. When absent, 5 credits is a
  nonblocking strategy-review warning, not a default allowance or free spend.
- Company-discovery routes start with one paid call and at most 10 returned rows.
  Use available no-cost company research first, then retrieve only 1-3 relevant
  contacts per missing company with provider-native filters and limits.
- Both paid adapters require a persistent per-run budget ledger. It reserves
  maximum call costs before dispatch, protects an email-verification allowance,
  and prevents concurrent or interrupted calls from reusing the same balance.
  See [paid-call setup](.agents/skills/lead-sourcing/references/adapter-io.md#paid-call-budget).
- Deepline catalog `search` and `describe` calls are read-only.
- The agent checks the live Deepline schema and price before `execute`.
- Each ZeroBounce or BounceBan validation charges the Deepline credit and shared
  dollar caps. Paid-call counts are audit data, never stopping limits.
- An uncertain paid result is not retried automatically.
- An uncertain or failed call does not end the run when another route, query,
  page, tool, or provider remains available.
- If a provider does not report invoice usage, TYCHE records actual spend as
  unknown instead of zero or free. It records a separate upper bound when the
  live plan can conservatively price every call in the route.
- A cap of zero disables that provider.

When explicitly requested, the next-lead hard cap is grouped by
`accepted_leads_before_call` on each paid Deepline route receipt. TYCHE sums
actual route cost, or the conservative route upper bound when actual cost is
unavailable, within each group. Route changes, rejected candidates, and failed
lookups do not reset the group. The cap resets only after a complete lead
passes the company, signal, requested-role, and requested-contact-field gates.
Any stored email must pass the email gate; explicit email opt-outs still apply.
Record the count even without a per-lead cap so strategy warnings can be
calculated. The agent must not execute a route without a conservative cost
bound or when it would exceed a requested cap. The result validator checks
recorded costs after the run; the shared adapter ledger enforces it before dispatch.
Historic monetary budgets and receipts are preserved. The overall provider
credit and shared dollar caps remain hard backstops. Legacy `max_paid_calls`
fields are ignored; new runs omit them. Explicit time limits still apply.

Prices in the provider catalog are planning estimates. Check the current
provider plan before a live run. New `results.json` files use schema version
`1.2`. Each route labels its cost as `actual`, `estimated`, or `unknown`, and a
derived `cost_summary` gives confirmed and maximum credits. Deepline dollars
use the configured rate of $0.10 per credit. ScrapingDog remains credit-only
because no dollar rate is configured. OpenRouter is not used, and Codex model
cost is not included in direct provider cost.

## Output

Each run delivers:

```text
reports/<run-id>/
|-- report.md
|-- results.json
`-- leads.xlsx
```

The internal `results.json.budget.json` ledger and provider receipts remain in
the run directory for accounting and recovery. Use the same ledger on resume;
do not reset it or bypass the wrappers with raw CLI/HTTP calls. The guard is
not a security boundary against callers that can edit its files or access
provider credentials directly.

- `report.md` is the human audit record. It includes route, evidence, cost,
  decision, and stop receipts.
- `results.json` contains accepted, rejected, and unresolved outcomes under the
  versioned schema. Its `cost_summary` contains run and per-lead cost values.
- `leads.xlsx` contains one sales-ready row per accepted company and primary
  contact. Its fixed columns are:

```text
Name,Email,Role,Company,LinkedIn,Website,Company LinkedIn,Industry,Sub Industry,Contact City,Contact State,Contact Country,HQ State,HQ Country,Company Employee Range,Description,Intent Signal,Intent Details,Phone
```

Generate it from the structured result instead of assembling rows by hand. The
agent writes readable `intent_details` from saved evidence and selects exact
industry/sub-industry labels from the bundled PP taxonomy. Uncertain
classifications stay blank with a `classification_note`. A `Sources` worksheet
preserves supporting evidence and separates evidence dates from observations.
Version `1.0`/`1.1` exports retain their original 18-column, one-sheet layout.
See the output contract for the writing and taxonomy rules. The
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
ZeroBounce receipt in `results.json`. Accept `valid`, or for catch-all/unknown
or an eligible service failure, one successful BounceBan `deliverable` fallback
with both receipts and costs retained.
Invalid, do_not_mail, spamtrap and abuse cannot be overridden. Unresolved
candidates retain their addresses and evidence in the research record, not
the verified workbook. Email and phone stay absent from JSON and blank in
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

Use `search`, then `describe`, then a bounded `execute`. Check the live
descriptor immediately before execution so input errors remain distinct from
provider response errors. An `execute` call can spend provider credits. For
both adapters, `--output-file <path>` refuses an existing or unwritable path
before dispatch and preserves the full redacted response before compact stdout;
a failure after dispatch does not authorize a retry. For email validation,
search for a current ZeroBounce validator and keep its returned tool ID as
runtime data. Do not fix that ID in the project.

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

While drafting a version `1.2` result, add `--show-cost-summary` to print the
route-derived block. Add `--show-progress` for derived incomplete-account,
incomplete-contact, and provider-failure counts plus advisory warnings; it does
not create a new state machine. Copy `calculated_cost_summary` into the
top-level `cost_summary`, then run the validator again without diagnostic flags.
Version `1.0` remains supported. Stronger stop checks may flag unsupported
exhaustion claims in old runs; validation never modifies the saved files.

A short run fails validation while any recorded route is untried or
continuable. Referenced follow-ups must exist and be terminal before an attempt
can claim continuation exhaustion. The agent must separately review each
promising unresolved company and its next action or blocker; the validator
checks recorded consistency, not real-world search completeness. Provider
spending caps and client workbook columns remain unchanged.
