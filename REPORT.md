# REPORT — GQL-07: runnable workflows, callable steps and edge checks

## Verdict

**COMPLETE.** `python3 /data/fleet/fleet/verify_wo.py` exits **0** in this working
directory. All four definition-of-done lines hold and are re-executable.

## Where the work is

| | |
|---|---|
| Repo | a-programmers-programmer/leviath |
| Working directory | `/data/wt/T01M2KX1Z7Y7Q7SBQ2Z7BM4S7N4-a1` |
| Branch | `fleet/gql-07` (never master/main) |
| Base commit | `5079d866ad6e582794f814666a3155f78bf04ad7` (root commit, GQL-07) |
| Prior commit | `154570c90f7f4b5f8a1a52609a36a1527b3bca5f` (rule contract + evidence) |
| Head commit | `9f42b39` (code change commit: `fda8c1a`) |
| Contract fingerprint | `ff9df4e32b05747ee3605042407dcc7e17a347f41bc1464ce567c2f484813559` (pinned, from GQL-01) |
| Provider spend | $0 (no model calls were made) |

A PR is the product artifact. This run pushed nothing and merged nothing.

## What I resumed (PRIOR WORK)

I read `/data/work/graphql-realization/gql-07/` first. It held a working prior
attempt: `leviath_workflow/` (6 files) and 6 passing tests. I did **not** redo it.
I copied the still-valid evidence into `evidence/` as `prior_*` files and kept
the prior implementation.

Two DoD gaps remained in that prior work, and I closed them:

1. **"An individual step runs through the same checks"** was only half met. The
   prior `runner.py` had `run()` for the whole DAG only; no single-step entry
   point existed. DoD line 2 had no code behind it.
2. **"unapproved sensitive flows block"** was not met. The prior
   `check_sensitive_flows` returned `ok=True` unconditionally and only emitted a
   text warning. A sensitive step could run without approval. That is the
   opposite of the DoD line.

## What I changed

**`leviath_workflow/runner.py`** — added `run_single_step(step_name, upstream_outputs)`.
It calls the same `_preflight()` (that is, `check_dag`) that `run()` calls,
returns early on any ERROR, and runs only the named step. Both modes now share
one check pipeline and one execution path (`_run_step`, `_record_waivers`).
Waivers moved from a bare `waiver_identity` string to real `Waiver` records
(step, identity, reason).

**`leviath_workflow/checks.py`** — rewrote `check_sensitive_flows` so an
**unapproved** sensitive flow is an `ERROR` that blocks, and an approved one is a
`WARN`. Added `check_reachability` (every step reachable from an entry step) and
`check_branches_joins` (a join may not bind the same source port twice). Added
`approved_steps` to `check_dag`.

**`leviath_workflow/model.py`** — added the `Waiver` dataclass. `Waiver.approved`
is true only when it names a principal **and** a reason. An empty waiver is not
an approval and does not unblock anything.

**`leviath_workflow/evidence.py`** — `RunEvidence` gained `blocked`, `mode` and
`step`. A blocked run records `blocked=True` and no results.

**Determinism fix (found by my own check).** `check_cycles` iterated a Python
`set` of step names, and `_topo_sort` drained a `set` into its Kahn queue. Set
order changes with hash randomization, so the **same** DAG reported
`"cycle detected: A -> C -> A"` on one run and `"C -> A -> C"` on the next, and
independent steps ran in different orders. Both now visit in declaration order.
`tests/test_determinism.py` proves it: five `PYTHONHASHSEED` values, one hash.

**`tests/test_workflow_dod.py`** (new, 11 tests) and
**`tests/test_determinism.py`** (new, 5 tests). `docs/workflow/edge-invariant-rules.json`
records the fixed rule → (severity, enforcement location, waivable) contract.

## Definition of done, line by line

| DoD line | Where it holds | Proof |
|---|---|---|
| Branch/join DAG runs with typed outputs | `runner.run()` topo-sorts, passes JSON through typed ports; `start→left/right→join` yields `HELLO\|olleh` | `evidence/run_branch_join.json`; `dod1.*` 7 PASS |
| An individual step runs through the same checks | `run_single_step()` calls the same `_preflight()`; blocks on missing endpoint, cycle, port mismatch, unapproved sensitive flow | `evidence/step_mode.json`; `dod2.*` 7 PASS; 4 step-mode block tests |
| Cycles, missing endpoints, incompatible ports and unapproved sensitive flows block | each returns `ok=False, blocked=True` and runs **no** step | each CLI exits **1**; `dod3.*` 12 PASS |
| WARN waiver identity and reason survive in run evidence | `WaiverRecord(step, identity, reason, severity)` survives `as_dict`/`to_json`; an empty waiver does not approve | `dod4.*` 9 PASS, incl. JSON round trip |

## Commands and results (all re-executed by the dispatcher)

| Command | rc | Result |
|---|---|---|
| `python3 evidence/verify_dod.py` | 0 | 35 PASS, 0 FAIL, `{"verify": "PASS", "dod_lines": 4}` |
| `python3 -m pytest tests/ -q` | 0 | 20 passed |
| `python3 -m leviath_workflow run --dag tests/fixtures/branch_join.json` | 0 | `ok=true`, `join.combined == "HELLO\|olleh"` |
| `python3 -m leviath_workflow step --dag .../branch_join.json --step left` | 0 | `mode=step`, only `left` ran, output `HELLO` |
| `python3 -m leviath_workflow run --dag tests/fixtures/cycle.json` | **1** | `blocked=true`, names the cycle, `results={}` |
| `... --dag tests/fixtures/missing_endpoint.json` | **1** | `blocked=true`, names `ghost.x` |
| `... --dag tests/fixtures/sensitive_unapproved.json` | **1** | `blocked=true`, `results={}` |
| `... --waive-step push_prod --waiver-identity release-manager --waiver-reason 'approved by CAB-42'` | 0 | `waivers[0] = {release-manager, approved by CAB-42, WARN}` |
| 5× `PYTHONHASHSEED` cycle run, hashed | 0 | all five sha256 = `9618a2d7…f0216` |

## Blocked / passed / verified

- **Verified:** all four DoD lines, by the CLI and by tests re-run under the dispatcher.
- **Passed:** 20 tests, 35 DoD assertions.
- **Blocked:** the four bad-graph classes each block and run no step. This is the required behaviour, not a failure to deliver.
- **Not done, on purpose:** no merge, no force-push, no deployment, no data migration, no outbound communication. The branch is the artifact.

## Before cutover

Revert this isolated change. The prior source and store stay intact and no
destructive migration was run. No compatibility route was removed.

## Honest limits

- `bd` (bead tool) is not installed in this container, so I could not claim or
  close a bead. No Bead ID is reported, because I have no real one to report.
  I did not invent one.
- No Leviath run ID is reported, for the same reason: this run was not driven
  through a Leviath run I can name. I did not invent one.
- The step runner executes commands via `subprocess` with a 30 s timeout. It
  does not sandbox them. A production cutover must add process isolation.
- The graph is supplied as JSON. Wiring to the GQL-02 reconciler is out of scope
  for this work order.
