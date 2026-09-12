# Leviath design recommendations

## Status and decisions

**A:** inject authoritative remaining-budget information on every logical model request, outside the stable prefix. **B:** optimize deterministic provider-cache prefixes, not local response caching; add supported cache hints and usage accounting.

**Evidence limitation:** both investigators failed without returning evidence. The interrupted synthesis saved no deliverable. The file/function anchors below come from the task brief, not verified repository reads. Exact lines, real code quotes, counter semantics, region ordering and provider adapters remain unverified. This is a provisional plan, not a merge-ready review. All snippets are proposed examples, not current code. No quotes or line numbers are fabricated.

The requested `/data/work/oracle-output/leviath-design-recs.md` destination was denied by the workspace boundary. This document is saved at `oracle-output/leviath-design-recs.md` inside `/data/work/leviath` instead. No repository code was changed, outbound communications sent or paid experiments run.

## A — Turn-cap visibility

### Recommendation: authoritative request-time annotation

Compute a budget snapshot **after iteration admission, immediately before final request serialization/send**, from the same authoritative state and policy as `enforce_max_iterations`. Recompute for every logical model request, including requests without tool activity. Transport retries reuse the logical request snapshot unless the actual runtime policy charges retries against the cap. Resume and stage/branch transitions reconstruct from their authoritative state. Replace, rather than accumulate, this ephemeral annotation; do not persist it as model-writable region content.

**Placement:** after all stable instructions/regions, preferably after existing chronological conversation in the last provider-supported runtime-instruction position. This preserves reuse of both static context and unchanged conversation. Never insert between a tool call and its results. If late runtime instructions are unsupported, append to the end of the instruction block after all stable content and before conversation. That fallback retains static-prefix caching but forfeits some history reuse. Verify the adapter's final serialization does not hoist changing metadata ahead of stable content. Do not change existing content's role to obtain caching.

**Alternatives:** `runtime_info` remains useful for inspection but optional discovery cannot reliably support pacing and may require another model cycle. An ordinary mutable region risks stale state, edits and early-prefix invalidation. A dedicated read-only ephemeral tail region is acceptable only with the same request-time semantics above.

### Counter semantics and model example

Trace admission, increment and dispatch together. An iteration may not equal a provider request: label it **stage iteration** unless equivalence is established. Proposed shared contract, not current code:

```text
BudgetSnapshot {
  stage, current_admitted_iteration, effective_limit,
  iterations_remaining_after_current, final_admitted_iteration
}
```

For a policy admitting exactly M iterations, current ordinal is counter+1 if counter counts completed iterations before admission, or counter if already incremented. `>=` versus `>` alone cannot settle this without check/increment order. Preserve existing enforcement behavior; any discovered cap bug needs a separate explicit change. Use the effective limit and actual unlimited representation, not an assumed `None` or zero. Exhausted/zero budget must refuse dispatch, not produce a fictitious admitted turn.

Before, per the brief: no automatic budget disclosure. After:

```text
[Runtime budget] Stage research: iteration 10/12; 2 remain after this iteration.
```

Last admitted iteration:

```text
[Runtime budget] Stage research: iteration 12/12; 0 remain afterward.
Complete using this stage's required completion mechanism now; do not plan another model turn.
```

For unlimited, omit the annotation or say `Stage iterations: unlimited`. Completion guidance must respect stages requiring a submission tool rather than final prose. Do not imply another response can follow a final-iteration tool action. Disclosure improves pacing but cannot replace clean termination if the model ignores it.

### File changes and validation

| File / anchor from brief | Proposed change | Exact lines / real quotes |
|---|---|---|
| `crates/leviath-runtime/src/pipeline/watchdog.rs`, `enforce_max_iterations` | Expose shared admission/budget policy; trace callers/increment before defining arithmetic. Preserve enforcement. | Unavailable; investigators returned no evidence. |
| `crates/leviath-runtime/src/pipeline/response.rs`, `assembled_context` / outbound assembly | Inject ephemeral request-time annotation outside stable spans; verify final wire placement. | Unavailable; dispatch site needs confirmation. |
| `crates/leviath-runtime/src/runtime_info_tool.rs` | Reuse shared policy; retain fields, optionally add explicit derived remaining/final fields. | Unavailable; names/types need confirmation. |
| Iteration/checkpoint/adapter tests | Assert snapshot/admission agreement and valid serialization. | Paths unresolved, not guessed. |

