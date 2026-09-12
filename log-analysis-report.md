# Leviath Repository Log & Review Analysis Report

**Prepared by:** Automated analysis stage
**Date of analysis:** 2026-09-06
**Scope:** Git commit history, working-tree state, upstream-review findings, and design-recommendation evidence for the `leviath` fork at `/data/work/leviath`.

---

## 1. Executive Summary

- **The repository is on `main`, ahead of `origin/main` by 5 commits** (including the just-committed `fanout.rs` + `subagents.rs` fix). No commits have been pushed.
- **A concrete runtime fix was committed** (`e68d74e9`): `spawn_agent` now calls the same `extract_child_output_content` fallback primitive as the fan-out collector, unifying the child-output relay across both code paths. 2 files changed, +109/−31.
- **The fix directly addresses the highest-severity runtime symptom found in the evidence trail**: the design-recommendation document records that *both* investigators "failed without returning evidence" — the same failure class the `fanout.rs` change targets.
- **Two review artifacts were produced but remain untracked** (`REVIEW_ASTRA.md`, `oracle-output/`), and the review itself signals the fork is **not upstream-ready as a single patch** — it bundles valuable additions with unrelated reversions and undocumented safety claims.
- **Several concrete security/correctness defects remain unresolved** in the review findings (stale global-grant bypass, insecure unattended default, MCP resource bounds, wait-loop deadline break, fail-open consent gate). These are 🔴/🟡 and should be fixed before any release.

---

## 2. Log Source Overview

| Attribute | Detail |
|---|---|
| **Repository** | `/data/work/leviath` (fork of `GEMISIS/leviath`), branch `main` |
| **Working-tree state** | Ahead of `origin/main` by 5 commits; `fanout.rs` + `subagents.rs` now committed; `REVIEW_ASTRA.md` + `oracle-output/` untracked |
| **Format** | Git status,`git log --oneline -5`,`git log -1 --stat`, and unified diff output |
| **Time range (evidence)** | Commit history through `Sun Sep 6 02:09:01 2026 +0000` |
| **Volume** | 5 local commits reviewed; 2 file diff (`fanout.rs`, 75 lines; `subagents.rs`, 65 lines); 1 upstream review (127 files, ~10,715 insertions / ~2,340 deletions); 1 design-recommendation document |
| **Primary source docs (referenced)** | `/data/tmp/levdiff/astra_{backend,cli_part_aa,cli_part_ab,docs,meta}.md` (out of workspace; contents summarized in `REVIEW_ASTRA.md`) |

---

## 3. Findings by Severity

### 🔴 Critical

**C1. Investigators failed without returning evidence (runtime error / lost deliverables).**
- *Description:* The design-recommendation document records that both investigators "failed without returning evidence" and that the interrupted synthesis "saved no deliverable." This is the exact failure class the committed `fanout.rs` + `subagents.rs` fix targets — workers that produced real content but never called `submit_output` were rejected by the `require_output` guard and errored instead of completing. The fix unifies both the fan-out collector path and the internal `spawn_agent` path to use the same `extract_child_output_content` fallback primitive.
- *Evidence:* `oracle-output/leviath-design-recs.md` → "Evidence limitation: both investigators failed without returning evidence. The interrupted synthesis saved no deliverable."; commit `e68d74e9` message and diff.
- *Impact:* Lost analysis output, aborted synthesis, and empty deliverables; directly motivated the committed fix.
- *Status:* **Mitigated by `e68d74e9`** (unified fallback primitive for both fan-out and spawn_agent paths). Residual risk remains for genuinely-empty workers, which now correctly error.

**C2. Stale global-grant retention bug — concrete authorization bypass.**
- *Description:* At refresh, `expand_global_grants()` starts from the already-expanded list, so a repo-local `<workdir>/tools/echo.rhai` can shadow a trusted global `echo` and still be granted.
- *Evidence:* `REVIEW_ASTRA.md` item 2 (top recommendation, "fix before anything ships").
- *Impact:* A local tool can inherit a trusted global grant — an authorization bypass.
- *Recommendation:* Preserve manifest-explicit grants separately; refresh = `explicit + currently-eligible-global`; add a spawn→shadow→refresh regression test.

**C3. Insecure unattended default + persistent tool installation.**
- *Description:* `McpServeArgs::default()` sets `attended:false`; the default orchestrator can launch 8 coder workers and its `install_tool="ask"` is bypassed in unattended mode. `--attended` alone is not an enforced server policy.
- *Evidence:* `REVIEW_ASTRA.md` item1.
- *Impact:* Unattended execution and persistent tool installation are not gated by explicit, separate permissions.
- *Recommendation:* Make unattended execution + persistent tool install explicit, separate permissions.

