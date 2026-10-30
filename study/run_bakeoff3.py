#!/usr/bin/env python3
"""
HarnessQL bakeoff runner v3 — diff-based merge for level N>0.
Level 0: 2 parallel lanes on SEED (unchanged, full-schema iteration).
Level N>0: compute compact diff of the two candidates, hand the diff to
each lane for reconciliation decisions, then apply_merge to produce output.

Uses LeviathClient.run_and_wait (async, no spawn-race).
"""
import asyncio, sys, os, json, textwrap

sys.path.insert(0, "/data/work/leviath/python")
sys.path.insert(0, "/data/work/leviath/study")
from leviath import LeviathClient
from harnessql import identical
from graphql import build_schema, GraphQLSyntaxError
from graphql_diff import (
    diff_types, apply_merge, parse_decisions, MergeDecision,
    extract_type_names, SchemaDiff
)

STUDY = "/data/work/leviath/study"
BO = os.path.join(STUDY, "bakeoff")
os.makedirs(BO, exist_ok=True)
SEED = os.path.join(STUDY, "B2.graphql")
MAX_LEVELS = 5
env = {"OPENROUTER_API_KEY": open("/data/state/openrouter_key.txt").read().strip()}


def build_diff_task(diff: SchemaDiff, label: str) -> str:
    """Build a compact task for a flash lane to decide on divergent types."""
    summary = diff.summary()

    # Identify likely renames between only_a and only_b
    renames = _guess_renames(diff)

    task = textwrap.dedent(f"""\
    BAKE_OFF label={label} MERGE_DECISIONS

    You are a schema arbitrator. Below is a COMPACT DIFF between two candidate
    GraphQL schemas (A and B) for the same domain. Your job: for each DIVERGENT
    type, decide whether to keep A's version, keep B's version, or reconcile
    (pick specific fields from each). Output your decisions in the format
    below, then call submit_output with the decisions text.

    === DIFF SUMMARY ===
    {summary}

    === LIKELY RENAMES (A name → B name) ===
    {_format_renames(renames)}

    === DECISION FORMAT ===
    For each divergent type, output one block:
      TYPE <TypeName>: reconcile | keep_a | keep_b
      REASON: <one line; for keep_* name the STRUCTURAL reason, not a preference>
      FIELDS: <comma-separated field names>   (required when reconcile: the fields to
               carry over from the other side onto A's base definition)

    For each likely rename pair, output:
      RENAME <OldName> -> <NewName>: keep_a | keep_b
      REASON: <one-line reason>

    === RULES ===
    - DEFAULT IS RECONCILE. For every divergent type, emit `reconcile` and list the
      fields to carry over, so the result is a SUPERSET of both: A's definition as the
      base, plus every field from B that A lacks or that B models better. Do not pick a
      whole side out of preference.
    - `keep_a` / `keep_b` (adopting one side wholesale) is the EXCEPTION. You may only
      emit it when field-level combination would be WRONG, and your REASON must name
      that structural reason (e.g. "B models this as a different abstraction", "the two
      variant sets are mutually exclusive"). "More evolved" / "better name" alone is
      NOT a justification for discarding the other side's fields.
    - Where a field exists on both sides but differs, reconcile it: include the field
      and describe in REASON which side's shape you are keeping and why.
    - Renames: a RENAME line decides the NAME only. The type's BODY is still decided by
      the TYPE line above, and it follows the reconcile-first rule like any other.
    - Output ALL divergent types and ALL renames. Do not skip any.


    After listing all decisions, call submit_output with the full decisions text.
    """)
    return task


def _guess_renames(diff: SchemaDiff) -> list[tuple[str, str]]:
    """Heuristically pair only_a with only_b types that look like renames."""
    renames = []
    # Known renames in this domain:
    known = [
        ("ProposalChange", "ProposalResult"),
        ("ReviewerBindingChange", "ReviewerBindingResult"),
        ("Resource", "ProposalResource"),
        ("ResourceNotFound", "ProposalNotFound"),
        ("ReviewOutcome", "RoundOutcome"),
        ("ResourceKind", "ProposalResource"),
        ("ConfirmationInput", "ConfirmationGateInput"),
        ("ChoiceInput", "ChoiceGateInput"),
    ]
    for a_name, b_name in known:
        if a_name in diff.only_a and b_name in diff.only_b:
            renames.append((a_name, b_name))
    return renames


