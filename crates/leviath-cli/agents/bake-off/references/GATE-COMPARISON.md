# Gate Comparison: GraphQL Schema Contract Gate vs. GraphQL Schema Bakeoff

Two deterministic-acceptance designs for GraphQL schema work, built on the same
Leviath runtime and the same `graphql-core` parser. They serve **different
purposes** — one designs a contract end-to-end, the other converges a schema
draft — so this is not a competition. It is a line-by-line audit of what each
design's enforcement machinery actually protects against, what it costs, and
what each could borrow from the other.

---

## 1. What Each Design Is For

### Design A — GraphQL Schema Contract Gate

**Location:** `/data/work/leviath-gql-schema/crates/leviath-cli/agents/graphql-schema/`

**Purpose:** Produce a complete, reviewed, checksummed **application contract**
before any implementation work begins. The output includes SDL, named client
operations, variable coercion examples, field-level policy assignments
(authorization, nullability, batching, source), pagination specifications,
mutation semantics, evolution analysis, and concrete backend/frontend handoff
work orders.

It is NOT a schema authoring tool. It is a **design-then-govern pipeline**:
intake → domain design → SDL + operations → static checks → independent review
→ bounded repair (×2) → final acceptance with anti-staleness re-check.

The README (`README.md:1-5`) states the boundary clearly:
> "It produces schema and implementation work orders. It does not implement
> resolvers or claim their authorization, performance, or business behavior
> has been tested."

The stage layout (`agent.leviath:7-8`) runs seven active stages: `intake`,
`design`, `draft`, `check`, `review`, `revise`, `accept`. Each stage has
explicit model pins (`agent.leviath:16`, `:55`, `:93`, `:125`, `:152`, `:176`,
`:207`, `:222`):
- High-tier: `claude-opus-5` / `gpt-5.5` for `design` and `review`.
- Cheaper: `gpt-5.4-mini` / `gemini-3.5-flash` for `intake`, `draft`, `check`,
  `revise`, `accept`.

### Design B — GraphQL Schema Bakeoff

**Location:** `/data/work/leviath/study/` (runner: `run_bakeoff3.py`; diff
engine: `graphql_diff.py`; blueprints:
`/data/.leviath/agents/bake-off/agent.leviath`,
`/data/.leviath/agents/bake-merge/agent.leviath`).

**Purpose:** Take a seed GraphQL schema, fork it through two **independent
flash-iteration lanes**, then **merge** their divergent outputs into a single
reconciled schema using a recursive diff-then-decide algorithm. The output is
one SDL file, nothing else: no operations, no policies, no handoff, no review
rubric.

It is a **schema convergence tool**, not a design governance tool. Its job is
to resolve the drift that happens when two models independently improve the
same schema: renames (`ProposalChange → ProposalResult`), different interface
decompositions, different pagination patterns, etc.

The runner (`run_bakeoff3.py:48-66`) explicitly names this as "MERGE_DECISIONS":
> "You are a schema arbitrator. Below is a COMPACT DIFF between two candidate
> GraphQL schemas (A and B) for the same domain."

---

## 2. Enforcement Mechanism

### Design A — Fail-Closed, Python-Centric, Three-Gate Acceptance

**Where validity is decided:** In `scripts/gate.py`, a 194-line Python module
with zero model calls. It is invoked through the `graphql_contract_gate` Rhai
tool (`tools/graphql_contract_gate.rhai`) which base64-encodes a JSON request
and shells out to Python (`tools/graphql_contract_gate.rhai:7-10`).

The gate has three operations (`gate.py:166 dispatch()`):

| Operation | Stage | What it does |
|-----------|-------|--------------|
| `check` | `check` stage | Runs ALL mechanical checks; returns `status: "checked"` or `"blocked"` + fingerprint |
| `accept` | `accept` stage | Re-runs ALL checks, validates review fingerprint matches, writes `accepted.json` atomically |
| `verify` | downstream pipelines | Re-reads receipt, re-hashes all files, rejects if changed or wrong parent fingerprint |

**What happens on failure:**

1. **Static checks fail** (`gate.py:48-49` SDL validation; `:70-72` missing
   field policy; `:74-93` collection/pagination violations; `:98-102` mutation
   + runtime gaps; `:104-107` operation validation; `:130-143` requirement
   traceability; `:145-155` variable coercion): status `"blocked"`, returned as
   structured JSON, stage routes to `revise`.

