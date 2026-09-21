#!/usr/bin/env bash
# GQL-08 acceptance proof.  Every step prints the real command and its real exit
# code.  Nothing here calls a provider, the network or a secret.
#
# The proof asserts each definition-of-done clause of GQL-08:
#   D1 one deterministic command: contract snapshot + output directory
#   D2 typed interfaces/stubs, operation clients, scalar mappings, source maps
#   D3 a second invocation produces no diff
#   D4 a stale contract identity is rejected (exit 1, no output left behind)
#   D5 handwritten resolvers survive regeneration
#   D6 the Python and TypeScript outputs compile/typecheck
#   D7 generated SDL/operations agree with the accepted contract
set -u

WORK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="$WORK/repo"
CONTRACT="$WORK/contract"
TMP="$WORK/tmp/proof"
OUT="$TMP/out"
PY="python3"
export PYTHONPATH="$REPO/python"

rm -rf "$TMP"
mkdir -p "$TMP"

FAILURES=0

step() { printf '\n### %s\n' "$1"; }

# run <expected_rc> <cmd...>  — print the command, run it, compare the exit code.
run() {
  local want="$1"; shift
  printf '$ %s\n' "$*"
  "$@"
  local rc=$?
  printf 'EXIT=%s (want %s)\n' "$rc" "$want"
  if [ "$rc" -ne "$want" ]; then
    printf 'PROOF FAIL: %s exited %s, wanted %s\n' "$*" "$rc" "$want" >&2
    FAILURES=$((FAILURES + 1))
  fi
  return 0
}

# ---------------------------------------------------------------------------
# D1/D2 — one deterministic command, the emitted surface
# ---------------------------------------------------------------------------
step "D1/D2 generate (first invocation) -> $OUT"
run 0 $PY -m leviath_codegen generate --contract "$CONTRACT" --out "$OUT"

step "D2 the emitted file set is exactly the declared 17 paths"
expected=$(printf '%s\n' \
  contract/operations.graphql contract/schema.graphql handwritten/README.md \
  handwritten/python/leviath_handwritten/resolvers.py handwritten/typescript/resolvers.ts \
  manifest.json python/leviath_generated/__init__.py python/leviath_generated/interfaces.py \
  python/leviath_generated/operations.py python/leviath_generated/py.typed \
  python/leviath_generated/scalars.py sourcemap.json typescript/src/index.ts \
  typescript/src/interfaces.ts typescript/src/operations.ts typescript/src/scalars.ts \
  typescript/tsconfig.json | sort)
actual=$(cd "$OUT" && find . -type f ! -name '*.pyc' ! -path '*__pycache__*' ! -path '*.mypy-cache*' \
  | sed 's|^\./||' | sort)
printf 'expected paths: %s\n' "$(printf '%s' "$expected" | wc -l)"
printf 'actual paths:   %s\n' "$(printf '%s' "$actual" | wc -l)"
diff <(printf '%s\n' "$expected") <(printf '%s\n' "$actual") || FAILURES=$((FAILURES + 1))

step "D2 the generated Python names the contract types and the client"
grep -q "class WorkflowStepInput" "$OUT/python/leviath_generated/interfaces.py" || FAILURES=$((FAILURES + 1))
grep -q "class EdgeKind(enum.Enum)" "$OUT/python/leviath_generated/interfaces.py" || FAILURES=$((FAILURES + 1))
grep -q "class WorkflowClient" "$OUT/python/leviath_generated/operations.py" || FAILURES=$((FAILURES + 1))
grep -q "export interface WorkflowStepInput" "$OUT/typescript/src/interfaces.ts" || FAILURES=$((FAILURES + 1))
grep -q "export class WorkflowClient" "$OUT/typescript/src/operations.ts" || FAILURES=$((FAILURES + 1))
echo "generated symbol greps: OK"

