# Unify child execution; retain `spawn_agent` as the stable model interface

**Status:** Architecture recommendation, not implementation. **Decision:** (a) + (c), with (b) applied internally only. **Evidence status:** provisional, repository verification blocked.

## 1. Decision

Keep the model-facing `spawn_agent` and `fan_out` tools. Replace their divergent execution plumbing with **one in-process child-run lifecycle and terminal-result primitive**, also used by daemon/direct/MCP execution adapters. `spawn_agent` becomes a thin, versioned, cost-bounded facade over that primitive. `fan_out` remains a scheduling/composition facade, not a second executor.

Do **not** make native spawning invoke `lev run`, launch an MCP subprocess, or call back through a transport endpoint. Share the implementation beneath those adapters. CLI/MCP should serialize requests and results, not define child completion semantics.

Why this choice:

- **(a), implementation reuse:** fixes semantic divergence only if lifecycle ownership, persistence and result extraction are all shared—not merely a helper that copies text out of a child.
- **(c), stable contract:** gives training and evaluation a single predictable action with explicit limits and typed outcomes. Reusing code without stabilizing its observable contract is insufficient.
- **(b), internal replacement only:** delete the redundant internal spawner after migration. Removing the public tools would discard useful single-child and batch interfaces, break callers and needlessly change the model's action space.

One execution primitive does not require identical root-run and child-run admission policy. Root/direct execution can have no parent; children require lineage, delegated authority and parent budget reservations. They must share the execution, finalization and result semantics that apply to both.

## 2. Evidence boundary and diagnosis

The task reports that Oracle's native `spawn_agent({blueprint, task, wait:true})` drops child findings, while direct/MCP execution relays `submit_output`. It identifies these source targets:

- `crates/leviath-runtime/src/host/subagents.rs`
- `crates/leviath-cli/src/daemon/subagent.rs`
- `fanout.rs`, specifically `worker_terminal_result` (full path unverified)
- `lev run` and `leviath_run`/MCP entry points (source locations unverified)

**These are task-supplied observations, not independently verified source findings.** All three round-one investigators returned no output. The exact tool diagnostic was:

> `error: the run finished without the final output it requires; the stage that owes one never called submit_output`

The final evidence-round dispatch was unavailable. That diagnostic proves an investigator run failed to deliver an output through this harness; it does **not** establish why the repository's native spawner loses output. There is no returned repository evidence to distinguish:

1. **Extraction defect:** the child's submitted output persists, but the native adapter reads a different field or omits a fallback.
2. **Lifecycle defect:** the caller observes completion before output persistence, the child exits before submission, or finalization is lost.
3. **Both.**

The architecture below addresses both classes. An extraction-only patch should not be declared a complete fix without a lifecycle regression test.

**Requested real code quotes and exact line numbers could not be obtained.** None are invented here. The quote above is a tool diagnostic, not repository code. All edit sites below are navigation targets or proposed new files, with verification explicitly outstanding. This document is usable for direction and contract review, but is not a source-verified patch plan.

## 3. Before / after

### Before — reported behavior, internal details unverified

```text
native spawn_agent -> internal child path -> output missing
fan_out            -> worker execution -> worker_terminal_result
lev run / MCP      -> direct/daemon path -> submitted output relayed
```

### After — proposed design

```text
spawn_agent facade -----------+
fan_out scheduler ------------+--> shared RunService / child admission
CLI / daemon / MCP adapters --+          |
                                        v
                             resolve + reserve + start
                                        |
                             execute and record output
                                        |
                             finalize terminal snapshot
                                        |
                             publish completion once
                                        |
                        shared typed result + transport encoding
```

Proposed service operations, **design notation, not existing Rust code**:

```text
start(spec, execution_context) -> RunHandle | AdmissionFailure
inspect(handle, principal)    -> Running | TerminalResult
wait(handle, principal)       -> TerminalResult
cancel(handle, principal)     -> CancelAcknowledgement
```

`execution_context` is trusted host input, never supplied by the model: parent execution ID, authorization, depth ceiling, remaining budget and deadline. `RunHandle` references a unique execution, not merely an agent/session ID that may be reused.

Put lifecycle/result types and orchestration in runtime or a lower dependency crate. Runtime must not depend on CLI or MCP. If daemon currently owns necessary storage or scheduling, inject runtime-defined interfaces for those services; do not move transport dependencies into runtime. Exact crate feasibility remains unverified.

### Lifecycle invariants

