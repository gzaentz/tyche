---
name: lead-sourcing
description: Source evidence-backed companies with current buying signals and requested-role contacts using the local Deepline and ScrapingDog wrappers; use for company-first account lists, not contact-only enrichment or outreach.
---

# TYCHE Lead Sourcing

Find companies first, then their requested buyers. Use the existing direct
Deepline and ScrapingDog wrappers; tools are optional choices, not a checklist.
Do not add a server, database, queue, CRM write, or outreach action.

## Workflow

Complete the requested research, enrichment, and email validation within scope
and budget, subject to runtime approvals, without asking for separate approval
per provider or contact, including on resume.

1. **Discover relevant tools and prices.** Normalize the ICP, target, signal
   window, roles, fields, and budgets; record start time and check credentials
   without printing secrets. Search and describe the selected Deepline tool
   before execution. Use [tools.md](references/tools.md) and its adapter.
2. **Run a small, budgeted search.** Use available no-cost company sources first.
   Initialize the [paid-call ledger](references/adapter-io.md#paid-call-budget)
   before spending; protect the email-verification allowance. Start each
   company-discovery route with one paid call and at most ten rows. Retrieve
   only 1-3 relevant contacts for each company missing a buyer. Every paid call
   needs a conservative bound and unique route ID. Never bypass the guard with
   raw CLI/HTTP. Use adapters' `--output-file` for redacted responses; never
   automatically retry an uncertain paid call.
3. **Verify company fit and intent from evidence.** Resolve and deduplicate
   canonical domains. Require separate company-fit and current-signal evidence
   from actual sources; snippets, keywords, ingestion dates, and empty responses
   do not prove qualification. Keep rejected, unresolved, and provider failures
   separate. Use the output contract's taxonomy and writing rules.
4. **Find the requested buyer and validate their email.** Only after the account
   passes, verify the person's current role/company before lookup. Respect role
   groups. Email is required unless overridden; validate every stored address
   with a freshly discovered Deepline ZeroBounce tool. Accept `valid`; never
   fall back from hard rejections (`invalid`, `do_not_mail`, `spamtrap`, or
   `abuse`). For any other ZeroBounce issue or non-valid status, discover and
   describe BounceBan through Deepline, then check the same address once within
   budget. Accept only a successful `deliverable` verdict, preserving both
   receipts. Otherwise retain the candidate as unresolved and continue with
   another address or buyer.
5. **Save results; continue until the target or an honest stopping condition.**
   Persist evidence, attempts, costs, decisions, and routes. Change route when
   a candidate fails and preserve links. Require a concrete action or blocker
   for every unresolved company. Before a shortfall, search the live catalog for
   evidence gaps and discovery routes, not just the initial provider list.
   Write `report.md`, `results.json`, and `leads.xlsx`; validate the full
   output contract and run
   `scripts/validate_run.py` with `--show-progress` before delivery. A successful
   `--check-stop` is not full validation. Use the full validator's
   account/contact groups in the report; keep route failures separate.
   Before sending the final chat response, check all four items in the
   [final-response checklist](references/output-contract.md#final-response-checklist).

## Full cost

Capture run-scoped harness usage, including models, retries, and verification.
Price input, cached-input, cache-write, and output tokens at applicable rates;
avoid double counting. Report provider spend, LLM cost, combined total, and
cost per accepted lead. Label estimates; incomplete usage means full cost is
unknown. Keep provider caps separate.

## References

Read [workflow-rules.md](references/workflow-rules.md) for evidence, roles,
budget, and shortfall rules.

Load remaining references by phase; reuse loaded rules.

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
