# Detailed Workflow Rules

Use this skill for a company-first request: a target count, ICP, current buying
signal, and one or more requested contact roles. The run produces unique,
evidence-backed companies and, for each accepted company, one primary contact
plus zero to two backups. Use [tools.md](tools.md) to select a route and load
only that adapter's required sections. Read the exact input, output, and Excel
contracts by the phases in [output-contract.md](output-contract.md#read-by-phase),
not as an upfront bundle.

The main workflow is the five steps in [SKILL.md](../SKILL.md). This reference
preserves the detailed qualification, spending, receipt, and completion rules.
Command paths below are relative to the skill directory, not this reference.

## Operating rules

- Keep the strategy open and the evidence gate closed. Form several materially
  different hypotheses (for example hiring, funding/news, paid activity,
  patents, facilities, official video, or firmographic fit) and change route
  when rows repeat or lack evidence.
- Source companies sequentially. Resolve and deduplicate canonical domains at
  the account stage. Contact lookup may use accepted company/domain rows only.
- Discover live Deepline capabilities with `search`, inspect a chosen tool with
  `describe`, and confirm its live input and price before every `execute`.
  Never invent or pin a Deepline tool ID. Optional provider hypotheses such as
  PredictLeads events, HarvestAPI LinkedIn posts, TheirStack jobs/projects, or
  DiscoLike niche discovery are choices to test, not a mandatory fanout.
- Start with available no-cost company research; paid scraping is not free.
  Pilot company-discovery routes with at most 10 returned rows and one paid call.
  Once an account passes, buy only 1-3 relevant contacts for that missing company
  using native company/title filters and result limits. Do not buy a broad
  people batch to fill a few known company gaps. Price and protect the remaining
  email-verification work before expanding discovery or buying backups.
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
- Apply `contact_fields: ["email"]` when the input omits contact fields. An
  explicit empty array opts out, and an explicit phone-only array overrides
  the email default. Retrieve contact data only after the identity/current-role
  gate. Never fabricate a value or infer a current role from model memory.
- Before storing any email, discover and describe a current ZeroBounce email
  validation capability through Deepline, then validate the exact address.
  Do not pin its Deepline tool ID. Only an explicit, trimmed, case-insensitive
  ZeroBounce status of `valid` passes this gate. Reject `invalid`,
  `do_not_mail`, `spamtrap`, and `abuse` with `email_invalid`; retain all other
  statuses as `email_validation_unresolved`, except catch-all/unknown or a
  recorded [service failure](deepline-adapter.md#bounceban-fallback) may
  receive one budgeted BounceBan check through Deepline. Discover and describe
  it first; accept only API success plus result deliverable. Preserve both
  receipts using `email_validation.fallback`. Never override a hard rejection
  or chain fallbacks. A missing status, missing receipt,
  `no_results`, or failed or uncertain provider call cannot pass by itself.
  Charge each validation execution against the Deepline credit and shared dollar
  caps; retain the execution count for audit only.
- Keep accepted, rejected, and unresolved output states separate from provider
  statuses. Reuse the existing `stage`, `reason_code`, and
  `qualification_checks` fields. Every unresolved company must state a
  concrete next action or blocker in `reason_text`; a contact-stage unresolved
  result keeps the account evidence that already passed. Provider failures are not
  companies and must not be counted in reviewed or accepted company totals.
  `no_results` is valid only when the provider actually returned no results;
  an input error, response error, timeout, or uncertain response is not
  `no_results` and remains unresolved or blocked as appropriate.
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
  a later continuation always needs a new ID. Store
  `continuation_route_ids` on a route to cross-link future searches to the
  route that produced them. Do not mark a route exhausted while a promised
  continuation is unresolved; `continuation_exhausted` must reference
  successors that were actually resolved. Before stopping, review promising
  unresolved paths and record why each is no longer actionable.
- Record actual provider usage only from a usage or billing receipt. If a paid
  route's actual cost is unavailable, store `null` for that route and provider
  spend and mark budget status and provider capacity `unknown`. In version
  `1.1` and later, also record a route-total upper bound when the live plan provides one,
  with `cost_basis: "estimated"`; use `unknown` when no bound exists. Never
  present an estimate as actual spend.
- Treat `budget.max_deepline_credits_per_next_lead` as an optional hard cap,
  applied only when the user explicitly requests it. Do not add it to new
  normalized requests when omitted, and preserve historic caps and runs. Use
  5 credits as a nonblocking strategy-review warning when the field is absent;
  it is not a free allowance and does not authorize spending. When the hard
  cap is present, enforce it. Record `accepted_leads_before_call` on every paid
  Deepline route receipt, including uncapped runs, and group each route's actual
  cost, or its conservative upper bound when actual cost is unavailable, by
  that accepted-lead count. Route
  changes, rejected candidates, and failed lookups do not reset the group.
  Reset the allowance only after a complete accepted lead (company, signal,
  requested contact, and requested contact fields) is stored. Any stored email
  must pass the ZeroBounce gate; preserve explicit email opt-outs. Before every
  paid Deepline execution, add the route's conservative cost upper bound to
  the amount already charged to the current group. Do not run the call if that
  sum would exceed the requested allowance, or when the requested-cap route
  has no conservative cost bound. Keep the overall provider and dollar caps
  as independent hard backstops. Actual cost that is unavailable remains
  bounded or `unknown`, never zero or free. The shared
  [paid-call ledger](adapter-io.md#paid-call-budget) enforces these reservations
  in both adapters; the validator checks recorded costs after the run. Omitting
  this field does not invalidate legacy budget records.

## Inputs and workflow

### Default run budget

When the user supplies no spending budget, the total paid-provider allowance
is USD 0.50 multiplied by `target_count` (10 requested leads means USD 5.00).
Apply this default without asking for approval of the missing budget. An
explicit user spending budget overrides the default, including a zero budget;
preserve separately specified provider spending caps. Do not impose a paid-call
limit; call counts are audit data only. A strategy-review
threshold is not a spending budget or permission to increase one.

Before execution, allocate the shared dollar allowance into the existing
provider credit caps using current, conservative USD conversion rates. The sum
of the allocations must not exceed the shared allowance; never grant the full
allowance to each provider. Record the dollar cap, its default/explicit origin,
rates and allocations in `report.md`, and persist the credit caps in
`request.budget` and `budget.limits`. Give an unused provider a zero allocation.
If a provider's dollar cost cannot be bounded, do not spend on that route;
use a priced alternative or public sources. Do not assume prepaid credits are
free. Reallocation may use only the unspent balance, including reservations for
uncertain calls, and must preserve explicit provider caps and prior receipts.

Initialize the [paid-call ledger](adapter-io.md#paid-call-budget) once before
the first paid call. It persists the shared USD cap, provider credit limits and
verification reserve independently of editable report totals. Missing prices,
missing ledger state, and repeated route IDs block dispatch. Resume the same
ledger after interruptions; do not reset it or execute the raw CLI/HTTP to
work around a budget refusal. The initial implementation freezes its limits
for the run; reallocations require explicit reconciliation, not a new ledger.

This is one run-wide allowance based on leads requested, not leads delivered.
Rejected companies, retries, refills, continuations and model resumptions do
not reset or enlarge it. Before each paid call, include all prior charges or
conservative reservations plus the next call's bound. Stop that call if it
would exceed the shared cap or an independent provider spending cap.

This default governs sourcing-provider charges. Report model cost and combined
full cost separately under the skill's Full cost rules; do not claim that the
default bounds model charges. If the user explicitly caps model-inclusive cost,
honor that scope and reserve it before provider spending.

### Request normalization

The normalized request must state the target count, ICP and exclusions,
geography, buying-signal kinds and freshness window, requested roles, contact
fields, and per-provider budget caps.
Do not add `budget.max_deepline_credits_per_next_lead` when it is omitted; carry
it through only when the user explicitly requests a per-next-lead hard cap.
Email is required by default; apply `["email"]` when the field is omitted, while
preserving an explicit empty or phone-only override. It may set a one-to-three contact
target per company, `signal_match_mode` (`any` or `all`, default `any`),
per-signal `min_age_days`/`max_age_days`, run ID, and as-of date. Validate that
each signal's lower bound is no greater than its upper bound. It may also set
`contact_role_groups` with `primary` and `secondary` role arrays. Keep the
required `requested_roles` as their deduplicated union for compatibility. If
groups are present, search primary roles first and use secondary roles only as
valid fallbacks; do not turn a secondary fallback into a contact false
negative. Resolve obvious company identity ambiguity before paid work.
Numeric employee filters use inclusive range semantics: a company passes when
its verified count is between the requested minimum and maximum. Translate that
range to the provider's live field semantics. For discovery, include every
provider bucket that overlaps the requested range. For acceptance, one credible
count or range wholly within the requested bounds suffices. A bucket crossing
a boundary needs further verification, not automatic rejection.

1. Record the request, assumptions, signal hypotheses, route frontier, and
   budget. Seed the frontier with materially different discovery paths for the
   requested signals, including available first-party, broad-discovery, and
   provider routes; one route may cover several signals.
2. Discover live provider capabilities and run bounded, materially different
   pilots. Record every attempted route, including public-web queries. Expand
   productive routes within the remaining provider caps and add useful new
   query or continuation paths to the frontier as they are discovered.
   Initialize `stop_audit.route_frontier` with the planned paths. Use
   `scripts/record_route.py <results.json> --input '<JSON>'` before each
   execution with `{"frontier": <route-frontier item in state untried>}`.
   Immediately afterward, call it with the same identity fields and
   `{"frontier": <updated item>, "receipt": <route receipt>}`. The helper
   persists both atomically and rejects changed attempts or reused query IDs.
   It checks continuation links before saving and detects intervening file
   edits. Reopen an exhausted parent before reopening its child; close the
   child before closing the parent. All writers should use this helper's lock.
   An estimated cost may settle once to a receipted actual cost within its
   original bound; an actual charge cannot be rewritten through this helper.
   It does not run providers, infer exhaustion, or attest audit completeness.
   Check the live descriptor immediately before execution so input errors can
   be distinguished from provider response errors; do not add a redundant
   framework around the existing wrappers. Keep reviewed counts, budget and
   cost summaries current separately. During
   work, completion validation should reject actionable routes. If an old
   receipt cannot be recovered, retain that path as blocked with the audit
   gap stated explicitly; never invent row counts or rerun paid work silently.
3. Verify account fit and signal evidence, then deduplicate by canonical domain.
   Keep failed candidates with a stable rejection reason and provider failures
   as unresolved; never turn either into a silent miss.
4. As each company passes the account gate, look up contacts only for that
   accepted company/domain. If role groups are present, search and rank the
   primary group first, then search the secondary group if no primary-role
   contact passes. Retrieve 1-3 relevant candidates with a provider-native limit,
   finishing one missing company before purchasing another contact batch. Select
   one output primary, and retain up to two others as backups. A valid secondary-role
   contact can be the output primary when no primary-role contact passes; mark
   it with `role_group: "secondary"` when known. If only one current contact
   passes, keep the company accepted and record the backup shortfall. If no
   approved role passes, record the company as unresolved. After a contact
   passes, retrieve each requested contact field. For every email, search the
   live Deepline catalog for ZeroBounce validation, describe the selected tool,
   and execute it once against the exact address. Store the status and source
   receipt. Accept `valid` directly. Reject `invalid`, `do_not_mail`, `spamtrap`,
   and `abuse`; try another discovered address or current-role contact. For
  catch-all/unknown or an eligible recorded service failure, check once with a freshly discovered/described
  BounceBan tool within budget. Accept only successful deliverable with both
  receipts. Risky/unknown stays unresolved; undeliverable is rejected. If
  validation has another verdict, lacks a receipt, or remains uncertain, keep the
  contact unresolved and change route; do not accept it or retry the uncertain
  paid call automatically. Store an email on a backup only after the same
  validation gate passes.
5. Refill from a changed route whenever an account or contact candidate fails
   a gate. Continue while the accepted count is below the target and the
   frontier contains an `untried` or `continuable` route. Do not infer route
   exhaustion from one failed provider, one empty query, or an unchanged page.
6. Apply the [stopping check](output-contract.md#stopping-check) before each next
   action and before delivery. Reassess discovery and every unresolved company
   using different useful tools/sources. A completed attempt is not an exhausted
   search strategy. Missing evidence does not prove failed fit. Preserve
   unfinished routes when a verified budget/time limit ends the run; never close
   them merely to pass validation. No implicit timeout or minimum-spend target.
7. Write version `1.2` `results.json`. Follow the output contract's client-writing
   and taxonomy rules. Every route must include actual,
   estimated, or unknown cost fields. Run
   `python3 scripts/validate_run.py <path-to-results.json> --show-cost-summary`,
   copy `calculated_cost_summary` into the top-level `cost_summary`, and use the
   same exact or bounded values in the report. Load the harness workspace
   dependencies and generate
   `leads.xlsx` with the returned Node and node_modules paths:
   `<node> scripts/export_xlsx.mjs <results.json> <leads.xlsx> --node-modules
   <node_modules>`. Write the report. The workbook library is supplied by the
   Codex harness; do not add it as a project dependency. Do not hand-build the
   XLSX archive or reorder columns. Validate all artifacts against the full
   output contract. Then run the separate completion validator with
   `python3 scripts/validate_run.py <path-to-results.json>` from this skill
   directory. If validation reports an actionable frontier item or incomplete
   stop audit, continue the run instead of presenting it as complete.

## Qualification policy

Keep one model and the existing outcomes; do not add scores or gates.

- Separate user must-haves from preferences at normalization. Preserve explicit
  constraints, required signals and their windows; do not silently add them.
  Optional intent improves ranking, not eligibility. Record the distinction in
  the report and existing required/preferred qualification checks.
- Verify must-haves. Ordinary workflows may be inferred from sourced business
  facts when a plausible use case is enough for the request. Label the inference
  and its basis in existing evidence/prose; never imply observed pain, intent,
  incumbent tools or manual processes. Likely handling agreements does not
  establish paper signing. A specifically required workflow needs evidence.
- One credible source can suffice. Use the same standard for every candidate;
  match current responsibilities and seniority, not literal titles. Resolve
  material contradictions, not merely overlapping headcount ranges.
- Accept supported must-haves, even without optional intent. Keep missing
  must-haves unresolved; reject evidenced mismatches. Recover the specific gap
  through another source, signal or buyer within existing limits. A bad signal
  or buyer does not reject the company. Do not count unresolved rows as qualified.

## Gates, statuses, and artifacts

The account gate requires canonical `company` and `domain`, an evidenced
`account_fit`, and separate `signal_evidence`. Apply the qualification policy:
require a current observed signal only when the request requires it. Otherwise
`signal_evidence` may contain a clearly labeled inferred use case grounded in
sourced business facts, with unknown pain, intent and incumbent tools disclosed.
Preserve URL, date, date basis, text and source for each object's supporting
facts. An observation date dates the business facts, never an inferred event.
Unsupported facts, search-results pages and stale required signals cannot pass. Resolve relative dates from retrieval time
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

The email gate follows the contact gate. It requires the email, a matching
ZeroBounce status, and a source receipt linked to its
Deepline `email_validation` route. The receipt records `provider: "deepline"`,
`validator: "zerobounce"`, `operation: "execute"`, the dynamically discovered
tool, and route ID. ZeroBounce valid passes after trimming/case normalization.
Catch-all/unknown or an eligible recorded service failure may instead pass with one successful BounceBan
result deliverable receipt nested in `email_validation.fallback`. Preserve
both receipts for the same address and distinct ordered validation routes.
For service failures keep `status: null` and the matching `provider_status` on
the ZeroBounce receipt, linked to its failed paid route. Preserve both costs.
API success alone is not a deliverability verdict. Never override invalid,
do_not_mail, spamtrap or abuse; do not chain fallbacks.
A missing status or a failed, blocked, timed-out, or otherwise uncertain call
cannot pass by itself. Preserve rejected and unresolved outcomes and continue with
another discovered address or requested-role buyer within the budget.

Write `reports/<run-id>/report.md`, `reports/<run-id>/results.json`, and
`reports/<run-id>/leads.xlsx`. The report must contain the request, assumptions,
hypotheses, route and evidence receipts, pilot observations, route cost bases,
confirmed and maximum credits, Deepline dollars and cost per accepted lead,
statuses, accepted rows, rejected rows, unresolved rows, contact selection, and stop
reason. For a target shortfall it must also show the full route frontier,
continuation decisions, remaining call capacity, reviewed-company counts, and
the reason each remaining route is exhausted or blocked. Record timing and
accepted-lead provenance as specified in the output contract's
[`report.md` and final response section](output-contract.md#reportmd-and-final-response).
Maintain these as work proceeds; do not reconstruct discovery attribution from
the final evidence URL or treat a validator as the email finder. Do not store
credentials or raw secrets.

The workbook is the sales-ready primary-contact view. Its `Leads` worksheet
uses the exact fixed header in the output contract. Company and contact fields
that are not verified stay blank. Email is requested by default, so its absence
is a qualification failure unless the input explicitly opts out or requests
phone only. Keep full
evidence, backup contacts, run status, and rejection details in `results.json`
and the report.

This is a small direct-wrapper workflow. It has no `Sourcing_model` or `pp`
runtime dependency, browser harness, server, database, queue, CRM write,
outreach action, required subagent, or hidden API. Do not add one to complete a
run.
