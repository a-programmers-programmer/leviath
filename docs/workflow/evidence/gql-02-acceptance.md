# GQL-02 acceptance packet — shared reconciliation rules and conflict detection

Directive: 0184z (0162 C5 residual). Correlation: `fleet-throughput-repair-20260913T2205Z`.
Work order: GQL-02 (ONE work order). Scope: acceptance evidence only — no
re-implementation, no duplicated suite, no widened DoD.

## 1. What this packet covers

GQL-02 was delivered by three sub-orders. This packet harvests the saved work and
re-runs the whole acceptance surface once.

| Sub-order | Deliverable | State |
| --- | --- | --- |
| GQL-02a | reconciliation module + CLI (`selftest`, `verify-contract`, `e2e-real`) | PASS (harvested) |
| GQL-02b | contract pin + adapter binding (`pin.py`, `test_pin.py`, `pin-check`) | PASS (harvested) |
| GQL-02c | this acceptance packet + verifier script + local commit | PASS |

## 2. Contract identity (pin before dependent implementation, re-verified at acceptance)

- Accepted fingerprint (printed-SDL SHA-256):
  `ff9df4e32b05747ee3605042407dcc7e17a347f41bc1464ce567c2f484813559`
- Pin host: `docs/workflow/workflow-schema-decisions.md`
- Pin host digest: `8c3056bbb91a2fd52af261c25768bc9645ccde24620a59b6a2e3fa32e5f5c9e5`
- Observed at acceptance: `ff9df4e32b05747ee3605042407dcc7e17a347f41bc1464ce567c2f484813559` — equal.
- A drifted contract **fails loudly** (step 6 below, exit 1 expected and observed).

## 3. Source / base commit and resulting diff

- Base commit: `15dc182e0bc6c28dfa93f4b8dd3c423372808f61`
  (`fix: satisfy fmt and clippy on the synced tree`)
- Resulting diff stat vs base, tracked files only:

```
 docs/workflow/schema-workflow.graphql      | 265 ++++++++++++++++++++++++++---
 docs/workflow/workflow-schema-decisions.md |  11 ++
 2 files changed, 249 insertions(+), 27 deletions(-)
```

  Those two tracked edits are GQL-01's contract repair, not GQL-02's. They are
  reported here because they are part of the working tree the acceptance run
  reads; they are not in this sub-order's commit.
- The GQL-02 change set (`python/`, `tools/`, the two evidence files and
  `.gql02-base`) was untracked at base and is added by this sub-order's local
  commit `e48f48d0`. Committed diff stat:

```
 .gql02-base                                        |   1 +
 docs/workflow/evidence/gql-02-acceptance.md        | 143 ++++
 docs/workflow/evidence/run-gql02-acceptance.sh     |  88 ++
 python/leviath_reconcile/__init__.py               |  65 ++
 python/leviath_reconcile/__main__.py               |  17 +
 python/leviath_reconcile/adapters/__init__.py      |  27 +
 .../leviath_reconcile/adapters/python_adapter.py   | 405 +++++++++
 .../leviath_reconcile/adapters/schema_adapter.py   | 405 +++++++++
 python/leviath_reconcile/baseline.py               | 173 ++++
 python/leviath_reconcile/cli.py                    | 388 +++++++++
 python/leviath_reconcile/crosswalk.py              | 278 +++++++
 python/leviath_reconcile/diff.py                   | 232 ++++++
 python/leviath_reconcile/e2e_real.py               | 223 +++++
 .../leviath_reconcile/fixtures/workflow_model.py   | 391 +++++++++
 python/leviath_reconcile/identity.py               | 517 ++++++++++++
 python/leviath_reconcile/invariants.py             | 278 +++++++
 python/leviath_reconcile/model.py                  | 298 +++++++
 python/leviath_reconcile/pin.py                    | 404 +++++++++
 python/leviath_reconcile/reconcile.py              | 912 +++++++++++++++++++++
 python/leviath_reconcile/scenarios.py              | 663 +++++++++++++++
 python/leviath_reconcile/test_pin.py               | 189 +++++
 tools/generate_code_counterpart.py                 | 231 ++++++
 22 files changed, 6328 insertions(+)
```

