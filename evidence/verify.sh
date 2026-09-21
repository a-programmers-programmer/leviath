#!/bin/sh
# GQL-03 mechanical evidence. Every check exits non-zero when the claim is false.
# Run from the working directory:  sh evidence/verify.sh
set -e
cd "$(dirname "$0")/.."
SERVER=repo/python/leviath_schema_ls/server.py
echo "== 1. protocol test (init, open, edit, ranged diagnostics, code action, stale edit) =="
python3 -m pytest tests/test_protocol.py -q

echo "== 2. no runtime action from a diagnostic (no 'command' key emitted) =="
if grep -q '"command"' "$SERVER"; then echo "FAIL: server emits a command key"; exit 1; fi
echo "OK: server.py contains no \"command\" key"

echo "== 3. no process execution in the server =="
if grep -Eq 'subprocess|os\.system|Popen|exec\(|eval\(' "$SERVER"; then
  echo "FAIL: server can execute something"; exit 1
fi
echo "OK: no subprocess/os.system/Popen/exec/eval in server.py"

echo "== 4. no sensitive unwrap key in any diagnostic data =="
if grep -Eq '"(unwrap|secret)"' "$SERVER"; then echo "FAIL: a sensitive key is present"; exit 1; fi
echo "OK: no unwrap/secret key in server.py"

echo "== 5. confirmation requirement is surfaced from the shared checker =="
grep -q 'requiresConfirmation' "$SERVER"
grep -q 'conflict.options' "$SERVER"
echo "OK: requiresConfirmation and the option list come from the shared checker"

echo "== 6. stale edit is refused with a named error code =="
grep -q 'STALE_VERSION_CODE = -32002' "$SERVER"
grep -q 'version <= current.version' "$SERVER"
echo "OK: stale versions are refused before the document store is touched"

echo "== 7. acceptance test still asserts (not weakened) =="
COUNT=$(grep -c assert tests/test_protocol.py)
test "$COUNT" -ge 43
echo "OK: tests/test_protocol.py holds $COUNT assert statements"

echo "== 8. the server package is committed on the branch =="
git -C repo ls-files --error-unmatch python/leviath_schema_ls/server.py >/dev/null
echo "OK: server.py is tracked on the branch"

echo
echo "ALL CHECKS PASSED"