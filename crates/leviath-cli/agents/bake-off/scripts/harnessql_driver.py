#!/usr/bin/env python3
"""HarnessQL conductor (portable) - work order GQL-09.

One driver, no scratch paths, no key file, no prose decisions.

    python3 scripts/harnessql_driver.py --help

What it does, per level:

  1. stages the two candidates into the workdir (the lanes' file tools are
     confined there, so a seed living anywhere is readable without widening
     `[read_paths]`);
  2. writes the COMPACT DIFF to `v<N>_diff.txt` and hashes it;
  3. dispatches the `bake-merge` lane with that diff inline, the candidate
     hashes, the per-type key checklist and the proposed renames, and with
     `--output-schema` so its answer must be the structured decision document;
  4. validates the document (omit/duplicate/unknown/conflict/stale are
     refusals), applies it, and re-applies it to prove the output is a function
     of (candidates, decisions) alone;
  5. writes an evidence bundle: rationale, candidate hashes, diff, decisions,
     result, and the hashes of each.

`lev` owns its own credentials: the driver never reads, injects or logs a key.
Exit codes: 0 passed/verified, 1 rejected (bad decisions), 2 blocked (bad input
or a failed run).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import harnessql_paths  # noqa: E402
from graphql_diff import diff_types, propose_renames  # noqa: E402
from merge_decisions import (  # noqa: E402
    CONTRACT,
    MergeDecisionError,
    Outcome,
    decision_json_schema,
    load_document,
    merge,
    sha256_text,
)

LANE = "bake-off"
MERGE_LANE = "bake-merge"


def log(msg: str) -> None:
    print(msg, flush=True)


def compact_diff_text(a_text: str, b_text: str, candidates: dict,
                      diff_sha_placeholder=None) -> str:
    """The only thing the merge reviewer sees, besides its instructions.

    Deterministic: two runs over the same candidate bytes produce the same text,
    so `diff.sha256` in the answer is a checkable commitment.
    """
    diff = diff_types(a_text, b_text)
    lines = [
        "CONTRACT: " + CONTRACT,
        f"candidate a: {candidates['a']} sha256 {sha256_text(a_text)}",
        f"candidate b: {candidates['b']} sha256 {sha256_text(b_text)}",
        "",
        diff.summary(),
        "",
        "=== EVERY TYPE BELOW IS DIVERGENT AND NEEDS A DECISION ===",
    ]
    for name in sorted(diff.divergent):
        td = diff.a_types[name]
        btd = diff.b_types[name]
        if td.kind != btd.kind:
            lines.append(f"  {name}: KIND CHANGED ({td.kind} vs {btd.kind}) - "
                         "keep_a or keep_b only")
            continue
        attr = ("fields" if td.kind in ("type", "interface", "input") else
                "values" if td.kind == "enum" else "members")
        a_keys = (list(td.fields) if attr == "fields" else
                  list(td.enum_values) if attr == "values" else list(td.union_members))
        b_keys = (list(btd.fields) if attr == "fields" else
                  list(btd.enum_values) if attr == "values" else list(btd.union_members))
        lines.append(f"  {name} [{td.kind}] {attr}={a_keys + [k for k in b_keys if k not in a_keys]}")
        if td.implements or btd.implements:
            ifaces = list(td.implements) + [i for i in btd.implements if i not in td.implements]
            lines.append(f"    implements={ifaces}")
    lines += ["", "=== PROPOSED RENAMES (structural: same kind, same body) ==="]
    proposals = propose_renames(diff)
    if proposals:
        lines += [f"  {old} -> {new}" for old, new in proposals]
    else:
        lines.append("  (none)")
    return "\n".join(lines) + "\n"


def merge_task(diff_text: str, out_hint: str) -> str:
    return f"""MERGE_DECISIONS contract={CONTRACT}

The compact diff below is the whole input. You cannot read the candidates and
you cannot write a schema: your only output is the decision document, submitted
through submit_output.

Read the exact format with the `merge_decision_format` tool before you answer.

{diff_text}
=== WHAT TO RETURN ===
One JSON object, no prose around it:

