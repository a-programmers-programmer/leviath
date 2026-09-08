# Leviath Fork Review — upsteam-readiness recommendations

**Reviewer:** OpenAI GPT-6 Astra (`openai/gpt-6-astra`), driven via OpenRouter
**Scope:** `a-programmers-programmer/leviath` (fork) vs `GEMISIS/leviath:main`
**Diff:** 127 files, ~10,715 insertions / ~2,340 deletions
**Method:** diff split into 5 domains (cli ×2, backend crates, docs, meta); each reviewed independently with a per-call budget guard
**Cost:** **$0.0049 total** against a $25 cap (413,794 input / 14,571 output tokens)
**Primary source docs:** `/data/tmp/levdiff/astra_{backend,cli_part_aa,cli_part_ab,docs,meta}.md`

---

## Bottom line

**Do not upstream this as one patch.** The fork's core additions — the MCP-to-daemon bridge, `lev integrate`, `lev run --wait`/shared waiter, and origin-scoped global-script grants — are genuinely valuable. But the diff bundles those with unrelated reversions of upstream release hygiene, and its safety claims (human gates, no-permanent-delete, provenance-as-trust) are not demonstrated by the code. It should be split into coherent PRs, not merged as-is.

## Unified top recommendations (across all 5 reviews)

### Security / correctness — fix before anything ships
1. **Make unattended execution + persistent tool installation explicit, separate permissions.** `McpServeArgs::default()` sets `attended:false`; the default orchestrator can launch 8 coder workers and its `install_tool="ask"` is bypassed in unattended mode. `--attended` alone (which `run.yolo` overrides) is not an enforced server policy.
2. **Fix the stale global-grant retention bug (concrete bypass).** At refresh, `expand_global_grants()` starts from the already-expanded list, so a repo-local `<workdir>/tools/echo.rhai` can shadow a trusted global `echo` and still be granted. Fix: preserve *manifest-explicit* grants separately; refresh = `explicit + currently-eligible-global`, never `previously-eligible + currently-eligible`. Add a spawn→shadow→refresh regression test (current fixtures hide it).
3. **Fix MCP duplicate-ID semantics, resource bounds, and stalled-writer shutdown.** A duplicate in-flight tool-call ID emits an error but leaves the original running → two replies under one ID. `read_line`, per-call tasks, and the response channel are unbounded; a stalled stdout writer can hang shutdown indefinitely (`io_dead` catches write *errors*, not stalled writes).
4. **Fix the wait-loop fairness/deadline break.** `sleep(tick)` is reset on every `select!` event; `biased;` starves an expired deadline under event traffic; deadline isn't polled during reconnect/interaction awaits. Use a persistent interval, prioritize expired deadlines.
5. **Forbid `Finished { status: Running }`.** Unknown completion labels fall back to any persisted status (including `Running`) and callers treat it as success. Only accept terminal fallback statuses.
6. **Make the fail-open read-path consent gate fail closed.** `has_granted_read_paths` returns `false` on any read/parse/report failure, yet controls whether implicit-unattended needs consent. Return `Result`; refuse implicit-unattended when indeterminate.

### Scope / split decisions
7. **Separate the unrelated reversions.** Removing tool groups (`@builtin`/`@scripts`/`@mcp`/`@all`), Ctrl+S, `j/k` provider shortcuts, the tool-groups UI, and their regression tests is a broad compatibility break — not needed for MCP integration. Restore them (or file an explicit breaking-change proposal with migration). Same for: `leviath-alloc publish=false` (release blocker), the `0.5.9→0.5.8` rollback, publish-order revert, removing the dependency-order guards, and deleting CodeQL.
8. **Suggested PR split:** (a) shared waiter + `lev run --wait`; (b) MCP server + delegation tools (depends on a); (c) host integration/bootstrap; (d) global-tool grants + install semantics (with provenance/dispatch tests); (e) small standalone fixes (installed-name validation, canonical-home). Ship the orchestrator/crystallization separately, experimental.

### Safety-claim honesty
9. **Don't describe visibility/hints as authorization.** Provenance comments are forgeable metadata, not trust evidence. `required_tools`/`require_modifications` retain a tool, they don't pre-authorize side effects. "Global ≠ human-approved." Document the MCP host-facing `install_tool` authorization boundary separately.
10. **Fix the Rhai example + tool annotations.** The `RHAI_TEMPLATE` puts `-p <target>` *after* `--` (compiler args, not cargo) and shell-interpolates an unquoted param. `ToolAnnotations::default()` isn't conservative; `DESTRUCTIVE` wrongly hard-codes idempotence. Use protocol-valid types (`structuredContent` must be an object; progress tokens string/number).

### Docs
11. Reconcile docs with the actual implementation (orchestrator has two conflicting graphs; fan-out shown as exact counts, not "up to N"; researcher `dig` branch dangles). Version the host timeout/behavior claims; treat provenance as unauthenticated metadata; correct the unconditional Claude-Code deny hook description.

### Tests
12. Prefer adversarial integration tests over coverage-shaped/helper tests: real-binary/host smoke tests, bounded test waits (virtual time), genuine concurrency/cancellation assertions, non-terminal-cursor pagination, and restore the deleted release-order/group regression tests.

## Where to act first
The two highest-leverage, lowest-ambiguity fixes are **(2) the stale global-grant refresh bypass** and **(1) the insecure unattended default** — both are concrete security bugs, not style. Then split the diff per (8). The `leviath-alloc publish=false` + 0.5.8 rollback are release blockers that should be reverted immediately regardless of anything else.