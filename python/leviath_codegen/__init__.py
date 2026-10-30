"""leviath_codegen — the simple code generation hook (GQL-08).

One deterministic command turns the **reconciled contract snapshot** into typed
Python and TypeScript: interfaces/stubs, operation clients, scalar mappings and
source maps.  Handwritten behavior lives outside the generated files, so it
survives regeneration.

    PYTHONPATH=python python3 -m leviath_codegen generate --contract <dir> --out <dir>

The hook reuses the accepted upstream code: the contract pin and the schema
adapter come from :mod:`leviath_reconcile` (GQL-02).  Nothing here calls a
provider, touches the network, reads a secret or mutates a production store.
Rollback is deleting this one package plus any generated output directory.
"""

from __future__ import annotations

__all__ = [
    "VERSION",
    "CONTRACT",
    "model",
    "render",
    "generate",
    "verify",
    "cli",
    "main",
]

VERSION = "1.0.0"
CONTRACT = "workflow-model v1"

def __getattr__(name: str):
    """Lazily expose the submodules in ``__all__``.

    Import-on-attribute keeps ``import leviath_codegen`` cheap and keeps the
    package importable when an optional dependency is absent.  It also makes
    ``from leviath_codegen import generate`` return the **module**, which is what
    the acceptance tests and the CLI expect.
    """
    if name in __all__ and name not in ("VERSION", "CONTRACT", "main"):
        import importlib

        module = importlib.import_module(f".{name}", __name__)
        globals()[name] = module
        return module
    if name == "main":
        from .cli import main as _main

        globals()["main"] = _main
        return _main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")