# Deepline adapter

For discovery, read [Capability discovery](#capability-discovery). Before the
first Deepline execution, read the core wrapper contract below and
[shared I/O](adapter-io.md), unless already loaded. The core contract ends at
Email validation. Read the [ZeroBounce gate](#deepline-zerobounce-email-gate)
before validating email; read [BounceBan fallback](#bounceban-fallback) only
when catch-all/unknown requires it. No other validator replaces these gates.

## Deepline wrapper

`scripts/deepline.py` adapts the installed Deepline CLI. It discovers tools,
schemas, prices, and bounded company, signal, or contact candidates. A catalog
hit is not company or contact evidence. A disconnected tool is not an empty
result.

### Capability discovery

Search the live catalog with narrow seeds that match the hypothesis. These are
search seeds, not fixed IDs; use only the tool ID returned by the current search:

- `companies with current hiring or job postings`
- `companies with recent funding, press, or product news`
- `companies with paid advertising or campaign activity`
- `companies with recent patents, facilities, openings, or expansion`
- `companies with official video or event activity`
- `company firmographic search by industry geography and size`
- `people by company domain and requested job title`
- `current title roster or leadership team by company domain`
- `ZeroBounce validate one known email address`

Use the [evidence-gap map](tools.md#choose-by-evidence-gap) and its linked
capability reference for broader seeds. Search by provider plus the missing capability. Do not restrict discovery
to catalog categories: useful public-data reads can be labeled `admin`, while
an `automation` result can be a paid research job. Inspect the actual contract
and side effects; neither category grants execution permission.

For each selected tool, call `describe` immediately before `execute` and record
its live input schema, connection state, and price. Prefer a title-roster tool
for nuanced roles. If that is unavailable, use broad function and seniority with
the full user-approved title family. Ignore non-callable or monitor-only search
hits for this one-shot workflow; do not deploy monitors. Do not use a CEO as an
automatic fallback.

```bash
python3 .agents/skills/lead-sourcing/scripts/deepline.py --input '{"operation":"search","query":"companies with current hiring or job postings"}'
python3 .agents/skills/lead-sourcing/scripts/deepline.py --input '{"operation":"describe","tool":"<id returned by search>"}'
python3 .agents/skills/lead-sourcing/scripts/deepline.py --input-file 'reports/<run-id>/requests/<route-id>.json'
```

The execute request file includes `operation`, `tool`, `payload`, and the
required [spend context](adapter-io.md#paid-call-budget). `execute` is paid.
A company-discovery pilot has one paid call and at most 10 returned rows;
contact lookups request 1-3 relevant people per missing company.
The wrapper `limit` is 10 or less and truncates normalized output only;
set provider-native result/count and page or cursor fields from the live schema,
then bound the cost before execution. Inspect the live price first; expand only
when rows are relevant, diverse, and evidentiary. The wrapper invokes
`deepline tools search`, `describe`, or
`execute`, writes a temporary payload file, redacts secrets, and emits one JSON
object. Catalog calls default to 30 seconds (cap 120); execute calls
default to 240 seconds (cap 780). Never automatically retry an uncertain call.
After each execute, use billed usage as `cost_credits` with
`cost_basis: "actual"`. If billed usage is unavailable but the live description
gives a conservative bound for all calls recorded by the route, use
`cost_credits: null`, that total bound as
`cost_upper_bound_credits`, and `cost_basis: "estimated"`. Use `unknown` with
both values `null` only when neither value is available.
A typical or midpoint price is not a conservative bound. Use `unknown` when
unresolved pricing inputs can make the route cost higher.

When Deepline supplies billing, the adapter preserves finite, non-negative
`billing.credits_charged` and `billing.cost_usd`. These are observed amounts,
not estimates inferred from row counts. Missing billing stays missing; keep
the conservative bound until an actual charge is available.

The wrapper normalizes candidate fields to `company`, `domain`, `signal`,
`evidence_url`, `evidence_date`, `evidence_text`, `provider`, and `tool`.
Generic labels such as `search_result`, `web_page`, `company_profile`, and
`hiring` are discovery labels only. For web-search rows, `domain` can be the
source host; resolve the canonical company domain before acceptance.

For recognized event and post envelopes, the wrapper retains optional
top-level `pagination`, `meta`, and `links` metadata with secrets redacted.
HarvestAPI's known `pagination.paginationToken` is exposed separately as
`pagination.next_cursor`. Map this opaque cursor back to the live provider
input field only for an explicitly budgeted continuation; paging is never
automatic. Do not advance past rows that the wrapper trimmed without reviewing
them; request provider pages small enough for the output limit where supported.

HarvestAPI post rows retain the post URL, content, `postedAt`, and author.
Verify the author, company, and original versus reposted source; the author is
not automatically a current employee or buyer. A person-authored post does
not establish the author's employer, and a company-authored post does not
establish its canonical domain.

For JSON:API event responses, relationship names such as `company1` and
`company2` are retained in `related_companies`. Related companies are
candidates, not accepted companies: resolve the relationship and account gate
explicitly, never pick the first company. Use a linked source `published_at`
for `evidence_date` when available, keep any effective `event_date` separately,
and never treat ingestion fields such as `found_at` or `updated` as event
freshness.

### Deepline status contract

The wrapper emits exactly: `ok`, `no_results`, `partial`, `rate_limited`,
`auth_failed`, `quota_exceeded`, `timeout`, `schema_error`, `provider_error`,
or `config_error`. Only `ok` and `partial` can supply candidates. `no_results`
does not prove absence. Other statuses are unresolved provider outcomes; stop or
change route and retain `status`, `error` when present, `provider`, `operation`,
`tool`, and normalized `results` in the report.

## Email validation

### BounceBan fallback

Only after ZeroBounce returns `catch-all` or `unknown`, search the live catalog
for `BounceBan verify single email` and describe the returned tool. Execute once
with the exact email and `entity_type: email_validation`, reserving the current
price against the existing provider and paid-call caps, plus any explicitly
requested per-next-lead cap. Do not
pin the tool ID or price. Keep catch-all verification enabled. Default to
regular mode: deepverify assumes the email domain matches the current company
website, which is not safe for all verified brand/alias domains. No webhook
or outreach is needed.

Read raw `result`, not API `status`. Acceptance requires API `success` and
`result: deliverable`; risky/unknown is unresolved, undeliverable is rejected.
The adapter exposes the verdict as `email_status` while retaining raw fields.
Store status/result, optional score/time and the Deepline source in the
original receipt's `fallback` object. Never overwrite the ZeroBounce receipt.
Do not override invalid, do_not_mail, spamtrap or abuse. An unsuccessful or
uncertain call stays unresolved; do not retry it automatically or chain
validators. Choose another address or requested buyer instead.

An asynchronous `queue`/`verifying` response is `partial` with empty results
and `pending_verification` containing the existing job ID. It is not an empty
search or a deliverability verdict. Preserve this receipt. The model may
discover and describe the single-status retrieval tool and, only after
confirming it is free, retrieve that same job ID. Match the returned ID and
email before using a successful final verdict; save the completion receipt
separately and record the retrieval as a zero-paid-call route. The fallback
source still references the original paid verification route and tool.
Respect `try_again_at`, allow at most three status reads with at least 30
seconds between reads, and leave a still-pending job unresolved. Never create
another paid verification to recover a pending job. The adapter itself does
not poll or retry.

An outer transport/auth/provider failure must not be promoted by a nested
positive validator row. Only the explicitly recognized default send-policy
rejection may retain a non-positive ZeroBounce verdict for the normal gate.

### Deepline ZeroBounce email gate

When the effective contact fields include email, first find and verify the
person and current role. Then search the live Deepline catalog for a ZeroBounce
single-address validation capability. Select only a result whose current
description identifies ZeroBounce email validation, and call `describe` to
confirm its input, output, connection, and price. The catalog tool ID is runtime
data; do not copy a fixed ID into this skill.

```bash
python3 .agents/skills/lead-sourcing/scripts/deepline.py --input '{"operation":"search","query":"ZeroBounce validate one known email address"}'
python3 .agents/skills/lead-sourcing/scripts/deepline.py --input '{"operation":"describe","tool":"<current ZeroBounce validation tool from search>"}'
python3 .agents/skills/lead-sourcing/scripts/deepline.py --input '{"operation":"execute","tool":"<same described tool>","entity_type":"email_validation","payload":{"email":"person@example.com"},"limit":1}'
```

Use the exact payload names returned by the live description; the example
shows the current scalar shape but does not override the live schema. The
adapter preserves the provider row and exposes `email`, `email_status`, and
`email_sub_status` for a recognized scalar validation result. Link the final
receipt to a unique `email_validation` route and record actual usage.

Apply TYCHE's current gate to the explicit ZeroBounce status after trimming and
case normalization. `valid` passes directly. Reject `invalid`, `do_not_mail`,
`spamtrap`, and `abuse` without fallback. Only `catch-all`/`unknown` may receive
the single BounceBan check above. Unfamiliar statuses stay unresolved.
Continue with another discovered address or requested buyer.
A missing status, `no_results`, provider error, timeout, or other uncertain
call is unresolved and cannot support an accepted email. Do not retry an
uncertain paid validation call automatically. Deepline owns the provider
credential; do not require or read a direct ZeroBounce key.
