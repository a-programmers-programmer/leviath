"""Crosswalk: how the two projections' media-specific spellings line up.

The reconciler compares facts from *two different authoring media*.  An SDL field
``id: ID!`` and a typed-Python attribute ``id: str`` are the **same domain fact**,
spelled in two languages.  Without a crosswalk, every typo-free artifact pair
would look like it disagreed about everything, and every first reconciliation
would be a wall of false conflicts.

The crosswalk therefore does three things, and nothing else:

1. **Roles.**  ``existence`` records a media-neutral *role*
   (``type``/``member``/``value``/``callable``/...) rather than an SDL keyword or
   a Python AST node type, so an SDL ``type`` and a Python ``class`` both assert
   role ``type``.  The media-specific spelling survives in the local
   ``annotation.declaration`` attribute for provenance.
2. **Types.**  ``canonical_type`` normalises ``ID!`` and ``str`` to the same
   canonical ``string`` (and ``String``/``str``, ``Int!``/``int``,
   ``[String!]!``/``list[str]``, ...).  A pair the table does not know is left as
   written, so ``DateTime!`` vs ``int`` stays a *real* type divergence rather
   than being smoothed away.  Nullability is preserved as a trailing ``?``:
   SDL's absence of ``!`` and Python's ``= None`` default both mean optional.
3. **Locality.**  ``location.*`` (where a symbol lives in *this* medium — its
   file, line and ordinal), ``annotation.*`` (a docstring versus an SDL
   description) and ``order.*`` are **local** attributes: they are legitimately
   different between the media, so they are never cross-compared and a
   difference on one is never a conflict.

Everything else — ``existence``, ``name``, ``type.kind``, ``callable.port`` — is
a shared domain attribute, and a difference there is a genuine divergence worth
reporting.
"""

from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

#: (projection, media-specific declaration) -> media-neutral role.
EXISTENCE_ROLES: Dict[Tuple[str, str], str] = {
    ("schema", "type"): "type",
    ("schema", "object"): "type",
    ("schema", "interface"): "interface",
    ("schema", "union"): "union",
    ("schema", "enum"): "enum",
    ("schema", "scalar"): "scalar",
    ("schema", "input"): "input",
    ("schema", "directive"): "directive",
    ("schema", "schema"): "schema",
    ("schema", "extend"): "extend",
    ("schema", "field"): "member",
    ("schema", "enum_value"): "value",
    ("schema", "union_member"): "member",
    ("code", "class"): "type",
    ("code", "field"): "member",
    # A Python ``Enum`` subclass is an enumerated type, the counterpart of an SDL
    # ``enum``; both must read as role ``enum`` or every real enum in the accepted
    # contract would look like an existence divergence.
    ("code", "enum"): "enum",
    # A Python ``Enum`` member is the counterpart of an SDL enum value: both
    # declare one named value of an enumerated type.  Without this entry every
    # real enum in the accepted contract would read as a divergence between the
    # two media rather than as one agreed value.
    ("code", "enum_value"): "value",
    # A Python method is the counterpart of an SDL field on the same key: both
    # declare a named member of a type.  Whether the member is data or a
    # callable is carried by ``callable.*``, not by the role.
    ("code", "method"): "member",
    ("code", "function"): "callable",
    # A module-level type alias (``Confidential = str``) declares a type name
    # with no structure to model: the counterpart of an SDL ``scalar``.
    ("code", "alias"): "scalar",
    # A module-level union alias (``Outcome = A | B``) is the counterpart of an
    # SDL ``union``: a type that is ``one of`` several members.
    ("code", "union_alias"): "union",
    # A ``typing.Protocol`` class is the counterpart of an SDL ``interface``: a
    # shape several concrete types implement, not a concrete record.
    ("code", "interface"): "interface",
}