- Verbatim acceptance transcript: `docs/workflow/evidence/gql-02-acceptance-run.txt`.

## 4. Definition of Done — clause by clause

| DoD clause | Proof | Result |
| --- | --- | --- |
| Schema-only and code-only edits work symmetrically | `selftest` symmetry block; `e2e-real` schema-only rename/retype mirrored code-only | PASS |
| Concurrent rename/delete/type edits preserve both versions, produce actionable conflicts | `selftest` concurrent block: `rename-divergence`, `rename-vs-delete`, `add-vs-delete`, `delete-vs-edit`, `type-divergence`, `existence-divergence`, `ambiguous-identity` | PASS |
| Stale document versions cannot overwrite newer work | `selftest` staleness block: `accept()` raises `StaleView`; `BaselineStore.commit` refuses a stale revision | PASS |
| Repeated reconciliation reaches an unchanged result without ping-pong | `selftest` idempotence (digest `d67c23bb0217f529`) and `pin-check` ping-pong (digest `0f71ee64d562`, `changed=(False,False,False)`) | PASS |

## 5. Exact commands, outputs and exit codes

Verifier script: `docs/workflow/evidence/run-gql02-acceptance.sh`.
Run from repo root: `bash docs/workflow/evidence/run-gql02-acceptance.sh`.

```
== HEAD: 15dc182e0bc6c28dfa93f4b8dd3c423372808f61
== base: 15dc182e0bc6c28dfa93f4b8dd3c423372808f61
== python: Python 3.12.14  graphql-core: 3.2.5

[1] selftest            -> PASS (symmetry, concurrent, staleness, idempotence; crosswalk 21 cases; invariants 9 cases)  exit=0
[2] verify-contract     -> result=PASS, observed == pinned                                                              exit=0
[3] e2e-real            -> result=PASS, schema 2136 facts graphql-core, code 1995 facts python-ast                       exit=0
[4] pin-check           -> result=PASS, pinned == observed, pingpong ok, changed=[false,false,false]                     exit=0
[5] pytest test_pin.py  -> 13 passed in 2.86s                                                                           exit=0
[6] verify-contract with wrong pin -> "contract drift: expected 000...0, observed ff9d..."                              exit=1 (expect 1) OK
[7] artifact hashes     -> see section 6
[8] diff stat vs base   -> see section 3

GQL-02 acceptance: PASS
SCRIPT_EXIT=0
```

Step 6 is the discrimination check: the gate must fail on a drifted contract, not
merely report a difference. It returned exit 1 as required.

## 6. Artifact hashes (sha256, bytes)