{{"contract": "{CONTRACT}",
  "diff": {{"sha256": "{sha256_text(diff_text)}"}},
  "candidates": {{"a": {{"path": "...", "sha256": "..."}},
                  "b": {{"path": "...", "sha256": "..."}}}},
  "types": [{{"name": "<TypeName>", "disposition": "reconcile",
              "fields": {{"<field>": "a"|"b", ...}},
              "implements": {{"<Interface>": "a"|"b"}},
              "reason": "<one line: which side's shape you kept and why>"}},
            {{"name": "<TypeName>", "disposition": "keep_a"|"keep_b",
              "reason": "<one line: the STRUCTURAL reason combining is wrong>"}}],
  "renames": [{{"from": "<A>", "to": "<B>", "disposition": "rename"|"distinct",
               "reason": "<one line>"}}]}}

Rules, all checked mechanically before your answer is used:
- Copy the candidate paths and sha256 values exactly as printed above, and the
  diff sha256 printed above. A mismatch is rejected as stale.
- Every divergent type gets exactly one entry. Every key printed for a type
  appears in its map with a source of "a" or "b". No extra keys.
- reconcile is the norm: name a source per key so the merged type is a superset
  of both. keep_a/keep_b is the exception, needs the structural reason, and its
  map must name exactly that side's keys.
- reconcile must take something from both sides, or it is a whole-side pick.
- Every proposed rename gets exactly one entry. `rename` carries it out;
  `distinct` keeps both types and is also a decision.
- Every entry needs a one-line `reason`. Reasons are recorded as evidence and
  are never interpreted.
