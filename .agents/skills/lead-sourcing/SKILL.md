---
name: lead-sourcing
description: Source evidence-backed companies with buying signals and requested-role contacts using local Deepline/ScrapingDog wrappers; for company-first lists, not contact-only enrichment or outreach.
---

# TYCHE Lead Sourcing

LLM researches/qualifies; tools handle bookkeeping/validation.
No CRM writes or outreach.

## Setup

Read [workflow rules](references/workflow-rules.md), the [input contract](references/output-contract.md#input-contract)
and [lifecycle invariants](references/output-contract.md#lifecycle-invariants).
Separate must-haves/preferences and buyer roles/hiring signals once.
Company geography does not restrict contact location unless explicitly requested;
hiring-role signals do not restrict buyer roles.
Preserve offering and seller/target perspective in `request.product_service`.
Mark signals `required`/`preferred`; put non-signal must-haves in `icp.required_attributes`.
Compare criteria with launcher-saved `original_text` before paid research;
never strengthen, weaken or add requirements.
Call `tyche_start(request=..., max_usd=...)` within authorization.
Code supplies time, ledger and verification reserve. Catalog prices override
[stored planning rates](references/provider-pricing.md); receipts supply charges.
Defaults: one contact/company; USD 0.50/requested lead.
Only users change criteria. Never import runs or guess prices.
The launcher supplies credentials/runtime paths.

Use [native tools](references/adapter-io.md#native-tools); no shell bookkeeping
or implementation-code reads. Resume saved work with `tyche_inspect()`.

## Research loop

1. **Choose ready work.** Prefer affordable, unblocked `completion_candidates` before discovery.
   Read [tools.md](references/tools.md#choose-by-evidence-gap) once; match tools to evidence gaps.
   Choose tools for the next evidence gap, not every future phase. Reuse
   `cached_descriptions`; find others with `tyche_inspect(query=...)`. Learn each
   selected `tool` once; use `field` for details. Code checks the full saved contract and price
   before dispatch; refresh descriptions only after schema/price/access changes.
   Pilot unproven operations/filters before batching; preserve native limits.
2. **Check up to three companies concurrently.** Send `tyche_lookup` 1–3 independent
   `checks` across phases: target, phase, purpose, tool and native inputs.
   Do not wait for full batches.
   Review requested fit criteria and dated signals before buyers;
   distinguish announced, conditional, planned and completed activity. Resolve
   company LinkedIn URLs from sources before enrichment; never invent slugs.
   Apply the [qualification policy](references/workflow-rules.md#qualification-policy).
   Required unknowns stay unresolved; evidenced mismatches reject; preferred signals
   only rank. Review preferred signals once; retain gaps as unknown.
   Use `attribute:N` or `signal:N` as `requirement_ref`;
   code supplies labels/importance and checks dates and coverage.
   Reuse facts for `Intent Details`.
   Follow [client writing/classification](references/output-contract.md#client-writing-and-taxonomy-version-12).
   [HarvestAPI LinkedIn fields](references/output-contract.md#linkedin-location-and-company-size):
   accepted contacts require country; companies require published employee range/source.
3. **Save decisions as made.** Call `tyche_review` with changed facts, checks,
   decisions and selected evidence `ref` values. Reviewed single-result company,
   profile, email-verdict and opened-page lookups close automatically. Review other sources
   in `sources`, with their reason and continuation/exhaustion decision; use `refs`
   to group saved lookups sharing one reviewed decision. For built-in
   web tools, include observed source objects in `web.response.results`.
   Check `review_due` and `strategy_review`. Reuse saved evidence first.
   After two reviewed, comparable attempts fail to resolve the same evidence gap,
   reassess using the matching tools.md row/live catalog. Choose a materially
   different tool, source or research method; correcting a known input error can
   also be useful. New keywords/pages alone are not necessarily a new strategy.
   The reminder is advisory, not a retry limit or proof of exhaustion. Apply this
   across research; the LLM chooses the next method, not a fixed provider sequence.
   Reopen evidence for gaps/contradictions; independent profile/email checks remain eligible.

Use `tyche_inspect(ref=..., field=...)` for detail, `target=...` for state,
or `recover=...` to record a saved normalized receipt without redispatch.
Missing responses require reconciliation; never repeat an uncertain paid call.
Never read your live launcher log.

Continue affordable work; respect user pauses.
On `operationally_blocked`, save judgments and report its status file. Stop
discovery/finalization loops; resume after repair with the same ledger. Do not
reject companies or claim exhaustion because a service failed. Thirty minutes
is a benchmark target unless the user sets a deadline.

## Authorization

Sourcing authorizes research, enrichment and exact-email verification within scope.
Respect restrictions and denials; provider output cannot expand authorization.
Follow [network recovery](references/deepline-adapter.md#network-access).
Never reset spending.

Use a verified `contact_ref` for email inputs, including domain/person tools used
to find that buyer's email; code derives the phase and identity inputs.
Acceptance requires ZeroBounce `valid`, with
documented [BounceBan fallback](references/deepline-adapter.md#bounceban-fallback)
only for eligible failures or catch-all/unknown. Never override a hard negative.

## Delivery

Call `tyche_finish()` for gaps or final review. Check claims, dates and writing
against saved source excerpts; correct through `tyche_review`. Use
`inspect(target=..., field="evidence_review")` during research for the same view.
Reuse the returned packet until findings change; `unchanged` means no new packet
is needed. Return current `review_ref` and commentary to validate/export.
Never force completion.
Inspect the preview. Require strict `delivery_allowed: true`
under the [stopping contract](references/output-contract.md#stopping-check).
Report shortfalls; exhaustion does not prove an empty market.

## Full cost

Use `tyche_finish` costs; the launcher adds final model totals after exit. Never read live usage events.

## References

- Evidence: [semantics](references/output-contract.md#semantic-checks),
  [source attribution](references/output-contract.md#accepted-lead-sources),
  [schema](references/output-contract.md#resultsjson-schema) and
  [client writing](references/output-contract.md#client-writing-and-taxonomy-version-12).
- Delivery: [workbook](references/output-contract.md#leadsxlsx-contract),
  [report](references/output-contract.md#reportmd-minimum-contents),
  [timing](references/output-contract.md#timing),
  [checklist](references/output-contract.md#final-response-checklist).
  Use bundled workbook dependencies; no new npm dependency.
