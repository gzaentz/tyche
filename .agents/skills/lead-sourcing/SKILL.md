---
name: lead-sourcing
description: Source evidence-backed companies with buying signals and requested-role contacts using local Deepline/ScrapingDog wrappers; for company-first lists, not contact-only enrichment or outreach.
---

# TYCHE Lead Sourcing

LLM: research and qualification. Tools: bookkeeping and validation.
No CRM writes or outreach.

## Setup

Read [workflow rules](references/workflow-rules.md), the [input contract](references/output-contract.md#input-contract)
and [lifecycle invariants](references/output-contract.md#lifecycle-invariants).
Interpret once: separate must-haves/preferences and buyer roles/hiring signals.
Preserve offering and seller/target perspective in `request.product_service`.
Mark signals `required`/`preferred`; put non-signal must-haves in `icp.required_attributes`.
Compare criteria with launcher-saved `original_text` before paid research;
never strengthen, weaken or add requirements.
Call `tyche_start` with that `request` and any authorized `max_usd`.
Code supplies time, ledger and verification reserve. Catalog prices override
[stored planning rates](references/provider-pricing.md); receipts supply charges.
Defaults: one contact/company; USD 0.50/requested lead.
Only the user changes criteria. Never import another run or guess prices.
The launcher supplies credentials and runtime paths.

Use [native tools](references/adapter-io.md#native-tools); no shell bookkeeping
or implementation-code reads. Resume saved work with `tyche_inspect()`.

## Research loop

1. **Choose ready work.** Prefer affordable, unblocked `completion_candidates` before discovery.
   Read [tools.md](references/tools.md#choose-by-evidence-gap) once before tool selection;
   match sources to requested evidence.
   Find capabilities with `tyche_inspect(query=...)`; learn each selected tool once
   with `tool=...`. Its compact description includes inputs, constraints and pricing;
   use `field` for omitted detail only when needed. Code checks the full saved
   contract and price before dispatch. Reuse descriptions; refresh only after
   evidence of schema/price/access changes. Pilot unproven operations/filters before
   batching; preserve native limits.
2. **Check up to three companies concurrently.** Send `tyche_lookup` 1–3 independent
   `checks` across phases: target, phase, purpose, tool and native inputs.
   Do not wait to fill batches.
   Review requested fit criteria and dated signals before buyers;
   distinguish announced, conditional, planned and completed activity. Resolve
   company LinkedIn URLs from sources before enrichment; never invent slugs.
   Apply the [qualification policy](references/workflow-rules.md#qualification-policy).
   Required unknowns stay unresolved; evidenced mismatches reject; preferred signals
   only rank. Review each preferred signal once; retain unverified gaps as unknown.
   Select returned `attribute:N` or `signal:N` as `requirement_ref` in checks;
   code supplies labels/importance and checks dates and coverage.
   Reuse facts for `Intent Details`.
   Follow [client writing/classification](references/output-contract.md#client-writing-and-taxonomy-version-12).
   [HarvestAPI LinkedIn fields](references/output-contract.md#linkedin-location-and-company-size):
   accepted contacts require country; companies require published employee range/source.
3. **Save decisions as made.** Call `tyche_review` with changed facts, checks,
   decisions and selected evidence `ref` values. Reviewed single-result company,
   profile and email-verdict lookups close automatically. Review other used sources
   in `sources`, with their reason and continuation/exhaustion decision. For built-in
   web tools, include the actual observed response in `web` in the same call.
   `review_due` lists reviews due.
   After two comparable attempts per company/phase without verified progress,
   revisit the matching guide row/live catalog and change strategy before
   repeating searches or claiming shortfall. Choose useful alternatives.
   Reopen evidence for gaps/contradictions;
   independent profile/email checks remain eligible.

Use `tyche_inspect(ref=..., field=...)` for detail, `target=...` for company state,
or `recover=...` to record a saved normalized receipt without redispatch.
Missing responses require reconciliation; never repeat an uncertain paid call.
Never read the worker's live launcher log.

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

Use a verified `contact_ref` for email inputs. Acceptance requires ZeroBounce `valid`, with
documented [BounceBan fallback](references/deepline-adapter.md#bounceban-fallback)
only for eligible failures or catch-all/unknown. Never override a hard negative.

## Delivery

Call `tyche_finish()` for gaps or final review. Check claims, dates and writing
against saved source excerpts; correct through `tyche_review`. Use
`inspect(target=..., field="evidence_review")` during research for the same view.
Return the current `review_ref` with commentary to validate/export.
Never force completion.
Inspect the preview. Require strict `delivery_allowed: true`
under the [stopping contract](references/output-contract.md#stopping-check).
Report shortfalls; exhausted searches do not prove an empty market.

## Full cost

Report launcher-refreshed provider, model, combined and per-lead costs after
worker exit; label estimates/unknowns.

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
