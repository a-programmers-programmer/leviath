# GQL-02 — shared reconciliation rules and conflict detection

## Result

Done. The mechanical check passes.

```
$ python3 /data/fleet/fleet/verify_wo.py
{"ok": true, ... "reason": "5 command(s) re-executed with the declared exit codes; 5 artifact(s) present"}
```

## Where the work lives

- Repo: `a-programmers-programmer/leviath`, branch `fleet/gql-02`.
- Base commit: `15dc182e0bc6c28dfa93f4b8dd3c423372808f61`.
- Delivery commit: `5b03d3d215250d13adf5bb8c374e4606d7f5d1c1`
  (`GQL-02c: commit acceptance packet (run transcript, packet, verifier script)`).
- Working directory: `/data/wt/T01M2KX1Z7XC5J5XNJQNXECS64R-a1` (the checkout is a
  copy of the repo tree, so `git` is not usable inside it; the git facts above
  were read from `/data/work/graphql-realization/gql-02/repo`).
- No push. No merge. No production write.

## What was done

I resumed from `HANDOFF.md` (GQL-02a and GQL-02b were already finished). I did
not redo them. I added the missing acceptance layer and fixed one real defect.

1. **Shared structured domain representation** — `python/leviath_reconcile/model.py`.
   `Fact` is a (symbol_key, projection, attribute, value) proposition with a
   `SourceRef` code/schema source map. `symbol_key` is the stable identity.
   `FactSet` and `Projection` carry the digest that is the projection version.
2. **Compare both projections to the last agreed version** —
   `python/leviath_reconcile/reconcile.py` and `baseline.py`. `Baseline` holds
   the last agreed body plus the agreed document identity per projection.
3. **Auto-apply compatible edits, represent competing edits as conflicts** —
   `Reconciler.reconcile` returns a plan with `auto_applied`, `resolutions`,
   `held` and `conflicts`. Each conflict keeps both sides with provenance and
   carries actionable options. Stale documents are blocked and `accept()` raises
   `StaleView`. `BaselineStore.commit` is compare-and-set, so a stale revision
   cannot overwrite newer work.
4. **New evidence command** — `tools/check_gql02_dod.py`. It re-runs the engine
   as a subprocess (it does not import the package) and asserts one DoD clause
   per check, plus two discrimination checks: the contract gate must exit 1 on a
   drifted pin, and the acceptance script must be deterministic across two runs.
5. **Defect fixed** — the recorded acceptance transcript did not match a fresh
   run. The cause was stderr/stdout capture in the acceptance script. The script
   now folds stderr into stdout, and the transcript was re-cut. Two fresh runs
   are now byte-identical and equal the recorded transcript
   (`e28bfa761eb11fdb7a9b78b7e4b85a2ba9756a94249fa146e736956380e18443`).

## Definition of done — clause by clause

| Clause | Proof | State |
| --- | --- | --- |
| Schema-only and code-only edits work symmetrically | `selftest` symmetry block; `e2e-real` real schema-only rename and retype mirrored code-only | PASS |
| Concurrent rename/delete/type edits preserve both versions and produce actionable conflicts | `selftest` concurrent block: rename-divergence, rename-vs-delete, add-vs-delete, delete-vs-edit, type-divergence, existence-divergence, ambiguous-identity; disjoint edits apply on both sides | PASS |
| Stale document versions cannot overwrite newer work | `selftest` staleness block: `accept()` raises `StaleView`; `BaselineStore.commit` refuses a stale revision; refused commit left state intact | PASS |
| Repeated reconciliation reaches an unchanged result without ping-pong | `selftest` idempotence digest `d67c23bb0217f529`, 0 changed rounds; `pin-check` digest `0f71ee64d562`, `changed=[false,false,false]` | PASS |

## Real artifacts

- Contract pin: `ff9df4e32b05747ee3605042407dcc7e17a347f41bc1464ce567c2f484813559`.
  Pin host: `docs/workflow/workflow-schema-decisions.md`. Pinned before the
  dependent code, re-verified at acceptance. A wrong pin exits 1.
- Real artifact readings: SDL 2136 facts (`graphql-core`), typed Python 1995
  facts (`python-ast`).
- Leviath run IDs (harvested from `/data/.leviath/runs/`, real):
  `gql-02-1789234187-ab93d93725bf` (error, 1.056913992 USD),
  `gql-02-1789241254-1e0323a2213d` (complete, 0.828047748 USD),
  `gql-02-1789250547-d0000b781f9f` (complete, 0.755491452 USD).
  Total actual provider spend: 2.640453192 USD. This run calls no provider.
- Bead ID: none. `bd` is not installed on this box, so no bead exists. I did not
  invent one.

## Blocked / passed / verified

- Passed: all four DoD clauses, 13 pinned unit tests, the acceptance script, and
  the DoD checker.
- Verified: the pinned fingerprint equals the observed fingerprint; a wrong pin
  fails loudly; the acceptance run is deterministic.
- Blocked: the bead store. This is an environment gap, not a work failure.

## What is left

- No push and no PR from this box. The delivery commit is local on `fleet/gql-02`.
- `pin-check` still has no independent consumer.

## Rollback

Revert the local commit on `fleet/gql-02`, or delete `python/leviath_reconcile/`
and `tools/`. No destructive data migration. No production store write.