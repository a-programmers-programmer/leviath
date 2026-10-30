#!/usr/bin/env python3
"""Reference evaluation of the edge-invariant rules, with self-tests.

This is a REFERENCE implementation, not the production resolver. It exists so the
definition-of-done claims for work order GQL-01 are executable and falsifiable
offline:

  * a violated ACYCLIC invariant produces an InvariantRejection;
  * InvariantRejection offers no waiver arm in its serialized shape, so an ERROR
    cycle cannot be turned into a DAG;
  * a violated NO_TRANSITIVE_ANCESTOR invariant (WARN) may be waived, but only
    with a non-empty reason and an acknowledging principal;
  * severity is a function of the rule, taken from the published contract — a
    caller cannot pass a severity in.

Usage:
    python3 edge_invariants.py --self-test     # exit 0 on pass, 1 on failure
    python3 edge_invariants.py --contract      # print the loaded rule contract
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONTRACT_PATH = HERE / "edge-invariant-rules.json"

# The GraphQL enum values the schema must publish, mirrored here so the shape of
# a disposition can be asserted without a GraphQL runtime.
REJECTION_FIELDS = ("rule", "severity", "enforcedAt", "reason")
WAIVER_FIELDS = ("rule", "severity", "enforcedAt", "reason", "acknowledgedBy")
SATISFIED_FIELDS = ("rule", "severity", "enforcedAt")


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def rule_map(contract: dict) -> dict:
    return {r["rule"]: r for r in contract["rules"]}


def check_contract_consistency(contract: dict) -> list[str]:
    """`waivable` must be exactly (severity == WARN). Nothing else is legal."""
    problems = []
    for r in contract["rules"]:
        expected = r["severity"] == "WARN"
        if bool(r["waivable"]) is not expected:
            problems.append(
                f"rule {r['rule']}: waivable={r['waivable']} but severity={r['severity']}"
            )
        if r["severity"] not in ("WARN", "ERROR"):
            problems.append(f"rule {r['rule']}: unknown severity {r['severity']!r}")
        if not r["enforcedAt"]:
            problems.append(f"rule {r['rule']}: no enforcement location declared")
    return problems


def has_cycle(edges: list[tuple[str, str]]) -> bool:
    """Iterative DFS; true iff the directed graph contains a cycle."""
    adj: dict[str, list[str]] = {}
    for a, b in edges:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, [])
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {n: WHITE for n in adj}
    for start in adj:
        if colour[start] != WHITE:
            continue
        stack = [(start, iter(adj[start]))]
        colour[start] = GREY
        while stack:
            node, it = stack[-1]
            advanced = False
            for nxt in it:
                if colour[nxt] == GREY:
                    return True
                if colour[nxt] == WHITE:
                    colour[nxt] = GREY
                    stack.append((nxt, iter(adj[nxt])))
                    advanced = True
                    break
            if not advanced:
                colour[node] = BLACK
                stack.pop()
    return False


def transitive_ancestor_edges(edges: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Direct edges (a,b) where b is already reachable from a via a longer path."""
    adj: dict[str, list[str]] = {}
    for a, b in edges:
        adj.setdefault(a, set()).add(b)
    reach = {n: set(v) for n, v in adj.items()}

    def closure(n, seen=None):
        if seen is None:
            seen = set()
        for m in adj.get(n, ()):  # type: ignore[arg-type]
            if m not in seen:
                seen.add(m)
                closure(m, seen)
        return seen

    reach = {n: closure(n) for n in adj}
    redundant = []
    for a, b in edges:
        others = [c for c in adj.get(a, ()) if c != b]
        if any(b in reach.get(c, set()) for c in others):
            redundant.append((a, b))
    return redundant


def evaluate(edges: list[tuple[str, str]], contract: dict, waiver: dict | None = None) -> dict:
    """Return the worst disposition, using severities from the contract only."""
    rules = rule_map(contract)
    dispositions = []

    if has_cycle(edges):
        dispositions.append({
            "rule": "ACYCLIC",
            "severity": rules["ACYCLIC"]["severity"],
            "enforcedAt": rules["ACYCLIC"]["enforcedAt"],
            "reason": "steps+edges graph contains a cycle",
        })

    for (a, b) in transitive_ancestor_edges(edges):
        if rules["NO_TRANSITIVE_ANCESTOR"]["waivable"] and waiver and waiver.get("reason") \
                and waiver.get("acknowledgedBy"):
            dispositions.append({
                "rule": "NO_TRANSITIVE_ANCESTOR",
                "severity": rules["NO_TRANSITIVE_ANCESTOR"]["severity"],
                "enforcedAt": rules["NO_TRANSITIVE_ANCESTOR"]["enforcedAt"],
                "reason": waiver["reason"],
                "acknowledgedBy": waiver["acknowledgedBy"],
            })
        else:
            dispositions.append({
                "rule": "NO_TRANSITIVE_ANCESTOR",
                "severity": rules["NO_TRANSITIVE_ANCESTOR"]["severity"],
                "enforcedAt": rules["NO_TRANSITIVE_ANCESTOR"]["enforcedAt"],
                "reason": f"{a}->{b} re-declares a transitive ancestor relation",
            })

    rejections = [d for d in dispositions if not rules[d["rule"]]["waivable"]]
    waivers = [d for d in dispositions if rules[d["rule"]]["waivable"] and "acknowledgedBy" in d]
    dispatchable = not rejections
    return {"dispatchable": dispatchable, "rejections": rejections, "waivers": waivers}