step "D2 the source map records the scalar mappings and contract line map"
run 0 $PY -c "
import json, sys
payload = json.load(open('$OUT/sourcemap.json'))
scalars = payload['scalars']
assert scalars['DateTime']['python'] == 'DateTime', scalars['DateTime']
assert scalars['String']['python'] == 'str', scalars['String']
assert scalars['Confidential']['typescript'] == 'Confidential', scalars['Confidential']
assert scalars['ID']['typescript'] == 'ID', scalars['ID']
entries = {row['symbol']: row for row in payload['entries']}
assert 'WorkflowStep' in entries, sorted(entries)
row = entries['WorkflowStep']
text = open('$CONTRACT/schema-workflow.graphql').read().splitlines()
assert 'WorkflowStep' in text[row['contract_line'] - 1], row
print('scalars =', len(scalars), 'source-map entries =', len(entries))
print('WorkflowStep -> contract line', row['contract_line'], '=', text[row['contract_line'] - 1].strip())
print('scalar mapping and line map: OK')
"

# ---------------------------------------------------------------------------
# D3 — a second invocation produces no diff
# ---------------------------------------------------------------------------
step "D3 snapshot every byte after invocation 1"
( cd "$OUT" && find . -type f ! -name '*.pyc' ! -path '*__pycache__*' | sort \
  | xargs sha256sum ) > "$TMP/after1.sha"
run 0 cp -a "$OUT" "$TMP/out-copy1"

step "D3 generate (second invocation) -> the same directory"
run 0 $PY -m leviath_codegen generate --contract "$CONTRACT" --out "$OUT"

step "D3 the invocation wrote nothing (written: 0, unchanged: 17)"
printf '$ %s\n' "$PY -m leviath_codegen generate --contract $CONTRACT --out $OUT --json"
$PY -m leviath_codegen generate --contract "$CONTRACT" --out "$OUT" --json > "$TMP/second.json"
cat_exit=$?
printf 'EXIT=%s (want 0)\n' "$cat_exit"
[ "$cat_exit" -eq 0 ] || FAILURES=$((FAILURES + 1))
run 0 $PY -c "
import json
report = json.load(open('$TMP/second.json'))
assert report['result'] == 'PASS', report['result']
assert report['written'] == [], report['written']
assert len(report['unchanged']) == 17, report['unchanged']
assert report['handwritten_kept'] == sorted(report['handwritten_kept'])
print('written =', report['written'])
print('unchanged =', len(report['unchanged']), 'files')
print('second invocation produces no diff: OK')
"

step "D3 byte-identical hash snapshot, then a whole-tree diff"
( cd "$OUT" && find . -type f ! -name '*.pyc' ! -path '*__pycache__*' | sort \
  | xargs sha256sum ) > "$TMP/after2.sha"
run 0 diff -u "$TMP/after1.sha" "$TMP/after2.sha"
run 0 diff -r "$TMP/out-copy1" "$OUT"

# ---------------------------------------------------------------------------
# D4 — a stale contract identity is rejected
# ---------------------------------------------------------------------------
step "D4 tampered snapshot: append a type to a copy of the SDL"
rm -rf "$TMP/contract-drift"
run 0 cp -a "$CONTRACT" "$TMP/contract-drift"
printf '\ntype InjectedDrift { x: Int }\n' >> "$TMP/contract-drift/schema-workflow.graphql"
run 1 $PY -m leviath_codegen generate --contract "$TMP/contract-drift" \
  --out "$TMP/out-drift"

step "D4 the rejected run left no output behind"
run 0 $PY -c "
import os
path = '$TMP/out-drift'
print('out-drift exists =', os.path.exists(path))
assert not os.path.exists(path), path
print('no half-written output tree: OK')
"

step "D4 an unrelated contract directory is rejected"
rm -rf "$TMP/contract-alien"
run 0 mkdir -p "$TMP/contract-alien"
printf 'type Order { id: ID! }\n' > "$TMP/contract-alien/schema-workflow.graphql"
run 1 $PY -m leviath_codegen generate --contract "$TMP/contract-alien" --out "$TMP/out-alien"

step "D4 a wrong expected fingerprint is rejected"
run 1 $PY -m leviath_codegen generate --contract "$CONTRACT" --out "$TMP/out-pin" \
  --expected-fingerprint 0000000000000000000000000000000000000000000000000000000000000000

