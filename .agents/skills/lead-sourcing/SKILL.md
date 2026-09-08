---
name: lead-sourcing
description: Source evidence-backed companies with current buying signals and requested-role contacts using the local Deepline and ScrapingDog wrappers; use for company-first account lists, not contact-only enrichment or outreach.
---

# TYCHE Lead Sourcing

Find companies first, then their requested buyers. Use the existing direct
Deepline and ScrapingDog wrappers; tools are optional choices, not a checklist.
Do not add a server, database, queue, CRM write, or outreach action.

## Continue or stop

Continue while qualified leads are below target, useful work is affordable, and
the user's time limit (if any) has not expired. A failed call, rejected lead,
finished batch, or closed route list is not a run-level stopping condition.
Try a different useful approach; do not repeat uncertain calls or spend just
to empty the budget.

Before each next action, update `stop_check` and run
`python3 scripts/validate_run.py <results.json> --check-stop`.
Choose only an `eligible_actions` entry. Missing discovery/recovery actions or
unknown prices require more planning, not a shortfall stop. Stop at the target,
a verified time/budget limit, or concrete blockers covering every remaining
action. Never exceed a cap first or invent a time limit after starting.
Read the [stopping contract](references/output-contract.md#stopping-check) at setup.

## Workflow

1. **Normalize and discover.** Record the ICP, target, signal window, roles,
   contact fields, start time and explicit limits. Default paid-provider budget:
   USD 0.50 per requested lead, shared across providers, unless overridden.
   Apply [budget normalization](references/workflow-rules.md#default-run-budget).
   Load credentials using repository setup; an unloaded `.env` is not a missing
   key. Use [tools.md](references/tools.md), then search and describe the live
   Deepline tool before every execution. Load only the selected adapter sections.
2. **Pilot within budget.** Use available no-cost company sources first.
   Initialize the [paid-call ledger](references/adapter-io.md#paid-call-budget)
   before spending; protect the email-verification allowance. Start each
   company-discovery route with one paid call and at most ten rows, using
   provider-native limits. Retrieve only 1-3 relevant contacts for each company
   still missing a buyer. Every paid adapter call needs a conservative maximum
   cost and a unique route ID. Never bypass the guard with the raw CLI or HTTP.
   Save full redacted responses with `--output-file`. Never automatically retry
   an uncertain paid call.
3. **Verify the company.** Deduplicate domains and owner groups. Read actual
   sources for separate company-fit and current-signal evidence. Snippets,
   keywords and missing results are not qualification or rejection proof.
   Keep rejected companies, missing evidence and provider failures separate.
4. **Verify the buyer, then their email.** Account fit and current role must
   pass before contact lookup. Respect requested role groups and contact fields.
   Email defaults to required: Deepline ZeroBounce `valid`, or only for
   catch-all/unknown one BounceBan `deliverable` fallback. Preserve both receipts;
   never override invalid/risky outcomes. Otherwise try another address or buyer.
5. **Persist, reassess and deliver.** Save evidence, receipts, costs and next actions as
   work proceeds. Keep a new-company discovery action and a recovery action for
   each unresolved company. Before a shortfall, search the live catalog for
   different discovery and gap-specific tools, and test useful affordable options.
   Only after the stop check permits delivery, write `report.md`, `results.json`
   and `leads.xlsx`. Validate the full output contract and run
   `python3 scripts/validate_run.py <results.json> --show-progress` (strict by
   default). A successful `--check-stop` is not full validation. Never use
   legacy validation for a current run. Complete the
   [final-response checklist](references/output-contract.md#final-response-checklist).

## Full cost

Report provider spend, run-scoped primary/delegated model cost, combined cost
and cost per accepted lead. Include retries and verification; use applicable
dated rates without double counting. Label API-equivalent pricing as estimated.
Incomplete model usage means full cost is unknown, never zero.

## References

Read [workflow-rules.md](references/workflow-rules.md) at setup for evidence,
role, budget and persistence rules. Load other references by phase and linked
section only; reuse already loaded rules.

| Phase | Required reading |
|---|---|
| Normalize the request, before discovery | [Lifecycle invariants](references/output-contract.md#lifecycle-invariants), [input contract](references/output-contract.md#input-contract), and [timing](references/output-contract.md#timing). |
| Choose or change a route | [Tool index](references/tools.md); only the relevant capability section and selected adapter contract it links. |
| Before recording the first route; when adding record fields | [Result semantics](references/output-contract.md#semantic-checks), [source attribution](references/output-contract.md#accepted-lead-sources), and the relevant [schema definitions](references/output-contract.md#resultsjson-schema). |
| Qualify companies and write accepted rows | [Client writing and taxonomy](references/output-contract.md#client-writing-and-taxonomy-version-12). |
| Export, validate, and deliver | [Workbook contract](references/output-contract.md#leadsxlsx-contract), [report requirements](references/output-contract.md#reportmd-minimum-contents), and [final-response checklist](references/output-contract.md#final-response-checklist). |

Phase loading never skips full artifact validation or an applicable safety
check. Use the bundled workspace dependencies for workbook generation;
do not add a project dependency.