2. **Review rejects** (`gate.py:172-175`): `review_ok()` raises `ValueError`
   ("Review is stale or for another contract", "Review did not approve", or
   specific rubric dimension missing evidence). Status `"blocked"`.

3. **Anti-staleness** (`gate.py:172`): The `accept` operation re-runs `check()`
   and compares `review.fingerprint == result.fingerprint`. If the content
   changed after the review was written (e.g. a repair modified files but the
   review wasn't re-executed), the fingerprint won't match and acceptance
   raises `ValueError("Review is stale or for another contract")`.

4. **Post-acceptance tampering** (`gate.py:192-193`): `verify` re-hashes all
   files and compares to the receipt. A single changed byte anywhere under
   `graphql-contract/` — even altering `handoff.md` — produces a different
   fingerprint and `ValueError("Contract changed after acceptance")`.

5. **Parent pipeline mismatch** (`gate.py:189-190`): `verify` accepts
   `--expected-fingerprint`; if the current contract's fingerprint doesn't
   match, raises `ValueError("Contract differs from the parent pipeline
   expected fingerprint")`.

6. **Receipt invalidated on new attempt** (`gate.py:170-171`): Any `check` or
   `accept` that finds `accepted.json` present **unlinks it** before running.
   This means a failed new run cannot accidentally leave an old success marker.
   Confirmed by test (`tests/test_gate.py:82-85`
   `test_failed_new_attempt_invalidates_old_receipt`).

7. **Exhausted repairs** → `blocked` stage (`agent.leviath:125` `max_revisits =
   2` on `revise` stage; after two repair passes the transition does not loop
   back).

**Specific failure modes caught:**

| Failure mode | Caught? | Evidence |
|---|---|---|
| Invalid SDL (unparseable) | ✅ | `gate.py:48` `build_schema()` |
| SDL with schema validation errors | ✅ | `gate.py:49` `validate_schema()` |
| Missing description on type/field | ✅ | `gate.py:54-57` |
| Wrong naming convention | ✅ | `gate.py:55-56,58,62` |
| Missing field policy (auth/nullability/source/batching) | ✅ | `gate.py:70-72` |
| Unbounded collection | ✅ | `gate.py:74-80` |
| Malformed cursor pagination | ✅ | `gate.py:82-93` |
| Missing mutation semantics | ✅ | `gate.py:98-100` |
| Missing runtime obligations | ✅ | `gate.py:101-102` |
| Operation that doesn't validate against schema | ✅ | `gate.py:107` |
| Unnamed operation | ✅ | `gate.py:109` |
| Requirement not traced to operations | ✅ | `gate.py:130-135` |
| Required coordinate not actually selected by operation | ✅ | `gate.py:135-143` |
| Variable coercion mismatch | ✅ | `gate.py:145-155` |
| Breaking change in evolution mode | ✅ | `gate.py:159-162` |
| Dangerous change (e.g. enum value added) | ✅ | `gate.py:159-162` |
| Review applied to wrong content (stale) | ✅ | `gate.py:172-175` |
| Review dimension missing evidence | ✅ | `gate.py:175-176` |
| Content changed after acceptance | ✅ | `gate.py:192-193` |
| Old receipt surviving a failed re-run | ✅ | `gate.py:170-171` |
| Symlink artifact substitution | ✅ | `gate.py:25-27` |
| Artifact too large | ✅ | `gate.py:29` |
| **Prose correctness of policies** | ❌ | `gate.py:70-72` checks presence, not truth (noted in README:189: "The checker verifies policy presence/coverage, not the truth of prose") |
| **Whether review prose is actually correct** | ❌ | Model judgment; the gate only verifies structural completeness |

### Design B — Conductor-Side, Parse-Only, Re-Dispatch Loop

**Where validity is decided:** In the **Python conductor** (`run_bakeoff3.py`),
not in Leviath hooks. The critical function is `parse_gate()`
(`run_bakeoff3.py:105-115`):

```python
def parse_gate(path, label):
    try:
        text = open(path).read()
        build_schema(text)
        return True
    except GraphQLSyntaxError as e:
        return False
```

**What happens on failure:**

1. **Lane produces unparseable output** (`run_bakeoff3.py:119-140`
   `run_schema_lane`): The conductor feeds the parse error back into the task
   string and **re-dispatches the same lane** with an amended prompt:
   > "*** YOUR PREVIOUS OUTPUT WAS REJECTED BY THE SCHEMA VALIDATOR. ***
   > It did not parse as GraphQL. The parser says: …"

   Up to `MAX_LANE_RETRIES = 2` (`run_bakeoff3.py:118`). After exhaustion, the
   lane is abandoned and the level aborts.

2. **L0 both lanes fail to produce valid output** (`run_bakeoff3.py:156-160`):
   `"ABORT: a lane never produced a VALID schema"`. The entire bakeoff fails.

3. **Merge produces unparseable artifact** (`run_bakeoff3.py:233-237`):
   `"CONVERGENCE-REFUSED"`. The merged schema is tested with `parse_gate()`
   before declaring convergence.

4. **No parseable decisions from decision lanes** (`run_bakeoff3.py:197-199`):
   `"ABORT: no parseable decisions from either lane"`. The level fails.

5. **Hook-level text checks** (`/data/.leviath/agents/bake-merge/hooks/validate-schema.rhai`):
   brace balance, duplicate definitions, orphan `|` lines. These are
   **guidance-only** — the hook can cancel a stage, but the real parse
   authority is `graphql-core` in the conductor (`validate-schema.rhai:5-6`):
   > "The real parse authority is the Python graphql-core gate in the runner."

**Specific failure modes caught:**

| Failure mode | Caught? | Evidence |
|---|---|---|
| Unparseable SDL from lane | ✅ | `run_bakeoff3.py:105-115` `parse_gate()` |
| Lane produces no file at all | ✅ | `run_bakeoff3.py:129` "no output file was written" |
| Merged output doesn't parse | ✅ | `run_bakeoff3.py:233-237` |
| No usable decision output | ✅ | `run_bakeoff3.py:197-199` |
| Unbalanced braces (hook) | ⚠️ | Guidance via `validate-schema.rhai:18-23` |
| Duplicate type definitions (hook) | ⚠️ | Guidance via `validate-schema.rhai:26-35` |
| Orphan `\|` union lines (hook) | ⚠️ | Guidance via `validate-schema.rhai:38-50` |
| **Semantic SDL errors** (e.g. undefined type reference) | ✅ | `build_schema()` catches all type-resolution errors |
| **Operations validation** | ❌ | No operations are produced or checked |
| **Field policy coverage** | ❌ | No policy model exists |
| **Requirement traceability** | ❌ | No requirements exist |
| **Variable coercion** | ❌ | No operations exist |
| **Mutation semantics** | ❌ | Prose-only, not structured |
| **Evolution/breaking changes** | ❌ | No baseline model |
| **Anti-staleness** | ❌ | No fingerprinting; a later run can silently overwrite |
| **Content tampering post-convergence** | ❌ | No receipt, no verify mode |
| **# comments in output** | ⚠️ | Spec says strip them (`bake-off.agent.leviath:67`), but the parse gate doesn't enforce this |
| **Duplicate types in same file** | ✅ | `build_schema()` rejects duplicates |

---

## 3. Cost / Latency Shape

### Design A

| Metric | Value | Source |
|--------|-------|--------|
| Stages | 8 (intake→design→draft→check→review→revise→check→accept→done) | `agent.leviath:7-222` |
| Max iterations (total) | ~140 (16+20+28+2+24+24+24+2+2) | `agent.leviath` per-stage `max_iterations` |
| Max revisit passes | 2 (revise) + 3 (review) | `agent.leviath:125,152` |
| High-tier model calls | 2 (design + review) | `agent.leviath:55,152` |
| Cheap model calls | 6+ (intake, draft, check ×2, revise ×2, accept) | per-stage model pins |
| Gate execution | ~0.5s pure Python | No model calls in gate |
| Total wall-clock (estimated) | 6-18 minutes | 8 stages × model latency + 2 repair cycles |
| On exhaustion | `blocked` status; receipt unlinked | `agent.leviath:237-249` |

The gate computation is fixed-cost: `check()` does one pass over all files with
no iteration. The `check` and `accept` stages are budgeted at **2 iterations**
each (`agent.leviath:176,207`) — just enough to call the gate once and read the
result.

### Design B

| Metric | Value | Source |
|--------|-------|--------|
| L0 lanes | 2 parallel, each up to 60 iterations | `/data/.leviath/agents/bake-off/agent.leviath:18` `max_iterations = 60` |
| L0 retries | up to 2 re-dispatches per lane on parse failure | `run_bakeoff3.py:118` |
| Merge level decision lanes | 2 parallel, each up to 25 iterations | `/data/.leviath/agents/bake-merge/agent.leviath:15` `max_iterations = 25` |
| Max merge levels | 5 | `run_bakeoff3.py:28` |
| Observed convergence | L2 (v8.log: L0 iters 9+19, L1 iters 2+3, L2 0 divergent) | `bakeoff_v8.log:1-25` |
| Observed total iterations | ~33 (9+19+2+3 = 33 model calls) | `bakeoff_v8.log` |
| Observed total wall-clock | ~10-20 minutes | L0 lanes run in parallel (async gather); L1+L2 fast |
| Diff compression | 38-60× (v8: 1454B diff vs 85837B raw) | `bakeoff_v8.log:10` |
| On exhaustion | "NO_CONVERGENCE after 5 levels" | `run_bakeoff3.py:244` |

The critical cost innovation in B is the **compact diff**: instead of feeding
two ~43KB schemas to a flash lane (which exhausts context and iteration
budget), the runner computes a type-graph diff and hands the lane a summary
that is 38-60× smaller (`run_bakeoff3.py:163-165`). The decision lane only
needs 2-3 iterations to emit keep_a/keep_b/reconcile blocks.

**Model used in the actual successful run (v8.log):**

The blueprints pin specific models with `allow_user_default = false`:

- Bake-off lanes: `deepseek/deepseek-v4.1-flash` via OpenRouter
  (`/data/.leviath/agents/bake-off/agent.leviath:18`)
- Bake-merge decision lanes: `deepseek/deepseek-v4.1-flash` via OpenRouter
  (`/data/.leviath/agents/bake-merge/agent.leviath:15`)

The successful v8 run (`bakeoff_v8.log`) completed with VALID outputs at both
levels, confirming the model pins were honored (no silent fallback to another
model). The v6 run (`bakeoff_v6.log`) failed because of a Rhai hook compilation
error (`on_completion` hook referenced a file that didn't define
`fn on_completion`), not a model issue. That hook was removed in the
`/data/.leviath/agents/bake-merge/agent.leviath` version (no `on_completion`
hook), which enabled v7 and v8 success.

---

## 4. Evidence Quality

### Design A — High

| Property | Implementation | Source |
|----------|---------------|--------|
| Content fingerprint | SHA-256 over canonical JSON of all file hashes | `gate.py:28-33` |
| Fingerprint in receipt | `accepted.json` contains `fingerprint` and `files` map | `gate.py:179-186` |
| Atomic write | `tempfile.mkstemp` + `os.replace` (not `open().write()`) | `gate.py:179-182` |
| Anti-staleness | Accept re-runs check(); compares `review.fingerprint == current_fingerprint` | `gate.py:172` |
| Post-accept verification | `verify` re-hashes all files; compares to receipt and expected fingerprint | `gate.py:188-194` |
| Receipt invalidation | Old receipt unlinked at start of any new check/accept | `gate.py:170-171` |
| Symlink rejection | `root.is_symlink()` and `path.is_symlink()` checks | `gate.py:25-27` |
| Size limits | Per-file 2MB, total 16MB | `gate.py:23,31` |
| On-disk artifacts | `graphql-contract/` contains all sources + `accepted.json` | Whole directory |
| Runner-level verification | `run_stage.py` independently verifies acceptance after `lev run --wait` | `tests/test_gate.py:171-175` |
| Test coverage | 22 focused acceptance tests including stale review, changed contract, parent pin, receipt invalidation, symlink rejection, CLI exit codes | `tests/test_gate.py` |

**What you can prove:**
- The exact set of files that were accepted (SHA-256 per file).
- That the review was written against those exact files (fingerprint match).
- That the files haven't changed since acceptance (verify mode).
- That the parent pipeline's expected contract identity is what was accepted.

**What you cannot prove:**
- That the prose in policies is correct (the gate checks presence, not truth).
- That the model review was actually read and acted upon (review is recorded,
  but model compliance is not mechanically verified beyond fingerprint match).
- That the contract was produced by a specific model or run (no run identity in
  the receipt).

### Design B — Low

| Property | Implementation | Source |
|----------|---------------|--------|
| Content fingerprint | ❌ None. No hash of output is computed. | |
| Receipt | ❌ None. No accepted.json or equivalent. | |
| Atomic write | ❌ Standard `open().write()`. | `run_bakeoff3.py:177-178` |
| Anti-staleness | ❌ None. A later run silently overwrites `bakeoff/v2_merged.graphql`. | |
| Post-convergence verification | ❌ None. No verify mode. | |
| Run identity | ❌ None. The merged output has no provenance marker. | |
| On-disk artifacts | ✅ Level-by-level artifacts (`v0_L0L0.graphql`, `v1_merged.graphql`, `v2_merged.graphql`, `vN_diff.txt`, `vN_L0_decisions.txt`) | `bakeoff/` directory |
| Log trail | ✅ `bakeoff_v8.log` records lane statuses, iteration counts, diff sizes, and convergence point | `bakeoff_v8.log` |
| Parse gate evidence | ✅ Every lane output is `build_schema()`-tested before being accepted | `run_bakeoff3.py:105-115` |

**What you can prove:**
- That the final artifact parses as valid GraphQL (the `build_schema()` gate).
- Roughly how the schema was produced (log shows levels, iterations, diff
  sizes).
- Which types were divergent and how they were decided (the decision files).

**What you cannot prove:**
- That the artifact on disk is the one the convergence produced (no hash).
- That the artifact hasn't been tampered with since convergence (no receipt).
- That the decision lanes' output was faithfully applied (the merge is
  deterministic Python, but there's no binding between decision text and
  merged output).

