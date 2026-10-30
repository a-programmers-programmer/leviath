"""Entry point: ``python -m leviath_schema_ls`` starts the server on stdin/stdout."""

from __future__ import annotations

import sys

from .server import run_stdio


def main(argv=None) -> int:
    """Run the language server on the process's stdin/stdout."""
    return run_stdio(sys.stdin.buffer, sys.stdout.buffer)


if __name__ == "__main__":
    raise SystemExit(main())