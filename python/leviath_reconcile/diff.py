"""Diffing and edit classification.

Two layers, kept deliberately separate:

**Layer A — :func:`identity_diff`**
    A mechanical, attribute-level diff of one projection's current facts against
    the last agreed facts for the same projection, keyed by *canonical* symbol
    key.  Output is a list of :class:`Change` (``add``/``update``/``delete``).
    Identity has already been resolved, so a symbol that was merely renamed does
    not appear as a delete+add pair.

**Layer B — :func:`classify_symbols`**
    Groups the layer-A changes per symbol and names the *semantic* edit:
    ``add``, ``delete``, ``rename``, ``retype``, ``move``, ``reorder`` or
    ``annotate``.  This is what the reconciliation matrix reasons over, and what
    makes a conflict reportable as "rename here, type change there" instead of
    "fact 41 differs".

Nothing in this module mutates a baseline or reads a file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .model import ABSENT, Fact, FactSet, Projection, SourceRef, digest_facts

#: Attribute namespaces that change a symbol's *shape* (its signature).
SHAPE_PREFIXES = ("type.", "callable.")

#: Attribute namespaces that change a symbol's *identity as addressed*.
#: A path move (``location.path``) is not identity-affecting: the same symbol
#: simply lives in another file.  A parent move (``location.parent``) and a
#: ``name`` change are.
IDENTITY_ATTRIBUTES = ("name", "location.parent")

ADD = "add"
UPDATE = "update"
DELETE = "delete"


@dataclass(frozen=True)
class Change:
    """One attribute-level difference for one symbol in one projection."""

    symbol_key: str
    projection: str
    attribute: str
    op: str                 # add | update | delete
    old: Optional[str]
    new: Optional[str]
    source: Optional[SourceRef] = None
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if self.op not in (ADD, UPDATE, DELETE):
            raise ValueError(f"unknown change op {self.op!r}")

    def key(self) -> Tuple[str, str, str, str]:
        return (self.symbol_key, self.projection, self.attribute, self.op)

    def describe(self) -> str:
        where = ""
        if self.source is not None:
            where = f" at {self.source.path}:{self.source.line}"
        if self.op == ADD:
            return f"{self.projection} adds {self.attribute}={_show(self.new)}{where}"
        if self.op == DELETE:
            return f"{self.projection} deletes {self.attribute} (was {_show(self.old)}){where}"
        return f"{self.projection} changes {self.attribute}: {_show(self.old)} -> {_show(self.new)}{where}"

    def as_dict(self) -> dict:
        return {
            "symbol_key": self.symbol_key,
            "projection": self.projection,
            "attribute": self.attribute,
            "op": self.op,
            "old": self.old,
            "new": self.new,
            "source": self.source.as_dict() if self.source else None,
            "confidence": self.confidence,
        }


def _show(value: Optional[str]) -> str:
    if value is None:
        return "<none>"
    if value == ABSENT:
        return "<absent>"
    text = value.replace("\n", "\\n")
    return repr(text if len(text) <= 60 else text[:57] + "...")


@dataclass(frozen=True)
class SemanticEdit:
    """A named edit to one symbol, declared by one projection."""

    symbol_key: str
    projection: str
    kind: str                       # add|delete|rename|retype|move|reorder|annotate|composite
    changes: Tuple[Change, ...]
    attributes: Tuple[str, ...]

    @property
    def identity_affecting(self) -> bool:
        return any(attr in IDENTITY_ATTRIBUTES or attr.startswith("location.parent") for attr in self.attributes)

    @property
    def shape_affecting(self) -> bool:
        return any(attr.startswith(SHAPE_PREFIXES) for attr in self.attributes)

    def targets(self) -> Dict[str, str]:
        """attribute -> proposed value, for overlap and convergence tests."""
        return {change.attribute: (change.new if change.new is not None else ABSENT) for change in self.changes}

    def describe(self) -> str:
        detail = "; ".join(change.describe() for change in self.changes)
        return f"{self.projection} {self.kind} {self.symbol_key}: {detail}"

    def as_dict(self) -> dict:
        return {
            "symbol_key": self.symbol_key,
            "projection": self.projection,
            "kind": self.kind,
            "attributes": list(self.attributes),
            "changes": [c.as_dict() for c in self.changes],
        }


def fact_index(facts: Iterable[Fact]) -> Dict[Tuple[str, str], Fact]:
    """(canonical symbol, attribute) -> fact, for one projection's facts."""
    out: Dict[Tuple[str, str], Fact] = {}
    for fact in facts:
        out[(fact.symbol_key, fact.attribute)] = fact
    return out


