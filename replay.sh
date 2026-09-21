#!/usr/bin/env bash
# replay.sh -- deterministic replay of the GQL-11 authoring runbook (RUNBOOK.md).
#
# Runs the runbook's command sequence against the fixture in ./fixtures/gql-11.
# NO model calls. Deterministic mechanics only: schema review/reconcile, codegen,
# workflow execution against recorded task/attempt records, confirm gate.
#
# Every command is printed, its exit code is printed, and the artifact paths it
# produced are printed. Steps the runbook marks as not implemented are SKIPped
# with an explicit line. Nothing is faked.
#
# Exit 0 on success. Exit 1 if any expected result did not hold.
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE" || exit 1

FIX="$HERE/fixtures/gql-11"
BP="$FIX/blueprint"                 # tracked blueprint, vendored at the base commit
WORK="$HERE/tmp/replay"
LOG="$HERE/tmp/replay.log"
BASE_COMMIT="15dc182e0bc6c28dfa93f4b8dd3c423372808f61"

FAIL=0
STEP=0
: > "$LOG"
mkdir -p "$HERE/tmp"

say()  { printf '%s\n' "$*" | tee -a "$LOG"; }
hr()   { say "------------------------------------------------------------"; }
head_() { hr; STEP=$((STEP+1)); say "STEP $STEP: $*"; hr; }

# run <label> <expected_rc> <cmd...>
# Prints the command, its real exit code, and the artifacts it wrote.
run() {
  local label="$1" want="$2"; shift 2
  say "\$ $*"
  local before after rc
  before="$(mktemp)"; after="$(mktemp)"
  find "$WORK" -type f 2>/dev/null | sort > "$before"
  "$@" >>"$LOG" 2>&1
  rc=$?
  find "$WORK" -type f 2>/dev/null | sort > "$after"
  say "  exit code: $rc (expected $want)"
  local new
  new="$(comm -13 "$before" "$after")"
  if [ -n "$new" ]; then
    while IFS= read -r f; do say "  artifact: ${f#$HERE/}"; done <<< "$new"
  else
    say "  artifact: (none written)"
  fi
  rm -f "$before" "$after"
  if [ "$rc" != "$want" ]; then
    say "  RESULT: FAIL (wanted exit $want, got $rc)"
    FAIL=$((FAIL+1))
  else
    say "  RESULT: ok"
  fi
  return 0
}

# assert <description> <cmd...>
assert() {
  local desc="$1"; shift
  if "$@" >>"$LOG" 2>&1; then
    say "  ASSERT ok: $desc"
  else
    say "  ASSERT FAIL: $desc"
    FAIL=$((FAIL+1))
  fi
}

skip() { say "SKIP: $*"; }

say "GQL-11-B deterministic replay of RUNBOOK.md"
say "base_commit: $BASE_COMMIT (branch fleet/gql-11)"
say "fixture:     fixtures/gql-11"
say "workdir:     tmp/replay"
say "no model calls are made by this script"

# ---------------------------------------------------------------- step 0
head_ "0. Install the one dependency / confirm the import"
run "dependency import" 0 python3 -c 'import graphql; print("graphql-core", graphql.version)'
assert "graphql-core is importable" python3 -c 'import graphql'

# ---------------------------------------------------------------- step 1
head_ "1. Brief -> working contract (seed from the tracked example)"
rm -rf "$WORK"; mkdir -p "$WORK"
run "seed working contract" 0 cp -r "$BP/examples/task-board" "$WORK/graphql-contract"
say "  seed files:"
find "$WORK/graphql-contract" -type f | sort | sed "s|$HERE/|    |" | tee -a "$LOG"
assert "seed matches the tracked example" diff -r "$BP/examples/task-board" "$WORK/graphql-contract"

