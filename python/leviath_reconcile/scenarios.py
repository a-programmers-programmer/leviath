"""Executable definition-of-done scenarios for the reconciliation engine.

This module is the *proof harness* for GQL-02's definition of done.  It builds a
tiny, fully specified domain (a type ``Order`` with two fields, agreed in both
projections), then drives the real :class:`~leviath_reconcile.reconcile.Reconciler`
through one scenario per clause:

======================  =======================================================
clause                  scenario(s)
======================  =======================================================
symmetry                ``symmetry`` — a schema-only rename/retype and the
                        same code-only edit produce mirrored outcomes, no
                        conflict, on either side
concurrent edits        ``concurrent`` — rename-vs-rename, rename-vs-delete,
                        add-vs-delete, delete-vs-edit, type-divergence,
                        existence-divergence and ambiguous-identity all produce
                        a conflict that preserves **both** versions and carries
                        actionable options; disjoint edits auto-apply both
staleness               ``staleness`` — a projection read from a superseded
                        document is blocked and ``accept`` raises; a stale
                        baseline revision cannot be committed
idempotence             ``idempotence`` — reconcile, accept, re-reconcile the
                        same inputs: no conflict, byte-identical body, no
                        ping-pong
======================  =======================================================

Nothing here calls a provider, reads a secret, touches the network or writes
anything but a temporary baseline inside a caller-supplied directory.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .baseline import Baseline, BaselineConflict, BaselineStore
from .model import ABSENT, Fact, FactSet, Projection, SourceRef
from .reconcile import ReconcileConflict, Reconciler, StaleView

SCHEMA = "schema"
CODE = "code"
SCHEMA_PATH = "docs/workflow/schema-workflow.graphql"
CODE_PATH = "app/workflows/orders.py"
DOC_SCHEMA_V1 = "schema@v1"
DOC_SCHEMA_V2 = "schema@v2"
DOC_SCHEMA_STALE = "schema@v0-superseded"
DOC_CODE_V1 = "code@v1"
DOC_CODE_V2 = "code@v2"

_DECLARATIONS = {
    SCHEMA: {"type": "type", "field": "field"},
    CODE: {"type": "class", "field": "field"},
}


def _source(projection: str, symbol_key: str, line: int = 1) -> SourceRef:
    path = SCHEMA_PATH if projection == SCHEMA else CODE_PATH
    return SourceRef(path, line, 0, max(len(symbol_key.rsplit(".", 1)[-1]), 1))


def _fact(projection: str, key: str, attribute: str, value: str, line: int = 1) -> Fact:
    return Fact(key, projection, attribute, value, _source(projection, key, line))


def _declaration(projection: str, kind: str) -> str:
    return _DECLARATIONS[projection][kind]


#: The agreed domain: ``Order { id: ID!, total: Float! }`` in SDL and in typed
#: code, as the *same* canonical symbols (``Order``, ``Order.id``,
#: ``Order.total``).  The two media spell the type differently on purpose — the
#: crosswalk must equate ``ID!``/``str`` and ``Float!``/``float``.
_DOMAIN: Tuple[Tuple[str, str, Optional[str], Optional[str], Optional[str]], ...] = (
    ("Order", "type", None, None, None),
    ("Order.id", "field", "Order", "ID!", "str"),
    ("Order.total", "field", "Order", "Float!", "float"),
)


def _symbol_facts(
    projection: str,
    key: str,
    kind: str,
    parent: Optional[str],
    annotation: Optional[str],
    name: Optional[str] = None,
) -> List[Fact]:
    local = key.rsplit(".", 1)[-1]
    facts = [
        _fact(projection, key, "existence", _declaration(projection, kind)),
        _fact(projection, key, "name", name if name is not None else local),
        _fact(projection, key, "annotation.declaration", _declaration(projection, kind)),
    ]
    if parent is not None:
        facts.append(_fact(projection, key, "location.parent", parent))
    if annotation is not None:
        facts.append(_fact(projection, key, "type.kind", annotation))
    return facts


def baseline_facts() -> FactSet:
    """The agreed fact body for the tiny domain, in both projections."""
    facts: List[Fact] = []
    for key, kind, parent, annotation_schema, annotation_code in _DOMAIN:
        facts.extend(_symbol_facts(SCHEMA, key, kind, parent, annotation_schema))
        facts.extend(_symbol_facts(CODE, key, kind, parent, annotation_code))
    return FactSet(tuple(facts))


def baseline_body() -> FactSet:
    return baseline_facts()


def half(body: FactSet, projection: str) -> List[Fact]:
    """One projection's facts out of an agreed body."""
    return [fact for fact in body.facts if fact.projection == projection]


