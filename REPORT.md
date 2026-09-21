# REPORT: GQL-11-B deterministic replay of the authoring runbook

Status: COMPLETE. `bash replay.sh` exits 0. No model calls are made.

## Deliverables

- `replay.sh` -- runs the GQL-11-A runbook command sequence against the fixture.
  It prints each command, its real exit code and the artifact paths it wrote.
  Not-implemented steps print an explicit `SKIP` line. Nothing is faked.
- `EVIDENCE.json` -- base commit, every command with cwd and expect_rc, artifacts.
- `fixtures/gql-11/blueprint/` -- the tracked blueprint, vendored so the replay
  is self-contained.
- `tmp/replay_stdout.txt` and `tmp/replay.log` -- the full transcripts.

## Base commit

`15dc182e0bc6c28dfa93f4b8dd3c423372808f61`, branch `fleet/gql-11`.
Repo used by 11-A: `/data/wt/T01M2KX1Z7Y15Y2E6VCKPKZNB5D-a2/repo`.

## What replay.sh does

Nine steps, eleven assertions, zero failures, four SKIP lines, two negative cases.

| Step | Action | Exit | Result |
| --- | --- | --- | --- |
| 0 | import graphql-core | 0 | ok |
| 1 | brief -> working contract (seed the tracked example) | 0 | seed byte-identical to the example |
| 2 | gate `check` -> schema review and reconcile | 0 | status `checked`, fingerprint `8d0e6394...a05b9a` |
| 3 | fill review, gate `accept`, gate `verify` | 0 | status `accepted`, receipt sealed by the gate |
| 4 | two-way edit: verify after the edit | 1 | **NEGATIVE CASE 1** -- `blocked` |
| 4 | re-check, re-review, re-accept, restore seed | 0 | fingerprint moved to `a58d7989...dd727b`, then back |
| 5 | client-interface codegen | SKIP | not implemented |
| 5 | `generate_tool.py` (the only tracked generator) | 0 | emitted tool matches the tracked tool byte for byte |
| 6 | live model run | SKIP | forbidden models, not run |
| 6 | `lev validate` + read the recorded task record | 0 | blueprint valid; task record replayed |
| 7 | confirm gate with the true pin | 0 | accepted |
| 7 | confirm gate with a wrong pin | 1 | **NEGATIVE CASE 2** |
| 8 | committed tests | 0 | Ran 24 tests, OK |

## Negative case (required)

`replay.sh` contains two deliberate invalid inputs. Both are expected to fail,
both are recorded, both are asserted.

1. **Contract changed after acceptance.** The script appends one line to
   `tmp/replay/graphql-contract/design.md` after the receipt is sealed. The gate
   refuses with exit 1, status `blocked`, and the message
   `Contract changed after acceptance; rerun the workflow`. The script asserts
   the exit code, the status and the message text.
2. **Wrong parent pipeline pin.** `verify --expected-fingerprint 0000...0000`
   exits 1. The true pin exits 0.

## SKIPs (honest nulls)

Four lines, printed with the `SKIP:` prefix. Nothing is faked.

1. **Client-interface generation is not implemented.** No tracked command emits
   `client.ts` or `manifest.json`.
2. The only tracked generator is `scripts/generate_tool.py`. It embeds `gate.py`
   into the Rhai tool. It does not emit client code.
3. `client.ts` / `manifest.json` stay a null. A future task must commit a generator.
4. **The live model run is not run.** The tracked `agent.leviath` names
   `gemini-3.5-flash` and `claude-opus-5`. Fleet policy forbids those models.
   This task also forbids model calls. The step is a null, not faked.

## Key findings

- The deterministic mechanics are sound. `check` -> `accept` -> `verify` works.
  The gate refuses an edited contract and refuses a wrong pin.
- The fingerprint is a real content identity. The edit changed it from
  `8d0e63943e584388cdd154c33a07a0551679558c38f9f88f49e1612f24a05b9a` to
  `a58d798980e10624801ab1a866c5b0e73065c0c55bb880a795c7590c08dd727b`.
- Codegen is reproducible. `generate_tool.py` emitted
  `tools/graphql_contract_gate.rhai` with sha256
  `3cee8427edfe43624a9b14864785ebd7171df66d0957259ad4a8b7afae2d5437`, the same
  hash as the tracked tool.
- The vendored blueprint is byte-identical to the tracked blueprint at the base
  commit. `diff -r fixtures/gql-11/blueprint <repo>/.../graphql-schema` differs
  only by the untracked `e2e/` directory that a clean checkout does not have.
- The task record in `/data/fleet/graph.db` carries `artifacts`, `attempt`,
  `check`, `current_run` and `status`. The replay reads it. It does not write it.
- `replay.sh` makes zero model calls and zero provider spend.

## Evidence paths

- `tmp/replay_stdout.txt` -- stdout of `bash replay.sh` (exit 0)
- `tmp/replay.log` -- the log the script writes while it runs
- `tmp/replay/checked.json` -- status `checked` and the fingerprint
- `tmp/replay/accepted.json` -- the sealed receipt written by the gate
- `tmp/replay/task_record.json` -- the recorded task/attempt record
- `tmp/replay/bp/tools/graphql_contract_gate.rhai` -- the regenerated tool
- `fixtures/gql-11/` -- the brief, the review template and the vendored blueprint

## Honest limits

- The filled review template is a mechanics fixture. It is not a model review.
  The replay proves the mechanics and the refusals, not model judgment.
- Two runbook steps remain nulls: client-interface codegen and the pinned live
  blueprint. A future task must build both.
- The replay reads the fleet task record at `/data/fleet/graph.db`. It does not
  write to it and it does not start a workflow.
