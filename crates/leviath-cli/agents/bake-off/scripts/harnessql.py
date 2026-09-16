#!/usr/bin/env python3
"""
HarnessQL — recursive bake-off schema breeder.

Usage pattern (driven externally per level):
  level0: 2 parallel lanes on SEED
  levelN: 2 parallel lanes, each given BOTH previous candidates, until identical or MAX_LEVEL

This module exposes:
  - invoke_lane(seed_paths, out_path, guide, level, lane) -> runs a flash blueprint via Leviath
  - merge_if_identical(a, b) -> True if both schemas are byte-identical after normalization

Portability (work order GQL-09): every path comes from :mod:`harnessql_paths`, which
resolves from this agent's own directory and an explicit `HARNESSQL_*` override, so the
module runs from any install location. There is no `/data/work/leviath` default. `lev`
owns its own credentials: this module never reads, injects or logs a key file.
"""
import subprocess
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import harnessql_paths

LANE_AGENT = "bake-off"


def normalize(schema_text: str) -> str:
    """Normalize to make byte-identity comparison meaningful (strip whitespace/blank lines, stable order)."""
    lines = [l.rstrip() for l in schema_text.splitlines()]
    # drop blanks + strip leading/trailing whitespace
    cleaned = [l.strip() for l in lines if l.strip()]
    return "\n".join(cleaned)

def identical(a_text: str, b_text: str) -> bool:
    return normalize(a_text) == normalize(b_text)

def invoke_lane(seed_paths: list, out_path: str, label: str, level: int, lane: int, max_iter: int = 25):
    """Run one flash lane that reads the guide + seed(s) and writes an iterated schema.

    The lane reads the Composition Guide through its bundled `composition_guide` tool,
    so no guide path is passed. The seeds are staged inside the workdir; anything
    outside it is invisible to the lane's file tools.
    """
    workdir = harnessql_paths.workdir()
    workdir.mkdir(parents=True, exist_ok=True)
    seeds_arg = ";".join(str(p) for p in seed_paths)
    task = f"BAKE_OFF label={label} level={level} lane={lane} seeds={seeds_arg} out={out_path}"
    cmd = [harnessql_paths.lev_bin(), "run", LANE_AGENT, "--yolo",
           "--workdir", str(workdir), "--task", task]
    # No env injection: `lev` resolves its own provider credentials. Passing this
    # process's environment through would leak whatever the caller had.
    subprocess.run(cmd, capture_output=True, text=True, timeout=max_iter * 60)
    return out_path

if __name__ == "__main__":
    # CLI: bakeoff merge <a> <b> <out>  |  bakeoff diffcheck <a> <b>  |  bakeoff invoke <seeds...> <out>
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("mode", choices=["merge", "diffcheck", "invoke", "paths"])
    ap.add_argument("paths", nargs="*")
    a = ap.parse_args()
    if a.mode == "paths":
        import json
        print(json.dumps(harnessql_paths.summary(), indent=2, sort_keys=True))
    elif a.mode == "diffcheck":
        a_text, b_text = a.paths
        print("IDENTICAL" if identical(open(a_text).read(), open(b_text).read()) else "DIFFERENT")
    elif a.mode == "merge":
        a_path, b_path, out = a.paths
        a_t = open(a_path).read(); b_t = open(b_path).read()
        print("MERGE_TERMINATE" if identical(a_t, b_t) else "MERGE_RECURSE")
    elif a.mode == "invoke":
        invoke_lane(a.paths[:-1], a.paths[-1], "lane", 0, 0)
