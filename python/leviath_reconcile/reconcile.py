"""Shared reconciliation rules: three-way merge, conflict detection, symmetry.

The engine implements one deterministic procedure:

1. **Match identities** for each projection against the baseline
   (:mod:`~leviath_reconcile.identity`), so a rename is one edit rather than a
   delete+add pair and not a false conflict.
2. **Diff** each projection against *its own* last agreed facts
   (:func:`~leviath_reconcile.diff.identity_diff`).
3. **Classify** each side's edits into named semantic edits
   (add/delete/rename/retype/move/reorder/annotate).
4. **Three-way merge**, per symbol:

   * **divergent** — both sides changed the *same category* of the *same*
     symbol (naming vs naming, shape vs shape, existence vs existence) to
     different values.  This is a **conflict**: no side is chosen, each side's
     version and source location is preserved verbatim in the conflict record,
     and the plan's ``options`` name the exact resolutions a principal can
     choose.
   * **converging** — both sides propose the same final value: auto-applied
     once, to both sides.  This is the *symmetric* path.
   * **single-side** — only one side changed the attribute: auto-applied (to
     that side's facts).
   * **disjoint** — both sides edited the same symbol but in different
     categories (a rename here, a retype there): both auto-applied, no conflict.
   * **converging delete** — both sides delete the same symbol: applied once.

   A deletion on one side against *any* edit on the other side is a conflict so
   that both versions survive (``rename-vs-delete``, ``add-vs-delete``,
   ``delete-vs-edit``).

5. **Staleness gate** — the merge is refused when a projection's facts were read
   from a document that is not the current one, or when a projection is unknown
   to a baseline that already holds agreed facts for it (a partial or stale view
   must not overwrite newer, fully-known work).

**Merged-state rule.** A resolution is applied to the projection(s) that
produced it (its ``origins``).  A single-side edit therefore lands in the merged
body on the editing side; the untouched side keeps its agreed facts.  Making the
other projection *match* is code/schema generation, which is GQL-08's job, not
this work order's, so the reconciler records the cross-projection gap instead of
inventing a value for the side that was not edited.  Because the merged body is
stored per projection, the next reconcile run diffs each side against exactly
what it declared, so the result is a fixed point (no ping-pong).

**Determinism.** Conflicts sort by ``(code, symbol_key, attribute)``,
resolutions by ``(symbol_key, attribute, origin)``, auto-applied changes by
``(symbol_key, projection, attribute, op)``.  Every conflict carries
``resolution="preserve-both"`` and a non-empty ``options`` list, which is what
makes it actionable rather than a bare diff.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .baseline import Baseline
from .crosswalk import cross_value, cross_values_differ, is_local
from .diff import (
    ADD,
    DELETE,
    Change,
    SemanticEdit,
    classify_symbols,
    identity_diff,
)
from .identity import IdentityReport, IdentityResolver, match_identities
from .invariants import (
    InvariantRejection,
    check_acyclic_aliases,
    check_unique_aliases,
)
from .model import ABSENT, Fact, FactSet, Projection, SourceRef

#: Attribute category -> attribute prefixes.  Two edits in the same category of
#: the same symbol are alternatives (a divergence); edits in different
#: categories are compatible and both apply.  Local attributes (``location.*``,
#: ``annotation.*``, ``order.*``) are deliberately absent: they are local to a
#: projection and never compete across the two media (see ``crosswalk``).
CATEGORY_PREFIXES: Mapping[str, Tuple[str, ...]] = {
    "name": ("name",),
    "existence": ("existence",),
    "type": ("type.",),
    "callable": ("callable.",),
}

#: Default per-attribute policy used **only** when a caller explicitly opts in
#: with ``auto_apply_divergence=True``.  The default behaviour is to represent a
#: divergence as a conflict and choose nothing.
DEFAULT_POLICY: Mapping[str, str] = {"name": "code", "existence": "schema"}

CONVERGING = "auto:converging"
SINGLE_SIDE = "auto:single-side"
DISJOINT = "auto:disjoint"
POLICY = "policy:auto"
ACCEPTED = "accepted:reconciled"

PRESERVE_BOTH = "preserve-both"


class StaleView(RuntimeError):
    """A projection does not describe the version its facts were read from."""


class ReconcileConflict(RuntimeError):
    """An acceptance was attempted while conflicts existed."""

    def __init__(self, plan: "Plan") -> None:
        self.plan = plan
        codes = ", ".join(sorted(set(plan.codes)))
        super().__init__(f"{len(plan.conflicts)} conflict(s) block acceptance: {codes}")


def categories(attributes: Iterable[str]) -> Set[str]:
    """The categories a set of attribute names falls into."""
    out: Set[str] = set()
    for attribute in attributes:
        for category, prefixes in CATEGORY_PREFIXES.items():
            if any(attribute == p or attribute.startswith(p) for p in prefixes):
                out.add(category)
                break
    return out


def _show(value: Optional[str]) -> str:
    if value is None:
        return "<none>"
    if value == ABSENT:
        return "<absent>"
    text = value.replace("\n", "\\n")
    return repr(text if len(text) <= 60 else text[:57] + "...")


@dataclass(frozen=True)
class Conflict:
    """A competing change.  Both versions are preserved; nothing is chosen."""

    code: str
    symbol_key: str
    attribute: str
    message: str
    left: Dict[str, object]
    right: Dict[str, object]
    options: Tuple[str, ...]
    resolution: str = PRESERVE_BOTH

    @property
    def actionable(self) -> bool:
        return bool(self.options) and self.resolution == PRESERVE_BOTH

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "symbol_key": self.symbol_key,
            "attribute": self.attribute,
            "message": self.message,
            "left": dict(self.left),
            "right": dict(self.right),
            "options": list(self.options),
            "resolution": self.resolution,
            "actionable": self.actionable,
        }


@dataclass(frozen=True)
class Resolution:
    """One accepted value in the merged result, with the rule that produced it."""

    symbol_key: str
    attribute: str
    value: str
    origin: str
    detail: str = ""
    #: Projections this resolution is applied to in the merged body.
    origins: Tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "symbol_key": self.symbol_key,
            "attribute": self.attribute,
            "value": self.value,
            "origin": self.origin,
            "detail": self.detail,
            "origins": list(self.origins),
        }


@dataclass
class Plan:
    """The deterministic result of reconciling both projections."""

    ok: bool = True
    conflicts: List[Conflict] = field(default_factory=list)
    resolutions: List[Resolution] = field(default_factory=list)
    auto_applied: List[Change] = field(default_factory=list)
    held: List[Change] = field(default_factory=list)
    identity_report: Dict[str, object] = field(default_factory=dict)
    edit_counts: Dict[str, int] = field(default_factory=dict)
    stale: List[str] = field(default_factory=list)
    blocked: List[str] = field(default_factory=list)
    invariant_violations: List[str] = field(default_factory=list)

    @property
    def codes(self) -> List[str]:
        return [c.code for c in self.conflicts]

    def conflicts_for(self, symbol_key: str) -> List[Conflict]:
        return [c for c in self.conflicts if c.symbol_key == symbol_key]

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "blocked": bool(self.blocked) or bool(self.stale),
            "stale": list(self.stale),
            "blockers": list(self.blocked),
            "invariant_violations": list(self.invariant_violations),
            "edit_counts": dict(sorted(self.edit_counts.items())),
            "identity": self.identity_report,
            "conflicts": [c.as_dict() for c in self.conflicts],
            "actions": [c.as_dict() for c in self.conflicts if c.actionable],
            "resolutions": [r.as_dict() for r in self.resolutions],
            "auto_applied": [c.as_dict() for c in self.auto_applied],
            "held": [c.as_dict() for c in self.held],
        }

    def summary(self) -> str:
        if self.blocked or self.stale:
            return "blocked: " + "; ".join(self.blocked + self.stale)
        if self.conflicts:
            return f"{len(self.conflicts)} conflict(s): " + ", ".join(sorted(set(self.codes)))
        return f"applied {len(self.auto_applied)} change(s)"


@dataclass
class Outcome:
    """A :class:`Plan` plus the merged state it produces."""

    plan: Plan
    body: FactSet
    identity: IdentityResolver
    projection: Projection
    changed: bool
    #: The canonical source projections the merged body was synthesized from.
    #: ``check_idempotent`` re-diffs the body against *these* — diffing it
    #: against a projection named ``"merged"`` would compare the body with an
    #: empty prior and report every fact as an add.
    sources: Tuple[Projection, ...] = ()

    @property
    def ok(self) -> bool:
        return self.plan.ok

    def check_idempotent(self) -> "Outcome":
        """Re-diff the merged body against each source projection.

        Must produce zero changes and zero conflicts; otherwise the merge has not
        reached a fixed point (ping-pong) and this raises.  A plan that is blocked,
        stale, or still holds conflicts is not a fixed point by construction, so
        it is refused here rather than being reported as non-idempotent.
        """
        if self.plan.blocked or self.plan.stale:
            raise RuntimeError("cannot check idempotence of a blocked plan")
        if self.plan.conflicts:
            raise RuntimeError(
                "cannot check idempotence of a plan with unresolved conflicts: "
                + ", ".join(sorted(set(self.plan.codes)))
            )
        for source in self.sources:
            diff = identity_diff(self.body, source)
            if diff:
                raise RuntimeError(
                    "reconciliation is not idempotent: "
                    + "; ".join(change.describe() for change in diff)
                )
        return self


def _fact_index(facts: Iterable[Fact]) -> Dict[Tuple[str, str], Fact]:
    return {(f.symbol_key, f.attribute): f for f in facts}


def _canonical_projection(projection: Projection, identity: IdentityResolver) -> Projection:
    """Rewrite a projection's facts onto canonical symbol keys.

    A fact's ``location.parent`` value names a *local* parent symbol; it is
    canonicalised too, so a renamed parent does not make every child look like a
    move and so the two projections compare parent-by-parent on canonical names.
    """
    facts = []
    for fact in projection.facts:
        value = fact.value
        if fact.attribute == "location.parent":
            value = identity.canonical(value)
        facts.append(replace(fact, symbol_key=identity.canonical(fact.symbol_key), value=value))
    return projection.with_document(projection.document_identity, facts)


def _conflict_options(code: str, symbol: str, attribute: str, left: dict, right: dict) -> Tuple[str, ...]:
    if code == "rename-divergence":
        return (
            f"keep the {left['projection']} name {_show(left['value'])} for {symbol}",
            f"keep the {right['projection']} name {_show(right['value'])} for {symbol}",
            "choose a third name and apply it to both projections",
            "keep both names as aliases and mark the symbol non-canonical until a principal chooses",
        )
    if code in ("rename-vs-delete", "add-vs-delete", "delete-vs-edit"):
        return (
            f"restore {symbol} from the {left['projection']} version ({right['projection']} proposed deletion)",
            f"accept the {right['projection']} deletion and revert the {left['projection']} edit",
            "split the symbol: keep the surviving version and record the removal separately",
        )
    if code == "type-divergence":
        return (
            f"keep the {left['projection']} shape for {attribute}",
            f"keep the {right['projection']} shape for {attribute}",
            "apply a widened type acceptable to both projections",
        )
    if code == "existence-divergence":
        return (
            f"keep {symbol} present (proposed by {left['projection']})",
            f"accept {symbol} as removed (proposed by {right['projection']})",
        )
    if code == "ambiguous-identity":
        return (
            f"declare an explicit alias for {symbol} in the baseline",
            "disambiguate the local keys so each names one canonical symbol",
        )
    return (
        f"keep the {left['projection']} value {_show(left['value'])}",
        f"keep the {right['projection']} value {_show(right['value'])}",
    )


def _side(edit: SemanticEdit, change: Optional[Change]) -> dict:
    if change is None:
        return {"projection": edit.projection, "op": "change", "value": None, "old": None, "source": None, "kind": edit.kind}
    return {
        "projection": change.projection,
        "op": change.op,
        "value": change.new if change.new is not None else change.old,
        "old": change.old,
        "source": change.source.as_dict() if change.source else None,
        "kind": edit.kind,
    }


def _first_change(edit: SemanticEdit, attributes: Iterable[str]) -> Optional[Change]:
    wanted = set(attributes)
    for change in edit.changes:
        if change.attribute in wanted:
            return change
    return edit.changes[0] if edit.changes else None


def _competing_category(left: SemanticEdit, right: SemanticEdit) -> Optional[Tuple[str, Dict[str, str], Dict[str, str]]]:
    """How two edits of one symbol compete, or ``None`` when they are compatible.

    Only a shared category whose proposed values *differ* competes.  Values are
    compared through the crosswalk, so ``ID!`` and ``str`` are the same fact and
    ``DateTime!`` vs ``int`` is not.  Local attributes
    (``location.*``/``annotation.*``/``order.*``) never compete: each projection
    legitimately spells them its own way.
    """
    shared = categories(left.attributes) & categories(right.attributes)
    if not shared:
        return None
    lt = left.targets()
    rt = right.targets()
    for category in sorted(shared):
        prefixes = CATEGORY_PREFIXES[category]
        l_sub = {
            a: v
            for a, v in lt.items()
            if any(a == p or a.startswith(p) for p in prefixes) and not is_local(a)
        }
        r_sub = {
            a: v
            for a, v in rt.items()
            if any(a == p or a.startswith(p) for p in prefixes) and not is_local(a)
        }
        if not l_sub or not r_sub:
            continue
        if _values_differ(left.projection, right.projection, l_sub, r_sub):
            return category, l_sub, r_sub
    return None


def _values_differ(
    left_projection: str, right_projection: str, left: Mapping[str, str], right: Mapping[str, str]
) -> bool:
    """True when two sides' proposed values really disagree (crosswalk-aware)."""
    for attribute in sorted(set(left) & set(right)):
        left_value, right_value = left[attribute], right[attribute]
        if is_local(attribute):
            continue
        if left_value == ABSENT or right_value == ABSENT:
            return left_value != right_value
        if cross_values_differ(attribute, left_value, right_value, left_projection, right_projection):
            return True
    return False


