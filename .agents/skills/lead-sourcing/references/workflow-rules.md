# Detailed Workflow Rules

Use this skill for a company-first request: a target count, ICP, current buying
signal, and one or more requested contact roles. The run produces unique,
evidence-backed companies and, for each accepted company, one primary contact
plus zero to two backups. Use [tools.md](tools.md) to select a route and load
only that adapter's required sections. Read the exact input, output, and Excel
contracts by the phases in [output-contract.md](output-contract.md#read-by-phase),
not as an upfront bundle.

The main workflow is the research loop in [SKILL.md](../SKILL.md). This reference
preserves the detailed qualification, spending, receipt, and completion rules.
Command paths below are relative to the skill directory, not this reference.

## Operating rules

- Keep the strategy open and the evidence gate closed. Form several materially
  different hypotheses (for example hiring, funding/news, paid activity,
  patents, facilities, official video, or firmographic fit) and change route
  when rows repeat or lack evidence.
- Use one agent to check up to three independent companies concurrently via
  [bounded batches](adapter-io.md#concurrent-company-checks). Resolve and
  deduplicate canonical domains, aliases and owner groups before batching.
  Keep each company's account, buyer and email steps in order; contact lookup
  requires passing account evidence for that company.
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
  `qualification_checks` fields. Every unresolved company must state its
  missing evidence in `reason_text`; park reviewed gaps without inventing another
  action. A contact-stage unresolved
  result keeps the account evidence that already passed. Provider failures are not
  companies and must not be counted in reviewed or accepted company totals.
  Keep buyer-role gaps in contact evidence and `reason_text`; do not add them
  as failed company checks that prevent the lookup needed to resolve that buyer.
  `no_results` is valid only when the provider actually returned no results;
  an input error, response error, timeout, or uncertain response is not
  `no_results` and remains unresolved or blocked as appropriate.
- Treat `target_count` as the completion condition. While accepted companies
  remain below it, refill from a changed route, query, page, tool, or provider.
  Check at most three companies at once, reducing the batch to the remaining
  lead shortfall. Do not use a fixed 5x multiplier or any other fixed over-fetch.
- When useful remaining approaches are exhausted, apply the evidenced
  `no_productive_route` review in the [stopping contract](output-contract.md#stopping-check).
  A shortfall can be an honest final result without spending the whole budget.
  Keep its candidates unresolved, preserve every receipt and report missing
  evidence; do not turn missing evidence into acceptance or invent a provider
  outage to finish. Do not repeat full-state validation between ordinary reads
  or after an attempt helper has already returned the current stop decision.
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
  that accepted-lead count. When review removes accepted leads, preserve their
  historical receipts and counts. Include spend at the current count or higher
  in the next-lead allowance; demotion never resets spend. Route
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

### Review and delivery details

Use the [research loop](../SKILL.md#research-loop) and
[review helper](adapter-io.md#save-a-review). The helper records company decisions,
closes reviewed sources and refreshes totals; do not maintain a second plan or
copy calculated summaries by hand. Preserve historical receipts. With existing
continuation links, close children before parents; reopen the parent before a
child. An unrecoverable receipt stays blocked with the audit gap stated.

For role groups, search and rank the primary group first, then valid secondary
fallbacks when no primary-role contact passes. Select one output primary and
up to two backups. A valid secondary contact can be the output primary; record
`role_group: "secondary"` when known. One qualified contact is sufficient unless
the user explicitly requires more. Record any backup shortfall. Every stored
backup email must pass the same validation gate as the primary email.

Use the [stopping contract](output-contract.md#stopping-check) for final delivery.
Keep unfinished routes open when an actual budget/time limit ends work. A
reviewed company can remain parked while fresh discovery continues; do not
invent another action to satisfy a checklist. Missing evidence never proves
failed fit. No implicit timeout or minimum-spend target applies.

Write version `1.2` results and use the helper's calculated costs in the report.
Generate the workbook with the [documented exporter](output-contract.md#leadsxlsx-contract)
and bundled dependencies. Run full strict validation on the finished artifacts;
a successful helper call alone is not permission to deliver them.

## Qualification policy

Keep one qualification policy and the existing outcomes; research models supply
evidence, not a separate acceptance standard. Do not add lead scores or gates.
Work small batches through company, buyer and contact checks before broadening
discovery. After a blocked or unproductive stage, record its recovery action and
change source for that gap; do not leave promising qualified accounts untouched
while repeatedly starting new country searches.

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
- Corroborate facts across credible sources for the same identified project.
  A recent announcement or substantive progress update can establish activity
  within the requested window; drawings, specifications or another project
  report can establish its technical requirement. Technical evidence may be
  older when it still applies to that project. Record the project linkage,
  each source's actual date and the fact it supports in existing evidence
  arrays and prose. Never date old technical work as new, combine unrelated
  projects, or substitute general service capability for required recent intent.
  Installed work shows project activity, not an outstanding purchase; preserve
  any explicit request for future demand or a new award.
- Accept supported must-haves, even without optional intent. Keep missing
  must-haves unresolved; reject evidenced mismatches. Recover the specific gap
  through another source, signal or buyer within existing limits. A bad signal
  or buyer does not reject the company. Do not count unresolved rows as qualified.

## Gates, statuses, and artifacts

`results.json.request` is the authoritative normalized ICP. Check decisions
against it, not an earlier candidate's band or a rewritten interpretation.
Do not turn a service area into an office requirement or a preferred signal
into a requirement. Save an exact sourced employee count in
`company.employee_count` (accepted) or `candidate.employee_count` (unresolved or
rejected). The validator compares it to `request.icp.company_size`; an in-range
count cannot fail that gate. Retain evidence for the count. When only a range
or an uncertain estimate is known, preserve that uncertainty in the check;
do not invent an exact count. Missing intent remains unknown even when size passes.

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
Save resolved criteria as evidence arrives; do not collapse several known facts
and one missing field into a single unknown `complete_account_fit` check.

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
