# Shared adapter I/O

Read once before the first provider call, and revisit for receipt or transport
failures. This is the shared credential, response-file, and recovery contract;
provider-specific inputs and statuses live in [Deepline](deepline-adapter.md)
and [ScrapingDog](scrapingdog-adapter.md). Examples beginning with `.agents/`
run from the repository root.

## One attempt

Use `scripts/run_attempt.py <results.json> --input-file <attempt.json>` for normal
dispatch. It composes the existing wrappers, budget guard and route recorder;
there is no new provider or orchestration service. Initialize the run and paid
ledger below first. The attempt file contains one action and its wrapper input:

```json
{
  "action": {
    "id": "catalog-discovery-1",
    "scope": "discovery",
    "phase": "account_discovery",
    "approach": "capability-discovery",
    "description": "Find multilingual product-page and research capabilities",
    "provider": "deepline",
    "paid_calls": 0,
    "cost_upper_bound_credits": 0
  },
  "request": {"operation": "search", "query": "multilingual niche webshop product research"}
}
```

For provider execution, use a freshly described tool and its native payload.
Every Deepline `execute` and ScrapingDog call sets `paid_calls: 1` and a priced
whole-call bound, even if that bound is zero. The helper adds `spend`; do not
provide a separate ledger or reset its limits. Put `phase` and `scope` on each
action; contact phases require a non-excluded company with passing account
evidence in accepted rows or contact-stage unresolved rows.

Use stable `approach` labels describing the source family and search/evidence
strategy, not tool names, batch numbers or cosmetic rewordings. The helper hashes
the actual request, so changing a route ID or approach label cannot repeat a
possibly billed request. Two completed substantive attempts without new verified
milestones require a changed approach. Catalog reads do not count as progress.

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
unknown charges keep provider capacity unknown. It never marks the frontier complete.
Each saved response also retains the action and redacted input in `attempt`,
so recovering a damaged draft does not require inventing scope or approach labels.
If only raw response bytes survived, normalize that receipt locally first. A
pending/unknown remote outcome is not permission to retry. Async research jobs
need their documented result-retrieval call, not another job submission.
For a freshly described **free job-status getter only**, mark its action
`status_read: true` with a zero cost bound. Repeated reads of the same job are
allowed only after a saved `partial` status response with a zero cost bound;
pending transport, failures and job submissions remain protected. Respect the
provider's polling interval and applicable read limit; never label submission
or enrichment as a status read. These calls still use the guarded ledger;
missing actual charges remain unknown, even when the reserved bound is zero.

For built-in public-web tools, set `provider: "public_web"`, zero cost/calls and
use `--plan-only`. Execute the planned search/read through the available tool.
Update the returned receipt file with the observed normalized `status`,
`operation`, and `results` array, retaining the helper's provider, fingerprint,
attempt and progress metadata, then use `--complete`. This path records evidence; it
does not invent a browser, scrape or API response.

The helper never infers qualification or market exhaustion. Assess the saved
evidence and submit company/route decisions together with `--review-file` below.
Full strict validation remains required before delivery.

The attempt CLI prints normalized provider results once, with the full receipt
path. Repeated progress snapshots, request metadata and duplicate evidence stay
in that receipt. Inspect specific saved fields when needed; the CLI view does
not truncate or replace the underlying evidence or accounting.

## Save a review

After a completed batch, write one review file. Use existing company rows and
their full evidence; `state` is `accepted`, `unresolved` or `rejected`. Unresolved
rows retain `stage: "account"` or `"contact"` and the missing facts in
`reason_text`. A route review needs only its ID and your reason that this exact
source has been checked. For example, closing a search without new candidates:

```json
{
  "routes": [{"route_id": "checked-search", "reason": "All saved results were reviewed; none establishes the requested recent project signal."}]
}
```

Add company decisions as `"companies": [{"state": "unresolved", "row": <full row>}]`
and concrete new actions in `next_actions` when needed. These keys are optional;
omitted companies and routes are untouched. Then run:

```bash
python3 .agents/skills/lead-sourcing/scripts/run_attempt.py \
  reports/<run-id>/results.json --review-file reports/<run-id>/review.json
```