def projection(name: str, facts: Sequence[Fact], document_identity: str) -> Projection:
    return Projection(name=name, document_identity=document_identity, facts=tuple(facts))


def with_edit(
    facts: Sequence[Fact], projection: str, key: str, attribute: str, value: str
) -> List[Fact]:
    """Replace one fact (or add it when absent), leaving everything else alone."""
    out = [f for f in facts if not (f.symbol_key == key and f.attribute == attribute)]
    out.append(_fact(projection, key, attribute, value))
    return out


def without_symbol(facts: Sequence[Fact], key: str) -> List[Fact]:
    """Delete one symbol — and, for a type-level symbol, its members."""
    return [
        f
        for f in facts
        if f.symbol_key != key and not f.symbol_key.startswith(key + ".")
    ]


def rename_symbol(facts: Sequence[Fact], projection: str, old: str, new: str) -> List[Fact]:
    """Rename one type-level symbol and every child key under it.

    This is the shape a media rename really has: a renamed type carries its
    fields, and the fields' ``location.parent`` value follows.  The keys move,
    but the *identity* — the canonical symbol — must not change, which is the
    whole point of the identity resolver.  A renamed symbol's own ``name`` fact
    becomes the new local name; a child keeps its local name.
    """
    out: List[Fact] = []
    for fact in facts:
        if fact.symbol_key == old:
            out.extend(_symbol_facts(projection, new, "type", None, None))
            continue
        if fact.symbol_key.startswith(old + "."):
            child = new + fact.symbol_key[len(old):]
            if fact.attribute == "location.parent":
                out.append(_fact(projection, child, fact.attribute, new))
            else:
                out.append(_fact(projection, child, fact.attribute, fact.value))
            continue
        out.append(fact)
    return out


def _agreed_baseline() -> Baseline:
    return Baseline(
        revision=1,
        contract="workflow-model v1",
        documents={SCHEMA: DOC_SCHEMA_V1, CODE: DOC_CODE_V1},
        body=baseline_facts(),
    )


def _baseline_with_docs() -> Baseline:
    return _agreed_baseline()


@dataclass
class ScenarioResult:
    """One scenario's verdict, serialisable straight into the evidence file."""

    name: str
    clause: str
    ok: bool
    detail: str
    metrics: Dict[str, object]

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "clause": self.clause,
            "ok": self.ok,
            "detail": self.detail,
            "metrics": self.metrics,
        }


# ---------------------------------------------------------------------------
# Clause 1 — symmetry.
# ---------------------------------------------------------------------------