Tests: caps 0/1/2/12; first/last admitted iteration; refusal beyond cap; unlimited; counted versus transport retries; checkpoint restore; stage/branch reset; consistent concurrent snapshot; required final submission accepted; no annotation accumulation; intact tool exchanges; stable-prefix bytes unchanged across hints.

**Effort:** 1–2 engineer-days after flow verification; more if iterations and requests differ. **Benefit:** fewer avoidable cap-exhaustion failures and better pacing, not guaranteed elimination of wedges. Measure stage completion, cap exits and useful partial outputs. Annotation token overhead is small but tokenizer-dependent.

## B — Smart input prompt caching

### Recommendation: stable prefix, volatile tail

Provider caching generally reuses a matching leading token sequence, not arbitrary repeated passages later. A change limits reuse at the first changed position; it need not invalidate the earlier prefix.

Target layout, **not a claim about current assembly**:

```text
Stable tool definitions / system instructions [provider-defined order]
Stable blueprint instructions
Long-lived regions, deterministic declared order
Slow-changing regions, deterministic declared order
Frequently changing regions
Chronological conversation, including complete tool exchanges
Ephemeral runtime-budget annotation [where adapter supports it]
```

Within semantic/role constraints, order least frequently changing content first. Keep system/blueprint bytes identical while configuration is unchanged. Preserve region IDs/order, delimiters, whitespace, tool-schema ordering and deterministic serialization. Exclude per-turn counters, timestamps, random IDs and changing headers from stable spans.

Do not conceal real edits or freeze stale regions for cache hits: an edit correctly invalidates its suffix. Reordering precedence-sensitive instructions is not a cache-only optimization; use explicit stable/slow/dynamic metadata with compatibility and semantic tests. Never promote tool/user content to system authority. Keep conversation chronological and tool-call/result pairs intact. Append-only history can extend a reusable prefix; changing metadata before history sacrifices that opportunity.

### Before / after — hypothetical defect

```text
Before: system | volatile region | unchanged blueprint/regions | history
Turn 2: system | CHANGED region  | unchanged blueprint/regions | history+
Reuse may stop at the changed region despite later repeated text.

After: system | blueprint | long-lived regions | slow regions | dynamic regions | history | hint
                          reusable prefix                    | changing suffix
```

Current `assembled_context` order and serialization are unknown, not proven defective. If already stable-first, preserve them and focus on accidental volatility, wire hints and telemetry.

### Provider hints and parameters

General guidance, not locally verified adapter support, externally checked documentation or current price quotations:

| Route | Recommendation |
|---|---|
| **OpenRouter** | Caching depends on model and downstream provider. Automatic-prefix and explicit-cache-control routes differ; there is no universal discount or universally sufficient switch. Preserve documented content-block `cache_control` for supported Anthropic routes. Stable provider routing can improve cache locality, but do not disable necessary failover solely for savings. |
| **DeepSeek** | Prefix caching is generally automatic; do not invent `cache_prompt` or a cache-control flag. Preserve matching prefixes and measure hit/miss usage. Granularity, retention, read/miss prices and hit availability depend on endpoint/model; promise neither a fixed TTL nor guaranteed turn-two hits. |
| **Anthropic-compatible** | Where supported, attach `cache_control: {"type":"ephemeral"}` to the last eligible stable content block before dynamic content. Observe model-specific minimum lengths, breakpoint limits and retention. Writes may carry premiums; reads are discounted. Additional breakpoints need a demonstrated reason. |
| **Kernal** | Compatibility, forwarding and billing are unknown. Do not assume Anthropic compatibility or send guessed parameters. Enable explicit hints only after confirming support; deterministic prefix layout remains useful independently. |

Use capability gating, conceptually `auto / disabled / explicit-supported`, rather than identical parameters everywhere. Memoizing local rendering saves CPU, not billed input tokens. Do not build a response cache for iterative model outputs.

