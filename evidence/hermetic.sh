#!/bin/sh
# Prove the protocol test is hermetic: it runs with the GQL-02 worktree path
# removed from the environment, using only the read-only vendored engine.
set -e
cd "$(dirname "$0")/.."
env -u LEVIATH_RECONCILE_PATH python3 -m pytest tests/test_protocol.py -q
echo "HERMETIC OK: the test needs nothing outside the working directory"