def self_test() -> int:
    failures: list[str] = []

    def check(name, cond, detail=""):
        if not cond:
            failures.append(f"{name}: {detail}")

    contract = load_contract()
    check("contract-consistency", not check_contract_consistency(contract),
          str(check_contract_consistency(contract)))
    rules = rule_map(contract)

    # T1: no cycle, no redundancy -> dispatchable, nothing to waive.
    r = evaluate([("a", "b"), ("b", "c")], contract)
    check("T1 clean graph", r["dispatchable"] is True and not r["rejections"] and not r["waivers"], str(r))

    # T2: a cycle is an ERROR rejection and NOT dispatchable.
    r = evaluate([("a", "b"), ("b", "c"), ("c", "a")], contract)
    check("T2 cycle rejected", r["dispatchable"] is False and len(r["rejections"]) == 1, str(r))
    check("T2 cycle is ACYCLIC+ERROR",
          r["rejections"] and r["rejections"][0]["rule"] == "ACYCLIC"
          and r["rejections"][0]["severity"] == "ERROR", str(r))

    # T3: THE DoD CLAIM — a waiver aimed at the ERROR cycle does not disarm it.
    r = evaluate([("a", "b"), ("b", "c"), ("c", "a")], contract,
                 waiver={"reason": "we know", "acknowledgedBy": "josh"})
    check("T3 ERROR cycle cannot be waived", r["dispatchable"] is False and r["rejections"], str(r))
    check("T3 no waiver arm on a rejection",
          all(set(d) == set(REJECTION_FIELDS) for d in r["rejections"]), str(r["rejections"]))
    check("T3 rejection carries no acknowledgedBy",
          all("acknowledgedBy" not in d for d in r["rejections"]), str(r["rejections"]))

    # T4: a WARN redundancy may be waived with a reason + principal.
    edges = [("a", "b"), ("b", "c"), ("a", "c")]
    r = evaluate(edges, contract)
    check("T4 redundancy is a WARN rejection-less finding",
          r["dispatchable"] is True and len(r["rejections"]) == 0, str(r))
    r = evaluate(edges, contract, waiver={"reason": "kept for clarity", "acknowledgedBy": "josh"})
    check("T4 WARN waivable with reason",
          r["dispatchable"] is True and len(r["waivers"]) == 1
          and set(r["waivers"][0]) == set(WAIVER_FIELDS), str(r))

    # T5: a WARN with no reason/principal is recorded as a plain finding, not a waiver.
    r = evaluate(edges, contract, waiver={"reason": "", "acknowledgedBy": ""})
    check("T5 empty waiver is not a waiver",
          len(r["waivers"]) == 0 and r["dispatchable"] is True, str(r))

    # T6: severity cannot be passed in — it is read from the contract.
    check("T6 ACYCLIC severity is fixed ERROR", rules["ACYCLIC"]["severity"] == "ERROR")
    check("T6 ACYCLIC not waivable", rules["ACYCLIC"]["waivable"] is False)
    check("T6 NO_TRANSITIVE_ANCESTOR is WARN",
          rules["NO_TRANSITIVE_ANCESTOR"]["severity"] == "WARN")
    check("T6 enforcement locations distinct from severities",
          all(e in ("AUTHORING_RECONCILER", "RUN_PREFLIGHT")
              for r_ in contract["rules"] for e in r_["enforcedAt"]))

    # T7: self-loop is a cycle.
    check("T7 self-loop is a cycle", evaluate([("a", "a")], contract)["dispatchable"] is False)

    if failures:
        for f in failures:
            print(f"FAIL {f}", file=sys.stderr)
        print(json.dumps({"self_test": "FAIL", "failures": failures}, indent=2))
        return 1
    print(json.dumps({"self_test": "PASS", "cases": 7, "contract": contract["contract"]}, indent=2))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--contract", action="store_true")
    a = ap.parse_args(argv)
    if a.contract:
        print(json.dumps(load_contract(), indent=2))
        return 0
    if a.self_test:
        return self_test()
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