def _conflict_code(category: str, left: SemanticEdit, right: SemanticEdit) -> str:
    kinds = {left.kind, right.kind}
    if "delete" in kinds and "rename" in kinds:
        return "rename-vs-delete"
    if "delete" in kinds and "add" in kinds:
        return "add-vs-delete"
    if "delete" in kinds:
        return "delete-vs-edit"
    if category == "name":
        return "rename-divergence"
    if category == "existence":
        return "existence-divergence"
    if category in ("type", "callable"):
        return "type-divergence"
    return "value-divergence"


def plan_reconciliation(
    prior: FactSet,
    projections: Sequence[Projection],
    identity: IdentityResolver,
    *,
    auto_apply_divergence: bool = False,
    policy: Optional[Mapping[str, str]] = None,
    waivers: Optional[Mapping[str, Mapping]] = None,
    enforce_invariants: bool = True,
) -> Tuple[Plan, IdentityResolver, Dict[str, List[SemanticEdit]], Dict[str, IdentityReport]]:
    """Compute the merge plan without mutating anything.

    Returns ``(plan, identity, edits_by_projection, identity_reports)``.  The
    returned identity resolver is the one that was used, with the renames this
    run recognised applied; the input resolver is not mutated.
    """
    policy = dict(policy if policy is not None else DEFAULT_POLICY)
    waivers = dict(waivers or {})
    plan = Plan()
    names = [projection.name for projection in projections]

    if not projections:
        plan.ok = False
        plan.blocked.append("no projections supplied")
        return plan, identity, {}, {}

    # -- 1. identity --------------------------------------------------------
    current_identity = identity
    reports: Dict[str, IdentityReport] = {}
    resolved: Dict[str, Projection] = {}
    for projection in projections:
        current_identity, report = match_identities(
            prior.facts, projection.facts, current_identity, projection.name
        )
        reports[projection.name] = report
        resolved[projection.name] = _canonical_projection(projection, current_identity)

    renamed_rows: List[dict] = []
    unresolved_rows: List[dict] = []
    new_rows: Dict[str, List[str]] = {}
    removed_rows: Dict[str, List[str]] = {}
    for name, report in sorted(reports.items()):
        for match in report.renamed:
            renamed_rows.append({"projection": name, **match.as_dict()})
        for match in report.unresolved:
            unresolved_rows.append({"projection": name, **match.as_dict()})
        new_rows[name] = sorted(report.new)
        removed_rows[name] = sorted(report.removed)
    plan.identity_report = {
        "renamed": renamed_rows,
        "unresolved": unresolved_rows,
        "new": new_rows,
        "removed": removed_rows,
    }

    # -- 1b. the reconciler's own invariants (R3/R4) ------------------------
    if enforce_invariants:
        acyclic = check_acyclic_aliases(current_identity.moves(), waivers.get("RECONCILE_ACYCLIC"))
        if isinstance(acyclic, InvariantRejection):
            plan.invariant_violations.append("RECONCILE_ACYCLIC: cycle in the canonical alias graph")
        claims: Dict[str, List[str]] = {}
        for name, report in sorted(reports.items()):
            for match in report.unresolved:
                claims[f"{name}.{match.candidate_key}"] = list(match.alternatives)
        unique = check_unique_aliases(claims, waivers.get("RECONCILE_UNIQUE_ALIAS"))
        if isinstance(unique, InvariantRejection):
            # One conflict per ambiguous local key, so each carries its own
            # actionable option set and both candidate readings survive.
            for key, targets in sorted(claims.items()):
                plan.conflicts.append(
                    Conflict(
                        code="ambiguous-identity",
                        symbol_key=key,
                        attribute="existence",
                        message=(
                            f"local key {key!r} could denote {len(targets)} prior symbols "
                            f"({', '.join(targets)}); identity is ambiguous"
                        ),
                        left={"projection": "schema", "op": "match", "value": key, "old": None, "source": None},
                        right={
                            "projection": "code",
                            "op": "match",
                            "value": ", ".join(targets),
                            "old": None,
                            "source": None,
                        },
                        options=_conflict_options("ambiguous-identity", key, "existence", {"projection": "schema", "value": key}, {"projection": "code", "value": ", ".join(targets)}),
                    )
                )

    # -- 2/3. per-projection diff + classification --------------------------
    edits_by_projection: Dict[str, List[SemanticEdit]] = {}
    for projection in projections:
        changes = identity_diff(prior, resolved[projection.name])
        rename_map = {m.canonical_key: m.candidate_key for m in reports[projection.name].renamed}
        edits = classify_symbols(changes, rename_map)
        edits_by_projection[projection.name] = edits
        for edit in edits:
            plan.edit_counts[edit.kind] = plan.edit_counts.get(edit.kind, 0) + 1

    # -- 4. merge -----------------------------------------------------------
    by_symbol: Dict[Tuple[str, str], SemanticEdit] = {}
    for name in names:
        for edit in edits_by_projection.get(name, ()):
            by_symbol[(edit.symbol_key, edit.projection)] = edit

    for symbol in sorted({key for key, _ in by_symbol}):
        sides: Dict[str, SemanticEdit] = {
            name: by_symbol[(symbol, name)] for name in names if (symbol, name) in by_symbol
        }
        if not sides:
            continue
        active = sorted(sides)
        conflicting = False

        if len(active) == 2:
            left_edit, right_edit = sides[active[0]], sides[active[1]]
            competition = _competing_category(left_edit, right_edit)
            if competition is not None:
                category, l_sub, r_sub = competition
                code = _conflict_code(category, left_edit, right_edit)
                attributes = sorted(set(l_sub) | set(r_sub))
                attribute = attributes[0] if attributes else "name"
                left_side = _side(left_edit, _first_change(left_edit, l_sub or r_sub))
                right_side = _side(right_edit, _first_change(right_edit, r_sub or l_sub))
                conflict = Conflict(
                    code=code,
                    symbol_key=symbol,
                    attribute=attribute,
                    message=(
                        f"{active[0]} {left_edit.kind} and {active[1]} {right_edit.kind} both change "
                        f"{symbol}.{attribute}"
                    ),
                    left=left_side,
                    right=right_side,
                    options=_conflict_options(code, symbol, attribute, left_side, right_side),
                )
                affected = set(attributes)
                plan.held.extend(
                    change
                    for edit in (left_edit, right_edit)
                    for change in edit.changes
                    if change.attribute in affected
                )
                if auto_apply_divergence and category in policy:
                    winner = policy[category]
                    chosen = l_sub if winner == active[0] else r_sub if winner == active[1] else {}
                    for attr, value in sorted(chosen.items()):
                        plan.resolutions.append(
                            Resolution(
                                symbol,
                                attr,
                                value,
                                POLICY,
                                f"policy: {category} -> {winner}",
                                (winner,),
                            )
                        )
                else:
                    plan.conflicts.append(conflict)
                    conflicting = True

        if conflicting:
            continue

        # compatible path: shared values converge, single-side values apply
        per_attribute: Dict[str, Dict[str, str]] = {}
        for name in active:
            for attr, value in sides[name].targets().items():
                per_attribute.setdefault(attr, {})[name] = value

        for symbol_change in sides.values():
            plan.auto_applied.extend(symbol_change.changes)

        for attr in sorted(per_attribute):
            values = per_attribute[attr]
            if is_local(attr):
                # Each projection owns its own spelling; both are accepted.
                for name in sorted(values):
                    plan.resolutions.append(
                        Resolution(symbol, attr, values[name], SINGLE_SIDE, f"local to {name}", (name,))
                    )
                continue
            distinct = {cross_value(name, attr, values[name]) for name in values}
            if len(values) > 1 and len(distinct) == 1:
                agreed = sorted(values)[0]
                plan.resolutions.append(
                    Resolution(
                        symbol,
                        attr,
                        values[agreed],
                        CONVERGING,
                        f"both projections agree on {attr}",
                        tuple(sorted(values)),
                    )
                )
            else:
                owner = sorted(values)[0]
                origin = SINGLE_SIDE if len(active) == 1 else DISJOINT
                detail = (
                    f"only {owner} changed {attr}"
                    if origin == SINGLE_SIDE
                    else f"{owner} changed {attr}; the other side edited {symbol} elsewhere"
                )
                plan.resolutions.append(Resolution(symbol, attr, values[owner], origin, detail, (owner,)))

    plan.auto_applied.sort(key=lambda c: (c.symbol_key, c.projection, c.attribute, c.op))
    plan.held.sort(key=lambda c: (c.symbol_key, c.projection, c.attribute, c.op))
    plan.conflicts.sort(key=lambda c: (c.code, c.symbol_key, c.attribute))
    plan.resolutions.sort(key=lambda r: (r.symbol_key, r.attribute, r.origin))
    plan.ok = not plan.conflicts and not plan.blocked and not plan.invariant_violations
    return plan, current_identity, edits_by_projection, reports


