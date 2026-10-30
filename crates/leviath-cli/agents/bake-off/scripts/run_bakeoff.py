#!/usr/bin/env python3
"""
HarnessQL runner — drives the recursive bake-off.

Level 0: 2 parallel flash lanes both iterate on the SEED          -> v[0][0], v[0][1]
Level N: 2 parallel flash lanes, EACH given both prior candidates -> v[N][0], v[N][1]
  - if v[N][0] == v[N][1]  (normalized identical) -> MERGED, stop
  - else recurse to level N+1, up to MAX_LEVELS
After MAX_LEVELS with no identity, declare best-so-far (pick the lane whose out is larger; else 0).

Portability (GQL-09): paths come from harnessql_paths (agent-relative, HARNESSQL_*
overridable). No `/data/work/leviath` default and no key file read - `lev` resolves
its own credentials.
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import harnessql_paths
from harnessql import identical

STUDY = harnessql_paths.agent_dir()
OUT = harnessql_paths.workdir()
OUT.mkdir(parents=True, exist_ok=True)

SEED = harnessql_paths.default_seed()      # in-repo study/B2.graphql unless overridden
MAX_LEVELS = 5
LANE_AGENT = "bake-off"


def run_lane(cand_paths, level, lane, label):
    out = OUT / f"v{level}_lane{lane}.graphql"
    task = (f"BAKE_OFF label={label} level={level} lane={lane} "
            f"seeds={';'.join(str(p) for p in cand_paths)} out={out}")
    cmd = [harnessql_paths.lev_bin(), "run", LANE_AGENT, "--yolo",
           "--workdir", str(OUT), "--task", task]
    # spawn (lev returns after spawning) — poll for out file. No env injection:
    # `lev` owns its credentials.
    subprocess.Popen(cmd, cwd=str(OUT))
    # poll up to 15 min for the out file to appear fresh
    deadline = time.time() + 900
    while time.time() < deadline:
        if out.exists() and out.stat().st_mtime > time.time() - 60:
            return out
        time.sleep(5)
    return out if out.exists() else None


def main():
    print(f"HarnessQL bake-off: seed={SEED} out={OUT} max_levels={MAX_LEVELS}", flush=True)
    candidates = [SEED]
    for level in range(0, MAX_LEVELS):
        out0 = OUT / f"v{level}_lane0.graphql"
        out1 = OUT / f"v{level}_lane1.graphql"
        # clean stale
        for p in (out0, out1):
            if p.exists():
                p.unlink()
        print(f"[L{level}] lanes iterating on {len(candidates)} seed(s): {candidates}", flush=True)
        run_lane(candidates, level, 0, f"L{level}L0")
        run_lane(candidates, level, 1, f"L{level}L1")
        # wait for both
        for _ in range(60):
            if out0.exists() and out1.exists():
                break
            time.sleep(5)
        if not (out0.exists() and out1.exists()):
            print(f"[L{level}] one/both lanes missing output; abort. "
                  f"{out0}={out0.exists()} {out1}={out1.exists()}", flush=True)
            break
        same = identical(out0.read_text(), out1.read_text())
        print(f"[L{level}] lane0 {out0.stat().st_size}B vs lane1 {out1.stat().st_size}B -> "
              f"{'IDENTICAL-MERGED' if same else 'DIFFERENT-recurse'}", flush=True)
        if same:
            print(f"RESULT: MERGED at level {level} -> {out0}", flush=True)
            break
        candidates = [out0, out1]   # recurse: next level's seeds = both candidates
    else:
        # no identity after MAX_LEVELS
        print("RESULT: NO_CONVERGENCE after 5 levels; best-so-far = larger lane output",
              flush=True)


if __name__ == "__main__":
    main()
