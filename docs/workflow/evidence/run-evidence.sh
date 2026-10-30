#!/usr/bin/env bash
# GQL-01 evidence run. Deterministic; no network; no provider call.
# Usage: bash docs/workflow/evidence/run-evidence.sh   (from repo root)
set -u
cd "$(dirname "$0")/../../.." || exit 2
echo "== repo: $(pwd)"
echo "== HEAD:  $(git rev-parse HEAD 2>/dev/null)"
echo "== base for diff: $(cat .gql01-base 2>/dev/null || echo '15dc182e')"
echo

echo "=== [1] gate: repaired SDL (host graphql-core $(python3 -c 'import graphql;print(graphql.version)' 2>/dev/null))"
python3 docs/workflow/check-workflow-contract.py; echo "exit=$?"
echo

echo "=== [2] invariant reference self-test"
python3 docs/workflow/edge_invariants.py --self-test; echo "exit=$?"
echo

echo "=== [3] pre-existing bake-off validator (unchanged, agrees)"
python3 crates/leviath-cli/agents/bake-off/hooks/validate_schema.py docs/workflow/schema-workflow.graphql; echo "exit=$?"
echo

echo "=== [4] fingerprint pin re-verification"
python3 docs/workflow/check-workflow-contract.py \
  --expected-fingerprint ff9df4e32b05747ee3605042407dcc7e17a347f41bc1464ce567c2f484813559; echo "exit=$?"
echo

echo "=== [5] gate discrimination: base SDL must FAIL, repaired must PASS"
python3 docs/workflow/check-workflow-contract.py docs/workflow/evidence/schema-workflow.base-15dc182e.graphql >/dev/null 2>&1; echo "base SDL gate exit=$? (expect 1)"
python3 docs/workflow/check-workflow-contract.py docs/workflow/schema-workflow.graphql >/dev/null 2>&1; echo "repaired gate exit=$? (expect 0)"
echo

echo "=== [6] negative matrix: each DoD regression must be caught"
N=../neg
if [ -d "$N" ]; then
  for f in neg4 neg5 neg6 neg7 neg8 neg9 neg10; do
    printf '%-7s ' "$f"
    python3 docs/workflow/check-workflow-contract.py "$N/$f.graphql" >/dev/null 2>&1
    printf 'exit=%s (expect 1)\n' "$?"
  done
else
  echo "(../neg not present in this checkout; see REPORT.md transcript)"
fi
echo

echo "=== [7] artifact hashes"
sha256sum docs/workflow/evidence/* docs/workflow/edge-invariant-rules.json 2>/dev/null
python3 - <<'PY'
import hashlib
for p in ("docs/workflow/schema-workflow.graphql",
          "docs/workflow/workflow-reconciliation-v1.md",
          "docs/workflow/check-workflow-contract.py",
          "docs/workflow/edge_invariants.py",
          "docs/workflow/workflow-schema-decisions.md",
          "crates/leviath-cli/agents/bake-off/references/composition-guide.md"):
    try:
        h = hashlib.sha256(open(p,'rb').read()).hexdigest()
        print(f"{h}  {p}")
    except FileNotFoundError:
        print(f"(missing)  {p}")
PY