---

## 5. Reusability

### Design A — Highly Reusable as Standalone CLI

The gate is callable independent of Leviath:

```bash
python scripts/gate.py check --root examples/task-board
python scripts/gate.py accept --root examples/task-board --review review.json
python scripts/gate.py verify --root graphql-contract --expected-fingerprint <fp>
```

The CLI is documented in `gate.py:1-2` and exposed via `argparse`
(`gate.py:199-208`). It exits 0 for `checked`/`accepted`, nonzero otherwise
(`gate.py:212`).

The embedded tool (`tools/graphql_contract_gate.rhai`) wraps the same Python
module with base64 encoding for the Leviath tool interface, but the underlying
`gate.dispatch()` function is pure Python with no Leviath dependency
(`gate.py:166-194`).

The entire package can be installed as a Leviath agent (`lev add <path>` per
`README.md:30-31`), but `gate.py` can also be copied into any Python project
that has `graphql-core>=3.2.6`.

**Could Design B use Design A's gate?** Yes, trivially. The bakeoff's
`parse_gate()` (`run_bakeoff3.py:105-115`) does exactly one thing that gate.py
does (SDL validation). A bakeoff that wanted contract-level guarantees could
call `gate.check()` on its output directory and reject convergence unless
status is `"checked"`. The gate is orthogonal to the convergence algorithm.