def _format_renames(renames: list[tuple[str, str]]) -> str:
    if not renames:
        return "  (none detected)"
    return '\n'.join(f"  {a} → {b}" for a, b in renames)


def parse_gate(path: str, label: str) -> bool:
    """Validate a schema file with graphql-core's build_schema().
    Returns True if valid, False (prints warning) if not.
    An unparseable artifact cannot be declared converged."""
    try:
        text = open(path).read()
        build_schema(text)
        print(f"[{label}] PARSE GATE PASS: {os.path.basename(path)}", flush=True)
        return True
    except GraphQLSyntaxError as e:
        print(f"[{label}] PARSE GATE FAIL: {os.path.basename(path)}: {e}", flush=True)
        return False
    except Exception as e:
        print(f"[{label}] PARSE GATE ERROR: {os.path.basename(path)}: {e}", flush=True)
        return False


def parse_error_of(path: str) -> str:
    """Return the parser's complaint about a file, or '' if it parses."""
    try:
        build_schema(open(path).read())
        return ""
    except Exception as e:
        return str(e).split("\n")[0]


MAX_LANE_RETRIES = 2


async def run_schema_lane(client, out: str, label: str, base_task: str, agent: str = "bake-off"):
    """Dispatch a lane and ENFORCE validity: if its schema output fails the
    graphql-core parse gate, re-dispatch that lane with the parse error fed back.
    This is the real 'cannot finish until the schema is valid' — the engine's
    hooks cannot retry, so the conductor enforces it. Up to MAX_LANE_RETRIES."""
    task = base_task
    result = None
    for attempt in range(MAX_LANE_RETRIES + 1):
        if os.path.exists(out):
            os.remove(out)
        try:
            result = await client.run_and_wait(agent, task=task, workdir=STUDY, timeout=0)
        except Exception as e:
            print(f"[{label}] attempt {attempt+1} RUN FAILED: {e}", flush=True)
            return None, False
        if os.path.exists(out) and parse_gate(out, f"{label} attempt{attempt+1}"):
            if attempt > 0:
                print(f"[{label}] VALID after re-dispatch (attempt {attempt+1})", flush=True)
            return result, True
        err = parse_error_of(out) if os.path.exists(out) else "no output file was written"
        print(f"[{label}] attempt {attempt+1} REJECTED — not valid GraphQL: {err}", flush=True)
        task = (
            base_task
            + "\n\n*** YOUR PREVIOUS OUTPUT WAS REJECTED BY THE SCHEMA VALIDATOR. ***\n"
            + f"It did not parse as GraphQL. The parser says:\n    {err}\n"
            + f"You MUST write a CORRECTED, SYNTACTICALLY VALID GraphQL SDL to {out} "
            + "(no duplicated type/enum/union/input definitions; every union member "
            + "defined exactly once; braces balanced) and then call submit_output."
        )
    print(f"[{label}] EXHAUSTED {MAX_LANE_RETRIES+1} attempts — schema never validated", flush=True)
    return result, False



async def run_level0():
    """Level 0: two lanes each iterate on the seed schema independently.
    Each lane is validity-enforced via run_schema_lane (re-dispatch until parseable)."""
    outs = [os.path.join(BO, "v0_L0L0.graphql"), os.path.join(BO, "v0_L0L1.graphql")]
    tasks = []
    async with LeviathClient(env=env) as client:
        for lane, out in enumerate(outs):
            label = f"L0L{lane}"
            task = (
                f"BAKE_OFF label={label} level=0 seed={SEED} out={out}\n"
                f"Iterate on the GraphQL schema at {SEED} to produce an improved "
                f"version. Follow the Composition Guide at /data/work/leviath/study/"
                f"composition-guide.md. The output MUST be syntactically valid GraphQL SDL "
                f"(no duplicated type/enum/union/input definitions; every union member defined "
                f"exactly once; braces balanced). Write the final schema to {out} and call "
                f"submit_output."
            )
            tasks.append(asyncio.create_task(
                run_schema_lane(client, out, label, task, agent="bake-off")))
        results = await asyncio.gather(*tasks, return_exceptions=True)
    # results are (RunResult|None, valid:bool) tuples, or exceptions
    ok = [r for r in results if isinstance(r, tuple) and r[1]]
    return outs, results



