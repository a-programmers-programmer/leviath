"""The shared structured domain representation.

This is the single representation both projections (schema SDL and typed code)
extract into and reconcile over.  It is deliberately tiny and value-oriented so
that it is trivially serialisable, hashable and comparable:

``SourceRef``
    A code/schema source map entry: path, 1-based line, inclusive [start,end]
    character columns, plus an optional human note.  This is what makes a
    conflict "actionable" — every fact can point back at the text that produced
    it, in either projection.

``Fact``
    One (subject, attribute, value) proposition with a stable identity.  A fact
    is *nothing more than* that triple plus provenance and confidence.  Kind is
    derived from the attribute's position in the namespace (the first dot
    component), so the namespace *is* the fact schema — there is no separate
    fact-type registry to drift.

``FactSet``
    A deterministic, hashable collection of facts keyed by ``symbol_key``.

``Projection``
    One side's extraction result: its raw document identity, its source map, its
    facts and the canonical digest of those facts.  The digest is the projection's
    *current version*; ``Reconciler`` refuses to reconcile a projection whose
    document identity does not match what the caller declared.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

#: Namespaces a fact attribute may live in.  The first component of every
#: attribute MUST be one of these, which keeps the representation structured
#: (facts about a symbol's existence, type, naming, location, callability and
#: ordering are distinguishable without parsing the attribute string).
ATTRIBUTE_NAMESPACES = (
    "existence",
    "name",
    "type",
    "location",
    "callable",
    "order",
    "annotation",
)

#: Absence marker.  A fact whose value is ``ABSENT`` proposes that the attribute
#: is *not* present (used for existence revocation and edge removal).  Using a
#: real sentinel rather than ``None`` keeps "no value" distinguishable from
#: "value is null", which matters when a projection legitimately reports null.
ABSENT = "\x00<absent>"


def attribute_kind(attribute: str) -> str:
    """Return the namespace of ``attribute`` (the part before the first dot).

    Raises ``ValueError`` for an empty attribute or an unknown namespace so a
    typo becomes a loud failure rather than a silently-dropped fact.
    """
    if not attribute:
        raise ValueError("fact attribute must be non-empty")
    kind = attribute.split(".", 1)[0]
    if kind not in ATTRIBUTE_NAMESPACES:
        raise ValueError(
            f"unknown fact namespace {kind!r}; expected one of {ATTRIBUTE_NAMESPACES}"
        )
    return kind


def _require_nonempty(value: str, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{what} must be a non-empty string")
    return value


@dataclass(frozen=True)
class SourceRef:
    """A span in one projection's source text (its source map entry)."""

    path: str
    line: int
    col_start: int
    col_end: int
    note: str = ""

    def __post_init__(self) -> None:
        _require_nonempty(self.path, "SourceRef.path")
        if self.line < 1:
            raise ValueError("SourceRef.line is 1-based and must be >= 1")
        if self.col_start < 0 or self.col_end < self.col_start:
            raise ValueError("SourceRef columns must satisfy 0 <= start <= end")

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "line": self.line,
            "col_start": self.col_start,
            "col_end": self.col_end,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> "SourceRef":
        return cls(
            path=data["path"],
            line=int(data["line"]),
            col_start=int(data["col_start"]),
            col_end=int(data["col_end"]),
            note=data.get("note", ""),
        )