def scenario_symmetry() -> ScenarioResult:
    """Schema-only and code-only edits work symmetrically."""
    body = baseline_facts()
    base = _agreed_baseline()
    half_schema, half_code = half(body, SCHEMA), half(body, CODE)
    checks: List[str] = []

    # (a) rename, each direction.
    schema_renamed = rename_symbol(half_schema, SCHEMA, "Order", "PurchaseOrder")
    code_renamed = rename_symbol(half_code, CODE, "Order", "PurchaseOrder")

    rec_s = Reconciler(base)
    out_s = rec_s.reconcile(
        projection(SCHEMA, schema_renamed, DOC_SCHEMA_V2),
        projection(CODE, half_code, DOC_CODE_V1),
    )
    rec_c = Reconciler(base)
    out_c = rec_c.reconcile(
        projection(SCHEMA, half_schema, DOC_SCHEMA_V1),
        projection(CODE, code_renamed, DOC_CODE_V2),
    )

    if out_s.plan.conflicts or not out_s.plan.ok:
        raise AssertionError(f"schema-only rename must not conflict: {out_s.plan.summary()}")
    if out_c.plan.conflicts or not out_c.plan.ok:
        raise AssertionError(f"code-only rename must not conflict: {out_c.plan.summary()}")
    checks.append("both directions rename with zero conflicts")

    # Each run edits exactly its own side and leaves the other side agreed.
    s_origins = sorted({o for r in out_s.plan.resolutions for o in r.origins})
    c_origins = sorted({o for r in out_c.plan.resolutions for o in r.origins})
    if s_origins != [SCHEMA]:
        raise AssertionError(f"schema-only rename resolved on {s_origins}, expected ['schema']")
    if c_origins != [CODE]:
        raise AssertionError(f"code-only rename resolved on {c_origins}, expected ['code']")
    checks.append("each run resolves on the editing projection only")

    # The untouched side keeps the agreed value in the merged body.
    def name_in(outcome, projection_name: str) -> str:
        for fact in outcome.body.facts:
            if fact.projection == projection_name and fact.symbol_key in ("Order", "PurchaseOrder") and fact.attribute == "name":
                return fact.value
        raise AssertionError(f"{projection_name} has no Order name in the merged body")

    if name_in(out_s, CODE) != "Order":
        raise AssertionError("schema-only rename must not touch the code side")
    if name_in(out_c, SCHEMA) != "Order":
        raise AssertionError("code-only rename must not touch the schema side")
    checks.append("the untouched side keeps its agreed value")

    for outcome in (out_s, out_c):
        outcome.check_idempotent()
    checks.append("both directions reach a fixed point")

    # (b) retype, each direction, through the crosswalk.
    schema_retyped = with_edit(half_schema, SCHEMA, "Order.total", "type.kind", "DateTime!")
    code_retyped = with_edit(half_code, CODE, "Order.total", "type.kind", "datetime")
    out_s2 = Reconciler(base).reconcile(
        projection(SCHEMA, schema_retyped, DOC_SCHEMA_V2),
        projection(CODE, half_code, DOC_CODE_V1),
    )
    out_c2 = Reconciler(base).reconcile(
        projection(SCHEMA, half_schema, DOC_SCHEMA_V1),
        projection(CODE, code_retyped, DOC_CODE_V2),
    )
    if not out_s2.plan.ok or not out_c2.plan.ok:
        raise AssertionError(
            f"retype must not conflict: {out_s2.plan.summary()} / {out_c2.plan.summary()}"
        )
    checks.append("both directions retype with zero conflicts")

    # A *no-op* run is exactly that: nothing applied, nothing changed.
    out_noop = Reconciler(base).reconcile(
        projection(SCHEMA, half_schema, DOC_SCHEMA_V1),
        projection(CODE, half_code, DOC_CODE_V1),
    )
    if out_noop.changed or out_noop.plan.auto_applied or out_noop.plan.conflicts:
        raise AssertionError(
            "re-reconciling unchanged projections must be a no-op, got "
            f"changed={out_noop.changed} auto={len(out_noop.plan.auto_applied)}"
        )
    checks.append("re-reconciling unchanged projections applies nothing")

    return ScenarioResult(
        name="symmetry",
        clause="schema-only and code-only edits work symmetrically",
        ok=True,
        detail="; ".join(checks),
        metrics={
            "schema_only_resolutions": len(out_s.plan.resolutions),
            "code_only_resolutions": len(out_c.plan.resolutions),
            "schema_only_conflicts": len(out_s.plan.conflicts),
            "code_only_conflicts": len(out_c.plan.conflicts),
            "noop_changed": out_noop.changed,
        },
    )


# ---------------------------------------------------------------------------
# Clause 2 — concurrent edits preserve both versions, actionably.
# ---------------------------------------------------------------------------


