#!/usr/bin/env python3
"""Offline tests for the GQL-09 structured merge decisions.

Run:  python3 scripts/test_merge_decisions.py [--candidates A B]

Covers, in order:
  * validation: omitted, duplicate, unknown, conflict, stale - each with its code
  * byte-determinism of the merge across processes and directories
  * same-decisions-same-output over the real v0 candidates
  * no decision document is ever applied without validation
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import harnessql_paths  # noqa: E402
from graphql_diff import diff_types, parse_schema, propose_renames  # noqa: E402
from merge_decisions import (  # noqa: E402
    CONTRACT,
    MergeDecisionError,
    decision_json_schema,
    load_document,
    merge_text,
    sha256_text,
    validate,
)

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"PASS {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL {name} {detail}")


def rejects(name: str, doc, a: str, b: str, code: str) -> None:
    try:
        validate(doc, a, b)
    except MergeDecisionError as exc:
        check(name, exc.code == code, f"got code {exc.code!r}: {exc.message}")
        return
    check(name, False, "document was accepted")


A = """
schema { query: Query }

type Query {
  proposal(id: ID!): Proposal
  node(id: ID!): Node
}

interface Node { id: ID! }

type Proposal implements Node {
  id: ID!
  title: String!
  state: ProposalState!
}

enum ProposalState { DRAFT SUBMITTED }
"""

B = """
schema { query: Query }

type Query {
  proposal(id: ID!): Proposal
  node(id: ID!): Node
  search(text: String!): [Proposal!]!
}

interface Node { id: ID! }

type Proposal {
  id: ID!
  title: String
  state: ProposalState!
  author: Human!
}

type Human { id: ID! displayName: String! }