### Design B — Coupled to Leviath Runtime

The runner (`run_bakeoff3.py`) imports `LeviathClient` from the local leviath
Python binding (`run_bakeoff3.py:15-16`). It cannot run without a working
Leviath daemon.

The diff engine (`graphql_diff.py`) is fully standalone — it requires only
`graphql-core>=3` and has no Leviath dependency. Its public API:
- `diff_types(a_text, b_text) → SchemaDiff`
- `apply_merge(a_text, b_text, decisions, renames) → str`
- `parse_decisions(text) → (decisions, renames)`

These could be used by any Python tool that needs to diff or merge GraphQL
schemas.

The `parse_gate()` function (`run_bakeoff3.py:105-115`) is 11 lines and could
be extracted into `graphql_diff.py` as a utility. It currently isn't.

**Could Design A use Design B's diff/merge?** Yes, usefully. If Design A
produces two competing schema drafts (e.g. from different design stages or
parallel lanes), `graphql_diff.diff_types()` + `apply_merge()` could reconcile
them before feeding them to the gate. The gate would then validate the merged
result against all its policy checks.

---

## 6. Strongest-Intersection Recommendations

### Worth Adopting From A Into B

1. **Content fingerprint + receipt (HIGH).** Gate A's `accepted.json` pattern
   (`gate.py:179-186`) adds 30 lines. It gives the bakeoff a provable answer to
   "was this the artifact the convergence produced?" Write a
   `bakeoff/converged.json` containing `{fingerprint, level, file_hashes,
   diff_summary_hash}` at convergence time. This costs one SHA-256 computation
   per level and enables downstream pipelines to `verify` the artifact hasn't
   been tampered with.

