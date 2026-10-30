"""The contract model the code generation hook reads and emits from.

The hook's input is the **reconciled contract snapshot**: the accepted SDL plus
the decision log that carries the pin.  This module loads that snapshot, verifies
the pin, and turns the schema into the deterministic structure the renderers
consume:

``Contract``
    The built schema, the canonical printed SDL, its fingerprint, the operations
    and the contract line map for the source map.
``Operation``
    One operation document per root field, with its variables and its selection
    tree.
``Selection``
    The response shape of one field, so both language renderers emit the same
    tree.

Nothing here calls a provider, touches the network, reads a secret or writes to a
store.  A drifted snapshot raises :class:`ContractDrift` before any output is
produced.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from graphql import (
    GraphQLNonNull,
    GraphQLSchema,
    Undefined,
    build_schema,
    is_enum_type,
    is_interface_type,
    is_object_type,
    is_union_type,
    parse,
    print_schema,
)

from leviath_reconcile import pin as reconcile_pin

#: The generator version, recorded in the manifest.  The single source of truth;
#: ``leviath_codegen.__init__`` re-exports it.
VERSION = "1.0.0"

#: The contract artifact names inside a snapshot directory.
CONTRACT_SDL_NAME = "schema-workflow.graphql"
CONTRACT_RULES_NAME = "edge-invariant-rules.json"
CONTRACT_DECISIONS_NAME = "workflow-schema-decisions.md"

#: How deep a generated selection expands.  A composite field always keeps
#: ``__typename``, so a capped depth still yields a valid document.
MAX_SELECTION_DEPTH = 2

#: GraphQL built-in scalars mapped onto Python builtins.
PYTHON_BUILTIN_SCALARS = {
    "String": ("String", "str"),
    "Int": ("Int", "int"),
    "Float": ("Float", "float"),
    "Boolean": ("Boolean", "bool"),
    "ID": ("ID", "str"),
}

#: GraphQL custom scalars keep a named alias, so the mapping stays visible.
PYTHON_CUSTOM_SCALARS = {
    "DateTime": "DateTime",
    "Duration": "Duration",
    "Decimal": "Decimal",
    "URI": "URI",
    "Confidential": "Confidential",
    "PortValue": "PortValue",
}

#: TypeScript maps every contract scalar onto a named alias.
TYPESCRIPT_SCALARS = {
    "String": "String",
    "Int": "Int",
    "Float": "Float",
    "Boolean": "Boolean",
    "ID": "ID",
    "DateTime": "DateTime",
    "Duration": "Duration",
    "Decimal": "Decimal",
    "URI": "URI",
    "Confidential": "Confidential",
    "PortValue": "PortValue",
}


class ContractDrift(RuntimeError):
    """The contract snapshot is not the accepted, pinned contract."""


# ---------------------------------------------------------------------------
# Name helpers.
# ---------------------------------------------------------------------------


def pascal(name: str) -> str:
    """``searchWorkflows`` -> ``SearchWorkflows``."""
    return name[:1].upper() + name[1:]


_CAMEL_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])")


def snake(name: str) -> str:
    """``runWorkflowStep`` -> ``run_workflow_step``."""
    return _CAMEL_BOUNDARY.sub(r"\1_\2", name).lower()


# ---------------------------------------------------------------------------
# Selection tree.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Fragment:
    """One union member's inline fragment and its children."""

    name: str
    children: Tuple["Selection", ...] = ()


@dataclass(frozen=True)
class Selection:
    """The response shape of one selected field."""

    name: str
    graphql_type: str                       # as written, e.g. "[WorkflowStep!]!"
    named_type: str                         # unwrapped name
    kind: str                               # scalar|enum|object|interface|union
    children: Tuple["Selection", ...] = ()
    fragments: Tuple[Fragment, ...] = ()
    args: Tuple[Tuple[str, str, bool], ...] = ()   # (name, type, required)

    @property
    def is_leaf(self) -> bool:
        return self.kind in ("scalar", "enum")

    @property
    def is_composite(self) -> bool:
        return not self.is_leaf


@dataclass(frozen=True)
class Variable:
    """One operation variable, derived from a root field argument."""

    name: str
    graphql_type: str
    required: bool


@dataclass(frozen=True)
class Operation:
    """One generated operation: a query or mutation over one root field."""

    name: str
    kind: str                               # "query" | "mutation"
    root_field: str
    document: str
    variables: Tuple[Variable, ...]
    selection: Selection
    contract_line: int

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "root_field": self.root_field,
            "contract_line": self.contract_line,
            "variables": [
                {"name": v.name, "type": v.graphql_type, "required": v.required}
                for v in self.variables
            ],
        }