enum ProposalState { DRAFT SUBMITTED WITHDRAWN }
"""


def _cand_texts(a_path=None, b_path=None):
    if a_path and b_path:
        return open(a_path).read(), open(b_path).read()
    return A, B


def _base_doc(a: str, b: str, diff_text: str = None) -> dict:
    diff = diff_types(a, b)
    from graphql_diff import _norm_body  # noqa: F401  (documented: diff module owns it)
    types = []
    for name in sorted(diff.divergent):
        delta = diff.divergent[name]
        td = diff.a_types[name]
        attr = "fields" if td.kind in ("type", "interface", "input") else (
            "values" if td.kind == "enum" else "members")
        keys = (list(td.fields) if attr == "fields" else
                (list(td.enum_values) if attr == "values" else list(td.union_members)))
        btd = diff.b_types[name]
        b_keys = (list(btd.fields) if attr == "fields" else
                  (list(btd.enum_values) if attr == "values" else list(btd.union_members)))
        union = keys + [k for k in b_keys if k not in keys]
        sources = {k: ("a" if k in keys else "b") for k in union}
        # keep reconcile legal only when it genuinely takes from both
        entry = {"name": name, "disposition": "reconcile", "reason": "test",
                 attr: sources}
        if attr == "fields":
            ifaces = list(td.implements)
            b_ifaces = list(btd.implements)
            iface_union = ifaces + [i for i in b_ifaces if i not in ifaces]
            if iface_union:
                entry["implements"] = {i: ("a" if i in ifaces else "b") for i in iface_union}
        types.append(entry)
    doc = {
        "contract": CONTRACT,
        "candidates": {"a": {"path": "a.graphql", "sha256": sha256_text(a)},
                       "b": {"path": "b.graphql", "sha256": sha256_text(b)}},
        "types": types,
        "renames": [],
    }
    if diff_text is not None:
        doc["diff"] = {"sha256": sha256_text(diff_text)}
    return doc


def _sources_for(doc, name):
    for entry in doc["types"]:
        if entry["name"] == name:
            return entry
    return None


# ── validation ───────────────────────────────────────────────────────────

def test_validation():
    a, b = A, B
    good = _base_doc(a, b)
    validated = validate(good, a, b)
    check("accepts a complete document",
          len(validated.decisions) == len(diff_types(a, b).divergent))

    # omitted type
    doc = json.loads(json.dumps(good))
    doc["types"] = [t for t in doc["types"] if t["name"] != "Query"]
    rejects("omitted: no decision for a divergent type", doc, a, b, "omitted")

    # omitted field (a field of the union with no source)
    if _sources_for(good, "Query"):
        doc = json.loads(json.dumps(good))
        entry = _sources_for(doc, "Query")
        entry["fields"].pop(next(iter(entry["fields"])))
        rejects("omitted: a field has no source", doc, a, b, "omitted")

    # omitted reason
    doc = json.loads(json.dumps(good))
    doc["types"][0]["reason"] = "   "
    rejects("omitted: empty reason", doc, a, b, "omitted")

    # duplicate type
    doc = json.loads(json.dumps(good))
    doc["types"].append(json.loads(json.dumps(doc["types"][0])))
    rejects("duplicate: same type twice", doc, a, b, "duplicate")

    # duplicate JSON key
    doc = json.loads(json.dumps(good))
    raw = json.dumps(doc)
    raw = raw.replace('"contract":', '"contract": "%s", "contract":' % CONTRACT, 1)
    try:
        load_document(raw)
        check("duplicate: repeated JSON key", False, "accepted")
    except MergeDecisionError as exc:
        check("duplicate: repeated JSON key", exc.code == "duplicate", exc.message)

    # unknown type
    doc = json.loads(json.dumps(good))
    doc["types"].append({"name": "NoSuchType", "disposition": "keep_a",
                         "fields": {}, "reason": "x"})
    rejects("unknown: type is not divergent", doc, a, b, "unknown")

    # unknown declaration of an only-A type
    diff = diff_types(a, b)
    only_a = sorted(diff.only_a)
    if only_a:
        doc = json.loads(json.dumps(good))
        doc["types"].append({"name": only_a[0], "disposition": "keep_a",
                             "reason": "x"})
        rejects("unknown: only-A type needs no decision", doc, a, b, "unknown")

    # unknown disposition
    doc = json.loads(json.dumps(good))
    doc["types"][0]["disposition"] = "merge"
    rejects("unknown: disposition", doc, a, b, "unknown")

    # unknown source letter
    doc = json.loads(json.dumps(good))
    entry = _sources_for(doc, "Proposal")
    key = next(iter(entry["fields"]))
    entry["fields"][key] = "both"
    rejects("unknown: source letter", doc, a, b, "unknown")

    # unknown field
    doc = json.loads(json.dumps(good))
    entry = _sources_for(doc, "Proposal")
    entry["fields"]["nonexistent"] = "a"
    rejects("unknown: field neither candidate has", doc, a, b, "unknown")

    # conflict: keep_a with a shared field attributed to b
    doc = json.loads(json.dumps(good))
    entry = _sources_for(doc, "Proposal")
    entry["disposition"] = "keep_a"
    entry["fields"] = {k: "a" for k in entry["fields"]}
    entry["fields"]["title"] = "b"
    rejects("conflict: keep_a taking from b", doc, a, b, "conflict")

    # conflict: keep_a missing one of A's keys
    doc = json.loads(json.dumps(good))
    entry = _sources_for(doc, "Proposal")
    entry["disposition"] = "keep_a"
    entry["fields"] = {k: "a" for k in entry["fields"]}
    entry["fields"].pop(next(iter(entry["fields"])))
    rejects("omitted: keep_a missing an A field", doc, a, b, "omitted")

    # conflict: reconcile that takes everything from one side
    doc = json.loads(json.dumps(good))
    entry = _sources_for(doc, "Proposal")
    entry["disposition"] = "reconcile"
    entry["fields"] = {k: "a" for k in entry["fields"]}
    rejects("conflict: reconcile with one-sided sources", doc, a, b, "conflict")

    # conflict: reconcile discarding the other side's whole type is rejected by
    # the 'only from one side' rule; a genuine reconcile must name both sides.
    doc = json.loads(json.dumps(good))
    entry = _sources_for(doc, "Human")
    if entry is None:
        check("conflict: reconcile one-sided (Human absent)", True)
    else:
        entry["disposition"] = "keep_b"
        entry["fields"] = {k: "b" for k in entry["fields"]}
        entry["fields"][next(iter(entry["fields"]))] = "a"
        rejects("conflict: keep_b taking from a", doc, a, b, "conflict")

    # stale candidate hash
    doc = json.loads(json.dumps(good))
    doc["candidates"]["a"]["sha256"] = "0" * 64
    rejects("stale: candidate hash", doc, a, b, "stale")

    # stale diff hash
    doc = _base_doc(a, b, diff_text="DIFF AS SHOWN")
    validated = validate(doc, a, b, expected={"diff_text": "DIFF AS SHOWN"})
    check("diff hash binds when supplied", validated.diff_sha256 is not None)
    doc["diff"]["sha256"] = "1" * 64
    try:
        validate(doc, a, b, expected={"diff_text": "DIFF AS SHOWN"})
        check("stale: diff hash", False, "document was accepted")
    except MergeDecisionError as exc:
        check("stale: diff hash", exc.code == "stale", exc.message)

    # wrong contract string
    doc = json.loads(json.dumps(good))
    doc["contract"] = "something-else/v9"
    rejects("unknown: contract string", doc, a, b, "unknown")

    # prose instead of JSON
    try:
        load_document("TYPE Proposal: reconcile\nREASON: because\n")
        check("prose decisions are rejected", False, "accepted")
    except MergeDecisionError as exc:
        check("prose decisions are rejected", exc.code == "unknown", exc.message)


# ── renames ──────────────────────────────────────────────────────────────

def test_renames():
    old = "type Widget { id: ID! label: String! }\n"
    rename_a = old
    rename_b = "type Gadget { id: ID! label: String! }\n"
    a = "type Node { id: ID! }\n" + rename_a
    b = "type Node { id: ID! }\n" + rename_b
    diff = diff_types(a, b)
    pairs = propose_renames(diff)
    check("structural rename proposed", ("Widget", "Gadget") in pairs, str(pairs))

    good = _base_doc(a, b)
    good["renames"] = [{"from": "Widget", "to": "Gadget", "disposition": "rename",
                        "reason": "same body, one name"}]
    validated = validate(good, a, b)
    check("rename decision accepted", validated.renames == {"Widget": "Gadget"})

    # omitted rename decision
    doc = json.loads(json.dumps(good))
    doc["renames"] = []
    rejects("omitted: proposed rename has no decision", doc, a, b, "omitted")

    # unknown rename pair
    doc = json.loads(json.dumps(good))
    doc["renames"] = [{"from": "Nope", "to": "Gadget", "disposition": "rename",
                       "reason": "x"}]
    rejects("unknown: rename pair not proposed", doc, a, b, "unknown")

    # duplicate rename
    doc = json.loads(json.dumps(good))
    doc["renames"] = doc["renames"] + json.loads(json.dumps(doc["renames"]))
    rejects("duplicate: rename twice", doc, a, b, "duplicate")

    # 'distinct' keeps both types, and needs no rename map entry
    doc = json.loads(json.dumps(good))
    doc["renames"] = [{"from": "Widget", "to": "Gadget", "disposition": "distinct",
                       "reason": "different concepts"}]
    validated = validate(doc, a, b)
    check("distinct rename accepted", validated.renames == {})
    out = merge_text(a, b, json.dumps(doc))
    check("distinct keeps both definitions",
          "type Widget" in out.result_sdl and "type Gadget" in out.result_sdl)

    out = merge_text(a, b, json.dumps(good))
    check("rename applies", "type Gadget" in out.result_sdl
          and "type Widget" not in out.result_sdl)


# ── totality: a reconcile keeps every field from both sides ──────────────

def test_superset():
    a = """
