# RUNBOOK: end-to-end GraphQL authoring workflow (task-board example)

This is ONE command sequence. A clean checkout follows it from brief to
confirmation. Run every command from the repository root.

Base commit: `15dc182e0bc6c28dfa93f4b8dd3c423372808f61` (branch `fleet/gql-11`).

Every command below is real and copy-pasteable today. Where a step is not
implemented yet, this runbook says so and gives no invented command.

Two path variables are used below. Set them once:

```sh
export REPO="<absolute path to the leviath checkout>"
export FIX="<absolute path to the fixtures/gql-11 directory delivered with this task>"
export BP="$REPO/crates/leviath-cli/agents/graphql-schema"
export WORK="$PWD/tmp/run"
```

`RUNBOOK.md`, `REPORT.md`, `EVIDENCE.json` and `fixtures/gql-11/` are delivered
together as this task's artifacts. Copy `fixtures/gql-11/` next to the checkout,
or use the copy that ships with the task.

## What is tracked and what is not

The workflow uses only files tracked at the base commit. A clean checkout has
these:

- `crates/leviath-cli/agents/graphql-schema/scripts/gate.py` -- the deterministic checker
- `crates/leviath-cli/agents/graphql-schema/scripts/generate_tool.py` -- embeds the checker into the Rhai tool
- `crates/leviath-cli/agents/graphql-schema/scripts/run_stage.py` -- application pipeline entrypoint
- `crates/leviath-cli/agents/graphql-schema/examples/task-board/` -- the example contract
- `crates/leviath-cli/agents/graphql-schema/tests/test_gate.py` -- the committed tests
- `crates/leviath-cli/agents/graphql-schema/agent.leviath` -- the blueprint

An earlier attempt added an `e2e/` directory. `git status` shows it as
untracked (`??`). A clean checkout does NOT have it. This runbook does not use it.

## 0. Install the one dependency

```sh
python3 -m pip install -r crates/leviath-cli/agents/graphql-schema/requirements.txt
```

Confirm the import:

```sh
python3 -c 'import graphql; print("graphql-core", graphql.version)'
```

## 1. Brief -> working contract

The brief is the task text. The example contract is the seed for a new run.

```sh
export BP="$PWD/crates/leviath-cli/agents/graphql-schema"
export WORK="$PWD/tmp/run"
rm -rf "$WORK" && mkdir -p "$WORK"
cp -r "$BP/examples/task-board" "$WORK/graphql-contract"
find "$WORK/graphql-contract" -type f | sort
```

## 2. Static check -> checked fingerprint

```sh
python3 "$BP/scripts/gate.py" check --root "$WORK/graphql-contract" > "$WORK/checked.json"
python3 -c "import json;d=json.load(open('$WORK/checked.json'));print('status:',d['status']);print('fingerprint:',d['fingerprint']);print('errors:',d['errors'])"
```

Exit code 0 and status `checked` mean the mechanics passed. The fingerprint is
the content identity of every contract file.

## 3. Reviewed contract -> sealed receipt

The review record lives in the `review` context region of a live run, never as a
file inside `graphql-contract/`. For this deterministic sequence, write a review
JSON outside the contract directory and pass it with `--review`.

Fill the fingerprint from the check:

```sh
FP=$(python3 -c "import json;print(json.load(open('$WORK/checked.json'))['fingerprint'])")
python3 - "$FP" "$FIX/review.template.json" "$WORK/review.json" <<'PY'
import json, sys
fp, tpl, out = sys.argv[1], sys.argv[2], sys.argv[3]
r = json.load(open(tpl)); r["fingerprint"] = fp
json.dump(r, open(out, "w"), indent=2)
PY
```

Accept and seal:

```sh
python3 "$BP/scripts/gate.py" accept --root "$WORK/graphql-contract" --review "$WORK/review.json" > "$WORK/accepted.json"
python3 -c "import json;print('status:',json.load(open('$WORK/accepted.json'))['status'])"
```

Verify against the receipt:

```sh
python3 "$BP/scripts/gate.py" verify --root "$WORK/graphql-contract"
```

`accepted.json` is written by the gate only. Never write it by hand.

The filled template is a mechanics fixture. It is NOT evidence of a real model
review. A real review comes from step 6.

## 4. Two-way edits

An implementer proposes a contract change. The gate must refuse it until a fresh
review re-seals it.

```sh
printf '\nImplementer proposes an additive field.\n' >> "$WORK/graphql-contract/design.md"
python3 "$BP/scripts/gate.py" verify --root "$WORK/graphql-contract"
```

