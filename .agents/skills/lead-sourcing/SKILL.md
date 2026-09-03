---
name: lead-sourcing
description: Source evidence-backed companies with current buying signals and requested-role contacts using the local Deepline and ScrapingDog wrappers; use for company-first account lists, not contact-only enrichment or outreach.
---

# TYCHE lead sourcing

Use this skill for a company-first request: a target count, ICP, current buying
signal, and one or more requested contact roles. The run produces unique,
evidence-backed companies and, for each accepted company, one primary contact
plus zero to two backups. Read [references/tools.md](references/tools.md) for
live provider operations and [references/output-contract.md](references/output-contract.md)
for the exact input, output, and CSV contracts.

## Operating rules

- Keep the strategy open and the evidence gate closed. Form several materially
  different hypotheses (for example hiring, funding/news, paid activity,
  patents, facilities, official video, or firmographic fit) and change route
  when rows repeat or lack evidence.
- Source companies sequentially. Resolve and deduplicate canonical domains at
  the account stage. Contact lookup may use accepted company/domain rows only.
- Discover live Deepline capabilities with `search`, inspect a chosen tool with
  `describe`, and confirm its live input and price before every `execute`.
  Never invent or pin a Deepline tool ID.
- Pilot each material route with at most 10 returned rows and one paid call.
  Inspect rows, evidence, duplicates, misses, provider status, and cost before
  expanding. No automatic retry; a timeout or other uncertain paid outcome is
  unresolved and needs a different route.
- Do not begin contact lookup for a company until that company passes the
  account evidence gate. Start contact lookup as each company passes; there is
  no global account-count gate. For nuanced roles, prefer title-roster
  discovery. Otherwise use broad function plus seniority and the complete
  user-approved title family. A CEO is not an automatic fallback.
- Apply separate gates: an account needs current signal evidence that identifies
  the same company; a contact needs a current role and company identity that
  match the accepted company/domain and the requested role family. Apply the
  identity/current-role gate before any email or phone lookup.
- Request and retrieve email or phone only when the input asks for that field.
  Never fabricate a value or infer a current role from model memory.
- Keep accepted, rejected, and unresolved output states separate from provider
  statuses. `no_results` is not proof that a signal or contact is absent.
- Maintain an adaptive reserve and refill one company or contact candidate at a
  time after observed attrition, subject to budget. Do not source a fixed 5x
  multiplier or any other fixed over-fetch.

## Inputs and workflow

The normalized request must state the target count, ICP and exclusions,
geography, buying-signal kinds and freshness window, requested roles, optional
contact fields, and per-provider budget caps. It may set a one-to-three contact
target per company, run ID, and as-of date. Resolve obvious company identity
ambiguity before paid work.

1. Record the request, assumptions, signal hypotheses, route plan, and budget.
2. Discover live provider capabilities and run bounded, materially different
   pilots. Expand only productive routes within the remaining provider caps.
3. Verify account fit and signal evidence, then deduplicate by canonical domain.
   Keep failed candidates with a stable rejection reason and provider failures
   as unresolved; never turn either into a silent miss.
4. As each company passes the account gate, look up contacts only for that
   accepted company/domain. Target up to three relevant candidates, select one
   primary, and retain up to two others as backups. If only one current contact
   passes, keep the company accepted and record the backup shortfall. If no
   primary passes, record the company as unresolved.
5. Refill from a changed route when an account or contact candidate fails a
   gate. Stop when the target is met, no productive route remains, or a budget
   or provider stop condition is reached.
6. Write all required artifacts and validate them against the output contract.

## Gates, statuses, and artifacts

The account gate requires trimmed, non-empty canonical `company` and `domain`,
an `account_fit` object with an explicit ICP `fit_claim`, and a separate
`signal_evidence` object with a current `signal` claim. Each object requires its
own evidence URL, date, date basis, text, and source; the URLs and sources may
differ. Each URL must identify the same company and substantiate its own claim;
a search-results page, profile-only fit fact, stale date, or unsupported
inference fails the relevant gate. Resolve relative dates from retrieval time
and retain the original wording in the report.

The contact gate requires `full_name`, current title, requested-role match,
company/domain match, a person-identifying URL, and evidence URL/date/text that
show the role is current at that company. Run this gate before email/phone
lookup. Only `ok` or `partial` provider responses can supply candidates; all
provider statuses and stable reasons belong in the receipts, while output
`accepted`, `rejected`, and `unresolved` remain separate states.

Write `reports/<run-id>/report.md`, `reports/<run-id>/results.json`, and
`reports/<run-id>/leads.csv`. The report must contain the request, assumptions,
hypotheses, route and evidence receipts, pilot observations, costs, statuses,
accepted rows, rejected rows, unresolved rows, contact selection, and stop
reason. Do not store credentials or raw secrets.

This is a small direct-wrapper workflow. It has no `Sourcing_model` or `pp`
runtime dependency, browser harness, server, database, queue, CRM write,
outreach action, required subagent, or hidden API. Do not add one to complete a
run.