2. **Anti-staleness re-check before declaring convergence (MEDIUM).** Gate A's
   pattern (`gate.py:172`) of re-running the mechanical checks at acceptance
   time and comparing fingerprints would catch a scenario where the merge step
   writes a valid output, a human edits it, and the next level's diff comes out
   empty (self-merge). The bakeoff's current convergence check
   (`run_bakeoff3.py:220` `diff.is_empty`) is satisfied trivially by self-merge
   (`run_bakeoff3.py:240` `cands = [merged_outs[0], merged_outs[0]]`). Adding a
   fingerprint comparison would catch the case where the merged output was
   altered between levels.

3. **Atomic write for the merged output (LOW).** Gate A's `tempfile.mkstemp` +
   `os.replace` (`gate.py:179-182`) prevents a partial write from being read as
   a valid artifact. The bakeoff's `open(out, 'w').write(text)`
   (`run_bakeoff3.py:177-178`) is vulnerable to crash-during-write producing a
   truncated file that happens to parse.

4. **Structured exit code for pipeline use (LOW).** Gate A's CLI exits 0 for
   `checked`/`accepted`, nonzero otherwise (`gate.py:212`). The bakeoff's
   `run_bakeoff3.py` has no structured exit code — it prints `RESULT:` to
   stdout. Adding `sys.exit(0)` on convergence and `sys.exit(1)` otherwise
   would make it usable in shell pipelines.

