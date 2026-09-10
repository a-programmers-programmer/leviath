# Workflow Schema — Decision Log

(append-only; latest first)

## 2026-09-08 — FINAL LOCK
- Authoring = 2-way symmetric LSP (2 LSP servers, not a build/hook): reconcile schema<->code, semantic workflow checks, + dead-simple codegen hook.
- @user_confirmation = first-class opinionated sensitive-string monad, surfaced in LSP, unwrap only via confirm gate.
- Effects removed (flat list) -> type-level @user_confirmation + derived from implementing type.
- WorkflowStep IS callable (killed StepContract/binding/stepNumber/prev-next).
- WorkflowDefinition = DAG (steps + first-class WorkflowEdge); invariants severity WARN(ignorable w/ reason)/ERROR.
- Both Workflow + WorkflowStep runnable; no mirror Create*Input mutation types.