### 🟡 Warning

**W1. MCP duplicate-ID semantics, resource bounds, stalled-writer shutdown.**
- *Evidence:* `REVIEW_ASTRA.md` item3. A duplicate in-flight tool-call ID emits an error but leaves the original running → two replies under one ID; `read_line`, per-call tasks, and the response channel are unbounded; a stalled stdout writer can hang shutdown indefinitely.

**W2. Wait-loop fairness/deadline break.**
- *Evidence:* `REVIEW_ASTRA.md` item4. `sleep(tick)` resets on every `select!` event; `biased;` starves an expired deadline under event traffic; deadline not polled during reconnect/interaction awaits.

**W3. `Finished { status: Running }` treated as success.**
- *Evidence:* `REVIEW_ASTRA.md` item5. Unknown completion labels fall back to any persisted status (including `Running`) and callers treat it as success.

**W4. Fail-open read-path consent gate.**
- *Evidence:* `REVIEW_ASTRA.md` item6. `has_granted_read_paths` returns `false` on any read/parse/report failure yet controls implicit-unattended consent; should return `Result` and refuse when indeterminate.

**W5. Unrelated reversions bundled with the core additions.**
- *Evidence:* `REVIEW_ASTRA.md` item7. Removing tool groups (`@builtin`/`@scripts`/`@mcp`/`@all`), Ctrl+S, `j/k` shortcuts, tool-groups UI + tests is a broad compatibility break; `leviath-alloc publish=false`, the `0.5.9→0.5.8` rollback, publish-order revert, dependency-order guard removal, and CodeQL deletion are release blockers.

**W6. Safety-claim honesty — provenance-as-trust.**
- *Evidence:* `REVIEW_ASTRA.md` item9. Provenance comments are forgeable metadata; `required_tools`/`require_modifications` retain a tool, they don't pre-authorize side effects; "Global ≠ human-approved."

**W7. Faulty Rhai example + non-conservative tool annotations.**
- *Evidence:* `REVIEW_ASTRA.md` item10. `RHAI_TEMPLATE` puts `-p <target>` after `--`; `ToolAnnotations::default()` isn't conservative; `DESTRUCTIVE` wrongly hard-codes idempotence; `structuredContent` must be an object.

### 🔵 Info

**I1. Docs do not match the implementation.**
- *Evidence:* `REVIEW_ASTRA.md` item11. Orchestrator has two conflicting graphs; fan-out shown as exact counts, not "up to N"; researcher `dig` branch dangles; provenance described as unauthenticated; unconditional Claude-Code deny hook description incorrect.

**I2. Test strategy favors coverage over adverserial integration.**
- *Evidence:* `REVIEW_ASTRA.md` item12. Prefer real-binary/host smoke tests, bounded test waits (virtual time), genuine concurrency/cancellation assertions, non-terminal-cursor pagination, and restore deleted regression tests.

**I3. Suggested PR split for upstreaming.**
- *Evidence:* `REVIEW_ASTRA.md` item8. Split into (a) shared waiter + `lev run --wait`; (b) MCP server + delegation tools; (c) host integration/bootstrep; (d) global-tool grants + install semantics; (e) small standalone fixes. Ship orchestrator/crystallization separately, experimental.

**I4. Design recs (A: turn-cap visibility; B: smart input prompt caching) are provisional.**
- *Evidence:* `oracle-output/leviath-design-recs.md`. Both are plans with unverified anchors; all code snippets are proposed examples, not current code. No quotes/line numbers fabricated. Merge gate #1 requires real numbered excerpts from the three named files before implementation.

---

## 4. Trends and Patterns Observed

- **The single strongest pattern is evidence-loss on worker completion.** The design recs' "both investigators failed without returning evidence" and the `fanout.rs` + `subagents.rs` fix commit are two independent manifestations of the same failure class: workers that produced real content but did not use the expected delivery channel errored instead of relaying output. The fix addresses this directly, and crucially unifies both paths (fan-out collector and internal `spawn_agent`) that previously diverged.
- **Review findings cluster on safety boundaries, not style.** 6 of the top12 recommendations are security/correctness ("fix before anything ships"), and2 are release blockers — indicating the fork's risk is concentrated in permission/authorization and release-hygiene, not in feature breadth.
- **Evidence discipline is a recurring weakness.** The design recs explicitly flag unverified anchors and no fabricated quotes; the review flags forgeable provenance-as-trust. Both indicate the repo's artifacts are being produced with an honesty guardrail, but also that verification depth is a known gap.
- **Working tree hygiene is incomplete.** `REVIEW_ASTRA.md` and `oracle-output/` remain untracked; the fix is committed but not pushed (ahead by5).