1. Validate and resolve the blueprint to an immutable revision; check authorization, depth, deadline and budget before allocating a runnable child.
2. Atomically create the execution record and reserve budget. Idempotent replay returns the same execution rather than launching another child.
3. Execute using the same runner regardless of entry point. Record accepted `submit_output` durably, with execution identity and event sequence.
4. Finalize exactly once: commit execution status, canonical output/source, usage and diagnostics in one terminal snapshot. **Persist that snapshot before notifying waiters.**
5. `wait:true`, subsequent inspection, and transports return that same terminal snapshot. Notifications are wake-ups; storage is the source of truth, so a missed notification cannot lose the result.
6. Reconcile reservations and release unused funds only when chargeable work has stopped or remains fully reserved. Recovery after supervisor restart must finish or explicitly fail interrupted executions; it must not silently rerun billable work.

## 4. Terminal output and the fan-out fallback chain

**The actual `worker_terminal_result` fallback precedence is unverified.** The first migration gate is to read that function, freeze its ordering in characterization tests and extract it into the shared result resolver. Native spawning must then call the same resolver; it must not implement an approximate copy. Existing working behavior is the compatibility baseline, not an inferred ordering from this document.

The recommended explicit v1 policy below is a **proposal**, subject to reconciliation with that baseline. If it differs from current behavior, retain legacy semantics in a compatibility adapter and document the versioned change.

1. Determine authoritative execution status first. Cancellation, deadline, budget exhaustion, execution failure or finalization failure cannot become success because an earlier message exists. Preserve available content as `partial_output` on failure.
2. On successful completion, use the committed canonical structured output produced by accepted `submit_output`.
3. If that field is absent, recover the corresponding accepted submission from durable, same-execution events. Select the last accepted submission by monotonic event sequence if multiple submissions are permitted. Do not parse arbitrary log text as a submission.
4. Only if the blueprint allows final-message output, use the last eligible, completed assistant final message—not reasoning, tool chatter or a partial stream.
5. Otherwise return `MISSING_OUTPUT`. A blueprint that requires `submit_output` must not succeed merely because it emitted conversational text.

Distinguish **absent output** from an explicitly submitted empty string or JSON null. Validate against the blueprint's declared output schema; do not discard legitimate falsy values. Malformed committed output produces `INVALID_OUTPUT`, rather than silently falling through to a weaker source. Fallback recovery repairs missing representations, not contradictory or invalid authoritative state.

Return structured JSON as JSON without double encoding; text as text. Record `source: submitted | recovered_submission | final_message`. Give transports one canonical envelope, never transport-specific fallback logic. A durable external result reference may be added later with uniform retrieval semantics; v1 should instead enforce a documented output-size limit and return `OUTPUT_TOO_LARGE`, not silently truncate.

## 5. Proposed versioned tool contract

Keep the name `spawn_agent`. Publish and freeze a v1 JSON Schema; additive implementation changes must not alter its outcome semantics. Use `additionalProperties:false`. Proposed strict-v1 fields:

| Field | Type / rule | Default |
|---|---|---|
| `contract_version` | integer, exactly `1` | required in strict v1 |
| `blueprint` | nonempty registered blueprint name | required |
| `task` | nonempty UTF-8 string; published size limit | required |
| `wait` | boolean | `true` |
| `cost_budget_usd_micros` | integer >= 1; requested subtree cap | required |
| `timeout_ms` | integer >= 1; requested elapsed execution limit | required |
| `request_id` | nonempty string, 1–128 characters; idempotency key | required |
| `max_child_depth` | integer >= 0; additional permitted descendant levels | inherited, never widened |

Example:

```json
{
  "contract_version": 1,
  "blueprint": "investigator",
  "task": "Determine whether output is committed before completion is published.",
  "wait": true,
  "cost_budget_usd_micros": 2000000,
  "timeout_ms": 120000,
  "request_id": "evidence-native-lifecycle-01"
}
```

This requests a $2 subtree cap, not a spending authorization independent of host policy. Effective limits are the minima of request, blueprint, parent and host limits; disclose them in every accepted response. Invalid or unknown values fail admission. Blueprint resolution and the price schedule are pinned and reported for reproducibility.

Compatibility: legacy calls omitting v1 fields continue through an explicit legacy adapter using published, bounded host defaults. Inventory existing optional fields before freezing the schema; do not silently remove them. A training environment advertises only the strict contract it evaluates. There must not be two undocumented interpretations of the same schema.

### Deterministic response envelope

Every response includes `contract_version:1`, `request_id`, `run_id` (null only if not admitted), and `status`. Accepted executions additionally include resolved blueprint revision, effective limits and usage with explicit units. The result is a tagged union:

