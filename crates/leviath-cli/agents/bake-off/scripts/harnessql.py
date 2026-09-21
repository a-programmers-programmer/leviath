#!/usr/bin/env python3
"""
HarnessQL — recursive bake-off schema breeder.

Usage pattern (driven externally per level):
  level0: 2 parallel lanes on SEED
  levelN: 2 parallel lanes, each given BOTH previous candidates, until identical or MAX_LEVEL

This module exposes:
  - invoke_lane(seed_paths, out_path, guide, level, lane) -> runs a flash blueprint via Leviath
  - merge_if_identical(a, b) -> True if both schemas are byte-identical after normalization
"""
import subprocess, os, sys, hashlib, json

STUDY = "/data/work/leviath/study"
GUIDE = "/data/work/leviath/study/composition-guide.md"

def normalize(schema_text: str) -> str:
    """Normalize to make byte-identity comparison meaningful (strip whitespace/blank lines, stable order)."""
    lines = [l.rstrip() for l in schema_text.splitlines()]
    # drop blanks + strip leading/trailing whitespace
    cleaned = [l.strip() for l in lines if l.strip()]
    return "\n".join(cleaned)

def identical(a_text: str, b_text: str) -> bool:
    return normalize(a_text) == normalize(b_text)

def invoke_lane(seed_paths: list, out_path: str, label: str, level: int, lane: int, max_iter: int = 25):
    """Run one flash lane that reads the guide + seed(s) and writes an iterated schema."""
    env = dict(os.environ)
    env["OPENROUTER_API_KEY"] = open("/data/state/openrouter_key.txt").read().strip()
    # Pass which seeds to read via task string
    seeds_arg = ";".join(seed_paths)
    task = f"BAKE_OFF label={label} level={level} lane={lane} seeds={seeds_arg} out={out_path}"
    cmd = ["lev", "run", "draft-flash", "--yolo", "--workdir", STUDY, "--task", task]
    subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=max_iter * 60)
    return out_path

if __name__ == "__main__":
    # CLI: bakeoff merge <a> <b> <out>  |  bakeoff diffcheck <a> <b>
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["merge", "diffcheck", "invoke"])
    ap.add_argument("paths", nargs="+")
    a = ap.parse_args()
    if a.mode == "diffcheck":
        a_text, b_text = a.paths
        print("IDENTICAL" if identical(open(a_text).read(), open(b_text).read()) else "DIFFERENT")
    elif a.mode == "merge":
        a_path, b_path, out = a.paths
        a_t = open(a_path).read(); b_t = open(b_path).read()
        print("MERGE_TERMINATE" if identical(a_t, b_t) else "MERGE_RECURSE")
    elif a.mode == "invoke":
        invoke_lane(a.paths[:-1], a.paths[-1], "lane", 0, 0)