"""Adapters: turn a real artifact into the shared structured domain representation.

Two adapters, one shape, symmetric in both directions:

``schema_adapter``
    Reads GraphQL SDL with graphql-core's own parser.  Also re-verifies the
    accepted ``workflow-model v1`` contract fingerprint before any extraction, so
    a drifted GQL-01 contract blocks reconciliation instead of being merged as if
    it were the accepted model.

``python_adapter``
    Reads typed Python source with :mod:`ast` only — it never imports, never
    executes and never evaluates the application, which is the rule GQL-04
    inherits ("do not execute arbitrary application imports to infer the
    schema").  A dynamic construct that cannot be read statically is a
    ``WARN``-level finding, not a silent omission.

Both adapters emit the same fact vocabulary over the SAME symbol-key namespace
(``Parent.child``, dotted), so a fact from either side is directly comparable
with its counterpart from the other.
"""

from __future__ import annotations

from . import python_adapter, schema_adapter

__all__ = ["schema_adapter", "python_adapter"]
