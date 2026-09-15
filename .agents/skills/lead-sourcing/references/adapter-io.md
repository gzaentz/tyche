# Shared adapter I/O

Use the native tools for normal sourcing. The later CLI sections are diagnostics
for specific tool failures and compatibility with runtimes without native tools.
This is the shared credential, response-file, and recovery contract;
provider-specific inputs and statuses live in [Deepline](deepline-adapter.md)
and [ScrapingDog](scrapingdog-adapter.md). Examples beginning with `.agents/`
run from the repository root.

## Native tools

The launcher binds these tools to the assigned `results.json`; callers supply
research choices, not paths, route IDs or accounting envelopes.

| Tool | Agent input | Code handles |
| --- | --- | --- |
| `tyche_start` | Interpreted `request`, authorized `max_usd` if supplied | Original clock, files, defaults, ledger, priced email reserve; safe resume |
| `tyche_lookup` | `checks` (1–3): `target`, `phase`, `purpose`, `tool`, `inputs` | Cached live description, schema checks, whole-call bound where known, reservations, dispatch, receipts |
| `tyche_review` | Changed company fields, evidence refs, explicit decisions/source reviews; optional observed `web` | Existing company updates, authoritative LinkedIn/email fields, bookkeeping |
| `tyche_inspect` | No arguments, or `target`, `ref`, `tool`, `query`, `recover` | Compact state, saved request or detail, catalog search, local receipt recovery |
| `tyche_finish` | No arguments for review; then `review_ref` and research `commentary` | Mechanical preflight, claims beside saved source excerpts, strict export/readback/preview and cost summary |

At start, supply the LLM-selected `contact_role_groups` without repeating
`requested_roles`; code saves their combined list. A request without groups still
needs `requested_roles`. Explicit conflicting lists remain errors.
Save each requested signal's `importance` (`required` or `preferred`) and any
supplied `product_service` with its `description` and `perspective` (`seller`
or `target`). These are the LLM's interpretation of the current request.
The launcher binds the original request file and saves its text once as
`request.original_text`. Compare it with the interpretation before paid research.
Every non-signal must-have belongs in `icp.required_attributes` and needs its own passing,
required evidence check before contact work or delivery.

New runs check the mandatory Harvest company/profile tools and price the email
verification reserve through free catalog reads before initializing research.
The saved descriptions are reused by lookups. If a required tool is unavailable
or its price has no verified bound, report the prerequisite to the monitor and
stop this invocation. More company searches or finalization retries cannot fix
it. A retry refreshes failed free catalog reads while preserving the clock.

`target` is the canonical company domain or `discovery`. Phases are
`account_discovery`, `account_verification`, `contact_discovery`,
`contact_verification`, and `email_validation`. `provider` defaults to Deepline.
For recognized email finders and validators, omit `phase`; code derives it from
the selected operation without changing providers or spending eligibility.
For ScrapingDog, pass `provider: "scrapingdog"` and its wrapper input in `inputs`.
`approach` may name a stable strategy. Unknown pricing still needs a verified
whole-call `max_cost_credits`; ScrapingDog also needs its plan conversion at start.
A caller-supplied bound cannot undercut a known catalog price. Code never chooses
a different provider, recipient, criterion or qualification judgment.

Lookup returns a route and result references such as `lookup-abc:0`. Inspect a
route to see its response status/results, or select a result and `field` for
more detail. Page a saved route with `offset`/`limit` and its returned `next_offset`;
long text also returns `next_offset`. `tyche_inspect(tool=...)` returns cached
native inputs, constraints/pricing and output field names. Long help and enum
lists have explicit detail paths; select those with `field` and page with
`offset`/`limit`. Code validates against the complete saved contract. Learn a
selected tool once and reuse its description. Catalog searches show usable tool
summaries first; explicitly non-callable entries stay in the saved receipt. Use
short provider or capability terms when a search has no callable matches.
Use `field` for a specific nested
tool, company or run field; `refresh: true` is only for a confirmed schema,
pricing or access change. Retained receipts and execution contracts remain complete.

Company decisions: `hold_account` (research missing fit), `qualify_account`
(ready for contacts), `hold_contact`, `reject` (supported mismatch), `accept`.
Example review inputs, with references selected from actual results:

