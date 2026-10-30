# Workflow Model ↔ Composition Guide — Reconciliation Specification

- **Work order:** GQL-01 — *Reconcile the workflow model and Composition Guide*
- **Contract:** `workflow-model v1` (first versioned contract for these files)
- **Repository:** `a-programmers-programmer/leviath`
- **Branch / base commit:** `sync/upstream-0.6.0` @ `15dc182e0bc6c28dfa93f4b8dd3c423372808f61`
- **Authored:** 2026-09-12
- **Owner role:** contract-design
- **Depends on:** GQL-00

This document is the **normative** reconciliation. Where it and an older note
disagree, this wins. The September 8 2026 decisions are **preserved unchanged**;
this spec only fixes the model's defects and removes the places where the
Composition Guide and the model said different things.

## 0. Why this work exists

`docs/workflow/schema-workflow.graphql` was authored on 2026-09-08 and locked in
`docs/workflow/workflow-schema-decisions.md`, but it was **never mechanically
checked**. It does not parse and it cannot build. Separately, the guide that the
bake-off lanes treat as the arbiter
(`crates/leviath-cli/agents/bake-off/references/composition-guide.md`) contains
rules that the workflow model itself violates, so the arbiter could not arbitrate
its own subject. GQL-01 fixes both and pins the result.

## 1. Findings against the base commit

All findings are reproduced by `check-workflow-contract.py` (see §6). Line
numbers are from the base revision.

| # | Finding | Evidence | Class |
|---|---------|----------|-------|
| F1 | **SDL does not parse.** `@user_confirmation` has an argument description string placed *after* its last argument (`by: String!`), which is not valid GraphQL grammar. | `parse()` → `Syntax Error: Expected Name, found ')'` at 36:87 | blocking |
| F2 | **SDL cannot build.** `Mutation.dispatchWorkflow` and `Mutation.runWorkflowStep` pass `WorkflowStepInput!` as an argument type. `WorkflowStepInput` is an **interface**, and GraphQL forbids an interface from being an input type. | `build_schema()` → `TypeError: Mutation fields cannot be resolved. Argument type must be a GraphQL input type.` | blocking |
| F3 | **Severity is conflated with enforcement location.** `EdgeInvariantSeverity` says how bad a violation is; nothing says *where* it is checked. Where-to-check was left to prose. | `EdgeInvariant` fields in base SDL | DoD |
| F4 | **An ERROR cycle is waivable into a DAG.** `EdgeInvariant.reason: String` is nullable and undocumented; its implicit contract is "present means waived". Because `ACYCLIC` is one rule in the same `EdgeInvariantRule` enum as the WARN rule, a cycle could be recorded with a reason and treated as accepted — which is precisely what the DoD forbids. | `EdgeInvariant.reason` in base SDL | DoD |
| F5 | **`EdgeKind.NEXT` is a prev/next revival.** The file's own header says the September 8 decision removed `prev/next` and that edges are branch/gate/join/parallel, yet the enum shipped `NEXT` as the linear successor kind. | header vs `enum EdgeKind` | DoD |
| F6 | **Implicit-state run contract.** `WorkflowRun`/`WorkflowStepRun` carry nullable `output` + nullable `error` and a nullable state machine, so "succeeded", "failed", "stopped", "awaiting gate" are all encoded by which nulls are present. The guide's own §5 rejects this shape. | run types in base SDL | guide violation |
| F7 | **Input transport was undefined, not chosen.** With F2, the model had no working answer for how a port value travels into a mutation. The guide's §4 warns that inputs must not mirror outputs, but the model declared no transport at all. | absence | DoD |
| F8 | **Enum-vs-interface outcome rule not applied.** `GateResult.passed: Boolean!` flattens a three-valued question; the `Output` suffix is used both for GraphQL interfaces and (implicitly) for argument positions, so the enum/interface/union rule is stated in the guide but not obeyed by the model. | `GateResult`, mutation args | DoD |
| F9 | **Sensitive value carried as a plain string behind a directive.** `token: String` + `@user_confirmation` puts the faithful path and the secret in the same type, and the architecture text already says authority is *not* a directive alone. | `OAuthRefreshTokenInput.token` | architecture |
| F10 | **Arbiter/guide mismatch.** The guide's §3 decision tree does not distinguish an *input-side choice set* from an *outcome set*, and its §4 does not mention that a domain port is not an input object. The model needed both rules and the guide lacked them. | guide §3, §4 | reconciliation |