@dataclass(frozen=True)
class Contract:
    """The loaded, pin-verified contract snapshot."""

    contract_dir: str
    sdl_path: str
    sdl_text: str
    printed_sdl: str
    fingerprint: str
    pinned_fingerprint: str
    schema: GraphQLSchema = field(repr=False, compare=False)
    operations: Tuple[Operation, ...] = ()
    contract_lines: Mapping[str, int] = field(default_factory=dict)
    gated_fields: Tuple[Tuple[str, str], ...] = ()       # (symbol, authority)
    port_implementations: Tuple[Tuple[str, str], ...] = ()   # (port, type)
    rules: Mapping[str, object] = field(default_factory=dict)
    scalars: Tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "contract_dir": self.contract_dir,
            "sdl": CONTRACT_SDL_NAME,
            "fingerprint": self.fingerprint,
            "pinned_fingerprint": self.pinned_fingerprint,
            "operations": [op.as_dict() for op in self.operations],
            "scalars": list(self.scalars),
            "gated_fields": [{"symbol": s, "authority": a} for s, a in self.gated_fields],
            "port_implementations": [
                {"port": p, "type": t} for p, t in self.port_implementations
            ],
        }


# ---------------------------------------------------------------------------
# Loading and pin verification.
# ---------------------------------------------------------------------------


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def verify_contract_pin(
    contract_dir: str | Path,
    *,
    sdl_path: Optional[str | Path] = None,
    expected_fingerprint: Optional[str] = None,
) -> Tuple[str, str]:
    """Re-verify the accepted contract pin.  Returns ``(pinned, observed)``.

    Reuses the accepted GQL-02 pin: the fingerprint is read from the decision log
    in the snapshot and cross-checked against the carried constant, then the SDL
    on disk is re-verified against it.  Any drift raises :class:`ContractDrift`,
    so a stale contract identity is rejected instead of being generated from.
    """
    directory = Path(contract_dir)
    host = directory / CONTRACT_DECISIONS_NAME
    target = Path(sdl_path) if sdl_path is not None else directory / CONTRACT_SDL_NAME
    try:
        report = reconcile_pin.verify_pin(directory, sdl_path=target, host=host)
    except reconcile_pin.ContractPinMismatch as exc:
        raise ContractDrift(str(exc)) from exc
    if expected_fingerprint and expected_fingerprint != report.pinned:
        raise ContractDrift(
            f"expected fingerprint {expected_fingerprint} is not the pinned "
            f"contract {report.pinned}"
        )
    return report.pinned, report.observed


def verify_rules_agreement(schema: GraphQLSchema, rules: Mapping[str, object]) -> List[str]:
    """Cross-check the edge-invariant rule table against the SDL enums.

    The rule table is part of the accepted contract.  It must name only rules the
    SDL declares, only severities the SDL declares and only enforcement locations
    the SDL declares.  ``waivable`` must be true exactly for ``WARN``.
    """
    problems: List[str] = []
    rule_enum = schema.type_map.get("EdgeInvariantRule")
    severity_enum = schema.type_map.get("EdgeInvariantSeverity")
    enforcement_enum = schema.type_map.get("InvariantEnforcement")
    for entry in rules.get("rules", ()):  # type: ignore[union-attr]
        name = entry.get("rule")
        severity = entry.get("severity")
        if rule_enum is None or name not in rule_enum.values:  # type: ignore[union-attr]
            problems.append(f"rule {name!r} is not in the SDL EdgeInvariantRule enum")
        if severity_enum is None or severity not in severity_enum.values:  # type: ignore[union-attr]
            problems.append(f"severity {severity!r} is not in the SDL EdgeInvariantSeverity enum")
        if bool(entry.get("waivable")) != (severity == "WARN"):
            problems.append(f"rule {name!r}: waivable must be true exactly for WARN")
        for where in entry.get("enforcedAt", ()):
            if enforcement_enum is None or where not in enforcement_enum.values:  # type: ignore[union-attr]
                problems.append(f"enforcement {where!r} is not in the SDL InvariantEnforcement enum")
    if not rules.get("rules"):
        problems.append("the edge-invariant rule table declares no rules")
    return problems


def _contract_lines(sdl_text: str) -> Dict[str, int]:
    """Map each declared symbol to its line in the accepted SDL text."""
    lines: Dict[str, int] = {}
    document = parse(sdl_text)
    for definition in document.definitions:
        name_node = getattr(definition, "name", None)
        if name_node is None:
            continue
        name = name_node.value
        start = getattr(getattr(definition, "loc", None), "start_token", None)
        if start is not None:
            lines[name] = start.line
        for member in list(getattr(definition, "fields", ()) or ()):
            member_start = getattr(getattr(member, "loc", None), "start_token", None)
            if member_start is not None:
                lines[f"{name}.{member.name.value}"] = member_start.line
        for value in list(getattr(definition, "values", ()) or ()):
            value_start = getattr(getattr(value, "loc", None), "start_token", None)
            if value_start is not None:
                lines[f"{name}.{value.name.value}"] = value_start.line
    return lines