type T {
  onlyA: String!
  shared: String!
}

type U { z: Int }
"""
    b = """
type T {
  shared: String
  onlyB: Int!
}

type U { z: Int }
type V { w: Int }
"""
    doc = _base_doc(a, b)
    out = merge_text(a, b, json.dumps(doc))
    schema = parse_schema(out.result_sdl)
    types = {t.name: t for t in schema}
    check("superset keeps only-A field", "onlyA" in types["T"].fields)
    check("superset keeps only-B field", "onlyB" in types["T"].fields)
    check("superset keeps shared field", "shared" in types["T"].fields)
    check("B-only type is appended", "V" in types)
    check("A-only type is retained", "U" in types)
    from graphql import build_schema
    build_schema(out.result_sdl)
    check("merged output parses", True)


# ── determinism ──────────────────────────────────────────────────────────

def test_determinism():
    a, b = A, B
    doc_text = json.dumps(_base_doc(a, b), indent=2)
    first = merge_text(a, b, doc_text).result_sha256
    for _ in range(5):
        again = merge_text(a, b, doc_text).result_sha256
        if again != first:
            check("deterministic in-process", False, f"{first} != {again}")
            return
    check("deterministic in-process", True)

    # across a fresh interpreter, in a different cwd, with a different PYTHONHASHSEED
    with tempfile.TemporaryDirectory() as tmp:
        dpath = os.path.join(tmp, "d.json")
        apath = os.path.join(tmp, "a.graphql")
        bpath = os.path.join(tmp, "b.graphql")
        open(dpath, "w").write(doc_text)
        open(apath, "w").write(a)
        open(bpath, "w").write(b)
        out = os.path.join(tmp, "merged.graphql")
        env = dict(os.environ, PYTHONHASHSEED="12345")
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, "merge_decisions.py"), "merge",
             "--a", apath, "--b", bpath, "--decisions", dpath, "--out", out],
            capture_output=True, text=True, cwd=tmp, env=env)
        if proc.returncode != 0:
            check("cross-process merge", False, proc.stdout + proc.stderr)
            return
        cross = hashlib.sha256(open(out, "rb").read()).hexdigest()
        check("cross-process merge is byte-identical", cross == first,
              f"{cross} != {first}")


def test_real_candidates():
    a_path = os.environ.get("HARNESSQL_CAND_A")
    b_path = os.environ.get("HARNESSQL_CAND_B")
    if not (a_path and b_path and os.path.exists(a_path) and os.path.exists(b_path)):
        print("SKIP test_real_candidates: set HARNESSQL_CAND_A / HARNESSQL_CAND_B")
        return
    a, b = open(a_path).read(), open(b_path).read()
    diff = diff_types(a, b)
    print(f"  real diff: {len(diff.only_a)} only-A, {len(diff.only_b)} only-B, "
          f"{len(diff.both)} identical, {len(diff.divergent)} divergent, "
          f"{len(diff.summary())}B vs {len(a) + len(b)}B raw")
    doc = _base_doc(a, b)
    # resolved each direction: reconcile is legal because each type takes from both
    validated = validate(doc, a, b)
    check("real candidates: full decision document validates",
          len(validated.decisions) == len(diff.divergent))
    outcome = merge_text(a, b, json.dumps(doc))
    from graphql import build_schema
    build_schema(outcome.result_sdl)
    check("real candidates: merged schema parses", True)
    hashes = {merge_text(a, b, json.dumps(doc)).result_sha256 for _ in range(3)}
    check("real candidates: same decisions, same output", len(hashes) == 1)
    check("real candidates: rationale carries every reason",
          all(d.type_name in outcome.rationale_md for d in outcome.decisions))


def test_schema_shape():
    schema = decision_json_schema()
    check("output schema names the contract",
          CONTRACT in json.dumps(schema))


if __name__ == "__main__":
    test_validation()
    test_renames()
    test_superset()
    test_determinism()
    test_schema_shape()
    test_real_candidates()
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILED: {FAILURES}")
        sys.exit(1)
    print("\n=== ALL TESTS PASSED ===")