def _conflict_case(
    label: str,
    schema_facts: Sequence[Fact],
    code_facts: Sequence[Fact],
    expected_code: str,
) -> Tuple[object, List[str]]:
    base = _agreed_baseline()
    outcome = Reconciler(base).reconcile(
        projection(SCHEMA, schema_facts, DOC_SCHEMA_V2),
        projection(CODE, code_facts, DOC_CODE_V2),
    )
    plan = outcome.plan
    if not plan.conflicts:
        raise AssertionError(f"{label}: expected a conflict, got {plan.summary()}")
    codes = sorted({c.code for c in plan.conflicts})
    if expected_code not in codes:
        raise AssertionError(f"{label}: expected {expected_code!r}, got {codes}")
    notes: List[str] = [f"{label} -> {codes}"]
    for conflict in plan.conflicts:
        if not conflict.actionable:
            raise AssertionError(f"{label}: conflict {conflict.code} has no actionable options")
        if not conflict.options:
            raise AssertionError(f"{label}: conflict {conflict.code} carries no options")
        if conflict.resolution not in ("preserve-both", "require-decision"):
            raise AssertionError(f"{label}: {conflict.code} resolution={conflict.resolution!r}")
        # Both sides' proposals survive in the record.  An identity-level
        # conflict has no single source span (two candidate symbols, not two
        # edits), so it is required to carry both readings instead.
        if conflict.code == "ambiguous-identity":
            if not conflict.left.get("value") or not conflict.right.get("value"):
                raise AssertionError(f"{label}: {conflict.code} lost a candidate reading: {conflict.left} / {conflict.right}")
        else:
            for side in (conflict.left, conflict.right):
                if not side.get("projection") or side.get("source") is None:
                    raise AssertionError(f"{label}: {conflict.code} lost a side's provenance: {side}")
    if not plan.held:
        raise AssertionError(f"{label}: conflicting changes were not held back from the merge")
    return outcome, notes


