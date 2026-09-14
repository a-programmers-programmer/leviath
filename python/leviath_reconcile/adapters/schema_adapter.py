"""Schema projection: GraphQL SDL -> facts, with the pure-SDL fallback.

The adapter maps an SDL document onto the shared representation using the SAME
symbol-key spelling as the code adapter (``Parent.child``, dotted, no generic
spelling) and the same fact vocabulary:

======================  ======  ==================================
attribute               kind    meaning
======================  ======  ==================================
``existence``           type    the symbol exists, and its declaration kind
``annotation.doc``      type    the SDL description string
``type.kind``           type    base type name as written/list/non-null flags
``type.wrapper``        type    ``NON_NULL``/``LIST``/``NONE``
``location.path``       type    file the symbol was read from
``location.line``       type    line of the symbol
``location.parent``     type    containing type (canonicalised by the reconciler)
``location.order``      type    ordinal position among its siblings
``callable.port``       field   the port an object type exposes (interface name)
======================  ======  ==================================

Candidates are spelled exactly like their code counterparts: a field spelled
``Type.field`` in Python is ``Type.field`` here.  That symmetric spelling is why
a rename is one identity move and a revert is a no-op, instead of a delete+add
pair that a merge would report as a conflict.

Also provides a dependency-free :func:`parse_sdl` for the pure-SDL cases (a
fresh checkout, a contract fixture) so the package still works when graphql-core
is absent; the fallback is explicit about its lower fidelity.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ..model import ABSENT, Fact, Projection, SourceRef, digest_facts

#: Annotation/port names that denote a domain port (R1 port direction).  A field
#: typed with one of these is the schema projection's counterpart of a code
#: attribute with the same port annotation.
PORT_ANNOTATIONS = ("WorkflowStepInput", "WorkflowStepOutput")

#: SDL declaration keywords -> the symbol kind recorded in ``existence``.
DECLARATION_KINDS = {
    "type": "type",
    "enum": "enum",
    "interface": "interface",
    "union": "union",
    "scalar": "scalar",
    "input": "input",
    "directive": "directive",
    "schema": "schema",
    "extend": "extend",
}


@dataclass
class Extraction:
    """The result of reading one artifact: facts plus any non-fatal findings."""

    projection: Projection
    warnings: List[str] = field(default_factory=list)
    mode: str = "graphql-core"   # "graphql-core" | "pure-sdl-fallback"


# ---------------------------------------------------------------------------
# graphql-core extraction (preferred).
# ---------------------------------------------------------------------------


def _declaration_kind(node) -> str:
    """The declaration keyword this type-level node came from."""
    from graphql.language import ast as gql_ast

    for node_type, keyword in (
        (gql_ast.ObjectTypeDefinitionNode, "type"),
        (gql_ast.ObjectTypeExtensionNode, "extend"),
        (gql_ast.EnumTypeDefinitionNode, "enum"),
        (gql_ast.InterfaceTypeDefinitionNode, "interface"),
        (gql_ast.UnionTypeDefinitionNode, "union"),
        (gql_ast.ScalarTypeDefinitionNode, "scalar"),
        (gql_ast.InputObjectTypeDefinitionNode, "input"),
        (gql_ast.DirectiveDefinitionNode, "directive"),
        (gql_ast.SchemaDefinitionNode, "schema"),
    ):
        if isinstance(node, node_type):
            return DECLARATION_KINDS[keyword]
    return "other"


def _span(node, doc: str, length: int = 1) -> SourceRef:
    """A source map entry for an AST node, from its start token."""
    loc = getattr(node, "loc", None)
    token = getattr(loc, "start_token", None)
    if token is None:
        return SourceRef(doc, 1, 0, length)
    return SourceRef(doc, token.line, token.column, token.column + length)


def _type_facts(type_node, parent: Optional[str], doc: str, order: int) -> List[Fact]:
    """Facts for one field declaration, using the code-side key spelling."""
    from graphql import print_ast

    name = type_node.name.value
    key = f"{parent}.{name}" if parent else name
    span = _span(type_node.name, doc, len(name))
    line = span.line
    written = print_ast(type_node.type)
    base = re.sub(r"[\[\]!]", "", written)
    wrapper = "NON_NULL" if written.endswith("!") and "[" not in written else (
        "LIST_NON_NULL" if written.endswith("]!") else "LIST" if "[" in written else "NONE"
    )
    facts = [
        Fact(key, "schema", "existence", "field", span),
        Fact(key, "schema", "name", name, span),
        Fact(key, "schema", "location.path", doc, span),
        Fact(key, "schema", "location.line", str(line), span),
        Fact(key, "schema", "type.kind", written, span),
        Fact(key, "schema", "type.wrapper", wrapper, span),
        Fact(key, "schema", "location.order", str(order), span),
        Fact(key, "schema", "annotation.declaration", "field", span),
    ]
    if parent:
        facts.append(Fact(key, "schema", "location.parent", parent, span))
    if base in PORT_ANNOTATIONS:
        facts.append(Fact(key, "schema", "callable.port", base, span))
    description = getattr(type_node, "description", None)
    if description is not None:
        facts.append(Fact(key, "schema", "annotation.doc", description.value, span))
    return facts


def _type_level_facts(node, doc: str, order: int) -> Tuple[List[Fact], List[str]]:
    """Facts for one type-level declaration, plus its member facts."""
    from graphql import print_ast

    name = node.name.value
    kind = _declaration_kind(node)
    span = _span(node.name, doc, len(name))
    facts = [
        Fact(name, "schema", "existence", kind, span),
        Fact(name, "schema", "name", name, span),
        Fact(name, "schema", "location.path", doc, span),
        Fact(name, "schema", "location.line", str(span.line), span),
        Fact(name, "schema", "location.order", str(order), span),
        Fact(name, "schema", "annotation.declaration", kind, span),
    ]
    description = getattr(node, "description", None)
    if description is not None:
        facts.append(Fact(name, "schema", "annotation.doc", description.value, span))

    warnings: List[str] = []
    members = list(getattr(node, "fields", ()) or ())
    for position, member in enumerate(members):
        facts.extend(_type_facts(member, name, doc, position))
    for position, value in enumerate(getattr(node, "values", ()) or ()):
        key = f"{name}.{value.name.value}"
        value_span = _span(value.name, doc, len(value.name.value))
        facts.append(Fact(key, "schema", "existence", "enum_value", value_span))
        facts.append(Fact(key, "schema", "name", value.name.value, value_span))
        facts.append(Fact(key, "schema", "location.path", doc, value_span))
        facts.append(Fact(key, "schema", "location.parent", name, value_span))
        facts.append(Fact(key, "schema", "location.order", str(position), value_span))
        description = getattr(value, "description", None)
        if description is not None:
            facts.append(Fact(key, "schema", "annotation.doc", description.value, value_span))
    for position, member in enumerate(getattr(node, "types", ()) or ()):  # union members
        member_name = member.name.value
        facts.append(
            Fact(f"{name}.{member_name}", "schema", "existence", "union_member", span)
        )
        facts.append(Fact(f"{name}.{member_name}", "schema", "location.parent", name, span))
        facts.append(Fact(f"{name}.{member_name}", "schema", "location.order", str(position), span))
    return facts, warnings


def extract_sdl(source: str, path: str = "schema.graphql") -> Extraction:
    """Extract the shared representation from SDL using graphql-core.

    Raises whatever graphql-core raises for invalid SDL, so a broken document is
    a loud failure rather than an empty projection that the reconciler might read
    as "delete everything".
    """
    from graphql import parse
    from graphql.language import ast as gql_ast

    document = parse(source)
    facts: List[Fact] = []
    warnings: List[str] = []
    top_level = [
        definition
        for definition in document.definitions
        if isinstance(definition, (gql_ast.ObjectTypeDefinitionNode, gql_ast.EnumTypeDefinitionNode,
                                  gql_ast.InterfaceTypeDefinitionNode, gql_ast.UnionTypeDefinitionNode,
                                  gql_ast.ScalarTypeDefinitionNode, gql_ast.InputObjectTypeDefinitionNode,
                                  gql_ast.DirectiveDefinitionNode, gql_ast.SchemaDefinitionNode))
    ]
    for order, node in enumerate(top_level):
        node_facts, node_warnings = _type_level_facts(node, path, order)
        facts.extend(node_facts)
        warnings.extend(node_warnings)

    projection = Projection(name="schema", document_identity=_document_identity(path, source), facts=tuple(facts))
    return Extraction(projection=projection, warnings=warnings, mode="graphql-core")


# ---------------------------------------------------------------------------
# Pure-SDL fallback (no dependencies).
# ---------------------------------------------------------------------------

_DECL_RE = re.compile(r"^\s*(type|enum|interface|union|scalar|input|directive)\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)
_FIELD_RE = re.compile(r"^\s{2,}([A-Za-z_][A-Za-z0-9_]*)\s*(\([^)]*\))?\s*:\s*([^\s#]+)", re.M)
_ENUM_VALUE_RE = re.compile(r"^\s{2,}([A-Z][A-Z0-9_]*)\s*$", re.M)


def parse_sdl(source: str, path: str = "schema.graphql") -> Extraction:
    """Dependency-free SDL extraction.

    Same vocabulary, lower fidelity: descriptions, directives and multi-line
    signatures are not represented, and the result is flagged in ``warnings`` so
    a caller is never told the two modes are equivalent.  Used only when
    graphql-core is unavailable.
    """
    facts: List[Fact] = []
    warnings = ["pure-SDL fallback: descriptions, directives and unions are not extracted"]
    for order, match in enumerate(_DECL_RE.finditer(source)):
        keyword, name = match.group(1), match.group(2)
        line = source[: match.start()].count("\n") + 1
        span = SourceRef(path, line, match.start(2) - source.rfind("\n", 0, match.start(2)) - 1,
                         match.start(2) - source.rfind("\n", 0, match.start(2)) - 1 + len(name))
        kind = DECLARATION_KINDS[keyword]
        facts.extend(
            [
                Fact(name, "schema", "existence", kind, span),
                Fact(name, "schema", "name", name, span),
                Fact(name, "schema", "location.path", path, span),
                Fact(name, "schema", "location.line", str(line), span),
                Fact(name, "schema", "location.order", str(order), span),
            ]
        )
        block = _body_block(source, match.end())
        if keyword in ("type", "interface", "input"):
            for position, field_match in enumerate(_FIELD_RE.finditer(block)):
                field_name, args, field_type = field_match.groups()
                if field_name in DECLARATION_KINDS and not args:
                    continue
                key = f"{name}.{field_name}"
                field_line = line + block[: field_match.start()].count("\n") + 1
                facts.extend(
                    [
                        Fact(key, "schema", "existence", "field", SourceRef(path, field_line, field_match.start(1), field_match.end(1))),
                        Fact(key, "schema", "location.parent", name, SourceRef(path, field_line, field_match.start(1), field_match.end(1))),
                        Fact(key, "schema", "location.order", str(position), SourceRef(path, field_line, field_match.start(1), field_match.end(1))),
                        Fact(key, "schema", "type.kind", field_type, SourceRef(path, field_line, field_match.start(3), field_match.end(3))),
                    ]
                )
        elif keyword == "enum":
            for position, value_match in enumerate(_ENUM_VALUE_RE.finditer(block)):
                value = value_match.group(1)
                key = f"{name}.{value}"
                facts.extend(
                    [
                        Fact(key, "schema", "existence", "enum_value", SourceRef(path, line, value_match.start(1), value_match.end(1))),
                        Fact(key, "schema", "location.parent", name, SourceRef(path, line, value_match.start(1), value_match.end(1))),
                        Fact(key, "schema", "location.order", str(position), SourceRef(path, line, value_match.start(1), value_match.end(1))),
                    ]
                )
    projection = Projection(name="schema", document_identity=_document_identity(path, source), facts=tuple(facts))
    return Extraction(projection=projection, warnings=warnings, mode="pure-sdl-fallback")


def _body_block(source: str, start: int) -> str:
    """The braced body of a declaration beginning at/after ``start``."""
    open_brace = source.find("{", start)
    if open_brace == -1:
        return ""
    depth = 0
    for index in range(open_brace, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[open_brace + 1: index]
    return source[open_brace + 1:]


# ---------------------------------------------------------------------------
# Document identity and the contract fingerprint gate.
# ---------------------------------------------------------------------------


def _document_identity(path: str, source: str) -> str:
    import hashlib

    return hashlib.sha256((path + "\x00" + source).encode("utf-8")).hexdigest()


def load_sdl(path: str | Path, *, parse_document: bool = True) -> Extraction:
    """Read and extract an SDL file, preferring graphql-core."""
    target = Path(path)
    source = target.read_text(encoding="utf-8")
    if parse_document:
        try:
            return extract_sdl(source, str(target))
        except ImportError:  # pragma: no cover - environment without graphql-core
            return parse_sdl(source, str(target))
    return parse_sdl(source, str(target))


# ---------------------------------------------------------------------------
# Contract fingerprint pinning / re-verification.
# ---------------------------------------------------------------------------


class ContractDrift(RuntimeError):
    """The accepted contract fingerprint is not the one on disk."""


#: Artifacts that make up the semantic contract, in fingerprint order.
CONTRACT_ARTIFACTS = ("schema-workflow.graphql", "edge-invariant-rules.json")


def contract_fingerprint(contract_dir: str | Path, *, sdl_path: Optional[str | Path] = None) -> str:
    """The **pinned** contract identity: SHA-256 of the *printed* SDL.

    This is the value GQL-01 accepted and published in
    ``docs/workflow/workflow-schema-decisions.md`` and in
    ``docs/workflow/check-workflow-contract.py`` (which prints it as
    ``fingerprint`` and re-verifies it with ``--expected-fingerprint``).
    ``check-workflow-contract.py`` computes it as
    ``sha256(print_schema(build_schema(sdl)))``, so it is a *semantic* digest:
    comments, formatting and whitespace do not churn it, any semantic change
    does.  Mirroring exactly that is the point — a dependent worker must be able
    to re-verify the accepted fingerprint with this package and with GQL-01's
    gate and get the **same bytes**, or the pin means nothing.

    ``contract_artifact_fingerprint`` below is the coarser file-set identity; it
    is reported alongside as provenance, never as the accepted identity.
    """
    directory = Path(contract_dir)
    target = Path(sdl_path) if sdl_path is not None else directory / "schema-workflow.graphql"
    if not target.is_file():
        raise ContractDrift(f"contract SDL not found: {target}")
    from graphql import build_schema, print_schema

    try:
        schema = build_schema(target.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        raise ContractDrift(f"accepted SDL does not parse/build: {exc}") from exc
    return hashlib.sha256(print_schema(schema).encode("utf-8")).hexdigest()


def contract_artifact_fingerprint(contract_dir: str | Path) -> str:
    """File-set identity of the contract artifact set (provenance, not the pin).

    Per-artifact SHA-256, then SHA-256 of the canonical JSON of
    ``{name: digest}`` — the convention recorded in
    ``crates/leviath-cli/agents/graphql-schema/scripts/gate.py``.  It is *not*
    the accepted fingerprint GQL-01 published (that one is semantic, see
    :func:`contract_fingerprint`), so it is reported separately.
    """
    directory = Path(contract_dir)
    hashes = {}
    for name in CONTRACT_ARTIFACTS:
        file_path = directory / name
        if file_path.is_file():
            hashes[name] = hashlib.sha256(file_path.read_bytes()).hexdigest()
    canonical = json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def verify_contract(
    expected_fingerprint: str,
    contract_dir: str | Path,
    *,
    sdl_path: Optional[str | Path] = None,
) -> str:
    """Pin-then-re-verify the accepted contract.  Returns the observed fingerprint.

    Raises :class:`ContractDrift` when the observed fingerprint differs from the
    pinned one, and when the SDL does not parse or build.
    """
    observed = contract_fingerprint(contract_dir, sdl_path=sdl_path)
    if observed != expected_fingerprint:
        raise ContractDrift(
            f"contract drift: expected {expected_fingerprint}, observed {observed}"
        )
    target = Path(sdl_path) if sdl_path is not None else Path(contract_dir) / "schema-workflow.graphql"
    if target.is_file():
        from graphql import build_schema, validate_schema

        try:
            schema = build_schema(target.read_text(encoding="utf-8"))
            errors = validate_schema(schema)
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            raise ContractDrift(f"accepted SDL does not build: {exc}") from exc
        if errors:
            raise ContractDrift(f"accepted SDL fails validate_schema: {errors[0]}")
    return observed
