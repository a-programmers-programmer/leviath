#!/usr/bin/env python3
"""Diagnostic: trace a self-merge's byte accounting (portable, GQL-09).

Run:  python3 scripts/trace_merge.py [schema.graphql]
Defaults to the in-repo seed resolved through harnessql_paths, so it runs from
any install location. Nothing here reads a key file.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import harnessql_paths
from graphql_diff import diff_types, apply_merge, parse_schema

seed_path = sys.argv[1] if len(sys.argv) > 1 else str(harnessql_paths.default_seed())
seed = open(seed_path).read()
print("Seed:", seed_path, len(seed), "bytes")

diff = diff_types(seed, seed)
merged = apply_merge(seed, seed, [], renames={})
print("apply_merge:", len(merged), "bytes")
print("Lost:", len(seed) - len(merged), "bytes")

# Now trace the internal steps
a_types = parse_schema(seed)
sorted_a = sorted(a_types, key=lambda t: t.start)

parts = []
last = 0
for t in sorted_a:
    pref = seed[last:t.start]
    parts.append(pref)
    parts.append(t.body)
    last = t.end
parts.append(seed[last:])
raw = ''.join(parts)
print("Raw before dedup:", len(raw), "bytes")

deduped = raw
if hasattr(__import__('graphql_diff'), '_strip_duplicate_defs'):
    deduped = __import__('graphql_diff')._strip_duplicate_defs(raw)
print("After dedup:", len(deduped), "bytes")

orphaned = deduped
if hasattr(__import__('graphql_diff'), '_strip_orphan_docstrings'):
    orphaned = __import__('graphql_diff')._strip_orphan_docstrings(deduped)
print("After orphan:", len(orphaned), "bytes")

lines_raw = raw.split('\n')
lines_dedup = deduped.split('\n')
diff_lines = set(lines_raw) - set(lines_dedup)
print("Lines removed by dedup:")
for l in sorted(diff_lines)[:15]:
    if l.strip():
        print("  ", l[:80])
