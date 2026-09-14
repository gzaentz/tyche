# Provider pricing

Native tools calculate reservations from the current catalog. When Harvest's
profile catalog omits a price, `scripts/provider_pricing.py` supplies the measured
rate for the exact tested mode. The saved attempt includes its measurement date,
basis and receipt hash. The LLM does not supply routine prices or reserves.

Deepline measurements from the authorized September 14, 2026 diagnostic:

| Operation | Tested option | Credits | USD |
| --- | --- | ---: | ---: |
| Harvest company lookup | One company | 0.03 | 0.003 |
| Harvest company search | One page, including no results | 0.03 | 0.003 |
| Harvest profile | `main="true"`, no add-ons | 0.03 | 0.003 |
| Harvest profile | `main` omitted, no add-ons | 0.05 | 0.005 |
| Harvest lead search | One page | 0.70 | 0.070 |

Only the two profile rates need a fallback; other operations use their catalog
prices. Optional email, SMTP or About-profile parameters are unpriced until
separately verified. New options never inherit a basic-profile price.

These are planning prices, not provider-enforced maximums or actual bills.
Returned billing settles the ledger; unknown charges retain their reservations.
A charge above its reservation pauses paid work for reconciliation. Never turn
an unknown/error response into a zero charge or repeat it to discover its cost.

The initial email reserve is the catalog's ZeroBounce rate times the requested
lead count. It protects future verification money without counting it as spent.
Additional attempts and eligible BounceBan fallbacks still reserve their costs.
At the diagnostic date, ZeroBounce quoted $0.028 per check; BounceBan quoted
$0.006 per verification and $0 for the saved job's status getter. Poll the getter
with its saved job ID; do not resubmit paid verification as a status read.