```json
{"companies":[{"target":"example.com","decision":"hold_account",
  "reason":"Current funding stage still needs evidence",
  "company":{"ref":"lookup-company:0","industry":"<verified taxonomy parent>","sub_industry":"<verified taxonomy child>"},
  "account_fit":{"ref":"lookup-source:0","fit_claim":"<supported match to the requested business activity>"}}],
 "sources":[{"ref":"lookup-source","state":"exhausted","reason":"Reviewed the complete product page"}]}
```

Progress's `review_due` and finalization's `pending_sources` come from the same
open saved lookups, including discovery. Use `tyche_inspect(field="pending_sources")`
with `offset`/`limit` to page them. Review the existing receipts; do not repeat a
lookup just to record a decision. Closed aliases do not create review reminders.

Evidence refs expand into saved source/URL/date/text. Usually use `{"ref":"..."}`
and put the qualification judgment in its `claim`, without copying source fields
again. Inspect `requirements` and select an `attribute:N` or `signal:N` as a check's
`requirement_ref`. Code supplies its label and importance; no separate registry
or semantic matching service is used. For a new check, omit `criterion`, `signal`
and `importance`. Put multiple supporting sources
in that check's evidence array. Unknown kinds or conflicting importance are
input errors, not new requirements. Aviato funding refs retain the supplied announcement date and round name;
they do not decide which funding stage is current. For a requested company attribute,
a funding ref can use its verified, company-bound receipt without a public URL.
Keep the captured date/text unchanged and explain the stage judgment in `claim`.
Signals still require a source URL. Compatible `sources` decisions
for different results in one lookup are combined into its existing route review;
conflicting states require one explicit decision for that lookup.
Supply reviewed `text`, `date` and `date_basis` only when interpreting an event, distinguishing announcement
from completion. Store signals in `qualification_checks` using `criterion`,
`importance`, `status`, `claim`, `signal`, and `evidence`. Use `signal` only for
requested intent. Code derives the legacy primary `signal_evidence` field.
A replacement check without `signal` removes the old label; do not copy signal
facts into a second field. The workbook and final review use these same checks.
Contact-stage and delivery checks compare reviewed signal dates with the saved
request's time windows. Historical evidence can remain on an unresolved account;
it cannot be promoted as a current signal. Errors after a web observation was
saved return its reusable reference; correct the judgment without rewriting the observation.
Company/profile `ref` values must select the matched Harvest getter. Add industry,
subindustry and the two-sentence description as reviewed facts. For contacts,
supply requested role and role match; code derives the saved role group.
For email lookup, pass `contact_ref` with the selected profile reference and
omit routine name, company domain and LinkedIn inputs; code fills the native
fields from the verified receipt. Supply an exact email when validating it.
A later `primary_contact: {"email_ref":"lookup-validation:0"}` supplies the exact
address and verdict from the selected validation result. An existing different
email is a conflict; explicitly select the new email to replace it. Changing
people requires a new profile ref; changing email clears the old email evidence.
Selecting an eligible BounceBan result automatically retains its original
ZeroBounce receipt and links the fallback. Backup entries are full selections.

For built-in web tools, execute the chosen search/read, then send its observed
`status` and `results` with `target`, `purpose`, `query` and `operation` under
`web` in the review call. Reference the first observed page as `web:0:0` and the
whole observation in `sources` as `web:0`. The tool records this observation;
it cannot invoke or independently capture Codex's built-in browser. Replaying
the same observation is safe; replacing it with different content is rejected.

`recover` never redispatches: it finishes recording a saved normalized receipt.
An outcome marked `recorded: true` is already saved; repeated recovery cannot
settle unknown billing. At finish, one bounded read-only billing lookup can match
posted charges by request ID and operation. Unmatched, ambiguous or pending
charges keep their reservations. A later resume permits a fresh bounded read.
Original research receipts are preserved.
If only a pending or raw response survived, retain the reservation and reconcile
it locally through diagnostics. Never retry an uncertain paid call. Explicit
`sources` reviews retain the existing continuation/exhaustion rules; saving a
company does not exhaust search results or pagination. Selecting and saving a
successful single-result company getter, profile getter or email verdict closes
that individual lookup automatically. Pending jobs and multi-result lookups
retain explicit review. A `valid` email on a catch-all
domain stays valid; fallback eligibility is checked before spending.