async def run_merge_level(cands: list[str], level: int):
    """
    Merge level (N>0): compute diff, ask lanes to decide, apply_merge.
    Returns (out_paths, results) where out_paths are the merged files.
    """
    a_text = open(cands[0]).read()
    b_text = open(cands[1]).read()

    # Compute the compact diff
    diff = diff_types(a_text, b_text)
    diff_file = os.path.join(BO, f"v{level}_diff.txt")
    diff_task_text = build_diff_task(diff, f"L{level}")

    # Write the diff for reference
    with open(diff_file, 'w') as f:
        f.write(diff.summary())

    print(f"[L{level}] Diff: {len(diff.only_a)} only-A, {len(diff.only_b)} only-B, "
          f"{len(diff.both)} identical, {len(diff.divergent)} divergent "
          f"(diff size: {len(diff.summary())}B vs {len(a_text)}+{len(b_text)}="
          f"{len(a_text)+len(b_text)}B raw)", flush=True)

    # CONVERGED: nothing divergent means there is nothing to arbitrate. There is
    # no decision for a lane to make, so do not ask one — a lane handed an empty
    # diff has no TYPE/RENAME blocks to emit and its output is unparseable by
    # design. The candidates already agree; emit one as the result and stop.
    if diff.is_empty or (not diff.divergent and not diff.only_a and not diff.only_b):
        converged = os.path.join(BO, f"v{level}_merged.graphql")
        with open(converged, 'w') as f:
            f.write(a_text)
        print(f"[L{level}] CONVERGED — no divergence between candidates; "
              f"nothing to arbitrate. -> {converged}", flush=True)
        return [converged], []

    # Two parallel lanes, each gets the same diff task
    outs = [os.path.join(BO, f"v{level}_L{lane}_decisions.txt") for lane in range(2)]
    tasks = []
    async with LeviathClient(env=env) as client:
        for lane, out in enumerate(outs):
            label = f"L{level}L{lane}"
            task = f"{diff_task_text}\nWrite your final decisions to {out}."
            tasks.append(asyncio.create_task(client.run_and_wait(
                "bake-merge", task=task, workdir=STUDY, timeout=0)))
        results = await asyncio.gather(*tasks, return_exceptions=True)

    # Check results
    for i, (r, out) in enumerate(zip(results, outs)):
        if isinstance(r, Exception):
            print(f"[L{level}] lane{i} FAILED: {r}", flush=True)
        else:
            print(f"[L{level}] lane{i} {r.status.value} iters={r.iteration} "
                  f"-> {out} ({os.path.getsize(out) if os.path.exists(out) else 0}B)", flush=True)

    # One valid decision set is enough (two lanes are two attempts at the same decision)
    usable = [o for r, o in zip(results, outs) if not isinstance(r, Exception) and os.path.exists(o) and os.path.getsize(o) > 0]
    if not usable:
        print(f"[L{level}] ABORT: no usable decision output from either lane", flush=True)
        return [], results

    # Parse decisions from whichever lane(s) succeeded (prefer lane0)
    ordered = sorted(usable, key=lambda p: 0 if p == outs[0] else 1)
    decisions, renames = [], {}
    for p in ordered:
        d, rn = parse_decisions(open(p).read())
        if d or rn:
            decisions, renames = d, rn
            print(f"[L{level}] using decisions from {os.path.basename(p)}: {len(d)} decisions, {len(rn)} renames", flush=True)
            break

    if not decisions and not renames:
        print(f"[L{level}] ABORT: no parseable decisions from either lane", flush=True)
        return [], results

    # Apply merge
    # Build detected renames from diff (same-concept types with different names)
    detected_renames = {}
    for a_name, b_name in renames.items():
        detected_renames[a_name] = b_name
    # Also add the auto-detected renames from _guess_renames
    guessed = _guess_renames(diff)
    for a_name, b_name in guessed:
        if a_name not in detected_renames:
            detected_renames[a_name] = b_name

    merged_text = apply_merge(a_text, b_text, decisions, renames=renames,
                              detected_renames=detected_renames)
    merged_out = os.path.join(BO, f"v{level}_merged.graphql")
    with open(merged_out, 'w') as f:
        f.write(merged_text)

    print(f"[L{level}] Merged -> {merged_out} ({len(merged_text)}B)", flush=True)

    # For the purpose of the bakeoff, we also write the merged result to both
    # lane output paths (so the next level can pick them up)
    for out in outs:
        with open(out.replace('_decisions.txt', '.graphql'), 'w') as f:
            f.write(merged_text)

    return [merged_out], results


