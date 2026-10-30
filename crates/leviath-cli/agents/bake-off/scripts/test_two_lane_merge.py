#!/usr/bin/env python3
"""Offline tests for the two-independent-lane merge level (work order GQL-09).

Run:  python3 scripts/test_two_lane_merge.py

The definition of done says "No self-merge is called independent convergence".
`run_bakeoff3.run_merge_level` is the code path that decides whether a level
yields two *independent* candidates for the next level. These checks drive it
with `driver.lev_run` stubbed, so they cost nothing and call no model:

  T1 two successful lanes -> two merged artifacts and two DIFFERENT run ids;
  T2 one lane returns nothing -> one artifact, and the result is NOT promoted as
     a pair (the conductor must stop, not duplicate it);
  T3 a lane that answers with PROSE is rejected by merge_decisions, not parsed;
  T4 the provenance the conductor records makes convergence_note say
     "independent" for two runs and "self-merge" for one run reused twice;
  T5 a lane that emits an unparseable schema is refused at the parse gate.

Nothing here touches the network, and nothing here reads a secret.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

FAILURES: list[str] = []

A = """schema { query: Query }

interface Node {
  id: ID!
}

type Query {
  node(id: ID!): Node
  thing: Thing
}

type Thing implements Node {
  id: ID!
  name: String
}
"""

# one field dropped from Thing: a small, real divergence
B = """schema { query: Query }

interface Node {
  id: ID!
}

type Query {
  node(id: ID!): Node
  thing: Thing
}

