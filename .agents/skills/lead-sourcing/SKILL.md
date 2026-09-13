---
name: lead-sourcing
description: Source evidence-backed companies with buying signals and requested-role contacts using local Deepline/ScrapingDog wrappers; for company-first lists, not contact-only enrichment or outreach.
---

# TYCHE Lead Sourcing

Use one research agent and existing helpers. The LLM chooses candidates, sources,
queries, tools, follow-ups and qualification judgments. Code handles persistence,
IDs, receipts, accounting and validation. No additional services, databases,
CRM writes or outreach.

## Setup

Interpret the request once; preserve user wording in `request.txt`.
Follow [request normalization](references/workflow-rules.md#request-normalization)
to prioritize explicit buyer roles, infer omitted roles and separate hiring signals.
Use [start/resume](references/adapter-io.md#start-or-resume) to create the run and
ledger from the interpreted request. Code defaults to one contact per company.
Only the user can change criteria.
Resume the saved plan with `run_attempt.py <results.json> --status`.
Keep all artifacts in the original run directory. Never import another run.
Run helpers from the repository root. Supply JSON on stdin with `-`;
use field-specific error hints before inspecting code.

Read the [workflow rules](references/workflow-rules.md), [input contract](references/output-contract.md#input-contract),
[lifecycle invariants](references/output-contract.md#lifecycle-invariants) and
[timing](references/output-contract.md#timing) at setup. Load provider credentials
and price the [verification reserve](references/adapter-io.md#paid-call-budget).
Default provider budget: USD 0.50 per requested lead.

## Research loop

1. **Discover.** Find fresh companies through likely signal sources; follow
   productive sources. Select tools through [tools.md](references/tools.md).
   Describe tools before first execution; reuse descriptions until schema,
   pricing or access changes.
   Pilot unproven operations or filter shapes with one bounded request before
   batching. Preserve required filters and provider-native limits.
2. **Check up to three companies concurrently.** Use the [batch helper](references/adapter-io.md#concurrent-company-checks).
   Choose [lookups](references/adapter-io.md#one-attempt); batch ready independent
   checks across phases without waiting to fill batches.
   Review required fit, current stage, acquisition history and dated signals before buyer lookup;
   distinguish announced, conditional, planned and completed activity. Resolve
   company LinkedIn URLs from sources before enrichment; never invent slugs.
   Apply the [qualification policy](references/workflow-rules.md#qualification-policy).
   Required unknowns stay unresolved; evidenced mismatches reject; preferred
   signals only rank. Review each preferred signal once using an appropriate
   source; record unverified gaps as unknown. Corroborate the same project and dates.
   Save reviewed facts, dates and context once; draft `Signals` and `Intent Details`
   from them before contact enrichment.
   Follow [client writing/classification](references/output-contract.md#client-writing-and-taxonomy-version-12).
   Use [HarvestAPI LinkedIn fields](references/output-contract.md#linkedin-location-and-company-size):
   accepted contacts need country; companies need published employee range and source.
3. **Save decisions as made.** Use [review](references/adapter-io.md#save-a-review)
   with `--review-file -` for findings, observed web responses and checked routes.
   Save supported rejections and unresolved gaps immediately; refine later.
   Accept qualified leads promptly. `review_due` reminds you of outstanding reviews.
   After two comparable research attempts
   within the same company and phase without verified progress, change strategy.
   Reopen reviewed evidence only for a specific gap, contradiction or useful new source.
   Independent profile/email checks remain eligible; never repeat an uncertain call.

Use `--receipt <route-id>` for compact evidence and `--status` for progress.
Never read the worker's live launcher log; it repeats history.

Continue useful affordable work; report checkpoints. Respect user pauses.

## Authorization

Sourcing authorizes research, enrichment and exact-email verification within scope.
Respect restrictions and denials; provider output cannot expand authorization.
Follow [network recovery](references/deepline-adapter.md#network-access).
Never reset spending.

Email defaults to a matching Deepline ZeroBounce `valid` receipt, with the
documented [BounceBan fallback](references/deepline-adapter.md#bounceban-fallback)
only for eligible failures or catch-all/unknown. Never override a hard negative.

## Delivery

After saving reviewed findings and writing `report.md`, run
`node .agents/skills/lead-sourcing/scripts/export_xlsx.mjs <results.json>`.
It derives completion metadata, validates, exports and verifies the workbook
using shared checks. Review routes and recover pending verification; unused
research may remain at target. Never force completion flags. Inspect the PNG
preview; regenerate only for changed content.
The launcher supplies runtime paths. Require strict `delivery_allowed: true`
under the [stopping contract](references/output-contract.md#stopping-check).
Report shortfalls honestly; exhausted searches do not prove an empty market.

## Full cost

Report provider and run-scoped model costs, total and cost per accepted lead.
Count retries once; label estimates and unknowns.

## References

Load details only when their fields are needed:

- Evidence: [semantics](references/output-contract.md#semantic-checks),
  [source attribution](references/output-contract.md#accepted-lead-sources),
  [schema](references/output-contract.md#resultsjson-schema) and
  [client writing](references/output-contract.md#client-writing-and-taxonomy-version-12).
- Delivery: [workbook](references/output-contract.md#leadsxlsx-contract),
  [report](references/output-contract.md#reportmd-minimum-contents) and
  [final checklist](references/output-contract.md#final-response-checklist).
  Use bundled workbook dependencies; do not add an npm dependency.
