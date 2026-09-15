# Codex lab compatibility audit

## Decision

**Keep the Codex architecture, but do not merge/promote this as ready for the
current PR #198.** TYCHE uses the actual Codex CLI, its native model instructions,
code-mode engine and MCP tools. The remaining blocker is Leadpoet's admitted
model protocol. Replacing Codex with PydanticAI, adding a second model client,
or changing Luna's metadata to impersonate another model would not meet the
agreed goal.

The intended boundary remains small:

```text
lab harness.run_icp(icp)
  → host-provided Codex session
  → native TYCHE MCP tools
  → existing lab provider broker
  → existing source review and validation
  → reviewed company JSON, checkpoint, return
```

This audit is against TYCHE PR #10 and
[Leadpoet PR #198](https://github.com/leadpoet/leadpoet/pull/198) at
`2558d4bc418046ac9146c7992032405034150601`, on September 15, 2026.
Leadpoet was only read as source. Its code, tests, image and services were not
imported or run. No live model, sourcing provider or personal Codex login was used.

## Blocking protocol mismatch

The official, checksum-verified Codex 0.154.0 CLI and matching code-mode companion
were run against a TYCHE-owned loopback server. The first request contains:

```json
{
  "model": "openai/gpt-5.6-luna",
  "reasoning": {"effort": "xhigh", "context": "all_turns"},
  "input": [{"type": "additional_tools", "tools": ["native tool namespaces"]}]
}
```

This is an abbreviated diagnostic, not a valid request example. The captured
native tool tree reaches depth 16, measured with the operation parameters at
depth zero. The enclosing worker frame adds another level.

| Native behavior observed | Current PR #198 contract | Result |
| --- | --- | --- |
| `reasoning.context: "all_turns"` | Only `effort` and `summary` | Rejected |
| `input[].type: "additional_tools"` | Six admitted message/call/reasoning item kinds | Rejected |
| Namespaced tools, including `functions.exec` | Only flat `function` and `custom` schemas | Requires explicit native support |
| Tool schema reaches depth 16 | Operation depth 12; worker-frame depth 14 | Rejected |
| Continued custom calls contain `namespace` | Call-history fields omit `namespace` | Rejected on continuation |
| Code-mode tool output is an array of text content parts | Call output must be a string | Rejected on continuation |

These are source-backed incompatibilities, not conclusions drawn from HTTP errors
alone. The fixture checks the relevant closed fields and structural limit without
importing Leadpoet's validator. It is intentionally not a replacement validator.

Sources:

- [Pinned Codex Luna metadata](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/models-manager/models.json): Responses Lite and code-mode selection are model metadata.
- [Codex request construction](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/core/src/client.rs): builds `additional_tools`, tool namespaces and `reasoning.context`.
- [PR #198 operation schema and validator](https://github.com/leadpoet/leadpoet/blob/2558d4bc418046ac9146c7992032405034150601/lab_arena/operations.py): closed fields, text-only tool outputs and depth ceiling.
- [PR #198 worker document limits](https://github.com/leadpoet/leadpoet/blob/2558d4bc418046ac9146c7992032405034150601/lab_arena/contracts.py): independently validates the enclosing frame.
- [PR #198 bridge](https://github.com/leadpoet/leadpoet/blob/2558d4bc418046ac9146c7992032405034150601/lab_arena/lab_arena_codex.py): forwards the native body after removing stateless transport/telemetry fields; it does not adapt these protocols.

PR #198's native test uses `openai/gpt-4o-mini`, which takes a different metadata
path in Codex. Its shell-call test does not establish compatibility for Luna's
code-mode/MCP loop. An experimental direct-tool model profile was also rejected
on MCP namespaces; that workaround is not included in TYCHE.

## TYCHE issues fixed during this audit

| Issue | Change | Evidence |
| --- | --- | --- |
| `features.multi_agent=false` can lose to model-selected agent metadata | Set `agents.enabled=false` and `features.multi_agent_v2=false`; disable image generation | Native request has no agent/image namespace; CLI config checked |
| Killing only Codex's process group can leave MCP alive | MCP watches parent identity and exits when Codex dies | Real Python parent/child test with separate process groups |
| A stalled/trickling provider could occupy most of the research window | Bound the entire response to 125 seconds or remaining research time, whichever is shorter | Absolute-deadline test; unknown outcome still retains its reservation |
| Only 30 seconds remained for final evidence review | Stop research at 2,250 seconds; retain the 2,670-second process bound | Margin covers two 185-second model waits plus local finalization |
| Silent host version drift | Require the audited `CODEX_VERSION` on the mounted helper | Wrong-version and wrong-helper tests |

[Codex agent selection](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/core/src/config/mod.rs)
explains why the explicit agent setting is necessary.
[Codex MCP launcher](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/rmcp-client/src/stdio_server_launcher.rs)
creates the separate process group. The parent watcher allows up to 250 ms for
local cleanup; requests already dispatched remain subject to host accounting.

## Other boundaries reviewed

- **Trigger and isolation:** synchronous `run_icp` returns company dictionaries.
  The mounted source path, both lab sockets, host helpers, binary and output
  path are required. State is unique per run. Desktop launching is unchanged.
- **Runtime package:** PR #198 extracts the full checksum-pinned npm vendor
  tree. Inspection of that exact archive confirmed `bin/codex-code-mode-host`
  is included beside Codex. The native runtime does not require a separate Node
  installation. Linux/gVisor execution of that package is still unverified here.
- **Model transport:** PR #198 owns its loopback bridge, ephemeral credential,
  upstream provider key, price table and billing. TYCHE adds no model SDK or
  standalone service and does not replace native Codex model metadata.
  A read-only check of [OpenRouter's public model catalog](https://openrouter.ai/api/v1/models)
  confirmed the exact Luna ID and `xhigh` effort. It does not prove this native
  Responses protocol works or that a particular lab round admits the model.
- **Provider calls:** only the reviewed 21-tool Deepline catalog is advertised.
  Calls use lab operation frames. Actual billing receipts and provider identities
  are retained; uncertain outcomes block further paid research without replay.
- **Input:** primary signal, optional bonus order and age bounds, requested roles,
  geography, seniority, exclusions and company criteria are retained. Structured
  signal/attribute objects are explicitly unsupported. This targets the current
  text-based `intent_details_v1` + `contacts_v1` rounds, not every historical ICP.
- **Output:** only the current reviewed evidence fingerprint can deliver. Strict
  validation runs before JSON checkpointing and again before returning. Saved and
  checkpointed JSON must match. False final prose and checkpoint failure do not
  count as delivery. This journey passed with TYCHE fixtures.
- **Contact provenance:** the selected email must be observed in the saved
  HarvestAPI `findEmail="true"` profile. Its provider record ID is emitted.
  The lab's same-run receipt resolution/trusted refetch remains authoritative.
- **Policy differences:** TYCHE retains its stricter email gate. ScrapingDog,
  local workbook generation, arbitrary observed web evidence and Fast service
  tier are outside this lab adapter. It is not identical to a desktop session.

## Required work in PR #198 before readiness

1. Define and validate the native Luna protocol end to end: Responses Lite,
   namespaced tool schemas/calls, typed text tool outputs and bounded nesting.
   Preserve closed validation, operation authorization, quota limits and billing.
   Both operation and worker-frame validators must accept the same request.
2. Verify that the selected OpenRouter route actually supports those forms.
   Widening a gateway allowlist alone does not prove upstream compatibility.
   Protocol adaptation, if required, belongs in the host Codex integration so
   TYCHE remains a small shared-tool bundle.
3. Extend the native test to the actual Luna/xhigh configuration with its
   code-mode companion, TYCHE MCP schema, two tool round trips and compaction.
   Check first request, continued history and the returned tool result shapes.
4. Resolve the 4,096 output-token ceiling with the selected model's reasoning
   needs. A high-effort response can spend its allowance before producing useful
   tool output. This is a risk to measure, not a demonstrated sourcing failure.
   [OpenRouter reasoning-budget guidance](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens)
   explains the shared reasoning/output budget.
5. After deployment, verify one lab trigger through reviewed company JSON
   received by the actual entrypoint and scorer. Include timeout cleanup and
   quota exhaustion. Model admission, live costs and sourcing quality still
   require this bounded integration check.

TYCHE compacts at 16,000 reported input tokens and caps tool output. These are
useful bounds, but token count is not the same as PR #198's 128-item limit or
32,000-character field limit. Host admission must remain the final authority.

## Reproducing the offline checks

Results: 15 adapter tests passed; two native Codex tests passed (ordinary
continuation and forced compaction); one native contract case was an explicit
expected failure. The staged bundle contained 36 exact source matches and
passed isolated import/local-refusal checks. Previously passed shared research
checks remain applicable: 75 passed, one optional workbook-runtime test skipped;
this audit did not modify those shared modules.

```sh
python -m pytest tests/test_arena_codex.py -q
TYCHE_TEST_CODEX_BINARY=/path/to/codex python -m pytest tests/test_codex_wire.py -q -rx
```

Use the full official 0.154.0 package, with `codex-code-mode-host` beside the
binary. The native fixture starts the production TYCHE launch path with a
fixture host session and a fixture MCP implementation behind the real shared
tool schemas. It replaces the lab-only startup guard, so it can run locally.
Only its scripted `tyche_inspect` implementation executes; it cannot source leads.

The rejecting case reports an explicit expected failure for PR #198. The
permissive scripted cases prove native Codex/code-mode/MCP continuation and
compaction, not that PR #198 currently accepts them. JSON request diagnostics
are saved under pytest's temporary test directory. No live credentials are
inherited by the native process.