```text
status = running:
  output absent; error absent
status = succeeded:
  output = { kind: "json"|"text", value: JSONValue|string,
             source: "submitted"|"recovered_submission"|"final_message" }
  error absent
status = failed:
  error = { code: FailureCode, message: string, retryable: boolean,
            details: object }
  partial_output optional; output absent
```

Usage reports `cost_usd_micros`, input/output tokens, accounting finality and any still-reserved amount. Identity, timing and usage naturally vary; **determinism means stable schema, ordering rules and terminal interpretation, not identical generated prose or execution IDs**. Evaluators branch on `status` and `error.code`, never diagnostic prose.

Failure codes: `INVALID_ARGUMENT`, `BLUEPRINT_NOT_FOUND`, `PERMISSION_DENIED`, `DEPTH_EXCEEDED`, `BUDGET_EXCEEDED`, `BUDGET_UNENFORCEABLE`, `DEADLINE_EXCEEDED`, `CANCELLED`, `START_FAILED`, `EXECUTION_FAILED`, `MISSING_OUTPUT`, `INVALID_OUTPUT`, `OUTPUT_TOO_LARGE`, `FINALIZATION_FAILED`, `IDEMPOTENCY_CONFLICT`, `RUN_NOT_FOUND`, `INTERNAL_ERROR`. Version code meanings, required details and retryability in fixtures. An exhausted effective budget or deadline is not retryable under unchanged limits. No implicit model-visible retry or second billable child.

`wait:true` returns only a terminal envelope. `wait:false` returns `running`, unless already terminal, with the **same execution handle** later used to inspect, await or cancel. Changing `wait` on an idempotent replay is permitted because it changes observation, not execution. Reusing a request key with a different execution spec returns `IDEMPOTENCY_CONFLICT`; key scope is tenant/principal plus parent execution.

Provide stable inspection/wait/cancel tools or map verified existing equivalents onto the service. Their current existence and names are unverified. Observer wait timeouts return a running snapshot; they do not terminate the child. Execution `timeout_ms` applies whether or not anyone waits. Cancellation is authorized, idempotent and eventually terminal; acknowledgement alone is not evidence that chargeable work stopped. Parent termination cancels owned descendants by default. Detached execution requires separate explicit host authorization, not `wait:false` alone.

## 6. Cost and deadline enforcement

Budgets are admission and execution controls, not prompts to the child. Use integer micro-USD internally and a pinned conservative pricing table. Reserve a child's allocation atomically from the parent's remaining subtree allowance; nested reservations partition available capacity, while actual spend rolls up without double counting. Concurrent `fan_out` workers must not each receive the parent's full unreserved remainder.

Before every billable dispatch, reserve its known worst-case cost, including bounded input/output tokens and priced tools. Reconcile actual usage afterward. Enforce the minimum of child and ancestor deadlines using a monotonic clock during execution, with a recoverable wall-clock deadline in persistent state. Queue time counts from admission. Retries consume the same cap and deadline; no silent reset.

A strict cap is possible only where each chargeable operation has a defensible maximum charge. Reject strict-budget execution with `BUDGET_UNENFORCEABLE` when a provider or tool cannot be bounded. Cancelling an in-flight request cannot be assumed to erase its bill; retain the reservation until settlement. Bound tokens, concurrency, output bytes and total elapsed time as well as dollars.

Whether current accounting supports any of these guarantees remains unverified. Do not advertise hard caps before these controls exist. Maintain existing security, filesystem and tool-permission restrictions; children cannot widen authority by choosing a different blueprint.

## 7. Edit map and migration gates

**All existing-source line numbers: UNVERIFIED / unavailable.** Proposed new paths below are design suggestions, not claims that files exist.

| Location / symbol | Recommended change |
|---|---|
| `crates/leviath-runtime/src/host/subagents.rs` — native `spawn_agent` implementation, exact symbol/lines unverified | Retain registration/facade; normalize legacy/v1 input; call shared admission/start/wait; return canonical envelope; remove bespoke execution/extraction after migration. |
| `crates/leviath-cli/src/daemon/subagent.rs` — child-run lifecycle, lines unverified | Reuse shared runner and finalizer. Keep daemon request handling, transport and supervisory integration in CLI. |
| `fanout.rs::worker_terminal_result` — full path/lines unverified | Characterize actual fallback and failure precedence, extract into shared resolver, then delegate. Keep fan-out queueing/concurrency; return one typed result per item in input order, including failures. |
| `lev run` / MCP `leviath_run` handlers — paths/lines unverified | Adapt shared results to transport responses; remove local result interpretation without breaking legacy wire formats. |
| `submit_output` persistence and terminal completion publisher — paths/lines unverified | Establish durable submission, atomic terminal snapshot and commit-before-notify ordering. |
| Tool schema registry, async inspection/cancellation handlers — paths/lines unverified | Publish strict schema, preserve legacy adapter, authorize run handles and unify observation. |
| Proposed `crates/leviath-runtime/src/child_run.rs` and `child_run/result.rs` | Shared service, typed spec/handle/envelope, terminal resolver and lifecycle interfaces. Adjust placement if dependency inspection requires a lower crate. |
| Runtime/CLI manifests, module exports, tests and documentation — exact sites unverified | Verify dependency direction; add conformance fixtures, tracing and migration documentation. |