- Return ALL of it in one submit_output call. Write nothing to disk ({out_hint}).
"""


# ── lev plumbing ─────────────────────────────────────────────────────────

class LevError(RuntimeError):
    pass


def final_output_text(result: dict) -> str:
    """The lane's answer as TEXT, whatever shape `lev run --json` chose.

    `lev run --json --wait` returns `{run_id, status, final_output}`. On this box
    `final_output` is not always a plain string: with a submitted document it is
    an object like `{"content": "<the document>"}`. `str()` of that is Python
    repr with single quotes, which is not JSON, so every decision document was
    rejected as `malformed: not valid JSON`. This unwraps the common shapes and
    falls back to the string form, so a real lane answer is read as text.
    """
    if result is None:
        return ""
    value = result.get("final_output")
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("content", "text", "output", "final_output"):
            inner = value.get(key)
            if isinstance(inner, str):
                return inner
        # A dict with no text field: keep it lossless as JSON, not repr.
        return json.dumps(value)
    if isinstance(value, list):
        parts = [p for p in value if isinstance(p, str)]
        if len(parts) == len(value):
            return "\n".join(parts)
        return json.dumps(value)
    return str(value)


def lev_run(agent: str, task: str, workdir: Path, *, output_schema: Path = None,
            timeout: int = 3600) -> dict:
    """Run one lane to completion; return {run_id, status, final_output}.

    Uses `lev run --json --wait`, so the run id and the answer come back from one
    call. The driver passes no environment beyond its own: `lev` resolves its own
    credentials.
    """
    cmd = [harnessql_paths.lev_bin(), "run", harnessql_paths.agent_path(agent),
           "--yolo",
           "--workdir", str(workdir), "--json", "--wait"]
    task_path = workdir / f".task-{time.time_ns()}.txt"
    task_path.write_text(task, encoding="utf-8")
    cmd += ["--task", str(task_path)]
    if output_schema is not None:
        cmd += ["--output-schema", "@" + str(output_schema)]
    log(f"  $ {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise LevError(f"{agent} run exceeded {timeout}s") from exc
    finally:
        task_path.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise LevError(f"{agent} run failed (exit {proc.returncode}): "
                       f"{proc.stderr.strip()[:800]}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise LevError(f"could not parse `lev run --json` output: "
                       f"{proc.stdout[:400]!r}") from exc


# ── evidence ─────────────────────────────────────────────────────────────

def write_evidence(dir_: Path, name: str, text: str) -> dict:
    path = dir_ / name
    path.write_text(text, encoding="utf-8")
    return {"path": str(path), "bytes": len(text.encode('utf-8')),
            "sha256": sha256_text(text)}


def record(outcome: Outcome, bundle: dict, decisions_text: str) -> dict:
    bundle.update({
        "status": "verified",
        "result_sha256": outcome.result_sha256,
        "decision_sha256": outcome.decision_hash,
        "candidate_sha256": outcome.candidate_hashes,
        "diff_sha256": outcome.diff_sha256,
        "rationale": outcome.rationale_md,
        "decisions": decisions_text,
    })
    return bundle


# ── verification ─────────────────────────────────────────────────────────

def reapply(decisions_text: str, a_text: str, b_text: str, diff_text: str,
            first: Outcome) -> tuple[bool, Outcome]:
    """Re-apply the SAME decisions to the SAME candidates and compare bytes.

    This is the machine check behind "the same candidates and decisions produce
    identical output": it is not a claim about the model, it is a re-run.
    """
    again = merge(a_text, b_text, load_document(decisions_text),
                  expected={"diff_text": diff_text,
                            "candidates": {"a": first.candidate_paths.get("a", "a"),
                                           "b": first.candidate_paths.get("b", "b")}})
    return again.result_sha256 == first.result_sha256, again


def convergence_note(cands: list[str], provenance: Optional[list] = None,
                     a_text: Optional[str] = None, b_text: Optional[str] = None) -> dict:
    """Whether a result may be called independent convergence.

    Convergence means the two candidates AGREE. Independence is a claim about
    where they CAME FROM, so it cannot be read off the bytes: two lanes fed the
    same merged file, or one lane's output copied to both slots, produce
    byte-identical candidates that prove nothing. This checks provenance.

    `provenance` is an optional `[{"path":…, "run_id":…, "lane":…}, …]` pair as
    recorded by the conductor. When it is absent the note says so instead of
    implying independence.
    """
    same_file = os.path.realpath(cands[0]) == os.path.realpath(cands[1])
    same_bytes = None
    if a_text is not None and b_text is not None:
        same_bytes = sha256_text(a_text) == sha256_text(b_text)

    reasons = []
    if same_file:
        reasons.append("both candidates are the same file")

    run_ids = []
    if provenance and len(provenance) >= 2:
        for entry in provenance[:2]:
            run_ids.append(str(entry.get("run_id") or entry.get("path") or "?"))
        if run_ids[0] == run_ids[1]:
            reasons.append(f"both candidates came from one run ({run_ids[0]})")
        lanes = [entry.get("lane") for entry in provenance[:2]]
        if lanes[0] is not None and lanes[0] == lanes[1]:
            reasons.append(f"both candidates carry lane label {lanes[0]}")
    else:
        reasons.append("provenance was not recorded, so independence cannot be claimed")

    independent = not reasons
    return {
        "same_input_file": same_file,
        "same_bytes": same_bytes,
        "run_ids": run_ids,
        "provenance_recorded": bool(provenance),
        "independent": independent,
        "reasons": reasons,
        "label": ("two independent candidates" if independent
                  else "self-merge (" + "; ".join(reasons)
                        + "); NOT independent convergence"),
    }


# ── CLI ──────────────────────────────────────────────────────────────────

def do_merge(a_path: str, b_path: str, out: str, *, decisions: str = None,
             evidence: Path = None, diff_out: Path = None,
             run_lane: bool = False, timeout: int = 3600) -> int:
    """One merge step: diff, then either an offline decision file or a live lane.

    Returns an exit code: 0 verified, 1 rejected, 2 blocked.
    """
    try:
        a_text = Path(a_path).read_text(encoding="utf-8")
        b_text = Path(b_path).read_text(encoding="utf-8")
    except OSError as exc:
        log(f"BLOCKED: cannot read a candidate: {exc}")
        return 2

    cands = {"a": str(a_path), "b": str(b_path)}
    diff_text = compact_diff_text(a_text, b_text, cands)
    diff_out = diff_out or Path(out).with_suffix(".diff.txt")
    diff_out.write_text(diff_text, encoding="utf-8")
    log(f"diff -> {diff_out} ({len(diff_text)}B, sha256 {sha256_text(diff_text)})")

    if decisions:
        decisions_text = Path(decisions).read_text(encoding="utf-8")
        log(f"offline: decisions from {decisions}")
    elif run_lane:
        workdir = harnessql_paths.workdir()
        workdir.mkdir(parents=True, exist_ok=True)
        schema_path = workdir / "merge-decisions.schema.json"
        schema_path.write_text(
            json.dumps(decision_json_schema(), indent=2, sort_keys=True), encoding="utf-8")
        log(f"dispatching {MERGE_LANE} (the reviewer has no file tools)")
        try:
            result = lev_run(MERGE_LANE, merge_task(diff_text, str(out)), workdir,
                             output_schema=schema_path, timeout=timeout)
        except LevError as exc:
            log(f"BLOCKED: {exc}")
            return 2
        log(f"  run {result.get('run_id')} status {result.get('status')}")
        decisions_text = final_output_text(result)
        if not decisions_text.strip():
            log("BLOCKED: the merge lane produced no final output")
            return 2
    else:
        log("BLOCKED: pass --decisions <file> (offline) or --run-lane")
        return 2

    try:
        doc = load_document(decisions_text)
        outcome = merge(a_text, b_text, doc, expected={"diff_text": diff_text,
                                                       "candidates": cands})
    except MergeDecisionError as exc:
        log(f"REJECTED: {exc}")
        if evidence:
            evidence.mkdir(parents=True, exist_ok=True)
            write_evidence(evidence, "merge-rejection.json",
                           json.dumps({"diff_sha256": sha256_text(diff_text),
                                       "decisions": decisions_text,
                                       "error": exc.as_dict()}, indent=2) + "\n")
        return 1

    Path(out).write_text(outcome.result_sdl, encoding="utf-8")
    Path(out + ".rationale.md").write_text(outcome.rationale_md, encoding="utf-8")

    same, again = reapply(decisions_text, a_text, b_text, diff_text, outcome)
    log(f"reapply: {'IDENTICAL' if same else 'DIVERGED'} "
        f"({outcome.result_sha256[:12]} vs {again.result_sha256[:12]})")
    if not same:
        log("REJECTED: re-applying the same decisions to the same candidates "
            "produced different bytes")
        return 1

    note = convergence_note([a_path, b_path], provenance=None, a_text=a_text, b_text=b_text)
    log(f"convergence: {note['label']}")
    if evidence:
        evidence.mkdir(parents=True, exist_ok=True)
        bundle = {
            "status": "verified",
            "diff": write_evidence(evidence, "diff.txt", diff_text),
            "decisions": write_evidence(evidence, "decisions.json", decisions_text),
            "rationale": write_evidence(evidence, "rationale.md", outcome.rationale_md),
            "result": write_evidence(evidence, "merged.graphql", outcome.result_sdl),
            "candidate_sha256": outcome.candidate_hashes,
            "candidate_paths": outcome.candidate_paths,
            "decision_sha256": outcome.decision_hash,
            "diff_sha256": outcome.diff_sha256,
            "result_sha256": outcome.result_sha256,
            "reapply": {"identical": same, "second_result_sha256": again.result_sha256},
            "convergence": note,
        }
        (evidence / "bundle.json").write_text(
            json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        log(f"evidence -> {evidence}/bundle.json")
    log(f"VERIFIED: {out} sha256 {outcome.result_sha256}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="mode", required=True)

    sub.add_parser("paths", help="resolved paths, as JSON (a fresh-install check)")

    p_diff = sub.add_parser("diff-task", help="write the compact diff for two candidates")
    p_diff.add_argument("--a", required=True)
    p_diff.add_argument("--b", required=True)
    p_diff.add_argument("--out", required=True)

    p_schema = sub.add_parser("json-schema", help="write the submission JSON Schema")
    p_schema.add_argument("--out", required=True)

    p_merge = sub.add_parser("merge", help="diff, decide, apply, verify, record evidence")
    p_merge.add_argument("--a", required=True)
    p_merge.add_argument("--b", required=True)
    p_merge.add_argument("--out", required=True)
    p_merge.add_argument("--decisions", help="an existing decision document (offline mode)")
    p_merge.add_argument("--run-lane", action="store_true",
                         help="dispatch the bake-merge lane for the decisions")
    p_merge.add_argument("--evidence-dir")
    p_merge.add_argument("--timeout", type=int, default=3600)

    args = parser.parse_args(argv)

    if args.mode == "paths":
        print(json.dumps(harnessql_paths.summary(), indent=2, sort_keys=True))
        return 0

    if args.mode == "json-schema":
        Path(args.out).write_text(
            json.dumps(decision_json_schema(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        log(f"wrote {args.out}")
        return 0

    if args.mode == "diff-task":
        a_text = Path(args.a).read_text(encoding="utf-8")
        b_text = Path(args.b).read_text(encoding="utf-8")
        text = compact_diff_text(a_text, b_text, {"a": args.a, "b": args.b})
        Path(args.out).write_text(text, encoding="utf-8")
        log(f"wrote {args.out} ({len(text)}B, sha256 {sha256_text(text)})")
        return 0

    evidence = Path(args.evidence_dir) if args.evidence_dir else None
    return do_merge(args.a, args.b, args.out, decisions=args.decisions,
                    evidence=evidence, run_lane=args.run_lane, timeout=args.timeout)


if __name__ == "__main__":
    sys.exit(main())
