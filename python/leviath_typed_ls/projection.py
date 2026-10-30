"""Projection bridge: typed-code facts <-> schema facts <-> SDL text.

The two projections of GQL-02 share one symbol-key namespace and one fact
vocabulary, but they are *different media*: a code fact says ``str`` where its
schema counterpart says ``ID!``.  This module is the one place that translates
between them, so the language server can answer "what would the schema look like
if this Python file were the source of truth?" without a second, drifting notion
of the model.

* :func:`code_facts_to_schema_facts` re-labels code facts as schema facts, using
  the accepted crosswalk to canonicalise existence roles and types.
* :func:`render_sdl_projection` prints those facts as SDL, so an editor can show
  the projection a Python edit produced.
* :func:`schema_facts_to_code_type` is the inverse spelling, used by codegen.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from leviath_reconcile.crosswalk import cross_value, existence_role
from leviath_reconcile.model import Fact

#: Canonical type -> SDL spelling.
SDL_TYPE_NAMES: Dict[str, str] = {
    "string": "String",
    "integer": "Int",
    "number": "Float",
    "boolean": "Boolean",
    "datetime": "DateTime",
    "duration": "Duration",
    "decimal": "Decimal",
    "uri": "URI",
    "uuid": "UUID",
    "confidential": "Confidential",
    "json-pointer": "JsonPointer",
    "bytes": "Bytes",
    "object": "PortValue",
    "any": "PortValue",
}

#: Canonical type -> typed-Python spelling.
CODE_TYPE_NAMES: Dict[str, str] = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "datetime": "datetime",
    "duration": "timedelta",
    "decimal": "Decimal",
    "uri": "AnyUrl",
    "uuid": "UUID",
    "confidential": "Confidential",
    "json-pointer": "str",
    "bytes": "bytes",
    "object": "object",
    "any": "Any",
}

#: Existence roles that declare a type (they head a block in SDL).
TYPE_ROLES = frozenset({"type", "interface", "enum", "union", "scalar", "input"})

#: Fact attributes that survive the code->schema relabelling unchanged.
SHARED_ATTRIBUTES = (
    "name",
    "callable.port",
    "callable.params",
    "callable.returns",
    "callable.signature",
    "callable.confirmation",
)


def code_facts_to_schema_facts(facts: Iterable[Fact]) -> Tuple[Fact, ...]:
    """Re-label code facts as schema facts through the accepted crosswalk.

    ``existence`` becomes the media-neutral role and ``type.kind`` becomes the
    canonical type, so a code fact is directly comparable with — and renderable
    as — its schema counterpart.  Local attributes (``location.*``,
    ``annotation.*``) keep their values: they are legitimately media-specific.
    """
    out: List[Fact] = []
    for fact in facts:
        if fact.attribute == "existence":
            value = existence_role(fact.projection, fact.value)
        elif fact.attribute.startswith("type."):
            value = cross_value(fact.projection, fact.attribute, fact.value)
        else:
            value = fact.value
        out.append(Fact(fact.symbol_key, "schema", fact.attribute, value, fact.source, fact.confidence))
    return tuple(out)


def _split_canonical(canonical: str) -> Tuple[str, bool, bool]:
    """``'string[]?'`` -> ``('string', required=False, is_list=True)``."""
    text = canonical
    nullable = text.endswith("?")
    if nullable:
        text = text[:-1]
    is_list = text.endswith("[]")
    if is_list:
        text = text[:-2]
    return text, not nullable, is_list


def crosswalk_canonical(spelled: str) -> str:
    """A canonical param spelling (``'confidential?'``) -> canonical base type."""
    base, _, _ = _split_canonical(spelled)
    return base


def schema_facts_to_code_type(canonical: str) -> str:
    """Canonical type -> typed-Python annotation spelling.

    Nullability is spelled ``X | None`` (the code media's optional form) and a
    list is ``list[X]``, so the result is a concrete, applicable code edit rather
    than a pretty name.
    """
    base, required, is_list = _split_canonical(canonical)
    name = CODE_TYPE_NAMES.get(base, base)
    if is_list:
        name = f"list[{name}]"
    return name if required else f"{name} | None"


def sdl_type_spelling(canonical: str) -> str:
    """Canonical type -> SDL type spelling, with ``!`` when required."""
    base, required, is_list = _split_canonical(canonical)
    name = SDL_TYPE_NAMES.get(base, base)
    if is_list:
        return f"[{name}!]!" if required else f"[{name}!]"
    return f"{name}!" if required else name


def _index(facts: Sequence[Fact]) -> Dict[Tuple[str, str], Fact]:
    return {(f.symbol_key, f.attribute): f for f in facts}


def render_sdl_projection(facts: Iterable[Fact]) -> str:
    """Print a fact set as an SDL projection.

    Accepts either projection's facts: code facts are relabelled first, so a
    caller can render "the schema this Python file implies" in one call.  Output
    is deterministic (types and members sorted by key), so it is safe to diff.
    """
    rows = tuple(facts)
    if rows and rows[0].projection != "schema":
        rows = code_facts_to_schema_facts(rows)
    index = _index(rows)

    type_keys = sorted(
        key
        for key in {f.symbol_key for f in rows}
        if index.get((key, "existence")) is not None
        and index[(key, "existence")].value in TYPE_ROLES
    )
    members: Dict[str, List[str]] = {key: [] for key in type_keys}
    for fact in rows:
        if fact.attribute != "location.parent":
            continue
        if fact.value in members and fact.symbol_key not in members:
            members[fact.value].append(fact.symbol_key)

    lines: List[str] = []
    for key in type_keys:
        role = index[(key, "existence")].value
        keyword = "interface" if role == "interface" else role
        lines.append(f"{keyword} {key} {{")
        for member in sorted(set(members[key])):
            kind = index.get((member, "type.kind"))
            if kind is None:
                continue
            order = index.get((member, "location.order"))
            authority = index.get((member, "callable.confirmation"))
            suffix = (
                f" @user_confirmation(by: {authority.value.split('=', 1)[-1]})" if authority else ""
            )
            lines.append(f"  {member.rsplit('.', 1)[-1]}: {sdl_type_spelling(kind.value)}{suffix}")
        lines.append("}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"