"""leviath_reconcile — shared reconciliation rules and conflict detection.

Work order GQL-02 (directive 0070).  Builds on the accepted ``workflow-model v1``
contract from GQL-01 (``docs/workflow/schema-workflow.graphql``, fingerprint
``ff9df4e32b05747ee3605042407dcc7e17a347f41bc1464ce567c2f484813559``).

The package implements one shared, structured domain representation with stable
symbol identity and code/schema source maps; compares both current projections
against their last agreed version; auto-applies compatible edits; and represents
competing changes as actionable conflicts.  The definition of done it serves:

* schema-only and code-only edits work symmetrically;
* concurrent rename/delete/type edits preserve both versions and produce
  actionable conflicts;
* stale document versions cannot overwrite newer work;
* repeated reconciliation reaches an unchanged result without ping-pong.

Nothing here calls a provider, touches the network, reads a secret or mutates a
production store.  Rollback is deleting this one package.
"""

from __future__ import annotations

__all__ = [
    "VERSION",
    "CONTRACT",
    "model",
    "identity",
    "diff",
    "crosswalk",
    "reconcile",
    "baseline",
    "invariants",
    "pin",
    "adapters",
    "scenarios",
    "cli",
    "main",
]

VERSION = "1.0.0"
CONTRACT = "workflow-model v1"


def __getattr__(name: str):
    """Lazily expose the submodules in ``__all__``.

    Import-on-attribute rather than import-at-module-scope keeps ``import
    leviath_reconcile`` cheap and, more importantly, keeps the package
    importable when an *optional* dependency (graphql-core, needed only by
    ``adapters.schema_adapter``) is absent: the pure-SDL fallback path and the
    definition-of-done scenarios must still work.
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