def identity_diff(prior: FactSet, current: Projection) -> List[Change]:
    """Layer A: attribute-level diff of ``current`` against ``prior``.

    Only facts belonging to ``current.projection`` in the prior agreement are
    considered, so each side is diffed against *its own* last agreed version.
    A symbol deleted by this projection yields ``delete`` changes for each
    attribute it used to declare; a new symbol yields ``add`` changes.
    """
    projection = current.name
    prior_facts = [f for f in prior.facts if f.projection == projection]
    prior_index = fact_index(prior_facts)

    current_facts = [f for f in current.facts if f.projection == projection]
    current_index = fact_index(current_facts)

    changes: List[Change] = []
    for key in sorted(set(prior_index) | set(current_index)):
        symbol, attribute = key
        old = prior_index.get(key)
        new = current_index.get(key)
        if old is None and new is not None:
            changes.append(
                Change(symbol, projection, attribute, ADD, None, new.value, new.source, new.confidence)
            )
        elif new is None and old is not None:
            changes.append(
                Change(symbol, projection, attribute, DELETE, old.value, ABSENT, old.source, old.confidence)
            )
        elif old is not None and new is not None and (old.value != new.value or old.confidence != new.confidence):
            changes.append(
                Change(symbol, projection, attribute, UPDATE, old.value, new.value, new.source, new.confidence)
            )
    return changes


def _edit_kind(changes: Sequence[Change], renamed: bool) -> str:
    ops = {change.op for change in changes}
    attributes = [change.attribute for change in changes]
    if ops == {DELETE} or (DELETE in ops and not [c for c in changes if c.op != DELETE]):
        return "delete"
    if ops == {ADD}:
        return "add"
    if any(attr == "existence" for attr in attributes) and DELETE in ops:
        return "delete"
    if renamed or any(attr == "name" for attr in attributes):
        other = [a for a in attributes if a != "name"]
        return "composite" if other and not all(a.startswith("location.") for a in other) else "rename"
    if all(attr.startswith("order.") for attr in attributes):
        return "reorder"
    if all(attr.startswith("annotation.") or attr == "location.path" for attr in attributes):
        return "annotate"
    if all(attr.startswith("location.") or attr == "annotation.doc" for attr in attributes):
        return "move" if any(a.startswith("location.") and a != "location.path" for a in attributes) else "annotate"
    if all(attr.startswith(SHAPE_PREFIXES) for attr in attributes):
        return "retype"
    if all(attr.startswith(SHAPE_PREFIXES) or attr.startswith("location.") for attr in attributes):
        return "retype"
    return "composite"


def classify_symbols(
    changes: Sequence[Change], renamed_symbols: Mapping[str, str] | None = None
) -> List[SemanticEdit]:
    """Layer B: group layer-A changes into named semantic edits.

    ``renamed_symbols`` maps canonical symbol -> the alias this projection now
    uses for it, when identity matching recognised a rename.  That is what makes
    a pure key move classify as ``rename`` even though the ``name`` fact may be
    the only thing that changed.
    """
    renamed = set((renamed_symbols or {}).keys())
    grouped: Dict[Tuple[str, str], List[Change]] = {}
    for change in changes:
        grouped.setdefault((change.symbol_key, change.projection), []).append(change)

    edits: List[SemanticEdit] = []
    for (symbol, projection) in sorted(grouped):
        rows = sorted(grouped[(symbol, projection)], key=lambda c: (c.attribute, c.op))
        kind = _edit_kind(rows, symbol in renamed)
        edits.append(
            SemanticEdit(
                symbol_key=symbol,
                projection=projection,
                kind=kind,
                changes=tuple(rows),
                attributes=tuple(sorted({c.attribute for c in rows})),
            )
        )
    return edits


def projection_digest(facts: Iterable[Fact], projection: str) -> str:
    """Canonical digest of one projection's facts (its current version)."""
    return digest_facts([f for f in facts if f.projection == projection])
