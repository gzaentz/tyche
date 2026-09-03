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
for the exact input, output, and Excel workbook contracts.

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
- Treat `target_count` as the completion condition. While accepted companies
  remain below it, refill one company or contact candidate at a time from a
  changed route, query, page, tool, or provider. Do not use a fixed 5x
  multiplier or any other fixed over-fetch.
- If `contact_role_groups` is present, search and rank its `primary` roles
  first. Use `secondary` roles as valid fallbacks when no primary-role contact
  passes; a secondary-role contact may fill `primary_contact` and must not be
  rejected only because it is secondary. Set the optional contact
  `role_group` to `primary` or `secondary` when the group is known. The output
  slot name `primary_contact` is separate from this role group.
- Maintain a route frontier for paid and no-cost public-web work. Each concrete
  route/query path is `untried`, `continuable`, `exhausted`, or `blocked`. A
  failed or uncertain provider call blocks automatic retry of that call, but it
  does not end the run while another route is untried or continuable. Mark a
  provider error or uncertain outcome as `blocked`, never `exhausted`. Keep the
  frontier append-only: add paths and update states, but never remove a path.
- Give each concrete attempt or continuation a unique route ID. A failed
  attempt receipt may share its ID only with the outcome for that same failure;
  a later continuation always needs a new ID.
- Record actual provider usage only from a usage or billing receipt. If a paid
  route's actual cost is unavailable, store `null` for that route and provider
  spend, mark budget status and provider capacity `unknown`, and keep planning
  estimates separate in the report. Never present an estimate as actual spend.

## Inputs and workflow

The normalized request must state the target count, ICP and exclusions,
geography, buying-signal kinds and freshness window, requested roles, optional
contact fields, and per-provider budget caps. It may set a one-to-three contact
target per company, `signal_match_mode` (`any` or `all`, default `any`),
per-signal `min_age_days`/`max_age_days`, run ID, and as-of date. Validate that
each signal's lower bound is no greater than its upper bound. It may also set
`contact_role_groups` with `primary` and `secondary` role arrays. Keep the
required `requested_roles` as their deduplicated union for compatibility. If
groups are present, search primary roles first and use secondary roles only as
valid fallbacks; do not turn a secondary fallback into a contact false
negative. Resolve obvious company identity ambiguity before paid work.

1. Record the request, assumptions, signal hypotheses, route frontier, and
   budget. Seed the frontier with materially different discovery paths for the
   requested signals, including available first-party, broad-discovery, and
   provider routes; one route may cover several signals.
2. Discover live provider capabilities and run bounded, materially different
   pilots. Record every attempted route, including public-web queries. Expand
   productive routes within the remaining provider caps and add useful new
   query or continuation paths to the frontier as they are discovered.
3. Verify account fit and signal evidence, then deduplicate by canonical domain.
   Keep failed candidates with a stable rejection reason and provider failures
   as unresolved; never turn either into a silent miss.
4. As each company passes the account gate, look up contacts only for that
   accepted company/domain. If role groups are present, search and rank the
   primary group first, then search the secondary group if no primary-role
   contact passes. Target up to three relevant candidates, select one output
   primary, and retain up to two others as backups. A valid secondary-role
   contact can be the output primary when no primary-role contact passes; mark
   it with `role_group: "secondary"` when known. If only one current contact
   passes, keep the company accepted and record the backup shortfall. If no
   approved role passes, record the company as unresolved.
5. Refill from a changed route whenever an account or contact candidate fails
   a gate. Continue while the accepted count is below the target and the
   frontier contains an `untried` or `continuable` route. Do not infer route
   exhaustion from one failed provider, one empty query, or an unchanged page.
6. Stop only at the target or at an auditable terminal condition. For a
   shortfall, every frontier item must be `exhausted` or `blocked`, and the stop
   audit must attest that the seeded frontier is complete and state why each
   blocked item cannot run. `no_productive_route` requires at least one
   attempted route to be exhausted; use `provider_stop` when all routes are
   blocked. `budget_exhausted`
   additionally requires that neither paid provider can make another bounded
   call. Never exceed a hard cap to reach the target.
7. Write `results.json`, load the harness workspace dependencies, and generate
   `leads.xlsx` with the returned Node and node_modules paths:
   `<node> scripts/export_xlsx.mjs <results.json> <leads.xlsx> --node-modules
   <node_modules>`. Write the report. The workbook library is supplied by the
   Codex harness; do not add it as a project dependency. Do not hand-build the
   XLSX archive or reorder columns. Validate all artifacts against the full
   output contract. Then run the separate completion validator with
   `python3 scripts/validate_run.py <path-to-results.json>` from this skill
   directory. If validation reports an actionable frontier item or incomplete
   stop audit, continue the run instead of presenting it as complete.

## Gates, statuses, and artifacts

The account gate requires trimmed, non-empty canonical `company` and `domain`,
an `account_fit` object with an explicit ICP `fit_claim`, and a separate
`signal_evidence` object with a current `signal` claim. Each object requires its
own evidence URL, date, date basis, text, and source; the URLs and sources may
differ. Each URL must identify the same company and substantiate its own claim;
a search-results page, profile-only fit fact, stale date, or unsupported
inference fails the relevant gate. Resolve relative dates from retrieval time
and retain the original wording in the report. When `qualification_checks` is
present, record each criterion as `pass`, `fail`, or `unknown` with its
`required`/`preferred` importance and an evidence array. Reject only an
explicit failure of a required criterion; keep an unknown required criterion
as unresolved so missing evidence does not become a silent false negative.

The contact gate requires `full_name`, current title, requested-role match,
company/domain match, a person-identifying URL, and evidence URL/date/text that
show the role is current at that company. Run this gate before email/phone
lookup. Only `ok` or `partial` provider responses can supply candidates; all
provider statuses and stable reasons belong in the receipts, while output
`accepted`, `rejected`, and `unresolved` remain separate states.

Write `reports/<run-id>/report.md`, `reports/<run-id>/results.json`, and
`reports/<run-id>/leads.xlsx`. The report must contain the request, assumptions,
hypotheses, route and evidence receipts, pilot observations, costs, statuses,
accepted rows, rejected rows, unresolved rows, contact selection, and stop
reason. For a target shortfall it must also show the full route frontier,
continuation decisions, remaining call capacity, reviewed-company counts, and
the reason each remaining route is exhausted or blocked. Do not store
credentials or raw secrets.

The workbook is the sales-ready primary-contact view. Its `Leads` worksheet
uses the exact fixed header in the output contract. Company and contact fields
that are not verified stay blank; their absence does not become a qualification
failure unless the input explicitly requests that contact field. Keep full
evidence, backup contacts, run status, and rejection details in `results.json`
and the report.

This is a small direct-wrapper workflow. It has no `Sourcing_model` or `pp`
runtime dependency, browser harness, server, database, queue, CRM write,
outreach action, required subagent, or hidden API. Do not add one to complete a
run.