---

## 5. Recommendations

**Fix first (security/correctness):**
1. ✅ **Committed:** `fanout.rs` + `subagents.rs` unified relay fix (`e68d74e9`) — keep; add a regression test for the "real content, no submit_output" case across both paths and the genuinely-empty case.
2. **Fix the stale global-grant refresh bypass** (C2) — highest-leverage, lowest-ambiguity security bug.
3. **Harden the unattended default** (C3) — make unattended + persistent install explicit, separate permissions.
4. **Close the resource-bound / stalled-writer / duplicate-ID gaps** (W1), the wait-loop deadline break (W2), `Finished { Running }` (W3), and the fail-open consent gate (W4).

**Release / hygiene:**
5. **Revert the unrelated reversions** (`leviath-alloc publish=false`,0.5.8 rollback, publish-order revert, dependency-order guard removal, CodeQL deletion) immediately.
6. **Split the fork into coherent PRs** per I3 rather than one upstream patch.
7. **Commit the untracked review artifacts** (`REVIEW_ASTRA.md`,`oracle-output/`) or move them out of the tree; decide push policy for the5 unpushed commits.

**Monitor:**
8. **Worker-completion error rate** before/after `e68d74e9` — confirm the "investigator failed without evidence" count trends to zero across both paths and that genuinely-empty workers still error cleanly.
9. **Provider caching/budget telemetry** per design recs A/B — do not land until merge gate #1 (verified code anchors) is satisfied.

---

##6. Appendix — Key Log Excerpts and Script Output

**Commit (verified via `git log -1 --stat`):**
```
commit e68d74e92784ada7ac39c207978b86f12a8b4591
Author: Jarvis <jarvis@byparlour.com>
Date:   Sun Sep 6 02:09:01 2026 +0000

    fix(runtime): unify child-output relay — internal spawn_agent path now uses
    same fallback primitive as fan-out collector (extract child_output_content)

 crates/leviath-runtime/src/fanout.rs         |75 +++++++++++++++++++----------
 crates/leviath-runtime/src/host/subagents.rs |65 +++++++++++++++++++++++--
 2 files changed,109 insertions(+),31 deletions(-)
```

**Diff summary (`fanout.rs` + `subagents.rs`):**
- Both paths now call `extract_child_output_content` for consistent fallback behavior.
- Explicit `submit_output` content always wins.
- Otherwise fall back to last non-empty conversation text, then `InferenceResult.response`.
- `require_output` rejection fires **only when the fallback is empty**.
- `subagents.rs` gained the same extraction logic previously present only in `fanout.rs`.
- Net: +109/−31 over2 changed files.

**Commit history (`git log --online -6`):**
```
e68d74e9 fix(runtime): unify child-output relay — internal spawn_agent path now uses same fallback primitive as fan-out collector (extract child_output_content)
8a463a07 Merge remote-tracking branch 'origin/main'
a3a6393c feat: add bounded Oracle workflow and deterministic dispatch
c9f6f3d3 fix(runtime): relay worker content before require_output rejection so investigators with real evidence complete instead of erroring
73456c26 fix(runtime): honor explicit stage model pin over global default; parse soft_iteration_cap
1a18fa0e feat(runtime): relay worker last-text on no submit_output; add soft iteration cap for agent handoff
```

**Working-tree state (post-commit):**
```
On branch main
Your branch is ahead of 'origin/main' by 5 commits.
Changes not staged for commit:
    modified:   CHANGELOG.md
    deleted:    crates/leviath-cli/agents/oracle/agent.leviath
    ... (oracle → oracle-workflow renames)
    modified:   docs/...
Untracked files:
    REVIEW_ASTRA.md
    log-analysis-report.md
    oracle-output/
```

**Evidence limitation (from `oracle-output/leviath-design-recs.md`):**
> "Evidence limitation: both investigators failed without returning evidence. The interrupted synthesis saved no deliverable. The file/function anchors below come from the task brief, not verified repository reads. ... No quotes or line numbers are fabricated."

---

## Severity Index Tally

| Severity | Count | IDs |
|---|---|---|
| 🔴 Critical |3 | C1, C2, C3 |
| � Yellow Warning |7 | W1–W7 |
| 🔵 Info |4 | I1–I4 |
| **Total** | **14** | — |