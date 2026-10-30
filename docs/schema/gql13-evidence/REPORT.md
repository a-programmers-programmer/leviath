# GQL-13 lane report — Desk, queue, run and decision contract

Lane `L02-gql13`. Branch `lane/L02-gql13`.
Worktree `/data/work/graphql-realization/gql-00/wt/L02-gql13`.

## 1. Status, stated plainly

| Surface | State |
| --- | --- |
| Contract artifacts at `docs/schema/gql13/` | WRITTEN |
| Gate `check` over `docs/schema/gql13` | PASSED (`status: "checked"`, 0 errors) |
| Gate `accept` / `verify` (needs an independent reviewer receipt) | BLOCKED — no reviewer exists in this lane |
| Rust module `crates/leviath-core/src/schema/mod.rs` | WRITTEN, compiles, 15/15 unit tests pass |
| Rust module wired into `lib.rs` | NOT DONE — `lib.rs` is outside my declared set |
| Runtime / caller compatibility against a live service | UNVERIFIED — no live service was contacted |

A terminal status alone is not acceptance, so the rest of this report is the
evidence behind those five lines.

## 2. Base identity (observed, not assumed)

```
$ git rev-parse HEAD
7b6e050e89e6aea7b51453e633fbe9209c50a844
$ git rev-parse --abbrev-ref HEAD
lane/L02-gql13
```

`7b6e050e` is the base commit and it is UNCHANGED. The resulting diff to
tracked files is EMPTY: every artifact is a new untracked path inside my
declared set. `git status --porcelain` reads:

```
?? crates/leviath-core/src/schema/
?? docs/schema/gql13-evidence/
?? docs/schema/gql13/
```

No tracked file was modified, so there is no tracked diff to show. The
`find | sha256sum` block in section 6 is the artifact identity instead.

## 3. Harvest first — what I adopted instead of authoring

Read before writing:

* `gql-13/repo` observed at `3af3cf36aefe7f8c6b5599b133fe578d9a3bda47`.
* `queue_store.py` (1253 lines) is QUEUE-STATE AUTHORITY. States, the
  `NAMED_KIND_ONLY` guard, `ARCHIVABLE`, and the by-source helpers were copied
  into `mod.rs` as constants rather than re-invented.
* `desk_pub.py` (3865 lines) is the Desk. `PRODUCTS`, `KINDS`, `OWNERS`,
  `DELIVERIES`, `DECIDE_KIND` and the `_slip_record` check ORDER were adopted.
* `desk_threads.py`, `desk_stream.py`, `kanban_pub.py` for thread kinds, the
  stream's change-digest shape, and the queue-to-board projection.
* The gate at `crates/leviath-cli/agents/graphql-schema/scripts/gate.py`,
  VERSION 1, graphql-core 3.2.5. NOT re-authored. I ran it as-is.
* The example at `crates/leviath-cli/agents/graphql-schema/examples/task-board`
  was used as the shape template only.

Raw harvest copies sit beside this report as
`harvest-slip-record.txt` and `harvest-run-state.txt`.

## 4. RED, then GREEN — the TDD gate

RED was captured before any artifact existed, and it is reproduced here from
the verification stage verbatim:

```
File ".../scripts/gate.py", line 70, in check
  contract = obj("contract.json")
File ".../scripts/gate.py", line 67, in obj
  require(name in files, f"Missing {name}")
ValueError: Missing contract.json
EXIT=1
```

The test is `docs/schema/gql13-evidence/test_contract_check.py`. It imports the
EXISTING gate and asserts `status == "checked"` plus "no `baseline.graphql` for
mode=new". It was not weakened and not deleted.

One honest intermediate RED appeared after the artifacts existed, and it is the
reason one edit was made. The first `variables.json` marked a `ClaimQueueItem`
case `invalid` by omitting `rowId`/`holder`, but both were nullable, so coercion
succeeded and the gate refused the case — the gate was right and my case was
wrong:

```
{"fingerprint": "ee0e2f02e62c397bb45f5ceda53d93382726a6b9e2cbb7eb9f45307520615e85",
 "status": "blocked", "version": 1}
  ERROR: Unexpected variable coercion: ClaimQueueItem
FAIL: gate status is blocked
EXIT=1
```

I fixed the CASE, not the gate: the invalid case now passes `leaseSec` a string.
GREEN:

```
$ python3 docs/schema/gql13-evidence/test_contract_check.py
{"fingerprint": "24d8c7340aa08692df0a76e48dc6e2b52308ee49c9e3b9283f4b90a0464bd6cb",
 "status": "checked", "version": 1}
OK: checked, errors=0, fingerprint=24d8c7340aa08692df0a76e48dc6e2b52308ee49c9e3b9283f4b90a0464bd6cb
EXIT=0
```

