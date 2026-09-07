# Shared adapter I/O

Read once before the first provider call, and revisit for receipt or transport
failures. This is the shared credential, response-file, and recovery contract;
provider-specific inputs and statuses live in [Deepline](deepline-adapter.md)
and [ScrapingDog](scrapingdog-adapter.md). Examples beginning with `.agents/`
run from the repository root.

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

For each run, write exactly `reports/<run-id>/report.md`,
`reports/<run-id>/results.json`, and `reports/<run-id>/leads.xlsx`. Include the
request, hypotheses, route commands and filters, pilot observations, statuses,
route cost bases, confirmed and maximum credits, Deepline dollar cost and cost
per accepted lead, accepted evidence, contact selection, and rejected or
unresolved rows with stable reasons. Keep provider receipts separate from output state. Never
infer evidence from memory or present an unverified company or contact as final.