#: Base type name -> canonical domain type.  Both spellings map onto one name.
TYPE_ALIASES: Dict[str, str] = {
    "ID": "string",
    "String": "string",
    "str": "string",
    "Text": "string",
    "Int": "integer",
    "int": "integer",
    "Float": "number",
    "float": "number",
    "Boolean": "boolean",
    "bool": "boolean",
    "DateTime": "datetime",
    "datetime": "datetime",
    "date": "datetime",
    "Duration": "duration",
    "timedelta": "duration",
    "Decimal": "decimal",
    "decimal": "decimal",
    "URI": "uri",
    "AnyUrl": "uri",
    "UUID": "uuid",
    "uuid": "uuid",
    "Confidential": "confidential",
    "JsonPointer": "json-pointer",
    "bytes": "bytes",
    "dict": "object",
    "Any": "any",
    "object": "object",
}

#: Attribute prefixes that are local to one projection.  A difference on one of
#: these is never a conflict: the two media legitimately spell them differently.
LOCAL_PREFIXES: Tuple[str, ...] = ("location.", "annotation.", "order.")

#: Attributes that carry the media-neutral role of an existence fact.
_ROLE_ATTRIBUTE = "annotation.declaration"


def is_local(attribute: str) -> bool:
    """True when ``attribute`` is local to a projection (never cross-compared)."""
    return attribute.startswith(LOCAL_PREFIXES)


def existence_role(projection: str, declaration: str) -> str:
    """The media-neutral role for a media-specific declaration spelling."""
    return EXISTENCE_ROLES.get((projection, declaration), declaration)


def _split_modifiers(written: str, projection: str = "") -> Tuple[str, bool, bool]:
    """Split a written type into ``(base, required, is_list)``.

    Nullability is spelled differently by the two media, so the *bare* spelling
    means opposite things and the projection must be known:

    * **SDL** marks non-null with a trailing ``!`` and leaves an optional field
      bare, so a bare SDL spelling is **nullable**.
    * **typed code** marks optional with ``?`` (the adapter's marker) or with
      ``Optional[...]`` / ``X | None``, so a bare code spelling is **required** —
      the exact counterpart of SDL's ``!``.

    An explicit nullable spelling (``?``, ``Optional[...]``, ``X | None``) always
    wins.  Getting this wrong is not cosmetic: if both media were read as
    "required unless marked", every nullable SDL field would look like a real
    disagreement with its correctly-read code counterpart.
    """
    text = written.strip()
    is_list = False
    # Projection-specific default: SDL bare == nullable, code bare == required.
    required = text.endswith("!") if projection == "schema" else True
    nullable = False

    if text.endswith("?"):
        nullable = True
        text = text[:-1].strip()
    if text.endswith("!"):
        text = text[:-1].strip()
    if " | None" in text or text.endswith("| None"):
        nullable = True
        text = text.split("|", 1)[0].strip()
    if text.startswith("Optional[") and text.endswith("]"):
        nullable = True
        text = text[len("Optional["): -1].strip()
    lowered = text.lower()
    if lowered.startswith("list[") and text.endswith("]"):
        is_list = True
        text = text[len("list["): -1].strip()
        if text.endswith("!"):
            text = text[:-1].strip()
    elif text.startswith("[") and text.endswith("]"):
        is_list = True
        text = text[1:-1].strip()
        if text.endswith("!"):
            text = text[:-1].strip()
    if text.endswith("?"):
        nullable = True
        text = text[:-1].strip()
    if nullable:
        required = False
    return text, required, is_list


def canonical_type(written: str, projection: str = "") -> str:
    """Normalise a written type spelling onto a canonical domain type.

    Unknown base names are preserved verbatim, so a spelling the crosswalk does
    not recognise surfaces as a divergence rather than being silently equated.
    ``projection`` is required to read the *bare* nullability spelling correctly
    (SDL: nullable; code: required) — see :func:`_split_modifiers`.

    A **dotted** base name (``decimal.Decimal``, ``datetime.datetime``) is looked
    up by its final segment, because ``decimal.Decimal`` and SDL's ``Decimal``
    name the same domain type; leaving the dotted spelling un-normalised would
    make every qualified Python annotation look like a real type divergence.
    """
    base, required, is_list = _split_modifiers(written, projection)
    canonical = TYPE_ALIASES.get(base)
    if canonical is None:
        canonical = TYPE_ALIASES.get(base.lower())
    if canonical is None and "." in base:
        tail = base.rsplit(".", 1)[-1]
        canonical = TYPE_ALIASES.get(tail, TYPE_ALIASES.get(tail.lower()))
    if canonical is None:
        canonical = base
    suffix = "[]" if is_list else ""
    return f"{canonical}{suffix}{'' if required else '?'}"