This exits 1 with `Contract changed after acceptance; rerun the workflow`.

Re-check, re-review and re-accept:

```sh
python3 "$BP/scripts/gate.py" check --root "$WORK/graphql-contract" > "$WORK/checked2.json"
FP2=$(python3 -c "import json;print(json.load(open('$WORK/checked2.json'))['fingerprint'])")
python3 - "$FP2" "$FIX/review.template.json" "$WORK/review2.json" <<'PY'
import json, sys
fp, tpl, out = sys.argv[1], sys.argv[2], sys.argv[3]
r = json.load(open(tpl)); r["fingerprint"] = fp
json.dump(r, open(out, "w"), indent=2)
PY
python3 "$BP/scripts/gate.py" accept --root "$WORK/graphql-contract" --review "$WORK/review2.json" > "$WORK/accepted2.json"
python3 "$BP/scripts/gate.py" verify --root "$WORK/graphql-contract"
```

Restore the pristine seed:

```sh
cp "$BP/examples/task-board/design.md" "$WORK/graphql-contract/design.md"
python3 "$BP/scripts/gate.py" accept --root "$WORK/graphql-contract" --review "$WORK/review.json" > "$WORK/accepted3.json"
```

## 5. Generated interfaces -- NOT IMPLEMENTED (honest null)

There is no committed command today that generates client interfaces from an
accepted contract. The only tracked generator is:

```sh
python3 "$BP/scripts/generate_tool.py"
```

That script embeds `gate.py` into `tools/graphql_contract_gate.rhai`. It does
NOT emit `client.ts` or `manifest.json`. Do not claim otherwise.

The untracked `e2e/pkg/generate.py` did emit those files, but a clean checkout
does not have it. So this step is a null. A future task must commit a generator
and add it here.

## 6. Workflow execution -- live model run (needs `lev` and a permitted model)

This step spends provider money. It is not deterministic.

First confirm the blueprint is valid:

```sh
lev validate "$BP"
```

Then run the workflow with the brief as the task:

```sh
lev run "$BP/agent.leviath" --task "$(cat "$FIX/BRIEF.md")" \
    --workdir "$WORK/graphql-contract" --yolo --wait --json
```

A live run outlives a 60 second shell. Background it and poll the run id.

The shipped `agent.leviath` names models fleet policy forbids in some stages
(`gemini-3.5-flash`, `claude-opus-5`). A live run needs a blueprint pinned to a
permitted model. The untracked `e2e/live-review/agent.leviath` did that pin; a
clean checkout does not have it. So the live run is a null until a permitted
blueprint is committed.

A successful `lev` process alone does not mean acceptance. The workflow may
legitimately finish `blocked`.

## 7. Confirmation with the parent pipeline pin

The parent pipeline saves the fingerprint and re-checks it before work starts.

```sh
python3 "$BP/scripts/gate.py" verify --root "$WORK/graphql-contract" \
    --expected-fingerprint "$FP"
```

This exits 0. A different expected fingerprint exits 1:

```sh
python3 "$BP/scripts/gate.py" verify --root "$WORK/graphql-contract" \
    --expected-fingerprint 0000000000000000000000000000000000000000000000000000000000000000
```

## 8. Committed tests

```sh
python3 -m unittest discover -s crates/leviath-cli/agents/graphql-schema/tests
```

## Orchestration: task and attempt records

The workflow attaches the fingerprint and artifact paths to the existing task
and attempt records. The task controller owns lifecycle transitions.

The task record for this work is in `/data/fleet/graph.db`. Read it with:

```sh
PYTHONPATH=/data/fleet python3 - <<'PY'
from fleet.store import Store
s = Store('/data/fleet/graph.db')
t = s.get('T01M2NM76EAKVAXNQA8QNMYNZGK')
print(t['id'], t['kind'], t['status'])
print('artifacts:', t['artifacts'])
print('check:', t['check'])
PY
```

The record carries `artifacts`, `attempt`, `check`, `current_run` and `status`.
The blueprint is an execution stage. It is not a second task store.

## Status vocabulary

- **blocked** -- the gate refused. Nothing is accepted.
- **checked** -- static checks passed and a fingerprint exists. No review yet.
- **accepted** -- static checks passed and a matching review sealed the contract.
- **verified** -- `verify` re-ran every check against the accepted receipt.

## Evidence checklist

For each run record: the base commit, the exact commands, the working
directories, the exit codes, the run id, the log paths and the content hashes.
Distinguish blocked from checked from accepted from verified. Record actual
provider spend for a live run. Never list a live run as a deterministic command.