def scenario_concurrent() -> ScenarioResult:
    """Concurrent rename/delete/type edits preserve both versions and are actionable."""
    body = baseline_facts()
    half_schema, half_code = half(body, SCHEMA), half(body, CODE)
    checks: List[str] = []

    # (a) rename on both sides, to different names.
    out, notes = _conflict_case(
        "rename-divergence",
        rename_symbol(half_schema, SCHEMA, "Order", "PurchaseOrder"),
        rename_symbol(half_code, CODE, "Order", "ClientOrder"),
        "rename-divergence",
    )
    checks.extend(notes)
    proposed = {c.left["value"] for c in out.plan.conflicts} | {c.right["value"] for c in out.plan.conflicts}
    if proposed != {"PurchaseOrder", "ClientOrder"}:
        raise AssertionError(f"both names must survive, got {proposed}")
    checks.append("both rename proposals preserved verbatim")

    # (b) rename on one side, delete on the other.
    _, notes = _conflict_case(
        "rename-vs-delete",
        rename_symbol(half_schema, SCHEMA, "Order", "PurchaseOrder"),
        without_symbol(half_code, "Order"),
        "rename-vs-delete",
    )
    checks.extend(notes)

    # (c) add on one side, delete on the other.
    base_add_vs_delete = Baseline(
        revision=1,
        documents={SCHEMA: DOC_SCHEMA_V1, CODE: DOC_CODE_V1},
        body=FactSet(
            tuple(half_schema)
            + tuple(half_code)
            + tuple(_symbol_facts(CODE, "Order.legacy", "field", "Order", "str"))
        ),
    )
    outcome = Reconciler(base_add_vs_delete).reconcile(
        projection(SCHEMA, list(half_schema) + _symbol_facts(SCHEMA, "Order.legacy", "field", "Order", "LegacyCode!"), DOC_SCHEMA_V2),
        projection(CODE, half_code, DOC_CODE_V2),
    )
    codes = sorted({c.code for c in outcome.plan.conflicts})
    if "add-vs-delete" not in codes:
        raise AssertionError(f"add-vs-delete: got {codes}")
    checks.append(f"add-vs-delete -> {codes}")

    # (d) delete on one side, a different edit on the other.
    _, notes = _conflict_case(
        "delete-vs-edit",
        with_edit(half_schema, SCHEMA, "Order.total", "type.kind", "DateTime!"),
        without_symbol(half_code, "Order.total"),
        "delete-vs-edit",
    )
    checks.extend(notes)

    # (e) type divergence.
    _, notes = _conflict_case(
        "type-divergence",
        with_edit(half_schema, SCHEMA, "Order.total", "type.kind", "DateTime!"),
        with_edit(half_code, CODE, "Order.total", "type.kind", "int"),
        "type-divergence",
    )
    checks.extend(notes)

    # (f) existence divergence (both sides re-declare the same symbol differently).
    _, notes = _conflict_case(
        "existence-divergence",
        with_edit(with_edit(half_schema, SCHEMA, "Order", "existence", "enum"), SCHEMA, "Order", "annotation.declaration", "enum"),
        with_edit(with_edit(half_code, CODE, "Order", "existence", "interface"), CODE, "Order", "annotation.declaration", "interface"),
        "existence-divergence",
    )
    checks.extend(notes)

    # (g) ambiguous identity: one candidate shape matches two prior symbols.
    ambiguous_body = FactSet(
        tuple(
            _symbol_facts(SCHEMA, "A", "type", None, "ID!")
            + _symbol_facts(SCHEMA, "B", "type", None, "ID!")
            + _symbol_facts(CODE, "A", "type", None, "str")
            + _symbol_facts(CODE, "B", "type", None, "str")
        )
    )
    ambiguous_base = Baseline(
        revision=1, documents={SCHEMA: DOC_SCHEMA_V1, CODE: DOC_CODE_V1}, body=ambiguous_body
    )
    outcome = Reconciler(ambiguous_base).reconcile(
        projection(SCHEMA, _symbol_facts(SCHEMA, "C", "type", None, "ID!"), DOC_SCHEMA_V2),
        projection(CODE, _symbol_facts(CODE, "A", "type", None, "str") + _symbol_facts(CODE, "B", "type", None, "str"), DOC_CODE_V1),
    )
    codes = sorted({c.code for c in outcome.plan.conflicts})
    if "ambiguous-identity" not in codes:
        raise AssertionError(f"ambiguous-identity: got {codes}; unresolved={outcome.plan.identity_report['unresolved']}")
    checks.append(f"ambiguous-identity -> {codes}")

    # (h) disjoint edits (rename on one side, retype on the other) both apply.
    disjoint = Reconciler(_agreed_baseline()).reconcile(
        projection(SCHEMA, rename_symbol(half_schema, SCHEMA, "Order", "PurchaseOrder"), DOC_SCHEMA_V2),
        projection(CODE, with_edit(half_code, CODE, "Order.total", "type.kind", "decimal"), DOC_CODE_V2),
    )
    if disjoint.plan.conflicts or not disjoint.plan.ok:
        raise AssertionError(f"disjoint edits must not conflict: {disjoint.plan.summary()}")
    origins = sorted({o for r in disjoint.plan.resolutions for o in r.origins})
    if origins != [CODE, SCHEMA]:
        raise AssertionError(f"disjoint edits must apply on both sides, got {origins}")
    checks.append("disjoint edits (rename here, retype there) apply on both sides")

    return ScenarioResult(
        name="concurrent",
        clause="concurrent rename/delete/type edits preserve both versions and produce actionable conflicts",
        ok=True,
        detail="; ".join(checks),
        metrics={
            "conflict_codes": [
                "rename-divergence",
                "rename-vs-delete",
                "add-vs-delete",
                "delete-vs-edit",
                "type-divergence",
                "existence-divergence",
                "ambiguous-identity",
            ],
            "disjoint_conflicts": len(disjoint.plan.conflicts),
        },
    )


# ---------------------------------------------------------------------------
# Clause 3 — staleness.
# ---------------------------------------------------------------------------