# ---------------------------------------------------------------------------
# State synthesis.
# ---------------------------------------------------------------------------


def _synthesize_body(prior: FactSet, plan: Plan, projections: Sequence[Projection]) -> FactSet:
    """Apply the plan's resolutions to the prior agreed body.

    Held (conflicting) changes are deliberately **not** applied, so the
    conflicting facts remain exactly as agreed and both versions survive: the
    baseline plus the two projections carry the full content of both sides.
    """
    prior_index = _fact_index(prior.facts)
    current_index: Dict[str, Dict[Tuple[str, str], Fact]] = {}
    for projection in projections:
        index = current_index.setdefault(projection.name, {})
        for fact in projection.facts:
            index[(fact.symbol_key, fact.attribute)] = fact

    updates: Dict[Tuple[str, str, str], Fact] = {}
    for resolution in plan.resolutions:
        for origin in resolution.origins:
            key = (resolution.symbol_key, resolution.attribute)
            current = current_index.get(origin, {}).get(key)
            if current is None:
                current = prior_index.get(key)
                if current is None or current.projection != origin:
                    continue
            if resolution.value == ABSENT:
                updates[current.key()] = replace(current, value=ABSENT)
            else:
                updates[current.key()] = replace(current, value=resolution.value)
    return prior.with_updates(updates)