F1–F2 are correctness failures. F3–F9 are the definition-of-done items. F10 is the
reconciliation itself.

## 2. Rules (normative)

**R1 — A domain port is not a GraphQL input object.**
`WorkflowStepInput` / `WorkflowStepOutput` are **domain ports**: GraphQL
interfaces describing the shape of a step's declared input and output. In these
names `Input`/`Output` denote **port direction**, never "GraphQL input object".
An interface may therefore never appear in an argument position. TypeScript-side,
a port is a domain `interface`, not a DTO.

**R2 — Input transport: intent arguments + one generated finite envelope.**
A mutation takes **intent arguments** (what the caller wants to happen) and the
port value travels inside exactly **one** generated envelope scalar, `PortValue`.
The envelope's admissible shapes are exactly the registered `WorkflowStepInput`
implementations, so its codec is **generated** from the port registry. This is a
*generated finite envelope*, not a hand-maintained `Create*`/`Update*` mirror and
not one input object per port. Adding a port implementation regenerates the
envelope; it does not add a hand-written type.

**R3 — Severity and enforcement location are separate.**
`severity` answers *how bad* (`WARN` | `ERROR`). `enforcedAt` answers *where it is
checked* (`AUTHORING_RECONCILER`, `RUN_PREFLIGHT`). They are different fields and
different enums, and neither is derivable from the other. **Severity is a fixed
function of the rule**, published in `Query.edgeInvariantRules` and
`edge-invariant-rules.json`; it is never chosen per edge instance. This removes
the ability to quietly promote or demote a rule by editing one edge.

**R4 — An ERROR is not waivable, so an ERROR cycle cannot become a DAG.**
An invariant has exactly one disposition, and the disposition is a **union**, not
a nullable reason field:

```
InvariantSatisfied | InvariantWaiver | InvariantRejection
```

- `InvariantSatisfied` — nothing was violated.
- `InvariantWaiver` — a **WARN** was violated and explicitly acknowledged with a
  non-empty reason and an authenticated principal.
- `InvariantRejection` — the invariant did not hold. **This type has no waiver,
  override, acknowledgement or Boolean-force field.** A rejection carries a
  non-null `reason` only to *explain* the failure, which confers no authority.

`waivable` is true **iff** `severity == WARN`. `ACYCLIC` is `ERROR`, so it is not
waivable, so no input in this schema can turn a cycle into a dispatchable DAG.
`WorkflowValidation.dispatchable` is false whenever any ERROR rejection exists,
and a rejection cannot be removed by anything in the request. `edge_invariants.py
--self-test` demonstrates this on a real cyclic graph (case T3).

**R5 — Enum vs interface vs union for outcomes.**
| Situation | Choice |
|---|---|
| Closed finite set of symbolic values, **no payload**, and the caller may choose it | **enum**, named as an input-side choice |
| Closed finite set of symbolic values, **no payload**, describing what happened | **enum**, named as an outcome |
| A finite set where the *caller's* admissible values are a proper subset of the outcomes the system can produce | **two enums** — never one. Exposing an outcome the caller cannot cause is a modelling error. |
| Outcome carries **per-variant payload** | **union** (gated-value pattern, guide §5) |
| Types share a structural contract *queries traverse*, and "exactly one of N" is **not** the intent | **interface** |
| "Exactly one of N" with divergent payloads | **union** |

The input-choice enum and the outcome enum are distinct types with distinct
values; a caller-selected value is *coerced into* an outcome by the resolver. In
this model `GateDecision` (APPROVE|REJECT) is the choice and `GateOutcome`
(APPROVED|REJECTED|EXPIRED) is the record — `EXPIRED` is deliberately absent from
the choice enum.

