"""Make the GQL-03 protocol test hermetic.

The acceptance test (``tests/test_protocol.py``) reads the shared reconciliation
engine from ``LEVIATH_RECONCILE_PATH`` and otherwise falls back to the GQL-02
worktree path.  This conftest prefers a read-only copy vendored next to the test,
so the test does not depend on anything outside the working directory.

The test file is not touched: it still holds all of its assertions.
"""

from __future__ import annotations

import os

HERE = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(HERE, "vendor", "python")

# Only fill the variable when the vendored copy is actually present, so an
# explicit LEVIATH_RECONCILE_PATH from the caller always wins.
if os.path.isdir(os.path.join(VENDOR, "leviath_reconcile")):
    os.environ.setdefault("LEVIATH_RECONCILE_PATH", VENDOR)
