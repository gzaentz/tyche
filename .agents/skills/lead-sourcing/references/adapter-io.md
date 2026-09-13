# Shared adapter I/O

Read once before the first provider call, and revisit for receipt or transport
failures. This is the shared credential, response-file, and recovery contract;
provider-specific inputs and statuses live in [Deepline](deepline-adapter.md)
and [ScrapingDog](scrapingdog-adapter.md). Examples beginning with `.agents/`
run from the repository root.

## Start or resume

The LLM interprets the ICP, signals and role priorities. Put that
[input request](output-contract.md#input-contract) under `request` in a setup
object; use `--start-file setup.json` (or `-` for UTF-8 JSON on stdin):

```bash
python3 .agents/skills/lead-sourcing/scripts/run_attempt.py \
  reports/<run-id>/results.json --start-file reports/<run-id>/setup.json
```

Optional setup fields are `max_usd`, `scrapingdog_usd_per_credit`,
`verification_reserve_credits` and `started_at`. Supply the priced verification
reserve for email-required runs; never guess prices. Free catalog inspection
through the Deepline wrapper can establish that price before initialization.
The helper supplies defaults, the run ID, original clock, empty result records
and ledger. It validates before writing and preserves existing criteria,
evidence, reservations and spending on an identical retry. Interrupted writes
retain the original initialization settings. A leftover lock still requires
checking that its writer has stopped; locks are never expired automatically.

Use `--status` to resume an existing run without resubmitting its request. Do
not manually construct result bookkeeping or run `budget_guard.py` afterward.
The ledger's existing initialization command remains for legacy callers.

## One attempt

Use `--lookup-file lookup.json` with one research lookup or an array of up to
three independent lookups. This composes the existing wrappers, budget guard
and recorder; it does not choose a research strategy. For free catalog search:

```json
{"request": {"operation": "search", "query": "multilingual product-page research"}}
```

For a chosen provider tool, supply `scope` (canonical company domain or
`discovery`), `phase`, `purpose`, `approach`, `request` and a verified whole-call
`max_cost_credits`. `provider` defaults to `deepline`; `scrapingdog` and
`public_web` use their existing wrapper inputs. For example:

```json
{
  "scope": "example.com",
  "phase": "account_verification",
  "purpose": "Check the current business and funding stage",
  "approach": "current-company-profile",
  "max_cost_credits": null,
  "request": {"operation": "execute", "tool": "<live-described-tool>", "payload": {}}
}
```

The null price and empty payload above are placeholders, not dispatchable inputs.
Use a live-described tool and its native payload. The helper checks its saved
same-run description for availability, required top-level fields and primitive
types; the provider still owns the full native schema. Reuse descriptions until
schema, pricing or access changes. Unknown pricing blocks paid dispatch.

Code generates route IDs, fingerprints, receipt paths, paid-call flags and
`spend` metadata. Catalog reads receive their own scope/phase automatically.
Contact phases retain the existing passing-account-evidence gate. The legacy
`--input-file` action/request envelope and `--batch-files` remain compatible;
new work should use `--lookup-file` rather than reconstruct those envelopes.

Use stable `approach` labels describing the source family and search/evidence
strategy, not tool names, batch numbers or cosmetic rewordings. The helper hashes
the actual request, so changing a route ID or approach label cannot repeat a
possibly billed request. Two comparable research attempts within the same scope
and phase without new verified milestones require a changed approach. Progress
at another company does not reset that company's research. Profile/email checks
for distinct targets and advancement to another phase remain eligible; finishing
one source does not exhaust the company. Catalog reads do not count as progress.

The helper saves `receipts/<action-id>.json` before updating run state. A crash
leaves the route pending and retains any reservation. Resume a saved normalized
response with `--complete <action-id>`; this only records it, never dispatches.
New ledgers and helper receipts carry a fingerprint of the canonical results
path. Resume in place: rewriting paths in a copied ledger or receipt does not
make it belong to another run. Missing or mismatched identities require origin
and billing reconciliation; never erase them, reset spend, or rerun uncertain calls.
Historical ledgers without this marker remain available to the validator's
read-only `--legacy-stop-policy`; that mode cannot authorize execution or delivery.
It derives summary/review counts and cost totals from saved outcomes and receipts;
unknown charges keep provider capacity unknown. Attempt recording never marks
the frontier complete; the export command prepares completion after review.
Each saved response also retains the action and redacted input in `attempt`,
so recovering a damaged draft does not require inventing scope or approach labels.
If only raw response bytes survived, normalize that receipt locally first. A
pending/unknown remote outcome is not permission to retry. Async research jobs
need their documented result-retrieval call, not another job submission.
For a described **free job-status getter only**, mark its action
`status_read: true` with a zero cost bound. Repeated reads of the same job are
allowed only after a saved `partial` status response with a zero cost bound;
pending transport, failures and job submissions remain protected. Respect the
provider's polling interval and applicable read limit; never label submission
or enrichment as a status read. These calls still use the guarded ledger;
missing actual charges remain unknown, even when the reserved bound is zero.

For built-in public-web tools, set `provider: "public_web"` and use
`--lookup-file` with `--plan-only`; code supplies zero cost/calls. Execute the planned search/read through the available tool.
Write only the observed response to a separate UTF-8 JSON file, for example
`{"status":"ok","results":[{"url":"https://example.com/news","text":"Observed source text"}]}`,
then attach it to the planned route:

```bash
python3 .agents/skills/lead-sourcing/scripts/run_attempt.py \
  reports/<run-id>/results.json --complete <route-id> \
  --response-file reports/<run-id>/web-response.json
```

The helper retains run/route identity and progress metadata, saves the response,
then records it. `operation` is optional but must match the plan; `error` may
describe an observed failure. Do not label a failed check `no_results` or a pending
outcome complete. Do not edit receipt metadata. A saved response cannot be
replaced; recover an interrupted update using `--complete <route-id>` alone.
This path records observed evidence and never invokes a browser or paid provider.

The helper never infers qualification or market exhaustion. Assess the saved
evidence and submit company/route decisions together with `--review-file` below.
Full strict validation remains required before delivery.

The review helper fills missing email verdicts from saved same-run provider
responses and rejects conflicts. A valid verdict remains valid when the domain
has a catch-all flag. Before a BounceBan verification, the attempt helper checks
the saved same-email ZeroBounce result and refuses ineligible or repeated calls.
Recover pending jobs with the documented free status getter; never resubmit them.

The attempt CLI prints normalized provider results once, with the full receipt
path. Harvest rows show company/contact facts, current-role candidates, discovered
emails and missing fields; `omitted_fields` identifies additional saved data.
For Harvest profile getters, the helper supplies the reviewed company's LinkedIn
URL as local `target_company_linkedin_url` metadata to select its current role.
It is not sent to the provider. Multiple matching roles require review; a headline
or a historical role without an end date does not establish the current title.
Finder email flags never replace ZeroBounce or eligible BounceBan validation.
Repeated progress snapshots, request metadata and duplicate evidence stay in
the receipt. Reopen it with `run_attempt.py <results.json> --receipt <route-id>`
to reuse this compact view without dispatching or changing state. The view checks
run ownership and the saved route's request/provider identity, including pending
receipts; mismatches require origin reconciliation. Inspect specific
raw fields only for a missing fact or contradiction; full receipts remain saved.

## Save a review

Save findings as they become available; do not rebuild the full company row.
A `companies` item identifies `scope` and only the fields being updated:

```json
{
  "companies": [{
    "scope": "example.com",
    "state": "unresolved",
    "company": {"canonical_name": "Example"},
    "reason_text": "The business fits; the announcement date remains unknown.",
    "qualification_checks": [{
      "criterion": "recent_intent", "importance": "required", "status": "unknown",
      "claim": "The announcement has no verified date yet.", "evidence": []
    }]
  }],
  "routes": [{"route_id": "<returned-route-id>", "reason": "Reviewed the saved announcement; its date is not established."}]
}
```

`company` updates the supplied factual fields. `qualification_checks` updates
one judgment per exact `criterion` name, retaining earlier supporting evidence
and unrelated checks. Supply importance, status, claim and evidence explicitly;
code does not decide fit. Use the same criterion name when refining a judgment.

`account_fit`, `signal_evidence`, `intent_details`, `primary_contact` and
`backup_contacts` are complete replacements when supplied, and untouched when
omitted. Review new contacts and source identities before replacing them.
State defaults to the saved state (new companies start unresolved); set
`state: "accepted"` or `"rejected"` explicitly. Acceptance retains all existing
evidence/contact gates; a missing fact cannot become a rejection without an
evidenced required mismatch. Set `stage: "contact"` after account review passes,
with remaining buyer gaps in `reason_text`.

```bash
python3 .agents/skills/lead-sourcing/scripts/run_attempt.py \
  reports/<run-id>/results.json --review-file reports/<run-id>/review.json
```

`--review-file -` accepts stdin. The old `{state, row}` form remains available
for complete replacements. `companies`, `routes` and `next_actions` are optional;
omitted records remain intact. Usually execute your next choice directly with
`--lookup-file`; supply `next_actions` only for concrete work that needs saving.

One atomic update saves the selected company rows, closes reviewed routes,
retires their completed actions, removes speculative follow-ups for reviewed
parked/terminal companies, and refreshes counts and cost summaries. Receipts,
ledger charges and the saved request remain intact. The CLI response includes
`request_file` and the next decision, without repeating the full request.
No separate route-closing script, manual summary edits or duplicate stop check
is needed. Use the returned next decision for the next batch; reopen receipts
only for a missing fact or contradiction. `--status` includes the authoritative request for setup/resume,
without a write.

Routes default to `exhausted`; the helper derives the existing exhaustion basis
from the saved receipt. Use `state: "continuable"` for genuinely unfinished
work, or `"blocked"` for an evidenced provider failure. Existing continuation
links are preserved; supply `continuation_route_ids` only to add actual links.
Never close a pending/unknown response. The helper refuses missing receipts,
failed calls presented as exhausted, or decisions contradicting the saved
employee range. It does not infer source credibility or qualify companies for
you. Full strict output validation still checks delivery.

## Concurrent company checks

After the pilot, use one agent and up to three ready checks for different
companies. Use fewer when fewer checks are ready or the remaining lead shortfall
is smaller. Batch ready checks instead of calling them one by one; different
companies may be at different phases. Do not wait to fill a batch.
Save the same concise lookup objects above as an array in `batch.json`:

```bash
python3 .agents/skills/lead-sourcing/scripts/run_attempt.py \
  reports/<run-id>/results.json --lookup-file reports/<run-id>/batch.json
```

The existing `--batch-files` option remains compatible with one array file or
multiple individual attempt files. All paths use the same execution, independence
and budget checks. The helper validates every input with the existing provider
validators before planning or spending. Malformed fields identify their batch item
and stop the entire batch without changing run state.

Give each lookup its canonical company domain as `scope`; code assigns IDs.
Batch mode accepts account verification, contact discovery, contact verification
and email validation. Free catalog lookups may share discovery scope in a batch.
Keep substantive discovery pilots on the single-attempt path. Choose
only independent work: a company's buyer lookup waits for saved passing account
evidence, and email validation waits for its buyer/address checks. Deduplicate
aliases and owner groups before choosing the batch; do not run redundant provider
requests for the same company at once.

The helper plans serially, runs up to three provider calls concurrently, and
records results serially. Each call has its own receipt. Budget reservations and
settlements share the existing ledger and are serialized; pending costs still
count against all caps and the verification reserve. After input validation,
a member refused by eligibility/budget checks or a failed provider call
does not discard successful siblings. The batch returns all outcomes and exits
nonzero if any member fails; recover saved receipts with `--complete`, never
rerun the whole batch or retry an uncertain billed request.
Single attempts follow the same exit-code rule: `ok`, `partial`, and `no_results`
are successful provider outcomes; provider failures return nonzero even when
the adapter successfully returned JSON. Adapter errors remain nonzero. Saved
receipts, reservations and successful batch members are preserved. A successful
attempt still requires evidence review and full delivery validation.
The batch returns one final `stop_decision` after recording its outcomes; use
that decision without a separate status or stop-check command.

For built-in public-web checks, add `--plan-only` to prepare up to three receipts,
then execute those independent searches/reads together through the available
tool's parallel-call facility. Python only plans and records this work; it does
not invoke the built-in browser. Save each observed response to a separate file
and attach it with `--complete <route-id> --response-file <response.json>` serially.

Wait for the batch to finish, then save its decisions with `--review-file` and
use the returned next step. Batch completion alone does not qualify leads.
Use one check when only one is ready or a provider requires serial access; a
rate limit is a reason to reduce concurrency, never to increase retries.

## Paid-call budget

Use [start/resume](#start-or-resume) to initialize both records before paid
research. Keep the same results path throughout the run.

The shared cap defaults to USD 0.50 per requested lead. Supply setup `max_usd` for
an explicit user cap, including zero. Deepline uses the configured USD 0.10 per
credit. An enabled ScrapingDog allocation also requires
`scrapingdog_usd_per_credit` from the current plan; zero allocation disables
that provider. Existing provider and optional per-next-lead spending caps
remain independent. Email-required runs need an explicit verification reserve,
priced for the remaining leads and any planned fallback. Email opt-outs do not.

Paid-call counts are audit data, not limits. New runs omit `max_paid_calls`.
Legacy request, result, and ledger fields are ignored without rewriting the
ledger or resetting charges, pending reservations, or monetary limits.

Every Deepline `execute` and ScrapingDog request uses a guarded reservation.
The lookup helper supplies its `spend` object; do not assemble it manually.
Supply a conservative **whole-call** `max_cost_credits` from the live descriptor
and bound provider-native rows/pages. The wrapper output `limit` only truncates
the preview; it does not limit billing. Catalog `search`/`describe` do not execute
providers and need no paid reservation.

The adapters atomically persist reservations in `results.json.budget.json`
before dispatch. All callers for a run must use the same results file. Confirmed
charges plus outstanding maximum costs plus the next call and protected
verification balance must fit every cap. Only Deepline requests marked
`entity_type: "email_validation"` consume the verification allowance. Use that
metadata only for a validation tool described in this run, never for discovery.
`spend_receipt` identifies the ledger entry. The helper records its route ID,
accepted-lead count and actual cost or retained bound, and marks email-validation
actions so the stopping check distinguishes verification from other spending.
Both validator CLI modes cross-check these routes against the ledger when it
is present. Record every dispatched call in the batch before checking the next
batch or delivering results; `--check-stop` alone is still not full output validation.

A finite Deepline billing receipt on a determinate response settles the reported
currency, including an explicit zero charge. A USD-only receipt releases the
dollar reservation but keeps the credit bound until credit usage is known;
never infer actual credits from dollar pricing. Missing billing, uncertain
outcomes and ScrapingDog calls retain their bounds. Success or `no_results`
alone never releases money.
A charge above its bound is preserved and blocks further paid work. A reused
route ID is refused, even after a crash. Batch workers serialize ledger writes
within one process. A lock conflict with another process still fails without
sending; retry that local refusal only after the active writer finishes. Never expire a
lock automatically: after an interrupted write, inspect the ledger and receipts
and confirm there is no writer before removing its stale `.lock` file.

The ledger cannot be reinitialized over existing spend or have its caps raised
by editing report totals. Start it before paid work; migrating an old paid run
or adjusting frozen limits requires explicit billing reconciliation. Do not
delete reservations, reset the ledger, or bypass the adapters with raw CLI/HTTP.
This is a local execution guard, not a security sandbox against code or callers
that can alter its files or use provider credentials directly.

## Response files

Both adapters accept `--output-file <new-path>`. Use a distinct path per route
in the run's receipt directory. The parent directory must already exist.
The adapter checks the destination before dispatch and refuses existing files.
It atomically saves the full redacted provider body before normalization, then
adds the normalized result. Stdout remains the existing single JSON response.
The saved `provider_response` includes Deepline exit code/body/stderr or
ScrapingDog HTTP status/body; candidate output limits do not truncate this copy.
Existing transport size and timeout bounds still apply.
Non-finite or malformed JSON is retained as redacted diagnostic text, never
promoted to results. Non-finite input is rejected before dispatch. Available
partial Deepline stdout/stderr is also retained after a timeout; it is not a
successful provider result and must not promote an email or trigger a retry.
Interrupted ScrapingDog responses retain available bytes with `incomplete: true`;
neither broken chunks nor a short declared body is a successful empty result.
JSON output escapes non-ASCII text so receipts and stdout also work with narrow
terminal encodings. Invalid UTF-8 CLI bytes are preserved as escaped diagnostic
text and yield a response error, not candidates.

`receipt_status` is `pending`, `response_received`, or `complete`; these describe
file processing, not provider success or billing. A local input failure has
`error_stage: request`; an unrecognized response has `error_stage: response`.
An explicit remote schema error may have `error_stage: provider`. Do not assume
every schema error means the request payload was wrong. Before a paid execute,
compare required fields, types, native limits and cost inputs with the freshly
discovered descriptor; the adapter does not implement every provider's schema.

A failed final save returns a nonzero exit code and `receipt_error` while
preserving the normalized stdout and any previously saved raw response. Check
those artifacts and billing before recovery; never rerun a possibly paid call
merely to recreate a file. File output does not spend credits or retry providers.

```bash
python3 .agents/skills/lead-sourcing/scripts/deepline.py \
  --input '{"operation":"search","query":"small textile wholesalers"}' \
  --output-file 'reports/<run-id>/receipts/catalog-1.json'
```

## Credentials and artifacts

Use the environment or connected credential store. Record only availability,
never a value:

- `DEEPLINE_API_KEY` and optional `DEEPLINE_HOST_URL`.
- `DEEPLINE_BIN` (optional path to the Deepline CLI binary).
- `SCRAPINGDOG_API_KEY`.

For each run, deliver `reports/<run-id>/report.md`,
`reports/<run-id>/results.json`, and `reports/<run-id>/leads.xlsx`. Include the
request, hypotheses, route commands and filters, pilot observations, statuses,
route cost bases, confirmed and maximum credits, Deepline dollar cost and cost
per accepted lead, accepted evidence, contact selection, and rejected or
unresolved rows with stable reasons. Keep provider receipts separate from output state. Never
infer evidence from memory or present an unverified company or contact as final.