async def main():
    # Level 0: independent iteration
    print("[L0] Running level 0 (independent iteration on seed)...", flush=True)
    outs, results = await run_level0()

    for i, (r, out) in enumerate(zip(results, outs)):
        if isinstance(r, Exception):
            print(f"[L0] lane{i} FAILED: {r}", flush=True)
        elif isinstance(r, tuple):
            rr, valid = r
            status = getattr(rr, "status", None)
            iters = getattr(rr, "iteration", "?")
            print(f"[L0] lane{i} {status} iters={iters} valid={valid} "
                  f"-> {out} ({os.path.getsize(out) if os.path.exists(out) else 0}B)", flush=True)
        else:
            print(f"[L0] lane{i} {getattr(r,'status',r)}", flush=True)

    def lane_ok(r):
        return (isinstance(r, tuple) and r[1]) or (not isinstance(r, (Exception, tuple)) and not isinstance(r, Exception))

    if any(isinstance(r, Exception) for r in results) or not all(
            isinstance(r, tuple) and r[1] for r in results):
        print("[L0] ABORT: a lane never produced a VALID schema", flush=True)
        return

    a = open(outs[0]).read()
    b = open(outs[1]).read()
    same = identical(a, b)
    print(f"[L0] lane0 {len(a)}B vs lane1 {len(b)}B -> "
          f"{'IDENTICAL-MERGED' if same else 'DIFFERENT-recurse'}", flush=True)

    # PARSE GATE: L0 outputs must parse before we declare anything
    for i, out in enumerate(outs):
        if not parse_gate(out, f"L0 lane{i}"):
            print(f"[L0] ABORT: unparseable output {out}", flush=True)
            return

    if same:
        print(f"RESULT: MERGED at level 0 -> {outs[0]}", flush=True)
        return

    cands = outs

    # Level 1+: diff-based merge
    for level in range(1, MAX_LEVELS):
        print(f"\n[L{level}] Running diff-based merge level...", flush=True)
        merged_outs, results = await run_merge_level(cands, level)

        if not merged_outs:
            print(f"[L{level}] ABORT: merge failed", flush=True)
            break

        # For convergence check: compare the merged output to itself (always identical
        # since we only produce one merged output). If the diff was empty, we converged.
        diff = diff_types(open(cands[0]).read(), open(cands[1]).read())
        if diff.is_empty:
            # PARSE GATE: convergence requires the merged artifact to parse
            if parse_gate(merged_outs[0], f"L{level} converged"):
                print(f"RESULT: CONVERGED at level {level} (no diff between candidates)", flush=True)
            else:
                print(f"RESULT: CONVERGENCE-REFUSED at level {level} — "
                      "merged schema is unparseable", flush=True)
            break

        # Check if the merge produced something new
        merged_text = open(merged_outs[0]).read()
        a_text = open(cands[0]).read()
        b_text = open(cands[1]).read()

        if identical(merged_text, a_text) or identical(merged_text, b_text):
            if parse_gate(merged_outs[0], f"L{level} merged"):
                print(f"RESULT: MERGED at level {level} -> {merged_outs[0]}", flush=True)
            else:
                print(f"RESULT: MERGED-REFUSED — unparseable", flush=True)
            break

        # Continue with the merged output as the new candidate
        # (self-merge: both lanes get the same merged output)
        # PARSE GATE: validate before continuing
        if not parse_gate(merged_outs[0], f"L{level} merge"):
            print(f"RESULT: CONVERGENCE-REFUSED at level {level} — "
                  "merged schema is unparseable", flush=True)
            break
        cands = [merged_outs[0], merged_outs[0]]

    else:
        print("RESULT: NO_CONVERGENCE after 5 levels", flush=True)


if __name__ == "__main__":
    asyncio.run(main())