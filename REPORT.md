# REPORT: GQL-11-A fixture and runbook for the end-to-end authoring workflow

Status: COMPLETE. All three declared artifacts are present.

## Deliverables

- `RUNBOOK.md` -- one command sequence from brief to confirmation.
- `fixtures/gql-11/` -- the brief and the inputs the runbook consumes.
- `EVIDENCE.json` -- base commit, commands with cwd and expect_rc, artifacts.

## Base commit

`15dc182e0bc6c28dfa93f4b8dd3c423372808f61`, branch `fleet/gql-11`.

## Key finding

The previous attempt shipped `crates/leviath-cli/agents/graphql-schema/e2e/`.
`git status` shows that directory as UNTRACKED (`??`). `git ls-files` lists 0
files under it. So `e2e/pkg/replay.py`, `e2e/pkg/generate.py`, `e2e/pkg/live.sh`
and `e2e/tests/test_e2e.py` do NOT exist in a clean checkout.

The runbook therefore uses only tracked files, and marks each missing step as a
null. This is the honest result: the earlier runbook was correct in shape but
cited files a clean checkout cannot see.

Tracked blueprint files at the base commit (17 total) include:

- `README.md`, `agent.leviath`, `requirements.txt`, `hooks/protocol.rhai`
- `examples/task-board/` (requirements, design, SDL, contract.json, variables, operations, handoff)
- `scripts/gate.py`, `scripts/generate_tool.py`, `scripts/run_stage.py`
- `tests/test_gate.py`, `tools/graphql_contract_gate.rhai`, `tools/graphql_contract_guide.rhai`

## Verified today

`tmp/runbook_check.sh` runs the runbook sequence from the base checkout. The
transcript is `tmp/runbook_check.out`. Result:

- 14 commands recorded; the deterministic steps pass.
- `check` reaches `checked`, fingerprint
  `8d0e63943e584388cdd154c33a07a0551679558c38f9f88f49e1612f24a05b9a`.
- `accept` seals the receipt; `verify` re-checks it and returns `accepted`.
- Two-way edit: `verify` refuses the edited contract with exit 1;
  re-check changes the fingerprint; re-accept and verify return `accepted`.
- The pipeline pin accepts the true fingerprint and rejects a wrong one with exit 1.
- 24 committed tests pass.

## Nulls (not implemented today, stated in the runbook)

1. **Generated interfaces.** No tracked command emits `client.ts` or
   `manifest.json`. The only tracked generator, `scripts/generate_tool.py`,
   embeds `gate.py` into the Rhai gate tool. The runbook says this and gives the
   real command for what that script actually does.
2. **Live model run.** The tracked `agent.leviath` names `gemini-3.5-flash` and
   `claude-opus-5`, which fleet policy forbids. A live run needs a blueprint
   pinned to a permitted model. No such blueprint is tracked. The runbook marks
   this step a null and does not invent a command.

## Orchestration

The runbook documents the existing task and attempt records in
`/data/fleet/graph.db`. The task record carries `artifacts`, `attempt`, `check`,
`current_run` and `status`, plus `blueprint` and `goal`. The blueprint is an
execution stage, not a second task store.

## Evidence paths

- `tmp/runbook_check.out` -- the full transcript of the sequence
- `tmp/runbook_check.sh` -- the script that produced it
- `fixtures/gql-11/BRIEF.md` -- the brief
- `fixtures/gql-11/review.template.json` -- the review record shape
- `EVIDENCE.json` -- commands with cwd and expect_rc

## Honest limits

- The filled review template is a mechanics fixture, not a model review.
- The deterministic sequence proves the mechanics and the refusals. It does not
  prove model judgment.
- Client codegen and the pinned live blueprint remain to be built.