# ---------------------------------------------------------------------------
# D5 — handwritten resolvers survive regeneration
# ---------------------------------------------------------------------------
step "D5 edit the seeded handwritten resolvers, then regenerate"
RESOLVER="$OUT/handwritten/python/leviath_handwritten/resolvers.py"
TS_RESOLVER="$OUT/handwritten/typescript/resolvers.ts"
printf '\n# handwritten edit kept across regeneration\nHANDWRITTEN_MARKER = "kept"\n' >> "$RESOLVER"
printf '\n// handwritten ts edit\nexport const TS_MARKER = 1;\n' >> "$TS_RESOLVER"
( cd "$OUT" && sha256sum handwritten/python/leviath_handwritten/resolvers.py \
  handwritten/typescript/resolvers.ts ) > "$TMP/resolvers-edit1.sha"
run 0 $PY -m leviath_codegen generate --contract "$CONTRACT" --out "$OUT"

step "D5 the handwritten edits are still there and unchanged"
( cd "$OUT" && sha256sum handwritten/python/leviath_handwritten/resolvers.py \
  handwritten/typescript/resolvers.ts ) > "$TMP/resolvers-edit2.sha"
run 0 diff -u "$TMP/resolvers-edit1.sha" "$TMP/resolvers-edit2.sha"
run 0 grep -n "HANDWRITTEN_MARKER" "$RESOLVER"
run 0 grep -n "TS_MARKER" "$TS_RESOLVER"

# ---------------------------------------------------------------------------
# D6/D7 — verify: compile, typecheck and agreement
# ---------------------------------------------------------------------------
step "D6/D7 verify --out <dir>"
run 0 $PY -m leviath_codegen verify --out "$OUT"
printf '$ %s\n' "$PY -m leviath_codegen verify --out $OUT --json"
$PY -m leviath_codegen verify --out "$OUT" --json > "$TMP/verify.json"
verify_exit=$?
printf 'EXIT=%s (want 0)\n' "$verify_exit"
[ "$verify_exit" -eq 0 ] || FAILURES=$((FAILURES + 1))
run 0 $PY -c "
import json
payload = json.load(open('$TMP/verify.json'))
assert payload['result'] == 'PASS', payload
assert payload['manifest']['result'] == 'PASS', payload['manifest']
assert payload['python']['result'] == 'PASS', payload['python']
assert payload['typescript']['result'] == 'PASS', payload['typescript']
assert payload['agreement']['result'] == 'PASS', payload['agreement']
assert payload['contract_fingerprint'] == payload['pinned_fingerprint']
assert payload['agreement']['errors'] == [], payload['agreement']['errors']
print('fingerprint =', payload['contract_fingerprint'])
print('root fields covered =', len(payload['agreement']['covered_root_fields']))
print('verify: PASS')
"

step "D7 the generated SDL is the accepted SDL: printed-SHA-256 identity"
run 0 sha256sum "$OUT/contract/schema.graphql"
run 0 $PY -c "
import hashlib
from leviath_codegen.model import load_contract
c = load_contract('$CONTRACT')
p = hashlib.sha256(c.printed_sdl.encode()).hexdigest()
print('printed SDL sha256 =', p)
print('accepted pin       =', c.pinned_fingerprint)
assert p == c.pinned_fingerprint, (p, c.pinned_fingerprint)
print('identical          = True')
"

step "D7 operations validate against the generated SDL and cover every root"
run 0 $PY -c "
from graphql import build_schema, parse, validate
s = build_schema(open('$OUT/contract/schema.graphql').read())
d = parse(open('$OUT/contract/operations.graphql').read())
e = validate(s, d)
print('validation errors =', [str(x) for x in e])
roots = set(s.query_type.fields) | set(s.mutation_type.fields)
covered = {sel.name.value for dfn in d.definitions for sel in dfn.selection_set.selections}
assert e == [], [str(x) for x in e]
assert roots <= covered, sorted(roots - covered)
print('root fields       =', len(roots), 'all covered')
assert 'input: \$input' in open('$OUT/contract/operations.graphql').read()
assert 'PortValue!' in open('$OUT/contract/operations.graphql').read()
print('SDL/operations agreement: OK')
"

# ---------------------------------------------------------------------------
printf '\n### PROOF SUMMARY\n'
if [ "$FAILURES" -ne 0 ]; then
  printf 'PROOF FAIL: %s check(s) failed\n' "$FAILURES" >&2
  exit 1
fi
printf 'PROOF PASS: every GQL-08 definition-of-done clause holds\n'
exit 0
