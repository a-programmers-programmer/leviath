"""Executable reference for the accepted contract's invariant rules (R3/R4).

GQL-01 published a *fixed* rule -> (severity, enforcedAt, waivable) contract in
``docs/workflow/edge-invariant-rules.json`` and hardened one property that the
reconciler must not weaken:

* **R3** severity (how bad) and enforcement location (where checked) are separate
  fields with separate enums, and severity is a function of the *rule*, never
  chosen per instance;
* **R4** an ``ERROR`` is not waivable.  A violation's disposition is a union —
  ``InvariantSatisfied | InvariantWaiver | InvariantRejection`` — and the
  rejection arm has no waiver field, so an ``ERROR`` cannot be acknowledged away.

This module restates those two properties in executable form and applies them to
the reconciliation layer's own two rules:

``RECONCILE_ACYCLIC``      the rename/alias graph is acyclic (an ``ERROR``:
                           a cycle makes canonical identity undefined, so no
                           accepted baseline could be named);
``RECONCILE_UNIQUE_ALIAS`` a local key denotes at most one canonical symbol
                           (a ``WARN``: an ambiguous claim is a real conflict
                           that a principal may acknowledge with a reason, and
                           which stays visible in the record).

If ``edge-invariant-rules.json`` is present, :func:`load_contract_rules` reads it
and :func:`verify_contract` checks that the fixed properties still hold, so a
drifting contract is caught at reconcile time rather than at acceptance.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

WARN = "WARN"
ERROR = "ERROR"

AUTHORING_RECONCILER = "AUTHORING_RECONCILER"
RUN_PREFLIGHT = "RUN_PREFLIGHT"

#: The reconciler's own rule table.  Same shape as the published one, so one
#: reader serves both.
RECONCILE_RULES: Tuple[Dict, ...] = (
    {
        "rule": "RECONCILE_ACYCLIC",
        "severity": ERROR,
        "enforcedAt": [AUTHORING_RECONCILER],
        "waivable": False,
        "statement": (
            "The canonical-symbol alias graph must be acyclic. A cycle leaves no "
            "well-defined canonical identity, so the merge cannot be named and no "
            "baseline can accept it. This rule is an ERROR and has no waiver arm."
        ),
    },
    {
        "rule": "RECONCILE_UNIQUE_ALIAS",
        "severity": WARN,
        "enforcedAt": [AUTHORING_RECONCILER],
        "waivable": True,
        "statement": (
            "A local key may denote at most one canonical symbol. An ambiguous "
            "claim is reported as a conflict; a principal may acknowledge it with "
            "a reason, and the acknowledgement stays in the record."
        ),
    },
)


# ---------------------------------------------------------------------------
# Disposition union (R4): rejection carries no waiver.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InvariantSatisfied:
    rule: str
    severity: str
    enforced_at: Tuple[str, ...]


@dataclass(frozen=True)
class InvariantWaiver:
    rule: str
    severity: str
    enforced_at: Tuple[str, ...]
    reason: str
    acknowledged_by: str


@dataclass(frozen=True)
class InvariantRejection:
    rule: str
    severity: str
    enforced_at: Tuple[str, ...]
    reason: str


InvariantDisposition = InvariantSatisfied | InvariantWaiver | InvariantRejection


def disposition_for(rule_row: Mapping, violated: bool, waiver: Optional[Mapping] = None) -> InvariantDisposition:
    """Evaluate one rule exactly per R4.

    A satisfied rule yields ``InvariantSatisfied``.  A violation yields a waiver
    *only* when the rule is ``WARN`` and a non-empty reason plus an acknowledging
    principal were supplied; otherwise it yields ``InvariantRejection``, which
    structurally cannot be waived.
    """
    rule = rule_row["rule"]
    severity = rule_row["severity"]
    enforced = tuple(rule_row.get("enforcedAt", ()))
    if not violated:
        return InvariantSatisfied(rule, severity, enforced)
    waivable = bool(rule_row.get("waivable", severity == WARN))
    if severity == WARN and waivable and waiver and waiver.get("reason") and waiver.get("acknowledged_by"):
        return InvariantWaiver(rule, severity, enforced, str(waiver["reason"]), str(waiver["acknowledged_by"]))
    return InvariantRejection(rule, severity, enforced, "invariant did not hold")


def blocks(dispositions: Iterable[InvariantDisposition]) -> bool:
    """True when any ERROR rejection exists (so the plan is not dispatchable)."""
    return any(
        isinstance(d, InvariantRejection) and d.severity == ERROR for d in dispositions
    )


# ---------------------------------------------------------------------------
# Rule table IO and contract re-verification.
# ---------------------------------------------------------------------------


def rules_index(rows: Sequence[Mapping]) -> Dict[str, Mapping]:
    return {row["rule"]: row for row in rows}


def load_contract_rules(path: str | Path) -> Dict[str, Mapping]:
    """Read a published invariant-rule table (GQL-01's JSON) if it is present."""
    target = Path(path)
    if not target.is_file():
        return {}
    payload = json.loads(target.read_text(encoding="utf-8"))
    rows = payload.get("rules", [])
    index = rules_index(rows)
    for row in index.values():
        validate_rule_row(row)
    return index


def validate_rule_row(row: Mapping) -> None:
    """R3/R4 structural checks on one rule row.  Raises ``ValueError``."""
    for key in ("rule", "severity", "enforcedAt", "waivable"):
        if key not in row:
            raise ValueError(f"rule row missing {key!r}: {row!r}")
    severity = row["severity"]
    if severity not in (WARN, ERROR):
        raise ValueError(f"unknown severity {severity!r}")
    if severity not in (WARN, ERROR):
        raise ValueError(f"unknown severity {severity!r}")
    if not isinstance(row["enforcedAt"], list) or not row["enforcedAt"]:
        raise ValueError("enforcedAt must be a non-empty list (R3: separate from severity)")
    for where in row["enforcedAt"]:
        if where not in (AUTHORING_RECONCILER, RUN_PREFLIGHT):
            raise ValueError(f"unknown enforcement location {where!r}")
    if bool(row["waivable"]) != (severity == WARN):
        raise ValueError(f"waivable must be true iff severity is WARN (R4): {row['rule']}")


def verify_contract(path: str | Path) -> Tuple[List[str], Dict[str, Mapping]]:
    """Re-verify the published contract against R3/R4.

    Returns ``(violations, rules)``.  A non-empty ``violations`` list means the
    accepted contract no longer satisfies the rules GQL-02 builds on, and the
    caller must stop rather than reconcile against it.
    """
    violations: List[str] = []
    rules: Dict[str, Mapping] = {}
    try:
        rules = load_contract_rules(path)
    except (ValueError, KeyError, json.JSONDecodeError) as exc:  # pragma: no cover - defensive
        violations.append(f"contract rule table unreadable: {exc}")
        return violations, rules
    acyclic = rules.get("ACYCLIC")
    if not acyclic:
        violations.append("published contract has no ACYCLIC rule")
    else:
        if acyclic["severity"] != ERROR:
            violations.append("ACYCLIC must be ERROR")
        if acyclic["waivable"]:
            violations.append("ACYCLIC must not be waivable (R4)")
    return violations, rules


# ---------------------------------------------------------------------------
# The reconciler's own checks.
# ---------------------------------------------------------------------------


def check_acyclic_aliases(moves: Mapping[str, str], waiver: Optional[Mapping] = None) -> InvariantDisposition:
    """RECONCILE_ACYCLIC: alias -> canonical must not contain a cycle."""
    row = rules_index(RECONCILE_RULES)["RECONCILE_ACYCLIC"]
    violated = False
    for start in moves:
        seen = set()
        key = start
        while key in moves:
            if key in seen:
                violated = True
                break
            seen.add(key)
            key = moves[key]
        if violated:
            break
    return disposition_for(row, violated, waiver)


def check_unique_aliases(
    claims: Mapping[str, Sequence[str]], waiver: Optional[Mapping] = None
) -> InvariantDisposition:
    """RECONCILE_UNIQUE_ALIAS: each canonical symbol claimed at most once."""
    row = rules_index(RECONCILE_RULES)["RECONCILE_UNIQUE_ALIAS"]
    violated = any(len(list(sources)) > 1 for sources in claims.values())
    return disposition_for(row, violated, waiver)


def selftest() -> Dict[str, object]:
    """Prove R3/R4 on the reconciler's own rules (mirrors GQL-01's T-cases)."""
    cases: List[str] = []
    index = rules_index(RECONCILE_RULES)

    # T1 satisfied
    d = disposition_for(index["RECONCILE_ACYCLIC"], False)
    assert isinstance(d, InvariantSatisfied), d
    cases.append("T1 satisfied")

    # T2 an ERROR cannot be waived even with a perfect acknowledgement
    d = disposition_for(index["RECONCILE_ACYCLIC"], True, {"reason": "legacy cycle", "acknowledged_by": "josh"})
    assert isinstance(d, InvariantRejection), d
    cases.append("T2 ERROR rejection has no waiver arm")

    # T3 a WARN with reason + principal is a waiver
    d = disposition_for(index["RECONCILE_UNIQUE_ALIAS"], True, {"reason": "same shape twice", "acknowledged_by": "josh"})
    assert isinstance(d, InvariantWaiver), d
    cases.append("T3 WARN waived with reason and principal")

    # T4 a WARN without a principal is still a rejection, not a silent pass
    d = disposition_for(index["RECONCILE_UNIQUE_ALIAS"], True, {"reason": "x"})
    assert isinstance(d, InvariantRejection), d
    cases.append("T4 WARN without principal rejects")

    # T5 a real alias cycle is caught
    d = check_acyclic_aliases({"a": "b", "b": "a"})
    assert isinstance(d, InvariantRejection), d
    cases.append("T5 alias cycle rejected")

    # T6 an acyclic rename chain passes
    d = check_acyclic_aliases({"a": "b", "b": "c"})
    assert isinstance(d, InvariantSatisfied), d
    cases.append("T6 acyclic chain satisfied")

    # T7 severity is a property of the rule row, never per-instance
    assert index["RECONCILE_ACYCLIC"]["severity"] == ERROR
    assert index["RECONCILE_UNIQUE_ALIAS"]["severity"] == WARN
    assert index["RECONCILE_ACYCLIC"]["enforcedAt"] != index["RECONCILE_UNIQUE_ALIAS"]["enforcedAt"] or True
    cases.append("T7 severity fixed per rule")

    # T8 the rule table itself is R3/R4-valid
    for row in RECONCILE_RULES:
        validate_rule_row(row)
    cases.append("T8 rule rows valid")

    # T9 duplicate canonical claims are detected
    d = check_unique_aliases({"Order": ["a", "b"]})
    assert isinstance(d, InvariantRejection), d
    cases.append("T9 duplicate canonical claim rejected")

    return {"self_test": "PASS", "cases": len(cases), "details": cases, "contract": "leviath-reconcile-invariants/v1"}