Native email-validation results include `email_decisions`: receipt-derived
`usable`, `fallback_allowed` and the next step. `valid` with a domain catch-all
flag remains usable. Do not revalidate it with BounceBan or reject its company.
Email lookup/validation requires a previously saved matching Harvest profile and
requested-role review. Free pending-job recovery remains available.

`completion_candidates` is derived from saved qualified candidates and receipts.
Prefer completing these when affordable. A blocker allows the researcher to
choose different work; this advice neither reserves funds nor dispatches calls.

The native relay reconnects a confirmed-dead sandboxed child at most once per
invocation, with identical sandbox policy, checkout and run. Only pure saved-state
inspection is replayed automatically. After a lost lookup/review response, inspect
the saved run and recover receipts; do not resubmit uncertain paid work. A second
connection failure returns a clear operational block with its captured exit code.

`tyche_finish` returns `needs_research`, `review_required`, `needs_repair` or
validated artifact paths. Mechanical errors and pending source reviews appear
before a review reference is issued. On `review_required`, compare the company's
claims with `sources`, which contains excerpts from the saved receipts, then
review dates, meaning and writing before passing `review_ref`. Full excerpts are
available through ordinary result inspection. `inspect(target=..., field="evidence_review")`
provides the same view during account research. Changes
to research invalidate that reference. Correct named errors through `tyche_review`
and finish again. Source observations for accepted companies can still be saved
after reaching the target; corrections preserve verified contacts and emails.
The review approval is saved for that exact state. If export is interrupted,
code can retry it once when the worker ends without repeating research or
approving an unreviewed snapshot. Do not repeat unchanged failing calls.

`capture_method` distinguishes adapter-saved provider responses from
`agent_recorded_web` excerpts. For public web, save a short source passage with
its qualifiers intact; keep interpretation in the company check's `claim`.
If an earlier saved excerpt is a paraphrase or omits decisive context, reopen the
source before relying on it for final review. A saved summary is not independent
confirmation of its own claim.

The success response includes a concise cost summary; final model usage is still
refreshed by the launcher after exit. Use `inspect(field="costs")` for saved costs.

Older abbreviated signal labels are not automatically guessed or accepted.
On resume, inspect requirements and explicitly update the existing criterion
with its selected `requirement_ref`, reviewed claim, status and existing evidence.
Keep `criterion` unchanged to replace that check; omit the old `signal` label.
This preserves the request, receipts and ledger while applying current evidence
and date checks. An ambiguous or unsupported mapping remains unresolved.

## Diagnostic CLI

Use `-` to supply JSON directly on stdin, normally with a quoted heredoc as
shown below. The helper saves durable records; routine lookups and reviews
do not need temporary input files. Correct the named field in an error before
reading implementation code.

## Start or resume