One atomic update saves the selected company rows, closes reviewed routes,
retires their completed actions, removes speculative follow-ups for reviewed
parked/terminal companies, and refreshes counts and cost summaries. Receipts,
ledger charges and the saved request remain intact. The CLI response includes
`request_file` and the next decision, without repeating the full request.
No separate route-closing script, manual summary edits or duplicate stop check
is needed. `--status` includes the authoritative request for setup/resume,
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
Each file uses the same `action`/`request` format above:

```bash
python3 .agents/skills/lead-sourcing/scripts/run_attempt.py \
  reports/<run-id>/results.json --batch-files \
  reports/<run-id>/check-a.json reports/<run-id>/check-b.json reports/<run-id>/check-c.json
```

Alternatively, save those 1-3 objects as a JSON array in `batch.json` and pass
`--batch-files reports/<run-id>/batch.json`. Use either one array file or
individual attempt files; the same independence and budget checks apply.

Give each action a unique route ID and its canonical company domain as `scope`.
Batch mode accepts account verification, contact discovery, contact verification
and email validation. Keep discovery pilots on the single-attempt path. Choose
only independent work: a company's buyer lookup waits for saved passing account
evidence, and email validation waits for its buyer/address checks. Deduplicate
aliases and owner groups before choosing the batch; do not run redundant provider
requests for the same company at once.

The helper plans serially, runs up to three provider calls concurrently, and
records results serially. Each call has its own receipt. Budget reservations and
settlements share the existing ledger and are serialized; pending costs still
count against all caps and the verification reserve. A refused or failed member
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
not invoke the built-in browser. Save observed responses to their own receipts
and run `--complete` for each serially.

Wait for the batch to finish, then save its decisions with `--review-file` and
use the returned next step. Batch completion alone does not qualify leads.
Use one check when only one is ready or a provider requires serial access; a
rate limit is a reason to reduce concurrency, never to increase retries.

## Paid-call budget

Before the first paid call, create `results.json` with the normalized request,
empty `accepted`/`routes`, and the existing `budget.limits` and `budget.paid_calls`.
Initialize its ledger once:

```bash
python3 .agents/skills/lead-sourcing/scripts/budget_guard.py \
  reports/<run-id>/results.json \
  --verification-reserve-credits <priced-total-verification-allowance>
```

The shared cap defaults to USD 0.50 per requested lead. Supply `--max-usd` for
an explicit user cap, including zero. Deepline uses the configured USD 0.10 per
credit. An enabled ScrapingDog allocation also requires
`--scrapingdog-usd-per-credit` from the current plan; zero allocation disables
that provider. Existing provider and optional per-next-lead spending caps
remain independent. Email-required runs need an explicit verification reserve,
priced for the remaining leads and any planned fallback. Email opt-outs do not.

Paid-call counts are audit data, not limits. New runs omit `max_paid_calls`.
Legacy request, result, and ledger fields are ignored without rewriting the
ledger or resetting charges, pending reservations, or monetary limits.

Every Deepline `execute` and every ScrapingDog request now requires this
wrapper-only object alongside `operation`/`payload` or the other native inputs:

```json
{
  "spend": {
    "run_file": "reports/<run-id>/results.json",
    "route_id": "company-1-contact-1",
    "max_cost_credits": 1
  }
}
```

The number above is illustrative, not a price. Obtain a conservative **whole
call** bound from the live descriptor and bound provider-native rows/pages.
The wrapper output `limit` only truncates the preview; it does not limit billing.
Catalog `search`/`describe` stay unguarded because they do not execute providers.

The adapters atomically persist reservations in `results.json.budget.json`
before dispatch. All callers for a run must use the same results file. Confirmed
charges plus outstanding maximum costs plus the next call and protected
verification balance must fit every cap. Only Deepline requests marked
`entity_type: "email_validation"` consume the verification allowance. Use that
metadata only for a freshly described validation tool, never for discovery.
`spend_receipt` identifies the ledger entry; record its route ID, accepted-lead
count, and actual cost or retained upper bound in the usual result fields.
Mark email-validation `next_actions` with the same `entity_type` so the stopping
check can distinguish verification from other spending.
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
