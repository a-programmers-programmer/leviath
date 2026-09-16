#!/usr/bin/env bash
# GQL-02 acceptance run. Deterministic; no network; no provider call; no secret read.
# Harvests the saved sub-orders (GQL-02a code proof, GQL-02b contract pin) and
# re-runs the acceptance checks for the whole of GQL-02.
# Usage: bash docs/workflow/evidence/run-gql02-acceptance.sh   (from repo root)
set -u
cd "$(dirname "$0")/../../.." || exit 2

FAILED=0
step() { printf '\n=== [%s] %s\n' "$1" "$2"; }
check() { # check <label> <expected_exit> <cmd...>
  local label="$1" want="$2"; shift 2
  "$@"
  local got=$?
  if [ "$got" -eq "$want" ]; then
    printf '%-46s exit=%s (expect %s) OK\n' "$label" "$got" "$want"
  else
    printf '%-46s exit=%s (expect %s) FAIL\n' "$label" "$got" "$want"
    FAILED=1
  fi
}

echo "== GQL-02 acceptance: shared reconciliation rules and conflict detection"
echo "== repo: $(pwd)"
echo "== HEAD: $(git rev-parse HEAD 2>/dev/null)"
echo "== base: $(cat .gql02-base 2>/dev/null || echo '15dc182e0bc6c28dfa93f4b8dd3c423372808f61')"
echo "== python: $(python3 --version 2>&1)  graphql-core: $(python3 -c 'import graphql;print(graphql.version)' 2>/dev/null)"

step 1 "DoD clause: symmetry, concurrent conflicts, staleness, idempotence (selftest)"
PYTHONPATH=python python3 -m leviath_reconcile selftest; echo "exit=$?"

step 2 "contract pin re-verification (GQL-01 accepted fingerprint)"
PYTHONPATH=python python3 -m leviath_reconcile verify-contract --json; echo "exit=$?"

step 3 "real-artifact end-to-end proof (real SDL vs real typed Python)"
PYTHONPATH=python python3 -m leviath_reconcile e2e-real --json; echo "exit=$?"

step 4 "contract pin + both adapters bound end-to-end + no ping-pong"
PYTHONPATH=python python3 -m leviath_reconcile pin-check --json; echo "exit=$?"

step 5 "pinned acceptance tests (GQL-02b)"
# pytest -q ends with a wall-clock tail. Strip it so the recorded transcript repeats.
PYTHONPATH=python python3 -m pytest python/leviath_reconcile/test_pin.py -q 2>&1 \
  | sed -E 's/ in [0-9]+\.[0-9]+s$//'
echo "exit=${PIPESTATUS[0]}"

step 6 "gate discrimination: a drifted contract must FAIL loudly"
check "verify-contract with wrong pin" 1 \
  env PYTHONPATH=python python3 -m leviath_reconcile verify-contract \
  --expected 0000000000000000000000000000000000000000000000000000000000000000

step 7 "artifact hashes (sha256)"
python3 - <<'PY'
import hashlib, os
paths = [
    "docs/workflow/schema-workflow.graphql",
    "docs/workflow/workflow-schema-decisions.md",
    "python/leviath_reconcile/pin.py",
    "python/leviath_reconcile/test_pin.py",
    "python/leviath_reconcile/cli.py",
    "python/leviath_reconcile/__init__.py",
    "python/leviath_reconcile/reconcile.py",
    "python/leviath_reconcile/identity.py",
    "python/leviath_reconcile/crosswalk.py",
    "python/leviath_reconcile/scenarios.py",
    "python/leviath_reconcile/e2e_real.py",
    "python/leviath_reconcile/adapters/schema_adapter.py",
    "python/leviath_reconcile/adapters/python_adapter.py",
    "python/leviath_reconcile/fixtures/workflow_model.py",
    "tools/generate_code_counterpart.py",
]
for p in paths:
    try:
        h = hashlib.sha256(open(p, "rb").read()).hexdigest()
        print(f"{h}  {os.path.getsize(p):>7}  {p}")
    except FileNotFoundError:
        print(f"(missing)              {p}")
PY

step 8 "resulting diff stat vs base (committed only; reproducible)"
# Committed-only by design, so the number never depends on the working tree.
# Scoped to python+tools because the evidence files are the record itself: a diff
# that included them would change with every evidence commit. The unstable full
# number is printed afterwards as informational only.
BASE="$(cat .gql02-base 2>/dev/null || echo 15dc182e0bc6c28dfa93f4b8dd3c423372808f61)"
git --no-pager diff --stat "$BASE" HEAD -- python tools 2>/dev/null
printf 'informational, grows with each evidence commit:\n'
git --no-pager diff --shortstat "$BASE" HEAD 2>/dev/null
printf 'informational, worktree contract files vs base (GQL-01, uncommitted):\n'
git --no-pager diff --shortstat "$BASE" -- docs/workflow/schema-workflow.graphql docs/workflow/workflow-schema-decisions.md 2>/dev/null

echo
if [ "$FAILED" -eq 0 ]; then
  echo "GQL-02 acceptance: PASS"
else
  echo "GQL-02 acceptance: FAIL"
fi
exit "$FAILED"