### Worth Adopting From B Into A

1. **Compact type-graph diff for the review stage (HIGH).** Design A's reviewer
   receives the full raw artifact set. The reviewer must read SDL, operations,
   design.md, contract.json, and requirements.md — all manually. If the
   pipeline produced two design alternatives, `graphql_diff.diff_types()` could
   generate a compact summary of their structural differences. The reviewer
   could use this to focus on the *contentious* types rather than identical
   ones. The diff engine is self-contained (`graphql_diff.py:1-500+`), requires
   only `graphql-core`, and the compression ratio (38-60×) means it fits in a
   context window comfortably.

2. **AST-based merge for reconciliation decisions (MEDIUM).** When the review
   stage produces `verdict: "revise"` and identifies specific type-level
   changes, the `apply_merge()` function (`graphql_diff.py:344-418`) could
   apply those decisions deterministically instead of requiring the repair
   stage to rewrite files by hand. A revise instruction like "keep B's
   ProposalState interface but restore A's implements Node" maps cleanly to
   `MergeDecision(action='reconcile', include_fields=[...])`. This is a
   **bounded repair** that doesn't risk introducing new errors during rewrite.

3. **Re-dispatch with parser error feedback for the draft stage (MEDIUM).**
   Bakeoff B's `run_schema_lane()` re-dispatch loop (`run_bakeoff3.py:119-140`)
   feeds the exact `graphql-core` parse error back to the model when its output
   is invalid. Design A's `draft` stage (`agent.leviath:93-116`) has no such
   feedback loop — if the drafted SDL fails `build_schema()`, the model doesn't
   learn this until the `check` stage, wasting a full pipeline pass. A
   `build_schema()` validation at the end of `draft` with error feedback would
   reduce repair cycles. Note: this requires Leviath to support tool-like
   re-invocation within a stage, which the current hooks model may not permit.

4. **Lexical `#` comment stripping (LOW, defensive).** The bakeoff blueprint
   explicitly tells models "Do NOT write comments ('#') anywhere"
   (`bake-off.agent.leviath:67`). Design A's draft stage has no such
   instruction, and the gate (`gate.py:48`) doesn't reject `#` comments (they
   are valid SDL). But downstream tooling that parses SDL with a regex-based
   extractor can be confused by `#` lines. Adding a gate check that rejects `#`
   comments (or a hook that strips them) would prevent a class of integration
   bugs.

---

## 7. Honest Weaknesses of Design B

This section is an explicit self-assessment, per the task requirements.

### 7.1 Merge is Binary: `keep_a` or `keep_b`

The decision language (`run_bakeoff3.py:48-66`, `graphql_diff.py:parse_decisions():473-518`)
only supports three actions per type: `keep_a`, `keep_b`, or `reconcile`. The
`reconcile` action allows specifying a field list, but the implementation
(`graphql_diff.py:_reconcile_definition_ast():421-495`) is heavily reliant on
heuristic parsing of the reason string (`"adopt B's fields"`, `"restore X from
A"`). It cannot express "take field X from A's version and field Y from B's
version with a modified type." The result is that **the merge often adopts one
side wholesale**: in the v8 run, 19 decisions were made at L1
(`bakeoff_v8.log:14`), and the resulting merged schema was close to the B
candidate (which is explicitly preferred by the prompt, `run_bakeoff3.py:63`).