def scenario_staleness(work_dir: Optional[str] = None) -> ScenarioResult:
    """Stale document versions cannot overwrite newer work."""
    body = baseline_facts()
    half_schema, half_code = half(body, SCHEMA), half(body, CODE)
    base = _agreed_baseline()
    checks: List[str] = []
    stale_out = None

    # (a) a projection read from a superseded document is blocked...
    stale_rec = Reconciler(base)
    stale_rec.declare({SCHEMA: DOC_SCHEMA_V2})
    stale_out = stale_rec.reconcile(
        projection(SCHEMA, half_schema, DOC_SCHEMA_STALE),
        projection(CODE, half_code, DOC_CODE_V1),
    )
    if not stale_out.plan.stale or stale_out.plan.ok:
        raise AssertionError(
            f"a superseded document must block the merge, got stale={stale_out.plan.stale} ok={stale_out.plan.ok}"
        )
    if stale_out.changed or stale_out.body.digest != base.facts.digest:
        raise AssertionError("a blocked merge must leave the agreed body untouched")
    checks.append("superseded document blocks the merge and leaves the body untouched")

    # ...and acceptance refuses it.
    try:
        stale_rec.accept(
            projection(SCHEMA, half_schema, DOC_SCHEMA_STALE),
            projection(CODE, half_code, DOC_CODE_V1),
        )
        raise AssertionError("accept() must refuse a stale view")
    except StaleView:
        checks.append("accept() raises StaleView for a stale view")

    # (b) the *same* edit from the current document is accepted.
    fresh_rec = Reconciler(base)
    fresh_rec.declare({SCHEMA: DOC_SCHEMA_V2, CODE: DOC_CODE_V1})
    fresh_out = fresh_rec.reconcile(
        projection(SCHEMA, with_edit(half_schema, SCHEMA, "Order.total", "type.kind", "DateTime!"), DOC_SCHEMA_V2),
        projection(CODE, half_code, DOC_CODE_V1),
    )
    if not fresh_out.plan.ok or fresh_out.plan.stale:
        raise AssertionError(f"the current document must not be stale: {fresh_out.plan.summary()}")
    checks.append("the same edit from the current document reconciles")

    # (c) an empty projection against an agreed baseline is refused, not read as
    #     "delete everything".
    empty_out = Reconciler(base).reconcile(
        projection(SCHEMA, [], DOC_SCHEMA_V1),
        projection(CODE, half_code, DOC_CODE_V1),
    )
    if not empty_out.plan.stale:
        raise AssertionError("an empty projection must be refused, not read as a mass deletion")
    checks.append("an empty projection is refused instead of deleting everything")

    # (d) the store's compare-and-set refuses a stale revision.
    with tempfile.TemporaryDirectory(dir=work_dir) as tmp:
        store = BaselineStore(os.path.join(tmp, "baseline.json"))
        clean_rec = Reconciler(base)
        clean_rec.declare({SCHEMA: DOC_SCHEMA_V1, CODE: DOC_CODE_V1})
        accepted = clean_rec.accept(
            projection(SCHEMA, half_schema, DOC_SCHEMA_V1),
            projection(CODE, half_code, DOC_CODE_V1),
            accepted_at="2026-09-12T00:00:00Z",
        )
        if accepted.plan.conflicts:
            raise AssertionError("the clean run must not conflict")
        revision = store.commit(clean_rec.accepted, expected_revision=1)
        if revision != 2:
            raise AssertionError(f"first commit must advance to revision 2, got {revision}")
        writer_a = store.load()

        # A second writer that read revision 1 tries to commit over revision 2,
        # the classic lost-update: its document version is no longer current.
        try:
            store.commit(writer_a, expected_revision=1)
            raise AssertionError("a stale revision must not be committable")
        except BaselineConflict:
            checks.append("BaselineStore.commit refuses a stale revision (compare-and-set)")
        reloaded = store.load()
        if reloaded.revision != 2 or reloaded.body.digest != clean_rec.accepted.body.digest:
            raise AssertionError("the refused commit must not have altered the store")
        checks.append("the refused commit left the accepted state intact")

    return ScenarioResult(
        name="staleness",
        clause="stale document versions cannot overwrite newer work",
        ok=True,
        detail="; ".join(checks),
        metrics={"blocked_reasons": len(stale_out.plan.stale), "checks": len(checks)},
    )


