#!/usr/bin/env python3
"""
HarnessQL runner — drives the recursive bake-off.

Level 0: 2 parallel flash lanes both iterate on the SEED          -> v[0][0], v[0][1]
Level N: 2 parallel flash lanes, EACH given both prior candidates -> v[N][0], v[N][1]
  - if v[N][0] == v[N][1]  (normalized identical) -> MERGED, stop
  - else recurse to level N+1, up to MAX_LEVELS
After MAX_LEVELS with no identity, declare best-so-far (pick the lane whose out is larger; else 0).
"""
import subprocess, os, sys, time, json
sys.path.insert(0, "/data/work/leviath/study")
from harnessql import identical

STUDY = "/data/work/leviath/study"
OUT   = os.path.join(STUDY, "bakeoff")
os.makedirs(OUT, exist_ok=True)

SEED = "/data/work/leviath/study/B2.graphql"   # Proposal, front-tier, needs review
MAX_LEVELS = 5

def run_lane(cand_paths, level, lane, label):
    out = os.path.join(OUT, f"v{level}_lane{lane}.graphql")
    env = dict(os.environ)
    env["OPENROUTER_API_KEY"] = open("/data/state/openrouter_key.txt").read().strip()
    task = (f"BAKE_OFF label={label} level={level} lane={lane} "
            f"seeds={';'.join(cand_paths)} out={out}")
    cmd = ["lev", "run", "bake-off", "--yolo", "--workdir", STUDY, "--task", task]
    # spawn (lev returns after spawning) — poll for out file
    subprocess.Popen(cmd, env=env, cwd=STUDY)
    # poll up to 15 min for the out file to appear fresh
    deadline = time.time() + 900
    while time.time() < deadline:
        if os.path.exists(out) and os.path.getmtime(out) > time.time() - 60:
            return out
        time.sleep(5)
    return out if os.path.exists(out) else None

def main():
    print(f"HarnessQL bake-off: seed={SEED} max_levels={MAX_LEVELS}", flush=True)
    candidates = [SEED]
    for level in range(0, MAX_LEVELS):
        out0 = os.path.join(OUT, f"v{level}_lane0.graphql")
        out1 = os.path.join(OUT, f"v{level}_lane1.graphql")
        # clean stale
        for p in (out0, out1):
            if os.path.exists(p): os.remove(p)
        print(f"[L{level}] lanes iterating on {len(candidates)} seed(s): {candidates}", flush=True)
        run_lane(candidates, level, 0, f"L{level}L0")
        run_lane(candidates, level, 1, f"L{level}L1")
        # wait for both
        for attempt in range(60):
            if os.path.exists(out0) and os.path.exists(out1):
                break
            time.sleep(5)
        if not (os.path.exists(out0) and os.path.exists(out1)):
            print(f"[L{level}] one/both lanes missing output; abort. {out0}={os.path.exists(out0)} {out1}={os.path.exists(out1)}", flush=True)
            break
        same = identical(open(out0).read(), open(out1).read())
        print(f"[L{level}] lane0 {os.path.getsize(out0)}B vs lane1 {os.path.getsize(out1)}B -> {'IDENTICAL-MERGED' if same else 'DIFFERENT-recurse'}", flush=True)
        if same:
            print(f"RESULT: MERGED at level {level} -> {out0}", flush=True)
            break
        candidates = [out0, out1]   # recurse: next level's seeds = both candidates
    else:
        # no identity after MAX_LEVELS
        print("RESULT: NO_CONVERGENCE after 5 levels; best-so-far = larger lane output", flush=True)

if __name__ == "__main__":
    main()