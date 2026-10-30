"""Schema-side reader for the typed-code server.

Reuses GQL-02's accepted ``schema_adapter`` (graphql-core) and adds the facts the
typed-code server compares against: a field's declared arguments become
``callable.params``/``callable.returns``/``callable.signature``, and a
``@user_confirmation(by: ...)`` directive becomes ``callable.confirmation``.

Nothing here executes application code: the schema is read from SDL text and the
code side is read with :mod:`ast`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from leviath_reconcile.adapters import schema_adapter
from leviath_reconcile.crosswalk import canonical_type
from leviath_reconcile.model import Fact, Projection, SourceRef

#: The directive that declares a confirmation gate.
CONFIRMATION_DIRECTIVE = "user_confirmation"


@dataclass
class SchemaExtraction:
    """One SDL artifact's extraction, plus the mode it was read in."""

    projection: Projection
    warnings: List[str] = field(default_factory=list)
    mode: str = "graphql-core"

    @property
    def facts(self) -> Tuple[Fact, ...]:
        return self.projection.facts


def _directive_authority(node) -> Optional[str]:
    """The ``by:`` argument of ``@user_confirmation(...)`` on a field, if present."""
    from graphql import print_ast

    for directive in getattr(node, "directives", ()) or ():
        if directive.name.value != CONFIRMATION_DIRECTIVE:
            continue
        for argument in directive.arguments or ():
            if argument.name.value == "by":
                return print_ast(argument.value).strip()
    return None


def _type_text(type_node) -> str:
    from graphql import print_ast

    return print_ast(type_node)


def _callable_facts(node, parent: str, path: str) -> List[Fact]:
    """Argument/return/signature facts for one SDL field."""
    name = node.name.value
    key = f"{parent}.{name}"
    token = getattr(getattr(node, "loc", None), "start_token", None)
    line = token.line if token is not None else 1
    col = token.column if token is not None else 0
    span = SourceRef(path, line, col, col + len(name))

    pieces: List[str] = []
    for argument in node.arguments or ():
        canonical = canonical_type(_type_text(argument.type), "schema")
        # ``partition`` yields (head, separator, tail): the *separator* is the
        # marker.  Reading the tail here would always be empty, silently
        # dropping the nullability marker from every nullable parameter.
        base, marker, _ = canonical.partition("?")
        pieces.append(f"{argument.name.value}:{base}{'?' if marker else ''}")

    returns = canonical_type(_type_text(node.type), "schema")
    facts = [
        Fact(key, "schema", "callable.params", ",".join(pieces), span),
        Fact(key, "schema", "callable.returns", returns, span),
        Fact(key, "schema", "callable.signature", f"({','.join(pieces)})->{returns}", span),
    ]
    authority = _directive_authority(node)
    if authority:
        facts.append(Fact(key, "schema", "callable.confirmation", f"by={authority}", span))
    return facts


def read_sdl(source: str, path: str = "schema.graphql") -> SchemaExtraction:
    """Read SDL text into the shared representation, plus callable facts."""
    base = schema_adapter.extract_sdl(source, path)
    facts: List[Fact] = list(base.projection.facts)

    from graphql import parse
    from graphql.language import ast as gql_ast

    document = parse(source)
    for definition in document.definitions:
        if not isinstance(definition, gql_ast.ObjectTypeDefinitionNode):
            continue
        parent = definition.name.value
        for member in definition.fields or ():
            facts.extend(_callable_facts(member, parent, path))

    projection = Projection(
        name="schema",
        document_identity=base.projection.document_identity,
        facts=tuple(facts),
    )
    return SchemaExtraction(projection=projection, warnings=list(base.warnings), mode=base.mode)


def load_sdl(path: str | Path) -> SchemaExtraction:
    target = Path(path)
    return read_sdl(target.read_text(encoding="utf-8"), str(target))