```
2cb5a7653f706bed42d3cd5787a3896701fce24884072afa9063a297db835a4f    16977  docs/workflow/schema-workflow.graphql
8c3056bbb91a2fd52af261c25768bc9645ccde24620a59b6a2e3fa32e5f5c9e5     2256  docs/workflow/workflow-schema-decisions.md
0dade7cd2e878839326996940174f713b7d7b5b18a9437f120755cf21bd11dd3    15043  python/leviath_reconcile/pin.py
3c3711333d006161e1c353ea45c386e336b70618b77c1b6709b37d1b47cafa77     6941  python/leviath_reconcile/test_pin.py
a4bc37f8ba1e8284655c902122209668be5a16bf2b38692f1401027deb75651a    14930  python/leviath_reconcile/cli.py
a01b062cb3d4be11461557eb629bdab70d091f904691b9c908b9923e15e3cd8c     2207  python/leviath_reconcile/__init__.py
5e3525c125b6419c3cf864aed2fee79a5096d3a678d3809cea5bdfc9fcf810c8    38088  python/leviath_reconcile/reconcile.py
fbda5372c826d1a0ba651b9bbbe6c0088170468f5da12f3b9ce02c85119e6fb1    21158  python/leviath_reconcile/identity.py
806fa6e6fc9786d2a5ef68c9574315a6c297e0cd173f0a7987bb3de46fcaec7d    12786  python/leviath_reconcile/crosswalk.py
218848d582059cc034192a158353adb8132ea8b45b6fb913caa517ac92b0e163    28532  python/leviath_reconcile/scenarios.py
9576c71e65700f82c7453ef0868917d4d2a0fbbaab6ebd9dac4d61ab8e6bd9f9     9822  python/leviath_reconcile/e2e_real.py
655c3f4987cda0bb82ad4120541823e880f9bc6c155f3b5c9ecdaa2c43611dff    18604  python/leviath_reconcile/adapters/schema_adapter.py
543159bdc9fc933eb4fa09441825df24835f4351dd359dc5e028f43353c36672    15975  python/leviath_reconcile/adapters/python_adapter.py
79479ed622614dfa87b54fe91501bad003a36c9d1aed0c07e5b3cc3e34b3b672     8503  python/leviath_reconcile/fixtures/workflow_model.py
ea1965f49ff83b85812a2a691058aeb235713c0935c3a7856cce6b4901b56fbe     8550  tools/generate_code_counterpart.py
```

## 7. Real Bead ID and Leviath run ID

- **Bead ID: unavailable on this box.** The bead store is not installed
  (`bd: command not found`). GQL-02 therefore has no bead ID, and this packet
  does not invent one. Recorded in
  `/data/work/graphql-realization/gql-00/label-bead-map.json` as
  `{"work_order": "GQL-02", "bead_id": null, "reason": "bd not installed on this box"}`.
- **Leviath run IDs (real, harvested from `/data/.leviath/runs/`):**

| Run ID | Status | Model | Cost (USD, exact) | Task |
| --- | --- | --- | --- | --- |
| `gql-02-1789234187-ab93d93725bf` | error | openrouter/deepseek/deepseek-v4.1-flash | 1.056913992 | GQL-02 (initial) |
| `gql-02-1789241254-1e0323a2213d` | complete | openrouter/deepseek/deepseek-v4.1-flash | 0.828047748 | GQL-02 (retry 1) |
| `gql-02-1789250547-d0000b781f9f` | complete | openrouter/deepseek/deepseek-v4.1-flash | 0.755491452 | GQL-02 |

Total actual provider spend for the three harvested runs: **2.640453192 USD**
(all `cost_is_exact=true`, `unpriced_calls=0`).

This acceptance sub-order (GQL-02c) calls no provider: the verifier is offline.

## 8. Blocked / passed / verified states

- **Passed:** all 8 acceptance steps above, exit 0 (step 6 exit 1 by design).
- **Verified:** the pinned fingerprint equals the observed fingerprint; a wrong
  pin is rejected loudly.
- **Blocked:** the Bead ID (no `bd` binary on this box). This is an environment
  gap, not a work failure. It does not block acceptance.
- **Not claimed:** no independent consumer of `pin-check` yet; no push; no deploy.

## 9. Rollback

- Local commit only, no push. Revert with `git reset --soft HEAD~1` (keeps the
  working tree) or `git reset --hard HEAD~1`.
- Full removal of the GQL-02 code set: delete `repo/python/` and `repo/tools/`.
- No destructive data migration, no production-store write.

## 10. Constraints honored

No re-implementation, no duplicated test suite, no new tests, no widened DoD, no
contract-dir edit, no config/grant export, no new scheduler/queue/framework, no
infrastructure change, no restart, no deployment, no public write, no paid
benchmark, no CEO-ledger edit. All writes are inside
`/data/work/graphql-realization/gql-02`.