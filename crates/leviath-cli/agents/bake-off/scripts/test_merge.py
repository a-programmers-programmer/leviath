#!/usr/bin/env python3
"""Parse-gate check for the merge engine on a real seed (portable, GQL-09).

Run:  python3 scripts/test_merge.py [schema.graphql]

Was: a /data/work/leviath scratch test that loaded B2.graphql by absolute path.
Now: resolves the seed through harnessql_paths, so it runs from any install.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import harnessql_paths
from graphql import build_schema
from graphql_diff import diff_types, apply_merge

seed_path = sys.argv[1] if len(sys.argv) > 1 else str(harnessql_paths.default_seed())
seed_text = open(seed_path).read()
print(f"Seed: {seed_path}")

diff_self = diff_types(seed_text, seed_text)
print(f"Self-diff: {len(diff_self.only_a)} only-A, {len(diff_self.only_b)} only-B, "
      f"{len(diff_self.both)} identical, {len(diff_self.divergent)} divergent")

# Self-merge (keep_b on all divergent — should be identity)
merge_module = __import__("merge_decisions")
outcome = merge_module.merge(seed_text, seed_text, {
    "contract": merge_module.CONTRACT,
    "candidates": {"a": {"path": seed_path, "sha256": merge_module.sha256_text(seed_text)},
                   "b": {"path": seed_path, "sha256": merge_module.sha256_text(seed_text)}},
    "types": [],
    "renames": [],
}) if not diff_self.divergent else None
if outcome is None:
    # divergent self-diff cannot be reconciled field by field; use the engine directly
    merged = apply_merge(seed_text, seed_text, [], renames={})
else:
    merged = outcome.result_sdl
print(f"Merged self: {len(merged)}B (seed: {len(seed_text)}B)")

try:
    build_schema(merged)
    print("SELF-MERGE PARSE: OK")
except Exception as e:
    print(f"SELF-MERGE PARSE FAIL: {e}")

lines = merged.split('\n')
dup_enums = [(i + 1, line) for i, line in enumerate(lines)
             if line.strip().startswith('enum ProposalInputRule')]
print(f"\nDuplicate 'enum ProposalInputRule' entries: {len(dup_enums)}")
for ln, line in dup_enums:
    print(f"  L{ln}: {line}")

orphans = []
in_union = False
for i, line in enumerate(lines):
    t = line.strip()
    if t.startswith('union '):
        in_union = True
        continue
    if t.startswith('|') and not in_union:
        orphans.append((i + 1, t))
    if t.startswith(('type ', 'enum ', 'input ', 'scalar ', 'interface ')):
        in_union = False
print(f"Orphan '|' lines: {len(orphans)}")
for ln, line in orphans[:5]:
    print(f"  L{ln}: {line}")
