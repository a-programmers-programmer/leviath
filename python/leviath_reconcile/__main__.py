"""``python -m leviath_reconcile`` entry point.

Kept trivial on purpose: the argument surface and the exit codes live in
:mod:`leviath_reconcile.cli`, so the module form and the console-script form
cannot drift apart.  Without this file ``python -m leviath_reconcile`` fails with
"No module named leviath_reconcile.__main__", which is exactly what the GQL-02
evidence transcript is required to run.
"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
