#!/usr/bin/env python3
"""
HarnessQL bakeoff runner v3 — diff-based merge for level N>0 (portable, GQL-09).

Level 0: two parallel lanes on the seed (full-schema iteration).
Level N>0: compute the COMPACT diff of the two candidates, hand it to the
`bake-merge` lane (which has no file tools), then apply the returned STRUCTURED
decision document with merge_decisions, and parse-gate the result.

Portability (GQL-09):
  * every path comes from :mod:`harnessql_paths` (agent-relative, `HARNESSQL_*`
    overridable) — no `/data/work/leviath` default and no `/data/work/leviath/python`
    import;
  * no key file is read: `lev` owns its own credentials;
  * decisions are the harnessql-merge-decisions/v1 JSON document, not prose. A
    lane that answers with prose is rejected by `merge_decisions.validate`, and
    the rejection is reported, never reinterpreted.
"""
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import harnessql_driver as driver
import harnessql_paths
from graphql import build_schema
from graphql_diff import diff_types
from harnessql import identical
from merge_decisions import MergeDecisionError, decision_json_schema

BO = harnessql_paths.workdir()
BO.mkdir(parents=True, exist_ok=True)
SEED = harnessql_paths.default_seed()
MAX_LEVELS = 5
LANE_AGENT = "bake-off"
MERGE_AGENT = "bake-merge"
MAX_LANE_RETRIES = 2


def dbg(msg):
    print(msg, flush=True)


def parse_gate(path, label) -> bool:
    """Validate a schema file with graphql-core's build_schema().

    An unparseable artifact can never be declared converged.
    """
    if not os.path.exists(path):
        dbg(f"[{label}] PARSE GATE FAIL: no file at {path}")
        return False
    try:
        build_schema(open(path).read())
        dbg(f"[{label}] PARSE GATE PASS: {os.path.basename(path)}")
        return True
    except Exception as e:
        dbg(f"[{label}] PARSE GATE FAIL: {os.path.basename(path)}: {e}")
        return False


def parse_error_of(path: str) -> str:
    try:
        build_schema(open(path).read())
        return ""
    except Exception as e:
        return str(e).split("\n")[0]


def run_schema_lane(out: str, label: str, base_task: str, workdir: Path) -> dict:
    """Dispatch a lane and ENFORCE validity, re-dispatching with the parse error.

    The engine cannot retry a hook, so the conductor enforces the gate: a lane
    whose schema does not parse gets its parser complaint handed back, up to
    MAX_LANE_RETRIES. Returns a record carrying the run id, so the caller can
    prove the candidates came from two different runs.
    """
    task = base_task
    record = {"label": label, "out": out, "run_id": None, "state": "blocked",
              "reason": None}
    for attempt in range(MAX_LANE_RETRIES + 1):
        if os.path.exists(out):
            os.remove(out)
        try:
            result = driver.lev_run(LANE_AGENT, task, workdir, timeout=3600)
        except driver.LevError as e:
            dbg(f"[{label}] attempt {attempt+1} RUN FAILED: {e}")
            record["reason"] = str(e)
            return record
        record["run_id"] = result.get("run_id")
        dbg(f"[{label}] attempt {attempt+1} run {record['run_id']} "
            f"status {result.get('status')}")
        if os.path.exists(out) and parse_gate(out, f"{label} attempt{attempt+1}"):
            record["state"] = "verified"
            return record
        err = (parse_error_of(out) if os.path.exists(out)
               else "no output file was written")
        dbg(f"[{label}] attempt {attempt+1} REJECTED — not valid GraphQL: {err}")
        task = (
            base_task
            + "\n\n*** YOUR PREVIOUS OUTPUT WAS REJECTED BY THE SCHEMA VALIDATOR. ***\n"
            + f"It did not parse as GraphQL. The parser says:\n    {err}\n"
            + f"You MUST write a CORRECTED, SYNTACTICALLY VALID GraphQL SDL to {out} "
            + "(no duplicated type/enum/union/input definitions; every union member "
            + "defined exactly once; braces balanced) and then call submit_output."
        )
    dbg(f"[{label}] EXHAUSTED {MAX_LANE_RETRIES+1} attempts — schema never validated")
    record["reason"] = "schema never validated"
    return record


