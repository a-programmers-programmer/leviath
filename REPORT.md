# GQL-09 — Make HarnessQL portable and its merge decisions exact

**Work order:** GQL-09
**Repository:** a-programmers-programmer/leviath
**Worktree / workdir:** `/data/wt/T01M2KX1Z7YRPM4SQ8HCPP8FJH9-a1`
**Branch:** `fleet/gql-09`
**Base commit:** `15dc182e0bc6c28dfa93f4b8dd3c423372808f61`
**Landed commit:** `cb261862c3a8adf0b1fafdb2d2ac21d7ceeb4578` (22 files staged and committed on `fleet/gql-09`)
**State:** code-complete, committed on the branch, verified by an offline suite that exits 0.
One item stays **blocked** and is reported honestly: no Bead ID exists for this work order.

I harvested `/data/work/graphql-realization/gql-09` first (HANDOFF.md, REPORT-GQL-09.md,
evidence/*). The completed steps were not redone: I copied the still-valid evidence into
`evidence/` and re-verified it against the sources in this worktree.

---

## 1. Definition of done, line by line

| DoD line | State | What proves it |
|---|---|---|
| A fresh install works outside `/data/work/leviath` | **verified** | `test_portability.py` copies both agent trees to a temp dir and *runs* the copied driver. Checks A1–A2c, B1–B8, C1–C4g pass. |
| No raw candidate read or full-schema write tool for the merge reviewer | **verified** | `bake-merge/agent.leviath` has `[read_paths] allow = []` and `available_tools = ["merge_decision_format", "submit_output"]`. Checks D1–D8 and E1–E3 pass. `lev validate` exits 0. |
| Same candidates + decisions produce identical output | **verified** | C4c: two runs in two trees give one `result_sha256`. The driver re-applies the decisions in-process and compares bytes (`reapply: IDENTICAL`), and exits 1 on divergence. |
| No self-merge is called independent convergence | **verified** | `convergence_note()` plus checks T1–T5. A shared run id prints `self-merge (…); NOT independent convergence`. Missing provenance prints `independent: false`. |
| Rationale, candidate hashes, diff, decisions and result are preserved | **verified** | Every merge writes `bundle.json` naming and hashing all five (C4e). Candidate paths are recorded too (C4f). |

---

## 2. What the change is

1. **The guide is a packaged tool, not a scratch read.** `generate_tools.py` renders
   `bake-off/tools/composition_guide.rhai` and `bake-merge/tools/merge_decision_format.rhai`
   from the in-repo references. `--check` exits 0, so a stale tool is a test failure.
2. **The merge reviewer has no file tools.** `bake-merge/agent.leviath` v0.2.0 grants
   `allow = []` read paths and only `merge_decision_format` + `submit_output`. The limit is
   in the blueprint, not in prose.
3. **Decisions are structured.** `merge_decisions.py` validates a
   `harnessql-merge-decisions/v1` JSON document per type and per field. Omitted, duplicate,
   unknown, conflicting and stale decisions are refusals with a code. Reasons are stored and
   hashed; they are never parsed for intent.
4. **Level N>0 makes two independent candidates.** `run_bakeoff3.run_merge_level` dispatches
   two merge lanes, each with its own `lev run` id and its own decision document. A pair is
   called independent only when the recorded run ids differ.
5. **Portability.** `harnessql_paths.py` is the one resolver. Every path is agent-relative and
   `HARNESSQL_*`-overridable. The old scratch study is opt-in only via
   `HARNESSQL_LEGACY_STUDY`; it resolves to `None` when unset.

---

## 3. Commands and results (re-executed by the dispatcher from `EVIDENCE.json`)

```
cwd = repo/crates/leviath-cli/agents/bake-off/scripts
  python3 test_merge_decisions.py   -> === ALL TESTS PASSED ===                      rc 0
  python3 test_portability.py       -> === ALL PORTABILITY CHECKS PASSED ===          rc 0
  python3 test_two_lane_merge.py    -> === ALL TWO-LANE CHECKS PASSED ===             rc 0
  python3 test_lane_dispatch.py     -> === ALL LANE-DISPATCH CHECKS PASSED ===        rc 0
  python3 generate_tools.py --check -> OK / OK (both tools current)                   rc 0
  python3 -m py_compile <sources>   -> no failures                                    rc 0
cwd = repo
  grep -q 'allow = \[\]' bake-merge/agent.leviath                                     rc 0
  lev validate bake-merge / bake-off -> valid, no broad-read-path warning             rc 0
```

---

## 4. Evidence bundle

| Artifact | What it is |
|---|---|
| `evidence/commands.log` | every verification command with its output and exit code |
| `evidence/lane-dispatch.json` | the two real lanes: run ids, statuses, decision and result hashes |
| `evidence/run-identity.json` | `bead_id: null`, `leviath_run_id: bake-merge-1789376732-976109904b31` |
| `evidence/states.json` | per-check state: passed / verified / blocked |
| `evidence/artifacts.json` | the 22 owned paths with bytes and sha256 |
| `evidence/base-commit.txt` | base `15dc182e`, HEAD subject, dirty paths, `committed` flag |
| `evidence/live/` | real lane output: candidates, diff, both decision documents, both results |
| `evidence/INDEX.json` | sha256 of the evidence files (a file cannot hash itself) |

Real, measured numbers from the live run:

| item | value |
|---|---|
| lane 0 run id | `bake-merge-1789376732-976109904b31` (Complete) |
| lane 1 run id | `bake-merge-1789376732-661590885a9e` (Complete) |
| lane 0 decision sha256 | `31fffd6be956a6d9c60f8f025cc07ae19057f000aebd63ebc9dad7ae43b64363` |
| lane 1 decision sha256 | `98d18a7145e254cd02cde4d7be621c0d2812a011aa911286d844d386f5527b5a` |
| result sha256, both lanes | `ba4985f621e27dba0c98b4465729e3bb6c83297790b47f1fda8c7189b75edfc9` |

Two **different** decision documents produced the **same** result bytes. That is the whole
point: output is a function of (candidates, decisions) alone.

---

## 5. Blocked / not done, honestly

1. **No Bead ID.** No Bead exists for GQL-09. `run_identity.json` reports `null` rather than
   invent a value. The real Leviath run ids above are reported instead.
2. **Provider spend is recorded in `evidence/spend.json`, measured from each run's own
   `meta.json` (`cost_is_exact: true`).** Four real restricted bake-merge lanes ran
   (`$0.01369209`) and, separately, three labelled exploratory probes (`$0.0034839`). The
   earlier `$0.00` claim was true only of the offline phase and is stale.
3. **The stale installed blueprint still exists on disk.** A bare `lev run bake-merge`
   resolves to the older permissive installed copy. The drivers now dispatch the in-repo path,
   so this work order's dispatch is correct. Retiring the installed copy is separate cleanup.
4. **No PR was opened.** The policy forbids outbound communication and pushing is not
   authorised. The commit `cb261862` on `fleet/gql-09` is the product artifact so far.

---

## 6. Provider spend

`$0.01369209` for the four restricted merge lanes, measured from each run's `meta.json`
(`cost_is_exact: true`). A separately labelled set of three exploratory probes cost
`$0.0034839` and is not counted as original-criteria evidence. Total recorded
`$0.01717599`. This amends the earlier `$0.00` line, which described the offline phase only.

## 7. Rollback

Nothing destructive ran. No migration ran. Revert with
`git revert cb261862` on `fleet/gql-09`, or check out base `15dc182e`.
`HARNESSQL_LEGACY_STUDY` keeps the old scratch study reachable when set.
