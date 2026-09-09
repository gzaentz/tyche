# Shared adapter I/O

Read once before the first provider call, and revisit for receipt or transport
failures. This is the shared credential, response-file, and recovery contract;
provider-specific inputs and statuses live in [Deepline](deepline-adapter.md)
and [ScrapingDog](scrapingdog-adapter.md). Examples beginning with `.agents/`
run from the repository root.

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
is present. Record every dispatched call before checking the next action or
delivering results; `--check-stop` alone is still not full output validation.

A finite Deepline billing receipt on a determinate response settles the reported
currency, including an explicit zero charge. A USD-only receipt releases the
dollar reservation but keeps the credit bound until credit usage is known;
never infer actual credits from dollar pricing. Missing billing, uncertain
outcomes and ScrapingDog calls retain their bounds. Success or `no_results`
alone never releases money.
A charge above its bound is preserved and blocks further paid work. A reused
route ID is refused, even after a crash. A lock conflict fails without sending;
retry that local refusal only after the active writer finishes. Never expire a
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
