"""Stable symbol identity and code/schema source maps.

The same domain symbol has a different *name* in each projection — an SDL type
``Order``, a Python class ``Order``, a database symbol ``orders`` — and a
different *local key* in each artifact.  Reconciliation is only meaningful if we
can tell "the same symbol, renamed" from "one symbol deleted and another
created".  This module does that.

Identity model
--------------
* A raw extraction produces **candidate symbol keys** that are local to one
  artifact (``path.to.Type.field`` for SDL, ``module.Class.attr`` for Python).
  Candidate keys are *ephemeral*.
* A **resolution** maps each candidate key to a **canonical symbol key**, the
  stable identity used by the shared representation.  The canonical key is drawn
  from the *prior* projection whenever possible, so identities do not churn when
  a document moves or a file is renamed.
* A resolution is a union-find over keys.  Anything not in the resolution is its
  own canonical key, so a resolution serialises to exactly the aliases it
  introduced and a no-op reconciliation introduces none.

Matching rules, in precedence order (each is deterministic):

``same-key``      the candidate key is a prior canonical key or an alias of one;
                  identity is preserved outright.
``propagated``    the candidate key equals a prior canonical key after applying
                  the current rename prefix map (a renamed parent type carries
                  its children with it).
``structural``    exactly one prior canonical symbol in the same projection has
                  the same *shape* (kind, type, callable ports, local name) —
                  this is how a rename is recognised.  More than one such prior
                  symbol is an **ambiguous** match and is deliberately left
                  unmatched: the conflict engine then reports it rather than
                  silently picking a side.
``new``           nothing prior matches; the symbol is new.

The resolver records confidence, so an ambiguous match can be surfaced as an
advisory while a resolved one is authoritative.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .model import ABSENT, Fact, attribute_kind

#: Fact attributes that describe a symbol's *shape* rather than its identity.
#: Renaming must not change these; retyping must.  ``value.type.kind`` is
#: included through the ``type.`` prefix; ``location.parent`` is included so a
#: field is matched to the same containing type.
STRUCTURAL_PREFIXES = ("type.", "callable.", "location.parent")

#: Symbol kinds that act as *anchors* for rename propagation: a renamed type
#: carries every child key under it.  "type" and "enum"/"interface"/"union"/
#: "scalar" are the schema-side kinds; "class"/"object" the code-side ones (a
#: Python class is the counterpart of an SDL type, and its methods the
#: counterparts of SDL fields).
TYPE_LEVEL_KINDS = frozenset(
    {"type", "enum", "interface", "union", "scalar", "directive", "object", "class"}
)

#: Confidence levels reported in the identity report.
CONF_SAME_KEY = 1.0
CONF_PROPAGATED = 0.95
CONF_STRUCTURAL = 0.9
CONF_AMBIGUOUS = 0.4


def local_name(symbol_key: str) -> str:
    """The last dot-segment of a symbol key (its local name)."""
    return symbol_key.rsplit(".", 1)[-1]


def parent_path(symbol_key: str) -> str:
    """Everything before the last dot-segment ("" for a top-level symbol)."""
    return symbol_key.rsplit(".", 1)[0] if "." in symbol_key else ""


def normalize_key(key: str) -> str:
    """Fold projection-specific syntax out of a candidate key.

    SDL candidate keys spell instance fields as ``Type.field`` while type
    declarations themselves are ``Type``; Python spells a method
    ``Class.method`` and a module symbol ``module``.  Parentheses and generic
    noise are dropped so the two spellings line up.
    """
    return key.replace("::", ".").replace("()", "").strip(".")


def structural_slots(facts: Iterable[Fact]) -> Tuple[Tuple[str, str], ...]:
    """The shape of a symbol: sorted (attribute, value) pairs of structural facts.

    Ordering is by attribute so the result is insertion-order independent.  This
    is the *signature* a rename preserves and a retype changes.
    """
    slots = {
        fact.attribute: fact.value
        for fact in facts
        if fact.attribute.startswith(STRUCTURAL_PREFIXES)
    }
    return tuple(sorted(slots.items()))


def anchor_signature(
    groups: Mapping[str, Sequence[Fact]], key: str
) -> Tuple[Tuple[str, str, str], ...]:
    """The shape of a type-level symbol: its own and its members' structural facts.

    A type-level symbol's *own* structural facts are usually empty — an SDL
    ``type`` or a Python ``class`` declares no ``type.``/``callable.`` fact
    itself; its shape **is** the set of its members.  Comparing only the symbol's
    own slots therefore makes every type in a real contract look identical, and a
    rename inside such a contract is reported as ``ambiguous-identity`` instead of
    being recognised.

    Members are keyed by their *relative* key (``id``, not ``Order.id``) and by
    their ``type.``/``callable.`` facts only: ``location.parent`` follows a
    rename, so including it would make a renamed parent look like a different
    shape and defeat the very match this exists to make.
    """
    rows: List[Tuple[str, str, str]] = []
    prefix = key + "."
    for member_key, facts in groups.items():
        if member_key == key:
            relative = ""
        elif member_key.startswith(prefix):
            relative = member_key[len(prefix):]
        else:
            continue
        for fact in facts:
            if fact.attribute.startswith(("type.", "callable.")):
                rows.append((relative, fact.attribute, fact.value))
    return tuple(sorted(rows))


def symbol_kind(facts: Iterable[Fact]) -> str:
    """The declared kind of a symbol (``type``, ``field``, ``enum_value``, ...).

    Falls back to the empty string when a projection has no existence fact, which
    makes the symbol kind-unknown rather than silently guessing one from the key.
    """
    for fact in facts:
        if fact.attribute == "existence":
            return "" if fact.value == ABSENT else fact.value
    return ""


# ---------------------------------------------------------------------------
# Union-find with a serialisable representation.
# ---------------------------------------------------------------------------


@dataclass
class IdentityResolver:
    """A canonical-name resolution over candidate symbol keys.

    ``parent`` is a union-find forest: a key absent from it is a canonical name
    for itself.  It is deliberately stored as a plain dict so it serialises
    (and hashes) without custom machinery, and so two empty resolvers compare
    equal — which is what makes repeated reconciliation a no-op.
    """

    parent: Dict[str, str] = field(default_factory=dict)
    #: alias -> (canonical, confidence, reason); the *report*, not the state.
    proposals: Dict[str, Tuple[str, float, str]] = field(default_factory=dict)

    # -- state -------------------------------------------------------------
    def canonical(self, key: str) -> str:
        seen = set()
        while key in self.parent and self.parent[key] != key:
            if key in seen:
                raise ValueError(f"identity cycle at {key!r}")
            seen.add(key)
            key = self.parent[key]
        return key

    def alias(self, alias: str, canonical: str, confidence: float, reason: str) -> None:
        """Record that ``alias`` denotes ``canonical``.

        The canonical side is resolved to its root so chains collapse, and the
        root of the alias is unioned onto it.  Re-aliasing the same pair is a
        no-op, which keeps repeated reconciliation idempotent.
        """
        root_alias = self.canonical(alias) if alias in self.parent else alias
        root_canon = self.canonical(canonical)
        if root_alias == root_canon:
            self.proposals.setdefault(alias, (root_canon, confidence, reason))
            return
        self.parent[root_alias] = root_canon
        self.parent.setdefault(root_canon, root_canon)
        self.parent.setdefault(alias, root_canon)
        self.proposals[alias] = (root_canon, confidence, reason)

    def known(self, key: str) -> bool:
        return key in self.parent

    def moves(self) -> Dict[str, str]:
        """alias -> canonical, for keys whose canonical differs from themselves."""
        return {
            alias: self.canonical(alias)
            for alias in self.parent
            if self.canonical(alias) != alias
        }

    def is_empty(self) -> bool:
        return not self.moves() and not self.proposals

    def as_dict(self) -> dict:
        moves = self.moves()
        return {
            "parent": {k: self.canonical(k) for k in sorted(self.parent)},
            "proposals": {
                k: {"canonical": v[0], "confidence": v[1], "reason": v[2]}
                for k, v in sorted(self.proposals.items())
            },
            "moves": {k: moves[k] for k in sorted(moves)},
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> "IdentityResolver":
        resolver = cls()
        for alias, canonical in (data.get("parent") or {}).items():
            resolver.parent[alias] = canonical
        for alias, info in (data.get("proposals") or {}).items():
            resolver.proposals[alias] = (
                info["canonical"],
                float(info["confidence"]),
                info["reason"],
            )
        return resolver

    def copy(self) -> "IdentityResolver":
        return IdentityResolver(dict(self.parent), dict(self.proposals))

    def fingerprint(self) -> str:
        return json.dumps(self.as_dict()["moves"], sort_keys=True, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Rename propagation: a renamed parent carries its children.
# ---------------------------------------------------------------------------


def apply_prefix_map(key: str, prefix_map: Mapping[str, str]) -> str:
    """Rewrite ``key`` by replacing its longest matching renamed prefix.

    Matching is on dot boundaries, so renaming ``a.b`` does not silently rewrite
    ``a.bc.d``.  The longest prefix wins, which makes nested renames deterministic.
    """
    best_from = ""
    best_to = None
    for source, target in prefix_map.items():
        if key == source or key.startswith(source + "."):
            if len(source) > len(best_from):
                best_from, best_to = source, target
    if best_to is None:
        return key
    return best_to + key[len(best_from):]


def prefix_map_from(moves: Mapping[str, str]) -> Dict[str, str]:
    """Project an identity resolution onto the type-level prefixes it renamed."""
    return {
        alias: canonical
        for alias, canonical in moves.items()
        if parent_path(alias) == "" or alias.count(".") >= 0
    }


# ---------------------------------------------------------------------------
# The matcher.
# ---------------------------------------------------------------------------


@dataclass
class Match:
    """One candidate symbol matched (or deliberately not matched) to a prior one.

    ``alternatives`` is populated for an ambiguous match: the prior canonical
    symbols that could equally have been meant.  That is what makes an
    ``ambiguous-identity`` conflict actionable ("declare an alias" / "disambiguate
    the local keys") rather than a bare disagreement.
    """

    candidate_key: str
    canonical_key: str
    confidence: float
    reason: str
    kind: str = ""
    alternatives: Tuple[str, ...] = ()

    def as_dict(self) -> dict:
        """Serialise one match.  Used by the reconcile plan's identity report."""
        return {
            "candidate": self.candidate_key,
            "canonical": self.canonical_key,
            "confidence": self.confidence,
            "reason": self.reason,
            "kind": self.kind,
            "alternatives": list(self.alternatives),
        }


@dataclass
class IdentityReport:
    """Everything the caller needs to explain identity decisions."""

    matches: List[Match] = field(default_factory=list)
    unresolved: List[Match] = field(default_factory=list)
    renamed: List[Match] = field(default_factory=list)
    new: List[str] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        def rows(items: Sequence[Match]) -> List[dict]:
            return [
                {
                    "candidate": m.candidate_key,
                    "canonical": m.canonical_key,
                    "confidence": m.confidence,
                    "reason": m.reason,
                    "kind": m.kind,
                    "alternatives": list(m.alternatives),
                }
                for m in items
            ]

        return {
            "renamed": rows(self.renamed),
            "unresolved": rows(self.unresolved),
            "new": sorted(self.new),
            "removed": sorted(self.removed),
            "matches": rows(self.matches),
        }


def _facts_by_symbol(facts: Sequence[Fact], projection: str) -> Dict[str, List[Fact]]:
    out: Dict[str, List[Fact]] = {}
    for fact in facts:
        if fact.projection != projection:
            continue
        out.setdefault(fact.symbol_key, []).append(fact)
    return out


def _prior_canonical_groups(
    prior_facts: Sequence[Fact], prior_identity: IdentityResolver, projection: str
) -> Dict[str, List[Fact]]:
    """Prior facts folded onto their canonical symbol keys (drop revocations)."""
    groups: Dict[str, List[Fact]] = {}
    for key, facts in _facts_by_symbol(prior_facts, projection).items():
        canonical = prior_identity.canonical(key)
        live = [f for f in facts if f.value != ABSENT]
        if live:
            groups.setdefault(canonical, []).extend(f.with_key(canonical) for f in live)
    return groups


def match_identities(
    prior_facts: Sequence[Fact],
    candidate_facts: Sequence[Fact],
    prior_identity: IdentityResolver,
    projection: str,
) -> Tuple[IdentityResolver, IdentityReport]:
    """Match candidate symbols to prior canonical symbols for one projection.

    Returns a **new** resolver (the input is not mutated) plus a report of what
    was recognised as a rename, what was new, what is gone and what could not be
    resolved unambiguously.  A candidate key with no prior correspondence maps to
    itself, so a brand-new artifact introduces no aliases at all.
    """
    resolver = prior_identity.copy()
    report = IdentityReport()

    prior_groups = _prior_canonical_groups(prior_facts, prior_identity, projection)
    cand_groups = _facts_by_symbol(candidate_facts, projection)

    # --- round 1: anchors (type-level symbols) -----------------------------
    def signature_for(groups: Mapping[str, Sequence[Fact]], key: str) -> Tuple:
        if symbol_kind(groups[key]) in TYPE_LEVEL_KINDS:
            return anchor_signature(groups, key)
        return structural_slots(groups[key])

    prior_sig: Dict[str, Tuple] = {
        key: signature_for(prior_groups, key) for key in prior_groups
    }
    prior_kind: Dict[str, str] = {key: symbol_kind(facts) for key, facts in prior_groups.items()}
    prior_name: Dict[str, str] = {key: local_name(key) for key in prior_groups}

    def candidates_for(predicate) -> List[str]:
        return sorted(k for k, f in cand_groups.items() if predicate(k, f))

    resolved: Dict[str, str] = {}          # candidate -> canonical (round 1+2)
    claimed: Dict[str, List[str]] = {}     # canonical -> candidates claiming it

    def claim(candidate: str, canonical: str, confidence: float, reason: str) -> None:
        resolved[candidate] = canonical
        claimed.setdefault(canonical, []).append(candidate)
        report.matches.append(
            Match(candidate, canonical, confidence, reason, kind=symbol_kind(cand_groups[candidate]))
        )
        if reason in ("structural", "propagated"):
            report.renamed.append(
                Match(candidate, canonical, confidence, reason, kind=symbol_kind(cand_groups[candidate]))
            )
        # Record the move in the resolver whenever the key actually changed.
        # This is NOT the same as "the local name changed": a *propagated* rename
        # (a renamed type carrying its children, ``Order.total`` ->
        # ``PurchaseOrder.total``) keeps the child's local name while its key
        # moves, and it must still be aliased back onto the canonical symbol or
        # the merged body keeps the child under both keys.
        if candidate != canonical:
            resolver.alias(candidate, canonical, confidence, reason)

    anchors = candidates_for(
        lambda k, f: symbol_kind(f) in TYPE_LEVEL_KINDS
    )
    anchor_pairs: List[Tuple[str, str]] = []  # (candidate, canonical)
    for candidate in anchors:
        canonical = resolver.canonical(candidate)
        if canonical in prior_groups and canonical != candidate:
            claim(candidate, canonical, CONF_SAME_KEY, "same-key")
            continue
        if candidate in prior_groups:
            claim(candidate, candidate, CONF_SAME_KEY, "same-key")
            continue
        # structural: unique prior type-level symbol with identical shape+name
        sig_c = signature_for(cand_groups, candidate)
        name_c = local_name(candidate)
        exact = [
            p
            for p, sig in prior_sig.items()
            if p not in claimed
            and sig == sig_c
            and prior_kind[p] in TYPE_LEVEL_KINDS
            and prior_name[p] == name_c
        ]
        if len(exact) == 1:
            claim(candidate, exact[0], CONF_STRUCTURAL, "structural")
            anchor_pairs.append((candidate, exact[0]))
            continue
        shape_only = [
            p for p, sig in prior_sig.items() if p not in claimed and sig == sig_c and prior_kind[p] in TYPE_LEVEL_KINDS
        ]
        if len(shape_only) == 1:
            claim(candidate, shape_only[0], CONF_STRUCTURAL, "structural")
            anchor_pairs.append((candidate, shape_only[0]))
        elif len(shape_only) > 1:
            report.unresolved.append(
                Match(
                    candidate,
                    "",
                    CONF_AMBIGUOUS,
                    "ambiguous-rename",
                    kind=symbol_kind(cand_groups[candidate]),
                    alternatives=tuple(sorted(shape_only)),
                )
            )

    # --- rename propagation -------------------------------------------------
    propagate: Dict[str, str] = {}
    for candidate, canonical in anchor_pairs:
        if candidate != canonical:
            propagate[candidate] = canonical

    # --- round 2: everything else, with propagation applied ---------------
    for candidate in sorted(cand_groups):
        if candidate in resolved:
            continue
        projected = normalize_key(apply_prefix_map(candidate, propagate))
        if projected != candidate and projected in prior_groups and projected not in claimed:
            claim(candidate, projected, CONF_PROPAGATED, "propagated")
            continue
        if candidate in prior_groups and candidate not in claimed:
            claim(candidate, candidate, CONF_SAME_KEY, "same-key")
            continue
        canonical = resolver.canonical(candidate)
        if canonical in prior_groups and canonical not in claimed:
            claim(candidate, canonical, CONF_SAME_KEY, "same-key")
            continue
        # structural fallback: unique prior symbol with identical shape
        sig_c = signature_for(cand_groups, candidate)
        if sig_c:
            exact = [p for p, sig in prior_sig.items() if p not in claimed and sig == sig_c]
            if len(exact) == 1:
                claim(candidate, exact[0], CONF_STRUCTURAL, "structural")
                continue
            if len(exact) > 1:
                report.unresolved.append(
                    Match(
                        candidate,
                        "",
                        CONF_AMBIGUOUS,
                        "ambiguous-rename",
                        kind=symbol_kind(cand_groups[candidate]),
                        alternatives=tuple(sorted(exact)),
                    )
                )
                continue
        report.new.append(candidate)

    for canonical in sorted(prior_groups):
        if canonical not in claimed:
            report.removed.append(canonical)

    return resolver, report


def renamed_prefixes(report: IdentityReport) -> Dict[str, str]:
    """Rename moves from a report, for use as a prefix map on a reverse pass."""
    return {
        m.canonical_key: m.candidate_key
        for m in report.renamed
        if m.reason == "structural" and m.canonical_key
    }