def _gated_fields(sdl_text: str) -> Tuple[Tuple[str, str], ...]:
    """Fields that declare the confirmation gate, with their authority."""
    out: List[Tuple[str, str]] = []
    document = parse(sdl_text)
    for definition in document.definitions:
        parent = getattr(getattr(definition, "name", None), "value", None)
        if parent is None:
            continue
        for member in list(getattr(definition, "fields", ()) or ()):
            for directive in list(getattr(member, "directives", ()) or ()):
                if directive.name.value != "user_confirmation":
                    continue
                authority = ""
                for argument in directive.arguments:
                    if argument.name.value == "by":
                        authority = getattr(argument.value, "value", "")
                out.append((f"{parent}.{member.name.value}", authority))
    return tuple(out)


def _port_implementations(schema: GraphQLSchema) -> Tuple[Tuple[str, str], ...]:
    """Every object type that implements a domain port, in SDL order."""
    out: List[Tuple[str, str]] = []
    for name, type_ in schema.type_map.items():
        if name.startswith("__") or not is_object_type(type_):
            continue
        for interface in type_.interfaces:
            if interface.name in ("WorkflowStepInput", "WorkflowStepOutput"):
                out.append((interface.name, name))
    return tuple(out)


def _named_type(graphql_type) -> str:
    while hasattr(graphql_type, "of_type"):
        graphql_type = graphql_type.of_type
    return graphql_type.name


def _type_kind(schema: GraphQLSchema, name: str) -> str:
    type_ = schema.type_map.get(name)
    if type_ is None:
        return "scalar"
    if is_enum_type(type_):
        return "enum"
    if is_union_type(type_):
        return "union"
    if is_interface_type(type_):
        return "interface"
    if is_object_type(type_):
        return "object"
    return "scalar"


def _has_required_args(field) -> bool:
    for argument in field.args.values():
        if isinstance(argument.type, GraphQLNonNull) and argument.default_value is Undefined:
            return True
    return False


def _children_for(
    schema: GraphQLSchema, type_name: str, depth: int
) -> Tuple[Tuple[Selection, ...], Tuple[Fragment, ...]]:
    """The children of a composite field, honouring the depth cap.

    A field with a required argument is skipped: the generated document carries no
    argument value for it, and an unset required argument would make the document
    invalid.  Skipping keeps the generated operations valid against the accepted
    contract, which is the point of the agreement check.
    """
    type_ = schema.type_map.get(type_name)
    if type_ is None or depth >= MAX_SELECTION_DEPTH:
        return (), ()
    if is_union_type(type_):
        fragments = tuple(
            Fragment(member.name, _children_for(schema, member.name, depth + 1)[0])
            for member in type_.types
        )
        return (), fragments
    if is_object_type(type_) or is_interface_type(type_):
        children: List[Selection] = []
        for field_name, field in type_.fields.items():
            if field_name.startswith("__") or _has_required_args(field):
                continue
            children.append(_selection_for_field(schema, field_name, field.type, depth + 1, {}))
        return tuple(children), ()
    return (), ()


def _selection_for_field(schema: GraphQLSchema, name: str, graphql_type, depth: int, args) -> Selection:
    """One selection node: its written type, its kind and its children."""
    written = str(graphql_type)
    named = _named_type(graphql_type)
    kind = _type_kind(schema, named)
    children, fragments = _children_for(schema, named, depth)
    return Selection(
        name=name,
        graphql_type=written,
        named_type=named,
        kind=kind,
        children=children,
        fragments=fragments,
        args=tuple(
            (
                arg_name,
                str(argument.type),
                isinstance(argument.type, GraphQLNonNull) and argument.default_value is Undefined,
            )
            for arg_name, argument in args.items()
        ),
    )


def _render_selection(selection: Selection, indent: int) -> List[str]:
    pad = "  " * indent
    arguments = ""
    if selection.args:
        arguments = "(" + ", ".join(f"{name}: ${name}" for name, _, _ in selection.args) + ")"
    if selection.is_leaf:
        return [f"{pad}{selection.name}{arguments}"]
    lines = [f"{pad}{selection.name}{arguments} {{", f"{pad}  __typename"]
    for child in selection.children:
        lines.extend(_render_selection(child, indent + 1))
    for fragment in selection.fragments:
        lines.append(f"{pad}  ... on {fragment.name} {{")
        if not fragment.children:
            # A GraphQL fragment must select at least one field.  Only
            # ``__typename`` is always legal, so an empty fragment still yields a
            # valid document against the accepted contract.
            lines.append(f"{pad}    __typename")
        for child in fragment.children:
            lines.extend(_render_selection(child, indent + 2))
        lines.append(f"{pad}  }}")
    lines.append(f"{pad}}}")
    return lines


