"""GraphQL-side language server for Leviath (GQL-03).

The server speaks LSP over stdin/stdout.  It turns the shared reconciliation
engine (:mod:`leviath_reconcile`, GQL-02) into editor feedback:

* **diagnostics** — workflow semantic errors and confirmation requirements,
  each carrying a *source range* read from the SDL text;
* **code actions** — versioned, text-only quick fixes for a conflict;
* **stale-edit rejection** — a document version the server has already
  superseded is refused with a JSON-RPC error.

Safety: a diagnostic never performs a runtime action and never unwraps a
sensitive value.  A code action is a pure ``TextEdit``; the server never emits
a ``command`` and never executes anything.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]