def synthesize(
    prior: FactSet,
    plan: Plan,
    identity: IdentityResolver,
    projections: Sequence[Projection],
) -> Tuple[FactSet, Projection]:
    """Apply the plan and return ``(merged body, merged projection)``."""
    body = _synthesize_body(prior, plan, projections)
    digest = body.digest
    merged = Projection(
        name="merged",
        document_identity=f"reconciled:merged:{digest[:32]}",
        facts=body.facts,
        source_map={},
    )
    return body, merged


# ---------------------------------------------------------------------------
# The reconciler entry point.
# ---------------------------------------------------------------------------


class Reconciler:
    """Reconciles schema and code projections against one accepted baseline.

    Two independent mechanisms stop a stale document version from overwriting
    newer work, because either alone is insufficient:

    * **document identity** — the caller declares which *current* document each
      projection was read from (``current_documents``), and a projection whose
      facts came from any other version is refused
      (:meth:`staleness` / :class:`StaleView`);
    * **baseline revision** — :class:`~leviath_reconcile.baseline.BaselineStore`
      commits compare-and-set against the live revision, so a writer that read
      an older baseline cannot commit over a newer one.

    The reconciler itself is also *contract-pinned*: when an expected fingerprint
    is given, it is re-verified against the artifact set on disk before any
    merge, so a drifted contract blocks reconciliation instead of being merged
    as if it were accepted.
    """

    def __init__(
        self,
        baseline: Optional[Baseline] = None,
        *,
        auto_apply_divergence: bool = False,
        policy: Optional[Mapping[str, str]] = None,
        current_documents: Optional[Mapping[str, str]] = None,
        expected_contract_fingerprint: str = "",
        contract_dir: Optional[str] = None,
        contract_sdl: Optional[str] = None,
    ) -> None:
        self.baseline = baseline if baseline is not None else Baseline()
        self.auto_apply_divergence = auto_apply_divergence
        self.policy = dict(policy if policy is not None else DEFAULT_POLICY)
        #: projection name -> the document identity that is current *now*.
        self.current_documents: Dict[str, str] = dict(current_documents or {})
        self.expected_contract_fingerprint = expected_contract_fingerprint
        self.contract_dir = contract_dir
        #: Optional override of the contract SDL path inside ``contract_dir``.
        self.contract_sdl = contract_sdl
        self._accepted: Optional[Baseline] = None

    # -- contract identity --------------------------------------------------
    def verify_contract(self) -> str:
        """Pin-then-re-verify the accepted contract fingerprint.

        No-op when no fingerprint was pinned.  Raises
        :class:`~leviath_reconcile.adapters.schema_adapter.ContractDrift` when the
        artifact set on disk is not the accepted one.  Called at the start of
        every :meth:`plan`/:meth:`reconcile`/:meth:`accept` so a dependent's
        implementation cannot silently run against a moved contract.
        """
        if not self.expected_contract_fingerprint:
            return ""
        if self.contract_dir is None:
            raise ValueError(
                "expected_contract_fingerprint was pinned but no contract_dir was given"
            )
        from .adapters.schema_adapter import verify_contract as _verify

        return _verify(
            self.expected_contract_fingerprint, self.contract_dir, sdl_path=self.contract_sdl
        )

    # -- document identity --------------------------------------------------
    def declare(self, documents: Mapping[str, str]) -> None:
        """Declare the current document identity of each projection."""
        self.current_documents.update({str(k): str(v) for k, v in documents.items()})

    # -- staleness ----------------------------------------------------------
    def staleness(self, projection: Projection, declared_document: Optional[str] = None) -> List[str]:
        """Reasons this projection may not be used to overwrite the baseline."""
        reasons: List[str] = []
        base = self.baseline
        current = declared_document
        if current is None:
            current = self.current_documents.get(projection.name)
        if current is not None and current != projection.document_identity:
            reasons.append(
                f"{projection.name}: facts were read from document {projection.document_identity!r} "
                f"but {current!r} is current"
            )
        if base.documents.get(projection.name) is None and base.facts:
            reasons.append(
                f"{projection.name}: unknown to a baseline that already holds agreed facts "
                "(partial or stale view)"
            )
        if not projection.facts and base.documents.get(projection.name) is not None:
            reasons.append(
                f"{projection.name}: empty projection against a baseline with agreed "
                f"{projection.name} facts (refusing to read a failed extraction as a deletion)"
            )
        return reasons

    # -- planning -----------------------------------------------------------
    def plan(
        self,
        schema: Projection,
        code: Projection,
        *,
        waivers: Optional[Mapping[str, Mapping]] = None,
    ) -> Plan:
        self.verify_contract()
        plan, _identity, _edits, _reports = plan_reconciliation(
            self.baseline.facts,
            (schema, code),
            self.baseline.identity,
            auto_apply_divergence=self.auto_apply_divergence,
            policy=self.policy,
            waivers=waivers,
        )
        for projection in (schema, code):
            plan.stale.extend(self.staleness(projection))
        if plan.stale:
            plan.blocked.extend(plan.stale)
            plan.ok = False
        return plan

    # -- full reconcile -----------------------------------------------------
    def reconcile(
        self,
        schema: Projection,
        code: Projection,
        *,
        waivers: Optional[Mapping[str, Mapping]] = None,
    ) -> Outcome:
        """Reconcile both projections; never raises merely because of conflicts."""
        self.verify_contract()
        plan, identity, _edits, _reports = plan_reconciliation(
            self.baseline.facts,
            (schema, code),
            self.baseline.identity,
            auto_apply_divergence=self.auto_apply_divergence,
            policy=self.policy,
            waivers=waivers,
        )
        for projection in (schema, code):
            plan.stale.extend(self.staleness(projection))
        if plan.stale:
            plan.blocked.extend(plan.stale)
            plan.ok = False

        canonical = tuple(_canonical_projection(projection, identity) for projection in (schema, code))
        body, merged = synthesize(self.baseline.facts, plan, identity, canonical)
        changed = body.digest != self.baseline.facts.digest or bool(plan.held)
        if plan.blocked:
            body = self.baseline.facts
            merged = Projection(
                name="merged",
                document_identity=f"reconciled:merged:{body.digest[:32]}",
                facts=body.facts,
            )
            changed = False
        return Outcome(
            plan=plan,
            body=body,
            identity=identity,
            projection=merged,
            changed=changed,
            sources=canonical,
        )

    # -- commit -------------------------------------------------------------
    def accept(
        self,
        schema: Projection,
        code: Projection,
        *,
        waivers: Optional[Mapping[str, Mapping]] = None,
        accepted_at: str = "",
    ) -> Outcome:
        """Reconcile, require zero conflicts, then name the resulting state.

        Raises :class:`StaleView` for a blocked/stale plan and
        :class:`ReconcileConflict` when conflicts exist, because an accepted
        baseline must be unambiguous.
        """
        outcome = self.reconcile(schema, code, waivers=waivers)
        if outcome.plan.stale:
            raise StaleView("; ".join(outcome.plan.stale))
        if outcome.plan.blocked:
            raise StaleView("; ".join(outcome.plan.blocked))
        if outcome.plan.conflicts:
            raise ReconcileConflict(outcome.plan)
        outcome.check_idempotent()
        outcome.plan.resolutions.append(
            Resolution(
                "<baseline>",
                "accept",
                outcome.body.digest,
                ACCEPTED,
                f"revision {self.baseline.revision + 1}",
                (),
            )
        )
        outcome.plan.resolutions.sort(key=lambda r: (r.symbol_key, r.attribute, r.origin))
        self._accepted = Baseline(
            revision=self.baseline.revision,
            contract=self.baseline.contract,
            contract_fingerprint=(
                self.expected_contract_fingerprint or self.baseline.contract_fingerprint
            ),
            documents={schema.name: schema.document_identity, code.name: code.document_identity},
            digests={schema.name: schema.digest, code.name: code.digest},
            body=outcome.body,
            identity=outcome.identity,
            accepted_at=accepted_at,
        )
        return outcome

    @property
    def accepted(self) -> Baseline:
        """The baseline produced by the most recent :meth:`accept`."""
        if self._accepted is None:
            raise RuntimeError("accept() has not been called")
        return self._accepted


def reconciler_for(baseline: Baseline, **kwargs) -> Reconciler:
    return Reconciler(baseline, **kwargs)
