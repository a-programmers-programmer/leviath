# GQL-01 — Reconcile the workflow model and Composition Guide

Work order: GQL-01. Run: R01M2M6CH130PMSGKSYV8Y29QRX (T01M2KX1Z7W0H3H29S4T6PF5CX8-a1).
Branch: `fleet/gql-01`. Base commit: `15dc182e0bc6c28dfa93f4b8dd3c423372808f61`.
Head commit: `d290522d4273f818fdbb7def602afcb7cc1692fc`.
Repository: `a-programmers-programmer/leviath`, checked out at `./repo`.

## What was done

I resumed the earlier GQL-01 attempt. The reconciliation commit `d290522d`
already exists on branch `fleet/gql-01`. I verified it, repaired the negative
matrix, and captured the evidence.

Deliverables in the repository (all in `d290522d`):

1. `docs/workflow/workflow-reconciliation-v1.md` — the versioned spec
   (`workflow-model v1`), findings F1–F10, rules R1–R6, fingerprint pin.
2. `docs/workflow/schema-workflow.graphql` — the repaired SDL. It parses and
   passes `validate_schema` with 0 errors.
3. `docs/workflow/edge-invariant-rules.json` — the fixed
   rule → (severity, enforcedAt, waivable) table.
4. `docs/workflow/edge_invariants.py` — executable reference for R3/R4, 7 cases.
5. `docs/workflow/check-workflow-contract.py` — the offline contract gate.
6. `crates/leviath-cli/agents/bake-off/references/composition-guide.md` — the
   arbiter, reconciled. New sections §3a, §4a, §7a, §7b.
7. `docs/workflow/workflow-schema-decisions.md` — September 8 preserved,
   GQL-01 appended.
8. `docs/workflow/evidence/` — archived base SDL, base guide, base decisions,
   the evidence script, and the negative-matrix transcript.

## Definition of done, one line each

- **SDL parses and passes validate_schema.** Gate prints `"result": "PASS"`,
  `"parses": true`, `"validate_schema_errors": 0`. F1 and F2 are fixed.
- **No StepContract, binding, stepNumber or prev/next revival.**
  `EdgeKind` is `{ BRANCH GATE JOIN PARALLEL }`; `NEXT` is gone. The gate
  rejects all these names.
- **Severity and enforcement location are separate.** `EdgeInvariant` carries
  `severity: EdgeInvariantSeverity!` and `enforcedAt: [InvariantEnforcement!]!`.
  Severity is fixed by the rule, published in `Query.edgeInvariantRules`.
- **ERROR cycles cannot be waived into a DAG.** The disposition is the union
  `InvariantSatisfied | InvariantWaiver | InvariantRejection`.
  `InvariantRejection` has no waiver, override or force field.
  `edge_invariants.py --self-test` case T3 proves this on a real cycle.
- **Input transport choice is documented.** R2: intent arguments plus one
  generated finite envelope scalar `PortValue`. No `Create*`/`Update*` mirror.
- **Enum/interface outcome rules are documented.** R5 table, plus guide §3a.
  `GateDecision` (choice) and `GateOutcome` (record) are separate; `EXPIRED` is
  only in the outcome enum.

## Steps of the work order

1. **Versioned spec + SDL repaired, September 8 decisions preserved.** Done.
   The spec is `docs/workflow/workflow-reconciliation-v1.md`. Section 3 locks
   the unchanged decisions; only `EdgeKind.NEXT` is removed, which restores the
   September 8 header (F5).
2. **Domain ports vs input objects.** Done. R1 in the spec, §4a in the guide.
   `WorkflowStepInput`/`WorkflowStepOutput` are interfaces and never argument
   types. Transport is R2.
3. **Callable steps, DAG edges, WARN/ERROR preserved.** Done. `WorkflowStep`
   is still the callable. `WorkflowEdge` is still first-class with
   branch/gate/join/parallel. `WARN` stays waivable with a reason; `ERROR`
   stays a hard rejection.

## Evidence

`EVIDENCE.json` lists the commands. The dispatcher re-runs each one. All exit 0.

Command summary:

