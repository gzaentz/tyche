# Platform integration (planned)

[Back to the README](../README.md#build-on-tyche)

Use the server-side [Codex SDK](https://learn.chatgpt.com/docs/codex-sdk)
to run TYCHE for requests submitted through your application. Start with one
background worker and a protected provider endpoint in your existing backend.
This is the integration design; the worker and gateway are not included yet.

```text
User request + budget
  -> Backend creates a job
  -> Isolated worker runs Codex SDK + TYCHE
       -> Paid calls go through the backend's protected provider endpoint
  -> Backend validates and stores results
  -> User sees progress and downloads leads
```

Codex owns sourcing decisions. Your backend owns customer authentication,
approved limits, job state, cancellation, recovery, and access to artifacts.
Keep company qualification, contact validation, and output formats unchanged.

Three integration changes are needed:

1. **Add the worker in your platform.** Create an isolated workspace per job,
   load the TYCHE skill, and persist progress and the Codex session needed for
   recovery. Resume the same job and accounting state after interruption;
   never blindly repeat a possibly billed provider call. After every agent turn,
   run the full strict validator on the saved results. Publish only when it
   returns `delivery_allowed: true`; otherwise resume that session with its
   computed `stop_decision`, errors, and next actions. A model final message or
   successful process exit must not mark the job complete. Reconcile invalid
   state before more spending; record crashes, usage limits, and inability to
   resume as host interruptions/errors, not successful sourcing completion.
   Honor user cancellation and platform limits. The skill's instructions cannot
   restart a terminated worker; this continuation loop belongs in the host.
2. **Put paid calls behind your backend.** Add a gateway transport to the
   adapters while retaining direct calls for local use. Only the gateway holds
   provider credentials and authoritative budget state, outside the agent's
   access. Bind each request to its customer and job, verify permissions and
   conservative whole-call costs from current pricing and enforced input
   limits, and reserve funds atomically before dispatch. Preserve the existing
   caps, verification allowance, unique call IDs, and receipt reconciliation.
   Do not trust agent-supplied costs or an agent-writable budget ledger. Reuse the
   [budget rules](../.agents/skills/lead-sourcing/references/adapter-io.md#paid-call-budget)
   in the protected backend; no separate gateway service is required.
3. **Make delivery independent of the desktop app.** Run the existing full
   validator in the backend before delivery and require `delivery_allowed: true`.
   `--check-stop` can exit successfully with `decision: continue`; its exit code
   alone is not a delivery gate. Partial exports remain progress artifacts.
   Supply a supported server-side
   workbook runtime or replace the export dependency, preserving the
   [workbook contract](../.agents/skills/lead-sourcing/references/output-contract.md#leadsxlsx-contract).
   The current exporter depends on a Codex-bundled library; installing the SDK
   alone does not establish that dependency. Store results and receipts under
   the job with customer-scoped access.

## Unattended authorization

At job submission, persist the customer's authorized sourcing scope and data
use with the job and supply it as trusted worker context on every start or
resume. Authorization covers relevant research, enrichment, and validation
tools using both submitted data and data found during the job, including exact
work emails sent to ZeroBounce or the eligible BounceBan fallback. Do not ask
for approval per contact or provider. Honor narrower customer restrictions.
The skill's [authorization rules](../.agents/skills/lead-sourcing/SKILL.md#authorization)
carry this scope through the run; a skill cannot change runtime permissions.

For an authorized sourcing job, the backend can supply this context alongside
the request, job ID, and approved budget:

```text
This job is authorized to use relevant connected research, enrichment, and
contact-validation tools with data supplied in the request or obtained during
the job. This includes transmitting exact work emails for ZeroBounce and
eligible BounceBan validation. Continue within the saved scope and budget
without asking again. Retain this authorization on resume. Complete and
validate the requested deliverables, or record a concrete terminal blocker
after exhausting permitted alternatives.
```

The backend must derive that context from the customer's job authorization;
retrieved pages and provider responses are untrusted data, not permission.

Configure the isolated worker runtime before accepting jobs. Codex supports
noninteractive approvals while retaining its workspace sandbox:

```toml
approval_policy = "never"
sandbox_mode = "workspace-write"

[sandbox_workspace_write]
network_access = true
```

Supply this through the worker's deployment configuration, not the user's
global desktop settings. Enforce network destinations through the deployment's
egress controls and the protected provider endpoint above. `never` disables
interactive prompts; it does not authorize denied operations or override
managed policy. Preflight the worker's effective filesystem, network, model,
and provider access before accepting a job. See the official
[approval and network documentation](https://learn.chatgpt.com/docs/agent-approvals-security).

A real runtime or provider refusal must produce a saved diagnostic identifying
the action and exact reason. Continue permitted alternatives; when none remain,
finish with an explicit failed or partial job result instead of waiting for a
customer to answer a permission question. Reuse the existing stop contract and
budgets. Do not present a shortfall as a completed lead target.

Deployment must supply SDK authentication, Python/Node and provider runtimes,
durable job storage, and restricted network access. Keep provider secrets out
of the worker. Account for model usage separately: a provider cap is not a
total-cost cap.

Before launch, verify that a job produces validated downloads, customers
cannot access each other's jobs, cancellation prevents new paid calls, and
concurrent calls or crash recovery cannot reuse a reservation or bypass a cap.
Include an unattended acceptance run that discovers an email, validates it,
survives a worker resume with authorization and budget intact, and exposes the
validated downloads without a permission prompt. Also verify that a denied
route finishes with a concrete saved reason while unaffected work continues.