def run_level0() -> tuple[list, bool, list]:
    """Level 0: two lanes each iterate on the seed independently.

    Both lanes run concurrently (the point of a bake-off is that they are
    independent, not sequential) and each is validity-enforced separately.
    Returns (paths, all_ok, provenance) where provenance carries each lane's run
    id, so a converged pair can be labelled honestly.
    """
    outs = [BO / "v0_L0L0.graphql", BO / "v0_L0L1.graphql"]

    # Stage the seed into the workdir: the lanes' file tools are confined there,
    # so the seed is readable without widening [read_paths].
    seed_local = BO / Path(str(SEED)).name
    if not seed_local.exists():
        seed_local.write_text(Path(SEED).read_text(), encoding="utf-8")

    jobs = []
    for lane, out in enumerate(outs):
        label = f"L0L{lane}"
        task = (
            f"BAKE_OFF label={label} level=0 seed={seed_local} out={out}\n"
            f"Iterate on the GraphQL schema at {seed_local} to produce an improved "
            f"version. Read the Composition Guide with the composition_guide tool. "
            f"The output MUST be syntactically valid GraphQL SDL (no duplicated "
            f"type/enum/union/input definitions; every union member defined exactly "
            f"once; braces balanced). Write the final schema to {out} and call "
            f"submit_output."
        )
        jobs.append((out, label, task))

    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(
            lambda job: run_schema_lane(str(job[0]), job[1], job[2], BO), jobs))
    provenance = [{"lane": i, "label": r["label"], "run_id": r["run_id"],
                   "path": r["out"], "state": r["state"], "reason": r["reason"]}
                  for i, r in enumerate(records)]
    return outs, all(r["state"] == "verified" for r in records), provenance


def run_merge_lane(cands: list, level: int, lane: int, task: str,
                   diff_file: Path, schema_path: Path) -> dict:
    """Run ONE merge-decision lane and apply its document. Returns a record.

    Each lane is an independent decision: its own `lev run` (its own run id), its
    own document, its own merged output. Two lanes at a level give the next level
    two genuinely independent candidates; one lane reused for both would be a
    self-merge dressed up as convergence.
    """
    out = BO / f"v{level}_merged_lane{lane}.graphql"
    record = {"lane": lane, "run_id": None, "status": None, "out": str(out),
              "state": "blocked", "reason": None}
    try:
        result = driver.lev_run(MERGE_AGENT, task, BO,
                                output_schema=schema_path, timeout=3600)
    except driver.LevError as e:
        record["reason"] = f"merge lane failed: {e}"
        return record
    record["run_id"] = result.get("run_id")
    record["status"] = result.get("status")
    dbg(f"[L{level}] lane{lane} run {record['run_id']} status {record['status']}")

    decisions_text = driver.final_output_text(result)
    if not decisions_text.strip():
        record["reason"] = "the merge lane produced no final output"
        return record
    dec_file = BO / f"v{level}_decisions_lane{lane}.json"
    dec_file.write_text(decisions_text, encoding="utf-8")

    code = driver.do_merge(str(cands[0]), str(cands[1]), str(out),
                           decisions=str(dec_file),
                           evidence=harnessql_paths.evidence_dir() / f"L{level}_lane{lane}",
                           diff_out=diff_file if lane == 0 else None)
    if code != 0:
        record["reason"] = {1: "REJECTED (invalid decision document)",
                            2: "BLOCKED (unusable input)"}.get(code, "FAILED")
        return record
    if not parse_gate(str(out), f"L{level} lane{lane} merged"):
        record["reason"] = "CONVERGENCE-REFUSED: merged schema is unparseable"
        return record
    record["state"] = "verified"
    return record