The LLM interprets the ICP, signals and role priorities. Put that
[input request](output-contract.md#input-contract) under `request` in a setup
object; supply it as UTF-8 JSON on stdin with `--start-file -`:

```bash
python3 .agents/skills/lead-sourcing/scripts/run_attempt.py \
  reports/<run-id>/results.json --start-file -
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

Use `--lookup-file -` with one research lookup or an array of up to
three independent lookups. This composes the existing wrappers, budget guard
and recorder; it does not choose a research strategy. For free catalog search:

```bash
python3 .agents/skills/lead-sourcing/scripts/run_attempt.py \
  reports/<run-id>/results.json --lookup-file - <<'JSON'
{"request": {"operation": "search", "query": "multilingual product-page research"}}
JSON
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
Contact phases retain the existing passing-account-evidence gate.
`review_due` returns a count and up to three company scopes with completed
research awaiting review. It is a reminder, not another qualification rule.

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

For built-in public-web tools, plan a single discovery pilot as an object:

```bash
python3 .agents/skills/lead-sourcing/scripts/run_attempt.py \
  reports/<run-id>/results.json --lookup-file - --plan-only <<'JSON'
{"provider":"public_web","scope":"discovery","phase":"account_discovery",
 "purpose":"Find announcements matching requested signals","approach":"official-announcements",
 "request":{"operation":"search_query","query":"<query derived from the current ICP>"}}
JSON
```

Code supplies zero cost/calls and the route ID. Execute the planned search/read
through the available web tool. Include its observed response in the route's
`response` when [saving your review](#save-a-review), together with company
findings. Supply observed status/results only; receipt metadata comes from code.
`operation` is optional but must match the plan; `error` may describe a failure.
Never label a failure `no_results` or a pending outcome complete. Saving a review
does not invoke a browser or provider.

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

Save each company decision when made, including supported rejections and
unresolved gaps. Refine the record as new facts arrive. A `companies` item
identifies `scope` and only the fields being updated. One call can also save
the observed web response and close its reviewed route:

```bash
python3 .agents/skills/lead-sourcing/scripts/run_attempt.py \
  reports/<run-id>/results.json --review-file - <<'JSON'
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
  "routes": [{"route_id": "<returned-route-id>",
    "response": {"status":"ok","results":[{"url":"https://example.com/news","text":"Observed announcement text without a date."}]},
    "reason": "Reviewed the announcement; its date is not established."}]
}
JSON
```

`company` updates the supplied factual fields. `qualification_checks` updates
one judgment per `criterion` name, ignoring capitalization and extra whitespace.
Supply importance, status, claim and the selected current evidence explicitly;
that evidence replaces the previous selection. Omitted `signal` metadata and
unrelated checks remain unchanged, and original provider receipts stay saved.
Duplicate criterion updates or multiple saved matches require reconciliation;
code does not decide which judgment is correct or whether the company fits.

`account_fit`, `signal_evidence`, `intent_details`, `primary_contact` and
`backup_contacts` are complete replacements when supplied, and untouched when
omitted. Review new contacts and source identities before replacing them.
State defaults to the saved state (new companies start unresolved); set
`state: "accepted"` or `"rejected"` explicitly. Acceptance retains all existing
evidence/contact gates; a missing fact cannot become a rejection without an
evidenced required mismatch. Set `stage: "contact"` after account review passes,
with remaining buyer gaps in `reason_text`.

`response` is optional and only for observed public-web results; provider
responses are already saved by their lookup. All attached responses are checked
before any are written. Responses persist before the company/route review:
if that review fails or is interrupted, correct and resubmit the same review.
Already saved responses stay immutable; never repeat research or a paid call
to repair a save. `companies`, `routes` and `next_actions` are optional;
omitted records remain intact. Execute your next choice with `--lookup-file -`;
supply `next_actions` only for concrete work that needs saving.

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

### Compatibility and recovery

File paths remain supported instead of `-`. Existing `--input-file`
action/request envelopes, `--batch-files` and complete `{state, row}` reviews
remain available for older callers. Use the concise forms above for new work.
`--complete <route-id>` recovers an already saved response without dispatch;
`--complete <route-id> --response-file -` can attach an observed web response
alone. Neither recovery path can replace an existing response or change its
run identity.

## Concurrent company checks

After the pilot, use one agent and up to three ready checks for different
companies. Use fewer when fewer checks are ready or the remaining lead shortfall
is smaller. Batch ready checks instead of calling them one by one; different
companies may be at different phases. Do not wait to fill a batch.
Supply the same concise lookup objects above as an array on stdin:

```bash
python3 .agents/skills/lead-sourcing/scripts/run_attempt.py \
  reports/<run-id>/results.json --lookup-file -
```

All paths use the same execution, independence and budget checks.
The helper validates every input with the existing provider
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
not invoke the built-in browser. Include observed responses in one `--review-file -`
call with company findings and source reviews. Separate response files are only
needed for targeted diagnostic recovery.

Wait for the batch to finish, then save its decisions with `--review-file` and
use the returned next step. Batch completion alone does not qualify leads.
Use one check when only one is ready or a provider requires serial access; a
rate limit is a reason to reduce concurrency, never to increase retries.

## Paid-call budget

Use [start/resume](#start-or-resume) to initialize both records before paid
research. Keep the same results path throughout the run.

The shared cap defaults to USD 0.50 per requested lead. Supply setup `max_usd` for
an explicit user cap, including zero. Deepline uses the configured USD 0.10 per
credit. Omit `request.budget`, or supply `{"hard_stop":true}` with optional
spending restrictions, to let code derive the default Deepline allocation from
that cap. Explicit provider credit caps, including zero, remain binding.
An enabled ScrapingDog allocation also requires
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
every schema error means the request payload was wrong. Native lookup checks
the saved contract and cost before dispatch. Use its exact field errors to
correct inputs; inspect only the missing constraint. Refresh a descriptor when
there is evidence it changed. The adapter does not implement every provider's
schema, so provider-specific response errors may still need investigation.

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