def cross_value(projection: str, attribute: str, value: str) -> str:
    """The value used when comparing this attribute across projections."""
    if is_local(attribute):
        return value
    if attribute == "existence":
        return existence_role(projection, value)
    if attribute.startswith("type."):
        return canonical_type(value, projection)
    return value


def cross_values_differ(attribute: str, left: str, right: str, left_projection: str, right_projection: str) -> bool:
    """True when two declared values of one attribute really disagree."""
    return cross_value(left_projection, attribute, left) != cross_value(
        right_projection, attribute, right
    )


def local_attributes() -> Tuple[str, ...]:
    return LOCAL_PREFIXES


def selftest() -> Dict[str, object]:
    """Prove the crosswalk equates the pairings it claims and nothing more."""
    cases = []
    pairs = [
        (("schema", "type.kind", "ID!"), ("code", "type.kind", "str")),
        (("schema", "type.kind", "String!"), ("code", "type.kind", "str")),
        (("schema", "type.kind", "String"), ("code", "type.kind", "str?")),
        (("schema", "type.kind", "Int!"), ("code", "type.kind", "int")),
        (("schema", "type.kind", "Float!"), ("code", "type.kind", "float")),
        (("schema", "type.kind", "Boolean!"), ("code", "type.kind", "bool")),
        (("schema", "type.kind", "[String!]!"), ("code", "type.kind", "list[str]")),
        (("schema", "type.kind", "DateTime!"), ("code", "type.kind", "datetime")),
        (("schema", "existence", "type"), ("code", "existence", "class")),
        (("schema", "existence", "field"), ("code", "existence", "method")),
    ]
    for left, right in pairs:
        assert not cross_values_differ(left[1], left[2], right[2], left[0], right[0]), (left, right)
        cases.append(f"{left[2]!r} == {right[2]!r}")
    divergences = [
        (("schema", "type.kind", "DateTime!"), ("code", "type.kind", "int")),
        (("schema", "type.kind", "ID!"), ("code", "type.kind", "int")),
        (("schema", "existence", "enum"), ("code", "existence", "class")),
        (("schema", "type.kind", "String!"), ("code", "type.kind", "string?")),
        (("schema", "type.kind", "ID!"), ("code", "type.kind", "str?")),
    ]
    for left, right in divergences:
        assert cross_values_differ(left[1], left[2], right[2], left[0], right[0]), (left, right)
        cases.append(f"{left[2]!r} != {right[2]!r}")
    # A Python Enum member is one named value of an enumerated type, the same
    # domain fact as an SDL enum value; an SDL enum value is *not* a field.
    assert not cross_values_differ("existence", "enum_value", "enum_value", "schema", "code")
    assert cross_values_differ("existence", "enum_value", "member", "schema", "code")
    cases.append("enum_value role")
    # The three type-level constructs of typed Python must read as the same role
    # as their SDL counterparts, or every scalar, union and interface in the
    # accepted contract would look like an existence divergence.
    for code_decl, schema_decl in (("alias", "scalar"), ("union_alias", "union"), ("interface", "interface")):
        assert not cross_values_differ("existence", schema_decl, code_decl, "schema", "code"), (
            schema_decl,
            code_decl,
        )
        cases.append(f"{schema_decl}(schema) == {code_decl}(code)")
    assert cross_values_differ("existence", "scalar", "class", "schema", "code")
    assert cross_values_differ("existence", "union", "alias", "schema", "code")
    cases.append("type-level role divergences")
    assert is_local("location.path") and is_local("annotation.doc") and is_local("order.0")
    assert not is_local("name") and not is_local("type.kind") and not is_local("existence")
    cases.append("locality")
    return {"self_test": "PASS", "cases": len(cases), "details": cases, "contract": "leviath-reconcile-crosswalk/v1"}