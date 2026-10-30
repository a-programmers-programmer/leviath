"""Read-only fact driver for the accepted Python typed-code adapter.

The accepted Python language server (``python -m leviath_typed_ls serve``) does
**not** expose source spans: it answers ``leviath/schemaProjection`` (SDL text)
and ``leviath/codeEdits``.  Cross-language navigation needs the span that
produced each fact, so this driver calls the accepted adapter interface
directly, read-only:

    leviath_typed_ls.typed_code_adapter.extract_typed(source, path)

It never imports, executes or evaluates the analysed artifact: ``extract_typed``
is static (``ast`` only).  Nothing here writes anything.

Protocol
--------
One JSON request per line on stdin, one JSON reply per line on stdout::

    {"source": "<text>", "path": "app/ports.py"}
    -> {"facts": [{"k", "a", "v", "p", "line", "cs", "ce"}], "blocked", ...}

``line`` is 1-based; ``cs`` (col_start) and ``ce`` (col_end) are 0-based, exactly
as the accepted ``SourceRef`` stores them.  The line protocol is deliberate: the
session spawns this driver once and keeps it for the whole session.
"""

from __future__ import annotations

import json
import sys


def _reply(source: str, path: str) -> dict:
    from leviath_typed_ls import typed_code_adapter

    extraction = typed_code_adapter.extract_typed(source, path)
    facts = []
    for fact in extraction.projection.facts:
        span = fact.source
        facts.append(
            {
                "k": fact.symbol_key,
                "a": fact.attribute,
                "v": fact.value,
                "p": span.path if span else path,
                "line": span.line if span else 1,
                "cs": span.col_start if span else 0,
                "ce": span.col_end if span else 0,
            }
        )
    return {
        "facts": facts,
        "blocked": extraction.blocked,
        "mode": extraction.mode,
        "unsupported": [finding.as_dict() for finding in extraction.unsupported],
    }


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            reply = _reply(str(request.get("source", "")), str(request.get("path", "module.py")))
        except Exception as exc:  # noqa: BLE001 - reported to the caller, not swallowed
            reply = {"error": f"{type(exc).__name__}: {exc}"}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())