**R6 — Authority is not a string in the request.**
A sensitive value is the opaque scalar `Confidential` (or the generated envelope
codec). No read path returns its contents. `@user_confirmation` **declares** that
a value is gated and by whom (`AUTHENTICATED_PRINCIPAL`, `HUMAN_GATE`,
`STEP_RUNNER`); it confers no authority and carries no secret. Authority is the
authenticated principal plus the exact approved intent the gate recorded.

## 3. Decisions preserved from 2026-09-08 (unchanged)

- `WorkflowStep` **is** the callable. No `StepContract` wrapper, no binding, no
  `stepNumber`, no `prev`/`next`.
- `WorkflowDefinition` is a DAG of steps plus first-class `WorkflowEdge`s.
- Authoring is genuinely **2-way symmetric** between a schema file and typed code
  (`@sink2sync`); neither is canonical.
- `@user_confirmation` remains the first-class opinionated sensitive-value
  monad, surfaced in the LSP, unwrapped only through the confirm gate.
- Effects stay removed; type-level confirmation and derivation from the
  implementing type stand.
- `Workflow` **and** `WorkflowStep` remain runnable.
- No hand-authored mirror `Create*Input` mutation types.
- `simpleEnglishDescription` stays on every port as the search/discovery and
  authoring annotation.
- Invariants keep `WARN` (ignorable **with** reason) and `ERROR`.

**One change inside a preserved decision:** the header's edge set ("branch/gate/
join/parallel") is now `enum EdgeKind { BRANCH GATE JOIN PARALLEL }`. The
previously shipped `NEXT` value was already inconsistent with the September 8
header, so removing it *restores* the locked decision rather than revising it
(F5). Sequencing is expressed by edges, not by an ordering pointer.

**`DAG` is load-bearing.** A DAG permits diamond reconvergence; it forbids cycles.
`ACYCLIC` is an `ERROR` because a cycle makes the graph not a DAG. This is why R4
must hold and why removal of `NEXT` matters: with `NEXT` plus a waivable ACYCLIC,
the model could silently degenerate into a chain with an accepted cycle.

## 4. Artifacts

| Artifact | Role |
|---|---|
| `docs/workflow/schema-workflow.graphql` | The repaired SDL, contract `workflow-model v1` |
| `docs/workflow/workflow-reconciliation-v1.md` | This specification (normative) |
| `docs/workflow/edge-invariant-rules.json` | The published fixed rule → (severity, enforcedAt, waivable) contract |
| `docs/workflow/edge_invariants.py` | Executable reference for R3/R4, with 7 self-test cases |
| `docs/workflow/check-workflow-contract.py` | The offline contract gate: parse + `validate_schema` + DoD + fingerprint |
| `crates/leviath-cli/agents/bake-off/references/composition-guide.md` | The arbiter, reconciled to R1–R5 |
| `docs/workflow/workflow-schema-decisions.md` | Decision log; September 8 preserved, GQL-01 appended |

## 5. Fingerprint and contract identity

`check-workflow-contract.py` prints `fingerprint`, the SHA-256 of the *printed*
schema (graphql-core `print_schema`), so comments, formatting and open questions
do not churn it while any semantic change does.

Accepted fingerprint for this contract:

```
ff9df4e32b05747ee3605042407dcc7e17a347f41bc1464ce567c2f484813559
```

Per the work order's contract-identity requirement, a dependent worker pins this
value **before** implementing and re-verifies before acceptance:

```sh
python3 docs/workflow/check-workflow-contract.py \
    --expected-fingerprint ff9df4e32b05747ee3605042407dcc7e17a347f41bc1464ce567c2f484813559
```

Exit 0 means the accepted contract is the one being built against. Exit 1 with
`FAIL fingerprint` means the SDL changed and the review must be redone.

## 6. Evidence

### 6.1 Environment

Host interpreter: CPython 3.12.14.

```
host python3        -> graphql-core 3.2.5
isolation venv (.venv-326) -> graphql-core 3.2.6
```

`crates/leviath-cli/agents/graphql-schema/requirements.txt` pins
`graphql-core==3.2.6`; the host has 3.2.5. The checks were therefore run under
**both** and agree, including the fingerprint (§6.4). The gate requires graphql-core
but does not pin a patch version, because the semantics it asserts do not depend
on one.

