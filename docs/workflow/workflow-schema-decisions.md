# Workflow Schema — Decision Log

(append-only; latest first)

## 2026-09-12 — GQL-01 reconciliation (contract workflow-model v1)
- Reconciliation spec: `docs/workflow/workflow-reconciliation-v1.md`. Nothing in the 2026-09-08 lock below is revised except the two items marked *(corrective)*.
- *(corrective)* The SDL did not parse (F1) and could not build (F2). Both fixed; the model is now mechanically checked by `docs/workflow/check-workflow-contract.py`.
- *(corrective)* `EdgeKind.NEXT` contradicted this log's own 2026-09-08 entry; removed. Edge kinds are BRANCH/GATE/JOIN/PARALLEL.
- Domain **port** (interface, port direction: `*Input`/`*Output`) is distinguished from a GraphQL **input object**. A port is never an argument type; a port value travels in one **generated** envelope (`PortValue`). No hand-maintained Create/Update mirror.
- Severity and enforcement location are separate fields with separate enums; severity is a fixed property of the rule, published in `edge-invariant-rules.json`.
- An `ERROR` is not waivable: disposition is a union whose rejection arm has no waiver field. `ACYCLIC` is ERROR, so a cycle cannot be waived into a DAG.
- Enum/interface/union outcome rules written down (guide §3a, §4a, §7a, §7b) and the Composition Guide reconciled to them.
- Sensitive value is the opaque scalar `Confidential`; `@user_confirmation` declares the gate, authority is the principal + the exact approved intent. No secret in the SDL.
- Accepted contract fingerprint (printed-SDL SHA-256): `ff9df4e32b05747ee3605042407dcc7e17a347f41bc1464ce567c2f484813559`.

## 2026-09-08 — FINAL LOCK
- Authoring = 2-way symmetric LSP (2 LSP servers, not a build/hook): reconcile schema<->code, semantic workflow checks, + dead-simple codegen hook.
- @user_confirmation = first-class opinionated sensitive-string monad, surfaced in LSP, unwrap only via confirm gate.
- Effects removed (flat list) -> type-level @user_confirmation + derived from implementing type.
- WorkflowStep IS callable (killed StepContract/binding/stepNumber/prev-next).
- WorkflowDefinition = DAG (steps + first-class WorkflowEdge); invariants severity WARN(ignorable w/ reason)/ERROR.
- Both Workflow + WorkflowStep runnable; no mirror Create*Input mutation types.