## 5. Rust module — compiled and tested standalone

`lib.rs` is outside my declared set, so I could not add `pub mod schema;`. I did
not stop at that: I made the module standard-library-only, which lets me PROVE
it compiles and passes its tests with plain `rustc`, no `lib.rs` edit and no
`Cargo.toml` edit.

```
$ rustc --edition 2024 --test crates/leviath-core/src/schema/mod.rs -o /tmp/gql13_schema_test
BUILD_EXIT=0
$ /tmp/gql13_schema_test
running 15 tests
... ok ... (15 passed; 0 failed)
TEST_EXIT=0
```

The 15 tests cover the invariants the contract states, including `report != done`,
"a typoed delivery never folds to auto", "a blind sweep cannot take a
named-kind-only row", "a decide slip is never autoqueued", and "archive is
hygiene and needs a finished state". The scratch binary was deleted; nothing was
left under `/tmp`.

## 6. Artifact identity

```
a0e93ad596b8e05ae71e201258c42a46c53417e4590a8aedf0837e6676486b6b  crates/leviath-core/src/schema/mod.rs
457a55c773977a4f312e613ad25345a26aa909541031bad33ca4e99c7ead2880  docs/schema/gql13-evidence/build_contract.py
9ac96c5d62c8be30107ceaa37b116f4948419eb67f5114552498d3d2ef05e8bb  docs/schema/gql13-evidence/harvest-run-state.txt
57781c12083451984ca6dbf6d73c37a07369a0c9914308f0cc0378ec90f42828  docs/schema/gql13-evidence/harvest-slip-record.txt
1b4545346f0fc07c6ccf0e87fbd50f8185f29c8bf7cfef47b00e3c31bb5484f6  docs/schema/gql13-evidence/test_contract_check.py
1c8cb1e9eb3976b0ca0872a72e44ef4e28b2482e98cd219b5ec1154b1e419ce8  docs/schema/gql13/contract.json
ad32d82e3acbc79499a4ac13d04078ce444ad80c146955bd4c2ec0a314b50ff7  docs/schema/gql13/design.md
616fc411c8284e1f19b7672d60b612a7c4a9fcf71a92d5eb78a6c4091de66c7c  docs/schema/gql13/handoff.md
fec9cfc979e4fcba1d50d51238b9f65ea31c36b6acba4d3fa8fbd011778e0f87  docs/schema/gql13/operations/desk.graphql
954a9e832ec8ffd11c910cebe1d306c79c2948c7023633aa73d7b7eb3acd2e6a  docs/schema/gql13/operations/queue.graphql
f53a0cc482e3c91fec5db9a1bf3022aa0dc05abffe90de2e9201dbb07f56ba3a  docs/schema/gql13/operations/status.graphql
7de74a9918d4db1fd92d3f6f4c80f87792fbae4269aad6a1603a95cad4556be7  docs/schema/gql13/requirements.md
30382ae54f8cc28e691fd409b6813b8231bcc9edc2a61473caa0fccbc71885de  docs/schema/gql13/schema.graphql
bfa0ffe27ce8e18f9aa60f588809e1c0aea68ec4d086f0dbadd15bc05bb01158  docs/schema/gql13/variables.json
```

