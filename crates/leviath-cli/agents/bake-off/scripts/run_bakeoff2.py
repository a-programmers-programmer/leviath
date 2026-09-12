#!/usr/bin/env python3
"""
HarnessQL bakeoff runner v2 — polls Leviath run COMPLETION, not file mtime.
Level 0: 2 lanes on seed. Level N: 2 lanes each given BOTH prior candidates.
Terminate when lanes normalized-identical; else recurse (max 5 levels).
"""
import subprocess, os, sys, time, re, glob
sys.path.insert(0, "/data/work/leviath/study")
from harnessql import identical

STUDY = "/data/work/leviath/study"
BO = os.path.join(STUDY, "bakeoff")
os.makedirs(BO, exist_ok=True)
SEED = os.path.join(STUDY, "B2.graphql")
MAX_LEVELS = 5
RUNS = "/data/.leviath/runs"

def latest_run_for(prefix):
    """Return the newest run dir matching prefix (by started_at in meta)."""
    best, bt = None, -1
    for d in glob.glob(f"{RUNS}/{prefix}-*"):
        mp = f"{d}/meta.json"
        if not os.path.exists(mp): continue
        try: t = json_load(mp).get('started_at', 0)
        except Exception: t = 0
        if t > bt: best, bt = d, t
    return best

def run_lane(cand_paths, level, label):
    out = os.path.join(BO, f"v{level}_{label}.graphql")
    env = dict(os.environ); env["OPENROUTER_API_KEY"] = open("/data/state/openrouter_key.txt").read().strip()
    task = f"BAKE_OFF label={label} level={level} seeds={';'.join(cand_paths)} out={out}"
    subprocess.run(["lev","run","bake-off","--yolo","--workdir",STUDY,"--task",task],
                   env=env, cwd=STUDY, capture_output=True)
    return out

def dbg(msg):
    print(msg, flush=True)

def main():
    cands = [SEED]
    # clear old lane outputs
    for l in ("L0L0","L0L1","L1L0","L1L1","L2L0","L2L1","L3L0","L3L1","L4L0","L4L1"):
        p=os.path.join(BO,f"v0_{l}.graphql")
        for gp in glob.glob(os.path.join(BO,f"v*_{l}.graphql")): 
            try: os.remove(gp)
            except: pass
    for level in range(MAX_LEVELS):
        outs = []
        for lane in (0,1):
            out = run_lane(cands, level, f"L{level}L{lane}")
            outs.append(out)
            dbg(f"  spawned lane{level}L{lane} -> {out}")
        # wait until BOTH run dirs have final_output (completion), cap 20 min
        deadline = time.time() + 1200
        while time.time() < deadline:
            miss = 0
            for lane in (0,1):
                # find newest bake-off run dir; both lanes
                done = len([d for d in glob.glob(f"{RUNS}/bake-off-*") if os.path.exists(f"{d}/final_output")]) >= 2
            if done: break
            time.sleep(15)
        time.sleep(8)
        a = open(outs[0]).read() if os.path.exists(outs[0]) else ""
        b = open(outs[1]).read() if os.path.exists(outs[1]) else ""
        if not a or not b:
            dbg(f"[L{level}] missing outputs a={bool(a)} b={bool(b)}; abort"); break
        same = identical(a,b)
        dbg(f"[L{level}] lane0 {len(a)}B vs lane1 {len(b)}B -> {'IDENTICAL-MERGED' if same else 'DIFFERENT-recurse'}")
        if same:
            dbg(f"RESULT: MERGED at level {level} -> {outs[0]}"); break
        cands = outs
    else:
        dbg("RESULT: NO_CONVERGENCE after 5 levels; best-so-far output kept")

def json_load(p):
    import json; return json.load(open(p))

if __name__ == "__main__":
    main()