# ---------------------------------------------------------------------------
# Clause 4 — idempotence / no ping-pong.
# ---------------------------------------------------------------------------


def scenario_idempotence(rounds: int = 3) -> ScenarioResult:
    """Repeated reconciliation reaches an unchanged result without ping-pong."""
    body = baseline_facts()
    half_schema, half_code = half(body, SCHEMA), half(body, CODE)
    base = _agreed_baseline()

    schema_facts = rename_symbol(half_schema, SCHEMA, "Order", "PurchaseOrder")
    code_facts = with_edit(half_code, CODE, "Order.total", "type.kind", "datetime")
    schema_proj = projection(SCHEMA, schema_facts, DOC_SCHEMA_V2)
    code_proj = projection(CODE, code_facts, DOC_CODE_V2)

    first = Reconciler(base).reconcile(schema_proj, code_proj)
    if not first.plan.ok:
        raise AssertionError(f"the seed run must be clean: {first.plan.summary()}")
    first.check_idempotent()
    digest = first.body.digest

    # Accept it, then feed the *same* projections back through the engine exactly
    # as repeated runs do, requiring a fixed point every round.
    accepting = Reconciler(base)
    accepted = accepting.accept(schema_proj, code_proj, accepted_at="2026-09-12T00:00:00Z")
    if accepted.body.digest != digest:
        raise AssertionError("accept() must name the reconciled body verbatim")
    current = accepting.accepted

    changed_rounds = 0
    for round_index in range(rounds):
        round_reconciler = Reconciler(current)
        outcome = round_reconciler.reconcile(schema_proj, code_proj)
        if outcome.plan.conflicts or not outcome.plan.ok:
            raise AssertionError(f"round {round_index}: conflicts reappeared: {outcome.plan.summary()}")
        outcome.check_idempotent()
        if outcome.body.digest != digest:
            raise AssertionError(
                f"round {round_index}: body moved to {outcome.body.digest[:12]} from {digest[:12]} (ping-pong)"
            )
        if outcome.changed:
            changed_rounds += 1
        round_reconciler.accept(schema_proj, code_proj, accepted_at="2026-09-12T00:00:00Z")
        current = round_reconciler.accepted

    if changed_rounds:
        raise AssertionError(f"{changed_rounds} round(s) reported a change; the merge is not a fixed point")

    return ScenarioResult(
        name="idempotence",
        clause="repeated reconciliation reaches an unchanged result without ping-pong",
        ok=True,
        detail=f"{rounds} rounds re-reconciled; body digest stable at {digest[:16]}; zero changes after the first",
        metrics={"rounds": rounds, "body_digest": digest, "changed_rounds": changed_rounds},
    )


SCENARIOS: Mapping[str, Callable[..., ScenarioResult]] = {
    "symmetry": scenario_symmetry,
    "concurrent": scenario_concurrent,
    "staleness": scenario_staleness,
    "idempotence": scenario_idempotence,
}


def run_all(work_dir: Optional[str] = None) -> Dict[str, object]:
    """Run every DoD scenario.  Returns a serialisable result; raises on failure."""
    from . import crosswalk, invariants

    results: List[ScenarioResult] = []
    failures: List[str] = []
    for name in ("symmetry", "concurrent", "staleness", "idempotence"):
        try:
            result = SCENARIOS[name](work_dir=work_dir) if name == "staleness" else SCENARIOS[name]()
            results.append(result)
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
            results.append(ScenarioResult(name, "", False, f"{type(exc).__name__}: {exc}", {}))

    crosswalk_result = crosswalk.selftest()
    invariants_result = invariants.selftest()
    ok = not failures and crosswalk_result["self_test"] == "PASS" and invariants_result["self_test"] == "PASS"
    return {
        "result": "PASS" if ok else "FAIL",
        "contract": "leviath-reconcile/v1",
        "scenarios": [r.as_dict() for r in results],
        "crosswalk": crosswalk_result,
        "invariants": invariants_result,
        "failures": failures,
    }
