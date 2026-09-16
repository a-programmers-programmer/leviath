#!/usr/bin/env python3
"""
HarnessQL bakeoff runner v2 — polls for lane COMPLETION, not file mtime (portable, GQL-09).

Level 0: 2 lanes on the seed. Level N: 2 lanes each given BOTH prior candidates.
Terminate when the lanes are normalized-identical; else recurse (max 5 levels).

Portability (GQL-09): paths come from harnessql_paths; `lev` owns its own
credentials, so no key file is read or injected. The run id comes back from
`lev run --json`, which is a real id — the old version globbed run directories
under a hard-coded /data/.leviath/runs.
"""
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import harnessql_driver as driver
import harnessql_paths
from harnessql import identical

BO = harnessql_paths.workdir()
BO.mkdir(parents=True, exist_ok=True)
SEED = harnessql_paths.default_seed()
MAX_LEVELS = 5
LANE_AGENT = "bake-off"


def dbg(msg):
    print(msg, flush=True)


def run_lane(cand_paths, level, lane, label):
    """Dispatch one lane and wait for it; return (out_path, run_id, status)."""
    out = BO / f"v{level}_{label}.graphql"
    task = (f"BAKE_OFF label={label} level={level} lane={lane} "
            f"seeds={';'.join(str(p) for p in cand_paths)} out={out}")
    try:
        result = driver.lev_run(LANE_AGENT, task, BO, timeout=3600)
    except driver.LevError as e:
        dbg(f"  lane {label} FAILED: {e}")
        return out, None, "failed"
    return out, result.get("run_id"), result.get("status")


def main() -> int:
    dbg(f"HarnessQL bakeoff v2: seed={SEED} out={BO} max_levels={MAX_LEVELS}")
    cands = [SEED]
    results_by_level = {}
    for level in range(MAX_LEVELS):
        labels = [f"L{level}L0", f"L{level}L1"]
        dbg(f"[L{level}] two lanes iterating on {len(cands)} seed(s): "
            f"{[str(c) for c in cands]}")
        with ThreadPoolExecutor(max_workers=2) as pool:
            dispatched = list(pool.map(
                lambda lane: run_lane(cands, level, lane, labels[lane]),
                (0, 1)))
        results_by_level[level] = dispatched
        for lane, (out, run_id, status) in enumerate(dispatched):
            dbg(f"  lane{lane} run_id={run_id} status={status} -> {out}")

        outs = [Path(out) for out, _, _ in dispatched]
        a = outs[0].read_text() if outs[0].exists() else ""
        b = outs[1].read_text() if outs[1].exists() else ""
        if not a or not b:
            dbg(f"[L{level}] missing outputs a={bool(a)} b={bool(b)}; abort")
            return 2
        same = identical(a, b)
        dbg(f"[L{level}] lane0 {len(a)}B vs lane1 {len(b)}B -> "
            f"{'IDENTICAL-MERGED' if same else 'DIFFERENT-recurse'}")
        note = driver.convergence_note([str(outs[0]), str(outs[1])], provenance=None,
                                       a_text=a, b_text=b)
        dbg(f"[L{level}] convergence: {note['label']}")
        if same:
            dbg(f"RESULT: MERGED at level {level} -> {outs[0]}")
            return 0
        cands = outs
    dbg("RESULT: NO_CONVERGENCE after 5 levels; best-so-far output kept")
    return 1


if __name__ == "__main__":
    sys.exit(main())