Contract fingerprint (the gate's own identity of the artifact set):
`24d8c7340aa08692df0a76e48dc6e2b52308ee49c9e3b9283f4b90a0464bd6cb`.

`build_contract.py` is a generator, not a deliverable. It exists so
`contract.json` (102 field policies, 9 collections, 6 mutations, 10
requirements) is reproducible rather than hand-maintained. The gate reads only
the generated files; the generator is on the path so a reviewer can re-derive
them and compare hashes.

## 7. Done criteria — one line each

The work order names nine operations. Each is an authored client document and
each is exercised by the gate (operation count read from the gate's own output,
not from my grep):

| Work-order item | Operation | Evidence |
| --- | --- | --- |
| Desk filing | `FileSlip` | `operations/desk.graphql`; gate selects it |
| claim | `ClaimQueueItem` | `operations/queue.graphql` |
| renew | `RenewClaim` | `operations/queue.graphql` |
| complete | `CompleteQueueItem` | `operations/queue.graphql` |
| release | `ReleaseQueueItem` | `operations/queue.graphql` |
| decisions | `RecordDecision` | `operations/status.graphql` |
| status | `GetSlipStatus` | `operations/status.graphql` |
| thread | `GetThread` | `operations/status.graphql` |
| stream | `WatchSlipStatus` | `operations/status.graphql` |

`queue_store` remains queue-state authority: the claim/renew/complete/release
commands project that store and go through its rules, they do not re-implement
it. Planning dependencies are NOT modelled here — the planning tool already
owns that edge. Baseline/caller compatibility and field policies are carried as
`mode: "new"` plus per-field authorization/nullability/source/batching policy
for all 102 output fields.

## 8. What I ACCEPTED

* The contract at `docs/schema/gql13/`, `mode: "new"`, fingerprinted as above.
  The gate reports `status: "checked"` with `errors: []`.
* The observed vocabulary as the field policy base: `queue_store` stays
  queue-state authority, `desk_pub` stays the filing authority for product,
  kind, owner and delivery, `desk_threads` stays the thread authority.
* `report != done` as a stated invariant, not a comment.
* The named-kind refusal as a HOLDER refusal, not only a kind filter.
* `delivery` as a validated member with no silent fallback.
* The Rust invariants module, proven by compilation and 15 passing tests.

## 9. What I REJECTED

* Did NOT write `baseline.graphql`. The repo SDLs (`memory/schema.graphql`,
  `kit/example-schema.graphql`) are DIFFERENT APIs from the Desk and queue HTTP
  service. Claiming `evolve` would assert a caller-compatibility lineage I did
  not observe. The test asserts the absence.
* Did NOT fork or re-author the gate.
* Did NOT model planning dependencies. The planning tool already owns them;
  duplicating them would create a second authority for the same edge.
* Did NOT collapse Slip, queue row and thread entry into one type. They are
  three state machines and one identity would let a client read a run outcome
  off a filing state.
* Did NOT use a model anywhere on this path. Zero provider calls, zero spend.
* Did NOT weaken the test to reach GREEN. The single edit was to a wrong
  VARIABLE CASE, which the gate correctly refused.

## 10. What I could NOT determine — real limits

1. **The Rust module is not wired.** `lib.rs` has no `pub mod schema;` and
   `lib.rs` is not in my declared set. This is a DECOMPOSITION REQUEST, not a
   defect I can fix: I need a one-line grant to write
   `crates/leviath-core/src/lib.rs`, or somebody else must add that line. Until
   then the module is unreachable from the crate even though it compiles.
2. **`accept` and `verify` are BLOCKED.** Both require a review receipt that
   approves the exact fingerprint, and `review_ok` requires six rubric items
   with evidence. No independent reviewer exists in this lane, and a
   self-written review would be a false claim. So the furthest honest state is
   `checked`, not `accepted`. I did not fabricate a receipt.
3. **No runtime verification.** Nothing was executed against a live Desk or
   queue. Every behavioral claim in `handoff.md` is an ORDER to verify, not a
   result. Caller compatibility is asserted by vocabulary match with the
   observed services, which is weaker than a live test.
4. **No GQL-13 spec was found fleet-wide.** I authored the contract from the
   observed services. If an authoritative GQL-13 spec exists elsewhere, my
   requirements list may not match it.
5. **`/data/work/graphql-realization/gql-12/repo` does not exist**, so GQL-12
   could not be harvested for a prior contract shape.
6. **A prior claimed fingerprint `ff9df4e3...f484813559` appears in no
   artifact I could find.** I cannot verify it and did not reuse it.

## 11. Out-of-scope compliance

* Writes are confined to `docs/schema` and `crates/leviath-core/src/schema`.
  A `git status --porcelain` filter for anything outside those prefixes returns
  empty.
* The TAKEN subtree `/data/work/graphql-realization/gql-05/repo/integrations/typed-ls-ts`
  was never written, built or tested. No other lane's worktree was touched.
* No write to `Cargo.toml`, `Cargo.lock`, `/data/.leviath/config.toml`,
  `ceo_bus_metrics.py`, any sweeper, `/data/work/ceo-bus`, `/data/bin`,
  `/opt/hermes`, or `/data/state`.
* No admission file, outbox write, bus write, scheduler, queue, service or
  dashboard was created.
* No credential or permission change, no secret read or write.
* No Railway mutation, restart or redeploy.
* No commit, push or deploy. HEAD is still `7b6e050e`.
* No `--wait`, no `--yolo`, no spawned coordinator.
* Model gate honored: the only permitted path is
  `openrouter/deepseek/deepseek-v4.1-flash`, and this lane made ZERO inference
  calls, so no other namespace was used in any role. Actual provider spend: 0.
* The retired 0104 numeric ceiling is not cited, not sized against, and no
  limit is reported from it. No provider throttling was observed, so there is
  no raw log line to report and no count to report.

## 12. Disposition

The contract gate is GREEN at `checked`. `accept` is blocked only by the absence
of an independent reviewer. The one open request is a one-line grant to write
`crates/leviath-core/src/lib.rs` to wire `pub mod schema;`.