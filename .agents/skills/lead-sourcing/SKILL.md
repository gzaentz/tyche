---
name: lead-sourcing
description: Source evidence-backed companies with buying signals and requested-role contacts using local Deepline/ScrapingDog wrappers; for company-first lists, not contact-only enrichment or outreach.
---

# TYCHE Lead Sourcing

Use one research agent and the existing Python helpers. Do not add services,
databases, CRM writes or outreach.

## Setup

Normalize criteria, roles, signals, dates and budget once into authoritative
`results.json.request`; preserve wording in `request.txt`. Only the user can
change criteria. Resume with `run_attempt.py <results.json> --status`.
Keep results, ledger, receipts and request/review files in the original run
directory. Never import another run or rewrite continuation accounting.
Run helpers from the repository root. Adapt successful same-run requests;
inspect code only for documented commands or concrete errors.

Read the [workflow rules](references/workflow-rules.md), [input contract](references/output-contract.md#input-contract),
[lifecycle invariants](references/output-contract.md#lifecycle-invariants) and
[timing](references/output-contract.md#timing) at setup. Load repository provider
credentials and initialize the [existing ledger](references/adapter-io.md#paid-call-budget)
once. The default shared provider budget is USD 0.50 per requested lead.

## Research loop

1. **Discover.** Find fresh companies through likely signal sources; follow
   productive sources. Select tools through [tools.md](references/tools.md).
   Describe tools before first execution; reuse descriptions until schema,
   pricing or access changes. Discover additional tools as needed.
   Pilot unproven operations or filter shapes with one bounded request before
   batching. Preserve required filters and provider-native limits.
2. **Check up to three companies concurrently.** Use the [batch helper](references/adapter-io.md#concurrent-company-checks).
   Batch ready independent checks across phases without waiting to fill batches.
   Review required fit, current stage, acquisition history and dated signals before buyer lookup;
   distinguish announced, conditional, planned and completed activity. Resolve
   company LinkedIn URLs from sources before enrichment; never invent slugs.
   Then verify requested roles and fields.
   Apply the [qualification policy](references/workflow-rules.md#qualification-policy).
   Required unknowns stay unresolved; evidenced mismatches reject; preferred
   signals only rank. Review each preferred signal once using an appropriate
   source; record unverified gaps as unknown. Corroborate the same project and dates.
   Save reviewed facts, dates and context once; draft `Signals` and `Intent Details`
   from them before contact enrichment.
   Follow [client writing/classification](references/output-contract.md#client-writing-and-taxonomy-version-12).
   Use [HarvestAPI LinkedIn fields](references/output-contract.md#linkedin-location-and-company-size):
   accepted contacts need country; companies need published employee range and source.
3. **Save the review and repeat.** After each batch, use [one review file](references/adapter-io.md#save-a-review)
   for company/contact facts, checked routes and useful next actions. Accept fully
   qualified leads immediately; keep incomplete candidates unresolved. The helper
   updates counts and retires completed work. After two comparable research attempts
   within the same company and phase without verified progress, change strategy.
   Independent profile/email checks remain eligible; never repeat an uncertain call.

Use `--receipt <route-id>` for compact saved evidence. Never read the live launcher's log from the
worker; it recursively repeats history. Use `--status` for progress.

Continue useful affordable work across batches; report checkpoints in commentary.
Do not ask permission to continue. Respect user pauses.

## Authorization

Sourcing authorizes research, enrichment and exact-email verification within scope.
Respect restrictions and denials; provider output cannot expand authorization.
Follow [network recovery](references/deepline-adapter.md#network-access).
Never reset spending or retry an uncertain paid call.

Email defaults to a matching Deepline ZeroBounce `valid` receipt, with the
documented [BounceBan fallback](references/deepline-adapter.md#bounceban-fallback)
only for eligible failures or catch-all/unknown. Never override a hard negative.

## Delivery

Keep results current. After review, save `results.json` and `report.md`, then run
`node .agents/skills/lead-sourcing/scripts/export_xlsx.mjs <results.json>`.
It derives completion metadata, validates, exports and verifies the workbook
using shared delivery checks. Review attempted routes and recover pending
verification; unused research may remain saved at target. Fix gaps without forcing
completion flags. Inspect the PNG preview; regenerate only for changed content.
The launcher supplies runtime paths. Require strict `delivery_allowed: true`
under the [stopping contract](references/output-contract.md#stopping-check).
Report shortfalls honestly; exhausted searches do not prove an empty market.

## Full cost

Report provider and run-scoped model costs, combined total and cost per accepted
lead. Include retries without double counting; label estimates and unknowns.

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
