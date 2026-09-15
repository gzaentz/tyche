# Leadpoet Arena integration

TYCHE now supplies Arena's `harness.run_icp(icp) -> list[dict]` entrypoint.
One PydanticAI agent drives the existing research tools directly. The adapter
adds broker transport and JSON delivery. It needs no Codex process, MCP server,
Node, Excel runtime, database, web service, or provider credentials.

## Supported contract

- Current rounds with `intent_details_policy: intent_details_v1` and
  `contact_policy: contacts_v1`, using output schema v5. Historical policies
  are rejected before spending; this is a bundle for future baseline use.
- `intent_signals` is a unique, nonempty list of strings; `required_attribute`
  is plain text. Structured variants and separate `bonus_intents` are rejected
  before spending. This first bundle does not guess their scoring semantics.
- At most the host's `LAB_ARENA_COMPANY_LIMIT` (1–5) company/contact pairs.
- The supplied signal order, original ICP, target roles, geography, seniority,
  and required attribute remain available to the researcher. Excluded companies
  enter the existing qualification gate. Contact country/region/city and the
  current Arena seniority vocabulary are checked again before delivery. Product/service
  describes the target's offering. The independent Arena verifier remains the
  authority on factual fit and contact qualification.
- Email must occur in the selected HarvestAPI `get_profile` response from a
  lookup with `findEmail: "true"`. Output uses that profile's record ID, which
  Arena can resolve from its own execution ledger or re-fetch. A local route ID
  or Deepline request ID is never passed off as a broker call ID. The worker
  does not currently expose its broker call ID to submissions.
- TYCHE's existing qualification, email, accounting, stopping, and final
  source-review gates remain in force. Its email rule is stricter than Arena's
  catch-all acceptance rule. Only reviewed, validated records are exported.

## Trigger and output

Use Python 3.11 and `requirements.txt`. The default model matches the inspected
promoted baseline: `openai/gpt-5.6-sol`, medium reasoning. Arena supplies the
worker socket and owns provider/model credentials and authoritative cost limits.

```python
from harness import run_icp

companies = run_icp(icp)  # Arena invokes this itself with its supplied ICP.
```

Each invocation creates separate state under `/tmp/tyche-arena-*`. The saved
`results.json`, receipts, budget ledger, `validation.json`, and `companies.json`
are per-run. Arena's entrypoint writes the returned list to its standard output
file. With `LAB_ARENA_OUTPUT_PATH` present, approved delivery also calls the
host's atomic checkpoint writer. An exception produces a local `failure.json`
and remains an execution failure; it does not masquerade as an empty market.
Arena owns persistence beyond the sandbox's lifetime.

The runtime reserves 30 seconds of a 285-second run for final review, fitting
inside the legacy five-minute host limit. New 45-minute checkpoint rounds can
also run this bundle, but it deliberately keeps the conservative limit: the
current runner does not expose the signed duration to the submitted harness.
The model chooses when to finish. Deadline or quota exhaustion never silently
approves draft records. All socket reads are bounded; SDK retries are disabled.

## Provider boundary

The bundle sends the existing length-prefixed Arena operation protocol for
`openrouter.chat` and `deepline.execute`. No direct network fallback exists.
Provider calls retain their raw responses, billing, and request identifiers
through TYCHE's existing normalization and reservation code. An uncertain call
retains its reservation and blocks more paid research. The adapter caps calls
at 60 OpenRouter and 30 Deepline; the broker enforces the actual round budget.
TYCHE's provider cap remains USD 0.50 per requested pair, separate from model
costs; it is not a total-cost guarantee.
Known worker refusals retain their original codes and are distinguished from
uncertain transport failures. Without a billing receipt their reservations stay
conservative; no zero-charge provider receipt is invented.

Arena has no provider-catalog endpoint. `tyche_arena/catalog.json` therefore
contains public schemas/prices captured from the Deepline CLI for exactly the
inspected Arena allowlist. It contains no account credentials. Refresh it
before a new release if the approved operations or catalog have changed:

```bash
python3 scripts/refresh_arena_catalog.py /path/to/leadpoet
```

This reads free metadata only. Unknown or unpriced configurations keep the
existing TYCHE refusal behavior; the catalog is not a price guarantee.
ScrapingDog is omitted from this first adapter; approved Deepline research
operations provide the initial research surface.

## Build and verify

```bash
python3 scripts/build_arena_bundle.py /tmp/tyche-arena-bundle
LEADPOET_SOURCE=/path/to/leadpoet python -m pytest tests/test_arena.py -q
python -m unittest discover -s .agents/skills/lead-sourcing/tests
```

The builder stages only runtime code, shared instructions, taxonomy, public
catalog, requirements, and the required license. Reports, local settings,
credentials, tests, and repository history are excluded. Supply that staged
directory through Leadpoet's existing source-bundle/baseline process.

The integration tests use fixture model/provider responses over a real Unix
socket, run the real PydanticAI loop through Leadpoet's actual agent entrypoint,
and check its saved file with Leadpoet's actual operation, output, and contact
validators. These prove the boundary; they do not measure live sourcing quality
or replace a production-equivalent Linux/runsc trial with real brokered calls.
Publishing or promoting the bundle is a separate deployment step.