| Check | Result |
|---|---|
| Gate + fingerprint pin on the repaired SDL | PASS, exit 0 |
| `edge_invariants.py --self-test` | PASS, 7 cases, exit 0 |
| `validate_schema.py` (pre-existing bake-off validator) | VALID, exit 0 |
| `negative_matrix.py repo` | PASS, 7 regressions caught, base rejected, exit 0 |
| Base SDL rejected by the gate | exit 1 (expected) |
| No `.rs` file in the GQL-01 diff | no_rust_rc=0 |
| Guide sections §3a/§4a/§7a/§7b present | all OK |
| DoD semantic assertions on the built schema | ASSERT_OK, exit 0 |

The negative matrix is the important one. It proves the gate is not vacuous:
it fails on the base SDL and on 7 deliberate DoD regressions (`neg4` interface
as argument, `neg5` severity/enforcement conflation, `neg6` `NEXT` revival,
`neg7` `StepContract`/`stepNumber` revival, `neg8` nullable output/error pair,
`neg9` mirror `Create*Input`, `neg10` `EXPIRED` in the choice enum).

Transcript: `evidence/negative-matrix.txt`
(sha256 `0c141bd17f8f0b555f05c5e44eaecab48ffa981111cff7a16b2e8e4e0b2742c0`).
Driver: `negative_matrix.py`
(sha256 `533f261b03b41882b72e8d9f108e5fba33a799a600a0a2235ee55a8015e46e8d`).

The earlier attempt had one wrong anchor string in the negative matrix for
`neg4`: it looked for `'not input types'`, but the gate reports
`Argument type must be a GraphQL input type.` I fixed the anchor to
`'GraphQL input type'`. That is the only repair I made.

## Fingerprint

Accepted contract fingerprint:

```
ff9df4e32b05747ee3605042407dcc7e17a347f41bc1464ce567c2f484813559
```

The gate accepts this value with `--expected-fingerprint` and exits 0.

Artifact hashes (sha256):

```
2cb5a7653f706bed42d3cd5787a3896701fce24884072afa9063a297db835a4f  repo/docs/workflow/schema-workflow.graphql
3e80eb30f8cf255231f821d4924156a67f13dd7a217c756d283acd33238b7d5f  repo/docs/workflow/workflow-reconciliation-v1.md
fbb049086b56ce5b6b20053a2a9f72ba42cdd1f86bc3fbdf2f38614600f479c8  repo/docs/workflow/edge-invariant-rules.json
c0e54ad3396db97f8a44f86080c02f37a38a2ed8328ddced496d4fd72f894a9c  repo/docs/workflow/edge_invariants.py
9296515e947448a6af6f91ec7b9beb6dcb44b6a0b37a37809eeea5fb72e1cd34  repo/docs/workflow/check-workflow-contract.py
4a5f398a2ffe1353cfcafb295f6a5ad8d6c882f3d6b687e36137c711e346506f  repo/crates/leviath-cli/agents/bake-off/references/composition-guide.md
```

## Rollback

The change touches no Rust source, no resolver, no store and no routing
registration. `git revert d290522d` restores the base. Base files are also
archived under `repo/docs/workflow/evidence/`. No data migration is involved.

## States

- **blocked:** none.
- **passed:** the mechanical check `python3 /data/fleet/fleet/verify_wo.py`,
  and every command in `EVIDENCE.json`.
- **verified:** by re-execution of `EVIDENCE.json` at the end of this run.

## Left over (handoff, not done here)

1. The `PortValue` codec generator (R2). The envelope is declared; its codec is
   not generated yet.
2. Resolver implementations for `Query.edgeInvariantRules`, the run outcome
   union, and `respondToGate`.
3. The two LSP servers.
4. Migration of any consumer from a legacy shape.

## Team metadata

- Bead ID: none available in this environment (`bd` is not installed). The run
  id is `R01M2M6CH130PMSGKSYV8Y29QRX`.
- Leviath run id: `T01M2KX1Z7W0H3H29S4T6PF5CX8-a1`.
- Provider spend: $0.00. No provider or network call was made.
- Prior work read: `/data/work/graphql-realization/gql-01/` (repo checkout,
  `neg/` variants, `evidence-transcript.txt`, `.venv-326`). There was no
  `HANDOFF.md` or `REPORT-*.md` in that directory. I copied the still-valid
  negative variants and re-derived the matrix from the repaired SDL.