@dataclass(frozen=True)
class Fact:
    """One proposition about one symbol, tagged with its projection and source."""

    symbol_key: str           # stable identity of the subject (see identity.py)
    projection: str           # "schema" | "code"
    attribute: str            # namespaced attribute, e.g. "type.kind", "name"
    value: str                # compare-and-merge value
    source: Optional[SourceRef] = None
    confidence: float = 1.0   # < 1.0 => provisional (advisory), never fatal

    def __post_init__(self) -> None:
        _require_nonempty(self.symbol_key, "Fact.symbol_key")
        _require_nonempty(self.projection, "Fact.projection")
        attribute_kind(self.attribute)
        if not isinstance(self.value, str):
            raise ValueError("Fact.value must be a string")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("Fact.confidence must be within [0, 1]")

    @property
    def kind(self) -> str:
        return attribute_kind(self.attribute)

    def key(self) -> Tuple[str, str, str]:
        return (self.symbol_key, self.projection, self.attribute)

    def with_key(self, symbol_key: str) -> "Fact":
        """Return this fact re-attributed to ``symbol_key`` (identity move)."""
        return replace(self, symbol_key=symbol_key)

    def as_dict(self) -> dict:
        return {
            "symbol_key": self.symbol_key,
            "projection": self.projection,
            "attribute": self.attribute,
            "value": self.value,
            "confidence": self.confidence,
            "source": self.source.as_dict() if self.source else None,
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> "Fact":
        source = data.get("source")
        return cls(
            symbol_key=data["symbol_key"],
            projection=data["projection"],
            attribute=data["attribute"],
            value=data["value"],
            source=SourceRef.from_dict(source) if source else None,
            confidence=float(data.get("confidence", 1.0)),
        )


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_facts(facts: Iterable[Fact]) -> str:
    """Content digest of a set of facts.

    Facts are canonicalised to (key, value, confidence, source span) — **not**
    to the full source map note — and sorted, so the digest is stable across
    insertion order, dict ordering and cosmetically different but semantically
    identical extractions.  It changes when *and only when* the represented
    domain changes.
    """
    rows = []
    for fact in facts:
        source = fact.source
        rows.append(
            [
                fact.symbol_key,
                fact.projection,
                fact.attribute,
                fact.value,
                round(fact.confidence, 6),
                None if source is None else [source.path, source.line, source.col_start, source.col_end],
            ]
        )
    rows.sort(key=lambda row: json.dumps(row, separators=(",", ":")))
    return sha256_bytes(_canonical(rows))


@dataclass(frozen=True)
class Projection:
    """One projection's extraction result, together with its current version.

    ``document_identity`` is the *current* document version the projection was
    read from (file digest, containing commit, or a monotonically increasing
    revision id).  ``digest`` is the canonical digest of the facts.  Both are
    part of the projection's version; ``Reconciler`` verifies both against the
    caller-declared/current state so a stale document cannot overwrite newer
    work.
    """

    name: str
    document_identity: str
    facts: Tuple[Fact, ...] = ()
    source_map: Mapping[str, SourceRef] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_nonempty(self.name, "Projection.name")
        _require_nonempty(self.document_identity, "Projection.document_identity")
        object.__setattr__(self, "facts", tuple(self.facts))

    @property
    def digest(self) -> str:
        return digest_facts(self.facts)

    def by_key(self) -> Dict[Tuple[str, str], Fact]:
        out: Dict[Tuple[str, str], Fact] = {}
        for fact in self.facts:
            out[fact.key()] = fact
        return out

    def with_document(self, document_identity: str, facts: Sequence[Fact]) -> "Projection":
        return Projection(
            name=self.name,
            document_identity=document_identity,
            facts=tuple(facts),
            source_map=dict(self.source_map),
        )


@dataclass(frozen=True)
class FactSet:
    """A deterministic symbol-keyed collection of facts.

    This is the durable half of the accepted baseline and the body of an
    accepted reconciliation: everything except the ephemeral process keys used
    for identity matching, which are recorded separately so the accepted body
    is invariant under renumbering.
    """

    facts: Tuple[Fact, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "facts", tuple(self.facts))

    @property
    def digest(self) -> str:
        return digest_facts(self.facts)

    def merged(self, extra: Iterable[Fact]) -> "FactSet":
        by_key: Dict[Tuple[str, str, str], Fact] = {}
        for fact in list(self.facts) + list(extra):
            by_key[fact.key()] = fact
        return FactSet(tuple(by_key.values()))

    def with_updates(self, updates: Mapping[Tuple[str, str, str], Fact]) -> "FactSet":
        del_keys = {k for k, v in updates.items() if v.value == ABSENT}
        by_key: Dict[Tuple[str, str, str], Fact] = {
            f.key(): f for f in self.facts if f.key() not in del_keys
        }
        for key, fact in updates.items():
            if fact.value != ABSENT:
                by_key[key] = fact
        return FactSet(tuple(by_key.values()))

    def by_key(self) -> Dict[Tuple[str, str, str], Fact]:
        return {f.key(): f for f in self.facts}

    def by_symbol(self) -> Dict[str, Dict[str, Fact]]:
        out: Dict[str, Dict[str, Fact]] = {}
        for fact in self.facts:
            out.setdefault(fact.symbol_key, {})[fact.projection + "|" + fact.attribute] = fact
        return out

    def as_dict(self) -> dict:
        return {"facts": [f.as_dict() for f in sorted(self.facts, key=lambda f: f.key())]}

    @classmethod
    def from_dict(cls, data: Mapping) -> "FactSet":
        return cls(tuple(Fact.from_dict(row) for row in data.get("facts", ())))

    def __len__(self) -> int:
        return len(self.facts)