### 6.2 Commands and results

```sh
# before (base 15dc182e)
python3 -c "from graphql import parse; parse(open('docs/workflow/schema-workflow.graphql').read())"
# -> Syntax Error: Expected Name, found ')'.  (exit 1)                      [F1]

# with only F1 patched, to surface F2
python3 -c "from graphql import build_schema; build_schema(open('probe1.graphql').read())"
# -> TypeError: Mutation fields cannot be resolved. Argument type must be a GraphQL input type. (exit 1) [F2]

# after
python3 docs/workflow/check-workflow-contract.py
# -> {"result": "PASS", "parses": true, "validate_schema_errors": 0, "dod_violations": []}  (exit 0)

python3 docs/workflow/edge_invariants.py --self-test
# -> {"self_test": "PASS", "cases": 7}                                       (exit 0)

# the pre-existing bake-off validator agrees
python3 crates/leviath-cli/agents/bake-off/hooks/validate_schema.py docs/workflow/schema-workflow.graphql
# -> VALID: docs/workflow/schema-workflow.graphql                            (exit 0)
```

### 6.3 Gate discrimination

The gate is not vacuous: on the base SDL it fails, and on seven deliberate
regressions applied to the repaired SDL it fails with the expected reason —
`neg4` interface-as-argument, `neg5` severity/enforcement conflation, `neg6`
`NEXT` revival, `neg7` `StepContract`/`stepNumber` revival, `neg8` nullable
output/error pair, `neg9` mirror `Create*Input`, `neg10` `EXPIRED` in the choice
enum. All seven exit 1. See `REPORT.md` for the full transcript.

### 6.4 graphql-core 3.2.6

Re-run in an isolation virtualenv with `graphql-core==3.2.6` (the pin in
`crates/leviath-cli/agents/graphql-schema/requirements.txt`):

```sh
python3 -m venv .venv-326 && . .venv-326/bin/activate
pip install "graphql-core==3.2.6"
python docs/workflow/check-workflow-contract.py          # PASS, exit 0
python docs/workflow/edge_invariants.py --self-test      # PASS, exit 0
python crates/leviath-cli/agents/bake-off/hooks/validate_schema.py docs/workflow/schema-workflow.graphql  # VALID, exit 0
```

The fingerprint is **byte-identical** under 3.2.5 and 3.2.6:
`ff9df4e32b05747ee3605042407dcc7e17a347f41bc1464ce567c2f484813559`. Contract
semantics do not depend on the patch version.

### 6.5 Archived base artifacts

The pre-change SDL and the pre-change guide/decision files are archived under
`docs/workflow/evidence/` so the base is inspectable without checking out
`15dc182e`. SHA-256:

```
22c36aebe46b012c001a6bd492c6ff53f6fbf5173ef3947811d5387da4eb46f8  evidence/schema-workflow.base-15dc182e.graphql
22c36aebe46b012c001a6bd492c6ff53f6fbf5173ef3947811d5387da4eb46f8  evidence/schema-workflow.as-found-copy.graphql
801968ba63a4d567751d6ffbbd3bcc88bfc25982ac7bccc9c48b4fac89dafa5b  evidence/workflow-schema-decisions.base-15dc182e.md
4a0ac2351e72c65ff5c70c4ebfda50a42abc56090a760e59d3de4bbc67fa0e03  evidence/composition-guide.base-15dc182e.md
```

The as-found copy and the `git show` copy of the base SDL hash identically, which
also confirms the comparison was against the right revision.

## 7. Rollback

This is an isolated documentation/contract change. It touches no Rust source, no
resolver, no store and no routing registration, so the existing source/store and
compatibility route are untouched by construction. Revert is `git revert` of the
GQL-01 commit, or restore the base files from `docs/workflow/evidence/`. No
destructive data migration is involved and none is implied.

## 8. Out of scope

Resolver implementations, the two LSP servers, the codegen for the `PortValue`
codec, and the migration of any consumer from a hypothetical legacy shape are
**not** done here. They are handoff obligations named in `REPORT.md`. This work
order repairs the contract and the arbiter and makes both checked.