### Files, implementation and tests

| File / module | Proposed change | Exact lines / quotes |
|---|---|---|
| `crates/leviath-runtime/src/pipeline/response.rs`, `assembled_context` | Identify stable/changing spans; remove accidental volatility; deterministic safe ordering. Snapshot final serialized requests. | Unavailable; no returned code evidence. |
| Region registry/order/render implementation reached from assembly | Explicit volatility/order metadata only if needed; stable tie-breaking; preserve precedence or migrate explicitly. | Path and current order unresolved. |
| Provider request types, serializers and config | Capability-gated content-block hints; preserve them through serialization; stable schemas and appropriate routing. | Adapter paths/current parameters unresolved. |
| Usage parsing/accounting | Normalize cached-read/write and uncached tokens, billed cost, model/provider and fallback. Missing fields mean unknown, not zero. | Paths/existing telemetry unresolved. |

Tests: two-turn wire snapshots; edits preserve earlier prefix and invalidate intended suffix; deterministic reloads; unchanged roles/precedence; valid tool exchanges; A hint outside stable spans; hints survive supported adapters; unsupported routes receive no unknown parameters. Usage fixtures should cover DeepSeek-style `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`, nested cached-token fields where used, and cache-write accounting; verify actual schemas first.

Start with offline fixtures. A separately authorized live canary should compare billed input cost and latency with controlled model/provider/prefix size. Test parser correctness offline, not guaranteed live cache hits. Avoid full-prompt logging merely to measure caching.

### Conditional savings: input versus total cost

S = reusable prefix tokens/request; V = average uncached tail tokens; N = requests; h = eligible-prefix hit fraction after the first request; r = cached-read / ordinary input price; w = additional write premium in ordinary-price token units across the run.

```text
Baseline input cost = N × (S + V) × input_price_per_token
Saved cost ≈ [(N−1) × h × S × (1−r) − w] × input_price_per_token
Input savings fraction ≈ [(N−1) × h × S × (1−r) − w] / [N × (S+V)]
```

Illustrations with no write premium, not vendor-price promises:

| Scenario | Assumptions | Input savings |
|---|---|---:|
| Large stable prefix, long run | N=10, stable share=80%, h=100%, r=0.10 | 64.8% |
| Weaker hits/discount | N=10, stable share=80%, h=50%, r=0.50 | 18.0% |
| Short run | N=2, stable share=80%, h=100%, r=0.10 | 36.0% |
| No hits | h=0 | 0%, or negative with write premiums |

If input is 60% of the original bill, 64.8% input savings means about **38.9% total-cost savings**, assuming output usage unchanged. Growing history, edits, expiry, small prefixes and failover reduce savings; reused history can improve them. Repeated explicit writes can erase short-run gains.

**Correction to interrupted draft:** S=20,000 tokens, ordinary input $0.25/million and cached input $0.025/million gives **$0.0045 saved/hit**, not $4.50. Nine hits save $0.0405; 49 save $0.2205 before write premiums. These are hypothetical prices.

**Effort:** 2–4 engineer-days for assembly, hints and accounting; longer if content-block types or precedence-sensitive migration are required. **Benefit:** potentially material input savings for large stable-prefix workloads; no unconditional percentage until prefix share, hits and billed costs are measured.

## Implementation sequence and merge gates

1. Resolve missing evidence: real numbered excerpts from the three named files, admission/increment/dispatch, region ordering and active provider serializers. Establish exact patch sites before merging; this requirement remains open.
2. Implement shared budget snapshot and ephemeral annotation without changing enforcement. Test stage completion, retry and resume.
3. Snapshot final outbound requests; preserve existing stable prefixes; reorder only when safe and beneficial.
4. Add capability-gated hints and usage accounting; validate offline.
5. Evaluate completion and billed-input costs in a separately authorized rollout. This plan authorizes no spending decision over $200.

**Critical coupling:** never put changing budget metadata ahead of reusable context. Validate the boundary in the provider's final serialized request. Visibility helps pacing; runtime enforcement and clean finalization remain necessary to prevent wedges.