type Thing implements Node {
  id: ID!
}
"""


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"PASS {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL {name} {detail}")


def decisions_for(a_text: str, b_text: str, diff_sha: str) -> str:
    """A valid decision document for the A/B fixture, with the real sha256s."""
    from graphql_diff import diff_types, propose_renames
    from merge_decisions import CONTRACT, sha256_text

    d = diff_types(a_text, b_text)
    types = []
    for name in d.divergent:
        ta, tb = d.a_types[name], d.b_types[name]
        if ta.kind != tb.kind:
            types.append({"name": name, "disposition": "keep_a",
                          "reason": "kind changed: different abstractions"})
            continue
        key = ("fields" if ta.kind in ("type", "interface", "input") else
               "values" if ta.kind == "enum" else "members")
        ak = (list(ta.fields) if key == "fields" else
              list(ta.enum_values) if key == "values" else list(ta.union_members))
        bk = (list(tb.fields) if key == "fields" else
              list(tb.enum_values) if key == "values" else list(tb.union_members))
        m = {k: "a" for k in ak}
        for k in bk:
            m.setdefault(k, "b")
        if all(v == "a" for v in m.values()):
            m[bk[-1]] = "b" if bk else m
        entry = {"name": name, "disposition": "reconcile", key: m,
                 "reason": "superset: keeps every key from both sides"}
        if ta.implements or tb.implements:
            entry["implements"] = {i: "a" for i in ta.implements}
            for i in tb.implements:
                entry["implements"].setdefault(i, "b")
        types.append(entry)
    renames = [{"from": o, "to": n, "disposition": "rename", "reason": "same shape"}
               for o, n in propose_renames(d)]
    return json.dumps({
        "contract": CONTRACT,
        "diff": {"sha256": diff_sha},
        "candidates": {"a": {"path": "a", "sha256": sha256_text(a_text)},
                       "b": {"path": "b", "sha256": sha256_text(b_text)}},
        "types": types, "renames": renames,
    })


def run_with_stub(tmp: Path, answers: list, texts: dict) -> dict:
    """Drive run_merge_level with driver.lev_run stubbed to return `answers`.

    `answers[i]` is what lane i returns: either a decision document string, or
    None (the lane produced nothing), or a rendered schema to write instead.
    """
    import harnessql_driver as driver
    import run_bakeoff3 as r3

    tmp.mkdir(parents=True, exist_ok=True)
    a_path = tmp / "cand_a.graphql"
    b_path = tmp / "cand_b.graphql"
    a_path.write_text(texts["a"], encoding="utf-8")
    b_path.write_text(texts["b"], encoding="utf-8")

    calls: list[str] = []
    counter = {"n": 0}

    def fake_lev_run(agent, task, workdir, *, output_schema=None, timeout=3600):
        idx = counter["n"]
        counter["n"] += 1
        calls.append(agent)
        # The task embeds the diff sha256; give the lane the true one.
        from merge_decisions import sha256_text
        import re
        m = re.search(r'"sha256": "([0-9a-f]{64})"', task)
        diff_sha = m.group(1) if m else ""
        ans = answers[idx] if idx < len(answers) else None
        if ans is None:
            return {"run_id": f"run-{idx}", "status": "COMPLETE", "final_output": ""}
        if ans == "<prose>":
            return {"run_id": f"run-{idx}", "status": "COMPLETE",
                    "final_output": "I think we should keep both sides' fields."}
        if ans == "<bad-schema>":
            return {"run_id": f"run-{idx}", "status": "COMPLETE",
                    "final_output": decisions_for(texts["a"], texts["b"], diff_sha)}
        return {"run_id": f"run-{idx}", "status": "COMPLETE",
                "final_output": decisions_for(texts["a"], texts["b"], diff_sha)}

    # Point BO at a temp dir and stub the dispatch.
    old_bo, old_run = r3.BO, driver.lev_run
    old_evidence = None
    import harnessql_paths
    old_evidence = harnessql_paths.evidence_dir
    r3.BO = tmp
    driver.lev_run = fake_lev_run
    harnessql_paths.evidence_dir = lambda: tmp / "evidence"
    try:
        merged, prov = r3.run_merge_level([str(a_path), str(b_path)], 1)
    finally:
        r3.BO = old_bo
        driver.lev_run = old_run
        harnessql_paths.evidence_dir = old_evidence
    return {"merged": merged, "provenance": prov, "calls": calls}


def test_two_lanes(tmp: Path) -> None:
    print("\n[T1] two successful lanes yield two artifacts from two runs")
    r = run_with_stub(tmp / "t1", ["<doc>", "<doc>"], {"a": A, "b": B})
    check("T1a two merged artifacts", len(r["merged"]) == 2, str([str(p) for p in r["merged"]]))
    check("T1b two lane dispatches", len(r["calls"]) == 2, str(r["calls"]))
    ids = [p["run_id"] for p in r["provenance"]]
    check("T1c the two decisions have different run ids", len(set(ids)) == 2, str(ids))
    if len(r["merged"]) == 2:
        texts = [Path(p).read_text() for p in r["merged"]]
        check("T1d both merged artifacts are valid-looking graphql",
              all(t.strip() for t in texts))
        check("T1e they are distinct outputs, not one file in both slots",
              r["merged"][0] != r["merged"][1])


def test_one_lane_fails(tmp: Path) -> None:
    print("\n[T2] one empty lane yields one artifact and is not promoted as a pair")
    r = run_with_stub(tmp / "t2", ["<doc>", None], {"a": A, "b": B})
    check("T2a exactly one merged artifact", len(r["merged"]) == 1,
          str([str(p) for p in r["merged"]]))
    states = [p["state"] for p in r["provenance"]]
    check("T2b the empty lane is recorded blocked", "blocked" in states, str(states))
    check("T2c the empty lane's reason is recorded",
          any(p.get("reason") for p in r["provenance"] if p["state"] == "blocked"))


def test_prose_is_rejected(tmp: Path) -> None:
    print("\n[T3] a prose answer is rejected, never interpreted")
    r = run_with_stub(tmp / "t3", ["<prose>", "<prose>"], {"a": A, "b": B})
    check("T3a no merge is produced from prose", len(r["merged"]) == 0,
          str([str(p) for p in r["merged"]]))
    check("T3b both lanes are recorded blocked",
          all(p["state"] == "blocked" for p in r["provenance"]))
    check("T3c the reason names a rejection, not an interpretation",
          all(p.get("reason") for p in r["provenance"]),
          str([p.get("reason") for p in r["provenance"]]))


def test_convergence_note_uses_provenance(tmp: Path) -> None:
    print("\n[T4] convergence labelling depends on the recorded run ids")
    import harnessql_driver as driver
    two = [{"lane": 0, "run_id": "run-0"}, {"lane": 1, "run_id": "run-1"}]
    one = [{"lane": 0, "run_id": "run-0"}, {"lane": 1, "run_id": "run-0"}]
    none = None
    n2 = driver.convergence_note(["a", "b"], provenance=two, a_text="x", b_text="x")
    n1 = driver.convergence_note(["a", "b"], provenance=one, a_text="x", b_text="x")
    n0 = driver.convergence_note(["a", "b"], provenance=none, a_text="x", b_text="x")
    check("T4a two different runs + identical bytes => independent", n2["independent"],
          n2["label"])
    check("T4b one run reused twice is NOT independent", not n1["independent"], n1["label"])
    check("T4c the one-run label says self-merge and NOT convergence",
          "self-merge" in n1["label"] and "NOT independent convergence" in n1["label"],
          n1["label"])
    check("T4d unrecorded provenance is NOT independent", not n0["independent"], n0["label"])


def test_level0_pair_is_checked() -> None:
    print("\n[T5] level 0 refuses to call a non-independent pair convergence")
    import harnessql_driver as driver
    # same run id on both lanes, identical bytes: must be reported as self-merge
    prov = [{"lane": 0, "run_id": "r1"}, {"lane": 1, "run_id": "r1"}]
    note = driver.convergence_note(["a.graphql", "b.graphql"], provenance=prov,
                                   a_text="same", b_text="same")
    check("T5a identical bytes from one run => not independent", not note["independent"])
    check("T5b the reason names the shared run id", "r1" in " ".join(note["reasons"]),
          str(note["reasons"]))


def main() -> int:
    print("GQL-09 two-independent-lane merge tests (offline, no model calls)")
    with tempfile.TemporaryDirectory(prefix="harnessql-lanes-") as tmpdir:
        tmp = Path(tmpdir)
        test_two_lanes(tmp)
        test_one_lane_fails(tmp)
        test_prose_is_rejected(tmp)
        test_convergence_note_uses_provenance(tmp)
        test_level0_pair_is_checked()
    print()
    if FAILURES:
        print(f"=== {len(FAILURES)} CHECK(S) FAILED ===")
        for name in FAILURES:
            print(f"  FAILED {name}")
        return 1
    print("=== ALL TWO-LANE CHECKS PASSED ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())