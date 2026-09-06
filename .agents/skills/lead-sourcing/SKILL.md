---
name: lead-sourcing
description: Source evidence-backed companies with current buying signals and requested-role contacts using the local Deepline and ScrapingDog wrappers; use for company-first account lists, not contact-only enrichment or outreach.
---

# TYCHE Lead Sourcing

Find companies first, then their requested buyers. Use the existing direct
Deepline and ScrapingDog wrappers; tools are optional choices, not a checklist.
Do not add a server, database, queue, CRM write, or outreach action.

## Workflow

1. **Discover relevant tools and check their inputs and prices.** Normalize the
   ICP, target, signal window, roles, contact fields, and hard budgets. Check
   credentials in the process; an unloaded `.env` is not a missing key. Follow
   the repository's setup instructions without printing secrets. Search the
   live Deepline catalog and describe the selected tool before each execution.
   Use [tools.md](references/tools.md) for wrapper inputs and provider choices.
2. **Run a small, budgeted search.** Start each material route with one paid
   call and at most ten rows, using provider-native limits. Reserve a
   conservative cost bound before dispatch; keep separate provider/call caps
   and the default five-credit Deepline allowance per next complete lead.
   Save the full redacted wrapper output before displaying a compact summary.
   Never automatically retry an uncertain paid call.
3. **Verify company fit and intent from evidence.** Resolve and deduplicate
   canonical domains. Require separate company-fit and current-signal evidence
   for the same company. Read the actual source: a keyword match, search snippet,
   ingestion date, or empty provider response does not prove qualification or
   disqualification. Keep rejected, unresolved, and provider failures separate.
   Use the output contract's taxonomy and client-writing rules to classify the
   company and explain the signal from the same evidence.
4. **Find the requested buyer and validate their email.** Only after the account
   passes, verify the person's current role and company before contact-data
   lookup. Respect primary/secondary role groups. Email is required unless
   explicitly overridden; validate every stored address with a freshly
   discovered Deepline ZeroBounce tool. Accept explicit `valid` directly; reject
   `invalid`, `do_not_mail`, `spamtrap`, and `abuse` without fallback. For
   `catch-all` or `unknown`, discover and describe BounceBan through Deepline,
   then check the same address once within budget. Accept only a successful
   `deliverable` verdict, preserving both receipts. Otherwise retain the
   candidate as unresolved and continue with another address or requested buyer.
5. **Save results; continue until the target or an honest stopping condition.**
   Persist evidence, attempts, costs, decisions, and remaining routes as work
   proceeds. Change route when a candidate fails. Do not claim exhaustion while
   actionable routes remain, exceed a budget, or present a partial run as
   complete. Write `report.md`, `results.json`, and `leads.xlsx`; validate the
   full output contract and run `scripts/validate_run.py` before delivery.

## References

Read [workflow-rules.md](references/workflow-rules.md) when preparing a run for
the exact evidence gates, role fallbacks, budget accounting, receipt helper,
and shortfall rules. These safeguards remain mandatory; this shorter entry
point does not change them.

Use [output-contract.md](references/output-contract.md) for request/result
schemas, fixed workbook columns, and validation. Use the bundled workspace
dependencies for workbook generation; do not add a project dependency.