Migration order:

1. **Evidence gate:** obtain actual source and exact line references, quote the current spawn and daemon lifecycle plus `worker_terminal_result`, and capture the Oracle failure. No claim that the reported defect is fixed before this gate passes.
2. Freeze working-path behavior with characterization fixtures. Extract result resolution first without changing it; replay stored snapshots to compare outputs. Do not execute duplicate paid children for shadow comparisons.
3. Unify lifecycle/finalization and migrate native spawn, then daemon/direct/MCP and fan-out onto the service. Preserve ownership and permissions.
4. Ship strict v1 limits and envelopes behind an explicit capability/contract selection. Maintain a legacy adapter during migration.
5. Remove duplicate execution code after parity tests pass. Keep any rollback temporary; two permanent runners recreate the original problem.

## 8. RL target and acceptance tests

Train and evaluate the action **spawn(spec) -> typed outcome**, not a particular implementation or success-only narrative. Publish a normative schema, defaults, source precedence, error matrix, costs and asynchronous lifecycle examples. Pin blueprint revisions and tool-contract versions in benchmark episodes. Train recovery from typed failure and correct handle reuse; penalize exceeding limits, redundant spawning and success claims based on missing/partial output.

Required automated gates:

- Oracle regression: a child submits findings and exits; native `wait:true` returns exactly those findings.
- Cross-entry parity: native, daemon/direct, MCP and fan-out produce semantically identical terminal envelopes for the same simulated child snapshot.
- Resolver matrix: canonical submission, event recovery, permitted final message, required-but-missing submission, legitimate empty/null, invalid structured output and conflicting sources.
- Failure precedence: submitted output followed by execution failure remains failure with partial output.
- Lifecycle race: submission just before exit, delayed persistence, lost notification, duplicate completion, cancellation/finalization races and supervisor restart.
- Async identity: spawn nonblocking, inspect, await, cancel, repeated reads and retries all refer to one execution. Unauthorized handles fail without disclosure.
- Budget/deadline: simultaneous children, nested reservations, price bounds, in-flight cancellation, retry accounting, queued expiration and unbounded-cost providers. No oversubscription or premature reservation release.
- Fan-out: bounded concurrency, deterministic item order, per-item typed failures, aggregate budget and cancellation of owned workers.
- Contract fixtures: exact keys/types, version rejection, malformed input, idempotency conflict, output-size rejection and legacy compatibility.

Use deterministic fake runners/providers for conformance, fault injection and accounting tests. Separate semantic stability from stochastic task-quality evaluations. Track missing-output rate, cross-adapter mismatch, duplicate execution, unaccounted spend and time-to-terminal; a successful empty result must not conceal missing output.

## 9. Effort, risks and final recommendation

**Planning estimate only, not source-backed:** evidence and characterization 1–2 engineer-days; resolver extraction/native adapter 2–4; shared lifecycle and other adapters 4–8; strict accounting, async/recovery and contract tests 5–10. Approximately **12–24 engineer-days**, with overlap possible. Durable recovery or accounting infrastructure absent today would increase scope. An extraction-only defect may admit a much smaller immediate fix, but that is not the complete architecture. These estimates authorize no spending.

Principal risks: inadvertently changing the working fallback chain; losing daemon supervision when moving code; cycles in crate dependencies; legacy callers depending on untyped output; trusting completion before persistence; equating cancellation with zero further cost; hiding a failed run behind partial text; and declaring deterministic behavior without versioning policy. Mitigate with characterization, injected host services, a canonical persisted terminal snapshot and strict conformance gates.

**Recommendation stands:** retain `spawn_agent` as the stable RL-facing facade and `fan_out` as its batch scheduler; unify execution/finalization/result semantics below every transport. Do not route through subprocesses or merely copy a fallback helper while leaving lifecycle divergence. Source verification is the remaining prerequisite for exact edit lines, real code quotes, the existing fallback order and a defensible root-cause claim.