# ---------------------------------------------------------------- step 2
head_ "2. Static check -> checked fingerprint (schema review/reconcile)"
run "gate check" 0 python3 "$BP/scripts/gate.py" check --root "$WORK/graphql-contract"
# gate check prints JSON to stdout; re-run capturing to a file for the record.
python3 "$BP/scripts/gate.py" check --root "$WORK/graphql-contract" > "$WORK/checked.json" 2>>"$LOG"
say "  checked.json written: tmp/replay/checked.json"
python3 - "$WORK/checked.json" <<'PY' | tee -a "$LOG"
import json, sys
d = json.load(open(sys.argv[1]))
print("  status:", d["status"])
print("  fingerprint:", d["fingerprint"])
print("  errors:", d["errors"])
PY
assert "status is checked" python3 -c "import json;d=json.load(open('$WORK/checked.json'));assert d['status']=='checked',d['status']"
assert "no static errors" python3 -c "import json;d=json.load(open('$WORK/checked.json'));assert d['errors']==[],d['errors']"
FP="$(python3 -c "import json;print(json.load(open('$WORK/checked.json'))['fingerprint'])")"
say "  FP=$FP"

# ---------------------------------------------------------------- step 3
head_ "3. Reviewed contract -> sealed receipt (confirm gate)"
run "fill review fingerprint" 0 python3 - "$FP" "$FIX/review.template.json" "$WORK/review.json" <<'PY'
import json, sys
fp, tpl, out = sys.argv[1], sys.argv[2], sys.argv[3]
r = json.load(open(tpl)); r["fingerprint"] = fp
json.dump(r, open(out, "w"), indent=2)
print("  review.json fingerprint:", fp)
PY
run "gate accept" 0 python3 "$BP/scripts/gate.py" accept --root "$WORK/graphql-contract" --review "$WORK/review.json"
python3 "$BP/scripts/gate.py" accept --root "$WORK/graphql-contract" --review "$WORK/review.json" > "$WORK/accepted.json" 2>>"$LOG"
say "  accepted.json written by the gate: tmp/replay/accepted.json"
run "gate verify" 0 python3 "$BP/scripts/gate.py" verify --root "$WORK/graphql-contract"
assert "receipt status is accepted" python3 -c "import json;d=json.load(open('$WORK/accepted.json'));assert d['status']=='accepted',d['status']"

# ---------------------------------------------------------------- step 4
head_ "4. Two-way edits (negative case: the gate must refuse an edited contract)"
printf '\nImplementer proposes an additive field.\n' >> "$WORK/graphql-contract/design.md"
say "\$ printf '...' >> tmp/replay/graphql-contract/design.md"
say "  (deliberately invalid input: the contract changed after acceptance)"
run "gate verify after the edit -- EXPECTED FAILURE" 1 python3 "$BP/scripts/gate.py" verify --root "$WORK/graphql-contract"
assert "verify refused with the exact runbook message" \
  python3 -c "import json,subprocess,sys;r=subprocess.run(['python3','$BP/scripts/gate.py','verify','--root','$WORK/graphql-contract'],capture_output=True,text=True);d=json.loads(r.stdout);assert r.returncode==1;assert d['status']=='blocked';assert any('Contract changed after acceptance' in e for e in d['errors']),d"
say "  NEGATIVE CASE RECORDED: exit 1, status blocked, 'Contract changed after acceptance; rerun the workflow'"

run "re-check after the proposal" 0 python3 "$BP/scripts/gate.py" check --root "$WORK/graphql-contract"
python3 "$BP/scripts/gate.py" check --root "$WORK/graphql-contract" > "$WORK/checked2.json" 2>>"$LOG"
FP2="$(python3 -c "import json;print(json.load(open('$WORK/checked2.json'))['fingerprint'])")"
say "  fingerprint changed: $([ "$FP" != "$FP2" ] && echo yes || echo no) ($FP2)"
assert "fingerprint changed after the edit" test "$FP" != "$FP2"
run "fill fresh review fingerprint" 0 python3 - "$FP2" "$FIX/review.template.json" "$WORK/review2.json" <<'PY'
import json, sys
fp, tpl, out = sys.argv[1], sys.argv[2], sys.argv[3]
r = json.load(open(tpl)); r["fingerprint"] = fp
json.dump(r, open(out, "w"), indent=2)
print("  review2.json fingerprint:", fp)
PY
run "re-accept with the fresh review" 0 python3 "$BP/scripts/gate.py" accept --root "$WORK/graphql-contract" --review "$WORK/review2.json"
run "verify after re-accept" 0 python3 "$BP/scripts/gate.py" verify --root "$WORK/graphql-contract"
run "restore the pristine seed" 0 cp "$BP/examples/task-board/design.md" "$WORK/graphql-contract/design.md"
run "re-accept the pristine seed" 0 python3 "$BP/scripts/gate.py" accept --root "$WORK/graphql-contract" --review "$WORK/review.json"