### 7.2 Comments Are Stripped

The bake-off system prompt instructs: "Do NOT write comments ('#') anywhere.
Emit clean SDL only — comments are noise and are stripped downstream"
(`bake-off.agent.leviath:67`). This is acceptable because the bakeoff's purpose
is a converged schema for implementation, not documentation. But it means the
schema has no inline rationale — the RATIONALE section goes in `submit_output`
text, which is ephemeral (not written to any persistent file in the current
implementation; the bake-merge agent's `submit_output` content is discarded by
the runner).

### 7.3 Convergence Test Is Trivially Satisfied by Self-Merge

At levels > 1, the runner sets both candidates to the same merged output
(`run_bakeoff3.py:240` `cands = [merged_outs[0], merged_outs[0]]`). The
convergence test (`run_bakeoff3.py:218-225`) checks `diff.is_empty`, which is
**always true** when both candidates are the same file. This means convergence
at L2 is guaranteed regardless of whether the L1 merge was actually correct —
as long as the merged artifact parses. In the v8 run, L2 was empty-diff and
converged immediately (`bakeoff_v8.log:20-25`). The only guard is the parse
gate at L2 (`bakeoff_v8.log:24` `PARSE GATE PASS`). This is acceptable for the
bakeoff's purpose (it only needs one valid schema), but it means the algorithm
doesn't actually **verify** that the merge resolved the original divergence in
a semantically meaningful way — it just verifies that the result parses.

### 7.4 No Convergence Guarantee Beyond Parseability

The bakeoff's convergence criterion is exclusively: the diff between candidates
is empty AND the output parses (`run_bakeoff3.py:218-237`). There is no check
that:
- All type references in the schema resolve to defined types (this is covered
  by `build_schema()`).
- Field types haven't been silently narrowed/widened in incompatible ways
  (partially covered by `build_schema()` which catches outright type errors,
  but not semantic narrowing).
- The schema is actually **useful** — i.e., has a Query type, has at least one
  field, doesn't have orphan types with no path from Query. These are all
  outside the bakeoff's scope.

---

## 8. Summary Table

| Dimension | Design A (Contract Gate) | Design B (Bakeoff) |
|-----------|--------------------------|-------------------|
| **Purpose** | End-to-end contract design + governance | Schema convergence from parallel drafts |
| **Output** | SDL + operations + policies + handoff + receipt | One SDL file |
| **Enforcement locus** | Python `gate.py` (194 lines) | Python `run_bakeoff3.py` + `graphql_diff.py` |
| **Failure mode** | Fail-closed: `blocked` status, receipt unlinked | Re-dispatch with error; abort level on exhaustion |
| **Parse validation** | `build_schema()` + `validate_schema()` | `build_schema()` only |
| **Policy checks** | 15+ categories (auth, nullability, batching, pagination, mutations, runtime, etc.) | None |
| **Operations validation** | Full: parse, validate, fragment closure, coercion | None |
| **Evolution checks** | Breaking + dangerous changes via graphql-core | None |
| **Anti-staleness** | ✅ Review fingerprint vs. current content | ❌ |
| **Post-accept verification** | ✅ `verify` mode with parent fingerprint pin | ❌ |
| **Atomic receipt** | ✅ `mkstemp` + `os.replace` | ❌ |
| **Content fingerprint** | ✅ SHA-256 over all files | ❌ |
| **Model calls (observed)** | ~8-14 (8 stages, 2 repair cycles max) | ~33 (v8: 9+19+2+3) |
| **Model tiers** | 2 high-tier + 6 cheap | All cheap (deepseek flash) |
| **Iteration budget** | 140 max (16+20+28+2+24+24+24+2+2) | 60+60 (L0 lanes) + 5×25×2 (merge) = 370 max |
| **Standalone CLI** | ✅ `gate.py check\|accept\|verify` | ❌ (runner requires LeviathClient) |
| **Reusable diff engine** | ❌ | ✅ `graphql_diff.py` |
| **Test coverage** | 22 tests in `test_gate.py` | Self-tests in `graphql_diff.py` if `__name__ == "__main__"` |