def run_merge_level(cands: list, level: int) -> tuple[list, list]:
    """Merge level (N>0): diff, ask TWO independent merge lanes, apply, parse-gate.

    Returns (merged_paths, provenance). Two lanes are dispatched so the next level
    has two independent candidates; the run ids go into `provenance` so a
    converged pair is only ever labelled independent when the provenance says so.
    """
    a_text = Path(cands[0]).read_text()
    b_text = Path(cands[1]).read_text()
    diff = diff_types(a_text, b_text)

    diff_file = BO / f"v{level}_diff.txt"
    dbg(f"[L{level}] Diff: {len(diff.only_a)} only-A, {len(diff.only_b)} only-B, "
        f"{len(diff.both)} identical, {len(diff.divergent)} divergent")

    if diff.is_empty:
        converged = BO / f"v{level}_merged.graphql"
        converged.write_text(a_text)
        dbg(f"[L{level}] CONVERGED — no divergence; nothing to arbitrate -> {converged}")
        return [converged], []

    # The reviewer gets the compact diff and nothing else, and cannot read the
    # candidates or write a schema.
    schema_path = BO / f"v{level}_decisions.schema.json"
    schema_path.write_text(
        __import__("json").dumps(decision_json_schema(), indent=2, sort_keys=True),
        encoding="utf-8")
    diff_text = driver.compact_diff_text(a_text, b_text,
                                        {"a": str(cands[0]), "b": str(cands[1])})
    base_task = driver.merge_task(diff_text, str(diff_file))

    # Two independent decisions. The task text is identical (the contract is
    # fixed); independence comes from being two separate runs, and the recorded
    # run ids are what prove it.
    tasks = [(0, base_task), (1, base_task)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(
            lambda job: run_merge_lane(cands, level, job[0], job[1], diff_file,
                                       schema_path), tasks))

    merged = [Path(r["out"]) for r in records if r["state"] == "verified"]
    provenance = [{"lane": r["lane"], "run_id": str(r["run_id"]),
                   "path": r["out"], "state": r["state"], "reason": r["reason"]}
                  for r in records]

    if not merged:
        reasons = "; ".join(f"lane{r['lane']}: {r['reason']}" for r in records)
        dbg(f"[L{level}] ABORT: no merge decision was applied ({reasons})")
        return [], provenance

    if len(merged) == 1:
        dbg(f"[L{level}] only ONE merged candidate was produced; stopping rather "
            "than feeding it to both slots as a self-merge")
    return merged, provenance


def main() -> int:
    dbg(f"HarnessQL bakeoff v3: seed={SEED} workdir={BO} max_levels={MAX_LEVELS}")
    dbg("paths: " + __import__("json").dumps(harnessql_paths.summary(), sort_keys=True))

    outs, all_ok, prov0 = run_level0()
    if not all_ok:
        dbg("[L0] ABORT: a lane never produced a VALID schema")
        return 2

    a = Path(outs[0]).read_text()
    b = Path(outs[1]).read_text()
    same = identical(a, b)
    dbg(f"[L0] lane0 {len(a)}B vs lane1 {len(b)}B -> "
        f"{'IDENTICAL-MERGED' if same else 'DIFFERENT-recurse'}")
    note = driver.convergence_note([str(outs[0]), str(outs[1])],
                                   provenance=prov0, a_text=a, b_text=b)
    dbg(f"[L0] convergence: {note['label']} (run ids {note['run_ids']})")
    if same:
        if not note["independent"]:
            dbg("RESULT: NOT CONVERGENCE — the two 'converged' candidates are not "
                "independent (see the reason above); refusing to report a self-merge "
                "as convergence")
            return 1
        dbg(f"RESULT: MERGED at level 0 -> {outs[0]}")
        return 0

    cands = [str(p) for p in outs]
    for level in range(1, MAX_LEVELS):
        dbg(f"\n[L{level}] Running diff-based merge level...")
        merged_outs, provenance = run_merge_level(cands, level)
        if not merged_outs:
            dbg(f"[L{level}] ABORT: merge failed")
            break

        merged_text = Path(merged_outs[0]).read_text()
        a_text = Path(cands[0]).read_text()
        b_text = Path(cands[1]).read_text()

        # A merge that equals one candidate means the other's content was fully
        # absorbed (or the diff was empty) — that is the stopping condition.
        if identical(merged_text, a_text) or identical(merged_text, b_text):
            dbg(f"RESULT: MERGED at level {level} -> {merged_outs[0]}")
            return 0

        if len(merged_outs) < 2:
            dbg(f"RESULT: MERGED at level {level} -> {merged_outs[0]} "
                "(one independent merge lane only; stopping rather than "
                "self-merging)")
            return 0

        # Two independent merge lanes fed the next level two candidates that come
        # from two different runs. The provenance is recorded and checked below,
        # so a pair that is secretly one artifact is never called convergence.
        cands = [str(merged_outs[0]), str(merged_outs[1])]
        pair_texts = [Path(c).read_text() for c in cands]
        note = driver.convergence_note(cands, provenance=provenance,
                                       a_text=pair_texts[0], b_text=pair_texts[1])
        dbg(f"[L{level}] next-level candidates: {note['label']} "
            f"(run ids {note['run_ids']})")
        if not note["independent"]:
            dbg(f"RESULT: MERGED at level {level} -> {merged_outs[0]} "
                "(next level would be a self-merge)")
            return 0
    else:
        dbg("RESULT: NO_CONVERGENCE after 5 levels")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