# ---------------------------------------------------------------- step 5
head_ "5. Generated interfaces -- codegen (the tracked generator only)"
skip "no tracked command emits client.ts or manifest.json; client-interface codegen is NOT IMPLEMENTED"
skip "the only tracked generator is scripts/generate_tool.py, which embeds gate.py into the Rhai tool"
rm -rf "$WORK/bp"; mkdir -p "$WORK/bp/tools"
cp -r "$BP/scripts" "$BP/README.md" "$WORK/bp/"
rm -f "$WORK/bp/tools/graphql_contract_gate.rhai"
run "generate_tool.py (real command, real output)" 0 python3 "$WORK/bp/scripts/generate_tool.py"
say "  emitted:"
ls -l "$WORK/bp/tools" | sed "s|^|    |" | tee -a "$LOG"
assert "generated gate tool matches the tracked tool byte for byte" \
  diff "$WORK/bp/tools/graphql_contract_gate.rhai" "$BP/tools/graphql_contract_gate.rhai"
skip "client.ts / manifest.json remain a null; a future task must commit a generator"

# ---------------------------------------------------------------- step 6
head_ "6. Workflow execution -- recorded task/attempt records, NO model calls"
skip "live model run is NOT RUN: the tracked agent.leviath names fleet-forbidden models"
say "  forbidden model names in the tracked blueprint:"
grep -n 'gemini-3.5-flash\|claude-opus-5' "$BP/agent.leviath" | sed "s|^|    |" | tee -a "$LOG" || true
run "lev validate (blueprint is valid; no model call)" 0 lev validate "$BP"
say "  workflow execution is replayed against the recorded task/attempt records:"
run "read the recorded task record" 0 python3 - "$WORK/task_record.json" <<'PY'
import json, sys
sys.path.insert(0, "/data/fleet")
from fleet.store import Store
s = Store("/data/fleet/graph.db")
t = s.get("T01M2NM76EAKVAXNQA8QNMYNZGK")
out = {k: t.get(k) for k in ("id", "kind", "status", "attempt", "current_run",
                             "check", "artifacts", "blueprint", "goal")}
json.dump(out, open(sys.argv[1], "w"), indent=2, default=str)
print("  task:", out["id"], out["kind"], out["status"])
print("  attempt:", out["attempt"], "current_run:", out["current_run"])
print("  artifacts:", len(out["artifacts"] or []))
PY
assert "the task record carries artifacts, attempt, check and current_run" \
  python3 -c "import json;d=json.load(open('$WORK/task_record.json'));assert all(k in d for k in ('artifacts','attempt','check','current_run','status')),d.keys()"
say "  task_record.json written: tmp/replay/task_record.json"

# ---------------------------------------------------------------- step 7
head_ "7. Confirmation with the parent pipeline pin"
run "verify with the true pin" 0 python3 "$BP/scripts/gate.py" verify --root "$WORK/graphql-contract" --expected-fingerprint "$FP"
run "verify with a WRONG pin -- EXPECTED FAILURE" 1 python3 "$BP/scripts/gate.py" verify --root "$WORK/graphql-contract" --expected-fingerprint 0000000000000000000000000000000000000000000000000000000000000000
say "  NEGATIVE CASE RECORDED: the parent pipeline pin rejects a different contract (exit 1)"

# ---------------------------------------------------------------- step 8
head_ "8. Committed tests"
run "unittest discover" 0 python3 -m unittest discover -s "$BP/tests"
assert "24 committed tests ran" bash -c "python3 -m unittest discover -s '$BP/tests' 2>&1 | grep -q 'Ran 24 tests'"

# ---------------------------------------------------------------- done
hr
say "REPLAY SUMMARY"
say "  steps run: $STEP"
say "  failures:  $FAIL"
say "  log:       tmp/replay.log"
say "  artifacts under tmp/replay/:"
find "$WORK" -type f | sort | sed "s|$HERE/|    |" | tee -a "$LOG"
hr
if [ "$FAIL" -eq 0 ]; then
  say "REPLAY OK (exit 0)"
  exit 0
fi
say "REPLAY FAILED ($FAIL unexpected results, exit 1)"
exit 1
