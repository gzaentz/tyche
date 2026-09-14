---
name: lead-sourcing
description: Source evidence-backed companies with buying signals and requested-role contacts using local Deepline/ScrapingDog wrappers; for company-first lists, not contact-only enrichment or outreach.
---

# TYCHE Lead Sourcing

One LLM chooses candidates, sources, queries, tools, follow-ups and qualification.
Native tools handle persistence, IDs, receipts, accounting and validation.
No CRM writes or outreach.

## Setup

Read [workflow rules](references/workflow-rules.md), the [input contract](references/output-contract.md#input-contract)
and [lifecycle invariants](references/output-contract.md#lifecycle-invariants).
Interpret the request once, separating buyer roles from hiring signals.
Call `tyche_start` with that `request` and any authorized `max_usd`.
Code initializes/resumes the bound run, clock, ledger and priced verification
reserve. Defaults: one contact per company; USD 0.50 per requested lead.
Only the user can change criteria. Never import another run or guess prices.
Credentials and runtime paths come from the launcher.

Use [native examples](references/adapter-io.md#native-tools) for inputs and evidence
references. Ordinary research needs no shell bookkeeping or implementation-code
reads. CLI instructions are diagnostics or compatibility for runtimes without
native tools. Resume with `tyche_inspect()` for the saved request and pending work.

## Research loop

1. **Discover.** Find fresh companies through likely signal sources; follow
   productive sources. Select tools through [tools.md](references/tools.md).
   Use `tyche_inspect(query=...)` for capabilities or `tool=...` for native schemas.
   Lookup caches descriptions; refresh only after a schema, price or access change.
   Pilot unproven operations/filter shapes before batching. Preserve native limits.
2. **Check up to three companies concurrently.** Send `tyche_lookup` one to three
   `checks`: target, phase, purpose, tool and native inputs. Batch ready independent
   checks across phases without waiting to fill batches.
   Review required fit, current stage, acquisition history and dated signals before buyer lookup;
   distinguish announced, conditional, planned and completed activity. Resolve
   company LinkedIn URLs from sources before enrichment; never invent slugs.
   Apply the [qualification policy](references/workflow-rules.md#qualification-policy).
   Required unknowns stay unresolved; evidenced mismatches reject; preferred signals
   only rank. Review each preferred signal once; retain unverified gaps as unknown.
   Save reviewed facts/dates once for `Signals` and `Intent Details` before contacts.
   Follow [client writing/classification](references/output-contract.md#client-writing-and-taxonomy-version-12).
   Use [HarvestAPI LinkedIn fields](references/output-contract.md#linkedin-location-and-company-size):
   accepted contacts need country; companies need published employee range and source.
3. **Save decisions as made.** Call `tyche_review` with changed facts, checks,
   decisions and selected evidence `ref` values. Explicitly review each used source
   in `sources`, with its reason and continuation/exhaustion decision. For built-in
   web tools, include the actual observed response in `web` in the same call.
   This records an observation; it cannot execute or independently capture the browser.
   Save gaps/rejections immediately and qualified leads promptly. `review_due`
   identifies outstanding reviews. After two comparable attempts within one company
   and phase without verified progress, change strategy. Reopen evidence for a
   specific gap or contradiction; independent profile/email checks remain eligible.

Use `tyche_inspect(ref=..., field=...)` for detail, `target=...` for company state,
or `recover=...` to record a saved normalized receipt without redispatch.
Missing responses require reconciliation; never repeat an uncertain paid call.
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

Call `tyche_finish(commentary=...)` with reviewed assumptions, caveats and route-choice
reasoning. Code validates, exports, checks and delivers `report.md`, `results.json`
and `leads.xlsx`, plus a preview and validation receipt. Review sources and recover
pending verification; unused research may remain at target. Never force completion.
Inspect the preview. Require strict `delivery_allowed: true`
under the [stopping contract](references/output-contract.md#stopping-check).
Report shortfalls honestly; exhausted searches do not prove an empty market.

## Full cost

The launcher refreshes run-only costs when worker usage closes. Report provider,
model, combined and per-lead costs; label estimates and unknowns.

## References

Load details only when their fields are needed:

- Evidence: [semantics](references/output-contract.md#semantic-checks),
  [source attribution](references/output-contract.md#accepted-lead-sources),
  [schema](references/output-contract.md#resultsjson-schema) and
  [client writing](references/output-contract.md#client-writing-and-taxonomy-version-12).
- Delivery: [workbook](references/output-contract.md#leadsxlsx-contract),
  [report](references/output-contract.md#reportmd-minimum-contents),
  [timing](references/output-contract.md#timing) and
  [final checklist](references/output-contract.md#final-response-checklist).
  Use bundled workbook dependencies; do not add an npm dependency.
