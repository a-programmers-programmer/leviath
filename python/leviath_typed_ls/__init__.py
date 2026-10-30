"""Typed-code language server (GQL-04).

The second language-server process: it reads typed Python **statically**
(``ast`` only — never an import, exec or eval of the artifact) and reconciles it
with the GraphQL schema projection through GQL-02's accepted engine.  It reuses,
never re-implements, ``leviath_reconcile``: the accepted ``python_adapter``,
``schema_adapter``, ``crosswalk`` and ``model``.

Modules
-------
``typed_code_adapter``
    Declared callable ports, ``callable.params``/``callable.returns``/
    ``callable.signature``, confirmation wrappers, and the explicit
    ``UnsupportedConstruct`` findings.
``schema_reader``
    SDL -> facts, plus the same callable facts on the schema side.
``projection``
    The one bridge between the two media: code facts relabelled as schema facts
    and printed as SDL.
``codegen``
    The other half of the round trip: a schema edit -> concrete, applicable code
    edits with source spans.
``gate``
    Contract pin re-verification and the obligation block.

Run the server with ``python -m leviath_typed_ls serve``.
"""

from __future__ import annotations

# Import order follows the dependency direction so a partially-initialised
# package never sees a missing attribute.
from . import projection, schema_reader, typed_code_adapter  # noqa: F401
from . import codegen, gate  # noqa: F401

__all__ = [
    "projection",
    "schema_reader",
    "typed_code_adapter",
    "codegen",
    "gate",
]

__version__ = "0.1.0"