def _build_operations(schema: GraphQLSchema, contract_lines: Mapping[str, int]) -> Tuple[Operation, ...]:
    roots: List[Tuple[str, str, object]] = []
    if schema.query_type is not None:
        roots.extend(("query", name, field) for name, field in schema.query_type.fields.items())
    if schema.mutation_type is not None:
        roots.extend(("mutation", name, field) for name, field in schema.mutation_type.fields.items())

    operations: List[Operation] = []
    for kind, field_name, field in roots:
        selection = _selection_for_field(schema, field_name, field.type, 0, field.args)
        variables = tuple(
            Variable(name=name, graphql_type=graphql_type, required=required)
            for name, graphql_type, required in selection.args
        )
        header = f"{kind} {pascal(field_name)}"
        if variables:
            header += "(" + ", ".join(f"${v.name}: {v.graphql_type}" for v in variables) + ")"
        body = _render_selection(selection, 1)
        document = "\n".join([header + " {"] + body + ["}"])
        operations.append(
            Operation(
                name=pascal(field_name),
                kind=kind,
                root_field=field_name,
                document=document,
                variables=variables,
                selection=selection,
                contract_line=contract_lines.get(field_name, 1),
            )
        )
    return tuple(operations)


def load_contract(
    contract_dir: str | Path,
    *,
    sdl_path: Optional[str | Path] = None,
    expected_fingerprint: Optional[str] = None,
) -> Contract:
    """Load and pin-verify a reconciled contract snapshot.

    Raises :class:`ContractDrift` when the pin does not match, when the SDL does
    not parse or build, or when the rule table disagrees with the SDL enums.
    """
    directory = Path(contract_dir)
    target = Path(sdl_path) if sdl_path is not None else directory / CONTRACT_SDL_NAME
    if not target.is_file():
        raise ContractDrift(f"contract SDL not found: {target}")

    pinned, observed = verify_contract_pin(
        directory, sdl_path=target, expected_fingerprint=expected_fingerprint
    )

    sdl_text = _read_text(target)
    try:
        schema = build_schema(sdl_text)
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        raise ContractDrift(f"accepted SDL does not parse/build: {exc}") from exc
    printed = print_schema(schema)
    computed = hashlib.sha256(printed.encode("utf-8")).hexdigest()
    if computed != pinned:
        raise ContractDrift(
            f"printed SDL fingerprint {computed} is not the pinned {pinned}"
        )

    rules_path = directory / CONTRACT_RULES_NAME
    rules: Mapping[str, object] = {}
    if rules_path.is_file():
        try:
            rules = json.loads(_read_text(rules_path))
        except json.JSONDecodeError as exc:
            raise ContractDrift(f"rule table {rules_path} is not valid JSON: {exc}") from exc
        problems = verify_rules_agreement(schema, rules)
        if problems:
            raise ContractDrift("rule table disagrees with the SDL: " + "; ".join(problems))

    contract_lines = _contract_lines(sdl_text)
    scalars = tuple(
        name
        for name, type_ in schema.type_map.items()
        if not name.startswith("__") and name in TYPESCRIPT_SCALARS
    )
    return Contract(
        contract_dir=str(directory),
        sdl_path=str(target),
        sdl_text=sdl_text,
        printed_sdl=printed,
        fingerprint=computed,
        pinned_fingerprint=pinned,
        schema=schema,
        operations=_build_operations(schema, contract_lines),
        contract_lines=contract_lines,
        gated_fields=_gated_fields(sdl_text),
        port_implementations=_port_implementations(schema),
        rules=rules,
        scalars=scalars,
    )


def scalar_mapping(contract: Contract) -> Dict[str, Dict[str, str]]:
    """The scalar mapping table recorded in the source map.

    Built-in GraphQL scalars map onto Python builtins.  Custom scalars keep a
    named alias, so the mapping stays visible in the generated code.
    """
    table: Dict[str, Dict[str, str]] = {}
    for name in contract.scalars:
        if name in PYTHON_BUILTIN_SCALARS:
            alias, resolved = PYTHON_BUILTIN_SCALARS[name]
        elif name in PYTHON_CUSTOM_SCALARS:
            alias = resolved = PYTHON_CUSTOM_SCALARS[name]
        else:
            alias = resolved = name
        table[name] = {
            "graphql": name,
            "python": resolved,
            "python_alias": alias,
            "typescript": TYPESCRIPT_SCALARS.get(name, name),
        }
    return table